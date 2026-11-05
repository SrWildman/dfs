"""Practice trends from our own nflverse injury snapshots (Plan K item 5)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from dfs import practice

SEASON, WEEK = 2026, 9


def at(day_offset: int, hour_utc: int = 23) -> datetime:
    """A UTC instant `day_offset` days after the week's Tuesday (evening ET, after the report is posted)."""
    first, _ = practice.week_window(SEASON, WEEK)
    return datetime(first.year, first.month, first.day, hour_utc, tzinfo=UTC) + timedelta(days=day_offset)


def row(name="Tyler Allgeier", team="ATL", status="", report="", season=SEASON, week=WEEK) -> dict:
    return {
        "season": season,
        "week": week,
        "Name": name,
        "Team": team,
        "practice_status": status,
        "report_status": report,
    }


def test_nflverse_wording_is_shortened():
    assert practice.short_status("Did Not Participate In Practice") == "DNP"
    assert practice.short_status("Limited Participation in Practice") == "Limited"
    assert practice.short_status("Full Participation in Practice") == "Full"
    assert practice.short_status("") == ""


def test_a_trend_keeps_one_status_per_day_and_only_the_changes():
    snaps = [
        (at(1, 17), [row(status="Did Not Participate In Practice")]),  # Wed 1 pm ET
        (at(1), [row(status="Did Not Participate In Practice")]),  # Wed 7 pm ET: same day, one step
        (at(2), [row(status="Limited Participation in Practice")]),  # Thu
        (at(3), [row(status="Limited Participation in Practice", report="Questionable")]),  # Fri, no change
    ]
    week = practice.build(snaps, season=SEASON, week=WEEK, now=at(3))
    trend = week.for_player("Tyler Allgeier", "atl")
    assert trend.text() == "DNP Wed · Limited Thu"
    assert trend.direction == "up"
    assert trend.report == "Questionable"
    assert week.syncs == 4
    assert week.gaps == []


def test_a_missed_practice_day_is_named_and_other_weeks_are_ignored():
    snaps = [
        (at(1), [row(status="Full Participation in Practice")]),  # Wed
        (at(3), [row(status="Did Not Participate In Practice")]),  # Fri (no Thursday sync)
        (at(3), [row(name="Last Week", status="Full Participation in Practice", week=WEEK - 1)]),
    ]
    week = practice.build(snaps, season=SEASON, week=WEEK, now=at(3))
    assert week.gaps == ["Thu"]
    assert week.for_player("Tyler Allgeier", "ATL").direction == "down"
    assert week.for_player("Last Week", "ATL") is None
    assert "No sync on Thu" in week.note()


def test_load_history_reads_the_raw_snapshot_files(tmp_path):
    folder = tmp_path / practice.SNAPSHOT_SUBDIR
    folder.mkdir(parents=True)
    header = "season,week,Team,GsisId,Name,Position,report_status,practice_status\n"
    for offset, status in ((1, "Did Not Participate In Practice"), (2, "Full Participation in Practice")):
        stamp = at(offset).strftime(practice.SNAPSHOT_STAMP)
        (folder / f"{stamp}.csv").write_text(
            header + f"{SEASON},{WEEK},ATL,00-1,Tyler Allgeier,RB,,{status}\n"
        )
    week = practice.load_history(tmp_path, season=SEASON, week=WEEK, now=at(2))
    assert week is not None and week.syncs == 2
    edge = [
        {"Id": "42", "Name": "Tyler Allgeier", "Team": "ATL"},
        {"Id": "7", "Name": "Nobody", "Team": "ATL"},
    ]
    assert {k: t.text() for k, t in practice.by_edge_id(week, edge).items()} == {"42": "DNP Wed · Full Thu"}


def test_no_snapshots_means_no_practice(tmp_path):
    assert practice.load_history(tmp_path, season=SEASON, week=WEEK, now=at(2)) is None
    assert practice.by_edge_id(None, [{"Id": "1"}]) == {}
