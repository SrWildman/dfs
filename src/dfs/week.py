"""Pure logic for `dfs week new`: parsing a pasted sheet URL, rewriting
config.toml's sheet_id, and carrying the Results log forward -- kept
separate from cli.py's Sheets/typer-touching wrapper per CONTRIBUTING.md's
"split fetch logic into pure, testable functions" rule.

`rewrite_sheet_id` is a targeted line rewrite, not a `tomli-w` round-trip --
tomli-w would re-serialize the whole file and drop every comment (config.toml
is hand-commented throughout, see config.example.toml), so this only ever
touches the `sheet_id = "..."` line and one `previous_sheet_id = "..."` line
next to it, leaving everything else byte-for-byte as the user wrote it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_URL_ID_RE = re.compile(r"/spreadsheets/d/([a-zA-Z0-9_-]+)")
_SHEET_ID_LINE_RE = re.compile(r'^sheet_id\s*=\s*".*"\s*$', re.MULTILINE)
_PREVIOUS_SHEET_ID_LINE_RE = re.compile(r'^previous_sheet_id\s*=\s*".*"\s*$', re.MULTILINE)
_WEEK_TITLE_RE = re.compile(r"^Week (\d+)$")

# Cells `dfs week new` copies from the outgoing sheet's Bankroll tab to the
# new one -- the Ending balance of each of the three parallel bankrolls
# (main/DK, PP, UD) becomes the new sheet's Starting balance. See
# docs/planning/ROADMAP.md's Phase 4 section for how these cell addresses were found;
# they're specific to the current template layout, not derived from anything
# self-describing in the sheet.
BANKROLL_CARRYOVER_CELLS = [("B2", "B1"), ("I2", "I1"), ("L2", "L1")]

# The Results tab's column layout: A=Week, B=Cash Pts, C=Cash Line,
# D=Cash Results (formula, =IF(B, B>C, "")), E=H2H Entered, F=H2H Win,
# G=H2H % (formula, =F/E), H=Red, I=Blue, J=Black. D and G are already
# built into every row of the tab (same "pre-built per-row formula" shape
# as Bankroll's entry tables) -- carrying them over as literal values
# would freeze last week's formula result in place of this week's. Only
# the typed-value columns get carried; the formula columns are left for
# whatever's already sitting in that row on the destination sheet.
RESULTS_VALUE_COLUMN_RANGES = ["A", "B:C", "E:F", "H:J"]


def extract_results_value_columns(rows: list[list[str]]) -> dict[str, list[list[str]]]:
    """`rows` is the Results tab's A:J data range (one inner list per row,
    0-indexed A=0 .. J=9). Returns the same rows split into the
    column-groups in RESULTS_VALUE_COLUMN_RANGES, ready to write one
    `update_range` call per group -- skipping columns D and G (the
    formula columns) entirely, so a carryover write never overwrites a
    formula with a stale literal value."""

    def cell(row: list[str], idx: int) -> str:
        return row[idx] if idx < len(row) else ""

    return {
        "A": [[cell(r, 0)] for r in rows],
        "B:C": [[cell(r, 1), cell(r, 2)] for r in rows],
        "E:F": [[cell(r, 4), cell(r, 5)] for r in rows],
        "H:J": [[cell(r, 7), cell(r, 8), cell(r, 9)] for r in rows],
    }


def parse_sheet_id_from_url(url_or_id: str) -> str:
    """Pull the ID segment out of a pasted Google Sheets URL
    (docs.google.com/spreadsheets/d/<id>/edit...), or accept a bare ID
    as-is. Raises ValueError with a clear message on anything else."""
    match = _URL_ID_RE.search(url_or_id)
    if match:
        return match.group(1)

    candidate = url_or_id.strip()
    if not candidate or "/" in candidate or " " in candidate:
        raise ValueError(
            f"Could not find a sheet ID in {url_or_id!r} -- pass the full sheet URL "
            f"(https://docs.google.com/spreadsheets/d/<id>/edit) or just the ID segment."
        )
    return candidate


def parse_week_from_title(title: str) -> int:
    """Which week a sheet's own title claims to be ("Week 3" -> 3).

    Week-3-fixes Fix 1 (2026-09-23): `week close`/`bankroll sync` used to
    scope their ledger filter to `nfl_calendar.current_week()` -- today's
    calendar week -- rather than the week the TARGET SHEET represents.
    That breaks the normal Tuesday `week close` run: by Tuesday,
    `current_week()` has already rolled to the next week (see
    `nfl_calendar.WEEK_ROLLOVER_LEAD_DAYS`), so the filter kept zero
    entries from the week that was just played -- they never reach the
    ledger, silently, for either command. The sheet's own title is
    unambiguous about which week it is, so it's the correct scope. There
    is deliberately no fallback to `current_week()` when the title
    doesn't parse -- that fallback is exactly how this broke. The
    template's own title ("Template") correctly raises here too: nobody
    should be running a bankroll close against it.
    """
    match = _WEEK_TITLE_RE.match(title.strip())
    if not match:
        raise ValueError(
            f'Sheet title {title!r} does not match the expected "Week <n>" format -- '
            "can't tell which week's ledger this is. Pass --week explicitly."
        )
    return int(match.group(1))


@dataclass
class WeekTitleResolution:
    target_title: str
    needs_rename: bool


def resolve_week_title(current_title: str, resolved_week: int) -> WeekTitleResolution:
    """Week 3 follow-ups, Item 1 (2026-09-23): what `dfs week new` should
    title the fresh copy, given the week it's already resolved (from
    `--week` or `nfl_calendar.current_week()`) -- pure decision logic,
    split out of cli.py's own Sheets-touching wrapper the same way every
    other `week new` calculation here is, so it can be tested without a
    real sheet.

    Raises `ValueError` if `current_title` ALREADY parses as `Week <n>`
    for a DIFFERENT `n` -- `week_new` must stop and ask rather than
    silently overwrite a title that might have been deliberate (a sheet
    reused on purpose, or a typo Sam already caught and fixed by hand).
    A title that doesn't parse at all (`Copy of Template`, blank, a typo)
    is NOT a conflict -- that's the expected, common state of a sheet
    fresh out of Drive's "make a copy," and gets renamed freely."""
    target_title = f"Week {resolved_week}"
    try:
        existing_week = parse_week_from_title(current_title)
    except ValueError:
        existing_week = None
    if existing_week is not None and existing_week != resolved_week:
        raise ValueError(
            f'Sheet is already titled "{current_title}" (Week {existing_week}), but the '
            f"derived week is {resolved_week}."
        )
    return WeekTitleResolution(target_title=target_title, needs_rename=current_title != target_title)


def rewrite_sheet_id(config_text: str, *, new_sheet_id: str, previous_sheet_id: str) -> str:
    """Replace config.toml's `sheet_id = "..."` line with `new_sheet_id`,
    and set (inserting if absent) a `previous_sheet_id = "..."` line right
    after it recording what `sheet_id` used to be. Every other line,
    including comments and blank lines, passes through unchanged."""
    if not _SHEET_ID_LINE_RE.search(config_text):
        raise ValueError('config.toml has no `sheet_id = "..."` line to rewrite.')

    updated = _SHEET_ID_LINE_RE.sub(f'sheet_id = "{new_sheet_id}"', config_text, count=1)

    previous_line = f'previous_sheet_id = "{previous_sheet_id}"'
    if _PREVIOUS_SHEET_ID_LINE_RE.search(updated):
        updated = _PREVIOUS_SHEET_ID_LINE_RE.sub(previous_line, updated, count=1)
    else:
        updated = _SHEET_ID_LINE_RE.sub(f'sheet_id = "{new_sheet_id}"\n{previous_line}', updated, count=1)

    return updated
