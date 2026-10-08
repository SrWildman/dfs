"""Outcome probabilities for every player, from the model package's distribution engine applied to `CalPts`
(not to UM), and UM's own projection for the slate.

Pure apart from `um_projections`, which reads the model package's cached history and shipped artifacts
(`dfs model fetch` / `models/um/`). Nothing here edits `src/dfs/model/`; it only calls it.

| Column | Definition | For |
|---|---|---|
| `Hit3x%` | P(actual >= 3 x Salary/1000), 0-100 | cash: the 3x line behind `Val >= 3`, as a probability |
| `Boom%` | P(actual >= 4 x Salary/1000), 0-100 | GPP upside |
| `Bust%` | P(actual < 2 x Salary/1000), 0-100 | cash risk |
| `Floor` | 20th percentile, points | |
| `CeilM` | 85th percentile, points | the model's ceiling, beside TFFB's `Ceiling` |

**Review notes** (planning session, 2026-10-07, on `docs/MODEL.md`'s calibration): the engine passes
coverage everywhere and reliability everywhere except the lowest projection decile for QB and DST, where
realized outcomes beat the predicted probability by about 6 points. So:

- **QB with `CalPts` under `PROB_QB_MIN_CALPTS` (10)**: every column here is blank ("not rated below 10 pts").
  Those QBs are not rostered anyway.
- **DST with `CalPts` under `PROB_DST_LOW_CONFIDENCE_BELOW` (4)**: the values are shown but `LowConf` is True
  (drawn muted); they understate upside.
- The tables were learned from UM's errors and are applied here to `CalPts`, which is built on TFFB (it ranks
  better), so the real spread may be narrower and `Boom%`/`Bust%` slightly too wide. Nothing is adjusted;
  Model Check's reliability table is the check (`results_signals.reliability_report`).

A player with no `CalPts`, or no salary, is blank: never 0.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from dfs.model import data as model_data
from dfs.model import distribution
from dfs.model.artifacts import Artifacts
from dfs.model.history import History, team_game_context
from dfs.model.predict import predict_slate, slate_history

HIT_MULTIPLE = 3.0
BOOM_MULTIPLE = 4.0
BUST_MULTIPLE = 2.0
PROB_QB_MIN_CALPTS = 10.0
PROB_DST_LOW_CONFIDENCE_BELOW = 4.0
PROB_DECIMALS = 1
PROB_COLUMNS = ["Hit3x%", "Boom%", "Bust%", "Floor", "CeilM"]
RATED_NOTE = "not rated below 10 pts"
MODEL_POSITIONS = ("QB", "RB", "WR", "TE", "DST")


def _pct(p: np.ndarray) -> np.ndarray:
    return np.round(100.0 * p, PROB_DECIMALS)


def outcome_columns(frame: pd.DataFrame) -> pd.DataFrame:
    """`Hit3x%`, `Boom%`, `Bust%`, `Floor`, `CeilM` and `LowConf`, index-aligned with `frame`
    (`Position`, `Salary`, `CalPts`). Blank (NaN) where `CalPts` or `Salary` is missing, a position is not
    one the engine knows, or the QB rule applies."""
    out = pd.DataFrame(np.nan, index=frame.index, columns=PROB_COLUMNS)
    out["LowConf"] = False
    calpts = pd.to_numeric(frame["CalPts"], errors="coerce").to_numpy(dtype=float)
    salary = pd.to_numeric(frame["Salary"], errors="coerce").to_numpy(dtype=float)
    position = frame["Position"].to_numpy()
    for pos in MODEL_POSITIONS:
        mask = (position == pos) & ~np.isnan(calpts) & ~np.isnan(salary) & (salary > 0)
        if pos == "QB":
            mask &= calpts >= PROB_QB_MIN_CALPTS
        if not mask.any():
            continue
        proj, thousands = calpts[mask], salary[mask] / 1000.0
        hit = distribution.prob_at_least_many(pos, proj, HIT_MULTIPLE * thousands)
        boom = distribution.prob_at_least_many(pos, proj, BOOM_MULTIPLE * thousands)
        bust = 1.0 - distribution.prob_at_least_many(pos, proj, BUST_MULTIPLE * thousands)
        floor, ceil = distribution.floor_ceiling_many(pos, proj)
        idx = out.index[mask]
        out.loc[idx, "Hit3x%"] = _pct(hit)
        out.loc[idx, "Boom%"] = _pct(boom)
        out.loc[idx, "Bust%"] = _pct(bust)
        out.loc[idx, "Floor"] = np.round(floor, PROB_DECIMALS)
        out.loc[idx, "CeilM"] = np.round(ceil, PROB_DECIMALS)
        if pos == "DST":
            out.loc[idx, "LowConf"] = proj < PROB_DST_LOW_CONFIDENCE_BELOW
    return out


# ---------------------------------------------------------------------------------------------
# UM, for the slate
# ---------------------------------------------------------------------------------------------


def slate_rows(frame: pd.DataFrame, games: pd.DataFrame, *, season: int, week: int) -> pd.DataFrame:
    """The model package's slate input for every row of `frame` (`Position`, `Team`, `GsisId`): `gsis_id` (the
    team code for a DST), `position`, `team`, `opp`, `season`, `week` and the Vegas context of THAT team's
    side. `games` is the model cache's schedule (`dfs.model.data.read_games`). Rows whose team has no game
    that week are dropped; the result keeps `frame`'s index."""
    sched = games[(games["season"] == season) & (games["week"] == week) & (games["game_type"] == "REG")]
    if sched.empty:
        return pd.DataFrame()
    context = team_game_context(sched).set_index("team")
    opponents = {}
    for _, g in sched.iterrows():
        home, away = model_data.normalize_team(g["home_team"]), model_data.normalize_team(g["away_team"])
        opponents[home], opponents[away] = away, home
    team = frame["Team"].map(model_data.normalize_team)
    rows = pd.DataFrame(
        {
            "gsis_id": np.where(frame["Position"] == "DST", team, frame["GsisId"]),
            "position": frame["Position"],
            "team": team,
            "opp": team.map(opponents),
            "season": season,
            "week": week,
        },
        index=frame.index,
    )
    for column in ("implied", "spread", "total", "home"):
        rows[column] = team.map(context[column])
    rows = rows[rows["opp"].notna() & rows["position"].isin(MODEL_POSITIONS)]
    return rows[rows["gsis_id"].notna() & (rows["gsis_id"] != "")]


def um_projections(
    frame: pd.DataFrame,
    *,
    season: int,
    week: int,
    history: History | None = None,
    artifacts: Artifacts | None = None,
    games: pd.DataFrame | None = None,
    fetch: bool = False,
) -> pd.Series:
    """UM's mean for every row of `frame` (NaN where the model cannot rate him: outside its training
    population, no gsis id, no game, or no cache). Uses only games before `week` by construction
    (`dfs.model.features.build_features`), so it is the same number on the day and in a backfill.
    `fetch=False` never touches the network (the history is the cache from `dfs model fetch`)."""
    out = pd.Series(np.nan, index=frame.index, dtype=float)
    games = games if games is not None else model_data.read_games()
    rows = slate_rows(frame, games, season=season, week=week)
    if rows.empty:
        return out
    if history is None:
        history = slate_history(season, fetch=fetch)
    predicted = predict_slate(rows, history=history, artifacts=artifacts, fetch=False)
    out.loc[predicted.index] = predicted["um_mean"].to_numpy()
    return out.round(2)
