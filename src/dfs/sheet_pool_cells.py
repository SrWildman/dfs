"""The formulas behind a row's `Pool`, `Do` and `↗` cells, shared by the Edge Finder and the Board.

A player row on those tabs carries his DraftKings `Id` in a hidden cell; every formula finds him on EdgeRaw
by that `Id`, never by name. `Pool` is the live pool state (EdgeRaw's own tick, or "Added" when his name is
on Player Pool's hidden `Added` list), `Do` is the row's verb unless he is already pooled, and `↗` links to
his EdgeRaw row. The bound Apps Script (`apps_script/Code.gs`) writes EdgeRaw's `Pool` cell for the `Set`
dropdown, and these formulas pick the change up at once.
"""

from __future__ import annotations

from dfs.derived import EDGE_COLUMNS, EDGE_DATA_OFFSET
from dfs.sheets import SheetsClient, column_letter
from dfs.sources.edge import POOL_COLUMN
from dfs.weekly_reset import (
    PLAYER_POOL_ADDED_NAMES_HEADER,
    PLAYER_POOL_ADDED_NAMES_ROWS,
    PLAYER_POOL_HEADER_ROW,
)

POOL_TAB = "Player Pool"
SET_OPTIONS = ["Cash", "GPP", "Both", "Remove"]  # the `Set` dropdown; apps_script/Code.gs SET_VALUES


def edge_letter(name: str) -> str:
    """EdgeRaw's absolute column letter for a named column (its `EDGE_COLUMNS` index plus the Pool offset)."""
    return column_letter(EDGE_COLUMNS.index(name) + EDGE_DATA_OFFSET)


def quote(tab: str) -> str:
    return f"'{tab}'" if " " in tab else tab


def _match(row: int, edge_tab: str, id_col: str) -> str:
    i = edge_letter("Id")
    return f"MATCH(${id_col}{row},{edge_tab}!${i}:${i},0)"


def pool_formula(
    row: int, edge_tab: str, added_range: str | None, *, id_col: str, name_col: str = "A"
) -> str:
    """The player's pool state: EdgeRaw's own Pool tick (Cash / GPP / Both) found by his Id, or "Added" when
    his name (in `name_col` of this row) is on Player Pool's `Added` list, else blank. A blank Id gives a
    blank cell."""
    found = f"INDEX({edge_tab}!${POOL_COLUMN}:${POOL_COLUMN},{_match(row, edge_tab, id_col)})"
    added = f'IF(COUNTIF({added_range},${name_col}{row})>0,"Added","")' if added_range else '""'
    return f'=IF(${id_col}{row}="","",IFERROR(IF({found}<>"",{found},{added}),""))'


def do_formula(row: int, verb: str, *, pool_col: str) -> str:
    """The row's verb, unless the player is already pooled: then "In pool (Cash)" (live, no sync needed)."""
    safe = verb.replace('"', '""')
    return f'=IF(${pool_col}{row}<>"","In pool ("&${pool_col}{row}&")","{safe}")'


def link_formula(row: int, edge_tab: str, gid: int, *, id_col: str) -> str:
    """A same-spreadsheet link to this player's row on EdgeRaw, found by his Id."""
    n = edge_letter("Name")
    target = f'"#gid={gid}&range={n}"&{_match(row, edge_tab, id_col)}'
    return f'=IFERROR(HYPERLINK({target},"↗"),"-")'


def added_names_range(client: SheetsClient, pool_tab: str = POOL_TAB) -> str | None:
    """Player Pool's hidden `Added` list as an absolute range, found by header text; None when the tab or the
    column does not exist (then the Pool cell reads EdgeRaw alone)."""
    if not client.tab_exists(pool_tab):
        return None
    header_rows = client.read_range(pool_tab, f"A{PLAYER_POOL_HEADER_ROW}:{PLAYER_POOL_HEADER_ROW}")
    header = header_rows[0] if header_rows else []
    if PLAYER_POOL_ADDED_NAMES_HEADER not in header:
        return None
    col = column_letter(header.index(PLAYER_POOL_ADDED_NAMES_HEADER))
    first = PLAYER_POOL_HEADER_ROW + 1
    last = PLAYER_POOL_HEADER_ROW + PLAYER_POOL_ADDED_NAMES_ROWS
    return f"{quote(pool_tab)}!${col}${first}:${col}${last}"
