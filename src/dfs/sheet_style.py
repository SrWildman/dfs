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
formats before adding its own, so running twice leaves the same result as
running once rather than stacking duplicate rules.

`polish_edge()` applies EdgeRaw's frozen header and its three colour
scales (Leverage/CeilVal/GameEnv) as part of this pass -- the standalone
`dfs sheets format-edge` command that used to apply just those two effects
was removed once `polish` fully superseded it (nothing else called it, and
maintaining two implementations of the same three colour scales was pure
drift risk).
"""

from __future__ import annotations

from dfs.derived import EDGE_COLUMNS
from dfs.sheets import SheetsClient, column_letter

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

# ProjOwn is a raw 0-100 number in EdgeRaw (PlayerPoolRaw's Rstr% is the one
# that's a true fraction), so it gets a literal "%" suffix rather than a
# PERCENT format, which would multiply it by 100 again.
EDGE_NUMBER_FORMATS = {
    "Salary": _num("$#,##0", "CURRENCY"),
    "ProjPts": _num("0.0"),
    "ProjOwn": _num('0.0"%"'),
    "Ceiling": _num("0.0"),
    "Val": _num("0.00"),
    "CeilVal": _num("0.00"),
    "CeilPct": _num("0.0"),
    "Leverage": _num("0.0"),
    "GameEnv": _num("0.0"),
    "Wind": _num('0" mph"'),
    "LineMove": _num('"+"0.0;"-"0.0;0.0'),
}

# Collapsed by default: the join key and the two stadium descriptors, which
# matter to the code and almost never to you. Grouped, not hidden -- the
# +/- control above the column letters brings them straight back.
EDGE_COLUMN_GROUPS = [("Id", "Id"), ("Stadium", "Roof"), ("GameStart", "GameStart")]

EDGE_COLOR_SCALES = ("CeilVal", "Leverage", "GameEnv")

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


def _edge_letter(column_name: str) -> str | None:
    """Column letter for an EdgeRaw column, or None if that column isn't in
    EDGE_COLUMNS on this version of the CLI."""
    if column_name not in EDGE_COLUMNS:
        return None
    return column_letter(EDGE_COLUMNS.index(column_name))


def polish_edge(client: SheetsClient, edge_tab: str) -> str:
    """Direction B: make EdgeRaw readable without moving anything.

    Widths, a dark frozen header, Name pinned while you scroll right,
    number formats on every numeric column, the three colour scales, Flag
    and Avail as chips, and CeilPct greyed while Leverage is running on a
    proxy so the two identical-looking columns stop competing.
    """
    if not client.tab_exists(edge_tab):
        return f"{edge_tab}: not present -- skipped"

    last_col = column_letter(len(EDGE_COLUMNS) - 1)
    client.clear_conditional_formats(edge_tab)

    widths = {}
    for name, px in EDGE_WIDTHS.items():
        letter = _edge_letter(name)
        if letter:
            widths[letter] = px
    client.set_column_widths(edge_tab, widths)

    client.format_range(edge_tab, f"A1:{last_col}1", _HEADER_FMT)
    # Name and Position pinned: scroll to Wind and you still know who.
    name_idx = EDGE_COLUMNS.index("Name") if "Name" in EDGE_COLUMNS else 1
    client.freeze(edge_tab, rows=1, cols=name_idx + 1)

    for name, fmt in EDGE_NUMBER_FORMATS.items():
        letter = _edge_letter(name)
        if letter:
            client.format_range(edge_tab, f"{letter}2:{letter}{EDGE_ROWS}", fmt)

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

    for first, last in EDGE_COLUMN_GROUPS:
        a, b = _edge_letter(first), _edge_letter(last)
        if a and b:
            client.group_columns(edge_tab, a, b)

    return f"{edge_tab}: widths, frozen header, number formats, chips and colour scales applied"


# ---------------------------------------------------------------------------
# Player Pool / Lineups / PlayerPoolRaw -- number formats and freeze only
# ---------------------------------------------------------------------------

# Keyed by header TEXT, matched against whatever the tab's row 1 actually
# says, so this works on both the live layout (Venue at I, Ceil at L) and
# any other arrangement without knowing which is in front of it.
BUILDER_NUMBER_FORMATS = {
    "DK Sal": _num("$#,##0", "CURRENCY"),
    "O/U": _num("0.0"),
    "Spread": _num('"+"0.0;"-"0.0;0.0'),
    "Team Implied": _num("0.0"),
    "Pts": _num("0.0"),
    "Ceil": _num("0.0"),
    "Val": _num("0.00"),
    "Rstr%": _num("0.0%", "PERCENT"),
    "CeilVal": _num("0.00"),
    "CeilPct": _num("0.0"),
    "Leverage": _num("0.0"),
    "GameEnv": _num("0.0"),
    "Wind": _num('0" mph"'),
    "% of Rstr": _num("0.0%", "PERCENT"),
}

BUILDER_WIDTHS = {"Name": 165, "Pos.": 52, "Team": 54, "Opp.": 54, "Venue": 56, "DK Sal": 78}


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

    applied = 0
    data_start = header_row + 1
    for i, name in enumerate(header):
        fmt = BUILDER_NUMBER_FORMATS.get(name)
        if not fmt:
            continue
        letter = column_letter(i)
        client.format_range(tab, f"{letter}{data_start}:{letter}{last_row}", fmt)
        applied += 1

    pin_note = "Name pinned" if freeze_cols else "no column pin"
    return f"{tab}: header styled, {pin_note}, {applied} column(s) number-formatted"


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
    ("SoSComb", "decide"),
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

    for first, last in _BOARD_PANELS:
        client.format_range(tab, f"{first}5:{last}5", _PANEL_FMT)
        client.format_range(tab, f"{first}6:{last}6", _SUBHEAD_FMT)

    # Panel bodies: 12 rows for the two ranked panels, 14 for landmines.
    client.format_range(tab, "C7:C18", _num("0.0"))
    client.format_range(tab, "D7:D18", _num("0.00"))
    client.format_range(tab, "H7:H18", _num("$#,##0", "CURRENCY"))
    client.format_range(tab, "I7:I18", _num("0.00"))
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
    client.format_range(tab, "C2:C19", _num("0.0"))
    client.format_range(tab, "D2:D19", _num('"+"0.0;"-"0.0;0.0'))
    client.format_range(tab, "F2:G19", _num('0" mph"'))
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
    client.format_range(tab, "C2:C180", _num("$#,##0", "CURRENCY"))
    client.format_range(tab, "D2:D180", _num("0"))
    client.format_range(tab, "E2:G180", _num("0.0%", "PERCENT"))
    # Target is the one typed column in the whole workbook that this
    # module touches -- mark it as input rather than output.
    client.format_range(tab, "F2:F180", {"backgroundColor": _rgb("#FFFDF5"), "textFormat": {"italic": True}})
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
    client.format_range(tab, "C4:C60", _num('"+"0.0;"-"0.0;0.0'))
    client.add_color_scale(tab, "C4:C60", min_color=GRAD_MIN, mid_color=GRAD_MID, max_color=GRAD_MAX)
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
