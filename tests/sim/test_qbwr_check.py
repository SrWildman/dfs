"""Plan S check 2: the QB-receiver correlation re-check (`sim/qbwr_check.py`)."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest
from scipy.stats import norm

from dfs.sim import marginal, qbwr_check
from dfs.sim.correlation import CorrelationLookup
from dfs.sim.simulate import PlayerSpec, simulate_lineups


def pairs(rho: float, n: int = 4000, seed: int = 0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    z = rng.multivariate_normal([0, 0], [[1, rho], [rho, 1]], size=n)
    resid = np.column_stack([np.exp(z[:, 0]) - 1, 3 * z[:, 1]])  # monotone, not linear, in z
    return pd.DataFrame(
        {
            "game_id": np.repeat(np.arange(n // 2), 2),
            "season": 2024,
            "za": z[:, 0],
            "zb": z[:, 1],
            "ua": norm.cdf(z[:, 0]),
            "ub": norm.cdf(z[:, 1]),
            "ra": resid[:, 0],
            "rb": resid[:, 1],
            "pa": 20 + resid[:, 0],
            "pb": 12 + resid[:, 1],
        }
    )


def test_the_gaussian_joint_tail_is_the_simulators_for_a_pair_of_players():
    """The calibration's expected count is exactly what `simulate_lineups` gives two players: each keeps his
    own marginal, so both beating their own 90th percentile depends only on the pair's rho."""
    rho = 0.4
    table = pd.DataFrame(
        [{"relation": "same_team", "role_a": "QB1", "role_b": "WR1", "total_bucket": "all", "rho": rho,
          "n": 1000, "ci_lo": rho, "ci_hi": rho}]
    )  # fmt: skip
    qb = PlayerSpec("qb", "QB", "AAA", "BBB", "g", "QB1", 20.0)
    wr = PlayerSpec("wr", "WR", "AAA", "BBB", "g", "WR1", 14.0)
    res = simulate_lineups(
        [[qb], [wr]], n_sims=200_000, seed=3, correlations=CorrelationLookup(table), keep_scores=True
    )
    q90 = [
        marginal.ppf(marginal.marginals(p.position, [p.projection]), 0, np.array([0.9]))[0] for p in (qb, wr)
    ]
    both = float(((res.scores[:, 0] > q90[0]) & (res.scores[:, 1] > q90[1])).mean())
    assert both == pytest.approx(qbwr_check.joint_tail_prob(rho, 0.9), abs=0.0015)
    assert qbwr_check.joint_tail_prob(0.0, 0.9) == pytest.approx(0.01, abs=1e-6)
    assert qbwr_check.joint_tail_prob(0.45, 0.9) > qbwr_check.joint_tail_prob(0.31, 0.9)


def test_every_method_recovers_a_planted_gaussian_dependence():
    est = qbwr_check.estimates(pairs(0.35, n=20000))
    assert est["surprise"] == pytest.approx(0.35, abs=0.02)
    assert est["surprise_shrunk"] < est["surprise"]
    assert qbwr_check.as_copula_rho("z_spearman", est["z_spearman"]) == pytest.approx(0.35, abs=0.02)
    assert qbwr_check.as_copula_rho("resid_spearman", est["resid_spearman"]) == pytest.approx(0.35, abs=0.02)
    # Pearson on a non-linear transform of the scores is not the copula's correlation
    assert abs(est["resid_pearson"] - 0.35) > 0.02


def test_the_bootstrap_resamples_whole_games_and_brackets_the_estimate():
    t = qbwr_check.bootstrap(pairs(0.3), n_boot=100)
    assert list(t["method"]) == list(qbwr_check.METHODS)
    assert ((t["lo"] <= t["rho"]) & (t["rho"] <= t["hi"])).all()


def test_pairs_follow_the_relation():
    frame = pd.DataFrame(
        {
            "game_id": ["g"] * 4,
            "season": 2024,
            "team": ["A", "A", "B", "B"],
            "opp": ["B", "B", "A", "A"],
            "role": ["QB1", "WR1", "DST", "WR1"],
            "z": [1.0, 2.0, -1.0, 0.5],
            "u": [0.8, 0.9, 0.2, 0.6],
            "resid": [1.0, 2.0, -1.0, 0.5],
            "dk": [20.0, 18.0, 3.0, 10.0],
        }
    )
    same = qbwr_check.pairs_of(frame, "same_team", "QB1", "WR1")
    assert len(same) == 1 and same["zb"].iloc[0] == 2.0
    opp = qbwr_check.pairs_of(frame, "opp", "QB1", "DST")
    assert len(opp) == 1 and opp["zb"].iloc[0] == -1.0


def test_the_calibration_prefers_the_true_rho_and_exact_margins_are_uniform():
    p = pairs(0.45, n=40000, seed=1)
    cands = {"surprise_shrunk": 0.25, "truth": 0.45}
    t = qbwr_check.calibration_table(p, cands, n_boot=50)
    row = t[(t["view"] == "table margins") & (t["q"] == 0.9)].set_index("candidate")
    assert abs(row.loc["truth", "miss_se"]) < 2.5 < row.loc["surprise_shrunk", "miss_se"]
    assert row.loc["truth", "improvement_se"] > 2
    ex = qbwr_check.exact_margins(p)
    assert (ex["ua"] > 0.9).mean() == pytest.approx(0.1, abs=1e-3)


def test_the_held_out_likelihood_is_highest_near_the_planted_rho():
    p = pairs(0.3, n=20000, seed=2)
    t = qbwr_check.loglik_table(p, {"surprise_shrunk": 0.1, "a": 0.3, "b": 0.5}).set_index("candidate")
    assert t.loc["a", "gain_per_1000"] > t.loc["b", "gain_per_1000"] > -1e9
    assert t.loc["a", "gain_per_1000"] > 0 and t.loc["surprise_shrunk", "gain_per_1000"] == 0
