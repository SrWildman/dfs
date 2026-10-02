"""Fetch the three free nflverse files the results loop scores against: `stats_player`, `stats_team`
and the schedule's final scores (`games.csv`).

Thin and untested by design (the maths is in `results_actual.py`). Every file is snapshotted under
`data/raw/<name>/<UTC stamp>.parquet|csv`, like every other source's raw history. nflverse lags a day or
two after a week's games; a file that is not there yet raises `ResultsFetchError`, which callers turn
into "stats not published yet" -- never a failed command (`dfs week close` must not fail over it).
"""

from __future__ import annotations

import io

import httpx
import pandas as pd

from dfs import store
from dfs.log import get_logger
from dfs.paths import RAW_DIR, ensure_data_dirs
from dfs.results_actual import STATS_PLAYER_COLUMNS, STATS_TEAM_COLUMNS
from dfs.sources.nflverse_games import GAMES_CSV_URL

log = get_logger("sources.nflverse_results")

_RELEASES = "https://github.com/nflverse/nflverse-data/releases/download"
STATS_PLAYER_URL = _RELEASES + "/stats_player/stats_player_week_{season}.parquet"
STATS_TEAM_URL = _RELEASES + "/stats_team/stats_team_week_{season}.parquet"


class ResultsFetchError(Exception):
    """A results file is not published yet (or could not be read); callers say so and carry on."""

    pass


def _download(url: str, what: str) -> bytes:
    try:
        resp = httpx.get(url, timeout=90, follow_redirects=True)
        resp.raise_for_status()
    except httpx.HTTPError as e:
        raise ResultsFetchError(f"Request for {what} failed: {e}") from e
    return resp.content


def _snapshot(directory: str, content: bytes, suffix: str) -> None:
    ensure_data_dirs()
    target = RAW_DIR / directory
    target.mkdir(parents=True, exist_ok=True)
    (target / f"{store._now_stamp()}.{suffix}").write_bytes(content)


def _parquet(content: bytes, columns: list[str], what: str) -> pd.DataFrame:
    try:
        available = pd.read_parquet(io.BytesIO(content)).columns
        return pd.read_parquet(io.BytesIO(content), columns=[c for c in columns if c in available])
    except Exception as e:  # noqa: BLE001 - pyarrow raises its own types
        raise ResultsFetchError(f"Could not parse {what}: {e}") from e


def fetch_stats_player(season: int) -> pd.DataFrame:
    """nflverse `stats_player` for the season (per player-week), raw file snapshotted."""
    content = _download(STATS_PLAYER_URL.format(season=season), f"{season} stats_player")
    _snapshot("stats_player", content, "parquet")
    return _parquet(content, STATS_PLAYER_COLUMNS, f"{season} stats_player")


def fetch_stats_team(season: int) -> pd.DataFrame:
    """nflverse `stats_team` for the season (per team-week, includes defensive counting stats)."""
    content = _download(STATS_TEAM_URL.format(season=season), f"{season} stats_team")
    _snapshot("stats_team", content, "parquet")
    return _parquet(content, STATS_TEAM_COLUMNS, f"{season} stats_team")


def fetch_game_scores(season: int) -> pd.DataFrame:
    """The season's schedule with final scores (`week`, `away_team`, `home_team`, `away_score`,
    `home_score`). Team codes are left in nflverse's spelling (`LA`), the same as the two stats files, so
    the three merge on `team`; `player_join` normalises `LA` to DraftKings' `LAR` when joining to DK."""
    content = _download(GAMES_CSV_URL, "games.csv")
    _snapshot("game_scores", content, "csv")
    games = pd.read_csv(io.BytesIO(content))
    games = games[(games["season"] == season) & (games["game_type"] == "REG")]
    columns = ["week", "away_team", "home_team", "away_score", "home_score"]
    return games[columns].reset_index(drop=True)
