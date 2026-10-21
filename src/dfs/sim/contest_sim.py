"""The contest simulator: what a lineup is worth, in dollars, in a given contest.

For every simulated draw of the slate (the same correlated player outcomes the lineup simulator uses:
`simulate.py`'s Gaussian copula over the shipped correlations and distribution tables), the contest's field
(`field_gen`: the chalk herd plus the structured sampler, one field per format) and the candidate lineups are
scored, each candidate is placed among the field, and paid from the contest's payout table (`payouts`), with
its duplicates splitting the prize. Over the draws that gives, per candidate per contest:

- `e_dollars` (E[$]) and `roi` (E[$] / fee - 1); `e_top` is the part of E[$] paid by the top 0.1% of places,
  so `e_dollars - e_top` is what remains without the thin top of the payout curve; `e_dollars_herd_copies`
  is E[$] with only a herd lineup's copies splitting its prize (no dup-model discount for any other lineup);
- `p_cash` (finishing in the paid places), `p_top1` (in the top 1%);
- `e_copies`: the other entries expected to hold the identical lineup. A herd lineup's copies are the herd's
  own count in each draw; any other lineup's are the dup model's (`dups.DUP_MODEL_GPP` for a GPP,
  `DUP_MODEL_CASH` on the double-up shares for a cash contest, where a tie only matters at the cash line).

**Places.** A field is a capped sample (`SimField.sampler`, a few thousand lineups) standing in for the whole
contest: each sampler lineup carries `(1 - herd share) / n` of the field, each herd lineup its draw's share. A
candidate's place is 1 + (entries - 1) x the field's share that scored strictly more. With `c` copies it
occupies places `place .. place + c` and is paid their average (the cumulative table is interpolated, so a
fractional expected copy count is paid pro rata).

**Sim leverage.** Per draw, the field lineups (weighted) that make the GPP field's top 1% are counted per
player: `opt_pct` is his share of them (nine players' worth, like ownership), `sim_lev = opt_pct - own`.

**The lines.** Each draw's cash line (the score at the last paid place) and top-1% line are kept per contest,
so the 10-90% band of the lines can be checked against the real ones (`contest_retro`).

Pure functions; deterministic in the seed. No network, no sheet.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np
import pandas as pd
from scipy.special import ndtr

from dfs.sim import dups, field_gen, marginal, payouts
from dfs.sim.shape_table import default_lookup
from dfs.sim.simulate import PlayerSpec, _validate, correlation_matrix, repair_correlation  # noqa: PLC2701

TOP_FRACTION = 0.01
TOP_PRIZE_FRACTION = 0.001  # "the top 0.1% of prizes": places within the top 0.1% of the entries
DEFAULT_SIMS = 4000
CHUNK = 500  # draws scored at a time (memory: chunk x field lineups)
SAMPLER_SIZE = 5000


# ---------------------------------------------------------------------------------------------------------
# Player draws
# ---------------------------------------------------------------------------------------------------------


def draw_points(
    specs: Sequence[PlayerSpec | None], n_sims: int, *, seed: int = 0, correlations=None, tables=None
) -> np.ndarray:
    """(n_sims x len(specs)) DK points per player per draw: the lineup simulator's copula (same correlation
    table, PSD repair and principal-root draw). A `None` spec (a player with no projection) scores 0."""
    idx = [i for i, s in enumerate(specs) if s is not None]
    players = [_validate(specs[i]) for i in idx]
    out = np.zeros((n_sims, len(specs)), dtype=np.float32)
    if not players:
        return out
    raw, _ = correlation_matrix(players, correlations or default_lookup())
    r, _ = repair_correlation(raw)
    w, v = np.linalg.eigh(r)
    root = (v * np.sqrt(np.maximum(w, 0.0))) @ v.T
    rng = np.random.default_rng(seed)
    u = ndtr(rng.standard_normal((n_sims, len(players))) @ root)
    for position in ("QB", "RB", "WR", "TE", "DST"):
        cols = [k for k, p in enumerate(players) if p.position == position and p.projection > 0]
        if not cols:
            continue
        mg = marginal.marginals(position, [players[k].projection for k in cols], tables=tables)
        for j, k in enumerate(cols):
            out[:, idx[k]] = marginal.ppf(mg, j, u[:, k])
    return out


# ---------------------------------------------------------------------------------------------------------
# Fields
# ---------------------------------------------------------------------------------------------------------


@dataclass
class SimField:
    """One format's field for the sim: `sampler` (n x 9 slate indices, equal weights) and `chalk` (k x 9) with
    the herd model that spreads its share over them per draw. `own` is the target ownership it was raked to
    (what SimLev is measured against)."""

    fmt: str  # "cash" / "gpp"
    slate: field_gen.Slate
    sampler: np.ndarray
    chalk: np.ndarray
    herd: field_gen.HerdModel
    own: np.ndarray

    @property
    def herd_share(self) -> float:
        return self.herd.share if len(self.chalk) else 0.0


def build_field(
    slate: field_gen.Slate,
    rates: field_gen.FieldRates,
    herd: field_gen.HerdModel,
    *,
    fmt: str,
    chalk: np.ndarray | None = None,
    targets: np.ndarray | None = None,
    size: int = SAMPLER_SIZE,
    seed: int = 0,
) -> SimField:
    """The sim's field for one format: the chalk lineups, and `size` sampler lineups raked so that the herd's
    EXPECTED holdings (over its per-draw lead) plus the sampler's hold each player at `targets`."""
    rng = np.random.default_rng(seed)
    t = slate.targets() if targets is None else np.asarray(targets, dtype=float)
    if chalk is None:
        chalk = field_gen.chalk_lineups(slate, herd.k) if herd.share > 0 else np.zeros((0, 9), dtype=int)
    share = herd.share if len(chalk) else 0.0
    held = np.zeros(len(slate))
    if share > 0:
        ew = herd.expected_weights(len(chalk))
        np.add.at(held, chalk.ravel(), np.repeat(ew * share, chalk.shape[1]))
    need = np.maximum(t - held, 0.0) / max(1.0 - share, 1e-9)
    w = field_gen.rake_weights(slate, rates, need, rng, size=min(size, field_gen.RAKE_SIZE))
    sampler = field_gen.sample_lineups(slate, rates, size, w, rng)
    return SimField(fmt=fmt, slate=slate, sampler=sampler, chalk=chalk, herd=herd, own=t)


# ---------------------------------------------------------------------------------------------------------
# Payout arithmetic
# ---------------------------------------------------------------------------------------------------------


def _cumulative(table: np.ndarray) -> np.ndarray:
    return np.concatenate([[0.0], np.cumsum(table)])


def block_payout(cum: np.ndarray, place: np.ndarray, copies: np.ndarray) -> np.ndarray:
    """The average payout of places `place .. place + copies` (1-based, fractional allowed): the cumulative
    table interpolated at both ends, divided by `1 + copies`. Places past the table pay nothing."""
    m = len(cum) - 1
    xs = np.arange(m + 1, dtype=float)
    lo = np.interp(np.asarray(place, dtype=float) - 1.0, xs, cum)
    hi = np.interp(np.asarray(place, dtype=float) + np.asarray(copies, dtype=float), xs, cum)
    return (hi - lo) / (1.0 + np.asarray(copies, dtype=float))


# ---------------------------------------------------------------------------------------------------------
# The simulation
# ---------------------------------------------------------------------------------------------------------


@dataclass
class ContestSimResult:
    table: pd.DataFrame  # one row per candidate per contest
    opt: pd.DataFrame  # one row per slate player: name, own, opt_pct, sim_lev (GPP field)
    lines: dict[str, np.ndarray]  # contest label -> (draws x 2): each draw's cash line and top-1% line
    n_sims: int
    seconds: float = 0.0
    scores: np.ndarray | None = None  # draws x candidates: each candidate's points per draw


RESULT_COLUMNS = [
    "lineup",
    "contest",
    "format",
    "entries",
    "fee",
    "e_dollars",
    "roi",
    "e_top",
    "e_dollars_ex_top",
    "e_dollars_herd_copies",
    "p_cash",
    "p_top1",
    "e_copies",
]


def _lineup_shares(field: SimField, model_fmt: str) -> np.ndarray:
    shares = field.own * (9.0 / field.own.sum())
    if model_fmt == "cash":
        named = dups.double_up_shares(dict(enumerate(shares)))
        shares = np.array([named[i] for i in range(len(shares))])
    return shares


def simulate_contests(
    fields: dict[str, SimField],
    specs: Sequence[PlayerSpec | None],
    candidates: np.ndarray,
    kinds: Sequence[payouts.ContestKind],
    *,
    labels: Sequence[str] | None = None,
    n_sims: int = DEFAULT_SIMS,
    seed: int = 0,
    per_draw_herd: bool = True,
    points: np.ndarray | None = None,
) -> ContestSimResult:
    """Every candidate (row of slate indices) in every contest kind: see the module docstring. `fields` holds
    one `SimField` per format the kinds need ("cash" for Double Up / 50-50, "gpp" for the rest; a Multiplier
    is flat but plays a GPP-style field). `specs[i]` is slate player i's spec (None: scores 0).
    `per_draw_herd` False keeps the herd on its fixed weights (lead = the highest-product lineup) in every
    draw. `points` (draws x slate players) replaces the simulated draws (fixed outcomes, for tests)."""
    import time

    start = time.perf_counter()
    slate = next(iter(fields.values())).slate
    pts = draw_points(specs, n_sims, seed=seed) if points is None else np.asarray(points, dtype=np.float32)
    n_draws = pts.shape[0]
    candidates = np.asarray(candidates, dtype=int).reshape(-1, field_gen.LINEUP_SIZE)
    labels = list(labels) if labels is not None else [str(i + 1) for i in range(len(candidates))]
    cand_scores = pts[:, candidates].sum(axis=2)  # draws x candidates
    rng = np.random.default_rng(seed + 1)

    by_fmt: dict[str, dict] = {}
    for fmt, f in fields.items():
        herd_w = (
            f.herd.draw_weights(len(f.chalk), n_draws, rng)
            if per_draw_herd
            else np.broadcast_to(f.herd.weights(len(f.chalk)), (n_draws, len(f.chalk)))
        ) * f.herd_share
        n_s = len(f.sampler)
        w_s = (1.0 - f.herd_share) / max(n_s, 1)
        chalk_keys = {tuple(r): j for j, r in enumerate(f.chalk)}
        match = np.array([chalk_keys.get(tuple(np.sort(c)), -1) for c in candidates])
        above = np.empty((n_draws, len(candidates)), dtype=np.float32)
        herd_copy_share = np.zeros((n_draws, len(candidates)), dtype=np.float32)
        sorted_scores, sorted_cum = [], []
        opt = np.zeros(len(slate))
        all_lineups = np.concatenate([f.sampler, f.chalk])
        for lo in range(0, n_draws, CHUNK):
            hi = min(lo + CHUNK, n_draws)
            ss = pts[lo:hi][:, f.sampler].sum(axis=2)
            hs = pts[lo:hi][:, f.chalk].sum(axis=2) if len(f.chalk) else np.zeros((hi - lo, 0), np.float32)
            scores = np.concatenate([ss, hs], axis=1)
            weights = np.concatenate([np.full((hi - lo, n_s), w_s, np.float32), herd_w[lo:hi]], axis=1)
            order = np.argsort(-scores, axis=1, kind="stable")
            s_sorted = np.take_along_axis(scores, order, axis=1)
            cum = np.cumsum(np.take_along_axis(weights, order, axis=1), axis=1)
            for d in range(hi - lo):
                # the field's share that scored strictly more than each candidate
                k = np.searchsorted(-s_sorted[d], -cand_scores[lo + d], side="left")
                above[lo + d] = np.where(k > 0, cum[d, np.maximum(k - 1, 0)], 0.0)
                if fmt == "gpp":
                    # each lineup counts with the part of its weight inside the top 1% (a herd lineup that
                    # wins carries far more than 1% of the field: only its first 1% counts)
                    w_sorted = np.take_along_axis(weights[d : d + 1], order[d : d + 1], axis=1)[0]
                    inside = np.clip(TOP_FRACTION - (cum[d] - w_sorted), 0.0, w_sorted)
                    top = np.nonzero(inside > 0)[0]
                    lineups = all_lineups[order[d, top]]
                    np.add.at(opt, lineups.ravel(), np.repeat(inside[top] / TOP_FRACTION, lineups.shape[1]))
            sorted_scores.append(s_sorted)
            sorted_cum.append(cum)
            hit = match >= 0
            if hit.any():
                herd_copy_share[lo:hi][:, hit] = herd_w[lo:hi][:, match[hit]]
        by_fmt[fmt] = {
            "above": above,
            "herd_copy": herd_copy_share,
            "match": match,
            "sorted": sorted_scores,
            "cum": sorted_cum,
            "opt": opt / n_draws,
            "field": f,
        }

    rows, lines = [], {}
    for kind in kinds:
        fmt = "cash" if kind.cash else "gpp"
        if fmt not in by_fmt:
            fmt = next(iter(by_fmt))
        b = by_fmt[fmt]
        n = int(kind.entries)
        table = payouts.payout_table(kind)
        cum = _cumulative(table)
        top_places = max(1, math.ceil(TOP_PRIZE_FRACTION * n))
        top_table = table.copy()
        top_table[top_places:] = 0.0
        cum_top = _cumulative(top_table)
        place = 1.0 + b["above"].astype(float) * (n - 1)
        model = dups.DUP_MODEL_CASH if fmt == "cash" else dups.DUP_MODEL_GPP
        shares = _lineup_shares(b["field"], fmt)
        model_copies = np.array([model.expected(shares[c], n) for c in candidates])
        herd_copies = b["herd_copy"].astype(float) * (n - 1)
        copies = np.where(b["match"][None, :] >= 0, herd_copies, model_copies[None, :])
        pay = block_payout(cum, place, copies)
        pay_top = block_payout(cum_top, place, copies)
        # the variant with no dup-model discount: only a herd lineup's copies split its prize
        pay_herd = block_payout(cum, place, np.where(b["match"][None, :] >= 0, herd_copies, 0.0))
        m = int(kind.places_paid)
        label = kind.series
        for j, name in enumerate(labels):
            e = float(pay[:, j].mean())
            et = float(pay_top[:, j].mean())
            rows.append(
                {
                    "lineup": name,
                    "contest": label,
                    "format": kind.format,
                    "entries": n,
                    "fee": kind.fee,
                    "e_dollars": e,
                    "roi": e / kind.fee - 1.0 if kind.fee > 0 else math.nan,
                    "e_top": et,
                    "e_dollars_ex_top": e - et,
                    "e_dollars_herd_copies": float(pay_herd[:, j].mean()),
                    "p_cash": float((place[:, j] <= m).mean()),
                    "p_top1": float((place[:, j] <= max(1.0, TOP_FRACTION * n)).mean()),
                    "e_copies": float(copies[:, j].mean()),
                }
            )
        lines[label] = _draw_lines(b["sorted"], b["cum"], m / n if n else 0.0)
    gpp = by_fmt.get("gpp")
    opt_frame = pd.DataFrame()
    if gpp is not None:
        f = gpp["field"]
        own = f.own
        opt_frame = pd.DataFrame(
            {"name": list(slate.names), "own": own, "opt_pct": gpp["opt"], "sim_lev": gpp["opt"] - own}
        )
    return ContestSimResult(
        table=pd.DataFrame(rows, columns=RESULT_COLUMNS),
        opt=opt_frame,
        lines=lines,
        n_sims=n_draws,
        seconds=time.perf_counter() - start,
        scores=cand_scores,
    )


def _draw_lines(sorted_scores: list[np.ndarray], cums: list[np.ndarray], paid_share: float) -> np.ndarray:
    """(draws x 2): each draw's score at the field share `paid_share` (the cash line) and at the top 1%."""
    out = []
    for s, c in zip(sorted_scores, cums, strict=True):
        cols = []
        for share in (paid_share, TOP_FRACTION):
            k = np.minimum((c < share).sum(axis=1), c.shape[1] - 1)
            cols.append(s[np.arange(len(s)), k])
        out.append(np.column_stack(cols))
    return np.concatenate(out) if out else np.zeros((0, 2))
