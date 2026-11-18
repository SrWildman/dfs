"""The game log (`sources/player_log.py`): each player's last five games played, game by game, which
EdgeRaw's last-five columns, the Week page's drawer, the salary watch and team news read."""

import pandas as pd
import pytest

from dfs.sources import player_log
from dfs.usage_metrics import red_zone_counts


def _stat(pid, name, pos, team, week, opp, targets=0, carries=0, rec_yd=0, team_air=0):
    return {
        "player_id": pid,
        "player_display_name": name,
        "position": pos,
        "season_type": "REG",
        "week": week,
        "team": team,
        "opponent_team": opp,
        "targets": targets,
        "carries": carries,
        "receptions": targets // 2,
        "receiving_yards": rec_yd,
        "receiving_air_yards": 0,
        "passing_air_yards": team_air,
        "passing_yards": 0,
        "fantasy_points_ppr": 0.0,
    }


def _stats_frame(weeks=(1, 2, 3, 4, 5, 6, 7)):
    rows = []
    for w in weeks:
        rows += [
            _stat("w1", "Wide One", "WR", "AAA", w, "BBB", targets=6, rec_yd=60),
            _stat("w2", "Wide Two", "WR", "AAA", w, "BBB", targets=4),
            _stat("q1", "Quarter Back", "QB", "AAA", w, "BBB", carries=2, team_air=100),
        ]
    return pd.DataFrame(rows)


def _pbp(plays):
    base = {
        "season_type": "REG",
        "play_type": "pass",
        "pass": 1,
        "rush": 0,
        "two_point_attempt": 0,
        "play_deleted": 0,
        "receiver_player_id": None,
        "rusher_player_id": None,
    }
    return pd.DataFrame([{**base, **p} for p in plays])


def test_goal_line_option_adds_a_column_and_the_default_frame_is_unchanged():
    pbp = _pbp(
        [
            {"week": 1, "yardline_100": 4, "receiver_player_id": "w1"},
            {"week": 1, "yardline_100": 5, "receiver_player_id": "w1"},
            {"week": 1, "yardline_100": 6, "receiver_player_id": "w1"},
        ]
    )
    assert list(red_zone_counts(pbp).columns) == ["GsisId", "week", "rz", "hvt"]
    counts = red_zone_counts(pbp, goal_line=5).set_index("GsisId")
    assert (counts.loc["w1", "rz"], counts.loc["w1", "gl"]) == (3, 2)  # the 5 is inclusive


def test_usage_log_keeps_the_latest_five_games_played_most_recent_first():
    log = player_log.usage_log(_stats_frame(), None, None)
    mine = log[log["Name"] == "Wide One"].sort_values("Back")
    assert list(mine["Back"]) == [1, 2, 3, 4, 5]
    assert list(mine["Wk"]) == [7, 6, 5, 4, 3]
    assert (mine["Tgt"] == 6).all() and mine["Tgt%"].iloc[0] == pytest.approx(0.6)  # 6 of the team's 10
    assert mine["Opp"].iloc[0] == "BBB"
    assert mine["RZ"].isna().all() and mine["Snap%"].isna().all()  # no pbp / snaps: blank, not 0


def test_a_missed_week_is_skipped_never_a_zero():
    stats = _stats_frame()
    stats = stats[~((stats["player_id"] == "w1") & (stats["week"] == 5))]
    log = player_log.usage_log(stats, None, None)
    weeks = list(log[log["Name"] == "Wide One"].sort_values("Back")["Wk"])
    assert weeks == [7, 6, 4, 3, 2]


def test_a_quarterback_has_no_target_share_but_keeps_his_carries():
    log = player_log.usage_log(_stats_frame(), None, None)
    qb = log[log["Name"] == "Quarter Back"]
    assert qb["Tgt"].isna().all() and qb["Tgt%"].isna().all()
    assert (qb["Car"] == 2).all()


def test_red_zone_inside_five_and_snaps_are_joined_per_game():
    pbp = _pbp(
        [
            {"week": 7, "yardline_100": 3, "receiver_player_id": "w1"},
            {"week": 7, "yardline_100": 15, "receiver_player_id": "w1"},
            {"week": 6, "yardline_100": 40, "receiver_player_id": "w1"},
        ]
    )
    snaps = pd.DataFrame(
        {
            "player": ["Wide One", "Wide One"],
            "week": [7, 6],
            "offense_pct": [0.91, 0.88],
        }
    )
    log = player_log.usage_log(_stats_frame(), pbp, player_log.snap_log_frame(snaps))
    mine = log[log["Name"] == "Wide One"].set_index("Wk")
    assert (mine.loc[7, "RZ"], mine.loc[7, "In5"]) == (2, 1)
    assert (mine.loc[6, "RZ"], mine.loc[6, "In5"]) == (0, 0)  # played, no looks: a real 0
    assert mine.loc[7, "Snap%"] == pytest.approx(0.91)
    assert pd.isna(mine.loc[5, "Snap%"])


def test_usage_log_of_nothing_is_an_empty_frame_with_the_columns():
    log = player_log.usage_log(pd.DataFrame(), None, None)
    assert log.empty and list(log.columns) == player_log.LOG_COLUMNS
