"""The tail check: coverage of the engine's upper quantiles, the stretch hook, and the held-out judgement of a
per-group adjustment -- on synthetic rows drawn from the shipped distribution with a planted lighter top."""

import numpy as np
import pandas as pd

from dfs import tail_check as tc
from dfs.model import distribution as dist
from dfs.sim import marginal
from dfs.sim.simulate import PlayerSpec, simulate_lineups

ROLES = {"QB": ["QB1"], "RB": ["RB1", "RB2"], "WR": ["WR1", "WR2"], "TE": ["TE1"], "DST": ["DST"]}
RANGE = {"QB": (8, 28), "RB": (3, 22), "WR": (3, 22), "TE": (2, 16), "DST": (2, 14)}


def make_frame(planted: dict[str, float] | None = None, n: int = 1500, seed: int = 0) -> pd.DataFrame:
    """Rows 2014-2025 for every position; each role's `dk` is a draw from the shipped table at its projection
    with its upper half stretched by `planted[role]` (default none)."""
    rng = np.random.default_rng(seed)
    planted = planted or {}
    parts = []
    for position, roles in ROLES.items():
        for role in roles:
            pred = rng.uniform(*RANGE[position], n)
            m = marginal.marginals(position, pred, delta=np.full(n, planted.get(role, 0.0)))
            u = rng.uniform(size=n)
            dk = np.array([marginal.ppf(m, i, np.array([u[i]]))[0] for i in range(n)])
            parts.append(
                pd.DataFrame(
                    {
                        "season": 2014 + rng.integers(0, 12, n),
                        "week": 1,
                        "game_id": [f"{role}{i}" for i in range(n)],
                        "position": position,
                        "role": role,
                        "pred": pred,
                        "dk": dk,
                    }
                )
            )
    return pd.concat(parts, ignore_index=True)


def test_stretch_upper_moves_only_the_top_half_about_the_median():
    r = dist.ratio_matrix("WR", [6.0, 12.0, 18.0])
    assert np.allclose(marginal.stretch_upper(r, 0.0), r)
    lo = marginal.stretch_upper(r, -0.3)
    levels = np.asarray(dist.LEVELS)
    assert np.allclose(lo[:, levels <= 0.5], r[:, levels <= 0.5])
    assert (lo[:, levels > 0.5] < r[:, levels > 0.5]).all()
    assert (np.diff(lo, axis=1) >= 0).all()
    per_row = marginal.stretch_upper(r, np.array([0.0, -0.3, 0.3]))
    assert np.allclose(per_row[0], r[0]) and np.allclose(per_row[1], lo[1]) and (per_row[2] >= r[2]).all()


def test_marginals_without_delta_are_the_shipped_ones():
    a = marginal.marginals("RB", [8.0, 12.0])
    b = marginal.marginals("RB", [8.0, 12.0], delta=[0.0, 0.0])
    assert np.allclose(a.knots, b.knots)
    c = marginal.marginals("RB", [8.0, 12.0], delta=[0.0, -0.2])
    assert np.allclose(a.knots[0], c.knots[0]) and (c.knots[1, -5:-1] < a.knots[1, -5:-1]).all()


def test_the_simulator_hook_is_off_by_default_and_lowers_a_roles_top_when_given_a_negative_delta():
    def spec(i, role, position, proj):
        return PlayerSpec(f"p{i}", position, "AAA", "BBB", "G1", role, proj)

    lineup = [spec(1, "QB1", "QB", 18.0), spec(2, "RB2", "RB", 8.0), spec(3, "WR1", "WR", 12.0)]
    base = simulate_lineups([lineup], n_sims=20000, seed=1, keep_scores=True)
    same = simulate_lineups([lineup], n_sims=20000, seed=1, keep_scores=True, tail_delta=None)
    assert np.array_equal(base.scores, same.scores)
    cut = simulate_lineups([lineup], n_sims=20000, seed=1, keep_scores=True, tail_delta={"RB2": -0.4})
    assert cut.lineups[0].quantiles[0.99] < base.lineups[0].quantiles[0.99]
    assert (
        abs(cut.lineups[0].quantiles[0.10] - base.lineups[0].quantiles[0.10]) < 0.5
    )  # the bottom is left alone


def test_coverage_of_the_tables_own_draws_is_nominal():
    f = make_frame()
    held = tc.with_pit(f, None)
    cov = tc.coverage(held, ("position",))
    assert (cov["z90"].abs() < 3).all() and (cov["z99"].abs() < 3.5).all()
    assert abs(cov[">95"].mean() - 5.0) < 0.5


def test_a_planted_light_top_shows_in_the_coverage_and_the_fit_recovers_it():
    f = make_frame({"RB2": -0.4})
    tables = tc.train_tables(f)
    scored = tc.with_pit(f, tables)
    cov = tc.coverage(scored[scored["season"] > 2022], ("role",)).set_index("role")
    assert (
        cov.loc["RB2", ">90"] < cov.loc["RB1", ">90"] - 1.5
    )  # RB2 booms less often than the pooled RB table says
    fitted = tc.fit_adjustment(f, tables, group="role")
    assert fitted["RB2"][0] < -0.15  # the stretch is recovered in direction and rough size
    assert abs(fitted["QB1"][0]) < 0.15 and abs(fitted["DST"][0]) < 0.15


def test_the_judgement_clears_two_se_for_the_planted_role_and_not_for_a_clean_one():
    planted = make_frame({"RB2": -0.4}, n=3000)
    tables = tc.train_tables(planted)
    fitted = tc.fit_adjustment(planted, tables, group="role")
    out = tc.judge(planted, tables, fitted, range(2023, 2026), group="role").set_index("group")
    assert out.loc["RB2", "pinball_better"] > 2 * out.loc["RB2", "pinball_se"]
    assert out.loc["RB2", "tail_ll_better"] > 0
    clean = make_frame(n=3000, seed=4)
    tables = tc.train_tables(clean)
    fitted = tc.fit_adjustment(clean, tables, group="position")
    out = tc.judge(clean, tables, fitted, range(2023, 2026)).set_index("group")
    assert not out["clears_2se"].any()


def test_the_conditional_tail_density_integrates_to_one_beyond_the_threshold():
    ratios = dist.ratio_matrix("WR", [12.0])
    thr = ratios[:, [int(np.argmin(np.abs(np.asarray(dist.LEVELS) - 0.90)))]][0]
    grid = np.linspace(thr[0] + 1e-6, ratios[0, -1] * 1.6, 4000)
    ll = tc.tail_loglik(np.repeat(ratios, len(grid), axis=0), grid, np.full(len(grid), thr[0]))
    ok = ~np.isnan(ll)
    assert abs(np.exp(ll[ok]).sum() * (grid[1] - grid[0]) - 1.0) < 0.02


def test_adjust_with_a_scale_multiplies_every_quantile():
    r = dist.ratio_matrix("RB", [10.0])
    assert np.allclose(tc.adjust(r, 0.0, 0.9), 0.9 * r)
