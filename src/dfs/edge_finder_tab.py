"""The `Edge Finder` tab: pure layout from the sync's saved tables.

Nothing here reads a sheet or the network. `load_inputs` reads what `edge_finder.enrich` saved under
`data/current/edge_finder/` plus `data/current/edge.csv` and the weekly usage tables; `build_layout` turns
them into rows, groups and formatting instructions; `sheet_edge_finder.write_tab` writes them. Python writes
the tab on every sync (it is a view of the sync, not a formula tab).

**Columns (fixed across sections).** `A`-`D` Name, Pos, Team, Salary; `E`-`J` section-specific (six slots,
named by each section's header row); then, named in `TRAILING_HEADERS`: `K` **Why** (the plain-English
reason, wide), `L` **Do** (a verb), `M` **Pool** (the player's current pool state, a formula), `N` **Set**
(the action dropdown the Apps Script reads), `O` the `↗` link to his EdgeRaw row, `P` **Id** (his DraftKings
id, hidden: the Apps Script finds the player by it, never by name), and `Q` a hidden group key (see below).

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
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
import pandas as pd

from dfs import calibration, signals_data, usage_trends
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
from dfs.paths import CURRENT_DIR, RAW_DIR

EDGE_FINDER_TAB = "Edge Finder"
SECTION_TOP_N = 5  # the Board's one-line summary panel
VISIBLE_PER_POSITION = {"QB": 6, "RB": 10, "WR": 12, "TE": 6, "DST": 6}
MAX_PER_POSITION = 40
DISAGREEMENT_PER_DIRECTION = 2
BENEFICIARY_CONFIRMED_N = 12
BENEFICIARY_QUESTIONABLE_N = 6
SIGNALS_PER_TOKEN = 6
TREND_ROWS_PER_POSITION = 8
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
SET_OPTIONS = ["Cash", "GPP", "Both", "Remove"]

LEAD = ["Name", "Pos", "Team", "Salary"]  # A-D
SLOTS = 6  # E-J
TRAILING_HEADERS = ["Why", "Do", "Pool", "Set", "↗", "Id"]  # K-P
COLUMN_COUNT = len(LEAD) + SLOTS + len(TRAILING_HEADERS) + 1  # + Q, the hidden group key
FIRST_TRAILING = len(LEAD) + SLOTS


def column_letter(index: int) -> str:
    return chr(ord("A") + index)


def _trailing_letter(header: str) -> str:
    return column_letter(FIRST_TRAILING + TRAILING_HEADERS.index(header))


WHY_COL = _trailing_letter("Why")
DO_COL = _trailing_letter("Do")
POOL_COL = _trailing_letter("Pool")
SET_COL = _trailing_letter("Set")
LINK_COL = _trailing_letter("↗")
ID_COL = _trailing_letter("Id")
KEY_COL = column_letter(COLUMN_COUNT - 1)
LAST_COLUMN = KEY_COL

CASH_COLUMNS = ["CalPts", "Hit3x%", "Bust%", "ProjPts", "Rank", "Edge"]
GPP_COLUMNS = ["CalPts", "Boom%", "CeilM", "Own%", "Rank", "Edge"]
PUNT_COLUMNS = ["CalPts", "ValAdj", "Hit3x%", "Rank", "Edge"]
DISAGREE_COLUMNS = ["TFFB", "Sleeper", "FantasyPros", "CalPts", "Diff", "Edge"]
BENEFICIARY_COLUMNS = ["Gain Car/G", "Gain xFP/G", "Method", "Priced in?", "Edge"]
ABSENCE_COLUMNS = ["Role", "Tgt/G", "Car/G", "Games missed"]
TREND_HEADERS = ["Metric", "Last 3", "Earlier", "Change", "Trend"]
MATCHUP_LEAD = ["Matchup", "Pos", "", "Grade"]
MATCHUP_COLUMNS = ["Top 3 / CalPts", "Hit3x%", "Boom%"]
SIGNAL_COLUMNS = ["Token", "xFP/G", "DK/G L3"]

MEANINGS = {
    "CASH CORE": (
        "Players most likely to score 3× their salary, the pace that usually cashes.  ·  Do: pool the "
        "'Cash add' rows for cash lineups; 'Cash option' rows are the next tier."
    ),
    "GPP UPSIDE": (
        "Players with the best chance to score 4× their salary, the pace that wins tournaments.  ·  Do: pool "
        f"the 'GPP add' rows for tournaments; {STAR} marks the low-owned ones once ownership is out."
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
class Inputs:
    """What the tab is built from. `edge` is the enriched edge frame (`data/current/edge.csv`); `players` the
    signals table (`Games`, `DkG`, `GsisId`, ...); the rest are the sync's saved tables. `trends` is
    `usage_trends.compute_trends`; `history` maps a position to the typical best Hit3x% of earlier weeks."""

    edge: pd.DataFrame
    players: pd.DataFrame
    beneficiaries: pd.DataFrame
    matchups: pd.DataFrame
    status: dict
    absences: pd.DataFrame = field(default_factory=pd.DataFrame)
    calibration: calibration.Calibration | None = None
    trends: pd.DataFrame = field(default_factory=pd.DataFrame)
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
    note_rows: list[int] = field(default_factory=list)
    overflow_rows: list[int] = field(default_factory=list)
    team_rows: list[int] = field(default_factory=list)
    muted_rows: list[int] = field(default_factory=list)  # static muting (thin sample, questionable, unproven)
    player_rows: list[int] = field(default_factory=list)  # rows with Pool / Set / link cells
    status_rows: list[int] = field(default_factory=list)
    groups: list[Group] = field(default_factory=list)
    all_links: dict[int, str] = field(default_factory=dict)  # section row -> EdgeRaw filter view title
    percent_cells: list[str] = field(default_factory=list)
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


def latest_weekly_stats() -> pd.DataFrame:
    """The newest saved nflverse weekly stats file (`data/raw/stats_player/`), or empty."""
    directory = RAW_DIR / "stats_player"
    files = sorted(directory.glob("*.parquet")) if directory.exists() else []
    if not files:
        return pd.DataFrame()
    try:
        return pd.read_parquet(files[-1])
    except Exception:  # noqa: BLE001 - trends are context; a bad file must not stop the tab
        return pd.DataFrame()


def load_trends(edge: pd.DataFrame, players: pd.DataFrame) -> pd.DataFrame:
    """Usage trends for the slate: the weekly stats, red-zone and snap tables saved by the usage and snaps
    sources, with the band measured on the rosterable players only (fringe players whose usage never moves
    would shrink it). Empty when the weekly stats are not saved."""
    stats = latest_weekly_stats()
    if stats.empty:
        return pd.DataFrame(columns=usage_trends.TREND_COLUMNS)
    weekly = usage_trends.weekly_frame(
        stats,
        redzone=_read(CURRENT_DIR / "redzone_weekly.csv"),
        snaps=_read(CURRENT_DIR / "snaps_weekly.csv"),
    )
    pool = edge[rosterable(edge)]
    gsis = (
        players.loc[players["Id"].isin(pool["Id"]), "GsisId"].dropna() if "GsisId" in players.columns else []
    )
    return usage_trends.compute_trends(weekly, population=set(gsis) or None)


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
        trends=load_trends(edge, players),
        history=history,
    )


# ---------------------------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------------------------


def _clean(value):
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return ""
    if hasattr(value, "item"):
        value = value.item()
    if isinstance(value, str) and value.startswith(("=", "+", "-", "@")):
        return "'" + value
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


class _Builder:
    def __init__(self) -> None:
        self.layout = Layout(rows=[])
        self._section: tuple[str, int] | None = None
        self._open: tuple[str, int] | None = None  # (key, header row) of an open depth-2 group

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
        names = [*(lead or LEAD), *slots, *[""] * (SLOTS - len(slots)), *TRAILING_HEADERS]
        row = self.add(names)
        self.layout.header_rows.append(row)
        return row

    # ---- groups ------------------------------------------------------------------------------
    def open_group(self, header_text: str, key: str, *, team: bool = False) -> int:
        """A header row (the toggle sits on it) whose following rows form a collapsed level-2 group."""
        row = self.add([header_text])
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
    def player_row(self, lead: list, slots: list, *, why: str, verb: str, pid, muted: bool = False) -> int:
        slots = [*slots, *[""] * (SLOTS - len(slots))]
        row = self.add([*lead, *slots, why, verb, "", "", "", pid])
        self.layout.player_rows.append(row)
        if muted:
            self.layout.muted_rows.append(row)
        return row


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


def _status_lines(inputs: Inputs) -> list[str]:
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


def cash_verb(rank: int, bust: float, bust_median: float) -> str:
    """`Cash add`: top `CASH_ADD_TOP_N` Hit3x% at the position AND Bust% below the position median."""
    return "Cash add" if rank <= CASH_ADD_TOP_N and bust < bust_median else "Cash option"


def gpp_verb(boom: float, boom_cut: float, starred: bool) -> str:
    """`GPP leverage`: a top-quartile Boom% that is also a star (low owned, once ownership is out);
    `GPP add`: top-quartile Boom%; `GPP option` otherwise."""
    if boom >= boom_cut:
        return f"GPP leverage {STAR}" if starred else "GPP add"
    return "GPP option"


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


# ---------------------------------------------------------------------------------------------
# The layout
# ---------------------------------------------------------------------------------------------


def _lead(r, pos: str | None = None) -> list:
    return [r["Name"], pos or r["Position"], r["Team"], r["Salary"]]


def _muted(r) -> bool:
    return _is_thin(r.get("Games"))


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
    b = _Builder()
    b.add(["EDGE FINDER  —  calibrated projections, outcome odds and the signals that matter"])
    if inputs is None:
        b.note("Not synced yet -- run `dfs sync`; this tab is written by the sync and holds nothing typed.")
        for title in (
            "CASH CORE  —  best Hit3x% per position",
            "GPP UPSIDE  —  best Boom% per position",
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

    edge = with_games(inputs.edge, inputs.players)
    for col in ("SleeperPts", "FantasyProsPts"):
        if col in inputs.players.columns and col not in edge.columns:
            edge = edge.merge(inputs.players[["Id", col]].drop_duplicates("Id"), on="Id", how="left")
    for line in _status_lines(inputs):
        b.layout.status_rows.append(b.add([line]))
    published = (edge.get("OwnStatus", pd.Series(dtype=object)) == "real").any()

    _cash_section(b, edge, inputs)
    _gpp_section(b, edge, published)
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
        why = _join(
            f"{r['Hit3x%']:.0f}% to reach 3x salary, {r['Bust%']:.0f}% to bust (under 2x)",
            f"CalPts {r['CalPts']:.1f} vs TFFB {r['ProjPts']:.1f}",
            _thin_note(r.get("Games")),
        )
        b.player_row(
            _lead(r, pos),
            [r["CalPts"], r["Hit3x%"], r["Bust%"], r["ProjPts"], f"#{rank} of {total}", r["Edge"]],
            why=why,
            verb=cash_verb(rank, r["Bust%"], medians.get(pos, np.inf)),
            pid=r["Id"],
            muted=_muted(r),
        )

    _ranked_blocks(b, parts, row_for, colour={"Hit3x%": False, "Bust%": True}, before_rows=verdict)
    b.end_section()


def _gpp_section(b: _Builder, edge: pd.DataFrame, published: bool) -> None:
    b.begin_section("GPP UPSIDE  —  best Boom% per position", link="GPP")
    b.header(GPP_COLUMNS)
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
        why = _join(
            f"{r['Boom%']:.0f}% to reach 4x salary; the model's 85th percentile is {r['CeilM']:.1f} pts",
            f"owned {own:.0f}%" if own != "" else ("" if published else "ownership not out yet"),
            "low-owned for his upside" if starred else "",
            _thin_note(r.get("Games")),
        )
        b.player_row(
            _lead(r, pos),
            [r["CalPts"], r["Boom%"], r["CeilM"], own, f"#{rank} of {total}", r["Edge"]],
            why=why,
            verb=gpp_verb(r["Boom%"], boom_cut, starred),
            pid=r["Id"],
            muted=_muted(r),
        )

    _ranked_blocks(b, parts, row_for, colour={"Boom%": False})
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
            b.player_row(
                _lead(r, pos),
                [r["CalPts"], r["ValAdj"], r["Hit3x%"], f"#{rank} of {len(part)}", r["Edge"]],
                why=_join(
                    f"{money(gap)} above the cheapest {pos}" if gap else f"the cheapest {pos} on the slate",
                    f"ValAdj {r['ValAdj']:.1f} (value against what his price usually buys)",
                    _thin_note(r.get("Games")),
                ),
                verb="Punt option",
                pid=r["Id"],
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
                    verb=verb,
                    pid=r["Id"],
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
                b.player_row(
                    [r["Name"], r["Position"], r["Team"], info["Salary"] if info is not None else ""],
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
                        f"priced in: {priced}" if isinstance(priced, str) and priced else "",
                        _thin_note(games),
                    ),
                    verb=verb,
                    pid=info["Id"] if info is not None else "",
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
                [r["Name"], r["Position"], r["Team"], info["Salary"] if info is not None else ""],
                [r["Role"], round(r["tgt_g"], 1), round(r["car_g"], 1), int(r["GamesMissed"])],
                why=_join(f"{r.get('Regular', '')}", *texts),
                verb="Out",
                pid=info["Id"] if info is not None else "",
                muted=True,
            )
    b.end_section()


def _signed(metric: str, change: float) -> str:
    unit = usage_trends.METRIC_BY_NAME[metric].unit
    if unit == "pct":
        return f"{change * 100:+.0f} pts"
    return f"{change:+.2f}" if unit == "wopr" else f"{change:+.1f}"


def _trend_section(b: _Builder, inputs: Inputs, edge: pd.DataFrame) -> None:
    b.begin_section("USAGE TRENDS  —  whose last-3-games usage moved beyond normal noise")
    b.header(TREND_HEADERS)
    trends = inputs.trends
    marked = trends[trends["Direction"] != ""] if trends is not None and not trends.empty else pd.DataFrame()
    if marked.empty:
        b.note(
            "No player's usage has moved beyond its normal week-to-week noise yet "
            "(needs 3 recent games and at least 1 earlier game this season)."
        )
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
    for pos in usage_trends.TREND_POSITIONS:
        at = marked[marked["Position"] == pos].sort_values("_z", ascending=False)
        if at.empty:
            continue
        # one row per player: his biggest move, with his other moves named in the Why
        players = list(dict.fromkeys(at["_id"]))
        shown = players[:TREND_ROWS_PER_POSITION]
        more = len(players) - len(shown)
        b.sub(
            f"{pos}  —  {len(shown)} of {len(players)} players moved beyond noise"
            + (f" (+{more} more)" if more else "")
        )
        for pid in shown:
            moves = at[at["_id"] == pid]
            r = moves.iloc[0]
            e = by_id.loc[pid]
            metric = r["Metric"]
            others = [f"{m['Metric']} {m['Direction']}" for _, m in moves.iloc[1:].iterrows()]
            b.player_row(
                [e["Name"], pos, e["Team"], e["Salary"]],
                [
                    metric,
                    usage_trends.format_value(metric, r["Recent"]),
                    usage_trends.format_value(metric, r["Prior"]),
                    _signed(metric, r["Change"]),
                    r["Direction"],
                ],
                why=_join(usage_trends.why(r), f"also moved: {', '.join(others)}" if others else ""),
                verb="Watch",
                pid=pid,
            )
    b.end_section()


def _matchup_section(b: _Builder, inputs: Inputs, edge: pd.DataFrame) -> None:
    b.begin_section("MATCHUPS (CONTEXT)  —  the 8 best and 4 toughest offenses (DST: the defense's spot)")
    b.header(MATCHUP_COLUMNS, lead=MATCHUP_LEAD)
    mu = inputs.matchups
    if mu.empty:
        b.note("No matchup tables yet (the schedule or last season's results were unavailable).")
        b.end_section()
        return
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
            cells[1], cells[3] = pos, "Soft" if soft else "Tough"
            for i, (_, p) in enumerate(players.iterrows()):
                cells[len(LEAD) + i] = f"{p['Name']} {_k(p['Salary'])}"
            cells[FIRST_TRAILING] = _join(
                str(r["Reasons"]),
                f"score {r['Score']:+.2f} (standard deviations above the league average; higher is softer)",
            )
            cells[FIRST_TRAILING + 1] = "Context only"
            if not soft:
                b.layout.muted_rows.append(row)
            for _, p in players.iterrows():
                b.player_row(
                    _lead(p, pos),
                    [p["CalPts"], p["Hit3x%"], p["Boom%"]],
                    why=f"CalPts {p['CalPts']:.1f} vs TFFB {p['ProjPts']:.1f}",
                    verb="Context only",
                    pid=p["Id"],
                )
            b.close_group()
    b.end_section()


def _signal_section(b: _Builder, edge: pd.DataFrame) -> None:
    b.begin_section("CONTEXT SIGNALS  —  unproven; tracked in Model Check")
    b.header(SIGNAL_COLUMNS)
    pool = rosterable(edge)
    for token in ["FADE↓", "USAGE↑", "USAGE↓"]:
        has = edge["Edge"].fillna("").astype(str).str.split().map(lambda t, k=token: k in t)
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
                verb="Context only",
                pid=r["Id"],
                muted=True,
            )
    b.end_section()


POINT_HEADERS = frozenset(
    {
        "CalPts",
        "Top 3 / CalPts",
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
PERCENT_HEADERS = frozenset({"Hit3x%", "Boom%", "Bust%", "Own%"})
CHIP_HEADERS = frozenset({"Edge", "Token", "Trend"})


def _mark_formats(layout: Layout) -> None:
    """Number-format and chip ranges, derived from the header rows (every section's columns by NAME). A
    header's block runs until the next header, section or blank row."""
    section_rows = set(layout.section_rows)
    headers = set(layout.header_rows)
    for header_row in layout.header_rows:
        names = layout.rows[header_row - 1]
        last = header_row
        while (
            last < len(layout.rows)
            and any(c != "" for c in layout.rows[last])
            and (last + 1) not in section_rows
            and (last + 1) not in headers
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
