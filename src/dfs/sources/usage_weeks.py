"""The Week page's usage view (Plan O item 6): every game of the season per player (`usage_weeks`) and per
team (`team_weeks`). The math is `dfs.usage_weeks` (pure); this is the fetch wrapper. No sheet tab
(`uploads_to_sheet = False`), and neither is in `cli.LIVE_SYNC_SOURCES`: they change only when games are
played, like `usage` and `player_log`.

Same free nflverse files the other usage sources read: the newest stats_player snapshot `usage` saved
(downloaded only when there is none), the current season's play-by-play (downloaded once per process and
shared by both sources, `_pbp`) and the snap-count release. Fail soft: a file that cannot be fetched blanks
its columns, and no stats file gives an EMPTY frame, never an old week's rows.
"""

from __future__ import annotations

import functools
import io

import pandas as pd

from dfs.log import get_logger
from dfs.player_card import snap_log_frame
from dfs.sources.base import Source, SyncContext
from dfs.sources.nflverse_snaps import SNAP_COUNTS_URL_TEMPLATE
from dfs.sources.nflverse_usage import PBP_URL_TEMPLATE, NflverseUsageFetchError, _download, _read_parquet
from dfs.sources.player_log import _stats
from dfs.usage_weeks import PBP_COLUMNS, PLAYER_COLUMNS, TEAM_COLUMNS, player_weeks, team_weeks

log = get_logger("sources.usage_weeks")


@functools.lru_cache(maxsize=1)
def _pbp(season: int) -> pd.DataFrame:
    return _read_parquet(
        _download(PBP_URL_TEMPLATE.format(season=season), what=f"{season} play-by-play"),
        PBP_COLUMNS,
        what=f"{season} play-by-play",
    )


class UsageWeeksSource(Source):
    name = "usage_weeks"
    uploads_to_sheet = False

    def fetch(self, ctx: SyncContext) -> pd.DataFrame:
        try:
            stats = _stats(ctx)
        except (NflverseUsageFetchError, OSError, ValueError) as e:
            log.warning("usage_weeks: %s -- the Week page's usage view is blank this run", e)
            return pd.DataFrame(columns=PLAYER_COLUMNS)
        pbp = snaps = None
        try:
            pbp = _pbp(ctx.season)
        except NflverseUsageFetchError as e:
            log.warning("usage_weeks: %s -- RZ and HVT blank this run", e)
        try:
            text = _download(SNAP_COUNTS_URL_TEMPLATE.format(season=ctx.season), what="snap counts").decode()
            snaps = snap_log_frame(pd.read_csv(io.StringIO(text)))
        except (NflverseUsageFetchError, ValueError, KeyError) as e:
            log.warning("usage_weeks: %s -- Snap%% blank this run", e)
        return player_weeks(stats, pbp, snaps)


class TeamWeeksSource(Source):
    name = "team_weeks"
    uploads_to_sheet = False

    def fetch(self, ctx: SyncContext) -> pd.DataFrame:
        try:
            pbp = _pbp(ctx.season)
        except NflverseUsageFetchError as e:
            log.warning("team_weeks: %s -- the Week page's team usage is blank this run", e)
            return pd.DataFrame(columns=TEAM_COLUMNS)
        try:
            stats = _stats(ctx)
        except (NflverseUsageFetchError, OSError, ValueError):
            stats = None
        return team_weeks(pbp, stats)
