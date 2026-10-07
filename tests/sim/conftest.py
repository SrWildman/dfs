"""Shared fixtures for the simulator tests: no network, no cache -- only the committed distribution tables
(`models/um/distribution.csv`) and, for the shipped-table checks, `models/sim/correlations.csv`."""

import numpy as np
import pandas as pd
import pytest
from scipy.special import ndtr

from dfs.sim import marginal


def pytest_configure(config):
    config.addinivalue_line("markers", "speed: wall-clock budget tests; skip with -m 'not speed' on slow CI")


ROLE_POSITION = {
    "QB1": "QB",
    "RB1": "RB",
    "RB2": "RB",
    "WR1": "WR",
    "WR2": "WR",
    "WR3": "WR",
    "TE1": "TE",
    "DST": "DST",
}
ROLE_PRED = {
    "QB1": 18.0,
    "RB1": 14.0,
    "RB2": 9.0,
    "WR1": 14.0,
    "WR2": 11.0,
    "WR3": 8.0,
    "TE1": 8.0,
    "DST": 7.0,
}


def points_at(position: str, pred: float, u: np.ndarray) -> np.ndarray:
    """What a player projected at `pred` scores at probability `u` -- the engine's own quantile function."""
    m = marginal.marginals(position, [pred])
    return marginal.ppf(m, 0, np.asarray(u, dtype=float))


@pytest.fixture
def synth_frame():
    """Build a frame (`roles.build_frame`'s shape) of `n_games` games whose actual points are drawn from each
    player's own distribution through a latent normal with a KNOWN correlation: QB1-WR1 on the same team is
    `rho_qb_wr`, QB1 against the opposing DST is `rho_qb_dst`, everything else is independent. `rho_qb_wr` may
    be a dict from game total (38.5 / 44.5 / 50.5) to correlation."""

    def make(n_games=1500, rho_qb_wr=0.5, rho_qb_dst=-0.3, seed=0):
        rng = np.random.default_rng(seed)
        rows = []
        for g in range(n_games):
            gid = f"2020_01_G{g}"
            total = float(rng.choice([38.5, 44.5, 50.5]))
            teams = (f"A{g}", f"B{g}")
            rho_wr = rho_qb_wr[total] if isinstance(rho_qb_wr, dict) else rho_qb_wr
            # latent z per (team, role); correlated blocks are filled below
            z = {(t, role): rng.standard_normal() for t in teams for role in ROLE_PRED}
            for t, o in (teams, teams[::-1]):
                z[(t, "WR1")] = rho_wr * z[(t, "QB1")] + np.sqrt(1 - rho_wr**2) * z[(t, "WR1")]
                # DST of team o faces QB of team t
                z[(o, "DST")] = rho_qb_dst * z[(t, "QB1")] + np.sqrt(1 - rho_qb_dst**2) * z[(o, "DST")]
            for t, o in (teams, teams[::-1]):
                for role, pred in ROLE_PRED.items():
                    pos = ROLE_POSITION[role]
                    dk = float(points_at(pos, pred, ndtr(z[(t, role)])))
                    rows.append(
                        dict(
                            season=2020,
                            week=1 + g % 17,
                            game_id=gid,
                            team=t,
                            opp=o,
                            position=pos,
                            gsis_id=f"{t}-{role}",
                            name=f"{t} {role}",
                            role=role,
                            pred=pred,
                            dk=dk,
                            total=total,
                        )
                    )
        return pd.DataFrame(rows)

    return make


@pytest.fixture
def synth_week():
    """One week: 4 games, 8 teams, a full set of players per team (what `backtest.generate_lineups` reads)."""
    rng = np.random.default_rng(1)
    rows = []
    for g in range(4):
        gid = f"2023_01_G{g}"
        for t, o in ((f"H{g}", f"V{g}"), (f"V{g}", f"H{g}")):
            for role, pred in ROLE_PRED.items():
                rows.append(
                    dict(
                        season=2023,
                        week=1,
                        game_id=gid,
                        team=t,
                        opp=o,
                        position=ROLE_POSITION[role],
                        gsis_id=f"{t}-{role}",
                        name=f"{t} {role}",
                        role=role,
                        pred=pred * float(rng.uniform(0.8, 1.2)),
                        dk=float(rng.uniform(0, 25)),
                        total=45.0,
                    )
                )
    return pd.DataFrame(rows)
