"""nflverse's weekly injury reports (`injuries_<season>.parquet`), reduced to skill-position players.

One row per player-week: `report_status` (Out / Doubtful / Questionable, the game-day designation) and
`practice_status`. It feeds `injury_beneficiaries.py`, never a tab. The file also covers the whole
season, so the results backfill reads the same file and filters by week.

Fail soft: any fetch or parse problem logs a warning and returns an EMPTY frame (columns only), so the
injury-beneficiary list goes blank and the sync carries on. The empty frame overwrites any stale
`data/current/nflverse_injuries.csv`.
"""

from __future__ import annotations

import pandas as pd

from dfs.log import get_logger
from dfs.player_join import normalize_position, normalize_team
from dfs.sources import nflverse_files as nf
from dfs.sources.base import Source, SyncContext

log = get_logger("sources.nflverse_injuries")

SKILL_POSITIONS = {"QB", "RB", "WR", "TE"}
OUTPUT_COLUMNS = [
    "season",
    "week",
    "Team",
    "GsisId",
    "Name",
    "Position",
    "report_status",
    "practice_status",
    "report_primary_injury",
]
_READ_COLUMNS = [
    "season",
    "game_type",
    "team",
    "week",
    "gsis_id",
    "position",
    "full_name",
    "report_status",
    "practice_status",
    "report_primary_injury",
]


def empty_frame() -> pd.DataFrame:
    """No rows, the right columns."""
    return pd.DataFrame(columns=OUTPUT_COLUMNS)


def reduce_injuries(raw: pd.DataFrame) -> pd.DataFrame:
    """Regular-season skill-position rows in `OUTPUT_COLUMNS` (nflverse's own team codes mapped to DK's)."""
    if raw is None or raw.empty:
        return empty_frame()
    df = raw.copy()
    if "game_type" in df.columns:
        df = df[df["game_type"] == "REG"]
    df["Position"] = df["position"].map(normalize_position)
    df = df[df["Position"].isin(SKILL_POSITIONS)]
    out = pd.DataFrame(
        {
            "season": pd.to_numeric(df["season"], errors="coerce"),
            "week": pd.to_numeric(df["week"], errors="coerce"),
            "Team": df["team"].map(normalize_team),
            "GsisId": df["gsis_id"],
            "Name": df["full_name"],
            "Position": df["Position"],
            "report_status": df.get("report_status"),
            "practice_status": df.get("practice_status"),
            "report_primary_injury": df.get("report_primary_injury"),
        }
    )
    return out.dropna(subset=["GsisId", "week"])[OUTPUT_COLUMNS].reset_index(drop=True)


def fetch_injuries(season: int) -> pd.DataFrame:
    """The reduced injury frame for one season; raises `ContextFetchError`."""
    content = nf.download(nf.INJURIES_URL.format(season=season), what=f"{season} injuries")
    return reduce_injuries(nf.read_parquet(content, _READ_COLUMNS, what=f"{season} injuries"))


class NflverseInjuriesSource(Source):
    """This season's injury reports; no sheet tab."""

    name = "nflverse_injuries"
    uploads_to_sheet = False

    def fetch(self, ctx: SyncContext) -> pd.DataFrame:
        """The season's skill-position injury rows, or an EMPTY frame when the file is unavailable."""
        try:
            return fetch_injuries(ctx.season)
        except nf.ContextFetchError as e:
            log.warning("injuries: %s -- the injury beneficiaries list will be empty this run", e)
            return empty_frame()
