"""`CalPts`: a calibrated ensemble projection, and the expanding-window backtest that judges it.

Pure and offline-testable: every function takes dataframes, none reads a file or the network. The
callers (`results_loop` for the backfill, Model Check, later the sync) own the disk.

**Why it exists.** On Weeks 1-4 TFFB ranks players about as well as anything (within position-week
Spearman .48), but its error is mostly a LEVEL bias that depends on position x salary: it runs high
on cheap RBs and WRs and low on the expensive ones. Cash value plays lean on exactly those cheap
players. Fixing the level is the cheapest large win, so `CalPts` is "the sources, corrected for the
bias we have already measured, then averaged".

**Method** (every number is a named constant below, never a literal in the maths):

1. Salary tiers per position (`CAL_SALARY_TIERS`); a cell is (position, tier).
2. Per source and cell, `bias = sum(actual - source) / (n + CAL_SHRINK_K)`: empirical-Bayes
   shrinkage toward 0, the same for every source. n = 0 gives 0, n = 10 keeps 25% of the raw mean,
   n = 30 keeps 50%, n = 160 keeps 84%.
   `calibrated_source = source + bias`.
3. Per position, source weights from the inverse MSE of the calibrated sources, shrunk toward equal
   weights: `w = (n * w_invmse + CAL_WEIGHT_K * w_equal) / (n + CAL_WEIGHT_K)`. The MSE is measured
   on the training rows where EVERY source exists (comparing sources on different players would
   compare players, not sources) and n is that row count. A player's own weights are renormalised
   over the sources he actually has.
4. `CalPts = sum(w * calibrated_source)`, rounded to 0.1.

**No lookahead, ever.** `fit` takes the scored table and `before_week`, and uses only weeks
strictly before it, rosterable pool, `Status == "scored"`. Week 1 has no history, so its `CalPts`
is `AggPts` exactly. `tests/test_calibration.py` changes a week's actuals and asserts nothing in
that week's `CalPts` moves.

Players outside the rosterable pool get the same cell bias: they come from the same salary
distribution, and a backup's bias is the cell's bias.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from dfs.results_analysis import _spearman, usable

POSITIONS = ["QB", "RB", "WR", "TE", "DST"]
# Upper edges of each position's salary tiers (a salary equal to an edge belongs to the HIGHER tier):
# QB <$5.5k / 5.5-6.5k / 6.5k+; RB and WR <$4.5k / 4.5-6k / 6-7.5k / 7.5k+; TE <$3.5k / 3.5-5k / 5k+;
# DST <$2.8k / 2.8k+. From the planning session's prototype (Weeks 1-4, TFFB runs 3.1 points high on
# RBs under $4.5k and 4.8 low on WRs at $7.5k+).
CAL_SALARY_TIERS = {
    "QB": (5500, 6500),
    "RB": (4500, 6000, 7500),
    "WR": (4500, 6000, 7500),
    "TE": (3500, 5000),
    "DST": (2800,),
}
# Pseudo-observations pulling a cell's bias toward 0. 30 (was 40): Weeks 2-4 walk-forward MAE 4.99 -> 4.97,
# rho .517 unchanged; a thin cell (n = 11) now keeps 27% of its raw miss instead of 22%.
CAL_SHRINK_K = 30
CAL_WEIGHT_K = 100  # pseudo-rows pulling the source weights toward equal
CAL_DECIMALS = 1
# The projection sources the ensemble blends, as columns of the scored table. `ProjPts` is TFFB's own.
# UM (`UmPts`, the `dfs.model` mean, blank outside its training population) is NOT in the blend: the research
# found it adds nothing (MAE 5.29 -> 5.26, rho .446 -> .442; docs/RESEARCH.md), so it is tracked in the Model
# Check race as its own row and `UM_SOURCE` names its column there.
TFFB_SOURCE = "ProjPts"
UM_SOURCE = "UmPts"
CAL_SOURCES = ("ProjPts", "SleeperPts", "FantasyProsPts")
SOURCE_LABELS = {
    "ProjPts": "TFFB",
    "SleeperPts": "Sleeper",
    "FantasyProsPts": "FantasyPros",
    "UmPts": "UM",
}
_EPS = 1e-9


def tier_labels(position: str) -> list[str]:
    """Human labels for a position's tiers, low to high: ['<$4.5k', '$4.5-6k', '$6-7.5k', '$7.5k+']."""
    edges = CAL_SALARY_TIERS.get(position, ())
    if not edges:
        return []

    def k(value: float) -> str:
        return f"{value / 1000:g}k"

    labels = [f"<${k(edges[0])}"]
    labels += [f"${low / 1000:g}\u2013{k(high)}" for low, high in zip(edges, edges[1:], strict=False)]
    labels.append(f"${k(edges[-1])}+")
    return labels


def tier_index(position: pd.Series, salary: pd.Series) -> pd.Series:
    """Each row's tier number within its position (0 = cheapest); -1 when the position has no tiers or
    the salary is missing."""
    out = pd.Series(-1, index=position.index, dtype=int)
    salary = pd.to_numeric(salary, errors="coerce")
    for pos, edges in CAL_SALARY_TIERS.items():
        mask = (position == pos) & salary.notna()
        out[mask] = np.searchsorted(np.array(edges, dtype=float), salary[mask].to_numpy(), side="right")
    return out


@dataclass(frozen=True)
class Calibration:
    """What `fit` learned. `bias` is keyed (source, position, tier) and already shrunk; `counts` holds the
    n behind each cell; `weights` is position -> source -> weight (summing to 1 over `sources`)."""

    sources: tuple[str, ...]
    bias: dict[tuple[str, str, int], float] = field(default_factory=dict)
    counts: dict[tuple[str, str, int], int] = field(default_factory=dict)
    weights: dict[str, dict[str, float]] = field(default_factory=dict)
    weight_rows: dict[str, int] = field(default_factory=dict)
    train_rows: int = 0
    weeks: tuple[int, ...] = ()

    @property
    def is_empty(self) -> bool:
        """True with no training rows at all (Week 1): `CalPts` is then `AggPts`."""
        return self.train_rows == 0


def shrunk_bias(residual_sum: float, n: int, k: float = CAL_SHRINK_K) -> float:
    """`sum(actual - source) / (n + k)`. 0 at n = 0; the raw mean times n/(n+k) otherwise."""
    return residual_sum / (n + k) if (n + k) > 0 else 0.0


def blended_weight(w_invmse: float, w_equal: float, n: int, k: float = CAL_WEIGHT_K) -> float:
    """`(n * w_invmse + k * w_equal) / (n + k)`: equal weights at n = 0, the inverse-MSE weight as n grows."""
    return (n * w_invmse + k * w_equal) / (n + k)


def training_rows(scored: pd.DataFrame, before_week: int, season: int | None = None) -> pd.DataFrame:
    """The rows `fit` may learn from: weeks strictly BEFORE `before_week`, rosterable pool, scored,
    with a known actual. This is the only place the no-lookahead rule is enforced."""
    if scored is None or scored.empty:
        return pd.DataFrame()
    df = scored[pd.to_numeric(scored["week"], errors="coerce") < before_week]
    if season is not None and "season" in df.columns:
        df = df[df["season"] == season]
    df = df[df["RosterablePool"].astype(bool) & (df["Status"] == "scored")]
    return df[pd.to_numeric(df["DkActual"], errors="coerce").notna()].copy()


def fit(
    scored: pd.DataFrame,
    *,
    before_week: int,
    season: int | None = None,
    sources: tuple[str, ...] = CAL_SOURCES,
) -> Calibration:
    """Learn the cell biases and the position weights from weeks `< before_week` only."""
    train = training_rows(scored, before_week, season)
    sources = tuple(s for s in sources if train.empty or s in train.columns)
    if train.empty:
        return Calibration(sources=tuple(sources))
    train["_tier"] = tier_index(train["Position"], train["Salary"])
    actual = pd.to_numeric(train["DkActual"], errors="coerce")

    bias: dict[tuple[str, str, int], float] = {}
    counts: dict[tuple[str, str, int], int] = {}
    for source in sources:
        value = pd.to_numeric(train[source], errors="coerce")
        have = value.notna() & (train["_tier"] >= 0)
        residual = (actual - value)[have]
        grouped = residual.groupby([train.loc[have, "Position"], train.loc[have, "_tier"]])
        for (position, tier), total in grouped.sum().items():
            n = int(grouped.size()[(position, tier)])
            bias[(source, str(position), int(tier))] = shrunk_bias(float(total), n)
            counts[(source, str(position), int(tier))] = n

    weights: dict[str, dict[str, float]] = {}
    weight_rows: dict[str, int] = {}
    equal = 1.0 / len(sources) if sources else 0.0
    for position in POSITIONS:
        part = train[train["Position"] == position]
        common = part.dropna(subset=list(sources)) if sources else part
        n = len(common)
        weight_rows[position] = n
        if n == 0:
            weights[position] = {s: equal for s in sources}
            continue
        mse = {}
        for source in sources:
            calibrated = common[source].astype(float) + [
                bias.get((source, position, int(t)), 0.0) for t in common["_tier"]
            ]
            mse[source] = float(((pd.to_numeric(common["DkActual"]) - calibrated) ** 2).mean())
        inverse = {s: 1.0 / max(m, _EPS) for s, m in mse.items()}
        total = sum(inverse.values())
        weights[position] = {s: blended_weight(inverse[s] / total, equal, n) for s in sources}

    return Calibration(
        sources=tuple(sources),
        bias=bias,
        counts=counts,
        weights=weights,
        weight_rows=weight_rows,
        train_rows=len(train),
        weeks=tuple(sorted(int(w) for w in train["week"].unique())),
    )


def calibrated_sources(frame: pd.DataFrame, calibration: Calibration) -> pd.DataFrame:
    """Each source plus its cell bias, one column per source (NaN where the source is NaN)."""
    tiers = tier_index(frame["Position"], frame["Salary"])
    out = pd.DataFrame(index=frame.index)
    for source in calibration.sources:
        if source not in frame.columns:
            out[source] = np.nan
            continue
        shift = [
            calibration.bias.get((source, pos, int(t)), 0.0) if t >= 0 else 0.0
            for pos, t in zip(frame["Position"], tiers, strict=True)
        ]
        out[source] = pd.to_numeric(frame[source], errors="coerce") + np.array(shift)
    return out


def predict(frame: pd.DataFrame, calibration: Calibration) -> pd.Series:
    """`CalPts` per row. With no history (`calibration.is_empty`) it is `AggPts`; otherwise the
    weighted mean of the calibrated sources the player has, weights renormalised over those sources,
    rounded to 0.1. A player with no source at all is NaN, never 0."""
    if calibration.is_empty:
        if "AggPts" in frame.columns:
            return pd.to_numeric(frame["AggPts"], errors="coerce")
        return pd.Series(np.nan, index=frame.index)
    cal = calibrated_sources(frame, calibration)
    weight = pd.DataFrame(
        {
            s: frame["Position"].map(lambda p, s=s: calibration.weights.get(p, {}).get(s, 0.0))
            for s in calibration.sources
        },
        index=frame.index,
    )
    present = cal.notna()
    masked = weight.where(present, 0.0)
    total = masked.sum(axis=1)
    value = (cal.fillna(0.0) * masked).sum(axis=1) / total.where(total > 0)
    return value.round(CAL_DECIMALS)


@dataclass(frozen=True)
class GapExplanation:
    """Why one player's `CalPts` differs from TFFB's `ProjPts`, in two parts that add up to the gap.

    `bias` is the part that comes from the measured (shrunk) cell biases, weighted by the player's own source
    weights; `sources` is the rest, the other sources disagreeing with TFFB. `n` and `raw_bias` describe
    TFFB's own cell (the rows behind it and its unshrunk mean miss, actual minus TFFB)."""

    bias: float
    sources: float
    n: int
    raw_bias: float | None
    cell: str
    present: tuple[str, ...]  # the non-TFFB sources he has, as SOURCE_LABELS names
    higher: tuple[str, ...]  # of those, the ones above TFFB
    lower: tuple[str, ...]
    missing: tuple[str, ...]  # sources he lacks (TFFB included, in which case there is no gap to explain)


def explain_gap(row: pd.Series, fitted: Calibration | None) -> GapExplanation | None:
    """Split `CalPts - ProjPts` for one player into the bias part and the sources part. None when there is
    nothing to explain (no projection to compare, or no calibration was fitted)."""
    tffb = pd.to_numeric(row.get(TFFB_SOURCE), errors="coerce")
    cal = pd.to_numeric(row.get("CalPts"), errors="coerce")
    if fitted is None or pd.isna(tffb) or pd.isna(cal):
        return None
    position, salary = row["Position"], row["Salary"]
    tier = int(tier_index(pd.Series([position]), pd.Series([salary])).iloc[0])
    values = {s: pd.to_numeric(row.get(s), errors="coerce") for s in fitted.sources}
    present = [s for s, v in values.items() if pd.notna(v)]
    raw_weight = {s: fitted.weights.get(position, {}).get(s, 0.0) for s in present}
    total = sum(raw_weight.values())
    bias = 0.0
    if total > 0 and tier >= 0:
        bias = sum(raw_weight[s] / total * fitted.bias.get((s, position, tier), 0.0) for s in present)
    key = (TFFB_SOURCE, position, tier)
    n = fitted.counts.get(key, 0)
    raw = fitted.bias[key] * (n + CAL_SHRINK_K) / n if n else None
    cell = f"{position}s {tier_labels(position)[tier]}" if tier >= 0 and tier_labels(position) else position
    others = [s for s in present if s != TFFB_SOURCE]
    higher = tuple(SOURCE_LABELS[s] for s in others if values[s] > tffb)
    lower = tuple(SOURCE_LABELS[s] for s in others if values[s] < tffb)
    missing = tuple(SOURCE_LABELS[s] for s in fitted.sources if s not in present)
    gap = float(cal - tffb)
    return GapExplanation(
        bias=round(bias, 1) if fitted.train_rows else 0.0,
        sources=round(gap - (round(bias, 1) if fitted.train_rows else 0.0), 1),
        n=n,
        raw_bias=raw,
        cell=cell,
        present=tuple(SOURCE_LABELS[s] for s in others),
        higher=higher,
        lower=lower,
        missing=missing,
    )


def calpts_for_week(
    scored: pd.DataFrame, week: int, *, season: int | None = None, sources: tuple[str, ...] = CAL_SOURCES
) -> tuple[pd.DataFrame, Calibration]:
    """The week's own rows with a `CalPts` column, fit only on weeks before it."""
    rows = scored[pd.to_numeric(scored["week"], errors="coerce") == week].copy()
    if season is not None and "season" in rows.columns:
        rows = rows[rows["season"] == season].copy()
    calibration = fit(scored, before_week=week, season=season, sources=sources)
    if rows.empty:
        rows["CalPts"] = pd.Series(dtype=float)
        return rows, calibration
    rows["CalPts"] = predict(rows, calibration)
    return rows, calibration


# ---------------------------------------------------------------------------------------------
# Backtest
# ---------------------------------------------------------------------------------------------

BACKTEST_COLUMNS = ["Source", "Position", "n", "Rho", "MAE", "Bias"]
# (label, column) pairs: the race on every usable row, and with UM beside it (rows UM rates only).
PROJECTIONS = (("TFFB", "ProjPts"), ("AggPts", "AggPts"), ("CalPts", "CalPts"))
PROJECTIONS_WITH_UM = (("TFFB", "ProjPts"), ("AggPts", "AggPts"), ("UM", UM_SOURCE), ("CalPts", "CalPts"))


def score_predictions(frame: pd.DataFrame, columns: tuple[tuple[str, str], ...]) -> pd.DataFrame:
    """Per projection column and position (plus "All"): n, within position-week Spearman, MAE and mean
    bias (actual - projected). `frame` needs `week`, `Position`, `DkActual` and each column. Only rows
    where EVERY listed column exists are scored, so the columns compare on the same players. Rho is the
    n-weighted mean over position-week groups of at least 5 players."""
    needed = [c for _, c in columns]
    df = frame.dropna(subset=["DkActual", *needed])
    rows = []
    for label, column in columns:
        for position in [*POSITIONS, "All"]:
            part = df if position == "All" else df[df["Position"] == position]
            if part.empty:
                continue
            error = part["DkActual"] - part[column]
            rhos = [
                (len(g), _spearman(g[column], g["DkActual"])) for _, g in part.groupby(["week", "Position"])
            ]
            rhos = [(n, r) for n, r in rhos if n >= 5 and not math.isnan(r)]
            rho = (
                float(np.average([r for _, r in rhos], weights=[n for n, _ in rhos]))
                if rhos
                else float("nan")
            )
            rows.append(
                {
                    "Source": label,
                    "Position": position,
                    "n": len(part),
                    "Rho": round(rho, 3),
                    "MAE": round(float(error.abs().mean()), 2),
                    "Bias": round(float(error.mean()), 2),
                }
            )
    return pd.DataFrame(rows, columns=BACKTEST_COLUMNS)


def backtest_frame(scored: pd.DataFrame, *, season: int | None = None, with_um: bool = False) -> pd.DataFrame:
    """Every predictable week (the 2nd onward) with its out-of-sample projections: for k = 2..N, fit on
    weeks < k and predict week k. With `with_um` only the rows UM rated are kept, so UM and the others are
    judged on the same players. Rosterable pool rows with a real stat line and ProjPts > 0 only (Model
    Check's population)."""
    weeks = sorted(int(w) for w in pd.to_numeric(scored["week"], errors="coerce").dropna().unique())
    pieces = []
    for week in weeks[1:]:
        rows, _ = calpts_for_week(scored, week, season=season, sources=CAL_SOURCES)
        pieces.append(usable(rows, "pool"))
    if not pieces:
        return pd.DataFrame()
    out = pd.concat(pieces, ignore_index=True)
    if with_um:
        out = out[pd.to_numeric(out.get(UM_SOURCE), errors="coerce").notna()]
    return out


def backtest(scored: pd.DataFrame, *, season: int | None = None, with_um: bool = False) -> pd.DataFrame:
    """The expanding-window backtest table (`BACKTEST_COLUMNS`) by position and overall, each projection
    judged on weeks it did not train on: TFFB, AggPts and CalPts; with `with_um`, UM itself too, on the rows
    UM rates."""
    frame = backtest_frame(scored, season=season, with_um=with_um)
    if frame.empty:
        return pd.DataFrame(columns=BACKTEST_COLUMNS)
    return score_predictions(frame, PROJECTIONS_WITH_UM if with_um else PROJECTIONS)


def backtest_by_week(
    scored: pd.DataFrame, *, season: int | None = None, with_um: bool = False
) -> pd.DataFrame:
    """The same race, one row per week and projection (n, Rho, MAE, Bias over the position groups), for the
    per-week trend in Model Check."""
    frame = backtest_frame(scored, season=season, with_um=with_um)
    columns = PROJECTIONS_WITH_UM if with_um else PROJECTIONS
    if frame.empty:
        return pd.DataFrame(columns=["Week", "Source", "n", "Rho", "MAE", "Bias"])
    rows = []
    for week, part in frame.groupby("week"):
        table = score_predictions(part, columns)
        table = table[table["Position"] == "All"].drop(columns="Position")
        table.insert(0, "Week", int(week))
        rows.append(table)
    return pd.concat(rows, ignore_index=True)
