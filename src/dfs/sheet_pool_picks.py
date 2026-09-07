"""Task 4.2 -- the "Pool Picks" tab: a second, additive way to add a
player to Player Pool, for when typing a name is faster than scrolling
743 EdgeRaw rows to find and tick a checkbox (Task 4.1's filter view is
the other way -- see `sheet_filters.py`). Nothing about EdgeRaw or its
Pool tick column changes; Player Pool's blocks read the UNION of both
sources (see `sheet_pool_formulas.py`), and `UNIQUE` there dedupes a
player who ends up both ticked and typed.

Column A (the only typed column here) gets a live type-ahead search box
against EdgeRaw's own Name column (`ONE_OF_RANGE`, non-strict -- a name
that doesn't match yet, e.g. a bye-week/late-add player, stays editable
rather than getting locked out). Columns B-I are read-only VLOOKUPs
against EdgeRaw so a pick can be sanity-checked without leaving the tab;
column J states plainly whether the typed name actually matched this
week's slate.

Fix 3.1: row 1 is a plain-text title explaining the tab in place (this
tab showed up with no explanation the first time it shipped), so the
real header moves to row 2 and the typed rows to row 3 on -- a real,
one-time structural shift, not just a style change. This is exactly the
class of change CLAUDE.md's central hazard is about (a hardcoded row
number elsewhere silently pointing at the wrong row after a shift), so
every row constant lives here and `sheet_pool_formulas.py`'s cross-tab
range constants are derived from `FIRST_DATA_ROW`/`LAST_ROW`, not
retyped -- see CONTRIBUTING.md's changelog for this shift.
"""

from __future__ import annotations

from dfs.derived import EDGE_COLUMNS, EDGE_DATA_OFFSET
from dfs.sheet_links import edge_lookup_formula
from dfs.sheet_style import (
    BUILDER_WIDTHS,
    FLAG_CHIPS,
    HEADER_FMT,
    INPUT_BG,
    TITLE_FMT,
    apply_field_formats,
)
from dfs.sheets import SheetsClient, column_letter

POOL_PICKS_TAB = "Pool Picks"

_TITLE_ROW = 1
_TITLE = (
    "POOL PICKS -- click a cell in column A (row 3 down) and start typing a player name. "
    "They'll be added to your Player Pool."
)
HEADER_ROW = 2
FIRST_DATA_ROW = 3

# Rows 3-102: 100 typed picks -- generous headroom, matching the "costs
# nothing to provision past the real data" reasoning EDGE_ROWS/
# POOL_RAW_ROWS already use elsewhere in this codebase. (+1 vs. the
# pre-Fix-3.1 layout's 101, to keep the same 100-row capacity now that
# row 1 is a title instead of the header.)
LAST_ROW = 102

_HEADER = ["Player", "Pos", "Team", "Salary", "Pts", "Ceil", "CeilVal", "Leverage", "Flag", "Status"]

# Column B onward, in the order they appear in _HEADER -- each is an
# EDGE_COLUMNS name looked up by `sheet_links.edge_lookup_formula`, which
# already handles the VLOOKUP range/index math (and IFNA-wrapping where a
# field can legitimately be blank) the exact same way the EdgeRaw-linked
# block on Player Pool/Lineups does.
_LOOKUP_FIELDS = ["Position", "Team", "Salary", "ProjPts", "Ceiling", "CeilVal", "Leverage", "Flag"]

_edge_name_col = column_letter(EDGE_COLUMNS.index("Name") + EDGE_DATA_OFFSET)


def _status_formula(row: int, edge_tab: str) -> str:
    return (
        f'=IF($A{row}="","",IF(ISNA(MATCH($A{row},{edge_tab}!${_edge_name_col}:${_edge_name_col},0)),'
        f'"NOT ON SLATE","added"))'
    )


def _row_formulas(row: int, edge_tab: str) -> list[str]:
    cells = []
    for field in _LOOKUP_FIELDS:
        lookup = edge_lookup_formula(row, edge_tab, field)[1:]  # strip the formula's own leading "="
        cells.append(f'=IF($A{row}="","",{lookup})')
    cells.append(_status_formula(row, edge_tab))
    return cells


def create_pool_picks_tab(client: SheetsClient, *, edge_tab: str, tab: str = POOL_PICKS_TAB) -> str:
    """Additive and idempotent: (re)writes the title row, the header, and
    every formula column (B-J), and re-applies the Name-column validation/
    styling, but never touches column A's typed values -- a re-run must
    not erase a name Sam already picked. Safe to call from `dfs setup
    polish` on every run, the same discipline as `add_pool_deck`/
    `polish_edge`.
    """
    title_row = [_TITLE] + [""] * (len(_HEADER) - 1)
    if not client.tab_exists(tab):
        client.write_tab(tab, [title_row, _HEADER])
    else:
        # Full A:J range, not just A1 -- a sheet still on the pre-Fix-3.1
        # layout (title row didn't exist; row 1 WAS the header) has old
        # header text sitting in B1:J1 that a single-cell A1 write would
        # leave behind, looking like a second, stray header under the title.
        client.update_range(tab, f"A{_TITLE_ROW}:J{_TITLE_ROW}", [title_row])
        client.update_range(tab, f"A{HEADER_ROW}:J{HEADER_ROW}", [_HEADER])

    rows = [_row_formulas(row, edge_tab) for row in range(FIRST_DATA_ROW, LAST_ROW + 1)]
    client.update_range(tab, f"B{FIRST_DATA_ROW}:J{LAST_ROW}", rows)

    client.set_range_dropdown_validation(
        tab, f"A{FIRST_DATA_ROW}:A{LAST_ROW}", source=f"{edge_tab}!${_edge_name_col}$2:${_edge_name_col}"
    )
    client.format_range(tab, f"A{FIRST_DATA_ROW}:A{LAST_ROW}", {"backgroundColor": INPUT_BG})
    client.format_range(tab, f"A{_TITLE_ROW}", TITLE_FMT)
    client.format_range(tab, f"A{HEADER_ROW}:J{HEADER_ROW}", HEADER_FMT)
    client.freeze(tab, rows=HEADER_ROW)

    widths = {column_letter(i): BUILDER_WIDTHS.get(name, 90) for i, name in enumerate(_HEADER)}
    widths["A"] = 165
    client.set_column_widths(tab, widths)
    applied = apply_field_formats(client, tab, _HEADER, header_row=HEADER_ROW, last_row=LAST_ROW)

    flag_col = column_letter(_HEADER.index("Flag"))
    flag_range = f"{flag_col}{FIRST_DATA_ROW}:{flag_col}{LAST_ROW}"
    client.clear_conditional_formats(tab, column=flag_col)
    client.format_range(tab, flag_range, {"horizontalAlignment": "CENTER"})
    for text, fmt in FLAG_CHIPS.items():
        client.add_boolean_rule(tab, flag_range, condition_type="TEXT_EQ", values=[text], fmt=fmt)

    return (
        f"{tab}: title + header + formulas across {LAST_ROW - FIRST_DATA_ROW + 1} row(s), "
        f"column A validated against {edge_tab}, {applied} column(s) number-formatted, Flag chipped"
    )
