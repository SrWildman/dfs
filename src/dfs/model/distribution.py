"""Part 4: the distribution engine -- the model's main deliverable.

It turns ANY projection (the model's own `um_mean`, or Sam's calibrated TFFB number) into a spread of
outcomes: Floor, Ceiling, P(>= k x salary). It is empirical, not parametric: from out-of-fold predictions
on 12 seasons it records, per position and level, how the quantiles of `actual / predicted` depend on the
projection, plus the share of games that scored exactly 0. A projection is then scored by interpolating
between the two neighbouring rows of the stored table.

**How the dependence on the projection is modelled (table format `TABLE_FORMAT`).** Per position and per
quantile level, a linear-spline quantile regression of the ratio on log(projection) (knots at the
`SPLINE_KNOT_QUANTILES` of the projections), evaluated at `N_KNOTS` projections between the 0.5th and 99.5th
percentile of the data and stored as a table: one row per knot, `center` = the knot's projection. Beyond the
first / last knot the engine holds the ratio flat (the edge of the data, not a bucket centre). That is why
the stored table keeps the shape of the original per-bucket one (`center`, `p_zero`, one column per level)
and why `ratio_matrix` and everything built on it (`dfs.sim`, the sheet) did not change. The earlier
format stored the empirical quantiles of a handful of buckets (`build_bucket_tables`, still used for
P(actual = 0)); it reused the top bucket's ratios however high the projection, which overstated the best
players because real outcomes regress harder at the top. The measurements are in docs/MODEL.md.

P(actual = 0) is unchanged: the share of games that scored exactly 0 per `BUCKET_EDGES` bucket, blended
between bucket centres.

Quantile levels: every 0.05 from 0.05 to 0.95 (the required grid) plus 0.01, 0.025, 0.975 and 0.99. The
extra tail levels exist because "P(>= 4 x salary)" for a cheap player sits above the 95th percentile of
his outcomes, where a grid that stops at 0.95 would force an arbitrary extrapolation.

`prob_at_least` reads the ratio CDF, built from the quantile table with linear interpolation between knots
and a linear extrapolation of the end segments to CDF 0 and 1. A threshold <= 0 is always met (1.0); a
projection <= 0 has no outcomes above 0, so every function returns zeros for it; a NaN projection returns
NaN ("blank is not zero").
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.optimize import linprog

from dfs.paths import REPO_ROOT

ARTIFACT_DIR = REPO_ROOT / "models" / "um"
TABLES_FILE = "distribution.csv"

LEVELS = (
    0.01,
    0.025,
    *(round(0.05 * i, 2) for i in range(1, 20)),
    0.975,
    0.99,
)
# Positions whose points are always whole numbers. A projection scored through a continuous ratio CDF
# otherwise answers P(X > T) at a whole-number threshold, not P(X >= T), which understates it by the share of
# games that land exactly on T (7-8% of DST games land on exactly 3 or exactly 6). `prob_at_least` applies the
# standard continuity correction for them: P(X >= T) = P(X >= ceil(T)) is read at ceil(T) - 0.5.
INTEGER_SCORING = frozenset({"DST"})

FLOOR_LEVEL = 0.20
CEILING_LEVEL = 0.85

# The shape of the stored table. "knots-v2": one row per projection knot of a smooth fit (see the module
# docstring). "buckets-v1" (the first release) stored one row per bucket mean; a file in that format still
# loads (same columns) and is scored by the same interpolation, it just carries the old top-end bias.
TABLE_FORMAT = "knots-v2"
# The smooth fit: knots of the linear spline in log(projection), as quantiles of the training projections.
# Five knots (seven coefficients per level) is what the bench in docs/MODEL.md settled on: three knots left
# the top RB band at 0.948, six or more bought nothing (and were worse out of time).
SPLINE_KNOT_QUANTILES = (0.2, 0.4, 0.6, 0.8, 0.95)
# The data edge: the table is evaluated between these percentiles of the training projections and held flat
# in ratio beyond them. 99.5 leaves the thin top (QB 28+, DST 13+) almost entirely inside the fitted range;
# an edge at the 99th percentile lets RB 26+ and DST 13+ slip below 0.95 (docs/MODEL.md).
EDGE_PERCENTILES = (0.5, 99.5)
N_KNOTS = 21
# Fewer rows than this per position cannot support a 7-coefficient fit at the 1% / 99% levels.
MIN_FIT_ROWS = 500

# Inner bucket edges by predicted mean (the buckets are `<e0`, `e0-e1`, ..., `eN+`). Since "knots-v2" they
# only define the buckets whose P(actual = 0) is stored (and `build_bucket_tables`, the v1 table).
BUCKET_EDGES = {
    "QB": (14.0, 18.0, 22.0),
    "RB": (6.0, 10.0, 14.0, 18.0),
    "WR": (6.0, 10.0, 14.0, 18.0),
    "TE": (5.0, 8.0, 11.0),
    "DST": (5.0, 7.0, 9.0),
}


def level_column(level: float) -> str:
    return f"q{level:g}"


LEVEL_COLUMNS = [level_column(level) for level in LEVELS]


class DistributionError(Exception):
    """The distribution tables are missing or malformed."""


def bucket_label(position: str, index: int) -> str:
    edges = BUCKET_EDGES[position]
    if index == 0:
        return f"<{edges[0]:g}"
    if index == len(edges):
        return f"{edges[-1]:g}+"
    return f"{edges[index - 1]:g}-{edges[index]:g}"


def bucket_index(position: str, predicted: np.ndarray | pd.Series) -> np.ndarray:
    """Bucket number per predicted mean (a prediction exactly on an edge belongs to the upper bucket)."""
    return np.digitize(np.asarray(predicted, dtype=float), BUCKET_EDGES[position])


@dataclass(frozen=True)
class Quantiles:
    """The outcome distribution for one projection: `values[i]` is the `levels[i]` quantile of the points
    scored, in points. `p_zero` is the share of comparable games that scored exactly 0."""

    projection: float
    levels: tuple[float, ...]
    values: tuple[float, ...]
    p_zero: float

    def at(self, level: float) -> float:
        """The `level` quantile (0-1) in points, interpolated between the stored levels."""
        if any(np.isnan(self.values)):
            return float("nan")
        return float(np.interp(level, self.levels, self.values))


def build_bucket_tables(oof: pd.DataFrame, position: str) -> pd.DataFrame:
    """The per-bucket empirical ratio tables for `position` from out-of-fold rows (`pred`, `dk`) -- the
    "buckets-v1" format. `build_tables` takes P(actual = 0) from these; the bench compares against them.
    Only rows with a positive prediction have a defined ratio."""
    rows = oof[oof["pred"] > 0]
    ratio = rows["dk"] / rows["pred"]
    idx = bucket_index(position, rows["pred"])
    out = []
    for i in range(len(BUCKET_EDGES[position]) + 1):
        sel = idx == i
        if not sel.any():
            raise DistributionError(f"no rows in {position} bucket {bucket_label(position, i)}")
        edges = BUCKET_EDGES[position]
        record = {
            "position": position,
            "bucket": bucket_label(position, i),
            "lo": -np.inf if i == 0 else edges[i - 1],
            "hi": np.inf if i == len(edges) else edges[i],
            "n": int(sel.sum()),
            "center": float(rows.loc[sel, "pred"].mean()),
            "p_zero": float((rows.loc[sel, "dk"] == 0).mean()),
        }
        record.update(zip(LEVEL_COLUMNS, np.quantile(ratio[sel], LEVELS), strict=True))
        out.append(record)
    return pd.DataFrame(out)


def _spline_basis(x: np.ndarray, knots: np.ndarray) -> np.ndarray:
    """Linear spline in `x`: a constant, `x`, and one hinge `max(x - k, 0)` per knot."""
    return np.column_stack([np.ones_like(x), x, *(np.maximum(x - k, 0.0) for k in knots)])


def _quantile_fit(X: np.ndarray, y: np.ndarray, tau: float) -> np.ndarray:
    """Coefficients of the linear `tau`-quantile regression of `y` on the columns of `X`, exactly (no
    smoothing, no tolerance beyond the LP solver's): the Koenker-Bassett dual linear program. The dual has
    one bounded variable per row but only `X.shape[1]` equality constraints, which HiGHS solves in under a
    second for 19,000 rows; the primal form took 15 s for two coefficients. The coefficients are the
    equality constraints' marginals.

    The solution is checked against the defining property of a quantile fit -- no more than a `tau` share of
    the rows strictly below it and at least a `tau` share at or below it, up to the `k` rows it passes
    through -- so a solver whose sign convention differs fails loudly rather than shipping a wrong table."""
    n, k = X.shape
    res = linprog(-y, A_eq=X.T, b_eq=(1 - tau) * X.sum(axis=0), bounds=(0, 1), method="highs")
    if res.status != 0:
        raise DistributionError(f"quantile regression failed at level {tau}: {res.message}")
    beta = -np.asarray(res.eqlin.marginals, dtype=float)
    fit = X @ beta
    slack = (k + 1) / n
    if np.mean(y < fit - 1e-9) > tau + slack or np.mean(y <= fit + 1e-9) < tau - slack:
        raise DistributionError(f"quantile regression at level {tau} is not a {tau}-quantile fit")
    return beta


def build_tables(oof: pd.DataFrame, position: str) -> pd.DataFrame:
    """The ratio table for `position` from out-of-fold rows (`pred`, `dk`): "knots-v2", `N_KNOTS` rows.

    For every level, a quantile regression of `dk / pred` on a linear spline in log(pred) (knots at
    `SPLINE_KNOT_QUANTILES` of the projections), evaluated at `N_KNOTS` projections log-spaced between the
    `EDGE_PERCENTILES`. A fitted ratio quantile is never below zero (or below the lowest per-bucket quantile
    for a level that really goes negative: QB and DST), and each row is sorted across levels so the
    quantiles are monotone whatever the fits did. P(actual = 0) is the v1 bucket share, blended between
    bucket centres exactly as `_interpolated` does. Only rows with a positive prediction have a ratio."""
    base = build_bucket_tables(oof, position)  # also raises on an empty bucket
    rows = oof[oof["pred"] > 0]
    if len(rows) < MIN_FIT_ROWS:
        raise DistributionError(
            f"{position}: {len(rows)} rows is too few for a smooth fit (< {MIN_FIT_ROWS})"
        )
    pred = rows["pred"].to_numpy(dtype=float)
    ratio = (rows["dk"] / rows["pred"]).to_numpy(dtype=float)
    x = np.log(pred)
    inner = np.quantile(x, SPLINE_KNOT_QUANTILES)
    X = _spline_basis(x, inner)
    lo, hi = np.percentile(pred, EDGE_PERCENTILES)
    if not hi > lo:
        raise DistributionError(f"{position}: the projections have no spread")
    centers = np.geomspace(lo, hi, N_KNOTS)
    grid = _spline_basis(np.log(centers), inner)
    ratios = np.column_stack([grid @ _quantile_fit(X, ratio, tau) for tau in LEVELS])
    floor = np.minimum(base[LEVEL_COLUMNS].min().to_numpy(dtype=float), 0.0)
    ratios = np.sort(np.maximum(ratios, floor), axis=1)
    p_zero = np.interp(centers, base["center"].to_numpy(), base["p_zero"].to_numpy())
    # each knot's neighbourhood: the projections nearer to it than to the next knot (rows it speaks for)
    mids = np.sqrt(centers[:-1] * centers[1:])
    lo_edge = np.concatenate([[-np.inf], mids])
    hi_edge = np.concatenate([mids, [np.inf]])
    out = pd.DataFrame(ratios, columns=LEVEL_COLUMNS)
    out.insert(0, "p_zero", p_zero)
    out.insert(0, "center", centers)
    out.insert(0, "n", np.histogram(pred, bins=np.concatenate([[-np.inf], mids, [np.inf]]))[0])
    out.insert(0, "hi", hi_edge)
    out.insert(0, "lo", lo_edge)
    out.insert(0, "bucket", [f"{c:.2f}" for c in centers])
    out.insert(0, "position", position)
    return out


def table_metadata() -> dict:
    """What `metadata.json` records about how the stored tables were made."""
    return {
        "table_format": TABLE_FORMAT,
        "spline_knot_quantiles": list(SPLINE_KNOT_QUANTILES),
        "edge_percentiles": list(EDGE_PERCENTILES),
        "n_knots": N_KNOTS,
    }


def save_tables(tables: pd.DataFrame, directory: Path = ARTIFACT_DIR) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / TABLES_FILE
    tables.to_csv(path, index=False)
    load_tables.cache_clear()
    _shipped.cache_clear()
    return path


@lru_cache(maxsize=4)
def load_tables(directory: Path = ARTIFACT_DIR) -> pd.DataFrame:
    path = directory / TABLES_FILE
    if not path.exists():
        raise DistributionError(f"{path} not found -- run `dfs model train`")
    tables = pd.read_csv(path)
    missing = [
        c for c in ("position", "bucket", "center", "p_zero", *LEVEL_COLUMNS) if c not in tables.columns
    ]
    if missing:
        raise DistributionError(f"{path} is missing columns {missing} -- run `dfs model train`")
    return tables


@dataclass(frozen=True)
class _Compiled:
    """One position's table as arrays, sorted by bucket centre."""

    centers: np.ndarray
    ratios: np.ndarray  # buckets x LEVELS
    p_zero: np.ndarray


def _compile(position: str, tables: pd.DataFrame) -> _Compiled:
    sub = tables[tables["position"] == position].sort_values("center")
    if sub.empty:
        raise DistributionError(f"no distribution table for position {position!r}")
    return _Compiled(
        sub["center"].to_numpy(dtype=float),
        sub[LEVEL_COLUMNS].to_numpy(dtype=float),
        sub["p_zero"].to_numpy(dtype=float),
    )


@lru_cache(maxsize=8)
def _shipped(position: str) -> _Compiled:
    return _compile(position, load_tables())


def _table(position: str, tables: pd.DataFrame | None) -> _Compiled:
    return _shipped(position) if tables is None else _compile(position, tables)


def _interpolated(table: _Compiled, projections: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Ratio quantile vectors (rows x LEVELS) and zero shares at each projection: a linear blend of the
    two buckets whose centres (mean predicted value) bracket it; the first / last bucket's table beyond
    the end centres."""
    c = table.centers
    x = np.clip(projections, c[0], c[-1])
    hi = np.clip(np.searchsorted(c, x, side="right"), 1, len(c) - 1)
    lo = hi - 1
    w = ((x - c[lo]) / (c[hi] - c[lo]))[:, None]
    ratios = (1 - w) * table.ratios[lo] + w * table.ratios[hi]
    p_zero = (1 - w[:, 0]) * table.p_zero[lo] + w[:, 0] * table.p_zero[hi]
    return ratios, p_zero


def ratio_matrix(position: str, projections, *, tables: pd.DataFrame | None = None) -> np.ndarray:
    """The ratio quantile vector (rows x `LEVELS`) for each projection -- the vectorised core that the
    per-player functions and the calibration checks share. Projections must be > 0."""
    return _interpolated(_table(position, tables), np.asarray(projections, dtype=float))[0]


def outcome_distribution(
    position: str, projection: float, *, tables: pd.DataFrame | None = None
) -> Quantiles:
    """The outcome distribution for `projection` points at `position` (QB/RB/WR/TE/DST)."""
    levels = LEVELS
    if np.isnan(projection):
        return Quantiles(float("nan"), levels, (float("nan"),) * len(levels), float("nan"))
    if projection <= 0:
        return Quantiles(float(projection), levels, (0.0,) * len(levels), 1.0)
    ratios, p_zero = _interpolated(_table(position, tables), np.array([float(projection)]))
    return Quantiles(
        float(projection), levels, tuple(float(r) * projection for r in ratios[0]), float(p_zero[0])
    )


def _cdf_knots(ratios: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """(ratio, cumulative probability) knots: the stored quantiles, with the end segments extrapolated
    linearly out to probability 0 and 1 so the CDF is defined everywhere."""
    levels = np.asarray(LEVELS)
    lo_slope = (ratios[1] - ratios[0]) / (levels[1] - levels[0])
    hi_slope = (ratios[-1] - ratios[-2]) / (levels[-1] - levels[-2])
    lo = min(ratios[0] - lo_slope * levels[0], ratios[0])
    hi = max(ratios[-1] + hi_slope * (1 - levels[-1]), ratios[-1])
    return np.concatenate([[lo], ratios, [hi]]), np.concatenate([[0.0], levels, [1.0]])


def _cdf_at(ratios: np.ndarray, ratio: float) -> float:
    knots, cdf = _cdf_knots(ratios)
    return float(np.interp(ratio, knots, cdf))


def prob_at_least(
    position: str, projection: float, threshold_pts: float, *, tables: pd.DataFrame | None = None
) -> float:
    """P(actual points >= `threshold_pts`) for a player projected at `projection`, from the ratio CDF at
    `threshold / projection`."""
    return float(prob_at_least_many(position, [projection], [threshold_pts], tables=tables)[0])


def prob_at_least_many(
    position: str, projections, thresholds_pts, *, tables: pd.DataFrame | None = None
) -> np.ndarray:
    """`prob_at_least` over arrays; projections and thresholds broadcast against each other (a threshold per
    projection, one threshold for all, or one projection at several thresholds)."""
    proj, thr = np.broadcast_arrays(
        np.asarray(projections, dtype=float), np.asarray(thresholds_pts, dtype=float)
    )
    out = np.full(proj.shape, np.nan)
    valid = ~(np.isnan(proj) | np.isnan(thr))
    out[valid & (thr <= 0)] = 1.0
    out[valid & (thr > 0) & (proj <= 0)] = 0.0
    live = valid & (thr > 0) & (proj > 0)
    if position in INTEGER_SCORING:
        thr = np.where(thr > 0, np.ceil(thr - 1e-9) - 0.5, thr)
    if live.any():
        ratios = ratio_matrix(position, proj[live], tables=tables)
        out[live] = [1.0 - _cdf_at(r, t / p) for r, t, p in zip(ratios, thr[live], proj[live], strict=True)]
    return out


def floor_ceiling(
    position: str, projection: float, *, tables: pd.DataFrame | None = None
) -> tuple[float, float]:
    """(Floor, Ceiling): the 20th and 85th percentile of outcomes, in points."""
    q = outcome_distribution(position, projection, tables=tables)
    return q.at(FLOOR_LEVEL), q.at(CEILING_LEVEL)


def floor_ceiling_many(
    position: str, projections, *, tables: pd.DataFrame | None = None
) -> tuple[np.ndarray, np.ndarray]:
    """`floor_ceiling` over an array: (floors, ceilings), 0 for a projection <= 0, NaN for a NaN one."""
    proj = np.asarray(projections, dtype=float)
    floor, ceil = np.full(proj.shape, np.nan), np.full(proj.shape, np.nan)
    floor[proj <= 0] = ceil[proj <= 0] = 0.0
    live = proj > 0
    if live.any():
        ratios = ratio_matrix(position, proj[live], tables=tables)
        floor[live] = ratios[:, LEVELS.index(FLOOR_LEVEL)] * proj[live]
        ceil[live] = ratios[:, LEVELS.index(CEILING_LEVEL)] * proj[live]
    return floor, ceil
