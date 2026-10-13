"""Seeded simulation behind the three-way comparison quoted in docs/CONTESTS.md (run: `python
tests/contests/simulate_compare.py`). There is no real history in the cloud, so this is the only evidence for
how the week treatments behave; the first real run reports its own numbers in `toughness_models.csv`.

The world: 4 seasons x 18 weeks; each week ~10-15 Classic contests (eight series across three formats) with
known series levels; a week effect with sd 1.0 on the logit (about 20 points, the size of the swing in Sam's
typed cash lines); a slate index that is unrelated to the week effect (what the five typed cash lines
showed: r about 0.1). `--index-matters` instead makes the points scale with the index.
"""

from __future__ import annotations

import sys
import time

import numpy as np
import pandas as pd

from dfs.contests import toughness as T

SPEC = {
    "SE GPP": {"A": 0.6, "B": -0.4, "C": 0.0},
    "N-max GPP": {"D": 0.3, "E": -0.3},
    "Double Up": {"F": 0.2, "G": -0.2, "H": 0.0},
}
ALPHA = {"SE GPP": 7.5, "N-max GPP": 7.0, "Double Up": 3.0}
SLOPE = {"SE GPP": -0.05, "N-max GPP": -0.05, "Double Up": -0.02}
PAID = {"SE GPP": 0.2, "N-max GPP": 0.2, "Double Up": 0.45}


def world(seed: int = 3, week_sd: float = 1.0, index_matters: bool = False):
    rng = np.random.default_rng(seed)
    rows, index = [], []
    for season in range(2022, 2026):
        for week in range(1, 19):
            shock = rng.normal(0, week_sd)
            scale = float(np.exp(rng.normal(0, 0.07)))
            index.append((season, week, scale))
            for fmt, series in SPEC.items():
                for name, delta in series.items():
                    if rng.random() < 0.3:
                        continue
                    x_true = rng.normal(150, 22)
                    y = ALPHA[fmt] + delta + SLOPE[fmt] * x_true + rng.normal(0, 0.35)
                    y += 0.0 if index_matters else shock
                    entries = 3000
                    place = int(np.clip(round(1 / (1 + np.exp(-y)) * entries + 0.5), 1, entries))
                    points = x_true * scale if index_matters else x_true
                    rows.append(
                        {
                            "format": fmt,
                            "series": name,
                            "season": season,
                            "week": week,
                            "contest_key": f"{name}{season}{week}",
                            "slate": "main",
                            "place": place,
                            "entries": entries,
                            "places_paid": int(PAID[fmt] * entries),
                            "points": round(points, 2),
                        }
                    )
    log = pd.DataFrame(rows)
    for c in ("place", "entries", "places_paid", "season", "week"):
        log[c] = log[c].astype("Int64")
    log["points"] = log["points"].astype("Float64")
    return log, pd.DataFrame(index, columns=["season", "week", "index"])


if __name__ == "__main__":
    matters = "--index-matters" in sys.argv
    log, index = world(index_matters=matters)
    started = time.time()
    res = T.fit_toughness(log, index)
    print(f"{len(log)} entries, {time.time() - started:.1f}s; index_matters={matters}")
    print(res.comparison.round(4).to_string(index=False))
    print(f"chosen: {res.mode} (k={res.k:g}, k_w={res.k_w:g})")
    weeks = res.weeks_table
    print(
        f"week effects: sd {weeks['w'].std():.2f} logit, corr with the slate index "
        f"{weeks[['w', 'slate_index']].corr().iloc[0, 1]:+.2f}"
    )
