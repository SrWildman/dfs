"""Each QB / RB / WR / TE's last five games played, for the Player card (`player_card.usage_log`).

Same free nflverse files `usage` and `snaps` already read, kept per GAME instead of reduced to one row per
player: the stats_player box score (the newest snapshot `usage` saved, downloaded only when there is none),
the play-by-play for red-zone looks and the snap-count release for snap share. `uploads_to_sheet = False`:
`sheet_player_card.refresh_raw` lays it into the hidden `PlayerCardRaw` tab after the edge step.

Fail soft, like `usage`: a file that cannot be fetched blanks its columns (a pbp failure blanks RZ and Inside
5, a snaps failure blanks Snap%), and no stats file gives an EMPTY frame, never an old week's rows.
"""

from __future__ import annotations

import io

import pandas as pd

from dfs.log import get_logger
from dfs.paths import RAW_DIR
from dfs.player_card import LOG_COLUMNS, snap_log_frame, usage_log
from dfs.sources.base import Source, SyncContext
from dfs.sources.nflverse_snaps import SNAP_COUNTS_URL_TEMPLATE
from dfs.sources.nflverse_usage import (
    PBP_URL_TEMPLATE,
    STATS_PLAYER_URL_TEMPLATE,
    NflverseUsageFetchError,
    _download,
    _read_parquet,
)
from dfs.usage_metrics import PBP_USAGE_COLUMNS

log = get_logger("sources.player_log")


def _stats(ctx: SyncContext) -> pd.DataFrame:
    files = sorted((RAW_DIR / "stats_player").glob("*.parquet"))
    if files:
        return pd.read_parquet(files[-1])
    content = _download(
        STATS_PLAYER_URL_TEMPLATE.format(season=ctx.season), what=f"{ctx.season} stats_player"
    )
    return pd.read_parquet(io.BytesIO(content))


class PlayerLogSource(Source):
    name = "player_log"
    uploads_to_sheet = False

    def fetch(self, ctx: SyncContext) -> pd.DataFrame:
        try:
            stats = _stats(ctx)
        except (NflverseUsageFetchError, OSError, ValueError) as e:
            log.warning("player_log: %s -- the Player card's usage block will be blank this run", e)
            return pd.DataFrame(columns=LOG_COLUMNS)
        pbp = snaps = None
        try:
            pbp = _read_parquet(
                _download(PBP_URL_TEMPLATE.format(season=ctx.season), what=f"{ctx.season} play-by-play"),
                PBP_USAGE_COLUMNS,
                what=f"{ctx.season} play-by-play",
            )
        except NflverseUsageFetchError as e:
            log.warning("player_log: %s -- RZ looks and Inside 5 blank this run", e)
        try:
            text = _download(SNAP_COUNTS_URL_TEMPLATE.format(season=ctx.season), what="snap counts").decode()
            snaps = snap_log_frame(pd.read_csv(io.StringIO(text)))
        except (NflverseUsageFetchError, ValueError, KeyError) as e:
            log.warning("player_log: %s -- Snap%% blank this run", e)
        return usage_log(stats, pbp, snaps)
