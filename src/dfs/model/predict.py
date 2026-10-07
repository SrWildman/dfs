"""Part 5: inference. `predict_slate` turns this week's slate into UM projections and an outcome spread, using
the SAME `build_features` the model was trained with, the cached history (the current season's files fetched
fresh first) and the shipped artifacts.

A row is projected only if it is inside the population the model was trained on: an offensive player with at
least one prior game and last-3 xFP >= 4 (every defense qualifies). Outside it `um_mean`, `floor` and `ceil`
are NaN -- blank, never a fabricated zero -- so the caller can fall back to its own projection.

Slate conventions (what the sheet passes in):
  - `gsis_id`: the player's gsis id; for a defense, the team code (the id is only echoed back)
  - `position`: QB / RB / WR / TE / DST
  - `team`, `opp`: nflverse team codes (LA, OAK, SD and STL are read as LAR, LV, LAC, LAR)
  - `implied`: THIS team's implied total; `spread`: this team's spread, positive when it is favoured (the
    opposite sign of a betting-line "-3"); `total`: the game total; `home`: 1 if this team is home else 0
  - for a defense these describe the defense's own team; its opponent's implied total is `total - implied`
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from dfs.model import data, distribution
from dfs.model.artifacts import Artifacts, load_artifacts
from dfs.model.features import ROW_COLUMNS, build_features, feature_columns
from dfs.model.history import POSITIONS, History, load_history
from dfs.model.train import blend_prediction, in_population

SLATE_COLUMNS = ROW_COLUMNS


def available_seasons(through: int) -> list[int]:
    """Seasons from the first through `through` that have all three per-season files cached."""
    return [
        s
        for s in range(data.FIRST_SEASON, through + 1)
        if all(data.cache_path(source, s).exists() for source in data.SEASON_SOURCES)
    ]


def slate_history(season: int, *, fetch: bool = True, log=lambda msg: None) -> History:
    """The history a slate in `season` is predicted from."""
    if fetch:
        data.refresh_season(season, log)
    seasons = available_seasons(season)
    if not seasons:
        raise data.ModelDataError("no history cached -- run `dfs model fetch`")
    return load_history(seasons)[0]


def _check_slate(players: pd.DataFrame) -> pd.DataFrame:
    missing = [c for c in SLATE_COLUMNS if c not in players.columns]
    if missing:
        raise ValueError(f"slate is missing columns: {missing}")
    bad = sorted(set(players["position"]) - set(POSITIONS))
    if bad:
        raise ValueError(f"unknown positions in slate: {bad}")
    return players


def _project(position: str, feats: pd.DataFrame, artifacts: Artifacts) -> np.ndarray:
    """UM's mean for each row (NaN outside the training population)."""
    if artifacts.method(position) == "gbm":
        pred = artifacts.models[position].predict(feats[feature_columns(position)])
    else:
        pred = blend_prediction(feats, position).to_numpy(dtype=float)
    pred = np.asarray(pred, dtype=float).copy()
    pred[~in_population(feats, position).to_numpy()] = np.nan
    return pred


def predict_slate(
    players: pd.DataFrame,
    *,
    history: History | None = None,
    artifacts: Artifacts | None = None,
    fetch: bool = True,
    log=lambda msg: None,
) -> pd.DataFrame:
    """`gsis_id, um_mean, floor, ceil` for every row of `players`, in input order and on its index.

    `history` and `artifacts` default to the cache (refreshed first unless `fetch=False`) and `models/um/`;
    pass them to predict without touching either."""
    players = _check_slate(players)
    work = players[SLATE_COLUMNS].copy()
    for col in ("team", "opp"):
        work[col] = work[col].map(data.normalize_team)
    if history is None:
        history = slate_history(int(work["season"].max()), fetch=fetch, log=log)
    artifacts = artifacts or load_artifacts()

    feats = build_features(history, work.reset_index(drop=True))
    um_mean = np.full(len(feats), np.nan)
    floor = np.full(len(feats), np.nan)
    ceil = np.full(len(feats), np.nan)
    for position in POSITIONS:
        mask = (feats["position"] == position).to_numpy()
        if not mask.any():
            continue
        pred = _project(position, feats[mask], artifacts)
        um_mean[mask] = pred
        floor[mask], ceil[mask] = distribution.floor_ceiling_many(position, pred)
    return pd.DataFrame(
        {"gsis_id": players["gsis_id"].to_numpy(), "um_mean": um_mean, "floor": floor, "ceil": ceil},
        index=players.index,
    )


def prob_at_least(positions, projections, thresholds_pts) -> np.ndarray:
    """P(actual points >= threshold) per row, from any projection (UM's, or a calibrated TFFB number) --
    vectorised over mixed positions and per-row thresholds, e.g. `3 * salary / 1000`. NaN where the
    projection or threshold is NaN."""
    pos = np.asarray(positions)
    proj = np.asarray(projections, dtype=float)
    thr = np.broadcast_to(np.asarray(thresholds_pts, dtype=float), proj.shape)
    out = np.full(proj.shape, np.nan)
    for position in POSITIONS:
        mask = pos == position
        if mask.any():
            out[mask] = distribution.prob_at_least_many(position, proj[mask], thr[mask])
    return out
