"""ESPN's public fantasy API: weekly player projections, scored to DraftKings rules -- `EspnPts`.

**Access (checked 2026-10-13, Sam's go-ahead for personal use).** The `kona_player_info` view of the
league-defaults path that open-source fantasy tools use, `lm-api-reads.fantasy.espn.com/apis/v3/games/ffl/
seasons/{season}/segments/0/leaguedefaults/3`, answers a plain GET with an `X-Fantasy-Filter` header: no
cookies, no login. `www.espn.com/robots.txt` does not disallow the path and the API host serves no robots file
(403). Disney's Terms of Use (2.B) ban robots and scripts for anything beyond personal, non-commercial use;
Sam knowingly keeps the source for his own use. The data is never redistributed.

**What the endpoint returns.** One `stats` entry per player per requested id. The weekly projection is the
entry with `statSourceId == 1` (projected), `statSplitTypeId == 1` (a single week) and `scoringPeriodId ==
week` (its id is `"11" + season + week`, e.g. `1120266`; selected here by those fields, not by the string). An
entry with an empty `stats` dict is a player ESPN does not project (bye, out): NO projection, never zeros.
`appliedTotal` is ESPN's OWN fantasy scoring and is discarded: only the component stats feed `dk_scoring`.

**Stat ids, verified against nflverse** (Week 5 2026 actual game logs for 130+ players, Weeks 1-5 for the
defenses; exact match on every player-week with a value):

| id | stat | | id | stat |
|---|---|---|---|---|
| 3 | pass yards | | 99 | sacks |
| 4 | pass TDs | | 95 | interceptions |
| 20 | pass INTs | | 96 | fumble recoveries |
| 24 | rush yards | | 97 | blocked kicks |
| 25 | rush TDs | | 98 | safeties |
| 53 (41) | receptions | | 105 | defensive TDs |
| 42 | receiving yards | | 120 | points allowed |
| 43 | receiving TDs | | 19 / 26 / 44 | 2-pt: pass / rush / rec |
| 72 | fumbles lost | | | |

Receptions are id 53 in a projection (41 in a game log; 41 is the fallback). **Weakly verified**: 26 (rush
2-pt) and 98 (safeties) each occurred once in the sample, and the 2-point-return stat never occurred; none
of the three moves a projection by more than a few hundredths. A DST projection has no 2-point-return
field (DK's `DK_DST_SCORING` has none either).

ESPN's `proTeamId` maps to DraftKings' own team codes (`TEAM_CODES`); a defense is named "<Nickname> D/ST"
and is joined to DK by team alone, like every defense. Fails loudly in this module (a clear exception on a
missing or malformed response); `sync.run_sync` makes that soft for the whole sync.
"""

from __future__ import annotations

import json

import httpx
import pandas as pd

from dfs.dk_scoring import DST_STAT_FIELDS, OFFENSE_STAT_FIELDS, score_dst_frame, score_offense_frame
from dfs.log import get_logger
from dfs.sources.base import Source, SyncContext

log = get_logger("sources.espn_projections")

URL_TEMPLATE = (
    "https://lm-api-reads.fantasy.espn.com/apis/v3/games/ffl/seasons/{season}/segments/0/"
    "leaguedefaults/3?view=kona_player_info"
)
USER_AGENT = "dfs-personal-tool (single user, one request per sync)"
PROJECTED_SOURCE, SINGLE_WEEK_SPLIT = 1, 1
OFFENSE_SLOTS = [0, 2, 4, 6]  # QB, RB, WR, TE
DST_SLOTS = [16]
OFFENSE_LIMIT = 1200  # ESPN sorts by percent owned; ~320 players carry a projection in a normal week
DST_LIMIT = 40

# ESPN stat id -> dk_scoring field. `rec` lists 53 first (a projection), then 41 (a game log).
OFFENSE_STAT_IDS = {
    "pass_yd": (3,),
    "pass_td": (4,),
    "pass_int": (20,),
    "rush_yd": (24,),
    "rush_td": (25,),
    "rec": (53, 41),
    "rec_yd": (42,),
    "rec_td": (43,),
    "fum_lost": (72,),
}
TWO_POINT_IDS = (19, 26, 44)  # pass, rush, rec: summed into `two_pt`
DST_STAT_IDS = {
    "sack": 99,
    "def_int": 95,
    "fum_rec": 96,
    "blocked_kick": 97,
    "safety": 98,
    "def_td": 105,
    "points_allowed": 120,
}
POSITIONS = {1: "QB", 2: "RB", 3: "WR", 4: "TE", 16: "DST"}
# ESPN proTeamId -> DraftKings team code (0 = free agent, never projected).
TEAM_CODES = {
    1: "ATL",
    2: "BUF",
    3: "CHI",
    4: "CIN",
    5: "CLE",
    6: "DAL",
    7: "DEN",
    8: "DET",
    9: "GB",
    10: "TEN",
    11: "IND",
    12: "KC",
    13: "LV",
    14: "LAR",
    15: "MIA",
    16: "MIN",
    17: "NE",
    18: "NO",
    19: "NYG",
    20: "NYJ",
    21: "PHI",
    22: "ARI",
    23: "PIT",
    24: "LAC",
    25: "SF",
    26: "SEA",
    27: "TB",
    28: "WAS",
    29: "CAR",
    30: "JAX",
    33: "BAL",
    34: "HOU",
}


class EspnFetchError(Exception):
    pass


def filter_header(slots: list[int], limit: int, season: int, week: int) -> str:
    """The `X-Fantasy-Filter` value: these roster slots, ESPN's projected stats for this single week, sorted
    by percent owned (so a `limit` cuts the least-owned, never a starter)."""
    return json.dumps(
        {
            "players": {
                "filterSlotIds": {"value": slots},
                "filterStatsForSourceIds": {"value": [PROJECTED_SOURCE]},
                "filterStatsForSplitTypeIds": {"value": [SINGLE_WEEK_SPLIT]},
                "filterStatsForTopScoringPeriodIds": {
                    "value": 1,
                    "additionalValue": [f"11{season}{week}"],
                },
                "limit": limit,
                "offset": 0,
                "sortPercOwned": {"sortAsc": False, "sortPriority": 1},
            }
        }
    )


def weekly_projection(player: dict, week: int) -> dict[int, float] | None:
    """The player's projected component stats for `week` as `{stat id: value}`, or None when ESPN has no
    projection for him (no entry, or an empty one)."""
    for entry in player.get("stats") or []:
        if (
            entry.get("statSourceId") == PROJECTED_SOURCE
            and entry.get("statSplitTypeId") == SINGLE_WEEK_SPLIT
            and entry.get("scoringPeriodId") == week
        ):
            stats = entry.get("stats") or {}
            return {int(k): float(v) for k, v in stats.items()} if stats else None
    return None


def _value(stats: dict[int, float], ids: tuple[int, ...]) -> float:
    for stat_id in ids:
        if stat_id in stats:
            return stats[stat_id]
    return 0.0


def offense_row(player: dict, stats: dict[int, float]) -> dict:
    row = {
        "Name": str(player.get("fullName") or "").strip(),
        "Team": TEAM_CODES.get(player.get("proTeamId"), ""),
        "Position": POSITIONS.get(player.get("defaultPositionId"), ""),
    }
    for field, ids in OFFENSE_STAT_IDS.items():
        row[field] = _value(stats, ids)
    row["two_pt"] = sum(stats.get(i, 0.0) for i in TWO_POINT_IDS)
    return row


def dst_row(player: dict, stats: dict[int, float]) -> dict:
    name = str(player.get("fullName") or "").replace("D/ST", "").strip()
    row = {"Name": name, "Team": TEAM_CODES.get(player.get("proTeamId"), ""), "Position": "DST"}
    for field, stat_id in DST_STAT_IDS.items():
        row[field] = stats.get(stat_id, 0.0)
    return row


def _get(url: str, header: str, what: str) -> list[dict]:
    try:
        resp = httpx.get(
            url,
            headers={"x-fantasy-filter": header, "user-agent": USER_AGENT},
            timeout=60,
            follow_redirects=True,
        )
        resp.raise_for_status()
    except httpx.HTTPError as e:
        raise EspnFetchError(f"Request to ESPN failed for {what}: {e}") from e
    try:
        payload = resp.json()
    except ValueError as e:
        raise EspnFetchError(f"ESPN returned non-JSON for {what} -- endpoint shape may have changed.") from e
    players = payload.get("players") if isinstance(payload, dict) else None
    if not isinstance(players, list):
        raise EspnFetchError(
            f"ESPN's {what} response had no `players` list -- endpoint shape may have changed."
        )
    return [p.get("player") or {} for p in players]


def rows_from_players(offense: list[dict], dst: list[dict], week: int) -> pd.DataFrame:
    """The DK-scored frame (`Name, Team, Position, DkPts` + every `dk_scoring` stat field) from the two player
    lists. A player ESPN has no projection for is left out, not scored as zeros."""
    off_rows = [
        offense_row(p, s)
        for p in offense
        if (s := weekly_projection(p, week)) is not None and p.get("fullName")
    ]
    dst_rows = [
        dst_row(p, s) for p in dst if (s := weekly_projection(p, week)) is not None and p.get("fullName")
    ]
    if not off_rows and not dst_rows:
        raise EspnFetchError(
            f"ESPN has no projections for week {week} (nothing published yet, or the shape changed)."
        )
    off_df, dst_df = pd.DataFrame(off_rows), pd.DataFrame(dst_rows)
    if not off_df.empty:
        off_df["DkPts"] = score_offense_frame(off_df[OFFENSE_STAT_FIELDS])
    if not dst_df.empty:
        dst_df["DkPts"] = score_dst_frame(dst_df[DST_STAT_FIELDS])
    combined = pd.concat([off_df, dst_df], ignore_index=True, sort=False)
    keep = ["Name", "Team", "Position", "DkPts", *OFFENSE_STAT_FIELDS, *DST_STAT_FIELDS]
    for column in keep:
        if column not in combined.columns:
            combined[column] = pd.NA
    return combined[list(dict.fromkeys(keep))]


def fetch_projections(season: int, week: int) -> pd.DataFrame:
    url = URL_TEMPLATE.format(season=season)
    offense = _get(url, filter_header(OFFENSE_SLOTS, OFFENSE_LIMIT, season, week), "QB/RB/WR/TE")
    dst = _get(url, filter_header(DST_SLOTS, DST_LIMIT, season, week), "D/ST")
    return rows_from_players(offense, dst, week)


class EspnProjectionsSource(Source):
    name = "espn"
    # Shadow source: a column on EdgeRaw (`EspnPts`) and a Model Check row, no raw tab of its own.
    uploads_to_sheet = False

    def fetch(self, ctx: SyncContext) -> pd.DataFrame:
        log.info("fetching ESPN projections for week %s, season %s", ctx.week, ctx.season)
        return fetch_projections(ctx.season, ctx.week)
