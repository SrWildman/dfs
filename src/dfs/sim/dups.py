"""Duplicates: how many copies of a lineup the field probably holds, and what a top-1% finish is worth when
it is shared.

A tournament pays the top 1% by place, and ties (identical lineups score identically) split the prize. A
lineup that reaches the top-1% line but is one of 50 identical entries keeps a fraction of the payout, so the
objective for a GPP build is not P(top 1%) but P(top 1%) discounted by the copies expected:

    objective = P(score >= top-1% line) / (1 + E[dups])        (`top1_share`)

**Expected duplicates.** `ownership` is the share of the field's lineups that hold each player (the field
weights of `dfs.sim.field`: nine players' worth, summing to 9 over the slate). If a field lineup chose its
nine players independently, the chance it equals ours is the PRODUCT of our nine players' shares, and the
expected number of identical entries in a field of N is `N * product`. That independence is the **known
error**: field lineups are not independent draws.

- Chalk is correlated. The field herds into the same stacks, the same cheap value plays and the same few
  salary-feasible builds, so a lineup of popular players is duplicated far more often than the product says.
  Sam's Week 4 double-up lineup was one of about 500 identical entries out of 4,597 (11% of the field); the
  independence product would need to be 0.109, which no nine-player lineup of real ownerships comes close to
  (nine players at 50% average give 0.002, an estimate of 9 copies: 50 times too few).
- The salary cap removes most of the lineups an independent draw would produce, concentrating the field on
  the rest.
- A fixed number of slots makes inclusion slightly NEGATIVELY dependent (two RB slots: the chance of holding
  both backs is about half the product), which pulls the other way, by a factor of about 5-10 over a whole
  lineup. The two herding effects are far larger.

**The stated correction** is a power on the product: `E[dups] = N * product ** exponent` with
`DUP_EXPONENT = 0.5`, "about half as many independent choices as players" (a QB, his stack and the RB/DST
pairing move together). Exponent 1 is the plain independence estimate. 0.5 reproduces the order of
magnitude of the Week 4 lineup (a lineup of 55-60% average ownership in a 4,597-entry field comes to
310-460 copies where independence gives 20-50), and it makes a typical GPP build (summed ownership about
120%, so about 13% per player) come out at about a tenth of a copy in 1,000 entries and a chalky one (30% a
player) at several. It was set from that one anchor and the structure, NOT fitted: `fit_dup_model` fits
`(scale, exponent)` to any contest standings (copies of each lineup against its ownership product), and the
first thing to do with Sam's 13 GPP and 7 double-up standings files is run it and replace the default.

Pure functions, no network, no sheet. Players with no ownership number count at `OWN_FLOOR`.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

import numpy as np
from scipy.optimize import minimize

DEFAULT_GPP_ENTRIES = 1000  # Sam's typical single-entry GPP: about 800-1,500 entries
DEFAULT_DOUBLE_UP_ENTRIES = 3000  # his double-ups ran 2,758-4,597 entries (Weeks 4 and 5)
OWN_FLOOR = 0.005  # a player with no (or a tiny) ownership number counts as owned by 0.5% of the field
DUP_EXPONENT = 0.5  # the stated chalk correction (module docstring); 1.0 is the plain independence estimate
DUP_SCALE = 1.0  # the multiplier on `entries * product ** exponent`; 1.0 for the stated placeholder
DUP_WARN_COPIES = 25.0  # a double-up build expected to be held by this many entries or more gets a warning


def _shares(own: Iterable[float], floor: float) -> list[float]:
    return [min(1.0, max(floor, float(x))) if x == x else floor for x in own]


def ownership_sum(own: Iterable[float]) -> float:
    """The summed ownership of a lineup's players (1.16 = 116%): the usual chalk gauge."""
    return float(sum(x for x in own if x == x))


def ownership_product(own: Iterable[float], *, floor: float = OWN_FLOOR) -> float:
    """The product of a lineup's players' ownership shares (each at least `floor`): the independence
    probability that one random field lineup is this one, and the strongest single predictor of duplicates."""
    return float(math.prod(_shares(own, floor)))


def expected_dups(
    own: Iterable[float],
    entries: int,
    *,
    exponent: float = DUP_EXPONENT,
    scale: float = DUP_SCALE,
    floor: float = OWN_FLOOR,
) -> float:
    """Expected number of OTHER entries identical to a lineup with these players' shares, in a field of
    `entries`: `scale * entries * product ** exponent`. Exponent 1.0 and scale 1.0 is the independence
    estimate (see the module docstring for what it gets wrong and the stated correction)."""
    if entries <= 0:
        return 0.0
    return float(scale * entries * ownership_product(own, floor=floor) ** exponent)


@dataclass(frozen=True)
class DupModel:
    """`E[dups] = scale * entries * product ** exponent` for one kind of contest (see `fit_dup_model`)."""

    scale: float = DUP_SCALE
    exponent: float = DUP_EXPONENT

    def expected(self, own: Iterable[float], entries: int) -> float:
        return expected_dups(own, entries, exponent=self.exponent, scale=self.scale)


# Fitted on Sam's 2026 standings (Weeks 2, 4 and 5: 33,576 lineups in 13 GPPs), with the ownership the tool
# itself applies it to (the field model's `top` shares, from the pre-lock EdgeRaw snapshot).
# The stated placeholder (scale 1, exponent 0.5) overstated GPP copies 2.4x in total and 20x for the
# least-owned lineups; this tracks the top decile of products (observed 2.65 copies, fitted 2.66).
# docs/SIM.md has the table.
DUP_MODEL_GPP = DupModel(scale=10.02, exponent=0.712)
# The double-up model is still the placeholder: the same fit on the projected shares is poor (a few heavily
# duplicated lineups carry most copies, and double-ups are chalkier than projected). It waits for the
# per-player double-up ownership model (Contests Phase 2). Cash builds only WARN on the number.
DUP_MODEL_CASH = DupModel()


def top1_share(p_top1: float, e_dups: float) -> float:
    """The GPP objective: the chance of reaching the top-1% line divided by `1 + E[dups]`, an approximation of
    how much of a top-1% payout the lineup keeps once ties split it."""
    return float(p_top1 / (1.0 + max(0.0, e_dups)))


def dups_label(e_dups: float) -> str:
    """ "~0.3 dupes" / "~12 dupes" for a build title."""
    if e_dups < 0.05:
        return "~0 dupes"
    return f"~{e_dups:.1f} dupes" if e_dups < 10 else f"~{e_dups:.0f} dupes"


def shares_for(players: Sequence[str], weights: Mapping[str, float]) -> list[float]:
    """Each named player's field share (`nan` for a player the table does not have)."""
    return [float(weights.get(name, float("nan"))) for name in players]


def fit_dup_model(
    products: Sequence[float], entries: Sequence[int] | int, copies: Sequence[float]
) -> tuple[float, float]:
    """Fit `E[copies] = scale * entries * product ** exponent` by Poisson maximum likelihood to observed
    standings: one row per lineup, `products` its ownership product, `copies` how many OTHER entries in its
    contest hold the identical lineup (`entries` is that contest's size, one number or one per row). Returns
    `(scale, exponent)`; `exponent` near 1 would mean the independence estimate was right up to a constant.

    Run it on the real standings to replace `DUP_EXPONENT`; it needs a few hundred lineups with a spread of
    products to pin the exponent down."""
    x = np.log(np.maximum(np.asarray(products, dtype=float), 1e-300))
    n = np.broadcast_to(np.asarray(entries, dtype=float), x.shape)
    y = np.asarray(copies, dtype=float)
    if len(x) < 3 or float(np.ptp(x)) == 0.0:
        raise ValueError("need at least three lineups with different ownership products")

    def nll(theta: np.ndarray) -> float:
        log_mu = theta[0] + theta[1] * x + np.log(n)
        return float(np.sum(np.exp(log_mu) - y * log_mu))

    start = np.array([0.0, 0.7])
    res = minimize(nll, start, method="Nelder-Mead", options={"xatol": 1e-6, "fatol": 1e-9, "maxiter": 4000})
    return float(math.exp(res.x[0])), float(res.x[1])
