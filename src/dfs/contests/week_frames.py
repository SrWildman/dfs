"""The players frame of a past week, from the DraftKings salary snapshots the weekly sync already keeps.

`field.py` needs each lineup player's team, opponent and salary *as of the contest's own week*, and EdgeRaw
only holds the current one. `data/raw/draftkings/` holds every salary file the sync has pulled, stamped in
UTC; the frame of a week is the **last snapshot taken before that week's Sunday 1:00 pm ET lock** whose games
are on that Sunday, kept to the **main slate** (Sunday kickoffs 12:00-17:59 ET, the repo's definition). Name,
position, team and salary come from the file; the opponent from `Game Info` ("DET@ARI 10/11/2026
04:25PM ET"). Nothing is guessed: a week with no snapshot has no frame and the caller says so.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from dfs import nfl_calendar

EASTERN = ZoneInfo("America/New_York")
LOCK_HOUR = 13  # Sunday 1:00 pm ET: the first main-slate kickoff
SUNDAY = 6
MAIN_SLATE_HOURS = range(12, 18)
SNAPSHOT_LOOKBACK_DAYS = 14  # a Sunday's salaries are posted by the Tuesday before
_STAMP = re.compile(r"^(\d{8}T\d{6})Z\.csv$")
_GAME_INFO = re.compile(
    r"^(?P<away>[A-Za-z]{2,4})@(?P<home>[A-Za-z]{2,4})\s+(?P<when>\d{2}/\d{2}/\d{4}\s+\d{1,2}:\d{2}[AP]M)"
)
FRAME_COLUMNS = ["Season", "Week", "Name", "Position", "Team", "Opponent", "Salary", "Snapshot"]


@dataclass(frozen=True)
class Snapshot:
    path: Path
    stamp: datetime  # UTC


def sunday_of(season: int, week: int) -> date:
    """The Sunday of an NFL week (the calendar's weeks start on a Tuesday)."""
    return nfl_calendar.week_start_date(week, season) + timedelta(days=5)


def lock_utc(season: int, week: int) -> datetime:
    """Sunday 1:00 pm ET of the week, in UTC."""
    sunday = sunday_of(season, week)
    return datetime(sunday.year, sunday.month, sunday.day, LOCK_HOUR, tzinfo=EASTERN).astimezone(UTC)


def snapshots(dk_dir: Path) -> list[Snapshot]:
    """Every `<UTC stamp>Z.csv` in the folder, oldest first."""
    out = []
    for path in Path(dk_dir).glob("*.csv"):
        found = _STAMP.match(path.name)
        if found:
            out.append(Snapshot(path, datetime.strptime(found.group(1), "%Y%m%dT%H%M%S").replace(tzinfo=UTC)))
    return sorted(out, key=lambda s: s.stamp)


def _games_on(path: Path) -> set[date]:
    """The dates of the games a salary file lists (Classic files carry one Sunday's games)."""
    try:
        info = pd.read_csv(path, usecols=["Game Info"], dtype=str)["Game Info"].dropna().unique()
    except (ValueError, OSError, pd.errors.ParserError):
        return set()
    dates = set()
    for text in info:
        found = _GAME_INFO.match(str(text).strip())
        if found:
            dates.add(datetime.strptime(found.group("when"), "%m/%d/%Y %I:%M%p").date())
    return dates


def find_snapshot(dk_dir: Path, season: int, week: int) -> Snapshot | None:
    """The last snapshot before the week's lock whose games are on that week's Sunday."""
    sunday, lock = sunday_of(season, week), lock_utc(season, week)
    earliest = lock - timedelta(days=SNAPSHOT_LOOKBACK_DAYS)
    for snap in reversed([s for s in snapshots(dk_dir) if earliest <= s.stamp <= lock]):
        if sunday in _games_on(snap.path):
            return snap
    return None


def _kickoff(when: str) -> datetime:
    return datetime.strptime(when, "%m/%d/%Y %I:%M%p")


def read_frame(path: Path, season: int, week: int) -> pd.DataFrame:
    """The main-slate players of one salary file as a `FRAME_COLUMNS` frame."""
    raw = pd.read_csv(path, dtype=str)
    rows = []
    for rec in raw.to_dict("records"):
        found = _GAME_INFO.match(str(rec.get("Game Info", "")).strip())
        if not found:
            continue
        kickoff = _kickoff(found.group("when"))
        if kickoff.weekday() != SUNDAY or kickoff.hour not in MAIN_SLATE_HOURS:
            continue
        team = str(rec["TeamAbbrev"]).strip().upper()
        away, home = found.group("away").upper(), found.group("home").upper()
        if team not in (away, home):
            continue
        rows.append(
            {
                "Season": season,
                "Week": week,
                "Name": rec["Name"],
                "Position": rec["Position"],
                "Team": team,
                "Opponent": home if team == away else away,
                "Salary": pd.to_numeric(rec["Salary"], errors="coerce"),
                "Snapshot": path.name,
            }
        )
    return pd.DataFrame(rows, columns=FRAME_COLUMNS)


def week_frame(dk_dir: Path, season: int, week: int) -> tuple[pd.DataFrame | None, str]:
    """`(frame, note)`: the week's frame and the snapshot used, or `(None, why not)`."""
    snap = find_snapshot(dk_dir, season, week)
    if snap is None:
        return None, f"no DraftKings salary snapshot before the Week {week} {season} lock in {dk_dir}"
    frame = read_frame(snap.path, season, week)
    if frame.empty:
        return None, f"{snap.path.name} has no main-slate players"
    return frame, snap.path.name
