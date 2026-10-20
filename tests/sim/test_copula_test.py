"""The copula comparison: each log-likelihood against a reference computation, the limits that must agree
(a t-copula with huge nu, a game factor with no spread), and the simulator behind the tail-event rates."""

import numpy as np
import pandas as pd
from scipy import stats

from dfs.sim import copula_test as ct


def _game(z, r=None, teams=None, roles=None):
    d = len(z)
    teams = teams or ["AAA"] * (d // 2) + ["BBB"] * (d - d // 2)
    roles = roles or ["QB1", "WR1", "WR2", "TE1", "QB1", "WR1", "WR2", "TE1"][:d]
    r = np.eye(d) + 0.3 * (1 - np.eye(d)) if r is None else r
    return ct.Game("G", 2024, np.asarray(z, float), teams, roles, r, r)


def test_the_gaussian_copula_density_matches_the_joint_over_the_margins():
    z = np.array([0.4, -1.1, 2.0, 0.2])
    game = _game(z)
    ref = (
        stats.multivariate_normal(mean=np.zeros(4), cov=game.r_pearson).logpdf(z) - stats.norm.logpdf(z).sum()
    )
    assert np.isclose(ct.ll_gaussian(game, game.r_pearson), ref)


def test_a_t_copula_with_huge_nu_is_the_gaussian_one():
    game = _game([0.4, -1.1, 2.0, 0.2, 1.0, -0.3])
    assert np.isclose(ct.ll_t(game, game.r_pearson, 1e6), ct.ll_gaussian(game, game.r_pearson), atol=2e-3)


def test_a_t_copula_gives_a_joint_boom_more_density_than_the_gaussian():
    boom = _game([2.3, 2.3, 2.3, 2.3])
    assert ct.ll_t(boom, boom.r_pearson, 5) > ct.ll_gaussian(boom, boom.r_pearson)


def test_the_game_factor_with_no_spread_is_the_gaussian_one():
    game = _game([0.4, -1.1, 2.0, 0.2, 1.0, -0.3])
    assert np.isclose(ct.ll_factor(game, game.r_pearson, 0.0), ct.ll_gaussian(game, game.r_pearson))


def test_the_game_factor_is_a_proper_density_of_the_unit_margins():
    # one pair, so the copula density can be integrated on a grid: it must integrate to 1 over the square
    r = np.array([[1.0, 0.4], [0.4, 1.0]])
    grid = np.linspace(-6, 6, 241)
    dz = grid[1] - grid[0]
    total = 0.0
    for a in grid:
        for b in grid:
            g = ct.Game("G", 2024, np.array([a, b]), ["AAA", "AAA"], ["QB1", "WR1"], r, r)
            total += np.exp(ct.ll_factor(g, r, 0.1)) * stats.norm.pdf(a) * stats.norm.pdf(b) * dz * dz
    assert abs(total - 1.0) < 0.01


def test_a_singular_game_matrix_caps_the_factor_at_the_shipped_correlations():
    # all-ones correlation: R(m) is only a correlation matrix for m <= 1, so a base above 1 changes nothing
    r = np.ones((3, 3))
    game = ct.Game("G", 2024, np.array([0.3, 0.3, 0.3]), ["A"] * 3, ["QB1", "WR1", "WR2"], r, r)
    assert np.isclose(ct.ll_factor(game, r, 0.0, 1.0), ct.ll_factor(game, r, 0.0, 5.0))


def test_simulated_scores_keep_unit_margins_and_the_games_correlation():
    game = _game([0, 0, 0, 0])
    rng = np.random.default_rng(0)
    for kind, param in (("gaussian", 0.0), ("t", 8.0), ("t_p", 8.0), ("factor", (0.3, 1.0))):
        sims = ct.simulate_z(game, kind, param, 40000, rng)
        assert abs(sims.mean()) < 0.03 and abs(sims.std() - 1) < 0.03
        assert abs(np.corrcoef(sims[:, 0], sims[:, 1])[0, 1] - 0.3) < 0.04
    heavy = ct.simulate_z(game, "t", 4.0, 60000, rng)
    plain = ct.simulate_z(game, "gaussian", 0.0, 60000, rng)
    both = lambda s: float(((s[:, 0] > 1.5) & (s[:, 1] > 1.5)).mean())  # noqa: E731
    assert both(heavy) > both(plain)


def test_events_are_each_teams_qb_and_wr1_and_the_other_wr1():
    game = _game([0.0] * 8)
    assert ct.event_columns(game, "QB1+WR1") == [[0, 1], [4, 5]]
    assert ct.event_columns(game, "QB1+WR1+opp WR1") == [[0, 1, 5], [4, 5, 1]]
    no_wr = _game([0.0] * 4, roles=["QB1", "WR2", "QB1", "WR2"], teams=["A", "A", "B", "B"])
    assert ct.event_columns(no_wr, "QB1+WR1") == []


def test_observed_event_rates_count_players_over_the_cut():
    game = _game([2.0, 1.5, 0, 0, 0, 0, 0, 0])
    got = ct.event_rates([game], None, 0.0, 0, 0, cut=1.28)
    assert got[got["event"] == "QB1+WR1"]["p"].tolist() == [1.0, 0.0]


def test_pair_tables_are_shrunk_toward_zero_and_the_kendall_version_agrees_in_sign():
    rng = np.random.default_rng(0)
    rows = []
    for g in range(1500):
        qb, e = rng.standard_normal(2)
        wr = 0.5 * qb + np.sqrt(0.75) * e
        for role, z in (("QB1", qb), ("WR1", wr)):
            rows.append({"game_id": g, "team": "A", "opp": "B", "role": role, "z": z})
    t = ct.pair_tables(pd.DataFrame(rows))
    key = ("same_team", "QB1", "WR1")
    assert 0.35 < t["pearson"][key] < 0.5
    assert 0.35 < t["kendall"][key] < 0.55
    assert ("opp", "QB1", "WR1") not in t["pearson"]
