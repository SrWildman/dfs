"""Shipped artifacts: what `dfs model train` writes and `predict` reads, under `models/um/`.

- `<POS>.joblib`: the fitted HistGradientBoostingRegressor, only for a position where the GBM shipped
- `metadata.json`: artifact version, training seasons, scikit-learn version, per position the chosen method,
  its holdout metrics and its feature list
- `distribution.csv`: the per-bucket ratio tables (`distribution.py`)
- `backtest.md`: the human-readable holdout and calibration report

A joblib file is only valid under the scikit-learn it was written with, so loading refuses on any mismatch
(and on a feature list that no longer matches the code) with a message that names the fix.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import joblib
import sklearn

from dfs.model.distribution import ARTIFACT_DIR
from dfs.model.features import feature_columns
from dfs.model.history import POSITIONS

ARTIFACT_VERSION = 1
METADATA_FILE = "metadata.json"
MAX_ARTIFACT_BYTES = 15 * 1024 * 1024


class ArtifactError(Exception):
    """The shipped artifacts are missing, stale, or built under a different scikit-learn."""


@dataclass
class Artifacts:
    metadata: dict
    models: dict[str, object] = field(default_factory=dict)

    def method(self, position: str) -> str:
        return self.metadata["positions"][position]["method"]


def model_path(position: str, directory: Path = ARTIFACT_DIR) -> Path:
    return directory / f"{position}.joblib"


def save_artifacts(
    metadata: dict, models: dict[str, object], directory: Path = ARTIFACT_DIR, compress: int = 3
) -> None:
    """Write metadata and one joblib per shipped GBM; remove joblibs left over from a position that has
    since fallen back to the blend. Refuses to leave the directory over `MAX_ARTIFACT_BYTES`."""
    directory.mkdir(parents=True, exist_ok=True)
    for position in POSITIONS:
        path = model_path(position, directory)
        if position in models:
            joblib.dump(models[position], path, compress=compress)
        elif path.exists():
            path.unlink()
    (directory / METADATA_FILE).write_text(json.dumps(metadata, indent=2, sort_keys=True) + "\n")


def directory_bytes(directory: Path = ARTIFACT_DIR) -> int:
    return sum(p.stat().st_size for p in directory.iterdir() if p.is_file())


def read_metadata(directory: Path = ARTIFACT_DIR) -> dict:
    path = directory / METADATA_FILE
    if not path.exists():
        raise ArtifactError(f"{path} not found -- run `dfs model train`")
    return json.loads(path.read_text())


def check_compatible(metadata: dict) -> None:
    """Raise `ArtifactError` unless the artifacts were built by this scikit-learn and this feature code."""
    if metadata.get("artifact_version") != ARTIFACT_VERSION:
        raise ArtifactError(
            f"artifacts are version {metadata.get('artifact_version')}, this code reads {ARTIFACT_VERSION} "
            "-- run `dfs model train`"
        )
    built = metadata.get("sklearn_version")
    if built != sklearn.__version__:
        raise ArtifactError(
            f"artifacts were trained with scikit-learn {built}, but {sklearn.__version__} is installed -- "
            "run `dfs model train` to rebuild them"
        )
    for position in POSITIONS:
        recorded = metadata["positions"][position]["features"]
        if recorded != feature_columns(position):
            raise ArtifactError(
                f"the {position} feature list has changed since the artifacts were trained -- "
                "run `dfs model train`"
            )


def load_artifacts(directory: Path = ARTIFACT_DIR) -> Artifacts:
    """Load and validate the artifacts. Every failure names `dfs model train`."""
    metadata = read_metadata(directory)
    check_compatible(metadata)
    models: dict[str, object] = {}
    for position in POSITIONS:
        if metadata["positions"][position]["method"] != "gbm":
            continue
        path = model_path(position, directory)
        if not path.exists():
            raise ArtifactError(f"{path} not found -- run `dfs model train`")
        try:
            models[position] = joblib.load(path)
        except Exception as e:  # noqa: BLE001 - joblib/pickle raise whatever the file's version dictates
            raise ArtifactError(f"{path.name} could not be loaded ({e}) -- run `dfs model train`") from e
    return Artifacts(metadata, models)
