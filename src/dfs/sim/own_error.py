"""Ownership is a forecast, not a fact: a model of how far a contest's real ownership lands from it.

Every sim has treated the projected ownership as known. It is not: TFFB's Own% misses by ~3.4 points a
player, and Sam's double-ups run far chalkier than projected and swing with one megadup lineup. This module
is the error model Haugh & Singal (Management Science 2021) put at the centre of their field model: the
contest's true ownership of each player is a RANDOM draw around the forecast, and a sim should score a
lineup against the fields that draw implies, not against the one the forecast names.

**The mean** is `dfs.field_own`'s sharpened TFFB (`exp(k log Own%)` within position, scaled to the
position's average total ownership; one `k` per format). Held out by week it beats raw TFFB and the
double-up curve in the likelihood below by a wide margin (`docs/SIM.md`, "Ownership uncertainty").

**The error** (`FormatError`, one per format) is a log-normal around the mean with a variance that falls
with the level, the shape the data showed (relative error 0.7 on a 1% player, 0.2 on a 40% one):

    log theta_i = log mu_i + sigma_i * e_i,        sigma_i^2 = ln(1 + c0 + c1 / mu_i)

`c0` is the relative variance every player carries; `c1 / mu_i` is the Poisson-like part that grows as a
player's share shrinks (a Dirichlet is the `c0 = 0` case). Shares are then scaled back to the position's
total, so a position's ownership still adds up. Errors are not independent: a QB's popularity pulls his
receivers, so the QB, WR and TE of one team share a factor (`team_corr`, the correlation of a QB's error
with a receiver's, the loading of each on the factor `sqrt(team_corr)`); RB and DST errors are independent.
A week can also tilt toward the chalk or away from it (`tilt_sd`, the sd of a week-wide slope on log mu).

**What is fitted on what.** `c0`, `c1` by maximum composite likelihood on the contests' real player counts:
a player held by `c` of `N` lineups has `c ~ Binomial(N, theta)` with `theta` drawn from the family, so the
finite size of the contest (which a generated field reproduces by itself) is NOT counted as ownership error.
`team_corr` and `tilt_sd` by moments on the same residuals. `fit_*` live in `own_error_retro` (they need the
real contests); this module is pure: the families' likelihoods, the draw, and the JSON a fitted model is
saved as.

Pure functions, deterministic in the rng. No network, no sheet."""

from __future__ import annotations

import json
import math
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path

import numpy as np
from scipy.special import betainc, gammaln, ndtr

from dfs.paths import DATA_DIR

FORMATS = ("gpp", "cash")
POSITIONS = ("QB", "RB", "WR", "TE", "DST")
TEAM_FACTOR_POSITIONS = ("QB", "WR", "TE")  # the pass game: one shared draw per team
MODEL_FILE = DATA_DIR / "model_cache" / "own_error.json"
FAMILIES = ("point", "dirichlet", "lognormal", "hybrid")

MIN_MU = (
    0.01  # a player is in the likelihood when his FORECAST is 1%+ (known before the lock, never the outcome)
)
MAX_OWN = 0.95  # no player is held by more than this share of lineups
MU_FLOOR = 1e-4  # a forecast below this counts as this (log of zero)

# theta bins for the marginal of a player's true share: geometric from 1e-5 to 1 (small shares need the
# resolution; the first bin is [0, 1e-5))
_EDGES = np.concatenate([[0.0], np.geomspace(1e-5, 1.0, 260)])
_MID = np.sqrt(np.maximum(_EDGES[:-1], 5e-7) * _EDGES[1:])
_MID[0] = 5e-6
_LOG_MID = np.log(_MID)
_LOG_1M_MID = np.log1p(-np.minimum(_MID, 1 - 1e-9))


@dataclass(frozen=True)
class FormatError:
    """The ownership error of one contest format (see the module docstring)."""

    c0: float  # relative variance floor
    c1: float  # Poisson-like term: added relative variance is c1 / mu
    team_corr: float = 0.0  # correlation of a QB's error with a teammate WR/TE's (0: independent)
    tilt_sd: float = 0.0  # sd of the week-wide tilt toward (+) or away from (-) the chalk, per log-share

    def sigma(self, mu: np.ndarray) -> np.ndarray:
        """Each player's log-sd at forecast `mu`."""
        mu = np.maximum(np.asarray(mu, dtype=float), MU_FLOOR)
        return np.sqrt(np.log1p(self.c0 + self.c1 / mu))


REFERENCE_FEE = 5.0  # the stake and field size the mean's exponent is quoted at
REFERENCE_ENTRIES = 2000
OWN_FLOOR = 0.002  # as field_own: a player with no (or a tiny) projected Own% counts as 0.2%
CURVE_CENTRE = math.log(0.05)  # the curvature term is centred on a 5% projection


@dataclass(frozen=True)
class MeanModel:
    """The forecast of one format's ownership from TFFB's Own%, per contest.

    Within each position a player's share is `exp(score) / sum(exp(score))` times the position's total, with
    `score = k_c * L + curve * (L - log 5%)^2`, `L = log Own%`, and the exponent moving with the contest's
    stake
    and size: `k_c = k + k_fee * log(fee / $5) + k_size * log(entries / 2,000)`. `k = 1` and nothing else is
    TFFB as is (scaled to the totals); `field_own`'s GppOwn / CashOwn is a `k` fitted by cross-entropy."""

    k: float
    totals: dict[str, float]
    curve: float = 0.0
    k_fee: float = 0.0
    k_size: float = 0.0

    def exponent(self, fee: float | None = None, entries: int | None = None) -> float:
        k = self.k
        if fee is not None and fee > 0:
            k += self.k_fee * math.log(fee / REFERENCE_FEE)
        if entries is not None and entries > 0:
            k += self.k_size * math.log(entries / REFERENCE_ENTRIES)
        return k

    def shares(
        self,
        own: np.ndarray,
        positions: Sequence[str],
        *,
        fee: float | None = None,
        entries: int | None = None,
    ) -> np.ndarray:
        """Each player's forecast share of lineups (0 for a position without a total)."""
        lo = np.log(np.maximum(np.nan_to_num(np.asarray(own, dtype=float), nan=0.0), OWN_FLOOR))
        score = self.exponent(fee, entries) * lo + self.curve * (lo - CURVE_CENTRE) ** 2
        pos = np.asarray(positions)
        out = np.zeros(len(lo))
        for position in POSITIONS:
            m = pos == position
            if m.any():
                e = np.exp(score[m] - score[m].max())
                out[m] = e / e.sum() * self.totals.get(position, 0.0)
        return np.minimum(out, MAX_OWN)


@dataclass(frozen=True)
class OwnershipErrorModel:
    """The fitted model: per format the error (`formats`) and the mean it is drawn around (`means`), and the
    weeks it was fitted on."""

    formats: dict[str, FormatError]
    weeks: tuple[int, ...] = ()
    means: dict[str, MeanModel] = field(default_factory=dict)

    def to_json(self) -> dict:
        return {
            "formats": {k: asdict(v) for k, v in self.formats.items()},
            "means": {k: asdict(v) for k, v in self.means.items()},
            "weeks": list(self.weeks),
        }

    @classmethod
    def from_json(cls, data: dict) -> OwnershipErrorModel:
        return cls(
            formats={
                k: FormatError(**{f: float(x) for f, x in v.items()}) for k, v in data["formats"].items()
            },
            weeks=tuple(int(w) for w in data.get("weeks", ())),
            means={
                k: MeanModel(
                    k=float(v["k"]),
                    totals={p: float(x) for p, x in v["totals"].items()},
                    curve=float(v.get("curve", 0.0)),
                    k_fee=float(v.get("k_fee", 0.0)),
                    k_size=float(v.get("k_size", 0.0)),
                )
                for k, v in data.get("means", {}).items()
            },
        )


def save(model: OwnershipErrorModel, path: Path = MODEL_FILE) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(model.to_json(), indent=2) + "\n")
    return path


def load(path: Path = MODEL_FILE) -> OwnershipErrorModel | None:
    """The saved model, or None when there is none yet (or it does not read)."""
    try:
        return OwnershipErrorModel.from_json(json.loads(path.read_text()))
    except (OSError, ValueError, KeyError, TypeError):
        return None


# ---------------------------------------------------------------------------------------------------------
# Likelihood: the marginal of one player's count
# ---------------------------------------------------------------------------------------------------------


def theta_mass(
    family: str,
    mu: np.ndarray,
    positions: Sequence[str],
    totals: dict[str, float],
    params: Sequence[float],
) -> np.ndarray:
    """(players x bins): the probability that each player's true lineup share falls in each theta bin.

    `family` (`FAMILIES`): "point" (the forecast is exact), "dirichlet" (`params = (alpha,)`: the position's
    pick shares are Dirichlet(alpha * mu / total)), "lognormal" (`params = (sigma,)`: log theta ~ N(log mu -
    sigma^2/2, sigma^2)) or "hybrid" (`params = (c0, c1)`: the lognormal with `sigma^2 = ln(1 + c0 + c1/mu)`).
    The marginals ignore the within-position renormalisation (it is a small correction)."""
    mu = np.maximum(np.asarray(mu, dtype=float), 1e-9)
    if family == "point":
        out = np.zeros((len(mu), len(_MID)))
        idx = np.clip(np.searchsorted(_EDGES, mu, side="right") - 1, 0, len(_MID) - 1)
        out[np.arange(len(mu)), idx] = 1.0
        return out
    if family == "dirichlet":
        (alpha,) = params
        total = np.array([totals[p] for p in positions])
        share = np.clip(mu / total, 1e-9, 1 - 1e-9)
        edges = np.minimum(_EDGES[None, :] / total[:, None], 1.0)
        return np.diff(betainc(alpha * share[:, None], alpha * (1 - share)[:, None], edges), axis=1)
    if family == "lognormal":
        sigma = np.full(len(mu), float(params[0]))
    elif family == "hybrid":
        c0, c1 = params
        sigma = np.sqrt(np.log1p(c0 + c1 / np.maximum(mu, MU_FLOOR)))
    else:
        raise ValueError(f"unknown family {family!r}; one of {FAMILIES}")
    sigma = np.maximum(sigma, 1e-6)
    z = (np.log(np.maximum(_EDGES[None, :], 1e-12)) - np.log(mu)[:, None] + (sigma**2 / 2)[:, None]) / sigma[
        :, None
    ]
    z[:, 0], z[:, -1] = -np.inf, np.inf
    return np.diff(ndtr(z), axis=1)


def count_loglik(counts: np.ndarray, entries: int, mass: np.ndarray) -> np.ndarray:
    """Each player's log-likelihood of `counts` lineups (out of `entries`) holding him, given `mass` (his true
    share's distribution over the theta bins): `log sum_k mass_k Binomial(count | entries, theta_k)`."""
    c = np.asarray(counts, dtype=float)[:, None]
    log_pmf = (
        gammaln(entries + 1)
        - gammaln(c + 1)
        - gammaln(entries - c + 1)
        + c * _LOG_MID[None, :]
        + (entries - c) * _LOG_1M_MID[None, :]
    )
    top = log_pmf.max(axis=1, keepdims=True)
    return np.log((mass * np.exp(log_pmf - top)).sum(axis=1) + 1e-300) + top[:, 0]


# ---------------------------------------------------------------------------------------------------------
# Draw
# ---------------------------------------------------------------------------------------------------------


def draw_shares(
    mu: np.ndarray,
    positions: Sequence[str],
    teams: Sequence[str],
    totals: dict[str, float],
    error: FormatError,
    n: int,
    rng: np.random.Generator,
) -> np.ndarray:
    """(n x players): `n` draws of every player's true share of lineups around the forecast `mu`.

    A player's log-share gets his own `sigma_i` of noise (centred so its mean is the forecast), the team
    factor for a QB / WR / TE (the QB and his receivers move together) and the week's tilt toward the chalk
    (`error.tilt_sd`); each position is then scaled back to its total, and no share goes above `MAX_OWN`.
    Players with `mu <= 0` stay at 0."""
    mu = np.asarray(mu, dtype=float)
    pos = np.asarray(positions)
    team = np.asarray(teams)
    p = len(mu)
    live = mu > 0
    logmu = np.log(np.maximum(mu, MU_FLOOR))
    sigma = error.sigma(mu)
    noise = rng.standard_normal((n, p))
    rho = float(np.clip(error.team_corr, 0.0, 0.99))
    if rho > 0:
        in_factor = np.isin(pos, TEAM_FACTOR_POSITIONS)
        names = sorted(set(team[in_factor]))
        index = {t: i for i, t in enumerate(names)}
        col = np.array([index.get(t, -1) if f else -1 for t, f in zip(team, in_factor, strict=True)])
        shared = rng.standard_normal((n, max(len(names), 1)))
        member = col >= 0
        noise[:, member] = math.sqrt(rho) * shared[:, col[member]] + math.sqrt(1.0 - rho) * noise[:, member]
    eta = logmu[None, :] - sigma[None, :] ** 2 / 2 + sigma[None, :] * noise  # E[exp(eta)] = mu
    if error.tilt_sd > 0:
        tilt = rng.normal(0.0, error.tilt_sd, size=(n, 1))
        centred = np.zeros(p)
        for position in POSITIONS:
            m = (pos == position) & live
            if m.any():
                centred[m] = logmu[m] - logmu[m].mean()
        eta = eta + tilt * centred[None, :]
    out = np.zeros((n, p))
    for position in POSITIONS:
        m = (pos == position) & live
        if not m.any():
            continue
        e = np.exp(eta[:, m] - eta[:, m].max(axis=1, keepdims=True))
        out[:, m] = e / e.sum(axis=1, keepdims=True) * totals[position]
    return np.minimum(out, MAX_OWN)
