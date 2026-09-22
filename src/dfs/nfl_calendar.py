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

WEEK_ROLLOVER_LEAD_DAYS = 2
"""NFL weeks are treated as current Tuesday-to-Monday industry-wide (DK's
contest lobby, Vegas lines, the waiver wire) -- a week becomes "current"
the Tuesday after the previous week's Monday Night Football game, two
days before that week's own Thursday kickoff. Anchoring the week-number
boundary to the kickoff date itself instead of that Tuesday left
`current_week()` (and everything built on it: `SyncContext.current`,
`dfs week new`, `dfs sync`) reporting the PREVIOUS week's number for the
two days between Tuesday and Thursday -- exactly the days Sam runs the
weekly `dfs week new`/`dfs sync` loop. Found live 2026-09-22: a Tuesday,
with the sheet already titled "Week 3" and `dfs week new` already pointed
at it, `current_week()` still returned 2, and the odds source failed with
"No DraftKings odds found for week 2" as a direct result."""


def current_season(today: date | None = None) -> int:
    """NFL seasons are named for the year they start in (the "2025 season"
    runs Sep 2025 - Feb 2026). Jan-Jul counts as still-previous-season."""
    today = today or date.today()
    return today.year - 1 if today.month <= 7 else today.year


def season_kickoff(season: int) -> date:
    """Thursday after Labor Day (the first Monday of September) -- the
    actual date of the season's first game. Not used directly for week-
    number boundaries; see `_week_one_start`, which is this date minus
    `WEEK_ROLLOVER_LEAD_DAYS`."""
    sept_first = date(season, 9, 1)
    days_to_first_monday = (7 - sept_first.weekday()) % 7
    labor_day = sept_first + timedelta(days=days_to_first_monday)
    return labor_day + timedelta(days=3)


def _week_one_start(season: int) -> date:
    """The Tuesday week 1's numbering rolls over from -- see
    WEEK_ROLLOVER_LEAD_DAYS. Every week boundary (`week_for_date`,
    `week_start_date`) is measured from this date, not from kickoff
    itself."""
    return season_kickoff(season) - timedelta(days=WEEK_ROLLOVER_LEAD_DAYS)


def week_for_date(d: date, season: int) -> int:
    """Which NFL week `d` falls in, for a given `season` -- the same
    7-day-cadence math `current_week` uses for "today", generalized to
    any date (Fix 2.17: used to sort a DK contest-history export's real
    entry dates into weeks, rather than assuming the whole export is one
    week or asking the user which week it's for). Real game dates (Thu/
    Sun/Mon) are never within WEEK_ROLLOVER_LEAD_DAYS of this boundary,
    so this generalization doesn't change how historical entries land --
    only "today" during the Tue/Wed gap between weeks is affected."""
    start = _week_one_start(season)
    if d < start:
        return MIN_WEEK
    days_in = (d - start).days
    week = days_in // 7 + 1
    return max(MIN_WEEK, min(MAX_WEEK, week))


def current_week(today: date | None = None) -> int:
    today = today or date.today()
    return week_for_date(today, current_season(today))


def week_start_date(week: int, season: int) -> date:
    """The calendar date a given week starts on -- used to tell which
    locally-saved snapshots (see store.py) belong to the current week versus
    a previous one, since snapshot filenames only carry a timestamp, not a
    week number. Same 7-day cadence `week_for_date` assumes, from the same
    Tuesday anchor (`_week_one_start`) -- this is also `edge.py`'s baseline
    for "this week's" line movement, which needs to start Tuesday (when the
    week's lines actually open), not Thursday's kickoff."""
    return _week_one_start(season) + timedelta(weeks=week - 1)
