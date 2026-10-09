"""The "add a player" control row at the top of Player Pool.

One control row pinned above Player Pool's header: a type dropdown in the `Pool` column (Cash / GPP / Both,
default Both), the label "Add a player" in the `Name` column, and the input box beside it, a live search box
against EdgeRaw's Name column. Picking a name sets EdgeRaw's `Pool` to the type and clears the box: the bound
Apps Script (`apps_script/Code.gs`, `handleAddPlayer_`) does it the moment the cell is edited, and `dfs sync`
does the same for a name left in the box (`add_typed_player_to_pool`) as a safety net, since an API write
never fires the script. Player Pool's Name formulas read only EdgeRaw's `Pool`; the box feeds nothing else.

A hidden helper in the `Id` column of this row holds what the typed text resolves to ("kenneth walker" ->
"Kenneth Walker III", `sheet_names.resolve_name_expr`) so the script can use the sheet's own name resolver
rather than a second copy of it.

The row itself is a real, one-time `insertDimension` (row 1 becomes the control, the real header moves to
row 2: see `weekly_reset.PLAYER_POOL_NAME_BLOCKS` / `PLAYER_POOL_HEADER_ROW`), so it must run at most once per
sheet. State is detected from what is on the sheet (a cell in row 1 already reads "Add a player" means
"already migrated", wherever a column move has put it), so this is safe to call from `dfs setup polish` on
every run.
"""

from __future__ import annotations

from dfs.derived import EDGE_COLUMNS, EDGE_DATA_OFFSET
from dfs.sheet_columns import PLAYER_POOL_COLUMN_ORDER
from dfs.sheet_names import resolve_name_expr, resolve_typed_name
from dfs.sheet_pool_cells import POOL_OPTIONS
from dfs.sheet_pool_formulas import write_pool_formulas
from dfs.sheet_reorder import remove_header_columns
from dfs.sheet_style import INPUT_BG
from dfs.sheets import SheetsClient, column_letter
from dfs.sources.edge import POOL_COLUMN
from dfs.weekly_reset import PLAYER_POOL_CONTROL_ROW, PLAYER_POOL_HEADER_ROW

LABEL_TEXT = "Add a player"  # apps_script/Code.gs ADD_LABEL
DEFAULT_TYPE = "Both"  # apps_script/Code.gs ADD_DEFAULT_TYPE
TYPE_NOTE = "Pool type for the player you add: Cash, GPP or Both."
_NORMAL_ROW1_FORMAT = {
    "backgroundColor": {"red": 1, "green": 1, "blue": 1},
    "textFormat": {"foregroundColor": {"red": 0, "green": 0, "blue": 0}, "bold": False},
}

_edge_name_col = column_letter(EDGE_COLUMNS.index("Name") + EDGE_DATA_OFFSET)


def control_cells(header: list[str] | None = None) -> dict[str, str]:
    """A1 addresses of the control row's cells, found from Player Pool's header (`Name` and `Id` by text); the
    designed order is the fallback for a header not read yet. `type` is the Pool column (one left of Name),
    `label` the Name column, `input` the box (one right of Name), `resolved` the hidden helper in `Id`."""
    names = header if header else PLAYER_POOL_COLUMN_ORDER
    name_index = names.index("Name") if "Name" in names else PLAYER_POOL_COLUMN_ORDER.index("Name")
    row = PLAYER_POOL_CONTROL_ROW
    cells = {
        "type": f"{column_letter(max(name_index - 1, 0))}{row}",
        "label": f"{column_letter(name_index)}{row}",
        "input": f"{column_letter(name_index + 1)}{row}",
    }
    if "Id" in names:
        cells["resolved"] = f"{column_letter(names.index('Id'))}{row}"
    return cells


def _read_header(client: SheetsClient, player_pool_tab: str) -> list[str]:
    rows = client.read_range(player_pool_tab, f"A{PLAYER_POOL_HEADER_ROW}:{PLAYER_POOL_HEADER_ROW}")
    return list(rows[0]) if rows else []


def _already_migrated(client: SheetsClient, player_pool_tab: str) -> bool:
    """True when some cell of row 1 reads the control label (a column move may have shifted it from A)."""
    values = client.read_range(player_pool_tab, f"A{PLAYER_POOL_CONTROL_ROW}:{PLAYER_POOL_CONTROL_ROW}")
    return bool(values and LABEL_TEXT in values[0])


def ensure_pool_control_row(client: SheetsClient, player_pool_tab: str, edge_tab: str) -> str:
    """Idempotent: inserts the control row exactly once (detected via `_already_migrated`), then
    unconditionally re-applies the label, the type dropdown, the input validation and the formatting, the same
    "structural step runs once, everything else reruns" split `sheet_pool_deck.add_pool_deck` used.

    The row-1 background/text reset runs EVERY call and covers the tab's own CURRENT width (read from the
    real header), not a hardcoded guess: a column inserted or moved later can carry a stray dark header fill
    into its own row-1 cell, and nothing else would clean it up (found live as a dark bar across row 1).
    """
    migrated = _already_migrated(client, player_pool_tab)
    if not migrated:
        client.insert_rows(player_pool_tab, at_row=PLAYER_POOL_CONTROL_ROW, count=1)

    # insertDimension inherits the row pushed below it (the real header, with its dark fill and its own data
    # validation): reset both before writing this row's own content.
    header = _read_header(client, player_pool_tab)
    last_col = column_letter(max(len(header), 26) - 1)
    control_row_range = f"A{PLAYER_POOL_CONTROL_ROW}:{last_col}{PLAYER_POOL_CONTROL_ROW}"
    client.format_range(player_pool_tab, control_row_range, _NORMAL_ROW1_FORMAT)
    client.clear_data_validation(player_pool_tab, control_row_range)

    cells = control_cells(header)
    client.update_range(player_pool_tab, cells["label"], [[LABEL_TEXT]])
    client.update_range(player_pool_tab, cells["type"], [[DEFAULT_TYPE]])
    client.set_dropdown_validation(player_pool_tab, cells["type"], POOL_OPTIONS)
    client.set_note(player_pool_tab, cells["type"], TYPE_NOTE)
    client.set_range_dropdown_validation(
        player_pool_tab, cells["input"], source=f"{edge_tab}!${_edge_name_col}$2:${_edge_name_col}"
    )
    client.format_range(player_pool_tab, cells["type"], {"backgroundColor": INPUT_BG})
    client.format_range(player_pool_tab, cells["input"], {"backgroundColor": INPUT_BG})
    if "resolved" in cells:
        typed = cells["input"]
        client.update_range(
            player_pool_tab,
            cells["resolved"],
            [[f'=IF({typed}="","",{resolve_name_expr(typed, edge_tab)})']],
        )

    origin = "refresh" if migrated else "inserted"
    return f"{player_pool_tab}: add-a-player control row ({origin}), input at {cells['input']}"


def add_typed_player_to_pool(client: SheetsClient, player_pool_tab: str, edge_tab: str) -> str:
    """The sync's safety net for the add-a-player box (an API write never fires the Apps Script, and the
    script may not be pasted): a name left in the box is set on EdgeRaw's `Pool` as the type beside it
    (default Both) and the box is cleared. The name is resolved to DK's spelling first. A name EdgeRaw does
    not have is left in the box, so it is not silently lost."""
    cells = control_cells(_read_header(client, player_pool_tab))
    typed_rows = client.read_range(player_pool_tab, cells["input"])
    typed = typed_rows[0][0].strip() if typed_rows and typed_rows[0] else ""
    if not typed:
        return f"{player_pool_tab}: no pending add-a-player name"

    type_rows = client.read_range(player_pool_tab, cells["type"])
    pool_type = type_rows[0][0].strip() if type_rows and type_rows[0] else ""
    if pool_type not in POOL_OPTIONS:
        pool_type = DEFAULT_TYPE

    edge_names = [
        row[0] if row else "" for row in client.read_range(edge_tab, f"{_edge_name_col}2:{_edge_name_col}")
    ]
    name = resolve_typed_name(typed, [n for n in edge_names if n])
    if name is None or name not in edge_names:
        return f"{player_pool_tab}: {typed!r} is not on {edge_tab} -- left in the box"
    edge_row = edge_names.index(name) + 2
    client.update_range(edge_tab, f"{POOL_COLUMN}{edge_row}", [[pool_type]])
    client.clear_ranges(player_pool_tab, [cells["input"]])
    return f"{player_pool_tab}: {name!r} added to the pool as {pool_type}"


ADDED_HEADER = "Added"  # the retired hidden list of typed names
ADDED_ROWS = 50  # how many rows below the header it held


def retire_added_list(client: SheetsClient, player_pool_tab: str, edge_tab: str) -> str:
    """Remove Player Pool's hidden `Added` column (the actions round retired the list: the add-a-player box
    sets EdgeRaw's `Pool` directly now). Refuses, naming them, when the list still holds names, so nothing a
    person typed is lost: those go onto EdgeRaw's `Pool` first. Otherwise the Name / Overflow / Pool
    formulas are rewritten WITHOUT the list (they used to read it, and deleting a referenced column would
    leave `#REF!`), and then the column is deleted. A tab without the column is left alone."""
    header = _read_header(client, player_pool_tab)
    if ADDED_HEADER not in header:
        return f"{player_pool_tab}: no {ADDED_HEADER!r} column -- already retired"
    col = column_letter(header.index(ADDED_HEADER))
    first = PLAYER_POOL_HEADER_ROW + 1
    names = [
        row[0].strip()
        for row in client.read_range(player_pool_tab, f"{col}{first}:{col}{first + ADDED_ROWS - 1}")
        if row and row[0].strip()
    ]
    if names:
        raise ValueError(
            f"{player_pool_tab!r} {ADDED_HEADER!r} list still holds {names}: set each on EdgeRaw's Pool "
            "(Both) first, then run this again"
        )
    write_pool_formulas(client, player_pool_tab=player_pool_tab, edge_tab=edge_tab)
    return remove_header_columns(client, player_pool_tab, [ADDED_HEADER], header_row=PLAYER_POOL_HEADER_ROW)
