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
            g.get("startTime") or g.get("startDate") or g.get("gameTime")
            or g.get("date") or g.get("startDateTime") or ""
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


class DkSalariesSource(Source):
    name = "draftkings"

    def fetch(self, ctx: SyncContext) -> pd.DataFrame:
        draft_group_id = find_main_slate_draft_group()
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
                "now require an authenticated session (run `dfs auth dk`)."
            )

        from io import StringIO

        df = pd.read_csv(StringIO(resp.text))
        if df.empty:
            raise DkSalariesFetchError(f"Salary CSV for draft group {draft_group_id} was empty.")
        return df
