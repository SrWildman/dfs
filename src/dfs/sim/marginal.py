"""One player's outcome distribution as a CDF / quantile function that the copula can move through.

The distribution engine (`dfs.model.distribution`) stores, per projection, the quantiles of
`actual / projection` at `LEVELS` plus the share of games that scored exactly 0. This module reads that
as a distribution: the CDF is the engine's own (piecewise-linear through the quantile knots, end segments
extrapolated to probability 0 and 1 -- the same knots `prob_at_least` uses), except that the atom at zero is
made explicit so an actual of exactly 0 can be placed anywhere inside it:

    u in [0, lo0)              below zero (only a defense or a rare fumbling QB/RB can score under 0)
    u in [lo0, lo0 + p_zero]   the atom: exactly 0 points
    u above                    the continuous part

`cdf_u` (actual -> u, used to fit correlations) and `ppf` (u -> points, used to simulate) are inverses of
each other by construction.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from dfs.model import distribution as dist

_LEVELS = np.asarray(dist.LEVELS, dtype=float)
# The knots' probability axis: 0, the stored levels, 1.
KNOT_LEVELS = np.concatenate([[0.0], _LEVELS, [1.0]])
_EPS = 1e-9


@dataclass(frozen=True)
class Marginals:
    """The distributions of `n` players, as arrays: `knots[i]` are the ratio-to-projection values at
    `KNOT_LEVELS` for player i; `lo0[i]` / `p_zero[i]` place the zero atom on the probability axis."""

    position: str
    projection: np.ndarray
    knots: np.ndarray  # n x (len(LEVELS) + 2)
    lo0: np.ndarray
    p_zero: np.ndarray

    def __len__(self) -> int:
        return len(self.projection)


def _extend(ratios: np.ndarray) -> np.ndarray:
    """The engine's CDF knots (`distribution._cdf_knots`) for every row at once: the stored ratio quantiles
    with the end segments extrapolated linearly to probability 0 and 1."""
    lo_slope = (ratios[:, 1] - ratios[:, 0]) / (_LEVELS[1] - _LEVELS[0])
    hi_slope = (ratios[:, -1] - ratios[:, -2]) / (_LEVELS[-1] - _LEVELS[-2])
    lo = np.minimum(ratios[:, 0] - lo_slope * _LEVELS[0], ratios[:, 0])
    hi = np.maximum(ratios[:, -1] + hi_slope * (1 - _LEVELS[-1]), ratios[:, -1])
    return np.column_stack([lo, ratios, hi])


def marginals(position: str, projections, *, tables: pd.DataFrame | None = None) -> Marginals:
    """The distribution of each player projected at `projections` (all > 0) at `position`."""
    proj = np.asarray(projections, dtype=float)
    if proj.size and (np.isnan(proj).any() or (proj <= 0).any()):
        raise ValueError("projections must be positive numbers")
    if proj.size == 0:
        empty = np.empty((0, len(KNOT_LEVELS)))
        return Marginals(position, proj, empty, proj.copy(), proj.copy())
    ratios, p_zero = dist._interpolated(dist._table(position, tables), proj)
    knots = _extend(ratios)
    lo0 = np.array([np.interp(-_EPS, k, KNOT_LEVELS) for k in knots])
    return Marginals(position, proj, knots, lo0, p_zero)


def cdf_u(m: Marginals, actual, rng: np.random.Generator) -> np.ndarray:
    """The probability-integral transform of `actual` points through each player's distribution. An actual of
    exactly 0 is placed uniformly at random inside the zero atom (`rng` is the only randomness)."""
    actual = np.asarray(actual, dtype=float)
    ratio = actual / m.projection
    u = np.empty(len(m))
    for i in range(len(m)):
        u[i] = np.interp(ratio[i], m.knots[i], KNOT_LEVELS)
    atom = actual == 0
    lo = m.lo0[atom]
    u[atom] = lo + rng.random(atom.sum()) * m.p_zero[atom]
    pos = actual > 0
    u[pos] = np.maximum(u[pos], m.lo0[pos] + m.p_zero[pos])
    return u


def ppf(m: Marginals, i: int, u: np.ndarray) -> np.ndarray:
    """Points for player `i` at probability `u` (any shape): the quantile function."""
    ratio = np.interp(u, KNOT_LEVELS, m.knots[i])
    atom_top = m.lo0[i] + m.p_zero[i]
    ratio = np.where((u >= m.lo0[i]) & (u <= atom_top), 0.0, ratio)
    ratio = np.where(u > atom_top, np.maximum(ratio, 0.0), ratio)
    return ratio * m.projection[i]
