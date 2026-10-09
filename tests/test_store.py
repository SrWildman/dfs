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


def test_clear_current_deletes_every_current_csv_and_reports_names(monkeypatch, tmp_path):
    monkeypatch.setattr(store, "CURRENT_DIR", tmp_path / "current")
    current_dir = tmp_path / "current"
    current_dir.mkdir()
    (current_dir / "draftkings.csv").write_text("a,b\n1,2\n")
    (current_dir / "projections.csv").write_text("a,b\n1,2\n")

    removed = store.clear_current()

    assert sorted(removed) == ["draftkings", "projections"]
    assert list(current_dir.glob("*.csv")) == []


def test_clear_current_leaves_raw_history_untouched(monkeypatch, tmp_path):
    # The exact live incident this exists to prevent: a stale `current`
    # cache from a previous week silently broke a cross-source ID join
    # with no error. Raw snapshot history is a different concern (line-
    # movement diffing needs it) and must survive this prune.
    monkeypatch.setattr(store, "CURRENT_DIR", tmp_path / "current")
    monkeypatch.setattr(store, "RAW_DIR", tmp_path / "raw")
    (tmp_path / "current").mkdir()
    (tmp_path / "current" / "draftkings.csv").write_text("a,b\n1,2\n")
    raw_dir = tmp_path / "raw" / "draftkings"
    raw_dir.mkdir(parents=True)
    (raw_dir / "20260101T000000Z.csv").write_text("a,b\n1,2\n")

    store.clear_current()

    assert (raw_dir / "20260101T000000Z.csv").exists()


def test_clear_current_is_a_noop_on_an_empty_or_missing_dir(monkeypatch, tmp_path):
    monkeypatch.setattr(store, "CURRENT_DIR", tmp_path / "current")
    assert store.clear_current() == []  # directory doesn't even exist yet

    (tmp_path / "current").mkdir()
    assert store.clear_current() == []  # exists but empty


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


# ---- the week's opening lines (actions round, slice 7) ---------------------------------------------------


def _odds(raw_dir, stamp: str, *kickoffs: str, spread: float = 3.0) -> None:
    """An odds snapshot whose games kick off at the given Eastern wall-clock times."""
    raw_dir.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(
        {"abbr": [f"T{i}" for i in range(len(kickoffs))], "date": list(kickoffs), "spread": spread}
    ).to_csv(raw_dir / f"{stamp}.csv", index=False)


def test_the_opening_is_the_first_snapshot_after_the_previous_weeks_last_game(monkeypatch, tmp_path):
    raw = tmp_path / "raw" / "nfl_odds"
    monkeypatch.setattr(store, "RAW_DIR", tmp_path / "raw")
    week_four = ("2026-10-04 13:00:00", "2026-10-05 20:15:00")  # Sunday games, then Monday night
    _odds(raw, "20261004T162708Z", *week_four, spread=1.0)  # Sunday: still last week's slate
    _odds(raw, "20261005T140000Z", *week_four, spread=2.0)  # Monday 10:00 ET: MNF not played yet
    _odds(raw, "20261006T133030Z", "2026-10-08 20:15:00", "2026-10-11 13:00:00", spread=3.0)  # Tuesday
    _odds(raw, "20261008T221317Z", "2026-10-08 20:15:00", "2026-10-11 13:00:00", spread=4.0)

    opening = store.opening_snapshot("nfl_odds", date(2026, 10, 6))
    assert opening.stamp.isoformat() == "2026-10-06T13:30:30+00:00" and opening.frame["spread"].iloc[0] == 3.0
    assert opening.how == "after the previous week's last game"


def test_a_snapshot_that_already_shows_only_the_new_slate_is_the_opening_even_before_monday_night_ends(
    monkeypatch, tmp_path
):
    raw = tmp_path / "raw" / "nfl_odds"
    monkeypatch.setattr(store, "RAW_DIR", tmp_path / "raw")
    _odds(raw, "20261004T162708Z", "2026-10-04 13:00:00", "2026-10-05 20:15:00", spread=1.0)
    _odds(
        raw, "20261005T140000Z", "2026-10-08 20:15:00", "2026-10-11 13:00:00", spread=2.0
    )  # Monday 10:00 ET
    _odds(raw, "20261006T133030Z", "2026-10-08 20:15:00", "2026-10-11 13:00:00", spread=3.0)

    assert store.opening_snapshot("nfl_odds", date(2026, 10, 6)).frame["spread"].iloc[0] == 2.0


def test_the_opening_does_not_depend_on_when_the_sheet_is_copied(monkeypatch, tmp_path):
    """A Thursday `week new` still finds the Tuesday snapshot, and snapshots saved later do not move it."""
    raw = tmp_path / "raw" / "nfl_odds"
    monkeypatch.setattr(store, "RAW_DIR", tmp_path / "raw")
    _odds(raw, "20261004T162708Z", "2026-10-04 13:00:00", "2026-10-05 20:15:00", spread=1.0)
    _odds(raw, "20261006T140000Z", "2026-10-08 20:15:00", "2026-10-11 13:00:00", spread=3.0)
    _odds(raw, "20261008T150000Z", "2026-10-08 20:15:00", "2026-10-11 13:00:00", spread=5.0)  # Thursday

    assert store.load_since("nfl_odds", date(2026, 10, 6))["spread"].iloc[0] == 3.0


def test_as_of_ignores_snapshots_saved_later(monkeypatch, tmp_path):
    from datetime import UTC, datetime

    raw = tmp_path / "raw" / "nfl_odds"
    monkeypatch.setattr(store, "RAW_DIR", tmp_path / "raw")
    _odds(raw, "20261004T162708Z", "2026-10-04 13:00:00", "2026-10-05 20:15:00", spread=1.0)
    _odds(raw, "20261006T140000Z", "2026-10-08 20:15:00", spread=3.0)
    opening = store.opening_snapshot(
        "nfl_odds", date(2026, 10, 6), as_of=datetime(2026, 10, 6, 12, tzinfo=UTC)
    )
    assert (
        opening.how == "earliest on disk" and opening.frame["spread"].iloc[0] == 1.0
    )  # nothing after MNF yet


def test_with_no_earlier_slate_it_falls_back_to_the_first_snapshot_this_week(monkeypatch, tmp_path):
    raw = tmp_path / "raw" / "nfl_odds"
    monkeypatch.setattr(store, "RAW_DIR", tmp_path / "raw")
    _odds(raw, "20261006T140000Z", "2026-10-08 20:15:00", spread=3.0)
    _odds(raw, "20261007T140000Z", "2026-10-08 20:15:00", spread=4.0)
    opening = store.opening_snapshot("nfl_odds", date(2026, 10, 6))
    assert opening.frame["spread"].iloc[0] == 3.0 and opening.how == "first saved this week"
