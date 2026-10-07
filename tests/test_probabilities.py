"""Outcome probabilities: the engine applied to CalPts, the QB/DST review rules, UM's slate rows."""

import numpy as np
import pandas as pd
import pytest

from dfs import probabilities as pr
from dfs.model import distribution


def _frame(rows):
    return pd.DataFrame(rows, columns=["Position", "Salary", "CalPts"])


def test_the_three_probabilities_come_straight_from_the_engine_at_3x_4x_and_2x_salary():
    frame = _frame([("WR", 5500, 12.0)])
    out = pr.outcome_columns(frame)
    hit = distribution.prob_at_least("WR", 12.0, 3 * 5.5)
    boom = distribution.prob_at_least("WR", 12.0, 4 * 5.5)
    bust = 1 - distribution.prob_at_least("WR", 12.0, 2 * 5.5)
    assert out.loc[0, "Hit3x%"] == pytest.approx(100 * hit, abs=0.06)
    assert out.loc[0, "Boom%"] == pytest.approx(100 * boom, abs=0.06)
    assert out.loc[0, "Bust%"] == pytest.approx(100 * bust, abs=0.06)
    floor, ceil = distribution.floor_ceiling("WR", 12.0)
    assert out.loc[0, "Floor"] == pytest.approx(floor, abs=0.06) and out.loc[0, "CeilM"] == pytest.approx(
        ceil, abs=0.06
    )
    assert out.loc[0, "Floor"] < 12.0 < out.loc[0, "CeilM"]
    assert out.loc[0, "Hit3x%"] > out.loc[0, "Boom%"]  # a lower bar is cleared more often


def test_a_higher_salary_lowers_every_upside_probability():
    cheap = pr.outcome_columns(_frame([("RB", 4000, 12.0)])).iloc[0]
    dear = pr.outcome_columns(_frame([("RB", 8000, 12.0)])).iloc[0]
    assert (
        dear["Hit3x%"] < cheap["Hit3x%"] and dear["Boom%"] < cheap["Boom%"] and dear["Bust%"] > cheap["Bust%"]
    )


def test_no_projection_or_no_salary_is_blank_never_zero():
    out = pr.outcome_columns(
        _frame([("WR", 5000, np.nan), ("WR", np.nan, 12.0), ("WR", 0, 12.0), ("XX", 5000, 9.0)])
    )
    assert out[pr.PROB_COLUMNS].isna().all().all()
    assert not out["LowConf"].any()


def test_a_qb_under_ten_points_is_not_rated_and_ten_exactly_is():
    out = pr.outcome_columns(_frame([("QB", 5000, 9.9), ("QB", 5000, 10.0), ("QB", 5000, 3.0)]))
    assert out.loc[0, pr.PROB_COLUMNS].isna().all() and out.loc[2, pr.PROB_COLUMNS].isna().all()
    assert out.loc[1, pr.PROB_COLUMNS].notna().all()
    assert pr.RATED_NOTE == "not rated below 10 pts"
    # an RB at the same projection is rated: the rule is QB-only
    assert (
        pr.outcome_columns(_frame([("RB", 5000, 9.9)])).loc[0, "Hit3x%"]
        == pr.outcome_columns(_frame([("RB", 5000, 9.9)])).loc[0, "Hit3x%"]
    )
    assert pr.outcome_columns(_frame([("RB", 5000, 9.9)])).loc[0, pr.PROB_COLUMNS].notna().all()


def test_a_low_projected_dst_keeps_its_values_but_is_marked_low_confidence():
    out = pr.outcome_columns(_frame([("DST", 2500, 3.9), ("DST", 2500, 4.0), ("DST", 3500, 9.0)]))
    assert out.loc[0, pr.PROB_COLUMNS].notna().all()
    assert out["LowConf"].tolist() == [True, False, False]


def test_dst_uses_the_dst_tables_not_the_wr_ones():
    dst = pr.outcome_columns(_frame([("DST", 3000, 8.0)])).iloc[0]
    wr = pr.outcome_columns(_frame([("WR", 3000, 8.0)])).iloc[0]
    assert dst["Hit3x%"] != wr["Hit3x%"]


def _games():
    cols = ["game_id", "season", "week", "game_type", "away_team", "home_team", "spread_line", "total_line"]
    return pd.DataFrame(
        [
            ("2026_05_TB_DAL", 2026, 5, "REG", "TB", "DAL", 9.5, 47.5),
            ("2026_05_LA_SEA", 2026, 5, "REG", "LA", "SEA", -3.0, 44.0),
            ("2026_04_TB_DAL", 2026, 4, "REG", "TB", "DAL", 1.0, 40.0),
        ],
        columns=cols,
    )


def test_slate_rows_carry_each_teams_own_side_of_the_vegas_context():
    frame = pd.DataFrame(
        {
            "Position": ["WR", "QB", "DST", "RB"],
            "Team": ["DAL", "TB", "LAR", "ZZZ"],
            "GsisId": ["00-1", "00-2", None, "00-4"],
        }
    )
    rows = pr.slate_rows(frame, _games(), season=2026, week=5)
    assert list(rows.index) == [0, 1, 2]  # ZZZ has no game that week: dropped
    dal = rows.loc[0]
    assert dal["opp"] == "TB" and dal["home"] == 1 and dal["spread"] == 9.5  # home, favoured by 9.5
    assert dal["implied"] == pytest.approx(47.5 / 2 + 9.5 / 2) and dal["total"] == 47.5
    tb = rows.loc[1]
    assert tb["home"] == 0 and tb["spread"] == -9.5 and tb["implied"] == pytest.approx(47.5 / 2 - 9.5 / 2)
    lar = rows.loc[2]
    assert lar["gsis_id"] == "LAR" and lar["opp"] == "SEA"  # a DST is keyed by team code; LA is read as LAR
    assert lar["spread"] == pytest.approx(3.0) and lar["home"] == 0  # road team favoured when spread_line < 0


def test_slate_rows_for_a_week_with_no_games_is_empty():
    frame = pd.DataFrame({"Position": ["WR"], "Team": ["DAL"], "GsisId": ["00-1"]})
    assert pr.slate_rows(frame, _games(), season=2026, week=9).empty
