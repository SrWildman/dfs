from datetime import date, timedelta

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


def test_week_start_date_matches_kickoff_for_week_one():
    assert nfl_calendar.week_start_date(1, 2026) == nfl_calendar.season_kickoff(2026)


def test_week_start_date_advances_seven_days_per_week():
    kickoff = nfl_calendar.season_kickoff(2026)
    assert nfl_calendar.week_start_date(2, 2026) == kickoff + timedelta(days=7)
    assert nfl_calendar.week_start_date(3, 2026) == kickoff + timedelta(days=14)


def test_week_for_date_matches_current_week_for_the_same_date():
    # current_week is now just week_for_date(today, current_season(today)).
    for d in (date(2026, 9, 4), date(2026, 9, 11), date(2026, 9, 24), date(2027, 6, 1)):
        assert nfl_calendar.week_for_date(d, nfl_calendar.current_season(d)) == nfl_calendar.current_week(d)


def test_week_for_date_sorts_a_multi_week_span_of_real_dates():
    # Fix 2.17: a DK contest-history export spans many weeks -- each
    # entry's own date determines its week, not the file as a whole.
    season = 2026
    kickoff = nfl_calendar.season_kickoff(season)
    assert nfl_calendar.week_for_date(kickoff, season) == 1
    assert nfl_calendar.week_for_date(kickoff + timedelta(days=6), season) == 1
    assert nfl_calendar.week_for_date(kickoff + timedelta(days=7), season) == 2
    assert nfl_calendar.week_for_date(kickoff + timedelta(days=21), season) == 4
