"""Read-only structural validator for a weekly sheet copy -- `dfs doctor`.

Every check here is something that has already gone wrong silently at
least once: a stale template missing a tab `dfs export`/`dfs lineups
clear` needs (`DK Upload`, `Scratch`), a duplicate `LINKED_EDGE_COLUMNS`
append caused by the tail-only idempotency check `sheet_links.py` used to
have, an `EdgeRaw` header that's drifted from `derived.EDGE_COLUMNS`, or a
`Lineups`/`Bankroll` row layout that's shifted from what the hardcoded row
constants assume. None of these fail loudly where they happen -- they
surface three commands later as a blank column, a wrong VLOOKUP result, or
a `dfs export`/`dfs lineups clear` crash on a sheet that looked fine to
the eye. This module only reads; nothing here ever writes to the sheet.
"""

from __future__ import annotations

from dataclasses import dataclass

from dfs.config import Config
from dfs.derived import EDGE_COLUMNS
from dfs.sheet_links import LINKED_EDGE_COLUMNS, PLAYER_POOL_RAW_TAB
from dfs.sheet_views import EXPOSURE_TAB, LINEUP_COUNT_CELL
from dfs.sources.edge import POOL_HEADER
from dfs.weekly_reset import LINEUPS_NAME_BLOCKS, PLAYER_POOL_HEADER_ROW

# Column A of every repeated Lineups sub-header row is the literal text
# "Name" (see weekly_reset.py's module docstring) -- the tab's own header
# row (immediately above the first block) also starts with this, so the
# same check covers both.
_LINEUPS_HEADER_MARKER = "Name"


@dataclass
class DoctorIssue:
    check: str
    detail: str


class DoctorClient:
    """The subset of SheetsClient this module needs -- kept narrow so
    offline tests can fake it without a real gspread backend, the same way
    test_sheet_links.py's SpySheetsClient does."""

    def list_tabs(self) -> list:  # pragma: no cover - structural only
        raise NotImplementedError

    def read_range(self, tab_name: str, a1_range: str) -> list[list[str]]:  # pragma: no cover
        raise NotImplementedError


def _expected_tabs(cfg: Config) -> set[str]:
    tabs = set(cfg.google_sheets.tab_mappings.values())
    tabs.add(cfg.lineups.upload_tab)
    tabs.add(cfg.lineups.scratch_tab)
    tabs.add(cfg.lineups.builder_tab)
    tabs.add(cfg.lineups.player_pool_tab)
    tabs.add(cfg.bankroll.tab)
    tabs.add(cfg.results.tab)
    tabs.add(PLAYER_POOL_RAW_TAB)
    return tabs


def _check_tabs_exist(cfg: Config, tab_titles: set[str]) -> list[DoctorIssue]:
    issues = []
    for tab in sorted(_expected_tabs(cfg)):
        if tab not in tab_titles:
            issues.append(DoctorIssue("tab-exists", f"{tab!r} is missing from this sheet"))
    return issues


def _check_edge_header(
    cfg: Config, tab_titles: set[str], headers_by_tab: dict[str, list[str]]
) -> list[DoctorIssue]:
    edge_tab = cfg.google_sheets.tab_mappings.get("edge")
    if not edge_tab or edge_tab not in tab_titles:
        return []
    header = headers_by_tab.get(edge_tab, [])
    # Pool (Task K) sits in column A, ahead of EDGE_COLUMNS -- see
    # sources/edge.py's docstring on why it's a real column deliberately
    # kept out of the EDGE_COLUMNS list itself. Checked together with
    # EDGE_COLUMNS as one contiguous expected header, not separately, now
    # that its position is load-bearing (not just appended past the end).
    expected = [POOL_HEADER, *EDGE_COLUMNS]
    if header[: len(expected)] != expected:
        return [
            DoctorIssue(
                "edgeraw-header",
                f"{edge_tab!r} header does not match [Pool, *EDGE_COLUMNS].\n"
                f"    expected: {expected}\n"
                f"    actual:   {header[: len(expected)]}",
            )
        ]
    return []


def _check_linked_edge_columns(
    cfg: Config, tab_titles: set[str], headers_by_tab: dict[str, list[str]]
) -> list[DoctorIssue]:
    # Phase 3: LINKED_EDGE_COLUMNS no longer lands as one contiguous
    # appended block (see sheet_links.py's rewrite) -- a designed order
    # interleaves them with native columns, so this checks presence and
    # uniqueness of each NAME independently instead of matching a run.
    issues = []
    for tab in (cfg.lineups.player_pool_tab, cfg.lineups.builder_tab, PLAYER_POOL_RAW_TAB):
        if tab not in tab_titles:
            continue
        header = headers_by_tab.get(tab, [])
        for name in LINKED_EDGE_COLUMNS:
            count = header.count(name)
            if count == 0:
                issues.append(DoctorIssue("linked-edge-columns", f"{tab!r}: {name!r} not found in header"))
            elif count > 1:
                issues.append(
                    DoctorIssue(
                        "linked-edge-columns",
                        f"{tab!r}: {name!r} appears {count} times in header -- ambiguous",
                    )
                )
    return issues


def _check_lineups_header_repeats(
    client: DoctorClient, cfg: Config, tab_titles: set[str]
) -> list[DoctorIssue]:
    tab = cfg.lineups.builder_tab
    if tab not in tab_titles:
        return []

    expected_rows = [start - 1 for start, _ in LINEUPS_NAME_BLOCKS]
    last_row = max(expected_rows)
    raw = client.read_range(tab, f"A1:A{last_row}")

    def cell(row_num: int) -> str:
        idx = row_num - 1
        if idx < 0 or idx >= len(raw):
            return ""
        row = raw[idx]
        return row[0] if row else ""

    bad_rows = [row for row in expected_rows if cell(row) != _LINEUPS_HEADER_MARKER]
    if bad_rows:
        return [
            DoctorIssue(
                "lineups-header-repeats",
                f"{tab!r}: expected column A == {_LINEUPS_HEADER_MARKER!r} at row(s) "
                f"{bad_rows} (per LINEUPS_NAME_BLOCKS) -- header repeats have drifted",
            )
        ]
    return []


def _check_bankroll_headers(client: DoctorClient, cfg: Config, tab_titles: set[str]) -> list[DoctorIssue]:
    tab = cfg.bankroll.tab
    if tab not in tab_titles:
        return []

    issues = []
    for label, table in (("cash", cfg.bankroll.cash), ("gpp", cfg.bankroll.gpp)):
        if table is None:
            continue
        row = client.read_range(tab, f"A{table.header_row}:{table.header_row}")
        values = row[0] if row else []
        if not any(v.strip() for v in values if isinstance(v, str)):
            issues.append(
                DoctorIssue(
                    "bankroll-header-row",
                    f"{tab!r}: bankroll.{label}.header_row ({table.header_row}) is blank -- "
                    f"expected a header row there",
                )
            )
    return issues


def _check_lineup_count_cell(client: DoctorClient, cfg: Config, tab_titles: set[str]) -> list[DoctorIssue]:
    """Phase 5A (moved to Exposure when the pool deck was removed
    entirely, Phase 5-removal): `Exposure!H1` is Exposure's own live
    divisor (the "how many lineups this week" control) -- a blank/zero/
    non-numeric H1 falls back safely to full capacity in the formula
    itself, so this can't corrupt Exposure, but a value outside
    1..capacity is still a sign the control was typed into wrong (or
    overwritten by something that doesn't know what it is) and is worth
    flagging."""
    if EXPOSURE_TAB not in tab_titles:
        return []

    raw = client.read_range(EXPOSURE_TAB, LINEUP_COUNT_CELL)
    value = raw[0][0] if raw and raw[0] else ""
    capacity = len(LINEUPS_NAME_BLOCKS)
    try:
        number = float(value)
    except (TypeError, ValueError):
        return [
            DoctorIssue(
                "lineup-count-cell",
                f"{EXPOSURE_TAB!r}: {LINEUP_COUNT_CELL} (lineup count, Exposure's own divisor) "
                f"is {value!r}, not a number",
            )
        ]
    if not (1 <= number <= capacity):
        return [
            DoctorIssue(
                "lineup-count-cell",
                f"{EXPOSURE_TAB!r}: {LINEUP_COUNT_CELL} (lineup count, Exposure's own divisor) "
                f"is {number!r}, expected 1..{capacity}",
            )
        ]
    return []


def run_doctor(client: DoctorClient, cfg: Config) -> list[DoctorIssue]:
    """Every check below only reads. Order matters for readability, not
    correctness -- later checks on a tab that's missing entirely are
    skipped rather than raising, since `_check_tabs_exist` already reports
    that failure once."""
    tabs = client.list_tabs()
    tab_titles = {t.title for t in tabs}
    headers_by_tab = {t.title: t.header for t in tabs}

    # list_tabs()/TabInfo.header always reads row 1 -- true for every tab
    # except Player Pool, whose real header moved from row 1 to
    # PLAYER_POOL_HEADER_ROW when A3's add-a-player control row was
    # inserted above it. Without this override, _check_linked_edge_columns
    # would read that control row as Player Pool's "header" and report
    # every one of LINKED_EDGE_COLUMNS missing. Lineups needed the same
    # override for as long as the pool deck sat above ITS header too --
    # removed along with the deck itself; Lineups' header is back at row 1,
    # matching list_tabs()'s own default the same as every other tab.
    player_pool_tab = cfg.lineups.player_pool_tab
    if player_pool_tab in tab_titles:
        raw = client.read_range(player_pool_tab, f"A{PLAYER_POOL_HEADER_ROW}:{PLAYER_POOL_HEADER_ROW}")
        headers_by_tab[player_pool_tab] = raw[0] if raw else []

    issues: list[DoctorIssue] = []
    issues += _check_tabs_exist(cfg, tab_titles)
    issues += _check_edge_header(cfg, tab_titles, headers_by_tab)
    issues += _check_linked_edge_columns(cfg, tab_titles, headers_by_tab)
    issues += _check_lineups_header_repeats(client, cfg, tab_titles)
    issues += _check_bankroll_headers(client, cfg, tab_titles)
    issues += _check_lineup_count_cell(client, cfg, tab_titles)
    return issues
