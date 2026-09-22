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


def test_current_week_rolls_over_tuesday_not_kickoff_thursday():
    # Root cause found live 2026-09-22 (a Tuesday): week 2's Monday Night
    # game was already over, the sheet was already titled "Week 3", and
    # `dfs week new`/`dfs sync` had already been pointed at it -- but
    # `current_week()` still returned 2 because the old anchor didn't
    # roll the number over until week 3's own Thursday kickoff, two days
    # later. DK's contest lobby, Vegas lines, and the waiver wire all
    # already treat Tuesday as the start of the next week.
    assert nfl_calendar.current_week(date(2026, 9, 21)) == 2  # Monday: week 2's MNF night
    assert nfl_calendar.current_week(date(2026, 9, 22)) == 3  # Tuesday: week 3 starts
    assert nfl_calendar.current_week(date(2026, 9, 23)) == 3  # Wednesday: still week 3
    assert nfl_calendar.current_week(date(2026, 9, 24)) == 3  # Thursday: week 3 kickoff


def test_current_week_caps_at_max():
    assert nfl_calendar.current_week(date(2027, 6, 1)) == nfl_calendar.MAX_WEEK


def test_week_start_date_is_the_tuesday_before_kickoff_for_week_one():
    # Week 1 "starts" (numbering-wise) two days before its own Thursday
    # kickoff -- see WEEK_ROLLOVER_LEAD_DAYS.
    assert nfl_calendar.week_start_date(1, 2026) == nfl_calendar.season_kickoff(2026) - timedelta(days=2)


def test_week_start_date_advances_seven_days_per_week():
    week_one_start = nfl_calendar.week_start_date(1, 2026)
    assert nfl_calendar.week_start_date(2, 2026) == week_one_start + timedelta(days=7)
    assert nfl_calendar.week_start_date(3, 2026) == week_one_start + timedelta(days=14)


def test_week_for_date_matches_current_week_for_the_same_date():
    # current_week is now just week_for_date(today, current_season(today)).
    for d in (date(2026, 9, 4), date(2026, 9, 11), date(2026, 9, 24), date(2027, 6, 1)):
        assert nfl_calendar.week_for_date(d, nfl_calendar.current_season(d)) == nfl_calendar.current_week(d)


def test_week_for_date_sorts_a_multi_week_span_of_real_dates():
    # Fix 2.17: a DK contest-history export spans many weeks -- each
    # entry's own date determines its week, not the file as a whole.
    # Real contest dates (Thu/Sun/Mon) are always comfortably inside one
    # 7-day window regardless of which day the boundary itself falls on,
    # so this sorting is unaffected by the Tuesday-anchor fix -- only
    # kickoff + 6 (a Wednesday, which is never a real contest date) moved,
    # from week 1 to week 2, since week 2's numbering now starts the
    # Tuesday before it.
    season = 2026
    kickoff = nfl_calendar.season_kickoff(season)
    assert nfl_calendar.week_for_date(kickoff, season) == 1
    assert nfl_calendar.week_for_date(kickoff + timedelta(days=6), season) == 2
    assert nfl_calendar.week_for_date(kickoff + timedelta(days=7), season) == 2
    assert nfl_calendar.week_for_date(kickoff + timedelta(days=21), season) == 4
