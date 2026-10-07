"""Part 3: the mean model. One gradient-boosted regressor per position, judged against simple baselines on a
holdout the model never saw, and shipped only where it earns it.

- Training population: offense player-games with at least one prior game and last-3 xFP >= 4 (a player who
  is not getting real opportunities is not who a lineup is built from); DST: every team-game.
- Splits: fit on every season before the holdout, score on the last three COMPLETE seasons (2023-2025 as
  of this writing), then refit the shipped model on everything through the last complete season.
- Baselines: trailing-8 DK points, and the blend `0.5 * L8 DK + 0.5 * L3 xFP`. For DST the only baseline
  is trailing-8 DST points, and it plays the blend's role in the shipping rule.
- Shipping rule (`choose_method`): the GBM ships for a position only if its holdout MAE is <= the blend's AND
  its mean within-position-week Spearman rho is >= the blend's. Otherwise the blend formula ships. Data
  decides, and the result is recorded in the artifacts' metadata.

Hyper-parameters are the five the task fixed; everything else is scikit-learn's default (including its
automatic early stopping on large frames), chosen up front and not tuned on the holdout.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor

from dfs.model.features import ROW_COLUMNS, build_features, feature_columns
from dfs.model.history import History

HGB_PARAMS = {
    "max_iter": 400,
    "learning_rate": 0.05,
    "max_leaf_nodes": 31,
    "min_samples_leaf": 40,
    "l2_regularization": 1.0,
    "random_state": 0,
}
MIN_XFP_L3 = 4.0
MIN_GAMES = 1
HOLDOUT_YEARS = 3
BLEND_WEIGHTS = {"dk_l8": 0.5, "xfp_l3": 0.5}
MIN_WEEK_ROWS = 8  # a position-week needs this many rows to have a meaningful rank correlation

ID_COLUMNS = ["gsis_id", "season", "week"]


def blend_prediction(features: pd.DataFrame, position: str) -> pd.Series:
    """The fallback formula. Offense: `0.5 * L8 DK + 0.5 * L3 xFP`; DST: the trailing-8 DST points."""
    if position == "DST":
        return features["def_dk"].astype(float)
    return sum(w * features[col] for col, w in BLEND_WEIGHTS.items())


def trailing8_prediction(features: pd.DataFrame, position: str) -> pd.Series:
    return features["def_dk" if position == "DST" else "dk_l8"].astype(float)


def in_population(features: pd.DataFrame, position: str) -> pd.Series:
    """Rows the model is trained on, and the only rows it will project."""
    if position == "DST":
        return pd.Series(True, index=features.index)
    return (features["games_l8"] >= MIN_GAMES) & (features["xfp_l3"] >= MIN_XFP_L3)


def build_dataset(history: History, position: str) -> pd.DataFrame:
    """Every historical game of `position` with its features (prior games only), the actual DK points
    (`dk`), and the population flag. One row per player-game; nothing is dropped here."""
    table = history.team_games if position == "DST" else history.player_games
    table = table[table["position"] == position]
    feats = build_features(history, table[ROW_COLUMNS].reset_index(drop=True))
    feats["dk"] = table["dk"].to_numpy()
    feats["in_pop"] = in_population(feats, position).to_numpy()
    return feats


def fit_gbm(train: pd.DataFrame, position: str) -> HistGradientBoostingRegressor:
    cols = feature_columns(position)
    model = HistGradientBoostingRegressor(**HGB_PARAMS)
    model.fit(train[cols], train["dk"])
    return model


def mean_weekly_spearman(df: pd.DataFrame, pred_col: str) -> float:
    """Mean over position-weeks of the Spearman rank correlation between prediction and actual points."""
    rhos = []
    for _, g in df.groupby(["season", "week"]):
        if len(g) >= MIN_WEEK_ROWS and g[pred_col].nunique() > 1 and g["dk"].nunique() > 1:
            rhos.append(g[pred_col].corr(g["dk"], method="spearman"))
    return float(np.mean(rhos)) if rhos else float("nan")


def mae(df: pd.DataFrame, pred_col: str) -> float:
    return float((df[pred_col] - df["dk"]).abs().mean())


def choose_method(gbm_mae: float, blend_mae: float, gbm_rho: float, blend_rho: float) -> str:
    """ "gbm" only if it is at least as accurate (MAE <=) AND ranks at least as well (rho >=) as the blend;
    anything else -- including a NaN -- ships the blend."""
    if gbm_mae <= blend_mae and gbm_rho >= blend_rho:
        return "gbm"
    return "blend"


@dataclass
class PositionResult:
    position: str
    method: str
    n_train: int
    n_holdout: int
    metrics: dict[str, float]
    model: HistGradientBoostingRegressor | None = None
    params: dict = field(default_factory=dict)


def holdout_metrics(dataset: pd.DataFrame, position: str, holdout: list[int]) -> tuple[dict, int, int]:
    """Fit on seasons before the holdout, score the holdout. Returns the metrics, train rows, holdout rows."""
    pop = dataset[dataset["in_pop"]]
    train = pop[pop["season"] < min(holdout)]
    test = pop[pop["season"].isin(holdout)].copy()
    model = fit_gbm(train, position)
    cols = feature_columns(position)
    test["gbm"] = model.predict(test[cols])
    test["l8"] = trailing8_prediction(test, position)
    test["blend"] = blend_prediction(test, position)
    test = test.dropna(subset=["l8", "blend"])
    metrics = {}
    for name in ("l8", "blend", "gbm"):
        metrics[f"mae_{name}"] = mae(test, name)
        metrics[f"rho_{name}"] = mean_weekly_spearman(test, name)
    return metrics, len(train), len(test)


def train_position(
    history: History, position: str, through: int, dataset: pd.DataFrame | None = None
) -> PositionResult:
    """Holdout comparison, the shipping decision, then the shipped model refit on everything through
    `through` (only if the GBM ships)."""
    ds = dataset if dataset is not None else build_dataset(history, position)
    holdout = list(range(through - HOLDOUT_YEARS + 1, through + 1))
    metrics, n_train, n_holdout = holdout_metrics(ds, position, holdout)
    method = choose_method(metrics["mae_gbm"], metrics["mae_blend"], metrics["rho_gbm"], metrics["rho_blend"])
    model = None
    if method == "gbm":
        pop = ds[ds["in_pop"] & (ds["season"] <= through)]
        model = fit_gbm(pop, position)
    return PositionResult(position, method, n_train, n_holdout, metrics, model, dict(HGB_PARAMS))


def oof_predictions(dataset: pd.DataFrame, position: str, method: str, seasons: list[int]) -> pd.DataFrame:
    """Out-of-fold predictions of the SHIPPED method over `seasons`, for the distribution engine: for the
    GBM each season is predicted by a model fit on all the other seasons; the blend needs no fit. Only
    in-population rows; columns `season, week, gsis_id, pred, dk`."""
    pop = dataset[dataset["in_pop"] & dataset["season"].isin(seasons)].copy()
    cols = feature_columns(position)
    if method == "blend":
        pop["pred"] = blend_prediction(pop, position)
    else:
        pop["pred"] = np.nan
        for season in seasons:
            held = pop["season"] == season
            model = fit_gbm(pop[~held], position)
            pop.loc[held, "pred"] = model.predict(pop.loc[held, cols])
    pop = pop.dropna(subset=["pred"])
    return pop[[*ID_COLUMNS, "pred", "dk"]].reset_index(drop=True)


def last_complete_season(history: History) -> int:
    """The most recent season with a full regular season in the history (17+ distinct weeks)."""
    weeks = history.player_games.groupby("season")["week"].nunique()
    complete = weeks[weeks >= 17]
    if complete.empty:
        raise ValueError("no complete season in the history")
    return int(complete.index.max())
