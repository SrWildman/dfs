from datetime import date

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


def _write_snapshot(raw_dir, stamp: str, value: str) -> None:
    (raw_dir).mkdir(parents=True, exist_ok=True)
    pd.DataFrame({"v": [value]}).to_csv(raw_dir / f"{stamp}.csv", index=False)


def test_load_since_missing_source_raises(monkeypatch, tmp_path):
    monkeypatch.setattr(store, "RAW_DIR", tmp_path / "raw")
    with pytest.raises(FileNotFoundError, match="widgets"):
        store.load_since("widgets", date(2026, 9, 1))


def test_load_since_returns_earliest_snapshot_on_or_after_cutoff(monkeypatch, tmp_path):
    raw_dir = tmp_path / "raw" / "widgets"
    monkeypatch.setattr(store, "RAW_DIR", tmp_path / "raw")
    _write_snapshot(raw_dir, "20260901T120000Z", "before-week")  # last week
    _write_snapshot(raw_dir, "20260910T090000Z", "week-open")  # this week's first sync
    _write_snapshot(raw_dir, "20260912T090000Z", "mid-week")

    df = store.load_since("widgets", date(2026, 9, 10))
    assert df["v"].iloc[0] == "week-open"


def test_load_since_falls_back_to_earliest_snapshot_when_none_qualify(monkeypatch, tmp_path):
    raw_dir = tmp_path / "raw" / "widgets"
    monkeypatch.setattr(store, "RAW_DIR", tmp_path / "raw")
    _write_snapshot(raw_dir, "20260910T090000Z", "only-sync-so-far")

    df = store.load_since("widgets", date(2026, 9, 10))
    assert df["v"].iloc[0] == "only-sync-so-far"
