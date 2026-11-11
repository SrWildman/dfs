"""`dfs news backfill` and `dfs news changes`: files only."""

import pytest
from typer.testing import CliRunner

from dfs import news_store
from dfs.cli import app
from dfs.news_cli import _local, parse_fields
from tests.test_news_store import SUNDAY, save_snapshots

runner = CliRunner()


def test_backfill_reports_rows_per_week_and_a_second_run_adds_nothing():
    save_snapshots(SUNDAY)
    first = runner.invoke(app, ["news", "backfill"])
    assert first.exit_code == 0, first.output
    assert "2026 Week 9: 4 row(s) added" in first.output and "4 row(s) added to" in first.output
    saved = news_store.changes_path().read_bytes()
    second = runner.invoke(app, ["news", "backfill"])
    assert second.exit_code == 0 and "nothing to add" in second.output
    assert news_store.changes_path().read_bytes() == saved


def test_backfill_with_no_snapshots_is_not_an_error():
    result = runner.invoke(app, ["news", "backfill"])
    assert result.exit_code == 0 and "nothing to add" in result.output
    assert not news_store.changes_path().exists()


def test_changes_lists_the_weeks_status_changes_in_eastern_time():
    save_snapshots(SUNDAY)
    news_store.backfill()
    before = news_store.changes_path().read_bytes()
    result = runner.invoke(app, ["news", "changes", "--week", "9", "--season", "2026"])
    assert result.exit_code == 0, result.output
    assert "2 change(s), status" in result.output
    assert "Sun 11:57 am" in result.output and "Puka Nacua" in result.output and "OUT" in result.output
    assert "Sun 12:40 pm" in result.output and "George Kittle" in result.output
    assert "proj_tffb" not in result.output
    assert news_store.changes_path().read_bytes() == before  # read-only


def test_changes_takes_fields_and_a_player_filter():
    save_snapshots(SUNDAY)
    news_store.backfill()
    everything = runner.invoke(app, ["news", "changes", "--week", "9", "--season", "2026", "--field", "all"])
    assert "4 change(s)" in everything.output and "proj_tffb" in everything.output
    kyren = runner.invoke(
        app,
        ["news", "changes", "--week", "9", "--season", "2026", "--field", "proj_tffb", "--player", "kyren"],
    )
    assert "1 change(s)" in kyren.output and "19.50" in kyren.output and "Nacua" not in kyren.output


def test_changes_says_so_when_the_week_has_none_and_refuses_an_unknown_field():
    empty = runner.invoke(app, ["news", "changes", "--week", "3", "--season", "2026"])
    assert empty.exit_code == 0 and "No changes logged for 2026 Week 3" in empty.output
    bad = runner.invoke(app, ["news", "changes", "--week", "9", "--field", "salary"])
    assert bad.exit_code == 1 and "unknown field(s) salary" in bad.output


def test_parse_fields_and_the_eastern_clock():
    assert parse_fields("status") == ("status",)
    assert parse_fields("Status, proj_tffb") == ("status", "proj_tffb")
    assert parse_fields("all") == ()
    with pytest.raises(ValueError):
        parse_fields("")
    assert _local("2026-11-08T16:57:02Z") == "Sun 11:57 am"
    assert _local("2026-11-08T14:05:00Z") == "Sun 9:05 am"
    assert _local("2026-09-13T04:52:51Z") == "Sun 12:52 am"  # daylight time: UTC-4
