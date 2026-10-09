"""The formulas behind a row's `Pool`, `Do` and `↗` cells, shared by the Edge Finder and the Board.

A player row on those tabs carries his DraftKings `Id` in a hidden cell; every formula finds him on EdgeRaw
by that `Id`, never by name. `Pool` is the live pool state (EdgeRaw's own tick: blank, Cash, GPP or Both),
`Do` is the row's verb unless he is already pooled, and `↗` links to his EdgeRaw row. The `Pool` cell is also
the control: it carries a Cash / GPP / Both dropdown, and the bound Apps Script (`apps_script/Code.gs`) writes
EdgeRaw's `Pool` cell for whatever is picked and puts the formula back, so every `Pool` cell for that player
follows at once.
"""

from __future__ import annotations

from dfs.derived import edge_sheet_letter
from dfs.sources.edge import (  # noqa: F401 - POOL_HEADER re-exported
    POOL_COLUMN,
    POOL_HEADER,
    POOL_TYPE_OPTIONS,
)

POOL_TAB = "Player Pool"
POOL_OPTIONS = [option for option in POOL_TYPE_OPTIONS if option]  # the Pool dropdown; Code.gs POOL_VALUES
POOL_COLUMN_WIDTH = 70  # px: "Cash" / "Both" and the dropdown arrow never clip


def edge_letter(name: str) -> str:
    """EdgeRaw's absolute column letter for a named column (`derived.edge_sheet_letter`)."""
    return edge_sheet_letter(name)


def quote(tab: str) -> str:
    return f"'{tab}'" if " " in tab else tab


def _match(row: int, edge_tab: str, id_col: str) -> str:
    i = edge_letter("Id")
    return f"MATCH(${id_col}{row},{edge_tab}!${i}:${i},0)"


def pool_formula(row: int, edge_tab: str, *, id_col: str) -> str:
    """The player's pool state: EdgeRaw's own Pool tick (Cash / GPP / Both) found by the `Id` in column
    `id_col` of this row, blank when the row has no Id or EdgeRaw does not have him. The text is the one
    `poolFormula` in `apps_script/Code.gs` writes back after an edit (`tests/test_apps_script.py` pins them
    equal)."""
    found = f"INDEX({edge_tab}!${POOL_COLUMN}:${POOL_COLUMN},{_match(row, edge_tab, id_col)})"
    return f'=IF(${id_col}{row}="","",IFERROR({found},""))'


def do_formula(row: int, verb: str, *, pool_col: str) -> str:
    """The row's verb, unless the player is already pooled: then "In pool (Cash)" (live, no sync needed)."""
    safe = verb.replace('"', '""')
    return f'=IF(${pool_col}{row}<>"","In pool ("&${pool_col}{row}&")","{safe}")'


def link_formula(row: int, edge_tab: str, gid: int, *, id_col: str) -> str:
    """A same-spreadsheet link to this player's row on EdgeRaw, found by his Id."""
    n = edge_letter("Name")
    target = f'"#gid={gid}&range={n}"&{_match(row, edge_tab, id_col)}'
    return f'=IFERROR(HYPERLINK({target},"↗"),"-")'
