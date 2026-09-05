"""Pure functions computing the "edge" signals from already-synced sources.

Design principle from docs/ROADMAP.md Phase 1: a column that shows two
numbers is worse than one that says *look at this*. Every score computed
here ends up sortable (Leverage, CeilVal, GameEnv) or turned into a short
flag token (Avail, Flag) -- not just a raw number restated.

Everything here is offline-testable (no network, no Sheets): `edge.py`'s
EdgeSource is the thin, untested wrapper that reads already-synced CSVs from
`store.load_current()` and calls into this module, per CONTRIBUTING.md's
"split the pure logic out" rule.

Join: TFFB's projections `Id` is DraftKings' own player ID (verified 742/742
exact overlap against a live pull) -- no name matching needed for the
projections<->salaries join. A TFFB row without a DK salary match is
*reported* (via EdgeBuildResult.unmatched_names), never silently dropped --
this can legitimately happen since TFFB's optimizer isn't scoped to "DK's
main Sunday slate only" the way dk_salaries.py is, so a Thursday/Monday-only
player can show up projected with no match in that week's main-slate pull.

GameEnv is computed only from the Vegas context TFFB already attaches to
each player (OU/Spread) -- deliberately *not* cross-referenced against the
separately-synced nfl_odds source in this first pass, since nfl_odds rows
are keyed by team nickname/abbreviation (`rotowire_odds.py`'s `team`/`abbr`
columns) rather than DK's team codes, and building that name-matching layer
just to double-check numbers TFFB already provides isn't worth the join risk
yet.

Stadium/Roof/Wind, added in Phase 2, *do* use the clean team-code join
`nflverse_games` provides: each player's `Team` is looked up against
`GamesRaw`'s Away/Home columns (already normalized to DK's team codes by
`nflverse_games.py`), and `WeatherRaw` is then joined on GameId. Both
`games`/`weather` are optional -- a week without them synced still produces
the exact same EdgeRaw column set, just with those columns blank, because
a tab whose column *count* changes week to week is the precondition for
the Phase 8 formula-shift bug (see CONTRIBUTING.md) if it's ever pasted
into a sheet with hardcoded column references.

LineMove, added in Phase 3, joins `line_movement.diff_odds()`'s output by
team code (`Abbr`, already DK-compatible -- `rotowire_odds.py`'s own
`abbr` field) directly onto `Team`, no intermediate game lookup needed.
Also optional, same blank-not-omitted rule as everything else in this
paragraph.

WeekLineMove, added in Phase 5, is the same `diff_odds()` join reused with
a different baseline: LineMove diffs against the last sync (`store.
load_previous`), WeekLineMove against the earliest snapshot of the current
NFL week (`store.load_since` + `nfl_calendar.week_start_date`) -- one shows
the latest tick, the other the week's overall drift. `GameStart`, also
added in Phase 5, needs no attach step at all: TFFB's projections.csv
already carries it (an ISO-8601 UTC kickoff time) straight through the
join, it just wasn't kept in EDGE_COLUMNS' final column selection before
now. Both back `dfs lineups late-swap`'s lock-time check.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from dfs.line_movement import LINE_MOVE_FLAG_THRESHOLD

# Leverage = CeilPct - ProjOwn once real ownership exists; while every
# player's ProjOwn reads 0 (TFFB hasn't computed ownership yet this week),
# that formula degenerates to CeilPct alone, which is already the proxy
# we'd want -- but LevBasis names which case is in effect so a proxy number
# is never mistaken for the real metric.
LEV_BASIS_REAL = "real"
LEV_BASIS_PROXY = "proxy"

# Heuristic thresholds for the Flag column. Not derived from anything
# empirical -- starting points to tune once a week's worth of real ProjOwn
# data exists. Leverage needs two different thresholds because it means two
# different things depending on LevBasis: under "real" it's a gap (CeilPct
# minus actual ownership, roughly -100..100, centered near 0), so 15 is a
# meaningful edge. Under "proxy" it degenerates to CeilPct alone (0..100,
# centered ~50) -- reusing the "real" threshold there would flag roughly
# every above-average player as LEVERAGE, which is exactly the "reports
# everything" failure this column exists to avoid. So proxy mode only flags
# the actual top of the ceiling distribution.
LEVERAGE_FLAG_THRESHOLD_REAL = 15.0
LEVERAGE_FLAG_THRESHOLD_PROXY = 85.0
CHALK_OWNERSHIP_THRESHOLD = 20.0
OUT_STATUSES = frozenset({"OUT", "IR"})
# Mirrors sources/weather.py's WIND_FLAG_THRESHOLD_MPH. Duplicated rather
# than imported so derived.py (pure, source-agnostic logic) never depends
# on a specific source module -- sources depend on derived.py, not the
# other way around.
WIND_FLAG_THRESHOLD_MPH = 20.0

# The EdgeRaw tab's column order -- exposed so `dfs sheets format-edge` can
# locate a column by name without an extra round-trip read of the sheet.
EDGE_COLUMNS = [
    "Id",
    "Name",
    "Position",
    "Team",
    "Opp",
    "Salary",
    "ProjPts",
    "ProjOwn",
    "Ceiling",
    "Val",
    "CeilVal",
    "CeilPct",
    "Leverage",
    "LevBasis",
    "GameEnv",
    "Stadium",
    "Roof",
    "Wind",
    "Avail",
    "Flag",
    # LineMove is appended at the very end, not inserted among the
    # existing columns above -- `dfs sheets link-edge` already wrote
    # formulas into PlayerPoolRaw/Player Pool/Lineups with hardcoded
    # column-index integers pointing at Stadium/Roof/Wind/Avail/Flag's
    # *positions*. Inserting a column before them shifts every later
    # column's position without updating those already-written formulas'
    # hardcoded integers -- the exact Phase 8 bug class (see
    # CONTRIBUTING.md). Anything new added here must go at the end until
    # `dfs sheets link-edge` is re-run against a cleared block.
    "LineMove",
    # WeekLineMove/GameStart, added in Phase 5, follow the same append-only
    # rule as LineMove above.
    "WeekLineMove",
    "GameStart",
]


@dataclass
class EdgeBuildResult:
    frame: pd.DataFrame
    unmatched_names: list[str]


def _percentile_within(series: pd.Series, group: pd.Series) -> pd.Series:
    """Percentile rank (0-100) of `series` within each `group` value. NaN
    inputs stay NaN in the output -- pandas' rank() already skips them,
    which is exactly what we want for Ceiling's ~40% missing rows."""
    return series.groupby(group).rank(pct=True) * 100


def _game_env_scores(game: pd.Series, ou: pd.Series, spread: pd.Series) -> pd.Series:
    """0-100 per game: half from total (higher = more scoring expected),
    half from spread tightness (smaller |spread| = more competitive, more
    reason for both teams to keep throwing). Computed once per unique game
    and broadcast back to every player in it."""
    games = pd.DataFrame({"Game": game, "OU": pd.to_numeric(ou, errors="coerce")})
    games["AbsSpread"] = pd.to_numeric(spread, errors="coerce").abs()
    games = games.drop_duplicates(subset="Game").set_index("Game")

    ou_pct = games["OU"].rank(pct=True) * 100
    tightness_pct = (1 - games["AbsSpread"].rank(pct=True)) * 100
    game_env = ((ou_pct + tightness_pct) / 2).round(1)

    return game.map(game_env)


def _team_game_lookup(games: pd.DataFrame) -> pd.DataFrame:
    """GamesRaw (one row per game) -> one row per team (each team appears
    once, as either Away or Home), indexed by team code."""
    away = games[["Away", "GameId", "Stadium", "Roof"]].rename(columns={"Away": "Team"})
    home = games[["Home", "GameId", "Stadium", "Roof"]].rename(columns={"Home": "Team"})
    return pd.concat([away, home], ignore_index=True).set_index("Team")


def _attach_games(merged: pd.DataFrame, games: pd.DataFrame | None) -> pd.DataFrame:
    if games is None:
        merged["GameId"] = pd.NA
        merged["Stadium"] = pd.NA
        merged["Roof"] = pd.NA
        return merged
    by_team = _team_game_lookup(games)
    joined = merged["Team"].map(by_team["GameId"])
    merged["GameId"] = joined
    merged["Stadium"] = merged["Team"].map(by_team["Stadium"])
    merged["Roof"] = merged["Team"].map(by_team["Roof"])
    return merged


def _attach_weather(merged: pd.DataFrame, weather: pd.DataFrame | None) -> pd.DataFrame:
    if weather is None or weather.empty:
        merged["Wind"] = pd.NA
        return merged
    wind_by_game = weather.set_index("GameId")["Wind"]
    merged["Wind"] = merged["GameId"].map(wind_by_game)
    return merged


def _attach_line_movement(
    merged: pd.DataFrame, line_movement: pd.DataFrame | None, *, column: str
) -> pd.DataFrame:
    """Shared by LineMove (since the last sync) and WeekLineMove (since the
    start of the current NFL week) -- both are `line_movement.diff_odds()`
    output, just diffed against a different baseline snapshot by the
    caller (see sources/edge.py)."""
    if line_movement is None or line_movement.empty:
        merged[column] = pd.NA
        return merged
    delta_by_team = line_movement.set_index("Abbr")["TeamPointsDelta"]
    merged[column] = merged["Team"].map(delta_by_team)
    return merged


def _dst_nickname(full_team_name: str) -> str:
    """Mirrors sources/tffb_projections.py's `_dst_nickname` -- duplicated
    rather than imported for the same reason as WIND_FLAG_THRESHOLD_MPH
    above (derived.py stays source-module-agnostic). "Los Angeles
    Chargers" -> "Chargers": every NFL nickname is one word, so the last
    token is always right."""
    return full_team_name.strip().rsplit(" ", 1)[-1]


def _flag_for_row(row: pd.Series) -> str:
    if row["Avail"] in OUT_STATUSES:
        return "OUT"
    if pd.notna(row["Wind"]) and row["Wind"] >= WIND_FLAG_THRESHOLD_MPH:
        return "WIND"
    if pd.notna(row["LineMove"]) and row["LineMove"] >= LINE_MOVE_FLAG_THRESHOLD:
        return "LINE↑"
    if pd.notna(row["LineMove"]) and row["LineMove"] <= -LINE_MOVE_FLAG_THRESHOLD:
        return "LINE↓"
    is_real = row["LevBasis"] == LEV_BASIS_REAL
    threshold = LEVERAGE_FLAG_THRESHOLD_REAL if is_real else LEVERAGE_FLAG_THRESHOLD_PROXY
    if pd.notna(row["Leverage"]) and row["Leverage"] >= threshold:
        return "LEVERAGE"
    if is_real and row["ProjOwn"] >= CHALK_OWNERSHIP_THRESHOLD:
        return "CHALK"
    return ""


def build_edge_frame(
    projections: pd.DataFrame,
    salaries: pd.DataFrame,
    games: pd.DataFrame | None = None,
    weather: pd.DataFrame | None = None,
    line_movement: pd.DataFrame | None = None,
    week_line_movement: pd.DataFrame | None = None,
) -> EdgeBuildResult:
    """Join TFFB projections to DK salaries on player ID and compute every
    derived column for the EdgeRaw tab. Rows are returned pre-sorted by
    Leverage descending, so the top of the tab is the answer.

    `salaries` is the raw draftkings.csv shape (columns include `ID`,
    `Salary`, `Status`); `projections` is the raw projections.csv shape
    (columns include `Id`, `ProjPts`, `ProjOwn`, `Ceiling`, `OU`, `Spread`,
    `Game`, `GameStart`). `games`/`weather`/`line_movement`/
    `week_line_movement` are the GamesRaw/WeatherRaw shapes from
    `nflverse_games.py`/`weather.py`, and two separate `line_movement.
    diff_odds()` calls -- one against the last sync, one against the start
    of the current NFL week (see sources/edge.py) -- all four optional; see
    module docstring for why a missing one blanks columns rather than
    omitting them.
    """
    proj = projections.copy()
    sal = salaries[["ID", "Salary", "Status"]].rename(
        columns={"ID": "Id", "Salary": "DkSalary", "Status": "Status"}
    )

    merged = proj.merge(sal, on="Id", how="left", indicator=True)
    unmatched_names = merged.loc[merged["_merge"] == "left_only", "Name"].tolist()
    merged = merged.drop(columns="_merge")

    # DK's own salary feed is the authoritative number (it's what's actually
    # charged); fall back to TFFB's own Salary field for the rare unmatched
    # row rather than dropping it.
    merged["Salary"] = merged["DkSalary"].fillna(merged["Salary"])
    merged = merged.drop(columns="DkSalary")

    # TFFB's own Name for a DST is the full team name ("Jacksonville
    # Jaguars"); DraftKings' -- and therefore DkSalClean/PlayerPoolRaw/
    # whatever a human types into Player Pool/Lineups -- is just the
    # nickname ("Jaguars"). Rewriting here (not only at TFFBOptoRaw's own
    # to_sheet_rows, which only reformats *that* tab) is what makes every
    # Name-keyed join against EdgeRaw actually match for DST rows.
    is_dst = merged["Position"] == "DST"
    merged.loc[is_dst, "Name"] = merged.loc[is_dst, "Name"].apply(_dst_nickname)

    merged["Val"] = (merged["ProjPts"] / (merged["Salary"] / 1000)).round(2)
    merged["CeilVal"] = (merged["Ceiling"] / (merged["Salary"] / 1000)).round(2)
    merged["CeilPct"] = _percentile_within(merged["Ceiling"], merged["Position"]).round(1)

    has_real_ownership = merged["ProjOwn"].fillna(0).gt(0).any()
    merged["LevBasis"] = LEV_BASIS_REAL if has_real_ownership else LEV_BASIS_PROXY
    proj_own_for_leverage = merged["ProjOwn"] if has_real_ownership else 0
    merged["Leverage"] = (merged["CeilPct"] - proj_own_for_leverage).round(1)

    merged["GameEnv"] = _game_env_scores(merged["Game"], merged["OU"], merged["Spread"])

    merged = _attach_games(merged, games)
    merged = _attach_weather(merged, weather)
    merged = merged.drop(columns="GameId")
    merged = _attach_line_movement(merged, line_movement, column="LineMove")
    merged = _attach_line_movement(merged, week_line_movement, column="WeekLineMove")

    merged["Avail"] = merged["Status"].fillna("")
    merged = merged.drop(columns="Status")

    merged["Flag"] = merged.apply(_flag_for_row, axis=1)

    merged = merged.sort_values("Leverage", ascending=False, na_position="last").reset_index(drop=True)

    return EdgeBuildResult(frame=merged[EDGE_COLUMNS], unmatched_names=unmatched_names)
