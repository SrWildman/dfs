import numpy as np
import pytest
from scipy.special import ndtri

from dfs.model import distribution as dist
from dfs.sim import marginal

POSITIONS = ["QB", "RB", "WR", "TE", "DST"]
PROJ = {"QB": 18.0, "RB": 11.0, "WR": 5.5, "TE": 6.0, "DST": 7.0}


@pytest.mark.parametrize("position", POSITIONS)
def test_ppf_inverts_cdf_above_the_atom(position):
    m = marginal.marginals(position, [PROJ[position]] * 5)
    pts = np.array([0.6, 0.9, 1.1, 1.5, 2.0]) * PROJ[position]
    rng = np.random.default_rng(0)
    u = marginal.cdf_u(m, pts, rng)
    back = np.array([marginal.ppf(m, i, np.array([u[i]]))[0] for i in range(5)])
    # an outcome in the part of the distribution the quantile table resolves comes back unchanged
    keep = u > m.lo0 + m.p_zero + 1e-6
    np.testing.assert_allclose(back[keep], pts[keep], rtol=1e-6, atol=1e-6)


@pytest.mark.parametrize("position", POSITIONS)
def test_u_is_uniform_when_actuals_come_from_the_distribution(position):
    n = 20000
    m = marginal.marginals(position, [PROJ[position]] * n)
    rng = np.random.default_rng(1)
    actual = np.array([marginal.ppf(m, 0, np.array([x]))[0] for x in rng.random(n)])
    u = marginal.cdf_u(m, actual, np.random.default_rng(2))
    assert abs(u.mean() - 0.5) < 0.01
    # the share of u below each decile is that decile (the zero atom is spread uniformly, so it holds)
    for q in (0.1, 0.25, 0.5, 0.75, 0.9):
        assert abs((u < q).mean() - q) < 0.02


def test_the_zero_atom_is_randomised_inside_its_mass_and_is_seeded():
    m = marginal.marginals("WR", [5.0] * 400)  # a cheap WR scores exactly 0 about a fifth of the time
    zeros = np.zeros(400)
    u1 = marginal.cdf_u(m, zeros, np.random.default_rng(3))
    u2 = marginal.cdf_u(m, zeros, np.random.default_rng(3))
    u3 = marginal.cdf_u(m, zeros, np.random.default_rng(4))
    np.testing.assert_array_equal(u1, u2)
    assert not np.array_equal(u1, u3)
    assert u1.min() >= m.lo0[0] and u1.max() <= m.lo0[0] + m.p_zero[0] + 1e-12
    assert m.p_zero[0] > 0.1 and len(set(np.round(u1, 6))) > 300


def test_a_simulated_zero_is_a_zero():
    m = marginal.marginals("WR", [5.0])
    assert marginal.ppf(m, 0, np.array([0.01]))[0] == 0.0
    assert marginal.ppf(m, 0, np.array([0.99]))[0] > 5.0


def test_normal_scores_of_the_engines_own_draws_are_standard_normal():
    n = 30000
    m = marginal.marginals("RB", [11.0] * n)
    rng = np.random.default_rng(5)
    actual = np.array([marginal.ppf(m, 0, np.array([x]))[0] for x in rng.random(n)])
    z = ndtri(np.clip(marginal.cdf_u(m, actual, np.random.default_rng(6)), 0.005, 0.995))
    assert abs(z.mean()) < 0.03 and abs(z.std() - 1) < 0.03


@pytest.mark.parametrize("position", ["QB", "RB", "WR", "TE"])
def test_cdf_agrees_with_the_engines_prob_at_least(position):
    proj = PROJ[position]
    m = marginal.marginals(position, [proj])
    for threshold in (0.5 * proj, proj, 1.5 * proj):
        u = marginal.cdf_u(m, np.array([threshold]), np.random.default_rng(0))[0]
        assert abs((1 - u) - dist.prob_at_least(position, proj, threshold)) < 0.02


def test_bad_projection_is_refused():
    with pytest.raises(ValueError):
        marginal.marginals("QB", [0.0])
    with pytest.raises(ValueError):
        marginal.marginals("QB", [float("nan")])
