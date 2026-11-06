"""The plays model's simulation (`dfs.plays`): one slate's draws, the lines each draw is judged by, and the
two per-player measures the cash and GPP calls are built on.

**The draws.** Every playing player with a projection (`sim_inputs.build_specs`: CalPts first) is drawn
together in one Gaussian-copula simulation (`contest_sim.draw_points`, the lineup simulator's correlations and
marginal tables), with the tails study's per-role stretch of the top applied (`TAIL_DELTA`: the roles whose
stretch cleared 2 SE held out, `dfs research refresh`'s `tails` study, fitted 2014-2022 and so free of any
2026 week). Each draw's **cash line** is the double-up field's mean plus the relative offset, and its **top-1%
line** the GPP field's mean plus its offset (`sim_inputs.resolve_lines`, the same lines the builder uses);
before ownership publishes both are the fixed fallback lines.

**Cash: priced P(cash) in near-optimal lineups** (`cash_scores`). The reference lineups are the best
`CASH_REF_LINEUPS` by simulated Cash % among the `CASH_SEARCH_K` best legal lineups by projection from a
cash-sized pool (`CASH_POOL_COUNTS` per position, by CalPts and by value). In each reference lineup, every
slot is refilled with each eligible player in turn and the lineup's Cash % is measured on the shared draws.
Cash % rewards a high floor (the line sits near the field's average), and it is **priced** with the
Lagrangian of the salary cap: `lam`, the Cash % one more $1K buys at the margin (the median slope of Cash %
on salary across the refills), so a player's value is `Cash % - lam x salary / 1000`, his best slot in each
lineup, averaged over the lineups. `cash_value` is in points of Cash %.

**GPP: the simulated top 1% against ownership** (`gpp_scores`). A GPP field is generated (`field_gen`, raked
to the GPP ownership; construction rates from a `stacks_retro.FieldModel` fitted on earlier weeks' real
fields only, `DEFAULT_RATES` when there is none), each draw's top 1% of it is taken, and a player's
`opt_pct` is his share of those lineups (nine players' worth, like ownership): `lift` is that share over his
share of the generated field (shrunk toward 1 by `LIFT_PRIOR` pseudo-hits), `opt_pct = own x lift` at his
shown ownership, `lev = opt_pct - own`. A quarterback's best stack is the teammates (and the opponent) most
often in his top-1% lineups.

Pure functions apart from the loaders the caller passes in; deterministic in the seed. No network, no sheet.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime

import numpy as np
import pandas as pd

from dfs import sim_inputs
from dfs.sim import contest_sim as csim
from dfs.sim import field_gen

# The tails study's per-role stretch of the upper quantiles (`dfs.tail_check.fit_adjustment`, group "role",
# fitted 2014-2022; `dfs research refresh` "tails"): the roles whose stretch clears 2 SE held out. All three
# are LIGHTER tops than the tables say (a negative delta): RB2 and WR4 reach their ceilings less often than
# modelled, and defenses beat their 90th percentile 7.75% of the time, not 10%.
TAIL_DELTA: dict[str, float] = {"RB2": -0.053, "WR4": -0.215, "DST": -0.001}
N_SIMS = 3000
SEED = 0
CASH_SEARCH_K = 300  # legal lineups by projection the reference set is chosen from
CASH_REF_LINEUPS = 30  # of those, the best this many by Cash % are the near-optimal cash lineups
CASH_POOL_COUNTS = {"QB": 6, "RB": 12, "WR": 16, "TE": 6, "DST": 6}  # by CalPts, and again by value
MIN_SLOPE_CANDIDATES = 5
FIELD_LINEUPS = 4000
TOP_FRACTION = 0.01
LIFT_PRIOR = 20.0  # pseudo top-1% hits at the average rate: a rarely drafted player's lift stays near 1
STACK_MATE_SHARE = 0.25  # a teammate in this share of the QB's top-1% lineups is part of his best stack
BRINGBACK_SHARE = 0.25
# The field's construction before any real field has been fitted (only a week with no earlier standings):
# most GPP lineups stack their QB, about half with a second receiver and a bring-back.
DEFAULT_RATES = field_gen.FieldRates(
    stack1=0.80,
    stack2=0.45,
    bringback_stacked=0.50,
    bringback_unstacked=0.30,
    unspent=(0.0, 0.0, 100.0, 200.0, 300.0, 400.0, 600.0, 900.0, 1300.0),
)
CASH_SLOTS = ("QB", "RB", "RB", "WR", "WR", "WR", "TE", "FLEX", "DST")
FLEX = ("RB", "WR", "TE")
NOT_PLAYING = frozenset({"OUT", "IR", "D"})


@dataclass
class SlateSim:
    """One slate's draws. `names` index the columns of `draws` (n_sims x players)."""

    names: tuple[str, ...]
    position: np.ndarray
    team: np.ndarray
    opp: np.ndarray
    salary: np.ndarray
    role: tuple[str, ...]
    draws: np.ndarray
    cash_line: np.ndarray  # one per draw
    top_line: np.ndarray
    mode: str  # "relative" (lines move with the simulated field) or "fallback" (fixed numbers)
    index: dict[str, int] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.index = {n: i for i, n in enumerate(self.names)}

    @property
    def n_sims(self) -> int:
        return int(self.draws.shape[0])


def playing(edge: pd.DataFrame) -> pd.DataFrame:
    """The rows that can play: not OUT, IR or Doubtful."""
    avail = edge["Avail"].fillna("").astype(str).str.upper() if "Avail" in edge.columns else ""
    return edge[~pd.Series(avail, index=edge.index).isin(NOT_PLAYING)].reset_index(drop=True)


def simulate_slate(
    edge: pd.DataFrame,
    typed_cash: sim_inputs.CashLine,
    *,
    n_sims: int = N_SIMS,
    seed: int = SEED,
    tails: Mapping[str, float] | None = None,
    lines_cfg=None,  # noqa: ANN001 - dfs.config.SimLinesConfig (default: the shipped `[sim.lines]`)
) -> SlateSim:
    """Draw the slate (`edge`: one week's rows; the projection is `CalPts`, else `ProjPts`)."""
    from dfs.config import SimConfig

    cfg = SimConfig()
    lines_cfg = lines_cfg if lines_cfg is not None else cfg.lines
    frame = playing(edge)
    specs = sim_inputs.build_specs(frame)
    names = tuple(sorted(specs))
    spec_list = [specs[n] for n in names]
    tails = TAIL_DELTA if tails is None else tails
    deltas = [float(tails.get(s.role, 0.0)) for s in spec_list]
    draws = csim.draw_points(spec_list, n_sims, seed=seed, deltas=deltas)
    lines = sim_inputs.resolve_lines(frame, specs, typed_cash, cfg.gpp_target, lines_cfg)
    index = {n: i for i, n in enumerate(names)}
    if lines.field is not None:
        f = lines.field
        cols = np.array([index[p.id] for p in f.players])
        cash = draws[:, cols].astype(float) @ f.cash_weights + f.cash_offset
        top = draws[:, cols].astype(float) @ f.top_weights + f.top_offset
        mode = "relative"
    else:
        cash = np.full(n_sims, float(lines.cash_line))
        top = np.full(n_sims, float(lines.gpp_target))
        mode = "fallback"
    return SlateSim(
        names=names,
        position=np.array([s.position for s in spec_list]),
        team=np.array([s.team for s in spec_list]),
        opp=np.array([s.opp for s in spec_list]),
        salary=np.array([float(s.salary or 0.0) for s in spec_list]),
        role=tuple(s.role for s in spec_list),
        draws=draws,
        cash_line=cash,
        top_line=top,
        mode=mode,
    )


# ---------------------------------------------------------------------------------------------------------
# Cash: priced P(cash) in the near-optimal cash lineups
# ---------------------------------------------------------------------------------------------------------


def cash_pool_names(edge: pd.DataFrame, counts: Mapping[str, int] = CASH_POOL_COUNTS) -> set[str]:
    """A cash-sized pool: per position the best `counts[pos]` by CalPts and the best `counts[pos]` by CalPts
    per $1K among the position's top half by CalPts."""
    frame = playing(edge).copy()
    frame["_cal"] = pd.to_numeric(frame.get("CalPts"), errors="coerce")
    frame["_sal"] = pd.to_numeric(frame.get("Salary"), errors="coerce")
    frame = frame[frame["_cal"].notna() & (frame["_cal"] > 0) & (frame["_sal"] > 0)]
    out: set[str] = set()
    for pos, n in counts.items():
        part = frame[frame["Position"] == pos].sort_values("_cal", ascending=False)
        out |= set(part["Name"].head(n).astype(str))
        half = part.head(max(n * 2, len(part) // 2))
        value = (half["_cal"] / (half["_sal"] / 1000)).sort_values(ascending=False)
        out |= set(half.loc[value.index[:n], "Name"].astype(str))
    return out


def reference_lineups(
    sim: SlateSim,
    edge: pd.DataFrame,
    *,
    cap: int,
    now: datetime,
    k: int = CASH_SEARCH_K,
    keep: int = CASH_REF_LINEUPS,
) -> tuple[np.ndarray, np.ndarray]:
    """`(lineups, p_cash)`: the near-optimal cash lineups as (n x 9) column indices into `sim` in the roster
    slot order of `CASH_SLOTS`, best Cash % first, and their Cash %."""
    from dfs import lineup_suggest as ls
    from dfs.late_swap_search import swap_pool

    names = cash_pool_names(edge) & set(sim.index)
    pool = swap_pool(edge, now=now, metric="CalPts", pool_names=names)
    found, _ = ls.cash_lineups(pool, cap=cap, k=k)
    rows = [
        [sim.index[p.name] for p in lineup] for lineup in found if all(p.name in sim.index for p in lineup)
    ]
    if not rows:
        return np.zeros((0, 9), dtype=int), np.zeros(0)
    idx = np.array(rows, dtype=int)
    totals = sim.draws[:, idx].sum(axis=2)
    p = (totals >= sim.cash_line[:, None]).mean(axis=0)
    order = np.argsort(-p, kind="stable")[:keep]
    return idx[order], p[order]


def _eligible(sim: SlateSim, slot: str, scored: np.ndarray) -> np.ndarray:
    pos = sim.position[scored]
    return scored[np.isin(pos, FLEX)] if slot == "FLEX" else scored[pos == slot]


@dataclass
class CashScores:
    frame: pd.DataFrame  # name, cash_value, cash_raw (Cash % at his best slot, unpriced), lineups
    lam: float  # Cash % per $1K at the margin (points of Cash %)
    ref_p_cash: float  # the reference lineups' average Cash %
    n_ref: int


def cash_scores(sim: SlateSim, lineups: np.ndarray, scored_names: list[str]) -> CashScores:
    """Each scored player's priced Cash % value across the reference `lineups` (module docstring)."""
    scored = np.array(sorted({sim.index[n] for n in scored_names if n in sim.index}), dtype=int)
    if len(lineups) == 0 or len(scored) == 0:
        empty = pd.DataFrame(columns=["name", "cash_value", "cash_raw", "lineups"])
        return CashScores(empty, 0.0, float("nan"), 0)
    line = sim.cash_line[:, None]
    refill: list[tuple[int, np.ndarray, np.ndarray]] = []  # (lineup, candidates, Cash %)
    slopes: list[float] = []
    for li, lineup in enumerate(lineups):
        total = sim.draws[:, lineup].sum(axis=1)
        for j, slot in enumerate(CASH_SLOTS):
            q = lineup[j]
            others = set(lineup.tolist()) - {q}
            cand = np.array([c for c in _eligible(sim, slot, scored) if c not in others] or [q], dtype=int)
            if q not in cand:
                cand = np.append(cand, q)
            rest = (total - sim.draws[:, q])[:, None]
            p = ((rest + sim.draws[:, cand]) >= line).mean(axis=0) * 100.0
            refill.append((li, cand, p))
            sal = sim.salary[cand] / 1000.0
            if len(cand) >= MIN_SLOPE_CANDIDATES and np.ptp(sal) > 0:
                slopes.append(float(np.polyfit(sal, p, 1)[0]))
    lam = max(float(np.median(slopes)), 0.0) if slopes else 0.0
    best_u = np.full((len(scored), len(lineups)), -np.inf)
    best_p = np.full((len(scored), len(lineups)), np.nan)
    where = {c: i for i, c in enumerate(scored)}
    for li, cand, p in refill:
        u = p - lam * sim.salary[cand] / 1000.0
        for c, uc, pc in zip(cand, u, p, strict=True):
            i = where.get(int(c))
            if i is not None and uc > best_u[i, li]:
                best_u[i, li], best_p[i, li] = uc, pc
    seen = np.isfinite(best_u)
    value = np.where(seen.any(axis=1), np.nanmean(np.where(seen, best_u, np.nan), axis=1), np.nan)
    raw = np.nanmean(best_p, axis=1)
    frame = pd.DataFrame(
        {
            "name": [sim.names[c] for c in scored],
            "cash_value": value,
            "cash_raw": raw,
            "lineups": [int(np.isin(lineups, c).any(axis=1).sum()) for c in scored],
        }
    )
    ref = float((sim.draws[:, lineups].sum(axis=2) >= line).mean() * 100.0)
    return CashScores(frame, lam, ref, len(lineups))


# ---------------------------------------------------------------------------------------------------------
# GPP: the simulated top 1% against ownership
# ---------------------------------------------------------------------------------------------------------


@dataclass
class GppScores:
    frame: pd.DataFrame  # name, field_own, own, lift, opt_pct, lev
    stacks: dict[str, str]  # QB name -> his best stack, in words
    n_lineups: int


def own_column(edge: pd.DataFrame) -> str | None:
    """The GPP ownership the field is raked to: `GppOwn`, else TFFB's `Own%`; None before either publishes."""
    for col in ("GppOwn", "Own%"):
        if col in edge.columns and (pd.to_numeric(edge[col], errors="coerce").fillna(0) > 0).any():
            return col
    return None


def gpp_scores(
    sim: SlateSim,
    edge: pd.DataFrame,
    *,
    field_model=None,  # noqa: ANN001 - sim.stacks_retro.FieldModel fitted on earlier weeks, or None
    n_lineups: int = FIELD_LINEUPS,
    seed: int = SEED,
) -> GppScores:
    """Each player's share of the simulated top 1% against his ownership (module docstring)."""
    from dfs import stacks_inputs

    frame = playing(edge)
    frame = frame[frame["Name"].astype(str).isin(sim.index)]
    col = own_column(frame)
    own = (
        pd.to_numeric(frame[col], errors="coerce").fillna(0.0)
        if col is not None
        else stacks_inputs.stand_in_ownership(frame)
    )
    slate = field_gen.Slate.from_frame(
        pd.DataFrame(
            {
                "name": frame["Name"].astype(str),
                "position": frame["Position"].astype(str),
                "team": frame["Team"].astype(str),
                "opp": frame["Opp"].astype(str),
                "salary": frame["Salary"],
                "own": own.to_numpy(),
            }
        )
    )
    rates = field_model.rates if field_model is not None else DEFAULT_RATES
    herd = field_gen.HerdModel(share=0.0)  # the chalk herd moves the lines, not who makes the top 1%
    built = csim.build_field(slate, rates, herd, fmt="gpp", size=n_lineups, seed=seed)
    lineups = built.sampler
    cols = np.array([sim.index[n] for n in slate.names], dtype=int)
    n_top = max(1, round(TOP_FRACTION * len(lineups)))
    hits = np.zeros(len(slate))
    mates: dict[int, np.ndarray] = {}
    qbs = np.flatnonzero(slate.position == "QB")
    for q in qbs:
        mates[int(q)] = np.zeros(len(slate))
    qb_hits = np.zeros(len(slate))
    chunk = 250
    for start in range(0, sim.n_sims, chunk):
        pts = sim.draws[start : start + chunk][:, cols].astype(float)
        scores = pts[:, lineups].sum(axis=2)  # draws x lineups
        top = np.argpartition(-scores, n_top - 1, axis=1)[:, :n_top]
        rows = lineups[top].reshape(-1, lineups.shape[1])  # (draws x n_top) x 9
        np.add.at(hits, rows.ravel(), 1.0)
        for q in qbs:
            with_q = rows[(rows == q).any(axis=1)]
            if len(with_q):
                qb_hits[q] += len(with_q)
                np.add.at(mates[int(q)], with_q.ravel(), 1.0)
    total_top = float(sim.n_sims * n_top)
    sampler_own = np.bincount(lineups.ravel(), minlength=len(slate)) / max(len(lineups), 1)
    expected = sampler_own * total_top
    lift = (hits + LIFT_PRIOR) / (expected + LIFT_PRIOR)
    shown = slate.own.astype(float)
    opt = shown * lift
    out = pd.DataFrame(
        {
            "name": list(slate.names),
            "field_own": sampler_own,
            "own": shown,
            "lift": lift,
            "opt_pct": opt,
            "lev": opt - shown,
        }
    )
    stacks = {}
    for q in qbs:
        if qb_hits[q] <= 0:
            continue
        share = mates[int(q)] / qb_hits[q]
        team, opp = slate.team[q], slate.opp[q]
        own_side = [
            i
            for i in np.argsort(-share)
            if i != q and slate.team[i] == team and slate.position[i] in FLEX and share[i] >= STACK_MATE_SHARE
        ][:2]
        back = [
            i
            for i in np.argsort(-share)
            if slate.team[i] == opp and slate.position[i] in FLEX and share[i] >= BRINGBACK_SHARE
        ][:1]
        if not own_side:
            continue
        text = " + ".join(f"{slate.names[i]} ({share[i]:.0%})" for i in own_side)
        if back:
            text += f"; bring-back {slate.names[back[0]]} ({share[back[0]]:.0%})"
        stacks[slate.names[q]] = text
    return GppScores(out, stacks, len(lineups))
