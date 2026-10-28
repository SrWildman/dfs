"""The double-up cash line, modelled per draw from the herd (shadow; display only, default off).

Today's `Cash %` is P(lineup >= field mean + 0.6), the field mean from projected ownership. In Sam's
double-ups the line sits -4 to +8 points from the real field mean, and what decides the side is **the herd**:
one lineup holds 6-24% of every double-up (Week 7: 666 copies of his own cash lineup). The paid places are a
fixed share of the field (43.5%), so

- when the herd busts, it drops out of the paid places and the line is the rest of the field's
  `paid / (1 - s)` quantile (just above the herd: Week 4, herd 104.4, line 106.0);
- when it booms, it takes `s` of the paid places and the line is the rest's `(paid - s) / (1 - s)` quantile
  (well above the mean: Week 5, herd 159.0, line 153.1);
- in between it straddles the line, and the line IS the herd's score (Week 7: 114.06, his lineup tied and
  cashed). DraftKings pays every entry tied at the line.

**The model, per simulated draw** (`lines`): the rest of the field is normal around the mean that keeps the
whole field's mean at `field mean + shift` (the herd at its own drawn score `H`), with sd `cv` x that mean;
the line is the paid quantile of the mixture, so the three cases above fall out of one formula. `shift` is
the double-up field's average miss of the forecast field mean and `shift_sd` its spread week to week (drawn
once per draw): double-up fields are chalkier than the projected ownership, so the forecast misses by -14 to
+9 points.

The parameters are measured on real double-up standings (`fit`); the herd's score per draw is the pre-lock
top ownership-product lineup's (`field_gen.chalk_lineups`, k = 1; it shares 6-9 of the real herd's 9
players). `dfs.sim.du_line_retro` judges it held out by week. Nothing reads it unless `[sim.lines]
du_line_model` is on, and then only the SHOWN Cash %: builds keep ranking on today's number.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import asdict, dataclass

import numpy as np
from scipy.stats import norm

# DraftKings double-ups pay 43.5% of the field (every one Sam played but two: 45.1% and 50.1%).
PAID_SHARE = 0.435
# Draws of the week-level shift come from a fixed stream, so a display is the same on every run.
SHIFT_SEED = 7


@dataclass(frozen=True)
class DuLineModel:
    """`herd_share` (the most-copied lineup's share), `cv` (sd of the rest of the field over its mean),
    `shift` / `shift_sd` (points: real field mean minus the forecast, its mean and sd over weeks), `paid` (the
    share paid) and the weeks it was fitted on."""

    herd_share: float = 0.15
    cv: float = 0.15
    shift: float = 0.0
    shift_sd: float = 0.0
    paid: float = PAID_SHARE
    weeks: tuple[int, ...] = ()

    def to_dict(self) -> dict:
        return {**asdict(self), "weeks": list(self.weeks)}


def lines(
    field_mean: np.ndarray, herd: np.ndarray | None, model: DuLineModel, *, seed: int = SHIFT_SEED
) -> np.ndarray:
    """Each draw's cash line from the forecast field mean of that draw (`field_mean`), the herd lineup's
    score in it (`herd`, None: no herd) and `model`. Module docstring for the formula."""
    mu = np.asarray(field_mean, dtype=float)
    rng = np.random.default_rng(seed)
    total = mu + model.shift + (model.shift_sd * rng.standard_normal(len(mu)) if model.shift_sd > 0 else 0.0)
    q = model.paid
    s = model.herd_share if herd is not None else 0.0
    if s <= 0:
        sigma = model.cv * total
        return total + sigma * norm.ppf(1.0 - q)
    h = np.asarray(herd, dtype=float)
    rest_mean = (total - s * h) / (1.0 - s)
    sigma = model.cv * total
    # herd in the paid places: the rest supplies (q - s) of the field; herd out: the rest supplies all q
    above = (
        rest_mean + sigma * norm.ppf(1.0 - max(q - s, 1e-9) / (1.0 - s))
        if q > s
        else np.full_like(h, -np.inf)
    )
    below = rest_mean + sigma * norm.ppf(1.0 - q / (1.0 - s))
    return np.where(h >= above, above, np.where(h < below, below, h))


def cash_probability(scores: np.ndarray, line: np.ndarray) -> np.ndarray:
    """P(score >= line) per lineup: `scores` is draws x lineups, `line` one value per draw (ties cash)."""
    return (np.asarray(scores, dtype=float) >= np.asarray(line, dtype=float)[:, None]).mean(axis=0)


@dataclass(frozen=True)
class ContestStats:
    """What `fit` reads from one real double-up: its week, the share of the field on its most-copied
    lineup, the sd of the other entries' scores over the field's mean, the real field mean, the forecast field
    mean on the real points (projected ownership x real points), and the share paid."""

    week: int
    herd_share: float
    cv: float
    real_mean: float
    forecast_mean: float
    paid: float


def fit(stats: Sequence[ContestStats], *, exclude_week: int | None = None) -> DuLineModel:
    """The model from real double-ups, leaving out `exclude_week`. A week counts once (its contests share one
    slate, so their misses are one realisation): each parameter is the mean of the weekly means, and
    `shift_sd` is the sd of the weekly shifts (0 with fewer than two weeks)."""
    use = [s for s in stats if s.week != exclude_week]
    if not use:
        return DuLineModel()
    weeks = sorted({s.week for s in use})

    def weekly(attr) -> np.ndarray:
        return np.array([np.mean([attr(s) for s in use if s.week == w]) for w in weeks])

    shifts = weekly(lambda s: s.real_mean - s.forecast_mean)
    return DuLineModel(
        herd_share=float(weekly(lambda s: s.herd_share).mean()),
        cv=float(weekly(lambda s: s.cv).mean()),
        shift=float(shifts.mean()),
        shift_sd=float(shifts.std(ddof=1)) if len(shifts) > 1 else 0.0,
        paid=float(weekly(lambda s: s.paid).mean()),
        weeks=tuple(weeks),
    )
