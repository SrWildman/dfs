"""Prior-only rolling statistics: the one place a feature is allowed to look backwards in time.

Every function returns, for each row, a statistic of that key's EARLIER rows only (`shift(1)` before the
window), never the row's own game. Windows count games played and continue across the season boundary, as
`dfs.model.features` does. `tests/research/test_rolling.py` pins the property: changing or appending a row's
own value, or any later row's, leaves its feature unchanged.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

BLEND_PRIOR_GAMES = 6  # the previous season counts as this many games of evidence early in a season


def _sorted(frame: pd.DataFrame, key: list[str]) -> pd.DataFrame:
    return frame.sort_values([*key, "t"], kind="mergesort")


def prior_mean(frame: pd.DataFrame, key: list[str], col: str, window: int, min_periods: int = 1) -> pd.Series:
    """Mean of `col` over the previous `window` games of the same key (fewer if fewer exist, at least
    `min_periods`), aligned to `frame`'s index."""
    s = _sorted(frame, key)
    out = s.groupby(key, sort=False)[col].transform(
        lambda x: x.shift(1).rolling(window, min_periods=min_periods).mean()
    )
    return out.reindex(frame.index)


def prior_blend(frame: pd.DataFrame, key: list[str], col: str, k: int = BLEND_PRIOR_GAMES) -> pd.Series:
    """Season-to-date mean of the games before this one, shrunk toward last season's mean by `k` games of
    evidence: `(sum_cur + k * prev) / (n_cur + k)`. Early in a season it is mostly last year; by week 12 it
    is mostly this year. No previous season (the first one) leaves just the season to date; neither leaves
    NaN."""
    s = _sorted(frame, key)
    g = s.groupby([*key, "season"], sort=False)[col]
    cur_sum = g.transform(lambda x: x.shift(1).expanding().sum()).fillna(0.0)
    cur_n = g.transform(lambda x: x.shift(1).expanding().count()).fillna(0.0)
    season_mean = s.groupby([*key, "season"], as_index=False)[col].mean()
    season_mean["season"] = season_mean["season"] + 1
    prev = s[[*key, "season"]].merge(season_mean, on=[*key, "season"], how="left")[col].to_numpy()
    num = cur_sum.to_numpy() + np.where(np.isnan(prev), 0.0, k * np.nan_to_num(prev))
    den = cur_n.to_numpy() + np.where(np.isnan(prev), 0.0, float(k))
    out = pd.Series(np.where(den > 0, num / np.where(den > 0, den, 1.0), np.nan), index=s.index)
    return out.reindex(frame.index)


LOOKBACKS = ("l4", "l8", "blend")


def prior_stat(frame: pd.DataFrame, key: list[str], col: str, lookback: str) -> pd.Series:
    """`col` as of before each game under one of `LOOKBACKS`: the last 4 games, the last 8, or the
    season-plus-prior blend."""
    if lookback == "blend":
        return prior_blend(frame, key, col)
    window = {"l4": 4, "l8": 8}[lookback]
    return prior_mean(frame, key, col, window, min_periods=max(1, window // 2))
