"""Field ownership: what Sam's own contests will own, by format, and what they would own on our projection.

Shadow (2026-10-20, `docs/planning/RESEARCH_OWNERSHIP_2026-10-20.md`): three EdgeRaw columns, scored in Model
Check every week, used by nothing else until they have proven themselves out of sample and Sam says so.

- `GppOwn`: TFFB's projected `Own%`, sharpened for his GPPs. TFFB is the best ownership number we have (a
  model on our own inputs alone missed by 5.9 points per player against TFFB's 3.4), but it understates the
  chalk: players who finished at 20% or more were 5.5 points light in every test week.
- `CashOwn`: the same for his double-ups, where the chalk is far heavier (the role `dups.DOUBLE_UP_OWN_CURVE`
  plays today; this is the Contests Phase 2 double-up ownership model, refitted weekly).
- `OurOwn`: what his GPPs would own if the field used CalPts instead of its own projection. `OurOwn` minus
  `GppOwn` is where we disagree with the field: positive = the field is light on a player we like.

**The model.** Within each position a player's share is `exp(score) / sum(exp(score))` over the eligible
players (not OUT, IR or doubtful), times the position's average total ownership in that format (QB and DST 1,
RB about 2.35, WR 3.3, TE 1.3; FLEX makes the last three move a little). `score = k * log(Own%)`, one `k` per
format, fitted by within-position cross-entropy on his standings (`%Drafted`, the mean over a week's contests
of that format). For `OurOwn` the GPP score gains `r * (log CalPts - log ProjPts)`, where `r` is the field's
response to its own projection at a fixed salary (`fit_response`, about 4.7: a 10% higher projection at the
same salary draws about 1.6 times the ownership). No player is shown above `MAX_OWN`.

**No lookahead.** The fit uses each week's last EdgeRaw snapshot before the Sunday lock (`retro`), with
`CalPts` as the calibration fitted on earlier weeks only. `score_weeks` judges each week on a fit from the
weeks before it. Everything here is pure apart from the loaders and `save` / `load`.
"""

from __future__ import annotations

import json
import math
from collections.abc import Iterable, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.optimize import minimize_scalar

from dfs.derived import OUT_STATUSES
from dfs.paths import DATA_DIR
from dfs.player_join import normalize_name

FORMATS = ("gpp", "cash")
POSITIONS = ("QB", "RB", "WR", "TE", "DST")
GPP_COLUMN = "GppOwn"
CASH_COLUMN = "CashOwn"
OUR_COLUMN = "OurOwn"
COLUMNS = (GPP_COLUMN, CASH_COLUMN, OUR_COLUMN)
FORMAT_COLUMN = {"gpp": GPP_COLUMN, "cash": CASH_COLUMN}
MODEL_FILE = DATA_DIR / "model_cache" / "field_own.json"

EXCLUDED_STATUSES = frozenset({*OUT_STATUSES, "D"})  # never owned: the softmax gives them nothing
OWN_FLOOR = 0.002  # a player with no (or a tiny) projected Own% counts as 0.2% before sharpening
POINTS_FLOOR = 0.5  # projections below this are clipped before the log
EXPONENT_BOUNDS = (0.5, 4.0)
RELEVANT_OWN = 0.05  # scored on players owned 5%+ (actual) or projected 5%+ (TFFB), where errors matter
CHALK_OWN = {"gpp": 0.20, "cash": 0.30}  # "chalk" for the bias line: actual ownership at or above this
GAP_MIN_OWN = 0.01  # the disagreement check: rosterable players projected at 1%+ who played
RESPONSE_MIN_OWN = 0.01  # the projection response is fitted on players drafted at 1%+
GAP_MIN_POINTS = 3.0  # below this either projection is noise for the counterfactual
GAP_LOG_CAP = 0.25  # at most about 28% more or 22% less than the field's projection counts
MAX_OWN = 0.95  # no single player is shown owned above this (the counterfactual can overshoot)


@dataclass(frozen=True)
class FieldOwnModel:
    exponent: dict[str, float]  # format -> k
    totals: dict[str, dict[str, float]]  # format -> position -> mean total ownership
    response: float  # r: the GPP field's response to log projection at a fixed salary
    weeks: tuple[int, ...]
    contests: dict[str, int] = field(default_factory=dict)  # format -> contests fitted

    def to_json(self) -> dict:
        out = asdict(self)
        out["weeks"] = list(self.weeks)
        return out

    @classmethod
    def from_json(cls, data: dict) -> FieldOwnModel:
        return cls(
            exponent={k: float(v) for k, v in data["exponent"].items()},
            totals={f: {p: float(v) for p, v in t.items()} for f, t in data["totals"].items()},
            response=float(data["response"]),
            weeks=tuple(int(w) for w in data["weeks"]),
            contests={k: int(v) for k, v in data.get("contests", {}).items()},
        )


def save(model: FieldOwnModel, path: Path = MODEL_FILE) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(model.to_json(), indent=2) + "\n")
    return path


def load(path: Path = MODEL_FILE) -> FieldOwnModel | None:
    """The saved model, or None when there is none yet (or it does not read)."""
    try:
        return FieldOwnModel.from_json(json.loads(path.read_text()))
    except (OSError, ValueError, KeyError, TypeError):
        return None


# ---------------------------------------------------------------------------------------------------------
# Inputs
# ---------------------------------------------------------------------------------------------------------


def _num(frame: pd.DataFrame, column: str) -> pd.Series:
    if column not in frame.columns:
        return pd.Series(np.nan, index=frame.index, dtype=float)
    return pd.to_numeric(frame[column], errors="coerce")


def eligible(frame: pd.DataFrame) -> pd.Series:
    """Players who can be owned: a known position, not OUT / IR / doubtful."""
    avail = frame["Avail"].fillna("").astype(str) if "Avail" in frame.columns else ""
    ok = frame["Position"].isin(POSITIONS)
    return ok & ~pd.Series(avail, index=frame.index).isin(EXCLUDED_STATUSES)


def _log_own(frame: pd.DataFrame) -> np.ndarray:
    return np.log(_num(frame, "Own%").fillna(0.0).clip(lower=OWN_FLOOR).to_numpy())


def _log_points(frame: pd.DataFrame, column: str) -> np.ndarray:
    return np.log(_num(frame, column).clip(lower=POINTS_FLOOR).to_numpy())


def projection_gap(frame: pd.DataFrame) -> np.ndarray:
    """`log CalPts - log ProjPts`: how much more (or less) we project than the field's projection, within
    +/-`GAP_LOG_CAP`; 0 where either is missing or under `GAP_MIN_POINTS` (a 0-point TFFB projection is not
    a 4x disagreement, it is a player nobody is considering)."""
    cal, proj = _num(frame, "CalPts"), _num(frame, "ProjPts")
    gap = _log_points(frame, "CalPts") - _log_points(frame, "ProjPts")
    gap = np.where((cal >= GAP_MIN_POINTS) & (proj >= GAP_MIN_POINTS), gap, 0.0)
    return np.clip(np.nan_to_num(gap, nan=0.0), -GAP_LOG_CAP, GAP_LOG_CAP)


def _groups(frame: pd.DataFrame) -> list[np.ndarray]:
    """Row positions of each (week, position) choice set's eligible players."""
    week = frame["week"].to_numpy() if "week" in frame.columns else np.zeros(len(frame))
    pos = frame["Position"].to_numpy()
    ok = eligible(frame).to_numpy()
    out = []
    for w in pd.unique(week):
        for p in POSITIONS:
            idx = np.where((week == w) & (pos == p) & ok)[0]
            if len(idx):
                out.append(idx)
    return out


def _shares(scores: np.ndarray, frame: pd.DataFrame, totals: dict[str, float]) -> np.ndarray:
    out = np.zeros(len(frame))
    pos = frame["Position"].to_numpy()
    for idx in _groups(frame):
        s = scores[idx]
        e = np.exp(s - s.max())
        out[idx] = e / e.sum() * totals.get(pos[idx[0]], 0.0)
    return np.minimum(out, MAX_OWN)


# ---------------------------------------------------------------------------------------------------------
# Predict
# ---------------------------------------------------------------------------------------------------------


def predict(frame: pd.DataFrame, model: FieldOwnModel, fmt: str) -> pd.Series:
    """`GppOwn` (`fmt` "gpp") or `CashOwn` ("cash") for every row, as a fraction; 0 for an excluded player."""
    scores = model.exponent[fmt] * _log_own(frame)
    return pd.Series(_shares(scores, frame, model.totals[fmt]), index=frame.index)


def predict_ours(frame: pd.DataFrame, model: FieldOwnModel) -> pd.Series:
    """`OurOwn`: the GPP field's ownership had it projected every player at CalPts."""
    scores = model.exponent["gpp"] * _log_own(frame) + model.response * projection_gap(frame)
    return pd.Series(_shares(scores, frame, model.totals["gpp"]), index=frame.index)


def ownership_published(frame: pd.DataFrame) -> bool:
    """TFFB's Own% is out: before it is, every row reads 0 and there is nothing to sharpen."""
    return bool((_num(frame, "Own%").fillna(0.0) > 0).any())


def attach(frame: pd.DataFrame, model: FieldOwnModel | None) -> pd.DataFrame:
    """The edge frame with `GppOwn`, `CashOwn` and `OurOwn` filled (fractions, like `Own%`); blank when there
    is no fitted model yet or TFFB has not published ownership. Every row is one slate, so it needs no
    `week` column."""
    out = frame.copy()
    for column in COLUMNS:
        out[column] = np.nan
    if model is None or out.empty or not ownership_published(out):
        return out
    slate = out.assign(week=0)
    out[GPP_COLUMN] = predict(slate, model, "gpp").round(4)
    out[CASH_COLUMN] = predict(slate, model, "cash").round(4)
    out[OUR_COLUMN] = predict_ours(slate, model).round(4)
    excluded = ~eligible(out)
    out.loc[excluded, list(COLUMNS)] = 0.0
    return out


# ---------------------------------------------------------------------------------------------------------
# Fit
# ---------------------------------------------------------------------------------------------------------


def _target(frame: pd.DataFrame, fmt: str) -> np.ndarray:
    return _num(frame, f"act_{fmt}").fillna(0.0).to_numpy()


def _cross_entropy(scores: np.ndarray, target: np.ndarray, groups: Sequence[np.ndarray]) -> float:
    loss = 0.0
    for idx in groups:
        t = target[idx]
        if t.sum() <= 0:
            continue
        s = scores[idx]
        logp = s - s.max() - math.log(np.exp(s - s.max()).sum())
        loss -= float((t / t.sum() * logp).sum())
    return loss


def fit_exponent(frame: pd.DataFrame, fmt: str) -> float:
    """`k` for one format: the power on TFFB's Own% that best explains his contests' %Drafted."""
    target, groups, base = _target(frame, fmt), _groups(frame), _log_own(frame)
    found = minimize_scalar(
        lambda k: _cross_entropy(k * base, target, groups), bounds=EXPONENT_BOUNDS, method="bounded"
    )
    return float(found.x)


def fit_response(frame: pd.DataFrame) -> float:
    """`r`: how strongly his GPP fields follow their projection at a fixed salary, the elasticity of
    ownership to TFFB's projection: log(%Drafted) on log ProjPts and log Salary within each week and position
    (players drafted at `RESPONSE_MIN_OWN`+). About 4.7 on Weeks 2-6: a 10% higher projection at the same
    salary means about 1.6 times the ownership. (A softmax fitted on projection alone over-concentrates and
    overstates it; this is the steadier estimate.) 0 without enough players."""
    rows = frame[eligible(frame) & (_num(frame, "act_gpp") >= RESPONSE_MIN_OWN)]
    rows = rows[_num(rows, "ProjPts").notna() & (_num(rows, "Salary") > 0)]
    if len(rows) < 20:
        return 0.0
    y = np.log(_num(rows, "act_gpp").to_numpy())
    proj = _log_points(rows, "ProjPts")
    salary = np.log(_num(rows, "Salary").to_numpy())
    group = (rows["week"].astype(str) + rows["Position"].astype(str)).to_numpy()

    def demean(values: np.ndarray) -> np.ndarray:
        return values - pd.Series(values).groupby(group).transform("mean").to_numpy()

    x = np.column_stack([demean(proj), demean(salary)])
    beta, *_ = np.linalg.lstsq(x, demean(y), rcond=None)
    return float(max(0.0, beta[0]))


def position_totals(frame: pd.DataFrame, fmt: str) -> dict[str, float]:
    """Each position's total ownership in a format, averaged over the weeks that have that format."""
    has = frame.groupby("week")[f"act_{fmt}"].transform("sum") > 0
    sums = frame[has].groupby(["week", "Position"])[f"act_{fmt}"].sum()
    means = sums.groupby(level="Position").mean()
    return {p: float(means.get(p, 0.0)) for p in POSITIONS}


def weeks_with(frame: pd.DataFrame, fmt: str) -> list[int]:
    column = f"act_{fmt}"
    if column not in frame.columns:
        return []
    sums = frame.groupby("week")[column].sum()
    return sorted(int(w) for w, s in sums.items() if s > 0)


def fit(frame: pd.DataFrame, contests: dict[str, int] | None = None) -> FieldOwnModel | None:
    """The model from a training frame (`training_frame`); None unless both formats have a week."""
    if any(not weeks_with(frame, fmt) for fmt in FORMATS):
        return None
    exponent, totals = {}, {}
    for fmt in FORMATS:
        rows = frame[frame["week"].isin(weeks_with(frame, fmt))]
        exponent[fmt] = round(fit_exponent(rows, fmt), 4)
        totals[fmt] = {p: round(v, 4) for p, v in position_totals(rows, fmt).items()}
    gpp_rows = frame[frame["week"].isin(weeks_with(frame, "gpp"))]
    return FieldOwnModel(
        exponent=exponent,
        totals=totals,
        response=round(fit_response(gpp_rows), 4),
        weeks=tuple(sorted(int(w) for w in frame["week"].unique())),
        contests=dict(contests or {}),
    )


# ---------------------------------------------------------------------------------------------------------
# The weeks he has standings for
# ---------------------------------------------------------------------------------------------------------


def actual_ownership(contests: Iterable, week: int, fmt: str) -> tuple[pd.Series, int]:
    """`normalize_name -> %Drafted` (a fraction), the mean over the week's contests of a format (a player a
    contest did not draft counts 0 in it), and the number of contests. `contests` are `retro.ContestField`."""
    chosen = [c for c in contests if c.week == week and c.fmt == fmt]
    if not chosen:
        return pd.Series(dtype=float), 0
    table = pd.DataFrame({c.contest_id: pd.Series(c.own, dtype=float) for c in chosen}).fillna(0.0)
    return table.mean(axis=1), len(chosen)


def week_frame(edge: pd.DataFrame, contests: Iterable, week: int) -> tuple[pd.DataFrame, dict[str, int]]:
    """One week's pre-lock edge frame with `act_gpp` / `act_cash` (0 for a player nobody drafted; all 0 for
    a format with no contest that week)."""
    contests = list(contests)
    out = edge.copy()
    out["week"] = week
    key = out["Name"].map(normalize_name)
    counts = {}
    for fmt in FORMATS:
        own, n = actual_ownership(contests, week, fmt)
        out[f"act_{fmt}"] = key.map(own).fillna(0.0) if n else 0.0
        counts[fmt] = n
    return out, counts


def training_frame(season: int, *, through: int | None = None) -> tuple[pd.DataFrame, dict[str, int]]:
    """Every week with standings (to `through`), each with its pre-lock snapshot and actual ownership, and
    the number of contests per format. Weeks without a pre-lock snapshot are left out."""
    from dfs import results_update
    from dfs.sim import retro

    contests = [c for c in retro.load_contests() if through is None or c.week <= through]
    scored = results_update.load_all_scored(season)
    frames, counts = [], dict.fromkeys(FORMATS, 0)
    for week in sorted({c.week for c in contests}):
        try:
            path, _ = retro.edge_snapshot_before(week, season)
        except FileNotFoundError:
            continue
        edge = retro.with_calpts(pd.read_csv(path), scored, week, season)
        frame, n = week_frame(edge, contests, week)
        frames.append(frame)
        for fmt in FORMATS:
            counts[fmt] += n[fmt]
    if not frames:
        return pd.DataFrame(), counts
    return pd.concat(frames, ignore_index=True), counts


# ---------------------------------------------------------------------------------------------------------
# Scoring (Model Check)
# ---------------------------------------------------------------------------------------------------------

SCORE_COLUMNS = ["Week", "Format", "Model", "n", "MAE", "Chalk n", "Chalk bias"]


def _score(
    actual: np.ndarray, predicted: np.ndarray, tffb: np.ndarray, fmt: str
) -> tuple[int, float, int, float]:
    relevant = (actual >= RELEVANT_OWN) | (tffb >= RELEVANT_OWN)
    chalk = actual >= CHALK_OWN[fmt]
    mae = float(np.abs(actual - predicted)[relevant].mean()) if relevant.any() else math.nan
    bias = float((predicted - actual)[chalk].mean()) if chalk.any() else math.nan
    return int(relevant.sum()), mae, int(chalk.sum()), bias


def score_weeks(frame: pd.DataFrame, curve=None) -> pd.DataFrame:
    """Each week judged on a model fitted on the weeks before it: TFFB's Own% as is, `curve` (the double-up
    curve, a function of projected Own%; cash only) and ours. Errors and bias in ownership points (0.05 =
    5 points). A week needs an earlier week of the same format."""
    rows = []
    for fmt in FORMATS:
        weeks = weeks_with(frame, fmt)
        for week in weeks:
            earlier = [w for w in weeks if w < week]
            if not earlier:
                continue
            train = frame[frame["week"].isin(earlier)]
            model = FieldOwnModel(
                exponent={fmt: fit_exponent(train, fmt)},
                totals={fmt: position_totals(train, fmt)},
                response=0.0,
                weeks=tuple(earlier),
            )
            test = frame[frame["week"] == week]
            actual = _target(test, fmt)
            tffb = _num(test, "Own%").fillna(0.0).to_numpy()
            candidates = [("TFFB Own%", tffb)]
            if fmt == "cash" and curve is not None:
                candidates.append(("Double-up curve", np.array([curve(x) for x in tffb])))
            candidates.append(("Ours", predict(test, model, fmt).to_numpy()))
            for label, predicted in candidates:
                n, mae, chalk_n, bias = _score(actual, predicted, tffb, fmt)
                rows.append([week, fmt, label, n, mae, chalk_n, bias])
    return pd.DataFrame(rows, columns=SCORE_COLUMNS)


def score_summary(scores: pd.DataFrame) -> pd.DataFrame:
    """`score_weeks` averaged over its weeks, per format and model (the Model Check table)."""
    if scores.empty:
        return pd.DataFrame(columns=["Format", "Model", "Weeks", "MAE", "Chalk bias"])
    grouped = scores.groupby(["Format", "Model"], sort=False)
    return grouped.agg(
        Weeks=("Week", "nunique"), MAE=("MAE", "mean"), **{"Chalk bias": ("Chalk bias", "mean")}
    ).reset_index()


GAP_COLUMNS = ["Week", "n", "Gap share", "Top fifth", "Bottom fifth"]


def disagreement(snapshots: pd.DataFrame, scored: pd.DataFrame) -> pd.DataFrame:
    """When CalPts and TFFB disagreed, who was right? Per week and overall, over rosterable players
    projected at `GAP_MIN_OWN`+ who played: the share of the gap (`CalPts - ProjPts`) that came true (the
    slope of actual minus ProjPts on the gap; 1 = CalPts was right, 0 = TFFB was), and actual minus ProjPts
    for the fifth we liked most and the fifth we liked least. `snapshots` are pre-lock edge frames with a
    `week` column; `scored` has `week`, `Id`, `DkActual`."""
    actual = scored[["week", "Id", "DkActual"]].copy()
    actual["Id"] = actual["Id"].astype(str)
    df = snapshots.assign(Id=snapshots["Id"].astype(str)).merge(actual, on=["week", "Id"], how="inner")
    cal, proj, act = _num(df, "CalPts"), _num(df, "ProjPts"), _num(df, "DkActual")
    keep = act.notna() & cal.notna() & proj.notna() & (_num(df, "Own%") >= GAP_MIN_OWN) & eligible(df)
    df = df[keep].assign(gap=(cal - proj)[keep], miss=(act - proj)[keep])

    def row(label, g: pd.DataFrame) -> list:
        if len(g) < 10 or g["gap"].var() == 0:
            return [label, len(g), math.nan, math.nan, math.nan]
        slope = float(np.polyfit(g["gap"], g["miss"], 1)[0])
        lo, hi = g["gap"].quantile([0.2, 0.8])
        top, bottom = g.loc[g["gap"] >= hi, "miss"].mean(), g.loc[g["gap"] <= lo, "miss"].mean()
        return [label, len(g), slope, top, bottom]

    rows = [row(str(int(w)), g) for w, g in df.groupby("week")]
    if rows:
        rows.append(row("All", df))
    return pd.DataFrame(rows, columns=GAP_COLUMNS)
