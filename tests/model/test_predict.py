"""Inference: the slate in, UM projections and a spread out -- through the same features as training."""

import numpy as np
import pandas as pd
import pytest
from sklearn.ensemble import HistGradientBoostingRegressor

from dfs.model import data, distribution
from dfs.model import predict as P
from dfs.model.artifacts import Artifacts
from dfs.model.features import ROW_COLUMNS, build_features, feature_columns
from dfs.model.history import POSITIONS
from dfs.model.train import blend_prediction, in_population

T = 202406


def _blend_artifacts() -> Artifacts:
    meta = {"positions": {p: {"method": "blend"} for p in POSITIONS}}
    return Artifacts(meta, {})


def _slate(history, t=T, with_rookie=False) -> pd.DataFrame:
    pg = history.player_games[history.player_games["t"] == t]
    tg = history.team_games[history.team_games["t"] == t]
    rows = pd.concat([pg[ROW_COLUMNS], tg[ROW_COLUMNS]], ignore_index=True)
    if not with_rookie:
        rows = rows[rows["gsis_id"] != "ROOKIE-WR"]
    return rows.reset_index(drop=True)


def test_blend_projection_matches_the_formula_and_ignores_the_slate_weeks_own_outcome(history):
    slate = _slate(history)
    out = P.predict_slate(slate, history=history, artifacts=_blend_artifacts())
    assert list(out.columns) == ["gsis_id", "um_mean", "floor", "ceil"]
    assert out["gsis_id"].tolist() == slate["gsis_id"].tolist()
    feats = build_features(history, slate)
    offense = feats["position"] != "DST"
    expected = blend_prediction(feats[offense], "RB")  # the formula is position-independent for offense
    assert np.allclose(out.loc[offense.to_numpy(), "um_mean"], expected)
    dst = ~offense
    assert np.allclose(out.loc[dst.to_numpy(), "um_mean"], feats.loc[dst, "def_dk"])


def test_floor_is_below_the_mean_and_ceiling_above(history):
    out = P.predict_slate(_slate(history), history=history, artifacts=_blend_artifacts())
    live = out[(out["um_mean"] > 0)]
    assert len(live) > 0
    assert (live["floor"] < live["um_mean"]).all() and (live["um_mean"] < live["ceil"]).all()


def test_a_player_outside_the_training_population_is_blank_not_zero(history):
    slate = _slate(history, with_rookie=True)
    low = history.__class__(history.player_games.copy(), history.team_games, history.def_vs_pos)
    low.player_games.loc[low.player_games["gsis_id"] == "AAA-TE0", "xfp"] = 0.5  # no real opportunity
    # the rookie row is in the history at T (his debut); predict from before it, so he has no prior games
    out = P.predict_slate(slate, history=low.before(T), artifacts=_blend_artifacts()).set_index("gsis_id")
    for pid in ("ROOKIE-WR", "AAA-TE0"):
        assert out.loc[pid, ["um_mean", "floor", "ceil"]].isna().all()
    assert out.loc["AAA-QB0", "um_mean"] > 0
    # a defense is always projected
    assert out.loc["AAA", "um_mean"] == out.loc["AAA", "um_mean"]


def test_the_gbm_path_uses_the_shipped_model_on_the_same_features(history):
    ds_rows = history.player_games[history.player_games["position"] == "WR"]
    feats = build_features(history, ds_rows[ROW_COLUMNS])
    pop = feats[in_population(feats, "WR")]
    target = ds_rows["dk"].to_numpy()[pop.index]
    model = HistGradientBoostingRegressor(max_iter=30, random_state=0).fit(pop[feature_columns("WR")], target)
    arts = Artifacts(
        {"positions": {p: {"method": "blend"} for p in POSITIONS} | {"WR": {"method": "gbm"}}}, {"WR": model}
    )
    slate = _slate(history)
    out = P.predict_slate(slate, history=history, artifacts=arts)
    wr = slate["position"] == "WR"
    feats_t = build_features(history, slate[wr])
    expected = model.predict(feats_t[feature_columns("WR")])
    gated = in_population(feats_t, "WR").to_numpy()
    got = out.loc[wr, "um_mean"].to_numpy()
    assert np.allclose(got[gated], expected[gated]) and np.isnan(got[~gated]).all()
    assert (
        out.loc[~wr & (slate["position"] != "DST"), "um_mean"].dropna() > 0
    ).all()  # others still use the blend


def test_output_is_aligned_to_the_input_index(history):
    slate = _slate(history)
    slate.index = slate.index + 100
    out = P.predict_slate(slate.iloc[::-1], history=history, artifacts=_blend_artifacts())
    assert out.index.tolist() == slate.iloc[::-1].index.tolist()
    assert out["gsis_id"].tolist() == slate.iloc[::-1]["gsis_id"].tolist()


def test_a_bad_slate_is_rejected(history):
    slate = _slate(history)
    with pytest.raises(ValueError, match="missing columns"):
        P.predict_slate(slate.drop(columns=["opp"]), history=history, artifacts=_blend_artifacts())
    bad = slate.copy()
    bad.loc[0, "position"] = "K"
    with pytest.raises(ValueError, match="unknown positions"):
        P.predict_slate(bad, history=history, artifacts=_blend_artifacts())


def test_vectorised_prob_at_least_handles_mixed_positions_and_per_row_thresholds():
    positions = ["QB", "RB", "WR", "TE", "DST", "WR"]
    proj = [20.0, 12.0, 9.0, 7.0, 6.0, np.nan]
    thr = [3 * 6.2, 3 * 5.0, 4 * 4.1, 2 * 3.5, 3 * 2.5, 10.0]
    got = P.prob_at_least(positions, proj, thr)
    for i in range(5):
        assert got[i] == pytest.approx(distribution.prob_at_least(positions[i], proj[i], thr[i]))
    assert np.isnan(got[5])
    assert ((got[:5] > 0) & (got[:5] < 1)).all()


def test_slate_history_uses_only_cached_seasons_and_refresh_is_optional(history, monkeypatch):
    monkeypatch.setattr(P, "available_seasons", lambda through: [])
    with pytest.raises(data.ModelDataError, match="dfs model fetch"):
        P.slate_history(2026, fetch=False)
    refreshed = []
    monkeypatch.setattr(data, "refresh_season", lambda season, log=None: refreshed.append(season))
    with pytest.raises(data.ModelDataError):
        P.slate_history(2026, fetch=True)
    assert refreshed == [2026]
