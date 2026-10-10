"""The `Edge Finder` tab: pure layout from the sync's saved tables.

Nothing here reads a sheet or the network. `load_inputs` reads what `edge_finder.enrich` saved under
`data/current/edge_finder/` plus `data/current/edge.csv` and the weekly usage tables; `build_layout` turns
them into rows, groups and formatting instructions; `sheet_edge_finder.write_tab` writes them. Python writes
the tab on every sync (it is a view of the sync, not a formula tab).

**Columns (fixed across sections).** `A` **Pool** (the player's pool state and its dropdown: a formula the
bound Apps Script restores after an edit, see `sheet_pool_cells`), `B`-`E` Name, Pos, Team, Salary, `F`
**Own%** (blank until ownership publishes); `G`-`L` section-specific (six slots, named by each section's
header row); then, named in `TRAILING_HEADERS`: `M` **Do** (a verb), `N` **Why** (the one most useful fact,
about 60 characters, last so it can overflow right; the full text is the cell's note), `O` **Id** (his
DraftKings id, hidden: the Apps Script and every formula find the player by it, never by name), and `P` a
hidden group key (see below).

**Sections, top to bottom:** cash core; GPP upside; punt plays; projection disagreements; injury beneficiaries
(carries only) with the absent regulars as context; usage trends (context); matchups (context, with each
team's top players one click away); context signals (unproven). Each section opens with a title bar and a
second line, "what it is · what to do".

**Overflow and groups.** Cash core, GPP upside and punt plays write every rosterable player up to
`MAX_PER_POSITION`; the first `VISIBLE_PER_POSITION[pos]` rows show, the rest sit in a level-2 row group
(collapsed) under a header row "▸ 34 more RBs (click + to show)". Each section's body is a level-1 group. A
group's key is written in hidden column `Q` on the row above it (the section title, or
`section|position|more`), so `sheet_edge_finder` can read which groups Sam had open and shut them again
after a rewrite, even when the count in a header changes.

**Do verbs.** The static verb for each row is chosen here (`cash_verb`, `gpp_verb`, ...); the sheet turns it
into a formula that reads "In pool (Cash)" instead whenever the player is already pooled, so the override is
live.

**Short Why, full note.** Every player row carries two texts: `why` (what the cell shows, `SHORT_WHY_CHARS` at
most, number first, nothing another column on the row already shows) and `why_full` (the whole reason, kept as
the cell's note). `Layout.notes` maps a row to its note.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd

from dfs import calibration, signals_data, usage_r6
from dfs.derived import _rosterable_pool_mask
from dfs.edge_finder import (
    ABSENCES_FILE,
    BENEFICIARIES_FILE,
    MATCHUPS_FILE,
    OUTPUT_DIR,
    PLAYERS_FILE,
    STATUS_FILE,
)
from dfs.kickoff import format_et, parse_kickoff
from dfs.paths import CURRENT_DIR
from dfs.research_constants import ResearchConstantsError, signal_thresholds
from dfs.sheet_pool_cells import POOL_OPTIONS  # noqa: F401 - re-exported for the Edge Finder writer

EDGE_FINDER_TAB = "Edge Finder"
SECTION_TOP_N = 5  # the Board's one-line summary panel
VISIBLE_PER_POSITION = {"QB": 6, "RB": 10, "WR": 12, "TE": 6, "DST": 6}
MAX_PER_POSITION = 40
DISAGREEMENT_PER_DIRECTION = 2
BENEFICIARY_CONFIRMED_N = 12
BENEFICIARY_QUESTIONABLE_N = 6
SIGNALS_PER_TOKEN = 6
TREND_ROWS_PER_POSITION = 8
R6_SIGNAL_ROWS = 8  # players listed per R6 signal in Context signals
PUNT_SALARY_WINDOW = 1000  # dollars above each position's cheapest salary on the slate (the Board's old rule)
PUNT_ROWS_PER_POSITION = 5
MATCHUP_PLAYERS = 3
MUTED_BELOW_GAMES = 3
CASH_CORE_MIN_CALPTS_PCT = 50.0
CASH_ADD_TOP_N = 3  # "Cash add": top 3 Hit3x% at the position AND Bust% below the position median
GPP_ADD_TOP_QUARTILE = 0.75
OWN_STAR_BOTTOM_HALF = 0.5
POSITIONS = ["QB", "RB", "WR", "TE", "DST"]
OUT_STATUSES = {"OUT", "IR", "D"}
STAR = "★"
LEVERAGE_MIN_LEV = 25  # `Lev` at or above this, with Boom% in the top half, is "GPP leverage"
LEVERAGE_ROWS_PER_POSITION = 5
CHALK_ROWS_PER_POSITION = 3
CHALK_THIRD = 1 / 3  # Bust% in the bottom third of the position -> eat; top third -> fade candidate
OWN_NOT_OUT = "Ownership not out yet; GPP leverage appears when it does."

POOL_HEADER = "Pool"
LEAD = [POOL_HEADER, "Name", "Pos", "Team", "Salary", "Own%"]  # A-F
SLOTS = 6  # G-L
TRAILING_HEADERS = ["Do", "Why", "Id"]  # M-O
COLUMN_COUNT = len(LEAD) + SLOTS + len(TRAILING_HEADERS) + 1  # + P, the hidden group key
FIRST_TRAILING = len(LEAD) + SLOTS
SHORT_WHY_CHARS = 60  # what the Why cell shows; the rest is the cell's note


def column_letter(index: int) -> str:
    return chr(ord("A") + index)


def _lead_letter(header: str) -> str:
    return column_letter(LEAD.index(header))


def _trailing_letter(header: str) -> str:
    return column_letter(FIRST_TRAILING + TRAILING_HEADERS.index(header))


POOL_COL = _lead_letter(POOL_HEADER)
NAME_COL = _lead_letter("Name")
OWN_COL = _lead_letter("Own%")
DO_COL = _trailing_letter("Do")
WHY_COL = _trailing_letter("Why")
ID_COL = _trailing_letter("Id")
KEY_COL = column_letter(COLUMN_COUNT - 1)
LAST_COLUMN = KEY_COL
LAST_VISIBLE_COL = WHY_COL  # Id and the group key are hidden

CASH_COLUMNS = ["CalPts", "Hit3x%", "Bust%", "ProjPts", "Rank", "Edge"]
GPP_COLUMNS = ["CalPts", "Boom%", "CeilM", "Lev", "Rank", "Edge"]
LEVERAGE_COLUMNS = ["CalPts", "Boom%", "Bust%", "Lev", "Rank", "Edge"]
CHALK_COLUMNS = ["CalPts", "Boom%", "Bust%", "ProjPts", "Rank", "Edge"]
PUNT_COLUMNS = ["CalPts", "ValAdj", "Hit3x%", "Rank", "Edge"]
DISAGREE_COLUMNS = ["TFFB", "Sleeper", "FantasyPros", "CalPts", "Diff", "Edge"]
BENEFICIARY_COLUMNS = ["Gain Car/G", "Gain xFP/G", "Method", "Priced in?", "Edge"]
ABSENCE_COLUMNS = ["Role", "Tgt/G", "Car/G", "Games missed"]
TREND_HEADERS = ["Metric", "Last 3", "Earlier", "Change", "Trend"]
# A team row reads Matchup | Pos | Grade | Players (B-E; the players' names are one left-aligned cell that
# overflows right). The player rows nested under it have their own small header row, the standard player
# columns, so their Salary / Own% / CalPts ... carry the sheet-wide formats and colours.
MATCHUP_LEAD = ["", "Matchup", "Pos", "Grade", "Players", ""]
MATCHUP_COLUMNS = ["CalPts", "Hit3x%", "Boom%"]  # the nested player rows' number columns
SIGNAL_COLUMNS = ["Token", "xFP/G", "DK/G L3"]
R6_SIGNAL_COLUMNS = ["Read", "CalPts", "ProjPts"]

MEANINGS = {
    "CASH CORE": (
        "Players most likely to score 3× their salary, the pace that usually cashes.  ·  Do: pool the "
        "'Cash add' rows for cash lineups; 'Cash option' rows are the next tier."
    ),
    "GPP UPSIDE": (
        "Players with the best chance to score 4× their salary, the pace that wins tournaments.  ·  Do: pool "
        f"the 'GPP add' and 'GPP leverage' rows for tournaments. {STAR} marks a top-quartile Boom% that is "
        "also owned below his position's median (once ownership is out)."
    ),
    "LEVERAGE PLAYS": (
        "The best Boom% against ownership: Lev is his Boom% rank minus his Own% rank among his position's "
        "players (+35 = far more upside than ownership).  ·  Do: pool 'GPP leverage' rows for tournaments; "
        "ownership is a large-field projection, so it is directional for small fields."
    ),
    "CHALK TO FADE OR EAT": (
        "The 3 highest-owned players at each position.  ·  Do: 'Chalk: eat' when his bust odds are in the "
        "bottom third of his position (safe to roster), 'Chalk: fade candidate' when they are in the top "
        "third."
    ),
    "PUNT PLAYS": (
        "The best value within $1,000 of each position's cheapest salary on the slate.  ·  Do: pick one to "
        "free up salary for your stars; the top row at a position is the best punt."
    ),
    "PROJECTION DISAGREEMENTS": (
        "Where our corrected projection (CalPts) differs most from TFFB's, which most of the field uses.  ·  "
        "Do: look closer at 'Look closer ▲' (we are higher), be careful with 'Caution ▼' (we are lower); "
        "Why splits the gap."
    ),
    "INJURY BENEFICIARIES": (
        "Backs who inherit carries when a regular back is out.  ·  Do: 'Bump ▲' = confirmed out, a pool "
        "candidate; 'Watch' = questionable, decide after the final sync."
    ),
    "USAGE TRENDS": (
        "Players whose usage over the last 3 games moved beyond normal week-to-week noise (context only, "
        "no chips).  ·  Do: 'Watch' rising or falling roles; the Why line says what moved."
    ),
    "MATCHUPS (CONTEXT)": (
        "How soft or tough each offense's matchup is: Soft is the 8 best, Tough the 4 worst. Context only, "
        "it moves no projection.  ·  Do: open a team (click +) to add its top players with Set."
    ),
    "CONTEXT SIGNALS": (
        "FADE↓ (TEs), USAGE↑ and USAGE↓ (RBs): unproven chips.  ·  Do: use them as a tiebreaker at most; "
        "Model Check tracks every one."
    ),
}


@dataclass
class R6View:
    """The sheet's view of R6 (`usage_r6`): the 12 chip definitions, who they fire for (rosterable pool only),
    the trend arrows, and how many pool players are too new for an arrow."""

    chips: dict[str, usage_r6.Chip]
    signals: dict[str, usage_r6.PlayerSignal]  # gsis id -> what fired for him
    trends: pd.DataFrame  # `usage_r6.compute_trends`
    min_games: int
    short_games: int = 0  # pool players with fewer than `min_games` earlier games: no arrow for them


@dataclass
class Inputs:
    """What the tab is built from. `edge` is the enriched edge frame (`data/current/edge.csv`); `players` the
    signals table (`Games`, `DkG`, `GsisId`, ...); the rest are the sync's saved tables. `r6` is the
    `R6View` (the R6 usage arrows and the 12 `Proj` signals; None when its data is not available);
    `history` maps a position to the typical best Hit3x% of earlier weeks."""

    edge: pd.DataFrame
    players: pd.DataFrame
    beneficiaries: pd.DataFrame
    matchups: pd.DataFrame
    status: dict
    absences: pd.DataFrame = field(default_factory=pd.DataFrame)
    calibration: calibration.Calibration | None = None
    r6: R6View | None = None
    history: dict[str, tuple[float, int]] = field(default_factory=dict)


@dataclass
class Group:
    """A row group to create: `first`..`last` (1-based), `depth` 1 (a section) or 2 (an overflow or a team),
    its `key` (written in hidden column Q on the row above) and whether it starts collapsed."""

    first: int
    last: int
    depth: int
    key: str
    collapsed: bool


@dataclass
class Layout:
    """Rows (1-based numbers resolved) plus what the writer must format."""

    rows: list[list]
    title_row: int = 1
    section_rows: list[int] = field(default_factory=list)
    meaning_rows: list[int] = field(default_factory=list)
    subheader_rows: list[int] = field(default_factory=list)
    verdict_rows: list[int] = field(default_factory=list)
    header_rows: list[int] = field(default_factory=list)
    inner_header_rows: list[int] = field(default_factory=list)  # a small header inside a group (matchups)
    note_rows: list[int] = field(default_factory=list)
    overflow_rows: list[int] = field(default_factory=list)
    team_rows: list[int] = field(default_factory=list)
    muted_rows: list[int] = field(default_factory=list)  # static muting (thin sample, questionable, unproven)
    player_rows: list[int] = field(default_factory=list)  # rows with a Pool control and an Id
    notes: dict[int, str] = field(default_factory=dict)  # player row -> the full Why, kept as the cell's note
    status_rows: list[int] = field(default_factory=list)
    groups: list[Group] = field(default_factory=list)
    all_links: dict[int, str] = field(default_factory=dict)  # section row -> EdgeRaw filter view title
    percent_cells: list[str] = field(default_factory=list)
    own_cells: list[str] = field(default_factory=list)  # Own% ranges (a fraction shown as a percent)
    lev_cells: list[str] = field(default_factory=list)  # Lev ranges (a signed whole number)
    # (cell, pattern, type): a number format chosen per ROW (usage trends mix percents and per-game numbers)
    cell_formats: list[tuple[str, str, str]] = field(default_factory=list)
    money_cells: list[str] = field(default_factory=list)
    point_cells: list[str] = field(default_factory=list)
    chip_ranges: list[str] = field(default_factory=list)  # `Edge` / token cells
    # (column header, first row, last row, lower is better): the probability colour inside each position block
    prob_blocks: list[tuple[str, int, int, bool]] = field(default_factory=list)


# ---------------------------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------------------------


def _read(path: Path) -> pd.DataFrame:
    return pd.read_csv(path) if path.exists() else pd.DataFrame()


def load_r6(edge: pd.DataFrame, players: pd.DataFrame) -> R6View | None:
    """The R6 view for the slate, from the features the sync saved (`usage_r6.SAVED_FILE`) and the research
    files (`usage_signals.json`, `trend_bands.json`). None when either is missing: the tab then says so."""
    path = OUTPUT_DIR / usage_r6.SAVED_FILE
    if not path.exists() or "GsisId" not in players.columns:
        return None
    try:
        features = pd.read_csv(path)
        chips = usage_r6.load_chips()
        bands = usage_r6.load_trend_bands()
        min_games = usage_r6.min_earlier_games()
    except (OSError, ValueError, ResearchConstantsError):
        return None
    pool_ids = edge.loc[rosterable(edge), "Id"]
    pool = set(players.loc[players["Id"].isin(pool_ids), "GsisId"].dropna())
    in_pool = features[features["gsis_id"].isin(pool)]
    return R6View(
        chips={c.id: c for c in chips},
        signals=usage_r6.slate_signals(features, chips, pool),
        trends=usage_r6.compute_trends(features, bands, min_games=min_games, population=pool),
        min_games=min_games,
        short_games=int((in_pool["earlier_games"] < min_games).sum()),
    )


def typical_best_hit3x(season: int, before_week: int) -> dict[str, tuple[float, int]]:
    """Per position: the median across earlier weeks of that week's best cash-core `Hit3x%`, and how many
    weeks it rests on. A week is read the way Sam saw it: the last saved signals archive taken before each
    player's own kickoff. Empty with no earlier week (the verdict line is then skipped, never invented)."""
    best: dict[str, list[float]] = {p: [] for p in POSITIONS}
    for week in range(1, before_week):
        rows = signals_data.select_archive_rows(signals_data.load_archives(season, week), None)
        if rows.empty or "Hit3x%" not in rows.columns:
            continue
        pool = rows[rosterable(rows)]
        for position in POSITIONS:
            part = pool[pool["Position"] == position]
            calpts = pd.to_numeric(part["CalPts"], errors="coerce")
            part = part[calpts >= calpts.median()]
            top = pd.to_numeric(part["Hit3x%"], errors="coerce").max()
            if pd.notna(top):
                best[position].append(float(top))
    return {p: (float(np.median(v)), len(v)) for p, v in best.items() if v}


def load_inputs(*, scored: pd.DataFrame | None = None, season: int | None = None) -> Inputs | None:
    """The saved sync outputs, or None when no sync has produced them yet. `scored` (every scored table) lets
    the disagreement reasons quote the calibration's own biases."""
    edge = _read(CURRENT_DIR / "edge.csv")
    players = _read(OUTPUT_DIR / PLAYERS_FILE)
    if edge.empty or players.empty:
        return None
    status = {}
    status_path = OUTPUT_DIR / STATUS_FILE
    if status_path.exists():
        status = json.loads(status_path.read_text())
    fitted = None
    if scored is not None and not scored.empty and status.get("week"):
        fitted = calibration.fit(
            scored, before_week=int(status["week"]), season=season or status.get("season")
        )
    history = {}
    if status.get("week") and (season or status.get("season")):
        history = typical_best_hit3x(int(season or status["season"]), int(status["week"]))
    return Inputs(
        edge=edge,
        players=players,
        beneficiaries=_read(OUTPUT_DIR / BENEFICIARIES_FILE),
        matchups=_read(OUTPUT_DIR / MATCHUPS_FILE),
        absences=_read(OUTPUT_DIR / ABSENCES_FILE),
        status=status,
        calibration=fitted,
        r6=load_r6(edge, players),
        history=history,
    )


# ---------------------------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------------------------


_PERCENT_TEXT = re.compile(r"^[\d.]+%$")


def _clean(value):
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return ""
    if hasattr(value, "item"):
        value = value.item()
    if isinstance(value, str) and (value.startswith(("=", "+", "-", "@")) or _PERCENT_TEXT.match(value)):
        return "'" + value  # kept as text: "79%" would otherwise become the number 0.79
    return value


def available(edge: pd.DataFrame) -> pd.Series:
    """True where DraftKings does not list him OUT, IR or doubtful."""
    return ~edge["Avail"].fillna("").astype(str).str.upper().isin(OUT_STATUSES)


def rosterable(edge: pd.DataFrame) -> pd.Series:
    return _rosterable_pool_mask(pd.to_numeric(edge["ProjPts"], errors="coerce"), edge["Position"])


def with_games(edge: pd.DataFrame, players: pd.DataFrame) -> pd.DataFrame:
    """The edge frame with `Games`, `DkG`, `TdExcess` and `UsageN` from the signals table, by `Id`."""
    cols = [c for c in ("Id", "Games", "DkG", "TdExcess", "UsageN", "GsisId") if c in players.columns]
    return edge.merge(players[cols].drop_duplicates("Id"), on="Id", how="left")


def _is_thin(games) -> bool:
    return bool(pd.notna(games) and games < MUTED_BELOW_GAMES)


def _thin_note(games) -> str:
    return f"only {int(games)} game{'s' if int(games) != 1 else ''} of data" if _is_thin(games) else ""


def _join(*parts: str) -> str:
    return "; ".join(p for p in parts if p)


def _plural(position: str) -> str:
    return "DSTs" if position == "DST" else f"{position}s"


def money(value) -> str:
    return f"${value:,.0f}"


def _k(value) -> str:
    return f"${value / 1000:.1f}k"


def existing_chip_reasons(edge_text: object) -> str:
    """Why the older unproven chips (FADE↓ for TEs, USAGE↑ / USAGE↓ for RBs) are on a player, in words, read
    from the research thresholds. Shown beside an R6 reason when both apply, so there is one Why, never two
    contradictory stories."""
    tokens = usage_r6.edge_tokens(edge_text)
    try:
        s = signal_thresholds()
    except ResearchConstantsError:
        return ""
    parts = []
    if "FADE↓" in tokens:
        parts.append(f"FADE↓: last-3 DK points a game at least {s.fade_gap_points:g} above expected")
    if "USAGE↑" in tokens:
        parts.append(f"USAGE↑: carry share up {s.usage_up:.0%}+ over the last 2 games")
    if "USAGE↓" in tokens:
        parts.append(f"USAGE↓: carry share down {s.usage_down:.0%}+ over the last 2 games")
    return "; ".join(parts)


class _Builder:
    def __init__(self, r6: R6View | None = None) -> None:
        self.r6 = r6
        self.layout = Layout(rows=[])
        self._section: tuple[str, int] | None = None
        self._open: tuple[str, int] | None = None  # (key, header row) of an open depth-2 group
        self._edge_last = False  # the current header's last slot is `Edge`: it sits in the final slot column

    @property
    def next_row(self) -> int:
        return len(self.layout.rows) + 1

    @property
    def last_row(self) -> int:
        return len(self.layout.rows)

    def add(self, values: list) -> int:
        cleaned = [_clean(v) for v in values]
        self.layout.rows.append(cleaned + [""] * (COLUMN_COUNT - len(cleaned)))
        return len(self.layout.rows)

    def blank(self) -> None:
        self.add([])

    def set_key(self, row: int, key: str) -> None:
        self.layout.rows[row - 1][COLUMN_COUNT - 1] = key

    # ---- sections ------------------------------------------------------------------------
    def begin_section(self, title: str, *, link: str | None = None) -> None:
        """Blank row, the title bar, and the "what it is · what to do" line. The section's body (everything
        until `end_section`) becomes a level-1 group whose toggle sits on the meaning line."""
        short = title.split("  ")[0]
        self.blank()
        row = self.add([title])
        self.layout.section_rows.append(row)
        if link:
            self.layout.all_links[row] = link
        meaning = self.add([MEANINGS[short]])
        self.layout.meaning_rows.append(meaning)
        self._section = (short, meaning)

    @property
    def section_key(self) -> str:
        return self._section[0]

    def end_section(self) -> None:
        key, meaning = self._section
        if self.last_row > meaning:
            self.layout.groups.append(Group(meaning + 1, self.last_row, 1, key, False))
            self.set_key(meaning, key)
        self._section = None

    def sub(self, title: str) -> int:
        row = self.add([title])
        self.layout.subheader_rows.append(row)
        return row

    def verdict(self, text: str) -> None:
        self.layout.verdict_rows.append(self.add([text]))

    def note(self, text: str) -> None:
        self.layout.note_rows.append(self.add([text]))

    def header(self, slots: list[str], *, lead: list[str] | None = None) -> int:
        """A column-header row. A section whose last slot is `Edge` (the chips) always shows it in the same,
        final slot column, with any unused slots blank before it, so the chips line up down the whole tab."""
        self._edge_last = bool(slots) and slots[-1] == "Edge"
        slots = self._edge_to_the_end(slots)
        names = [*(lead or LEAD), *slots, *TRAILING_HEADERS]
        row = self.add(names)
        self.layout.header_rows.append(row)
        return row

    def inner_header(self, slots: list[str]) -> int:
        """A small header row for the player rows of the group being built: the standard player columns
        (`LEAD`), then `slots`, Do, Why, Id. It is first in the group, so it folds away with it, and it names
        the columns the number formats and colours key off."""
        row = self.add([*LEAD, *self._edge_to_the_end(slots), *TRAILING_HEADERS])
        self.layout.inner_header_rows.append(row)
        return row

    # ---- groups ------------------------------------------------------------------------------
    def open_group(self, header_text: str, key: str, *, team: bool = False) -> int:
        """A header row (the toggle sits on it) whose following rows form a collapsed level-2 group. Its text
        starts in the Name column, under the section's names, not in the narrow Pool column."""
        row = self.add(["", header_text])
        (self.layout.team_rows if team else self.layout.overflow_rows).append(row)
        self._open = (key, row)
        self.set_key(row, key)
        return row

    def close_group(self) -> None:
        key, row = self._open
        if self.last_row > row:
            self.layout.groups.append(Group(row + 1, self.last_row, 2, key, True))
        self._open = None

    # ---- rows ----------------------------------------------------------------------------------
    def r6_text(self, gsis, edge_text: object, why: str, verb: str) -> tuple[str, str, bool]:
        """Add the R6 signals behind a player to a row's `Why` and `Do`: every signal that fired in plain
        words (with the reason of an older chip beside it when one overlaps), and the chip beside the verb
        (`verb_with_proj`: one verb per row). Returns (why, verb, muted): a chip on weaker evidence only
        mutes."""
        sig = self.r6.signals.get(gsis) if self.r6 is not None and isinstance(gsis, str) else None
        if sig is None:
            return why, verb, False
        why = _join(why, usage_r6.signal_why(sig, self.r6.chips), existing_chip_reasons(edge_text))
        verb = verb_with_proj(verb, sig.chip)
        return why, verb, usage_r6.weaker_only(sig, self.r6.chips)

    def _edge_to_the_end(self, slots: list) -> list:
        """`slots` padded to `SLOTS`, with the Edge value last when the section's header ends in `Edge`."""
        slots = list(slots)
        if self._edge_last and slots:
            return [*slots[:-1], *[""] * (SLOTS - len(slots)), slots[-1]]
        return [*slots, *[""] * (SLOTS - len(slots))]

    def player_row(
        self,
        lead: list,
        slots: list,
        *,
        why: str,
        verb: str,
        pid,
        short: str | None = None,
        muted: bool = False,
        gsis=None,
        edge="",
    ) -> int:
        """One player row: Pool (filled by the writer), `lead` (Name, Pos, Team, Salary, Own%), the section's
        `slots`, Do, Why, Id. `why` is the whole reason (the cell's note); `short` is what the cell shows (the
        whole reason cut to `SHORT_WHY_CHARS` when not given)."""
        why, verb, weak = self.r6_text(gsis, edge, why, verb)
        muted = muted or weak
        slots = self._edge_to_the_end(slots)
        row = self.add(["", *lead, *slots, verb, short_why(short if short is not None else why), pid])
        self.layout.player_rows.append(row)
        self.layout.notes[row] = why
        if muted:
            self.layout.muted_rows.append(row)
        return row


def short_why(text: str, limit: int = SHORT_WHY_CHARS) -> str:
    """The cell's version of a reason: its first clause (up to the first `;` or `. `), cut at a word boundary
    to `limit` characters with an ellipsis. Specific rows pass their own short text instead."""
    text = (text or "").strip()
    cut = min((i for i in (text.find("; "), text.find(". ")) if i > 0), default=len(text))
    text = text[:cut]
    if len(text) <= limit:
        return text
    return text[: limit - 1].rsplit(" ", 1)[0].rstrip(",:·-") + "…"


def on_slate(beneficiaries: pd.DataFrame, by_gsis: pd.DataFrame) -> pd.DataFrame:
    """Only beneficiaries DraftKings lists this week (a back with no salary is no option)."""
    if beneficiaries is None or beneficiaries.empty or by_gsis.empty:
        return beneficiaries.iloc[0:0] if beneficiaries is not None else pd.DataFrame()
    return beneficiaries[beneficiaries["GsisId"].isin(by_gsis.index)]


# ---------------------------------------------------------------------------------------------
# Status lines
# ---------------------------------------------------------------------------------------------


def _snapshot_et(stamp: str) -> str:
    try:
        return format_et(datetime.strptime(stamp, "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC))
    except ValueError:
        return "unknown"


def _injury_phrase(info: dict | None) -> str:
    if not info:
        return "from DraftKings' Avail (no injury report recorded)"
    if int(info.get("with_status") or 0) == 0:
        return "practice reports only until Friday"
    return "final statuses are in"


def _injury_report_line(info: dict | None) -> str:
    """Where the injury statuses came from and how complete that source was when fetched. A report with no
    final statuses yet is a timing fact (teams publish them Friday), not a fault."""
    if not info:
        return "Injury report: not recorded for this sync (statuses come from DraftKings' Avail)."
    fetched = str(info.get("fetched") or "")
    try:
        when = format_et(datetime.fromisoformat(fetched.replace("Z", "+00:00")))
    except ValueError:
        when = "unknown time"
    rows, with_status = int(info.get("rows") or 0), int(info.get("with_status") or 0)
    text = (
        f"Injury report: nflverse release for Week {info.get('week', '?')}: {rows} rows, "
        f"{with_status} with a final status, fetched {when}."
    )
    if with_status == 0:
        text += " Outs shown are DraftKings' Avail until the final statuses come Friday."
    return text


def _status_lines(inputs: Inputs, published: bool = True) -> list[str]:
    s = inputs.status
    weeks = s.get("calpts_weeks") or []
    trained = f"Weeks {min(weeks)}–{max(weeks)}" if weeks else "no earlier weeks (CalPts = AggPts)"
    sources = ", ".join(s.get("calpts_sources", []))
    first_kick = ""
    if "GameStart" in inputs.edge.columns:
        starts = [parse_kickoff(v) for v in inputs.edge["GameStart"].dropna().unique()]
        starts = [x for x in starts if x is not None]
        if starts:
            first_kick = f" First kickoff {format_et(min(starts))}."
    return [
        f"Stats through Week {s.get('stats_through_week', '?')} · "
        f"Injuries: {_injury_phrase(s.get('injury_report'))} · "
        f"Projections updated {_snapshot_et(str(s.get('projection_snapshot') or ''))}",
        _injury_report_line(s.get("injury_report")),
        f"CalPts trained on {trained} ({sources}). The context columns are not proven to beat projections; "
        "Model Check tracks every one.",
        f"Run the final `dfs sync --live` about 90 minutes before kickoff.{first_kick}",
        *([] if published else [OWN_NOT_OUT]),
    ]


# ---------------------------------------------------------------------------------------------
# Pools, verbs and verdicts
# ---------------------------------------------------------------------------------------------


def cash_core(edge: pd.DataFrame, position: str) -> pd.DataFrame:
    """Rosterable, available players at a position by `Hit3x%`, among those whose `CalPts` is at least the
    position's median (CalPts%ile >= 50). Every one of them, best first; the sheet shows the top few."""
    part = edge[(edge["Position"] == position) & available(edge) & rosterable(edge)]
    part = part[(pd.to_numeric(part["CalPts%ile"], errors="coerce") >= CASH_CORE_MIN_CALPTS_PCT)]
    part = part[pd.to_numeric(part["Hit3x%"], errors="coerce").notna()]
    return part.sort_values("Hit3x%", ascending=False)


def gpp_upside(edge: pd.DataFrame, position: str) -> pd.DataFrame:
    part = edge[(edge["Position"] == position) & available(edge) & rosterable(edge)]
    part = part[pd.to_numeric(part["Boom%"], errors="coerce").notna()]
    return part.sort_values("Boom%", ascending=False)


def disagreements(edge: pd.DataFrame, position: str, direction: str) -> pd.DataFrame:
    """Largest CalPts - ProjPts at a position among rosterable, available players with a TFFB projection:
    `up` = the model is higher than TFFB, `down` = lower."""
    mask = (edge["Position"] == position) & available(edge) & rosterable(edge)
    part = edge[mask].copy()
    part["Diff"] = pd.to_numeric(part["CalPts"], errors="coerce") - pd.to_numeric(
        part["ProjPts"], errors="coerce"
    )
    part = part[part["Diff"].notna() & (pd.to_numeric(part["ProjPts"], errors="coerce") > 0)]
    return part.sort_values("Diff", ascending=(direction == "down"))


def punt_plays(edge: pd.DataFrame, position: str) -> tuple[pd.DataFrame, float | None]:
    """Rosterable, available players within `PUNT_SALARY_WINDOW` of the position's cheapest salary on the
    slate (the floor counts every listing, as DraftKings' own price floor does), best `ValAdj` first. Returns
    the players and the floor. The Board's old Punt finder, ported."""
    at = edge[edge["Position"] == position]
    salary = pd.to_numeric(at["Salary"], errors="coerce")
    if salary.dropna().empty:
        return at.iloc[0:0], None
    floor = float(salary.min())
    part = edge[(edge["Position"] == position) & available(edge) & rosterable(edge)]
    part = part[pd.to_numeric(part["Salary"], errors="coerce") <= floor + PUNT_SALARY_WINDOW]
    part = part[pd.to_numeric(part["ValAdj"], errors="coerce").notna()]
    return part.sort_values("ValAdj", ascending=False), floor


def _and(names: tuple[str, ...]) -> str:
    return " and ".join(names)


def disagreement_reason(row: pd.Series, fitted: calibration.Calibration | None) -> str:
    """Why CalPts differs from ProjPts, split into two parts that add up to the gap: what the measured bias
    for his position and salary contributes (the amount actually applied, after shrinking for a small sample)
    and what the other sources contribute by disagreeing with TFFB. A source he lacks is named."""
    why = calibration.explain_gap(row, fitted)
    if why is None:
        return "sources disagree with TFFB"
    parts = []
    if abs(why.bias) >= 0.05:
        thin = "; shrunk, small sample" if 0 < why.n < calibration.CAL_SHRINK_K else ""
        parts.append(f"{why.bias:+.1f} from TFFB's measured bias on {why.cell} (n={why.n}{thin})")
    if abs(why.sources) >= 0.05:
        side = "higher" if why.higher and not why.lower else "lower"
        names = why.higher if side == "higher" else why.lower
        if names and not (why.higher and why.lower):
            verb = f"{_and(names)} both project {side}" if len(names) > 1 else f"{names[0]} projects {side}"
        else:
            verb = "the other sources differ from TFFB"
        parts.append(f"{why.sources:+.1f} because {verb}")
    if why.missing:
        parts.append(f"no {_and(why.missing)} projection for him")
    return "; ".join(parts) if parts else "sources agree with TFFB"


def disagreement_short(row: pd.Series, fitted: calibration.Calibration | None) -> str:
    """The two parts of the gap in one line: `-1.2 other sources, +0.4 bias`. The whole reason is the note."""
    why = calibration.explain_gap(row, fitted)
    if why is None:
        return "sources disagree with TFFB"
    parts = []
    if abs(why.sources) >= 0.05:
        parts.append(f"{why.sources:+.1f} other sources")
    if abs(why.bias) >= 0.05:
        parts.append(f"{why.bias:+.1f} bias")
    return ", ".join(parts) or "sources agree with TFFB"


def verb_with_proj(verb: str, chip: str | None) -> str:
    """One verb per row, with the R6 chip beside it, never a second instruction. A `Proj ▼` DOWNGRADES a
    `Cash add` to `Cash option (Proj ▼)` (a fade signal cannot sit beside "add"); a `Proj ▲` never upgrades
    anything, it only shows beside the verb (`Cash option (Proj ▲)`). `Out` carries no chip."""
    if verb == "Out" or chip not in (usage_r6.PROJ_DOWN, usage_r6.PROJ_UP):
        return verb
    if chip == usage_r6.PROJ_DOWN and verb == "Cash add":
        verb = "Cash option"
    return f"{verb} ({chip})"


def cash_verb(rank: int, bust: float, bust_median: float) -> str:
    """`Cash add`: top `CASH_ADD_TOP_N` Hit3x% at the position AND Bust% below the position median."""
    return "Cash add" if rank <= CASH_ADD_TOP_N and bust < bust_median else "Cash option"


def gpp_verb(
    boom: float, boom_cut: float, starred: bool, lev: float | None = None, boom_half: bool = False
) -> str:
    """`GPP leverage`: `Lev` at least `LEVERAGE_MIN_LEV` with Boom% in the top half of his position;
    `GPP add`: top-quartile Boom%; `GPP option` otherwise. A star (top-quartile Boom% owned below his
    position's median, `gpp_upside` / `_gpp_section`) is appended to any of them."""
    if lev is not None and pd.notna(lev) and lev >= LEVERAGE_MIN_LEV and boom_half:
        verb = "GPP leverage"
    elif boom >= boom_cut:
        verb = "GPP add"
    else:
        verb = "GPP option"
    return f"{verb} {STAR}" if starred else verb


def chalk_verb(bust_pct: float) -> str:
    """`Chalk: eat` when his Bust% rank within the position is in the bottom third (safe), `Chalk: fade
    candidate` in the top third, plain `Chalk` between. `bust_pct` is that rank, 0-1 (1 = most bust-prone)."""
    if bust_pct <= CHALK_THIRD:
        return "Chalk: eat"
    if bust_pct >= 1 - CHALK_THIRD:
        return "Chalk: fade candidate"
    return "Chalk"


def cash_verdict(position: str, best: float, history: dict[str, tuple[float, int]]) -> str | None:
    """ "Thin week at RB: best cash odds 34% (typical best ~41%)" when the block's best `Hit3x%` is below the
    median best of the earlier weeks; None when it is not, or when there is no history (never invented)."""
    if position not in history:
        return None
    typical, weeks = history[position]
    if best >= typical:
        return None
    return (
        f"Thin week at {position}: best cash odds {best:.0f}% (typical best ~{typical:.0f}%, "
        f"{weeks} earlier week{'s' if weeks != 1 else ''})"
    )


def team_players(edge: pd.DataFrame, team: str, position: str) -> pd.DataFrame:
    """A team's top `MATCHUP_PLAYERS` rosterable, available players at a position by CalPts, among those the
    outcome model rated (a backup it has no odds for is not an option worth listing)."""
    part = edge[(edge["Team"] == team) & (edge["Position"] == position) & available(edge) & rosterable(edge)]
    part = part[pd.to_numeric(part["Hit3x%"], errors="coerce").notna()]
    part = part.assign(_c=pd.to_numeric(part["CalPts"], errors="coerce"))
    return part.sort_values("_c", ascending=False).head(MATCHUP_PLAYERS)


def with_leverage(edge: pd.DataFrame) -> pd.DataFrame:
    """The edge frame with `BoomRank`, `OwnRank`, `Lev` and `BoomHalf`, all within position among the
    rosterable, available players who have both a `Boom%` and an `Own%`: the percentile rank (0-100) of each,
    `Lev` their difference (Boom% rank minus Own% rank, rounded), `BoomHalf` whether his Boom% is at or above
    the position's median. All NaN / False for a player outside that pool, and for everyone while ownership is
    unpublished. Reads `Own%`, changes none of the projection columns."""
    out = edge.copy()
    out["BoomRank"] = np.nan
    out["OwnRank"] = np.nan
    out["Lev"] = np.nan
    out["BoomHalf"] = False
    pool = rosterable(out) & available(out)
    for position in POSITIONS:
        at = out[pool & (out["Position"] == position)]
        boom = pd.to_numeric(at["Boom%"], errors="coerce")
        own = pd.to_numeric(at["Own%"], errors="coerce")
        ok = boom.notna() & own.notna()
        if "OwnStatus" in at.columns:
            ok &= at["OwnStatus"] == "real"  # a placeholder 0 before ownership publishes is not ownership
        if ok.sum() < 2:
            continue
        boom, own = boom[ok], own[ok]
        boom_pct, own_pct = boom.rank(pct=True) * 100, own.rank(pct=True) * 100
        out.loc[boom.index, "BoomRank"] = boom_pct.round()
        out.loc[own.index, "OwnRank"] = own_pct.round()
        out.loc[boom.index, "Lev"] = (boom_pct - own_pct).round()
        out.loc[boom.index, "BoomHalf"] = boom >= boom.median()
    return out


def leverage_plays(edge: pd.DataFrame, position: str) -> pd.DataFrame:
    """Players at a position with Boom% in the top half and a `Lev`, best `Lev` first."""
    part = edge[(edge["Position"] == position) & edge["BoomHalf"] & edge["Lev"].notna()]
    return part.sort_values(["Lev", "Boom%"], ascending=False)


def chalk_players(edge: pd.DataFrame, position: str) -> pd.DataFrame:
    """The highest-owned rosterable, available players at a position, with `BustPct`: his Bust% rank within
    that position's pool, 0-1 (1 = the most bust-prone)."""
    pool = rosterable(edge) & available(edge) & (edge["Position"] == position)
    part = edge[pool & pd.to_numeric(edge["Own%"], errors="coerce").gt(0)].copy()
    if part.empty:
        return part
    bust = pd.to_numeric(edge.loc[pool, "Bust%"], errors="coerce")
    part["BustPct"] = bust.rank(pct=True).reindex(part.index)
    return part.sort_values("Own%", ascending=False)


# ---------------------------------------------------------------------------------------------
# The layout
# ---------------------------------------------------------------------------------------------


def _lead(r, pos: str | None = None) -> list:
    """Name, Pos, Team, Salary and Own% (blank until ownership publishes)."""
    return [r["Name"], pos or r["Position"], r["Team"], r["Salary"], r.get("Own%", "")]


def _muted(r) -> bool:
    return _is_thin(r.get("Games"))


def _vs_tffb(r) -> str:
    """`CalPts 1.8 under TFFB`: the one fact about the projection the other columns do not state."""
    diff = float(r["CalPts"]) - float(r["ProjPts"])
    if abs(diff) < 0.05:
        return "CalPts matches TFFB"
    return f"CalPts {abs(diff):.1f} {'over' if diff > 0 else 'under'} TFFB"


def _short(*parts: str) -> str:
    return " · ".join(p for p in parts if p)


def _thin_short(games) -> str:
    return f"{int(games)} game{'s' if int(games) != 1 else ''}" if _is_thin(games) else ""


def _ranked_blocks(
    b: _Builder,
    parts: dict[str, pd.DataFrame],
    row_for,
    *,
    colour: dict[str, bool],
    before_rows=None,
) -> None:
    """One block per position: subheader ("RB — top 10 of 28"), the optional verdict, the visible rows, then
    the rest (up to `MAX_PER_POSITION` in all) in a collapsed group under "▸ 18 more RBs (click + to show)".
    `row_for(position, rank, total, row)` writes one player row. `colour` maps a header name to whether a
    LOWER value is better, for the probability colour inside each block."""
    for pos in POSITIONS:
        part = parts.get(pos)
        if part is None or part.empty:
            continue
        total = len(part)
        shown = part.head(MAX_PER_POSITION)
        visible = VISIBLE_PER_POSITION[pos]
        b.sub(f"{pos}  —  top {min(visible, total)} of {total}")
        if before_rows is not None:
            before_rows(pos, part)
        block_first = b.next_row
        for rank, (_, r) in enumerate(shown.head(visible).iterrows(), start=1):
            row_for(pos, rank, total, r)
        rest = shown.iloc[visible:]
        if not rest.empty:
            b.open_group(
                f"▸ {len(rest)} more {_plural(pos)} (click + to show)", f"{b.section_key}|{pos}|more"
            )
            for rank, (_, r) in enumerate(rest.iterrows(), start=visible + 1):
                row_for(pos, rank, total, r)
            b.close_group()
        for name, lower_is_better in colour.items():
            b.layout.prob_blocks.append((name, block_first, b.last_row, lower_is_better))


def build_layout(inputs: Inputs | None, *, edge_tab_name: str = "EdgeRaw") -> Layout:
    """The whole tab. `inputs` None gives the empty-state layout (a template, or no sync yet)."""
    b = _Builder(inputs.r6 if inputs is not None else None)
    b.add(["EDGE FINDER  —  calibrated projections, outcome odds and the signals that matter"])
    if inputs is None:
        b.note("Not synced yet -- run `dfs sync`; this tab is written by the sync and holds nothing typed.")
        for title in (
            "CASH CORE  —  best Hit3x% per position",
            "GPP UPSIDE  —  best Boom% per position",
            "LEVERAGE PLAYS  —  the most upside for the ownership",
            "CHALK TO FADE OR EAT  —  the highest-owned at each position",
            "PUNT PLAYS  —  the best value near each position's cheapest salary",
            "PROJECTION DISAGREEMENTS  —  where CalPts differs most from TFFB",
            "INJURY BENEFICIARIES  —  who inherits a back's carries; absences are context",
            "USAGE TRENDS  —  whose usage moved beyond normal noise",
            "MATCHUPS (CONTEXT)  —  softest and toughest offenses",
            "CONTEXT SIGNALS  —  unproven",
        ):
            b.blank()
            b.layout.section_rows.append(b.add([title]))
            b.note("Nothing yet.")
        return b.layout

    edge = with_leverage(with_games(inputs.edge, inputs.players))
    for col in ("SleeperPts", "FantasyProsPts"):
        if col in inputs.players.columns and col not in edge.columns:
            edge = edge.merge(inputs.players[["Id", col]].drop_duplicates("Id"), on="Id", how="left")
    published = (edge.get("OwnStatus", pd.Series(dtype=object)) == "real").any()
    if not published:
        edge["Own%"] = np.nan  # defensive: the status line and every Own% cell agree it is not out
    for line in _status_lines(inputs, published):
        b.layout.status_rows.append(b.add([line]))

    _cash_section(b, edge, inputs)
    _gpp_section(b, edge, published)
    _leverage_section(b, edge, published)
    _chalk_section(b, edge, published)
    _punt_section(b, edge)
    _disagreement_section(b, edge, inputs.calibration)
    by_gsis = (
        edge.drop_duplicates("GsisId").set_index("GsisId") if "GsisId" in edge.columns else pd.DataFrame()
    )
    _injury_section(b, inputs, by_gsis)
    _trend_section(b, inputs, edge)
    _matchup_section(b, inputs, edge)
    _signal_section(b, edge)
    _mark_formats(b.layout)
    return b.layout


def _cash_section(b: _Builder, edge: pd.DataFrame, inputs: Inputs) -> None:
    b.begin_section(
        "CASH CORE  —  best Hit3x% per position (CalPts at least the position median)", link="Cash"
    )
    b.header(CASH_COLUMNS)
    parts = {pos: cash_core(edge, pos) for pos in POSITIONS}
    medians = {p: pd.to_numeric(df["Bust%"], errors="coerce").median() for p, df in parts.items() if len(df)}

    def verdict(pos: str, part: pd.DataFrame) -> None:
        text = cash_verdict(pos, float(part.iloc[0]["Hit3x%"]), inputs.history)
        if text:
            b.verdict(text)

    def row_for(pos: str, rank: int, total: int, r: pd.Series) -> None:
        # Cash rows never mention ownership: it is a large-field projection and Sam ignores it in cash.
        why = _join(
            f"{r['Hit3x%']:.0f}% to reach 3x salary, {r['Bust%']:.0f}% to bust (under 2x)",
            f"CalPts {r['CalPts']:.1f} vs TFFB {r['ProjPts']:.1f}",
            _thin_note(r.get("Games")),
        )
        b.player_row(
            _lead(r, pos),
            [r["CalPts"], r["Hit3x%"], r["Bust%"], r["ProjPts"], f"#{rank} of {total}", r["Edge"]],
            why=why,
            short=_short(_vs_tffb(r), _thin_short(r.get("Games"))),
            verb=cash_verb(rank, r["Bust%"], medians.get(pos, np.inf)),
            pid=r["Id"],
            gsis=r.get("GsisId"),
            edge=r.get("Edge", ""),
            muted=_muted(r),
        )

    _ranked_blocks(b, parts, row_for, colour={"Hit3x%": False, "Bust%": True}, before_rows=verdict)
    b.end_section()


def _gpp_section(b: _Builder, edge: pd.DataFrame, published: bool) -> None:
    b.begin_section("GPP UPSIDE  —  best Boom% per position", link="GPP")
    b.header(GPP_COLUMNS)
    if not published:
        b.note(OWN_NOT_OUT)
    parts = {pos: gpp_upside(edge, pos) for pos in POSITIONS}
    cuts = {}
    for pos, part in parts.items():
        if part.empty:
            continue
        own = pd.to_numeric(part["Own%"], errors="coerce")
        cuts[pos] = (
            pd.to_numeric(part["Boom%"], errors="coerce").quantile(GPP_ADD_TOP_QUARTILE),
            own[own > 0].quantile(OWN_STAR_BOTTOM_HALF) if (own > 0).any() else np.nan,
        )

    def row_for(pos: str, rank: int, total: int, r: pd.Series) -> None:
        boom_cut, own_cut = cuts[pos]
        own = r["Own%"] if published and pd.notna(r["Own%"]) and r["Own%"] > 0 else ""
        starred = bool(
            published and own != "" and pd.notna(own_cut) and r["Boom%"] >= boom_cut and own <= own_cut
        )
        lev = r["Lev"] if pd.notna(r["Lev"]) else ""
        why = _join(
            f"{r['Boom%']:.0f}% to reach 4x salary; the model's 85th percentile is {r['CeilM']:.1f} pts",
            f"owned {own:.1%}" if own != "" else ("" if published else "ownership not out yet"),
            f"Lev {lev:+.0f} (Boom% rank minus Own% rank among {_plural(pos)})" if lev != "" else "",
            "low-owned for his upside" if starred else "",
            _thin_note(r.get("Games")),
        )
        short = (
            "Low-owned for his upside"
            if starred
            else (
                f"Boom {r['BoomRank']:.0f}th pct, owned {r['OwnRank']:.0f}th pct"
                if lev != ""
                else ("Top-quartile Boom%" if r["Boom%"] >= boom_cut else "")
            )
        )
        b.player_row(
            _lead(r, pos),
            [r["CalPts"], r["Boom%"], r["CeilM"], lev, f"#{rank} of {total}", r["Edge"]],
            why=why,
            short=_short(short, _thin_short(r.get("Games"))),
            verb=gpp_verb(r["Boom%"], boom_cut, starred, r["Lev"], bool(r["BoomHalf"])),
            pid=r["Id"],
            gsis=r.get("GsisId"),
            edge=r.get("Edge", ""),
            muted=_muted(r),
        )

    _ranked_blocks(b, parts, row_for, colour={"Boom%": False, "Lev": False})
    b.end_section()


def _leverage_section(b: _Builder, edge: pd.DataFrame, published: bool) -> None:
    """The top `LEVERAGE_ROWS_PER_POSITION` at each position by `Lev`, among players with a top-half Boom%."""
    b.begin_section("LEVERAGE PLAYS  —  the most upside for the ownership (Lev = Boom% rank minus Own% rank)")
    b.header(LEVERAGE_COLUMNS)
    if not published:
        b.note(OWN_NOT_OUT)
        b.end_section()
        return
    for pos in POSITIONS:
        part = leverage_plays(edge, pos)
        if part.empty:
            continue
        shown = part.head(LEVERAGE_ROWS_PER_POSITION)
        b.sub(f"{pos}  —  top {len(shown)} of {len(part)} by Lev")
        first = b.next_row
        for rank, (_, r) in enumerate(shown.iterrows(), start=1):
            lev = r["Lev"]
            b.player_row(
                _lead(r, pos),
                [r["CalPts"], r["Boom%"], r["Bust%"], lev, f"#{rank} of {len(part)}", r["Edge"]],
                why=_join(
                    f"Lev {lev:+.0f}: Boom% is at the {r['BoomRank']:.0f}th percentile of {_plural(pos)}, "
                    f"ownership at the {r['OwnRank']:.0f}th ({r['Own%']:.1%} owned)",
                    f"{r['Boom%']:.0f}% to reach 4x salary",
                    _thin_note(r.get("Games")),
                ),
                short=_short(
                    f"Boom {r['BoomRank']:.0f}th pct, owned {r['OwnRank']:.0f}th", _thin_short(r.get("Games"))
                ),
                verb="GPP leverage" if lev >= LEVERAGE_MIN_LEV else "GPP option",
                pid=r["Id"],
                gsis=r.get("GsisId"),
                edge=r.get("Edge", ""),
                muted=_muted(r),
            )
        b.layout.prob_blocks.append(("Lev", first, b.last_row, False))
    b.end_section()


def _chalk_section(b: _Builder, edge: pd.DataFrame, published: bool) -> None:
    """The `CHALK_ROWS_PER_POSITION` highest-owned players at each position, with an eat or fade read."""
    b.begin_section("CHALK TO FADE OR EAT  —  the highest-owned at each position")
    b.header(CHALK_COLUMNS)
    if not published:
        b.note(OWN_NOT_OUT)
        b.end_section()
        return
    for pos in POSITIONS:
        part = chalk_players(edge, pos).head(CHALK_ROWS_PER_POSITION)
        if part.empty:
            continue
        b.sub(f"{pos}  —  the {len(part)} highest-owned")
        for rank, (_, r) in enumerate(part.iterrows(), start=1):
            verb = chalk_verb(float(r["BustPct"]))
            third = {"Chalk: eat": "bottom", "Chalk: fade candidate": "top"}.get(verb, "middle")
            b.player_row(
                _lead(r, pos),
                [r["CalPts"], r["Boom%"], r["Bust%"], r["ProjPts"], f"#{rank} owned", r["Edge"]],
                why=_join(
                    f"owned {r['Own%']:.1%}; {r['Boom%']:.0f}% to reach 4x salary, {r['Bust%']:.0f}% to bust",
                    f"his bust odds are in the {third} third of {_plural(pos)}",
                    _thin_note(r.get("Games")),
                ),
                short=_short(f"Bust odds in the {third} third", _thin_short(r.get("Games"))),
                verb=verb,
                pid=r["Id"],
                gsis=r.get("GsisId"),
                edge=r.get("Edge", ""),
                muted=_muted(r),
            )
    b.end_section()


def _punt_section(b: _Builder, edge: pd.DataFrame) -> None:
    b.begin_section("PUNT PLAYS  —  the best value near each position's cheapest salary")
    b.header(PUNT_COLUMNS)
    for pos in POSITIONS:
        part, floor = punt_plays(edge, pos)
        if part.empty:
            b.sub(f"{pos}  —  no rosterable player within {money(PUNT_SALARY_WINDOW)} of the cheapest")
            continue
        shown = part.head(PUNT_ROWS_PER_POSITION)
        b.sub(f"{pos}  —  best {len(shown)} of {len(part)} within {money(PUNT_SALARY_WINDOW)} of {_k(floor)}")
        for rank, (_, r) in enumerate(shown.iterrows(), start=1):
            gap = float(r["Salary"]) - floor
            above = f"{money(gap)} above the cheapest {pos}" if gap else f"the cheapest {pos} on the slate"
            b.player_row(
                _lead(r, pos),
                [r["CalPts"], r["ValAdj"], r["Hit3x%"], f"#{rank} of {len(part)}", r["Edge"]],
                why=_join(
                    above,
                    f"ValAdj {r['ValAdj']:.1f} (value against what his price usually buys)",
                    _thin_note(r.get("Games")),
                ),
                short=_short(above, _thin_short(r.get("Games"))),
                verb="Punt option",
                pid=r["Id"],
                gsis=r.get("GsisId"),
                edge=r.get("Edge", ""),
                muted=_muted(r),
            )
    b.end_section()


def _disagreement_section(b: _Builder, edge: pd.DataFrame, fitted) -> None:
    b.begin_section(
        "PROJECTION DISAGREEMENTS  —  where CalPts differs most from TFFB (most of the field uses raw ones)"
    )
    b.header(DISAGREE_COLUMNS)
    for pos in POSITIONS:
        for direction, label, verb in (
            ("up", "CalPts above TFFB", "Look closer ▲"),
            ("down", "CalPts below TFFB", "Caution ▼"),
        ):
            part = disagreements(edge, pos, direction).head(DISAGREEMENT_PER_DIRECTION)
            if part.empty:
                continue
            b.sub(f"{pos}  —  {label}")
            for _, r in part.iterrows():
                b.player_row(
                    _lead(r, pos),
                    [
                        r["ProjPts"],
                        r.get("SleeperPts"),
                        r.get("FantasyProsPts"),
                        r["CalPts"],
                        round(r["Diff"], 1),
                        r["Edge"],
                    ],
                    why=_join(disagreement_reason(r, fitted), _thin_note(r.get("Games"))),
                    short=_short(disagreement_short(r, fitted), _thin_short(r.get("Games"))),
                    verb=verb,
                    pid=r["Id"],
                    gsis=r.get("GsisId"),
                    edge=r.get("Edge", ""),
                    muted=_muted(r),
                )
    b.end_section()


def _injury_section(b: _Builder, inputs: Inputs, by_gsis: pd.DataFrame) -> None:
    b.begin_section(
        "INJURY BENEFICIARIES  —  who inherits a back's carries (confirmed first, questionable muted)"
    )
    b.header(BENEFICIARY_COLUMNS)
    ben = on_slate(inputs.beneficiaries, by_gsis)
    if ben.empty:
        b.note("No regular back is out or questionable with a beneficiary this week.")
    else:
        for status, label, cap, muted, verb in (
            (
                "out",
                "Confirmed out (DraftKings or the injury report)",
                BENEFICIARY_CONFIRMED_N,
                False,
                "Bump ▲",
            ),
            ("questionable", "Questionable (assumed to play)", BENEFICIARY_QUESTIONABLE_N, True, "Watch"),
        ):
            part = ben[ben["OutStatus"] == status].head(cap)
            total = int((ben["OutStatus"] == status).sum())
            if part.empty:
                continue
            more = max(total - cap, 0)
            b.sub(f"{label}  —  {len(part)} of {total}" + (f" (+{more} more)" if more else ""))
            for _, r in part.iterrows():
                info = by_gsis.loc[r["GsisId"]] if r["GsisId"] in by_gsis.index else None
                games = info["Games"] if info is not None and "Games" in info else np.nan
                priced = r.get("PricedIn", "")
                method = r["Method"] + (f" ({int(r['n'])} g)" if r["Method"] == "with-or-without" else "")
                state = "out" if status == "out" else "questionable"
                priced_text = f"priced in: {priced}" if isinstance(priced, str) and priced else ""
                b.player_row(
                    [
                        r["Name"],
                        r["Position"],
                        r["Team"],
                        info["Salary"] if info is not None else "",
                        info["Own%"] if info is not None else "",
                    ],
                    [
                        round(r["car_gain"], 1),
                        round(r["xfp_gain"], 1),
                        method,
                        priced,
                        info["Edge"] if info is not None else "",
                    ],
                    why=_join(
                        f"{r['OutPlayers']} {state}: +{r['car_gain']:.1f} carries and "
                        f"+{r['xfp_gain']:.1f} expected points a game ({method})",
                        priced_text,
                        _thin_note(games),
                    ),
                    short=_short(f"{r['OutPlayers']} {state}", priced_text, _thin_short(games)),
                    verb=verb,
                    pid=info["Id"] if info is not None else "",
                    gsis=r["GsisId"],
                    edge=info["Edge"] if info is not None else "",
                    muted=muted or _is_thin(games),
                )
    # Every confirmed absence of a regular, as context. No points are attached, for any position.
    b.sub("Absent regulars  —  context, not an edge (no points are moved for targets)")
    b.header(ABSENCE_COLUMNS)
    absent = inputs.absences
    if absent is None or absent.empty:
        b.note("No regular is confirmed out this week.")
    else:
        for _, r in absent.iterrows():
            info = by_gsis.loc[r["GsisId"]] if r["GsisId"] in by_gsis.index else None
            texts = [str(t) for t in (r.get("WithWithout"), r.get("History")) if isinstance(t, str) and t]
            b.player_row(
                [
                    r["Name"],
                    r["Position"],
                    r["Team"],
                    info["Salary"] if info is not None else "",
                    info["Own%"] if info is not None else "",
                ],
                [r["Role"], round(r["tgt_g"], 1), round(r["car_g"], 1), int(r["GamesMissed"])],
                why=_join(f"{r.get('Regular', '')}", *texts),
                short=str(r.get("Regular", "") or ""),
                verb="Out",
                pid=info["Id"] if info is not None else "",
                muted=True,
            )
    b.end_section()


def _trend_section(b: _Builder, inputs: Inputs, edge: pd.DataFrame) -> None:
    b.begin_section("USAGE TRENDS  —  whose last-3-games usage moved a lot against the 6 before")
    b.header(TREND_HEADERS)
    view = inputs.r6
    if view is None:
        b.note(
            "Usage trends need the R6 data: run a full `dfs sync` (it fetches last season's and this "
            "season's stats, play-by-play and snap counts)."
        )
        b.end_section()
        return
    trends = view.trends
    marked = trends[trends["Direction"] != ""] if not trends.empty else trends
    if view.short_games:
        b.note(
            f"Not enough games: {view.short_games} pool players have fewer than {view.min_games} earlier "
            "games, so they get no arrow (3 for the last-3 window and 6 for the 6 before it)."
        )
    if marked.empty:
        b.note("No player's usage has moved by more than R6's measured band over the last 3 games yet.")
        b.end_section()
        return
    ids = (
        inputs.players.drop_duplicates("GsisId").set_index("GsisId")["Id"]
        if "GsisId" in inputs.players.columns
        else pd.Series(dtype=object)
    )
    by_id = edge.drop_duplicates("Id").set_index("Id")
    marked = marked.assign(_id=marked["GsisId"].map(ids), _z=marked["Z"].abs()).dropna(subset=["_id"])
    marked["_id"] = marked["_id"].astype(int)
    marked = marked[marked["_id"].isin(by_id.index)]
    for pos in usage_r6.TREND_POSITIONS:
        at = marked[marked["Position"] == pos].sort_values("_z", ascending=False)
        if at.empty:
            continue
        # one row per player: his biggest move, with his other moves named in the Why
        players = list(dict.fromkeys(at["_id"]))
        shown = players[:TREND_ROWS_PER_POSITION]
        more = len(players) - len(shown)
        b.sub(
            f"{pos}  —  {len(shown)} of {len(players)} players moved more than the measured band"
            + (f" (+{more} more)" if more else "")
        )
        for pid in shown:
            moves = at[at["_id"] == pid]
            r = moves.iloc[0]
            e = by_id.loc[pid]
            metric = r["Metric"]
            others = [f"{m['Metric']} {m['Direction']}" for _, m in moves.iloc[1:].iterrows()]
            move = "jump" if r["Direction"] == usage_r6.UP else "drop"
            numbers, patterns = usage_r6.trend_cells(metric, r["Recent"], r["Prior"], r["Change"])
            row = b.player_row(
                [e["Name"], pos, e["Team"], e["Salary"], e.get("Own%", "")],
                [metric, *numbers, r["Direction"]],
                why=_join(usage_r6.trend_why(r), f"also moved: {', '.join(others)}" if others else ""),
                short=f"A bigger {move} than {1 - float(r['FlagRate']):.0%} of weeks",
                verb="Watch",
                pid=pid,
                gsis=r["GsisId"],
                edge=e.get("Edge", ""),
            )
            for offset, (pattern, kind) in enumerate(patterns, start=1):  # slots 2-4: Last 3, Earlier, Change
                b.layout.cell_formats.append((f"{column_letter(len(LEAD) + offset)}{row}", pattern, kind))
    b.end_section()


def _matchup_section(b: _Builder, inputs: Inputs, edge: pd.DataFrame) -> None:
    b.begin_section("MATCHUPS (CONTEXT)  —  the 8 best and 4 toughest offenses (DST: the defense's spot)")
    b.header(MATCHUP_COLUMNS, lead=MATCHUP_LEAD)
    mu = inputs.matchups
    if mu.empty:
        b.note("No matchup tables yet (the schedule or last season's results were unavailable).")
        b.end_section()
        return
    pos_i, grade_i, players_i = LEAD.index("Pos"), LEAD.index("Team"), LEAD.index("Salary")
    for pos in POSITIONS:
        part = mu[mu["Position"] == pos]
        if part.empty:
            continue
        b.sub(pos)
        for _, r in part.iterrows():
            players = team_players(edge, r["Team"], pos)
            soft = r["Group"] == "top"
            row = b.open_group(f"{r['Team']} vs {r['Opp']}", f"MATCHUPS|{pos}|{r['Team']}", team=True)
            cells = b.layout.rows[row - 1]
            cells[pos_i], cells[grade_i] = pos, "Soft" if soft else "Tough"
            if not players.empty:  # one cell that overflows right; the salaries are on the player rows below
                cells[players_i] = ", ".join(players["Name"])
            cells[FIRST_TRAILING + TRAILING_HEADERS.index("Do")] = "Context only"
            reason = _join(
                str(r["Reasons"]),
                f"score {r['Score']:+.2f} (standard deviations above the league average; higher is softer)",
            )
            cells[FIRST_TRAILING + TRAILING_HEADERS.index("Why")] = short_why(reason)
            b.layout.notes[row] = reason
            if not soft:
                b.layout.muted_rows.append(row)
            if not players.empty:
                b.inner_header(MATCHUP_COLUMNS)
            for _, p in players.iterrows():
                b.player_row(
                    _lead(p, pos),
                    [p["CalPts"], p["Hit3x%"], p["Boom%"]],
                    why=f"CalPts {p['CalPts']:.1f} vs TFFB {p['ProjPts']:.1f}",
                    short=_vs_tffb(p),
                    verb="Context only",
                    pid=p["Id"],
                    gsis=p.get("GsisId"),
                    edge=p.get("Edge", ""),
                )
            b.close_group()
    b.end_section()


def _r6_signal_blocks(b: _Builder, edge: pd.DataFrame) -> None:
    """Each of R6's 12 signals as its own sub-block: who is flagged, the effect, the evidence tier."""
    view = b.r6
    if view is None:
        b.note(
            "R6 usage signals are not available this sync (they need a full `dfs sync` and models/research/)."
        )
        return
    by_gsis = (
        edge.drop_duplicates("GsisId").set_index("GsisId") if "GsisId" in edge.columns else pd.DataFrame()
    )
    b.sub("R6 USAGE SIGNALS  —  spikes and slumps fade back (measured against the research projection)")
    for chip in view.chips.values():
        flagged = [
            g
            for g, s in view.signals.items()
            if chip.id in s.fired and not by_gsis.empty and g in by_gsis.index
        ]
        part = by_gsis.loc[flagged].copy() if flagged else by_gsis.iloc[0:0]
        part = part.assign(_c=pd.to_numeric(part.get("CalPts"), errors="coerce")).sort_values(
            "_c", ascending=False
        )
        read = "FADE (Proj ▼)" if chip.direction == usage_r6.FADE else "BUMP (Proj ▲)"
        tier = "weaker evidence" if chip.weaker else "tested on 2014-21 and 2022-25"
        b.sub(
            f"{chip.position} {usage_r6.threshold_text(chip)}  —  {len(part)} flagged  ·  {read}  ·  "
            f"{chip.effect_um_test:+.1f} pts vs the research projection  ·  {tier}"
        )
        if not part.empty:  # these columns are not the section's (Token | xFP/G | DK/G L3): name them
            b.inner_header(R6_SIGNAL_COLUMNS)
        for _, r in part.head(R6_SIGNAL_ROWS).iterrows():
            b.player_row(
                _lead(r),
                [read.split(" ")[0], r["CalPts"], r["ProjPts"]],
                why=usage_r6.chip_why(chip),
                short=short_why(usage_r6.chip_why(chip)),
                verb="Context only",
                pid=r["Id"],
                gsis=r.get("GsisId"),
                edge=r.get("Edge", ""),
                muted=chip.weaker,
            )
        if len(part) > R6_SIGNAL_ROWS:
            b.note(f"+{len(part) - R6_SIGNAL_ROWS} more flagged")


def _signal_section(b: _Builder, edge: pd.DataFrame) -> None:
    b.begin_section("CONTEXT SIGNALS  —  unproven; tracked in Model Check")
    b.header(SIGNAL_COLUMNS)
    pool = rosterable(edge)
    for token in ["FADE↓", "USAGE↑", "USAGE↓"]:
        has = edge["Edge"].fillna("").astype(str).map(lambda text, k=token: k in usage_r6.edge_tokens(text))
        part = edge[has & pool & available(edge)].sort_values("CalPts", ascending=False)
        if part.empty:
            continue
        more = max(len(part) - SIGNALS_PER_TOKEN, 0)
        b.sub(
            f"{token}  —  {min(len(part), SIGNALS_PER_TOKEN)} of {len(part)}"
            + (f" (+{more} more)" if more else "")
        )
        for _, r in part.head(SIGNALS_PER_TOKEN).iterrows():
            b.player_row(
                _lead(r),
                [token, r["xFP/G"], r.get("DkG", "")],
                why=_join("context only, not proven to beat the projection", _thin_note(r.get("Games"))),
                short=_short("Context only, unproven", _thin_short(r.get("Games"))),
                verb="Context only",
                pid=r["Id"],
                gsis=r.get("GsisId"),
                edge=r.get("Edge", ""),
                muted=True,
            )
    _r6_signal_blocks(b, edge)
    b.end_section()


POINT_HEADERS = frozenset(
    {
        "CalPts",
        "ProjPts",
        "TFFB",
        "Sleeper",
        "FantasyPros",
        "CeilM",
        "Diff",
        "xFP/G",
        "Tgt/G",
        "Car/G",
        "Gain Car/G",
        "Gain xFP/G",
        "DK/G L3",
        "ValAdj",
    }
)
PERCENT_HEADERS = frozenset({"Hit3x%", "Boom%", "Bust%"})
OWN_HEADERS = frozenset({"Own%"})  # a 0-1 fraction on EdgeRaw, shown as a percent
LEV_HEADERS = frozenset({"Lev"})
CHIP_HEADERS = frozenset({"Edge", "Token", "Trend"})


def _mark_formats(layout: Layout) -> None:
    """Number-format and chip ranges, derived from the header rows (every section's columns by NAME). A
    header's block runs until the next header, section or blank row."""
    section_rows = set(layout.section_rows)
    team_rows = set(layout.team_rows)  # a team row's cells are text under other names: never in a block
    headers = set(layout.header_rows) | set(layout.inner_header_rows)
    for header_row in sorted(headers):
        names = layout.rows[header_row - 1]
        last = header_row
        while (
            last < len(layout.rows)
            and any(c != "" for c in layout.rows[last])
            and (last + 1) not in section_rows
            and (last + 1) not in headers
            and (last + 1) not in team_rows
        ):
            last += 1
        first = header_row + 1
        for index, name in enumerate(names[:FIRST_TRAILING]):
            letter = column_letter(index)
            rng = f"{letter}{first}:{letter}{last}"
            if name == "Salary":
                layout.money_cells.append(rng)
            elif name in POINT_HEADERS:
                layout.point_cells.append(rng)
            elif name in PERCENT_HEADERS:
                layout.percent_cells.append(rng)
            elif name in OWN_HEADERS:
                layout.own_cells.append(rng)
            elif name in LEV_HEADERS:
                layout.lev_cells.append(rng)
            elif name in CHIP_HEADERS:
                layout.chip_ranges.append(rng)


# ---------------------------------------------------------------------------------------------
# Board panel: "This week's edges", one line each
# ---------------------------------------------------------------------------------------------

BOARD_PANEL_LINES = 5
BOARD_TOP_CASH, BOARD_TOP_GPP, BOARD_TOP_DISAGREE, BOARD_TOP_INJURY = 3, 3, 3, 2


def _name_with(row: pd.Series, text: str) -> str:
    return f"{row['Name']} ({row['Position']}, {text})"


def board_panel_lines(inputs: Inputs | None) -> list[str]:
    """Five one-line summaries: top 3 cash core, top 3 GPP upside, top 3 disagreements, top 2 injury
    beneficiaries, best offense per position. A line with nothing to say says so."""
    if inputs is None:
        return ["Not synced yet -- run `dfs sync`."] + [""] * (BOARD_PANEL_LINES - 1)
    edge = with_games(inputs.edge, inputs.players)
    cash = pd.concat([cash_core(edge, p).head(BOARD_TOP_CASH) for p in POSITIONS if p != "DST"])
    cash = cash.sort_values("Hit3x%", ascending=False).head(BOARD_TOP_CASH)
    gpp = pd.concat([gpp_upside(edge, p).head(BOARD_TOP_GPP) for p in POSITIONS if p != "DST"])
    gpp = gpp.sort_values("Boom%", ascending=False).head(BOARD_TOP_GPP)
    diffs = pd.concat(
        [disagreements(edge, p, d).head(BOARD_TOP_DISAGREE) for p in POSITIONS for d in ("up", "down")]
    )
    diffs = (
        diffs.reindex(diffs["Diff"].abs().sort_values(ascending=False).index).head(BOARD_TOP_DISAGREE)
        if not diffs.empty
        else diffs
    )
    lines = [
        "Cash core: "
        + (" · ".join(_name_with(r, f"Hit3x {r['Hit3x%']:.0f}%") for _, r in cash.iterrows()) or "none"),
        "GPP upside: "
        + (" · ".join(_name_with(r, f"Boom {r['Boom%']:.0f}%") for _, r in gpp.iterrows()) or "none"),
        "Biggest disagreements with TFFB: "
        + (" · ".join(_name_with(r, f"CalPts {r['Diff']:+.1f}") for _, r in diffs.iterrows()) or "none"),
    ]
    by_gsis = (
        edge.drop_duplicates("GsisId").set_index("GsisId") if "GsisId" in edge.columns else pd.DataFrame()
    )
    ben = on_slate(inputs.beneficiaries, by_gsis)
    confirmed = ben[ben["OutStatus"] == "out"].head(BOARD_TOP_INJURY) if not ben.empty else ben
    lines.append(
        "Injury beneficiaries (carries): "
        + (
            " · ".join(
                f"{r['Name']} ({r['Position']}, +{r['xfp_gain']:.1f} xFP/G"
                + (
                    f", priced in: {r['PricedIn']}"
                    if isinstance(r.get("PricedIn"), str) and r["PricedIn"]
                    else ""
                )
                + ")"
                for _, r in confirmed.iterrows()
            )
            or "none"
        )
    )
    mu = inputs.matchups
    best = []
    if not mu.empty:
        for pos in POSITIONS:
            part = mu[(mu["Position"] == pos) & (mu["Group"] == "top")]
            if not part.empty:
                top = part.sort_values("Score", ascending=False).iloc[0]
                best.append(f"{pos} {top['Team']} vs {top['Opp']}")
    lines.append("Best offense per position: " + (" · ".join(best) or "none"))
    return lines
