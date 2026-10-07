"""Build the frame the correlations are fitted on, from the cached history: the out-of-fold UM predictions
of the shipped method (the training pipeline's own `oof_predictions`), joined to roles and game context.

No network, no sheet. `dfs model fetch` must have filled the cache.
"""

from __future__ import annotations

import pandas as pd

from dfs.model import data
from dfs.model.artifacts import read_metadata
from dfs.model.history import POSITIONS, load_history
from dfs.model.predict import available_seasons
from dfs.model.train import build_dataset, oof_predictions
from dfs.sim.roles import build_frame

FIRST_SEASON = data.FIRST_SEASON


def last_trained_season() -> int:
    """The last season the shipped UM artifacts were trained through (the last complete season)."""
    return int(read_metadata()["train_seasons"][1])


def load_frame(through: int | None = None, log=lambda msg: None) -> pd.DataFrame:
    """Every out-of-fold player-game from `FIRST_SEASON` through `through` (default: the shipped model's last
    season) with its role: `season, week, game_id, team, opp, position, gsis_id, name, role, pred, dk,
    total`."""
    through = through if through is not None else last_trained_season()
    seasons = available_seasons(through)
    if seasons != list(range(FIRST_SEASON, through + 1)):
        raise data.ModelDataError(
            f"history is not cached for {FIRST_SEASON}-{through} -- run `dfs model fetch`"
        )
    log(f"building history {FIRST_SEASON}-{through}")
    history, _ = load_history(seasons)
    methods = {p: info["method"] for p, info in read_metadata()["positions"].items()}
    oof = {}
    for position in POSITIONS:
        log(f"{position}: out-of-fold predictions ({methods[position]})")
        oof[position] = oof_predictions(
            build_dataset(history, position), position, methods[position], seasons
        )
    return build_frame(history, oof)
