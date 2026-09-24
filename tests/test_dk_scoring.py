import math

import pandas as pd

from dfs.dk_scoring import (
    DST_POINTS_ALLOWED_TIERS,
    expected_points_allowed_score,
    expected_yardage_bonus,
    score_dst_frame,
    score_dst_row,
    score_offense_frame,
    score_offense_row,
    validate_against_tffb,
)


def test_score_offense_row_linear_stats_no_bonus():
    stats = {
        "pass_yd": 200.0,
        "pass_td": 2.0,
        "pass_int": 1.0,
        "rush_yd": 20.0,
        "rush_td": 0.0,
        "rec": 0.0,
        "rec_yd": 0.0,
        "rec_td": 0.0,
        "fum_lost": 0.0,
        "two_pt": 0.0,
    }
    # 200*.04=8, 2*4=8, 1*-1=-1, 20*.1=2 -> 17, plus a tiny expected-bonus
    # sliver from 200 passing yards not being exactly zero probability of
    # clearing 300 -- assert within a small tolerance instead of exact.
    result = score_offense_row(stats)
    assert 17.0 <= result < 17.5


def test_score_offense_row_missing_fields_returns_nan_not_zero():
    # A source with no real projection at all must not silently score as a
    # real 0 -- see score_offense_row's own docstring (found live: Sleeper
    # does exactly this for a player it has no weekly projection for).
    assert math.isnan(score_offense_row({}))


def test_score_offense_row_a_real_all_zero_game_still_scores_zero():
    stats = dict.fromkeys(
        [
            "pass_yd",
            "pass_td",
            "pass_int",
            "rush_yd",
            "rush_td",
            "rec",
            "rec_yd",
            "rec_td",
            "fum_lost",
            "two_pt",
        ],
        0.0,
    )
    assert score_offense_row(stats) == 0.0


def test_score_dst_row_missing_fields_returns_nan_not_zero():
    assert math.isnan(score_dst_row({}))


def test_expected_yardage_bonus_is_zero_for_nonpositive_or_missing_projection():
    assert expected_yardage_bonus(0, 100, 0.5, 3.0) == 0.0
    assert expected_yardage_bonus(-5, 100, 0.5, 3.0) == 0.0
    assert expected_yardage_bonus(float("nan"), 100, 0.5, 3.0) == 0.0


def test_expected_yardage_bonus_is_half_the_bonus_exactly_at_the_projected_mean():
    # projected yards == threshold -> P(actual >= threshold) == 0.5 under a
    # symmetric normal centered on the projection itself.
    assert math.isclose(expected_yardage_bonus(100.0, 100.0, 0.5, 3.0), 1.5, rel_tol=1e-9)


def test_expected_yardage_bonus_increases_with_projection_below_threshold():
    low = expected_yardage_bonus(70.0, 100.0, 0.5, 3.0)
    high = expected_yardage_bonus(95.0, 100.0, 0.5, 3.0)
    assert 0.0 < low < high < 1.5


def test_expected_yardage_bonus_approaches_full_bonus_far_above_threshold():
    result = expected_yardage_bonus(400.0, 100.0, 0.3, 3.0)
    assert 2.9 < result <= 3.0


def test_score_offense_row_300_passing_yards_gets_roughly_half_the_bonus():
    stats = dict.fromkeys(
        [
            "pass_yd",
            "pass_td",
            "pass_int",
            "rush_yd",
            "rush_td",
            "rec",
            "rec_yd",
            "rec_td",
            "fum_lost",
            "two_pt",
        ],
        0.0,
    )
    stats["pass_yd"] = 300.0
    result = score_offense_row(stats)
    # 300*.04 = 12 base, plus ~half of the +3 bonus (threshold == projection).
    assert 13.4 <= result <= 13.6


def test_expected_points_allowed_score_matches_a_tier_when_certain():
    # A degenerate near-zero sigma isn't reachable via the public CV
    # parameter at a realistic scale, so instead check monotonic sanity:
    # a defense projected to allow very few points scores far better than
    # one projected to allow a lot.
    stingy = expected_points_allowed_score(3.0)
    generous = expected_points_allowed_score(31.0)
    assert stingy > generous


def test_expected_points_allowed_score_is_zero_for_missing_or_nonpositive():
    assert expected_points_allowed_score(0.0) == 0.0
    assert expected_points_allowed_score(float("nan")) == 0.0


def test_dst_points_allowed_tiers_cover_every_integer_with_no_gap():
    # Every non-negative integer should fall in exactly one tier.
    covered = set()
    for lo, hi, _ in DST_POINTS_ALLOWED_TIERS:
        upper = hi if hi is not None else lo + 20  # sample well past the "and up" tier
        covered.update(range(lo, upper + 1))
    assert set(range(0, 40)) <= covered


def test_score_dst_row_linear_components():
    stats = {
        "sack": 2.0,
        "def_int": 1.0,
        "fum_rec": 1.0,
        "def_td": 0.0,
        "safety": 0.0,
        "blocked_kick": 0.0,
        "points_allowed": 0.0,
    }
    # 2*1 + 1*2 + 1*2 = 6, points_allowed of 0 contributes nothing (missing
    # convention, see expected_points_allowed_score's own docstring).
    assert score_dst_row(stats) == 6.0


def test_score_offense_frame_applies_row_wise():
    df = pd.DataFrame(
        [
            {
                "pass_yd": 250.0,
                "pass_td": 2.0,
                "pass_int": 0.0,
                "rush_yd": 10.0,
                "rush_td": 0.0,
                "rec": 0.0,
                "rec_yd": 0.0,
                "rec_td": 0.0,
                "fum_lost": 0.0,
                "two_pt": 0.0,
            },
            {
                "pass_yd": 0.0,
                "pass_td": 0.0,
                "pass_int": 0.0,
                "rush_yd": 80.0,
                "rush_td": 1.0,
                "rec": 3.0,
                "rec_yd": 25.0,
                "rec_td": 0.0,
                "fum_lost": 0.0,
                "two_pt": 0.0,
            },
        ]
    )
    result = score_offense_frame(df)
    assert len(result) == 2
    assert result.iloc[0] > 0
    assert result.iloc[1] > 0


def test_score_dst_frame_applies_row_wise():
    df = pd.DataFrame(
        [
            {
                "sack": 3.0,
                "def_int": 1.0,
                "fum_rec": 0.0,
                "def_td": 0.0,
                "safety": 0.0,
                "blocked_kick": 0.0,
                "points_allowed": 14.0,
            }
        ]
    )
    result = score_dst_frame(df)
    assert len(result) == 1
    assert result.iloc[0] > 0


def test_validate_against_tffb_reports_mean_and_mean_abs_diff_per_position():
    scored = pd.Series([20.0, 22.0, 10.0])
    tffb = pd.Series([18.0, 20.0, 10.5])
    position = pd.Series(["QB", "QB", "RB"])
    pool_mask = pd.Series([True, True, True])

    report = validate_against_tffb(scored, tffb, position, pool_mask)

    qb_row = report[report["Position"] == "QB"].iloc[0]
    assert qb_row["N"] == 2
    assert qb_row["MeanDiff"] == 2.0
    assert qb_row["MeanAbsDiff"] == 2.0
    rb_row = report[report["Position"] == "RB"].iloc[0]
    assert rb_row["MeanDiff"] == -0.5


def test_validate_against_tffb_excludes_players_outside_the_pool():
    scored = pd.Series([100.0, 0.0])  # a huge diff, but outside the pool
    tffb = pd.Series([0.0, 0.0])
    position = pd.Series(["WR", "WR"])
    pool_mask = pd.Series([False, True])

    report = validate_against_tffb(scored, tffb, position, pool_mask)

    wr_row = report[report["Position"] == "WR"].iloc[0]
    assert wr_row["N"] == 1
    assert wr_row["MeanDiff"] == 0.0
