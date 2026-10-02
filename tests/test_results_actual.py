"""Actual DK scoring: real yardage bonuses, return TDs, defences, and the nflverse identity check."""

import math

import pandas as pd
import pytest

from dfs.dk_scoring import (
    ACTUAL_DST_FIELDS,
    ACTUAL_OFFENSE_FIELDS,
    dst_points_allowed_score,
    score_dst_actual_row,
    score_offense_actual_row,
)
from dfs.results_actual import (
    dst_actual_fields,
    identity_breaks,
    offense_identity_residual,
    score_dst_actual,
    score_offense_actual,
)


def _offense(**stats):
    base = dict.fromkeys(ACTUAL_OFFENSE_FIELDS, 0.0)
    return score_offense_actual_row({**base, **stats})


def test_passing_yardage_bonus_is_a_hard_threshold_at_300():
    # 299 / 300 / 301 passing yards: only the last two cross the line.
    assert _offense(pass_yd=299) == pytest.approx(299 * 0.04)
    assert _offense(pass_yd=300) == pytest.approx(300 * 0.04 + 3)
    assert _offense(pass_yd=301) == pytest.approx(301 * 0.04 + 3)


def test_rushing_and_receiving_bonuses_trigger_at_100_each_and_stack():
    assert _offense(rush_yd=99) == pytest.approx(9.9)
    assert _offense(rush_yd=100) == pytest.approx(13.0)
    assert _offense(rec_yd=100, rec=5) == pytest.approx(10 + 5 + 3)
    assert _offense(rush_yd=100, rec_yd=100) == pytest.approx(10 + 10 + 3 + 3)


def test_dk_offense_scoring_table():
    # DraftKings Classic: pass TD 4, INT -1, rush TD 6, rec TD 6, reception 1, lost fumble -1, 2pt 2.
    assert _offense(pass_td=2, pass_int=1) == pytest.approx(7)
    assert _offense(rush_td=1, rec_td=1, rec=3) == pytest.approx(15)
    assert _offense(fum_lost=2, two_pt=1) == pytest.approx(0)


def test_return_td_and_offensive_fumble_recovery_td_are_six_each():
    assert _offense(ret_td=1) == 6.0
    assert _offense(fumrec_td=1) == 6.0


def test_a_missing_field_makes_the_whole_score_nan_not_zero():
    stats = dict.fromkeys(ACTUAL_OFFENSE_FIELDS, 0.0)
    stats["rec_yd"] = float("nan")
    assert math.isnan(score_offense_actual_row(stats))


@pytest.mark.parametrize(
    ("allowed", "points"),
    [
        (0, 10),
        (1, 7),
        (6, 7),
        (7, 4),
        (13, 4),
        (14, 1),
        (20, 1),
        (21, 0),
        (27, 0),
        (28, -1),
        (34, -1),
        (35, -4),
        (60, -4),
    ],
)
def test_points_allowed_tier_boundaries(allowed, points):
    assert dst_points_allowed_score(allowed) == points


def test_dst_actual_row_adds_counting_stats_to_the_tier():
    stats = dict.fromkeys(ACTUAL_DST_FIELDS, 0.0)
    stats.update(
        sack=3, def_int=1, fum_rec=2, def_td=1, safety=1, blocked_kick=1, two_pt_return=1, points_allowed=13
    )
    # 3 + 2 + 4 + 6 + 2 + 2 + 2 = 21, plus the 7-13 tier (+4)
    assert score_dst_actual_row(stats) == 25.0


def _stats_player(rows):
    base = {
        "player_id": "p",
        "player_display_name": "P",
        "position": "WR",
        "season_type": "REG",
        "week": 1,
        "team": "AAA",
    }
    cols = [
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
    return pd.DataFrame([{**base, **dict.fromkeys(cols, 0), **r} for r in rows])


def test_identity_dk_minus_ppr_equals_bonuses_plus_interceptions_plus_lost_fumbles():
    # QB: 310 yds, 2 TD, 1 INT, 1 lost fumble.
    # nflverse PPR: 12.4 + 8 - 2 - 2 = 16.4.  DK: 12.4 + 8 - 1 - 1 + 3 = 21.4.
    stats = _stats_player(
        [
            {
                "player_id": "q",
                "position": "QB",
                "passing_yards": 310,
                "passing_tds": 2,
                "passing_interceptions": 1,
                "rushing_fumbles_lost": 1,
                "fantasy_points_ppr": 16.4,
            }
        ]
    )
    scored = score_offense_actual(stats)
    assert scored.loc[0, "dk_actual"] == pytest.approx(21.4)
    assert offense_identity_residual(scored).tolist() == [0.0]
    assert identity_breaks(scored).empty


def test_identity_flags_a_row_nflverse_scores_differently():
    # A kick-return TD nflverse's PPR total omitted would show as a residual of +6.
    stats = _stats_player([{"special_teams_tds": 1, "fantasy_points_ppr": 0}])
    scored = score_offense_actual(stats)
    assert offense_identity_residual(scored).tolist() == [6.0]
    broken = identity_breaks(scored)
    assert len(broken) == 1 and broken.iloc[0]["ret_td"] == 1


def test_fb_and_hb_are_scored_as_rb_and_non_offense_is_dropped():
    stats = _stats_player(
        [{"player_id": "f", "position": "FB", "rushing_yards": 10}, {"player_id": "k", "position": "K"}]
    )
    scored = score_offense_actual(stats)
    assert scored["Position"].tolist() == ["RB"]


def _team(team, opp, **kw):
    base = dict.fromkeys(
        [
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
        ],
        0,
    )
    return {"season_type": "REG", "week": 1, "team": team, "opponent_team": opp, **base, **kw}


def _scores(home, away, home_score, away_score):
    return pd.DataFrame(
        [
            {
                "week": 1,
                "home_team": home,
                "away_team": away,
                "home_score": home_score,
                "away_score": away_score,
            }
        ]
    )


def test_dst_points_allowed_is_the_opponents_final_score_less_their_defensive_and_special_teams_tds():
    teams = pd.DataFrame([_team("AAA", "BBB"), _team("BBB", "AAA", def_tds=1)])  # BBB scored a pick-six
    players = _stats_player([{"player_id": "x", "team": "AAA"}])
    scores = _scores("AAA", "BBB", 10, 27)
    fields = dst_actual_fields(teams, players, scores).set_index("Team")
    # BBB's 27 includes a pick-six: AAA's defense is charged 21, not 27 (tier 0 not -1).
    assert fields.loc["AAA", "points_allowed"] == 21
    assert fields.loc["BBB", "points_allowed"] == 10


def test_dst_fumble_recovery_td_counts_for_the_defense_but_not_when_an_offensive_player_recovered_it():
    teams = pd.DataFrame([_team("AAA", "BBB", fumble_recovery_tds=2), _team("BBB", "AAA")])
    players = _stats_player(
        [
            {"player_id": "lb", "position": "LB", "team": "AAA", "fumble_recovery_tds": 1},
            {
                "player_id": "wr",
                "position": "WR",
                "team": "AAA",
                "fumble_recovery_tds": 1,
            },  # offensive recovery
        ]
    )
    fields = dst_actual_fields(teams, players, _scores("AAA", "BBB", 20, 10)).set_index("Team")
    assert fields.loc["AAA", "def_td"] == 1  # only the linebacker's


def test_dst_blocked_kicks_sum_punts_field_goals_and_extra_points():
    teams = pd.DataFrame(
        [_team("AAA", "BBB", def_punt_blocks=1, def_fg_blocks=1, def_pat_blocks=1), _team("BBB", "AAA")]
    )
    players = _stats_player([{"player_id": "x"}])
    fields = dst_actual_fields(teams, players, _scores("AAA", "BBB", 20, 10)).set_index("Team")
    assert fields.loc["AAA", "blocked_kick"] == 3


def test_dst_with_no_final_score_scores_nan_not_a_number():
    teams = pd.DataFrame([_team("AAA", "BBB"), _team("BBB", "AAA")])
    players = _stats_player([{"player_id": "x"}])
    scores = _scores("AAA", "BBB", float("nan"), float("nan"))
    scored = score_dst_actual(teams, players, scores)
    assert scored["dk_actual"].isna().all()
