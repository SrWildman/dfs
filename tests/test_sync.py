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


class FakeTabClient:
    """Tracks each tab's current content -- just enough of SheetsClient's
    surface for run_sync's upload path (write_tab only; every source here
    uses the base class's no-op pre_upload/post_upload)."""

    def __init__(self, initial: dict[str, list]):
        self.tabs = dict(initial)

    def write_tab(
        self, tab_name: str, rows: list, *, create_if_missing: bool = True, clear_first: bool = True
    ):
        self.tabs[tab_name] = rows
        return len(rows)


def test_partial_sync_after_clear_synced_tabs_leaves_failed_source_tab_empty(monkeypatch, cfg, tmp_path):
    # Fix 2.14: `dfs week new` clears every synced tab up front,
    # unconditionally, before the first sync -- so a source that fails
    # during that sync leaves an empty tab, never the template's own
    # stale (real-looking, but wrong) leftover data.
    import dfs.store as store
    from dfs.weekly_reset import clear_synced_tabs

    monkeypatch.setattr(store, "RAW_DIR", tmp_path / "raw")
    monkeypatch.setattr(store, "CURRENT_DIR", tmp_path / "current")
    monkeypatch.setattr(store, "MANIFEST_FILE", tmp_path / "manifest.json")
    (tmp_path / "current").mkdir()

    # Pre-seeded with "stale template" content -- what a template rebuilt
    # from a past live sheet could actually carry (CONTRIBUTING.md).
    client = FakeTabClient({"Good": [["stale", "good", "data"]], "Bad": [["stale", "bad", "data"]]})

    clear_synced_tabs(client, cfg.google_sheets.tab_mappings, ["good", "bad"])
    assert client.tabs["Good"] == []
    assert client.tabs["Bad"] == []

    monkeypatch.setattr(
        "dfs.sync.get_source",
        lambda name: {"good": FakeGoodSource(), "bad": FakeFailingSource()}[name],
    )
    monkeypatch.setattr("dfs.sync.SheetsClient", lambda _cfg: client)

    results = run_sync(cfg, ["good", "bad"], SyncContext(week=1, season=2026), upload=True)

    by_name = {r.source: r for r in results}
    assert by_name["good"].ok is True
    assert by_name["bad"].ok is False

    # The successful source got fresh real data; the failed one's tab is
    # still exactly as clear_synced_tabs left it -- never repopulated
    # with anything, stale or otherwise.
    assert client.tabs["Good"] != []
    assert client.tabs["Bad"] == []
