"""Practice reports for Questionable / Doubtful players: the week's DNP / Limited / Full trend, from our own
syncs.

**What exists.** nflverse's injury file (`sources/nflverse_injuries.py`) carries one `practice_status` per
player-week: the LATEST one, overwritten as the week goes on, with no date and no Wednesday / Thursday /
Friday breakdown (checked 2026-11-05: the parquet's columns are season, game type, team, week, ids, names, the
report and practice injuries, `report_status` and `practice_status`; no date column). No free source we use
has the daily participation. But every `dfs sync` keeps a raw snapshot of that file
(`data/raw/nflverse_injuries/<UTC stamp>.csv`), so the trend is in our own history: each snapshot is what the
file said when that sync ran.

**The trend** (Sam, 2026-11-05): each distinct status with the day we SAW it (Eastern), e.g. `DNP Wed ·
Limited Thu · Full Fri`. A team's report is usually posted the afternoon of its practice, so a Thursday
morning sync holds Wednesday's report: the day is when we saw it, never a claim about the practice day. A
practice day (Wednesday to Friday of the week) with no sync at all is named in `gaps`, so a missing day is
never read as "no change". The direction (`up` / `down` / `flat`) compares the last status with the first (DNP
< Limited < Full).

Pure apart from `load_history`, which only reads the snapshot files. No network, no sheet.
"""

from __future__ import annotations

import csv
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from dfs.player_join import normalize_name

ET = ZoneInfo("America/New_York")
SNAPSHOT_STAMP = "%Y%m%dT%H%M%SZ"  # store's raw snapshot file name
SNAPSHOT_SUBDIR = Path("raw") / "nflverse_injuries"

DNP, LIMITED, FULL = "DNP", "Limited", "Full"
LEVEL = {DNP: 0, LIMITED: 1, FULL: 2}
# Wednesday, Thursday, Friday: the practice days of a Sunday game's week (Monday = 0).
PRACTICE_WEEKDAYS = (2, 3, 4)
DAY = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


def short_status(text: object) -> str:
    """nflverse's wording as DNP / Limited / Full ("" for anything else, e.g. blank)."""
    t = str(text or "").strip().lower()
    if t.startswith("did not"):
        return DNP
    if t.startswith("limited"):
        return LIMITED
    if t.startswith("full"):
        return FULL
    return ""


@dataclass(frozen=True)
class Seen:
    """One snapshot's line for one player: when we saw it and what it said."""

    at: datetime  # UTC
    practice: str  # DNP / Limited / Full / ""
    report: str  # Out / Doubtful / Questionable / "" (the game-day designation, set late in the week)


@dataclass
class Trend:
    """A player's week: the distinct statuses in the order we saw them (`(ET weekday, status)`)."""

    steps: list[tuple[str, str]] = field(default_factory=list)
    report: str = ""  # the newest non-blank report status
    last_seen: datetime | None = None

    @property
    def latest(self) -> str:
        return self.steps[-1][1] if self.steps else ""

    @property
    def direction(self) -> str:
        if len(self.steps) < 2:
            return "flat"
        first, last = LEVEL[self.steps[0][1]], LEVEL[self.steps[-1][1]]
        return "up" if last > first else "down" if last < first else "flat"

    def text(self) -> str:
        """`DNP Wed · Limited Thu · Full Fri` ("" when no status was ever seen)."""
        return " · ".join(f"{status} {day}" for day, status in self.steps)


def trend_from(seen: Iterable[Seen]) -> Trend:
    """Collapse snapshots to one status per Eastern day (the last that day), then to the changes."""
    by_day: dict[date, Seen] = {}
    for s in sorted(seen, key=lambda s: s.at):
        by_day[s.at.astimezone(ET).date()] = s
    out = Trend()
    for day in sorted(by_day):
        s = by_day[day]
        if s.report:
            out.report = s.report
        out.last_seen = s.at
        if not s.practice:
            continue
        if not out.steps or out.steps[-1][1] != s.practice:
            out.steps.append((DAY[day.weekday()], s.practice))
    return out


def player_key(name: object, team: object) -> tuple[str, str]:
    return normalize_name(name), str(team or "").strip().upper()


def snapshot_time(path: Path) -> datetime | None:
    try:
        return datetime.strptime(path.stem, SNAPSHOT_STAMP).replace(tzinfo=UTC)
    except ValueError:
        return None


@dataclass
class WeekPractice:
    """Every reported player's trend this week, the syncs it was read from and the practice days we missed."""

    trends: dict[tuple[str, str], Trend]
    syncs: int
    gaps: list[str]  # "Thu" for a practice day of the week (Wed-Fri, up to today) with no sync at all

    def for_player(self, name: object, team: object) -> Trend | None:
        return self.trends.get(player_key(name, team))

    def note(self) -> str:
        base = (
            f"Practice: nflverse's latest status at each of {self.syncs} syncs this week, by the day we saw "
            "it "
            "(a report is posted the afternoon of the practice)."
        )
        if self.gaps:
            base += f" No sync on {', '.join(self.gaps)}, so a change that day shows on the next sync's day."
        return base


def week_window(season: int, week: int) -> tuple[date, date]:
    """The Eastern dates a week's reports can be seen on: its Tuesday to the Monday after."""
    from dfs import nfl_calendar

    start = nfl_calendar.week_start_date(week, season)
    return start, start + timedelta(days=6)


def build(
    rows_by_snapshot: Iterable[tuple[datetime, list[dict]]],
    *,
    season: int,
    week: int,
    now: datetime,
) -> WeekPractice:
    """Pure: every snapshot's rows (`(UTC time, rows)`), reduced to this season-week's trends."""
    first, last = week_window(season, week)
    per_player: dict[tuple[str, str], list[Seen]] = {}
    days_seen: set[date] = set()
    syncs = 0
    for at, rows in rows_by_snapshot:
        day = at.astimezone(ET).date()
        if not (first <= day <= last):
            continue
        mine = [r for r in rows if _int(r.get("season")) == season and _int(r.get("week")) == week]
        if not mine:
            continue
        syncs += 1
        days_seen.add(day)
        for r in mine:
            per_player.setdefault(player_key(r.get("Name"), r.get("Team")), []).append(
                Seen(at, short_status(r.get("practice_status")), str(r.get("report_status") or "").strip())
            )
    today = now.astimezone(ET).date()
    gaps = [
        DAY[d.weekday()]
        for d in (first + timedelta(days=i) for i in range(7))
        if d.weekday() in PRACTICE_WEEKDAYS and d <= today and d not in days_seen
    ]
    return WeekPractice({k: trend_from(v) for k, v in per_player.items()}, syncs, gaps)


def _int(value: object) -> int | None:
    try:
        return int(float(str(value)))
    except (TypeError, ValueError):
        return None


def load_history(data_dir: Path, *, season: int, week: int, now: datetime) -> WeekPractice | None:
    """The week's trends from the raw snapshots under `data_dir`; None when there are none for the week."""
    folder = data_dir / SNAPSHOT_SUBDIR
    if not folder.exists():
        return None
    first, last = week_window(season, week)
    snaps = []
    for path in sorted(folder.glob("*.csv")):
        at = snapshot_time(path)
        if at is None or not (first <= at.astimezone(ET).date() <= last):
            continue
        try:
            with path.open(newline="") as fh:
                snaps.append((at, list(csv.DictReader(fh))))
        except OSError:
            continue
    result = build(snaps, season=season, week=week, now=now)
    return result if result.syncs else None


def by_edge_id(practice: WeekPractice | None, edge: Iterable[dict]) -> dict[str, Trend]:
    """Trends keyed by EdgeRaw `Id`, for the players the snapshots name (matched on name and team)."""
    if practice is None:
        return {}
    out = {}
    for r in edge:
        trend = practice.for_player(r.get("Name"), r.get("Team"))
        if trend is not None and trend.steps and r.get("Id"):
            out[str(r["Id"])] = trend
    return out
