"""Real kickoff instants from TFFB's `GameStart`.

TFFB writes `GameStart` as **Eastern wall-clock time with a trailing "Z"**: `2026-09-20T16:05:00Z` is the
4:05 pm ET kickoff, not 4:05 pm UTC. Checked against nflverse's own kickoff times, which are published in
Eastern: 13:00, 16:05 and 16:25 match TFFB's strings exactly. Reading the "Z" literally puts every kickoff
four hours too early in the season's EDT weeks (five in EST, after the clocks change).

Everything that asks "has this game started?" must go through here: the late-swap check (`late_swap.py`),
the launcher's started/finished state (`cli.collect_launcher_state`) and the results loop's snapshot
selection (`results_loop.py`). Reading the column as UTC anywhere else reintroduces the bug.
"""

from __future__ import annotations

from datetime import datetime

import pandas as pd

KICKOFF_TIMEZONE = "America/New_York"


def kickoff_utc(values: pd.Series) -> pd.Series:
    """TFFB's `GameStart` strings as real UTC instants (NaT where blank or unparseable).

    The "Z" is dropped and the time localised to Eastern (DST-aware), then converted to UTC. The one
    hour a year that does not exist (spring forward) or occurs twice (fall back) becomes NaT; no game
    kicks off then."""
    naive = pd.to_datetime(values.astype(str).str.replace("Z", "", regex=False), errors="coerce")
    return naive.dt.tz_localize(KICKOFF_TIMEZONE, ambiguous="NaT", nonexistent="NaT").dt.tz_convert("UTC")


def parse_kickoff(value: object) -> datetime | None:
    """One `GameStart` value as a timezone-aware UTC datetime, or None when it is blank or unparseable."""
    if value is None or (isinstance(value, float) and pd.isna(value)) or not str(value).strip():
        return None
    parsed = kickoff_utc(pd.Series([value])).iloc[0]
    return None if pd.isna(parsed) else parsed.to_pydatetime()


def games_state(game_starts: pd.Series, now: datetime, finished_after_hours: float) -> tuple[bool, bool]:
    """`(any game has started, the last game has had `finished_after_hours` to finish)` at `now`.

    Both are False when no kickoff parses (nothing synced yet): never guess a state from missing data."""
    starts = kickoff_utc(game_starts).dropna()
    if starts.empty:
        return (False, False)
    stamp = pd.Timestamp(now)
    stamp = stamp.tz_localize("UTC") if stamp.tzinfo is None else stamp.tz_convert("UTC")
    started = bool((starts <= stamp).any())
    finished = started and (stamp - starts.max()).total_seconds() / 3600 >= finished_after_hours
    return (started, finished)
