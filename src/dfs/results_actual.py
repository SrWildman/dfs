"""Actual DK points for finished games, from nflverse's free `stats_player` / `stats_team` files and
the schedule's final scores (results loop, 2026-10-02). Pure and offline-testable; the thin fetch
wrapper is `sources/nflverse_results.py`.

Offense is scored per player-week from his stat line through `dk_scoring.score_offense_actual_row`
(real yardage bonuses, not the projection path's expected-value version). Defenses are not in
`stats_player`, so each is built per team-week from `stats_team` (sacks, interceptions, fumble
recoveries, safeties, blocked kicks, return TDs, 2-point returns) plus the OPPONENT's final score
(points allowed).

Column mapping (nflverse -> DK field), all verified against DK's own points in a real Week 2
contest file (95 of 95 offensive players exact, 22 of 23 defenses exact):

- offense: `passing_yards`/`passing_tds`/`passing_interceptions`, `rushing_yards`/`rushing_tds`,
  `receptions`/`receiving_yards`/`receiving_tds`, lost fumbles = `sack_fumbles_lost` +
  `rushing_fumbles_lost` + `receiving_fumbles_lost`, two-point conversions = the passing, rushing
  and receiving `*_2pt_conversions` summed, `special_teams_tds` -> kick/punt/FG return TD,
  `fumble_recovery_tds` -> offensive fumble-recovery TD.
- defense: `def_sacks`, `def_interceptions`, `fumble_recovery_opp`, `def_safeties`, blocks =
  `def_punt_blocks` + `def_fg_blocks` + `def_pat_blocks`, 2-point returns = `def_2pt_made`, and TDs =
  `def_tds` + `special_teams_tds` + the team's `fumble_recovery_tds` MINUS what its offensive players
  recovered (an offensive fumble-recovery TD is the player's, not the defense's). `fumble_recovery_tds`
  is a separate nflverse column from `def_tds`: dropping it cost the Patriots 6 points in the check.
"""

from __future__ import annotations

import pandas as pd

from dfs.dk_scoring import (
    ACTUAL_DST_FIELDS,
    ACTUAL_OFFENSE_FIELDS,
    DST_POINTS_ALLOWED_EXCLUDES_OPP_DEF_ST_TDS,
    YARDAGE_BONUS_POINTS,
    YARDAGE_BONUS_THRESHOLDS,
    score_dst_actual_row,
    score_offense_actual_row,
)
from dfs.player_join import normalize_position

OFFENSE_POSITIONS = {"QB", "RB", "WR", "TE"}
# nflverse position labels that are offensive (before `normalize_position`): used to split an
# offensive fumble-recovery TD (the player's) from a defensive one (the defense's).
_OFFENSIVE_LABELS = {"QB", "RB", "FB", "HB", "WR", "TE"}

STATS_PLAYER_COLUMNS = [
    "player_id",
    "player_display_name",
    "position",
    "season_type",
    "week",
    "team",
    "passing_yards",
    "passing_tds",
    "passing_interceptions",
    "rushing_yards",
    "rushing_tds",
    "receptions",
    "receiving_yards",
    "receiving_tds",
    "sack_fumbles_lost",
    "rushing_fumbles_lost",
    "receiving_fumbles_lost",
    "passing_2pt_conversions",
    "rushing_2pt_conversions",
    "receiving_2pt_conversions",
    "special_teams_tds",
    "fumble_recovery_tds",
    "fantasy_points_ppr",
]
STATS_TEAM_COLUMNS = [
    "season_type",
    "week",
    "team",
    "opponent_team",
    "def_sacks",
    "def_interceptions",
    "fumble_recovery_opp",
    "fumble_recovery_tds",
    "def_safeties",
    "def_tds",
    "special_teams_tds",
    "def_punt_blocks",
    "def_fg_blocks",
    "def_pat_blocks",
    "def_2pt_made",
]


def _num(df: pd.DataFrame, column: str) -> pd.Series:
    if column not in df.columns:
        return pd.Series(0.0, index=df.index)
    return pd.to_numeric(df[column], errors="coerce").fillna(0.0)


def _regular_season(df: pd.DataFrame) -> pd.DataFrame:
    return df[df["season_type"] == "REG"].copy() if "season_type" in df.columns else df.copy()


def offense_actual_fields(stats_player: pd.DataFrame) -> pd.DataFrame:
    """One row per offensive player-week (QB/RB/WR/TE, FB/HB read as RB) with the canonical
    `ACTUAL_OFFENSE_FIELDS` columns."""
    stats = _regular_season(stats_player)
    stats["position"] = stats["position"].map(normalize_position)
    stats = stats[stats["position"].isin(OFFENSE_POSITIONS)].copy()
    out = pd.DataFrame(
        {
            "player_id": stats["player_id"],
            "Name": stats["player_display_name"],
            "Team": stats["team"],
            "Position": stats["position"],
            "week": pd.to_numeric(stats["week"], errors="coerce"),
            "pass_yd": _num(stats, "passing_yards"),
            "pass_td": _num(stats, "passing_tds"),
            "pass_int": _num(stats, "passing_interceptions"),
            "rush_yd": _num(stats, "rushing_yards"),
            "rush_td": _num(stats, "rushing_tds"),
            "rec": _num(stats, "receptions"),
            "rec_yd": _num(stats, "receiving_yards"),
            "rec_td": _num(stats, "receiving_tds"),
            "fum_lost": _num(stats, "sack_fumbles_lost")
            + _num(stats, "rushing_fumbles_lost")
            + _num(stats, "receiving_fumbles_lost"),
            "two_pt": _num(stats, "passing_2pt_conversions")
            + _num(stats, "rushing_2pt_conversions")
            + _num(stats, "receiving_2pt_conversions"),
            "ret_td": _num(stats, "special_teams_tds"),
            "fumrec_td": _num(stats, "fumble_recovery_tds"),
            "fantasy_points_ppr": _num(stats, "fantasy_points_ppr"),
        }
    )
    return out.reset_index(drop=True)


def score_offense_actual(stats_player: pd.DataFrame) -> pd.DataFrame:
    """`offense_actual_fields` plus `dk_actual`, the real DK points."""
    fields = offense_actual_fields(stats_player)
    fields["dk_actual"] = fields[ACTUAL_OFFENSE_FIELDS].apply(
        lambda row: score_offense_actual_row(row.to_dict()), axis=1
    )
    return fields


def offense_identity_residual(scored: pd.DataFrame) -> pd.Series:
    """The check Sam asked for. nflverse's `fantasy_points_ppr` scores interceptions and lost
    fumbles at -2 and has no yardage bonuses; DK scores them at -1 and adds +3 bonuses. So for
    every player-week

        DK - fantasy_points_ppr = bonuses + interceptions + fumbles_lost   (+ return TDs not in ppr)

    This returns the residual `DK - ppr - (bonuses + interceptions + fumbles lost)`; any row not
    at 0 needs an explanation (return TDs, 2-point conversions and offensive fumble-recovery TDs
    are the usual suspects, since nflverse's own formula treats them differently)."""
    bonus = sum(
        YARDAGE_BONUS_POINTS * (scored[field] >= threshold)
        for field, threshold in YARDAGE_BONUS_THRESHOLDS.items()
    )
    expected = bonus + scored["pass_int"] + scored["fum_lost"]
    return (scored["dk_actual"] - scored["fantasy_points_ppr"] - expected).round(2)


def identity_breaks(scored: pd.DataFrame) -> pd.DataFrame:
    """The rows where `offense_identity_residual` is not 0, with the stat that explains each."""
    residual = offense_identity_residual(scored)
    broken = scored[residual != 0].copy()
    broken["residual"] = residual[residual != 0]
    return broken[
        [
            "Name",
            "Team",
            "Position",
            "week",
            "dk_actual",
            "fantasy_points_ppr",
            "residual",
            "ret_td",
            "fumrec_td",
            "two_pt",
        ]
    ]


def dst_actual_fields(
    stats_team: pd.DataFrame, stats_player: pd.DataFrame, game_scores: pd.DataFrame
) -> pd.DataFrame:
    """One row per defense-week with the canonical `ACTUAL_DST_FIELDS` columns.

    `game_scores` has `week`, `away_team`, `home_team`, `away_score`, `home_score` (the schedule's
    final scores). Weeks with no final score yet get NaN `points_allowed`, which scores as NaN
    rather than a fabricated number."""
    teams = _regular_season(stats_team)
    teams["week"] = pd.to_numeric(teams["week"], errors="coerce")
    players = _regular_season(stats_player)
    players["week"] = pd.to_numeric(players["week"], errors="coerce")
    offensive = players[players["position"].isin(_OFFENSIVE_LABELS)]
    offense_recovery_tds = (
        _num(offensive, "fumble_recovery_tds").groupby([offensive["team"], offensive["week"]]).sum()
    )

    teams = teams.join(offense_recovery_tds.rename("_off_fr"), on=["team", "week"])
    teams["_off_fr"] = teams["_off_fr"].fillna(0.0)
    teams["_def_st_tds"] = (
        _num(teams, "def_tds")
        + _num(teams, "special_teams_tds")
        + (_num(teams, "fumble_recovery_tds") - teams["_off_fr"])
    )

    scores = game_scores.copy()
    scores["week"] = pd.to_numeric(scores["week"], errors="coerce")
    long = pd.concat(
        [
            pd.DataFrame(
                {
                    "week": scores["week"],
                    "team": scores["home_team"],
                    "opp": scores["away_team"],
                    "opp_score": scores["away_score"],
                }
            ),
            pd.DataFrame(
                {
                    "week": scores["week"],
                    "team": scores["away_team"],
                    "opp": scores["home_team"],
                    "opp_score": scores["home_score"],
                }
            ),
        ]
    )
    merged = teams.merge(long[["week", "team", "opp_score"]], on=["week", "team"], how="left")
    opp_tds = teams[["week", "team", "_def_st_tds"]].rename(
        columns={"team": "opponent_team", "_def_st_tds": "_opp_tds"}
    )
    merged = merged.merge(opp_tds, on=["week", "opponent_team"], how="left")
    points_allowed = pd.to_numeric(merged["opp_score"], errors="coerce")
    if DST_POINTS_ALLOWED_EXCLUDES_OPP_DEF_ST_TDS:
        points_allowed = points_allowed - 6 * merged["_opp_tds"].fillna(0.0)
    return pd.DataFrame(
        {
            "Team": merged["team"],
            "week": merged["week"],
            "Position": "DST",
            "sack": _num(merged, "def_sacks"),
            "def_int": _num(merged, "def_interceptions"),
            "fum_rec": _num(merged, "fumble_recovery_opp"),
            "def_td": merged["_def_st_tds"],
            "safety": _num(merged, "def_safeties"),
            "blocked_kick": _num(merged, "def_punt_blocks")
            + _num(merged, "def_fg_blocks")
            + _num(merged, "def_pat_blocks"),
            "two_pt_return": _num(merged, "def_2pt_made"),
            "points_allowed": points_allowed,
        }
    ).reset_index(drop=True)


def score_dst_actual(
    stats_team: pd.DataFrame, stats_player: pd.DataFrame, game_scores: pd.DataFrame
) -> pd.DataFrame:
    """`dst_actual_fields` plus `dk_actual`."""
    fields = dst_actual_fields(stats_team, stats_player, game_scores)
    fields["dk_actual"] = fields[ACTUAL_DST_FIELDS].apply(
        lambda row: score_dst_actual_row(row.to_dict()), axis=1
    )
    return fields
