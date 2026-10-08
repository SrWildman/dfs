"""The flagged-versus-unflagged comparison every signal study shares (R3 signals, R5 quick checks).

An effect is the difference in mean residual (`actual - UM`, demeaned within position and season) between
flagged and unflagged player-games, with a cluster-robust 90% interval. A signal is judged by whether it
holds in BOTH the fit seasons (2014-2021) and the test seasons (2022-2025)."""

from __future__ import annotations

import numpy as np
import pandas as pd

from dfs.research.common import FIT_SEASONS, OFFENSE_POSITIONS
from dfs.research.stats import cluster_diff

MIN_EFFECT = 0.5  # points; the smallest test-period effect worth acting on


def add_split_and_demeaned(frame: pd.DataFrame) -> pd.DataFrame:
    """`split` (fit / test by season) and `resid_c`: the residual minus its mean within (position, season),
    so that a level shift between positions or eras is not mistaken for an effect."""
    out = frame.copy()
    out["split"] = np.where(out["season"].isin(FIT_SEASONS), "fit", "test")
    out["resid_c"] = out["resid"] - out.groupby(["position", "season"])["resid"].transform("mean")
    return out


def effect(f: pd.DataFrame, flag: pd.Series, direction: int, cluster: str = "gsis_id") -> dict:
    """Flagged minus unflagged mean `resid_c`, with a 90% interval clustered by `cluster`; the raw mean
    residuals of each group; and the hit rate (share of flagged rows that went the way the signal says:
    beat UM for an up signal, miss it for a down signal) against the unflagged hit rate."""
    flag = flag.reset_index(drop=True)
    f = f.reset_index(drop=True)
    stat = cluster_diff(f["resid_c"].to_numpy(), flag.to_numpy(), f[cluster].to_numpy())
    resid = f["resid"].to_numpy()
    wins = (resid > 0) if direction > 0 else (resid < 0)
    mask = flag.to_numpy()
    return {
        "n_flagged": int(stat["n_flag"]),
        "n_other": int(stat["n_other"]),
        "mean_resid_flagged": float(resid[mask].mean()) if mask.any() else np.nan,
        "mean_resid_unflagged": float(resid[~mask].mean()) if (~mask).any() else np.nan,
        "diff": stat["diff"],
        "lo90": stat["lo"],
        "hi90": stat["hi"],
        "t": stat["t"],
        "hit_rate_flagged": float(wins[mask].mean()) if mask.any() else np.nan,
        "hit_rate_unflagged": float(wins[~mask].mean()) if (~mask).any() else np.nan,
    }


def evaluate(f: pd.DataFrame, flag: pd.Series, direction: int, cluster: str = "gsis_id") -> dict:
    """`effect` in the fit and the test seasons, flagged rows per season, and the by-position breakdown."""
    f = f.reset_index(drop=True)
    flag = flag.reset_index(drop=True)
    res = {}
    for name in ("fit", "test"):
        sel = (f["split"] == name).to_numpy()
        res[name] = effect(f[sel], flag[sel], direction, cluster)
    per_season = flag.groupby(f["season"]).sum()
    res["n_per_season"] = {int(k): int(v) for k, v in per_season.items()}
    by_pos = {}
    for pos in (*OFFENSE_POSITIONS, "DST"):
        in_pos = (f["position"] == pos).to_numpy()
        if not flag[in_pos].any():
            continue
        by_pos[pos] = {
            name: effect(
                f[in_pos & (f["split"] == name).to_numpy()],
                flag[in_pos & (f["split"] == name).to_numpy()],
                direction,
                cluster,
            )
            for name in ("fit", "test")
        }
    res["by_position"] = by_pos
    return res


def verdict(res: dict, direction: int, min_effect: float = MIN_EFFECT) -> dict:
    """`keep`, `borderline` or `drop`, with the reasons.

    keep        the expected sign in both periods, a test effect of at least `min_effect` points, and a test
                90% interval that excludes zero.
    borderline  the expected sign in both periods and a test interval that excludes zero, but a test effect
                below `min_effect` -- real but small: context, not a flag that should move a lineup.
    drop        anything else."""
    fit, test = res["fit"], res["test"]
    reasons = []
    right_fit = direction * fit["diff"] > 0
    if not right_fit:
        reasons.append("fit-period effect has the wrong sign")
    wrong_test_sign = not (direction * test["diff"] > 0)
    if wrong_test_sign:
        reasons.append("test-period effect has the wrong sign")
    interval_excludes_zero = bool(test["lo90"] > 0 or test["hi90"] < 0)
    if not interval_excludes_zero:
        reasons.append("test 90% interval includes zero")
    too_small = (not wrong_test_sign) and direction * test["diff"] < min_effect
    if too_small:
        reasons.append(f"test effect {test['diff']:+.2f} pts is smaller than {min_effect}")
    if not reasons:
        return {"recommendation": "keep", "reasons": []}
    if right_fit and interval_excludes_zero and reasons == [reasons[-1]] and too_small:
        return {"recommendation": "borderline", "reasons": reasons}
    return {"recommendation": "drop", "reasons": reasons}
