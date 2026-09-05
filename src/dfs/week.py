"""Pure logic for `dfs week new`: parsing a pasted sheet URL and rewriting
config.toml's sheet_id, kept separate from cli.py's Sheets/typer-touching
wrapper per CONTRIBUTING.md's "split fetch logic into pure, testable
functions" rule.

`rewrite_sheet_id` is a targeted line rewrite, not a `tomli-w` round-trip --
tomli-w would re-serialize the whole file and drop every comment (config.toml
is hand-commented throughout, see config.example.toml), so this only ever
touches the `sheet_id = "..."` line and one `previous_sheet_id = "..."` line
next to it, leaving everything else byte-for-byte as the user wrote it.
"""

from __future__ import annotations

import re

_URL_ID_RE = re.compile(r"/spreadsheets/d/([a-zA-Z0-9_-]+)")
_SHEET_ID_LINE_RE = re.compile(r'^sheet_id\s*=\s*".*"\s*$', re.MULTILINE)
_PREVIOUS_SHEET_ID_LINE_RE = re.compile(r'^previous_sheet_id\s*=\s*".*"\s*$', re.MULTILINE)

# Cells `dfs week new` copies from the outgoing sheet's Bankroll tab to the
# new one -- the Ending balance of each of the three parallel bankrolls
# (main/DK, PP, UD) becomes the new sheet's Starting balance. See
# docs/ROADMAP.md's Phase 4 section for how these cell addresses were found;
# they're specific to the current template layout, not derived from anything
# self-describing in the sheet.
BANKROLL_CARRYOVER_CELLS = [("B2", "B1"), ("I2", "I1"), ("L2", "L1")]


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
