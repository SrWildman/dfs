"""Relative lines: the contest's cash and top-end lines move with the slate, so the simulator scores a lineup
against the FIELD instead of against a fixed number.

The field holds the same players you do. A contest's **field mean** (the mean entry score) is exactly

    field mean = sum over players of  own_i * points_i

where `own_i` is the share of lineups holding player i (it sums to 9: one lineup's worth) and `points_i` is
his score that week. The lines Sam's contests actually pay at sit a stable distance from it (`docs/SIM.md`,
"Relative lines": double-up cash line +0.6, GPP min-cash +17.9, top 10% +31.9, top 1% +63.2 points above the
field mean), so a lineup's Cash % is P(score >= field mean + 0.6) and its Top 1% is P(score >= field mean +
63.2), per draw.

This module holds the pure parts: turning a projected-ownership table into field weights (with the optional
projected-to-expected ownership curve), choosing which players must be simulated to carry 95% of the
ownership, and the expected points of the rest. `simulate.simulate_lineups(..., field=...)` does the draws:
every player in `FieldModel.cover` joins the lineups' players in the one correlated simulation, so a lineup
that shares players with the field (the chalk) is correlated with the field mean the way it should be, and
the remaining ownership (the tail) enters at its players' EXPECTED points -- a few points of fixed mass
rather than five percent of the slate left out.

No sheet, no network; deterministic.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

import numpy as np

from dfs.sim import marginal

if TYPE_CHECKING:  # simulate.py imports this module; only the type is needed here
    from dfs.sim.simulate import PlayerSpec

LINEUP_SIZE = 9.0  # ownership sums to one lineup's worth: nine players
DEFAULT_COVERAGE = 0.95  # the simulated field players hold at least this share of the total ownership
EXPECTATION_GRID = 400  # midpoints of the probability axis used for a tail player's expected points

Curve = Sequence[tuple[float, float]]


@dataclass(frozen=True)
class FieldModel:
    """The ownership-weighted field of one slate, ready for `simulate_lineups(field=...)`.

    `players` are the players that have both a projection and an ownership; `cash_weights` / `top_weights` are
    their weights (each sums to 9) for the double-up field and the large-field GPP field (they differ only
    when the config's ownership curves do). `cover` holds the indices of `players` that are always simulated
    (together at least `coverage` of the ownership in either field); every other player is the tail."""

    players: tuple[PlayerSpec, ...]
    cash_weights: np.ndarray
    top_weights: np.ndarray
    cover: tuple[int, ...]
    cash_offset: float
    top_offset: float
    coverage: float = DEFAULT_COVERAGE

    @property
    def cover_players(self) -> list[PlayerSpec]:
        return [self.players[i] for i in self.cover]

    def shares(self, contest: str = "top") -> dict[str, float]:
        """`{player id: share of the contest's lineups holding him}` (they sum to 9): `contest` is `"top"`
        for the large-field GPP field, `"cash"` for the double-up field. The ownership every duplicate
        estimate uses (`dfs.sim.dups`)."""
        weights = self.top_weights if contest == "top" else self.cash_weights
        return {p.id: float(w) for p, w in zip(self.players, weights, strict=True)}


def apply_curve(own: np.ndarray, curve: Curve = ()) -> np.ndarray:
    """Projected ownership -> expected ownership through a piecewise-linear curve of `(projected, expected)`
    points (both fractions, 0.15 = 15%). The curve is anchored at (0, 0) and (1, 1) unless it names those
    x-values itself, so an EMPTY curve is the identity. Points must be strictly increasing in `projected`
    and non-decreasing in `expected`."""
    own = np.asarray(own, dtype=float)
    if not curve:
        return own.copy()
    points = sorted((float(x), float(y)) for x, y in curve)
    xs = [x for x, _ in points]
    ys = [y for _, y in points]
    if any(b <= a for a, b in zip(xs, xs[1:], strict=False)):
        raise ValueError("an ownership curve's projected values must be strictly increasing")
    if any(b < a for a, b in zip(ys, ys[1:], strict=False)):
        raise ValueError("an ownership curve's expected values must not decrease")
    if xs[0] > 0.0:
        xs, ys = [0.0, *xs], [0.0, *ys]
    if xs[-1] < 1.0:
        xs, ys = [*xs, 1.0], [*ys, max(1.0, ys[-1])]
    return np.interp(own, xs, ys)


def normalise(own: np.ndarray, total: float = LINEUP_SIZE) -> np.ndarray:
    """`own` scaled to sum to `total` (nine: one lineup's worth)."""
    own = np.asarray(own, dtype=float)
    s = own.sum()
    if not s > 0:
        raise ValueError("ownership sums to zero: there is no field to weigh")
    return own * (total / s)


def field_weights(own: np.ndarray, curve: Curve = ()) -> np.ndarray:
    """The weights of one contest type's field: projected ownership through its curve, summing to 9."""
    return normalise(apply_curve(own, curve))


def cover_indices(weights: np.ndarray, coverage: float = DEFAULT_COVERAGE) -> list[int]:
    """The fewest players, highest weight first (ties by position in `weights`), whose weights add up to at
    least `coverage` of the total."""
    order = np.argsort(-np.asarray(weights, dtype=float), kind="stable")
    cum = np.cumsum(np.asarray(weights, dtype=float)[order])
    need = coverage * float(cum[-1]) if len(cum) else 0.0
    take = int(np.searchsorted(cum, need - 1e-12, side="left")) + 1 if len(cum) else 0
    return [int(i) for i in order[:take]]


def clean_ownership(own: Mapping[str, object]) -> dict[str, float]:
    """`{name: share}` with every blank, non-finite or negative value dropped (a player the table has no
    number for is left out of the field, not treated as unowned)."""
    out: dict[str, float] = {}
    for name, value in own.items():
        try:
            share = float(value)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            continue
        if math.isfinite(share) and share >= 0:
            out[str(name)] = share
    return out


def has_ownership(own: Mapping[str, object]) -> bool:
    """True when the table has any positive share at all (Own% is blank until TFFB publishes it)."""
    return any(v > 0 for v in clean_ownership(own).values())


def build_field(
    specs: Iterable[PlayerSpec],
    own: Mapping[str, object],
    *,
    cash_offset: float,
    top_offset: float,
    cash_curve: Curve = (),
    top_curve: Curve = (),
    coverage: float = DEFAULT_COVERAGE,
) -> FieldModel | None:
    """The field for `specs` (every player the simulator can rate) given `own` (name -> projected share).

    Only players with a spec AND a positive ownership are in it; the weights are normalised over those, so
    ownership on a player the simulator cannot rate is dropped, not guessed. Returns None when no ownership is
    published (the caller falls back to fixed lines and says so). Players are ordered by name, so the model
    does not depend on the order `specs` came in."""
    shares = clean_ownership(own)
    rated = sorted({s.id: s for s in specs if shares.get(s.id, 0.0) > 0}.values(), key=lambda s: s.id)
    if not rated:
        return None
    raw = np.array([shares[s.id] for s in rated])
    cash_w, top_w = field_weights(raw, cash_curve), field_weights(raw, top_curve)
    cover = sorted(set(cover_indices(cash_w, coverage)) | set(cover_indices(top_w, coverage)))
    return FieldModel(
        players=tuple(rated),
        cash_weights=cash_w,
        top_weights=top_w,
        cover=tuple(cover),
        cash_offset=float(cash_offset),
        top_offset=float(top_offset),
        coverage=coverage,
    )


def expected_points(specs: Sequence[PlayerSpec], *, tables=None) -> np.ndarray:
    """Each player's expected points under his own distribution (the mean of his quantile function), in the
    order given: what a tail player contributes to the field mean without being simulated. A projection <= 0
    is exactly 0 points, as in the simulator."""
    out = np.zeros(len(specs))
    u = (np.arange(EXPECTATION_GRID) + 0.5) / EXPECTATION_GRID
    for position in ("QB", "RB", "WR", "TE", "DST"):
        cols = [i for i, p in enumerate(specs) if p.position == position and p.projection > 0]
        if not cols:
            continue
        mg = marginal.marginals(position, [specs[i].projection for i in cols], tables=tables)
        for k, col in enumerate(cols):
            out[col] = float(marginal.ppf(mg, k, u).mean())
    return out
