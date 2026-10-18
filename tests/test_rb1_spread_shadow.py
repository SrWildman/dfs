"""The RB1 spread shadow: CalPts plus 0.1 point per point of favouritism, for each team's top RB only."""

import numpy as np
import pandas as pd

from dfs import calibration as cal
from dfs import results_loop


def _frame():
    return pd.DataFrame(
        {
            "week": [3, 3, 3, 3, 3],
            "Team": ["KC", "KC", "KC", "DEN", "DEN"],
            "Position": ["RB", "RB", "WR", "RB", "QB"],
            "AggPts": [14.0, 8.0, 12.0, 11.0, 18.0],
            "CalPts": [14.0, 8.0, 12.0, 11.0, 18.0],
            "Spread": [-10.0, -10.0, -10.0, 10.0, 10.0],
        }
    )


def test_only_each_teams_top_rb_is_adjusted_and_a_favourite_gains_points():
    out = cal.with_rb1_spread(_frame())
    col = cal.RB1_SPREAD_SOURCE
    assert out[col].iloc[0] == 14.0 + 1.0  # KC RB1, favoured by 10
    assert out[col].iloc[3] == 11.0 - 1.0  # DEN RB1, 10-point underdog
    assert out[col].iloc[[1, 2, 4]].isna().all()  # RB2, a WR and a QB are left alone


def test_a_missing_spread_or_calpts_column_leaves_the_shadow_blank():
    no_spread = _frame().drop(columns="Spread")
    assert cal.with_rb1_spread(no_spread)[cal.RB1_SPREAD_SOURCE].isna().all()
    blank = _frame().assign(Spread=np.nan)
    assert cal.with_rb1_spread(blank)[cal.RB1_SPREAD_SOURCE].isna().all()


def test_a_tie_for_rb1_adjusts_one_back_not_two():
    f = _frame()
    f.loc[1, "AggPts"] = 14.0
    assert cal.with_rb1_spread(f)[cal.RB1_SPREAD_SOURCE].iloc[:2].notna().sum() == 1


def test_the_race_is_empty_without_spreads_so_old_scored_files_still_work():
    assert cal.rb1_spread_race(pd.DataFrame({"week": [1], "season": [2026]})).empty


def test_scored_files_carry_the_spread():
    assert "Spread" in results_loop.SCORED_COLUMNS
    assert results_loop.SCORED_COLUMNS[-2:] == ["EspnPts", "PropPts"]
