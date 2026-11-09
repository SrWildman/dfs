"""Plan S check 1: the per-player spread term (`sim/volatility_check.py`)."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from dfs.model import distribution as dist
from dfs.sim import marginal
from dfs.sim import volatility_check as vc


def games(n: int = 12) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "gsis_id": "p1",
            "season": 2024,
            "week": np.arange(1, n + 1),
            "position": "WR",
            "dk": np.arange(1, n + 1, dtype=float) * 2,
            "targets": np.arange(1, n + 1, dtype=float),
            "attempts": 0.0,
            "receiving_air_yards": np.arange(1, n + 1, dtype=float) * 10,
            "passing_air_yards": 0.0,
            "air_yards_share": 0.2,
            "receiving_tds": [1.0] + [0.0] * (n - 1),
            "rushing_tds": 0.0,
            "passing_tds": 0.0,
            "snap_pct": np.linspace(0.5, 0.9, n),
        }
    )


def test_features_use_previous_games_only():
    f = vc.player_features(games())
    assert f.loc[: vc.MIN_GAMES - 1, list(vc.FEATURES)].isna().all().all()  # too few previous games
    row = f.iloc[5]  # week 6: games 1-5 before it
    assert row["adot"] == pytest.approx(10.0)
    assert row["tgt_sd"] == pytest.approx(np.std([1, 2, 3, 4, 5], ddof=1))
    assert row["td_share"] == pytest.approx(6.0 / sum(2 * k for k in range(1, 6)))
    assert row["snap_trend"] == pytest.approx((0.9 - 0.5) / 11, rel=1e-6)
    # the game itself never moves its own features
    changed = games()
    changed.loc[5, ["dk", "targets", "receiving_air_yards"]] = [99.0, 50.0, 900.0]
    again = vc.player_features(changed).iloc[5]
    for col in vc.FEATURES:
        assert again[col] == pytest.approx(row[col], nan_ok=True)
    # the window is the last WINDOW games
    late = f.iloc[11]
    assert late["tgt_sd"] == pytest.approx(np.std(np.arange(11 - vc.WINDOW + 1, 12), ddof=1))


def test_a_planted_spread_is_found_and_judged_better_held_out():
    rng = np.random.default_rng(0)
    n = 30000
    pred = rng.uniform(8, 16, n)
    x = rng.normal(size=n)
    ratios = dist.ratio_matrix("WR", pred)
    true = marginal.stretch_upper(ratios, np.clip(0.3 * x, -0.5, 0.5))
    u = rng.uniform(0.02, 0.98, n)
    levels = np.asarray(dist.LEVELS)
    dk = np.array([np.interp(u[i], levels, true[i]) for i in range(n)]) * pred
    beta = vc.fit_beta(ratios, pred, dk, x.reshape(-1, 1))
    assert beta[0] == pytest.approx(0.3, abs=0.07)
    test = pd.DataFrame({"pred": pred, "dk": dk, "season": 2024, "week": rng.integers(1, 19, n)})
    groups = np.digitize(x, np.quantile(x, [1 / 3, 2 / 3]))
    res = vc.judge(test, ratios, marginal.stretch_upper(ratios, vc.deltas(x.reshape(-1, 1), beta)), groups)
    assert res["pinball_gain"] > 2 * res["pinball_se"] and res["weeks_better"] > 0.5
    assert res["chi2_90_new"] < res["chi2_90_base"]


def test_no_signal_fits_near_zero_and_does_not_clear():
    rng = np.random.default_rng(1)
    n = 20000
    pred = rng.uniform(8, 16, n)
    ratios = dist.ratio_matrix("WR", pred)
    u = rng.uniform(0.02, 0.98, n)
    levels = np.asarray(dist.LEVELS)
    dk = np.array([np.interp(u[i], levels, ratios[i]) for i in range(n)]) * pred
    x = rng.normal(size=(n, 1))
    beta = vc.fit_beta(ratios, pred, dk, x)
    assert abs(beta[0]) < 0.05
    test = pd.DataFrame({"pred": pred, "dk": dk, "season": 2024, "week": rng.integers(1, 19, n)})
    groups = np.digitize(x[:, 0], np.quantile(x[:, 0], [1 / 3, 2 / 3]))
    res = vc.judge(test, ratios, marginal.stretch_upper(ratios, vc.deltas(x, beta)), groups)
    assert not res["clears"]


def test_chi2_is_zero_at_the_nominal_rate():
    hits = np.array([True] + [False] * 9 + [True] + [False] * 9)
    assert vc.chi2_calibration(hits, np.array([0] * 10 + [1] * 10), 0.9) == pytest.approx(0.0)


def test_standardise_zeros_a_missing_value():
    train = pd.DataFrame({"a": [1.0, 2.0, 3.0]})
    rows = pd.DataFrame({"a": [2.0, np.nan, 100.0]})
    x = vc.standardise(rows, train, ("a",))
    assert x[0, 0] == 0.0 and x[1, 0] == 0.0 and x[2, 0] == 4.0  # clipped
