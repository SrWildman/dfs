"""One-time structural change, superseding the 7-row "Bench" this codebase
shipped first (`sheet_bench.py`, now removed -- see CONTRIBUTING.md's
changelog): 14 frozen rows at the top of `Lineups` holding a sortable,
filterable window into `Player Pool` -- full metric columns (Salary, Pts,
Ceil, Val, CeilVal, Leverage, Flag, ...), not just names.

The Bench showed names only. Picking a player needs the numbers next to
the name -- that's exactly why split-screen was the workaround in the
first place, and a names-only pinned list didn't remove the need for it.
This "deck" is a real decision surface: pick a position and a sort field,
set a starting rank, and the ten rows below show that slice of the pool
with every column Lineups itself already has, so the same eye that reads
a lineup can read the pool immediately above it.

Structure:
  Row 1    Controls: B1 position filter (dropdown), D1 sort field
           (dropdown), F1 window start (number), G1 a hidden helper
           (maps D1's label to a PoolSort column number for row 3.4's
           formulas), I1 a "X in pool, showing N-M" readout.
  Row 2    Blank.
  Row 3    Player Pool's header row, copied verbatim.
  Rows 4-13  The 10-row window: each cell is an IFERROR(IF(INDEX(...))
           pull from PoolSort, offset by F1.
  Row 14   Blank separator.

`PoolSort` (new, hidden tab) exists because Sheets' OFFSET/INDEX need a
range reference, not an array -- "start at rank N" can't be windowed
inline against a live FILTER/SORT result, so the sorted-and-filtered pool
is materialized there first and the deck just INDEXes into it.

Migration-aware: `add_pool_deck` detects three states before touching
anything --
  "deck"  A3 already reads "Name" (Player Pool's header text): the deck
          is already built. No-op.
  "bench" A1 holds the old Bench's title text: 7 rows already inserted.
          Clears that content (not the rows) and inserts 7 MORE, bringing
          the total shift to +14 without re-running from scratch.
  "fresh" Neither: insert 14 rows outright.
All three converge on the same 14 blank rows to fill, so the fill/freeze/
row-height/validation steps run identically regardless of which path got
there.
"""

from __future__ import annotations

from dfs.sheets import SheetsClient, column_letter

DECK_ROWS = 14
POOL_SORT_TAB = "PoolSort"

# The old Bench's exact title text (sheet_bench.py, removed) -- kept here
# only to recognize a sheet still in that state during migration.
_OLD_BENCH_TITLE = "BENCH — your Player Pool, by position. Lineup blocks start at row 8."
_OLD_BENCH_ROWS = 7

# Player Pool / Lineups' shared column grammar (see CONTRIBUTING.md and
# sheet_links.py's own docstring on this): both tabs' A..N are the same
# hand-authored columns, Q..Z the same EdgeRaw-linked block. Deliberately
# literal, not derived -- this is the template's authored layout, the
# same class of exception style_board/style_slate_grid/etc. already
# document in sheet_style.py.
_WINDOW_COLUMNS = [*"ABCDEFGHIJKLMN", *"QRSTUVWXYZ"]  # skips O (spacer), P (% of Rstr)

_SORT_OPTIONS = ["CeilVal", "Leverage", "Pts", "Ceil", "Val", "DK Sal", "Rstr%"]
_POSITION_OPTIONS = ["ALL", "QB", "RB", "WR", "TE", "DST"]
_WINDOW_SIZE = 10

# G1's MATCH array -- Player Pool's full A..Z header text, in order. Two
# blanks at positions 15-16 (O, P) since sorting by the spacer or "% of
# Rstr" is meaningless in a deck window that never populates either.
_MATCH_ARRAY = (
    '{"Name","Pos.","Team","DK Sal","O/U","Spread","Team Implied","Opp.","Venue","OppPosRank",'
    '"Pts","Ceil","Val","Rstr%","","","CeilVal","CeilPct","Leverage","LevBasis","GameEnv",'
    '"Stadium","Roof","Wind","Avail","Flag"}'
)


def _deck_state(client: SheetsClient, lineups_tab: str) -> str:
    a3 = client.read_range(lineups_tab, "A3")
    if a3 and a3[0] and a3[0][0] == "Name":
        return "deck"
    a1 = client.read_range(lineups_tab, "A1")
    if a1 and a1[0] and a1[0][0] == _OLD_BENCH_TITLE:
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
    """`row` is the deck row (4..13); PoolSort's window start is $F$1, and
    row 4 must read PoolSort row 2 (PoolSort's first data row) when F1=1,
    hence the -3 offset (row 4 - 3 = 1, PoolSort's own header adds the
    other +1)."""
    ref = f"{POOL_SORT_TAB}!{col}:{col}"
    idx = f"$F$1+{row}-3"
    return f'=IFERROR(IF(INDEX({ref},{idx})="","",INDEX({ref},{idx})),"")'


def _write_deck_controls(client: SheetsClient, lineups_tab: str, pool_tab: str) -> None:
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
                f"MIN($F$1+9,COUNTA({POOL_SORT_TAB}!$A$2:$A$74))",
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
    client.update_range(lineups_tab, "A4:Z13", window_rows)


def add_pool_deck(client: SheetsClient, *, lineups_tab: str, pool_tab: str) -> str:
    state = _deck_state(client, lineups_tab)
    if state == "deck":
        return f"{lineups_tab}: pool deck already present -- skipped"

    if state == "bench":
        client.clear_ranges(lineups_tab, ["A1:Z7"])
        client.insert_rows(lineups_tab, at_row=1, count=_OLD_BENCH_ROWS)
    else:
        client.insert_rows(lineups_tab, at_row=1, count=DECK_ROWS)

    _build_pool_sort(client, POOL_SORT_TAB, pool_tab, lineups_tab)
    _write_deck_controls(client, lineups_tab, pool_tab)

    client.freeze(lineups_tab, rows=DECK_ROWS)
    client.set_row_heights(lineups_tab, start_row=3, end_row=13, pixel_size=18)

    origin = "bench migration" if state == "bench" else "scratch"
    return f"{lineups_tab}: {DECK_ROWS}-row pool deck built from {origin}, frozen"
