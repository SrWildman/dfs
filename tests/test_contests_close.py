"""The contest step of the close: files kept, standings moved, ownership logged, Cash Line and tab written."""

import shutil
from pathlib import Path

import pandas as pd
import pytest

from dfs import contests_close
from dfs.config import Config, ContestsConfig, GoogleSheetsConfig

FIXTURES = Path(__file__).parent / "contests" / "fixtures"


class _Client:
    """Records every call; serves a Results tab whose Cash Line is blank for Week 6."""

    def __init__(self):
        self.calls = []
        self.results_rows = [["Week", "Cash Pts", "Cash Line"], [6, 150.0, ""]]

    def read_range_unformatted(self, tab, a1):
        return self.results_rows

    def __getattr__(self, name):
        def record(*args, **kwargs):
            self.calls.append((name, args, kwargs))

        return record


@pytest.fixture
def world(tmp_path, monkeypatch):
    kept = tmp_path / "data"
    monkeypatch.setattr(contests_close, "DK_HISTORY_DIR", kept / "dk_history")
    monkeypatch.setattr(contests_close, "DK_STANDINGS_DIR", kept / "dk_standings")
    monkeypatch.setattr(contests_close, "CONTESTS_DIR", kept / "contests")
    monkeypatch.setattr(contests_close, "OWNERSHIP_LOG_FILE", kept / "ownership_log.csv")
    monkeypatch.setattr(contests_close, "RAW_DIR", kept / "raw")
    monkeypatch.setattr("dfs.ownership.OWNERSHIP_LOG_FILE", kept / "ownership_log.csv")
    monkeypatch.setattr("dfs.ownership.ensure_data_dirs", lambda: kept.mkdir(parents=True, exist_ok=True))
    downloads = tmp_path / "Downloads"
    downloads.mkdir()
    shutil.copy(FIXTURES / "history_export.csv", downloads / "dk export (4).csv")
    shutil.copy(
        FIXTURES / "standings" / "contest-standings-900000001.csv",
        downloads / "contest-standings-900000001.csv",
    )
    (downloads / "contest-standings-900000005.csv").write_bytes(b"")
    cfg = Config(
        google_sheets=GoogleSheetsConfig(sheet_id="x", credentials_file="x.json", tab_mappings={}),
        contests=ContestsConfig(
            downloads_dir=str(downloads), cash_line_series="NFL Tiny $2 Double Up [Single Entry]"
        ),
    )
    return cfg, downloads, kept


def test_the_history_is_found_by_header_kept_and_the_standings_are_moved(world):
    cfg, downloads, kept = world
    report = contests_close.run_contests(cfg, None, fetch_season=None, season=2025)
    text = "\n".join(report.lines)
    assert "dk export (4).csv" in text and "found by its header row" in text
    assert report.history_used == downloads / "dk export (4).csv"
    assert list((kept / "dk_history").glob("*.csv")), "a copy of the export is kept"
    assert (kept / "dk_standings" / "contest-standings-900000001.csv").exists()
    assert not (downloads / "contest-standings-900000001.csv").exists()  # moved, not copied
    assert (downloads / "contest-standings-900000005.csv").exists()  # empty download left alone
    assert "EMPTY" in text
    assert (kept / "contests" / "contest_log.csv").exists() and (kept / "contests" / "profiles.csv").exists()


def test_ownership_is_logged_once_per_contest_and_a_rerun_logs_nothing_new(world):
    cfg, _downloads, kept = world
    first = contests_close.run_contests(cfg, None, fetch_season=None, season=2025)
    log = pd.read_csv(kept / "ownership_log.csv", dtype={"contest_id": str})
    assert "900000001" in set(log["contest_id"]) and set(log["week"]) == {6}
    assert "1 contest(s) newly logged" in "\n".join(first.lines)
    again = contests_close.run_contests(cfg, None, fetch_season=None, season=2025)
    assert "0 contest(s) newly logged, 1 already there" in "\n".join(again.lines)


def test_without_a_client_no_sheet_is_touched_and_the_files_are_still_written(world):
    cfg, _d, kept = world
    report = contests_close.run_contests(cfg, None, client=None, fetch_season=None, season=2025)
    assert report.analysis is not None and "Contests tab" not in "\n".join(report.lines)


def test_with_a_client_the_blank_cash_line_is_filled_from_the_configured_series_and_the_tab_is_written(world):
    cfg, _d, _k = world
    client = _Client()
    report = contests_close.run_contests(cfg, None, client=client, fetch_season=None, season=2025)
    text = "\n".join(report.lines)
    names = [c[0] for c in client.calls]
    assert "update_ranges" in names, text  # Week 6's blank Cash Line was filled
    filled = next(c for c in client.calls if c[0] == "update_ranges")
    assert filled[1][1] == {"C2": [[190.5]]}  # the tiny fixture double-up's cash line
    assert "write_tab" in names and "Contests: built" in text


def test_a_template_run_never_fills_results(world):
    cfg, _d, _k = world
    client = _Client()
    contests_close.run_contests(
        cfg, None, client=client, fill_cash_line=False, fetch_season=None, season=2025
    )
    assert "update_ranges" not in [c[0] for c in client.calls]


def test_no_history_export_anywhere_is_said_plainly_and_standings_are_still_kept(world, tmp_path):
    cfg, downloads, kept = world
    (downloads / "dk export (4).csv").unlink()
    report = contests_close.run_contests(cfg, None, fetch_season=None, season=2025)
    text = "\n".join(report.lines)
    assert "No contest history to read" in text and report.history_used is None
    assert (kept / "dk_standings" / "contest-standings-900000001.csv").exists()


def test_an_analysis_failure_is_reported_and_never_raised(world, monkeypatch):
    cfg, _d, _k = world
    monkeypatch.setattr(
        "dfs.contests.analysis.run", lambda *a, **k: (_ for _ in ()).throw(ValueError("boom"))
    )
    report = contests_close.run_contests(cfg, None, fetch_season=None, season=2025)
    assert "Contest analysis failed (boom)" in "\n".join(report.lines)


def test_the_pool_hit_row_is_logged_for_the_latest_graded_week_and_replaced_on_a_rerun(tmp_path, monkeypatch):
    from types import SimpleNamespace

    import pandas as pd

    from dfs import calibration, contests_close, pool, results_update

    monkeypatch.setattr(contests_close, "CONTESTS_DIR", tmp_path)
    scored = pd.DataFrame({"season": [2026] * 2, "week": [5, 5]})
    rows = pd.DataFrame(
        {
            "Status": "scored",
            "Name": ["Josh Allen", "Sleeper Pick"],
            "DkActual": [30.0, 20.0],
            "CalPts": [20.0, 12.0],
        }
    )
    monkeypatch.setattr(results_update, "load_all_scored", lambda season: scored)
    monkeypatch.setattr(calibration, "calpts_for_week", lambda s, week, season=None: (rows, None))
    monkeypatch.setattr(
        pool, "read_players", lambda client, tab: [SimpleNamespace(name="Josh Allen", pooled=True)]
    )
    monkeypatch.setattr(contests_close, "board_likes", lambda client: {"Sleeper Pick"})
    cfg = SimpleNamespace(google_sheets=SimpleNamespace(tab_mappings={"edge": "EdgeRaw"}))
    result = SimpleNamespace(decisions=pd.DataFrame({"season": [2026], "week": [5]}))

    said = contests_close.log_pool_hits(cfg, object(), result, 2026)
    assert (
        said == "Pool hit rate, Week 5: 1 of 1 pooled players beat CalPts; 1 of 1 model favourites left out."
    )
    contests_close.log_pool_hits(cfg, object(), result, 2026)  # a rerun replaces the week, never doubles it
    logged = pd.read_csv(tmp_path / contests_close.POOL_HITS_FILE)
    assert len(logged) == 1 and logged.iloc[0]["pooled_hits"] == 1
    # a week that is not scored yet says so
    monkeypatch.setattr(results_update, "load_all_scored", lambda season: scored.assign(week=4))
    assert "not scored yet" in contests_close.log_pool_hits(cfg, object(), result, 2026)
