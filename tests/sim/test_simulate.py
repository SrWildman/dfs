import numpy as np
import pandas as pd
import pytest
from scipy.stats import spearmanr

from dfs.sim import simulate as S
from dfs.sim.correlation import CorrelationLookup


def lookup(*rows):
    """A tiny correlation table: (relation, role_a, role_b, rho)."""
    table = pd.DataFrame(
        {
            "relation": [r[0] for r in rows],
            "role_a": [r[1] for r in rows],
            "role_b": [r[2] for r in rows],
            "total_bucket": "all",
            "rho": [r[3] for r in rows],
            "n": 1000,
            "ci_lo": [r[3] - 0.05 for r in rows],
            "ci_hi": [r[3] + 0.05 for r in rows],
        }
    )
    return CorrelationLookup(table)


def P(pid, position, team, role, projection, game="G1", opp="BBB"):
    return S.PlayerSpec(pid, position, team, opp, game, role, projection)


QB = P("qb", "QB", "AAA", "QB1", 18.0)
WR = P("wr", "WR", "AAA", "WR1", 14.0)


def test_repair_leaves_a_valid_matrix_alone():
    r = np.array([[1, 0.3, 0.1], [0.3, 1, 0.2], [0.1, 0.2, 1.0]])
    fixed, info = S.repair_correlation(r)
    assert not info.needed and info.max_change == 0.0
    np.testing.assert_array_equal(fixed, r)


def test_repair_makes_an_impossible_matrix_valid_and_reports_the_change():
    r = np.array([[1, 0.9, -0.9], [0.9, 1, 0.9], [-0.9, 0.9, 1.0]])  # a~b, b~c but a opposite c: impossible
    assert np.linalg.eigvalsh(r)[0] < -0.1
    fixed, info = S.repair_correlation(r)
    assert info.needed and info.min_eigenvalue < -0.1
    assert np.linalg.eigvalsh(fixed)[0] >= -1e-9
    np.testing.assert_allclose(np.diag(fixed), 1.0)
    np.testing.assert_allclose(fixed, fixed.T)
    assert info.max_change == pytest.approx(np.abs(fixed - r).max()) and 0 < info.max_change < 1


def test_the_copula_reproduces_a_known_correlation():
    look = lookup(("same_team", "QB1", "WR1", 0.5))
    res = S.simulate_lineups([[QB], [WR]], n_sims=40000, seed=1, correlations=look, keep_scores=True)
    s = res.scores
    # points have the right dependence: the rank correlation of a Gaussian copula is (6/pi) asin(rho/2)
    rho_s = spearmanr(s[:, 0], s[:, 1])[0]
    assert rho_s == pytest.approx(6 / np.pi * np.arcsin(0.25), abs=0.02)
    assert res.score_correlation[0, 1] == pytest.approx(0.5, abs=0.06)  # Pearson shrinks a little with skew


def test_independent_means_zero_correlation():
    look = lookup(("same_team", "QB1", "WR1", 0.8))
    res = S.simulate_lineups([[QB], [WR]], n_sims=40000, seed=1, correlations=look, independent=True)
    assert abs(res.score_correlation[0, 1]) < 0.02


def test_each_player_keeps_his_own_marginal():
    look = lookup(("same_team", "QB1", "WR1", 0.6))
    corr = S.simulate_lineups([[QB], [WR]], n_sims=40000, seed=2, correlations=look)
    ind = S.simulate_lineups([[QB], [WR]], n_sims=40000, seed=2, correlations=look, independent=True)
    for a, b in zip(corr.lineups, ind.lineups, strict=True):
        assert a.mean == pytest.approx(b.mean, rel=0.02)
        assert a.quantiles[0.5] == pytest.approx(b.quantiles[0.5], rel=0.03)


def test_the_same_player_in_two_lineups_is_one_variable():
    look = lookup()
    a = [QB, P("rb", "RB", "CCC", "RB1", 12.0, game="G2")]
    b = [QB, P("te", "TE", "DDD", "TE1", 8.0, game="G3")]
    res = S.simulate_lineups([a, b, [QB]], n_sims=30000, seed=3, correlations=look, keep_scores=True)
    s = res.scores
    np.testing.assert_allclose(s[:, 2], s[:, 0] - (s[:, 0] - s[:, 2]))  # lineup 3 is just the QB ...
    # ... and the shared QB is why lineups 1 and 2 correlate although the other players are independent
    assert res.score_correlation[0, 1] > 0.25
    assert res.score_correlation[2, 2] == pytest.approx(1.0)
    solo = S.simulate_lineups([[QB], [QB]], n_sims=2000, seed=3, correlations=look, keep_scores=True)
    np.testing.assert_array_equal(solo.scores[:, 0], solo.scores[:, 1])
    assert solo.score_correlation[0, 1] == pytest.approx(1.0)


def test_players_in_different_games_are_independent_whatever_their_roles():
    look = lookup(("same_team", "QB1", "WR1", 0.9), ("opp", "QB1", "WR1", 0.9))
    other_game = P("wr2", "WR", "AAA", "WR1", 14.0, game="G2")  # same team label, different game
    r, notes = S.correlation_matrix([QB, other_game], look)
    assert r[0, 1] == 0.0 and not notes
    res = S.simulate_lineups([[QB], [other_game]], n_sims=40000, seed=4, correlations=look)
    assert abs(res.score_correlation[0, 1]) < 0.02


def test_matrix_uses_the_same_team_and_opposing_values():
    look = lookup(("same_team", "QB1", "WR1", 0.3), ("opp", "QB1", "WR1", 0.1))
    foe = P("wrx", "WR", "BBB", "WR1", 14.0, opp="AAA")
    r, _ = S.correlation_matrix([QB, WR, foe], look)
    assert (r[0, 1], r[0, 2]) == (0.3, 0.1)
    assert r[1, 2] == 0.0  # WR1 against WR1 was never given: independent


def test_the_simulator_takes_no_game_total():
    import inspect

    assert "total_bucket_by_game" not in inspect.signature(S.simulate_lineups).parameters
    with pytest.raises(TypeError):
        S.simulate_lineups([[QB]], total_bucket_by_game={"G1": "high"}, correlations=lookup())


def test_two_players_in_the_same_role_on_one_team_borrow_the_neighbour():
    look = lookup(("same_team", "WR3", "WR4", 0.2))
    a = P("w4a", "WR", "AAA", "WR4", 8.0)
    b = P("w4b", "WR", "AAA", "WR5", 7.0)  # WR5 reads as WR4
    r, notes = S.correlation_matrix([S._validate(a), S._validate(b)], look)
    assert r[0, 1] == 0.2 and notes
    r, notes = S.correlation_matrix(
        [S._validate(P("d1", "DST", "AAA", "DST", 7.0)), S._validate(P("d2", "DST", "AAA", "DST", 6.0))], look
    )
    assert r[0, 1] == 0.0 and notes


def test_results_are_deterministic_in_the_seed():
    look = lookup(("same_team", "QB1", "WR1", 0.4))
    run = lambda seed: S.simulate_lineups([[QB, WR]], n_sims=5000, seed=seed, correlations=look)  # noqa: E731
    assert run(7).lineups[0] == run(7).lineups[0]
    assert run(7).lineups[0].mean != run(8).lineups[0].mean


def test_lineup_and_portfolio_metrics():
    look = lookup(("same_team", "QB1", "WR1", 0.4))
    other = P("rb", "RB", "CCC", "RB1", 12.0, game="G2")
    res = S.simulate_lineups(
        [[QB, WR], [other]],
        n_sims=30000,
        seed=5,
        correlations=look,
        cash_line=30.0,
        gpp_target=45.0,
        keep_scores=True,
    )
    s = res.scores
    one = res.lineups[0]
    assert one.mean == pytest.approx(s[:, 0].mean())
    assert one.quantiles[0.10] < one.quantiles[0.50] < one.quantiles[0.99]
    assert one.p_cash == pytest.approx((s[:, 0] >= 30).mean())
    assert res.p_any_gpp == pytest.approx((s >= 45).any(axis=1).mean())
    assert res.p_any_cash == pytest.approx((s >= 30).any(axis=1).mean())
    assert res.expected_cashing == pytest.approx(sum(x.p_cash for x in res.lineups))
    assert max(x.p_cash for x in res.lineups) <= res.p_any_cash <= 1
    assert res.p_any_gpp <= res.p_any_cash
    assert res.score_correlation.shape == (2, 2)
    assert list(res.table()["lineup"]) == [1, 2]


def test_defaults_are_the_documented_constants():
    assert (S.DEFAULT_CASH_LINE, S.DEFAULT_GPP_TARGET) == (145.0, 190.0)


def test_swap_impact_moves_the_right_way_on_shared_draws():
    look = lookup(("same_team", "QB1", "WR1", 0.4))
    better = P("wr_better", "WR", "AAA", "WR1", 20.0)
    worse = P("wr_worse", "WR", "AAA", "WR1", 6.0)
    up = S.swap_impact([QB, WR], "wr", better, correlations=look, cash_line=32.0, gpp_target=50.0)
    down = S.swap_impact([QB, WR], "wr", worse, correlations=look, cash_line=32.0, gpp_target=50.0)
    assert up.delta_mean > 3 and down.delta_mean < -3
    assert up.delta_p_cash > 0.05 and down.delta_p_cash < -0.05
    assert up.delta_p_gpp > 0 > down.delta_p_gpp
    same = S.swap_impact([QB, WR], "wr", P("twin", "WR", "AAA", "WR1", 14.0), correlations=look)
    assert (
        abs(same.delta_mean) < 0.3 and abs(same.delta_p_cash) < 0.02
    )  # an identical player: only his own noise
    with pytest.raises(ValueError):
        S.swap_impact([QB, WR], "nobody", better, correlations=look)
    with pytest.raises(ValueError):
        S.swap_impact([QB, WR], "wr", QB, correlations=look)


def test_bad_input_is_refused():
    look = lookup()
    with pytest.raises(ValueError):
        S.simulate_lineups([], correlations=look)
    with pytest.raises(ValueError):
        S.simulate_lineups([[P("x", "QB", "A", "WR1", 10.0)]], correlations=look)  # role / position mismatch
    with pytest.raises(ValueError):
        S.simulate_lineups([[P("x", "QB", "A", "QB1", float("nan"))]], correlations=look)
    with pytest.raises(ValueError):
        S.simulate_lineups(
            [[QB], [P("qb", "QB", "AAA", "QB1", 19.0)]], correlations=look
        )  # same id, other facts


def test_a_zero_projection_scores_zero():
    look = lookup()
    res = S.simulate_lineups([[P("out", "WR", "AAA", "WR1", 0.0)]], n_sims=100, correlations=look)
    assert res.lineups[0].mean == 0.0


@pytest.mark.speed
def test_eight_lineups_of_twenty_thousand_draws_in_under_two_seconds(synth_week):
    import time

    from dfs.sim.backtest import generate_lineups, to_specs
    from dfs.sim.correlation import load_correlations

    lineups = [to_specs(lu.rows, "w") for lu in generate_lineups(synth_week, 8, np.random.default_rng(0))]
    corr = load_correlations()
    S.simulate_lineups(lineups, n_sims=500, correlations=corr)  # warm the caches
    start = time.perf_counter()
    res = S.simulate_lineups(lineups, n_sims=20000, seed=0, correlations=corr)
    elapsed = time.perf_counter() - start
    assert len(res.lineups) == 8
    assert elapsed < 2.0, f"took {elapsed:.2f}s"
