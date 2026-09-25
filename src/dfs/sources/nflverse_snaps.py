"""nflverse's free, unauthenticated snap-count release -- Part C, C6.

`https://github.com/nflverse/nflverse-data/releases/download/snap_counts/
snap_counts_{season}.csv` -- confirmed live (2026-09-25) via nflverse-
data's own GitHub Releases API, not the "URL pattern in
docs/planning/PROMPT_WEEK3.md" that prompt pointed to (that file only
names the R/py loader function, `load_snap_counts()`, no actual URL).

C6 says this file is "keyed on pfr_player_id, not gsis," and describes
routing it through nflverse's own players crosswalk (pfr -> gsis) before
reaching DK. Verified live: the file already carries `player`/`team`/
`position` columns directly, everything `player_join.join_source_to_dk`
needs -- no separate crosswalk step is actually required to reach a
name/team/position triple. `pfr_player_id` is still used here, just for
grouping a player's own rows across weeks, not for an external join.

"Most recent completed week" is PER PLAYER, not one global week number --
confirmed live (2026-09-25, week 3): the release already carries a lone
Thursday-night week-3 game (ATL@GB) alongside every week-1/2 team, so a
single "current week minus one" filter would wrongly serve stale week-2
data to that game's own players while everyone else correctly gets
week 2. Grouped by `pfr_player_id`, taking each player's own latest
available week's row instead.

A player with a real recorded 0 offense snaps in their own latest game
(inactive/DNP) keeps that real 0.0 -- it's a known fact, not missing
data. A player absent from the file entirely (never played, a rookie's
bye) is simply absent from this source's output; the C1 join upstream
(`player_join.join_source_to_dk`) is what leaves `Snap%` blank for them
on EdgeRaw, per C6's own "never 0 for missing data" instruction --
nothing in this module fabricates a 0 for a player it never saw."""

from __future__ import annotations

import io

import httpx
import pandas as pd

from dfs.log import get_logger
from dfs.sources.base import Source, SyncContext

log = get_logger("sources.nflverse_snaps")

SNAP_COUNTS_URL_TEMPLATE = (
    "https://github.com/nflverse/nflverse-data/releases/download/snap_counts/snap_counts_{season}.csv"
)

_REQUIRED_COLUMNS = {"pfr_player_id", "player", "team", "position", "week", "offense_pct"}


class NflverseSnapsFetchError(Exception):
    pass


def parse_snap_counts(csv_text: str) -> pd.DataFrame:
    df = pd.read_csv(io.StringIO(csv_text))
    missing = _REQUIRED_COLUMNS - set(df.columns)
    if missing:
        raise NflverseSnapsFetchError(
            f"snap_counts.csv is missing expected column(s) {missing} -- "
            "nflverse may have changed their schema."
        )
    if df.empty:
        raise NflverseSnapsFetchError("snap_counts.csv had no rows.")

    latest = df.sort_values("week").groupby("pfr_player_id", as_index=False).last()
    return latest[["player", "team", "position", "offense_pct"]].rename(
        columns={"player": "Name", "team": "Team", "position": "Position", "offense_pct": "Snap%"}
    )


class NflverseSnapsSource(Source):
    name = "snaps"

    def fetch(self, ctx: SyncContext) -> pd.DataFrame:
        url = SNAP_COUNTS_URL_TEMPLATE.format(season=ctx.season)
        log.info("fetching nflverse snap counts for season %s", ctx.season)
        try:
            resp = httpx.get(url, timeout=30, follow_redirects=True)
            resp.raise_for_status()
        except httpx.HTTPError as e:
            raise NflverseSnapsFetchError(f"Request to nflverse failed: {e}") from e
        return parse_snap_counts(resp.text)
