"""Training logic: the shipping rule, baselines, the population gate, holdout and out-of-fold mechanics."""

import numpy as np
import pandas as pd
import pytest

from dfs.model import train as T


@pytest.mark.parametrize(
    "gbm_mae, blend_mae, gbm_rho, blend_rho, expected",
    [
        (5.7, 5.8, 0.50, 0.49, "gbm"),  # better on both
        (5.8, 5.8, 0.49, 0.49, "gbm"),  # ties go to the GBM: the rule is <= and >=
        (5.9, 5.8, 0.50, 0.49, "blend"),  # worse MAE
        (5.7, 5.8, 0.48, 0.49, "blend"),  # worse rank correlation
        (5.9, 5.8, 0.48, 0.49, "blend"),  # worse on both
        (float("nan"), 5.8, 0.50, 0.49, "blend"),  # an undefined metric never ships the GBM
        (5.7, 5.8, float("nan"), 0.49, "blend"),
    ],
)
def test_the_gbm_ships_only_if_it_matches_the_blend_on_both_measures(
    gbm_mae, blend_mae, gbm_rho, blend_rho, expected
):
    assert T.choose_method(gbm_mae, blend_mae, gbm_rho, blend_rho) == expected


def test_blend_and_trailing_baselines():
    f = pd.DataFrame({"dk_l8": [10.0, 20.0], "xfp_l3": [14.0, 8.0], "def_dk": [5.0, 7.0]})
    assert T.blend_prediction(f, "WR").tolist() == [12.0, 14.0]
    assert T.trailing8_prediction(f, "WR").tolist() == [10.0, 20.0]
    # DST has one baseline -- trailing-8 DST points -- and it is its "blend" too
    assert T.blend_prediction(f, "DST").tolist() == [5.0, 7.0]
    assert T.trailing8_prediction(f, "DST").tolist() == [5.0, 7.0]


def test_population_gate_needs_a_prior_game_and_real_opportunity_but_dst_takes_everyone():
    f = pd.DataFrame({"games_l8": [0, 3, 3, 8], "xfp_l3": [9.0, 3.9, 4.0, np.nan]})
    assert T.in_population(f, "RB").tolist() == [False, False, True, False]
    assert T.in_population(f, "DST").all()


def test_weekly_spearman_averages_over_position_weeks_and_skips_thin_ones():
    rows = []
    for week, order in ((1, range(10)), (2, range(10)), (3, range(3))):
        for i in order:
            rows.append({"season": 2024, "week": week, "pred": float(i), "dk": float(i * 2)})
    perfect = pd.DataFrame(rows)
    assert T.mean_weekly_spearman(perfect, "pred") == pytest.approx(1.0)  # week 3 (3 rows) is skipped
    reversed_ = perfect.assign(pred=-perfect["pred"])
    assert T.mean_weekly_spearman(reversed_, "pred") == pytest.approx(-1.0)
    assert np.isnan(T.mean_weekly_spearman(perfect.iloc[-3:], "pred"))


def test_mae():
    df = pd.DataFrame({"p": [1.0, 5.0], "dk": [3.0, 4.0]})
    assert T.mae(df, "p") == pytest.approx(1.5)


@pytest.fixture
def one_year_holdout(monkeypatch):
    monkeypatch.setattr(T, "HOLDOUT_YEARS", 1)


def test_train_position_ships_the_method_the_rule_picks(history, one_year_holdout):
    result = T.train_position(history, "WR", through=2024)
    m = result.metrics
    assert result.method == T.choose_method(m["mae_gbm"], m["mae_blend"], m["rho_gbm"], m["rho_blend"])
    assert (result.model is not None) == (result.method == "gbm")
    assert set(m) == {f"{k}_{b}" for k in ("mae", "rho") for b in ("l8", "blend", "gbm")}
    assert result.n_holdout > 0 and result.n_train > 0


def test_a_worse_gbm_falls_back_to_the_blend_and_ships_no_model(history, one_year_holdout, monkeypatch):
    worse = {
        "mae_l8": 5.0,
        "mae_blend": 4.0,
        "mae_gbm": 4.5,
        "rho_l8": 0.3,
        "rho_blend": 0.4,
        "rho_gbm": 0.45,
    }
    monkeypatch.setattr(T, "holdout_metrics", lambda *a, **k: (worse, 10, 5))
    result = T.train_position(history, "RB", through=2024)
    assert result.method == "blend" and result.model is None


def test_a_better_gbm_ships_a_model_refit_on_every_season_through_the_last(
    history, one_year_holdout, monkeypatch
):
    better = {
        "mae_l8": 5.0,
        "mae_blend": 4.0,
        "mae_gbm": 3.9,
        "rho_l8": 0.3,
        "rho_blend": 0.4,
        "rho_gbm": 0.45,
    }
    monkeypatch.setattr(T, "holdout_metrics", lambda *a, **k: (better, 10, 5))
    seen = []
    real_fit = T.fit_gbm
    monkeypatch.setattr(
        T,
        "fit_gbm",
        lambda train, pos: (seen.append(sorted(train["season"].unique())), real_fit(train, pos))[1],
    )
    result = T.train_position(history, "WR", through=2024)
    assert result.method == "gbm" and result.model is not None
    assert seen == [[2023, 2024]]  # the shipped refit uses the holdout seasons too


def test_out_of_fold_predictions_never_train_on_the_season_they_predict(history, monkeypatch):
    ds = T.build_dataset(history, "WR")
    fitted_on = []
    real_fit = T.fit_gbm

    def spy(train, position):
        fitted_on.append(set(train["season"]))
        return real_fit(train, position)

    monkeypatch.setattr(T, "fit_gbm", spy)
    oof = T.oof_predictions(ds, "WR", "gbm", [2023, 2024])
    assert fitted_on == [{2024}, {2023}]  # predicting 2023 used only 2024, and vice versa
    assert len(oof) == int(ds["in_pop"].sum())
    assert np.isfinite(oof["pred"]).all()
    assert list(oof.columns) == ["gsis_id", "season", "week", "pred", "dk"]


def test_out_of_fold_blend_needs_no_fit_and_covers_only_the_population(history, monkeypatch):
    monkeypatch.setattr(T, "fit_gbm", lambda *a: pytest.fail("the blend must not fit a model"))
    ds = T.build_dataset(history, "RB")
    oof = T.oof_predictions(ds, "RB", "blend", [2023, 2024])
    pop = ds[ds["in_pop"]]
    assert len(oof) == len(pop)
    assert np.allclose(oof["pred"], (0.5 * pop["dk_l8"] + 0.5 * pop["xfp_l3"]).to_numpy())


def test_dataset_has_the_actual_points_and_the_population_flag(history):
    ds = T.build_dataset(history, "QB")
    assert len(ds) == int((history.player_games["position"] == "QB").sum())
    assert {"dk", "in_pop", "dk_l8", "xfp_l3"} <= set(ds.columns)
    first = ds[ds["games_l8"] == 0]
    assert len(first) > 0 and not first["in_pop"].any()


def test_last_complete_season_ignores_a_season_in_progress(history):
    with pytest.raises(ValueError, match="no complete season"):
        T.last_complete_season(history)  # the synthetic seasons have 6 weeks each
    full = pd.DataFrame({"season": [2023] * 17 + [2024] * 4, "week": [*range(1, 18), *range(1, 5)]})
    assert T.last_complete_season(history.__class__(full, full, full)) == 2023
