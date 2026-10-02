"""Player usage metrics (volume, not efficiency) from nflverse's `stats_player` file and its
play-by-play: `Tgt%`, `WOPR`, `Rush%`, `RZ/G`, `HVT/G`.

Pure and offline-testable, like `team_metrics.py`; the thin fetch wrapper is
`sources/nflverse_usage.py`. Why these and not others (decided with Sam 2026-10-02): published
research finds usage holds up week to week and efficiency regresses -- target share, WOPR and
(for RBs) snap share and carries predict future points; receiving TDs, receiving EPA and yards
per target predicted *negatively*; for QBs only rushing usage predicted anything. So this module
adds volume columns only. Player efficiency stats (EPA, RACR, YPT, YAC over expected) are
deliberately NOT computed here and must not be added.

Window: each player's last `USAGE_WINDOW_GAMES` games PLAYED (fewer if he has fewer). Shares are
computed over those games only, so a missed game never counts as a zero. A game counts as
"played" when the player has a row in `stats_player` for that week; a player who suited up
but recorded no stat at all has no row, and is skipped the same way a missed game is. There is
no prior-season blend: roles change between seasons, so last year's share would mislead.

Definitions (every share is a ratio of window SUMS, not a mean of weekly ratios):
- `Tgt%`  = the player's targets / his team's targets in those same team-weeks. WR, TE, RB.
- `WOPR`  = 1.5 * `Tgt%` + 0.7 * air-yards share, the same formula nflverse uses per week,
  re-evaluated over the window. WR, TE.
- `Rush%` = the player's carries / his team's carries (QB scrambles are carries). RB, QB.
- `RZ/G`  = targets + carries run from inside the opponent's 20, per game. RB, WR, TE, QB.
- `HVT/G` = "high-value touches": targets + carries from inside the opponent's 10, per game.
  RB only.
Team totals are the sum over every player in the file for that team-week. The air-yards share's
denominator is the team's PASSING air yards (the sum of its passers' `passing_air_yards`), which
is what nflverse's own `air_yards_share` divides by -- checked against all 987 WR/TE/RB rows of
the 2026 file (max difference 0.0); dividing by the sum of receivers' `receiving_air_yards`
instead is off by up to 0.09 because throwaways and spikes carry air yards no receiver is
credited with. Red-zone counts come
from the pbp (`yardline_100`, `receiver_player_id`, `rusher_player_id`, all gsis ids) over real
scrimmage plays (the `team_metrics` allowlist), excluding two-point-conversion attempts.
"""

from __future__ import annotations

import pandas as pd

from dfs.player_join import normalize_position

USAGE_WINDOW_GAMES = 3
RZ_YARDLINE = 20
HVT_YARDLINE = 10
# nflverse's own definition (`wopr` in stats_player): 1.5 * target share + 0.7 * air-yards share.
WOPR_TARGET_WEIGHT = 1.5
WOPR_AIR_YARDS_WEIGHT = 0.7
# A week counts as "through" when at least this many teams have a game in the file (a Thursday
# game alone does not): half the league.
FULL_WEEK_MIN_TEAMS = 16

USAGE_METRIC_COLUMNS = ["Tgt%", "WOPR", "Rush%", "RZ/G", "HVT/G"]
# Where a metric is meaningful. Outside it the value is blank, never 0.
USAGE_APPLIES_TO = {
    "Tgt%": {"WR", "TE", "RB"},
    "WOPR": {"WR", "TE"},
    "Rush%": {"RB", "QB"},
    "RZ/G": {"RB", "WR", "TE", "QB"},
    "HVT/G": {"RB"},
}
USAGE_PLAYER_POSITIONS = {"QB", "RB", "WR", "TE"}
USAGE_SOURCE_COLUMNS = ["GsisId", "Name", "Team", "Position", *USAGE_METRIC_COLUMNS, "Games", "ThroughWeek"]

STATS_COLUMNS = [
    "player_id",
    "player_display_name",
    "position",
    "season_type",
    "week",
    "team",
    "targets",
    "carries",
    "receiving_air_yards",
    "passing_air_yards",
]
PBP_USAGE_COLUMNS = [
    "game_id",
    "week",
    "season_type",
    "play_type",
    "pass",
    "rush",
    "yardline_100",
    "receiver_player_id",
    "rusher_player_id",
    "two_point_attempt",
    "play_deleted",
]
_SCRIMMAGE_PLAY_TYPES = {"pass", "run"}


def empty_usage_frame() -> pd.DataFrame:
    """No players: the right columns and no rows (Week 1, or a failed fetch -- every usage column blank)."""
    return pd.DataFrame(columns=USAGE_SOURCE_COLUMNS)


def _num(df: pd.DataFrame, column: str) -> pd.Series:
    if column not in df.columns:
        return pd.Series(0.0, index=df.index)
    return pd.to_numeric(df[column], errors="coerce").fillna(0.0)


def through_week(stats: pd.DataFrame) -> int | None:
    """The latest week with a (nearly) full slate in `stats` -- what the header note says the
    data runs "through". A lone Thursday game does not count, though the players in it still
    carry that game in their own window."""
    reg = stats[stats["season_type"] == "REG"] if "season_type" in stats.columns else stats
    if reg.empty:
        return None
    teams_per_week = reg.groupby("week")["team"].nunique()
    full = teams_per_week[teams_per_week >= FULL_WEEK_MIN_TEAMS]
    return int(full.index.max()) if not full.empty else int(teams_per_week.index.max())


def team_week_totals(stats: pd.DataFrame) -> pd.DataFrame:
    """Per (team, week) targets / carries / PASSING air yards summed over every player (the
    team air-yards total nflverse itself divides by -- see the module docstring)."""
    frame = pd.DataFrame(
        {
            "team": stats["team"],
            "week": pd.to_numeric(stats["week"], errors="coerce"),
            "targets": _num(stats, "targets"),
            "carries": _num(stats, "carries"),
            "air": _num(stats, "passing_air_yards"),
        }
    )
    totals = frame.groupby(["team", "week"], as_index=False).sum()
    return totals.rename(columns={"targets": "team_targets", "carries": "team_carries", "air": "team_air"})


def red_zone_counts(pbp: pd.DataFrame) -> pd.DataFrame:
    """Per (gsis id, week): `rz` = targets + carries from inside the opponent's 20, `hvt` = the
    same from inside the 10. Real scrimmage plays only (pass/run), no two-point attempts, no
    deleted plays."""
    plays = pbp
    if "season_type" in plays.columns:
        plays = plays[plays["season_type"] == "REG"]
    plays = plays[plays["play_type"].isin(_SCRIMMAGE_PLAY_TYPES)]
    if "two_point_attempt" in plays.columns:
        plays = plays[pd.to_numeric(plays["two_point_attempt"], errors="coerce").fillna(0) != 1]
    if "play_deleted" in plays.columns:
        plays = plays[pd.to_numeric(plays["play_deleted"], errors="coerce").fillna(0) != 1]
    yard = pd.to_numeric(plays["yardline_100"], errors="coerce")
    is_pass = pd.to_numeric(plays["pass"], errors="coerce").fillna(0) == 1
    is_rush = pd.to_numeric(plays["rush"], errors="coerce").fillna(0) == 1
    week = pd.to_numeric(plays["week"], errors="coerce")

    touches = []
    for ids, mask in ((plays["receiver_player_id"], is_pass), (plays["rusher_player_id"], is_rush)):
        keep = mask & ids.notna() & yard.notna()
        touches.append(pd.DataFrame({"GsisId": ids[keep], "week": week[keep], "yard": yard[keep]}))
    all_touches = pd.concat(touches, ignore_index=True)
    all_touches["rz"] = (all_touches["yard"] <= RZ_YARDLINE).astype(int)
    all_touches["hvt"] = (all_touches["yard"] <= HVT_YARDLINE).astype(int)
    return all_touches.groupby(["GsisId", "week"], as_index=False)[["rz", "hvt"]].sum()


def player_usage(
    stats: pd.DataFrame,
    pbp: pd.DataFrame | None,
    *,
    window: int = USAGE_WINDOW_GAMES,
) -> pd.DataFrame:
    """One row per player (`USAGE_SOURCE_COLUMNS`): the five metrics over his last `window`
    games played, blank outside each metric's positions. `pbp=None` blanks `RZ/G`/`HVT/G` only.
    An empty `stats` (Week 1, nothing played) returns an empty frame with the right columns."""
    if stats is None or stats.empty:
        return empty_usage_frame()
    stats = stats[stats["season_type"] == "REG"].copy() if "season_type" in stats.columns else stats.copy()
    if stats.empty:
        return empty_usage_frame()
    stats["week"] = pd.to_numeric(stats["week"], errors="coerce")
    stats["position"] = stats["position"].map(normalize_position)
    team_totals = team_week_totals(stats)
    through = through_week(stats)

    players = stats[stats["position"].isin(USAGE_PLAYER_POSITIONS)].copy()
    players["targets"] = _num(players, "targets")
    players["carries"] = _num(players, "carries")
    players["air"] = _num(players, "receiving_air_yards")
    players = players.drop_duplicates(subset=["player_id", "week"], keep="last")
    players = players.merge(team_totals, on=["team", "week"], how="left")

    # Last `window` games played, per player.
    players = players.sort_values(["player_id", "week"], ascending=[True, False])
    windowed = players.groupby("player_id", sort=False).head(window)

    if pbp is not None and not pbp.empty:
        rz = red_zone_counts(pbp).rename(columns={"GsisId": "player_id"})
        windowed = windowed.merge(rz, on=["player_id", "week"], how="left")
        windowed[["rz", "hvt"]] = windowed[["rz", "hvt"]].fillna(0)
    else:
        windowed["rz"] = float("nan")
        windowed["hvt"] = float("nan")

    agg = windowed.groupby("player_id").agg(
        targets=("targets", "sum"),
        carries=("carries", "sum"),
        air=("air", "sum"),
        team_targets=("team_targets", "sum"),
        team_carries=("team_carries", "sum"),
        team_air=("team_air", "sum"),
        rz=("rz", "sum"),
        hvt=("hvt", "sum"),
        Games=("week", "count"),
    )
    # A NaN-only column sums to 0.0 in pandas; keep it blank when pbp was unavailable.
    if pbp is None or pbp.empty:
        agg["rz"] = float("nan")
        agg["hvt"] = float("nan")

    tgt_share = agg["targets"] / agg["team_targets"].where(agg["team_targets"] > 0)
    air_share = agg["air"] / agg["team_air"].where(agg["team_air"] > 0)
    agg["Tgt%"] = tgt_share
    agg["WOPR"] = WOPR_TARGET_WEIGHT * tgt_share + WOPR_AIR_YARDS_WEIGHT * air_share
    agg["Rush%"] = agg["carries"] / agg["team_carries"].where(agg["team_carries"] > 0)
    agg["RZ/G"] = agg["rz"] / agg["Games"]
    agg["HVT/G"] = agg["hvt"] / agg["Games"]

    latest = players.sort_values(["player_id", "week"]).groupby("player_id").tail(1).set_index("player_id")
    out = agg.join(latest[["player_display_name", "team", "position"]]).reset_index()
    out = out.rename(
        columns={
            "player_id": "GsisId",
            "player_display_name": "Name",
            "team": "Team",
            "position": "Position",
        }
    )
    for metric, positions in USAGE_APPLIES_TO.items():
        out[metric] = out[metric].where(out["Position"].isin(positions))
    out["ThroughWeek"] = through
    out[["Tgt%", "Rush%"]] = out[["Tgt%", "Rush%"]].round(4)
    out["WOPR"] = out["WOPR"].round(3)
    out[["RZ/G", "HVT/G"]] = out[["RZ/G", "HVT/G"]].round(2)
    return out[USAGE_SOURCE_COLUMNS].sort_values(["Position", "Name"]).reset_index(drop=True)
