"""The Compare tab: the Player card's measures for up to four players side by side. Pure: no I/O.

Four typed pickers, `B1:E1` (name dropdowns over EdgeRaw, exempt from protection), one column each. Rows are
the Player card's own `Field` specs (`player_card`), so a measure is defined in one place and the card and
this tab cannot describe different columns: `SECTIONS` below only chooses which fields to compare and which
way is better. Everything is a formula over EdgeRaw (columns from `derived.edge_sheet_letter`, never typed),
keyed by a helper row (`HELPER_ROW`: each picker's position within EdgeRaw, `MATCH`); a blank picker is a
blank column, never an error. The game-by-game rows and the props/signals lists stay on the Player card (the
L5 columns cover usage).

Highlight: per row, the best value among the picked players is shaded green when the row has a stated better
direction (`HIGH` / `LOW`; `None` = no clear direction, no shading). A value is shaded only when at least two
players have a number and they are not all equal (`best_columns` is the Python statement of the rule the
conditional format in `_highlight_formula` writes). Different positions get a one-line note, not a refusal:
a QB's targets are blank and a RB's price is not a TE's.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from dfs import player_card as pc
from dfs.weekly_reset import LINEUPS_NAME_BLOCKS

COMPARE_TAB = "Compare"
PICKER_COLUMNS = ["B", "C", "D", "E"]  # one per player
PICKER_ROW = 1
PICKER_RANGE = f"{PICKER_COLUMNS[0]}{PICKER_ROW}:{PICKER_COLUMNS[-1]}{PICKER_ROW}"
HELPER_ROW = 2  # each picker's position within EdgeRaw's data rows (MATCH), "" when blank / not found
STATUS_ROW = 3  # "pick up to four players" / the different-positions note
COLUMN_COUNT = 1 + len(PICKER_COLUMNS)  # A .. E
LAST_COLUMN = chr(ord("A") + COLUMN_COUNT - 1)
LABEL_PX, VALUE_PX = 170, 130

HIGH, LOW = "high", "low"  # which end of a row is better; None = no clear direction

LINEUPS_LABEL, EXPOSURE_LABEL = "In your lineups", "Exposure"


@dataclass(frozen=True)
class Row:
    """One compared measure: a Player-card `Field` and which way is better."""

    spec: pc.Field
    better: str | None = HIGH


def _row(label: str, column: str, fmt: str, better: str | None = HIGH) -> Row:
    return Row(pc.Field(label, pc.EDGE, column, fmt), better)


# (section title, [Row, ...]). Directions: shaded = the best of the picked players. LOW rows are the ones
# where "better" is not "bigger" -- salary (cheaper), Bust%, ownership (fewer other lineups have him: the
# leverage view) and OppPosRank (1 = softest matchup). Spread, wind, team/status text and the lineup counts
# have no clear direction and are never shaded.
SECTIONS: list[tuple[str, list[Row]]] = [
    (
        "PLAYER",
        [
            _row("Team", "Team", pc.TEXT, None),
            _row("Pos", "Position", pc.TEXT, None),
            _row("Salary", "Salary", pc.MONEY, LOW),
            _row("Opp", "Opp", pc.TEXT, None),
            _row("Kickoff (ET)", "Kickoff", pc.TEXT, None),
            _row("Status", "Avail", pc.TEXT, None),
            _row("Flags", "Flags", pc.TEXT, None),
        ],
    ),
    (
        "PROJECTIONS",
        [
            _row("TFFB", "ProjPts", pc.PTS),
            _row("Sleeper", "SleeperPts", pc.PTS),
            _row("FantasyPros", "FantasyProsPts", pc.PTS),
            _row("ESPN", "EspnPts", pc.PTS),
            _row("Props", "PropPts", pc.PTS),
            _row("AggPts", "AggPts", pc.PTS),
            _row("CalPts", "CalPts", pc.PTS),
            _row("Hit3x%", "Hit3x%", pc.PCT100),
            _row("Boom%", "Boom%", pc.PCT100),
            _row("Bust%", "Bust%", pc.PCT100, LOW),
            _row("Ceil (model)", "CeilM", pc.PTS),
            _row("Val", "Val", pc.PTS),
            _row("ValAdj", "ValAdj", pc.PTS),
        ],
    ),
    (
        "OWNERSHIP",
        [
            _row("Own% (TFFB)", "Own%", pc.PCT1, LOW),
            _row("GppOwn (shadow)", "GppOwn", pc.PCT1, LOW),
            _row("CashOwn (shadow)", "CashOwn", pc.PCT1, LOW),
            _row("Leverage", "Leverage", pc.PTS),
        ],
    ),
    (
        "USAGE",
        [
            _row("Snap% L5", "Snap% L5", pc.PCT0),
            _row("Targets L5", "Tgt L5", pc.PTS),
            _row("Carries L5", "Car L5", pc.PTS),
            _row("DK pts L5", "DK L5", pc.PTS),
            _row("Target share", "Tgt%", pc.PCT1),
            _row("xFP / game", "xFP Szn", pc.PTS),
            _row("xFP last 3", "xFP L3", pc.PTS),
            _row("xFP trend", "xFP trend", pc.ZSCORE),
        ],
    ),
    (
        "OPPORTUNITY PER DOLLAR",
        [
            _row("TPRR~ (proxy)", "TPRR~", pc.PCT0),
            _row("xFP per $1k", "xFP/$", pc.PTS2),
            _row("Targets per $1k", "Tgt/$", pc.PTS2),
            _row("HVT per $1k (RB)", "HVT/$", pc.PTS2),
            _row("PriceGap (SD)", "PriceGap", pc.ZSCORE),
        ],
    ),
    (
        "MATCHUP AND GAME",
        [
            _row("Total", "OverUnder", pc.PTS),
            _row("Spread", "Spread", pc.PTS, None),
            Row(pc.IMPLIED, HIGH),
            _row("OppPosRank", "OppPosRank", pc.INT, LOW),
            _row("OppEPA", "OppEPA", "pts3"),
            _row("Wind (mph)", "Wind", pc.PTS, None),
        ],
    ),
]

SAME_POSITION_NOTE = (
    "Different positions picked: compare within a position (a QB has no targets, and a RB's price is not a "
    "TE's)."
)
PICK_NOTE = f"Pick up to four players in {PICKER_RANGE}: type a name or choose from the list."
HIGHLIGHT_NOTE = (
    "Green = the best of the picked players, when two or more have a number and they differ. Lowest is best "
    "for Salary, Bust%, Own% / GppOwn / CashOwn (fewer lineups share him) and OppPosRank (1 = softest); "
    "highest for everything else. Spread, wind, team, status, flags and your lineups are never shaded."
)
NOTES = {
    "PROJECTIONS": (
        "Sources side by side. TFFB, Sleeper and FantasyPros feed AggPts; CalPts is the calibrated blend; "
        "ESPN and Props are shadow sources scored in Model Check, not in CalPts yet."
    ),
    "OWNERSHIP": pc.OWNERSHIP_NOTE,
    "USAGE": (
        "Last five games he played (Snap%, targets, carries, real DK points) and this season's expected "
        "fantasy points (xFP). The game by game rows are on the Player tab."
    ),
    "OPPORTUNITY PER DOLLAR": pc.OPPORTUNITY_NOTE,
    "MATCHUP AND GAME": pc.MATCHUP_NOTE,
}


# ---------------------------------------------------------------------------------------------
# The highlight rule: stated once in Python, written once as a conditional format
# ---------------------------------------------------------------------------------------------


def _number(value: object) -> float | None:
    if value is None or isinstance(value, bool) or value == "":
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return None if pd.isna(number) else number


def best_columns(values: list[object], better: str | None) -> list[int]:
    """Indexes of the values to shade. Nothing when the row has no direction, fewer than two players have a
    number, or they are all equal (then there is no best). Ties for the best are all shaded."""
    numbers = [_number(v) for v in values]
    present = [n for n in numbers if n is not None]
    if better is None or len(present) < 2 or max(present) == min(present):
        return []
    target = max(present) if better == HIGH else min(present)
    return [i for i, n in enumerate(numbers) if n is not None and n == target]


def _highlight_formula(row: int, better: str) -> str:
    """The conditional-format formula for one row, relative to its first picker column: shade a cell holding
    the row's best number when two or more players have one and they differ (`best_columns` as a formula;
    the direction picks MAX or MIN)."""
    first, last = PICKER_COLUMNS[0], PICKER_COLUMNS[-1]
    span = f"${first}{row}:${last}{row}"
    pick = "MAX" if better == HIGH else "MIN"
    return (
        f"=AND(ISNUMBER({first}{row}),COUNT({span})>=2,MAX({span})<>MIN({span}),{first}{row}={pick}({span}))"
    )


# ---------------------------------------------------------------------------------------------
# The layout
# ---------------------------------------------------------------------------------------------


@dataclass
class Layout:
    rows: list[list] = field(default_factory=list)
    title_rows: list[int] = field(default_factory=list)  # 1-based
    section_rows: list[int] = field(default_factory=list)
    note_rows: list[int] = field(default_factory=list)
    label_rows: list[int] = field(default_factory=list)  # the measure labels (column A) are bold
    patterns: list[tuple[str, str]] = field(default_factory=list)  # (A1 range, number pattern)
    highlights: list[tuple[int, str]] = field(default_factory=list)  # (1-based row, HIGH | LOW)

    def add(self, cells: list) -> int:
        self.rows.append([*cells, *[""] * (COLUMN_COUNT - len(cells))])
        return len(self.rows)


def _row_ref(column: str) -> str:
    return f"{column}${HELPER_ROW}"


def _name_ref(column: str) -> str:
    return f"{column}${PICKER_ROW}"


def _picked(column: str, formula: str) -> str:
    """Blank when the picker is empty (a MATCH on an empty cell is not guaranteed to miss)."""
    return f'=IF({_name_ref(column)}="","",{formula[1:]})'


def build_layout(
    *,
    edge_tab: str,
    lineups_tab: str,
    exposure_tab: str,
    exposure: dict[str, str] | None,
    lineup_count_cell: str,
    names: list[str] | None = None,
) -> Layout:
    """Every row of the Compare tab. `names` keeps the typed pickers through a rebuild."""
    lay = Layout()
    typed = [*(names or []), *[""] * len(PICKER_COLUMNS)][: len(PICKER_COLUMNS)]
    lay.add(["Compare", *typed])
    lay.title_rows.append(PICKER_ROW)

    name_range = pc.edge_range(edge_tab, "Name")
    lay.add(
        [
            "EdgeRaw row (helper)",
            *[f'=IFERROR(MATCH({_name_ref(c)},{name_range},0),"")' for c in PICKER_COLUMNS],
        ]
    )
    assert len(lay.rows) == HELPER_ROW

    lay.add([""])  # the status row: filled in below, once the Pos row's position is known
    lay.note_rows.append(STATUS_ROW)
    lay.note_rows.append(lay.add([HIGHLIGHT_NOTE]))

    pos_row = 0

    def measure(label: str, formulas: list[str], fmt: str, better: str | None) -> int:
        row = lay.add([label, *formulas])
        lay.label_rows.append(row)
        lay.patterns.append((f"{PICKER_COLUMNS[0]}{row}:{PICKER_COLUMNS[-1]}{row}", pc.SHEET_PATTERNS[fmt]))
        if better is not None:
            lay.highlights.append((row, better))
        return row

    for title, rows in SECTIONS:
        lay.add([])
        lay.section_rows.append(lay.add([title]))
        for item in rows:
            formulas = [pc.formula(item.spec, edge_tab, _row_ref(c)) for c in PICKER_COLUMNS]
            row = measure(item.spec.label, formulas, item.spec.fmt, item.better)
            if item.spec.column == "Position":
                pos_row = row
        if title in NOTES:
            lay.note_rows.append(lay.add([NOTES[title]]))
    first, last = PICKER_COLUMNS[0], PICKER_COLUMNS[-1]
    pos_span = f"${first}${pos_row}:${last}${pos_row}"
    picked = f"${first}${PICKER_ROW}:${last}${PICKER_ROW}"
    distinct = f'SUMPRODUCT(({pos_span}<>"")/COUNTIF({pos_span},{pos_span}&""))'
    lay.rows[STATUS_ROW - 1][0] = (
        f'=IF(COUNTA({picked})=0,"{PICK_NOTE}",IF({distinct}>1,"{SAME_POSITION_NOTE}",""))'
    )

    lay.add([])
    lay.section_rows.append(lay.add(["IN YOUR LINEUPS"]))
    measure(
        LINEUPS_LABEL,
        [pc.in_lineups_formula(lineups_tab, name_ref=_name_ref(c)) for c in PICKER_COLUMNS],
        pc.INT,
        None,
    )
    if exposure is None:
        measure(f"{EXPOSURE_LABEL} (no Exposure tab)", [""] * len(PICKER_COLUMNS), pc.PCT0, None)
    else:
        measure(
            EXPOSURE_LABEL,
            [
                _picked(c, pc.exposure_formula(exposure_tab, exposure, "Exposure", _name_ref(c)))
                for c in PICKER_COLUMNS
            ],
            pc.PCT0,
            None,
        )
    lay.note_rows.append(
        lay.add(
            [
                f"Counted from the Lineups tab's name cells ({len(LINEUPS_NAME_BLOCKS)} blocks); Exposure is "
                f"Exposure's own column. Divisor: Exposure!{lineup_count_cell}."
            ]
        )
    )
    return lay


def highlight_specs(layout: Layout) -> list[dict]:
    """The conditional-format rules (`SheetsClient.add_boolean_rules` specs), one per shaded row."""
    from dfs.sheet_style import OK_BG, OK_FG

    return [
        {
            "a1_range": f"{PICKER_COLUMNS[0]}{row}:{PICKER_COLUMNS[-1]}{row}",
            "condition_type": "CUSTOM_FORMULA",
            "values": [_highlight_formula(row, better)],
            "fmt": {"backgroundColor": OK_BG, "textFormat": {"bold": True, "foregroundColor": OK_FG}},
        }
        for row, better in layout.highlights
    ]


def unprotected_ranges() -> list[str]:
    """The four typed cells."""
    return [PICKER_RANGE]


def shifted_refs(expected: list[list], found: list[list], edge_tab: str):
    """`player_card.shifted_refs` for this tab: every cell whose EdgeRaw letters differ from a fresh build."""
    return pc.shifted_refs(expected, found, edge_tab)


# ---------------------------------------------------------------------------------------------
# The text side: the same specs, read from the EdgeRaw frame (the offline check, `render_text`)
# ---------------------------------------------------------------------------------------------


def _player(edge: pd.DataFrame, name: str) -> pd.Series | None:
    hit = edge[edge["Name"].str.casefold() == name.casefold()]
    return None if hit.empty else hit.iloc[0]


def values_for(item: Row, players: list[pd.Series | None]) -> list[object]:
    return [None if p is None else pc.value(item.spec, p, None) for p in players]


def render_text(names: list[str], edge: pd.DataFrame, *, lineups_note: str = "") -> str:
    """The tab as plain text for the names picked (blank names are blank columns), from the same specs the
    formulas come from. `*` marks the shaded value of a row."""
    names = [n for n in names if n][: len(PICKER_COLUMNS)]
    players = [_player(edge, n) for n in names]
    lines: list[str] = []
    positions = {str(p["Position"]) for p in players if p is not None}
    if not names:
        return PICK_NOTE
    headers = [
        "",
        *[n if p is not None else f"{n} (not on EdgeRaw)" for n, p in zip(names, players, strict=True)],
    ]
    table: list[list[str]] = [headers]
    sections: list[tuple[int, str]] = []
    for title, rows in SECTIONS:
        sections.append((len(table), title))
        for item in rows:
            values = values_for(item, players)
            shaded = set(best_columns(values, item.better))
            cells = [item.spec.label]
            for i, v in enumerate(values):
                text = pc.format_value(item.spec.fmt, v)
                cells.append(f"{text} *" if i in shaded else text)
            table.append(cells)
    widths = [max(len(r[i]) for r in table) for i in range(len(headers))]
    fmt = lambda r: "  ".join(c.ljust(widths[i]) for i, c in enumerate(r)).rstrip()  # noqa: E731
    starts = dict(sections)
    lines.append(fmt(table[0]))
    for index, row in enumerate(table[1:], start=1):
        if index in starts:
            lines.append("")
            lines.append(starts[index])
        lines.append("  " + fmt(row))
    if len(positions) > 1:
        lines += ["", SAME_POSITION_NOTE]
    lines += ["", HIGHLIGHT_NOTE]
    if lineups_note:
        lines.append(lineups_note)
    return "\n".join(lines)
