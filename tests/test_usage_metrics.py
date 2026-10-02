"""Usage metrics (`usage_metrics.py`): window, shares, red zone, position masks, fail-soft."""

import math

import pandas as pd
import pytest

from dfs.usage_metrics import (
    USAGE_METRIC_COLUMNS,
    USAGE_SOURCE_COLUMNS,
    player_usage,
    red_zone_counts,
    through_week,
)


def _row(pid, name, pos, team, week, targets=0, carries=0, rec_air=0, pass_air=0):
    return {
        "player_id": pid,
        "player_display_name": name,
        "position": pos,
        "season_type": "REG",
        "week": week,
        "team": team,
        "targets": targets,
        "carries": carries,
        "receiving_air_yards": rec_air,
        "passing_air_yards": pass_air,
    }


def _stats(rows):
    return pd.DataFrame(rows)


def _two_receiver_team(weeks):
    """Team AAA: WR1 (id w1) and WR2 (w2) split 10 targets a week 6/4, a QB (q1) throws for
    100 air yards, a RB (r1) carries 10 of the team's 20 carries (a QB carries the rest)."""
    rows = []
    for w in weeks:
        rows += [
            _row("w1", "Wide One", "WR", "AAA", w, targets=6, rec_air=60),
            _row("w2", "Wide Two", "WR", "AAA", w, targets=4, rec_air=30),
            _row("q1", "Quarter Back", "QB", "AAA", w, carries=10, pass_air=100),
            _row("r1", "Run Back", "RB", "AAA", w, carries=10),
        ]
    return rows


def test_tgt_share_is_a_ratio_of_window_sums_over_the_players_own_team():
    usage = player_usage(_stats(_two_receiver_team([1, 2, 3])), None).set_index("GsisId")
    assert usage.loc["w1", "Tgt%"] == pytest.approx(0.6)  # 18 of 30
    assert usage.loc["w2", "Tgt%"] == pytest.approx(0.4)
    assert usage.loc["w1", "Games"] == 3


def test_window_is_the_last_three_games_and_ignores_earlier_ones():
    # Week 1 is wildly different (w1 sees 50 targets). If it leaked into the window the share would move.
    rows = _two_receiver_team([2, 3, 4]) + [
        _row("w1", "Wide One", "WR", "AAA", 1, targets=50),
        _row("w2", "Wide Two", "WR", "AAA", 1, targets=0),
    ]
    usage = player_usage(_stats(rows), None).set_index("GsisId")
    assert usage.loc["w1", "Games"] == 3
    assert usage.loc["w1", "Tgt%"] == pytest.approx(0.6)  # weeks 2-4 only


def test_a_missed_game_is_skipped_not_counted_as_a_zero():
    rows = [r for r in _two_receiver_team([1, 2, 3, 4]) if not (r["player_id"] == "w2" and r["week"] == 3)]
    usage = player_usage(_stats(rows), None).set_index("GsisId")
    assert usage.loc["w2", "Games"] == 3  # weeks 1, 2 and 4: an earlier game fills the window
    # Week 3 is not in HIS window, so his team's week-3 targets are not in his denominator either:
    # 12 of the team's 30 over those three team-weeks.
    assert usage.loc["w2", "Tgt%"] == pytest.approx(0.4)


def test_fewer_than_three_games_uses_what_there_is():
    usage = player_usage(_stats(_two_receiver_team([2])), None).set_index("GsisId")
    assert usage.loc["w1", "Games"] == 1
    assert usage.loc["w1", "Tgt%"] == pytest.approx(0.6)


def test_wopr_divides_air_yards_by_the_teams_passing_air_yards_like_nflverse():
    usage = player_usage(_stats(_two_receiver_team([1, 2, 3])), None).set_index("GsisId")
    # tgt share 0.6, air share 180 / 300 = 0.6 -> 1.5*0.6 + 0.7*0.6 = 1.32
    assert usage.loc["w1", "WOPR"] == pytest.approx(1.32)
    # Denominator is NOT the receivers' own air yards (90/wk): that would give 180/270 = 0.667.
    assert usage.loc["w1", "WOPR"] != pytest.approx(1.5 * 0.6 + 0.7 * (180 / 270), abs=0.001)


def test_rush_share_counts_qb_scrambles_in_the_team_total():
    usage = player_usage(_stats(_two_receiver_team([1, 2, 3])), None).set_index("GsisId")
    assert usage.loc["r1", "Rush%"] == pytest.approx(0.5)  # 30 of 60 (the QB's 30 are in the total)
    assert usage.loc["q1", "Rush%"] == pytest.approx(0.5)


def test_metrics_are_blank_outside_their_positions_never_zero():
    usage = player_usage(_stats(_two_receiver_team([1, 2, 3])), None).set_index("GsisId")
    assert math.isnan(usage.loc["r1", "WOPR"])  # WR/TE only
    assert math.isnan(usage.loc["w1", "Rush%"])  # RB/QB only
    assert math.isnan(usage.loc["q1", "Tgt%"])  # QBs are not target-share players
    assert not math.isnan(usage.loc["r1", "Tgt%"])  # RBs are


def test_a_fullback_is_an_rb():
    rows = _two_receiver_team([1]) + [_row("f1", "Full Back", "FB", "AAA", 1, carries=2)]
    usage = player_usage(_stats(rows), None).set_index("GsisId")
    assert usage.loc["f1", "Position"] == "RB"


def _run(week, yard, pid):
    return {
        "week": week,
        "yardline_100": yard,
        "play_type": "run",
        "pass": 0,
        "rush": 1,
        "rusher_player_id": pid,
    }


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


def test_red_zone_counts_targets_and_carries_inside_the_20_and_the_10():
    pbp = _pbp(
        [
            {"week": 1, "yardline_100": 18, "receiver_player_id": "w1"},  # RZ
            {"week": 1, "yardline_100": 8, "receiver_player_id": "w1"},  # RZ + HVT
            {"week": 1, "yardline_100": 21, "receiver_player_id": "w1"},  # neither
            {
                "week": 1,
                "yardline_100": 20,
                "play_type": "run",
                "pass": 0,
                "rush": 1,
                "rusher_player_id": "r1",
            },
            {
                "week": 1,
                "yardline_100": 10,
                "play_type": "run",
                "pass": 0,
                "rush": 1,
                "rusher_player_id": "r1",
            },
            {
                "week": 1,
                "yardline_100": 11,
                "play_type": "run",
                "pass": 0,
                "rush": 1,
                "rusher_player_id": "r1",
            },
        ]
    )
    counts = red_zone_counts(pbp).set_index("GsisId")
    assert (counts.loc["w1", "rz"], counts.loc["w1", "hvt"]) == (2, 1)
    assert (counts.loc["r1", "rz"], counts.loc["r1", "hvt"]) == (3, 1)  # the 20 and the 10 are inclusive


def test_red_zone_ignores_two_point_attempts_deleted_plays_and_non_scrimmage_plays():
    pbp = _pbp(
        [
            {"week": 1, "yardline_100": 2, "receiver_player_id": "w1", "two_point_attempt": 1},
            {"week": 1, "yardline_100": 2, "receiver_player_id": "w1", "play_deleted": 1},
            {"week": 1, "yardline_100": 2, "receiver_player_id": "w1", "play_type": "no_play"},
            {"week": 1, "yardline_100": 2, "receiver_player_id": "w1"},
        ]
    )
    assert red_zone_counts(pbp).set_index("GsisId").loc["w1", "rz"] == 1


def test_rz_and_hvt_are_per_game_over_the_window_and_a_game_with_none_counts_as_zero():
    pbp = _pbp(
        [
            {"week": 1, "yardline_100": 5, "receiver_player_id": "r1"},
            {
                "week": 1,
                "yardline_100": 5,
                "play_type": "run",
                "pass": 0,
                "rush": 1,
                "rusher_player_id": "r1",
            },
            {"week": 3, "yardline_100": 15, "receiver_player_id": "r1"},
        ]
    )
    usage = player_usage(_stats(_two_receiver_team([1, 2, 3])), pbp).set_index("GsisId")
    assert usage.loc["r1", "RZ/G"] == pytest.approx(1.0)  # 3 over 3 games (week 2 had none)
    assert usage.loc["r1", "HVT/G"] == pytest.approx(2 / 3, abs=0.01)  # 2 inside the 10, RB only
    assert math.isnan(usage.loc["w1", "HVT/G"])  # HVT is an RB metric
    assert usage.loc["w1", "RZ/G"] == pytest.approx(0.0)  # played, no red-zone looks: a real 0


def test_pbp_unavailable_blanks_only_the_red_zone_columns():
    usage = player_usage(_stats(_two_receiver_team([1, 2, 3])), None).set_index("GsisId")
    assert math.isnan(usage.loc["r1", "RZ/G"]) and math.isnan(usage.loc["r1", "HVT/G"])
    assert not math.isnan(usage.loc["w1", "Tgt%"])  # the stats-file metrics are unaffected


def test_week_one_with_nothing_played_is_an_empty_frame_with_the_right_columns():
    empty = player_usage(pd.DataFrame(columns=_stats(_two_receiver_team([1])).columns), None)
    assert empty.empty
    assert list(empty.columns) == USAGE_SOURCE_COLUMNS
    assert list(player_usage(None, None).columns) == USAGE_SOURCE_COLUMNS  # type: ignore[arg-type]


def test_through_week_ignores_a_lone_thursday_game():
    rows = []
    for week in (1, 2, 3):
        for i in range(20):  # 20 teams played each of weeks 1-3
            rows.append(_row(f"p{week}{i}", "P", "WR", f"T{i:02d}", week))
    rows.append(_row("thu", "Thursday Guy", "WR", "T00", 4))  # week 4: one team so far
    assert through_week(_stats(rows)) == 3


def test_usage_columns_match_the_edge_contract():
    assert USAGE_METRIC_COLUMNS == ["Tgt%", "WOPR", "Rush%", "RZ/G", "HVT/G"]
    assert set(USAGE_METRIC_COLUMNS) <= set(USAGE_SOURCE_COLUMNS)
