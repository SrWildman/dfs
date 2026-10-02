"""nflverse's free `stats_player` weekly file (plus the current season's play-by-play for
red-zone usage) reduced to one row per player: `Tgt%`, `WOPR`, `Rush%`, `RZ/G`, `HVT/G`.

All the maths -- the last-3-games-played window, the team-total shares, the red-zone counts --
lives in `usage_metrics.py`, pure and offline-testable; this is the thin fetch wrapper.

Release-asset URLs only (same host `nflverse_pbp.py`/`nflverse_snaps.py` already use).

Fail soft, by Sam's instruction: if the stats file can't be fetched or parsed, this source logs a
warning and returns an EMPTY frame, so the usage columns go blank and the sync carries on. It
never falls back to last season (roles change between seasons), and an empty frame also
overwrites any stale `data/current/usage.csv`, so a failed fetch can't leave an old week's
numbers on the sheet. Week 1 (no games yet) is the same all-blank result. A pbp failure alone
blanks only `RZ/G`/`HVT/G`.

The raw stats file is snapshotted to `data/raw/stats_player/<timestamp>.parquet` on every sync,
like every other source's raw history; `store.save` separately snapshots the reduced frame under
`data/raw/usage/`. `uploads_to_sheet = False`: usage reaches the sheet through EdgeRaw, per
player, never as raw rows.
"""

from __future__ import annotations

import io

import httpx
import pandas as pd

from dfs import store
from dfs.log import get_logger
from dfs.paths import RAW_DIR, ensure_data_dirs
from dfs.sources.base import Source, SyncContext
from dfs.usage_metrics import PBP_USAGE_COLUMNS, STATS_COLUMNS, empty_usage_frame, player_usage

log = get_logger("sources.nflverse_usage")

STATS_PLAYER_URL_TEMPLATE = (
    "https://github.com/nflverse/nflverse-data/releases/download/stats_player/"
    "stats_player_week_{season}.parquet"
)
PBP_URL_TEMPLATE = (
    "https://github.com/nflverse/nflverse-data/releases/download/pbp/play_by_play_{season}.parquet"
)
STATS_RAW_DIRNAME = "stats_player"


class NflverseUsageFetchError(Exception):
    """The stats or play-by-play file could not be fetched or read (reported as a warning, never fatal)."""

    pass


def _download(url: str, *, what: str) -> bytes:
    try:
        resp = httpx.get(url, timeout=90, follow_redirects=True)
        resp.raise_for_status()
    except httpx.HTTPError as e:
        raise NflverseUsageFetchError(f"Request for {what} failed: {e}") from e
    return resp.content


def _read_parquet(content: bytes, columns: list[str], *, what: str) -> pd.DataFrame:
    try:
        return pd.read_parquet(io.BytesIO(content), columns=columns)
    except Exception as e:  # pyarrow raises its own exception types, not one common base
        raise NflverseUsageFetchError(f"Could not parse {what}: {e}") from e


def _snapshot_raw_stats(content: bytes) -> None:
    """Keep the raw file, like every other source's raw history."""
    ensure_data_dirs()
    directory = RAW_DIR / STATS_RAW_DIRNAME
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{store._now_stamp()}.parquet").write_bytes(content)


class NflverseUsageSource(Source):
    """Per-player usage (`Tgt%`, `WOPR`, `Rush%`, `RZ/G`, `HVT/G`); no sheet tab, it feeds EdgeRaw."""

    name = "usage"
    uploads_to_sheet = False

    def fetch(self, ctx: SyncContext) -> pd.DataFrame:
        """One row per player, or an EMPTY frame (columns only) when the stats file is unavailable."""
        log.info("fetching nflverse stats_player for season %s", ctx.season)
        try:
            stats_bytes = _download(
                STATS_PLAYER_URL_TEMPLATE.format(season=ctx.season), what=f"{ctx.season} stats_player"
            )
            stats = _read_parquet(stats_bytes, STATS_COLUMNS, what=f"{ctx.season} stats_player")
        except NflverseUsageFetchError as e:
            log.warning(
                "usage: %s -- Tgt%%/WOPR/Rush%%/RZ/HVT will be blank this run (never last season's)", e
            )
            return empty_usage_frame()
        _snapshot_raw_stats(stats_bytes)

        pbp = None
        try:
            pbp = _read_parquet(
                _download(PBP_URL_TEMPLATE.format(season=ctx.season), what=f"{ctx.season} play-by-play"),
                PBP_USAGE_COLUMNS,
                what=f"{ctx.season} play-by-play",
            )
        except NflverseUsageFetchError as e:
            log.warning("usage: %s -- RZ/G and HVT/G will be blank this run", e)
        return player_usage(stats, pbp)
