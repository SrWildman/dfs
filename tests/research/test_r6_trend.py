"""R6 trend bands: the arrow threshold flags about 15% of rows, discrete metrics keep their boundary
value, and the band table is built from player-games of 2018-2025 only."""

import numpy as np
import pandas as pd
import pytest

from dfs.research import r6_trend as T
from dfs.research.r6_features import GAME_KEY


def test_the_arrow_threshold_flags_about_fifteen_percent_of_a_continuous_change():
    rng = np.random.default_rng(0)
    change = rng.normal(0, 0.05, 20_000)
    out = T.arrow_threshold(change)
    assert out["flag_rate"] == pytest.approx(0.15, abs=0.005)
    assert out["threshold"] == pytest.approx(np.quantile(np.abs(change), 0.85), rel=0.01)
    assert (np.abs(change) >= out["threshold"]).mean() == pytest.approx(out["flag_rate"], abs=0.002)


def test_a_count_metric_moving_in_sixths_keeps_its_boundary_value():
    # 2/3 is an observed |change|; the threshold must be written so that 2/3 itself is flagged
    change = np.array([0.0] * 80 + [2 / 3] * 12 + [-2 / 3] * 3 + [1.0] * 5)
    out = T.arrow_threshold(change)
    assert out["threshold"] <= 2 / 3 and out["threshold"] > 0.6
    assert (np.abs(change) >= out["threshold"]).sum() == 20
    assert out["flag_rate"] == pytest.approx(0.20)


def test_floor_sig_rounds_toward_zero():
    assert T._floor_sig(0.66666) == 0.666
    assert T._floor_sig(2.16667) == 2.16
    assert T._floor_sig(0.0752) == 0.0752
    assert T._floor_sig(0.0) == 0.0


def test_the_closest_available_rate_wins_and_ties_go_to_fewer_arrows():
    change = np.array([0.0] * 70 + [1.0] * 15 + [2.0] * 15)  # >=1 flags 30%, >=2 flags 15%
    assert T.arrow_threshold(change)["threshold"] == 2.0
    tie = np.array([0.0] * 80 + [1.0] * 10 + [2.0] * 10)  # >=1: 20%, >=2: 10%, both 5 points from 15%
    assert T.arrow_threshold(tie)["threshold"] == 2.0


def test_describe_reports_n_sd_and_the_percentiles_of_the_absolute_change():
    change = np.array([-3.0, -1.0, 0.0, 1.0, 3.0, np.nan])
    out = T.describe(change)
    assert out["n"] == 5
    assert out["sd"] == pytest.approx(float(np.std([-3, -1, 0, 1, 3], ddof=1)), rel=0.01)
    assert out["abs_p90"] >= out["abs_p80"] > 0
    empty = T.describe(np.array([np.nan]))
    assert empty["n"] == 0 and empty["arrow_threshold"] is None


def test_bands_use_2018_to_2025_and_only_positions_the_sheet_shows_per_metric():
    rng = np.random.default_rng(1)
    rows = []
    for pid, pos in (("w1", "WR"), ("r1", "RB"), ("t1", "TE"), ("q1", "QB")):
        for season in (2016, 2017, 2018, 2019, 2024, 2025):
            for week in range(1, 18):
                rows.append((pid, season, week, pos))
    games = pd.DataFrame(rows, columns=[*GAME_KEY, "position"])
    games["team"] = "AAA"
    feats = games[[*GAME_KEY, "team"]].copy()
    for _, feature, _, _ in T.TREND_METRICS.values():
        feats[f"{feature}_chg"] = rng.normal(0, 0.1, len(feats))
    pool = games[games["season"] >= 2018]
    # a 2016 outlier must not move any 2018-2025 band
    feats.loc[feats["season"] == 2016, "tgt_share_chg"] = 1e3
    bands = T.trend_bands(games, feats, pool)
    m = bands["metrics"]["tgt_share"]
    assert bands["seasons"] == [2018, 2025] and bands["window"]["min_prior_games"] == 9
    assert set(m["positions"]) == {"RB", "WR", "TE"}  # no QB
    assert set(bands["metrics"]["carry_share"]["positions"]) == {"QB", "RB"}
    assert m["pooled"]["all"]["abs_p90"] < 1.0
    n_wr = m["positions"]["WR"]["all"]["n"]
    assert n_wr == int(((games["position"] == "WR") & (games["season"] >= 2018)).sum())
    rec = m["recommended"]
    assert set(rec["per_position"]) == {"RB", "WR", "TE"}
    assert all(0.10 < v["flag_rate"] < 0.20 for v in rec["per_position"].values())
    assert set(rec["flag_rate_at_pooled"]) == {"RB", "WR", "TE"}
