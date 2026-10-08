"""ffopportunity's weekly expected-points file, reduced to one row per skill-position player-week (`xfp.py`).

Source: `https://github.com/ffverse/ffopportunity/releases/download/latest-data/ep_weekly_<season>.parquet`
(about 330 KB, updated weekly). The current season AND last season are read (last season feeds the
with-or-without injury history); `season` is a column. The actual DK points per game ride along as
`dk_actual` (scored from nflverse's `stats_player` by the same code the results loop uses), so the
FADE token can compare what happened with what the opportunity was worth.

Fail soft: any fetch or parse problem logs a warning and returns an EMPTY frame (columns only), so
`xFP/G` and every token built on it go blank and the sync carries on. `uploads_to_sheet = False`: it
feeds `signals.py`, never a tab of its own.
"""

from __future__ import annotations

import pandas as pd

from dfs import results_actual, xfp
from dfs.log import get_logger
from dfs.sources import nflverse_files as nf
from dfs.sources.base import Source, SyncContext

log = get_logger("sources.ffopportunity")

OUTPUT_COLUMNS = [*xfp.PLAYER_WEEK_COLUMNS, "dk_actual"]
STATS_PLAYER_URL = nf.NFLVERSE_RELEASES + "/stats_player/stats_player_week_{season}.parquet"


def empty_frame() -> pd.DataFrame:
    """No rows: the right columns, so a failed fetch blanks everything instead of failing the sync."""
    return pd.DataFrame(columns=OUTPUT_COLUMNS)


def fetch_ffo(season: int) -> pd.DataFrame:
    """The raw ffopportunity frame for one season (the columns `xfp` reads)."""
    content = nf.download(nf.FFOPPORTUNITY_URL.format(season=season), what=f"{season} ffopportunity")
    return nf.read_parquet(content, xfp.FFO_COLUMNS, what=f"{season} ffopportunity")


def fetch_offense_actual(season: int) -> pd.DataFrame:
    """Real DK points per offensive player-week, from the season's `stats_player`."""
    content = nf.download(STATS_PLAYER_URL.format(season=season), what=f"{season} stats_player")
    stats = nf.read_parquet(content, results_actual.STATS_PLAYER_COLUMNS, what=f"{season} stats_player")
    return results_actual.score_offense_actual(stats)


class FfopportunitySource(Source):
    """Per-player-week expected points (`xFP`), current and prior season; no sheet tab."""

    name = "ffopportunity"
    uploads_to_sheet = False

    def fetch(self, ctx: SyncContext) -> pd.DataFrame:
        """One row per skill-position player-week for the season and the one before, or an EMPTY frame."""
        frames = []
        for season in (ctx.season - 1, ctx.season):
            try:
                weeks = xfp.player_weeks(fetch_ffo(season), season=season)
            except nf.ContextFetchError as e:
                if season == ctx.season:
                    log.warning("ffopportunity: %s -- xFP/G and its tokens will be blank this run", e)
                    return empty_frame()
                log.warning("ffopportunity: %s -- no last-season history for with-or-without", e)
                continue
            try:
                weeks = xfp.attach_actual_points(weeks, fetch_offense_actual(season), season)
            except nf.ContextFetchError as e:
                log.warning("ffopportunity: %s -- actual DK points blank for %s, FADE needs them", e, season)
            frames.append(weeks)
        if not frames:
            return empty_frame()
        out = pd.concat(frames, ignore_index=True)
        if "dk_actual" not in out.columns:
            out["dk_actual"] = float("nan")
        return out[OUTPUT_COLUMNS]
