"""portfolio_retro.py: the arithmetic of the realized replay, the shocked worlds and the `keep_draws` hook."""

import collections

import numpy as np
import pandas as pd
import pytest

from dfs.sim import contest_sim as csim
from dfs.sim import payouts
from dfs.sim import portfolio_retro as pr
from dfs.sim.simulate import PlayerSpec


def real_field(points, copies=None, entries=1000, paid=200, pool=8000.0, fee=10.0):
    kind = payouts.ContestKind("c", "N-max GPP", entries, paid, pool, fee, 1.5, 5)
    return pr.RealField(
        np.sort(np.asarray(points, float))[::-1],
        collections.Counter(copies or {}),
        csim._cumulative(payouts.payout_table(kind)),
        entries,
        max(1.0, 0.01 * entries),
    )


def test_an_entry_is_placed_among_the_others_scores_and_paid_that_place():
    rf = real_field([200.0, 190.0, 180.0, 100.0])
    pay, top, places = pr.realized_set(rf, [("a",)], [185.0])
    assert places == [3]  # two others scored more
    want = csim.block_payout(rf.cum, np.array([3.0]), np.array([0.0]))[0]
    assert pay == pytest.approx(want) and top == 1  # the top 1% of 1000 is 10 places


def test_his_other_entries_count_in_a_place_and_clones_split_the_prize():
    rf = real_field([200.0, 100.0])
    _, _, places = pr.realized_set(rf, [("a",), ("b",)], [150.0, 160.0])
    assert places == [3, 2]  # b beats a, and both lose to the 200
    pay, _, _ = pr.realized_set(rf, [("a",), ("a",)], [150.0, 150.0])
    shared = csim.block_payout(rf.cum, np.array([2.0]), np.array([1.0]))[0]
    assert pay == pytest.approx(2 * shared)


def test_identical_lineups_held_by_other_users_split_the_prize_too():
    rf = real_field([200.0], copies={("a",): 3})
    pay, _, _ = pr.realized_set(rf, [("a",)], [150.0])
    assert pay == pytest.approx(csim.block_payout(rf.cum, np.array([2.0]), np.array([3.0]))[0])


def test_no_entries_is_zero():
    assert pr.realized_set(real_field([1.0]), [], []) == (0.0, 0, [])


def specs():
    out = []
    for team in ("AAA", "BBB"):
        for pos in ("QB", "WR", "WR", "DST"):
            out.append(PlayerSpec(f"{team}{pos}{len(out)}", pos, team, "XXX", "g", pos, 15.0))
    out.append(None)
    return out


def test_shocked_specs_scale_every_projection_and_a_team_shares_its_offense_shock():
    rng = np.random.default_rng(0)
    base = specs()
    shocked = pr.shocked_specs(base, rng, 0.0, 0.3)  # no player shock: only the team factor remains
    assert shocked[-1] is None
    ratios = {(s.team, s.position): s.projection / 15.0 for s in shocked if s is not None}
    assert ratios[("AAA", "QB")] == pytest.approx(ratios[("AAA", "WR")])  # one factor per offense
    assert ratios[("AAA", "QB")] != pytest.approx(ratios[("BBB", "QB")])
    assert ratios[("AAA", "DST")] == pytest.approx(1.0)  # a defense takes no team shock


def test_shocked_specs_are_deterministic_and_keep_the_mean_near_one():
    a = pr.shocked_specs(specs(), np.random.default_rng(5), 0.2, 0.1)
    b = pr.shocked_specs(specs(), np.random.default_rng(5), 0.2, 0.1)
    assert [s and s.projection for s in a] == [s and s.projection for s in b]
    factors = []
    for seed in range(400):
        for s in pr.shocked_specs(specs(), np.random.default_rng(seed), 0.2, 0.1):
            if s is not None and s.position == "WR":
                factors.append(s.projection / 15.0)
    assert np.mean(factors) == pytest.approx(1.0, abs=0.03)


def test_shocked_points_come_in_equal_blocks_per_world():
    pts = pr.shocked_points(specs(), n_sims=80, worlds=4, seed=1)
    assert pts.shape == (80, len(specs()))
    assert np.allclose(pts[:, -1], 0.0)  # the None spec scores 0


def test_paired_reports_the_mean_standard_error_and_weeks_won():
    per_week = pd.DataFrame(
        {
            "week": [2, 2, 4, 4, 5, 5],
            "arm": ["today", "x"] * 3,
            "profit": [0.0, 10.0, 0.0, 20.0, 0.0, -10.0],
        }
    )
    out = pr.paired(per_week, "profit", "x")
    assert out["mean"] == pytest.approx(20.0 / 3) and out["won"] == 2 and out["weeks"] == 3
    assert out["se"] == pytest.approx(np.std([10, 20, -10], ddof=1) / np.sqrt(3))
