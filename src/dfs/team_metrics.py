"""Part C, C7: per-team offense metrics (Pace, PROE, Expl%) from nflverse
play-by-play, and the combination logic that rebuilds `GameEnv` around them.

Pure and offline-testable, like `dk_scoring.py`/`player_join.py` -- the
thin, untested wrapper that fetches the real parquet files lives at
`sources/nflverse_pbp.py`, per `derived.py`'s own "split the pure logic
out" rule.

Column names below match nflverse's own play-by-play schema, verified
against a real pull (2026-09-25, season 2026 through week 3): `posteam`/
`defteam`/`home_team`/`away_team` (team codes -- nflverse still calls the
Rams `LA`, same drift `sources/nflverse_games.py` already found and fixed;
remapping happens at the source-module boundary, not here, so this module
never needs to know DraftKings' own codes), `play_type` ("pass"/"run" for
real scrimmage snaps -- "no_play" covers both penalties-with-no-play and
timeouts, "qb_kneel"/"qb_spike" are their own separate values, so
restricting to {"pass", "run"} already excludes every one of C7's named
Expl% exclusions with no extra filtering needed), `wp` (the POSSESSION
team's own win probability -- confirmed live: a road team's own early-game
snap reads close to 0.5, not the home team's complement), `qtr`,
`half_seconds_remaining`, `game_seconds_remaining`, `drive`, `play_id`,
`yards_gained`, `pass`/`rush` (nflverse's own 0/1 classification, not a
`play_type` string match, since a scramble is `play_type == "run"` but
`pass == 1`), and `pass_oe` (nflverse's own expected-pass-rate-over-
expected model output -- confirmed live: populated on 99.7-99.9% of
pass/run rows and ~0% of everything else, i.e. it's already scored on
exactly the same play population this module restricts to).
"""

from __future__ import annotations

import pandas as pd

# Real offensive scrimmage snaps only. Deliberately NOT a `play_type`
# blocklist (kickoff/punt/extra_point/field_goal/no_play/qb_kneel/
# qb_spike) -- an allowlist of the two real snap types can't miss a future
# nflverse play_type value the way a blocklist silently would. This single
# filter satisfies C7's Expl% exclusion list (kneels, spikes, penalties
# with no play) for free, since none of those are "pass" or "run".
_SCRIMMAGE_PLAY_TYPES = {"pass", "run"}

# Neutral script (Pace/PROE only, per C7): both teams still playing it
# straight -- not protecting a big lead, not desperately chasing one, and
# not in the clock-killing/hurry-up final two minutes of a half, all three
# of which distort tempo and play-calling independent of a team's real
# identity.
NEUTRAL_SCRIPT_WP_LOW = 0.2
NEUTRAL_SCRIPT_WP_HIGH = 0.8
NEUTRAL_SCRIPT_MAX_QTR = 3
NEUTRAL_SCRIPT_MIN_HALF_SECONDS = 120.0

# Expl%: DK-relevant ceiling plays -- a 20+ yard pass or a 10+ yard rush,
# each threshold picked because it's the standard "explosive play"
# definition used across public football analytics, not something this
# codebase is inventing.
EXPLOSIVE_PASS_YARDS = 20.0
EXPLOSIVE_RUSH_YARDS = 10.0

# Early-season blend (C7): `weight_current = games_played / (games_played
# + PBP_PRIOR_WEIGHT_GAMES)`. Picked 4 as a defensible starting value, not
# fit to anything: at 2 games played (this week's own slate, week 3, most
# teams have 2 played), weight_current = 2/6 = 33% -- mostly last season's
# shape, appropriate this early with almost no current-season signal yet.
# By 8 games (roughly mid-season) it's already 8/12 = 67%; it never fully
# reaches 100% (17/21 = 81% at a full season), which is the right shape --
# a small-sample current season should never fully drown out the prior
# one, even late. Flagged to Sam per C7's own "state it and why" -- not
# tuned against real week-over-week data the way LEVERAGE_FLAG_TOP_SHARE
# was, since there's only one week of this data so far.
PBP_PRIOR_WEIGHT_GAMES = 4.0

# GameEnv (C7 rebuild): four equal-weight percentile inputs -- total,
# spread tightness (both existing/Vegas-only), plus real combined pace and
# PROE now that C7 computes them. Equal weighting is the starting point,
# not a tuned result -- say so, per C7's own instruction. A game missing
# pace/PROE entirely (pbp failed to fetch this run) renormalizes over
# whatever's left (see `derived._game_env_scores`), which is what makes
# the "falls back to the existing Vegas-only formula" fail-soft behaviour
# fall out for free: with only `total`/`spread_tightness` available, the
# renormalized weighted mean of those two IS the old 50/50 formula exactly.
GAME_ENV_WEIGHTS = {"total": 0.25, "spread_tightness": 0.25, "pace": 0.25, "proe": 0.25}


def _scrimmage_mask(pbp: pd.DataFrame) -> pd.Series:
    return pbp["play_type"].isin(_SCRIMMAGE_PLAY_TYPES)


def _neutral_script_mask(pbp: pd.DataFrame) -> pd.Series:
    wp = pd.to_numeric(pbp["wp"], errors="coerce")
    qtr = pd.to_numeric(pbp["qtr"], errors="coerce")
    half_seconds = pd.to_numeric(pbp["half_seconds_remaining"], errors="coerce")
    return (
        wp.between(NEUTRAL_SCRIPT_WP_LOW, NEUTRAL_SCRIPT_WP_HIGH)
        & (qtr <= NEUTRAL_SCRIPT_MAX_QTR)
        & (half_seconds > NEUTRAL_SCRIPT_MIN_HALF_SECONDS)
    )


def team_pace(pbp: pd.DataFrame) -> pd.Series:
    """Mean seconds between consecutive offensive snaps in the same drive,
    neutral script only -- lower is faster. Computed as the (absolute)
    diff of `game_seconds_remaining` between consecutive neutral-script
    scrimmage plays within each `(game_id, drive)` group, sorted by
    `play_id` -- a drive's own first qualifying snap has no prior
    qualifying snap to gap against within that same drive and is dropped
    (`groupby().diff()`'s own first-row NaN), never compared across a
    drive boundary. Returns a Series indexed by nflverse's own team code
    (`posteam`) -- callers remap to DraftKings' codes."""
    plays = pbp[_scrimmage_mask(pbp) & _neutral_script_mask(pbp)].copy()
    plays["game_seconds_remaining"] = pd.to_numeric(plays["game_seconds_remaining"], errors="coerce")
    plays = plays.sort_values(["game_id", "drive", "play_id"])
    gaps = plays.groupby(["game_id", "drive"])["game_seconds_remaining"].diff().abs()
    return gaps.groupby(plays["posteam"]).mean().round(2)


def team_proe(pbp: pd.DataFrame) -> pd.Series:
    """Mean `pass_oe` over neutral-script scrimmage plays -- higher means
    pass-heavier than the situation implies. Same team-code/index
    convention as `team_pace`."""
    plays = pbp[_scrimmage_mask(pbp) & _neutral_script_mask(pbp)]
    pass_oe = pd.to_numeric(plays["pass_oe"], errors="coerce")
    return pass_oe.groupby(plays["posteam"]).mean().round(2)


def team_explosive_pct(pbp: pd.DataFrame) -> pd.Series:
    """Share of scrimmage plays (every game state, not neutral-script-
    restricted -- C7's table doesn't scope this one) gaining >= 20 yards
    on a pass or >= 10 on a rush, as a 0-100 percentage. `pass`/`rush` are
    nflverse's own play-type classification columns (a scramble is
    `play_type == "run"` but `pass == 0, rush == 1`), not a `play_type`
    string match, so this counts by what the play actually was, not how
    it's logged."""
    plays = pbp[_scrimmage_mask(pbp)].copy()
    yards = pd.to_numeric(plays["yards_gained"], errors="coerce")
    is_pass = pd.to_numeric(plays["pass"], errors="coerce").fillna(0) == 1
    is_rush = pd.to_numeric(plays["rush"], errors="coerce").fillna(0) == 1
    explosive = (is_pass & (yards >= EXPLOSIVE_PASS_YARDS)) | (is_rush & (yards >= EXPLOSIVE_RUSH_YARDS))
    return (explosive.groupby(plays["posteam"]).mean() * 100).round(1)


def team_games_played(pbp: pd.DataFrame) -> pd.Series:
    """Distinct games each team has appeared in this season -- by
    `home_team`/`away_team`, not by counting a team's own offensive plays,
    so a team is credited with a game it played even if every one of its
    offensive snaps happened to get filtered out upstream (never actually
    possible today, but this is the more directly correct definition of
    "games played" regardless)."""
    home = pbp[["game_id", "home_team"]].rename(columns={"home_team": "Team"})
    away = pbp[["game_id", "away_team"]].rename(columns={"away_team": "Team"})
    games = pd.concat([home, away], ignore_index=True).drop_duplicates()
    return games.groupby("Team")["game_id"].nunique()


# Round 5 item 9: defensive matchup efficiency from the SAME play-by-play the
# offensive metrics use. EPA is per-play expected points added; nflverse's own
# `epa`/`success` columns. Unlike Pace/PROE these use ALL game states -- no
# neutral-script filter -- because a defense's efficiency allowed is what it is
# regardless of score; garbage-time plays are part of the sample on purpose
# (this is a matchup signal, not a play-calling one). The same real-scrimmage-
# snap filter as the offensive metrics still applies (no kneels/spikes/no-plays).
EPA_DECIMALS = 3


def _epa_by_side(pbp: pd.DataFrame, side: str, kind: str) -> pd.Series:
    """Mean `epa` per team over real scrimmage plays, all game states.
    `side` is `"defteam"` (what a defense allowed) or `"posteam"` (what an
    offense produced); `kind` is `"play"` (every scrimmage play), `"pass"`
    (`pass == 1`, so a scramble counts as a pass, matching nflverse) or
    `"rush"` (`rush == 1`). Indexed by nflverse's own team code."""
    plays = pbp[_scrimmage_mask(pbp)]
    if kind == "pass":
        plays = plays[pd.to_numeric(plays["pass"], errors="coerce").fillna(0) == 1]
    elif kind == "rush":
        plays = plays[pd.to_numeric(plays["rush"], errors="coerce").fillna(0) == 1]
    epa = pd.to_numeric(plays["epa"], errors="coerce")
    return epa.groupby(plays[side]).mean().round(EPA_DECIMALS)


def defense_epa_pass(pbp: pd.DataFrame) -> pd.Series:
    """`DefEPA/Pass`: mean EPA on pass plays a defense allowed."""
    return _epa_by_side(pbp, "defteam", "pass")


def defense_epa_rush(pbp: pd.DataFrame) -> pd.Series:
    """`DefEPA/Rush`: mean EPA on rush plays a defense allowed."""
    return _epa_by_side(pbp, "defteam", "rush")


def defense_success_pct(pbp: pd.DataFrame) -> pd.Series:
    """`DefSucc%`: mean nflverse `success` allowed, as 0-100."""
    plays = pbp[_scrimmage_mask(pbp)]
    success = pd.to_numeric(plays["success"], errors="coerce")
    return (success.groupby(plays["defteam"]).mean() * 100).round(1)


def offense_epa_per_play(pbp: pd.DataFrame) -> pd.Series:
    """`OffEPA/Play`: mean EPA per scrimmage play an OFFENSE produced. A DST's
    matchup is the opposing offense, so `derived._attach_opp_epa` reads this
    (sign-flipped) for defenses."""
    return _epa_by_side(pbp, "posteam", "play")


def blend_with_prior(
    current: pd.Series,
    prior: pd.Series,
    games_played: pd.Series,
    prior_weight_games: float,
    *,
    decimals: int = 2,
) -> pd.Series:
    """`value = weight_current * current + (1 - weight_current) * prior`,
    `weight_current = games_played / (games_played + prior_weight_games)`
    (see `PBP_PRIOR_WEIGHT_GAMES`). A team missing one side (no current-
    season neutral-script sample yet, or -- not expected for an existing
    franchise, but handled rather than assumed away -- no prior-season
    row) falls back to whichever side it has rather than blending toward a
    fabricated 0; missing both leaves it NaN, same "blank is not zero"
    rule as everywhere else in this codebase. `games_played` missing for a
    team (bye week databases sometimes drop a team version-to-version)
    is treated as 0 games played -- pure prior, the same as week 1."""
    teams = current.index.union(prior.index).union(games_played.index)
    current = current.reindex(teams)
    prior = prior.reindex(teams)
    games_played = games_played.reindex(teams).fillna(0)

    weight_current = games_played / (games_played + prior_weight_games)
    blended = weight_current * current.fillna(0) + (1 - weight_current) * prior.fillna(0)

    only_current = current.notna() & prior.isna()
    only_prior = current.isna() & prior.notna()
    both_missing = current.isna() & prior.isna()
    blended = blended.mask(only_current, current)
    blended = blended.mask(only_prior, prior)
    blended = blended.mask(both_missing, float("nan"))
    return blended.round(decimals)


def combined_by_game(game: pd.Series, team: pd.Series, metric: pd.Series) -> pd.Series:
    """A per-game (not per-team) Series: the mean of both teams' own
    value in that game -- "combined pace/PROE of both offenses." `metric`
    is a per-ROW series already carrying each row's own TEAM's value (e.g.
    `merged["Pace"]`, identical for every player on the same team) --
    deduplicated to one row per (game, team) first so a game with more
    rostered players on one team than the other isn't accidentally
    player-count-weighted."""
    frame = pd.DataFrame({"Game": game, "Team": team, "Value": metric})
    per_team = frame.drop_duplicates(subset=["Game", "Team"])
    return per_team.groupby("Game")["Value"].mean()


def weighted_mean_skipna(components: pd.DataFrame, weights: dict[str, float]) -> pd.Series:
    """A weighted mean of `components[list(weights)]`, renormalized per row
    over whatever columns aren't NaN in that row -- so a row missing some
    inputs (e.g. Pace/PROE blank because pbp didn't sync this run) still
    gets a sensible score from whatever it has, rather than going NaN
    itself just because one input is missing. With every input present
    this is a plain weighted mean; with only a subset present, the
    remaining weights are scaled up to sum to 1 rather than left as a
    diluted partial sum."""
    weight_series = pd.Series(weights)
    values = components[list(weights)]
    present = values.notna()
    weighted = values.fillna(0).mul(weight_series, axis=1)
    numerator = weighted.sum(axis=1)
    denominator = present.mul(weight_series, axis=1).sum(axis=1)
    return (numerator / denominator.replace(0, float("nan"))).round(1)
