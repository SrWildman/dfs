"""Cluster-robust means and mean differences. Player-games are not independent (the same player, the same
game), so every interval in this package is clustered: by game for game-level effects (weather, rest), by
player for signals that follow a player across weeks."""

from __future__ import annotations

import numpy as np
import pandas as pd

Z90 = 1.6449  # two-sided 90%


def _codes(cluster: pd.Series | np.ndarray) -> tuple[np.ndarray, int]:
    codes, uniques = pd.factorize(np.asarray(cluster))
    return codes, len(uniques)


def _se_from_scores(scores: np.ndarray, codes: np.ndarray, n_clusters: int) -> float:
    per_cluster = np.bincount(codes, weights=scores, minlength=n_clusters)
    g = max(n_clusters, 2)
    return float(np.sqrt(g / (g - 1) * np.sum(per_cluster**2)))


def cluster_mean(x: pd.Series | np.ndarray, cluster: pd.Series | np.ndarray) -> dict:
    """Mean of x with a cluster-robust 90% interval."""
    x = np.asarray(x, dtype=float)
    ok = ~np.isnan(x)
    x = x[ok]
    n = len(x)
    if n == 0:
        return {"n": 0, "mean": np.nan, "se": np.nan, "lo": np.nan, "hi": np.nan}
    codes, g = _codes(np.asarray(cluster)[ok])
    m = float(x.mean())
    se = _se_from_scores((x - m) / n, codes, g)
    return {"n": n, "mean": m, "se": se, "lo": m - Z90 * se, "hi": m + Z90 * se}


def cluster_diff(x: pd.Series | np.ndarray, flag: pd.Series | np.ndarray, cluster) -> dict:
    """Mean of x among flagged rows minus the mean among unflagged rows, with a cluster-robust 90%
    interval, the t statistic, and both group means. NaN when either group is empty."""
    x = np.asarray(x, dtype=float)
    f = np.asarray(flag, dtype=bool)
    ok = ~np.isnan(x)
    x, f = x[ok], f[ok]
    n1, n0 = int(f.sum()), int((~f).sum())
    out = {"n_flag": n1, "n_other": n0}
    if n1 == 0 or n0 == 0:
        return {**out, "mean_flag": np.nan, "mean_other": np.nan, "diff": np.nan, "se": np.nan} | {
            "lo": np.nan,
            "hi": np.nan,
            "t": np.nan,
        }
    m1, m0 = float(x[f].mean()), float(x[~f].mean())
    scores = np.where(f, (x - m1) / n1, -(x - m0) / n0)
    codes, g = _codes(np.asarray(cluster)[ok])
    se = _se_from_scores(scores, codes, g)
    diff = m1 - m0
    return {
        **out,
        "mean_flag": m1,
        "mean_other": m0,
        "diff": diff,
        "se": se,
        "lo": diff - Z90 * se,
        "hi": diff + Z90 * se,
        "t": diff / se if se > 0 else np.nan,
    }


def percentile_interval(x: pd.Series | np.ndarray, level: float = 0.90) -> tuple[float, float]:
    """The central `level` range of the values themselves (not an interval for the mean)."""
    x = np.asarray(x, dtype=float)
    x = x[~np.isnan(x)]
    if len(x) == 0:
        return (np.nan, np.nan)
    tail = (1 - level) / 2
    return float(np.quantile(x, tail)), float(np.quantile(x, 1 - tail))


def demean_within(frame: pd.DataFrame, value: str, by: list[str]) -> pd.Series:
    """`value` minus its mean within `by` groups (level shifts between positions and eras removed)."""
    return frame[value] - frame.groupby(by)[value].transform("mean")


def ols_cluster(x: np.ndarray, y: np.ndarray, cluster: np.ndarray) -> dict:
    """OLS of y on the columns of x (an intercept is added) with cluster-robust standard errors. Returns the
    coefficients (intercept first), their standard errors and the row count; rows with a NaN anywhere are
    dropped."""
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    ok = ~(np.isnan(x).any(axis=1) | np.isnan(y))
    x, y, cluster = x[ok], y[ok], np.asarray(cluster)[ok]
    xd = np.column_stack([np.ones(len(x)), x])
    bread = np.linalg.inv(xd.T @ xd)
    beta = bread @ xd.T @ y
    resid = y - xd @ beta
    codes, g = _codes(cluster)
    meat = np.zeros((xd.shape[1], xd.shape[1]))
    scores = xd * resid[:, None]
    for k in range(g):
        s = scores[codes == k].sum(axis=0)
        meat += np.outer(s, s)
    n, p = xd.shape
    adj = (g / max(g - 1, 1)) * ((n - 1) / max(n - p, 1))
    cov = adj * bread @ meat @ bread
    return {"coef": beta, "se": np.sqrt(np.diag(cov)), "n": int(n)}
