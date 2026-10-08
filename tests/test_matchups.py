"""Matchups: adjusted points allowed by hand, the prior-season blend, z-scores, groups and reasons."""

import numpy as np
import pandas as pd
import pytest

from dfs import matchups


def _schedule():
    # week 1: A@B and C@D; week 2: A@C and B@D (away_team@home_team)
    return pd.DataFrame(
        [
            {"week": 1, "away_team": "A", "home_team": "B"},
            {"week": 1, "away_team": "C", "home_team": "D"},
            {"week": 2, "away_team": "A", "home_team": "C"},
            {"week": 2, "away_team": "B", "home_team": "D"},
        ]
    )


def _qb_points():
    rows = [
        ("A", 1, 20),
        ("A", 2, 10),
        ("B", 1, 15),
        ("B", 2, 12),
        ("C", 1, 18),
        ("C", 2, 22),
        ("D", 1, 14),
        ("D", 2, 16),
    ]
    return pd.DataFrame(
        [{"Team": t, "week": w, "Position": "QB", "dk_actual": pts, "Name": f"{t}qb"} for t, w, pts in rows]
    )


def test_position_points_include_zero_for_a_position_with_no_stat_line():
    tp = matchups.team_position_points(_qb_points(), _schedule())
    assert len(tp) == 8 * 4  # every team-week, every position
    row = tp[(tp["team"] == "A") & (tp["week"] == 1)].set_index("Position")
    assert row.loc["QB", "pts"] == 20 and row.loc["RB", "pts"] == 0 and row.loc["QB", "opp"] == "B"


def test_adjusted_points_allowed_is_points_above_what_the_offense_scores_elsewhere():
    tp = matchups.team_position_points(_qb_points(), _schedule())
    allowed = matchups.adjusted_points_allowed(tp, before_week=3)
    qb = allowed[allowed["Position"] == "QB"].set_index("Team")["adj"]
    # by hand: B faced A (20 against A's other game 10 = +10) and D (16 against D's 14 = +2): mean 6.0
    assert qb["B"] == pytest.approx(6.0)
    assert qb["A"] == pytest.approx(3.5)  # faced B (15 vs 12 = +3) and C (22 vs 18 = +4)
    assert qb["C"] == pytest.approx(-6.0)  # faced A (10 vs 20 = -10) and D (14 vs 16 = -2): mean -6.0
    assert qb["D"] == pytest.approx(-3.5)  # faced C (18 vs 22 = -4) and B (12 vs 15 = -3)


def test_only_games_before_the_slate_week_count():
    tp = matchups.team_position_points(_qb_points(), _schedule())
    week2 = matchups.adjusted_points_allowed(
        tp, before_week=2
    )  # week 1 only: one game each, league mean fallback
    assert set(week2["games"]) == {1}
    changed = tp.copy()
    changed.loc[changed["week"] == 2, "pts"] = 999.0  # week 2's own numbers cannot reach a week-2 signal
    pd.testing.assert_frame_equal(week2, matchups.adjusted_points_allowed(changed, before_week=2))


def test_an_offense_with_one_game_falls_back_to_the_league_average():
    tp = matchups.team_position_points(_qb_points(), _schedule())
    tp = tp[tp["week"] == 1]
    allowed = matchups.adjusted_points_allowed(tp, before_week=2)
    qb = allowed[allowed["Position"] == "QB"].set_index("Team")["adj"]
    league = np.mean([20, 15, 18, 14])
    assert qb["B"] == pytest.approx(20 - league)  # A's only game, against the league mean


def test_dst_allowed_is_the_mirror_by_offense():
    dst = pd.DataFrame(
        [
            {"Team": t, "week": w, "dk_actual": pts}
            for t, w, pts in [
                ("A", 1, 8),
                ("A", 2, 2),
                ("B", 1, 4),
                ("B", 2, 6),
                ("C", 1, 5),
                ("C", 2, 3),
                ("D", 1, 9),
                ("D", 2, 1),
            ]
        ]
    )
    out = matchups.adjusted_dst_allowed(dst, _schedule(), before_week=3).set_index("Team")["adj"]
    assert out["A"] == pytest.approx(-2.0)
    assert out["B"] == pytest.approx(-1.0)
    assert out["C"] == pytest.approx(1.0)
    assert out["D"] == pytest.approx(2.0)


def test_blend_with_last_season_uses_the_early_season_weight():
    current = pd.DataFrame({"Team": ["A"], "adj": [6.0], "games": [2]})
    prior = pd.DataFrame({"Team": ["A"], "adj": [-3.0], "games": [17]})
    blended = matchups.blend_allowed(current, prior, ["Team"])
    # 2 games vs PBP_PRIOR_WEIGHT_GAMES=4 -> 1/3 current, 2/3 prior
    assert blended.loc[0, "adj"] == pytest.approx(6.0 / 3 + (-3.0) * 2 / 3, abs=0.01)
    assert matchups.blend_allowed(current, None, ["Team"]).equals(current)


def _scores(allowed_by_team, implied=None, metrics=None):
    pairs = pd.DataFrame({"Team": ["A", "B", "C", "D"], "Opp": ["B", "A", "D", "C"]})
    allowed = pd.DataFrame(
        [
            {"Team": t, "Position": p, "adj": v, "games": 2}
            for t, v in allowed_by_team.items()
            for p in matchups.OFFENSE_POSITIONS
        ]
    )
    dst = pd.DataFrame({"Team": list("ABCD"), "adj": [-2.0, -1.0, 1.0, 2.0], "games": 2})
    return matchups.matchup_scores(
        pairs, allowed, dst, metrics, implied if implied is not None else pd.Series(dtype=float)
    )


def test_score_is_the_z_of_the_opponents_adjusted_allowed_when_it_is_the_only_input():
    scores = _scores({"A": 4.0, "B": 6.0, "C": -6.0, "D": -3.5})
    qb = scores[scores["Position"] == "QB"].set_index("Team")
    # A faces B (allows +6.0), B faces A (+4.0), C faces D (-3.5), D faces C (-6.0)
    assert qb["Rank"].to_dict() == {"A": 1.0, "B": 2.0, "C": 3.0, "D": 4.0}
    assert qb["Score"].sum() == pytest.approx(0.0)  # z-scores centre on zero
    assert np.isnan(qb["z_epa"]).all()  # a missing input is blank, never zero


def test_missing_inputs_renormalise_instead_of_diluting_the_score():
    metrics = pd.DataFrame(
        {
            "Team": list("ABCD"),
            "Pace": [30, 31, 32, 33],
            "PROE": [1.0, 2.0, -1.0, 0.0],
            "DefEPA/Pass": [0.1, 0.0, -0.1, 0.2],
            "DefEPA/Rush": [0, 0, 0, 0],
        }
    )
    implied = pd.Series({"A": 24.0, "B": 20.0, "C": 22.0, "D": 18.0})
    full = _scores({"A": 4.0, "B": 6.0, "C": -6.0, "D": -3.5}, implied, metrics)
    qb = full[full["Position"] == "QB"].set_index("Team")
    parts = qb[["z_allowed", "z_epa", "z_implied", "z_pace", "z_proe"]]
    assert qb["Score"].to_dict() == pytest.approx(parts.mean(axis=1).to_dict())
    # RB's PROE sign is flipped: pass-heavy teams are a worse RB environment
    rb = full[full["Position"] == "RB"].set_index("Team")
    assert rb.loc["B", "z_proe"] == pytest.approx(-qb.loc["B", "z_proe"])


def test_faster_pace_is_better_and_dst_uses_the_opponents_inverted_implied_total():
    metrics = pd.DataFrame({"Team": list("ABCD"), "Pace": [28.0, 30.0, 32.0, 34.0]})
    implied = pd.Series({"A": 24.0, "B": 20.0, "C": 22.0, "D": 18.0})
    scores = _scores({"A": 0, "B": 0, "C": 0, "D": 0}, implied, metrics)
    qb = scores[scores["Position"] == "QB"].set_index("Team")
    assert qb.loc["A", "z_pace"] > 0 > qb.loc["D", "z_pace"]
    dst = scores[scores["Position"] == "DST"].set_index("Team")
    # defense B faces offense A (implied 24), defense D faces offense C (implied 22): the lower-scoring
    # opponent is the better DST spot, so the inverted z is higher for D
    assert dst.loc["D", "z_implied"] > dst.loc["B", "z_implied"]
    assert (
        dst.loc["B", "z_implied"] < 0 < dst.loc["C", "z_implied"]
    )  # C faces D, the lowest implied total (18)


def test_groups_never_list_a_team_twice_and_reasons_name_the_pull():
    scores = _scores({"A": 4.0, "B": 6.0, "C": -6.0, "D": -3.5})
    groups = matchups.select_groups(scores)
    qb = groups[groups["Position"] == "QB"]
    assert len(qb) == 4 and qb["Team"].nunique() == 4  # 4 teams: all "top", none repeated as "bottom"
    assert (qb["Group"] == matchups.GROUP_TOP).all()
    assert "allow" in qb.iloc[0]["Reasons"] and "to QB above average" in qb.iloc[0]["Reasons"]


def test_top_eight_and_bottom_four_split_a_full_slate():
    teams = [f"T{i:02d}" for i in range(16)]
    pairs = pd.DataFrame({"Team": teams, "Opp": teams[::-1]})
    allowed = pd.DataFrame(
        [{"Team": t, "Position": "QB", "adj": float(i), "games": 3} for i, t in enumerate(teams)]
    )
    scores = matchups.matchup_scores(
        pairs, allowed, pd.DataFrame(columns=["Team", "adj", "games"]), None, pd.Series(dtype=float)
    )
    groups = matchups.select_groups(scores)
    qb = groups[groups["Position"] == "QB"]
    assert (qb["Group"] == "top").sum() == matchups.MATCHUP_TOP_N
    assert (qb["Group"] == "bottom").sum() == matchups.MATCHUP_BOTTOM_N
    assert qb.iloc[0]["Rank"] == 1 and qb.iloc[-1]["Rank"] == 16


def test_attach_players_names_the_teams_top_two_by_calpts_with_salary():
    groups = pd.DataFrame(
        [{"Position": "WR", "Team": "A", "Opp": "B", "Group": "top", "Reasons": "", "Players": ""}]
    )
    players = pd.DataFrame(
        [
            {"Name": "W1", "Team": "A", "Position": "WR", "Salary": 8000, "CalPts": 15.0, "ProjPts": 9.0},
            {"Name": "W2", "Team": "A", "Position": "WR", "Salary": 5500, "CalPts": 12.0, "ProjPts": 13.0},
            {"Name": "W3", "Team": "A", "Position": "WR", "Salary": 3000, "CalPts": 4.0, "ProjPts": 14.0},
            {"Name": "X", "Team": "B", "Position": "WR", "Salary": 9000, "CalPts": 20.0, "ProjPts": 20.0},
        ]
    )
    out = matchups.attach_players(groups, players)
    assert out.loc[0, "Players"] == "W1 $8,000, W2 $5,500"  # by CalPts, not ProjPts
    fallback = matchups.attach_players(groups, players.assign(CalPts=np.nan))
    assert fallback.loc[0, "Players"].startswith("W3")  # no CalPts at all -> ProjPts


def test_week_pairs_lists_only_teams_with_a_game_that_week():
    pairs = matchups.week_pairs(_schedule(), 1, {"A", "B", "C"})
    assert set(zip(pairs["Team"], pairs["Opp"], strict=True)) == {("A", "B"), ("B", "A"), ("C", "D")}
    assert matchups.week_pairs(_schedule(), 9, {"A"}).empty
