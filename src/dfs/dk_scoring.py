"""Part C, C2: re-scores any source's component stats to exact DraftKings
Classic NFL rules -- never a source's own fantasy-point total (Sleeper's
`pts_ppr`, FantasyPros' `FPTS` are NOT DraftKings scoring; a source's own
total is ignored everywhere in this module by design).

Verified against DraftKings' current rules (2026-09-24): draftkings.com/
help/rules/nfl renders entirely client-side (confirmed both live and via
the Wayback Machine -- no scoring content in the raw HTML either way), so
checked instead against RotoGrinders' independent NFL site-scoring-
comparison page, which lists the same table PROMPT_PART_C.md already had:
every offense and DST number below matches with no discrepancy.

Every caller passes stats through a canonical per-position schema (see
`OFFENSE_STAT_FIELDS`/`DST_STAT_FIELDS`) rather than a source's own raw
field names -- each source module is responsible for renaming its own
columns into this schema before calling `score_offense_row`/
`score_dst_row`, so this module stays source-agnostic (same split
`derived.py`'s own module docstring describes for source-vs-pure-logic
code)."""

from __future__ import annotations

import math

import pandas as pd

# Linear per-unit scoring -- verified 2026-09-24, see module docstring.
DK_OFFENSE_SCORING = {
    "pass_yd": 0.04,
    "pass_td": 4.0,
    "pass_int": -1.0,
    "rush_yd": 0.1,
    "rush_td": 6.0,
    "rec": 1.0,
    "rec_yd": 0.1,
    "rec_td": 6.0,
    "fum_lost": -1.0,
    "two_pt": 2.0,
}
OFFENSE_STAT_FIELDS = list(DK_OFFENSE_SCORING)

# (stat field, yardage threshold) pairs that carry a DK bonus -- same $3 for
# all three, but each measured on its own stat and its own CV (see
# YARDAGE_CV below).
YARDAGE_BONUS_THRESHOLDS = {"pass_yd": 300.0, "rush_yd": 100.0, "rec_yd": 100.0}
YARDAGE_BONUS_POINTS = 3.0

# The trap PROMPT_PART_C.md calls out: a *projected* yardage total is a
# mean, and DK's bonuses are thresholds on a single game's *realized*
# yardage -- awarding the full +3 the moment projected yards crosses the
# threshold creates a false cliff at exactly 100 (or 300) and is wrong on
# both sides of it. Treated instead as an expected value: bonus * P(actual
# yards >= threshold), modeling a game's yardage as roughly normal around
# the projection with std = CV * projection (see `expected_yardage_bonus`).
#
# These CV (coefficient of variation = std/mean) values are DEFENSIBLE
# STARTING GUESSES, not fit to real game logs -- flagged to Sam per
# PROMPT_PART_C.md's "don't bury a guess" instruction. Rough shape: a
# starting QB's passing yards are the most game-to-game consistent of the
# three (a bad game still means dropbacks, just inefficient ones), while
# a single RB/WR's rushing/receiving yards swing harder start to start
# (game script, target competition, a long touchdown run skewing one
# game's total) -- ordering QB < RB/WR is a standard DFS-projection
# heuristic, not a measured fact yet.
YARDAGE_CV = {"pass_yd": 0.35, "rush_yd": 0.55, "rec_yd": 0.55}

# DST linear per-unit scoring -- verified 2026-09-24, same source as above.
# `def_td` covers every defensive/return touchdown DK counts the same way
# (fumble return, interception return, kick/punt return, blocked-kick
# return) -- DK scores them identically, so callers sum every TD type a
# source reports into this one field before calling `score_dst_row`.
DK_DST_SCORING = {
    "sack": 1.0,
    "def_int": 2.0,
    "fum_rec": 2.0,
    "def_td": 6.0,
    "safety": 2.0,
    "blocked_kick": 2.0,
}
DST_STAT_FIELDS = [*DK_DST_SCORING, "points_allowed"]

# DK's points-allowed tiers -- (low, high inclusive, DK points); high=None
# means "and up". Verified 2026-09-24 (see module docstring): matches
# PROMPT_PART_C.md's table with no discrepancy.
DST_POINTS_ALLOWED_TIERS = [
    (0, 0, 10.0),
    (1, 6, 7.0),
    (7, 13, 4.0),
    (14, 20, 1.0),
    (21, 27, 0.0),
    (28, 34, -1.0),
    (35, None, -4.0),
]
# Same shape of guess as YARDAGE_CV above, same caveat: a defense's
# points-allowed is at least as game-to-game volatile as an individual
# skill player's yardage (one defensive/special-teams touchdown allowed
# swings a whole tier), so this sits at the high end of YARDAGE_CV's
# range rather than being fit to real game logs. Flagged to Sam, not
# buried, same as YARDAGE_CV.
POINTS_ALLOWED_CV = 0.60


def _normal_cdf(z: float) -> float:
    """Standard normal CDF via `math.erf` -- no scipy dependency for one
    formula used in exactly two places in this module."""
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))


def expected_yardage_bonus(projected_yards: float, threshold: float, cv: float, bonus: float) -> float:
    """`bonus * P(actual yards >= threshold)`, modeling a single game's
    yardage as normal around `projected_yards` with `std = cv *
    projected_yards`. A non-positive or missing projection can't clear any
    positive threshold, so it scores 0 without needing a defined sigma."""
    if pd.isna(projected_yards) or projected_yards <= 0:
        return 0.0
    sigma = cv * projected_yards
    if sigma <= 0:
        return bonus if projected_yards >= threshold else 0.0
    z = (threshold - projected_yards) / sigma
    return bonus * (1.0 - _normal_cdf(z))


def expected_points_allowed_score(projected_points_allowed: float, cv: float = POINTS_ALLOWED_CV) -> float:
    """Expected DK points-allowed score, modeling a defense's realized
    points allowed as normal around the projection (std = cv *
    projection) and integrating DK's discrete tiers against it, with a
    +/-0.5 continuity correction since points allowed is an integer.
    A non-positive/missing projection (a bye, or a source that simply
    doesn't project it) can't be modeled -- returns 0.0, read the same
    way `derived.py`'s "blank is not zero" convention would want a real
    caller to treat a missing input, not a real score of exactly zero
    tier-4 value."""
    if pd.isna(projected_points_allowed) or projected_points_allowed <= 0:
        return 0.0
    mean = projected_points_allowed
    sigma = cv * mean
    total = 0.0
    for lo, hi, pts in DST_POINTS_ALLOWED_TIERS:
        lower = _normal_cdf((lo - 0.5 - mean) / sigma)
        upper = 1.0 if hi is None else _normal_cdf((hi + 0.5 - mean) / sigma)
        total += (upper - lower) * pts
    return total


def _is_missing(value: object) -> bool:
    return value is None or (isinstance(value, float) and math.isnan(value)) or value is pd.NA


def score_offense_row(stats: dict[str, float]) -> float:
    """DK Classic points for one offensive player-game, from component
    stats keyed by `OFFENSE_STAT_FIELDS`. A source with NO real projection
    for this player (found live: Sleeper returns a `stats` dict for every
    player in its database, real projection or not -- see sources/
    sleeper_projections.py's own `_has_real_projection`) must pass NaN/None
    for every field, not 0 for each -- a single missing field anywhere
    makes the WHOLE row's score `nan`, same "blank is not zero" rule
    `derived.py` already enforces elsewhere. A source that legitimately
    knows a stat is exactly 0 (a real, if unlikely, 0-catch game) passes a
    real 0.0, which scores normally. Yardage bonuses use the expected-
    value treatment above, never a hard cutoff on the projection itself."""
    if any(_is_missing(stats.get(field)) for field in OFFENSE_STAT_FIELDS):
        return float("nan")
    total = sum(stats[field] * weight for field, weight in DK_OFFENSE_SCORING.items())
    for field, threshold in YARDAGE_BONUS_THRESHOLDS.items():
        total += expected_yardage_bonus(stats[field], threshold, YARDAGE_CV[field], YARDAGE_BONUS_POINTS)
    return round(total, 2)


def score_dst_row(stats: dict[str, float]) -> float:
    """DK Classic points for one DST-game, from component stats keyed by
    `DST_STAT_FIELDS` (`points_allowed` is the projected points allowed
    this game, scored via `expected_points_allowed_score`; every other
    field is a linear counting stat). Same all-fields-or-nothing missing-
    data contract as `score_offense_row` -- see its own docstring."""
    if any(_is_missing(stats.get(field)) for field in DST_STAT_FIELDS):
        return float("nan")
    total = sum(stats[field] * weight for field, weight in DK_DST_SCORING.items())
    total += expected_points_allowed_score(stats["points_allowed"])
    return round(total, 2)


def score_offense_frame(df: pd.DataFrame) -> pd.Series:
    """`score_offense_row` applied row-wise -- `df` must already have every
    column in `OFFENSE_STAT_FIELDS` (a source missing one, e.g. no 2-point
    conversion data, should fill it with 0 before calling this, not omit
    the column, so a `KeyError` here surfaces a real integration bug
    rather than a silently-blank score)."""
    return df[OFFENSE_STAT_FIELDS].apply(lambda row: score_offense_row(row.to_dict()), axis=1)


def score_dst_frame(df: pd.DataFrame) -> pd.Series:
    """`score_dst_row` applied row-wise -- same all-columns-present
    contract as `score_offense_frame`."""
    return df[DST_STAT_FIELDS].apply(lambda row: score_dst_row(row.to_dict()), axis=1)


def validate_against_tffb(
    scored: pd.Series, tffb_proj_pts: pd.Series, position: pd.Series, pool_mask: pd.Series
) -> pd.DataFrame:
    """C2's own required calibration check: per position, over the
    rosterable pool only, mean difference and mean absolute difference of
    a freshly-DK-scored source against TFFB's own (already DK-scored)
    `ProjPts`. A consistent per-position offset near +/-1 or more is a
    scoring bug, not a real disagreement -- report this to Sam before
    trusting the source for anything (PROMPT_PART_C.md's own instruction:
    stop and ask if any position's mean difference exceeds about 1 point)."""
    diff = (scored - tffb_proj_pts).where(pool_mask)
    rows = []
    for pos in sorted(position.unique()):
        pos_diff = diff[position == pos].dropna()
        if pos_diff.empty:
            continue
        rows.append(
            {
                "Position": pos,
                "N": len(pos_diff),
                "MeanDiff": round(pos_diff.mean(), 2),
                "MeanAbsDiff": round(pos_diff.abs().mean(), 2),
            }
        )
    return pd.DataFrame(rows)
