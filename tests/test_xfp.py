"""xFP: the DK conversion by hand, the windows, and the BUY/FADE/USAGE tokens at their thresholds."""

import numpy as np
import pandas as pd
import pytest

from dfs import xfp


def _ffo(**kw):
    base = {
        "season": 2026,
        "posteam": "DEN",
        "week": 1,
        "player_id": "00-1",
        "full_name": "A Player",
        "position": "WR",
        "rec_attempt": 8,
        "rush_attempt": 0,
        "rec_attempt_team": 30,
        "rush_attempt_team": 25,
        "receptions_exp": 5.0,
        "rec_yards_gained_exp": 60.0,
        "rec_touchdown_exp": 0.5,
        "rush_yards_gained_exp": 0.0,
        "rush_touchdown_exp": 0.0,
        "pass_yards_gained_exp": 0.0,
        "pass_touchdown_exp": 0.0,
        "pass_interception_exp": 0.0,
        "pass_two_point_conv_exp": 0.0,
        "rec_two_point_conv_exp": 0.0,
        "rush_two_point_conv_exp": 0.0,
        "pass_touchdown": 0,
        "rec_touchdown": 1,
        "rush_touchdown": 0,
    }
    return {**base, **kw}


def test_the_dk_conversion_matches_a_hand_calculation():
    # receiving 5 + 0.1*60 + 6*0.5 = 14; rushing 0.1*40 + 6*0.25 + 2*0.1 = 5.7;
    # passing 0.04*250 + 4*1.5 - 0.8 + 2*0.05 = 15.3
    row = _ffo(
        rush_yards_gained_exp=40.0,
        rush_touchdown_exp=0.25,
        rush_two_point_conv_exp=0.1,
        pass_yards_gained_exp=250.0,
        pass_touchdown_exp=1.5,
        pass_interception_exp=0.8,
        pass_two_point_conv_exp=0.05,
    )
    parts = xfp.expected_dk_points(pd.DataFrame([row]))
    assert parts.loc[0, "rec_xfp"] == pytest.approx(14.0)
    assert parts.loc[0, "rush_xfp"] == pytest.approx(5.7)
    assert parts.loc[0, "pass_xfp"] == pytest.approx(15.3)
    assert parts.loc[0, "xfp"] == pytest.approx(35.0)


def test_a_dropped_expectation_column_costs_only_its_own_term():
    row = pd.DataFrame([_ffo()]).drop(columns=["rec_touchdown_exp"])
    assert xfp.expected_dk_points(row).loc[0, "xfp"] == pytest.approx(5 + 6.0)  # no 6*0.5 TD term


def test_player_weeks_keeps_skill_positions_and_normalises_the_team_code():
    ffo = pd.DataFrame([_ffo(), _ffo(player_id="00-2", position="P"), _ffo(player_id="00-3", posteam="LA")])
    weeks = xfp.player_weeks(ffo, season=2026)
    assert sorted(weeks["GsisId"]) == ["00-1", "00-3"]
    assert weeks.set_index("GsisId").loc["00-3", "Team"] == "LAR"
    assert weeks.loc[0, "td"] == 1 and weeks.loc[0, "td_exp"] == pytest.approx(0.5)


def _weeks(rows):
    """rows: (gsis, week, xfp, dk_actual, td, td_exp)"""
    return pd.DataFrame(
        [
            {
                "GsisId": g,
                "Name": g,
                "Team": "DEN",
                "Position": "WR",
                "season": 2026,
                "week": w,
                "targets": 6.0,
                "carries": 0.0,
                "team_targets": 30.0,
                "team_carries": 25.0,
                "rec_xfp": x,
                "rush_xfp": 0.0,
                "pass_xfp": 0.0,
                "xfp": x,
                "td": td,
                "td_exp": te,
                "dk_actual": a,
            }
            for g, w, x, a, td, te in rows
        ]
    )


def test_window_is_the_last_three_games_played_before_the_week():
    weeks = _weeks(
        [
            ("a", 1, 10, 10, 0, 0),
            ("a", 2, 20, 20, 0, 0),
            ("a", 4, 30, 30, 0, 0),
            ("a", 5, 40, 40, 0, 0),
            ("a", 6, 99, 99, 0, 0),
        ]
    )
    window = xfp.xfp_windows(weeks, before_week=6)  # weeks 2, 4, 5 (week 3 was missed, not a zero)
    assert window.loc["a", "Games"] == 3
    assert window.loc["a", "xFP/G"] == pytest.approx(30.0)
    # nothing at or after the slate's own week can reach it
    assert xfp.xfp_windows(weeks, before_week=5).loc["a", "xFP/G"] == pytest.approx(20.0)


def _tokens(xfp_g, actual, td_excess, games=3, top=True):
    windows = pd.DataFrame(
        {"xFP/G": [xfp_g], "DkG": [actual], "TdExcess": [td_excess], "Games": [games]}, index=["a"]
    )
    return xfp.buy_fade_tokens(windows, pd.Series([top], index=["a"])).iloc[0]


def test_buy_fires_on_either_the_points_gap_or_the_percentage_gap():
    assert _tokens(20.0, 16.9, 0) == xfp.TOKEN_BUY  # 3.1 points under (and 15%)
    assert _tokens(10.0, 7.4, 0) == xfp.TOKEN_BUY  # only 2.6 points, but 26% under
    assert _tokens(20.0, 17.5, 0) == ""  # 2.5 points and 12.5%: neither
    assert _tokens(10.0, 7.6, 0) == ""  # 2.4 points, 24%: neither


def test_buy_and_fade_need_the_top_half_and_a_real_window():
    assert _tokens(20.0, 10.0, 0, top=False) == ""
    assert _tokens(20.0, 10.0, 0, games=0) == ""


def test_fade_needs_the_mirror_gap_and_the_td_excess():
    assert _tokens(10.0, 14.0, 1.5) == xfp.TOKEN_FADE
    assert _tokens(10.0, 14.0, 1.4) == ""  # a hot stretch without TD luck is not faded
    assert _tokens(10.0, 11.0, 3.0) == ""  # TD luck without the gap is not faded
    assert _tokens(10.0, 12.6, 2.0) == xfp.TOKEN_FADE  # 26% over


def _usage_weeks(shares):
    """One player, one week each, with the given target shares (team targets fixed at 100)."""
    rows = [("a", w + 1, 10, 10, 0, 0) for w in range(len(shares))]
    frame = _weeks(rows)
    frame["team_targets"] = 100.0
    frame["targets"] = [s * 100 for s in shares]
    return frame


def test_usage_needs_two_recent_and_two_earlier_games_and_shows_n():
    # three games only: 2 recent + 1 earlier is not enough
    assert xfp.usage_jumps(_usage_weeks([0.10, 0.20, 0.20]), None, before_week=4).empty
    jumps = xfp.usage_jumps(_usage_weeks([0.10, 0.10, 0.20, 0.20]), None, before_week=5)
    assert jumps.loc["a", "n"] == 2
    assert jumps.loc["a", "token"] == xfp.TOKEN_USAGE_UP  # +0.10 target share >= 0.06
    assert (
        xfp.usage_jumps(_usage_weeks([0.25, 0.25, 0.15, 0.15]), None, before_week=5).loc["a", "token"]
        == xfp.TOKEN_USAGE_DOWN
    )


def test_usage_jump_thresholds_are_inclusive_and_an_rz_jump_counts():
    flat = _usage_weeks([0.10, 0.10, 0.16, 0.16])
    assert xfp.usage_jumps(flat, None, before_week=5).loc["a", "token"] == xfp.TOKEN_USAGE_UP  # exactly +0.06
    small = _usage_weeks([0.10, 0.10, 0.15, 0.15])
    assert xfp.usage_jumps(small, None, before_week=5).loc["a", "token"] == ""
    rz = pd.DataFrame({"GsisId": ["a"] * 4, "week": [1, 2, 3, 4], "rz": [0, 0, 1, 1]})
    assert xfp.usage_jumps(small, rz, before_week=5).loc["a", "token"] == xfp.TOKEN_USAGE_UP  # +1.0 RZ/G


def test_usage_ignores_games_at_or_after_the_slate_week():
    shares = _usage_weeks([0.10, 0.10, 0.10, 0.10, 0.90])  # a huge week 5 must not leak into week 5's signal
    assert xfp.usage_jumps(shares, None, before_week=5).loc["a", "token"] == ""


def test_position_percentile_ranks_within_position_over_the_pool():
    values = pd.Series([1.0, 2.0, 3.0, 4.0, 10.0, 20.0])
    position = pd.Series(["WR"] * 4 + ["RB"] * 2)
    pct = xfp.position_percentile(values, position)
    assert pct.iloc[3] == 1.0 and pct.iloc[0] == 0.25 and pct.iloc[5] == 1.0
    assert np.isnan(xfp.position_percentile(pd.Series([np.nan]), pd.Series(["WR"])).iloc[0])


def test_the_xfp_total_equals_the_model_packages_own_conversion():
    from dfs.model.history import expected_dk_points as model_xfp

    rows = [
        _ffo(player_id="a", rush_yards_gained_exp=40.0, rush_touchdown_exp=0.25, rush_two_point_conv_exp=0.1),
        _ffo(
            player_id="b",
            pass_yards_gained_exp=250.0,
            pass_touchdown_exp=1.5,
            pass_interception_exp=0.8,
            pass_two_point_conv_exp=0.05,
            rec_two_point_conv_exp=0.2,
        ),
    ]
    frame = pd.DataFrame(rows)
    mine = xfp.expected_dk_points(frame)["xfp"].to_numpy()
    theirs = model_xfp(frame).set_index("player_id").loc[["a", "b"], "xfp"].to_numpy()
    assert mine == pytest.approx(theirs)
