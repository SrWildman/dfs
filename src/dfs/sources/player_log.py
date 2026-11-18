"""Each QB / RB / WR / TE's last five games played (`usage_log`), saved as `data/current/player_log.csv`.

Same free nflverse files `usage` and `snaps` already read, kept per GAME instead of reduced to one row per
player: the stats_player box score (the newest snapshot `usage` saved, downloaded only when there is none),
the play-by-play for red-zone looks and the snap-count release for snap share. `uploads_to_sheet = False`:
no tab. Its readers: EdgeRaw's last-five columns (`opportunity.attach_last5`), the Week page's player drawer
(`week_page.player_log`), the salary watch and team news. (It fed the sheet's Player tab until removal F9.)

Fail soft, like `usage`: a file that cannot be fetched blanks its columns (a pbp failure blanks RZ and Inside
5, a snaps failure blanks Snap%), and no stats file gives an EMPTY frame, never an old week's rows.
"""

from __future__ import annotations

import io

import pandas as pd

from dfs import usage_metrics
from dfs.log import get_logger
from dfs.paths import RAW_DIR
from dfs.player_join import normalize_name, normalize_position
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

GAMES = 5
INSIDE_FIVE = 5  # "goal-line looks": targets + carries from inside the opponent's 5
GAME_FIELDS = ["Wk", "Opp", "Snap%", "Tgt", "Car", "Tgt%", "RZ", "In5", "DK"]
LOG_COLUMNS = ["NameKey", "Name", "Team", "Position", "Back", *GAME_FIELDS]


def snap_log_frame(snap_counts: pd.DataFrame) -> pd.DataFrame:
    """nflverse snap_counts rows reduced to `NameKey, Wk, Snap%` (the offense share, a fraction)."""
    if snap_counts is None or snap_counts.empty:
        return pd.DataFrame(columns=["NameKey", "Wk", "Snap%"])
    frame = pd.DataFrame(
        {
            "NameKey": snap_counts["player"].map(normalize_name),
            "Wk": pd.to_numeric(snap_counts["week"], errors="coerce"),
            "Snap%": pd.to_numeric(snap_counts["offense_pct"], errors="coerce"),
        }
    )
    if "game_type" in snap_counts.columns:
        frame = frame[snap_counts["game_type"].eq("REG").to_numpy()]
    return frame.dropna(subset=["Wk"]).drop_duplicates(["NameKey", "Wk"], keep="last")


def usage_log(
    stats: pd.DataFrame,
    pbp: pd.DataFrame | None,
    snaps: pd.DataFrame | None,
    *,
    games: int = GAMES,
) -> pd.DataFrame:
    """One row per (player, game) for each QB / RB / WR / TE's last `games` games PLAYED (`LOG_COLUMNS`,
    `Back` = 1 for the latest): snap share, targets, carries, target share of his team, red-zone and
    inside-the-5 looks, and the real DK points. The window is the same rule as `usage_metrics`: a game counts
    when he has a stats row, so a missed week is skipped, never a zero. `pbp` / `snaps` None blank theirs.
    """
    from dfs import results_actual

    if stats is None or stats.empty:
        return pd.DataFrame(columns=LOG_COLUMNS)
    stats = stats[stats["season_type"] == "REG"].copy() if "season_type" in stats.columns else stats.copy()
    stats["position"] = stats["position"].map(normalize_position)
    stats["week"] = pd.to_numeric(stats["week"], errors="coerce")
    totals = usage_metrics.team_week_totals(stats)
    scored = results_actual.score_offense_actual(stats)[["player_id", "week", "dk_actual"]]
    players = stats[stats["position"].isin(usage_metrics.USAGE_PLAYER_POSITIONS)].copy()
    players = players.drop_duplicates(["player_id", "week"], keep="last")
    players = players.merge(totals, on=["team", "week"], how="left").merge(
        scored.drop_duplicates(["player_id", "week"]), on=["player_id", "week"], how="left"
    )
    players = players.sort_values(["player_id", "week"], ascending=[True, False])
    window = players.groupby("player_id", sort=False).head(games).copy()
    window["Back"] = window.groupby("player_id").cumcount() + 1
    window["targets"] = pd.to_numeric(window["targets"], errors="coerce").fillna(0.0)
    window["carries"] = pd.to_numeric(window["carries"], errors="coerce").fillna(0.0)
    window["tgt_share"] = (window["targets"] / window["team_targets"]).where(window["team_targets"] > 0)
    # A quarterback's targets are not a role: blank, never a 0% that reads like one.
    passer = window["position"] == "QB"
    window.loc[passer, ["targets", "tgt_share"]] = float("nan")

    if pbp is not None and not pbp.empty:
        looks = usage_metrics.red_zone_counts(pbp, goal_line=INSIDE_FIVE).rename(
            columns={"GsisId": "player_id"}
        )
        window = window.merge(looks, on=["player_id", "week"], how="left")
        window[["rz", "gl"]] = window[["rz", "gl"]].fillna(0)
    else:
        window["rz"] = window["gl"] = float("nan")

    window["NameKey"] = window["player_display_name"].map(normalize_name)
    if snaps is not None and not snaps.empty:
        window = window.merge(
            snaps.rename(columns={"Wk": "week"}).drop_duplicates(["NameKey", "week"]),
            on=["NameKey", "week"],
            how="left",
        )
    else:
        window["Snap%"] = float("nan")

    out = pd.DataFrame(
        {
            "NameKey": window["NameKey"],
            "Name": window["player_display_name"],
            "Team": window["team"],
            "Position": window["position"],
            "Back": window["Back"],
            "Wk": window["week"].astype("Int64"),
            "Opp": window["opponent_team"],
            "Snap%": window["Snap%"],
            "Tgt": window["targets"],
            "Car": window["carries"],
            "Tgt%": window["tgt_share"],
            "RZ": window["rz"],
            "In5": window["gl"],
            "DK": window["dk_actual"],
        }
    )
    return out[LOG_COLUMNS].sort_values(["Name", "Back"]).reset_index(drop=True)


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
            log.warning("player_log: %s -- the last-five games are blank this run", e)
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
