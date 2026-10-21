"""Part 2: the lineup simulator.

Each player has his own outcome distribution (`dfs.model.distribution`, through `marginal.py`); players in
the same game move together according to the role-pair correlations (`correlation.py`). The simulator joins
them with a **Gaussian copula**:

    z ~ N(0, R)      R: the correlation matrix of every distinct player in every lineup
    u = Phi(z)       a uniform per player
    points = the player's quantile function at u

so each player keeps exactly his own marginal while the pairs inherit R. The same player in two lineups is
ONE variable (one column of z), so lineups that share players are correlated with each other the way real
lineups are.

R is assembled from the shipped table: a pair in different games is 0; in the same game it is the table's
'same_team' or 'opp' value for the two roles. Pairs
assembled from separately estimated values need not form a valid correlation matrix, so R is repaired to the
nearest positive semi-definite matrix by eigenvalue clipping and rescaling to a unit diagonal; the result
records whether that was needed and the largest entry it moved.

**Lines.** By default a lineup is scored against two FIXED numbers (`cash_line`, `gpp_target`). Passing a
`field` (`dfs.sim.field.FieldModel`) scores it against the FIELD instead: the top-owned players join the
lineups' players in the same correlated draw, each draw's field mean is the ownership-weighted sum of their
points, and `p_cash` / `p_gpp` become P(score >= field mean + the double-up offset) and P(score >= field
mean + the top-1% offset), per draw. `p_gpp` is the "Top 1%" column in that mode; the names stay for the
fixed-line callers (the back-test, the tests).
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd
from scipy.special import ndtr

from dfs.sim import field as field_model
from dfs.sim import marginal
from dfs.sim.correlation import CorrelationLookup
from dfs.sim.roles import ROLE_POSITION, normalize_role
from dfs.sim.shape_table import default_lookup

# Placeholders, not advice: Sam types his own cash line each week, and the local integration (which owns the
# sheet) passes it in. 145 is a typical 50/50 / double-up line on a full NFL main slate, 190 a typical
# small-field tournament winner's neighbourhood.
DEFAULT_CASH_LINE = 145.0
DEFAULT_GPP_TARGET = 190.0

QUANTILE_LEVELS = (0.10, 0.25, 0.50, 0.75, 0.90, 0.99)
EIGEN_FLOOR = 1e-8  # eigenvalues below this are lifted to it
REPAIR_TOLERANCE = 1e-9  # a matrix whose smallest eigenvalue is above -this needs no repair

# A same-team pair of two different players in the SAME role (a team's WR4 and a fifth receiver, both "WR4";
# two RB3s) has no estimate of its own: it borrows the pair with the neighbouring role.
_NEIGHBOUR_ROLE = {"RB3": "RB2", "WR4": "WR3", "TE2": "TE1"}


@dataclass(frozen=True)
class PlayerSpec:
    """One player in a lineup. `id` identifies the player across lineups (the same id is the same random
    variable); `role` is one of `roles.ROLES` ("QB" and "TE" read as QB1 / TE1; RB4, WR5 ... collapse into the
    last role); `projection` is his projected points (> 0); `salary` is informational."""

    id: str
    position: str
    team: str
    opp: str
    game_id: str
    role: str
    projection: float
    salary: float | None = None
    # The team's closing spread (positive = favoured) and implied total. Ignored by the shipped pooled table;
    # read only by a conditional lookup (`script.ContextLookup`, the game-script research).
    spread: float | None = None
    implied: float | None = None
    # The player's / team's shape (three-game priors; `script_shape.FEATURES`): his share of the team's
    # targets, a QB's share of the team's carries, the team's pass rate over expected and its target
    # concentration. Read only by a shape lookup (`script_shape.ShapeLookup`); ignored by the pooled table.
    tshare: float | None = None
    qb_rush: float | None = None
    proe: float | None = None
    conc: float | None = None


@dataclass(frozen=True)
class LineupStats:
    mean: float
    sd: float
    quantiles: dict[float, float]
    p_cash: float
    p_gpp: float


@dataclass(frozen=True)
class RepairInfo:
    """What the positive-semi-definite repair did to the assembled correlation matrix."""

    n_players: int
    needed: bool
    min_eigenvalue: float
    max_change: float


@dataclass
class SimResult:
    lineups: list[LineupStats]
    p_any_gpp: float
    p_any_cash: float
    expected_cashing: float
    score_correlation: np.ndarray  # lineups x lineups
    cash_line: float
    gpp_target: float
    n_sims: int
    seed: int
    repair: RepairInfo
    notes: list[str] = field(default_factory=list)
    scores: np.ndarray | None = None  # n_sims x lineups
    # "fixed": scored against `cash_line` / `gpp_target`. "relative": against the field mean per draw, and
    # then `cash_line` / `gpp_target` hold the MEAN of those per-draw lines (what they came to on average).
    lines: str = "fixed"
    field_mean: float | None = None  # relative lines: mean and sd of the double-up field's average score
    field_sd: float | None = None
    field_extra_players: int = 0  # simulated only for the field (not in any lineup)
    cash_lines: np.ndarray | None = None  # relative lines, with keep_scores: each draw's cash line ...
    top_lines: np.ndarray | None = None  # ... and top-end line

    def table(self) -> pd.DataFrame:
        """One row per lineup."""
        rows = []
        for i, s in enumerate(self.lineups, start=1):
            rows.append(
                {
                    "lineup": i,
                    "mean": s.mean,
                    **{f"p{round(q * 100)}": v for q, v in s.quantiles.items()},
                    "p_cash": s.p_cash,
                    "p_gpp": s.p_gpp,
                }
            )
        return pd.DataFrame(rows)


@dataclass(frozen=True)
class SwapImpact:
    """The effect of replacing one player. Both lineups are simulated in one run, so every player they share
    has the same draws in both; the sampling noise left in the difference is the swapped-in player's."""

    out_id: str
    in_id: str
    before: LineupStats
    after: LineupStats
    delta_p_cash: float
    delta_p_gpp: float
    delta_mean: float


def _validate(spec: PlayerSpec) -> PlayerSpec:
    if spec.position not in ("QB", "RB", "WR", "TE", "DST"):
        raise ValueError(f"{spec.id}: unknown position {spec.position!r}")
    role = normalize_role(spec.role)
    if ROLE_POSITION[role] != spec.position:
        raise ValueError(f"{spec.id}: role {spec.role!r} does not belong to position {spec.position}")
    if spec.projection is None or np.isnan(spec.projection):
        raise ValueError(f"{spec.id}: projection is missing")
    if not spec.game_id:
        raise ValueError(f"{spec.id}: game_id is required (players in different games are independent)")
    return spec if role == spec.role else PlayerSpec(**{**spec.__dict__, "role": role})


def _distinct_players(lineups: list[list[PlayerSpec]]) -> list[PlayerSpec]:
    seen: dict[str, PlayerSpec] = {}
    for lineup in lineups:
        for raw in lineup:
            spec = _validate(raw)
            old = seen.get(spec.id)
            if old is None:
                seen[spec.id] = spec
            elif old != spec:
                raise ValueError(f"player {spec.id!r} appears twice with different details: {old} vs {spec}")
    return list(seen.values())


def correlation_matrix(players: list[PlayerSpec], lookup: CorrelationLookup) -> tuple[np.ndarray, list[str]]:
    """The raw (unrepaired) correlation matrix for `players`, and notes on any pair that had to borrow."""
    n = len(players)
    r = np.eye(n)
    notes: list[str] = []
    for i in range(n):
        for j in range(i):
            a, b = players[i], players[j]
            if a.game_id != b.game_id:
                continue
            same_team = a.team == b.team
            relation = "same_team" if same_team else "opp"
            role_a, role_b = a.role, b.role
            if same_team and role_a == role_b:
                role_b = _NEIGHBOUR_ROLE.get(role_a)
                if role_b is None:
                    notes.append(f"{a.id} and {b.id} are both {role_a} on {a.team}; treated as independent")
                    continue
                notes.append(
                    f"{a.id} and {b.id} are both {role_a} on {a.team}; used the {role_a}-{role_b} value"
                )
            rho_pair = getattr(lookup, "rho_pair", None)  # a context-aware lookup also sees the two specs
            r[i, j] = r[j, i] = (
                rho_pair(relation, role_a, role_b, a, b) if rho_pair else lookup.rho(relation, role_a, role_b)
            )
    return r, notes


def repair_correlation(r: np.ndarray) -> tuple[np.ndarray, RepairInfo]:
    """The nearest positive semi-definite correlation matrix by eigenvalue clipping and rescaling (unchanged
    when `r` already is one)."""
    n = len(r)
    if n == 0:
        return r, RepairInfo(0, False, 1.0, 0.0)
    w, v = np.linalg.eigh(r)
    if w[0] >= -REPAIR_TOLERANCE:
        return r, RepairInfo(n, False, float(w[0]), 0.0)
    fixed = (v * np.maximum(w, EIGEN_FLOOR)) @ v.T
    scale = 1.0 / np.sqrt(np.diag(fixed))
    fixed = fixed * np.outer(scale, scale)
    np.fill_diagonal(fixed, 1.0)
    return fixed, RepairInfo(n, True, float(w[0]), float(np.abs(fixed - r).max()))


def _draw(r: np.ndarray, n_sims: int, rng: np.random.Generator) -> np.ndarray:
    """`n_sims` x n standard normals with correlation `r` (any positive semi-definite matrix)."""
    w, v = np.linalg.eigh(r)
    # The PRINCIPAL square root, v sqrt(w) v^T, not v sqrt(w): an eigenvector's sign (and the basis inside a
    # repeated eigenvalue) is whatever the LAPACK build returns, and v sqrt(w) turns a flipped sign into a
    # different set of draws for the same seed (27-29 of 40 lineups changed their P(GPP)). The principal root
    # is the same matrix whichever sign or basis eigh chose, so a seed means the same draws on every machine.
    root = (v * np.sqrt(np.maximum(w, 0.0))) @ v.T
    return rng.standard_normal((n_sims, len(r))) @ root


def simulate_lineups(
    lineups: list[list[PlayerSpec]],
    *,
    n_sims: int = 20000,
    seed: int = 0,
    cash_line: float = DEFAULT_CASH_LINE,
    gpp_target: float = DEFAULT_GPP_TARGET,
    correlations: CorrelationLookup | None = None,
    tables: pd.DataFrame | None = None,
    independent: bool = False,
    keep_scores: bool = False,
    field: field_model.FieldModel | None = None,
    tail_delta: dict[str, float] | None = None,
) -> SimResult:
    """Simulate every lineup on the same draws.

    `correlations` defaults to `models/sim/correlations.csv` with the shipped team-shape layer on top
    (`shape_table`: QB1-TE1 by the tight end's target share; a player whose `tshare` is unknown gets the
    pooled value), `tables` to the shipped distribution tables; `independent=True` zeroes every correlation
    (the comparison the back-test runs). Deterministic in `seed`.

    `tail_delta` (role -> delta, default none) stretches each player's upper quantiles about his median by
    `1 + delta` for his role (`dfs.tail_check`, a research hook: the shipped simulator passes none).

    `field` switches to relative lines (see the module docstring): `cash_line` and `gpp_target` are then
    ignored, and the players of `field.cover` are simulated alongside the lineups' (a player who is in both is
    one random variable, so a lineup of chalk is correlated with the field mean).
    """
    if not lineups or not all(lineups):
        raise ValueError("lineups must be non-empty lists of players")
    field_cover = field.cover_players if field is not None else []
    players = _distinct_players([*lineups, field_cover] if field_cover else lineups)
    lineup_ids = {p.id for lineup in lineups for p in lineup}
    index = {p.id: i for i, p in enumerate(players)}
    m = len(players)
    notes: list[str] = []
    if independent:
        r, repair = np.eye(m), RepairInfo(m, False, 1.0, 0.0)
    else:
        raw, notes = correlation_matrix(players, correlations or default_lookup())
        r, repair = repair_correlation(raw)

    rng = np.random.default_rng(seed)
    u = ndtr(_draw(r, n_sims, rng))
    points = np.zeros((n_sims, m))
    for position in ("QB", "RB", "WR", "TE", "DST"):
        cols = [i for i, p in enumerate(players) if p.position == position and p.projection > 0]
        if not cols:
            continue
        delta = None if tail_delta is None else [tail_delta.get(players[i].role, 0.0) for i in cols]
        mg = marginal.marginals(position, [players[i].projection for i in cols], tables=tables, delta=delta)
        for k, col in enumerate(cols):
            points[:, col] = marginal.ppf(mg, k, u[:, col])

    scores = np.column_stack([points[:, [index[p.id] for p in lineup]].sum(axis=1) for lineup in lineups])
    cash_thr: float | np.ndarray = cash_line  # a number, or (relative lines) one threshold per draw
    gpp_thr: float | np.ndarray = gpp_target
    lines, f_mean, f_sd, cash_lines, top_lines = "fixed", None, None, None, None
    if field is not None:
        cash_lines, top_lines, tail_notes = _field_lines(field, points, index, tables)
        notes.extend(tail_notes)
        cash_thr, gpp_thr = cash_lines[:, None], top_lines[:, None]
        lines, f_mean, f_sd = (
            "relative",
            float((cash_lines - field.cash_offset).mean()),
            float((cash_lines - field.cash_offset).std()),
        )
        cash_line, gpp_target = float(cash_lines.mean()), float(top_lines.mean())
    q = np.quantile(scores, QUANTILE_LEVELS, axis=0)
    hit_cash, hit_gpp = scores >= cash_thr, scores >= gpp_thr
    stats = [
        LineupStats(
            mean=float(scores[:, k].mean()),
            sd=float(scores[:, k].std()),
            quantiles={level: float(q[i, k]) for i, level in enumerate(QUANTILE_LEVELS)},
            p_cash=float(hit_cash[:, k].mean()),
            p_gpp=float(hit_gpp[:, k].mean()),
        )
        for k in range(scores.shape[1])
    ]
    with np.errstate(invalid="ignore", divide="ignore"):
        corr = np.corrcoef(scores.T) if scores.shape[1] > 1 else np.ones((1, 1))
    return SimResult(
        lineups=stats,
        p_any_gpp=float(hit_gpp.any(axis=1).mean()),
        p_any_cash=float(hit_cash.any(axis=1).mean()),
        expected_cashing=float(hit_cash.sum(axis=1).mean()),
        score_correlation=corr,
        cash_line=cash_line,
        gpp_target=gpp_target,
        n_sims=n_sims,
        seed=seed,
        repair=repair,
        notes=notes,
        scores=scores if keep_scores else None,
        lines=lines,
        field_mean=f_mean,
        field_sd=f_sd,
        field_extra_players=len(players) - len(lineup_ids) if field is not None else 0,
        cash_lines=cash_lines if keep_scores else None,
        top_lines=top_lines if keep_scores else None,
    )


def _field_lines(
    field: field_model.FieldModel, points: np.ndarray, index: dict[str, int], tables
) -> tuple[np.ndarray, np.ndarray, list[str]]:
    """Each draw's cash line and top-end line: the field mean (the ownership-weighted sum of the draw's
    points) plus the offset. A field player who was simulated enters at his drawn points; the rest of the
    ownership (the tail) enters at his expected points, a constant."""
    drawn = [(k, index[p.id]) for k, p in enumerate(field.players) if p.id in index]
    tail = [k for k, p in enumerate(field.players) if p.id not in index]
    ks, cols = [k for k, _ in drawn], [c for _, c in drawn]
    tail_points = (
        field_model.expected_points([field.players[k] for k in tail], tables=tables) if tail else np.zeros(0)
    )
    means = []
    for weights, drawn_own in ((field.cash_weights, field.cash_draws), (field.top_weights, field.top_draws)):
        if drawn_own is None:
            means.append(points[:, cols] @ weights[ks] + float(weights[tail] @ tail_points))
            continue
        # ownership uncertainty: draw d is scored against scenario d % scenarios's field
        w = drawn_own[np.arange(len(points)) % len(drawn_own)]
        means.append((points[:, cols] * w[:, ks]).sum(axis=1) + w[:, tail] @ tail_points)
    notes = []
    if tail:
        share = float(field.cash_weights[tail].sum() / field.cash_weights.sum())
        notes.append(
            f"field: {len(drawn)} players simulated, {len(tail)} more at their expected points "
            f"({share:.1%} of the ownership)"
        )
    return means[0] + field.cash_offset, means[1] + field.top_offset, notes


def swap_impact(
    lineup: list[PlayerSpec],
    out_id: str,
    in_spec: PlayerSpec,
    **kwargs,
) -> SwapImpact:
    """What swapping `out_id` for `in_spec` does to the lineup's P(cash), P(GPP target) and mean. Both
    versions are simulated in one call, so the players they share have identical draws. `kwargs` are
    `simulate_lineups`'s (n_sims, seed, cash_line, gpp_target, ...)."""
    if all(p.id != out_id for p in lineup):
        raise ValueError(f"{out_id!r} is not in the lineup")
    if any(p.id == in_spec.id for p in lineup):
        raise ValueError(f"{in_spec.id!r} is already in the lineup")
    swapped = [in_spec if p.id == out_id else p for p in lineup]
    result = simulate_lineups([lineup, swapped], **kwargs)
    before, after = result.lineups
    return SwapImpact(
        out_id=out_id,
        in_id=in_spec.id,
        before=before,
        after=after,
        delta_p_cash=after.p_cash - before.p_cash,
        delta_p_gpp=after.p_gpp - before.p_gpp,
        delta_mean=after.mean - before.mean,
    )
