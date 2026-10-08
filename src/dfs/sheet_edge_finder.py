"""Write the `Edge Finder` tab (`edge_finder_tab.build_layout`) and the Board's "This week's edges" panel.

The tab is rebuilt from scratch by every write (nothing on it is typed): its values come from the sync; its
`Pool`, `Do` and `↗` cells are live formulas keyed on the hidden `Id` cell (the player's DraftKings id,
never his name), and its `Set` cells are dropdowns the bound Apps Script (`apps_script/Code.gs`) reads.
Formatting is static for section/header/note rows; the probability colour is a gradient per position block,
and the muting of thin samples is written with the rows.

**Open and shut groups survive a rewrite.** Before anything is cleared, `read_group_state` reads the tab's row
groups and remembers each one's collapsed flag by its key (hidden column `Q`, on the row above the group);
after the rewrite the same keys get the same state back, and a key never seen before gets its default
(sections open, overflow and team groups shut).
"""

from __future__ import annotations

import contextlib

from dfs import edge_finder_tab as eft
from dfs.derived import EDGE_COLUMNS, EDGE_DATA_OFFSET
from dfs.sheet_color_scales import GRAD_MAX, WHITE
from dfs.sheet_style import (
    _HEADER_FMT,
    _PANEL_FMT,
    _TITLE_FMT,
    EDGE_CHIPS,
    INK_MUTED,
    INPUT_BG,
    _num,
)
from dfs.sheet_views import write_board_edges
from dfs.sheets import SheetsClient, column_letter
from dfs.sources.edge import POOL_COLUMN
from dfs.weekly_reset import (
    PLAYER_POOL_ADDED_NAMES_HEADER,
    PLAYER_POOL_ADDED_NAMES_ROWS,
    PLAYER_POOL_HEADER_ROW,
)

TAB = eft.EDGE_FINDER_TAB
POOL_TAB = "Player Pool"
WIDTHS = {
    "A": 190,
    "B": 44,
    "C": 48,
    "D": 64,
    "E": 84,
    "F": 84,
    "G": 84,
    "H": 84,
    "I": 84,
    "J": 100,
    "K": 640,
    "L": 150,
    "M": 96,
    "N": 80,
    "O": 40,
    "P": 90,
    "Q": 90,
}
HIDDEN_FIRST, HIDDEN_LAST = eft.ID_COL, eft.KEY_COL
NO_FILL = {"red": 1.0, "green": 1.0, "blue": 1.0}
VERDICT_FG = {"red": 0.60, "green": 0.38, "blue": 0.0}
ADD_FG = {"red": 0.10, "green": 0.45, "blue": 0.25}
SHEET_URL = "https://docs.google.com/spreadsheets/d/{sheet_id}/edit#gid={gid}&fvid={fvid}"


def _edge_letter(name: str) -> str:
    return column_letter(EDGE_COLUMNS.index(name) + EDGE_DATA_OFFSET)


def _name_letter() -> str:
    return _edge_letter("Name")


def _id_letter() -> str:
    return _edge_letter("Id")


def _quote(tab: str) -> str:
    return f"'{tab}'" if " " in tab else tab


def _match(row: int, edge_tab: str) -> str:
    i = _id_letter()
    return f"MATCH(${eft.ID_COL}{row},{edge_tab}!${i}:${i},0)"


def pool_formula(row: int, edge_tab: str, added_range: str | None = None) -> str:
    """The player's pool state: EdgeRaw's own Pool tick (Cash / GPP / Both) found by his Id, or "Added" when
    his name is on Player Pool's hidden `Added` list (a name typed into the add-a-player control), else
    blank. Blank
    Id, blank result."""
    found = f"INDEX({edge_tab}!${POOL_COLUMN}:${POOL_COLUMN},{_match(row, edge_tab)})"
    added = f'IF(COUNTIF({added_range},$A{row})>0,"Added","")' if added_range else '""'
    return f'=IF(${eft.ID_COL}{row}="","",IFERROR(IF({found}<>"",{found},{added}),""))'


def do_formula(row: int, verb: str) -> str:
    """The row's verb, unless the player is already pooled: then "In pool (Cash)" (live, no sync needed)."""
    safe = verb.replace('"', '""')
    return f'=IF(${eft.POOL_COL}{row}<>"","In pool ("&${eft.POOL_COL}{row}&")","{safe}")'


def link_formula(row: int, edge_tab: str, gid: int) -> str:
    """A same-spreadsheet link to this player's row on EdgeRaw, found by his Id."""
    n = _name_letter()
    target = f'"#gid={gid}&range={n}"&{_match(row, edge_tab)}'
    return f'=IFERROR(HYPERLINK({target},"↗"),"-")'


def tab_rows(layout: eft.Layout, edge_tab: str, gid: int, *, added_range: str | None = None) -> list[list]:
    """The layout's rows with the Pool, Do and link formulas filled into every player row."""
    rows = [list(r) for r in layout.rows]
    col = {name: ord(letter) - ord("A") for name, letter in (("do", eft.DO_COL), ("pool", eft.POOL_COL))}
    link = ord(eft.LINK_COL) - ord("A")
    for row in layout.player_rows:
        verb = rows[row - 1][col["do"]]
        rows[row - 1][col["pool"]] = pool_formula(row, edge_tab, added_range)
        rows[row - 1][col["do"]] = do_formula(row, str(verb))
        rows[row - 1][link] = link_formula(row, edge_tab, gid)
    return rows


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
    return f"{_quote(pool_tab)}!${col}${first}:${col}${last}"


def read_group_state(client: SheetsClient) -> dict[str, bool]:
    """Which of the tab's row groups are collapsed right now, by group key. The key of a group is the text in
    hidden column `Q` on the row above it. Empty when the tab has no groups (or does not exist yet)."""
    if not client.tab_exists(TAB):
        return {}
    groups = client.read_row_groups(TAB)
    if not groups:
        return {}
    last = max(g["start"] for g in groups)
    keys = client.read_range(TAB, f"{eft.KEY_COL}1:{eft.KEY_COL}{last}")
    state: dict[str, bool] = {}
    for g in groups:
        above = g["start"] - 2  # the row above the group, 0-based
        key = keys[above][0] if 0 <= above < len(keys) and keys[above] else ""
        if key:
            state[key] = g["collapsed"]
    return state


def group_specs(layout: eft.Layout, state: dict[str, bool]) -> list[tuple[int, int, int, bool]]:
    """The groups to create, `(first, last, depth, collapsed)`: a key seen before keeps the state Sam left it
    in; a new key gets the layout's default."""
    return [(g.first, g.last, g.depth, state.get(g.key, g.collapsed)) for g in layout.groups]


def _runs(rows: list[int]) -> list[tuple[int, int]]:
    out: list[tuple[int, int]] = []
    for row in sorted(rows):
        if out and row == out[-1][1] + 1:
            out[-1] = (out[-1][0], row)
        else:
            out.append((row, row))
    return out


def write_tab(client: SheetsClient, inputs: eft.Inputs | None, *, edge_tab: str = "EdgeRaw") -> str:
    """Write and style the whole tab. `inputs` None writes the empty-state layout (a template). The
    formatting is hundreds of small requests, sent together through `client.batched()`."""
    batched = getattr(client, "batched", None)
    with batched() if batched is not None else contextlib.nullcontext():
        return _write_tab(client, inputs, edge_tab=edge_tab)


def _all_link(client: SheetsClient, edge_tab: str, view: str) -> str | None:
    if not client.tab_exists(edge_tab):
        return None
    fvid = client.filter_view_id(edge_tab, view)
    if fvid is None:
        return None
    url = SHEET_URL.format(sheet_id=client.spreadsheet_id, gid=client.tab_gid(edge_tab), fvid=fvid)
    return f'=HYPERLINK("{url}","All ↗")'


def _write_tab(client: SheetsClient, inputs: eft.Inputs | None, *, edge_tab: str) -> str:
    layout = eft.build_layout(inputs, edge_tab_name=edge_tab)
    state = read_group_state(client)
    gid = client.tab_gid(edge_tab) if client.tab_exists(edge_tab) else 0
    rows = tab_rows(layout, edge_tab, gid, added_range=added_names_range(client))
    for row, view in layout.all_links.items():
        link = _all_link(client, edge_tab, view)
        if link:
            rows[row - 1][ord(eft.LINK_COL) - ord("A")] = link
    last_row = max(len(rows), 40)

    if client.tab_exists(TAB):
        client.ensure_row_capacity(TAB, last_row + 20)
        client.clear_row_groups(TAB)
        client.unhide_rows(TAB, 1, last_row + 20)  # deleting a collapsed group leaves its rows hidden
    client.write_tab(TAB, rows)
    client.clear_conditional_formats(TAB)
    client.format_range(
        TAB,
        f"A1:{eft.LAST_COLUMN}{last_row + 20}",
        {
            "backgroundColor": WHITE,
            "textFormat": {"bold": False, "italic": False, "fontSize": 10},
            "horizontalAlignment": None,
            "numberFormat": None,
            "wrapStrategy": "OVERFLOW_CELL",
        },
    )
    client.set_column_widths(TAB, WIDTHS)
    _static_formats(client, layout)
    _conditional_formats(client, layout)
    client.hide_columns(TAB, HIDDEN_FIRST, HIDDEN_LAST)
    client.freeze(TAB, rows=0, cols=1)
    client.set_row_group_control_before(TAB)
    client.apply_row_groups(TAB, group_specs(layout, state))
    return (
        f"{TAB}: written ({len(rows)} rows, {len(layout.player_rows)} player rows, "
        f"{len(layout.groups)} groups)"
    )


def _static_formats(client: SheetsClient, layout: eft.Layout) -> None:
    last = eft.LINK_COL  # visible columns end at the link; P and Q are hidden
    client.format_range(TAB, f"A{layout.title_row}", _TITLE_FMT)
    for row in layout.status_rows:
        client.format_range(TAB, f"A{row}", {"textFormat": {"fontSize": 9, "foregroundColor": INK_MUTED}})
    for row in layout.section_rows:
        client.format_range(TAB, f"A{row}:{last}{row}", _PANEL_FMT)
    for row in layout.meaning_rows:
        client.format_range(
            TAB, f"A{row}", {"textFormat": {"italic": True, "fontSize": 9, "foregroundColor": INK_MUTED}}
        )
    for row in layout.header_rows:
        client.format_range(TAB, f"A{row}:{last}{row}", _HEADER_FMT)
    for row in layout.subheader_rows:
        client.format_range(TAB, f"A{row}:{last}{row}", {"textFormat": {"bold": True, "fontSize": 10}})
    for row in layout.note_rows:
        client.format_range(
            TAB, f"A{row}", {"textFormat": {"italic": True, "fontSize": 9, "foregroundColor": INK_MUTED}}
        )
    for row in layout.verdict_rows:
        client.format_range(
            TAB,
            f"A{row}",
            {"textFormat": {"italic": True, "bold": True, "fontSize": 9, "foregroundColor": VERDICT_FG}},
        )
    for row in layout.overflow_rows:
        client.format_range(
            TAB,
            f"A{row}:{last}{row}",
            {"textFormat": {"italic": True, "fontSize": 9, "foregroundColor": INK_MUTED}},
        )
    for row in layout.team_rows:
        client.format_range(TAB, f"A{row}:{last}{row}", {"textFormat": {"bold": True, "fontSize": 10}})
    for rng in layout.money_cells:
        client.format_range(TAB, rng, {**_num("$#,##0", "CURRENCY"), "horizontalAlignment": "RIGHT"})
    for rng in layout.point_cells:
        client.format_range(TAB, rng, {**_num("0.0"), "horizontalAlignment": "RIGHT"})
    for rng in layout.percent_cells:
        client.format_range(TAB, rng, {**_num('0"%"'), "horizontalAlignment": "RIGHT"})
    for rng in layout.chip_ranges:
        client.format_range(TAB, rng, {"horizontalAlignment": "CENTER"})
    # Why wraps (a reason can run to two lines); Do / Pool read centred; Set is an input cell.
    for first, last_row in _runs(layout.player_rows):
        client.format_range(TAB, f"{eft.WHY_COL}{first}:{eft.WHY_COL}{last_row}", {"wrapStrategy": "WRAP"})
        client.format_range(
            TAB, f"{eft.DO_COL}{first}:{eft.POOL_COL}{last_row}", {"horizontalAlignment": "CENTER"}
        )
        client.format_range(
            TAB,
            f"{eft.SET_COL}{first}:{eft.SET_COL}{last_row}",
            {"backgroundColor": INPUT_BG, "horizontalAlignment": "CENTER"},
        )
        client.set_dropdown_validation(TAB, f"{eft.SET_COL}{first}:{eft.SET_COL}{last_row}", eft.SET_OPTIONS)
    # Static muting: thin samples, questionable beneficiaries, context signals, the toughest matchups.
    for row in layout.muted_rows:
        client.format_range(
            TAB,
            f"A{row}:{last}{row}",
            {"textFormat": {"italic": True, "foregroundColor": INK_MUTED}},
        )


def _blend(a: dict, b: dict, t: float) -> dict:
    return {k: a[k] + (b[k] - a[k]) * t for k in ("red", "green", "blue")}


def _conditional_formats(client: SheetsClient, layout: eft.Layout) -> None:
    """Conditional formats, all relative to their own cells. Added first = lowest priority."""
    # The best in a position block is green, the worst neutral (never red for the best players available).
    mid = _blend(NO_FILL, GRAD_MAX, 0.5)
    for header, first, last_row, lower_is_better in layout.prob_blocks:
        letter = _column_of(layout, header, first)
        if letter is None:
            continue
        low, high = (GRAD_MAX, NO_FILL) if lower_is_better else (NO_FILL, GRAD_MAX)
        client.add_color_scale(
            TAB, f"{letter}{first}:{letter}{last_row}", min_color=low, mid_color=mid, max_color=high
        )
    for rng in layout.chip_ranges:
        for text, fmt in EDGE_CHIPS.items():
            client.add_boolean_rule(TAB, rng, condition_type="TEXT_CONTAINS", values=[text], fmt=fmt)
    if layout.player_rows:
        first, last_row = min(layout.player_rows), max(layout.player_rows)
        do = f"{eft.DO_COL}{first}:{eft.DO_COL}{last_row}"
        for text in ("add", "leverage"):
            client.add_boolean_rule(
                TAB,
                do,
                condition_type="TEXT_CONTAINS",
                values=[text],
                fmt={"textFormat": {"bold": True, "foregroundColor": ADD_FG}},
            )
        client.add_boolean_rule(
            TAB,
            do,
            condition_type="TEXT_CONTAINS",
            values=["In pool"],
            fmt={"textFormat": {"bold": False, "italic": True, "foregroundColor": INK_MUTED}},
        )


def _column_of(layout: eft.Layout, header: str, row: int) -> str | None:
    """The letter of `header` in the nearest header row above `row` (a block's own section header)."""
    above = [h for h in layout.header_rows if h < row]
    if not above:
        return None
    names = layout.rows[max(above) - 1]
    return column_letter(names.index(header)) if header in names else None


def write_board_panel(client: SheetsClient, inputs: eft.Inputs | None) -> str:
    """The Board's five "This week's edges" lines."""
    return write_board_edges(client, eft.board_panel_lines(inputs))


def ensure_tab(client: SheetsClient, *, edge_tab: str = "EdgeRaw") -> str:
    """`dfs setup build-views`: create the tab in its empty state when it does not exist, never touch one that
    does (it holds the last sync's data; the sync rewrites it)."""
    if client.tab_exists(TAB):
        return f"{TAB}: already present -- left as the last sync wrote it"
    return write_tab(client, None, edge_tab=edge_tab) + " (empty state)"
