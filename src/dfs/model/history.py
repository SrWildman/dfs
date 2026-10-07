"""Part 1: history and targets. Turns the raw nflverse / ffopportunity files into the three tables the
model works from, all keyed by gsis id (offense) or team (defense), season and week:

- `player_games`: one row per offensive player-game -- the ACTUAL DK points (`dk`), the expected DK points
  from ffopportunity (`xfp`), the usage stats the features roll up, and that game's Vegas context.
- `team_games`: one row per team-game -- the defense's actual DK points (`dk`) and the counting stats the
  DST features roll up.
- `def_vs_pos`: DK points each defense allowed to each position, net of the league average that week.

Actual DK points come from `dfs.results_actual` -- the same scorer (`dk_scoring`'s ACTUAL scorers: real
yardage bonuses, exact points-allowed tiers) the results loop already trusts -- so a model trained on this
history and a Model Check row are measured against the same number. This module only adds what the results
loop does not carry: season, opponent, usage stats, xFP and Vegas context.

Pure functions over frames (`build_history`) plus a thin cache reader (`load_history`), so tests need no
network and no cache.
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from dfs.dk_scoring import DK_FUMBLE_RECOVERY_TD_POINTS
from dfs.model import data
from dfs.results_actual import (
    offense_identity_residual,
    score_dst_actual,
    score_offense_actual,
)

OFFENSE_POSITIONS = ("QB", "RB", "WR", "TE")
POSITIONS = (*OFFENSE_POSITIONS, "DST")

# ffopportunity's expected components -> DK points. Yardage bonuses are deliberately NOT part of xFP.
XFP_WEIGHTS = {
    "receptions_exp": 1.0,
    "rec_yards_gained_exp": 0.1,
    "rec_touchdown_exp": 6.0,
    "rush_yards_gained_exp": 0.1,
    "rush_touchdown_exp": 6.0,
    "pass_yards_gained_exp": 0.04,
    "pass_touchdown_exp": 4.0,
    "pass_interception_exp": -1.0,
}
XFP_TWO_POINT_COLUMNS = ("pass_two_point_conv_exp", "rec_two_point_conv_exp", "rush_two_point_conv_exp")
XFP_TWO_POINT_POINTS = 2.0

# Usage columns carried from `stats_player` onto every player-game.
USAGE_COLUMNS = (
    "targets",
    "carries",
    "attempts",
    "target_share",
    "air_yards_share",
    "receiving_air_yards",
    "passing_epa",
)
# Counts and shares that are 0, not unknown, when the player was on the field but never targeted / never
# threw. nflverse leaves the shares NaN there; a rolling mean must not skip those games.
_ZERO_WHEN_NO_USAGE = (
    "targets",
    "carries",
    "attempts",
    "target_share",
    "air_yards_share",
    "receiving_air_yards",
)

CONTEXT_COLUMNS = ["implied", "spread", "total", "home"]


@dataclass
class History:
    """Everything the feature builder needs, as of whenever the underlying files were last fetched."""

    player_games: pd.DataFrame
    team_games: pd.DataFrame
    def_vs_pos: pd.DataFrame

    def before(self, t: int) -> History:
        """The history as it stood before time key `t` (`season * 100 + week`): used by tests, and
        proof that inference never sees the week it predicts."""
        return History(
            player_games=self.player_games[self.player_games["t"] < t].reset_index(drop=True),
            team_games=self.team_games[self.team_games["t"] < t].reset_index(drop=True),
            def_vs_pos=self.def_vs_pos[self.def_vs_pos["t"] < t].reset_index(drop=True),
        )


def time_key(season: pd.Series | int, week: pd.Series | int):
    """One sortable integer per (season, week) -- the axis every rolling window and lookup runs along."""
    return season * 100 + week


def team_game_context(games: pd.DataFrame) -> pd.DataFrame:
    """One row per (game_id, team) with the Vegas context from THAT team's side: `implied` team total,
    `spread` (positive = this team favoured), game `total`, `home`. nflverse's `spread_line` is from the home
    team's perspective with positive = home favoured, so the home side's implied total is
    `total/2 + spread/2` and the away side's `total/2 - spread/2`."""
    g = games[games["game_type"] == "REG"].copy()
    total = pd.to_numeric(g["total_line"], errors="coerce")
    spread = pd.to_numeric(g["spread_line"], errors="coerce")
    home = pd.DataFrame(
        {
            "game_id": g["game_id"],
            "team": g["home_team"].map(data.normalize_team),
            "implied": total / 2 + spread / 2,
            "spread": spread,
            "total": total,
            "home": 1,
        }
    )
    away = pd.DataFrame(
        {
            "game_id": g["game_id"],
            "team": g["away_team"].map(data.normalize_team),
            "implied": total / 2 - spread / 2,
            "spread": -spread,
            "total": total,
            "home": 0,
        }
    )
    return pd.concat([home, away], ignore_index=True)


def expected_dk_points(ep: pd.DataFrame) -> pd.DataFrame:
    """`xfp` per player-game: ffopportunity's expected components, weighted to DK scoring and SUMMED over
    that player's rows in the game (a player can carry more than one row per game). Rows without a gsis id
    (team-level remainders) are dropped."""
    e = ep[ep["player_id"].notna()].copy()
    e["season"] = e["season"].astype(int)
    e["week"] = e["week"].astype(int)
    xfp = sum(e[col] * weight for col, weight in XFP_WEIGHTS.items())
    xfp = xfp + XFP_TWO_POINT_POINTS * sum(e[col] for col in XFP_TWO_POINT_COLUMNS)
    e["xfp"] = xfp
    return e.groupby(["player_id", "season", "week"], as_index=False)["xfp"].sum()


def _regular_season_players(stats_player: pd.DataFrame) -> pd.DataFrame:
    sp = stats_player[stats_player["player_id"].notna() & (stats_player["season_type"] == "REG")].copy()
    sp["season"] = sp["season"].astype(int)
    sp["week"] = sp["week"].astype(int)
    sp["team"] = sp["team"].map(data.normalize_team)
    sp["opponent_team"] = sp["opponent_team"].map(data.normalize_team)
    return sp


def score_player_games(stats_player: pd.DataFrame) -> pd.DataFrame:
    """Actual DK points per offensive player-game across every season in `stats_player`, via the results
    loop's scorer (which is per season, as it keys on week alone)."""
    sp = _regular_season_players(stats_player)
    frames = []
    for season, chunk in sp.groupby("season"):
        scored = score_offense_actual(chunk)
        scored["season"] = season
        frames.append(scored)
    return pd.concat(frames, ignore_index=True)


def identity_residual(scored: pd.DataFrame) -> pd.Series:
    """The DK-vs-nflverse identity check: `DK - fantasy_points_ppr` must equal yardage bonuses +
    interceptions + lost fumbles, plus the one line nflverse's PPR formula never scores and DK does -- an
    offensive fumble-recovery TD (+6). Every row must come back 0.

    (Return TDs are in nflverse's total already, so they cancel; this was checked on every row 2014-2026.)"""
    residual = offense_identity_residual(scored)
    return (residual - DK_FUMBLE_RECOVERY_TD_POINTS * scored["fumrec_td"]).round(2)


def _fill_xfp(player_games: pd.DataFrame) -> pd.Series:
    """A missing ffopportunity row means "no opportunities" only when the player really had none: every
    such row 2014-2026 has zero targets, carries and attempts and averages 0.06 DK points. So xFP is 0
    exactly there -- and stays NaN if a row WITH touches ever lacks one, rather than silently reading 0."""
    touches = player_games[["targets", "carries", "attempts"]].fillna(0).sum(axis=1)
    return player_games["xfp"].where(player_games["xfp"].notna() | (touches > 0), 0.0)


def build_player_games(
    stats_player: pd.DataFrame, ep: pd.DataFrame, games: pd.DataFrame
) -> tuple[pd.DataFrame, dict]:
    """The `player_games` table plus its join report (`join_report`)."""
    sp = _regular_season_players(stats_player)
    scored = score_player_games(stats_player)
    raw_breaks = int((offense_identity_residual(scored) != 0).sum())
    scored["identity_residual"] = identity_residual(scored)

    meta = sp[["player_id", "season", "week", "game_id", "opponent_team", *USAGE_COLUMNS]].copy()
    pg = scored.merge(meta, on=["player_id", "season", "week"], how="left", validate="one_to_one")
    pg = pg.merge(expected_dk_points(ep), on=["player_id", "season", "week"], how="left")
    pg["has_ep"] = pg["xfp"].notna()
    pg["xfp"] = _fill_xfp(pg)
    for col in _ZERO_WHEN_NO_USAGE:
        pg[col] = pg[col].fillna(0.0)

    ctx = team_game_context(games)
    pg = pg.merge(ctx, left_on=["game_id", "Team"], right_on=["game_id", "team"], how="left")
    out = pd.DataFrame(
        {
            "gsis_id": pg["player_id"],
            "name": pg["Name"],
            "position": pg["Position"],
            "team": pg["Team"],
            "opp": pg["opponent_team"],
            "season": pg["season"],
            "week": pg["week"],
            "t": time_key(pg["season"], pg["week"]),
            "game_id": pg["game_id"],
            "dk": pg["dk_actual"],
            "xfp": pg["xfp"],
            "identity_residual": pg["identity_residual"],
            **{col: pg[col] for col in USAGE_COLUMNS},
            **{col: pg[col] for col in CONTEXT_COLUMNS},
        }
    )
    out = out.sort_values(["gsis_id", "t"]).reset_index(drop=True)
    return out, join_report(pg, out, raw_breaks)


def join_report(joined: pd.DataFrame, player_games: pd.DataFrame, raw_breaks: int) -> dict:
    """The numbers Sam asked for: how much of each source landed on the player-games, and the identity
    check's break count."""
    touches = joined[["targets", "carries", "attempts"]].fillna(0).sum(axis=1)
    has_touch = touches > 0
    return {
        "player_games": int(len(player_games)),
        "ep_join_rate": float(joined["has_ep"].mean()),
        "ep_join_rate_with_touches": float(joined.loc[has_touch, "has_ep"].mean()),
        "no_ep_rows": int((~joined["has_ep"]).sum()),
        "no_ep_rows_with_touches": int((~joined["has_ep"] & has_touch).sum()),
        "no_ep_mean_dk": float(joined.loc[~joined["has_ep"], "dk_actual"].mean()),
        "games_join_rate": float(player_games["total"].notna().mean()),
        "dk_missing": int(player_games["dk"].isna().sum()),
        "identity_breaks": int((player_games["identity_residual"] != 0).sum()),
        "identity_breaks_raw": raw_breaks,
    }


def build_team_games(
    stats_team: pd.DataFrame, stats_player: pd.DataFrame, games: pd.DataFrame
) -> pd.DataFrame:
    """One row per completed team-game: the defense's actual DK points (`dk`) and the counting stats the
    DST features roll up. Scored per season through the results loop's DST scorer (own team's defensive
    stats plus the opponent's final score; points the opponent's defense or special teams scored are not
    charged to it -- `dk_scoring.DST_POINTS_ALLOWED_EXCLUDES_OPP_DEF_ST_TDS`)."""
    st = stats_team[stats_team["season_type"] == "REG"].copy()
    st["season"] = st["season"].astype(int)
    st["week"] = st["week"].astype(int)
    for col in ("team", "opponent_team"):
        st[col] = st[col].map(data.normalize_team)
    sp = stats_player[stats_player["player_id"].notna()].copy()
    sp["season"] = sp["season"].astype(int)
    sp["team"] = sp["team"].map(data.normalize_team)
    scores = games[games["game_type"] == "REG"].copy()
    for col in ("home_team", "away_team"):
        scores[col] = scores[col].map(data.normalize_team)

    scored_frames = []
    for season, chunk in st.groupby("season"):
        scored = score_dst_actual(
            chunk, sp[sp["season"] == season], scores[scores["season"] == season]
        ).rename(columns={"Team": "team"})
        scored["season"] = season
        scored_frames.append(scored[["team", "season", "week", "dk_actual"]])
    scored_all = pd.concat(scored_frames, ignore_index=True)

    st["giveaways"] = (
        st["passing_interceptions"]
        + st["sack_fumbles_lost"]
        + st["rushing_fumbles_lost"]
        + st["receiving_fumbles_lost"]
    )
    st["takeaways"] = st["def_interceptions"] + st["fumble_recovery_opp"]
    tg = st.merge(scored_all, on=["team", "season", "week"], how="left")
    tg = tg.merge(team_game_context(games), on=["game_id", "team"], how="left")
    # DST points the opponent's defense scored against this team's offense: what this offense "allows".
    opp_dk = tg[["game_id", "team", "dk_actual"]].rename(
        columns={"team": "opponent_team", "dk_actual": "dst_conceded"}
    )
    tg = tg.merge(opp_dk, on=["game_id", "opponent_team"], how="left")
    out = pd.DataFrame(
        {
            "gsis_id": tg["team"],
            "position": "DST",
            "team": tg["team"],
            "opp": tg["opponent_team"],
            "season": tg["season"],
            "week": tg["week"],
            "t": time_key(tg["season"], tg["week"]),
            "game_id": tg["game_id"],
            "dk": tg["dk_actual"],
            "sacks": tg["def_sacks"],
            "takeaways": tg["takeaways"],
            "giveaways": tg["giveaways"],
            "sacks_allowed": tg["sacks_suffered"],
            "dst_conceded": tg["dst_conceded"],
            **{col: tg[col] for col in CONTEXT_COLUMNS},
        }
    )
    out = out[out["dk"].notna()]
    return out.sort_values(["team", "t"]).reset_index(drop=True)


def build_def_vs_pos(player_games: pd.DataFrame) -> pd.DataFrame:
    """DK points each defense allowed to each position per game, net of the league average for that same
    (season, week, position) -- the schedule-light adjustment. A defense that faced three bad offenses in
    a row reads as stingy in raw points; net of the week's average it reads as what it is."""
    allowed = (
        player_games.groupby(["opp", "position", "season", "week", "t"], as_index=False)["dk"]
        .sum()
        .rename(columns={"opp": "team", "dk": "allowed"})
    )
    league = allowed.groupby(["position", "season", "week"])["allowed"].transform("mean")
    allowed["rel_allowed"] = allowed["allowed"] - league
    return allowed.sort_values(["team", "position", "t"]).reset_index(drop=True)


def build_history(
    stats_player: pd.DataFrame,
    stats_team: pd.DataFrame,
    ep: pd.DataFrame,
    games: pd.DataFrame,
) -> tuple[History, dict]:
    """Build the whole history from the raw frames, and the join report."""
    player_games, report = build_player_games(stats_player, ep, games)
    team_games = build_team_games(stats_team, stats_player, games)
    report["team_games"] = int(len(team_games))
    report["team_games_context_rate"] = float(team_games["total"].notna().mean())
    return History(player_games, team_games, build_def_vs_pos(player_games)), report


def load_history(seasons: list[int]) -> tuple[History, dict]:
    """Build the history from the cached files (`dfs model fetch` first)."""
    games = data.read_games()
    return build_history(
        data.read_season_files("stats_player", seasons),
        data.read_season_files("stats_team", seasons),
        data.read_season_files("ep_weekly", seasons),
        games,
    )
