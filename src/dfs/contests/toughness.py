"""Score-to-finish curves per contest format and series, and the head-to-head record.

**The model.** For one entry, `x` is its score on the "average slate" footing (points divided by that week's
slate index, `slate_index.py`) and `q = (place - 0.5) / entries` is where it finished (0 = first; the
half-place keeps last place finite). Within a format the curve is a logistic,

    logit(q) = alpha + beta * x          (beta < 0: more points, better finish)

fitted by weighted least squares on the logit (clipped to +-6, i.e. q in [0.25%, 99.75%]). Each **series**
(one recurring contest) then gets its own level, shifted from its format's by `delta_s`:

    logit(q) = alpha + delta_s + beta * x

with the slope kept at the format's: a series has far too few entries to say anything about its own slope.
`delta_s > 0` means a worse finish for the same score, i.e. a tougher field.

**Shrinkage.** `delta_s` is shrunk toward zero (the format) by `n / (n + k)`: with only each entry's own
residual `r = logit(q) - alpha - beta * x` as evidence, `delta_s = sum(r) / (n + k)`. `k` is chosen by
**leave-one-week-out** error: for each (season, week) in turn, fit on every other week and score that week's
entries by the mean absolute error of the predicted finish percentile; the `k` with the lowest total wins
(`GRID_K`; ties go to the larger `k`). With too little data (`MIN_FOLDS` weeks, `MIN_ENTRIES` entries) `k` is
`DEFAULT_K` and the result says so.

**Same-week pairs.** One lineup entered in several series in the same week has one score and one slate; the
difference between its finishes is a pure series effect, with the week's slate-index error and the lineup's
own quality cancelled. Entries sharing (format, season, week, points) across 2+ different contests form a
*pair group*. Each member contributes a second equation, `r_i - mean(r of the group) = delta_s(i) -
mean(delta of the group's series)`, with weight `lambda` times the entry's weight, where `lambda = var(r) /
var(within-group deviation)`, clipped to [1, `LAMBDA_MAX`] (1 when there are fewer than `MIN_PAIR_GROUPS`
groups): an inverse-variance weighting, estimated from the data. The ordinary equations stay as they were,
and the `k` prior is added on top, so with no pairs this is exactly `n / (n + k)` shrinkage.

**Score profiles** (a whole contest's field, from a standings file) add points to the format fit and the
series equations: five points per contest (winner, top 1%, top 10%, cash line, median), each weighing
`PROFILE_WEIGHT` of an entry, since they are one contest's curve and share its week.

**What is fitted.** Classic main-slate contests of every format except H2H (two entries have no curve; see
`h2h_report`) and `Other`; Showdown on its own, on raw points (a single game has no main-slate index), never
pooled with Classic. Classic contests on other slates (Thursday, Sunday night, Monday) are left out: the
main-slate index does not describe them.

**Outputs.** Per series: the expected cash line on an average slate, the toughness against its format (the
points more, or fewer, it takes to reach the series' own cash percentile than the format's curve says, so
`-delta / beta`; positive = tougher), and `n`.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from dfs.contests import series as series_mod
from dfs.contests.standings import ScoreProfile

GRID_K = (0.0, 1.0, 2.0, 5.0, 10.0, 20.0, 50.0, 100.0, 200.0, 500.0, 1000.0, 1e9)
DEFAULT_K = 10.0
LOGIT_CLIP = 6.0
PROFILE_WEIGHT = 0.5
MIN_FORMAT_N = 8
MIN_FOLDS = 3
MIN_ENTRIES = 30
MIN_PAIR_GROUPS = 5
LAMBDA_MAX = 10.0

NOT_FITTED = frozenset({series_mod.H2H, series_mod.OTHER})
H2H_BAND_EDGES = (0, 100, 120, 140, 160, 180, math.inf)

OBS_COLUMNS = [
    "kind",
    "format",
    "series",
    "season",
    "week",
    "contest_key",
    "x",
    "y",
    "weight",
    "group",
    "q_cash",
]


def logit(q):
    q = np.clip(q, 1e-12, 1 - 1e-12)
    return np.clip(np.log(q / (1 - q)), -LOGIT_CLIP, LOGIT_CLIP)


def expit(y):
    return 1.0 / (1.0 + np.exp(-y))


def _index_lookup(index: pd.DataFrame | None) -> dict[tuple[int, int], float]:
    if index is None or index.empty:
        return {}
    return {
        (int(s), int(w)): float(v)
        for s, w, v in zip(index["season"], index["week"], index["index"], strict=True)
        if pd.notna(v) and v > 0
    }


def build_observations(
    log: pd.DataFrame,
    index: pd.DataFrame | None,
    profiles: list[tuple[ScoreProfile, str, str, int, int]] | None = None,
) -> tuple[pd.DataFrame, dict[str, int]]:
    """`(observations, skipped)` from `history.typed(log)`, the slate index (`season, week, index`) and
    optional score profiles as `(profile, format, series, season, week)` tuples. `skipped` counts the rows
    left out and why."""
    lookup = _index_lookup(index)
    skipped = {"unscored": 0, "not_fitted_format": 0, "other_slate": 0, "no_index": 0, "no_week": 0}
    rows = []
    for rec in log.itertuples(index=False):
        fmt = rec.format
        if fmt in NOT_FITTED:
            skipped["not_fitted_format"] += 1
            continue
        if pd.isna(rec.place) or pd.isna(rec.entries) or pd.isna(rec.points) or rec.entries < 2:
            skipped["unscored"] += 1
            continue
        if pd.isna(rec.season) or pd.isna(rec.week):
            skipped["no_week"] += 1
            continue
        if fmt == series_mod.SHOWDOWN:
            scale = 1.0
        elif rec.slate != "main":
            skipped["other_slate"] += 1
            continue
        else:
            scale = lookup.get((int(rec.season), int(rec.week)))
            if scale is None:
                skipped["no_index"] += 1
                continue
        q = (float(rec.place) - 0.5) / float(rec.entries)
        q_cash = (
            (float(rec.places_paid) - 0.5) / float(rec.entries)
            if pd.notna(rec.places_paid) and rec.places_paid > 0
            else math.nan
        )
        rows.append(
            {
                "kind": "entry",
                "format": fmt,
                "series": rec.series,
                "season": int(rec.season),
                "week": int(rec.week),
                "contest_key": str(rec.contest_key),
                "x": float(rec.points) / scale,
                "y": float(logit(q)),
                "weight": 1.0,
                "group": "",
                "q_cash": q_cash,
                "points": round(float(rec.points), 2),
            }
        )
    for profile, fmt, series, season, week in profiles or []:
        if fmt in NOT_FITTED:
            continue
        scale = 1.0 if fmt == series_mod.SHOWDOWN else lookup.get((int(season), int(week)))
        if scale is None:
            skipped["no_index"] += 1
            continue
        for q, score in profile.curve():
            rows.append(
                {
                    "kind": "profile",
                    "format": fmt,
                    "series": series,
                    "season": int(season),
                    "week": int(week),
                    "contest_key": str(profile.contest_id),
                    "x": float(score) / scale,
                    "y": float(logit(q)),
                    "weight": PROFILE_WEIGHT,
                    "group": "",
                    "q_cash": math.nan,
                    "points": math.nan,
                }
            )
    obs = pd.DataFrame(rows, columns=[*OBS_COLUMNS, "points"])
    return assign_pair_groups(obs), skipped


def assign_pair_groups(obs: pd.DataFrame) -> pd.DataFrame:
    """Fills `group` for entries that share (format, season, week, points) across 2+ different contests:
    one lineup entered more than once in a week."""
    out = obs.copy()
    out["group"] = ""
    entries = out[out["kind"] == "entry"]
    if entries.empty:
        return out
    keyed = entries.groupby(["format", "season", "week", "points"])
    for (fmt, season, week, points), sub in keyed:
        if len(sub) >= 2 and sub["contest_key"].nunique() >= 2:
            out.loc[sub.index, "group"] = f"{fmt}|{season}|{week}|{points}"
    return out


# ---- fitting -------------------------------------------------------------------------------------


@dataclass(frozen=True)
class FormatFit:
    format: str
    alpha: float
    beta: float
    n_entries: int
    n_profile: int
    reliable: bool
    note: str = ""


def fit_format(obs: pd.DataFrame, fmt: str) -> FormatFit | None:
    """Weighted least squares of `y` on `x` for one format; None when there is too little data."""
    sub = obs[obs["format"] == fmt]
    n_entries = int((sub["kind"] == "entry").sum())
    n_profile = int((sub["kind"] == "profile").sum())
    if n_entries + n_profile < MIN_FORMAT_N or sub["x"].nunique() < 2:
        return None
    w, x, y = sub["weight"].to_numpy(float), sub["x"].to_numpy(float), sub["y"].to_numpy(float)
    xbar, ybar = np.average(x, weights=w), np.average(y, weights=w)
    sxx = float((w * (x - xbar) ** 2).sum())
    if sxx <= 0:
        return None
    beta = float((w * (x - xbar) * (y - ybar)).sum() / sxx)
    alpha = float(ybar - beta * xbar)
    reliable = beta < 0
    note = "" if reliable else "slope is not negative: more points did not mean a better finish"
    return FormatFit(fmt, alpha, beta, n_entries, n_profile, reliable, note)


def estimate_lambda(obs: pd.DataFrame, fit: FormatFit) -> float:
    """Weight of the paired equations: var(r) / var(within-group deviation), clipped to [1, LAMBDA_MAX]."""
    entries = obs[(obs["format"] == fit.format) & (obs["kind"] == "entry")]
    if entries.empty:
        return 1.0
    r = entries["y"] - fit.alpha - fit.beta * entries["x"]
    grouped = entries.assign(r=r)[entries["group"] != ""]
    if grouped["group"].nunique() < MIN_PAIR_GROUPS:
        return 1.0
    dev = grouped["r"] - grouped.groupby("group")["r"].transform("mean")
    size = grouped.groupby("group")["r"].transform("size")
    dof = float((1 - 1 / size).sum())
    var_pair = float((dev**2).sum() / dof) if dof > 0 else 0.0
    var_abs = float(r.var(ddof=1)) if len(r) > 1 else 0.0
    if var_pair <= 0 or var_abs <= 0:
        return LAMBDA_MAX if var_pair <= 0 < var_abs else 1.0
    return float(min(max(var_abs / var_pair, 1.0), LAMBDA_MAX))


@dataclass(frozen=True)
class SeriesSystem:
    """The normal equations for one format's series levels, without the `k` prior (which is added at solve
    time, so one build serves every `k` in the grid)."""

    names: list[str]
    lhs: np.ndarray
    rhs: np.ndarray


def build_system(
    obs: pd.DataFrame, fit: FormatFit, *, use_pairs: bool = True, lam: float | None = None
) -> SeriesSystem:
    sub = obs[obs["format"] == fit.format]
    names = sorted(sub["series"].unique())
    size = len(names)
    lhs, rhs = np.zeros((size, size)), np.zeros(size)
    if not size:
        return SeriesSystem(names, lhs, rhs)
    pos = {name: i for i, name in enumerate(names)}
    resid = (sub["y"] - fit.alpha - fit.beta * sub["x"]).to_numpy(float)
    series_idx = np.array([pos[s] for s in sub["series"]])
    weights = sub["weight"].to_numpy(float)
    np.add.at(lhs, (series_idx, series_idx), weights)
    np.add.at(rhs, series_idx, weights * resid)
    if use_pairs:
        lam = estimate_lambda(obs, fit) if lam is None else lam
        groups = pd.Series(np.arange(len(sub))).groupby(sub["group"].to_numpy())
        for group, members in groups:
            members = members.to_numpy()
            if not group or len(members) < 2:
                continue
            share = np.bincount(series_idx[members], minlength=size) / len(members)
            mean_r = resid[members].mean()
            for i in members:
                d = -share.copy()
                d[series_idx[i]] += 1.0
                lhs += lam * weights[i] * np.outer(d, d)
                rhs += lam * weights[i] * d * (resid[i] - mean_r)
    return SeriesSystem(names, lhs, rhs)


def solve_system(system: SeriesSystem, k: float) -> dict[str, float]:
    if not system.names:
        return {}
    solution, *_ = np.linalg.lstsq(system.lhs + np.eye(len(system.names)) * float(k), system.rhs, rcond=None)
    return dict(zip(system.names, (float(v) for v in solution), strict=True))


def solve_series(
    obs: pd.DataFrame, fit: FormatFit, k: float, *, use_pairs: bool = True, lam: float | None = None
) -> dict[str, float]:
    """`delta_s` for every series of the format, given the format's curve. See the module docstring."""
    return solve_system(build_system(obs, fit, use_pairs=use_pairs, lam=lam), k)


@dataclass
class Model:
    k: float
    formats: dict[str, FormatFit] = field(default_factory=dict)
    deltas: dict[tuple[str, str], float] = field(default_factory=dict)

    def predict_q(self, fmt: str, series: str, x: float) -> float:
        fit = self.formats.get(fmt)
        if fit is None or not fit.reliable:
            return math.nan
        return float(expit(fit.alpha + self.deltas.get((fmt, series), 0.0) + fit.beta * x))


def fit_model(obs: pd.DataFrame, k: float, *, use_pairs: bool = True) -> Model:
    model = Model(k=k)
    for fmt in sorted(obs["format"].unique()):
        fit = fit_format(obs, fmt)
        if fit is None:
            continue
        model.formats[fmt] = fit
        if fit.reliable:
            for name, delta in solve_series(obs, fit, k, use_pairs=use_pairs).items():
                model.deltas[(fmt, name)] = delta
    return model


def fold_predictions(
    obs: pd.DataFrame, season: int, week: int, grid=GRID_K, *, use_pairs: bool = True
) -> dict[float, list[tuple[float, float]]]:
    """For one held-out (season, week): per `k`, `(predicted q, actual q)` for each of that week's entries,
    from a fit that has never seen the week (its observations, pairs and profiles are all removed)."""
    held = (obs["season"] == season) & (obs["week"] == week)
    train = obs[~held]
    test = obs[held & (obs["kind"] == "entry")]
    fits = {fmt: fit_format(train, fmt) for fmt in train["format"].unique()}
    systems = {
        fmt: build_system(train, fit, use_pairs=use_pairs)
        for fmt, fit in fits.items()
        if fit is not None and fit.reliable
    }
    out: dict[float, list[tuple[float, float]]] = {}
    for k in grid:
        deltas = {}
        for fmt, system in systems.items():
            for name, delta in solve_system(system, k).items():
                deltas[(fmt, name)] = delta
        pairs = []
        for rec in test.itertuples(index=False):
            fit = fits.get(rec.format)
            if fit is None or not fit.reliable:
                continue
            y_hat = fit.alpha + deltas.get((rec.format, rec.series), 0.0) + fit.beta * rec.x
            pairs.append((float(expit(y_hat)), float(expit(rec.y))))
        out[k] = pairs
    return out


def leave_one_week_out(obs: pd.DataFrame, grid=GRID_K, *, use_pairs: bool = True) -> pd.DataFrame:
    """Mean absolute error of the predicted finish percentile over every held-out week's entries, per `k`."""
    entries = obs[obs["kind"] == "entry"]
    weeks = sorted({(int(s), int(w)) for s, w in zip(entries["season"], entries["week"], strict=True)})
    abs_err: dict[float, list[float]] = {k: [] for k in grid}
    for season, week in weeks:
        for k, pairs in fold_predictions(obs, season, week, grid, use_pairs=use_pairs).items():
            abs_err[k].extend(abs(pred - actual) for pred, actual in pairs)
    return pd.DataFrame(
        [
            {"k": k, "mae": float(np.mean(v)) if v else math.nan, "n_scored": len(v)}
            for k, v in abs_err.items()
        ]
    )


def choose_k(obs: pd.DataFrame, grid=GRID_K, *, use_pairs: bool = True) -> tuple[float, pd.DataFrame, str]:
    """`(k, error table, how it was chosen)`."""
    entries = obs[obs["kind"] == "entry"]
    n_weeks = len({(int(s), int(w)) for s, w in zip(entries["season"], entries["week"], strict=True)})
    if n_weeks < MIN_FOLDS or len(entries) < MIN_ENTRIES:
        return (
            DEFAULT_K,
            pd.DataFrame(columns=["k", "mae", "n_scored"]),
            (
                f"default k={DEFAULT_K:g}: {len(entries)} entries over {n_weeks} weeks is too little for "
                f"leave-one-week-out (needs {MIN_ENTRIES} entries over {MIN_FOLDS} weeks)"
            ),
        )
    errors = leave_one_week_out(obs, grid, use_pairs=use_pairs)
    scored = errors.dropna(subset=["mae"])
    if scored.empty:
        return DEFAULT_K, errors, f"default k={DEFAULT_K:g}: no held-out week could be scored"
    best = scored["mae"].min()
    k = float(scored[scored["mae"] <= best + 1e-12]["k"].max())
    return k, errors, f"leave-one-week-out over {n_weeks} weeks"


# ---- results -------------------------------------------------------------------------------------


@dataclass
class ToughnessResult:
    model: Model
    k: float
    k_source: str
    k_errors: pd.DataFrame
    series_table: pd.DataFrame
    format_table: pd.DataFrame
    observations: pd.DataFrame
    skipped: dict[str, int]
    notes: list[str] = field(default_factory=list)


def _x_at(alpha: float, beta: float, delta: float, q: float) -> float:
    return float((logit(q) - alpha - delta) / beta)


SERIES_COLUMNS = [
    "format",
    "series",
    "n",
    "n_weeks",
    "shrink_weight",
    "delta",
    "payout_q",
    "expected_cash_line",
    "format_cash_line",
    "toughness",
    "pair_entries",
]
FORMAT_COLUMNS = [
    "format",
    "n_entries",
    "n_profile_points",
    "alpha",
    "beta",
    "reliable",
    "payout_q",
    "expected_cash_line",
    "note",
]


def fit_toughness(
    log: pd.DataFrame,
    index: pd.DataFrame | None,
    profiles: list[tuple[ScoreProfile, str, str, int, int]] | None = None,
    *,
    k: float | None = None,
    use_pairs: bool = True,
) -> ToughnessResult:
    """The whole pipeline: observations, `k` by leave-one-week-out (unless given), format and series fits."""
    obs, skipped = build_observations(log, index, profiles)
    if obs.empty:
        empty = Model(k=DEFAULT_K)
        return ToughnessResult(
            empty,
            DEFAULT_K,
            "no data",
            pd.DataFrame(),
            pd.DataFrame(columns=SERIES_COLUMNS),
            pd.DataFrame(columns=FORMAT_COLUMNS),
            obs,
            skipped,
            ["no scored entries to fit"],
        )
    if k is None:
        k, errors, source = choose_k(obs, use_pairs=use_pairs)
    else:
        errors, source = pd.DataFrame(columns=["k", "mae", "n_scored"]), f"given k={k:g}"
    model = fit_model(obs, k, use_pairs=use_pairs)

    entries = obs[obs["kind"] == "entry"]
    fmt_rows, series_rows, notes = [], [], []
    for fmt, fit in sorted(model.formats.items()):
        sub = entries[entries["format"] == fmt]
        payout = float(sub["q_cash"].median()) if sub["q_cash"].notna().any() else math.nan
        fmt_rows.append(
            {
                "format": fmt,
                "n_entries": fit.n_entries,
                "n_profile_points": fit.n_profile,
                "alpha": fit.alpha,
                "beta": fit.beta,
                "reliable": fit.reliable,
                "payout_q": payout,
                "expected_cash_line": _x_at(fit.alpha, fit.beta, 0.0, payout)
                if fit.reliable and not math.isnan(payout)
                else math.nan,
                "note": fit.note,
            }
        )
        if not fit.reliable:
            notes.append(f"{fmt}: {fit.note}")
    for fmt in sorted(entries["format"].unique()):
        if fmt not in model.formats:
            notes.append(f"{fmt}: fewer than {MIN_FORMAT_N} scored entries, no curve fitted")
    for (fmt, name), sub in entries.groupby(["format", "series"]):
        fit = model.formats.get(fmt)
        n = len(sub)
        payout = float(sub["q_cash"].median()) if sub["q_cash"].notna().any() else math.nan
        delta = model.deltas.get((fmt, name), math.nan)
        usable = fit is not None and fit.reliable
        cash = _x_at(fit.alpha, fit.beta, delta, payout) if usable and not math.isnan(payout) else math.nan
        base = _x_at(fit.alpha, fit.beta, 0.0, payout) if usable and not math.isnan(payout) else math.nan
        series_rows.append(
            {
                "format": fmt,
                "series": name,
                "n": n,
                "n_weeks": int(sub[["season", "week"]].drop_duplicates().shape[0]),
                "shrink_weight": n / (n + k) if (n + k) > 0 else math.nan,
                "delta": delta,
                "payout_q": payout,
                "expected_cash_line": cash,
                "format_cash_line": base,
                "toughness": cash - base if usable and not math.isnan(cash) else math.nan,
                "pair_entries": int((sub["group"] != "").sum()),
            }
        )
    series_table = pd.DataFrame(series_rows, columns=SERIES_COLUMNS).sort_values(
        ["format", "n"], ascending=[True, False]
    )
    return ToughnessResult(
        model=model,
        k=k,
        k_source=source,
        k_errors=errors,
        series_table=series_table.reset_index(drop=True),
        format_table=pd.DataFrame(fmt_rows, columns=FORMAT_COLUMNS),
        observations=obs,
        skipped=skipped,
        notes=notes,
    )


# ---- head to head --------------------------------------------------------------------------------


def _band(x: float) -> str:
    for lo, hi in zip(H2H_BAND_EDGES[:-1], H2H_BAND_EDGES[1:], strict=True):
        if lo <= x < hi:
            return f"<{hi:g}" if lo == 0 else (f">={lo:g}" if math.isinf(hi) else f"{lo:g}-{hi:g}")
    return ""


def h2h_report(log: pd.DataFrame, index: pd.DataFrame | None) -> tuple[pd.DataFrame, pd.DataFrame]:
    """`(win rate by score band, record per opponent with 2+ meetings)` from the H2H rows of `history.typed`.

    A result is W if the entry won more than its fee back, L if less, T if exactly its fee (a refund). Win
    rate counts a tie as half. Bands are of the score on the average-slate footing (main-slate Classic only;
    others cannot be placed on that scale), the opponent record uses every H2H row."""
    h2h = log[(log["format"] == series_mod.H2H) & log["place"].notna()].copy()
    cols_band = ["band", "n", "wins", "losses", "ties", "win_rate"]
    cols_opp = ["opponent", "meetings", "wins", "losses", "ties", "record", "net"]
    if h2h.empty:
        return pd.DataFrame(columns=cols_band), pd.DataFrame(columns=cols_opp)
    h2h["result"] = np.where(
        h2h["winnings"] > h2h["fee"] + 1e-9, "W", np.where(h2h["winnings"] < h2h["fee"] - 1e-9, "L", "T")
    )
    h2h["net"] = h2h["winnings"] - h2h["fee"]
    lookup = _index_lookup(index)
    scale = [
        lookup.get((int(s), int(w))) if pd.notna(s) and pd.notna(w) and sl == "main" else None
        for s, w, sl in zip(h2h["season"], h2h["week"], h2h["slate"], strict=True)
    ]
    h2h["x"] = [
        p / sc if sc and pd.notna(p) else math.nan for p, sc in zip(h2h["points"], scale, strict=True)
    ]

    def summarise(frame: pd.DataFrame, key: str) -> pd.DataFrame:
        rows = []
        for name, sub in frame.groupby(key, sort=False):
            wins, losses, ties = (int((sub["result"] == r).sum()) for r in ("W", "L", "T"))
            rows.append(
                {
                    key: name,
                    "n": len(sub),
                    "wins": wins,
                    "losses": losses,
                    "ties": ties,
                    "win_rate": (wins + 0.5 * ties) / len(sub),
                    "net": float(sub["net"].sum()),
                }
            )
        return pd.DataFrame(rows)

    scored = h2h[h2h["x"].notna()].copy()
    scored["band"] = scored["x"].map(_band)
    order = {_band(lo): i for i, lo in enumerate(H2H_BAND_EDGES[:-1])}
    bands = summarise(scored, "band")
    if not bands.empty:
        bands = bands.sort_values("band", key=lambda s: s.map(order)).reset_index(drop=True)
    bands = bands.reindex(columns=cols_band)

    named = h2h[h2h["opponent"] != ""]
    opp = summarise(named, "opponent")
    if opp.empty:
        return bands, pd.DataFrame(columns=cols_opp)
    opp = opp[opp["n"] >= 2].rename(columns={"n": "meetings"})
    opp["record"] = [
        f"{w}-{lo}" + (f"-{t}" if t else "")
        for w, lo, t in zip(opp["wins"], opp["losses"], opp["ties"], strict=True)
    ]
    opp = opp.sort_values(["meetings", "opponent"], ascending=[False, True]).reset_index(drop=True)
    return bands, opp.reindex(columns=cols_opp)
