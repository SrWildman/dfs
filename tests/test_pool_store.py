"""Sam's saved pool (`pool_store`): what a backtest builds from."""

from datetime import UTC, datetime, timedelta

from dfs import cli, pool_store

T0 = datetime(2026, 10, 17, 20, 0, tzinfo=UTC)
POOL = {"Josh Allen": "Both", "Bijan Robinson": "Cash", "Nico Collins": "GPP"}


def test_save_then_load_round_trips(tmp_path):
    path = pool_store.save(2026, 6, POOL, source="sync", sheet="Week 6", at=T0, directory=tmp_path)
    assert path == tmp_path / "2026-wk06.json"
    (snap,) = pool_store.load(2026, 6, tmp_path)
    assert snap.pool == POOL
    assert (snap.at, snap.source, snap.sheet) == (T0, "sync", "Week 6")


def test_an_identical_pool_is_not_stored_again_but_a_change_is(tmp_path):
    pool_store.save(2026, 6, POOL, source="sync", at=T0, directory=tmp_path)
    assert (
        pool_store.save(2026, 6, POOL, source="suggest", at=T0 + timedelta(hours=1), directory=tmp_path)
        is None
    )
    pool_store.save(
        2026, 6, {**POOL, "Puka Nacua": "Both"}, source="sync", at=T0 + timedelta(hours=2), directory=tmp_path
    )
    assert [len(s.pool) for s in pool_store.load(2026, 6, tmp_path)] == [3, 4]


def test_a_rules_box_change_alone_is_a_new_snapshot_and_a_sync_carries_the_box(tmp_path):
    pool_store.save(2026, 6, POOL, source="suggest", rules={"Never use": "Bijan"}, at=T0, directory=tmp_path)
    pool_store.save(
        2026,
        6,
        POOL,
        source="suggest",
        rules={"Never use": ""},
        at=T0 + timedelta(hours=1),
        directory=tmp_path,
    )
    assert len(pool_store.load(2026, 6, tmp_path)) == 2
    # a sync does not read the box: its snapshot keeps the last one's
    pool_store.save(
        2026, 6, {**POOL, "X": "Both"}, source="sync", at=T0 + timedelta(hours=2), directory=tmp_path
    )
    assert pool_store.load(2026, 6, tmp_path)[-1].rules == {"Never use": ""}


def test_an_empty_pool_is_never_saved(tmp_path):
    assert pool_store.save(2026, 6, {}, source="sync", directory=tmp_path) is None
    assert pool_store.save(2026, 6, {"A": " "}, source="sync", directory=tmp_path) is None
    assert pool_store.load(2026, 6, tmp_path) == []


def test_pool_at_is_the_last_snapshot_at_or_before_the_lock(tmp_path):
    for hours, extra in ((0, "A"), (5, "B"), (30, "C")):
        pool_store.save(
            2026, 6, {extra: "Both"}, source="sync", at=T0 + timedelta(hours=hours), directory=tmp_path
        )
    assert pool_store.pool_at(2026, 6, T0 + timedelta(hours=6), tmp_path).pool == {"B": "Both"}
    assert pool_store.pool_at(2026, 6, T0 - timedelta(hours=1), tmp_path) is None
    assert pool_store.pool_at(2026, 7, T0, tmp_path) is None


def test_an_unreadable_file_is_no_snapshots(tmp_path):
    (tmp_path / "2026-wk06.json").write_text("{not json")
    assert pool_store.load(2026, 6, tmp_path) == []


def test_the_cli_helper_never_raises(monkeypatch):
    def boom(*_a, **_k):
        raise RuntimeError("sheet is down")

    monkeypatch.setattr(cli, "read_players", boom)
    cli._save_pool_snapshot(object(), object(), 2026, 6, source="sync")  # no tags given: it reads the sheet


def test_the_cli_helper_saves_the_tags_it_is_given():
    cli._save_pool_snapshot(object(), object(), 2026, 7, source="suggest", tags=POOL, rules={"Adjust": "x"})
    (snap,) = pool_store.load(2026, 7)
    assert snap.pool == POOL and snap.rules == {"Adjust": "x"} and snap.source == "suggest"
