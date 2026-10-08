"""xFP: the DK conversion by hand, the windows, and the FADE (TE) / USAGE (RB carry share) tokens."""

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
    window = xfp.xfp_windows(weeks, before=(2026, 6))  # weeks 2, 4, 5 (week 3 was missed, not a zero)
    assert window.loc["a", "Games"] == 3
    assert window.loc["a", "xFP/G"] == pytest.approx(30.0)
    # nothing at or after the slate's own week can reach it
    assert xfp.xfp_windows(weeks, before=(2026, 5)).loc["a", "xFP/G"] == pytest.approx(20.0)


def test_the_window_runs_across_the_season_boundary():
    last_year = _weeks([("a", 16, 50, 50, 0, 0), ("a", 17, 60, 60, 0, 0)]).assign(season=2025)
    this_year = _weeks([("a", 1, 10, 10, 0, 0)])
    window = xfp.xfp_windows(pd.concat([last_year, this_year]), before=(2026, 2))
    assert window.loc["a", "Games"] == 3
    assert window.loc["a", "xFP/G"] == pytest.approx(40.0)  # 50, 60, 10: last season fills the window


def test_attach_actual_points_joins_one_season_and_leaves_the_other():
    weeks = pd.concat([_weeks([("a", 1, 10, 0, 0, 0)]).assign(season=2025), _weeks([("a", 1, 10, 0, 0, 0)])])
    weeks = weeks.drop(columns="dk_actual")
    actual = pd.DataFrame({"player_id": ["a"], "week": [1], "dk_actual": [17.0]})
    out = xfp.attach_actual_points(weeks, actual, 2026).set_index("season")
    assert out.loc[2026, "dk_actual"] == 17.0 and pd.isna(out.loc[2025, "dk_actual"])


def _tokens(xfp_g, actual, games=3, top=True, position="TE"):
    windows = pd.DataFrame({"xFP/G": [xfp_g], "DkG": [actual], "Games": [games]}, index=["a"])
    return xfp.fade_tokens(windows, pd.Series([top], index=["a"]), pd.Series([position], index=["a"])).iloc[0]


def test_fade_is_te_only_two_points_over_expected_and_inclusive():
    assert _tokens(10.0, 12.0) == xfp.TOKEN_FADE  # exactly +2.0
    assert _tokens(10.0, 11.9) == ""
    assert _tokens(20.0, 22.0) == xfp.TOKEN_FADE  # a points gap, not a percentage: 10% is enough
    for position in ("QB", "RB", "WR"):
        assert _tokens(10.0, 20.0, position=position) == ""


def test_fade_needs_the_top_half_and_a_full_window_and_no_td_condition():
    assert _tokens(10.0, 14.0, top=False) == ""
    assert _tokens(10.0, 14.0, games=2) == ""
    assert not hasattr(xfp, "FADE_TD_EXCESS")  # the touchdown condition is gone


def test_buy_is_gone():
    assert not hasattr(xfp, "TOKEN_BUY") and not hasattr(xfp, "buy_fade_tokens")


def _usage_weeks(shares, *, position="RB"):
    """One player, one game a week from Week 1, with the given carry shares (team carries fixed at 100)."""
    frame = _weeks([("a", w + 1, 10, 10, 0, 0) for w in range(len(shares))])
    frame["team_carries"] = 100.0
    frame["carries"] = [s * 100 for s in shares]
    return frame.assign(Position=position)


EARLIER6 = [0.20] * 6


def test_usage_needs_eight_prior_games_two_recent_and_six_before():
    assert xfp.usage_jumps(_usage_weeks([0.2] * 5 + [0.3, 0.3]), before=(2026, 8)).empty  # seven games
    jumps = xfp.usage_jumps(_usage_weeks([*EARLIER6, 0.30, 0.30]), before=(2026, 9))
    assert jumps.loc["a", "n"] == 8
    assert jumps.loc["a", "token"] == xfp.TOKEN_USAGE_UP  # +0.10 carry share, exactly the threshold
    down = xfp.usage_jumps(_usage_weeks([0.30] * 6 + [0.25, 0.25]), before=(2026, 9))
    assert down.loc["a", "token"] == xfp.TOKEN_USAGE_DOWN  # -0.05, exactly the threshold


def test_usage_thresholds_are_ten_up_and_five_down():
    assert xfp.usage_jumps(_usage_weeks([*EARLIER6, 0.29, 0.29]), before=(2026, 9)).loc["a", "token"] == ""
    assert xfp.usage_jumps(_usage_weeks([0.30] * 6 + [0.26, 0.26]), before=(2026, 9)).loc["a", "token"] == ""


def test_usage_is_rb_carry_share_only():
    receiver = _usage_weeks([*EARLIER6, 0.30, 0.30], position="WR")
    assert xfp.usage_jumps(receiver, before=(2026, 9)).empty
    tgt = _usage_weeks([0.0] * 8)
    tgt["team_targets"], tgt["targets"] = 100.0, [10.0] * 6 + [40.0, 40.0]  # a target-share jump: ignored
    assert xfp.usage_jumps(tgt, before=(2026, 9)).loc["a", "token"] == ""


def test_usage_window_crosses_the_season_boundary():
    last_year = _usage_weeks([0.20] * 4).assign(season=2025)
    this_year = _usage_weeks([0.20, 0.20, 0.30, 0.30])
    jumps = xfp.usage_jumps(pd.concat([last_year, this_year]), before=(2026, 5))
    assert jumps.loc["a", "n"] == 8 and jumps.loc["a", "token"] == xfp.TOKEN_USAGE_UP


def test_usage_ignores_games_at_or_after_the_slate_week():
    shares = _usage_weeks([*EARLIER6, 0.20, 0.20, 0.90])  # a huge week 9 must not leak into week 9's signal
    assert xfp.usage_jumps(shares, before=(2026, 9)).loc["a", "token"] == ""


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
