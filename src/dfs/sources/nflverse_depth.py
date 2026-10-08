"""nflverse's depth charts (`depth_charts_<season>.parquet`), reduced to skill positions.

The file is updated DAILY and carries every snapshot (`dt`) of the season, 600k rows in all. The sync keeps
only the last snapshot of each of the last `KEEP_DAYS` calendar days (a few thousand rows): enough for
"the latest `dt` before the slate's first kickoff" whenever the sync runs. The results backfill reads the
whole history (`fetch_depth(keep_days=None)`) and applies the same rule per past week.

Columns: `dt` (UTC stamp), `Team`, `GsisId`, `Name`, `Position`, `pos_rank` (1 = starter; the order of
players at a position within one team and snapshot).

Fail soft, like the other context sources: a problem logs a warning and returns an EMPTY frame.
"""

from __future__ import annotations

import pandas as pd

from dfs.log import get_logger
from dfs.player_join import normalize_position, normalize_team
from dfs.sources import nflverse_files as nf
from dfs.sources.base import Source, SyncContext

log = get_logger("sources.nflverse_depth")

KEEP_DAYS = 10
SKILL_POSITIONS = {"QB", "RB", "WR", "TE"}
OUTPUT_COLUMNS = ["dt", "Team", "GsisId", "Name", "Position", "pos_rank"]
_READ_COLUMNS = ["dt", "team", "gsis_id", "player_name", "pos_abb", "pos_rank"]


def empty_frame() -> pd.DataFrame:
    """No rows, the right columns."""
    return pd.DataFrame(columns=OUTPUT_COLUMNS)


def reduce_depth(raw: pd.DataFrame, *, keep_days: int | None = KEEP_DAYS) -> pd.DataFrame:
    """Skill-position rows in `OUTPUT_COLUMNS`. One row per (snapshot, team, player, position) with the
    best `pos_rank` (a player appears once per formation group). With `keep_days`, only the last snapshot
    of each of the most recent `keep_days` calendar days is kept; `None` keeps them all."""
    if raw is None or raw.empty:
        return empty_frame()
    df = raw.copy()
    df["Position"] = df["pos_abb"].map(normalize_position)
    df = df[df["Position"].isin(SKILL_POSITIONS) & df["gsis_id"].notna()]
    if df.empty:
        return empty_frame()
    df["day"] = df["dt"].astype(str).str[:10]
    if keep_days is not None:
        last_per_day = df.groupby("day")["dt"].max()
        keep = set(last_per_day.sort_index().tail(keep_days))
        df = df[df["dt"].isin(keep)]
    out = pd.DataFrame(
        {
            "dt": df["dt"],
            "Team": df["team"].map(normalize_team),
            "GsisId": df["gsis_id"],
            "Name": df["player_name"],
            "Position": df["Position"],
            "pos_rank": pd.to_numeric(df["pos_rank"], errors="coerce"),
        }
    )
    out = out.sort_values("pos_rank").drop_duplicates(
        subset=["dt", "Team", "GsisId", "Position"], keep="first"
    )
    return out[OUTPUT_COLUMNS].sort_values(["dt", "Team", "Position", "pos_rank"]).reset_index(drop=True)


def fetch_depth(season: int, *, keep_days: int | None = KEEP_DAYS) -> pd.DataFrame:
    """The reduced depth-chart frame; raises `ContextFetchError`."""
    content = nf.download(nf.DEPTH_CHARTS_URL.format(season=season), what=f"{season} depth charts")
    return reduce_depth(
        nf.read_parquet(content, _READ_COLUMNS, what=f"{season} depth charts"), keep_days=keep_days
    )


class NflverseDepthSource(Source):
    """Recent depth-chart snapshots; no sheet tab."""

    name = "nflverse_depth"
    uploads_to_sheet = False

    def fetch(self, ctx: SyncContext) -> pd.DataFrame:
        """The last few days' skill-position depth charts, or an EMPTY frame when the file is unavailable."""
        try:
            return fetch_depth(ctx.season)
        except nf.ContextFetchError as e:
            log.warning("depth charts: %s -- redistribution falls back to with-or-without only", e)
            return empty_frame()
