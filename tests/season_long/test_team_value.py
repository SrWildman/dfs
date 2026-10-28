import numpy as np
import pandas as pd
import pytest

from dfs.season_long import team_value as tv

POS = {
    "qb1": ("QB",),
    "qb2": ("QB",),
    "rb1": ("RB",),
    "rb2": ("RB",),
    "rb3": ("RB",),
    "wr1": ("WR",),
    "wr2": ("WR",),
    "wr3": ("WR",),
    "te1": ("TE",),
    "te2": ("TE",),
    "k1": ("K",),
}


def test_flex_takes_best_remaining_eligible_player():
    pts = {"qb1": 20, "rb1": 15, "rb2": 12, "rb3": 11, "wr1": 14, "wr2": 9, "te1": 8, "te2": 10.5}
    total, chosen = tv.best_lineup(pts, POS, ["QB", "RB", "RB", "WR", "TE", "FLEX"])
    assert set(chosen.values()) == {"qb1", "rb1", "rb2", "wr1", "te2", "rb3"}
    assert total == pytest.approx(20 + 15 + 12 + 14 + 10.5 + 11)


def test_superflex_starts_a_second_qb_when_he_outscores_the_flex_options():
    pts = {"qb1": 22, "qb2": 18, "rb1": 15, "wr1": 14, "wr2": 9, "te1": 8}
    _, chosen = tv.best_lineup(pts, POS, ["QB", "RB", "WR", "TE", "SUPER_FLEX"])
    assert chosen[4] == "qb2"
    _, chosen = tv.best_lineup(pts, POS, ["QB", "RB", "WR", "TE", "FLEX"])
    assert chosen[4] == "wr2"


def test_assignment_beats_greedy_with_overlapping_flexes():
    """WRRB_FLEX and REC_FLEX overlap on WR: putting the best WR in the first slot (WR/RB) leaves the TE for
    the second, 29; the best lineup moves him to WR/TE and starts the RB, 30."""
    pos = {"wr1": ("WR",), "rb1": ("RB",), "te1": ("TE",)}
    pts = {"wr1": 20, "rb1": 10, "te1": 9}
    total, chosen = tv.best_lineup(pts, pos, ["WRRB_FLEX", "REC_FLEX"])
    assert total == 30 and chosen == {0: "rb1", 1: "wr1"}


def test_unfillable_slot_scores_zero_and_dual_position_player_fits_both():
    pos = {**POS, "hunter": ("WR", "CB")}
    total, chosen = tv.best_lineup({"hunter": 12, "qb1": 20}, pos, ["QB", "WR", "TE"])
    assert total == 32 and 2 not in chosen


def _weekly(rows):
    return pd.DataFrame(rows, columns=["player_id", "week", "pts", "sd"])


def test_bye_week_is_covered_by_the_bench():
    # wr1 is on bye in week 9 (0 points from ros), so wr2 starts.
    weekly = _weekly([("wr1", 8, 15, 5), ("wr1", 9, 0, 0), ("wr2", 8, 8, 4), ("wr2", 9, 8, 4)])
    weeks = tv.week_lineups(["wr1", "wr2"], weekly, POS, ["WR"], miss_rate=0.0)
    assert [w.total for w in weeks] == [15, 8]
    assert weeks[1].starters == {0: "wr2"}


def test_bench_cover_term_is_small_and_rewards_depth():
    weekly = _weekly([("wr1", 8, 15, 5), ("wr2", 8, 10, 4)])
    thin = tv.week_lineups(["wr1"], weekly, POS, ["WR"], miss_rate=0.1)[0]
    deep = tv.week_lineups(["wr1", "wr2"], weekly, POS, ["WR"], miss_rate=0.1)[0]
    assert thin.value == pytest.approx(15 - 0.1 * 15)
    assert deep.value == pytest.approx(15 - 0.1 * 5)


def test_playoff_weeks_are_weighted_and_reported_apart():
    weekly = _weekly([("wr1", 14, 10, 3), ("wr1", 15, 10, 3)])
    v = tv.team_value(1, ["wr1"], weekly, POS, ["WR"], 0.0, playoff_weeks=[15], playoff_weight=2.0)
    assert v.value == 30 and v.playoff_value == 20 and v.lineup_pts == 20


def test_needs_rank_the_shortfall_against_the_league_median():
    strengths = {1: {"QB": 20, "RB": 20}, 2: {"QB": 18, "RB": 30}, 3: {"QB": 22, "RB": 25}}
    need = tv.needs(strengths)
    assert need[1][0] == ("RB", -5.0, 3)


def test_survival_ranks_the_weak_team_most_likely_to_be_chopped():
    def lineup(mean):
        return [tv.WeekLineup(w, mean, mean, {}, 10.0) for w in (8, 9, 10)]

    s = tv.survival({1: lineup(100), 2: lineup(120), 3: lineup(140), 4: lineup(141)}, decay=0.8, draws=4000)
    assert s.chop_now[1] > 0.5 > s.chop_now[3]
    assert s.expected_weeks[1] < s.expected_weeks[2] < s.expected_weeks[3]
    # Three chops among four teams leave exactly one alive at the end of every draw.
    assert sum(a[-1] for a in s.alive_after.values()) == pytest.approx(1.0)
    assert np.all(np.diff(s.alive_after[2]) <= 0)
