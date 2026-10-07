"""Write the `Edge Finder` tab (`edge_finder_tab.build_layout`) and the Board's "This week's edges" panel.

The tab is rebuilt from scratch by every write (nothing on it is typed): its values come from the sync, its
`Pool` and `↗` cells are live formulas keyed on the player's Name (EdgeRaw's own Pool tick, and a link to his
EdgeRaw row). Formatting is static for section/header/note rows; everything that depends on a row's own data
(low-games muting, token chips, the probability gradients) is a conditional format relative to its row, so it
survives a sort or filter of a section's rows. No basic filter is set: the sections are separate blocks.
"""

from __future__ import annotations

from dfs import edge_finder_tab as eft
from dfs.derived import EDGE_COLUMNS, EDGE_DATA_OFFSET
from dfs.sheet_color_scales import GRAD_MAX, GRAD_MIN, WHITE
from dfs.sheet_style import (
    _HEADER_FMT,
    _PANEL_FMT,
    _TITLE_FMT,
    EDGE_CHIPS,
    INK_MUTED,
    _num,
)
from dfs.sheet_views import write_board_edges
from dfs.sheets import SheetsClient, column_letter
from dfs.sources.edge import POOL_COLUMN

TAB = eft.EDGE_FINDER_TAB
WIDTHS = {
    "A": 190,
    "B": 52,
    "C": 56,
    "D": 70,
    "E": 78,
    "F": 78,
    "G": 84,
    "H": 120,
    "I": 120,
    "J": 100,
    "K": 60,
    "L": 40,
    "M": 56,
    "N": 620,
}


def _name_letter() -> str:
    return column_letter(EDGE_COLUMNS.index("Name") + EDGE_DATA_OFFSET)


def pool_formula(row: int, edge_tab: str) -> str:
    """EdgeRaw's own Pool tick (Cash / GPP / Both, blank when not pooled) for this row's Name."""
    n = _name_letter()
    return (
        f'=IFERROR(INDEX({edge_tab}!${POOL_COLUMN}:${POOL_COLUMN},MATCH($A{row},{edge_tab}!${n}:${n},0)),"")'
    )


def link_formula(row: int, edge_tab: str, gid: int) -> str:
    """A same-spreadsheet link to this player's row on EdgeRaw (the Player Pool `Edge ↗` convention)."""
    n = _name_letter()
    target = f'"#gid={gid}&range={n}"&MATCH($A{row},{edge_tab}!${n}:${n},0)'
    return f'=IFERROR(HYPERLINK({target},"↗"),"-")'


def tab_rows(layout: eft.Layout, edge_tab: str, gid: int) -> list[list]:
    """The layout's rows with the Pool and link formulas filled into every player row."""
    rows = [list(r) for r in layout.rows]
    for row in layout.pool_rows:
        rows[row - 1][ord(eft.POOL_COL) - ord("A")] = pool_formula(row, edge_tab)
        rows[row - 1][ord(eft.LINK_COL) - ord("A")] = link_formula(row, edge_tab, gid)
    return rows


def write_tab(client: SheetsClient, inputs: eft.Inputs | None, *, edge_tab: str = "EdgeRaw") -> str:
    """Write and style the whole tab. `inputs` None writes the empty-state layout (a template)."""
    layout = eft.build_layout(inputs, edge_tab_name=edge_tab)
    gid = client.tab_gid(edge_tab) if client.tab_exists(edge_tab) else 0
    rows = tab_rows(layout, edge_tab, gid)
    last_row = max(len(rows), 40)
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
    client.format_range(TAB, f"A{layout.title_row}", _TITLE_FMT)
    for row in layout.status_rows:
        client.format_range(TAB, f"A{row}", {"textFormat": {"fontSize": 9, "foregroundColor": INK_MUTED}})
    for row in layout.section_rows:
        client.format_range(TAB, f"A{row}:{eft.LAST_COLUMN}{row}", _PANEL_FMT)
    for row in layout.header_rows:
        client.format_range(TAB, f"A{row}:{eft.LAST_COLUMN}{row}", _HEADER_FMT)
    for row in layout.subheader_rows:
        client.format_range(
            TAB, f"A{row}:{eft.LAST_COLUMN}{row}", {"textFormat": {"bold": True, "fontSize": 10}}
        )
    for row in layout.note_rows:
        client.format_range(
            TAB, f"A{row}", {"textFormat": {"italic": True, "fontSize": 9, "foregroundColor": INK_MUTED}}
        )
    for rng in layout.money_cells:
        client.format_range(TAB, rng, {**_num("$#,##0", "CURRENCY"), "horizontalAlignment": "RIGHT"})
    for rng in layout.point_cells:
        client.format_range(TAB, rng, {**_num("0.0"), "horizontalAlignment": "RIGHT"})
    for rng in layout.percent_cells:
        client.format_range(TAB, rng, {**_num('0"%"'), "horizontalAlignment": "RIGHT"})
    for rng in layout.chip_ranges:
        client.format_range(TAB, rng, {"horizontalAlignment": "CENTER"})
    # Static muting: questionable beneficiaries, context signals, the toughest matchups.
    for row in layout.muted_rows:
        client.format_range(
            TAB,
            f"A{row}:{eft.LAST_COLUMN}{row}",
            {"textFormat": {"italic": True, "foregroundColor": INK_MUTED}},
        )
    # Conditional formats, all relative to their own row. Added first = lowest priority.
    if layout.player_ranges:
        first = min(r for r, _ in layout.player_ranges)
        last = max(r for _, r in layout.player_ranges)
        games = f"${eft.GAMES_COL}{first}"
        client.add_boolean_rule(
            TAB,
            f"A{first}:{eft.LAST_COLUMN}{last}",
            condition_type="CUSTOM_FORMULA",
            values=[f"=AND(ISNUMBER({games}),{games}<{eft.MUTED_BELOW_GAMES})"],
            fmt={"textFormat": {"italic": True, "foregroundColor": INK_MUTED}},
        )
    for name, ranges in layout.prob_ranges.items():
        low, high = (GRAD_MAX, GRAD_MIN) if name == "Bust%" else (GRAD_MIN, GRAD_MAX)
        for rng in ranges:
            client.add_color_scale(TAB, rng, min_color=low, mid_color=WHITE, max_color=high)
    for rng in layout.chip_ranges:
        for text, fmt in EDGE_CHIPS.items():
            client.add_boolean_rule(TAB, rng, condition_type="TEXT_CONTAINS", values=[text], fmt=fmt)
    client.freeze(TAB, rows=0, cols=1)
    return f"{TAB}: written ({len(rows)} rows, {len(layout.pool_rows)} player rows)"


def write_board_panel(client: SheetsClient, inputs: eft.Inputs | None) -> str:
    """The Board's five "This week's edges" lines."""
    return write_board_edges(client, eft.board_panel_lines(inputs))


def ensure_tab(client: SheetsClient, *, edge_tab: str = "EdgeRaw") -> str:
    """`dfs setup build-views`: create the tab in its empty state when it does not exist, never touch one that
    does (it holds the last sync's data; the sync rewrites it)."""
    if client.tab_exists(TAB):
        return f"{TAB}: already present -- left as the last sync wrote it"
    return write_tab(client, None, edge_tab=edge_tab) + " (empty state)"
