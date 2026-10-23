"""The Player card: pick a player in `B1`, see everything about him on one screen. Pure: frames in, cells out.

The `Player` tab (right after Edge Finder) is FORMULAS keyed off the one typed cell, `B1` (a dropdown over
EdgeRaw's names). A helper pair in row 1 turns the name into EdgeRaw's row (`ROW_CELL`) and the join key
(`KEY_CELL`); every field below is an `INDEX` into an EdgeRaw column, a `PlayerCardRaw` column (one row per
player: his last five games, game by game) or a `PlayerCardRows` row (the variable-length lists: props,
signals). Python writes the two hidden raw tabs on every `dfs sync` (`sheet_player_card.refresh_raw`); it
writes the card itself only in `dfs setup player-card`.
EdgeRaw is the one place for every per-player NUMBER (2026-10-21): Sleeper's and FantasyPros' projections, the
kickoff, the last-five-game averages and the opportunity-per-dollar measures (`opportunity.py`) are EdgeRaw
columns the card reads like any other; the hidden raw tab keeps only what is a list per player (the games).

One spec drives both the sheet and the text render. A `Field` names where a value lives (an EdgeRaw column, a
raw column) and how it is shown; `formula()` turns it into the cell formula, `value()` reads the same thing
from the frames, so `render_text` (the offline check) cannot describe a different field than the sheet.
Column letters are never typed: EdgeRaw's come from `derived.edge_sheet_letter`, the raw tabs' from
`raw_letter` / `rows_index` below.

What each block is, said on the tab: the projections side by side are different sources (TFFB, Sleeper,
FantasyPros, ESPN and the props' own scoring), only TFFB / Sleeper / FantasyPros feed `AggPts` and `CalPts` is
the calibrated blend; `GppOwn` / `CashOwn` / `OurOwn` are model estimates (a shadow), not published ownership;
the usage rows are nflverse's box score and play-by-play, last five games he PLAYED; the signal records are
Model Check's, as of the last `dfs results update`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import pandas as pd

from dfs import usage_metrics
from dfs.derived import edge_sheet_letter
from dfs.player_join import normalize_name, normalize_position
from dfs.weekly_reset import LINEUPS_NAME_BLOCKS

PLAYER_CARD_TAB = "Player"
RAW_TAB = "PlayerCardRaw"
ROWS_TAB = "PlayerCardRows"
NAME_CELL = "B1"  # the one typed cell
ROW_CELL = "H1"  # helper: the player's position within EdgeRaw's data rows (MATCH), "" when not found
KEY_CELL = "I1"  # helper: his `NameKey`, the join key into the raw tabs
COLUMN_COUNT = 9  # A .. I
GAMES = 5
MAX_PROPS = 8
MAX_SIGNALS = 4
INSIDE_FIVE = 5  # "goal-line looks": targets + carries from inside the opponent's 5
LAST_COLUMN = chr(ord("A") + COLUMN_COUNT - 1)

# ---------------------------------------------------------------------------------------------
# The raw tabs' shapes (positions come from these lists, never from typed letters)
# ---------------------------------------------------------------------------------------------

GAME_FIELDS = ["Wk", "Opp", "Snap%", "Tgt", "Car", "Tgt%", "RZ", "In5", "DK"]
RAW_COLUMNS = [
    "NameKey",
    *[f"{name}{game}" for game in range(1, GAMES + 1) for name in GAME_FIELDS],
]
ROWS_COLUMNS = ["Key", "A", "B", "C", "D"]
LOG_COLUMNS = ["NameKey", "Name", "Team", "Position", "Back", *GAME_FIELDS]


def raw_index(name: str) -> int:
    """0-based position of a `PlayerCardRaw` column."""
    return RAW_COLUMNS.index(name)


def _letter(index: int) -> str:
    letters, n = "", index + 1
    while n:
        n, rem = divmod(n - 1, 26)
        letters = chr(65 + rem) + letters
    return letters


def raw_letter(name: str) -> str:
    return _letter(raw_index(name))


def rows_index(name: str) -> int:
    """1-based VLOOKUP index of a `PlayerCardRows` column."""
    return ROWS_COLUMNS.index(name) + 1


# ---------------------------------------------------------------------------------------------
# Fields: one spec for the formula and for the text
# ---------------------------------------------------------------------------------------------

PTS, PCT0, PCT1, PCT100, INT, MONEY, TEXT = "pts", "pct0", "pct1", "pct100", "int", "money", "text"
PTS2, ZSCORE = "pts2", "z"
SHEET_PATTERNS = {
    PTS: "0.0",
    PCT0: "0%",
    PCT1: "0.0%",
    PCT100: '0.0"%"',  # EdgeRaw's Hit3x% / Boom% / Bust% are 0-100 numbers, not fractions
    INT: "0",
    MONEY: "$#,##0",
    TEXT: "@",
    "pts3": "0.000",
    "pts2": "0.00",
    "z": "+0.0;-0.0;0.0",
}
EDGE, RAW, DERIVED = "edge", "raw", "derived"


@dataclass(frozen=True)
class Field:
    label: str
    source: str  # EDGE (an EdgeRaw column), RAW (a PlayerCardRaw column) or DERIVED
    column: str  # the column's name in that source
    fmt: str = TEXT
    note: str = ""


def _edge(label: str, column: str, fmt: str = TEXT, note: str = "") -> Field:
    return Field(label, EDGE, column, fmt, note)


def _raw(label: str, column: str, fmt: str = TEXT, note: str = "") -> Field:
    return Field(label, RAW, column, fmt, note)


IMPLIED = Field("Implied (team)", DERIVED, "Implied", PTS, "(total - spread) / 2; spread is the team's own")

# (section title, [stripe, ...], note) -- a stripe is one row of labels over one row of values
HEADER_STRIPE = [
    _edge("Team", "Team"),
    _edge("Pos", "Position"),
    _edge("Salary", "Salary", MONEY),
    _edge("Opp", "Opp"),
    _edge("Kickoff (ET)", "Kickoff"),
    _edge("Status", "Avail"),
    _edge("Flags", "Flags"),
]
PROJECTION_STRIPES = [
    [
        _edge("TFFB", "ProjPts", PTS),
        _edge("Sleeper", "SleeperPts", PTS),
        _edge("FantasyPros", "FantasyProsPts", PTS),
        _edge("ESPN", "EspnPts", PTS),
        _edge("Props", "PropPts", PTS),
        _edge("AggPts", "AggPts", PTS),
        _edge("CalPts", "CalPts", PTS),
    ],
    [
        _edge("Hit3x%", "Hit3x%", PCT100),
        _edge("Boom%", "Boom%", PCT100),
        _edge("Bust%", "Bust%", PCT100),
        _edge("Floor", "Floor", PTS),
        _edge("Ceil (model)", "CeilM", PTS),
        _edge("Ceil (TFFB)", "Ceiling", PTS),
    ],
    [
        _edge("Val", "Val", PTS),
        _edge("ValAdj", "ValAdj", PTS),
        _edge("CeilVal", "CeilVal", PTS),
        _edge("xFP/G", "xFP/G", PTS),
    ],
]
PROJECTION_NOTE = (
    "Sources side by side. TFFB, Sleeper and FantasyPros feed AggPts; CalPts is the calibrated blend; "
    "ESPN and Props are shadow sources scored in Model Check, not in CalPts yet."
)
OWNERSHIP_STRIPE = [
    _edge("Own% (TFFB)", "Own%", PCT1),
    _edge("GppOwn (shadow)", "GppOwn", PCT1),
    _edge("CashOwn (shadow)", "CashOwn", PCT1),
    _edge("OurOwn (shadow)", "OurOwn", PCT1),
    _edge("Leverage", "Leverage", PTS),
    _edge("Ownership", "OwnStatus"),
    _edge("Avail", "Avail"),
]
OWNERSHIP_NOTE = (
    "Own% is TFFB's projection once it publishes. GppOwn / CashOwn are estimates of what your contests own, "
    "OurOwn what your GPPs would own on CalPts: models, not published numbers."
)
MATCHUP_STRIPES = [
    [
        _edge("Total", "OverUnder", PTS),
        _edge("Spread", "Spread", PTS),
        IMPLIED,
        _edge("GameEnv", "GameEnv", PTS),
        _edge("Pace", "Pace", PTS),
        _edge("PROE", "PROE", PTS),
        _edge("Expl%", "Expl%", PTS),
    ],
    [
        _edge("OppPosRank", "OppPosRank", INT),
        _edge("OppEPA (whole defense)", "OppEPA", "pts3"),
        _edge("Stadium", "Stadium"),
        _edge("Roof", "Roof"),
        _edge("Wind (mph)", "Wind", PTS),
    ],
]
MATCHUP_NOTE = (
    "OppPosRank: 1 = softest. OppEPA: higher = softer for every position. OppPosRank is that defense against "
    "his position, OppEPA the defense overall, so they can disagree."
)
USAGE_HEADER = ["Game", "Opp", "Snap%", "Targets", "Carries", "Tgt share", "RZ looks", "Inside 5", "DK pts"]
USAGE_FORMATS = [TEXT, TEXT, PCT0, INT, INT, PCT0, INT, INT, PTS]
# Each Average-row cell that EdgeRaw carries: header column -> its `derived.LAST5_COLUMNS` name. The others
# (target share, red-zone and goal-line looks) have no EdgeRaw column and stay a formula over the five rows.
USAGE_AVERAGE_EDGE = {"Snap%": "Snap% L5", "Targets": "Tgt L5", "Carries": "Car L5", "DK pts": "DK L5"}
OPPORTUNITY_STRIPES = [
    [
        _edge("TPRR~ (proxy)", "TPRR~", PCT0),
        _edge("TPRR~ last 3", "TPRR~ L3", PCT0),
        _edge("xFP / game", "xFP Szn", PTS),
        _edge("xFP trend", "xFP trend", ZSCORE),
        _edge("Receiving share of xFP", "xFP Rec%", PCT0),
    ],
    [
        _edge("xFP per $1k", "xFP/$", PTS2),
        _edge("Targets per $1k", "Tgt/$", PTS2),
        _edge("HVT per $1k (RB)", "HVT/$", PTS2),
        _edge("PriceGap (SD)", "PriceGap", ZSCORE),
    ],
]
OPPORTUNITY_NOTE = (
    "This season only, games he played before this slate. TPRR~ is a PROXY for targets per route run: "
    "routes are estimated as his offensive snaps x his team's dropback rate that game (no free in-season "
    "route counts exist). PriceGap = how many SDs more expected scoring (xFP) his role has than a typical "
    "player at his salary and position: positive = the opportunity is bigger than the price. Context, not "
    "a projection; scored in Model Check."
)
USAGE_NOTE = (
    "His last five games played (nflverse box score and play-by-play). RZ looks = targets + carries from "
    "inside the opponent's 20; Inside 5 = the same from inside the 5. Blank = not recorded or not applicable."
)
PROPS_HEADER = ["Market", "Line", "Over / Yes prob", "Books"]
PROPS_NOTE = "The market's own line and its probability with the margin removed, median across books."
SIGNALS_NOTE = (
    "Each signal with Model Check's record for it: average actual minus projection, over every flagged "
    "player-game so far. A signal is information, not a recommendation."
)
LINEUPS_LABELS = ["In lineups", "Which", "Exposure", "Target", "vs Target"]


# ---------------------------------------------------------------------------------------------
# The sheet side of a field: its formula
# ---------------------------------------------------------------------------------------------


def _abs(cell: str) -> str:
    """`H1` -> `$H$1`."""
    letters = cell.rstrip("0123456789")
    return f"${letters}${cell[len(letters) :]}"


NAME_REF, ROW_REF, KEY_REF = _abs(NAME_CELL), _abs(ROW_CELL), _abs(KEY_CELL)


def _q(tab: str) -> str:
    return f"'{tab}'" if (" " in tab or "-" in tab) else tab


def edge_range(edge_tab: str, column: str) -> str:
    letter = edge_sheet_letter(column)
    return f"{_q(edge_tab)}!${letter}$2:${letter}"


def edge_letters(formula: str, edge_tab: str) -> list[str]:
    """The EdgeRaw column letters a formula reads (`EdgeRaw!$BW$2:$BW` -> `BW`), in order."""
    return re.findall(re.escape(_q(edge_tab)) + r"!\$([A-Z]+)\$2:\$[A-Z]+", str(formula))


def shifted_refs(
    expected: list[list], found: list[list], edge_tab: str
) -> list[tuple[str, list[str], list[str]]]:
    """Every card cell whose EdgeRaw column letters differ from what a fresh build writes: `(cell, found
    letters, expected letters)`. The card is built from `derived.edge_sheet_letter`, so a difference means
    EdgeRaw's columns moved (or grew) under it and Sheets shifted the references: the cell reads blank or
    the wrong column with no error."""
    out = []
    for r, want_row in enumerate(expected):
        have_row = found[r] if r < len(found) else []
        for c, want_cell in enumerate(want_row):
            want = edge_letters(want_cell, edge_tab)
            if not want:
                continue
            have = edge_letters(have_row[c], edge_tab) if c < len(have_row) else []
            if have != want:
                out.append((f"{chr(ord('A') + c)}{r + 1}", have, want))
    return out


def raw_range(column: str) -> str:
    letter = raw_letter(column)
    return f"{RAW_TAB}!${letter}$2:${letter}"


def _blank_safe(expr: str) -> str:
    """Sheets shows a formula that points at an empty cell as 0 in some contexts: say blank explicitly."""
    return f'=IFERROR(IF({expr}="","",{expr}),"")'


def _with_row(body: str, row_ref: str = ROW_REF) -> str:
    """Guard a formula that INDEXes by `row_ref`: with no player picked the helper is "" and
    `INDEX(range, "")` spills the whole column (#REF! across the tab), so say blank instead."""
    return f'=IF({row_ref}="","",{body})'


def formula(spec: Field, edge_tab: str, row_ref: str = ROW_REF) -> str:
    """The cell formula for a field, keyed off the helper cell `row_ref` (the Player card's `$H$1`; the
    Compare tab passes each picker's own `B$2`). `RAW` fields are the Player card's alone."""
    if spec.source == EDGE:
        return _with_row(_blank_safe(f"INDEX({edge_range(edge_tab, spec.column)},{row_ref})")[1:], row_ref)
    if spec.source == RAW:
        match = f"MATCH({KEY_REF},{raw_range('NameKey')},0)"
        return _blank_safe(f"INDEX({raw_range(spec.column)},{match})")
    if spec.column == IMPLIED.column:
        total = f"INDEX({edge_range(edge_tab, 'OverUnder')},{row_ref})"
        spread = f"INDEX({edge_range(edge_tab, 'Spread')},{row_ref})"
        return _with_row(f'IFERROR(IF(OR({total}="",{spread}=""),"",({total}-{spread})/2),"")', row_ref)
    raise KeyError(spec.column)


def row_lookup(kind: str, n: int, column: str) -> str:
    """One cell of a keyed `PlayerCardRows` row: `<NameKey>|<kind>|<n>`, column `column`."""
    key = f'{KEY_REF}&"|{kind}|{n}"'
    span = f"{ROWS_TAB}!$A:${_letter(len(ROWS_COLUMNS) - 1)}"
    expr = f"VLOOKUP({key},{span},{rows_index(column)},FALSE)"
    return f'=IFERROR(IF({expr}="","",{expr}),"")'


def meta_lookup(kind: str, column: str) -> str:
    """The `meta|<kind>` row (the props pull's age, the signal records' date): not keyed by player."""
    span = f"{ROWS_TAB}!$A:${_letter(len(ROWS_COLUMNS) - 1)}"
    return f'=IFERROR(VLOOKUP("meta|{kind}",{span},{rows_index(column)},FALSE),"")'


# ---------------------------------------------------------------------------------------------
# Lineups / Exposure
# ---------------------------------------------------------------------------------------------


def lineup_terms(lineups_tab: str, blocks=LINEUPS_NAME_BLOCKS, name_ref: str = NAME_REF) -> list[str]:
    name = name_ref
    return [f"(COUNTIF({_q(lineups_tab)}!$A${s}:$A${e},{name})>0)" for s, e in blocks]


def in_lineups_formula(lineups_tab: str, blocks=LINEUPS_NAME_BLOCKS, name_ref: str = NAME_REF) -> str:
    return f'=IF({name_ref}="","",' + "+".join(lineup_terms(lineups_tab, blocks, name_ref)) + ")"


def which_lineups_formula(lineups_tab: str, blocks=LINEUPS_NAME_BLOCKS) -> str:
    """`L3, L7`: the same `L<number>` the DK Upload dropdown uses (`entries.SheetLineup.label`)."""
    name = NAME_REF
    parts = [
        f'IF(COUNTIF({_q(lineups_tab)}!$A${s}:$A${e},{name})>0,"L{i}","")'
        for i, (s, e) in enumerate(blocks, start=1)
    ]
    return f'=IF({NAME_REF}="","",TEXTJOIN(", ",TRUE,{",".join(parts)}))'


def exposure_columns(header: list) -> dict[str, str] | None:
    """Exposure's column letters read from ITS OWN header row (`sheet_views.build_exposure` writes it): None
    when a column the card needs is missing, so the block says so instead of pointing at the wrong column."""
    cells = [str(c).strip() for c in header]
    wanted = {"Name": "Name", "# Lineups": "# Lineups", "Exposure": "Exposure", "Target": "Target"}
    if not all(w in cells for w in wanted.values()):
        return None
    return {key: _letter(cells.index(label)) for key, label in wanted.items()}


def exposure_formula(
    exposure_tab: str, columns: dict[str, str], column: str, name_ref: str = NAME_REF
) -> str:
    rng = lambda c: f"{_q(exposure_tab)}!${columns[c]}$2:${columns[c]}"  # noqa: E731
    expr = f"INDEX({rng(column)},MATCH({name_ref},{rng('Name')},0))"
    return f'=IFERROR(IF({expr}="","",{expr}),"")'


# ---------------------------------------------------------------------------------------------
# The layout: rows of cells, with the formatting hints the writer applies
# ---------------------------------------------------------------------------------------------


@dataclass
class Layout:
    rows: list[list] = field(default_factory=list)
    title_rows: list[int] = field(default_factory=list)  # 1-based
    section_rows: list[int] = field(default_factory=list)
    label_rows: list[int] = field(default_factory=list)
    note_rows: list[int] = field(default_factory=list)
    patterns: list[tuple[str, str]] = field(default_factory=list)  # (A1 range, number pattern)

    def add(self, cells: list) -> int:
        self.rows.append([*cells, *[""] * (COLUMN_COUNT - len(cells))])
        return len(self.rows)


def build_layout(
    *,
    edge_tab: str,
    lineups_tab: str,
    exposure_tab: str,
    exposure: dict[str, str] | None,
    lineup_count_cell: str,
    name: str = "",
) -> Layout:
    """Every row of the Player tab. `name` keeps the typed `B1` through a rebuild."""
    lay = Layout()
    row_match = f'=IFERROR(MATCH({NAME_REF},{edge_range(edge_tab, "Name")},0),"")'
    key_expr = _with_row(f'IFERROR(INDEX({edge_range(edge_tab, "NameKey")},{ROW_REF}),"")')
    lay.add(["Player", name, "", "", "", "", "helpers ->", row_match, key_expr])
    lay.title_rows.append(1)

    def section(title: str) -> None:
        lay.add([])
        lay.section_rows.append(lay.add([title]))

    def note(text: str) -> None:
        lay.note_rows.append(lay.add([text]))

    def stripe(fields: list[Field]) -> None:
        lay.label_rows.append(lay.add([f.label for f in fields]))
        values_row = lay.add([formula(f, edge_tab) for f in fields])
        for i, f in enumerate(fields):
            lay.patterns.append((f"{_letter(i)}{values_row}", SHEET_PATTERNS[f.fmt]))

    lay.add([f'=IF({NAME_REF}="","Pick a player in {NAME_CELL}: type a name or choose from the list.","")'])
    lay.note_rows.append(2)
    section("PLAYER")
    stripe(HEADER_STRIPE)

    section("PROJECTIONS")
    for fields in PROJECTION_STRIPES:
        stripe(fields)
    note(PROJECTION_NOTE)

    section("OWNERSHIP")
    stripe(OWNERSHIP_STRIPE)
    note(OWNERSHIP_NOTE)

    section("USAGE, LAST 5 GAMES")
    lay.label_rows.append(lay.add(USAGE_HEADER))
    first = len(lay.rows) + 1
    for game in range(1, GAMES + 1):
        cells = [f'=IFERROR(IF({_raw_cell(f"Wk{game}")}="","","Wk "&{_raw_cell(f"Wk{game}")}),"")']
        for name_ in GAME_FIELDS[1:]:
            cells.append(_blank_safe(_raw_cell(f"{name_}{game}")))
        row = lay.add(cells)
        for i, fmt in enumerate(USAGE_FORMATS):
            lay.patterns.append((f"{_letter(i)}{row}", SHEET_PATTERNS[fmt]))
    last = len(lay.rows)
    avg = ["Average"]
    for i in range(1, COLUMN_COUNT):
        col = _letter(i)
        if USAGE_HEADER[i] in USAGE_AVERAGE_EDGE:
            avg.append(formula(_edge("", USAGE_AVERAGE_EDGE[USAGE_HEADER[i]]), edge_tab))
        else:
            avg.append(f'=IFERROR(AVERAGE({col}{first}:{col}{last}),"")' if USAGE_FORMATS[i] != TEXT else "")
    row = lay.add(avg)
    for i, fmt in enumerate(USAGE_FORMATS):  # an average of counts is not a whole number
        lay.patterns.append((f"{_letter(i)}{row}", SHEET_PATTERNS[PTS if fmt == INT else fmt]))
    note(USAGE_NOTE)

    section("OPPORTUNITY PER DOLLAR")
    for fields in OPPORTUNITY_STRIPES:
        stripe(fields)
    note(OPPORTUNITY_NOTE)

    section("MATCHUP AND GAME")
    for fields in MATCHUP_STRIPES:
        stripe(fields)
    note(MATCHUP_NOTE)

    section("PROPS")
    lay.label_rows.append(lay.add(PROPS_HEADER))
    for n in range(1, MAX_PROPS + 1):
        row = lay.add([row_lookup("prop", n, c) for c in ("A", "B", "C", "D")])
        lay.patterns.append((f"B{row}", "0.0"))
        lay.patterns.append((f"C{row}", "0%"))
    lay.note_rows.append(lay.add([meta_lookup("props", "A")]))
    note(PROPS_NOTE)

    section("SIGNALS AND FLAGS")
    lay.add(["Flags", formula(_edge("Flags", "Flags"), edge_tab)])
    lay.add(["Edge chips", formula(_edge("Edge", "Edge"), edge_tab)])
    for n in range(1, MAX_SIGNALS + 1):
        lay.add([row_lookup("sig", n, "A"), row_lookup("sig", n, "B")])
        lay.note_rows.append(lay.add(["", row_lookup("sig", n, "C")]))
    lay.note_rows.append(lay.add([meta_lookup("signals", "A")]))
    note(SIGNALS_NOTE)

    section("IN YOUR LINEUPS")
    lay.label_rows.append(lay.add(LINEUPS_LABELS))
    if exposure is None:
        values = [in_lineups_formula(lineups_tab), which_lineups_formula(lineups_tab), "(no Exposure tab)"]
    else:
        values = [
            in_lineups_formula(lineups_tab),
            which_lineups_formula(lineups_tab),
            exposure_formula(exposure_tab, exposure, "Exposure"),
            exposure_formula(exposure_tab, exposure, "Target"),
            "",
        ]
        r = len(lay.rows) + 1
        values[4] = f'=IF(OR(C{r}="",D{r}=""),"",C{r}-D{r})'
    row = lay.add(values)
    for col in "CDE":
        lay.patterns.append((f"{col}{row}", "0%"))
    note(
        f"Counted from the Lineups tab's name cells ({len(LINEUPS_NAME_BLOCKS)} blocks); Exposure and Target "
        f"are Exposure's own columns. Divisor: Exposure!{lineup_count_cell}."
    )
    return lay


def _raw_cell(column: str) -> str:
    match = f"MATCH({KEY_REF},{raw_range('NameKey')},0)"
    return f"INDEX({raw_range(column)},{match})"


def unprotected_ranges() -> list[str]:
    """The one typed cell."""
    return [NAME_CELL]


# ---------------------------------------------------------------------------------------------
# Last five games (the hidden raw tab's source data)
# ---------------------------------------------------------------------------------------------


def snap_log_frame(snap_counts: pd.DataFrame) -> pd.DataFrame:
    """nflverse snap_counts rows reduced to `NameKey, Wk, Snap%` (the offense share, a fraction)."""
    if snap_counts is None or snap_counts.empty:
        return pd.DataFrame(columns=["NameKey", "Wk", "Snap%"])
    frame = pd.DataFrame(
        {
            "NameKey": snap_counts["player"].map(normalize_name),
            "Wk": pd.to_numeric(snap_counts["week"], errors="coerce"),
            "Snap%": pd.to_numeric(snap_counts["offense_pct"], errors="coerce"),
        }
    )
    if "game_type" in snap_counts.columns:
        frame = frame[snap_counts["game_type"].eq("REG").to_numpy()]
    return frame.dropna(subset=["Wk"]).drop_duplicates(["NameKey", "Wk"], keep="last")


def usage_log(
    stats: pd.DataFrame,
    pbp: pd.DataFrame | None,
    snaps: pd.DataFrame | None,
    *,
    games: int = GAMES,
) -> pd.DataFrame:
    """One row per (player, game) for each QB / RB / WR / TE's last `games` games PLAYED (`LOG_COLUMNS`,
    `Back` = 1 for the latest): snap share, targets, carries, target share of his team, red-zone and
    inside-the-5 looks, and the real DK points. The window is the same rule as `usage_metrics`: a game counts
    when he has a stats row, so a missed week is skipped, never a zero. `pbp` / `snaps` None blank theirs.
    """
    from dfs import results_actual

    if stats is None or stats.empty:
        return pd.DataFrame(columns=LOG_COLUMNS)
    stats = stats[stats["season_type"] == "REG"].copy() if "season_type" in stats.columns else stats.copy()
    stats["position"] = stats["position"].map(normalize_position)
    stats["week"] = pd.to_numeric(stats["week"], errors="coerce")
    totals = usage_metrics.team_week_totals(stats)
    scored = results_actual.score_offense_actual(stats)[["player_id", "week", "dk_actual"]]
    players = stats[stats["position"].isin(usage_metrics.USAGE_PLAYER_POSITIONS)].copy()
    players = players.drop_duplicates(["player_id", "week"], keep="last")
    players = players.merge(totals, on=["team", "week"], how="left").merge(
        scored.drop_duplicates(["player_id", "week"]), on=["player_id", "week"], how="left"
    )
    players = players.sort_values(["player_id", "week"], ascending=[True, False])
    window = players.groupby("player_id", sort=False).head(games).copy()
    window["Back"] = window.groupby("player_id").cumcount() + 1
    window["targets"] = pd.to_numeric(window["targets"], errors="coerce").fillna(0.0)
    window["carries"] = pd.to_numeric(window["carries"], errors="coerce").fillna(0.0)
    window["tgt_share"] = (window["targets"] / window["team_targets"]).where(window["team_targets"] > 0)
    # A quarterback's targets are not a role: blank, never a 0% that reads like one.
    passer = window["position"] == "QB"
    window.loc[passer, ["targets", "tgt_share"]] = float("nan")

    if pbp is not None and not pbp.empty:
        looks = usage_metrics.red_zone_counts(pbp, goal_line=INSIDE_FIVE).rename(
            columns={"GsisId": "player_id"}
        )
        window = window.merge(looks, on=["player_id", "week"], how="left")
        window[["rz", "gl"]] = window[["rz", "gl"]].fillna(0)
    else:
        window["rz"] = window["gl"] = float("nan")

    window["NameKey"] = window["player_display_name"].map(normalize_name)
    if snaps is not None and not snaps.empty:
        window = window.merge(
            snaps.rename(columns={"Wk": "week"}).drop_duplicates(["NameKey", "week"]),
            on=["NameKey", "week"],
            how="left",
        )
    else:
        window["Snap%"] = float("nan")

    out = pd.DataFrame(
        {
            "NameKey": window["NameKey"],
            "Name": window["player_display_name"],
            "Team": window["team"],
            "Position": window["position"],
            "Back": window["Back"],
            "Wk": window["week"].astype("Int64"),
            "Opp": window["opponent_team"],
            "Snap%": window["Snap%"],
            "Tgt": window["targets"],
            "Car": window["carries"],
            "Tgt%": window["tgt_share"],
            "RZ": window["rz"],
            "In5": window["gl"],
            "DK": window["dk_actual"],
        }
    )
    return out[LOG_COLUMNS].sort_values(["Name", "Back"]).reset_index(drop=True)


# ---------------------------------------------------------------------------------------------
# The raw tabs' rows
# ---------------------------------------------------------------------------------------------


def raw_frame(edge: pd.DataFrame, *, log: pd.DataFrame | None = None) -> pd.DataFrame:
    """`PlayerCardRaw`: one row per EdgeRaw player (`RAW_COLUMNS`), his last five games game by game."""
    games: dict[tuple[str, str], dict[int, pd.Series]] = {}
    if log is not None and not log.empty:
        for row in log.to_dict("records"):
            games.setdefault((row["NameKey"], row["Position"]), {})[int(row["Back"])] = pd.Series(row)
    rows = []
    for player in edge.itertuples(index=False):
        key = normalize_name(player.Name)
        position = normalize_position(player.Position)
        out: dict[str, object] = {"NameKey": key}
        for back in range(1, GAMES + 1):
            game = games.get((key, position), {}).get(back)
            for name in GAME_FIELDS:
                out[f"{name}{back}"] = None if game is None else _clean(game[name])
        rows.append(out)
    return pd.DataFrame(rows, columns=RAW_COLUMNS)


def _clean(value: object) -> object:
    if value is None or (isinstance(value, float) and pd.isna(value)) or value is pd.NA:
        return None
    return value


# ---- props ---------------------------------------------------------------------------------

MARKET_LABELS = {
    "pass_yd": "Passing yards",
    "pass_td": "Passing TDs",
    "pass_int": "Interceptions",
    "rush_yd": "Rushing yards",
    "rec": "Receptions",
    "rec_yd": "Receiving yards",
    "td": "Anytime TD",
}


def props_rows(consensus: pd.DataFrame) -> list[dict]:
    """`PlayerCardRows` rows for the props, from the matched consensus frame (`props.attach_players`
    output: `name_key`, `stat`, `line`, `p`, `n_books`). A TD market has no line."""
    rows: list[dict] = []
    if consensus is None or consensus.empty:
        return rows
    order = list(MARKET_LABELS)
    for key, group in consensus.groupby("name_key", sort=False):
        group = group.assign(
            _o=group["stat"].map(lambda s: order.index(s) if s in order else 99)
        ).sort_values("_o")
        for n, item in enumerate(group.head(MAX_PROPS).itertuples(index=False), start=1):
            line = None if pd.isna(item.line) else round(float(item.line), 1)
            rows.append(
                {
                    "Key": f"{key}|prop|{n}",
                    "A": MARKET_LABELS.get(item.stat, item.stat),
                    "B": line,
                    "C": round(float(item.p), 4),
                    "D": int(item.n_books),
                }
            )
    return rows


# ---- signals ---------------------------------------------------------------------------------


def signal_group(token: str) -> str | None:
    """The Model Check row a `Edge` token is scored in (`results_signals.SIGNAL_GROUPS` / R6 groups)."""
    from dfs import results_signals, usage_r6

    if token in ("FADE↓", "USAGE↑", "USAGE↓"):
        return token
    if token == "INJ+":
        return "INJ+ confirmed"
    if token.startswith(usage_r6.PROJ_DOWN):
        return results_signals.PROJ_DOWN_GROUP
    if token.startswith(usage_r6.PROJ_UP):
        return results_signals.PROJ_UP_GROUP
    if token.startswith(usage_r6.PROPS_UP):
        return results_signals.PROPS_UP_GROUP
    if token.startswith(usage_r6.PROPS_DOWN):
        return results_signals.PROPS_DOWN_GROUP
    return None


def _signed(value: float) -> str:
    return f"{value:+.1f}".replace("-", "−")


def record_line(group: str | None, table: pd.DataFrame | None) -> str:
    """Model Check's record for one signal group, in one line."""
    if group is None:
        return "no Model Check record for this signal"
    if table is None or table.empty or group not in set(table["Signal"]):
        return "no record yet"
    row = table.set_index("Signal").loc[group]
    n = int(row["n"])
    if n == 0:
        return "has not fired in a scored game yet"
    cal = row["VsCal"] if pd.notna(row["VsCal"]) else None
    proj = row["VsProj"] if pd.notna(row["VsProj"]) else None
    hit = row["HitCal"] if pd.notna(row["HitCal"]) else row["HitProj"]
    parts = [f"n={n}"]
    if cal is not None:
        parts.append(f"{_signed(float(cal))} vs CalPts")
    if proj is not None:
        parts.append(f"{_signed(float(proj))} vs TFFB")
    if pd.notna(hit):
        parts.append(f"{float(hit):.0%} on the signal's side")
    if bool(row["Thin"]):
        parts.append("thin sample")
    return ", ".join(parts)


def signal_detail(token: str, player: pd.Series, beneficiaries: pd.DataFrame | None) -> str:
    """A short why for one token, from the numbers already on EdgeRaw / the saved beneficiaries."""
    xfp, proj = player.get("xFP/G"), player.get("ProjPts")
    if token in ("FADE↓", "USAGE↑", "USAGE↓") and pd.notna(xfp) and pd.notna(proj):
        return f"xFP/G {float(xfp):.1f} against TFFB's {float(proj):.1f}"
    if token == "INJ+" and beneficiaries is not None and not beneficiaries.empty:
        hit = beneficiaries[beneficiaries["Name"] == player["Name"]]
        if not hit.empty:
            b = hit.iloc[0]
            gain = f", +{float(b['xfp_gain']):.1f} xFP" if pd.notna(b.get("xfp_gain")) else ""
            return f"{b['OutPlayers']} {b['OutStatus']}{gain}"
    if token.startswith("Proj"):
        return "the usage trend says TFFB's projection is " + ("too high" if "▼" in token else "too low")
    if token.startswith("Props") and pd.notna(player.get("PropPts")) and pd.notna(player.get("CalPts")):
        return f"props score {float(player['PropPts']):.1f} against CalPts {float(player['CalPts']):.1f}"
    return ""


def signal_rows(
    edge: pd.DataFrame, table: pd.DataFrame | None, beneficiaries: pd.DataFrame | None = None
) -> list[dict]:
    """`PlayerCardRows` rows for each player's `Edge` tokens: `A` token, `B` detail, `C` the record."""
    from dfs import usage_r6

    rows: list[dict] = []
    for _, player in edge.iterrows():
        tokens = usage_r6.edge_tokens(player.get("Edge"))
        key = normalize_name(player["Name"])
        for n, token in enumerate(tokens[:MAX_SIGNALS], start=1):
            rows.append(
                {
                    "Key": f"{key}|sig|{n}",
                    "A": token,
                    "B": signal_detail(token, player, beneficiaries),
                    "C": record_line(signal_group(token), table),
                }
            )
    return rows


def rows_frame(props: list[dict], signals: list[dict], *, props_note: str, signals_note: str) -> pd.DataFrame:
    meta = [
        {"Key": "meta|props", "A": props_note},
        {"Key": "meta|signals", "A": signals_note},
    ]
    return pd.DataFrame([*meta, *props, *signals], columns=ROWS_COLUMNS)


# ---------------------------------------------------------------------------------------------
# The text side of a field: the same spec, read from the frames (`dfs player`, the offline check)
# ---------------------------------------------------------------------------------------------


def format_value(fmt: str, value: object) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)) or value is pd.NA or value == "":
        return "-"
    if fmt == TEXT:
        return str(value)
    number = float(value)
    return {
        PTS: f"{number:.1f}",
        "pts3": f"{number:.3f}",
        PCT0: f"{number:.0%}",
        PCT1: f"{number:.1%}",
        PTS2: f"{number:.2f}",
        ZSCORE: f"{number:+.1f}",
        PCT100: f"{number:.1f}%",
        INT: f"{number:.0f}",
        MONEY: f"${number:,.0f}",
    }[fmt]


def value(spec: Field, player: pd.Series, raw: pd.Series | None) -> object:
    if spec.source == EDGE:
        return player.get(spec.column)
    if spec.source == RAW:
        return None if raw is None else raw.get(spec.column)
    total, spread = player.get("OverUnder"), player.get("Spread")
    if pd.isna(total) or pd.isna(spread):
        return None
    return (float(total) - float(spread)) / 2


def _table(headers: list[str], rows: list[list[str]]) -> list[str]:
    widths = [max(len(str(r[i])) for r in [headers, *rows]) for i in range(len(headers))]
    fmt = lambda r: "  ".join(str(c).ljust(widths[i]) for i, c in enumerate(r)).rstrip()  # noqa: E731
    return ["  " + fmt(headers), *["  " + fmt(r) for r in rows]]


def _stripe_text(fields: list[Field], player: pd.Series, raw: pd.Series | None) -> list[str]:
    return _table([f.label for f in fields], [[format_value(f.fmt, value(f, player, raw)) for f in fields]])


def render_text(
    name: str,
    edge: pd.DataFrame,
    raw: pd.DataFrame,
    rows: pd.DataFrame,
    *,
    lineups_note: str = "(read from the sheet: the Lineups and Exposure tabs)",
) -> str:
    """The card for one player as plain text, from the same frames the writer puts on the hidden tabs."""
    hit = edge[edge["Name"].str.casefold() == name.casefold()]
    if hit.empty:
        return f"{name}: not on this week's EdgeRaw."
    player = hit.iloc[0]
    key = normalize_name(player["Name"])
    raw_hit = raw[raw["NameKey"] == key]
    raw_row = raw_hit.iloc[0] if not raw_hit.empty else None
    meta = {r["Key"]: r["A"] for _, r in rows.iterrows() if str(r["Key"]).startswith("meta|")}
    mine = rows[rows["Key"].astype(str).str.startswith(f"{key}|")]

    out = [f"=== {player['Name']} ==="]
    out += _stripe_text(HEADER_STRIPE, player, raw_row)
    out.append("\nPROJECTIONS")
    for fields in PROJECTION_STRIPES:
        out += _stripe_text(fields, player, raw_row)
    out.append("  " + PROJECTION_NOTE)
    out.append("\nOWNERSHIP")
    out += _stripe_text(OWNERSHIP_STRIPE, player, raw_row)
    out.append("  " + OWNERSHIP_NOTE)

    out.append("\nUSAGE, LAST 5 GAMES")
    usage_rows = []
    for back in range(1, GAMES + 1):
        wk = None if raw_row is None else raw_row.get(f"Wk{back}")
        if wk is None or pd.isna(wk):
            continue
        cells = [f"Wk {int(wk)}"]
        for name_, fmt in zip(GAME_FIELDS[1:], USAGE_FORMATS[1:], strict=True):
            cells.append(format_value(fmt, raw_row.get(f"{name_}{back}")))
        usage_rows.append(cells)
    out += _table(USAGE_HEADER, usage_rows) if usage_rows else ["  (no games on file for him)"]
    out.append("  " + USAGE_NOTE)

    average = [
        f"{label}: {format_value(fmt, player.get(USAGE_AVERAGE_EDGE[label]))}"
        for label, fmt in zip(USAGE_HEADER, USAGE_FORMATS, strict=True)
        if label in USAGE_AVERAGE_EDGE
    ]
    out.append("  Last-5 average (EdgeRaw): " + "   ".join(average))
    out.append("\nOPPORTUNITY PER DOLLAR")
    for fields in OPPORTUNITY_STRIPES:
        out += _stripe_text(fields, player, raw_row)
    out.append("  " + OPPORTUNITY_NOTE)

    out.append("\nMATCHUP AND GAME")
    for fields in MATCHUP_STRIPES:
        out += _stripe_text(fields, player, raw_row)

    out.append("\nPROPS")
    props = mine[mine["Key"].str.contains("|prop|", regex=False)]
    if props.empty:
        out.append("  no lines for him in the saved pull")
    else:
        out += _table(
            PROPS_HEADER,
            [
                [
                    r["A"],
                    format_value(PTS, r["B"]),
                    format_value(PCT0, r["C"]),
                    format_value(INT, r["D"]),
                ]
                for _, r in props.iterrows()
            ],
        )
    out.append("  " + str(meta.get("meta|props", "")))

    out.append("\nSIGNALS AND FLAGS")
    out.append(
        f"  Flags: {format_value(TEXT, player.get('Flags'))}"
        f"    Edge chips: {format_value(TEXT, player.get('Edge'))}"
    )
    for _, r in mine[mine["Key"].str.contains("|sig|", regex=False)].iterrows():
        out.append(f"  {r['A']}: {r['B']}" if r["B"] else f"  {r['A']}")
        out.append(f"      record: {r['C']}")
    out.append("  " + str(meta.get("meta|signals", "")))

    out.append("\nIN YOUR LINEUPS")
    out.append("  " + lineups_note)
    return "\n".join(out)
