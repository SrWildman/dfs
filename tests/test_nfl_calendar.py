from datetime import date

from dfs import nfl_calendar


def test_current_season_boundary():
    assert nfl_calendar.current_season(date(2026, 9, 4)) == 2026
    assert nfl_calendar.current_season(date(2027, 1, 15)) == 2026
    assert nfl_calendar.current_season(date(2027, 8, 1)) == 2027


def test_season_kickoff_is_thursday_after_labor_day():
    kickoff = nfl_calendar.season_kickoff(2026)
    assert kickoff == date(2026, 9, 10)
    assert kickoff.weekday() == 3  # Thursday


def test_current_week_before_kickoff_is_one():
    assert nfl_calendar.current_week(date(2026, 9, 4)) == 1


def test_current_week_advances_weekly_after_kickoff():
    assert nfl_calendar.current_week(date(2026, 9, 11)) == 1
    assert nfl_calendar.current_week(date(2026, 9, 17)) == 2
    assert nfl_calendar.current_week(date(2026, 9, 24)) == 3


def test_current_week_caps_at_max():
    assert nfl_calendar.current_week(date(2027, 6, 1)) == nfl_calendar.MAX_WEEK
