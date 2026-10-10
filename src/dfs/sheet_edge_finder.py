"""Write the `Edge Finder` tab (`edge_finder_tab.build_layout`) and the Board's "This week's edges" panel.

The tab is rebuilt from scratch by every write (nothing on it is typed): its values come from the sync; its
`Pool` and `Do` cells are live formulas keyed on the hidden `Id` cell (the player's DraftKings id, never his
name).
The `Pool` cell, first on every player row, is also the control: it carries a Cash / GPP / Both dropdown, and
the bound Apps Script (`apps_script/Code.gs`) writes EdgeRaw's `Pool` for the pick and puts the formula back.
`Why` is the last visible column, short, with the full reason as the cell's note. Formatting is static for
section/header/note rows; the probability colour is a gradient per position block, and the muting of thin
samples is written with the rows.

**Open and shut groups survive a rewrite.** Before anything is cleared, `read_group_state` reads the tab's row
groups and remembers each one's collapsed flag by its key (hidden column `P`, on the row above the group);
after the rewrite the same keys get the same state back, and a key never seen before gets its default
(sections open, overflow and team groups shut).
"""

from __future__ import annotations

import contextlib

from dfs import edge_finder_tab as eft
from dfs import sheet_pool_cells as pc
from dfs.sheet_clipping import FIT_PADDING_PX, FIT_PX_PER_CHAR, estimate_px, fitted_widths
from dfs.sheet_color_scales import GRAD_MAX, WHITE
from dfs.sheet_style import (
    _HEADER_FMT,
    _PANEL_FMT,
    _TITLE_FMT,
    BAND_BG,
    EDGE_CHIPS,
    INK_MUTED,
    INPUT_BG,
    POOL_TYPE_CHIPS,
    _num,
)
from dfs.sheets import SheetsClient, column_letter

TAB = eft.EDGE_FINDER_TAB
POOL_TAB = pc.POOL_TAB
# Sam (2026-10-09): Pool through Why fits about 1,270 px with no horizontal scroll. The fixed columns are
# tight; Edge and Do take the width of their longest real value (never past CHIP_MAX); Name and Why give up
# the difference (Name down to NAME_MIN, Why down to WHY_MIN) when something else had to grow.
# `fit_widths` does it.
NAME_MIN, NAME_MAX = 155, 170
WHY_MIN, WHY_MAX = 220, 300
CHIP_MAX = 140
BUDGET = 1270
SLOT_PX = 64
WIDTHS = {
    eft.POOL_COL: pc.POOL_COLUMN_WIDTH,
    "B": NAME_MAX,
    "C": 44,
    "D": 48,
    "E": 64,
    eft.OWN_COL: 60,
    "G": SLOT_PX,
    "H": SLOT_PX,
    "I": SLOT_PX,
    "J": SLOT_PX,
    "K": SLOT_PX,
    eft.EDGE_COL: 100,
    eft.DO_COL: 120,
    eft.WHY_COL: 250,
    eft.ID_COL: 90,
    eft.KEY_COL: 90,
}
# How far a column may grow past its fixed width when a real value would be cut off (the audit decides).
GROW_MAX = {"C": 56, "D": 56, "E": 72, eft.OWN_COL: 70, "G": 84, "H": 84, "I": 84, "J": 84, "K": 84}
HIDDEN_FIRST, HIDDEN_LAST = eft.ID_COL, eft.KEY_COL
NO_FILL = {"red": 1.0, "green": 1.0, "blue": 1.0}
VERDICT_FG = {"red": 0.60, "green": 0.38, "blue": 0.0}
ADD_FG = {"red": 0.10, "green": 0.45, "blue": 0.25}
SHEET_URL = "https://docs.google.com/spreadsheets/d/{sheet_id}/edit#gid={gid}&fvid={fvid}"


def _edge_letter(name: str) -> str:
    return pc.edge_letter(name)


def _name_letter() -> str:
    return pc.edge_letter("Name")


def _id_letter() -> str:
    return pc.edge_letter("Id")


def pool_formula(row: int, edge_tab: str) -> str:
    """The player's pool state (`sheet_pool_cells.pool_formula`) keyed on this tab's hidden `Id` cell."""
    return pc.pool_formula(row, edge_tab, id_col=eft.ID_COL)


def do_formula(row: int, verb: str) -> str:
    return pc.do_formula(row, verb, pool_col=eft.POOL_COL)


def _index(letter: str) -> int:
    return ord(letter) - ord("A")


def tab_rows(layout: eft.Layout, edge_tab: str) -> list[list]:
    """The layout's rows with the Pool and Do formulas filled into every player row."""
    rows = [list(r) for r in layout.rows]
    for row in layout.player_rows:
        verb = rows[row - 1][_index(eft.DO_COL)]
        rows[row - 1][_index(eft.POOL_COL)] = pool_formula(row, edge_tab)
        rows[row - 1][_index(eft.DO_COL)] = do_formula(row, str(verb))
    return rows


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
    rows = tab_rows(layout, edge_tab)
    for row, view in layout.all_links.items():
        link = _all_link(client, edge_tab, view)
        if link:
            rows[row - 1][_index(eft.DO_COL)] = link
    last_row = max(len(rows), 40)

    if client.tab_exists(TAB):
        client.ensure_row_capacity(TAB, last_row + 20)
        client.clear_row_groups(TAB)
        client.unhide_rows(TAB, 1, last_row + 20)  # deleting a collapsed group leaves its rows hidden
    client.write_tab(TAB, rows)
    client.clear_notes(TAB, f"{eft.WHY_COL}1:{eft.WHY_COL}{last_row + 20}")
    # the previous layout's dropdowns (the old Set column) stay behind unless cleared
    client.clear_data_validation(TAB, f"A1:{eft.LAST_COLUMN}{last_row + 20}")
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
    client.set_column_widths(TAB, fit_widths(layout))
    _static_formats(client, layout)
    _conditional_formats(client, layout)
    client.hide_columns(TAB, HIDDEN_FIRST, HIDDEN_LAST)
    client.freeze(TAB, rows=0, cols=2)  # Pool and Name stay in view
    _write_notes(client, layout)
    client.set_row_group_control_before(TAB)
    client.apply_row_groups(TAB, group_specs(layout, state))
    return (
        f"{TAB}: written ({len(rows)} rows, {len(layout.player_rows)} player rows, "
        f"{len(layout.groups)} groups)"
    )


def _display_rows(layout: eft.Layout) -> list[list]:
    """The layout's rows as text roughly as the sheet shows them (a number is the stored float, which is far
    longer than its formatted cell): `Own%` as a one-decimal percent, `Salary` as dollars, other floats to one
    decimal."""
    own, salary = _index(eft.OWN_COL), eft.LEAD.index("Salary")
    out = []
    for row in layout.rows:
        shown = []
        for i, cell in enumerate(row):
            if isinstance(cell, bool) or not isinstance(cell, (int, float)):
                shown.append(cell)
            elif i == own:
                shown.append(f"{cell:.1%}")
            elif i == salary:
                shown.append(f"${cell:,.0f}")
            else:
                shown.append(f"{cell:.1f}" if isinstance(cell, float) else str(cell))
        out.append(shown)
    return out


def _longest_px(texts: list[str]) -> int:
    return max(
        (estimate_px(x, per_char=FIT_PX_PER_CHAR, padding=FIT_PADDING_PX) for x in texts if x), default=0
    )


def fit_widths(layout: eft.Layout) -> dict[str, int]:
    """`WIDTHS` adjusted to the real values (Sam's rule, 2026-10-09):
    - Edge and Do: the width of their longest real value, between a floor and `CHIP_MAX`;
    - every other column: widened only when a real value would be cut off (`sheet_clipping`), up to
      `GROW_MAX`;
    - Name: its longest real name, between `NAME_MIN` and `NAME_MAX`;
    - Pool through Why stays within `BUDGET`: the surplus comes out of Why (down to `WHY_MIN`), then Name.
    The hidden columns are left alone."""
    letters = [column_letter(i) for i in range(eft.COLUMN_COUNT)]
    widths = [WIDTHS.get(letter, 100) for letter in letters]
    hidden = {i for i, letter in enumerate(letters) if HIDDEN_FIRST <= letter <= HIDDEN_LAST}
    rows = _display_rows(layout)
    players = [rows[r - 1] for r in layout.player_rows]
    edge_i, do_i, name_i, why_i = (_index(c) for c in (eft.EDGE_COL, eft.DO_COL, eft.NAME_COL, eft.WHY_COL))
    # the chip columns: the longest real value (and the longest value the Do formula can show instead)
    widths[edge_i] = min(CHIP_MAX, max(60, _longest_px([str(r[edge_i]) for r in players] + ["Edge"])))
    widths[do_i] = min(CHIP_MAX, max(90, _longest_px([str(r[do_i]) for r in players] + ["In pool (Both)"])))
    widths[name_i] = min(NAME_MAX, max(NAME_MIN, _longest_px([str(r[name_i]) for r in players])))
    fitted = fitted_widths(
        rows,
        widths,
        hidden,
        bold_rows=frozenset([*layout.header_rows, *layout.inner_header_rows]),
        skip_columns=frozenset({edge_i, do_i, why_i}),
        maxima={_index(letter): px for letter, px in GROW_MAX.items()} | {name_i: NAME_MAX},
    )
    for i, px in fitted.items():
        widths[i] = px
    over = sum(widths[: why_i + 1]) - BUDGET
    for i, floor in ((why_i, WHY_MIN), (name_i, NAME_MIN)):
        take = min(max(over, 0), widths[i] - floor)
        widths[i] -= take
        over -= take
    return {letter: widths[i] for i, letter in enumerate(letters)}


def _write_notes(client: SheetsClient, layout: eft.Layout) -> None:
    """The full reason behind each shortened `Why`, as the cell's note (hover to read it)."""
    for row, text in layout.notes.items():
        if text:
            client.set_note(TAB, f"{eft.WHY_COL}{row}", text)


def _static_formats(client: SheetsClient, layout: eft.Layout) -> None:
    last = eft.LAST_VISIBLE_COL  # Why is the last visible column; the Id and group key are hidden
    client.format_range(TAB, f"A{layout.title_row}:{last}{layout.title_row}", _TITLE_FMT)
    for row in layout.status_rows:
        client.format_range(
            TAB, f"A{row}:{last}{row}", {"textFormat": {"fontSize": 9, "foregroundColor": INK_MUTED}}
        )
    for row in layout.section_rows:
        client.format_range(TAB, f"A{row}:{last}{row}", _PANEL_FMT)
    for row in layout.meaning_rows:
        client.format_range(
            TAB,
            f"A{row}:{last}{row}",
            {"textFormat": {"italic": True, "fontSize": 9, "foregroundColor": INK_MUTED}},
        )
    for row in layout.header_rows:
        client.format_range(TAB, f"A{row}:{last}{row}", _HEADER_FMT)
    for row in layout.inner_header_rows:  # the small header inside a group: quiet, not a dark bar
        client.format_range(
            TAB,
            f"A{row}:{last}{row}",
            {
                "backgroundColor": BAND_BG,
                "textFormat": {"bold": True, "fontSize": 9, "foregroundColor": INK_MUTED},
            },
        )
    for row in layout.subheader_rows:
        client.format_range(TAB, f"A{row}:{last}{row}", {"textFormat": {"bold": True, "fontSize": 10}})
    for row in layout.note_rows:
        client.format_range(
            TAB,
            f"A{row}:{last}{row}",
            {"textFormat": {"italic": True, "fontSize": 9, "foregroundColor": INK_MUTED}},
        )
    for row in layout.verdict_rows:
        client.format_range(
            TAB,
            f"A{row}:{last}{row}",
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
    # The label cell keeps its row's look and adds bold (a format call replaces the whole textFormat).
    label_looks = [
        (layout.status_rows, {"fontSize": 9, "foregroundColor": INK_MUTED}),
        (layout.meaning_rows, {"italic": True, "fontSize": 9, "foregroundColor": INK_MUTED}),
        (layout.note_rows, {"italic": True, "fontSize": 9, "foregroundColor": INK_MUTED}),
        (layout.verdict_rows, {"italic": True, "fontSize": 9, "foregroundColor": VERDICT_FG}),
    ]
    for rows, look in label_looks:
        for row in rows:
            if layout.rows[row - 1][0]:  # a continuation line has no label
                client.format_range(TAB, f"A{row}", {"textFormat": {**look, "bold": True}})
    for rng in layout.money_cells:
        client.format_range(TAB, rng, {**_num("$#,##0", "CURRENCY"), "horizontalAlignment": "RIGHT"})
    for rng in layout.point_cells:
        client.format_range(TAB, rng, {**_num("0.0"), "horizontalAlignment": "RIGHT"})
    for rng in layout.percent_cells:
        client.format_range(TAB, rng, {**_num('0"%"'), "horizontalAlignment": "RIGHT"})
    for rng in layout.chip_ranges:
        client.format_range(TAB, rng, {"horizontalAlignment": "CENTER"})
    for cell, pattern, kind in layout.cell_formats:
        client.format_range(TAB, cell, {**_num(pattern, kind), "horizontalAlignment": "RIGHT"})
    # Own% is a 0-1 fraction on EdgeRaw, shown as a percent; Lev is a signed whole number.
    for rng in layout.own_cells:
        client.format_range(TAB, rng, {**_num("0.0%"), "horizontalAlignment": "RIGHT"})
    for rng in layout.lev_cells:
        client.format_range(TAB, rng, {**_num("+0;-0;0"), "horizontalAlignment": "RIGHT"})
    # Pool is the control (input colour + dropdown + a chip colour per state); Do reads centred; Why never
    # wraps (it is the last visible column and overflows right; the full reason is its note).
    for first, last_row in _runs(layout.player_rows):
        pool = f"{eft.POOL_COL}{first}:{eft.POOL_COL}{last_row}"
        client.format_range(
            TAB, pool, {"backgroundColor": INPUT_BG, "horizontalAlignment": "CENTER", "wrapStrategy": "CLIP"}
        )
        client.set_dropdown_validation(TAB, pool, pc.POOL_OPTIONS, strict=False)
        client.format_range(
            TAB, f"{eft.DO_COL}{first}:{eft.DO_COL}{last_row}", {"horizontalAlignment": "CENTER"}
        )
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
        pool = f"{eft.POOL_COL}{first}:{eft.POOL_COL}{last_row}"
        for state, fmt in POOL_TYPE_CHIPS.items():
            client.add_boolean_rule(TAB, pool, condition_type="TEXT_EQ", values=[state], fmt=fmt)
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


def ensure_tab(client: SheetsClient, *, edge_tab: str = "EdgeRaw") -> str:
    """`dfs setup build-views`: create the tab in its empty state when it does not exist, never touch one that
    does (it holds the last sync's data; the sync rewrites it)."""
    if client.tab_exists(TAB):
        return f"{TAB}: already present -- left as the last sync wrote it"
    return write_tab(client, None, edge_tab=edge_tab) + " (empty state)"
