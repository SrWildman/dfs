"""Compute the current NFL season/week without a hardcoded date.

The old code (utils/scraper_common.py) hardcoded
`NFL_SEASON_START_DATE = datetime(2025, 9, 5)` with a comment saying
"update each year" -- which nobody did, so every auto-detected week
capped out at 18 as soon as the 2025 season passed. This computes the
season kickoff (the Thursday after Labor Day, which is how the NFL has
opened its last several seasons) from the calendar itself, so it never
goes stale. `--week`/`--season` CLI flags always override this.
"""

from __future__ import annotations

from datetime import date, timedelta

MIN_WEEK = 1
MAX_WEEK = 18


def current_season(today: date | None = None) -> int:
    """NFL seasons are named for the year they start in (the "2025 season"
    runs Sep 2025 - Feb 2026). Jan-Jul counts as still-previous-season."""
    today = today or date.today()
    return today.year - 1 if today.month <= 7 else today.year


def season_kickoff(season: int) -> date:
    """Thursday after Labor Day (the first Monday of September)."""
    sept_first = date(season, 9, 1)
    days_to_first_monday = (7 - sept_first.weekday()) % 7
    labor_day = sept_first + timedelta(days=days_to_first_monday)
    return labor_day + timedelta(days=3)


def current_week(today: date | None = None) -> int:
    today = today or date.today()
    season = current_season(today)
    kickoff = season_kickoff(season)
    if today < kickoff:
        return MIN_WEEK
    days_in = (today - kickoff).days
    week = days_in // 7 + 1
    return max(MIN_WEEK, min(MAX_WEEK, week))


def week_start_date(week: int, season: int) -> date:
    """The calendar date a given week starts on -- used to tell which
    locally-saved snapshots (see store.py) belong to the current week versus
    a previous one, since snapshot filenames only carry a timestamp, not a
    week number. Same 7-day cadence `current_week` assumes, from the same
    kickoff anchor."""
    return season_kickoff(season) + timedelta(weeks=week - 1)
