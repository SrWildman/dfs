"""Shipped artifacts: a scikit-learn mismatch fails loudly with the fix; the committed ones must load."""

import json

import numpy as np
import pytest
import sklearn
from sklearn.ensemble import HistGradientBoostingRegressor

from dfs.model import artifacts as A
from dfs.model.features import feature_columns
from dfs.model.history import POSITIONS


def _metadata(methods: dict[str, str] | None = None, **overrides) -> dict:
    methods = methods or dict.fromkeys(POSITIONS, "blend")
    meta = {
        "artifact_version": A.ARTIFACT_VERSION,
        "sklearn_version": sklearn.__version__,
        "positions": {p: {"method": methods[p], "features": feature_columns(p)} for p in POSITIONS},
    }
    meta.update(overrides)
    return meta


def _write(directory, meta, models=None):
    A.save_artifacts(meta, models or {}, directory)


def test_a_scikit_learn_version_mismatch_says_to_retrain(tmp_path):
    _write(tmp_path, _metadata(sklearn_version="0.0.1"))
    with pytest.raises(A.ArtifactError) as e:
        A.load_artifacts(tmp_path)
    message = str(e.value)
    assert "0.0.1" in message and sklearn.__version__ in message
    assert "dfs model train" in message


def test_an_artifact_version_mismatch_says_to_retrain(tmp_path):
    _write(tmp_path, _metadata(artifact_version=A.ARTIFACT_VERSION + 1))
    with pytest.raises(A.ArtifactError, match="dfs model train"):
        A.load_artifacts(tmp_path)


def test_a_changed_feature_list_says_to_retrain(tmp_path):
    meta = _metadata()
    meta["positions"]["WR"]["features"] = meta["positions"]["WR"]["features"][:-1]
    _write(tmp_path, meta)
    with pytest.raises(A.ArtifactError, match="WR feature list has changed.*dfs model train"):
        A.load_artifacts(tmp_path)


def test_missing_metadata_says_to_train(tmp_path):
    with pytest.raises(A.ArtifactError, match="dfs model train"):
        A.load_artifacts(tmp_path)


def test_a_gbm_position_without_its_model_file_says_to_retrain(tmp_path):
    methods = dict.fromkeys(POSITIONS, "blend") | {"WR": "gbm"}
    _write(tmp_path, _metadata(methods))
    with pytest.raises(A.ArtifactError, match="WR.joblib not found.*dfs model train"):
        A.load_artifacts(tmp_path)


def test_a_corrupt_model_file_says_to_retrain(tmp_path):
    methods = dict.fromkeys(POSITIONS, "blend") | {"WR": "gbm"}
    _write(tmp_path, _metadata(methods))
    A.model_path("WR", tmp_path).write_bytes(b"not a joblib file")
    with pytest.raises(A.ArtifactError, match="could not be loaded.*dfs model train"):
        A.load_artifacts(tmp_path)


def test_a_fitted_model_round_trips_and_a_stale_joblib_is_removed(tmp_path):
    rng = np.random.default_rng(0)
    x = rng.normal(size=(200, 3))
    model = HistGradientBoostingRegressor(max_iter=20).fit(x, x[:, 0] * 2)
    methods = dict.fromkeys(POSITIONS, "blend") | {"WR": "gbm"}
    _write(tmp_path, _metadata(methods), {"WR": model})
    loaded = A.load_artifacts(tmp_path)
    assert loaded.method("WR") == "gbm" and loaded.method("QB") == "blend"
    assert np.allclose(loaded.models["WR"].predict(x), model.predict(x))
    # the next training run picks the blend for WR: its joblib must not linger
    _write(tmp_path, _metadata())
    assert not A.model_path("WR", tmp_path).exists()
    assert A.load_artifacts(tmp_path).models == {}


def test_metadata_is_stable_json(tmp_path):
    _write(tmp_path, _metadata())
    text = (tmp_path / A.METADATA_FILE).read_text()
    assert json.loads(text)["artifact_version"] == A.ARTIFACT_VERSION and text.endswith("\n")


def test_the_committed_artifacts_load_under_this_scikit_learn_and_stay_small():
    loaded = A.load_artifacts()
    assert set(loaded.metadata["positions"]) == set(POSITIONS)
    for position in POSITIONS:
        assert loaded.method(position) in ("gbm", "blend")
        assert (position in loaded.models) == (loaded.method(position) == "gbm")
    assert A.directory_bytes() < A.MAX_ARTIFACT_BYTES
