"""R2: components are prior-only; the ridge is validated by season, never by random rows."""

import numpy as np
import pandas as pd
import pytest

from dfs.research import r2_matchup as r2
from dfs.research.common import FIT_SEASONS, TEST_SEASONS
from dfs.research.rolling import LOOKBACKS


def _components(feats: pd.DataFrame) -> list[str]:
    return sorted(c for c in feats.columns if "__" in c)


def _sorted(f: pd.DataFrame) -> pd.DataFrame:
    return f.sort_values(["position", "gsis_id", "t"]).reset_index(drop=True)


def test_components_use_prior_games_only(matchup_tables):
    base, pos_game, team_game, off_game = matchup_tables
    original = _sorted(r2.matchup_features(base, pos_game, team_game, off_game))

    def perturb(frame: pd.DataFrame, cols: list[str]) -> pd.DataFrame:
        out = frame.copy()
        hit = (out["season"] == 2022) & (out["week"] == 6)
        out.loc[hit, cols] = out.loc[hit, cols] * 50 + 7
        return out

    changed = _sorted(
        r2.matchup_features(
            base,
            perturb(pos_game, ["dk"]),
            perturb(team_game, ["dk", "sacks_allowed", "giveaways", "dst_conceded"]),
            perturb(off_game, ["plays", "epa_pass", "epa_rush", "proe"]),
        )
    )
    cols = _components(original)
    assert cols
    upto = original["t"] <= 202206  # the perturbed game itself and everything before it
    pd.testing.assert_frame_equal(original.loc[upto, cols], changed.loc[upto, cols])
    after = original["t"] == 202207  # the next game does see it
    assert not original.loc[after, cols].equals(changed.loc[after, cols])


def test_every_variant_column_exists_for_every_position(matchup_tables):
    feats = r2.matchup_features(*matchup_tables)
    for position in r2.POSITIONS:
        for lb in LOOKBACKS:
            for adjusted in (False, True):
                cols = [r2.column_name(c, lb, adjusted) for c in r2.components_for(position)]
                assert set(cols) <= set(feats.columns), (position, lb, adjusted)


def test_schedule_adjustment_subtracts_the_offenses_own_norm(matchup_tables):
    base, pos_game, *_ = matchup_tables
    table = r2.defense_dk_table(pos_game)
    both = table[["def_dk__raw__l8", "def_dk__adj__l8"]].dropna()  # the adjusted form needs a norm first
    assert len(both) > 0
    assert not np.allclose(both["def_dk__raw__l8"], both["def_dk__adj__l8"])


def test_season_folds_hold_out_whole_seasons():
    seasons = np.array([2014] * 5 + [2015] * 4 + [2016] * 6 + [2017] * 3)
    folds = r2.season_folds(seasons)
    assert len(folds) == 4
    held_all = np.concatenate([held for _, held in folds])
    assert sorted(held_all.tolist()) == list(range(len(seasons)))  # every row held out exactly once
    for train, held in folds:
        held_seasons = set(seasons[held])
        assert len(held_seasons) == 1  # one season per fold
        assert not held_seasons & set(seasons[train])  # and never in its own training set


def test_cv_is_by_season_not_by_random_row():
    """Each season has its own offset and its own indicator column. A random-row split would learn the
    offsets from the indicators; a by-season split cannot, so its error must be much larger."""
    rng = np.random.default_rng(0)
    n_seasons, per = 6, 60
    seasons = np.repeat(np.arange(2014, 2014 + n_seasons), per)
    offsets = rng.normal(0, 5, n_seasons)
    x = (seasons[:, None] == np.arange(2014, 2014 + n_seasons)[None, :]).astype(float)
    y = offsets[seasons - 2014] + rng.normal(0, 0.5, len(seasons))
    by_season = r2.cv_mae(x, y, seasons, alpha=0.1)
    shuffled = rng.permutation(len(seasons)) % 6 + 2014  # pseudo-seasons that cut across real ones
    by_row = r2.cv_mae(x, y, shuffled, alpha=0.1)
    assert by_season > 3 * by_row
    assert by_season > 1.0


def test_ridge_recovers_known_weights_and_handles_missing_components():
    rng = np.random.default_rng(1)
    x = rng.normal(5, 3, size=(4000, 3))
    y = 2.0 * (x[:, 0] - 5) / 3 - 1.0 * (x[:, 2] - 5) / 3 + 0.7 + rng.normal(0, 0.2, 4000)
    fit = r2.fit_ridge(x, y, alpha=1e-3)
    assert fit.weights == pytest.approx([2.0, 0.0, -1.0], abs=0.05)  # points per 1 SD
    assert fit.intercept == pytest.approx(y.mean())
    x_missing = x[:5].copy()
    x_missing[:, 0] = np.nan  # a missing component counts as average (zero after standardizing)
    pred = fit.predict(x_missing)
    assert np.isfinite(pred).all()


def test_heavy_shrinkage_means_no_effect():
    rng = np.random.default_rng(2)
    x = rng.normal(size=(500, 4))
    y = rng.normal(size=500) * 3
    fit = r2.fit_ridge(x, y, alpha=1e9)
    assert np.abs(fit.weights).max() < 1e-4


def test_study_runs_on_pure_noise_and_calls_it_context(matchup_tables):
    feats = r2.matchup_features(*matchup_tables)
    result = r2.run_study(feats)
    assert set(result["positions"]) == set(r2.POSITIONS)
    for position, targets in result["positions"].items():
        assert set(targets) == {"vs_um", "vs_trailing8"}
        res = targets["vs_um"]
        assert set(res["chosen"]["weights_per_sd"]) == set(r2.components_for(position))
        assert res["chosen"]["lookback"] in LOOKBACKS
        assert res["n_fit"] > 0 and res["n_test"] > 0
        assert res["context_not_edge"]  # noise cannot beat 0.2 points
        assert len(res["variants"]) == 6  # 3 lookbacks x raw / schedule-adjusted


def test_the_study_fits_only_on_fit_seasons(matchup_tables):
    feats = r2.matchup_features(*matchup_tables)
    res = r2.study_position(feats, "WR", "resid")
    fit_rows = feats[(feats["position"] == "WR") & feats["season"].isin(FIT_SEASONS)]
    test_rows = feats[(feats["position"] == "WR") & feats["season"].isin(TEST_SEASONS)]
    assert res["n_fit"] == fit_rows["um"].notna().sum()
    assert res["n_test"] == test_rows["um"].notna().sum()


def test_holdout_scores_arithmetic():
    y = np.array([1.0, -1.0, 2.0, -2.0])
    pred = np.array([1.0, -1.0, 2.0, -2.0])  # a perfect model
    out = r2.holdout_scores(y, pred, intercept=0.0, game=np.array(["a", "a", "b", "b"]))
    assert out["mae_model"] == 0.0 and out["mae_baseline"] == 1.5
    assert out["mae_gain_bias_corrected"] == pytest.approx(1.5)
    assert out["r2_gain"] == pytest.approx(1.0)
