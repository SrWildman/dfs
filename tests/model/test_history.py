"""History and targets: DK actual points, the DK-vs-nflverse identity, xFP, Vegas context, DST."""

import numpy as np
import pandas as pd
import pytest

from dfs.model import history as H

# Real stat lines (nflverse stats_player), with the DK points worked out by hand from DraftKings Classic
# scoring: pass yd 0.04, pass TD 4, INT -1, rush/rec yd 0.1, rush/rec TD 6, rec 1, fumble lost -1,
# +3 for 300 pass / 100 rush / 100 rec yards, +6 for an offensive fumble-recovery TD.
_ZERO = {
    "passing_yards": 0,
    "passing_tds": 0,
    "passing_interceptions": 0,
    "sack_fumbles_lost": 0,
    "rushing_yards": 0,
    "rushing_tds": 0,
    "rushing_fumbles_lost": 0,
    "receptions": 0,
    "receiving_yards": 0,
    "receiving_tds": 0,
    "receiving_fumbles_lost": 0,
    "passing_2pt_conversions": 0,
    "rushing_2pt_conversions": 0,
    "receiving_2pt_conversions": 0,
    "special_teams_tds": 0,
    "fumble_recovery_tds": 0,
    "carries": 0,
    "attempts": 0,
    "targets": 0,
    "target_share": 0.0,
    "air_yards_share": 0.0,
    "receiving_air_yards": 0,
    "passing_epa": np.nan,
}
# name, position, season, week, team, opponent, stats, nflverse fantasy_points_ppr, expected DK
REAL_ROWS = [
    # Mahomes 2024 W1: 291 yd, 1 TD, 1 INT, 3 rush yd, 1 rec for 2: 11.64 + 4 - 1 + 0.3 + 1 + 0.2
    (
        "Patrick Mahomes",
        "QB",
        2024,
        1,
        "KC",
        "BAL",
        {
            "passing_yards": 291,
            "passing_tds": 1,
            "passing_interceptions": 1,
            "rushing_yards": 3,
            "receptions": 1,
            "receiving_yards": 2,
            "attempts": 28,
            "carries": 2,
            "targets": 1,
        },
        15.14,
        16.14,
    ),
    # Mixon 2024 W1: 159 rush yd + TD, 3 rec for 19: 15.9 + 6 + 3 + 1.9, plus the +3 rushing bonus
    (
        "Joe Mixon",
        "RB",
        2024,
        1,
        "HOU",
        "IND",
        {
            "rushing_yards": 159,
            "rushing_tds": 1,
            "receptions": 3,
            "receiving_yards": 19,
            "carries": 30,
            "targets": 3,
        },
        26.8,
        29.8,
    ),
    # Amon-Ra St. Brown 2024 W15: 14 rec, 193 yd, TD, 1 fumble lost: 14 + 19.3 + 6 - 1 + 3 receiving bonus
    (
        "Amon-Ra St. Brown",
        "WR",
        2024,
        15,
        "DET",
        "BUF",
        {
            "receptions": 14,
            "receiving_yards": 193,
            "receiving_tds": 1,
            "receiving_fumbles_lost": 1,
            "targets": 18,
        },
        37.3,
        41.3,
    ),
    # Chris Moore 2017 W8: nothing but an offensive fumble-recovery TD. DK pays +6; nflverse's PPR formula 0.
    ("Chris Moore", "WR", 2017, 8, "BAL", "MIA", {"fumble_recovery_tds": 1}, 0.0, 6.0),
    # Adam Thielen 2024 W1: 3 rec for 49 -- no bonus, nothing unusual
    (
        "Adam Thielen",
        "WR",
        2024,
        1,
        "CAR",
        "NO",
        {"receptions": 3, "receiving_yards": 49, "targets": 4},
        7.9,
        7.9,
    ),
]


def _stats_player(rows=REAL_ROWS) -> pd.DataFrame:
    out = []
    for i, (name, pos, season, week, team, opp, stats, ppr, _dk) in enumerate(rows):
        out.append(
            {
                **_ZERO,
                **stats,
                "player_id": f"00-{i:07d}",
                "player_display_name": name,
                "position": pos,
                "season": season,
                "week": week,
                "season_type": "REG",
                "team": team,
                "opponent_team": opp,
                "game_id": f"{season}_{week:02d}_{opp}_{team}",
                "fantasy_points_ppr": ppr,
            }
        )
    return pd.DataFrame(out)


def test_actual_dk_points_match_hand_scoring_on_real_rows():
    scored = H.score_player_games(_stats_player()).sort_values("player_id")
    assert scored["dk_actual"].tolist() == [r[-1] for r in REAL_ROWS]


def test_dk_identity_holds_on_real_rows_once_the_fumble_recovery_td_is_accounted_for():
    scored = H.score_player_games(_stats_player())
    scored["identity_residual"] = H.identity_residual(scored)
    assert (scored["identity_residual"] == 0).all()
    # ...and the raw identity breaks for exactly the one row nflverse's PPR formula doesn't score
    from dfs.results_actual import offense_identity_residual

    raw = offense_identity_residual(scored)
    assert raw[scored["Name"] == "Chris Moore"].iloc[0] == 6.0
    assert (raw[scored["Name"] != "Chris Moore"] == 0).all()


def test_postseason_and_idless_rows_are_dropped():
    sp = _stats_player()
    post = sp.iloc[[0]].copy()
    post["season_type"] = "POST"
    idless = sp.iloc[[1]].copy()
    idless["player_id"] = None
    scored = H.score_player_games(pd.concat([sp, post, idless], ignore_index=True))
    assert len(scored) == len(sp)


def test_fullback_is_scored_as_a_running_back():
    sp = _stats_player()
    sp.loc[1, "position"] = "FB"
    assert (H.score_player_games(sp)["Position"] == "RB").sum() == 1


def _ep_row(pid, season, week, **cols):
    base = {
        "player_id": pid,
        "season": str(season),  # ffopportunity ships season as a string
        "week": float(week),
        "receptions_exp": 0.0,
        "rec_yards_gained_exp": 0.0,
        "rec_touchdown_exp": 0.0,
        "rush_yards_gained_exp": 0.0,
        "rush_touchdown_exp": 0.0,
        "pass_yards_gained_exp": 0.0,
        "pass_touchdown_exp": 0.0,
        "pass_interception_exp": 0.0,
        "pass_two_point_conv_exp": 0.0,
        "rec_two_point_conv_exp": 0.0,
        "rush_two_point_conv_exp": 0.0,
    }
    return {**base, **cols}


def test_xfp_weights_components_and_sums_a_players_rows_within_a_game():
    ep = pd.DataFrame(
        [
            # rushing row and receiving row for the same player-game
            _ep_row(
                "P", 2024, 3, rush_yards_gained_exp=80.0, rush_touchdown_exp=0.5, rush_two_point_conv_exp=0.1
            ),
            _ep_row("P", 2024, 3, receptions_exp=4.0, rec_yards_gained_exp=40.0, rec_touchdown_exp=0.2),
            _ep_row(None, 2024, 3, receptions_exp=99.0),  # team-level remainder: no gsis id
        ]
    )
    out = H.expected_dk_points(ep)
    assert len(out) == 1
    # rush: 8 + 3 + 0.2 ; rec: 4 + 4 + 1.2
    assert out["xfp"].iloc[0] == pytest.approx(8 + 3 + 0.2 + 4 + 4 + 1.2)
    # a QB: 250 pass yd, 1.5 TD, 0.8 INT
    qb = H.expected_dk_points(
        pd.DataFrame(
            [
                _ep_row(
                    "Q",
                    2024,
                    1,
                    pass_yards_gained_exp=250.0,
                    pass_touchdown_exp=1.5,
                    pass_interception_exp=0.8,
                )
            ]
        )
    )
    assert qb["xfp"].iloc[0] == pytest.approx(10 + 6 - 0.8)


def test_missing_xfp_is_zero_only_for_a_game_with_no_touches():
    pg = pd.DataFrame(
        {
            "xfp": [np.nan, np.nan, 7.0],
            "targets": [0, 3, 0],
            "carries": [0, 0, 5],
            "attempts": [0, 0, 0],
        }
    )
    filled = H._fill_xfp(pg)
    assert filled.iloc[0] == 0.0  # zero touches: ffopportunity has nothing to say, and it is a true 0
    assert np.isnan(filled.iloc[1])  # touches but no row: unknown, never silently 0
    assert filled.iloc[2] == 7.0


def test_vegas_context_is_oriented_to_each_team():
    games = pd.DataFrame(
        {
            "game_id": ["g1", "g2"],
            "game_type": ["REG", "POST"],
            "home_team": ["LA", "KC"],
            "away_team": ["SF", "BUF"],
            "total_line": [48.0, 50.0],
            "spread_line": [3.0, 1.0],  # home favoured by 3
        }
    )
    ctx = H.team_game_context(games).set_index("team")
    assert list(ctx.index) == ["LAR", "SF"]  # regular season only, LA read as LAR
    assert ctx.loc["LAR", ["implied", "spread", "total", "home"]].tolist() == [25.5, 3.0, 48.0, 1]
    assert ctx.loc["SF", ["implied", "spread", "total", "home"]].tolist() == [22.5, -3.0, 48.0, 0]
    assert ctx["implied"].sum() == 48.0


def _dst_inputs():
    team = {
        "season": 2024,
        "week": 1,
        "season_type": "REG",
        "game_id": "2024_01_SF_LA",
        "def_interceptions": 0,
        "fumble_recovery_opp": 0,
        "fumble_recovery_tds": 0,
        "def_safeties": 0,
        "def_tds": 0,
        "special_teams_tds": 0,
        "def_punt_blocks": 0,
        "def_fg_blocks": 0,
        "def_pat_blocks": 0,
        "def_2pt_made": 0,
        "passing_interceptions": 0,
        "sack_fumbles_lost": 0,
        "rushing_fumbles_lost": 0,
        "receiving_fumbles_lost": 0,
        "sacks_suffered": 0,
        "def_sacks": 0,
    }
    stats_team = pd.DataFrame(
        [
            {
                **team,
                "team": "LA",
                "opponent_team": "SF",
                "def_sacks": 3,
                "def_interceptions": 1,
                "fumble_recovery_opp": 1,
                "passing_interceptions": 2,
                "sacks_suffered": 1,
            },
            {
                **team,
                "team": "SF",
                "opponent_team": "LA",
                "def_sacks": 1,
                "sack_fumbles_lost": 1,
                "sacks_suffered": 3,
            },
        ]
    )
    stats_player = pd.DataFrame(
        {
            "player_id": ["x"],
            "season": [2024],
            "week": [1],
            "season_type": ["REG"],
            "position": ["WR"],
            "team": ["LA"],
            "fumble_recovery_tds": [0],
        }
    )
    games = pd.DataFrame(
        {
            "game_id": ["2024_01_SF_LA"],
            "season": [2024],
            "game_type": ["REG"],
            "week": [1],
            "home_team": ["LA"],
            "away_team": ["SF"],
            "home_score": [27],
            "away_score": [13],
            "total_line": [48.0],
            "spread_line": [3.0],
        }
    )
    return stats_team, stats_player, games


def test_dst_actual_points_use_the_opponents_final_score_and_team_codes_are_normalised():
    tg = H.build_team_games(*_dst_inputs()).set_index("team")
    assert sorted(tg.index) == ["LAR", "SF"]
    # LAR: 3 sacks + 1 INT (2) + 1 fumble recovery (2) + 13 points allowed (4) = 11
    assert tg.loc["LAR", "dk"] == 11.0
    # SF: 1 sack + 27 points allowed (0) = 1
    assert tg.loc["SF", "dk"] == 1.0
    assert tg.loc["LAR", ["sacks", "takeaways", "giveaways", "sacks_allowed"]].tolist() == [3, 2, 2, 1]
    # what each offense "allows" is the DST points the other defense scored against it
    assert tg.loc["LAR", "dst_conceded"] == 1.0
    assert tg.loc["SF", "dst_conceded"] == 11.0
    assert tg.loc["LAR", "opp"] == "SF" and tg.loc["SF", "opp"] == "LAR"
    assert tg.loc["LAR", "implied"] == 25.5 and tg.loc["SF", "implied"] == 22.5


def test_unplayed_games_have_no_dst_points_and_are_dropped():
    stats_team, stats_player, games = _dst_inputs()
    games[["home_score", "away_score"]] = np.nan
    assert H.build_team_games(stats_team, stats_player, games).empty


def test_def_vs_pos_is_net_of_the_weeks_league_average():
    pg = pd.DataFrame(
        {
            "opp": ["X", "Y", "X", "Y"],
            "position": ["WR"] * 4,
            "season": [2024] * 4,
            "week": [1, 1, 2, 2],
            "t": [202401, 202401, 202402, 202402],
            "dk": [30.0, 10.0, 12.0, 12.0],
        }
    )
    out = H.build_def_vs_pos(pg).set_index(["team", "t"])
    assert out.loc[("X", 202401), "rel_allowed"] == 10.0  # allowed 30 vs a league average of 20
    assert out.loc[("Y", 202401), "rel_allowed"] == -10.0
    assert out.loc[("X", 202402), "rel_allowed"] == 0.0


def test_history_before_cuts_every_table(history):
    h = history.before(202403)
    assert h.player_games["t"].max() < 202403
    assert h.team_games["t"].max() < 202403
    assert h.def_vs_pos["t"].max() < 202403
