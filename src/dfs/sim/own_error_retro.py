"""The ownership error model checked against Sam's real contests, held out by week.

Local and read-only: the standings, salary snapshots and EdgeRaw snapshots are Sam's files under `data/`
(never in git). For every week with standings, a model fitted on the OTHER weeks forecasts that week's
ownership from the last pre-lock snapshot, and the real contests' player counts judge it:

1. `compare_families`: the held-out composite log-likelihood of the contests' counts under a point forecast
   (today's sim), a Dirichlet, a log-normal and the hybrid log-normal (`own_error`), with the mean the same
   in
   all of them (and raw TFFB / the double-up curve as mean-model comparisons).
2. `residual_structure`: what the likelihood cannot see: how a QB's error moves with his receivers', the
   week-wide tilt toward the chalk.
3. `field_mean_check`: the end-to-end test. A contest's field mean is `sum(share_i * points_i)`; with the
   week's real points and ownership drawn from the model (plus the contest's own finite-size noise), where
   does
   the contest's real field mean fall? A calibrated model puts it inside the 80% band four times in five.

Run it: `python -m dfs.sim.own_error_retro` (a few minutes). Part 2 (the sim) and Part 3 (stake / field
size) build on `load_weeks`, `fold` and the fits here."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
from scipy.optimize import minimize

from dfs import field_own
from dfs.sim import contest_retro, dups, own_error

MIN_ENTRIES_POOLED = 700  # a contest this big has little finite-size noise: used for the pooled residuals
RESIDUAL_MIN_MU = 0.02  # residuals (log actual / forecast) are taken on players forecast at 2%+
BAND = (0.10, 0.90)  # the 80% band the field-mean check counts
CHECK_DRAWS = 1000


@dataclass
class WeekData:
    """One week as it stood at the lock, with the real contests played in it."""

    week: int
    frame: pd.DataFrame  # contest_retro.week_slate_frame (name, position, team, ..., tffb)
    contests: list  # contest_retro.RealContest

    def edge_like(self) -> pd.DataFrame:
        """The frame in the shape `field_own` reads (Name, Position, Own%, Avail)."""
        f = self.frame
        return pd.DataFrame(
            {
                "Name": f["display"].to_numpy(),
                "Position": f["position"].to_numpy(),
                "Team": f["team"].to_numpy(),
                "Own%": f["tffb"].to_numpy(),
                "Avail": "",
            }
        )

    def by_format(self, fmt: str) -> list:
        return [c for c in self.contests if c.fmt == fmt]

    def counts(self, contest) -> np.ndarray:
        """Lineups holding each frame player in this contest: `round(%Drafted * entries)`."""
        share = self.frame["name"].map(contest.own).fillna(0.0).to_numpy()
        return np.round(share * contest.entries)

    def pooled_share(self, fmt: str) -> np.ndarray | None:
        """The entries-weighted real ownership over the week's big contests of `fmt` (None if none)."""
        big = [c for c in self.by_format(fmt) if c.entries >= MIN_ENTRIES_POOLED]
        if not big:
            return None
        w = np.array([c.entries for c in big], dtype=float)
        shares = np.column_stack([self.frame["name"].map(c.own).fillna(0.0).to_numpy() for c in big])
        return shares @ (w / w.sum())

    def points(self, contest) -> np.ndarray:
        """The week's real DK points per frame player in this contest's own table (0 if not listed)."""
        return self.frame["name"].map(contest.fpts).fillna(0.0).to_numpy()


def load_weeks(weeks: set[int] | None = None, season: int = contest_retro.SEASON) -> dict[int, WeekData]:
    contests = contest_retro.load_real_contests(weeks)
    out: dict[int, WeekData] = {}
    for week in sorted({c.week for c in contests}):
        try:
            frame = contest_retro.week_slate_frame(week, season)
        except FileNotFoundError:
            continue
        out[week] = WeekData(week, frame, [c for c in contests if c.week == week])
    return out


# ---------------------------------------------------------------------------------------------------------
# The mean (field_own's sharpened TFFB), fitted on the training weeks
# ---------------------------------------------------------------------------------------------------------


def formats_in(data: dict[int, WeekData]) -> dict[str, list[int]]:
    return {fmt: sorted(w for w, d in data.items() if d.by_format(fmt)) for fmt in own_error.FORMATS}


def fit_mean(data: dict[int, WeekData], weeks: list[int], fmt: str) -> field_own.FieldOwnModel:
    """`field_own`'s exponent and position totals for `fmt` on `weeks` (the response is not used)."""
    parts = [field_own.week_frame(data[w].edge_like(), data[w].contests, w)[0] for w in weeks]
    frame = pd.concat(parts, ignore_index=True)
    return field_own.FieldOwnModel(
        exponent={fmt: field_own.fit_exponent(frame, fmt)},
        totals={fmt: field_own.position_totals(frame, fmt)},
        response=0.0,
        weeks=tuple(weeks),
    )


def mean_shares(week: WeekData, model, fmt: str, kind: str = "softmax") -> np.ndarray:
    """The forecast of the week's ownership in a format. `model` is a fitted `own_error.MeanModel` (pooled
    over the week's big contests), or a `field_own.FieldOwnModel` read as `kind`: `softmax` (the default),
    `raw` (TFFB's Own% scaled to the position totals) or `curve` (the double-up curve, scaled alike)."""
    if isinstance(model, own_error.MeanModel):  # the fitted mean: its forecast for the pooled big contests
        big = [c for c in week.by_format(fmt) if c.entries >= MIN_ENTRIES_POOLED] or week.by_format(fmt)
        if not big:
            return model.shares(week.frame["tffb"].to_numpy(), week.frame["position"].to_numpy())
        w = np.array([c.entries for c in big], dtype=float)
        return np.column_stack([contest_mean(week, c, model) for c in big]) @ (w / w.sum())
    edge = week.edge_like().assign(week=week.week)
    if kind == "softmax":
        return field_own.predict(edge, model, fmt).to_numpy()
    own = edge["Own%"].fillna(0.0).to_numpy(dtype=float)
    base = np.array([dups.double_up_ownership(x) for x in own]) if kind == "curve" else own
    base = np.maximum(base, own_error.MU_FLOOR)
    out = np.zeros(len(edge))
    pos = week.frame["position"].to_numpy()
    for p in own_error.POSITIONS:
        m = pos == p
        if m.any():
            out[m] = base[m] / base[m].sum() * model.totals[fmt][p]
    return np.minimum(out, own_error.MAX_OWN)


# ---------------------------------------------------------------------------------------------------------
# The likelihood, fitted and compared
# ---------------------------------------------------------------------------------------------------------


def week_loglik(
    week: WeekData, mu: np.ndarray, fmt: str, totals: dict[str, float], family: str, params: tuple
) -> float:
    """The composite log-likelihood of all the week's contests of `fmt` (players forecast at 1%+)."""
    sel = mu >= own_error.MIN_MU
    pos = week.frame["position"].to_numpy()[sel]
    mass = own_error.theta_mass(family, mu[sel], pos, totals, params)
    return float(
        sum(own_error.count_loglik(week.counts(c)[sel], c.entries, mass).sum() for c in week.by_format(fmt))
    )


# (start, transform) per family: the optimiser works on logs so every parameter stays positive
_START = {
    "dirichlet": ([np.log(100.0)], lambda p: (float(np.exp(p[0])),)),
    "lognormal": ([np.log(0.5)], lambda p: (float(np.exp(p[0])),)),
    "hybrid": ([np.log(0.05), np.log(0.01)], lambda p: (float(np.exp(p[0])), float(np.exp(p[1])))),
}


def fit_family(
    data: dict[int, WeekData], train: list[int], fmt: str, family: str, mu_for, totals: dict[str, float]
) -> tuple[float, ...]:
    """The family's parameters by maximum composite likelihood on `train` weeks. `mu_for(week)` is the
    forecast of the week's ownership (the mean model fitted on the same weeks), `totals` its position
    totals."""
    if family == "point":
        return ()
    mus = {w: mu_for(w) for w in train}
    start, to_params = _START[family]

    def loss(p: np.ndarray) -> float:
        params = to_params(p)
        return -sum(week_loglik(data[w], mus[w], fmt, totals, family, params) for w in train)

    res = minimize(loss, start, method="Nelder-Mead", options={"xatol": 1e-2, "fatol": 0.5, "maxiter": 80})
    return to_params(res.x)


@dataclass
class Fold:
    """Everything fitted without one week: the mean and the error parameters per format."""

    held_out: int
    mean: dict[str, field_own.FieldOwnModel]
    params: dict[str, dict[str, tuple]]  # format -> family -> parameters
    train: dict[str, list[int]]


def fold(
    data: dict[int, WeekData],
    held_out: int,
    *,
    families=("dirichlet", "lognormal", "hybrid"),
    kind="softmax",
    earlier_only: bool = False,
) -> Fold:
    """The model fitted without `held_out`: on every other week, or (`earlier_only`) the weeks before it."""
    avail = formats_in(data)
    mean, params, train = {}, {}, {}
    for fmt in own_error.FORMATS:
        weeks = [w for w in avail[fmt] if (w < held_out if earlier_only else w != held_out)]
        if not weeks:
            continue
        train[fmt] = weeks
        mean[fmt] = fit_mean(data, weeks, fmt)
        params[fmt] = {}
        for family in families:
            params[fmt][family] = fit_family(
                data,
                weeks,
                fmt,
                family,
                lambda w, fmt=fmt, kind=kind: mean_shares(data[w], mean[fmt], fmt, kind),
                mean[fmt].totals[fmt],
            )
    return Fold(held_out, mean, params, train)


def compare_families(
    data: dict[int, WeekData],
    *,
    families=("point", "dirichlet", "lognormal", "hybrid"),
    kind="softmax",
    earlier_only: bool = False,
) -> tuple[pd.DataFrame, dict[tuple[int, str], Fold]]:
    """Held-out composite log-likelihood by format, family and week (higher is better), and the folds."""
    avail = formats_in(data)
    rows, folds = [], {}
    for week in sorted(data):
        f = fold(
            data, week, families=[x for x in families if x != "point"], kind=kind, earlier_only=earlier_only
        )
        for fmt in own_error.FORMATS:
            if week not in avail[fmt] or fmt not in f.mean:
                continue
            mu = mean_shares(data[week], f.mean[fmt], fmt, kind)
            totals = f.mean[fmt].totals[fmt]
            folds[(week, fmt)] = f
            for family in families:
                params = () if family == "point" else f.params[fmt][family]
                rows.append(
                    [week, fmt, family, week_loglik(data[week], mu, fmt, totals, family, params), params]
                )
    return pd.DataFrame(rows, columns=["week", "format", "family", "loglik", "params"]), folds


# ---------------------------------------------------------------------------------------------------------
# What the marginal likelihood cannot see: how errors move together
# ---------------------------------------------------------------------------------------------------------


def residual_table(data: dict[int, WeekData], weeks: list[int], fmt: str, models: dict) -> pd.DataFrame:
    """Per week, the players forecast at 2%+ with their pooled real share, the log residual `log(actual /
    forecast)` and it minus the week-position mean (`e0`). `models[week]` is the mean model for that week
    (fitted without it)."""
    rows = []
    for w in weeks:
        pooled = data[w].pooled_share(fmt)
        if pooled is None:
            continue
        mu = mean_shares(data[w], models[w], fmt)
        f = data[w].frame
        keep = (mu >= RESIDUAL_MIN_MU) & (pooled > 0)
        t = pd.DataFrame(
            {
                "week": w,
                "name": f["name"][keep].to_numpy(),
                "position": f["position"][keep].to_numpy(),
                "team": f["team"][keep].to_numpy(),
                "mu": mu[keep],
                "actual": pooled[keep],
            }
        )
        rows.append(t)
    if not rows:
        return pd.DataFrame()
    t = pd.concat(rows, ignore_index=True)
    t["e"] = np.log(t["actual"] / t["mu"])
    t["e0"] = t["e"] - t.groupby(["week", "position"])["e"].transform("mean")
    t["lmu"] = np.log(t["mu"])
    return t


def team_correlation(res: pd.DataFrame) -> tuple[float, int]:
    """The correlation of a QB's residual with his team's WR / TE residuals (pairs pooled), and the pairs."""
    qb = res[res["position"] == "QB"].set_index(["week", "team"])["e0"]
    rec = res[res["position"].isin(["WR", "TE"])]
    pairs = [
        (qb[(w, t)], e)
        for w, t, e in zip(rec["week"], rec["team"], rec["e0"], strict=True)
        if (w, t) in qb.index
    ]
    if len(pairs) < 5:
        return 0.0, len(pairs)
    a = np.array(pairs)
    return float(np.corrcoef(a[:, 0], a[:, 1])[0, 1]), len(pairs)


def week_tilts(res: pd.DataFrame) -> dict[int, float]:
    """Each week's slope of the residual on the centred log forecast: how far the week leaned into (+) or away
    from (-) its chalk, beyond the mean model."""
    out = {}
    for w, g in res.groupby("week"):
        x = g["lmu"] - g.groupby("position")["lmu"].transform("mean")
        if len(g) >= 10 and x.var() > 0:
            out[int(w)] = float(np.polyfit(x, g["e0"], 1)[0])
    return out


def fit_dependence(res: pd.DataFrame) -> tuple[float, float]:
    """`(team_corr, tilt_sd)` from a residual table: the QB-receiver correlation (floored at 0) and the sd of
    the weekly tilts about their mean (0 with fewer than two weeks). An empty table (no week with a big
    contest of the format, e.g. Week 4's model, trained on Week 2 alone) gives `(0, 0)`."""
    if res.empty:
        return 0.0, 0.0
    corr, _ = team_correlation(res)
    tilts = list(week_tilts(res).values())
    sd = float(np.std(tilts, ddof=1)) if len(tilts) >= 2 else 0.0
    return max(corr, 0.0), sd


# ---------------------------------------------------------------------------------------------------------
# The end-to-end check: the field mean
# ---------------------------------------------------------------------------------------------------------


def field_mean_draws(
    week: WeekData,
    contest,
    mu: np.ndarray,
    totals: dict[str, float],
    error: own_error.FormatError | None,
    *,
    draws: int = CHECK_DRAWS,
    seed: int = 0,
) -> np.ndarray:
    """Draws of the contest's field mean (points) from the forecast `mu`, its real points, and the model's
    ownership error (`error=None`: none, only the contest's own finite size). Each draw is a true ownership
    vector, then `entries` lineups' worth of Binomial noise per player."""
    rng = np.random.default_rng(seed)
    pts = week.points(contest)
    pos = week.frame["position"].to_numpy()
    team = week.frame["team"].to_numpy()
    if error is None:
        theta = np.broadcast_to(mu, (draws, len(mu)))
    else:
        theta = own_error.draw_shares(mu, pos, team, totals, error, draws, rng)
    held = rng.binomial(contest.entries, np.clip(theta, 0.0, 1.0)) / contest.entries
    return held @ pts


def field_mean_check(
    data: dict[int, WeekData], folds: dict[tuple[int, str], Fold], *, family: str = "hybrid", seed: int = 0
) -> pd.DataFrame:
    """For every real contest: its real field mean (the mean of its entries' scores), the model's draws of it
    with and without ownership error, and where the real one falls. Dependence parameters come from the weeks
    other than the contest's."""
    rows = []
    for (week, fmt), f in sorted(folds.items()):
        mu = mean_shares(data[week], f.mean[fmt], fmt)
        totals = f.mean[fmt].totals[fmt]
        c0, c1 = f.params[fmt][family]
        models = {
            w: fit_mean(data, [x for x in f.train[fmt] if x != w] or f.train[fmt], fmt) for w in f.train[fmt]
        }
        res = residual_table(data, f.train[fmt], fmt, models)
        corr, tilt = fit_dependence(res) if not res.empty else (0.0, 0.0)
        variants = {
            "none": None,
            "independent": own_error.FormatError(c0, c1),
            "team": own_error.FormatError(c0, c1, team_corr=corr),
            "team+tilt": own_error.FormatError(c0, c1, team_corr=corr, tilt_sd=tilt),
        }
        for contest in data[week].by_format(fmt):
            real = float(np.mean(contest.points))
            for name, error in variants.items():
                d = field_mean_draws(data[week], contest, mu, totals, error, seed=seed)
                rows.append(
                    {
                        "week": week,
                        "format": fmt,
                        "contest": contest.contest_id,
                        "entries": contest.entries,
                        "fee": contest.fee,
                        "variant": name,
                        "real": real,
                        "model_mean": float(d.mean()),
                        "model_sd": float(d.std()),
                        "z": (real - float(d.mean())) / max(float(d.std()), 1e-9),
                        "inside": bool(np.quantile(d, BAND[0]) <= real <= np.quantile(d, BAND[1])),
                        "pit": float((d <= real).mean()),
                        "team_corr": corr,
                        "tilt_sd": tilt,
                    }
                )
    return pd.DataFrame(rows)


def level_table(res: pd.DataFrame, errors: dict[str, own_error.FormatError]) -> pd.DataFrame:
    """The observed sd of the residual by forecast level against the model's sigma there (`res` carries a
    `format` column; `errors` the fitted `FormatError` per format)."""
    bins = [RESIDUAL_MIN_MU, 0.04, 0.07, 0.12, 0.20, 0.35, 1.0]
    r = res.assign(
        level=pd.cut(res["mu"], bins),
        sigma=[errors[f].sigma(m) for f, m in zip(res["format"], res["mu"], strict=True)],
    )
    return (
        r.groupby(["format", "level"], observed=True)
        .agg(n=("e", "size"), mean_e=("e", "mean"), sd_e=("e0", "std"), model_sigma=("sigma", "mean"))
        .reset_index()
    )


# The mean each format draws around, chosen by held-out fit (`compare_means`, docs/SIM.md): GPPs a power of
# TFFB's Own% that flattens as the field grows, double-ups a power with curvature
CHOSEN_SPECS = {"gpp": "size", "cash": "quad"}
FOLD_DIR = own_error.MODEL_FILE.parent / "own_error"


def fit_model(
    data: dict[int, WeekData], family: str = "hybrid", specs: dict[str, str] | None = None
) -> own_error.OwnershipErrorModel:
    """The model on every week in `data`: per format the chosen mean (`specs`, default `CHOSEN_SPECS`; None
    for a spec name keeps Part 1's field_own mean) with the hybrid error's `(c0, c1)` by composite likelihood,
    and `team_corr` / `tilt_sd` by moments on residuals whose mean never saw their own week. What the sim and
    the backtest draw from."""
    specs = dict(CHOSEN_SPECS if specs is None else specs)
    avail = formats_in(data)
    formats, means = {}, {}
    for fmt in own_error.FORMATS:
        weeks = avail[fmt]
        if not weeks:
            continue
        spec = specs.get(fmt)
        if spec is None:
            mean = fit_mean(data, weeks, fmt)
            c0, c1 = fit_family(
                data,
                weeks,
                fmt,
                family,
                lambda w, fmt=fmt, m=mean: mean_shares(data[w], m, fmt),
                mean.totals[fmt],
            )
            models = {w: fit_mean(data, [x for x in weeks if x != w] or weeks, fmt) for w in weeks}
        else:
            mean, (c0, c1) = fit_joint(data, weeks, fmt, spec)
            means[fmt] = mean
            models = {
                w: fit_joint(data, [x for x in weeks if x != w], fmt, spec)[0] if len(weeks) > 1 else mean
                for w in weeks
            }
        corr, tilt = fit_dependence(residual_table(data, weeks, fmt, models))
        formats[fmt] = own_error.FormatError(c0=c0, c1=c1, team_corr=corr, tilt_sd=tilt)
    return own_error.OwnershipErrorModel(formats=formats, weeks=tuple(sorted(data)), means=means)


def model_without(
    week: int, *, earlier_only: bool = False, data: dict[int, WeekData] | None = None, folder=None
) -> own_error.OwnershipErrorModel | None:
    """The model fitted without `week`: on every other week with standings, or (`earlier_only`) the weeks
    before it; None when there is none to fit on. Cached per week in `data/model_cache/own_error/` (refitted
    when the weeks on hand change)."""
    from pathlib import Path

    folder = Path(folder) if folder is not None else FOLD_DIR
    data = load_weeks() if data is None else data
    train = {w: d for w, d in data.items() if (w < week if earlier_only else w != week)}
    if not train:
        return None
    path = folder / f"{'before' if earlier_only else 'without'}_wk{week:02d}.json"
    cached = own_error.load(path)
    if cached is not None and cached.weeks == tuple(sorted(train)):
        return cached
    model = fit_model(train)
    own_error.save(model, path)
    return model


def model_before(week: int) -> own_error.OwnershipErrorModel | None:
    """What a backtest of `week` may know: the model fitted on the weeks before it."""
    return model_without(week, earlier_only=True)


# ---------------------------------------------------------------------------------------------------------
# The mean, chosen by held-out fit (Part 2's first step; Part 3's stake and size terms are in the same family)
# ---------------------------------------------------------------------------------------------------------

# spec -> the MeanModel fields fitted (with c0, c1) by composite likelihood; "tffb" fixes k = 1, "power_ce"
# fixes k at field_own's cross-entropy fit (today's GppOwn / CashOwn)
MEAN_SPECS: dict[str, tuple[str, ...]] = {
    "tffb": (),
    "power_ce": (),
    "power": ("k",),
    "quad": ("k", "curve"),
    "fee": ("k", "k_fee"),
    "size": ("k", "k_size"),
    "stake": ("k", "k_fee", "k_size"),
}
CHALK_CUT = {"gpp": 0.20, "cash": 0.30}  # field_own's chalk lines, for the bias rows
EXPONENT_RANGE = (0.5, 4.0)  # as field_own's bounds on k
LOG_C_RANGE = (np.log(1e-4), np.log(5.0))  # c0, c1 kept in a sane range
SCORED_OWN = 0.05  # MAE on players owned 5%+ (actual) or projected 5%+ (TFFB), as field_own scores


def contest_mean(week: WeekData, contest, mean: own_error.MeanModel) -> np.ndarray:
    return mean.shares(
        week.frame["tffb"].to_numpy(),
        week.frame["position"].to_numpy(),
        fee=contest.fee,
        entries=contest.entries,
    )


def contest_loglik(week: WeekData, contest, mean: own_error.MeanModel, c0: float, c1: float) -> float:
    """The contest's composite log-likelihood on the players TFFB projects at 1%+: a set fixed by the INPUT,
    so every mean is scored on the same players (selecting on the candidate's own forecast lets an extreme
    exponent drop players from the score and win by it)."""
    mu = contest_mean(week, contest, mean)
    sel = week.frame["tffb"].fillna(0.0).to_numpy() >= own_error.MIN_MU
    mass = own_error.theta_mass(
        "hybrid", mu[sel], week.frame["position"].to_numpy()[sel], mean.totals, (c0, c1)
    )
    return float(own_error.count_loglik(week.counts(contest)[sel], contest.entries, mass).sum())


def fit_joint(
    data: dict[int, WeekData], train: list[int], fmt: str, spec: str
) -> tuple[own_error.MeanModel, tuple[float, float]]:
    """The mean `spec` (`MEAN_SPECS`) and the hybrid error's `(c0, c1)` fitted together on `train` weeks by
    composite likelihood over every contest of `fmt` (each contest with its own stake and size)."""
    base = fit_mean(data, train, fmt)
    totals = base.totals[fmt]
    k0 = 1.0 if spec == "tffb" else float(base.exponent[fmt])
    free = MEAN_SPECS[spec]
    contests = [(data[w], c) for w in train for c in data[w].by_format(fmt)]

    def build(p: np.ndarray) -> tuple[own_error.MeanModel, float, float]:
        values = dict(zip(free, p[2:], strict=True))
        k = float(np.clip(values.pop("k", k0), *EXPONENT_RANGE))
        mean = own_error.MeanModel(k=k, totals=totals, **values)
        return mean, float(np.exp(np.clip(p[0], *LOG_C_RANGE))), float(np.exp(np.clip(p[1], *LOG_C_RANGE)))

    def loss(p: np.ndarray) -> float:
        mean, c0, c1 = build(p)
        return -sum(contest_loglik(w, c, mean, c0, c1) for w, c in contests)

    start = np.array([np.log(0.1), np.log(0.01), *[k0 if f == "k" else 0.0 for f in free]])
    res = minimize(loss, start, method="Nelder-Mead", options={"xatol": 1e-3, "fatol": 0.5, "maxiter": 600})
    mean, c0, c1 = build(res.x)
    return mean, (c0, c1)


def _scores(actual: np.ndarray, mu: np.ndarray, tffb: np.ndarray, fmt: str) -> dict[str, float]:
    """MAE (points) on the players that matter, and the chalk bias (forecast - actual, points) two ways: on
    the players who FINISHED as chalk (selected on the outcome, as field_own scores it) and on the players the
    forecast CALLED chalk (selected on the forecast)."""
    cut = CHALK_CUT[fmt]
    rel = (actual >= SCORED_OWN) | (tffb >= SCORED_OWN)
    by_actual, by_forecast = actual >= cut, mu >= cut
    return {
        "mae": 100 * float(np.abs(mu - actual)[rel].mean()),
        "bias_on_actual": 100 * float((mu - actual)[by_actual].mean()) if by_actual.any() else np.nan,
        "n_on_forecast": int(by_forecast.sum()),
        "bias_on_forecast": 100 * float((mu - actual)[by_forecast].mean()) if by_forecast.any() else np.nan,
    }


def compare_means(
    data: dict[int, WeekData], specs: tuple[str, ...] = tuple(MEAN_SPECS), *, earlier_only: bool = False
) -> tuple[pd.DataFrame, dict[tuple[int, str, str], tuple[own_error.MeanModel, tuple[float, float]]]]:
    """Every mean spec fitted without each week and judged on each of its contests: composite log-likelihood,
    MAE and the two chalk biases. Returns the table (one row per contest and spec) and the fits."""
    avail = formats_in(data)
    rows, fits = [], {}
    for fmt in own_error.FORMATS:
        for week in avail[fmt]:
            train = [w for w in avail[fmt] if (w < week if earlier_only else w != week)]
            if not train:
                continue
            for spec in specs:
                mean, (c0, c1) = fit_joint(data, train, fmt, spec)
                fits[(week, fmt, spec)] = (mean, (c0, c1))
                wd = data[week]
                tffb = wd.frame["tffb"].fillna(0.0).to_numpy()
                for c in wd.by_format(fmt):
                    actual = wd.frame["name"].map(c.own).fillna(0.0).to_numpy()
                    mu = contest_mean(wd, c, mean)
                    rows.append(
                        {
                            "week": week,
                            "format": fmt,
                            "spec": spec,
                            "contest": c.contest_id,
                            "entries": c.entries,
                            "fee": c.fee,
                            "loglik": contest_loglik(wd, c, mean, c0, c1),
                            **_scores(actual, mu, tffb, fmt),
                            "k_c": mean.exponent(c.fee, c.entries),
                        }
                    )
    return pd.DataFrame(rows), fits


def summarize_means(table: pd.DataFrame) -> pd.DataFrame:
    """Per format and spec: the held-out log-likelihood summed (and against TFFB as is), the weeks it beats
    TFFB in, the mean MAE and chalk biases over contests."""
    by_week = table.groupby(["format", "spec", "week"], sort=False)["loglik"].sum().unstack("spec")
    out = (
        table.groupby(["format", "spec"], sort=False)
        .agg(
            loglik=("loglik", "sum"),
            mae=("mae", "mean"),
            bias_on_actual=("bias_on_actual", "mean"),
            bias_on_forecast=("bias_on_forecast", "mean"),
        )
        .reset_index()
    )
    base = out[out["spec"] == "tffb"].set_index("format")["loglik"]
    out["vs tffb"] = out["loglik"] - out["format"].map(base)
    out["weeks better"] = [
        f"{int((by_week.loc[f][s] > by_week.loc[f]['tffb']).sum())}/{len(by_week.loc[f])}"
        for f, s in zip(out["format"], out["spec"], strict=True)
    ]
    return out


def summarize_field_mean(table: pd.DataFrame) -> pd.DataFrame:
    """Per format and variant: the contests inside the 80% band, the rms z-score (1.0 = calibrated), the mean
    model sd (points) and the mean absolute miss (points)."""
    g = table.assign(miss=(table["real"] - table["model_mean"]).abs()).groupby(
        ["format", "variant"], sort=False
    )
    return g.agg(
        contests=("z", "size"),
        inside=("inside", "mean"),
        rms_z=("z", lambda z: float(np.sqrt(np.mean(np.square(z))))),
        mean_z=("z", "mean"),
        model_sd=("model_sd", "mean"),
        abs_miss=("miss", "mean"),
    ).reset_index()


def summarize_families(table: pd.DataFrame) -> pd.DataFrame:
    """Held-out composite log-likelihood summed over weeks, per format and family, and minus the point
    forecast's."""
    s = table.groupby(["format", "family"], sort=False)["loglik"].sum().reset_index()
    base = s[s["family"] == "point"].set_index("format")["loglik"]
    s["vs point"] = s["loglik"] - s["format"].map(base)
    return s


def main() -> None:  # pragma: no cover - the study, run by hand
    pd.set_option("display.width", 200)
    pd.set_option("display.max_columns", 30)
    data = load_weeks()
    print(f"weeks {sorted(data)}; contests {sum(len(d.contests) for d in data.values())}")
    table, folds = compare_families(data)
    print(summarize_families(table).round(1).to_string(index=False))
    print(table.assign(loglik=table["loglik"].round(1)).to_string(index=False))
    check = field_mean_check(data, folds)
    print(summarize_field_mean(check).round(3).to_string(index=False))
    model = fit_model(data)
    print(model.to_json())


if __name__ == "__main__":  # pragma: no cover
    main()
