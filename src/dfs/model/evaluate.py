"""Calibration checks for the distribution engine, run on out-of-fold predictions, and their acceptance rule.

Two checks per position:

- Coverage: the realized share of games below the engine's 20th percentile and above its 85th. Target 20% and
  15%; accepted within +/-3 points. A quantile that lands on an atom (a cheap WR's 20th percentile is
  exactly 0 points because 22% of such games score 0) has no single "share below": it is the INTERVAL
  [P(actual < q), P(actual <= q)], and the check is that the target lies within 3 points of that interval.
- Reliability: salaries are not in this data, so P(actual >= T) is checked for T at the 25th, 50th and 75th
  percentile of the position's actual points. Rows are cut into ten equal-count bins by predicted
  probability and each bin's mean predicted probability is compared with the rate at which it was realized.
  Accepted if every bin is within +/-4 points. Each bin also reports its binomial standard error, because
  a ten-bin table over a few thousand games can miss by 4 points from sampling noise alone.

`out_of_time` re-runs both with tables built from the early seasons only and scored on the later ones: the
spec's check measures the tables on the same games that built them, this one measures them on games they
never saw.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from dfs.model import distribution as dist

COVERAGE_TOL = 0.03
RELIABILITY_TOL = 0.04
RELIABILITY_PCTS = (25, 50, 75)
N_BINS = 10


def quantile_points(position: str, preds: np.ndarray, level: float, tables: pd.DataFrame) -> np.ndarray:
    """The engine's `level` quantile in points for each prediction (0 for a projection <= 0)."""
    preds = np.asarray(preds, dtype=float)
    out = np.zeros(len(preds))
    live = preds > 0
    if live.any():
        col = list(dist.LEVELS).index(level)
        out[live] = dist.ratio_matrix(position, preds[live], tables=tables)[:, col] * preds[live]
    return out


def within(lo: float, hi: float, target: float, tol: float) -> bool:
    """Is `target` within `tol` of the interval [lo, hi]?"""
    return lo <= target + tol and hi >= target - tol


def coverage(oof: pd.DataFrame, position: str, tables: pd.DataFrame) -> dict:
    """Realized share below the 20th and above the 85th percentile, strict and inclusive, and the verdict."""
    dk = oof["dk"].to_numpy()
    q20 = quantile_points(position, oof["pred"].to_numpy(), dist.FLOOR_LEVEL, tables)
    q85 = quantile_points(position, oof["pred"].to_numpy(), dist.CEILING_LEVEL, tables)
    below = (float(np.mean(dk < q20)), float(np.mean(dk <= q20)))
    above = (float(np.mean(dk > q85)), float(np.mean(dk >= q85)))
    return {
        "n": len(oof),
        "below_q20": below,
        "above_q85": above,
        "ok": within(*below, 1 - 0.80, COVERAGE_TOL) and within(*above, 1 - 0.85, COVERAGE_TOL),
    }


def reliability(
    oof: pd.DataFrame, position: str, tables: pd.DataFrame, pcts: tuple[int, ...] = RELIABILITY_PCTS
) -> pd.DataFrame:
    """One row per (threshold, bin): `threshold` label, `T` points, `bin`, `n`, `mean_p`, `realized`,
    `gap_pts` (realized - predicted), `se_pts` (binomial standard error of the realized rate)."""
    frames = []
    for pct in pcts:
        threshold = float(np.percentile(oof["dk"], pct))
        p = dist.prob_at_least_many(position, oof["pred"].to_numpy(), threshold, tables=tables)
        hit = (oof["dk"].to_numpy() >= threshold).astype(float)
        bins = pd.qcut(pd.Series(p).rank(method="first"), N_BINS, labels=False)
        g = (
            pd.DataFrame({"p": p, "hit": hit, "bin": bins.to_numpy() + 1})
            .groupby("bin")
            .agg(n=("hit", "size"), mean_p=("p", "mean"), realized=("hit", "mean"))
            .reset_index()
        )
        g["gap_pts"] = (g["realized"] - g["mean_p"]) * 100
        g["se_pts"] = (
            np.sqrt(g["realized"].clip(0.01, 0.99) * (1 - g["realized"].clip(0.01, 0.99)) / g["n"]) * 100
        )
        g.insert(0, "T", threshold)
        g.insert(0, "threshold", f"p{pct}")
        frames.append(g)
    return pd.concat(frames, ignore_index=True)


def reliability_ok(table: pd.DataFrame) -> bool:
    return bool((table["gap_pts"].abs() <= RELIABILITY_TOL * 100).all())


def out_of_time(oof: pd.DataFrame, position: str, split_season: int) -> dict:
    """Tables built from seasons <= `split_season`, measured on the seasons after it."""
    early, late = oof[oof["season"] <= split_season], oof[oof["season"] > split_season]
    tables = dist.build_tables(early, position)
    rel = reliability(late, position, tables)
    return {"coverage": coverage(late, position, tables), "reliability": rel}


def summarize(position: str, cov: dict, rel: pd.DataFrame) -> dict:
    """The one-line verdicts: where it fails, if it does."""
    bad = rel[rel["gap_pts"].abs() > RELIABILITY_TOL * 100]
    return {
        "position": position,
        "coverage_ok": cov["ok"],
        "reliability_ok": reliability_ok(rel),
        "max_abs_gap_pts": float(rel["gap_pts"].abs().max()),
        "bins_over_tol": int(len(bad)),
        "bins": int(len(rel)),
        "bins_over_2se": int((rel["gap_pts"].abs() > 2 * rel["se_pts"]).sum()),
    }
