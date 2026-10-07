"""The baseline every study measures against: UM out-of-fold predictions, built exactly as `dfs.model` builds
them (`build_dataset` features from prior games only; `oof_predictions` of the SHIPPED method for each
position, each season predicted by a model fit on the other seasons), joined back onto every player-game.

One row per player-game (offense) or team-game (DST), 2014-2025:

- `um`        the out-of-fold UM projection (NaN outside UM's population: offense with >= 1 prior game and
              last-3 xFP >= 4; every DST team-game is in it)
- `resid`     `dk - um`; `resid_l8` is `dk - trailing-8` (the raw version: the total matchup / context effect)
- everything `dfs.model` computed as a feature (dk_l3, xfp_l3, target_share_l3, implied, ...), `game_id`,
  `name`, and the actual DK points `dk`.

The out-of-fold fits for the GBM positions (WR, DST) leave one season out of ALL twelve, so a 2022 projection
comes from a model that saw 2014-2021 and 2023-2025. That is how `dfs.model` builds its distribution tables
and is what the task specifies; it can only make the UM baseline slightly stronger on the test seasons, so
residual findings there are conservative. The research fits themselves (thresholds, ridge weights, tables)
only ever touch 2014-2021.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from dfs.model import artifacts
from dfs.model.data import ModelDataError
from dfs.model.features import ROW_COLUMNS
from dfs.model.history import POSITIONS, History, load_history
from dfs.model.train import build_dataset, oof_predictions
from dfs.research import data
from dfs.research.common import SEASONS

BASELINE_FILE = "baseline.parquet"


def shipped_methods() -> dict[str, str]:
    """Which method (`gbm` or `blend`) UM ships for each position, read from `models/um/metadata.json`."""
    meta = artifacts.read_metadata()
    return {p: meta["positions"][p]["method"] for p in POSITIONS}


def position_frame(history: History, position: str, method: str, seasons: list[int]) -> pd.DataFrame:
    """Every game of `position` with UM's features, actual points and out-of-fold projection."""
    table = history.team_games if position == "DST" else history.player_games
    table = table[table["position"] == position].reset_index(drop=True)
    ds = build_dataset(history, position)
    if len(ds) != len(table) or not (ds["gsis_id"].to_numpy() == table["gsis_id"].to_numpy()).all():
        raise RuntimeError(f"{position}: dataset rows no longer line up with the history table")
    ds["game_id"] = table["game_id"].to_numpy()
    ds["name"] = table["name"].to_numpy() if "name" in table else table["team"].to_numpy()
    oof = oof_predictions(ds, position, method, seasons).rename(columns={"pred": "um"})
    out = ds.merge(oof[["gsis_id", "season", "week", "um"]], on=["gsis_id", "season", "week"], how="left")
    baseline_col = "def_dk" if position == "DST" else "dk_l8"
    out["l8"] = out[baseline_col]
    return out


def build_baseline(history: History, methods: dict[str, str] | None = None, seasons=None) -> pd.DataFrame:
    methods = methods or shipped_methods()
    seasons = seasons or SEASONS
    frames = [position_frame(history, p, methods[p], seasons) for p in POSITIONS]
    base = pd.concat(frames, ignore_index=True)
    base = base[base["season"].isin(seasons)]
    base["resid"] = base["dk"] - base["um"]
    base["resid_l8"] = base["dk"] - base["l8"]
    keep_first = [*ROW_COLUMNS, "name", "game_id", "t", "dk", "um", "l8", "resid", "resid_l8", "in_pop"]
    rest = [c for c in base.columns if c not in keep_first]
    return base[[*keep_first, *rest]].sort_values(["position", "t", "gsis_id"]).reset_index(drop=True)


def game_tables(history: History) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Two game-level tables the matchup study rolls up, one row per team-game:

    - `pos_game`: DK points each offense produced at each position -- (team, opp, position, season, week,
      game_id, t, dk). Read from the defense's side these are the points it ALLOWED to the position.
    - `team_game`: the DST-side counting stats -- (team, opp, season, week, game_id, t, dk, sacks_allowed,
      giveaways, dst_conceded, implied, total) -- where `dk` is the defense's own DST score and the other
      three describe the OFFENSE of `team` (what it gives away to the opposing defense)."""
    pos_game = (
        history.player_games.groupby(
            ["team", "opp", "position", "season", "week", "game_id", "t"], as_index=False
        )["dk"]
        .sum()
        .sort_values(["team", "position", "t"])
        .reset_index(drop=True)
    )
    cols = ["team", "opp", "season", "week", "game_id", "t", "dk", "sacks_allowed", "giveaways"]
    team_game = history.team_games[[*cols, "dst_conceded", "implied", "total"]].reset_index(drop=True)
    return pos_game, team_game


def _cache_paths() -> dict[str, Path]:
    return {n: data.CACHE_DIR / f"{n}.parquet" for n in ("baseline", "pos_game", "team_game")}


def ensure_cache(refresh: bool = False, log=lambda msg: None) -> None:
    """Build the baseline and the two game tables (about a minute, one history load) unless cached. Delete
    the cache or pass `refresh=True` after `dfs model train` changes which method ships."""
    paths = _cache_paths()
    if not refresh and all(p.exists() for p in paths.values()):
        return
    log("building the UM out-of-fold baseline (loads 12 seasons, fits the WR and DST GBMs 12 times)")
    try:
        history, _ = load_history(SEASONS)
    except ModelDataError as e:
        raise data.ResearchDataError(str(e).replace("dfs model fetch", "dfs research fetch")) from e
    frames = {"baseline": build_baseline(history)}
    frames["pos_game"], frames["team_game"] = game_tables(history)
    data.CACHE_DIR.mkdir(parents=True, exist_ok=True)
    for name, frame in frames.items():
        frame.to_parquet(paths[name], index=False)


def load_baseline(refresh: bool = False, log=lambda msg: None) -> pd.DataFrame:
    """The cached baseline frame (see the module docstring)."""
    ensure_cache(refresh, log)
    return pd.read_parquet(_cache_paths()["baseline"])


def load_game_tables(refresh: bool = False, log=lambda msg: None) -> tuple[pd.DataFrame, pd.DataFrame]:
    """The cached `(pos_game, team_game)` tables."""
    ensure_cache(refresh, log)
    paths = _cache_paths()
    return pd.read_parquet(paths["pos_game"]), pd.read_parquet(paths["team_game"])
