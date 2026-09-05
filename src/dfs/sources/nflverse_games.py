"""Per-game context (stadium, roof, surface, rest days, closing lines) from
nflverse's free, unauthenticated `games.csv`.

Verified live (2026-09-05): this single free CSV already carries what
docs/HANDOFF.md assumed would need a hand-built stadium table -- roof and
surface per game, plus rest days and closing spread/total. No API key, no
auth, no browser.

Team codes mostly already match DraftKings'/TFFB's, with one confirmed
drift: nflverse names the Rams "LA", DraftKings names them "LAR" (see
`NFLVERSE_TO_DK_TEAM`). This module doesn't need to join against DK teams
itself -- it's a standalone, `nfl_odds`-shaped tab -- so the map lives here
for `derived.py` to use whenever a later join needs it, not applied here.
"""

from __future__ import annotations

import io

import httpx
import pandas as pd

from dfs.log import get_logger
from dfs.sources.base import Source, SyncContext

log = get_logger("sources.nflverse_games")

GAMES_CSV_URL = "https://github.com/nflverse/nfldata/raw/master/data/games.csv"

# The only team-code drift confirmed against a live pull so far -- see
# module docstring. Add to this if another mismatch turns up; don't guess
# ahead of what's actually been seen.
NFLVERSE_TO_DK_TEAM = {"LA": "LAR"}

# nflverse's raw column name -> GamesRaw's tab column name, also the
# GamesRaw column order -- exposed (like derived.EDGE_COLUMNS) for anything
# that needs the tab's header without a round-trip read of the sheet.
GAMES_COLUMNS = {
    "game_id": "GameId",
    "away_team": "Away",
    "home_team": "Home",
    "gameday": "Date",
    "gametime": "Time",
    "stadium": "Stadium",
    "roof": "Roof",
    "surface": "Surface",
    "away_rest": "AwayRest",
    "home_rest": "HomeRest",
    "div_game": "DivGame",
    "spread_line": "Spread",
    "total_line": "Total",
}


class NflverseGamesFetchError(Exception):
    pass


def parse_games_csv(csv_text: str, season: int, week: int) -> pd.DataFrame:
    df = pd.read_csv(io.StringIO(csv_text))
    missing = [c for c in GAMES_COLUMNS if c not in df.columns]
    if missing:
        raise NflverseGamesFetchError(
            f"games.csv is missing expected column(s) {missing} -- nflverse may have changed their schema."
        )

    week_games = df[(df["season"] == season) & (df["week"] == week)]
    if week_games.empty:
        raise NflverseGamesFetchError(
            f"No games found in games.csv for season {season}, week {week} -- "
            "the schedule may not be published yet, or week/season is wrong."
        )

    week_games = week_games[list(GAMES_COLUMNS)].rename(columns=GAMES_COLUMNS)
    week_games["Away"] = week_games["Away"].replace(NFLVERSE_TO_DK_TEAM)
    week_games["Home"] = week_games["Home"].replace(NFLVERSE_TO_DK_TEAM)
    return week_games.reset_index(drop=True)


class NflverseGamesSource(Source):
    name = "nflverse_games"

    def fetch(self, ctx: SyncContext) -> pd.DataFrame:
        log.info("fetching nflverse games for week %s, season %s", ctx.week, ctx.season)
        try:
            resp = httpx.get(GAMES_CSV_URL, timeout=30, follow_redirects=True)
            resp.raise_for_status()
        except httpx.HTTPError as e:
            raise NflverseGamesFetchError(f"Request to nflverse failed: {e}") from e

        return parse_games_csv(resp.text, ctx.season, ctx.week)
