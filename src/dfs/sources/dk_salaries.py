"""DraftKings NFL salaries: draftgroups + player-pool CSV, both unauthenticated.

Ported from scrapers/draftkings/scraper.py, which had already worked out the
right heuristic for finding "the main slate" (Fantasy Football Millionaire
contest type, Sunday-afternoon-only games, largest such slate by game count)
but assumed the CSV endpoint needed a logged-in browser session ("(LOCKED)"
placeholder data) and fell back to webbrowser.open() + an AppleScript Cmd+W
that could close whatever window happened to be frontmost. Verified live: the
CSV endpoint returns real salaries with no auth at all, so this is now a
plain two-request fetch -- no browser involved.
"""

from __future__ import annotations

import re
from datetime import datetime

import httpx
import pandas as pd

from dfs.log import get_logger
from dfs.sources.base import Source, SyncContext

log = get_logger("sources.draftkings")

DRAFTGROUPS_URL = "https://api.draftkings.com/draftgroups/v1/"
SALARY_CSV_URL = "https://www.draftkings.com/lineup/getavailableplayerscsv"
MILLIONAIRE_CONTEST_TYPE_ID = 21
_HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"}

_DATE_FORMATS = (
    "%Y-%m-%dT%H:%M:%S.%fZ",
    "%Y-%m-%dT%H:%M:%SZ",
    "%Y-%m-%d %H:%M:%S",
    "%m/%d/%Y %H:%M:%S",
    "%m-%d-%Y %H:%M:%S",
)


class DkSalariesFetchError(Exception):
    pass


def _is_sunday_afternoon(start_time: str) -> bool:
    """12 PM - 5 PM ET on a Sunday. Excludes SNF/MNF/Thursday games."""
    if not start_time:
        return False

    for fmt in _DATE_FORMATS:
        try:
            dt = datetime.strptime(start_time, fmt)
            return dt.weekday() == 6 and 12 <= dt.hour <= 17
        except ValueError:
            continue

    time_match = re.search(r"(\d{1,2}):(\d{2})(AM|PM)\s*ET", start_time, re.IGNORECASE)
    date_match = re.search(r"(\d{1,2}/\d{1,2}/\d{4})", start_time)
    if time_match and date_match:
        date = datetime.strptime(date_match.group(1), "%m/%d/%Y")
        if date.weekday() != 6:
            return False
        hour = int(time_match.group(1))
        if time_match.group(3).upper() == "PM" and hour != 12:
            hour += 12
        elif time_match.group(3).upper() == "AM" and hour == 12:
            hour = 0
        return 12 <= hour <= 17

    date_match = re.search(r"(\d{4}-\d{2}-\d{2})", start_time)
    if date_match:
        return datetime.strptime(date_match.group(1), "%Y-%m-%d").weekday() == 6

    return False


def find_main_slate_draft_group() -> int:
    """Find the largest Sunday-afternoon-only Millionaire-type contest's draftGroupId."""
    resp = httpx.get(DRAFTGROUPS_URL, headers=_HEADERS, timeout=30)
    resp.raise_for_status()
    data = resp.json()

    candidates = []
    for contest in data.get("draftGroups", []):
        if contest["contestType"]["sport"] != "NFL":
            continue
        if contest.get("draftGroupState") != "Upcoming":
            continue
        if contest["contestType"]["contestTypeId"] != MILLIONAIRE_CONTEST_TYPE_ID:
            continue

        games = contest.get("games", [])
        if not games:
            continue

        starts = [
            g.get("startTime")
            or g.get("startDate")
            or g.get("gameTime")
            or g.get("date")
            or g.get("startDateTime")
            or ""
            for g in games
        ]
        sunday_afternoon = [_is_sunday_afternoon(s) for s in starts]
        if sunday_afternoon and all(sunday_afternoon):
            candidates.append((len(games), contest["draftGroupId"]))

    if not candidates:
        raise DkSalariesFetchError(
            "No Sunday-afternoon-only Fantasy Football Millionaire contest found "
            "in DraftKings' current draftgroups."
        )

    candidates.sort(reverse=True)
    game_count, draft_group_id = candidates[0]
    log.info("selected draft group %s (%d Sunday afternoon games)", draft_group_id, game_count)
    return draft_group_id


_ISO_RE = re.compile(
    r"^(\d{4}-\d{2}-\d{2})[T ](\d{2}):(\d{2})(?::(\d{2})(?:\.\d+)?)?\s*(Z|[+-]\d{2}:?\d{2})?$"
)


def kickoff_eastern(start_time: str) -> datetime | None:
    """A draftgroups game time as an Eastern wall-clock datetime (naive). DraftKings sends UTC with a `Z`
    (`2026-11-26T17:30:00.0000000Z`, seven fractional digits); a time with no zone is read as Eastern
    already. None when it cannot be read. (The main slate's `_is_sunday_afternoon` is unchanged.)"""
    from zoneinfo import ZoneInfo

    m = _ISO_RE.match((start_time or "").strip())
    if not m:
        return None
    day, hh, mm, ss, zone = m.groups()
    naive = datetime.fromisoformat(f"{day}T{hh}:{mm}:{ss or '00'}")
    if not zone:
        return naive
    offset = "+00:00" if zone == "Z" else (zone if ":" in zone else f"{zone[:3]}:{zone[3:]}")
    aware = datetime.fromisoformat(f"{naive.isoformat()}{offset}")
    return aware.astimezone(ZoneInfo("America/New_York")).replace(tzinfo=None)


def _game_start(game: dict) -> str:
    return (
        game.get("startTime")
        or game.get("startDate")
        or game.get("gameTime")
        or game.get("date")
        or game.get("startDateTime")
        or ""
    )


def pick_slate_draft_group(data: dict, key: str, season: int, week: int) -> tuple[int, int]:
    """`(draftGroupId, games)` of a secondary slate (`slate.py`): the largest Classic (contest type
    `MILLIONAIRE_CONTEST_TYPE_ID`, the type the main slate and the salary CSV use) upcoming NFL draft group
    whose games ALL kick off, in Eastern time, on the slate's date of the NFL week (Thanksgiving = that
    week's Thursday). Ties go to the higher id. Raises DkSalariesFetchError when there is none."""
    from dfs import slate

    day = slate.slate_date(key, season, week)
    candidates = []
    seen = []  # every upcoming NFL Classic group, for the error: what DraftKings did list
    for group in data.get("draftGroups", []):
        ctype = group.get("contestType") or {}
        if ctype.get("sport") != "NFL" or group.get("draftGroupState") != "Upcoming":
            continue
        if ctype.get("contestTypeId") != MILLIONAIRE_CONTEST_TYPE_ID:
            continue
        games = group.get("games") or []
        kicks = [kickoff_eastern(_game_start(g)) for g in games]
        seen.append(_describe_group(group.get("draftGroupId"), kicks))
        if kicks and all(k is not None and k.date() == day for k in kicks):
            candidates.append((len(games), group["draftGroupId"]))
    if not candidates:
        listed = "; ".join(seen[:12]) or "none"
        raise DkSalariesFetchError(
            f"No Classic draft group whose games are all on {day:%A %b %d} (the {key} slate of Week {week}) "
            f"in DraftKings' current draftgroups (not posted yet, or already started). Upcoming NFL Classic "
            f"groups listed: {listed}."
        )
    candidates.sort(reverse=True)
    games, group_id = candidates[0]
    return group_id, games


def _describe_group(group_id: object, kicks: list[datetime | None]) -> str:
    """`138500 (11 games, Sun Nov 29 1:00 pm - 4:25 pm ET)`; unreadable times are counted."""
    known = sorted(k for k in kicks if k is not None)
    if not known:
        return f"{group_id} ({len(kicks)} games, no readable kickoff)"

    def at(k: datetime) -> str:
        return f"{k:%a %b} {k.day} {k.strftime('%I:%M %p').lstrip('0').lower()}"

    span = at(known[0]) if known[0] == known[-1] else f"{at(known[0])} - {at(known[-1])}"
    return f"{group_id} ({len(kicks)} games, {span} ET)"


def find_slate_draft_group(key: str, season: int, week: int) -> int:
    """The secondary slate's draftGroupId (`pick_slate_draft_group`), from the same public draftgroups list
    the main slate's picker reads."""
    resp = httpx.get(DRAFTGROUPS_URL, headers=_HEADERS, timeout=30)
    resp.raise_for_status()
    group_id, games = pick_slate_draft_group(resp.json(), key, season, week)
    log.info("selected draft group %s (%d games, the %s slate)", group_id, games, key)
    return group_id


class DkSalariesSource(Source):
    name = "draftkings"

    def fetch(self, ctx: SyncContext) -> pd.DataFrame:
        from dfs import slate

        key = slate.current()
        if key == slate.MAIN:
            draft_group_id = find_main_slate_draft_group()
        else:
            draft_group_id = find_slate_draft_group(key, ctx.season, ctx.week)
        resp = httpx.get(
            SALARY_CSV_URL,
            params={"contestTypeId": MILLIONAIRE_CONTEST_TYPE_ID, "draftGroupId": draft_group_id},
            headers=_HEADERS,
            timeout=30,
            follow_redirects=True,
        )
        resp.raise_for_status()
        if "(LOCKED)" in resp.text:
            raise DkSalariesFetchError(
                "DraftKings returned locked placeholder salaries -- this endpoint may "
                "now require an authenticated session (this tool never logs in to DraftKings)."
            )

        from io import StringIO

        df = pd.read_csv(StringIO(resp.text))
        if df.empty:
            raise DkSalariesFetchError(f"Salary CSV for draft group {draft_group_id} was empty.")
        return df
