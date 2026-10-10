"""Read-only structural validator for a weekly sheet copy -- `dfs doctor`.

Every check here is something that has already gone wrong silently at
least once: a stale template missing a tab `dfs export`/`dfs lineups
clear` needs (`DK Upload`), a duplicate `LINKED_EDGE_COLUMNS`
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
from dfs.derived import ALL_PCT_COLUMNS, EDGE_SHEET_ORDER
from dfs.edge_finder_tab import EDGE_FINDER_TAB
from dfs.edge_finder_tab import ID_COL as EDGE_FINDER_ID_COL
from dfs.edge_finder_tab import POOL_COL as EDGE_FINDER_POOL_COL
from dfs.sheet_columns import INTERNAL
from dfs.sheet_empty_guards import describe as describe_unguarded
from dfs.sheet_empty_guards import find_unguarded
from dfs.sheet_formula_ranges import DKSALCLEAN_TAB, describe_gap, find_gaps, formula_ranges
from dfs.sheet_instructions import INSTRUCTIONS_LAST_ROW, INSTRUCTIONS_TAB, render_instructions_grid
from dfs.sheet_lineup_keys import LINEUP_KEY_HEADER
from dfs.sheet_links import LINKED_EDGE_COLUMNS, PLAYER_POOL_RAW_TAB
from dfs.sheet_names import ALIAS_TAB
from dfs.sheet_style import EDGE_HIDDEN_HELPERS, edge_group_spans
from dfs.sheet_views import BOARD_ID_COL, BOARD_LIST_POOL_COL, BOARD_TAB, EXPOSURE_TAB, LINEUP_COUNT_CELL
from dfs.sheets import column_letter
from dfs.sources.edge import POOL_HEADER
from dfs.week import parse_week_from_title
from dfs.weekly_reset import LINEUPS_NAME_BLOCKS, PLAYER_POOL_HEADER_ROW

# Column A of every repeated Lineups sub-header row is the literal text
# "Name" (see weekly_reset.py's module docstring) -- the tab's own header
# row (immediately above the first block) also starts with this, so the
# same check covers both.
_LINEUPS_HEADER_MARKER = "Name"

# Week 3 follow-ups, Item 1 (2026-09-23): the one title that is
# deliberately NOT required to parse as "Week <n>" -- confirmed live
# (both sheets' own `describe()`) to be the template's actual, fixed
# title. There is no other signal in this codebase that distinguishes
# "this is the template" from "this is a weekly copy" (no dedicated
# sheet-id constant, no config flag) -- the title is the only thing that
# already reliably identifies it, which is also exactly the thing
# `parse_week_from_title` itself already treats as the boundary case
# (see that function's own docstring). Doctor runs against the template
# constantly as part of the "template first" verification workflow,
# so without this exemption every one of those runs would report a
# permanent, un-fixable failure.
_TEMPLATE_TITLE = "Template"


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

    def row_count(self, tab_name: str) -> int:  # pragma: no cover
        raise NotImplementedError

    def get_column_widths(self, tab_name: str, last_col_a1: str) -> list[dict]:  # pragma: no cover
        raise NotImplementedError

    def get_column_groups(self, tab_name: str) -> list[tuple[int, int, bool]]:  # pragma: no cover
        raise NotImplementedError


def _check_sheet_title(title: str) -> list[DoctorIssue]:
    """Week 3 follow-ups, Item 1: catches a bad weekly-copy title (`Week4`,
    `week 4`, `Week 4 DFS`, `Copy of Template`, ...) the moment `week new`
    runs `dfs doctor` against the fresh copy -- not at Tuesday's `week
    close`, which is where this used to fail instead (Fix 1). Reuses
    `week.parse_week_from_title` itself, so this can never drift from
    the exact rule `week close`/`bankroll sync` depend on."""
    if title == _TEMPLATE_TITLE:
        return []
    try:
        parse_week_from_title(title)
    except ValueError as e:
        return [DoctorIssue("sheet-title", str(e))]
    return []


def _check_instructions_drift(client: DoctorClient, tab_titles: set[str]) -> list[DoctorIssue]:
    """Week 3 follow-ups, Item 2: `sheet_instructions.build_instructions_tab`
    only ever runs when someone remembers to call it (`dfs setup
    instructions`, or `dfs setup polish`) -- the exact same "correct in
    code, stale on the sheet until someone reruns it" drift class every
    other check in this file exists to catch. Renders the same grid
    `build_instructions_tab` would write (`render_instructions_grid`,
    shared so the two can never disagree about what "correct" looks
    like) and diffs it against one bulk read of the live tab -- a single
    `A1:B<last row>` read, not one read per row, since this tab is
    ~29 rows and a doctor check shouldn't cost that many round trips.
    Every fact this tab generates comes from a static Python constant
    (no sheet title, URL, or date embedded anywhere in it -- confirmed
    by reading through `sheet_instructions.py` before writing this
    check), so the comparison needs no per-sheet normalisation: the
    exact same grid is correct on the template and on every weekly copy."""
    if INSTRUCTIONS_TAB not in tab_titles:
        return []

    expected = render_instructions_grid()
    raw = client.read_range(INSTRUCTIONS_TAB, f"A1:B{INSTRUCTIONS_LAST_ROW}")

    def actual_row(row_num: int) -> tuple[str, str]:
        idx = row_num - 1
        if idx >= len(raw) or not raw[idx]:
            return ("", "")
        row = raw[idx]
        a = row[0] if len(row) > 0 else ""
        b = row[1] if len(row) > 1 else ""
        return (a, b)

    drifted_rows = [row_num for row_num, values in expected.items() if actual_row(row_num) != values]
    if drifted_rows:
        return [
            DoctorIssue(
                "instructions-drift",
                f"{INSTRUCTIONS_TAB!r} differs from what sheet_instructions.py generates at "
                f"row(s) {sorted(drifted_rows)} -- run `dfs setup instructions` "
                "(or `dfs setup polish`) to bring it back in sync.",
            )
        ]
    return []


def _expected_tabs(cfg: Config) -> set[str]:
    tabs = set(cfg.google_sheets.tab_mappings.values())
    tabs.add(cfg.lineups.upload_tab)
    tabs.add(cfg.lineups.builder_tab)
    tabs.add(cfg.lineups.player_pool_tab)
    tabs.add(cfg.bankroll.tab)
    tabs.add(cfg.results.tab)
    tabs.add(cfg.season.tab)
    tabs.add(ALIAS_TAB)
    tabs.add(PLAYER_POOL_RAW_TAB)
    tabs.add(DKSALCLEAN_TAB)
    tabs.add(EDGE_FINDER_TAB)  # written by every sync; `dfs setup build-views` creates the empty state
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
    expected = [POOL_HEADER, *EDGE_SHEET_ORDER]
    if header[: len(expected)] != expected:
        return [
            DoctorIssue(
                "edgeraw-header",
                f"{edge_tab!r} header does not match [Pool, *EDGE_SHEET_ORDER].\n"
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
    for label, table in (("cash", cfg.bankroll.cash), ("gpp", cfg.bankroll.gpp), ("bets", cfg.bankroll.bets)):
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


def _check_formula_ranges(
    client: DoctorClient, cfg: Config, headers_by_tab: dict[str, list[str]]
) -> list[DoctorIssue]:
    """Round 5 follow-up item 2: the hand-built per-row formula ranges nothing else
    writes (Results' `Cash Results`/`H2H %`, DkSalClean, PlayerPoolRaw, Season's totals)
    must have a formula on every row, and -- where row N must read row N -- not one
    pointing at another row. Both a missing DkSalClean formula and a blank Results
    column went unnoticed through several sessions before this existed."""
    issues = []
    for spec in formula_ranges(cfg, headers_by_tab):
        if not spec.columns:
            issues.append(
                DoctorIssue(
                    "formula-ranges",
                    f"{spec.tab!r}: none of the expected formula columns were found in its header row",
                )
            )
            continue
        for gap in find_gaps(client, spec):
            issues.append(DoctorIssue("formula-ranges", describe_gap(gap)))
    return issues


def _check_empty_guards(client: DoctorClient, cfg: Config, tab_titles: set[str]) -> list[DoctorIssue]:
    """Round 5 cleanup item 2: Results, Bankroll and Season divide and average in
    places that are empty on a fresh week; an unguarded one shows `#DIV/0!` instead of
    a blank. Results' per-row `H2H %` is checked with the other per-row formula ranges."""
    return [
        DoctorIssue("empty-guards", describe_unguarded(cells))
        for cells in find_unguarded(client, cfg, tab_titles)
    ]


# A synced EdgeRaw has hundreds of scored players; a handful of numbers is not a sync.
_PCT_HELPER_MIN_SOURCE_VALUES = 20


def _as_number(cell: str) -> float | None:
    try:
        return float(str(cell).strip().replace("$", "").replace(",", "").replace("%", ""))
    except ValueError:
        return None


def _check_pct_helpers(
    client: DoctorClient, cfg: Config, tab_titles: set[str], headers_by_tab: dict[str, list]
) -> list[DoctorIssue]:
    """The player-metric colour steps read hidden within-position percentile columns
    (`derived.ALL_PCT_COLUMNS`). If a sync leaves one blank -- or the link onto
    PlayerPoolRaw breaks -- those cells quietly get NO colour, and nothing else notices. Fails when
    a tab has numbers in a metric but not one number in its percentile column. (Whether a column
    exists at all is `_check_edge_header`'s and `_check_linked_edge_columns`' job.)"""
    issues: list[DoctorIssue] = []
    edge_tab = cfg.google_sheets.tab_mappings.get("edge")
    for tab in (edge_tab, PLAYER_POOL_RAW_TAB):
        header = headers_by_tab.get(tab)
        if not tab or tab not in tab_titles or not header:
            continue
        wanted = [(m, h) for m, h in ALL_PCT_COLUMNS.items() if m in header and h in header]
        if not wanted:
            continue
        last_col = column_letter(max(header.index(c) for pair in wanted for c in pair))
        rows = client.read_range(tab, f"A2:{last_col}{client.row_count(tab)}")
        for metric, helper in wanted:
            m_i, h_i = header.index(metric), header.index(helper)
            source = [n for r in rows if len(r) > m_i and (n := _as_number(r[m_i])) not in (None, 0.0)]
            filled = [r for r in rows if len(r) > h_i and _as_number(r[h_i]) is not None]
            if len(source) >= _PCT_HELPER_MIN_SOURCE_VALUES and not filled:
                issues.append(
                    DoctorIssue(
                        "pct-helpers",
                        f"{tab}: {len(source)} non-zero {metric} values but no number in {helper} -- "
                        f"{metric}'s highlighting reads that column, so it shows no colour "
                        "(run `dfs sync --only edge`, then check the link with `dfs doctor`).",
                    )
                )
    return issues


def _letters(spans: list[tuple[int, int]]) -> list[str]:
    return [f"{column_letter(a)}:{column_letter(b)}" for a, b in spans]


def _check_column_visibility(
    client: DoctorClient, cfg: Config, tab_titles: set[str], headers_by_tab: dict[str, list[str]]
) -> list[DoctorIssue]:
    """Which columns are hidden, by header NAME. A hidden column is right only if it is a helper
    (`Id`, the `*%ile` columns, ...) or sits inside a column group; a helper must be hidden; and EdgeRaw's
    groups must sit exactly where `EDGE_COLUMN_GROUPS` says. A group the person has expanded is fine.
    Caught after the EdgeRaw reorder (slice 5): Avail, Flags, Edge and the GAME / MOVE / WX labels were
    hidden outside any group because the hide logic had run against the old letters."""
    edge_tab = cfg.google_sheets.tab_mappings.get("edge")
    targets: list[tuple[str, tuple[str, ...], list[tuple[int, int]] | None]] = [
        (cfg.lineups.player_pool_tab, tuple(INTERNAL), None),
        (cfg.lineups.builder_tab, (*INTERNAL, LINEUP_KEY_HEADER), None),
        (PLAYER_POOL_RAW_TAB, tuple(INTERNAL), None),
    ]
    if edge_tab:
        targets.insert(0, (edge_tab, EDGE_HIDDEN_HELPERS, edge_group_spans()))
    issues: list[DoctorIssue] = []
    for tab, helpers, expected_spans in targets:
        header = headers_by_tab.get(tab)
        if tab not in tab_titles or not header:
            continue
        hidden = [
            bool(m.get("hiddenByUser")) for m in client.get_column_widths(tab, column_letter(len(header) - 1))
        ]
        groups = client.get_column_groups(tab)
        grouped = {i for first, last, _ in groups for i in range(first, last + 1)}
        stray = [
            f"{column_letter(i)} {name}"
            for i, name in enumerate(header)
            if i < len(hidden) and hidden[i] and i not in grouped and name not in helpers
        ]
        shown = [
            f"{column_letter(i)} {name}"
            for i, name in enumerate(header)
            if name in helpers and not (i < len(hidden) and hidden[i])
        ]
        if stray:
            issues.append(
                DoctorIssue(
                    "column-visibility",
                    f"{tab!r}: hidden but not a helper and not inside a column group: {', '.join(stray)} "
                    "(`dfs setup polish` unhides them).",
                )
            )
        if shown:
            issues.append(
                DoctorIssue(
                    "column-visibility",
                    f"{tab!r}: helper column(s) showing that should be hidden: {', '.join(shown)} "
                    "(`dfs setup polish` hides them).",
                )
            )
        if expected_spans is not None:
            actual = sorted((first, last) for first, last, _ in groups)
            if actual != sorted(expected_spans):
                issues.append(
                    DoctorIssue(
                        "column-visibility",
                        f"{tab!r}: column groups are at {_letters(actual)} but belong at "
                        f"{_letters(sorted(expected_spans))} (`dfs setup polish` rebuilds them).",
                    )
                )
    return issues


def _check_pool_cells_hold_the_formula(
    client: DoctorClient, cfg: Config, tab_titles: set[str], headers_by_tab: dict[str, list[str]]
) -> list[DoctorIssue]:
    """Every player row's `Pool` cell (Edge Finder, Board, Player Pool) must hold the formula that shows his
    EdgeRaw state. The cell is also a dropdown: if the bound Apps Script is not pasted, or an edit failed, a
    picked value overwrites the formula and then goes stale. A player row is one whose hidden `Id` cell is
    filled; a Pool cell on it that is not a formula (a typed value, or empty because a restore failed) is the
    failure."""
    pool_tab = cfg.lineups.player_pool_tab
    targets: list[tuple[str, str, str]] = [
        (EDGE_FINDER_TAB, EDGE_FINDER_POOL_COL, EDGE_FINDER_ID_COL),
        (BOARD_TAB, BOARD_LIST_POOL_COL, BOARD_ID_COL),
    ]
    header = headers_by_tab.get(pool_tab, [])
    if "Pool" in header and "Id" in header:
        targets.append((pool_tab, column_letter(header.index("Pool")), column_letter(header.index("Id"))))
    issues = []
    for tab, pool_col, id_col in targets:
        if tab not in tab_titles:
            continue
        last = client.row_count(tab)
        formulas = client.read_formula(tab, f"{pool_col}1:{pool_col}{last}")
        ids = client.read_range(tab, f"{id_col}1:{id_col}{last}")
        plain = []
        for index, row in enumerate(formulas):
            cell = str(row[0]).strip() if row else ""
            has_id = index < len(ids) and bool(ids[index]) and str(ids[index][0]).strip() != ""
            is_header = cell == "Pool" or (index < len(ids) and str(ids[index][0]).strip() == "Id")
            if has_id and not is_header and not cell.startswith("="):  # a header row has "Id" / "Pool"
                plain.append(index + 1)  # a typed value, or empty (a failed restore left it blank)
        if plain:
            first = f"{pool_col}{plain[0]}"
            issues.append(
                DoctorIssue(
                    "pool-cell-plain-value",
                    f"{tab!r}: {len(plain)} Pool cell(s) are empty or hold a typed value instead of the "
                    f"formula (first at {first}) -- the Apps Script did not put it back (is it pasted and "
                    "current? see docs/APPS_SCRIPT.md); `dfs sync` or `dfs setup build-views` rewrites them.",
                )
            )
    return issues


def run_doctor(
    client: DoctorClient, cfg: Config, *, title: str, check_title: bool = True
) -> list[DoctorIssue]:
    """Every check below only reads. Order matters for readability, not
    correctness -- later checks on a tab that's missing entirely are
    skipped rather than raising, since `_check_tabs_exist` already reports
    that failure once.

    `title` is the connected sheet's own title (`SheetsClient.describe()`'s
    first element) -- the caller already fetches it to print "Checking:
    <title>" before calling this, so it's threaded through as a plain
    argument rather than pulled a second time via a `describe()` method
    on `DoctorClient`'s own narrow interface.

    `check_title=False` skips `_check_sheet_title` -- for exactly one
    caller, `dfs week new`'s own pre-flight doctor call, which runs
    BEFORE it has renamed the fresh copy (Week 3 follow-ups, Item 1):
    the copy's current title (`Copy of Template`, or whatever Drive's
    "make a copy" dialog left it as) is expected to not parse yet at
    that point, and `week new` validates/resolves the title itself via
    `week.parse_week_from_title` directly, with its own ask-on-conflict
    logic, rather than treating that as a doctor failure. Every other
    caller (plain `dfs doctor`) leaves this at the default and gets the
    real check."""
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
    if check_title:
        issues += _check_sheet_title(title)
    issues += _check_tabs_exist(cfg, tab_titles)
    issues += _check_instructions_drift(client, tab_titles)
    issues += _check_edge_header(cfg, tab_titles, headers_by_tab)
    issues += _check_linked_edge_columns(cfg, tab_titles, headers_by_tab)
    issues += _check_lineups_header_repeats(client, cfg, tab_titles)
    issues += _check_bankroll_headers(client, cfg, tab_titles)
    issues += _check_lineup_count_cell(client, cfg, tab_titles)
    issues += _check_formula_ranges(client, cfg, headers_by_tab)
    issues += _check_empty_guards(client, cfg, tab_titles)
    issues += _check_pct_helpers(client, cfg, tab_titles, headers_by_tab)
    issues += _check_pool_cells_hold_the_formula(client, cfg, tab_titles, headers_by_tab)
    issues += _check_column_visibility(client, cfg, tab_titles, headers_by_tab)
    return issues
