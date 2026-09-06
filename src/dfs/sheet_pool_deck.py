"""One-time structural change, superseding the 7-row "Bench" this codebase
shipped first (`sheet_bench.py`, now removed -- see CONTRIBUTING.md's
changelog): frozen rows at the top of `Lineups` holding a sortable,
filterable window into `Player Pool` -- full metric columns (Salary, Pts,
Ceil, Val, CeilVal, Leverage, Flag, ...), not just names.

The Bench showed names only. Picking a player needs the numbers next to
the name -- that's exactly why split-screen was the workaround in the
first place, and a names-only pinned list didn't remove the need for it.
This "deck" is a real decision surface: pick a position and a sort field,
set a starting rank, and the window below shows that slice of the pool
with every column Lineups itself already has, so the same eye that reads
a lineup can read the pool immediately above it.

Structure (DECK_ROWS total, frozen):
  Row 1              Controls: B1 position filter (dropdown), D1 sort
                     field (dropdown), F1 window start (number), G1 a
                     hidden helper (maps D1's label to a PoolSort column
                     number for the window formulas below), I1 a
                     "X in pool, showing N-M" readout.
  Row 2              Blank.
  Row 3              Player Pool's header row, copied verbatim.
  Rows 4..3+WINDOW_SIZE  The window: each cell is an
                     IFERROR(IF(INDEX(...))) pull from PoolSort, offset
                     by F1.
  Row DECK_ROWS      Blank separator.

First shipped as a 14-row / 10-row-window design; shrunk to 10 rows / a
6-row window after a live check on a 16" MacBook showed two full lineup
blocks didn't fit below the 14-row frozen zone (see CONTRIBUTING.md's
changelog) -- `DECK_ROWS`/`_WINDOW_SIZE` are the two numbers that must
move together if this is ever revisited; nothing else in this module
assumes a specific size.

`PoolSort` (new, hidden tab) exists because Sheets' OFFSET/INDEX need a
range reference, not an array -- "start at rank N" can't be windowed
inline against a live FILTER/SORT result, so the sorted-and-filtered pool
is materialized there first and the deck just INDEXes into it.

Migration-aware: `add_pool_deck` detects the sheet's current state before
touching anything --
  "deck"    A{DECK_ROWS+1} already reads "Name": already at the current
            size. No-op.
  "deck14"  A15 reads "Name" but A{DECK_ROWS+1} doesn't: built at the
            original 14-row size. Deletes the now-unwanted tail of the
            window (rows 10-13) rather than tearing down and rebuilding
            -- the surviving rows 1-9 already hold correct content (the
            first 6 window rows show the same ranks either way).
  "bench"   A1 holds the old Bench's title text: 7 rows already inserted.
            Clears that content (not the rows) and inserts
            `DECK_ROWS - _OLD_BENCH_ROWS` more.
  "fresh"   None of the above: insert `DECK_ROWS` rows outright.
Every path ends by (re)writing controls/PoolSort, freezing, fixing row
heights, and resetting the deck zone's background -- see
`_reset_deck_background`'s docstring for why that last step is not
optional.
"""

from __future__ import annotations

from dfs.sheets import SheetsClient, column_letter

DECK_ROWS = 10
_WINDOW_SIZE = 6
POOL_SORT_TAB = "PoolSort"

# The old Bench's exact title text (sheet_bench.py, removed) -- kept here
# only to recognize a sheet still in that state during migration.
_OLD_BENCH_TITLE = "BENCH — your Player Pool, by position. Lineup blocks start at row 8."
_OLD_BENCH_ROWS = 7

# The original 14-row deck's header row -- a sheet still at that size is
# recognized by this, and shrunk rather than rebuilt (see module
# docstring's "deck14" state).
_OLD_DECK14_HEADER_ROW = 15
_OLD_DECK14_ROWS = 14

# Player Pool / Lineups' shared column grammar (see CONTRIBUTING.md and
# sheet_links.py's own docstring on this): both tabs' A..N are the same
# hand-authored columns, Q..Z the same EdgeRaw-linked block. Deliberately
# literal, not derived -- this is the template's authored layout, the
# same class of exception style_board/style_slate_grid/etc. already
# document in sheet_style.py.
_WINDOW_COLUMNS = [*"ABCDEFGHIJKLMN", *"QRSTUVWXYZ"]  # skips O (spacer), P (% of Rstr)

_SORT_OPTIONS = ["CeilVal", "Leverage", "Pts", "Ceil", "Val", "DK Sal", "Rstr%"]
_POSITION_OPTIONS = ["ALL", "QB", "RB", "WR", "TE", "DST"]

# G1's MATCH array -- Player Pool's full A..Z header text, in order. Two
# blanks at positions 15-16 (O, P) since sorting by the spacer or "% of
# Rstr" is meaningless in a deck window that never populates either.
_MATCH_ARRAY = (
    '{"Name","Pos.","Team","DK Sal","O/U","Spread","Team Implied","Opp.","Venue","OppPosRank",'
    '"Pts","Ceil","Val","Rstr%","","","CeilVal","CeilPct","Leverage","LevBasis","GameEnv",'
    '"Stadium","Roof","Wind","Avail","Flag"}'
)


def _cell_equals(client: SheetsClient, tab: str, a1: str, expected: str) -> bool:
    values = client.read_range(tab, a1)
    return bool(values and values[0] and values[0][0] == expected)


def _deck_state(client: SheetsClient, lineups_tab: str) -> str:
    if _cell_equals(client, lineups_tab, f"A{DECK_ROWS + 1}", "Name"):
        return "deck"
    if _cell_equals(client, lineups_tab, f"A{_OLD_DECK14_HEADER_ROW}", "Name"):
        return "deck14"
    if _cell_equals(client, lineups_tab, "A1", _OLD_BENCH_TITLE):
        return "bench"
    return "fresh"


def _build_pool_sort(client: SheetsClient, pool_sort_tab: str, pool_tab: str, lineups_tab: str) -> None:
    header = client.read_range(pool_tab, "A1:Z1")
    header_row = header[0] if header else []
    formula = (
        f"=IFERROR(SORT(FILTER('{pool_tab}'!$A$2:$Z$74,"
        f"'{pool_tab}'!$A$2:$A$74<>\"\","
        f"({lineups_tab}!$B$1=\"ALL\")+('{pool_tab}'!$B$2:$B$74={lineups_tab}!$B$1)),"
        f'{lineups_tab}!$G$1,FALSE),"")'
    )
    client.write_tab(pool_sort_tab, [header_row, [formula]])
    client.set_tab_properties(pool_sort_tab, hidden=True)


def _window_formula(col: str, row: int) -> str:
    """`row` is the deck row (4..3+_WINDOW_SIZE); PoolSort's window start
    is $F$1, and row 4 must read PoolSort row 2 (PoolSort's first data
    row) when F1=1, hence the -3 offset (row 4 - 3 = 1, PoolSort's own
    header adds the other +1)."""
    ref = f"{POOL_SORT_TAB}!{col}:{col}"
    idx = f"$F$1+{row}-3"
    return f'=IFERROR(IF(INDEX({ref},{idx})="","",INDEX({ref},{idx})),"")'


def _write_deck_controls(client: SheetsClient, lineups_tab: str, pool_tab: str) -> None:
    last_rank_offset = _WINDOW_SIZE - 1
    client.update_range(
        lineups_tab,
        "A1:I1",
        [
            [
                "Position",
                "QB",
                "Sort by",
                "CeilVal",
                "Start at",
                1,
                f"=MATCH($D$1,{_MATCH_ARRAY},0)",
                "",
                f'=COUNTA({POOL_SORT_TAB}!$A$2:$A$74)&" in pool  ·  showing "&$F$1&"-"&'
                f"MIN($F$1+{last_rank_offset},COUNTA({POOL_SORT_TAB}!$A$2:$A$74))",
            ]
        ],
    )
    client.set_dropdown_validation(lineups_tab, "B1", _POSITION_OPTIONS)
    client.set_dropdown_validation(lineups_tab, "D1", _SORT_OPTIONS)
    # G1 reads as blank: its formula is load-bearing for the window
    # formulas below (never delete it), but it's plumbing, not something
    # to look at -- white-on-white so it's invisible without being hidden.
    white = {"red": 1, "green": 1, "blue": 1}
    client.format_range(lineups_tab, "G1", {"textFormat": {"foregroundColor": white}})

    header = client.read_range(pool_tab, "A1:Z1")
    header_row = header[0] if header else []
    client.update_range(lineups_tab, "A3:Z3", [header_row])

    window_rows = []
    for row in range(4, 4 + _WINDOW_SIZE):
        cells_by_col = {col: _window_formula(col, row) for col in _WINDOW_COLUMNS}
        window_rows.append([cells_by_col.get(column_letter(i), "") for i in range(26)])
    last_window_row = 3 + _WINDOW_SIZE
    client.update_range(lineups_tab, f"A4:Z{last_window_row}", window_rows)


def _reset_deck_background(client: SheetsClient, lineups_tab: str) -> None:
    """`insert_rows` at row 1 has no way to insert "blank" rows -- Sheets'
    `inheritFromBefore=False` means the new rows copy the formatting of
    whatever row is now pushed below them, which for an insert-at-top is
    the tab's own (dark-filled) header. Every row this module has ever
    inserted picked up that fill as a result; reset it explicitly rather
    than leaving a solid dark block where the deck should look blank. See
    `SheetsClient.insert_rows`'s docstring and CONTRIBUTING.md's
    changelog -- found via a live screenshot, not caught in review."""
    white = {"backgroundColor": {"red": 1, "green": 1, "blue": 1}}
    client.format_range(lineups_tab, f"A1:Z{DECK_ROWS}", white)


def add_pool_deck(client: SheetsClient, *, lineups_tab: str, pool_tab: str) -> str:
    state = _deck_state(client, lineups_tab)
    if state == "deck":
        return f"{lineups_tab}: pool deck already present -- skipped"

    if state == "deck14":
        # Rows 1-9 already hold correct content at this size (the first
        # _WINDOW_SIZE window rows show the same ranks regardless of how
        # many more used to follow them) -- just remove the now-unwanted
        # tail instead of tearing the whole thing down. The rows to
        # remove are the old window rows past the new window's end (row
        # 3 + _WINDOW_SIZE): deleting them shifts the old blank separator
        # row up to become the new row DECK_ROWS, exactly where it needs
        # to be. (Off-by-one here once shipped for real: deleting starting
        # at DECK_ROWS + 1 instead of 3 + _WINDOW_SIZE + 1 left a stray
        # leftover window formula sitting in the "blank separator" row
        # instead of an actual blank -- see CONTRIBUTING.md's changelog.)
        delete_start = 3 + _WINDOW_SIZE + 1
        client.delete_rows(lineups_tab, at_row=delete_start, count=_OLD_DECK14_ROWS - DECK_ROWS)
    elif state == "bench":
        client.clear_ranges(lineups_tab, ["A1:Z7"])
        client.insert_rows(lineups_tab, at_row=1, count=DECK_ROWS - _OLD_BENCH_ROWS)
    else:
        client.insert_rows(lineups_tab, at_row=1, count=DECK_ROWS)

    if state != "deck14":
        _build_pool_sort(client, POOL_SORT_TAB, pool_tab, lineups_tab)
        _write_deck_controls(client, lineups_tab, pool_tab)

    client.freeze(lineups_tab, rows=DECK_ROWS)
    client.set_row_heights(lineups_tab, start_row=3, end_row=3 + _WINDOW_SIZE, pixel_size=18)
    _reset_deck_background(client, lineups_tab)

    origin = {"bench": "bench migration", "deck14": "14-row deck shrink"}.get(state, "scratch")
    return f"{lineups_tab}: {DECK_ROWS}-row pool deck built from {origin}, frozen"
