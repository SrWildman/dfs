"""Score-to-finish curves per contest format and series, week effects, and the head-to-head record.

**The model.** For one entry, `x` is its score and `q = (place - 0.5) / entries` is where it finished (0 =
first; the half-place keeps last place finite). With `y = logit(q)` (clipped to +-6: q in [0.25%, 99.75%]),

    y = alpha_F + w_week + delta_s + beta_F * x

- `alpha_F`, `beta_F`: the **format** curve (beta < 0: more points, better finish), pooled over the format's
  series; not shrunk.
- `delta_s`: the **series** level within its format. `delta_s > 0` = the same score finishes worse = a
  tougher field. Shrunk toward 0 by `n / (n + k)`.
- `w_week`: the **week effect**, shared by every contest in the same week: how much tougher or softer the
  whole week played than the model expects for a score. It absorbs everything about a week that scores do
  not carry (whether the chalk hit, the slate's scoring, the field's mood) and it is what makes cash lines
  swing 106 to 159 between weeks. Shrunk toward 0 by `n / (n + k_w)`. Estimated *within a format group*:
  every Classic main-slate format shares one `w_week` (Sam enters ~10-15 of them a week, so it is well
  identified); Showdown has its own, on its own contests.

All of it is one ridge least-squares fit on the logit (an entry weighs 1, a profile point
`PROFILE_WEIGHT`); the penalties are `k` on the series levels and `k_w` on the week effects, so with only
series effects the shrinkage is exactly `delta_s = sum(r) / (n + k)` for the entry residuals `r`, and likewise
for weeks. A series' slope is the format's: a series has far too few entries for its own.

**Same-week pairs** (one lineup entered in several contests in a week: identical score, identical week) are
the special case of this model in which the score term cancels; they need no separate weighting, so there is
none. `pair_entries` in the series table still counts how many of a series' entries were such pairs.

**Choosing `k` and `k_w`: leave-one-week-out.** For each (season, week) in turn the model is refitted
without it and the held-out week's entries are scored by the mean absolute error of the predicted finish
percentile. A week that has not been played has no week effect, so the held-out prediction uses `w = 0`;
that is also how `expected_cash_line` is defined: **the score that finishes at the series' own typical cash
percentile in an average week (`w = 0`)**.

**Three ways to handle the week, compared by that same error** (`compare_models`; the best is used, ties go
to the simplest):

- `weeks`: raw points plus week effects (grid over `k` and `k_w`; `k_w = inf` is exactly `raw`);
- `index`: points divided by the main-slate scoring index (`slate_index.py`), no week effects;
- `raw`: raw points, no week effects.

The slate index is otherwise **context only**: `toughness_weeks.csv` carries it beside each week's fitted
effect, and nothing divides by it unless `index` wins the comparison. The three are compared on the same
rows (those the index covers), each with its own best `k` (and `k_w`); the `weeks` grid is the largest, so
its error is the most optimistic of the three by a little.

**What is fitted.** Classic main-slate contests of every format except H2H (two entries have no curve; see
`h2h_report`) and `Other`; Showdown on its own format and its own week effects, never pooled with Classic.
Classic contests on other slates (Thursday, Sunday night, Monday) are left out: their week is not the
main slate's. **Score profiles** (a whole field from a standings file) add five points per contest (winner,
top 1%, top 10%, cash line, median) at `PROFILE_WEIGHT` of an entry each.

**Outputs.** Per series: the expected cash line (`w = 0`), the toughness against its format (the points more,
or fewer, it takes to reach the series' own cash percentile than the format's curve says, `-delta / beta`;
positive = tougher) and `n`. Per week: the fitted effect in logit and in points.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from dfs.contests import series as series_mod
from dfs.contests.standings import ScoreProfile

INF_K = 1e8  # a penalty at or above this means "no such effect": its columns are dropped from the fit
GRID_K = (0.0, 1.0, 2.0, 5.0, 10.0, 20.0, 50.0, 100.0, 200.0, 500.0, 1000.0, 1e9)
GRID_KW = (0.0, 1.0, 2.0, 5.0, 10.0, 20.0, 50.0, 200.0, 1e9)
DEFAULT_K = 10.0
DEFAULT_KW = 10.0
LOGIT_CLIP = 6.0
PROFILE_WEIGHT = 0.5
MIN_FORMAT_N = 8
MIN_FOLDS = 3
MIN_ENTRIES = 30
# Scores are centred and scaled before the fit purely for conditioning; alpha and beta are unpenalised, so
# this changes nothing about the answer and the parameters are converted back to raw points.
XCENTER = 150.0
XSCALE = 25.0
RIDGE_FLOOR = 1e-8
COMPARISON_COLUMNS = ["mode", "k", "k_w", "mae", "n_scored", "n_entries"]

MODES = ("raw", "index", "weeks")  # simplest first: ties in the comparison go to the earlier one
NOT_FITTED = frozenset({series_mod.H2H, series_mod.OTHER})
H2H_BAND_EDGES = (0, 100, 120, 140, 160, 180, math.inf)
CLASSIC_GROUP, SHOWDOWN_GROUP = "classic", "showdown"

OBS_COLUMNS = [
    "kind",
    "format",
    "series",
    "season",
    "week",
    "wgroup",
    "contest_key",
    "x_raw",
    "x_idx",
    "y",
    "weight",
    "group",
    "q_cash",
    "points",
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
    """`(observations, skipped)` from `history.typed(log)`, the slate index (`season, week, index`; optional)
    and optional score profiles as `(profile, format, series, season, week)` tuples.

    `x_raw` is the score as scored; `x_idx` is the score divided by that week's slate index (NaN where the
    index has no value for the week; a Showdown score has no main-slate index and keeps its raw value).
    `skipped` counts the rows left out and why; `no_index` is informational (rows only the `index` model
    cannot use)."""
    lookup = _index_lookup(index)
    skipped = {"unscored": 0, "not_fitted_format": 0, "other_slate": 0, "no_index": 0, "no_week": 0}
    rows = []

    def scale_for(fmt: str, season: int, week: int) -> float | None:
        return 1.0 if fmt == series_mod.SHOWDOWN else lookup.get((season, week))

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
        if fmt != series_mod.SHOWDOWN and rec.slate != "main":
            skipped["other_slate"] += 1
            continue
        season, week = int(rec.season), int(rec.week)
        scale = scale_for(fmt, season, week)
        if scale is None:
            skipped["no_index"] += 1
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
                "season": season,
                "week": week,
                "wgroup": SHOWDOWN_GROUP if fmt == series_mod.SHOWDOWN else CLASSIC_GROUP,
                "contest_key": str(rec.contest_key),
                "x_raw": float(rec.points),
                "x_idx": float(rec.points) / scale if scale else math.nan,
                "y": float(logit(q)),
                "weight": 1.0,
                "group": "",
                "q_cash": q_cash,
                "points": round(float(rec.points), 2),
            }
        )
    for profile, fmt, name, season, week in profiles or []:
        if fmt in NOT_FITTED:
            continue
        scale = scale_for(fmt, int(season), int(week))
        for q, score in profile.curve():
            rows.append(
                {
                    "kind": "profile",
                    "format": fmt,
                    "series": name,
                    "season": int(season),
                    "week": int(week),
                    "wgroup": SHOWDOWN_GROUP if fmt == series_mod.SHOWDOWN else CLASSIC_GROUP,
                    "contest_key": str(profile.contest_id),
                    "x_raw": float(score),
                    "x_idx": float(score) / scale if scale else math.nan,
                    "y": float(logit(q)),
                    "weight": PROFILE_WEIGHT,
                    "group": "",
                    "q_cash": math.nan,
                    "points": math.nan,
                }
            )
    return assign_pair_groups(pd.DataFrame(rows, columns=OBS_COLUMNS)), skipped


def assign_pair_groups(obs: pd.DataFrame) -> pd.DataFrame:
    """Fills `group` for entries that share (format, season, week, points) across 2+ different contests:
    one lineup entered more than once in a week (reported as `pair_entries`; the week effects already
    cancel what pairs would)."""
    out = obs.copy()
    out["group"] = ""
    entries = out[out["kind"] == "entry"]
    if entries.empty:
        return out
    for (fmt, season, week, points), sub in entries.groupby(["format", "season", "week", "points"]):
        if len(sub) >= 2 and sub["contest_key"].nunique() >= 2:
            out.loc[sub.index, "group"] = f"{fmt}|{season}|{week}|{points}"
    return out


# ---- the joint fit -------------------------------------------------------------------------------


@dataclass(frozen=True)
class FormatFit:
    format: str
    alpha: float
    beta: float
    n_entries: int
    n_profile: int
    reliable: bool
    note: str = ""


@dataclass
class Model:
    mode: str
    k: float
    k_w: float
    formats: dict[str, FormatFit] = field(default_factory=dict)
    deltas: dict[tuple[str, str], float] = field(default_factory=dict)
    weeks: dict[tuple[str, int, int], float] = field(default_factory=dict)

    def predict_q(self, fmt: str, series: str, x: float) -> float:
        """The predicted finish percentile for a score in an average week (`w = 0`). `x` is in the model's
        own units (raw points, or index-divided points in `index` mode)."""
        fit = self.formats.get(fmt)
        if fit is None or not fit.reliable:
            return math.nan
        return float(expit(fit.alpha + self.deltas.get((fmt, series), 0.0) + fit.beta * x))


class Design:
    """The data side of the ridge fit for one mode, built once: the normal equations of the whole data set
    and of each (season, week), so a leave-one-week-out fold is a subtraction and each `(k, k_w)` a solve.

    Columns: an intercept and a slope per format (unpenalised), a week effect per (group, season, week)
    (`weeks` mode only, penalty `k_w`), a level per (format, series) (penalty `k`)."""

    def __init__(self, obs: pd.DataFrame, mode: str):
        if mode not in MODES:
            raise ValueError(f"mode must be one of {MODES}, not {mode!r}")
        self.mode = mode
        self.xcol = "x_idx" if mode == "index" else "x_raw"
        self.use_weeks = mode == "weeks"
        sub = obs[obs[self.xcol].notna()]
        keep = [
            fmt for fmt, g in sub.groupby("format") if len(g) >= MIN_FORMAT_N and g[self.xcol].nunique() >= 2
        ]
        self.formats = sorted(keep)
        self.sub = sub[sub["format"].isin(keep)].reset_index(drop=True)
        sub = self.sub
        n, nf = len(sub), len(self.formats)
        self.series = sorted(set(zip(sub["format"], sub["series"], strict=True)))
        self.week_keys = (
            sorted(
                {
                    (g, int(s), int(w))
                    for g, s, w in zip(sub["wgroup"], sub["season"], sub["week"], strict=True)
                }
            )
            if self.use_weeks
            else []
        )
        nw, ns = len(self.week_keys), len(self.series)
        self.nf, self.nw, self.ns = nf, nw, ns
        self.p = 2 * nf + nw + ns
        fi = np.array([self.formats.index(f) for f in sub["format"]], dtype=int)
        si = np.array(
            [self.series.index(fs) for fs in zip(sub["format"], sub["series"], strict=True)], dtype=int
        )
        xs = (sub[self.xcol].to_numpy(float) - XCENTER) / XSCALE
        rows = np.arange(n)
        design = np.zeros((n, self.p))
        design[rows, fi] = 1.0
        design[rows, nf + fi] = xs
        if self.use_weeks:
            index_of = {key: i for i, key in enumerate(self.week_keys)}
            wi = np.array(
                [
                    index_of[(g, int(s), int(w))]
                    for g, s, w in zip(sub["wgroup"], sub["season"], sub["week"], strict=True)
                ],
                dtype=int,
            )
            design[rows, 2 * nf + wi] = 1.0
        design[rows, 2 * nf + nw + si] = 1.0
        root = np.sqrt(sub["weight"].to_numpy(float))
        xw, yw = design * root[:, None], sub["y"].to_numpy(float) * root
        self.fi, self.si, self.xs = fi, si, xs
        self.G, self.b = xw.T @ xw, xw.T @ yw
        self.by_week: dict[tuple[int, int], tuple[np.ndarray, np.ndarray]] = {}
        for key, idx in sub.groupby(["season", "week"]).indices.items():
            self.by_week[(int(key[0]), int(key[1]))] = (xw[idx].T @ xw[idx], xw[idx].T @ yw[idx])
        kind = np.zeros(self.p, dtype=int)  # 0 unpenalised, 1 week effect, 2 series level
        kind[2 * nf : 2 * nf + nw] = 1
        kind[2 * nf + nw :] = 2
        self.kind = kind

    def solve(self, k: float, k_w: float, exclude: tuple[int, int] | None = None) -> np.ndarray:
        """The coefficient vector for penalties `(k, k_w)`, with the (season, week) `exclude` left out."""
        gram, rhs = self.G, self.b
        if exclude is not None and exclude in self.by_week:
            gram, rhs = gram - self.by_week[exclude][0], rhs - self.by_week[exclude][1]
        penalty = np.where(self.kind == 1, k_w, np.where(self.kind == 2, k, 0.0)) + RIDGE_FLOOR
        active = ~(((self.kind == 1) & (k_w >= INF_K)) | ((self.kind == 2) & (k >= INF_K)))
        theta = np.zeros(self.p)
        if active.any():
            a = np.ix_(active, active)
            theta[active] = np.linalg.solve(gram[a] + np.diag(penalty[active]), rhs[active])
        return theta

    def parts(self, theta: np.ndarray):
        nf, nw = self.nf, self.nw
        return theta[:nf], theta[nf : 2 * nf], theta[2 * nf : 2 * nf + nw], theta[2 * nf + nw :]

    def model(self, k: float, k_w: float) -> Model:
        """The fitted model on all the data."""
        a_s, b_s, weeks, deltas = self.parts(self.solve(k, k_w))
        out = Model(self.mode, k, k_w)
        for i, fmt in enumerate(self.formats):
            beta = float(b_s[i] / XSCALE)
            alpha = float(a_s[i] - b_s[i] * XCENTER / XSCALE)
            rows = self.sub[self.sub["format"] == fmt]
            reliable = beta < 0
            out.formats[fmt] = FormatFit(
                fmt,
                alpha,
                beta,
                int((rows["kind"] == "entry").sum()),
                int((rows["kind"] == "profile").sum()),
                reliable,
                "" if reliable else "slope is not negative: more points did not mean a better finish",
            )
        out.deltas = {fs: float(d) for fs, d in zip(self.series, deltas, strict=True)}
        out.weeks = {key: float(w) for key, w in zip(self.week_keys, weeks, strict=True)}
        return out

    def fold_errors(self, grid_k, grid_kw) -> dict[tuple[float, float], list[float]]:
        """For every `(k, k_w)`: the absolute error of the predicted finish percentile of each entry of each
        held-out week, from a fit that has never seen that week (its entries, profiles and week effect)."""
        entries = self.sub["kind"].to_numpy() == "entry"
        week_of = list(zip(self.sub["season"].astype(int), self.sub["week"].astype(int), strict=True))
        y = self.sub["y"].to_numpy(float)
        errors: dict[tuple[float, float], list[float]] = {(k, kw): [] for k in grid_k for kw in grid_kw}
        folds: dict[tuple[int, int], list[int]] = {}
        for i, key in enumerate(week_of):
            if entries[i]:
                folds.setdefault(key, []).append(i)
        for key, idx in folds.items():
            rows = np.array(idx)
            for k, kw in errors:
                a_s, b_s, _, deltas = self.parts(self.solve(k, kw, exclude=key))
                ok = b_s[self.fi[rows]] < 0  # a format whose slope is not negative predicts nothing
                y_hat = a_s[self.fi[rows]] + b_s[self.fi[rows]] * self.xs[rows] + deltas[self.si[rows]]
                err = np.abs(expit(y_hat) - expit(y[rows]))
                errors[(k, kw)].extend(float(e) for e in err[ok])
        return errors


# ---- choosing k and the mode ---------------------------------------------------------------------


def _enough(obs: pd.DataFrame) -> tuple[bool, int]:
    entries = obs[obs["kind"] == "entry"]
    n_weeks = len({(int(s), int(w)) for s, w in zip(entries["season"], entries["week"], strict=True)})
    return n_weeks >= MIN_FOLDS and len(entries) >= MIN_ENTRIES, n_weeks


def leave_one_week_out(obs: pd.DataFrame, mode: str, grid_k=GRID_K, grid_kw=GRID_KW) -> pd.DataFrame:
    """`k, k_w, mae, n_scored` for every penalty pair of one mode (`k_w` is `inf` where the mode has no week
    effects)."""
    design = Design(obs, mode)
    used_kw = grid_kw if design.use_weeks else (1e9,)  # no week effects: nothing to penalise
    rows = []
    for (k, kw), errs in design.fold_errors(grid_k, used_kw).items():
        rows.append(
            {
                "k": k,
                "k_w": kw if design.use_weeks else float("inf"),
                "mae": float(np.mean(errs)) if errs else math.nan,
                "n_scored": len(errs),
            }
        )
    return pd.DataFrame(rows, columns=["k", "k_w", "mae", "n_scored"])


def best_penalties(errors: pd.DataFrame) -> tuple[float, float, float] | None:
    """`(k, k_w, mae)` of the lowest error; ties go to the larger penalties (more shrinkage)."""
    scored = errors.dropna(subset=["mae"])
    if scored.empty:
        return None
    best = scored["mae"].min()
    tied = scored[scored["mae"] <= best + 1e-12].sort_values(["k", "k_w"], ascending=False)
    row = tied.iloc[0]
    return float(row["k"]), float(row["k_w"]), float(row["mae"])


def compare_models(obs: pd.DataFrame, modes=MODES) -> pd.DataFrame:
    """Leave-one-week-out error of each way of handling the week, each at its own best penalties, on the
    rows they can all use (those with a slate index, when the `index` model is among them).

    Columns: `mode, k, k_w, mae, n_scored, n_entries`; empty when there is too little data."""
    modes = list(modes)
    support = obs
    if "index" in modes:
        covered = obs[obs["x_idx"].notna()]
        if (covered["kind"] == "entry").sum() >= MIN_ENTRIES:
            support = covered
        else:
            modes.remove("index")
    ok, _ = _enough(support)
    if not ok:
        return pd.DataFrame(columns=COMPARISON_COLUMNS)
    rows = []
    n_entries = int((support["kind"] == "entry").sum())
    for mode in modes:
        best = best_penalties(leave_one_week_out(support, mode))
        if best is None:
            continue
        k, kw, mae = best
        scored = int(leave_one_week_out(support, mode, grid_k=(k,), grid_kw=(kw,))["n_scored"].iloc[0])
        rows.append({"mode": mode, "k": k, "k_w": kw, "mae": mae, "n_scored": scored, "n_entries": n_entries})
    return pd.DataFrame(rows, columns=COMPARISON_COLUMNS)


def choose_mode(comparison: pd.DataFrame) -> str:
    """The mode with the lowest error; ties (to 1e-9) go to the simplest, `MODES` order."""
    best = comparison["mae"].min()
    tied = comparison[comparison["mae"] <= best + 1e-9]
    return sorted(tied["mode"], key=MODES.index)[0]


# ---- results -------------------------------------------------------------------------------------


@dataclass
class ToughnessResult:
    model: Model
    mode: str
    k: float
    k_w: float
    penalty_source: str
    comparison: pd.DataFrame
    k_errors: pd.DataFrame
    series_table: pd.DataFrame
    format_table: pd.DataFrame
    weeks_table: pd.DataFrame
    observations: pd.DataFrame
    skipped: dict[str, int]
    notes: list[str] = field(default_factory=list)

    @property
    def k_source(self) -> str:  # the name the summary and the first release used
        return self.penalty_source


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
WEEK_COLUMNS = ["group", "season", "week", "n_entries", "w", "w_points", "slate_index"]


def _weeks_table(model: Model, obs: pd.DataFrame, index: pd.DataFrame | None) -> pd.DataFrame:
    """One row per (group, season, week): the fitted effect in logit and in points at the group's pooled slope
    (positive = a tougher week: it took that many more points for the same finish), beside the slate index
    as context."""
    lookup = _index_lookup(index)
    entries = obs[obs["kind"] == "entry"]
    pooled = {}
    for group in (CLASSIC_GROUP, SHOWDOWN_GROUP):
        fits = [
            f
            for fmt, f in model.formats.items()
            if f.reliable and (fmt == series_mod.SHOWDOWN) == (group == SHOWDOWN_GROUP)
        ]
        total = sum(f.n_entries for f in fits)
        pooled[group] = sum(f.beta * f.n_entries for f in fits) / total if total else math.nan
    rows = []
    for (group, season, week), w in sorted(model.weeks.items()):
        n = int(
            ((entries["wgroup"] == group) & (entries["season"] == season) & (entries["week"] == week)).sum()
        )
        beta = pooled[group]
        rows.append(
            {
                "group": group,
                "season": season,
                "week": week,
                "n_entries": n,
                "w": w,
                "w_points": -w / beta if beta and not math.isnan(beta) else math.nan,
                "slate_index": lookup.get((season, week), math.nan) if group == CLASSIC_GROUP else math.nan,
            }
        )
    return pd.DataFrame(rows, columns=WEEK_COLUMNS)


def fit_toughness(
    log: pd.DataFrame,
    index: pd.DataFrame | None,
    profiles: list[tuple[ScoreProfile, str, str, int, int]] | None = None,
    *,
    mode: str = "auto",
    k: float | None = None,
    k_w: float | None = None,
) -> ToughnessResult:
    """The whole pipeline: observations; the three ways of handling the week compared by leave-one-week-out
    error (unless `mode` is given); `k` and `k_w` chosen (unless given); the fit; the tables."""
    obs, skipped = build_observations(log, index, profiles)
    empty = ToughnessResult(
        Model("weeks", DEFAULT_K, DEFAULT_KW),
        "weeks",
        DEFAULT_K,
        DEFAULT_KW,
        "no data",
        pd.DataFrame(columns=COMPARISON_COLUMNS),
        pd.DataFrame(columns=["k", "k_w", "mae", "n_scored"]),
        pd.DataFrame(columns=SERIES_COLUMNS),
        pd.DataFrame(columns=FORMAT_COLUMNS),
        pd.DataFrame(columns=WEEK_COLUMNS),
        obs,
        skipped,
        ["no scored entries to fit"],
    )
    if obs.empty:
        return empty

    notes: list[str] = []
    enough, n_weeks = _enough(obs)
    comparison = (
        compare_models(obs, MODES if mode == "auto" else [mode])
        if enough
        else pd.DataFrame(columns=COMPARISON_COLUMNS)
    )
    if mode == "auto":
        chosen = choose_mode(comparison) if len(comparison) else "weeks"
    else:
        chosen = mode
    k_errors = pd.DataFrame(columns=["k", "k_w", "mae", "n_scored"])
    source = f"given k={k}, k_w={k_w}" if k is not None and k_w is not None else ""
    if k is None or k_w is None:
        if enough:
            support = obs if chosen != "index" else obs[obs["x_idx"].notna()]
            k_errors = leave_one_week_out(support, chosen)
            best = best_penalties(k_errors)
            if best is not None:
                k = best[0] if k is None else k
                k_w = best[1] if k_w is None else k_w
                source = f"leave-one-week-out over {n_weeks} weeks"
        if k is None or k_w is None:
            k = DEFAULT_K if k is None else k
            k_w = DEFAULT_KW if k_w is None else k_w
            source = (
                f"default k={DEFAULT_K:g}, k_w={DEFAULT_KW:g}: too little data for leave-one-week-out "
                f"(needs {MIN_ENTRIES} entries over {MIN_FOLDS} weeks)"
            )
    design = Design(obs, chosen)
    model = design.model(float(k), float(k_w))

    entries = obs[obs["kind"] == "entry"]
    fmt_rows, series_rows = [], []
    for fmt, fit in sorted(model.formats.items()):
        sub = entries[entries["format"] == fmt]
        payout = float(sub["q_cash"].median()) if sub["q_cash"].notna().any() else math.nan
        usable = fit.reliable and not math.isnan(payout)
        fmt_rows.append(
            {
                "format": fmt,
                "n_entries": fit.n_entries,
                "n_profile_points": fit.n_profile,
                "alpha": fit.alpha,
                "beta": fit.beta,
                "reliable": fit.reliable,
                "payout_q": payout,
                "expected_cash_line": _x_at(fit.alpha, fit.beta, 0.0, payout) if usable else math.nan,
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
        count = len(sub)
        payout = float(sub["q_cash"].median()) if sub["q_cash"].notna().any() else math.nan
        delta = model.deltas.get((fmt, name), math.nan)
        usable = fit is not None and fit.reliable and not math.isnan(payout)
        cash = _x_at(fit.alpha, fit.beta, delta, payout) if usable else math.nan
        base = _x_at(fit.alpha, fit.beta, 0.0, payout) if usable else math.nan
        series_rows.append(
            {
                "format": fmt,
                "series": name,
                "n": count,
                "n_weeks": int(sub[["season", "week"]].drop_duplicates().shape[0]),
                "shrink_weight": count / (count + k) if (count + k) > 0 else math.nan,
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
        mode=chosen,
        k=float(k),
        k_w=float(k_w),
        penalty_source=source,
        comparison=comparison,
        k_errors=k_errors,
        series_table=series_table.reset_index(drop=True),
        format_table=pd.DataFrame(fmt_rows, columns=FORMAT_COLUMNS),
        weeks_table=_weeks_table(model, obs, index),
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


def h2h_report(log: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """`(win rate by score band, record per opponent with 2+ meetings)` from the H2H rows of `history.typed`.

    A result is W if the entry won more than its fee back, L if less, T if exactly its fee (a refund). Win
    rate counts a tie as half. Bands are of the raw score (main-slate Classic only: other slates score on a
    different scale; nothing is divided by the slate index), the opponent record uses every H2H row."""
    h2h = log[(log["format"] == series_mod.H2H) & log["place"].notna()].copy()
    cols_band = ["band", "n", "wins", "losses", "ties", "win_rate"]
    cols_opp = ["opponent", "meetings", "wins", "losses", "ties", "record", "net"]
    if h2h.empty:
        return pd.DataFrame(columns=cols_band), pd.DataFrame(columns=cols_opp)
    h2h["result"] = np.where(
        h2h["winnings"] > h2h["fee"] + 1e-9, "W", np.where(h2h["winnings"] < h2h["fee"] - 1e-9, "L", "T")
    )
    h2h["net"] = h2h["winnings"] - h2h["fee"]
    h2h["x"] = [
        float(p) if pd.notna(p) and sl == "main" else math.nan
        for p, sl in zip(h2h["points"], h2h["slate"], strict=True)
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
