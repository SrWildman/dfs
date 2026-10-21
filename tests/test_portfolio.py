"""portfolio.py: joint selection on shared draws, on small hand-built problems."""

import numpy as np
import pytest

from dfs import portfolio as pf
from dfs.sim import contest_sim as csim
from dfs.sim.payouts import ContestKind


def kind(label="c", entries=1000, paid=200, pool=8000.0, fee=10.0, fmt="N-max GPP"):
    return ContestKind(label, fmt, entries, paid, pool, fee, 1.5, 5)


def contest(label, place, copies=None, *, slots=1, eligible=None, group="gpp", **kw):
    place = np.asarray(place, float)
    copies = np.zeros_like(place) if copies is None else np.asarray(copies, float)
    ok = np.ones(place.shape[1], bool) if eligible is None else np.asarray(eligible, bool)
    return pf.Contest(label, kind(label, **kw), slots, place, copies, ok, group)


def problem(contests, scores, members=None):
    scores = np.asarray(scores, float)
    keys = [(f"L{j}",) for j in range(scores.shape[1])]
    return pf.Problem(contests, scores, keys, members)


def test_single_entry_is_the_plain_block_payout():
    c = contest("a", [[1.0, 50.0], [300.0, 5.0]], [[0.0, 2.0], [0.0, 0.0]])
    scores = np.array([[10.0, 8.0], [3.0, 9.0]])
    pay, hit = c.pay(scores, [("x",), ("y",)], [1])
    want = csim.block_payout(c.cum, c.place[:, 1], c.copies[:, 1])
    assert np.allclose(pay, want)
    assert hit.tolist() == [False, True]  # top cut is 1% of 1000 = 10 places


def test_a_second_entry_that_scores_more_pushes_the_first_down_a_place():
    c = contest("a", [[1.0, 1.0]])
    scores = np.array([[5.0, 9.0]])  # entry 1 beats entry 0
    pay, _ = c.pay(scores, [("x",), ("y",)], [0, 1])
    first = csim.block_payout(c.cum, np.array([1.0]), np.array([0.0]))[0]
    second = csim.block_payout(c.cum, np.array([2.0]), np.array([0.0]))[0]
    assert pay[0] == pytest.approx(first + second)  # the winner keeps 1st, the other takes 2nd
    # in either order, the same set
    pay2, _ = c.pay(scores, [("x",), ("y",)], [1, 0])
    assert pay2[0] == pytest.approx(pay[0])


def test_the_same_lineup_twice_splits_its_own_prize():
    c = contest("a", [[1.0]])
    scores = np.array([[5.0]])
    pay, _ = c.pay(scores, [("x",)], [0, 0])
    one = csim.block_payout(c.cum, np.array([1.0]), np.array([1.0]))[0]  # 1st and 2nd, shared
    assert pay[0] == pytest.approx(2 * one)
    single, _ = c.pay(scores, [("x",)], [0])
    assert pay[0] < 2 * single[0]  # a clone is worth less than a lineup of its own


def test_pay_with_each_agrees_with_pay():
    rng = np.random.default_rng(3)
    n_draws, n_cands = 60, 7
    scores = rng.normal(100, 20, (n_draws, n_cands))
    place = rng.uniform(1, 900, (n_draws, n_cands))
    copies = rng.uniform(0, 3, (n_draws, n_cands))
    keys = [(f"L{j % 6}",) for j in range(n_cands)]  # candidates 0 and 6 are the same lineup
    c = contest("a", place, copies, slots=4)
    for chosen in ([], [2], [0, 3], [1, 4, 6]):
        cand = np.array([j for j in range(n_cands)])
        pay, hit = c.pay_with_each(scores, keys, chosen, cand)
        for j in cand:
            want_pay, want_hit = c.pay(scores, keys, [*chosen, int(j)])
            assert np.allclose(pay[:, j], want_pay), (chosen, j)
            assert (hit[:, j] == want_hit).all(), (chosen, j)


def test_edollars_is_separable_across_single_entry_contests():
    rng = np.random.default_rng(1)
    place = rng.uniform(1, 600, (200, 5))
    a = contest("a", place)
    b = contest("b", place * 0.5, fee=20.0, pool=20000.0)
    scores = rng.normal(100, 15, (200, 5))
    p = problem([a, b], scores)
    sel = pf.select(p, "edollars")
    for c in (a, b):
        best = int(np.argmax([c.pay(scores, p.keys, [j])[0].mean() for j in range(5)]))
        assert sel.assignment[c.label] == [best]


def test_top1_spreads_entries_where_edollars_clones():
    # candidates 0 and 1 hit the top 1% on the same draws; 2 hits on other draws and is weaker alone
    d = 100
    place = np.full((d, 3), 500.0)
    place[:30, 0] = 1.0
    place[:30, 1] = 1.0
    place[40:60, 2] = 1.0
    scores = np.zeros((d, 3))
    scores[:30, :2] = 100.0
    scores[40:60, 2] = 100.0
    a, b = contest("a", place), contest("b", place)
    p = problem([a, b], scores)
    assert pf.select(p, "edollars").order[0][1] == 0
    assert sorted(j for _, j in pf.select(p, "edollars").order) == [0, 0]  # the same lineup in both
    spread = sorted(j for _, j in pf.select(p, "top1").order)
    assert spread == [0, 2] or spread == [1, 2]
    assert pf.evaluate(p, pf.select(p, "top1").assignment)["p_any_top1"] == pytest.approx(0.5)


def test_robust_prefers_the_lineup_that_pays_in_more_worlds():
    # same E[$]; candidate 0 pays in every draw, candidate 1 only in a quarter of them
    d = 400
    place = np.full((d, 2), 5000.0)
    place[:, 0] = 90.0
    place[:100, 1] = 1.0
    p = problem([contest("a", place, slots=1, paid=300)], np.zeros((d, 2)))
    e0 = pf.evaluate(p, {"a": [0]})["e_dollars"]
    e1 = pf.evaluate(p, {"a": [1]})["e_dollars"]
    assert e1 > e0  # chosen so the plain E[$] likes the jackpot
    tight = pf.Problem(p.contests, p.scores, p.keys, bankroll=1.2 * p.fees)
    assert pf.select(p, "edollars").assignment["a"] == [1]
    assert pf.select(tight, "robust").assignment["a"] == [0]


def test_eligibility_is_respected():
    place = np.full((20, 3), 100.0)
    place[:, 2] = 1.0  # candidate 2 is by far the best, and not allowed
    c = contest("a", place, eligible=[True, True, False], slots=2)
    sel = pf.select(problem([c], np.zeros((20, 3))), "edollars")
    assert 2 not in sel.assignment["a"]


def test_a_lineup_is_not_repeated_in_one_contest_unless_asked():
    place = np.full((20, 3), 400.0)
    place[:, 0] = 5.0
    c = contest("a", place, slots=2)
    p = problem([c], np.zeros((20, 3)))
    assert len(set(pf.select(p, "edollars").assignment["a"])) == 2
    assert pf.select(p, "edollars", allow_repeat=True).assignment["a"][0] == 0


def test_lineup_share_cap_spreads_across_contests_and_reports_the_empty_slot_never_relaxing():
    place = np.full((20, 2), 300.0)
    place[:, 0] = 5.0
    cs = [contest(f"c{i}", place) for i in range(4)]
    p = problem(cs, np.zeros((20, 2)))
    sel = pf.select(p, "edollars", max_lineup_share=0.5)
    flat = [j for js in sel.assignment.values() for j in js]
    assert flat.count(0) == 2 and flat.count(1) == 2
    tight = pf.select(p, "edollars", max_lineup_share=0.25)  # one each: a third lineup does not exist
    assert None in [j for js in tight.assignment.values() for j in js]
    assert tight.unfilled and "no lineup left" in tight.unfilled[0]


def test_player_cap_and_overlap_use_the_members():
    place = np.full((20, 3), 300.0)
    place[:, 0] = 5.0
    place[:, 1] = 6.0
    members = np.array([[1, 2, 3], [1, 2, 4], [7, 8, 9]])
    c = contest("a", place, slots=2)
    p = problem([c], np.zeros((20, 3)), members)
    assert sorted(pf.select(p, "edollars").assignment["a"]) == [0, 1]
    assert sorted(pf.select(p, "edollars", max_overlap=1).assignment["a"]) == [0, 2]  # 0 and 1 share two
    assert sorted(pf.select(p, "edollars", max_player_share=0.5).assignment["a"]) == [0, 2]


def test_caps_do_not_bind_a_cash_group_by_default():
    place = np.full((20, 3), 300.0)
    place[:, 0] = 5.0
    cash = [contest(f"d{i}", place, group="cash") for i in range(3)]
    p = problem(cash, np.zeros((20, 3)), np.array([[1, 2], [1, 3], [1, 4]]))
    sel = pf.select(p, "edollars", max_player_share=0.5, max_lineup_share=0.34)
    assert None not in [j for js in sel.assignment.values() for j in js]


def test_ranked_assignment_is_what_suggest_pick_does():
    place = np.full((10, 4), 300.0)
    cs = [contest("a", place, slots=2), contest("b", place, slots=1)]
    p = problem(cs, np.zeros((10, 4)))
    value = {"gpp": np.array([0.1, 0.4, 0.3, 0.2])}
    got = pf.ranked_assignment(p, value)
    assert got == {"a": [1, 2], "b": [1]}  # the best build in every contest, never twice in one


def test_evaluate_counts_entries_fees_and_concentration():
    place = np.full((10, 2), 300.0)
    cs = [contest("a", place, slots=2), contest("b", place, slots=1)]
    p = problem(cs, np.zeros((10, 2)), np.array([[1, 2], [1, 3]]))
    out = pf.evaluate(p, {"a": [0, 1], "b": [0]})
    assert out["entries"] == 3 and out["fees"] == 30.0
    assert out["max_lineup_share"] == pytest.approx(2 / 3)
    assert out["max_player_share"] == 1.0  # player 1 is in every entry


def test_bad_objective_is_refused():
    with pytest.raises(ValueError, match="objective"):
        pf.select(problem([contest("a", np.ones((3, 1)))], np.zeros((3, 1))), "luck")


def test_a_bankroll_that_does_not_cover_the_fees_is_refused():
    c = contest("a", np.full((5, 1), 100.0))
    p = pf.Problem([c], np.zeros((5, 1)), [("x",)], bankroll=5.0)  # fees are 10
    with pytest.raises(ValueError, match="does not cover"):
        pf.select(p, "robust")
