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
"""

from __future__ import annotations

from dfs.derived import EDGE_COLUMNS, EDGE_DATA_OFFSET
from dfs.sheet_links import edge_lookup_formula
from dfs.sheet_style import BUILDER_WIDTHS, FLAG_CHIPS, HEADER_FMT, INPUT_BG, apply_field_formats
from dfs.sheets import SheetsClient, column_letter

POOL_PICKS_TAB = "Pool Picks"

# Rows 2-101: 100 typed picks -- generous headroom, matching the "costs
# nothing to provision past the real data" reasoning EDGE_ROWS/
# POOL_RAW_ROWS already use elsewhere in this codebase.
_LAST_ROW = 101

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
    """Additive and idempotent: (re)writes the header and every formula
    column (B-J), and re-applies the Name-column validation/styling, but
    never touches column A's typed values -- a re-run must not erase a
    name Sam already picked. Safe to call from `dfs setup polish` on
    every run, the same discipline as `add_pool_deck`/`polish_edge`.
    """
    if not client.tab_exists(tab):
        client.write_tab(tab, [_HEADER])
    else:
        client.update_range(tab, "A1:J1", [_HEADER])

    rows = [_row_formulas(row, edge_tab) for row in range(2, _LAST_ROW + 1)]
    client.update_range(tab, f"B2:J{_LAST_ROW}", rows)

    client.set_range_dropdown_validation(
        tab, f"A2:A{_LAST_ROW}", source=f"{edge_tab}!${_edge_name_col}$2:${_edge_name_col}"
    )
    client.format_range(tab, f"A2:A{_LAST_ROW}", {"backgroundColor": INPUT_BG})
    client.format_range(tab, "A1:J1", HEADER_FMT)
    client.freeze(tab, rows=1)

    widths = {column_letter(i): BUILDER_WIDTHS.get(name, 90) for i, name in enumerate(_HEADER)}
    widths["A"] = 165
    client.set_column_widths(tab, widths)
    applied = apply_field_formats(client, tab, _HEADER, header_row=1, last_row=_LAST_ROW)

    flag_col = column_letter(_HEADER.index("Flag"))
    client.clear_conditional_formats(tab, column=flag_col)
    client.format_range(tab, f"{flag_col}2:{flag_col}{_LAST_ROW}", {"horizontalAlignment": "CENTER"})
    for text, fmt in FLAG_CHIPS.items():
        client.add_boolean_rule(
            tab, f"{flag_col}2:{flag_col}{_LAST_ROW}", condition_type="TEXT_EQ", values=[text], fmt=fmt
        )

    return (
        f"{tab}: header + formulas across {_LAST_ROW - 1} row(s), "
        f"column A validated against {edge_tab}, {applied} column(s) number-formatted, Flag chipped"
    )
