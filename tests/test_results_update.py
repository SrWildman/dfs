"""`update_results`: stats not published yet, incomplete weeks, scoring only what is unscored."""

import pandas as pd
import pytest

from dfs import results_loop, results_update
from dfs.results_update import Fetchers, update_results
from dfs.sources.nflverse_results import ResultsFetchError


class _Cfg:
    google_sheets = None


def _games(complete_weeks):
    rows = []
    for week in (1, 2, 3):
        done = week in complete_weeks
        rows.append(
            {
                "week": week,
                "away_team": "AAA",
                "home_team": "BBB",
                "away_score": 17 if done else None,
                "home_score": 20 if done else None,
            }
        )
    return pd.DataFrame(rows)


def _empty_stats():
    return pd.DataFrame(
        columns=["player_id", "player_display_name", "position", "season_type", "week", "team"]
    )


def _fetchers(complete_weeks=(1, 2)):
    return Fetchers(
        stats_player=lambda season: _empty_stats(),
        stats_team=lambda season: pd.DataFrame(columns=["season_type", "week", "team", "opponent_team"]),
        game_scores=lambda season: _games(set(complete_weeks)),
    )


@pytest.fixture(autouse=True)
def _results_dir(monkeypatch, tmp_path):
    monkeypatch.setattr(results_loop, "RESULTS_DIR", tmp_path / "results")
    monkeypatch.setattr(results_update.results_loop, "RESULTS_DIR", tmp_path / "results")


def test_stats_not_published_yet_is_reported_not_raised():
    def boom(season):
        raise ResultsFetchError("Request for 2026 stats_player failed: 404")

    report = update_results(
        _Cfg(),
        season=2026,
        write_sheet=False,
        fetchers=Fetchers(stats_player=boom, stats_team=boom, game_scores=boom),
    )
    assert report.published is False
    assert "not published yet" in report.lines[0] and "dfs results update" in report.lines[0]


def test_a_named_week_that_is_not_complete_scores_nothing():
    report = update_results(_Cfg(), season=2026, week=3, write_sheet=False, fetchers=_fetchers((1, 2)))
    assert report.published is False and "not complete" in report.lines[0]


def _fake_week(week):
    scored = pd.DataFrame(
        [
            {
                "Id": 1,
                "Name": "X",
                "Position": "WR",
                "Team": "AAA",
                "ProjPts": 10.0,
                "RosterablePool": True,
                "Status": "scored",
                "DkActual": 9.0,
                "week": week,
            }
        ]
    )
    from datetime import UTC, datetime

    return results_loop.WeekResult(
        week=week,
        scored=scored,
        reference=datetime(2026, 9, 13, tzinfo=UTC),
        present_sources=["games"],
        snapshots_used=1,
        different_from_reference=0,
    )


def test_only_completed_weeks_without_a_scored_file_are_scored_and_files_are_written(monkeypatch):
    called = []

    def fake_score_week(week, season, **kwargs):
        called.append(week)
        return _fake_week(week)

    monkeypatch.setattr(results_loop, "score_week", fake_score_week)
    monkeypatch.setattr(results_update, "load_projection_snapshots", lambda: [])
    report = update_results(_Cfg(), season=2026, write_sheet=False, fetchers=_fetchers((1, 2)))
    assert called == [1, 2] and report.weeks_scored == [1, 2]  # week 3 is not complete
    assert results_loop.scored_path(2026, 1).exists() and results_loop.unmatched_path(2026, 2).exists()

    called.clear()
    again = update_results(_Cfg(), season=2026, write_sheet=False, fetchers=_fetchers((1, 2)))
    assert called == [] and "already scored" in again.lines[0]  # nothing new to do

    forced = update_results(_Cfg(), season=2026, week=2, write_sheet=False, fetchers=_fetchers((1, 2)))
    assert called == [2] and forced.weeks_scored == [2]  # --week rescoring


def test_a_week_with_no_archived_projections_is_skipped_with_a_message(monkeypatch):
    monkeypatch.setattr(results_loop, "score_week", lambda week, season, **kw: None)
    monkeypatch.setattr(results_update, "load_projection_snapshots", lambda: [])
    report = update_results(_Cfg(), season=2026, write_sheet=False, fetchers=_fetchers((1,)))
    assert report.weeks_scored == [] and "skipped" in report.lines[0]


def test_the_signals_backfill_never_fails_a_results_update(monkeypatch, tmp_path):
    """No model cache and every free file unavailable: the update still succeeds and says what is missing."""
    from dfs import signals_data
    from dfs.model import data as model_data
    from dfs.sources import nflverse_files as nf

    def gone(*args, **kwargs):
        raise nf.ContextFetchError("not published")

    monkeypatch.setattr(signals_data, "SIGNALS_DIR", tmp_path / "signals")
    monkeypatch.setattr(model_data, "read_games", gone)
    monkeypatch.setattr(results_loop, "score_week", lambda week, season, **kw: _fake_week(week))
    monkeypatch.setattr(results_update, "load_projection_snapshots", lambda: [])
    fetchers = signals_data.Fetchers(
        ffo=gone, injuries=gone, depth=gone, stats_player=gone, stats_team=gone, schedule=gone
    )
    report = update_results(
        _Cfg(),
        season=2026,
        write_sheet=False,
        write_signals=True,
        fetchers=_fetchers((1,)),
        signal_fetchers=fetchers,
    )
    text = "\n".join(report.lines)
    assert report.weeks_scored == [1]
    assert "UM unavailable" in text and "Signals input unavailable" in text
    assert report.published is True


def test_an_offline_update_does_not_touch_the_signals(monkeypatch):
    called = []
    monkeypatch.setattr(results_update, "_backfill_signals", lambda *a, **k: called.append(1))
    monkeypatch.setattr(results_loop, "score_week", lambda week, season, **kw: _fake_week(week))
    monkeypatch.setattr(results_update, "load_projection_snapshots", lambda: [])
    update_results(_Cfg(), season=2026, write_sheet=False, fetchers=_fetchers((1,)))
    assert called == []  # write_sheet=False means offline: no network, so no signals either
