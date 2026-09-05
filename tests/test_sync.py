import pandas as pd
import pytest

from dfs.config import Config
from dfs.sources.base import Source, SyncContext
from dfs.sync import run_sync


class FakeGoodSource(Source):
    name = "good"

    def fetch(self, ctx: SyncContext) -> pd.DataFrame:
        return pd.DataFrame({"a": [1, 2, 3]})


class FakeFailingSource(Source):
    name = "bad"

    def fetch(self, ctx: SyncContext) -> pd.DataFrame:
        raise RuntimeError("upstream is down")


@pytest.fixture
def cfg():
    return Config.model_validate(
        {
            "google_sheets": {
                "sheet_id": "fake",
                "credentials_file": "creds.json",
                "tab_mappings": {"good": "Good", "bad": "Bad"},
            }
        }
    )


def test_run_sync_no_upload_records_success_and_failure(monkeypatch, cfg, tmp_path):
    import dfs.store as store

    monkeypatch.setattr(store, "RAW_DIR", tmp_path / "raw")
    monkeypatch.setattr(store, "CURRENT_DIR", tmp_path / "current")
    monkeypatch.setattr(store, "MANIFEST_FILE", tmp_path / "manifest.json")
    (tmp_path / "current").mkdir()

    monkeypatch.setattr(
        "dfs.sync.get_source",
        lambda name: {"good": FakeGoodSource(), "bad": FakeFailingSource()}[name],
    )

    results = run_sync(cfg, ["good", "bad"], SyncContext(week=1, season=2026), upload=False)

    by_name = {r.source: r for r in results}
    assert by_name["good"].ok is True
    assert by_name["good"].rows == 3
    assert by_name["bad"].ok is False
    assert "upstream is down" in by_name["bad"].error


def test_run_sync_missing_tab_mapping_fails_that_source(monkeypatch, tmp_path):
    import dfs.store as store

    monkeypatch.setattr(store, "RAW_DIR", tmp_path / "raw")
    monkeypatch.setattr(store, "CURRENT_DIR", tmp_path / "current")
    monkeypatch.setattr(store, "MANIFEST_FILE", tmp_path / "manifest.json")
    (tmp_path / "current").mkdir()

    cfg = Config.model_validate(
        {
            "google_sheets": {
                "sheet_id": "fake",
                "credentials_file": "creds.json",
                "tab_mappings": {},  # no mapping for "good"
            }
        }
    )
    monkeypatch.setattr("dfs.sync.get_source", lambda name: FakeGoodSource())
    monkeypatch.setattr("dfs.sync.SheetsClient", lambda cfg: None)

    results = run_sync(cfg, ["good"], SyncContext(week=1, season=2026), upload=True)
    assert results[0].ok is False
    assert "No tab mapped" in results[0].error


def test_run_sync_unknown_source_name(cfg):
    results = run_sync(cfg, ["totally_unknown"], SyncContext(week=1, season=2026), upload=False)
    assert results[0].ok is False
    assert "Unknown source" in results[0].error
