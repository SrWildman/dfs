"""Train everything and write the artifacts: the orchestration `dfs model train` and `backtest` share.

For each position: build the dataset, compare the GBM to its baselines on the holdout, pick the method by the
shipping rule, take out-of-fold predictions of that method over every training season, and build the
distribution tables and calibration checks from them. Nothing here touches a sheet or the network.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import pandas as pd
import sklearn

from dfs.model import distribution as dist
from dfs.model import evaluate
from dfs.model.artifacts import ARTIFACT_VERSION, directory_bytes, save_artifacts
from dfs.model.features import feature_columns
from dfs.model.history import POSITIONS, History
from dfs.model.train import (
    BLEND_WEIGHTS,
    HGB_PARAMS,
    HOLDOUT_YEARS,
    MIN_GAMES,
    MIN_XFP_L3,
    PositionResult,
    build_dataset,
    last_complete_season,
    oof_predictions,
    train_position,
)

# The distribution tables are checked out-of-time with this many final seasons held back.
OUT_OF_TIME_SEASONS = 3


@dataclass
class TrainingRun:
    through: int
    first_season: int
    results: dict[str, PositionResult]
    oof: dict[str, pd.DataFrame]
    tables: pd.DataFrame
    coverage: dict[str, dict]
    reliability: dict[str, pd.DataFrame]
    out_of_time: dict[str, dict]
    join_report: dict = field(default_factory=dict)

    @property
    def holdout(self) -> list[int]:
        return list(range(self.through - HOLDOUT_YEARS + 1, self.through + 1))

    @property
    def seasons(self) -> list[int]:
        return list(range(self.first_season, self.through + 1))


def run_training(
    history: History, join_report: dict | None = None, through: int | None = None, log=lambda msg: None
) -> TrainingRun:
    """Everything `dfs model train` computes, in memory."""
    through = through if through is not None else last_complete_season(history)
    first = int(history.player_games["season"].min())
    seasons = list(range(first, through + 1))
    results, oof, tables, cov, rel, oot = {}, {}, [], {}, {}, {}
    for position in POSITIONS:
        log(f"{position}: building features")
        dataset = build_dataset(history, position)
        log(f"{position}: holdout comparison and shipping decision")
        result = train_position(history, position, through, dataset)
        results[position] = result
        log(f"{position}: {result.method} ships; out-of-fold predictions")
        oof[position] = oof_predictions(dataset, position, result.method, seasons)
        pos_tables = dist.build_tables(oof[position], position)
        tables.append(pos_tables)
        cov[position] = evaluate.coverage(oof[position], position, pos_tables)
        rel[position] = evaluate.reliability(oof[position], position, pos_tables)
        oot[position] = evaluate.out_of_time(oof[position], position, through - OUT_OF_TIME_SEASONS)
    return TrainingRun(
        through, first, results, oof, pd.concat(tables, ignore_index=True), cov, rel, oot, join_report or {}
    )


def metadata_for(run: TrainingRun, trained_on: date | None = None) -> dict:
    """`metadata.json`'s content."""
    positions = {}
    for position, result in run.results.items():
        positions[position] = {
            "method": result.method,
            "features": feature_columns(position),
            "n_train_holdout_fit": result.n_train,
            "n_holdout": result.n_holdout,
            "holdout_metrics": {k: round(v, 6) for k, v in result.metrics.items()},
            "n_oof": int(len(run.oof[position])),
        }
    return {
        "artifact_version": ARTIFACT_VERSION,
        "trained_on": (trained_on or date.today()).isoformat(),
        "train_seasons": [run.first_season, run.through],
        "holdout_seasons": [run.holdout[0], run.holdout[-1]],
        "sklearn_version": sklearn.__version__,
        "hgb_params": HGB_PARAMS,
        "population": {"min_prior_games": MIN_GAMES, "min_xfp_l3": MIN_XFP_L3},
        "blend": {"offense_weights": BLEND_WEIGHTS, "dst": "trailing-8 DST points"},
        "distribution": {
            "levels": list(dist.LEVELS),
            "bucket_edges": {k: list(v) for k, v in dist.BUCKET_EDGES.items()},
            "integer_scoring": sorted(dist.INTEGER_SCORING),
            **dist.table_metadata(),
        },
        "positions": positions,
    }


def write_run(run: TrainingRun, directory: Path, backtest_md: str, trained_on: date | None = None) -> dict:
    """Write the artifacts for `run`; returns `{filename: bytes}` for what is now in `directory`."""
    models = {p: r.model for p, r in run.results.items() if r.model is not None}
    save_artifacts(metadata_for(run, trained_on), models, directory)
    dist.save_tables(run.tables, directory)
    (directory / "backtest.md").write_text(backtest_md)
    return {p.name: p.stat().st_size for p in sorted(directory.iterdir()) if p.is_file()} | {
        "TOTAL": directory_bytes(directory)
    }
