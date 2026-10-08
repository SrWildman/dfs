"""Loaders for the two datasets `dfs.model.data` does not carry: the injury reports and play-by-play.

Release-asset URLs only (the `github.com/<org>/<repo>/raw/...` form 403s). Everything lands under
`data/research_cache/` (gitignored with the rest of `data/`). Play-by-play is about 20 MB a season, so it
is reduced to `PBP_COLUMNS` the moment it is downloaded and only the reduced file is kept. Failures raise
`ResearchDataError`; nothing here returns an empty frame to signal one.

Everything else (player stats, team stats, ffopportunity, schedules) is read through `dfs.model.data`.
"""

from __future__ import annotations

import io
import time
from collections.abc import Callable
from pathlib import Path

import httpx
import pandas as pd
import pyarrow.parquet as pq

from dfs.model import data as model_data
from dfs.paths import DATA_DIR

CACHE_DIR = DATA_DIR / "research_cache"
SEASONS = list(range(2014, 2026))

_NFLVERSE = "https://github.com/nflverse/nflverse-data/releases/download"
INJURIES_URL = _NFLVERSE + "/injuries/injuries_{season}.parquet"
PBP_URL = _NFLVERSE + "/pbp/play_by_play_{season}.parquet"

# Only what the studies read. R2: EPA / pace / PROE by team; R3: red-zone opportunity by player;
# R4: weather text, wind, temp, roof and pass rate.
PBP_COLUMNS = [
    "game_id",
    "season",
    "week",
    "season_type",
    "posteam",
    "defteam",
    "play_type",
    "pass",
    "rush",
    "qb_dropback",
    "qb_scramble",
    "qb_kneel",
    "qb_spike",
    "sack",
    "interception",
    "fumble_lost",
    "penalty",
    "two_point_attempt",
    "special",
    "epa",
    "xpass",
    "pass_oe",
    "yardline_100",
    "receiver_player_id",
    "rusher_player_id",
    "passer_player_id",
    "weather",
    "wind",
    "temp",
    "roof",
]

INJURY_COLUMNS = [
    "season",
    "game_type",
    "team",
    "week",
    "gsis_id",
    "position",
    "report_primary_injury",
    "report_status",
    "practice_status",
]

_ATTEMPTS = 4


class ResearchDataError(Exception):
    """A research dataset could not be fetched or read."""


def injuries_path(season: int) -> Path:
    return CACHE_DIR / f"injuries_{season}.parquet"


def pbp_path(season: int) -> Path:
    return CACHE_DIR / f"pbp_{season}.parquet"


def _download(url: str, what: str, sleep: Callable[[float], None] = time.sleep) -> bytes:
    """GET with a few retries (the release CDN occasionally resets a 20 MB transfer)."""
    last: Exception | None = None
    for attempt in range(_ATTEMPTS):
        try:
            resp = httpx.get(url, timeout=180, follow_redirects=True)
            resp.raise_for_status()
            return resp.content
        except httpx.HTTPError as e:
            last = e
            sleep(2.0 * 2**attempt)
    raise ResearchDataError(f"Request for {what} failed after {_ATTEMPTS} attempts: {last}") from last


def reduce_parquet(content: bytes, columns: list[str], what: str) -> pd.DataFrame:
    """The listed columns (those that exist) of a parquet payload."""
    try:
        buf = io.BytesIO(content)
        present = set(pq.ParquetFile(buf).schema_arrow.names)
        buf.seek(0)
        return pd.read_parquet(buf, columns=[c for c in columns if c in present])
    except Exception as e:  # noqa: BLE001 - pyarrow raises its own types
        raise ResearchDataError(f"{what} is not a readable parquet file: {e}") from e


def fetch_injuries(season: int) -> Path:
    path = injuries_path(season)
    frame = reduce_parquet(
        _download(INJURIES_URL.format(season=season), f"{season} injuries"), INJURY_COLUMNS, path.name
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_parquet(path, index=False)
    return path


def fetch_pbp(season: int) -> Path:
    path = pbp_path(season)
    frame = reduce_parquet(_download(PBP_URL.format(season=season), f"{season} pbp"), PBP_COLUMNS, path.name)
    frame = frame[frame["season_type"] == "REG"].reset_index(drop=True)
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_parquet(path, index=False)
    return path


def fetch_all(seasons: list[int] | None = None, *, refresh: bool = False, log=lambda msg: None) -> None:
    """Make every dataset the studies read available: the `dfs.model` cache (stats, ep, schedule) and this
    module's injuries and pbp. Completed seasons are fetched once."""
    seasons = seasons or SEASONS
    try:
        model_data.fetch_games()
        for season in seasons:
            for source in model_data.SEASON_SOURCES:
                if refresh or not model_data.cache_path(source, season).exists():
                    log(f"fetching {source} {season}")
                    model_data.fetch_season_file(source, season)
    except model_data.ModelDataError as e:
        raise ResearchDataError(str(e)) from e
    for season in seasons:
        if refresh or not injuries_path(season).exists():
            log(f"fetching injuries {season}")
            fetch_injuries(season)
        if refresh or not pbp_path(season).exists():
            log(f"fetching pbp {season}")
            fetch_pbp(season)


def _read_cached(path: Path, hint: str) -> pd.DataFrame:
    if not path.exists():
        raise ResearchDataError(f"{path.name} is not cached -- run `dfs research fetch`")
    return pd.read_parquet(path)


def read_injuries(seasons: list[int] | None = None) -> pd.DataFrame:
    """Regular-season injury reports, one row per (player, team, week) report."""
    frames = [_read_cached(injuries_path(s), "injuries") for s in seasons or SEASONS]
    inj = pd.concat(frames, ignore_index=True)
    inj = inj[inj["game_type"] == "REG"].copy()
    inj["team"] = inj["team"].map(model_data.normalize_team)
    return inj.reset_index(drop=True)


def read_pbp(seasons: list[int] | None = None) -> pd.DataFrame:
    """Reduced regular-season play-by-play (`PBP_COLUMNS`)."""
    frames = [_read_cached(pbp_path(s), "pbp") for s in seasons or SEASONS]
    pbp = pd.concat(frames, ignore_index=True)
    for col in ("posteam", "defteam"):
        pbp[col] = pbp[col].map(model_data.normalize_team)
    return pbp


def _model_read(call: Callable[[], pd.DataFrame]) -> pd.DataFrame:
    """Run a `dfs.model.data` reader, restating its "run `dfs model fetch`" failure as ours."""
    try:
        return call()
    except model_data.ModelDataError as e:
        raise ResearchDataError(str(e).replace("dfs model fetch", "dfs research fetch")) from e


def read_stats_player(seasons: list[int] | None = None, columns: list[str] | None = None) -> pd.DataFrame:
    """`dfs.model`'s cached player-week stats, regular season, teams normalised, FB read as RB."""
    sp = _model_read(lambda: model_data.read_season_files("stats_player", seasons or SEASONS, columns))
    sp = sp[sp["player_id"].notna()]
    if "season_type" in sp:
        sp = sp[sp["season_type"] == "REG"]
    sp = sp.copy()
    sp["team"] = sp["team"].map(model_data.normalize_team)
    if "opponent_team" in sp:
        sp["opponent_team"] = sp["opponent_team"].map(model_data.normalize_team)
    if "position" in sp:
        sp["position"] = sp["position"].replace({"FB": "RB"})
    return sp.reset_index(drop=True)


def read_ep(seasons: list[int] | None = None, columns: list[str] | None = None) -> pd.DataFrame:
    """ffopportunity's expected-points rows (`ep_weekly`), as cached by `dfs.model`. Season and week are
    text in some files; they are cast to int here."""
    ep = _model_read(lambda: model_data.read_season_files("ep_weekly", seasons or SEASONS, columns))
    for col in ("season", "week"):
        if col in ep:
            ep[col] = ep[col].astype(int)
    return ep


def read_games(seasons: list[int] | None = None) -> pd.DataFrame:
    """`dfs.model`'s cached schedule: regular-season games that have been played, teams normalised."""
    g = _model_read(model_data.read_games)
    g = g[(g["game_type"] == "REG") & g["season"].isin(seasons or SEASONS)].copy()
    g = g[g["home_score"].notna() & g["away_score"].notna()]
    for col in ("home_team", "away_team"):
        g[col] = g[col].map(model_data.normalize_team)
    return g.reset_index(drop=True)
