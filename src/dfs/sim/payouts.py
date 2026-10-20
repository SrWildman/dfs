"""Payout tables: what each finishing place of a contest pays, fitted from Sam's own contest history.

No DraftKings fetching: the only inputs are the history log's columns for each entry (`Place`,
`Contest_Entries`, `Places_Paid`, `Prize_Pool`, `Entry_Fee`, winnings). A contest's full payout table is never
in the log, so a curve is fitted to the places Sam actually landed in and scaled so it pays out exactly the
contest's prize pool.

**Flat contests** (Double Up, 50/50, the Multipliers) pay every paid place the same: `prize_pool /
places_paid`. On his 2021-26 history that reproduces every one of his paid entries to the cent.

**GPPs** (SE GPP, N-max GPP) pay a top-heavy curve down to a min-cash floor. The form chosen from the data
(`docs/SIM.md`, "Contest simulator") is a *shifted power law* in the place `r` of `M` paid places:

    pay(r) = c * fee + A * (r ** -b - M ** -b)        r = 1 .. M

so the last paid place pays exactly the min cash `c * fee`, and `A` is set by the prize pool (the table sums
to `prize_pool`). Two numbers per contest: `b`, the steepness (one value for every GPP, `PAYOUT_EXPONENT`,
fitted; stable between 1.002 and 1.028 when any one week is held out), and `c`, the min-cash multiple, which
DraftKings sets per contest (1.5x on the $1 contests, 2x on the Hundo and the Huddle, $25 on a $12 or $15
fee). `c` is read from his own entries in the bottom half of a series' paid places (`fit_min_cash`; the lowest
multiple there is the min-cash tier), falling back to the median for contests of the same fee, then to the
format's median.

A power law with a hard floor (`max(c * fee, A * r ** -b)`) was the alternative; it fits worse held out by
week (per-entry error $1.42 against $1.03). Pure functions; the loaders take the typed log (`history.typed`).
"""

from __future__ import annotations

import math
import re
from collections.abc import Iterable
from dataclasses import dataclass, field

import numpy as np
import pandas as pd
from scipy.optimize import minimize_scalar

from dfs.contests.series import DOUBLE_UP, FIFTY_FIFTY, MULTIPLIER, N_MAX_GPP, SE_GPP

FLAT_FORMATS = frozenset({DOUBLE_UP, FIFTY_FIFTY, MULTIPLIER})
GPP_FORMATS = frozenset({SE_GPP, N_MAX_GPP})
FORMS = ("shifted", "floored")
# Fitted on Sam's paid GPP entries 2022-26 (267 entries in 156 contests): `fit_exponent`.
PAYOUT_EXPONENT = 1.011
DEFAULT_MIN_CASH = 1.75  # used only when neither the series, its fee, nor its format has a paid entry
MIN_CASH_SHARE = 0.98  # the min-cash floor never takes more than this share of the prize pool
LOW_PAID_PLACES = 0.5  # entries placed in the bottom half of the paid places read the min-cash tier
EXPONENT_BOUNDS = (0.2, 2.0)
_ENTRY_MAX = re.compile(r"\[\s*(\d+)\s*entry max\s*\]", re.IGNORECASE)


@dataclass(frozen=True)
class ContestKind:
    """One contest to simulate: its size, its payout inputs and its format. `min_cash` is a multiple of the
    fee (None: flat formats, or not known yet). `max_entries` is the contest's per-user limit."""

    series: str
    format: str
    entries: int
    places_paid: int
    prize_pool: float
    fee: float
    min_cash: float | None = None
    max_entries: int = 1

    @property
    def flat(self) -> bool:
        return self.format in FLAT_FORMATS

    @property
    def cash(self) -> bool:
        """A cash contest (Double Up, 50/50): its field is the double-up field."""
        return self.format in (DOUBLE_UP, FIFTY_FIFTY)


def max_entries_of(series: str) -> int:
    """The per-user entry limit a series name states (`[3 Entry Max]` -> 3, `[Single Entry]` -> 1); 1 when the
    name says nothing."""
    found = _ENTRY_MAX.search(series)
    if found:
        return int(found.group(1))
    return 1


def payout_table(
    kind: ContestKind, *, exponent: float = PAYOUT_EXPONENT, form: str = "shifted"
) -> np.ndarray:
    """Dollars paid to each place 1 .. `places_paid` (it sums to the prize pool). See the module docstring."""
    m = int(kind.places_paid)
    if m <= 0 or kind.prize_pool <= 0:
        return np.zeros(0)
    if kind.flat:
        return np.full(m, kind.prize_pool / m)
    c = DEFAULT_MIN_CASH if kind.min_cash is None else float(kind.min_cash)
    floor = min(c * kind.fee, MIN_CASH_SHARE * kind.prize_pool / m) if kind.fee > 0 else 0.0
    if m == 1:
        return np.array([kind.prize_pool])
    power = np.arange(1, m + 1, dtype=float) ** -exponent
    if form == "shifted":
        base = power - power[-1]
        return floor + (kind.prize_pool - floor * m) * base / base.sum()
    if form == "floored":
        # max(floor, A r^-b): with the top k places above the floor,
        # A = (pool - floor (m - k)) / sum_{r<=k} r^-b; the largest k whose k-th place still clears the floor
        # is the consistent one.
        k = np.arange(1, m + 1)
        scale = (kind.prize_pool - floor * (m - k)) / np.cumsum(power)
        ok = np.nonzero(scale * power >= floor)[0]
        return np.maximum(floor, scale[ok.max() if len(ok) else 0] * power)
    raise ValueError(f"payout form {form!r} is not one of {FORMS}")


def payout_at(kind: ContestKind, places: Iterable[int], **kwargs) -> np.ndarray:
    """The payout of each 1-based place (0 for a place outside the paid places)."""
    table = payout_table(kind, **kwargs)
    idx = np.asarray(list(places), dtype=int)
    out = np.zeros(len(idx))
    ok = (idx >= 1) & (idx <= len(table))
    out[ok] = table[idx[ok] - 1]
    return out


# ---------------------------------------------------------------------------------------------------------
# Fitting
# ---------------------------------------------------------------------------------------------------------


def paid_entries(log: pd.DataFrame, formats: Iterable[str] = GPP_FORMATS) -> pd.DataFrame:
    """Sam's paid Classic entries of `formats` from the typed log (`history.typed`), with `multiple` (winnings
    over the fee) and `rel_place` (place over places paid). Freerolls and contests without a paid-places
    number are left out."""
    d = log[(log["game_type"] == "Classic") & (log["fee"] > 0) & log["format"].isin(list(formats))].copy()
    d = d[d["places_paid"].notna() & (d["places_paid"] > 0) & d["place"].notna()]
    d = d[d["place"] <= d["places_paid"]]
    d["multiple"] = d["winnings"] / d["fee"]
    d["rel_place"] = d["place"].astype(float) / d["places_paid"].astype(float)
    # The fold for "held out by week": season-week, or the contest's date for a slate the log gives no week
    # (the Sat-Mon and Thursday-Monday ones).
    by_date = d["season"].astype(str) + "-" + d["date"].dt.strftime("%Y-%m-%d")
    d["week_key"] = (d["season"].astype(str) + "-" + d["week"].astype(str)).where(d["week"].notna(), by_date)
    return d.reset_index(drop=True)


@dataclass(frozen=True)
class MinCash:
    """Min-cash multiples learned from paid entries: by series, then by fee, then by format."""

    by_series: dict[str, float] = field(default_factory=dict)
    by_fee: dict[float, float] = field(default_factory=dict)
    by_format: dict[str, float] = field(default_factory=dict)

    def lookup(self, series: str, fee: float, fmt: str) -> float:
        if series in self.by_series:
            return self.by_series[series]
        if round(float(fee), 2) in self.by_fee:
            return self.by_fee[round(float(fee), 2)]
        return self.by_format.get(fmt, DEFAULT_MIN_CASH)


def fit_min_cash(paid: pd.DataFrame) -> MinCash:
    """The min-cash multiple per series: the lowest winnings / fee among its entries in the bottom half of the
    paid places (`LOW_PAID_PLACES`). The fee and format fallbacks are medians of those series values."""
    low = paid[paid["rel_place"] > LOW_PAID_PLACES]
    if low.empty:
        return MinCash()
    series = low.groupby("series").agg(
        multiple=("multiple", "min"), fee=("fee", "first"), fmt=("format", "first")
    )
    return MinCash(
        by_series={str(k): float(v) for k, v in series["multiple"].items()},
        by_fee={round(float(k), 2): float(v) for k, v in series.groupby("fee")["multiple"].median().items()},
        by_format={str(k): float(v) for k, v in series.groupby("fmt")["multiple"].median().items()},
    )


def kind_of_row(rec, min_cash: MinCash | None = None) -> ContestKind:
    """The `ContestKind` of one typed-log row (`min_cash` looked up for a GPP)."""
    fmt = str(rec["format"])
    series = str(rec["series"])
    c = None
    if fmt in GPP_FORMATS and min_cash is not None:
        c = min_cash.lookup(series, float(rec["fee"]), fmt)
    return ContestKind(
        series=series,
        format=fmt,
        entries=int(rec["entries"]),
        places_paid=int(rec["places_paid"]),
        prize_pool=float(rec["prize_pool"]),
        fee=float(rec["fee"]),
        min_cash=c,
        max_entries=max_entries_of(series),
    )


def predict_winnings(
    paid: pd.DataFrame, min_cash: MinCash, *, exponent: float = PAYOUT_EXPONENT, form: str = "shifted"
) -> np.ndarray:
    """The curve's payout for each row's place, one table per contest."""
    out = np.zeros(len(paid))
    for _, idx in paid.groupby("contest_key").indices.items():
        rows = paid.iloc[idx]
        kind = kind_of_row(rows.iloc[0], min_cash)
        out[idx] = payout_at(kind, rows["place"].astype(int), exponent=exponent, form=form)
    return out


def _log_loss(paid: pd.DataFrame, min_cash: MinCash, exponent: float, form: str) -> float:
    pred = predict_winnings(paid, min_cash, exponent=exponent, form=form)
    return float(np.mean((np.log(np.maximum(pred, 1e-9)) - np.log(paid["winnings"].to_numpy(float))) ** 2))


def fit_exponent(paid: pd.DataFrame, min_cash: MinCash | None = None, *, form: str = "shifted") -> float:
    """The exponent `b` that minimises the mean squared log error of the predicted against the real winnings
    (a 60x first place and a 1.5x min cash weigh alike)."""
    if len(paid) < 3:
        raise ValueError("need at least three paid entries to fit a payout curve")
    mc = min_cash if min_cash is not None else fit_min_cash(paid)
    res = minimize_scalar(
        lambda b: _log_loss(paid, mc, b, form),
        bounds=EXPONENT_BOUNDS,
        method="bounded",
        options={"xatol": 1e-3},
    )
    return float(res.x)


# ---------------------------------------------------------------------------------------------------------
# Validation: held out by week
# ---------------------------------------------------------------------------------------------------------


def held_out_predictions(paid: pd.DataFrame, *, form: str = "shifted") -> pd.DataFrame:
    """Every paid entry's predicted winnings from a curve (exponent and min cash) fitted on the OTHER weeks:
    `paid` plus `predicted`, `exponent`."""
    out = paid.copy()
    out["predicted"] = np.nan
    out["exponent"] = np.nan
    for week, idx in paid.groupby("week_key").indices.items():
        train = paid[paid["week_key"] != week]
        mc = fit_min_cash(train)
        b = fit_exponent(train, mc, form=form)
        test = paid.iloc[idx]
        out.loc[out.index[idx], "predicted"] = predict_winnings(test, mc, exponent=b, form=form)
        out.loc[out.index[idx], "exponent"] = b
    return out


SUMMARY_COLUMNS = ["group", "entries", "actual", "predicted", "ratio", "mae", "median_abs_pct"]


def summarize(pred: pd.DataFrame, by: str | None = None) -> pd.DataFrame:
    """Totals and per-entry errors of `held_out_predictions`, overall or by a column."""
    groups = [("all", pred)] if by is None else list(pred.groupby(by))
    rows = []
    for name, g in groups:
        actual, predicted = g["winnings"].to_numpy(float), g["predicted"].to_numpy(float)
        rows.append(
            {
                "group": str(name),
                "entries": len(g),
                "actual": float(actual.sum()),
                "predicted": float(predicted.sum()),
                "ratio": float(predicted.sum() / actual.sum()) if actual.sum() else math.nan,
                "mae": float(np.abs(predicted - actual).mean()),
                "median_abs_pct": float(np.median(np.abs(predicted - actual) / actual)),
            }
        )
    return pd.DataFrame(rows, columns=SUMMARY_COLUMNS)


def flat_check(log: pd.DataFrame) -> pd.DataFrame:
    """The flat formats' paid entries with the flat table's payout beside the real winnings."""
    paid = paid_entries(log, FLAT_FORMATS)
    paid["predicted"] = paid["prize_pool"] / paid["places_paid"].astype(float)
    return paid


# ---------------------------------------------------------------------------------------------------------
# Sam's contest kinds
# ---------------------------------------------------------------------------------------------------------


def recent_kinds(
    log: pd.DataFrame, season: int, *, through_week: int | None = None, min_cash: MinCash | None = None
) -> list[ContestKind]:
    """One `ContestKind` per series Sam entered on the main slate this season (Classic, paid formats), from
    its most recent contest up to `through_week`: what a slate's contest sim runs against."""
    d = log[
        (log["season"] == season)
        & (log["game_type"] == "Classic")
        & (log["slate"] == "main")
        & (log["fee"] > 0)
        & log["format"].isin(list(FLAT_FORMATS | GPP_FORMATS))
        & log["places_paid"].notna()
    ]
    if through_week is not None:
        d = d[d["week"] <= through_week]
    if d.empty:
        return []
    mc = min_cash if min_cash is not None else fit_min_cash(paid_entries(log))
    latest = d.sort_values(["week", "date"]).groupby("series").tail(1)
    return [kind_of_row(rec, mc) for _, rec in latest.sort_values(["format", "fee", "series"]).iterrows()]
