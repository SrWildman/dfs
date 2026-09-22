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

ImpliedMove/TotMove/SpdMove (ImpliedMove added in Phase 3 as "LineMove", renamed
and joined by TotMove/SpdMove in Fix 2.2) join `line_movement.diff_odds()`'s
output by team code (`Abbr`, already DK-compatible -- `rotowire_odds.py`'s
own `abbr` field) directly onto `Team`, no intermediate game lookup
needed. `diff_odds()` always computed all three deltas (team implied
points, game total, spread); only the first ever reached EdgeRaw before
this fix, under a name that didn't say which line had moved. Also
optional, same blank-not-omitted rule as everything else in this
paragraph.

This diffs against the *last sync* originally (`store.load_previous`) --
reverted in Phase 5 after real use showed that's the wrong baseline: it
depends entirely on how often you happen to run `dfs sync`, so the exact
same real move could show as a big number or nothing depending on sync
cadence, which isn't a signal, it's noise. It now diffs against the
*start of the current NFL week* instead (`store.load_since` +
`nfl_calendar.week_start_date`), a fixed baseline that means the same
thing regardless of sync frequency. `GameStart`, added alongside it in
Phase 5, needs no attach step at all: TFFB's projections.csv already
carries it (an ISO-8601 UTC kickoff time) straight through the join, it
just wasn't kept in EDGE_COLUMNS' final column selection before now.
Backs `dfs lineups late-swap`'s lock-time check.

See docs/CALCULATIONS.md for the full worked explanation of every column
here, including Val/CeilVal/CeilPct/Leverage/GameEnv/Flag -- this
docstring covers the *why* of the design, that doc covers the exact
formula for anyone who just wants to verify a number.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from dfs.line_movement import LINE_MOVE_FLAG_THRESHOLD

# Leverage = CeilPct - OwnPct, both percentile ranks on the same 0-100
# scale within position -- see the module docstring's "second scale/index
# bug" postmortem for why this replaced a raw-percentage subtraction.
# `OwnPct` itself is dropped from EDGE_COLUMNS (Part 7.9: its only
# consumer anywhere in this codebase was this one subtraction -- verified
# by grep before removing) but stays a real pandas column here, computed
# and used exactly as before; only the sheet-facing output changed.
# OwnStatus's one remaining job: telling you whether ProjOwn has actually
# been published yet. Until it has (every player reads 0), there is no
# real ownership signal to rank against, so Leverage is left BLANK rather
# than showing a number that looks like leverage but isn't -- a confident
# wrong number is worse than an empty cell. The frame still sorts usefully
# in that window, just by CeilPct instead (see build_edge_frame).
# Renamed from LevBasis (Part 7.9): with Leverage demoted off the spine
# (Part 7.1), this marker's real job is gating `Own%`, a spine column --
# the old name no longer said what it does.
OWN_STATUS_REAL = "real"
OWN_STATUS_UNPUBLISHED = "unpublished"

# Re-tuned against a real Week 1 slate (744 players, real ProjOwn already
# published) once both sides of Leverage were rank-normalized onto the same
# 0-100 scale: that data's Leverage distribution was mean -0.01, std 16.7,
# min -56.2, max 68.7 -- genuinely centered on 0, unlike the old raw-minus-
# percentile formula this replaced (see docs/CALCULATIONS.md's postmortem).
# 30.0 sits at that slate's ~93rd percentile, flagging 53/744 players
# (7.1%) -- comfortably inside the 5-10% target band. 15.0 (the old flat
# threshold, kept as a first guess before this data existed) would have
# flagged 149/744 (20.0%), which is exactly the "reports everything"
# failure this column exists to avoid.
LEVERAGE_FLAG_THRESHOLD = 30.0
# Confirmed against the same slate: 5/744 players (0.7%) clear this today.
# An absolute ownership percentage, not a percentile -- correctly untouched
# by the Leverage scale fix. On the fraction scale (Phase 6, Part 2 --
# ProjOwn is now 0-1, not 0-100, to share one stored scale with
# PlayerPoolRaw's native Own%/Rstr%): 20% is 0.20, not 20.0.
CHALK_OWNERSHIP_THRESHOLD = 0.20
# Phase 6, Part 1.4: `has_real_ownership` used to be `.any()` -- a single
# non-zero ProjOwn (one early-published player, a data glitch, a bye-week
# artifact) flipped the WHOLE slate to "real," computing OwnPct/Leverage as
# a percentile over a column that's still ~99% zeros for everyone else.
# Live symptom, reproduced before this fix: LevBasis read "real" while
# every ProjOwn on EdgeRaw still read 0.0% and every Leverage cell was
# blank. A share threshold instead requires ownership to be genuinely
# published for a majority of the slate before trusting it.
OWNERSHIP_PUBLISHED_SHARE_THRESHOLD = 0.5
OUT_STATUSES = frozenset({"OUT", "IR"})
# Mirrors sources/weather.py's WIND_FLAG_THRESHOLD_MPH. Duplicated rather
# than imported so derived.py (pure, source-agnostic logic) never depends
# on a specific source module -- sources depend on derived.py, not the
# other way around.
WIND_FLAG_THRESHOLD_MPH = 20.0

# Phase 6, Part 3 (2026-09-22): the Board's "Slate shape" section flags a
# game as a probable shootout by its Vegas total. 48 is a standard DFS
# heuristic, not measured against real data the way `LEVERAGE_FLAG_
# THRESHOLD` was re-tuned against a real slate -- a first-pass number
# Sam should sanity-check once he's looked at a few real weeks, same as
# that one was.
SHOOTOUT_TOTAL_THRESHOLD = 48.0

# Week 3 feedback (A3): weight given to raw projected-points percentile
# vs. the price-edge residual percentile in ValAdj's blend -- see
# `_val_adj_blend`'s docstring for why a plain residual isn't enough on
# its own. A named constant, not a literal, since Sam may want to shift
# it toward projection later without a code review to find the number.
VAL_ADJ_PROJECTION_WEIGHT = 0.5

# EdgeRaw's real sheet layout is [Pool, *EDGE_COLUMNS] -- Pool sits in
# column A (so it's beside Name once Id, immediately after it, is hidden),
# not appended after EDGE_COLUMNS the way it first shipped. Every module
# that turns an EDGE_COLUMNS index into an absolute EdgeRaw column letter
# (sheet_links.py, sheet_style.py, sheet_pool_formulas.py, doctor.py) adds
# this offset -- `sources/edge.py`'s own POOL_COLUMN is the one column
# that ISN'T offset, since it's the thing the offset makes room for.
# Purely relative math (e.g. sheet_links._vlookup_index, computed as a
# distance from Name) is unaffected by a uniform shift and doesn't need it.
EDGE_DATA_OFFSET = 1

# The EdgeRaw tab's column order (as data columns; the real sheet position
# of each is `EDGE_COLUMNS.index(name) + EDGE_DATA_OFFSET`) -- exposed so
# `sheet_style.polish_edge` can locate a column by name without an extra
# round-trip read of the sheet.
#
# Phase 6, Part 2 (2026-09-17): redesigned into a shared SPINE (Name/
# Position/Team/Opp/Salary/ProjPts/Val/Ceiling/CeilVal/Own%/Avail/Flag --
# `sheet_columns.py`'s DECISION for the other three tabs, same grammar,
# each tab's own established spelling) visible identically across
# EdgeRaw/Player Pool/Lineups, with everything else behind collapsed
# groups in Sam's own fixed left-to-right order: GAME, CEILING DETAIL,
# MOVEMENT, WEATHER. `Id` stays hidden outright, not part of any visible
# group. `Own%` is the one deliberately shared name -- this column was
# `ProjOwn` here and `Rstr%` on the other three tabs, "the same value
# under two names" a shared spine can't have; renamed to `Own%`
# EVERYWHERE. Internally this module still computes and reasons about a
# `ProjOwn` pandas column throughout (TFFB's own field name, the real
# external data source) -- the rename to `Own%` happens ONLY at the very
# end, selecting into this list (see `build_edge_frame`'s return), so
# every internal formula/threshold/test below is unaffected. `Leverage`
# moves into the collapsed CEILING DETAIL group (Part 7.1's decision,
# folded into this same reorder -- see `sheet_columns.py`'s own docstring
# for why). Nothing deleted; every name below already existed, just
# renamed/repositioned. See CONTRIBUTING.md's structural changelog for
# the full before/after.
# Zone labels (2026-09-17, post-Part-7.9 usability fix): a real, always-
# visible column immediately before each collapsed zone, naming what's
# inside -- Sam: "make sure I know what group is what somehow and I'm not
# just clicking random stuff." A label CANNOT live inside the zone it
# names (collapsing a group hides every cell in its range, the label
# included), and it can't be a blank spacer either (an unlabeled `+`
# control is exactly the "guess and click" problem being fixed) -- so
# each zone gets its own narrow, native, no-formula label column, text
# only in the header row, blank below. This also happens to be what makes
# independent per-zone collapse possible at all: verified live (a raw
# `addDimensionGroup` test on the template's Scratch tab, depth 1 wrapping
# a range and depth 2 per-zone inside it) that Sheets merges adjacent
# same-depth groups into one regardless of nesting -- there is no way to
# get 4 independently-collapsible zones without a REAL gap between them.
GAME_LABEL = "GAME"
CEILING_DETAIL_LABEL = "CEIL"
MOVEMENT_LABEL = "MOVE"
WEATHER_LABEL = "WX"

EDGE_COLUMNS = [
    # SPINE
    "Name",
    "Position",
    "Team",
    "Opp",
    "Salary",
    "ProjPts",
    "Val",
    # Part 7.2: `Val` is salary- and position-biased (cheap players and
    # QBs both artificially outrank better plays), so it stays but is no
    # longer the tool's primary sort -- `ValAdj` is. See
    # `_val_adj_blend` below for the current (Week 3, A3) formula.
    "ValAdj",
    "Ceiling",
    "CeilVal",
    "Own%",
    "Avail",
    # Part 7.9: "Flag" (below, hidden) turned out to ALREADY be every
    # matching condition, space-separated (Fix 2.1, an earlier session) --
    # not the first-match-only value 7.9's own text assumed. Verified with
    # Sam directly rather than guessed: split for real, not just renamed.
    # "Flags" is the one on the spine now -- everything that fired, for
    # reading.
    "Flags",
    # GAME (collapsed) -- GAME_LABEL sits immediately before it, outside
    # the collapsed range, always visible.
    GAME_LABEL,
    "OverUnder",
    "Spread",
    "GameEnv",
    # Sam: "all data should be in edge raw" -- computed the same way
    # PlayerPoolRaw's own (now-fixed) `OppPosRank` is, but natively in
    # Python from the already-synced sos_qb/rb/wr/te/dst CSVs rather than
    # a live Sheets formula, matching EdgeRaw's own "computed locally, no
    # live formulas" design. See `_attach_opp_pos_rank` below.
    "OppPosRank",
    # Part 7.4 (2026-09-18): makes stacks visible. `GameID` was already
    # computed internally (`_attach_games`, as `GameId`) to join Stadium/
    # Roof/Wind, then dropped before this -- now kept and renamed to match
    # this column's own header text. `TmRank` is new: this player's
    # salary rank within his own team AND position (1 = highest-salaried
    # at that position on that team -- "WR1", "RB1", read alongside
    # Position) -- a crude but serviceable proxy for target hierarchy,
    # not a measured one. See `_tm_rank_within_team_position` below and
    # docs/CALCULATIONS.md for why it's labelled a proxy.
    "GameID",
    "TmRank",
    # CEILING DETAIL (collapsed) -- CeilPct/OwnStatus were already
    # collapsed together (the old INTERNAL group); Leverage joins them
    # here now that it's off the spine (Part 7.1). `OwnPct` dropped
    # entirely (Part 7.9) -- see the constant section above.
    CEILING_DETAIL_LABEL,
    "CeilPct",
    "Leverage",
    "OwnStatus",
    # MOVEMENT (collapsed)
    MOVEMENT_LABEL,
    "ImpliedMove",
    "TotMove",
    "SpdMove",
    "GameStart",
    # WEATHER (collapsed)
    WEATHER_LABEL,
    "Stadium",
    "Roof",
    "Wind",
    # Id/Flag stay hidden outright, not part of any visible group -- Pool
    # (column A, ahead of this whole list) sits directly beside Name with
    # no column between them. "Flag" (Part 7.9) is the single
    # highest-priority token only -- kept, not deleted, since other
    # formatting/filtering logic keys off it as a boolean/categorical
    # value; "Flags" (on the spine, above) is what a person reads.
    "Id",
    "Flag",
]

# The four zone labels, in the same left-to-right order they appear --
# used wherever code needs "all the label columns" as a group (e.g. to
# exclude them from formatting that only makes sense for real metrics).
ZONE_LABELS = (GAME_LABEL, CEILING_DETAIL_LABEL, MOVEMENT_LABEL, WEATHER_LABEL)


@dataclass
class EdgeBuildResult:
    frame: pd.DataFrame
    unmatched_names: list[str]


def _percentile_within(series: pd.Series, group: pd.Series) -> pd.Series:
    """Percentile rank (0-100) of `series` within each `group` value. NaN
    inputs stay NaN in the output -- pandas' rank() already skips them,
    which is exactly what we want for Ceiling's ~40% missing rows."""
    return series.groupby(group).rank(pct=True) * 100


def _tm_rank_within_team_position(
    salary: pd.Series, name: pd.Series, team: pd.Series, position: pd.Series
) -> pd.Series:
    """Part 7.4: this player's salary rank within his own TEAM and
    POSITION -- 1 is the highest-salaried player at that position on that
    team ("WR1", "RB1", read alongside `Position`). A crude but
    serviceable proxy for target hierarchy -- salary reflects the
    market's OWN belief about usage, not a measured target share -- see
    docs/CALCULATIONS.md for why this is never to be read as the latter.

    Ranked by Salary descending, ties broken by Name ascending so the
    result is deterministic regardless of the frame's own row order
    (which itself changes every sync, since EdgeRaw is sorted by
    `ValAdj`) -- two players priced identically at the same team/position
    is rare but real (a full slate of backups at the veteran minimum),
    and an arbitrary tiebreak would make "WR1"/"WR2" flip between syncs
    for no real-world reason."""
    frame = pd.DataFrame({"Salary": salary, "Name": name, "Team": team, "Position": position})
    ordered = frame.sort_values(["Salary", "Name"], ascending=[False, True])
    ordered["TmRank"] = ordered.groupby(["Team", "Position"]).cumcount() + 1
    return ordered["TmRank"].reindex(frame.index)


def _val_adj_residual_within_position(
    proj_pts: pd.Series, salary: pd.Series, position: pd.Series
) -> pd.Series:
    """Part 7.2: `residual = ProjPts - E[ProjPts | Salary, Position]` --
    fit a plain OLS line of `ProjPts` on `Salary` *within each position, on
    this slate's own projections*, and take the residual: "is this player
    projected above what this slate's own pricing implies for his
    position." Version 2 (refit against realized points once the results
    loop exists, additionally surfacing where the market is systematically
    wrong) is a deliberate later step, not built here.

    Week 3 feedback (A3), found live: this residual used to BE `ValAdj`
    directly, and that was the bug Sam flagged -- "cheap players float too
    high." A residual is scale-free, so a $3,200 RB beating his price by
    +1.3 outranked an $8,200 RB missing his by -0.2, even though the cheap
    player can't win a lineup and the expensive one can. This function's
    output is now only an internal input to `_val_adj_blend` (via its own
    within-position percentile, `EdgePct`) -- see that function for the
    fix. Still called "residual," not "ValAdj," to make that clear at
    every call site.

    A position with fewer than two usable rows, or one where every row
    shares the same `Salary` (can't fit a slope from a single price
    point), gets a residual of 0 for that whole group -- there's no
    "expectation" to measure against yet, and 0 reads as "no signal"
    rather than a fabricated number. A row with a missing `ProjPts`
    stays blank (NaN), consistent with this codebase's "blank is not
    zero" rule -- it is never coerced into 0.

    Deliberately NOT `groupby(...).apply(...)`: with exactly one
    position present (a real case -- position-scoped debugging, or a
    hypothetical single-position slate), pandas' own `apply` collapses
    the per-row result into a single aggregate row instead of returning
    it row-aligned, silently corrupting every value. Iterating positions
    explicitly and writing into a pre-sized result Series sidesteps that
    entirely and is easier to reason about besides."""
    proj_pts = pd.to_numeric(proj_pts, errors="coerce")
    salary = pd.to_numeric(salary, errors="coerce")
    result = pd.Series(np.nan, index=proj_pts.index, dtype=float)

    for pos_value in position.unique():
        idx = position.index[position == pos_value]
        pts = proj_pts.loc[idx].to_numpy(dtype=float)
        sal = salary.loc[idx].to_numpy(dtype=float)
        usable = ~(np.isnan(pts) | np.isnan(sal))
        if usable.sum() < 2 or np.ptp(sal[usable]) == 0:
            # No fittable slope for this position -- 0 for every row that
            # actually has a ProjPts to compare; a genuinely missing
            # ProjPts stays NaN rather than being coerced to a fabricated
            # 0 (see docstring).
            residual = np.where(np.isnan(pts), np.nan, 0.0)
        else:
            slope, intercept = np.polyfit(sal[usable], pts[usable], 1)
            predicted = intercept + slope * sal
            residual = pts - predicted
        result.loc[idx] = residual

    return result.round(2)


def _val_adj_blend(pts_pct: pd.Series, edge_pct: pd.Series) -> pd.Series:
    """Week 3 feedback (A3): `ValAdj = VAL_ADJ_PROJECTION_WEIGHT * PtsPct
    + (1 - VAL_ADJ_PROJECTION_WEIGHT) * EdgePct`, both within-position
    percentile ranks (0-100, via `_percentile_within` -- `PtsPct` of raw
    `ProjPts`, `EdgePct` of `_val_adj_residual_within_position`'s output).

    Worked example that motivated this (illustrative percentiles, not a
    real slate): an $8,200 RB projected 19.5 against a 19.7 par (residual
    -0.2) has PtsPct 98, EdgePct 45 -> ValAdj 71.5. A $3,200 RB projected
    9.0 against a 7.7 par (residual +1.3) "beats his price" more --
    PtsPct 30, EdgePct 85 -> ValAdj 57.5. The expensive back now wins,
    which is the point: a residual alone (old ValAdj) ranked the cheap
    back above the expensive one, even though the cheap back can't
    plausibly win a GPP lineup and the expensive one can. Blending in raw
    `ProjPts`' own percentile keeps scale in the picture without losing
    the price-edge signal `EdgePct` alone provides. See
    docs/CALCULATIONS.md for the full worked example."""
    return (VAL_ADJ_PROJECTION_WEIGHT * pts_pct + (1 - VAL_ADJ_PROJECTION_WEIGHT) * edge_pct).round(1)


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


def _attach_opp_pos_rank(
    merged: pd.DataFrame, sos_by_position: dict[str, pd.DataFrame] | None
) -> pd.DataFrame:
    """This player's OPPONENT's strength-of-schedule rank at THIS
    player's own position -- the same value `PlayerPoolRaw`'s own
    `OppPosRank` computes (via a `SoSComb` VLOOKUP, keyed by `Opp.`, not
    `Team` -- see `sheet_pool_raw_sos.py`'s docstring for the bug that
    inverted that one for months), computed here natively from each
    position's own already-synced `sos_<position>` frame instead
    (`Team.1`/`Rank` columns -- see `sources/tffb_sos.py`).

    `sos_by_position` is a dict of ONLY the positions that synced
    successfully this run (`sources/edge.py` builds it the same
    graceful-degradation way `games`/`weather` are already optional) --
    a position missing from it blanks just that position's players,
    never the whole column, since one position's TFFB page failing
    shouldn't hide every other position's real data."""
    if not sos_by_position:
        merged["OppPosRank"] = pd.NA
        return merged
    rank_by_team = {position: df.set_index("Team.1")["Rank"] for position, df in sos_by_position.items()}

    def _rank_for_row(row: pd.Series) -> object:
        lookup = rank_by_team.get(row["Position"])
        if lookup is None:
            return pd.NA
        return lookup.get(row["Opp"], pd.NA)

    merged["OppPosRank"] = merged.apply(_rank_for_row, axis=1)
    return merged


def _attach_line_movement(merged: pd.DataFrame, line_movement: pd.DataFrame | None) -> pd.DataFrame:
    """Fix 2.2: `diff_odds()` already computes all three deltas
    (TeamPointsDelta, TotalDelta, SpreadDelta); only the first ever
    reached EdgeRaw, under a name that didn't say which line moved. All
    three are surfaced now: ImpliedMove (team implied points -- what LineMove
    used to be), TotMove (game total), SpdMove (spread)."""
    if line_movement is None or line_movement.empty:
        merged["ImpliedMove"] = pd.NA
        merged["TotMove"] = pd.NA
        merged["SpdMove"] = pd.NA
        return merged
    by_team = line_movement.set_index("Abbr")
    merged["ImpliedMove"] = merged["Team"].map(by_team["TeamPointsDelta"])
    merged["TotMove"] = merged["Team"].map(by_team["TotalDelta"])
    merged["SpdMove"] = merged["Team"].map(by_team["SpreadDelta"])
    return merged


def _dst_nickname(full_team_name: str) -> str:
    """Mirrors sources/tffb_projections.py's `_dst_nickname` -- duplicated
    rather than imported for the same reason as WIND_FLAG_THRESHOLD_MPH
    above (derived.py stays source-module-agnostic). "Los Angeles
    Chargers" -> "Chargers": every NFL nickname is one word, so the last
    token is always right."""
    return full_team_name.strip().rsplit(" ", 1)[-1]


def _flags_for_row(row: pd.Series) -> list[str]:
    """Every matching flag, in priority order (most urgent first) -- a
    player who is both WIND and LEVERAGE showed only WIND under the old
    first-match-wins rule, silently hiding the second condition. All of
    these can be simultaneously true of the same player, so all of them
    are computed here; `build_edge_frame` derives both sheet columns from
    this one list (`Flags` = every token space-separated, for reading;
    `Flag` = just the first / highest-priority one, Part 7.9)."""
    flags = []
    if row["Avail"] in OUT_STATUSES:
        flags.append("OUT")
    if pd.notna(row["Wind"]) and row["Wind"] >= WIND_FLAG_THRESHOLD_MPH:
        flags.append("WIND")
    # LINE↑/↓ keys off ImpliedMove specifically (Fix 2.2) -- TotMove/SpdMove
    # are shown for context but don't drive this flag.
    if pd.notna(row["ImpliedMove"]) and row["ImpliedMove"] >= LINE_MOVE_FLAG_THRESHOLD:
        flags.append("LINE↑")
    if pd.notna(row["ImpliedMove"]) and row["ImpliedMove"] <= -LINE_MOVE_FLAG_THRESHOLD:
        flags.append("LINE↓")
    if pd.notna(row["Leverage"]) and row["Leverage"] >= LEVERAGE_FLAG_THRESHOLD:
        flags.append("LEVERAGE")
    # No ownership-published guard needed: ProjOwn reads 0 for everyone
    # until TFFB publishes it, so this can't fire before then regardless.
    if row["ProjOwn"] >= CHALK_OWNERSHIP_THRESHOLD:
        flags.append("CHALK")
    return flags


def build_edge_frame(
    projections: pd.DataFrame,
    salaries: pd.DataFrame,
    games: pd.DataFrame | None = None,
    weather: pd.DataFrame | None = None,
    line_movement: pd.DataFrame | None = None,
    sos_by_position: dict[str, pd.DataFrame] | None = None,
) -> EdgeBuildResult:
    """Join TFFB projections to DK salaries on player ID and compute every
    derived column for the EdgeRaw tab. Rows are returned pre-sorted by
    ValAdj descending, so the top of the tab is the answer.

    `salaries` is the raw draftkings.csv shape (columns include `ID`,
    `Salary`, `Status`); `projections` is the raw projections.csv shape
    (columns include `Id`, `ProjPts`, `ProjOwn`, `Ceiling`, `OU`, `Spread`,
    `Game`, `GameStart`). `games`/`weather`/`line_movement` are the
    GamesRaw/WeatherRaw shapes from `nflverse_games.py`/`weather.py`, and
    `line_movement.diff_odds()`'s output diffed against the start of the
    current NFL week (see sources/edge.py) -- all optional; see module
    docstring for why a missing one blanks columns rather than omitting
    them. `sos_by_position` is `{"QB": sos_qb_frame, ...}` -- each
    position's own already-synced `sources/tffb_sos.py` shape -- for
    however many positions synced successfully this run; see
    `_attach_opp_pos_rank`.
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

    # Phase 6, Part 2: TFFB's own ProjOwn is a raw percentage-as-number
    # (14.6 meaning 14.6%) -- converted to a true fraction (0.146) here so
    # it shares one stored scale with PlayerPoolRaw's native Rstr% (which
    # divides by 100 in its own formula, `=(...)/100`), now that both are
    # renamed to the same "Own%" name and need to share one PERCENT-type
    # sheet format. CHALK_OWNERSHIP_THRESHOLD is on this same fraction
    # scale as a result (0.20, not 20.0) -- see its own comment.
    merged["ProjOwn"] = merged["ProjOwn"] / 100

    merged["Val"] = (merged["ProjPts"] / (merged["Salary"] / 1000)).round(2)
    val_adj_residual = _val_adj_residual_within_position(
        merged["ProjPts"], merged["Salary"], merged["Position"]
    )
    val_adj_pts_pct = _percentile_within(merged["ProjPts"], merged["Position"])
    val_adj_edge_pct = _percentile_within(val_adj_residual, merged["Position"])
    merged["ValAdj"] = _val_adj_blend(val_adj_pts_pct, val_adj_edge_pct)
    merged["CeilVal"] = (merged["Ceiling"] / (merged["Salary"] / 1000)).round(2)
    merged["CeilPct"] = _percentile_within(merged["Ceiling"], merged["Position"]).round(1)

    has_real_ownership = merged["ProjOwn"].fillna(0).gt(0).mean() > OWNERSHIP_PUBLISHED_SHARE_THRESHOLD
    merged["OwnStatus"] = OWN_STATUS_REAL if has_real_ownership else OWN_STATUS_UNPUBLISHED
    if has_real_ownership:
        merged["OwnPct"] = _percentile_within(merged["ProjOwn"], merged["Position"]).round(1)
        merged["Leverage"] = (merged["CeilPct"] - merged["OwnPct"]).round(1)
    else:
        merged["OwnPct"] = pd.NA
        merged["Leverage"] = pd.NA

    merged["GameEnv"] = _game_env_scores(merged["Game"], merged["OU"], merged["Spread"])
    # Fix 2.3: O/U and Spread were computed into GameEnv but never
    # surfaced on EdgeRaw itself -- renamed OverUnder here (not OU) so its
    # header doesn't collide with Player Pool/Lineups' own "O/U" header
    # text sourced from a different tab (oddsFinal via PlayerPoolRaw).
    # Spread needs no rename; TFFB's own field is already called that.
    merged["OverUnder"] = merged["OU"]

    merged = _attach_games(merged, games)
    merged = _attach_weather(merged, weather)
    # Part 7.4: GameId used to be dropped right after Stadium/Roof/Wind
    # were joined off it -- an internal join key, never surfaced. Now
    # kept and renamed to GameID (this column's own header text) so
    # stacks (players sharing a game) are visible on EdgeRaw itself.
    merged = merged.rename(columns={"GameId": "GameID"})
    merged = _attach_line_movement(merged, line_movement)
    merged = _attach_opp_pos_rank(merged, sos_by_position)
    merged["TmRank"] = _tm_rank_within_team_position(
        merged["Salary"], merged["Name"], merged["Team"], merged["Position"]
    )

    merged["Avail"] = merged["Status"].fillna("")
    merged = merged.drop(columns="Status")

    flag_lists = merged.apply(_flags_for_row, axis=1)
    merged["Flags"] = flag_lists.apply(" ".join)
    # Part 7.9: "Flag" is just the single highest-priority token (empty
    # string, not NaN, when nothing fired -- consistent with "Flags").
    merged["Flag"] = flag_lists.apply(lambda flags: flags[0] if flags else "")

    # Part 7.2: `ValAdj` is EdgeRaw's default sort now -- Part 7.1 demoted
    # `Leverage` off the spine specifically because it's no longer a
    # primary sort anywhere (TFFB's ownership projection is large-field,
    # wrong-shaped for Sam's small-field contests; see docs/CALCULATIONS.
    # md). Unlike the old Leverage/CeilPct fallback, `ValAdj` never
    # depends on ownership having published, so no fallback branch is
    # needed here.
    merged = merged.sort_values("ValAdj", ascending=False, na_position="last").reset_index(drop=True)

    # Phase 6, Part 2: renamed to Own% ONLY here, at the very last step --
    # every computation above (has_real_ownership, OwnPct, Leverage,
    # CHALK's ProjOwn check) still reasons in terms of ProjOwn, TFFB's own
    # field name for this value. EDGE_COLUMNS lists the OUTPUT name
    # (Own%), which is why the rename has to land after every internal use
    # of "ProjOwn" and right before this final column selection.
    merged = merged.rename(columns={"ProjOwn": "Own%"})

    # Zone labels carry no per-row data -- text lives in the header only
    # (see ZONE_LABELS' own comment for why they exist at all).
    for label in ZONE_LABELS:
        merged[label] = ""

    return EdgeBuildResult(frame=merged[EDGE_COLUMNS], unmatched_names=unmatched_names)
