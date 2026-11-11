"""`data/news/changes.csv` on disk (`news_store`): append-only, an idempotent backfill, and the hooks in
`dfs sync` and the late-swap refresh. Everything runs on recorded-style snapshots in the test's own data
directory (the autouse fixture in `conftest.py`); no sheet, no network."""

import builtins
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest
from test_news_log import edge

from dfs import news_log, news_store, paths

SAT_NIGHT = "20261107T230109Z"
SUN_EARLY = "20261108T165702Z"
SUN_LATE = "20261108T174036Z"
TUESDAY = "20261110T150000Z"


def week10() -> pd.DataFrame:
    return edge(Opp=["ARI", "ARI", "DAL", "DAL"], GameID=["2026_10_ARI_LAR"] * 2 + ["2026_10_DAL_SF"] * 2)


SUNDAY = {
    SAT_NIGHT: lambda: edge(),
    SUN_EARLY: lambda: edge(Avail=["OUT", "", "", ""], ProjPts=[0.0, 19.5, 15.0, 19.2]),
    SUN_LATE: lambda: edge(Avail=["OUT", "", "Q", ""], ProjPts=[0.0, 19.5, 15.0, 19.2]),
    TUESDAY: week10,
}


def save_snapshots(stems) -> Path:
    folder = news_store.edge_dir()
    folder.mkdir(parents=True, exist_ok=True)
    for stem in stems:
        SUNDAY[stem]().to_csv(folder / f"{stem}.csv", index=False)
    return folder


def some_rows(stem=SUN_EARLY) -> list[dict]:
    return news_log.diff(edge(), SUNDAY[stem](), at=news_log.parse_stamp(stem), snapshot=stem)


def test_the_log_lives_under_the_data_directory_and_starts_with_the_header():
    path = news_store.changes_path()
    assert path == paths.DATA_DIR / "news" / "changes.csv"
    assert news_store.append(some_rows()) == 3
    lines = path.read_text().splitlines()
    assert lines[0] == ",".join(news_log.COLUMNS)
    assert lines[1] == f"2026-11-08T16:57:02Z,2026,9,101,Puka Nacua,WR,LAR,status,Q,OUT,dk,{SUN_EARLY}"
    assert len(lines) == 4


def test_appending_never_rewrites_a_row():
    """Every write leaves the bytes already in the file exactly as they were: the new file starts with the
    old one, whatever is appended (rows, nothing, a backfill, a hook)."""
    path = news_store.changes_path()
    news_store.append(some_rows(SUN_EARLY))
    history = [path.read_bytes()]
    writes = [
        lambda: news_store.append(some_rows(SUN_LATE)),
        lambda: news_store.append([]),
        lambda: news_store.append(some_rows(SUN_EARLY)),  # the same rows again are added, never merged
        lambda: (save_snapshots(SUNDAY), news_store.backfill()),
        news_store.log_latest,
        lambda: news_store.log_espn(
            edge(),
            [SimpleNamespace(name="Brock Purdy", before="", after="Q", updated=None)],
            at=datetime(2026, 11, 8, 17, 50, tzinfo=UTC),
        ),
    ]
    for write in writes:
        write()
        now = path.read_bytes()
        assert now.startswith(history[-1])
        history.append(now)
    assert len(history[-1]) > len(history[0])
    assert history[-1].count(b"at,season,week") == 1  # one header, at the top


def test_the_store_only_ever_opens_the_log_for_append(monkeypatch):
    opened = []
    real_open = Path.open

    def spy(self, mode="r", *args, **kwargs):
        if self.name == news_store.CHANGES_FILE:
            opened.append(mode)
        return real_open(self, mode, *args, **kwargs)

    real_builtin_open = builtins.open

    def builtin_spy(file, mode="r", *args, **kwargs):
        if str(file).endswith(news_store.CHANGES_FILE):
            opened.append(mode)
        return real_builtin_open(file, mode, *args, **kwargs)

    monkeypatch.setattr(Path, "open", spy)
    monkeypatch.setattr(builtins, "open", builtin_spy)
    save_snapshots(SUNDAY)
    news_store.backfill()
    news_store.backfill()
    news_store.log_latest()
    news_store.append(some_rows())
    assert opened and all(set(mode) & set("wx+") == set() for mode in opened)
    assert "a" in opened


def test_backfill_rebuilds_the_log_from_the_snapshots_and_a_second_run_changes_nothing():
    save_snapshots(SUNDAY)
    added = news_store.backfill()
    assert dict(added) == {(2026, 9): 4}  # Sunday's three + one; Tuesday is a new slate's baseline
    first = news_store.changes_path().read_bytes()
    assert dict(news_store.backfill()) == {}
    assert news_store.changes_path().read_bytes() == first
    log = news_store.read()
    assert list(log.columns) == news_log.COLUMNS
    assert list(log["snapshot"].unique()) == [SUN_EARLY, SUN_LATE]


def test_backfill_gives_the_same_file_as_the_hooks_did_sync_by_sync():
    folder = news_store.edge_dir()
    folder.mkdir(parents=True, exist_ok=True)
    for stem in SUNDAY:
        SUNDAY[stem]().to_csv(folder / f"{stem}.csv", index=False)
        news_store.log_latest()
    live = news_store.changes_path().read_bytes()
    news_store.changes_path().unlink()
    news_store.backfill()
    assert news_store.changes_path().read_bytes() == live


def test_backfill_fills_the_hole_a_failed_hook_left_and_only_that():
    save_snapshots([SAT_NIGHT, SUN_EARLY])
    # the hook for SUN_EARLY failed: nothing logged. SUN_LATE's hook then works.
    save_snapshots([SUN_LATE])
    assert news_store.log_latest() == 1
    before = news_store.changes_path().read_bytes()
    assert dict(news_store.backfill()) == {(2026, 9): 3}
    after = news_store.changes_path().read_bytes()
    assert after.startswith(before)
    assert sorted(news_store.read()["snapshot"].unique()) == [SUN_EARLY, SUN_LATE]


def test_log_latest_needs_two_snapshots_and_never_logs_one_twice():
    save_snapshots([SAT_NIGHT])
    assert news_store.log_latest() == 0 and not news_store.changes_path().exists()
    save_snapshots([SUN_EARLY])
    assert news_store.log_latest() == 3
    assert news_store.log_latest() == 0
    assert len(news_store.read()) == 3


def test_a_file_that_is_not_a_snapshot_and_one_that_cannot_be_read_are_skipped():
    folder = save_snapshots([SAT_NIGHT, SUN_EARLY])
    (folder / "notes.csv").write_text("hello\n")
    (folder / "20261108T170000Z.csv").write_text("")  # an empty file between two good ones
    save_snapshots([SUN_LATE])
    assert [p.stem for p in news_store.snapshot_files()] == [
        SAT_NIGHT,
        SUN_EARLY,
        "20261108T170000Z",
        SUN_LATE,
    ]
    assert dict(news_store.backfill()) == {(2026, 9): 4}  # SUN_LATE is diffed against SUN_EARLY


def test_the_week_nine_sunday_replay_lists_the_status_changes_with_their_times():
    save_snapshots(SUNDAY)
    news_store.backfill()
    status = news_store.week_changes(2026, 9, fields=("status",))
    assert status[["at", "name", "before", "after"]].values.tolist() == [
        ["2026-11-08T16:57:02Z", "Puka Nacua", "Q", "OUT"],
        ["2026-11-08T17:40:36Z", "George Kittle", "", "Q"],
    ]
    assert len(news_store.week_changes(2026, 9)) == 4
    assert news_store.week_changes(2026, 10).empty


def test_an_espn_refresh_is_logged_once_however_often_late_swap_runs():
    changes = [
        SimpleNamespace(
            name="Puka Nacua", before="Q", after="OUT", updated=datetime(2026, 11, 8, 17, 15, tzinfo=UTC)
        )
    ]
    at = datetime(2026, 11, 8, 17, 50, tzinfo=UTC)
    assert news_store.log_espn(edge(), changes, at=at) == 1
    assert news_store.log_espn(edge(), changes, at=at) == 0
    assert news_store.log_espn(edge(), [], at=at) == 0
    log = news_store.read()
    assert log[["at", "name", "field", "before", "after", "source", "snapshot"]].values.tolist() == [
        ["2026-11-08T17:50:00Z", "Puka Nacua", "status", "Q", "OUT", "espn_injuries", "espn:20261108T171500Z"]
    ]
    # the backfill never repeats or removes a late-swap row
    save_snapshots(SUNDAY)
    news_store.backfill()
    assert (news_store.read()["source"] == "espn_injuries").sum() == 1


# --- the hooks --------------------------------------------------------------------------------------------


@pytest.fixture
def sync_edge(monkeypatch):
    """`run_sync(["edge"])` with a fake source that returns the next frame of `frames`, each saved under the
    next stamp of `stamps`."""
    from dfs import store, sync
    from dfs.config import Config
    from dfs.sources.base import Source, SyncContext

    frames: list[pd.DataFrame] = []
    stamps: list[str] = []

    class FakeEdge(Source):
        name = "edge"
        uploads_to_sheet = False

        def fetch(self, ctx):
            return frames.pop(0)

    monkeypatch.setattr(sync, "get_source", lambda name: FakeEdge())
    monkeypatch.setattr(store, "_now_stamp", lambda: stamps.pop(0))
    cfg = Config.model_validate(
        {"google_sheets": {"sheet_id": "fake", "credentials_file": "creds.json", "tab_mappings": {}}}
    )

    def run(stem: str, frame: pd.DataFrame):
        frames.append(frame)
        stamps.append(stem)
        now = news_log.parse_stamp(stem)
        return sync.run_sync(cfg, ["edge"], SyncContext(week=9, season=2026), upload=False, now=now)[0]

    return run


def test_a_sync_logs_what_changed_after_its_snapshot_is_saved(sync_edge):
    assert sync_edge(SAT_NIGHT, edge()).ok
    assert not news_store.changes_path().exists()  # the first snapshot is the baseline
    assert sync_edge(SUN_EARLY, SUNDAY[SUN_EARLY]()).ok
    log = news_store.read()
    assert log[["name", "field", "before", "after", "snapshot"]].values.tolist() == [
        ["Puka Nacua", "status", "Q", "OUT", SUN_EARLY],
        ["Puka Nacua", "proj_tffb", "20.00", "0.00", SUN_EARLY],
        ["Kyren Williams", "proj_tffb", "16.50", "19.50", SUN_EARLY],
    ]
    assert (news_store.edge_dir() / f"{SUN_EARLY}.csv").exists()


def test_a_news_log_failure_warns_and_the_sync_carries_on(sync_edge, monkeypatch, caplog):
    sync_edge(SAT_NIGHT, edge())

    def boom(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(news_store, "log_latest", boom)
    with caplog.at_level("WARNING"):
        result = sync_edge(SUN_EARLY, SUNDAY[SUN_EARLY]())
    assert result.ok and result.rows == 4 and result.error is None
    assert "news change log not updated (disk full)" in caplog.text
    assert (news_store.edge_dir() / f"{SUN_EARLY}.csv").exists()  # the snapshot is saved all the same


def test_the_sync_hook_only_runs_for_the_edge_source(monkeypatch):
    from dfs import store, sync
    from dfs.config import Config
    from dfs.sources.base import Source, SyncContext

    class Other(Source):
        name = "weather"
        uploads_to_sheet = False

        def fetch(self, ctx):
            return pd.DataFrame({"a": [1]})

    called = []
    monkeypatch.setattr(sync, "get_source", lambda name: Other())
    monkeypatch.setattr(news_store, "log_latest", lambda *a, **k: called.append(1) or 0)
    monkeypatch.setattr(store, "_now_stamp", lambda: SAT_NIGHT)
    cfg = Config.model_validate(
        {"google_sheets": {"sheet_id": "fake", "credentials_file": "creds.json", "tab_mappings": {}}}
    )
    assert sync.run_sync(cfg, ["weather"], SyncContext(week=9, season=2026), upload=False)[0].ok
    assert called == []


def espn_feed():
    return pd.DataFrame(
        {
            "Name": ["Puka Nacua"],
            "Status": ["Out"],
            "Updated": [pd.Timestamp("2026-11-08T17:15:00Z")],
            "Note": ["hamstring"],
        }
    )


def test_the_late_swap_refresh_logs_espns_escalations(monkeypatch):
    from dfs import cli, late_swap_news

    monkeypatch.setattr(late_swap_news, "fetch_news", espn_feed)
    now = datetime(2026, 11, 8, 17, 50, tzinfo=UTC)
    out, changes = cli._late_swap_news(edge(), now=now, refresh=True)
    assert list(out["Avail"]) == ["OUT", "", "", ""] and len(changes) == 1
    assert news_store.read()[["at", "name", "before", "after", "source"]].values.tolist() == [
        ["2026-11-08T17:50:00Z", "Puka Nacua", "Q", "OUT", "espn_injuries"]
    ]
    cli._late_swap_news(edge(), now=now, refresh=True)  # a second run within the hour
    assert len(news_store.read()) == 1


def test_without_the_refresh_late_swap_logs_nothing(monkeypatch):
    from dfs import cli, late_swap_news

    def no_network():
        raise AssertionError("no fetch without --refresh-injuries")

    monkeypatch.setattr(late_swap_news, "fetch_news", no_network)
    cli._late_swap_news(edge(), now=datetime(2026, 11, 8, 17, 50, tzinfo=UTC), refresh=False)
    assert not news_store.changes_path().exists()


def test_a_news_log_failure_never_fails_the_late_swap_refresh(monkeypatch, capsys):
    from dfs import cli, late_swap_news

    def boom(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(late_swap_news, "fetch_news", espn_feed)
    monkeypatch.setattr(news_store, "log_espn", boom)
    out, changes = cli._late_swap_news(edge(), now=datetime(2026, 11, 8, 17, 50, tzinfo=UTC), refresh=True)
    assert list(out["Avail"]) == ["OUT", "", "", ""] and len(changes) == 1
    assert "News change log not updated: disk full" in capsys.readouterr().out
