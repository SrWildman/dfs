"""Download and cache the public history the model trains on (nflverse and ffverse release assets).

Everything lands under `data/model_cache/` (gitignored) as the raw parquet the release serves. Release-asset
URLs only: the `github.com/<org>/<repo>/raw/...` form 403s. The fetch layer raises `ModelDataError` on any
failure -- it never returns an empty frame to signal one (see `CONTRIBUTING.md`'s source contract).
"""

from __future__ import annotations

import io
from pathlib import Path

import httpx
import pandas as pd

from dfs.paths import DATA_DIR

CACHE_DIR = DATA_DIR / "model_cache"

FIRST_SEASON = 2014

_NFLVERSE = "https://github.com/nflverse/nflverse-data/releases/download"
STATS_PLAYER_URL = _NFLVERSE + "/stats_player/stats_player_week_{season}.parquet"
STATS_TEAM_URL = _NFLVERSE + "/stats_team/stats_team_week_{season}.parquet"
GAMES_URL = _NFLVERSE + "/schedules/games.parquet"
SNAP_COUNTS_URL = _NFLVERSE + "/snap_counts/snap_counts_{season}.parquet"
EP_WEEKLY_URL = (
    "https://github.com/ffverse/ffopportunity/releases/download/latest-data/ep_weekly_{season}.parquet"
)

# Per-season files, keyed by the cache subdirectory they live in.
SEASON_SOURCES = {
    "stats_player": STATS_PLAYER_URL,
    "stats_team": STATS_TEAM_URL,
    "ep_weekly": EP_WEEKLY_URL,
}

# nflverse still spells some franchises the way they were spelled when the games were played.
TEAM_ALIASES = {"LA": "LAR", "OAK": "LV", "SD": "LAC", "STL": "LAR"}


class ModelDataError(Exception):
    """A history file could not be fetched or read."""


def normalize_team(team: object) -> object:
    """nflverse's historical spellings mapped to today's codes (LA/STL -> LAR, OAK -> LV, SD -> LAC)."""
    if isinstance(team, str):
        code = team.strip().upper()
        return TEAM_ALIASES.get(code, code)
    return team


def cache_path(source: str, season: int | None = None) -> Path:
    name = f"{source}_{season}.parquet" if season is not None else f"{source}.parquet"
    return CACHE_DIR / name


def _download(url: str, what: str) -> bytes:
    try:
        resp = httpx.get(url, timeout=120, follow_redirects=True)
        resp.raise_for_status()
    except httpx.HTTPError as e:
        raise ModelDataError(f"Request for {what} failed: {e}") from e
    return resp.content


def _write(path: Path, content: bytes, what: str) -> None:
    try:
        pd.read_parquet(io.BytesIO(content))
    except Exception as e:  # noqa: BLE001 - pyarrow raises its own types
        raise ModelDataError(f"{what} is not a readable parquet file: {e}") from e
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)


def fetch_season_file(source: str, season: int) -> Path:
    """Download one per-season file (always overwrites the cached copy)."""
    path = cache_path(source, season)
    _write(path, _download(SEASON_SOURCES[source].format(season=season), f"{season} {source}"), path.name)
    return path


def fetch_games() -> Path:
    path = cache_path("games")
    _write(path, _download(GAMES_URL, "games.parquet"), path.name)
    return path


def refresh_season(season: int, log=lambda msg: None) -> None:
    """Fetch one (the current) season's files and the schedule fresh. A season that has not started has no
    stats file yet -- nflverse publishes one after the first games -- and that is not an error: it is skipped
    and the history ends with the last completed season."""
    try:
        fetch_games()
    except ModelDataError as e:
        log(f"schedule not refreshed ({e}); using the cached copy")
    for source in SEASON_SOURCES:
        try:
            fetch_season_file(source, season)
        except ModelDataError as e:
            log(f"{source} {season} not available yet ({e})")


def fetch_history(through: int, *, refresh_all: bool = False, log=lambda msg: None) -> list[int]:
    """Make the cache current through season `through` (the season in progress): completed seasons are
    downloaded only if missing (they never change; `refresh_all` re-downloads them), the current season and
    the schedule always. Returns the seasons downloaded."""
    completed = list(range(FIRST_SEASON, through))
    done: list[int] = []
    for season in completed:
        for source in SEASON_SOURCES:
            if refresh_all or not cache_path(source, season).exists():
                log(f"fetching {source} {season}")
                fetch_season_file(source, season)
                done.append(season)
    refresh_season(through, log)
    return sorted(set(done))


def read_season_files(source: str, seasons: list[int], columns: list[str] | None = None) -> pd.DataFrame:
    """Concatenate the cached per-season files. A missing file is an error naming `dfs model fetch`."""
    frames = []
    for season in seasons:
        path = cache_path(source, season)
        if not path.exists():
            raise ModelDataError(f"{path.name} is not cached -- run `dfs model fetch`")
        frame = pd.read_parquet(path)
        if columns is not None:
            frame = frame[[c for c in columns if c in frame.columns]]
        frames.append(frame)
    return pd.concat(frames, ignore_index=True)


def read_games() -> pd.DataFrame:
    path = cache_path("games")
    if not path.exists():
        raise ModelDataError(f"{path.name} is not cached -- run `dfs model fetch`")
    return pd.read_parquet(path)
