"""Presentation-only styling for the workbook: widths, freeze panes, number
formats, header treatment, conditional-format chips, tab colour/order/
visibility.

Deliberately scoped to things that CANNOT invalidate a hardcoded position.
Nothing here inserts, deletes, moves or renames a column, row or tab, and
nothing writes a cell value -- so no VLOOKUP index, no `LINEUPS_NAME_BLOCKS`
range, no `bankroll.cash.header_row`, and no `tab_mappings` entry can be
broken by running any of it. That is the whole design constraint; if a
future addition here needs to move something, it belongs in a different
module with a changelog entry attached.

Column positions are derived from `derived.EDGE_COLUMNS` rather than written
as literal letters, for the same reason `sheet_links.py` derives its VLOOKUP
indices: when that list changes, this file follows it instead of silently
formatting the wrong column.

Re-runnable. Every styling function clears the tab's existing conditional
formats (and, where relevant, its column groups/banding) before adding its
own, so running twice leaves the same result as running once rather than
stacking duplicate rules -- verified directly by `dfs sheets audit-style`.

`polish_edge()` applies EdgeRaw's frozen header and its colour scales as
part of this pass -- the standalone `dfs sheets format-edge` command that
used to apply a couple of these effects on its own was removed once
`polish` fully superseded it (nothing else called it, and maintaining two
implementations of the same colour scales was pure drift risk).

VISUAL GRAMMAR -- the whole workbook obeys this, not just EdgeRaw. If a new
tab or column needs styling, it fits one of these; if it doesn't, that's a
sign the new thing needs a new rule stated here, not a one-off in whatever
function happens to touch it first.

- Dark header (`_HEADER_FMT`, #20262F/white/bold) means "this row is a
  table header." Nothing else in the workbook uses this fill.
- Pale yellow (`INPUT_BG`, #FFFDF5) means "you type here" -- and is the
  ONLY thing that means that. Exactly five places carry it: EdgeRaw's Pool
  column, Pool Picks' Name column, Lineups' block column A
  (`polish_lineups_input_column`), Exposure's Target column, and the pool
  deck's B1/D1/F1 controls (`polish_pool_deck`). Everywhere else is a
  formula; if it isn't pale yellow, don't type into it.
- Red -> yellow -> green (`GRAD_MIN`/`GRAD_MID`/`GRAD_MAX`, via
  `add_color_scale`) means "more is better," for the decision numbers
  named in `EDGE_COLOR_SCALES`/`FIELD_FORMATS`. Reversed (max color at the
  low end) for a rank column, where 1st is best. A true diverging scale
  (`mid_type="NUMBER", mid_value="0"`, white midpoint) is for a signed
  delta where zero -- not the median -- is the meaningful center: LineMove
  on EdgeRaw and Movement, nowhere else.
- Chips (`_chip`, solid background + bold matching text) mark categorical
  STATE only -- Flag, Avail, the Guardrails column, position tints. Never
  put a chip on a number; that's what the colour scales are for.
- Grey italic (`INK_MUTED`, `italic: True`) marks a value that's computed
  but currently running on a degraded/proxy basis -- today, EdgeRaw's
  CeilPct while `LevBasis` reads "proxy" (see `polish_edge`).
- Colour that doesn't encode a value gets removed, full stop. Banding and
  the position tint are deliberately near-invisible for this reason: they
  carry structure (which row, which position), not a value, so they must
  never compete with a scale or a chip for attention.
"""

from __future__ import annotations

from dfs.derived import EDGE_COLUMNS, EDGE_DATA_OFFSET
from dfs.sheet_links import COLOR_SCALE_LINKED_COLUMNS
from dfs.sheets import SheetsClient, column_letter
from dfs.sources.edge import POOL_COLUMN, POOL_HEADER

# Rows to format on the big tabs. EdgeRaw currently carries ~743 players;
# 1000 is the provisioned depth and costs nothing to format past the data.
EDGE_ROWS = 1000
POOL_RAW_ROWS = 987

# ---------------------------------------------------------------------------
# Palette. Sheets wants 0-1 floats, not hex.
# ---------------------------------------------------------------------------


def _rgb(hex_str: str) -> dict:
    h = hex_str.lstrip("#")
    return {
        "red": int(h[0:2], 16) / 255,
        "green": int(h[2:4], 16) / 255,
        "blue": int(h[4:6], 16) / 255,
    }


INK = _rgb("#12161C")
INK_MUTED = _rgb("#798494")
WHITE = _rgb("#FFFFFF")
HEADER_BG = _rgb("#20262F")
BAND_BG = _rgb("#F7F8FA")

OK_BG, OK_FG = _rgb("#DCEBE3"), _rgb("#15704A")
WARN_BG, WARN_FG = _rgb("#F7E9CF"), _rgb("#8F5406")
CRIT_BG, CRIT_FG = _rgb("#F8DEDA"), _rgb("#A22C23")
FLAT_BG, FLAT_FG = _rgb("#E7EBF0"), _rgb("#4A5563")

# The ONE cue for "you type here", everywhere in the workbook -- see the
# visual-grammar docstring below. Every typed cell uses this and nothing
# else uses it: EdgeRaw's Pool column, Pool Picks column A, Lineups block
# column A, Exposure's Target column, and the pool deck's B1/D1/F1 controls.
INPUT_BG = _rgb("#FFFDF5")

# The red -> yellow -> green gradient the now-removed `dfs sheets
# format-edge` command originally introduced, kept identical here.
GRAD_MIN = {"red": 0.96, "green": 0.80, "blue": 0.80}
GRAD_MID = {"red": 1.0, "green": 1.0, "blue": 0.80}
GRAD_MAX = {"red": 0.72, "green": 0.88, "blue": 0.72}

# Tab families -- the same four-way taxonomy already in the sheet, just
# desaturated so the strip reads as a system instead of a highlighter set.
FAMILY_COLORS = {
    "decide": _rgb("#3F6E8C"),
    "build": _rgb("#A93B30"),
    "money": _rgb("#15704A"),
    "contest": _rgb("#856508"),
    "feed": _rgb("#79356F"),
}

_HEADER_FMT = {
    "backgroundColor": HEADER_BG,
    "textFormat": {"bold": True, "foregroundColor": WHITE, "fontSize": 10},
    "verticalAlignment": "MIDDLE",
    "horizontalAlignment": "LEFT",
}
# Public alias -- sheet_pool_deck.py reuses this exact style for its own
# mini-header row (row 3) so it visually matches every other header on
# the sheet, without duplicating the color/weight choices in two places.
HEADER_FMT = _HEADER_FMT


def _chip(bg: dict, fg: dict) -> dict:
    return {"backgroundColor": bg, "textFormat": {"bold": True, "foregroundColor": fg}}


def _num(pattern: str, type_: str = "NUMBER") -> dict:
    return {"numberFormat": {"type": type_, "pattern": pattern}}


# ---------------------------------------------------------------------------
# FIELD_FORMATS -- the single source of truth for "what does this field look
# like", keyed by header TEXT. Every tab in the workbook that shows a given
# field applies the SAME entry here, whether that tab discovers its header
# dynamically (`apply_field_formats`, used by EdgeRaw and the builder tabs)
# or is an authored view with a literal layout (Board/Slate Grid/Exposure/
# Movement/Bankroll, which reference these constants directly rather than
# re-deriving their own `_num(...)` call). Before this existed, the exact
# same field (e.g. `Pts`) could be `General` in one place and `0.0` in
# another purely because two different dicts had drifted -- measured for
# real in the Lineups pool deck, whose window sat directly above block rows
# using a different format for the same column. Keys are the header text a
# tab actually prints, so aliases for the same concept (`Pts` vs `ProjPts`,
# `Ceil` vs `Ceiling`, `DK Sal` vs `Salary`) each get their own entry
# pointing at an equivalent format rather than being normalized away --
# normalizing header text is a structural change this file deliberately
# never makes (see the module docstring).
FIELD_FORMATS = {
    "DK Sal": _num("$#,##0", "CURRENCY"),
    "Salary": _num("$#,##0", "CURRENCY"),
    "Pts": _num("0.0"),
    "ProjPts": _num("0.0"),
    "Ceil": _num("0.0"),
    "Ceiling": _num("0.0"),
    "O/U": _num("0.0"),
    "OU": _num("0.0"),
    "Team Implied": _num("0.0"),
    "GameEnv": _num("0.0"),
    "Total": _num("0.0"),
    "Leverage": _num("0.0"),
    "CeilPct": _num("0.0"),
    "Spread": _num('"+"0.0;"-"0.0;0.0'),
    "LineMove": _num('"+"0.0;"-"0.0;0.0'),
    "Val": _num("0.00"),
    "CeilVal": _num("0.00"),
    "Rstr%": _num("0.0%", "PERCENT"),
    "% of Rstr": _num("0.0%", "PERCENT"),
    "Exposure": _num("0.0%", "PERCENT"),
    "Target": _num("0.0%", "PERCENT"),
    "vs Target": _num("0.0%", "PERCENT"),
    "H2H %": _num("0.0%", "PERCENT"),
    "Wind": _num('0" mph"'),
    "Gust": _num('0" mph"'),
    # ProjOwn is a raw 0-100 number in EdgeRaw (Rstr%/Exposure above are
    # true fractions), so it gets a literal "%" suffix rather than a
    # PERCENT type, which would multiply it by 100 again.
    "ProjOwn": _num('0.0"%"'),
}


def apply_field_formats(
    client: SheetsClient, tab: str, header: list, *, header_row: int, last_row: int
) -> int:
    """Format every column in `header` whose text is a FIELD_FORMATS key.
    `header` is the tab's own header row, already read (or, for EdgeRaw,
    known from EDGE_COLUMNS without a read -- see `polish_edge`) so this
    never assumes a column's position. Returns how many columns matched,
    for callers' own status lines."""
    applied = 0
    data_start = header_row + 1
    for i, name in enumerate(header):
        fmt = FIELD_FORMATS.get(name)
        if not fmt:
            continue
        letter = column_letter(i)
        client.format_range(tab, f"{letter}{data_start}:{letter}{last_row}", fmt)
        applied += 1
    return applied


# ---------------------------------------------------------------------------
# EdgeRaw (Direction B)
# ---------------------------------------------------------------------------

# Pixel widths by EdgeRaw column NAME, not letter -- so a reordered
# EDGE_COLUMNS still gets the right width on the right column.
EDGE_WIDTHS = {
    "Id": 90,
    "Name": 165,
    "Position": 52,
    "Team": 54,
    "Opp": 54,
    "Salary": 78,
    "ProjPts": 62,
    "ProjOwn": 62,
    "Ceiling": 64,
    "Val": 58,
    "CeilVal": 68,
    "CeilPct": 64,
    "Leverage": 72,
    "LevBasis": 74,
    "GameEnv": 76,
    "Stadium": 150,
    "Roof": 76,
    "Wind": 68,
    "Avail": 60,
    "Flag": 96,
    "LineMove": 78,
    "GameStart": 132,
}

# Collapsed by default: the two stadium descriptors, which matter to the
# code and almost never to you. Grouped, not hidden -- the +/- control
# above the column letters brings them straight back. Id used to be a
# third entry here, but it's genuinely never useful to look at (a raw
# DraftKings player ID, not a human-meaningful value), so it's fully
# hidden instead (see polish_edge's hide_columns call) -- a group would
# just be a second click for something that never needs to come back.
EDGE_COLUMN_GROUPS = [("Stadium", "Roof"), ("GameStart", "GameStart")]

# The decision set: every number Sam actually weighs a pick on. Salary is
# deliberately excluded -- it's a constraint, not a quality, and scaling it
# would imply cheap is good. LineMove is excluded too: it's a signed delta
# scored with its own diverging scale below, not this red->yellow->green one.
EDGE_COLOR_SCALES = ("ProjPts", "Ceiling", "Val", "CeilVal", "Leverage", "GameEnv")

# Muted, per-position backgrounds -- just enough to see position boundaries
# while scanning a list sorted by Leverage, not loud enough to compete with
# the colour scales on the decision columns.
POSITION_TINTS = {
    "QB": _rgb("#ECE7F5"),
    "RB": _rgb("#E5F1E8"),
    "WR": _rgb("#E5EEF7"),
    "TE": _rgb("#FBEEE0"),
    "DST": _rgb("#EEEEEE"),
}

# Same threshold and colour Slate Grid's own Wind/Gust chip uses (see
# style_slate_grid) -- one number, one meaning, everywhere it appears.
WIND_CHIP_THRESHOLD = "15"

# Soft accent used only for "this player is already in your pool" -- applied
# to the Name column alone. A full-row tint was tried and rejected: Sheets
# shows exactly one conditional-format rule per cell, so tinting the whole
# row would silently blank out the six colour scales on every ticked
# player's row instead of coexisting with them.
POOL_TINT_BG = _rgb("#EAF1FB")

FLAG_CHIPS = {
    "OUT": _chip(CRIT_BG, CRIT_FG),
    "WIND": _chip(WARN_BG, WARN_FG),
    "LEVERAGE": _chip(OK_BG, OK_FG),
    "CHALK": _chip(FLAT_BG, FLAT_FG),
    "LINE↑": _chip(OK_BG, OK_FG),
    "LINE↓": _chip(CRIT_BG, CRIT_FG),
}

AVAIL_CHIPS = {
    "OUT": _chip(CRIT_BG, CRIT_FG),
    "IR": _chip(CRIT_BG, CRIT_FG),
    "Q": _chip(WARN_BG, WARN_FG),
}

# Player Pool's Source column (Task 5.3): which of the two ways a player
# got into the pool. Neutral, not OK/WARN/CRIT -- neither source is a
# problem, this is provenance, not a state to react to.
SOURCE_CHIPS = {
    "EdgeRaw": _chip(FLAT_BG, FLAT_FG),
    "Picks": _chip(OK_BG, OK_FG),
}


def _edge_letter(column_name: str) -> str | None:
    """Real EdgeRaw column letter for a column NAME, or None if that column
    isn't in EDGE_COLUMNS on this version of the CLI. Offset by
    EDGE_DATA_OFFSET since column A is Pool, not the first EDGE_COLUMNS
    entry -- see derived.py's own comment on EDGE_DATA_OFFSET."""
    if column_name not in EDGE_COLUMNS:
        return None
    return column_letter(EDGE_COLUMNS.index(column_name) + EDGE_DATA_OFFSET)


def polish_edge(client: SheetsClient, edge_tab: str) -> str:
    """Direction B: make EdgeRaw readable without moving anything.

    Widths, a dark frozen header, Id hidden and Pool+Name pinned while you
    scroll right, light row banding, number formats on every numeric
    column, six colour scales on the decision numbers (a diverging one for
    LineMove), a muted per-position tint, a Wind chip matching Slate Grid's,
    Flag/Avail as chips, the Name cell tinted when that player is already
    pooled and bolded when Flag is set, and CeilPct greyed while Leverage is
    running on a proxy so the two identical-looking columns stop competing.
    """
    if not client.tab_exists(edge_tab):
        return f"{edge_tab}: not present -- skipped"

    last_col = column_letter(len(EDGE_COLUMNS) - 1 + EDGE_DATA_OFFSET)
    client.clear_conditional_formats(edge_tab)
    client.clear_banding(edge_tab)
    client.add_row_banding(
        edge_tab, f"A2:{last_col}{EDGE_ROWS}", first_band_color=WHITE, second_band_color=BAND_BG
    )

    widths = {}
    for name, px in EDGE_WIDTHS.items():
        letter = _edge_letter(name)
        if letter:
            widths[letter] = px
    client.set_column_widths(edge_tab, widths)

    client.format_range(edge_tab, f"A1:{last_col}1", _HEADER_FMT)
    # The one typed column on this tab -- pale yellow, the same "you type
    # here" cue every other typed cell in the workbook uses (see the
    # visual-grammar docstring below).
    client.format_range(edge_tab, f"{POOL_COLUMN}2:{POOL_COLUMN}{EDGE_ROWS}", {"backgroundColor": INPUT_BG})
    # Pool and Name pinned while you scroll right; Id (between them) is
    # hidden outright rather than pinned -- a raw DraftKings ID is never
    # worth looking at, hiding it also means Pool and Name end up visually
    # adjacent despite Id physically sitting between them.
    id_col = _edge_letter("Id")
    if id_col:
        client.hide_columns(edge_tab, id_col, id_col)
    name_idx = EDGE_COLUMNS.index("Name") + EDGE_DATA_OFFSET if "Name" in EDGE_COLUMNS else 1
    client.freeze(edge_tab, rows=1, cols=name_idx + 1)

    # EdgeRaw's real header is `[POOL_HEADER, *EDGE_COLUMNS]` by construction
    # (see `sources/edge.py`'s `to_sheet_rows`) -- built here rather than
    # read back, same as every other position in this function.
    apply_field_formats(client, edge_tab, [POOL_HEADER, *EDGE_COLUMNS], header_row=1, last_row=EDGE_ROWS)

    for name in EDGE_COLOR_SCALES:
        letter = _edge_letter(name)
        if letter:
            client.add_color_scale(
                edge_tab,
                f"{letter}2:{letter}{EDGE_ROWS}",
                min_color=GRAD_MIN,
                mid_color=GRAD_MID,
                max_color=GRAD_MAX,
            )

    # Diverging, not the standard scale above: LineMove is a signed delta
    # and zero (no movement) is the meaningful midpoint, not the median.
    line_move_col = _edge_letter("LineMove")
    if line_move_col:
        client.add_color_scale(
            edge_tab,
            f"{line_move_col}2:{line_move_col}{EDGE_ROWS}",
            min_color=GRAD_MIN,
            mid_color=WHITE,
            max_color=GRAD_MAX,
            mid_type="NUMBER",
            mid_value="0",
        )

    wind_col = _edge_letter("Wind")
    if wind_col:
        client.add_boolean_rule(
            edge_tab,
            f"{wind_col}2:{wind_col}{EDGE_ROWS}",
            condition_type="NUMBER_GREATER",
            values=[WIND_CHIP_THRESHOLD],
            fmt=_chip(WARN_BG, WARN_FG),
        )

    position_col = _edge_letter("Position")
    if position_col:
        for position, bg in POSITION_TINTS.items():
            client.add_boolean_rule(
                edge_tab,
                f"{position_col}2:{position_col}{EDGE_ROWS}",
                condition_type="TEXT_EQ",
                values=[position],
                fmt={"backgroundColor": bg},
            )

    flag_col = _edge_letter("Flag")
    if flag_col:
        client.format_range(edge_tab, f"{flag_col}2:{flag_col}{EDGE_ROWS}", {"horizontalAlignment": "CENTER"})
        for text, fmt in FLAG_CHIPS.items():
            client.add_boolean_rule(
                edge_tab,
                f"{flag_col}2:{flag_col}{EDGE_ROWS}",
                condition_type="TEXT_EQ",
                values=[text],
                fmt=fmt,
            )

    avail_col = _edge_letter("Avail")
    if avail_col:
        client.format_range(
            edge_tab, f"{avail_col}2:{avail_col}{EDGE_ROWS}", {"horizontalAlignment": "CENTER"}
        )
        for text, fmt in AVAIL_CHIPS.items():
            client.add_boolean_rule(
                edge_tab,
                f"{avail_col}2:{avail_col}{EDGE_ROWS}",
                condition_type="TEXT_EQ",
                values=[text],
                fmt=fmt,
            )

    # While ProjOwn is all zeros, LevBasis reads "proxy" and Leverage is a
    # copy of CeilPct. Grey the copy rather than deleting it -- the CLI
    # still writes it, you just stop reading the same number twice.
    ceil_pct, lev_basis = _edge_letter("CeilPct"), _edge_letter("LevBasis")
    if ceil_pct and lev_basis:
        client.add_boolean_rule(
            edge_tab,
            f"{ceil_pct}2:{ceil_pct}{EDGE_ROWS}",
            condition_type="CUSTOM_FORMULA",
            values=[f'=${lev_basis}2="proxy"'],
            fmt={"textFormat": {"foregroundColor": INK_MUTED, "italic": True}},
        )
        client.format_range(
            edge_tab,
            f"{lev_basis}2:{lev_basis}{EDGE_ROWS}",
            {"textFormat": {"foregroundColor": INK_MUTED}, "horizontalAlignment": "CENTER"},
        )

    # "Already in my pool" + "flagged" on the Name cell, as the three
    # mutually-exclusive combinations rather than two independent rules --
    # Sheets renders only one matching conditional-format rule per cell, so
    # two overlapping single-condition rules would silently hide one cue
    # instead of showing both on a player that's pooled AND flagged.
    name_col = _edge_letter("Name")
    if name_col and flag_col:
        pool_ref = f"${POOL_COLUMN}2"
        flag_ref = f"${flag_col}2"
        pooled_and_flagged = {"backgroundColor": POOL_TINT_BG, "textFormat": {"bold": True}}
        pooled_only = {"backgroundColor": POOL_TINT_BG}
        flagged_only = {"textFormat": {"bold": True}}
        rules = [
            (f'=AND({pool_ref}=TRUE,{flag_ref}<>"")', pooled_and_flagged),
            (f'=AND({pool_ref}=TRUE,{flag_ref}="")', pooled_only),
            (f'=AND({pool_ref}<>TRUE,{flag_ref}<>"")', flagged_only),
        ]
        for formula, fmt in rules:
            client.add_boolean_rule(
                edge_tab,
                f"{name_col}2:{name_col}{EDGE_ROWS}",
                condition_type="CUSTOM_FORMULA",
                values=[formula],
                fmt=fmt,
            )
    elif name_col:
        client.add_boolean_rule(
            edge_tab,
            f"{name_col}2:{name_col}{EDGE_ROWS}",
            condition_type="CUSTOM_FORMULA",
            values=[f"=${POOL_COLUMN}2=TRUE"],
            fmt={"backgroundColor": POOL_TINT_BG},
        )

    client.clear_column_groups(edge_tab)
    for first, last in EDGE_COLUMN_GROUPS:
        a, b = _edge_letter(first), _edge_letter(last)
        if a and b:
            client.group_columns(edge_tab, a, b)

    return f"{edge_tab}: widths, header, banding, formats, 6 colour scales, position tint, chips applied"


# ---------------------------------------------------------------------------
# Player Pool / Lineups / PlayerPoolRaw -- number formats and freeze only
# ---------------------------------------------------------------------------

# EDGE_WIDTHS merged in so the EdgeRaw-linked block (CeilVal/Leverage/
# GameEnv/Stadium/Roof/Wind/Avail/Flag/...) gets the same widths here as
# on EdgeRaw itself, not left at Sheets' own default -- found missing by
# `dfs sheets audit-style`. "Name"/"Team" appear in both dicts with
# identical values, so the merge doesn't change either.
BUILDER_WIDTHS = {
    **EDGE_WIDTHS,
    "Pos.": 52,
    "Opp.": 54,
    "Venue": 56,
    "DK Sal": 78,
    "% of Rstr": 72,
    "Source": 64,
}


def polish_builder_tab(
    client: SheetsClient,
    tab: str,
    *,
    last_row: int,
    header_row: int = 1,
    freeze_rows: int | None = None,
    freeze_cols: int = 1,
    header_repeats_at: list[int] | None = None,
) -> str:
    """Number formats, widths, header treatment and (by default) a pinned
    Name column on a tab whose header row names its columns. Reads the
    header first and matches by name, so it never assumes a column is in a
    given position.

    `header_row` defaults to 1, true for Player Pool/PlayerPoolRaw, but not
    for Lineups: `sheet_pool_deck.py`'s `add_pool_deck` inserts frozen rows
    above its header, so its caller passes the real row (derived from
    `LINEUPS_NAME_BLOCKS`, not hardcoded). `freeze_rows` defaults to
    freezing through the header row itself; Lineups instead passes its
    deck row count, since freezing past the header would freeze into the
    first lineup block. `freeze_cols` defaults to 1 (pin Name); Lineups
    passes 0 so this doesn't fight the deck's own column-freeze choice
    (see `sheet_pool_deck.py`).

    `header_repeats_at` styles Lineups' repeated sub-header rows (one per
    lineup block after the first, see `weekly_reset.py`) the same dark
    way as the real header, so every block reads consistently instead of
    only the first one looking like a header. Player Pool/PlayerPoolRaw
    have no repeats and pass nothing.
    """
    if not client.tab_exists(tab):
        return f"{tab}: not present -- skipped"

    header_rows = client.read_range(tab, f"A{header_row}:{header_row}")
    header = header_rows[0] if header_rows else []
    if not header:
        return f"{tab}: empty header row -- skipped"

    last_col = column_letter(len(header) - 1)
    client.format_range(tab, f"A{header_row}:{last_col}{header_row}", _HEADER_FMT)
    for repeat_row in header_repeats_at or []:
        client.format_range(tab, f"A{repeat_row}:{last_col}{repeat_row}", _HEADER_FMT)
    client.freeze(tab, rows=freeze_rows if freeze_rows is not None else header_row, cols=freeze_cols)

    widths = {}
    for i, name in enumerate(header):
        if name in BUILDER_WIDTHS:
            widths[column_letter(i)] = BUILDER_WIDTHS[name]
    if widths:
        client.set_column_widths(tab, widths)

    applied = apply_field_formats(client, tab, header, header_row=header_row, last_row=last_row)

    # Flag/Avail chips, same as EdgeRaw's own (found missing entirely by
    # `dfs sheets audit-style`), plus Player Pool's own Source column
    # (Task 5.3). None of these three are ever colour-scaled by
    # `sheet_links.link_edge_columns`, and `polish_guardrails` owns
    # column O on Lineups, not Player Pool, so a column-scoped clear here
    # is safe on both tabs.
    chipped = 0
    data_start = header_row + 1
    for column_name, chips in (("Flag", FLAG_CHIPS), ("Avail", AVAIL_CHIPS), ("Source", SOURCE_CHIPS)):
        if column_name not in header:
            continue
        letter = column_letter(header.index(column_name))
        client.clear_conditional_formats(tab, column=letter)
        client.format_range(
            tab, f"{letter}{data_start}:{letter}{last_row}", {"horizontalAlignment": "CENTER"}
        )
        for text, fmt in chips.items():
            client.add_boolean_rule(
                tab,
                f"{letter}{data_start}:{letter}{last_row}",
                condition_type="TEXT_EQ",
                values=[text],
                fmt=fmt,
            )
        chipped += 1

    pin_note = "Name pinned" if freeze_cols else "no column pin"
    return f"{tab}: header styled, {pin_note}, {applied} column(s) number-formatted, {chipped} chip column(s)"


# ---------------------------------------------------------------------------
# The pool deck's window (sheet_pool_deck.py): make the numbers above the
# divider mean the same thing as the numbers below it
# ---------------------------------------------------------------------------


_CONTROL_BORDER = {"style": "SOLID_MEDIUM", "color": INK_MUTED}


def polish_pool_deck(client: SheetsClient, lineups_tab: str, *, header_row: int, window_end: int) -> str:
    """The deck (rows 1..DECK_ROWS) was built to align with the lineup
    blocks below it, but its window rows never got the block rows' own
    formatting: `Pts`/`Ceil`/`Val`/`Leverage` etc. showed as raw floats a
    few rows above block cells showing "0.0" for the identical field.
    Applies the same FIELD_FORMATS the blocks get (found by header name off
    row 3, not a literal column) plus the same colour scales
    `sheet_links.link_edge_columns` already put on the block's CeilVal/
    Leverage/GameEnv columns, so a number above the divider and the same
    field below it read identically. Also gives B1/D1/F1 -- the deck's only
    controls -- the workbook's one "you type here" treatment plus a border,
    since as plain cells they gave no visual hint they were interactive.

    `header_row`/`window_end` come from `sheet_pool_deck.py`'s own
    constants (row 3, and `3 + _WINDOW_SIZE`) rather than being
    re-derived here, same discipline as everywhere else column/row
    positions cross a module boundary in this codebase.
    """
    if not client.tab_exists(lineups_tab):
        return f"{lineups_tab}: not present -- skipped"

    control_borders = {side: _CONTROL_BORDER for side in ("top", "bottom", "left", "right")}
    for cell in ("B1", "D1", "F1"):
        client.format_range(lineups_tab, cell, {"backgroundColor": INPUT_BG, "borders": control_borders})

    header_rows = client.read_range(lineups_tab, f"A{header_row}:{header_row}")
    header = header_rows[0] if header_rows else []
    if not header:
        return f"{lineups_tab}: controls marked, deck header row {header_row} empty -- skipped"

    applied = apply_field_formats(client, lineups_tab, header, header_row=header_row, last_row=window_end)

    # Row-band clear, not `column=letter`: that column also carries
    # `sheet_links.link_edge_columns`' own scale on the real lineup blocks
    # further down, which this function does not own and must not delete
    # on every `add_pool_deck` re-run. And not an exact-range clear either
    # -- which column ends up holding a given field can change (Player
    # Pool/Lineups header drift, see `sheet_pool_deck.py`), which moved
    # this exact rule's column on a real re-run and left the old one
    # orphaned since an exact-range match against the NEW range never
    # found it. Clearing the whole window row band once, regardless of
    # column, finds it either way.
    client.clear_conditional_formats(lineups_tab, row_range=(header_row + 1, window_end))

    scaled = 0
    for name in COLOR_SCALE_LINKED_COLUMNS:
        if name not in header:
            continue
        letter = column_letter(header.index(name))
        window_range = f"{letter}{header_row + 1}:{letter}{window_end}"
        client.add_color_scale(
            lineups_tab,
            window_range,
            min_color=GRAD_MIN,
            mid_color=GRAD_MID,
            max_color=GRAD_MAX,
        )
        scaled += 1

    return f"{lineups_tab}: deck formatted ({applied} field(s), {scaled} colour scale(s)), controls marked"


# ---------------------------------------------------------------------------
# Lineups Guardrails (Task L): per-lineup validation in the empty O column
# ---------------------------------------------------------------------------

_GUARDRAILS_COLUMN = "O"
_GUARDRAILS_HEADER = "Check"
_GUARDRAILS_CHIPS = [
    ("TEXT_CONTAINS", "DUPLICATE", _chip(CRIT_BG, CRIT_FG)),
    ("TEXT_CONTAINS", "OVER", _chip(CRIT_BG, CRIT_FG)),
    ("TEXT_EQ", "OUT", _chip(CRIT_BG, CRIT_FG)),
    ("TEXT_EQ", "IR", _chip(CRIT_BG, CRIT_FG)),
    ("TEXT_EQ", "Q", _chip(WARN_BG, WARN_FG)),
    ("TEXT_CONTAINS", "INCOMPLETE", _chip(WARN_BG, WARN_FG)),
    ("TEXT_EQ", "OK", _chip(OK_BG, OK_FG)),
]


def _slot_check_formula(start: int, end: int, row: int, avail_col: str) -> str:
    """Flags a name duplicated elsewhere in its own block, else surfaces
    that pick's linked Avail flag (OUT/IR/Q) if it has one."""
    last_slot = end - 1
    return (
        f'=IF($A{row}="","",'
        f'IF(COUNTIF($A${start}:$A${last_slot},$A{row})>1,"DUPLICATE",'
        f'IF(${avail_col}{row}<>"",${avail_col}{row},"")))'
    )


def _totals_check_formula(start: int, end: int, totals_row: int) -> str:
    """Salary cap, roster completeness, or OK -- on the block's totals row."""
    last_slot = end - 1
    return (
        f'=IF(COUNTA($A${start}:$A${last_slot})=0,"",'
        f'IF(D{totals_row}>50000,"OVER "&TEXT(D{totals_row}-50000,"$#,##0"),'
        f"IF(COUNTA($A${start}:$A${last_slot})<9,"
        f'"INCOMPLETE "&COUNTA($A${start}:$A${last_slot})&"/9","OK")))'
    )


def polish_lineups_input_column(client: SheetsClient, tab: str, name_blocks: list[tuple[int, int]]) -> str:
    """Column A within each lineup block is one of the four places in the
    whole workbook you type into by hand -- give it the shared "you type
    here" cue (see the visual-grammar docstring below), same treatment as
    EdgeRaw's Pool column and Exposure's Target column. Player Pool's own
    column A must NEVER get this: it looks identical (a plain Name column)
    but is fully computed, not typed -- see `polish_builder_tab`, which is
    what actually styles Player Pool, and does not call this."""
    if not client.tab_exists(tab):
        return f"{tab}: not present -- skipped"
    for start, end in name_blocks:
        client.format_range(tab, f"A{start}:A{end}", {"backgroundColor": INPUT_BG})
    return f"{tab}: column A marked as input across {len(name_blocks)} lineup block(s)"


def polish_guardrails(
    client: SheetsClient, tab: str, *, header_row: int, name_blocks: list[tuple[int, int]]
) -> str:
    """Each lineup block currently checks exactly one thing (salary
    remaining, via its own D/E-column formulas). This adds the checks that
    actually catch mistakes, in column O -- the empty spacer immediately
    left of the EdgeRaw-linked block, 2.4px wide until this widens it.

    Per roster slot: DUPLICATE if the same name appears twice in that
    lineup, else that pick's Avail flag (OUT/IR/Q) if it has one. On the
    block's totals row: OVER the cap, INCOMPLETE (fewer than 9 picks), or
    OK. The Avail column is found by header name, not a hardcoded letter
    -- exactly the class of assumption that caused this feature's own
    prerequisite bug (see CONTRIBUTING.md's changelog); skips cleanly if
    `dfs sheets link-edge` hasn't run yet.

    Writing here is safe regardless of what's linked at Q..Z: O sits
    strictly to their left, so nothing here can collide with that block.
    Re-runnable -- clears only O's own conditional-format rules first
    (`column="O"`), never the tab's other rules, which
    `sheet_links.link_edge_columns` already owns.
    """
    if not client.tab_exists(tab):
        return f"{tab}: not present -- skipped"

    header_rows = client.read_range(tab, f"A{header_row}:{header_row}")
    header = header_rows[0] if header_rows else []
    if "Avail" not in header:
        return f"{tab}: 'Avail' not linked yet (run `dfs sheets link-edge` first) -- skipped"
    avail_col = column_letter(header.index("Avail"))

    client.set_column_widths(tab, {_GUARDRAILS_COLUMN: 110})
    client.update_range(tab, f"{_GUARDRAILS_COLUMN}{header_row}", [[_GUARDRAILS_HEADER]])

    for start, end in name_blocks:
        rows = [[_slot_check_formula(start, end, row, avail_col)] for row in range(start, end)]
        rows.append([_totals_check_formula(start, end, end)])
        client.update_range(tab, f"{_GUARDRAILS_COLUMN}{start}:{_GUARDRAILS_COLUMN}{end}", rows)

    client.clear_conditional_formats(tab, column=_GUARDRAILS_COLUMN)
    last_row = max(end for _, end in name_blocks)
    a1_range = f"{_GUARDRAILS_COLUMN}2:{_GUARDRAILS_COLUMN}{last_row}"
    for condition_type, value, fmt in _GUARDRAILS_CHIPS:
        client.add_boolean_rule(tab, a1_range, condition_type=condition_type, values=[value], fmt=fmt)

    return f"{tab}: guardrails applied to column {_GUARDRAILS_COLUMN} across {len(name_blocks)} lineup(s)"


# ---------------------------------------------------------------------------
# Bankroll (Direction G)
# ---------------------------------------------------------------------------

_CURRENCY = _num("$#,##0.00", "CURRENCY")
_PERCENT = _num("0.0%", "PERCENT")

# The KPI block at the top of Bankroll, addressed by the cells the tab
# already uses. Rows 1-13 only; both ledgers live below row 15 and are not
# touched here beyond their header rows and money columns.
BANKROLL_CURRENCY_CELLS = ["B1:B2", "D1:D3", "F1", "I1:I2", "B6:B7", "B9", "D12:D13", "F12:F13", "H12:H13"]
BANKROLL_PERCENT_CELLS = ["D4", "F2", "B8", "B10", "B12:B13"]
BANKROLL_LABEL_CELLS = ["A1:A13", "C1:C4", "E1:E2", "G12:G13", "H1:H2", "C12:E13"]


def polish_bankroll(
    client: SheetsClient,
    tab: str,
    *,
    cash: tuple[int, int, int],
    gpp: tuple[int, int, int],
) -> str:
    """Direction G: the same ledger, read as a scoreboard.

    Currency and percent formats across the KPI block, the two ledger
    header rows given the same dark treatment as everywhere else, money
    columns formatted, and green/red on the net figures. `cash` and `gpp`
    are (header_row, first_row, last_row) straight from config, so no row
    number is written here.
    """
    if not client.tab_exists(tab):
        return f"{tab}: not present -- skipped"

    client.clear_conditional_formats(tab)

    for rng in BANKROLL_CURRENCY_CELLS:
        client.format_range(tab, rng, _CURRENCY)
    for rng in BANKROLL_PERCENT_CELLS:
        client.format_range(tab, rng, _PERCENT)
    for rng in BANKROLL_LABEL_CELLS:
        client.format_range(
            tab, rng, {"textFormat": {"foregroundColor": INK_MUTED, "fontSize": 9, "bold": False}}
        )

    # The three figures worth reading first, made bigger than their labels.
    for cell in ("B1", "B2", "F1"):
        client.format_range(tab, cell, {"textFormat": {"bold": True, "fontSize": 12, "foregroundColor": INK}})

    for header_row, first, last in (cash, gpp):
        client.format_range(tab, f"A{header_row}:J{header_row}", _HEADER_FMT)
        client.format_range(tab, f"B{first}:B{last}", _num("0"))
        client.format_range(tab, f"C{first}:C{last}", _num("0.0"))
        for col in ("D", "F", "G"):
            client.format_range(tab, f"{col}{first}:{col}{last}", _CURRENCY)
        for col in ("I", "J"):
            client.format_range(tab, f"{col}{first}:{col}{last}", _PERCENT)

    # Net green when positive, red when negative -- on the season net, the
    # weekly net, and both ledger nets.
    for rng in ("F1", "B9", "H12:H13"):
        client.add_boolean_rule(
            tab,
            rng,
            condition_type="NUMBER_GREATER",
            values=["0"],
            fmt={"textFormat": {"foregroundColor": OK_FG, "bold": True}},
        )
        client.add_boolean_rule(
            tab,
            rng,
            condition_type="NUMBER_LESS",
            values=["0"],
            fmt={"textFormat": {"foregroundColor": CRIT_FG, "bold": True}},
        )

    return f"{tab}: KPI block tiled, ledger headers styled, money columns formatted"


# ---------------------------------------------------------------------------
# Tab chrome (Direction M, plus D's colours and hiding -- no renames)
# ---------------------------------------------------------------------------

# Left to right in the order the week actually runs: research, shortlist,
# build, enter, monitor, reconcile. Tabs absent from a given sheet are
# skipped, so this is safe on both the template and the live copy.
WEEK_ORDER = [
    ("Board", "decide"),
    ("EdgeRaw", "decide"),
    ("Slate Grid", "decide"),
    ("Player Pool", "build"),
    ("Lineups", "build"),
    ("Scratch", "build"),
    ("DK Upload", "build"),
    ("Movement", "contest"),
    ("Exposure", "contest"),
    ("GPPin", "contest"),
    ("DKLineupsFinal", "contest"),
    ("Bankroll", "money"),
    ("Results", "money"),
    ("SoSQB", "feed"),
    ("SoSRB", "feed"),
    ("SoSWr", "feed"),
    ("SoSTE", "feed"),
    ("SoSDef", "feed"),
    ("SoSComb", "feed"),
    ("Instructions", "decide"),
    ("PlayerPoolRaw", "feed"),
]

# Pure staging. Hidden, not deleted -- the API writes to hidden tabs
# perfectly happily, so `dfs sync` is unaffected. PlayerPoolRaw stays
# visible on purpose: it's the hub every other tab reads, and hiding it
# makes a broken lookup much harder to debug.
HIDE_TABS = [
    "DKSalRaw",
    "DkSalClean",
    "oddsraw",
    "oddsFinal",
    "TFFBOptoRaw",
    "GamesRaw",
    "WeatherRaw",
    "EntriesRaw",
    "DKLineupsRaw",
]


def apply_tab_chrome(client: SheetsClient, *, hide_staging: bool = True) -> list[str]:
    """Order the tab strip by phase of the week, colour it by family, and
    hide the staging tabs. Order, colour and visibility only -- no renames,
    so nothing in `tab_mappings` or `config.toml` is affected.
    """
    results = []
    index = 0
    for tab, family in WEEK_ORDER:
        if not client.tab_exists(tab):
            continue
        client.set_tab_properties(tab, color=FAMILY_COLORS[family], index=index, hidden=False)
        index += 1
    results.append(f"tab strip: {index} tab(s) ordered by week phase and colour-coded")

    if hide_staging:
        hidden = 0
        for tab in HIDE_TABS:
            if not client.tab_exists(tab):
                continue
            client.set_tab_properties(tab, color=FAMILY_COLORS["feed"], index=index, hidden=True)
            index += 1
            hidden += 1
        results.append(f"staging: {hidden} tab(s) hidden (still fully writable by dfs sync)")

    return results


# ---------------------------------------------------------------------------
# The derived view tabs (Directions A, E, F, H)
# ---------------------------------------------------------------------------

# `sheet_views` writes these tabs' formulas; this styles them. Kept here
# rather than in that module so all presentation decisions live in one
# file -- and so the layout constants below sit next to the palette they
# use. The column positions ARE literal here, unlike everywhere else in
# this file, because these are tabs we author ourselves: their layout is
# defined by `sheet_views`, not discovered from the sheet. If you change a
# layout there, change it here.

_TITLE_FMT = {"textFormat": {"bold": True, "fontSize": 13, "foregroundColor": INK}}
_PANEL_FMT = {
    "backgroundColor": HEADER_BG,
    "textFormat": {"bold": True, "foregroundColor": WHITE, "fontSize": 9},
    "horizontalAlignment": "LEFT",
}
_SUBHEAD_FMT = {
    "backgroundColor": FLAT_BG,
    "textFormat": {"bold": True, "foregroundColor": FLAT_FG, "fontSize": 9},
}
_BANNER_FMT = {
    "backgroundColor": WARN_BG,
    "textFormat": {"bold": True, "foregroundColor": WARN_FG, "fontSize": 10},
}

# Board panels: (first column, last column) for each of the three.
_BOARD_PANELS = [("A", "D"), ("F", "I"), ("K", "N")]


def style_board(client: SheetsClient, tab: str = "Board") -> str:
    if not client.tab_exists(tab):
        return f"{tab}: not present -- skipped"
    client.clear_conditional_formats(tab)
    client.set_column_widths(
        tab,
        {
            "A": 160,
            "B": 74,
            "C": 64,
            "D": 72,
            "E": 24,
            "F": 160,
            "G": 74,
            "H": 82,
            "I": 72,
            "J": 24,
            "K": 160,
            "L": 74,
            "M": 62,
            "N": 96,
        },
    )
    client.format_range(tab, "A1", _TITLE_FMT)
    client.format_range(tab, "A2:H2", {"textFormat": {"fontSize": 10}})
    for label in ("A2", "C2", "E2", "G2"):
        client.format_range(tab, label, {"textFormat": {"foregroundColor": INK_MUTED, "fontSize": 9}})
    for value in ("B2", "D2", "F2", "H2"):
        client.format_range(tab, value, {"textFormat": {"bold": True, "foregroundColor": INK}})
    client.format_range(tab, "A3:N3", _BANNER_FMT)

    # A visible break between the three panels -- narrow columns E and J
    # were spacers already, but an unstyled spacer reads the same as a
    # panel column at a glance. A muted grey fill makes them read as a
    # deliberate rule instead.
    for spacer in ("E", "J"):
        client.format_range(tab, f"{spacer}5:{spacer}20", {"backgroundColor": FLAT_BG})

    for first, last in _BOARD_PANELS:
        client.format_range(tab, f"{first}5:{last}5", _PANEL_FMT)
        client.format_range(tab, f"{first}6:{last}6", _SUBHEAD_FMT)

    # Panel bodies: 12 rows for the two ranked panels, 14 for landmines. Same
    # FIELD_FORMATS entries the rest of the workbook uses for Pts/CeilVal/
    # Salary -- this tab's columns are literal (an authored view, not a
    # discovered header) but the format for a given field must still match.
    client.format_range(tab, "C7:C18", FIELD_FORMATS["Pts"])
    client.format_range(tab, "D7:D18", FIELD_FORMATS["CeilVal"])
    client.format_range(tab, "H7:H18", FIELD_FORMATS["Salary"])
    client.format_range(tab, "I7:I18", FIELD_FORMATS["CeilVal"])
    for rng in ("C7:C18", "D7:D18", "I7:I18"):
        client.add_color_scale(tab, rng, min_color=GRAD_MIN, mid_color=GRAD_MID, max_color=GRAD_MAX)
    for text, fmt in FLAG_CHIPS.items():
        client.add_boolean_rule(tab, "N7:N20", condition_type="TEXT_EQ", values=[text], fmt=fmt)
    for text, fmt in AVAIL_CHIPS.items():
        client.add_boolean_rule(tab, "M7:M20", condition_type="TEXT_EQ", values=[text], fmt=fmt)
    client.freeze(tab, rows=6)
    return f"{tab}: styled (3 panels, banner, colour scales)"


def style_slate_grid(client: SheetsClient, tab: str = "Slate Grid") -> str:
    if not client.tab_exists(tab):
        return f"{tab}: not present -- skipped"
    client.clear_conditional_formats(tab)
    client.set_column_widths(
        tab,
        {"A": 140, "B": 116, "C": 64, "D": 72, "E": 84, "F": 72, "G": 72, "H": 84, "I": 52, "J": 160},
    )
    client.format_range(tab, "A1:J1", _HEADER_FMT)
    client.format_range(tab, "C2:C19", FIELD_FORMATS["Total"])
    client.format_range(tab, "D2:D19", FIELD_FORMATS["Spread"])
    client.format_range(tab, "F2:G19", FIELD_FORMATS["Wind"])
    client.format_range(tab, "I2:I19", {"horizontalAlignment": "CENTER"})
    client.add_color_scale(tab, "C2:C19", min_color=GRAD_MIN, mid_color=GRAD_MID, max_color=GRAD_MAX)
    client.add_boolean_rule(
        tab, "F2:G19", condition_type="NUMBER_GREATER", values=["15"], fmt=_chip(WARN_BG, WARN_FG)
    )
    client.add_boolean_rule(
        tab, "I2:I19", condition_type="TEXT_EQ", values=["DIV"], fmt=_chip(FLAT_BG, FLAT_FG)
    )
    client.freeze(tab, rows=1, cols=1)
    return f"{tab}: styled (totals colour-scaled, high wind flagged)"


def style_exposure(client: SheetsClient, tab: str = "Exposure") -> str:
    if not client.tab_exists(tab):
        return f"{tab}: not present -- skipped"
    client.clear_conditional_formats(tab)
    client.set_column_widths(
        tab, {"A": 165, "B": 52, "C": 78, "D": 84, "E": 84, "F": 84, "G": 88, "H": 24, "I": 96, "J": 72}
    )
    client.format_range(tab, "A1:G1", _HEADER_FMT)
    client.format_range(tab, "I1", {"textFormat": {"foregroundColor": INK_MUTED, "fontSize": 9}})
    client.format_range(tab, "C2:C180", FIELD_FORMATS["Salary"])
    client.format_range(tab, "D2:D180", _num("0"))
    client.format_range(tab, "E2:E180", FIELD_FORMATS["Exposure"])
    client.format_range(tab, "F2:F180", FIELD_FORMATS["Target"])
    client.format_range(tab, "G2:G180", FIELD_FORMATS["vs Target"])
    # Target is the one typed column in the whole workbook that this
    # module touches -- mark it as input rather than output.
    client.format_range(tab, "F2:F180", {"backgroundColor": INPUT_BG, "textFormat": {"italic": True}})
    client.add_color_scale(tab, "E2:E180", min_color=GRAD_MAX, mid_color=GRAD_MID, max_color=GRAD_MIN)
    client.add_boolean_rule(
        tab, "G2:G180", condition_type="NUMBER_GREATER", values=["0"], fmt=_chip(CRIT_BG, CRIT_FG)
    )
    client.add_boolean_rule(
        tab, "G2:G180", condition_type="NUMBER_LESS", values=["0"], fmt=_chip(WARN_BG, WARN_FG)
    )
    client.freeze(tab, rows=1, cols=1)
    return f"{tab}: styled (Target marked as input, over/under target flagged)"


def style_movement(client: SheetsClient, tab: str = "Movement") -> str:
    if not client.tab_exists(tab):
        return f"{tab}: not present -- skipped"
    client.clear_conditional_formats(tab)
    client.set_column_widths(tab, {"A": 165, "B": 92, "C": 92, "D": 152, "E": 96})
    client.format_range(tab, "A1", _TITLE_FMT)
    client.format_range(tab, "A3:E3", _HEADER_FMT)
    client.format_range(tab, "C4:C60", FIELD_FORMATS["LineMove"])
    # Diverging, not the standard red->yellow->green: this is a signed
    # delta and zero (no movement) is the meaningful midpoint, same
    # reasoning as EdgeRaw's own LineMove column (see polish_edge).
    client.add_color_scale(
        tab,
        "C4:C60",
        min_color=GRAD_MIN,
        mid_color=WHITE,
        max_color=GRAD_MAX,
        mid_type="NUMBER",
        mid_value="0",
    )
    for text, fmt in FLAG_CHIPS.items():
        client.add_boolean_rule(tab, "E4:E60", condition_type="TEXT_EQ", values=[text], fmt=fmt)
    client.freeze(tab, rows=3, cols=1)
    return f"{tab}: styled (movement colour-scaled, flags chipped)"


def style_view_tabs(client: SheetsClient) -> list[str]:
    """Style whichever of the four derived tabs exist. Each is skipped
    cleanly if `dfs sheets build-views` hasn't created it yet, so `polish`
    is safe to run on a sheet that has none of them."""
    return [
        style_board(client),
        style_slate_grid(client),
        style_exposure(client),
        style_movement(client),
    ]


# ---------------------------------------------------------------------------
# Tier 2/3 (Task 2.9): tabs that have never been styled at all
# ---------------------------------------------------------------------------

# Distinct from Sheets' own 100px column default (see
# `SheetsClient.get_column_widths`'s docstring) so `dfs sheets audit-style`
# recognizes a column here as deliberately set, not left untouched.
_GENERIC_COLUMN_PX = 110


def style_flat_tab(client: SheetsClient, tab: str, *, last_row: int, header_row: int = 1) -> str:
    """The standard treatment -- dark header, frozen pane, a width on
    every column, FIELD_FORMATS wherever a header matches -- for a tab
    that has otherwise never been styled: Scratch, DK Upload,
    DKLineupsFinal, SoSComb. Header-driven like `polish_builder_tab`, but
    without that function's Name-pin/Flag-Avail-chip assumptions, which
    don't apply to any of these (none have a Name, Flag or Avail column).
    Skips cleanly on an empty header -- Scratch/DK Upload/DKLineupsFinal
    always have one (their header is a fixed roster-slot or DK-export
    label row), but SoSComb is hand-built and could be blank before Sam
    has set it up for the week.
    """
    if not client.tab_exists(tab):
        return f"{tab}: not present -- skipped"

    header_rows = client.read_range(tab, f"A{header_row}:{header_row}")
    header = header_rows[0] if header_rows else []
    if not header:
        return f"{tab}: empty header row -- skipped"

    last_col = column_letter(len(header) - 1)
    client.format_range(tab, f"A{header_row}:{last_col}{header_row}", _HEADER_FMT)
    client.freeze(tab, rows=header_row)

    widths = {column_letter(i): BUILDER_WIDTHS.get(name, _GENERIC_COLUMN_PX) for i, name in enumerate(header)}
    client.set_column_widths(tab, widths)

    applied = apply_field_formats(client, tab, header, header_row=header_row, last_row=last_row)
    return f"{tab}: header styled, frozen, widths set, {applied} column(s) number-formatted"


def style_results(client: SheetsClient, tab: str = "Results", *, last_row: int) -> str:
    """Results is a season-long log with real win/loss and H2H data and
    had no header fill, no freeze, and no conditional formatting at all --
    the standard treatment plus the two columns worth a glance at rather
    than a read: `Cash Results` (TRUE/FALSE) chipped green/red, `H2H %`
    colour-scaled like every other percentage-of-success metric in the
    workbook. Column layout is `week.py`'s own documented one (Week,
    Cash Pts, Cash Line, Cash Results, H2H Entered, H2H Win, H2H %, Red,
    Blue, Black) but found by header name here too, not assumed.
    """
    if not client.tab_exists(tab):
        return f"{tab}: not present -- skipped"

    header_rows = client.read_range(tab, "A1:1")
    header = header_rows[0] if header_rows else []
    if not header:
        return f"{tab}: empty header row -- skipped"

    last_col = column_letter(len(header) - 1)
    client.clear_conditional_formats(tab)
    client.format_range(tab, f"A1:{last_col}1", _HEADER_FMT)
    client.freeze(tab, rows=1)

    widths = {column_letter(i): BUILDER_WIDTHS.get(name, _GENERIC_COLUMN_PX) for i, name in enumerate(header)}
    client.set_column_widths(tab, widths)

    applied = apply_field_formats(client, tab, header, header_row=1, last_row=last_row)

    if "Cash Results" in header:
        letter = column_letter(header.index("Cash Results"))
        a1 = f"{letter}2:{letter}{last_row}"
        client.add_boolean_rule(tab, a1, condition_type="TEXT_EQ", values=["TRUE"], fmt=_chip(OK_BG, OK_FG))
        client.add_boolean_rule(
            tab, a1, condition_type="TEXT_EQ", values=["FALSE"], fmt=_chip(CRIT_BG, CRIT_FG)
        )
    if "H2H %" in header:
        letter = column_letter(header.index("H2H %"))
        client.add_color_scale(
            tab, f"{letter}2:{letter}{last_row}", min_color=GRAD_MIN, mid_color=GRAD_MID, max_color=GRAD_MAX
        )

    return f"{tab}: header styled, frozen, {applied} column(s) number-formatted, Cash Results/H2H % coloured"


# The number of NFL teams -- a hard upper bound on how many data rows a
# hand-pasted Strength-of-Schedule tab can ever have, used only to bound
# how far down formatting is applied (harmless past the real data, same
# reasoning as EDGE_ROWS/POOL_RAW_ROWS above).
_SOS_MAX_ROWS = 32


def style_sos_tab(client: SheetsClient, tab: str) -> str:
    """SoSQB/SoSRB/SoSWr/SoSTE/SoSDef: hand-pasted Strength-of-Schedule
    data, current week only (see the Instructions tab). Standard header/
    freeze/widths, plus a colour scale on `Rank` -- REVERSED from every
    other rank/value column in the workbook, since here a LOW rank is
    the good matchup (an easy upcoming schedule), not a high one.
    Skips cleanly if nothing's been pasted yet this week -- these are
    entirely hand-built, not written by any `dfs` command, so an empty
    header is the normal state between weeks, not a bug.
    """
    if not client.tab_exists(tab):
        return f"{tab}: not present -- skipped"

    header_rows = client.read_range(tab, "A1:1")
    header = header_rows[0] if header_rows else []
    if not header:
        return f"{tab}: empty -- nothing pasted yet this week, skipped"

    last_col = column_letter(len(header) - 1)
    client.clear_conditional_formats(tab)
    client.format_range(tab, f"A1:{last_col}1", _HEADER_FMT)
    client.freeze(tab, rows=1)

    widths = {column_letter(i): BUILDER_WIDTHS.get(name, _GENERIC_COLUMN_PX) for i, name in enumerate(header)}
    client.set_column_widths(tab, widths)

    scaled = False
    if "Rank" in header:
        letter = column_letter(header.index("Rank"))
        # Reversed: max colour (green) at the MIN end, min colour (red)
        # at the MAX end -- a low rank is the good matchup here.
        client.add_color_scale(
            tab,
            f"{letter}2:{letter}{_SOS_MAX_ROWS + 1}",
            min_color=GRAD_MAX,
            mid_color=GRAD_MID,
            max_color=GRAD_MIN,
        )
        scaled = True

    return f"{tab}: header styled, frozen, widths set{', Rank scaled (reversed)' if scaled else ''}"


def style_tier23_tabs(
    client: SheetsClient,
    *,
    scratch_last_row: int,
    dk_upload_last_row: int,
    dk_lineups_final_last_row: int,
    results_last_row: int,
    sos_comb_last_row: int,
) -> list[str]:
    """Every Tier 2/3 tab in one call, each skipped cleanly if the tab
    doesn't exist or (for the hand-pasted SoS tabs) is currently empty."""
    results = [
        style_flat_tab(client, "Scratch", last_row=scratch_last_row),
        style_flat_tab(client, "DK Upload", last_row=dk_upload_last_row),
        style_flat_tab(client, "DKLineupsFinal", last_row=dk_lineups_final_last_row),
        style_results(client, last_row=results_last_row),
    ]
    for tab in ("SoSQB", "SoSRB", "SoSWr", "SoSTE", "SoSDef"):
        results.append(style_sos_tab(client, tab))
    results.append(style_flat_tab(client, "SoSComb", last_row=sos_comb_last_row))
    return results
