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

import re
from dataclasses import dataclass

from dfs.config import Config
from dfs.derived import EDGE_COLUMNS
from dfs.sheet_links import LINKED_EDGE_COLUMNS, PLAYER_POOL_RAW_TAB, find_all_contiguous
from dfs.sheet_pool_deck import DECK_ROWS, POOL_SORT_TAB
from dfs.sources.edge import POOL_HEADER
from dfs.weekly_reset import LINEUPS_NAME_BLOCKS, PLAYER_POOL_NAME_BLOCKS

# Column A of every repeated Lineups sub-header row is the literal text
# "Name" (see weekly_reset.py's module docstring) -- the tab's own header
# row (immediately above the first block) also starts with this, so the
# same check covers both.
_LINEUPS_HEADER_MARKER = "Name"

# PoolSort's A2 filter formula (sheet_pool_deck._build_pool_sort) always
# references Player Pool with explicit row numbers, e.g.
# 'Player Pool'!$A$2:$Z$80 or a single-cell 'Player Pool'!$A$2<>"" --
# _POOL_DECK_RANGE_RE grabs the whole $COL$ROW[:$COL$ROW] reference right
# after each 'Player Pool'! (a plain "match every $\d+" would also catch,
# say, a row number embedded in a *different* sheet's reference elsewhere
# in the formula); _ROW_NUM_RE then pulls every row number out of that
# captured reference. Taking the max across all of them is how this check
# independently re-derives what the deck can actually see, without
# importing sheet_pool_deck's own _POOL_LAST_ROW (that would just verify
# the module agrees with itself).
_POOL_DECK_RANGE_RE = re.compile(r"'Player Pool'!((?:\$[A-Z]+\$\d+:?)+)")
_ROW_NUM_RE = re.compile(r"\$(\d+)")


def _player_pool_rows_referenced(formula: str) -> list[int]:
    rows: list[int] = []
    for match in _POOL_DECK_RANGE_RE.finditer(formula):
        rows.extend(int(n) for n in _ROW_NUM_RE.findall(match.group(1)))
    return rows


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

    def read_formula(self, tab_name: str, a1_range: str) -> list[list[str]]:  # pragma: no cover
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
    issues = []
    for tab in (cfg.lineups.player_pool_tab, cfg.lineups.builder_tab, PLAYER_POOL_RAW_TAB):
        if tab not in tab_titles:
            continue
        header = headers_by_tab.get(tab, [])
        positions = find_all_contiguous(header, LINKED_EDGE_COLUMNS)
        if not positions:
            issues.append(DoctorIssue("linked-edge-columns", f"{tab!r}: LINKED_EDGE_COLUMNS not found"))
        elif len(positions) > 1:
            issues.append(
                DoctorIssue(
                    "linked-edge-columns",
                    f"{tab!r}: LINKED_EDGE_COLUMNS appears {len(positions)} times "
                    f"(at header index {positions}) -- looks like a duplicate append",
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


def _check_pool_deck_range(client: DoctorClient, cfg: Config, tab_titles: set[str]) -> list[DoctorIssue]:
    if POOL_SORT_TAB not in tab_titles:
        return []

    raw = client.read_formula(POOL_SORT_TAB, "A2")
    formula = raw[0][0] if raw and raw[0] else ""
    referenced_rows = _player_pool_rows_referenced(formula)
    if not referenced_rows:
        # Formula shape has changed enough that this check can't parse it --
        # not this check's job to flag that; nothing to compare here.
        return []

    formula_extent = max(referenced_rows)
    blocks_extent = max(end for _, end in PLAYER_POOL_NAME_BLOCKS)
    if formula_extent >= blocks_extent:
        return []

    pool_tab = cfg.lineups.player_pool_tab
    unreachable_positions = []
    for start, end in PLAYER_POOL_NAME_BLOCKS:
        if end <= formula_extent:
            continue
        label_cell = client.read_range(pool_tab, f"B{start}:B{start}") if pool_tab in tab_titles else []
        position = label_cell[0][0] if label_cell and label_cell[0] else f"rows {start}-{end}"
        unreachable_positions.append(position)

    return [
        DoctorIssue(
            "pool-deck-range",
            f"{POOL_SORT_TAB!r}'s filter formula only reaches row {formula_extent}, but "
            f"PLAYER_POOL_NAME_BLOCKS now extends to row {blocks_extent} -- "
            f"{', '.join(unreachable_positions)} player(s) past row {formula_extent} are "
            f"invisible to the pool deck's 'Start at' window. Re-run `dfs setup add-pool-deck`.",
        )
    ]


def _check_deck_block_alignment(
    cfg: Config,
    tab_titles: set[str],
    headers_by_tab: dict[str, list[str]],
    frozen_rows_by_tab: dict[str, int],
) -> list[DoctorIssue]:
    tab = cfg.lineups.builder_tab
    if tab not in tab_titles:
        return []

    issues = []
    header_row = LINEUPS_NAME_BLOCKS[0][0] - 1
    first_cell = (headers_by_tab.get(tab) or [""])[0]
    if first_cell != _LINEUPS_HEADER_MARKER:
        issues.append(
            DoctorIssue(
                "deck-block-alignment",
                f"{tab!r}: expected column A == {_LINEUPS_HEADER_MARKER!r} at row {header_row} "
                f"(the first sub-header, directly below the pool deck), found {first_cell!r}",
            )
        )

    frozen = frozen_rows_by_tab.get(tab)
    if frozen is not None and frozen != DECK_ROWS:
        issues.append(
            DoctorIssue(
                "deck-block-alignment",
                f"{tab!r}: frozen row count is {frozen}, expected DECK_ROWS ({DECK_ROWS}) -- "
                f"the deck and the lineup blocks have drifted apart",
            )
        )
    return issues


def run_doctor(client: DoctorClient, cfg: Config) -> list[DoctorIssue]:
    """Every check below only reads. Order matters for readability, not
    correctness -- later checks on a tab that's missing entirely are
    skipped rather than raising, since `_check_tabs_exist` already reports
    that failure once."""
    tabs = client.list_tabs()
    tab_titles = {t.title for t in tabs}
    headers_by_tab = {t.title: t.header for t in tabs}
    frozen_rows_by_tab = {t.title: t.frozen_rows for t in tabs}

    # list_tabs()/TabInfo.header always reads row 1 -- true for every tab
    # except Lineups, whose real header moved when sheet_pool_deck.py's
    # add_pool_deck inserted rows above it. Without this override,
    # _check_linked_edge_columns would read the deck's row-1 controls as
    # Lineups' "header", always report it unlinked, and (this happened for
    # real, on the template, during an earlier version of this row-insert)
    # `dfs setup link-edge` would append a second, wrongly-positioned copy
    # of LINKED_EDGE_COLUMNS on top of lineup data that's already correctly
    # linked -- see CONTRIBUTING.md's changelog.
    lineups_tab = cfg.lineups.builder_tab
    if lineups_tab in tab_titles:
        lineups_header_row = LINEUPS_NAME_BLOCKS[0][0] - 1
        raw = client.read_range(lineups_tab, f"A{lineups_header_row}:{lineups_header_row}")
        headers_by_tab[lineups_tab] = raw[0] if raw else []

    issues: list[DoctorIssue] = []
    issues += _check_tabs_exist(cfg, tab_titles)
    issues += _check_edge_header(cfg, tab_titles, headers_by_tab)
    issues += _check_linked_edge_columns(cfg, tab_titles, headers_by_tab)
    issues += _check_lineups_header_repeats(client, cfg, tab_titles)
    issues += _check_bankroll_headers(client, cfg, tab_titles)
    issues += _check_pool_deck_range(client, cfg, tab_titles)
    issues += _check_deck_block_alignment(cfg, tab_titles, headers_by_tab, frozen_rows_by_tab)
    return issues
