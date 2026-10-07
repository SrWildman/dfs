"""Prior-only rolling statistics: a row's feature never sees its own game or any later one."""

import numpy as np
import pandas as pd
import pytest

from dfs.research.rolling import BLEND_PRIOR_GAMES, LOOKBACKS, prior_blend, prior_mean, prior_stat


def _frame(values, seasons=None) -> pd.DataFrame:
    n = len(values)
    seasons = seasons or [2023] * n
    weeks = []
    seen: dict[int, int] = {}
    for s in seasons:
        seen[s] = seen.get(s, 0) + 1
        weeks.append(seen[s])
    f = pd.DataFrame({"k": "x", "season": seasons, "week": weeks, "v": values})
    f["t"] = f["season"] * 100 + f["week"]
    return f


def test_prior_mean_excludes_the_game_itself():
    f = _frame([10.0, 20.0, 30.0, 40.0])
    out = prior_mean(f, ["k"], "v", 2)
    assert np.isnan(out.iloc[0])
    assert out.iloc[1] == 10.0  # only game 1 before game 2
    assert out.iloc[2] == 15.0  # games 1, 2
    assert out.iloc[3] == 25.0  # games 2, 3 -- not game 4, not game 1


@pytest.mark.parametrize("lookback", LOOKBACKS)
def test_a_feature_is_unchanged_by_its_own_and_later_values(lookback):
    values = list(np.arange(1.0, 21.0))
    seasons = [2022] * 10 + [2023] * 10
    base = prior_stat(_frame(values, seasons), ["k"], "v", lookback)
    for i in (3, 9, 12, 17):
        changed = values.copy()
        changed[i:] = [1e6 + j for j in range(len(changed) - i)]  # this game and everything after it
        out = prior_stat(_frame(changed, seasons), ["k"], "v", lookback)
        pd.testing.assert_series_equal(base.iloc[: i + 1], out.iloc[: i + 1])
        if i + 1 < len(values):  # the next game's feature does see game i, so the check is not vacuous
            assert base.iloc[i + 1] != out.iloc[i + 1]


def test_each_key_rolls_on_its_own():
    f = pd.concat([_frame([1.0, 2.0, 3.0]), _frame([100.0, 200.0, 300.0]).assign(k="y")], ignore_index=True)
    out = prior_mean(f, ["k"], "v", 4)
    assert out.iloc[2] == 1.5 and out.iloc[5] == 150.0


def test_blend_is_last_season_early_and_this_season_late():
    prev = [10.0] * 10
    cur = [20.0] * 10
    f = _frame(prev + cur, [2022] * 10 + [2023] * 10)
    out = prior_blend(f, ["k"], "v")
    k = BLEND_PRIOR_GAMES
    assert np.isnan(out.iloc[0])  # nothing before the first game of the first season
    assert out.iloc[9] == pytest.approx(10.0)  # last season's own games, no earlier season
    assert out.iloc[10] == pytest.approx(10.0)  # first game of 2023: all last season
    assert out.iloc[11] == pytest.approx((20.0 + k * 10.0) / (1 + k))
    assert out.iloc[19] == pytest.approx((9 * 20.0 + k * 10.0) / (9 + k))


def test_a_missing_value_is_skipped_not_counted():
    f = _frame([10.0, np.nan, 30.0, 40.0])
    assert prior_mean(f, ["k"], "v", 8).iloc[3] == pytest.approx(20.0)
