"""The Week page's usage view (Plan O item 6): every game of the season, per player and per team.

Pure and offline-testable, like `usage_metrics.py` and `team_metrics.py`; the fetch wrapper is
`sources/usage_weeks.py`, which saves `data/current/usage_weeks.csv` (players) and
`data/current/team_weeks.csv` (teams) on a full `dfs sync`. Nothing here reaches a sheet.

Players (`PLAYER_COLUMNS`, one row per QB / RB / WR / TE game PLAYED, the `usage_metrics` rule: a game
counts when he has a stats row): snap share (nflverse's snap counts, by name key), targets, carries, his
share of the team's targets, receiving air yards and his share of the team's PASSING air yards (the
denominator nflverse's own `air_yards_share` uses), WOPR (1.5 x target share + 0.7 x air-yards share, per
game), red-zone looks (targets + carries from inside the opponent's 20) and high-value touches (the same
from inside the 10; `usage_metrics.red_zone_counts`), and the real DK points. A quarterback's targets and
shares are blank, never a 0% that reads like a role.

Teams (`TEAM_COLUMNS`, one row per team-game): scrimmage plays (pass and run, no two-point tries, no
deleted plays), the dropback rate (`pass` = 1, which counts sacks and scrambles, over those plays), PROE
and pace (neutral script, `team_metrics.team_proe` / `team_pace` on that week's plays alone), red-zone
TRIPS (distinct drives with a snap from inside the opponent's 20), and the team's targets and carries.
Team codes are DraftKings' (`NFLVERSE_TO_DK_TEAM`: LA = LAR).
"""

from __future__ import annotations

import pandas as pd

from dfs import team_metrics, usage_metrics
from dfs.player_join import normalize_name, normalize_position

TEAM_ALIASES = {"LA": "LAR"}  # nflverse -> DraftKings (sources.nflverse_games.NFLVERSE_TO_DK_TEAM)

PLAYER_COLUMNS = [
    "NameKey",
    "Name",
    "Team",
    "Position",
    "Wk",
    "Opp",
    "Snap%",
    "Tgt",
    "Car",
    "Tgt%",
    "Air",
    "Air%",
    "WOPR",
    "RZ",
    "HVT",
    "DK",
]
TEAM_COLUMNS = ["Team", "Wk", "Opp", "Plays", "Pass%", "PROE", "Pace", "RZTrips", "Tgt", "Car"]
PBP_COLUMNS = sorted(
    {
        *usage_metrics.PBP_USAGE_COLUMNS,
        "posteam",
        "defteam",
        "drive",
        "play_id",
        "game_seconds_remaining",
        "half_seconds_remaining",
        "qtr",
        "wp",
        "pass_oe",
    }
)


def _team(code: object) -> object:
    return TEAM_ALIASES.get(code, code) if isinstance(code, str) else code


def scrimmage(pbp: pd.DataFrame) -> pd.DataFrame:
    """Regular-season pass and run plays, no two-point tries and no deleted plays."""
    plays = pbp
    if "season_type" in plays.columns:
        plays = plays[plays["season_type"] == "REG"]
    plays = plays[plays["play_type"].isin({"pass", "run"}) & plays["posteam"].notna()]
    for flag in ("two_point_attempt", "play_deleted"):
        if flag in plays.columns:
            plays = plays[pd.to_numeric(plays[flag], errors="coerce").fillna(0) != 1]
    return plays


def team_weeks(pbp: pd.DataFrame | None, stats: pd.DataFrame | None = None) -> pd.DataFrame:
    """One row per team-game (`TEAM_COLUMNS`). `stats` (nflverse stats_player) adds the team's targets and
    carries; without it those two are blank. An empty or missing pbp gives an empty frame."""
    if pbp is None or pbp.empty:
        return pd.DataFrame(columns=TEAM_COLUMNS)
    plays = scrimmage(pbp).copy()
    plays["week"] = pd.to_numeric(plays["week"], errors="coerce")
    rows = []
    for week, wk in plays.groupby("week"):
        by = wk.groupby("posteam")
        dropback = pd.to_numeric(wk["pass"], errors="coerce").fillna(0).eq(1)
        yard = pd.to_numeric(wk["yardline_100"], errors="coerce")
        trips = (
            wk[yard <= usage_metrics.RZ_YARDLINE]
            .groupby("posteam")[["game_id", "drive"]]
            .apply(lambda d: len(d.drop_duplicates()))
        )
        frame = pd.DataFrame(
            {
                "Plays": by.size(),
                "Pass%": dropback.groupby(wk["posteam"]).mean(),
                "PROE": team_metrics.team_proe(wk),
                "Pace": team_metrics.team_pace(wk),
                "RZTrips": trips,
                "Opp": by["defteam"].first() if "defteam" in wk.columns else None,
            }
        )
        frame["Wk"] = int(week)
        rows.append(frame.rename_axis("Team").reset_index())
    out = pd.concat(rows, ignore_index=True)
    out["RZTrips"] = out["RZTrips"].fillna(0).astype(int)
    out["Pass%"] = out["Pass%"].round(3)
    if stats is not None and not stats.empty:
        st = stats[stats["season_type"] == "REG"] if "season_type" in stats.columns else stats
        totals = usage_metrics.team_week_totals(st).rename(
            columns={"team": "Team", "week": "Wk", "team_targets": "Tgt", "team_carries": "Car"}
        )
        out = out.merge(totals[["Team", "Wk", "Tgt", "Car"]], on=["Team", "Wk"], how="left")
    else:
        out["Tgt"] = out["Car"] = float("nan")
    out["Team"] = out["Team"].map(_team)
    out["Opp"] = out["Opp"].map(_team)
    return out[TEAM_COLUMNS].sort_values(["Team", "Wk"]).reset_index(drop=True)


def player_weeks(
    stats: pd.DataFrame | None, pbp: pd.DataFrame | None, snaps: pd.DataFrame | None
) -> pd.DataFrame:
    """One row per QB / RB / WR / TE game played (`PLAYER_COLUMNS`). `pbp` None blanks RZ / HVT,
    `snaps` None (a `player_card.snap_log_frame`) blanks Snap%."""
    from dfs import results_actual

    if stats is None or stats.empty:
        return pd.DataFrame(columns=PLAYER_COLUMNS)
    stats = stats[stats["season_type"] == "REG"].copy() if "season_type" in stats.columns else stats.copy()
    if stats.empty:
        return pd.DataFrame(columns=PLAYER_COLUMNS)
    stats["position"] = stats["position"].map(normalize_position)
    stats["week"] = pd.to_numeric(stats["week"], errors="coerce")
    totals = usage_metrics.team_week_totals(stats)
    scored = results_actual.score_offense_actual(stats)[["player_id", "week", "dk_actual"]]
    p = stats[stats["position"].isin(usage_metrics.USAGE_PLAYER_POSITIONS)].copy()
    p = p.drop_duplicates(["player_id", "week"], keep="last")
    p = p.merge(totals, on=["team", "week"], how="left").merge(
        scored.drop_duplicates(["player_id", "week"]), on=["player_id", "week"], how="left"
    )
    for c in ("targets", "carries", "receiving_air_yards"):
        p[c] = pd.to_numeric(p[c], errors="coerce").fillna(0.0) if c in p.columns else 0.0
    p["tgt_share"] = (p["targets"] / p["team_targets"]).where(p["team_targets"] > 0)
    p["air_share"] = (p["receiving_air_yards"] / p["team_air"]).where(p["team_air"] > 0)
    p["wopr"] = usage_metrics.WOPR_TARGET_WEIGHT * p["tgt_share"] + usage_metrics.WOPR_AIR_YARDS_WEIGHT * p[
        "air_share"
    ].fillna(0)
    passer = p["position"] == "QB"
    p.loc[passer, ["targets", "tgt_share", "receiving_air_yards", "air_share", "wopr"]] = float("nan")
    if pbp is not None and not pbp.empty:
        looks = usage_metrics.red_zone_counts(pbp).rename(columns={"GsisId": "player_id"})
        p = p.merge(looks, on=["player_id", "week"], how="left")
        p[["rz", "hvt"]] = p[["rz", "hvt"]].fillna(0)
    else:
        p["rz"] = p["hvt"] = float("nan")
    p["NameKey"] = p["player_display_name"].map(normalize_name)
    if snaps is not None and not snaps.empty:
        p = p.merge(
            snaps.rename(columns={"Wk": "week"}).drop_duplicates(["NameKey", "week"]),
            on=["NameKey", "week"],
            how="left",
        )
    else:
        p["Snap%"] = float("nan")
    out = pd.DataFrame(
        {
            "NameKey": p["NameKey"],
            "Name": p["player_display_name"],
            "Team": p["team"].map(_team),
            "Position": p["position"],
            "Wk": p["week"].astype("Int64"),
            "Opp": p["opponent_team"].map(_team),
            "Snap%": p["Snap%"],
            "Tgt": p["targets"],
            "Car": p["carries"],
            "Tgt%": p["tgt_share"].round(3),
            "Air": p["receiving_air_yards"],
            "Air%": p["air_share"].round(3),
            "WOPR": p["wopr"].round(3),
            "RZ": p["rz"],
            "HVT": p["hvt"],
            "DK": p["dk_actual"],
        }
    )
    return out[PLAYER_COLUMNS].sort_values(["Name", "Wk"]).reset_index(drop=True)
