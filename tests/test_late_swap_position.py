"""Late swap by position: LEVERAGE / HOLD labels from ownership, and a lineup's place on the top-1% line."""

from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pytest

from dfs import late_swap_position as lp
from dfs.late_swap_report import format_suggestions
from dfs.late_swap_search import LineupSuggestions, Swap, SwapPlayer


def player(name, salary=6000):
    return SwapPlayer(name, "WR", "ATL", "SF", "SF@ATL", salary, 10.0)


def swap(pairs, delta=1.0):
    """`pairs`: slot index -> (name out or None, name in)."""
    return Swap(
        changes={i: (player(o) if o else None, player(n)) for i, (o, n) in pairs.items()},
        delta=delta,
        salary_left=100,
    )


OWN = {"Chalk": 0.30, "Mid": 0.12, "Sleeper": 0.03, "Other": 0.125}


def test_a_swap_to_a_less_owned_player_is_leverage_and_to_a_more_owned_one_is_hold():
    lev = lp.swap_overlap(swap({3: ("Chalk", "Sleeper")}), OWN)
    assert (
        lev.label == lp.LEVERAGE and lev.own_out == pytest.approx(0.30) and lev.own_in == pytest.approx(0.03)
    )
    assert lev.delta == pytest.approx(-0.27)
    hold = lp.swap_overlap(swap({3: ("Sleeper", "Chalk")}), OWN)
    assert hold.label == lp.HOLD and hold.delta == pytest.approx(0.27)


def test_a_swap_that_barely_moves_the_summed_ownership_is_even():
    assert lp.swap_overlap(swap({3: ("Mid", "Other")}), OWN).label == lp.EVEN  # half a point


def test_a_two_for_two_sums_both_sides():
    o = lp.swap_overlap(swap({3: ("Chalk", "Mid"), 5: ("Mid", "Sleeper")}), OWN)
    assert o.own_out == pytest.approx(0.42) and o.own_in == pytest.approx(0.15) and o.label == lp.LEVERAGE


def test_an_empty_slot_has_nobody_going_out():
    o = lp.swap_overlap(swap({3: (None, "Chalk")}), OWN)
    assert o.label == lp.HOLD and o.own_out == 0.0


def test_no_label_where_ownership_is_missing_for_anyone_in_the_swap():
    assert lp.swap_overlap(swap({3: ("Chalk", "Nobody")}), OWN).label == ""
    assert lp.swap_overlap(swap({3: ("Chalk", "Sleeper")}), {}).label == ""
    assert lp.overlap_text(lp.SwapOverlap("")) == "" and lp.overlap_text(None) == ""
    assert (
        lp.overlap_text(lp.swap_overlap(swap({3: ("Chalk", "Sleeper")}), OWN)) == "LEVERAGE (own 30% -> 3%)"
    )


def test_buckets_are_the_chance_of_finishing_above_the_line():
    assert lp.bucket_of(0.002) == lp.BEHIND and lp.bucket_of(0.01) == lp.NEAR
    assert lp.bucket_of(0.09) == lp.NEAR and lp.bucket_of(0.10) == lp.AHEAD and lp.bucket_of(0.9) == lp.AHEAD


def test_the_approximate_finish_runs_from_the_top_one_percent_to_the_double_up_line():
    assert lp.approx_finish(5.0) == 0.01 and lp.approx_finish(0.0) == 0.01
    top10 = lp.approx_finish(-(63.2 - 31.9))
    assert top10 == pytest.approx(0.10)
    assert lp.approx_finish(-(63.2 - 0.6)) is None  # at the double-up line, out of the GPP money picture
    assert 0.01 < lp.approx_finish(-15.0) < 0.10 < lp.approx_finish(-45.0) < lp.DOUBLE_UP_FRACTION


def test_the_ladder_finish_is_the_median_first_place_the_score_reaches():
    fractions = (0.01, 0.10, 0.50)
    ladder = np.array([[200.0] * 4, [150.0] * 4, [100.0] * 4])
    assert lp.ladder_finish(np.array([210.0, 210.0, 210.0, 210.0]), fractions, ladder) == 0.01
    assert lp.ladder_finish(np.array([160.0, 160.0, 160.0, 160.0]), fractions, ladder) == 0.10
    assert lp.ladder_finish(np.array([120.0, 120.0, 120.0, 120.0]), fractions, ladder) == 0.50
    assert lp.ladder_finish(np.array([50.0, 50.0, 50.0, 50.0]), fractions, ladder) is None


class FakeLive:
    """`LiveLines` with two players: the lineup scores `base` plus its draws; the line is fixed."""

    def __init__(self, scores, top, ladder=None, fractions=()):
        self._scores, self.top_line = np.asarray(scores, float), np.asarray(top, float)
        self.ladder, self.ladder_fractions = ladder, fractions

    def scores(self, lineup):
        return self._scores


def test_a_lineup_behind_the_line_reports_the_gap_the_chance_and_its_bucket():
    rng = np.random.default_rng(0)
    scores = 150 + rng.normal(0, 10, 4000)
    live = FakeLive(scores, np.full(4000, 168.0))
    pos = lp.lineup_position(live, [])
    assert pos.gap == pytest.approx(-18.0, abs=1.0) and not pos.exact
    assert 0.0 < pos.p_top1 < 0.1 and pos.bucket == lp.NEAR
    assert pos.finish == pytest.approx(lp.approx_finish(pos.gap))
    assert lp.position_text(pos).startswith(
        "about top "
    ) and "18 pts behind the top-1% line" in lp.position_text(pos)


def test_a_real_field_gives_the_exact_finish():
    scores = np.full(100, 160.0)
    ladder = np.vstack([np.full(100, 200.0), np.full(100, 150.0)])
    pos = lp.lineup_position(FakeLive(scores, np.full(100, 200.0), ladder, (0.01, 0.12)), [])
    assert pos.exact and pos.finish == 0.12
    assert lp.position_text(pos).startswith("top 12%, 40 pts behind the top-1% line")


def test_a_lineup_on_the_line_is_said_to_be_on_it():
    pos = lp.Position(gap=0.2, spread=10.0, p_top1=0.5, finish=0.01, exact=True)
    assert lp.position_text(pos) == "top 1%, on the top-1% line (Top 1% 50.0%)"


def test_the_report_heads_a_labelled_swap_and_prints_the_position():
    s = swap({3: ("Chalk", "Sleeper")}, delta=-1.0)
    s.overlap = lp.swap_overlap(s, OWN)
    s.delta_p_gpp = 0.012
    state = SimpleNamespace(
        open=[SimpleNamespace(slot="WR", index=3, current=player("Chalk"))], fixed_salary=1000, cap=50000
    )
    suggestions = LineupSuggestions(state=state, refill=None, pairs=[], singles={3: [s]})
    pos = lp.Position(gap=-18.0, spread=10.0, p_top1=0.03, finish=0.12, exact=True)
    text = "\n".join(format_suggestions(suggestions, metric="CalPts", goal="gpp", position=pos))
    assert "Position: top 12%, 18 pts behind the top-1% line (Top 1% 3.0%)." in text
    assert "LEVERAGE (own 30% -> 3%): -1.0 pts" in text
    plain = "\n".join(format_suggestions(suggestions, metric="CalPts", goal="gpp"))
    assert "Position:" not in plain


def test_amplified_lines_keep_their_means_and_scale_their_swings():
    from dfs.late_swap_field import LiveLines

    top = np.array([100.0, 110.0, 120.0, 130.0])
    live = LiveLines(SimpleNamespace(), top - 50, top, "projected")
    out = live.amplified(1.5)
    assert out.top_line.mean() == pytest.approx(top.mean()) and out.top_line.std() == pytest.approx(
        1.5 * top.std()
    )
    assert out.cash_line.mean() == pytest.approx((top - 50).mean()) and out.source.endswith("(swings x1.5)")
