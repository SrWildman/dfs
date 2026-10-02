"""The questions the results loop answers (3a-3e), as pure functions over the scored table.

Input is the concatenation of `data/results/scored_*.csv` (`results_loop.SCORED_COLUMNS`). Every
table carries **n**; any cell with n < `THIN_N` (30) is marked thin; a rate carries a 90% Wilson
interval; and where there is not enough data the answer is "not enough data yet" rather than a
conclusion. Nothing here reads an archived flag: the `Flags` column in the scored table was
recomputed from raw snapshots with today's code (`results_loop.rebuild_week_frame`).

Two populations, both from that week's own snapshot: the **rosterable pool** (the top
`derived.VAL_ADJ_ROSTERABLE_TOP_N` per position by `ProjPts`) and **everyone with `ProjPts` > 0**.
Only players with a real stat line (`Status == "scored"`) are used; a `dnp` is shown as a count, not
as an actual of 0, because a player who did not play was not a projection miss Sam could act on.
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd

THIN_N = 30
Z_90 = 1.6449  # two-sided 90%
POSITIONS = ["QB", "RB", "WR", "TE", "DST"]
PROJECTION_BUCKETS = [(0, 5), (5, 10), (10, 15), (15, 20), (20, math.inf)]
# Salary tiers (DK $): chosen once so every table reads the same; a cheap-RB question is "tier 1".
SALARY_TIERS = [(0, 4500), (4500, 6000), (6000, 7500), (7500, 9000), (9000, math.inf)]
VAL_BANDS = [(0, 2.0), (2.0, 2.5), (2.5, 3.0), (3.0, 3.5), (3.5, math.inf)]
PINBALL_TAUS = (0.80, 0.85, 0.90)
REPORT_FLAGS = ["LEVERAGE", "CHALK", "TFFB↑", "TFFB↓", "IMPL↑", "IMPL↓", "WIND"]
MIN_WEEKS_FOR_CONSISTENCY = 4
NOT_ENOUGH = "not enough data yet"


def is_thin(n: int) -> bool:
    """A sample under `THIN_N` (30) is thin: shown, but not read as an answer."""
    return n < THIN_N


def usable(scored: pd.DataFrame, population: str = "pool") -> pd.DataFrame:
    """Players with a real stat line and a positive projection, in the chosen population
    (`"pool"` = rosterable pool, `"all"` = everyone with ProjPts > 0)."""
    df = scored.copy()
    for column in ("ProjPts", "DkActual", "Ceiling", "Salary", "Val", "ValAdj", "AggPts"):
        if column in df.columns:
            df[column] = pd.to_numeric(df[column], errors="coerce")
    df = df[(df["Status"] == "scored") & df["DkActual"].notna() & (df["ProjPts"] > 0)]
    if population == "pool":
        df = df[df["RosterablePool"].astype(bool)]
    return df


def wilson_interval(successes: int, n: int, z: float = Z_90) -> tuple[float, float]:
    """Wilson score interval for a rate (default 90%). (nan, nan) when n is 0."""
    if n == 0:
        return (float("nan"), float("nan"))
    p = successes / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return (max(0.0, centre - half), min(1.0, centre + half))


def _spearman(a: pd.Series, b: pd.Series) -> float:
    """Spearman rank correlation: the Pearson correlation of the (tie-averaged) ranks. Computed
    directly so no scipy dependency is needed."""
    pair = pd.DataFrame({"a": a, "b": b}).dropna()
    if len(pair) < 3 or pair["a"].nunique() < 2 or pair["b"].nunique() < 2:
        return float("nan")
    return float(pair["a"].rank().corr(pair["b"].rank()))


def _slope_r2(x: pd.Series, y: pd.Series) -> tuple[float, float]:
    pair = pd.DataFrame({"x": x, "y": y}).dropna()
    if len(pair) < 3 or pair["x"].nunique() < 2:
        return (float("nan"), float("nan"))
    slope = float(np.polyfit(pair["x"], pair["y"], 1)[0])
    r = float(np.corrcoef(pair["x"], pair["y"])[0, 1])
    return (slope, r * r)


def _bucket_label(low: float, high: float, unit: str = "") -> str:
    """A band label with an EN DASH. A hyphen ("5-10") is parsed by Sheets as a date (May 10)."""
    if math.isinf(high):
        return f"{low:g}+{unit}"
    return f"{low:g}\u2013{high:g}{unit}"


def _band(series: pd.Series, bands: list[tuple[float, float]]) -> pd.Series:
    labels = pd.Series(pd.NA, index=series.index, dtype="object")
    for low, high in bands:
        labels[(series >= low) & (series < high)] = _bucket_label(low, high)
    return labels


# ---------------------------------------------------------------------------------------------
# 3a. Projection accuracy
# ---------------------------------------------------------------------------------------------


def accuracy_by_position(scored: pd.DataFrame, population: str = "pool") -> pd.DataFrame:
    """Per position (and "All"): n, bias (mean actual - projected), MAE, calibration slope (OLS of
    actual on projected; 1.0 is well calibrated, below 1 means the projections are too extreme),
    R squared and Spearman rank correlation (does the projection ORDER players correctly?)."""
    df = usable(scored, population)
    rows = []
    for position in [*POSITIONS, "All"]:
        part = df if position == "All" else df[df["Position"] == position]
        if part.empty:
            continue
        error = part["DkActual"] - part["ProjPts"]
        slope, r2 = _slope_r2(part["ProjPts"], part["DkActual"])
        rows.append(
            {
                "Position": position,
                "n": len(part),
                "Bias": round(float(error.mean()), 2),
                "MAE": round(float(error.abs().mean()), 2),
                "Slope": round(slope, 2) if not math.isnan(slope) else float("nan"),
                "R2": round(r2, 2) if not math.isnan(r2) else float("nan"),
                "Spearman": round(_spearman(part["ProjPts"], part["DkActual"]), 2),
                "Thin": is_thin(len(part)),
            }
        )
    return pd.DataFrame(rows)


def calibration_buckets(scored: pd.DataFrame, population: str = "pool") -> pd.DataFrame:
    """When the projection says 0-5 / 5-10 / 10-15 / 15-20 / 20+, what was the mean actual?"""
    df = usable(scored, population)
    df = df.assign(Bucket=_band(df["ProjPts"], PROJECTION_BUCKETS))
    rows = []
    for low, high in PROJECTION_BUCKETS:
        part = df[df["Bucket"] == _bucket_label(low, high)]
        if part.empty:
            continue
        rows.append(
            {
                "Projected": _bucket_label(low, high),
                "n": len(part),
                "MeanProjected": round(float(part["ProjPts"].mean()), 2),
                "MeanActual": round(float(part["DkActual"].mean()), 2),
                "Thin": is_thin(len(part)),
            }
        )
    return pd.DataFrame(rows)


def accuracy_by_salary_tier(scored: pd.DataFrame, population: str = "pool") -> pd.DataFrame:
    """Bias and MAE by position x salary tier -- "does TFFB run high on cheap RBs?"."""
    df = usable(scored, population)
    df = df.assign(Tier=_band(df["Salary"], SALARY_TIERS))
    rows = []
    for position in POSITIONS:
        for low, high in SALARY_TIERS:
            part = df[(df["Position"] == position) & (df["Tier"] == _bucket_label(low, high))]
            if part.empty:
                continue
            error = part["DkActual"] - part["ProjPts"]
            rows.append(
                {
                    "Position": position,
                    "Salary": _bucket_label(low, high),
                    "n": len(part),
                    "Bias": round(float(error.mean()), 2),
                    "MAE": round(float(error.abs().mean()), 2),
                    "Thin": is_thin(len(part)),
                }
            )
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------------------------
# 3a/3e. Sources compared
# ---------------------------------------------------------------------------------------------


def source_comparison(scored: pd.DataFrame, population: str = "pool") -> tuple[pd.DataFrame, dict]:
    """TFFB `ProjPts`, Sleeper, FantasyPros and `AggPts` on the rows where ALL THREE sources exist
    (today: Week 3 only -- the table says which weeks). Per source: n, MAE, bias, Spearman within
    position (averaged over positions, weighted by n). Also the head-to-head: for each player, did
    `AggPts` or TFFB land closer to the actual score? Returns (table, head_to_head dict)."""
    df = usable(scored, population)
    for column in ("SleeperPts", "FantasyProsPts"):
        df[column] = pd.to_numeric(df[column], errors="coerce")
    both = df.dropna(subset=["SleeperPts", "FantasyProsPts"])
    both = both[both["SleeperPts"] > 0]
    weeks = sorted(int(w) for w in both["week"].unique()) if not both.empty else []
    if both.empty:
        return pd.DataFrame(), {"weeks": [], "note": NOT_ENOUGH}
    rows = []
    for label, column in (
        ("TFFB (ProjPts)", "ProjPts"),
        ("Sleeper", "SleeperPts"),
        ("FantasyPros", "FantasyProsPts"),
        ("AggPts", "AggPts"),
    ):
        error = both["DkActual"] - both[column]
        per_position = [
            (len(g), _spearman(g[column], g["DkActual"])) for _, g in both.groupby("Position") if len(g) >= 5
        ]
        weights = [n for n, rho in per_position if not math.isnan(rho)]
        spearman = (
            float(np.average([rho for n, rho in per_position if not math.isnan(rho)], weights=weights))
            if weights
            else float("nan")
        )
        rows.append(
            {
                "Source": label,
                "n": len(both),
                "MAE": round(float(error.abs().mean()), 2),
                "Bias": round(float(error.mean()), 2),
                "Spearman": round(spearman, 3),  # three decimals: the four sources differ in the third
                "Thin": is_thin(len(both)),
            }
        )
    agg_err = (both["DkActual"] - both["AggPts"]).abs()
    tffb_err = (both["DkActual"] - both["ProjPts"]).abs()
    decided = agg_err != tffb_err
    wins = int((agg_err < tffb_err).sum())
    n = int(decided.sum())
    lo, hi = wilson_interval(wins, n)
    head_to_head = {
        "weeks": weeks,
        "n": n,
        "agg_wins": wins,
        "win_rate": round(wins / n, 3) if n else float("nan"),
        "low": round(lo, 3) if n else float("nan"),
        "high": round(hi, 3) if n else float("nan"),
        "thin": is_thin(n),
    }
    return pd.DataFrame(rows), head_to_head


# ---------------------------------------------------------------------------------------------
# 3b. What Ceiling means
# ---------------------------------------------------------------------------------------------


def pinball_loss(actual: pd.Series, quantile_prediction: pd.Series, tau: float) -> float:
    """Pinball (quantile) loss of `quantile_prediction` as the `tau`-quantile of `actual`. This, not
    RMSE, is the right score for a quantile: RMSE would call a well-calibrated ceiling "bad" for not
    matching the average outcome."""
    diff = actual - quantile_prediction
    return float(np.maximum(tau * diff, (tau - 1) * diff).mean())


def pinball_skill(actual: pd.Series, ceiling: pd.Series, tau: float) -> float:
    """1 - (pinball loss of `ceiling` as the tau-quantile) / (loss of the best CONSTANT tau-quantile).

    Raw pinball loss shrinks as tau rises (the loss scale is ~tau(1-tau)), so comparing raw losses across
    tau levels always favours the highest one and says nothing about calibration. Skill against a
    constant-quantile baseline is scale-free: it is highest at the tau where `ceiling` is actually
    behaving like that quantile. 0 = no better than ignoring the player, 1 = perfect."""
    baseline = pinball_loss(actual, pd.Series(float(np.quantile(actual, tau)), index=actual.index), tau)
    if baseline <= 0:
        return float("nan")
    return 1.0 - pinball_loss(actual, ceiling, tau) / baseline


def ceiling_report(scored: pd.DataFrame, population: str = "pool") -> pd.DataFrame:
    """Per position and overall: the share whose actual score beat their published `Ceiling`, its 90%
    interval, the implied quantile (1 - hit rate), and Ceiling scored as the 0.80 / 0.85 / 0.90
    quantile with pinball loss (never RMSE: a quantile is not a mean). `BestTau` is the level with the
    highest pinball SKILL (see `pinball_skill`), not the lowest raw loss. A hit rate near 15% would mean
    an ~85th percentile -- FantasyLabs' own definition is the hypothesis, not an assumption."""
    df = usable(scored, population)
    df = df[df["Ceiling"].notna() & (df["Ceiling"] > 0)]
    rows = []
    for position in [*POSITIONS, "All"]:
        part = df if position == "All" else df[df["Position"] == position]
        if part.empty:
            continue
        hits = int((part["DkActual"] > part["Ceiling"]).sum())
        n = len(part)
        lo, hi = wilson_interval(hits, n)
        losses = {tau: pinball_loss(part["DkActual"], part["Ceiling"], tau) for tau in PINBALL_TAUS}
        skills = {tau: pinball_skill(part["DkActual"], part["Ceiling"], tau) for tau in PINBALL_TAUS}
        best = max(skills, key=lambda tau: -math.inf if math.isnan(skills[tau]) else skills[tau])
        rows.append(
            {
                "Position": position,
                "n": n,
                "HitRate": round(hits / n, 3),
                "Low90": round(lo, 3),
                "High90": round(hi, 3),
                "ImpliedQuantile": round(1 - hits / n, 3),
                **{f"Pinball{int(tau * 100)}": round(loss, 3) for tau, loss in losses.items()},
                **{f"Skill{int(tau * 100)}": round(skill, 3) for tau, skill in skills.items()},
                "BestTau": best,
                "Thin": is_thin(n),
            }
        )
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------------------------
# 3c. Does ValAdj point at players who beat their salary?
# ---------------------------------------------------------------------------------------------


def salary_residuals(scored: pd.DataFrame) -> pd.DataFrame:
    """Rosterable-pool players with `Residual` = actual - salary-expected points, where
    "salary-expected" is fit on ACTUAL points against salary, per position, across every week
    available (7.2's "version 2" seam). A position with fewer than 5 rows gets no residual."""
    df = usable(scored, "pool").copy()
    df["Residual"] = float("nan")
    for _position, part in df.groupby("Position"):
        if len(part) < 5 or part["Salary"].nunique() < 2:
            continue
        slope, intercept = np.polyfit(part["Salary"], part["DkActual"], 1)
        df.loc[part.index, "Residual"] = part["DkActual"] - (slope * part["Salary"] + intercept)
    return df


def valadj_quintiles(scored: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """Quintiles of `ValAdj` WITHIN each position (so a QB is only ranked against QBs), pooled across
    positions: per quintile n and the mean of actual minus salary-expected points. Also the rank
    correlation of `ValAdj` with that residual, overall and per position."""
    df = salary_residuals(scored)
    df = df[df["Residual"].notna() & df["ValAdj"].notna()].copy()
    if df.empty:
        return pd.DataFrame(), {"note": NOT_ENOUGH}
    df["Quintile"] = (
        df.groupby("Position")["ValAdj"].rank(pct=True, method="first").mul(5).apply(math.ceil).clip(1, 5)
    )
    rows = []
    for q in range(1, 6):
        part = df[df["Quintile"] == q]
        if part.empty:
            continue
        rows.append(
            {
                "Quintile": f"Q{q}"
                + (" (lowest ValAdj)" if q == 1 else " (highest ValAdj)" if q == 5 else ""),
                "n": len(part),
                "MeanResidual": round(float(part["Residual"].mean()), 2),
                "Thin": is_thin(len(part)),
            }
        )
    rho = {"All": round(_spearman(df["ValAdj"], df["Residual"]), 2), "n": len(df)}
    for position, part in df.groupby("Position"):
        rho[position] = round(_spearman(part["ValAdj"], part["Residual"]), 2)
    return pd.DataFrame(rows), rho


# ---------------------------------------------------------------------------------------------
# 3d. Flags (reported, not judged)
# ---------------------------------------------------------------------------------------------


def flag_report(scored: pd.DataFrame) -> pd.DataFrame:
    """Per flag (recomputed with today's code, never read from an archive): the count, the mean of
    actual minus projected, and the unflagged comparison group at the SAME positions. Reported, not
    judged -- almost all of it is thin this early."""
    df = usable(scored, "pool").copy()
    df["Error"] = df["DkActual"] - df["ProjPts"]
    tokens = df["Flags"].fillna("").astype(str).str.split()
    rows = []
    for flag in REPORT_FLAGS:
        has = tokens.map(lambda t, f=flag: f in t)
        flagged = df[has]
        n = len(flagged)
        if n == 0:
            rows.append(
                {
                    "Flag": flag,
                    "n": 0,
                    "MeanError": float("nan"),
                    "UnflaggedN": 0,
                    "UnflaggedMean": float("nan"),
                    "Thin": True,
                }
            )
            continue
        positions = set(flagged["Position"])
        any_flag = tokens.map(lambda t: any(f in t for f in REPORT_FLAGS))
        comparison = df[~any_flag & df["Position"].isin(positions)]
        unflagged_mean = round(float(comparison["Error"].mean()), 2) if len(comparison) else float("nan")
        rows.append(
            {
                "Flag": flag,
                "n": n,
                "MeanError": round(float(flagged["Error"].mean()), 2),
                "UnflaggedN": len(comparison),
                "UnflaggedMean": unflagged_mean,
                "Thin": is_thin(n),
            }
        )
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------------------------
# 3e. Salary-multiple hit rates
# ---------------------------------------------------------------------------------------------


def salary_multiple_hits(scored: pd.DataFrame) -> pd.DataFrame:
    """The share of players whose ACTUAL points reached 3x (the cash line) and 4x (the GPP line)
    their salary per $1,000, grouped by PROJECTED multiple (`Val`) in bands. Tests the `Val >= 3.0`
    cash filter directly."""
    df = usable(scored, "pool")
    df = df[df["Val"].notna() & (df["Salary"] > 0)]
    df = df.assign(Band=_band(df["Val"], VAL_BANDS))
    multiple = df["DkActual"] / (df["Salary"] / 1000.0)
    rows = []
    for low, high in VAL_BANDS:
        label = _bucket_label(low, high)
        part = df[df["Band"] == label]
        if part.empty:
            continue
        m = multiple[part.index]
        hit3, hit4 = int((m >= 3).sum()), int((m >= 4).sum())
        lo3, hi3 = wilson_interval(hit3, len(part))
        rows.append(
            {
                "ProjectedVal": label,
                "n": len(part),
                "Hit3x": round(hit3 / len(part), 3),
                "Hit3xLow": round(lo3, 3),
                "Hit3xHigh": round(hi3, 3),
                "Hit4x": round(hit4 / len(part), 3),
                "Thin": is_thin(len(part)),
            }
        )
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------------------------
# 3e. Consistency over time
# ---------------------------------------------------------------------------------------------


def consistency(scored: pd.DataFrame) -> pd.DataFrame | str:
    """Each source's week-by-week MAE and its coefficient of variation, once there are 4 or more
    scored weeks; until then the string "needs 4+ weeks"."""
    df = usable(scored, "pool")
    weeks = sorted(df["week"].unique())
    if len(weeks) < MIN_WEEKS_FOR_CONSISTENCY:
        return f"needs {MIN_WEEKS_FOR_CONSISTENCY}+ weeks"
    rows = []
    for label, column in (
        ("TFFB (ProjPts)", "ProjPts"),
        ("Sleeper", "SleeperPts"),
        ("FantasyPros", "FantasyProsPts"),
        ("AggPts", "AggPts"),
    ):
        values = pd.to_numeric(df[column], errors="coerce")
        part = df.assign(_err=(df["DkActual"] - values).abs()).dropna(subset=["_err"])
        weekly = part.groupby("week")["_err"].mean()
        if len(weekly) < 2:
            continue
        rows.append(
            {
                "Source": label,
                "Weeks": len(weekly),
                "MeanMAE": round(float(weekly.mean()), 2),
                "CV": round(float(weekly.std() / weekly.mean()), 3),
            }
        )
    return pd.DataFrame(rows)


def dnp_counts(scored: pd.DataFrame) -> dict[str, int]:
    """How many main-slate players were scored, did not play, or could not be found."""
    counts = scored["Status"].value_counts().to_dict()
    return {k: int(counts.get(k, 0)) for k in ("scored", "dnp", "unmatched")}
