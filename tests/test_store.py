import pandas as pd
import pytest

from dfs import store


def test_save_writes_raw_and_current(monkeypatch, tmp_path):
    monkeypatch.setattr(store, "RAW_DIR", tmp_path / "raw")
    monkeypatch.setattr(store, "CURRENT_DIR", tmp_path / "current")
    monkeypatch.setattr(store, "MANIFEST_FILE", tmp_path / "manifest.json")
    (tmp_path / "current").mkdir()

    df = pd.DataFrame({"a": [1, 2], "b": ["x", "y"]})
    store.save("widgets", df)

    current = pd.read_csv(tmp_path / "current" / "widgets.csv")
    pd.testing.assert_frame_equal(current, df)

    raw_files = list((tmp_path / "raw" / "widgets").glob("*.csv"))
    assert len(raw_files) == 1


def test_load_current_missing_raises(monkeypatch, tmp_path):
    monkeypatch.setattr(store, "CURRENT_DIR", tmp_path / "current")
    (tmp_path / "current").mkdir()
    with pytest.raises(FileNotFoundError, match="nope"):
        store.load_current("nope")


def test_record_success_then_failure_overwrites_entry(monkeypatch, tmp_path):
    manifest_file = tmp_path / "manifest.json"
    monkeypatch.setattr(store, "MANIFEST_FILE", manifest_file)

    store.record_success("widgets", 10)
    m = store.read_manifest()
    assert m["widgets"]["rows"] == 10
    assert m["widgets"]["error"] is None

    store.record_failure("widgets", "boom")
    m = store.read_manifest()
    assert m["widgets"]["rows"] is None
    assert m["widgets"]["error"] == "boom"
