"""Game-day injury news for late swap: ESPN's public injuries feed laid over the last sync's `Avail`.

The sync's `Avail` (TFFB's status) is only as fresh as the last `dfs sync`, and the nflverse injury report is
weekly. In the 4:05 / 4:25 window news breaks after both. ESPN's `site.api.espn.com/apis/site/v2/sports/
football/nfl/injuries` (public, no login, updated through game day, each entry stamped with its time) is read
on request: `dfs lineups late-swap --refresh-injuries`. Same terms as the other ESPN source (personal,
non-commercial use, Sam's go-ahead 2026-10-13); nothing here touches DraftKings.

`apply_news` only ESCALATES: an ESPN Out / Injured Reserve / Suspension / Doubtful overrides a softer `Avail`,
and a Questionable fills a blank one. ESPN's "Active" never clears a status the sync carries (it may be
stale). Players are joined by normalised name; a name held by two edge rows is skipped, never guessed.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

import httpx
import pandas as pd

from dfs.player_join import normalize_name

URL = "https://site.api.espn.com/apis/site/v2/sports/football/nfl/injuries"
TIMEOUT_SECONDS = 20.0
# ESPN's status -> the sync's `Avail` spelling (`derived.OUT_STATUSES`: OUT, IR; "D" = doubtful, counts as out
# for injury beneficiaries but still swappable here, like Q: only OUT and IR are removed from the swap pool)
ESPN_TO_AVAIL = {
    "Out": "OUT",
    "Injured Reserve": "IR",
    "Suspension": "OUT",
    "Doubtful": "D",
    "Questionable": "Q",
}
SEVERITY = {"": 0, "Q": 1, "D": 2, "OUT": 3, "IR": 3}
NEWS_COLUMNS = ["Name", "Status", "Updated", "Note"]


class NewsError(Exception):
    """The feed could not be read; late swap carries on with the sync's `Avail`."""


def parse_news(payload: dict) -> pd.DataFrame:
    """`NEWS_COLUMNS` rows (UTC `Updated`) from ESPN's injuries payload."""
    rows = []
    for team in payload.get("injuries", []):
        for item in team.get("injuries", []):
            name = (item.get("athlete") or {}).get("displayName")
            if not name:
                continue
            rows.append(
                {
                    "Name": name,
                    "Status": item.get("status", ""),
                    "Updated": pd.to_datetime(item.get("date"), utc=True, errors="coerce"),
                    "Note": item.get("shortComment", ""),
                }
            )
    return pd.DataFrame(rows, columns=NEWS_COLUMNS)


def fetch_news(client: httpx.Client | None = None) -> pd.DataFrame:
    try:
        with client or httpx.Client(timeout=TIMEOUT_SECONDS, follow_redirects=True) as c:
            response = c.get(URL)
            response.raise_for_status()
            return parse_news(response.json())
    except (httpx.HTTPError, ValueError) as e:
        raise NewsError(str(e)) from e


@dataclass(frozen=True)
class Change:
    name: str
    before: str
    after: str
    updated: datetime | None
    note: str


def apply_news(edge: pd.DataFrame, news: pd.DataFrame) -> tuple[pd.DataFrame, list[Change]]:
    """`edge` with `Avail` escalated by the news, and the changes made (worst first)."""
    out = edge.copy()
    if news.empty or "Avail" not in out.columns:
        return out, []
    keys = out["Name"].map(normalize_name)
    holders = keys.value_counts()
    changes = []
    for rec in news.itertuples(index=False):
        new = ESPN_TO_AVAIL.get(rec.Status)
        key = normalize_name(rec.Name)
        if new is None or holders.get(key, 0) != 1:
            continue
        idx = out.index[keys == key][0]
        before = "" if pd.isna(out.at[idx, "Avail"]) else str(out.at[idx, "Avail"])
        if SEVERITY.get(new, 0) > SEVERITY.get(before, 0):
            out.at[idx, "Avail"] = new
            updated = None if pd.isna(rec.Updated) else rec.Updated.to_pydatetime()
            changes.append(Change(str(out.at[idx, "Name"]), before, new, updated, rec.Note))
    changes.sort(key=lambda c: (-SEVERITY[c.after], c.name))
    return out, changes


def age_minutes(then: datetime, now: datetime | None = None) -> float:
    return ((now or datetime.now(UTC)) - then).total_seconds() / 60.0
