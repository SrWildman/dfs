"""Stack ranker: for every QB on the slate, his stacks of one and two teammates, what each is worth in a GPP
and how much of the field plays it.

**A stack** is a QB plus EXACTLY the teammates named (no other RB / WR / TE of his team), with a bring-back
(at least one RB / WR / TE of the QB's opponent) or without one (none). Exact, so two stacks never overlap and
their rates add up. The mates are WR / TE (a QB's `CATCHERS_PER_TEAM` best by projection) and, for a RB, only
one who catches: his `Tgt%` in the top third of the slate's RB1 / RB2 (the third the simulator's team-shape
research found moves with his QB, `docs/SIM.md` "Team shape"). Two RBs are never a stack.

**Top 1% rate** (`top1`). Every draw of the slate (the contest simulator's correlated player outcomes) puts
the generator's GPP field in order (`field_gen`: the chalk herd plus the structured sampler); the *top-1%
line* of a draw is the score where 1% of the field's weight is above it, the herd's lineups counted at their
per-draw shares. `top1` is the share of (draw, lineup) pairs in which a lineup built on the stack lands above
that line, the rest of the lineup filled the way the field fills it (the same raked weights, the same
salary-left draw, the stack's salary taken out first). 1% is an average field lineup; `lift` = `top1` / 1%.

**Field rate** (`field`). The share of the generated field that plays exactly the stack, bring-back or not
(counted on a large sample of the generator's lineups plus the herd's expected holdings). A stack the
generator has `MIN_FIELD_LINEUPS` or fewer lineups for is too rare to count: its rate is the product of its
players' projected ownership instead (`field_src` "own").

**Boom share and leverage.** `boom_share` is the share of the field's top-1% lineups that play the stack:
`top1` x `field` / 1%. `lev` = `boom_share` - `field`, in points of the field: the stack's slice of the top
1% against its slice of the field. The same arithmetic as `SimLev` for a player (`contest_sim`).

**With and without a bring-back.** `top1_bb` / `top1_naked` are the two variants; `top1` mixes them at the
rate the real fields' stacked lineups carry a bring-back (`FieldRates.bringback_stacked`), the field's own
mix. `bb_edge` = `top1_bb` - `top1_naked`.

An information view, not advice: it ranks stacks by what the simulator thinks of them and says how it was
measured; the tab shows the retro's track record (`sim.stacks_retro`) beside it.

Pure functions, deterministic in the seed. No network, no sheet.
"""

from __future__ import annotations

import itertools
import time
from collections.abc import Sequence
from dataclasses import dataclass, field

import numpy as np
import pandas as pd
from scipy import sparse

from dfs.sim import contest_sim as csim
from dfs.sim import field_gen
from dfs.sim.field_gen import (  # noqa: PLC2701 - the stack sampler fills the rest of a lineup as the field's does
    FLEX_POSITIONS,
    LINEUP_SIZE,
    MAX_REDRAWS,
    UNSPENT_SLACK,
    _Batch,
    _pick,
)

TOP_FRACTION = 0.01
DEFAULT_SIMS = 4000
CATCHERS_PER_TEAM = 5  # a QB's WR / TE considered as stack mates: the best this many by projection
RBS_PER_TEAM = 2  # pass-catching backs considered per team
CATCHING_RB_SHARE = 1 / 3  # a RB catches when his Tgt% is in the top third of the slate's RB1 / RB2
LINEUPS_PER_VARIANT = 60  # lineups sampled per (stack, bring-back) variant
MIN_VARIANT_LINEUPS = 20  # a variant the sampler cannot fill this often is not a buildable stack
RATE_SAMPLE = 12_000  # generated lineups the field rate is counted on
MIN_FIELD_LINEUPS = 6  # fewer generated lineups than this: the rate is the ownership product instead
SCORE_CHUNK = 6_000  # lineups scored against all draws at a time
BATCH_ROWS = 20_000  # lineups built at a time (memory: rows x slate players)
RAKE_SIZE = 3_500  # lineups per raking round of the stack field (the shared default is 5,000)
CHALK_K = 10  # herd lineups used: the 10th holds well under 1% of the herd's weight at the fitted decay
MAX_QBS = 24  # the best QB of each team, at most this many by projection
GAME_ROWS = 8
STACK_ROWS = 20

OWN_SRC, GEN_SRC = "own", "gen"


@dataclass(frozen=True)
class Stack:
    qb: int
    mates: tuple[int, ...]  # slate indices, ascending


@dataclass
class StackResult:
    table: pd.DataFrame  # one row per stack, best leverage first
    games: pd.DataFrame  # one row per game
    meta: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------------------------------------
# Which stacks
# ---------------------------------------------------------------------------------------------------------


def catching_backs(slate: field_gen.Slate, specs: Sequence, share: float = CATCHING_RB_SHARE) -> set[int]:
    """Slate indices of the RBs that count as pass catchers: RB1 / RB2 of their team whose `Tgt%` is in the
    top `share` of the slate's RB1 / RB2 that have one."""
    rows = [
        (i, s.tshare)
        for i, s in enumerate(specs)
        if s is not None and s.role in ("RB1", "RB2") and s.tshare is not None and s.tshare == s.tshare
    ]
    if not rows:
        return set()
    cut = float(np.quantile([t for _, t in rows], 1.0 - share))
    return {i for i, t in rows if t >= cut and t > 0}


def candidate_stacks(
    slate: field_gen.Slate,
    specs: Sequence,
    *,
    catchers: int = CATCHERS_PER_TEAM,
    backs: int = RBS_PER_TEAM,
    max_qbs: int = MAX_QBS,
) -> list[Stack]:
    """Every stack worth ranking: each team's best QB (by projection; the QBs with the `max_qbs` best), and
    every one- and two-teammate set from his team's best `catchers` WR / TE and its pass-catching RBs (at most
    one RB per stack)."""
    proj = np.array([s.projection if s is not None else 0.0 for s in specs])
    rb_ok = catching_backs(slate, specs)
    qbs: dict[str, int] = {}
    for i in np.nonzero((slate.position == "QB") & (proj > 0))[0]:
        t = str(slate.team[i])
        if t not in qbs or proj[i] > proj[qbs[t]]:
            qbs[t] = int(i)
    chosen = sorted(qbs.values(), key=lambda i: -proj[i])[:max_qbs]
    out: list[Stack] = []
    for q in chosen:
        mine = np.nonzero((slate.team == slate.team[q]) & (proj > 0))[0]
        wr_te = [int(i) for i in mine if slate.position[i] in field_gen.PASS_CATCHERS]
        wr_te = sorted(wr_te, key=lambda i: -proj[i])[:catchers]
        rbs = sorted((int(i) for i in mine if i in rb_ok), key=lambda i: -proj[i])[:backs]
        pool = wr_te + rbs
        for k in (1, 2):
            for combo in itertools.combinations(pool, k):
                if sum(1 for i in combo if slate.position[i] == "RB") > 1:
                    continue
                out.append(Stack(q, tuple(sorted(combo))))
    return out


# ---------------------------------------------------------------------------------------------------------
# Lineups built around a stack
# ---------------------------------------------------------------------------------------------------------


def _fill(
    slate: field_gen.Slate,
    rates: field_gen.FieldRates,
    qb: np.ndarray,
    mates: np.ndarray,
    bring: np.ndarray,
    weights: np.ndarray,
    rng: np.random.Generator,
) -> tuple[np.ndarray, np.ndarray]:
    """One lineup per row: the row's QB and mates (`mates` is n x 2, -1 for none) forced, the QB's other
    RB / WR / TE and (without `bring`) all of his opponent's offence barred, one opposing RB / WR / TE forced
    with `bring`, and the other slots filled as the field fills them. Returns (lineups n x 9 sorted, valid n):
    a row that could not be completed under the cap, or leaves too much salary, is not valid."""
    n = len(qb)
    s = slate
    b = _Batch(s, n)
    pos, team, opp = s.position, s.team, s.opp
    offense = np.isin(pos, FLEX_POSITIONS)
    target_left = rng.choice(np.asarray(rates.unspent, dtype=float), size=n)
    b.take(b.rows, qb)
    for k in range(mates.shape[1]):
        b.take(b.rows, mates[:, k])
    same = (team[None, :] == team[qb][:, None]) & offense[None, :]
    facing = (team[None, :] == opp[qb][:, None]) & offense[None, :]
    b.banned |= same & ~b.chosen
    got = np.ones(n, dtype=bool)
    if bring.any():
        pick = _pick(weights, b.fits(target_left) & facing & bring[:, None], rng)
        got = ~bring | (pick >= 0)
        b.take(b.rows, np.where(bring, pick, -1))
    b.banned |= facing & ~b.chosen
    for _ in range(LINEUP_SIZE):
        todo = b.slots_left() > 0
        if not todo.any():
            break
        steered = b.fits(target_left) & todo[:, None]
        loose = b.fits() & todo[:, None]
        has = steered.any(axis=1)
        pick = _pick(weights, np.where(has[:, None], steered, loose), rng)
        b.take(b.rows, np.where(todo, pick, -1))
    valid = (b.slots_left() == 0) & (b.budget >= 0) & (b.budget <= target_left + UNSPENT_SLACK) & got
    lineups = np.zeros((n, LINEUP_SIZE), dtype=int)
    lineups[valid] = np.sort(np.nonzero(b.chosen[valid])[1].reshape(-1, LINEUP_SIZE), axis=1)
    return lineups, valid


def stack_lineups(
    slate: field_gen.Slate,
    rates: field_gen.FieldRates,
    variants: Sequence[tuple[Stack, bool]],
    weights: np.ndarray,
    rng: np.random.Generator,
    *,
    per_variant: int = LINEUPS_PER_VARIANT,
) -> tuple[np.ndarray, np.ndarray]:
    """`per_variant` lineups for each (stack, with-bring-back) variant: (lineups m x 9, variant index m). A
    variant the sampler cannot fill keeps what it got (possibly none)."""
    qb_of = np.array([v[0].qb for v in variants])
    mates_of = np.full((len(variants), max([2, *(len(v[0].mates) for v in variants)])), -1)
    for j, (st, _) in enumerate(variants):
        mates_of[j, : len(st.mates)] = st.mates
    bring_of = np.array([v[1] for v in variants])
    nv = len(variants)
    need = np.full(nv, per_variant)
    accept = np.full(nv, 0.6)
    kept: list[np.ndarray] = []
    owner: list[np.ndarray] = []
    for _ in range(MAX_REDRAWS):
        todo = np.nonzero(need > 0)[0]
        if not len(todo):
            break
        reps = np.ceil(need[todo] / np.maximum(accept[todo], 0.05) * 1.1).astype(int) + 4
        vid = np.repeat(todo, reps)
        for lo in range(0, len(vid), BATCH_ROWS):
            ids = vid[lo : lo + BATCH_ROWS]
            rows, valid = _fill(slate, rates, qb_of[ids], mates_of[ids], bring_of[ids], weights, rng)
            tried, ok = np.bincount(ids, minlength=nv), np.bincount(ids[valid], minlength=nv)
            accept = np.where(tried > 0, np.maximum(ok / np.maximum(tried, 1), 0.05), accept)
            good = np.nonzero(valid)[0]
            order = np.argsort(ids[good], kind="stable")
            good, sorted_ids = good[order], ids[good][order]
            rank = np.arange(len(good)) - np.searchsorted(sorted_ids, sorted_ids, side="left")
            take = good[rank < need[sorted_ids]]
            if len(take):
                kept.append(rows[take])
                owner.append(ids[take])
                need = need - np.bincount(ids[take], minlength=nv)
    if not kept:
        return np.zeros((0, LINEUP_SIZE), dtype=int), np.zeros(0, dtype=int)
    return np.concatenate(kept), np.concatenate(owner)


# ---------------------------------------------------------------------------------------------------------
# The field and its top-1% line
# ---------------------------------------------------------------------------------------------------------


@dataclass
class GppField:
    """The generated GPP field: `sim` (the sampler and the chalk herd, as the contest simulator uses them) and
    the sampler's raked `weights`."""

    sim: csim.SimField
    weights: np.ndarray
    rates: field_gen.FieldRates


def build_gpp_field(
    slate: field_gen.Slate,
    rates: field_gen.FieldRates,
    herd: field_gen.HerdModel,
    *,
    chalk: np.ndarray | None = None,
    size: int = csim.SAMPLER_SIZE,
    seed: int = 0,
) -> GppField:
    """`contest_sim.build_field` for the GPP format, keeping the raked weights the sampler ended with (the
    stack lineups are filled with the same ones)."""
    rng = np.random.default_rng(seed)
    t = slate.targets()
    if chalk is None:
        chalk = (
            field_gen.chalk_lineups(slate, min(herd.k, CHALK_K))
            if herd.share > 0
            else np.zeros((0, 9), dtype=int)
        )
    share = herd.share if len(chalk) else 0.0
    held = np.zeros(len(slate))
    if share > 0:
        ew = herd.expected_weights(len(chalk))
        np.add.at(held, chalk.ravel(), np.repeat(ew * share, chalk.shape[1]))
    need = np.maximum(t - held, 0.0) / max(1.0 - share, 1e-9)
    w = field_gen.rake_weights(slate, rates, need, rng, size=min(size, RAKE_SIZE))
    sampler = field_gen.sample_lineups(slate, rates, size, w, rng)
    sim = csim.SimField(fmt="gpp", slate=slate, sampler=sampler, chalk=chalk, herd=herd, own=t)
    return GppField(sim=sim, weights=w, rates=rates)


def top_lines(points: np.ndarray, field: csim.SimField, rng: np.random.Generator) -> np.ndarray:
    """Each draw's top-1% line: the score at which `TOP_FRACTION` of the field's weight is above, the herd's
    lineups at their per-draw shares (`contest_sim` computes the same line for a contest)."""
    n_draws = points.shape[0]
    herd_w = (
        field.herd.draw_weights(len(field.chalk), n_draws, rng) * field.herd_share
        if len(field.chalk)
        else np.zeros((n_draws, 0))
    )
    n_s = len(field.sampler)
    w_s = (1.0 - field.herd_share) / max(n_s, 1)
    out = np.empty(n_draws)
    for lo in range(0, n_draws, csim.CHUNK):
        hi = min(lo + csim.CHUNK, n_draws)
        block = points[lo:hi]
        scores = block[:, field.sampler].sum(axis=2)
        if len(field.chalk):
            scores = np.concatenate([scores, block[:, field.chalk].sum(axis=2)], axis=1)
        weights = np.concatenate([np.full((hi - lo, n_s), w_s, np.float32), herd_w[lo:hi]], axis=1)
        order = np.argsort(-scores, axis=1, kind="stable")
        s_sorted = np.take_along_axis(scores, order, axis=1)
        cum = np.cumsum(np.take_along_axis(weights, order, axis=1), axis=1)
        k = np.minimum((cum < TOP_FRACTION).sum(axis=1), scores.shape[1] - 1)
        out[lo:hi] = s_sorted[np.arange(hi - lo), k]
    return out


def _indicator(lineups: np.ndarray, n_players: int) -> sparse.csr_matrix:
    m = len(lineups)
    return sparse.csr_matrix(
        (
            np.ones(lineups.size, dtype=np.float32),
            lineups.ravel(),
            np.arange(0, lineups.size + 1, LINEUP_SIZE),
        ),
        shape=(m, n_players),
    )


def hit_counts(points: np.ndarray, lineups: np.ndarray, line: np.ndarray) -> np.ndarray:
    """For each lineup, the number of draws in which it scores above that draw's `line`."""
    pts_t = np.ascontiguousarray(points.T, dtype=np.float32)
    out = np.zeros(len(lineups))
    for lo in range(0, len(lineups), SCORE_CHUNK):
        part = lineups[lo : lo + SCORE_CHUNK]
        scores = _indicator(part, points.shape[1]) @ pts_t
        out[lo : lo + SCORE_CHUNK] = (scores > line[None, :]).sum(axis=1)
    return out


# ---------------------------------------------------------------------------------------------------------
# How much of the field plays a stack
# ---------------------------------------------------------------------------------------------------------


def stack_keys(slate: field_gen.Slate, lineups: np.ndarray) -> list[tuple[int, tuple[int, ...], bool]]:
    """Each lineup's stack as `(qb, exact mates, has a bring-back)`; a lineup without exactly one QB gets
    `(-1, (), False)`."""
    out = []
    pos, team, opp = slate.position, slate.team, slate.opp
    offense = np.isin(pos, FLEX_POSITIONS)
    for row in lineups:
        qbs = [i for i in row if pos[i] == "QB"]
        if len(qbs) != 1:
            out.append((-1, (), False))
            continue
        q = qbs[0]
        mates = tuple(sorted(int(i) for i in row if offense[i] and team[i] == team[q]))
        bring = any(offense[i] and team[i] == opp[q] for i in row)
        out.append((int(q), mates, bool(bring)))
    return out


def field_stack_rates(
    slate: field_gen.Slate, gpp: GppField, rates: field_gen.FieldRates, *, sample: int, seed: int
) -> tuple[dict[tuple[int, tuple[int, ...], bool], float], dict[tuple[int, tuple[int, ...]], int]]:
    """The share of the whole field (sampler plus the herd's expected holdings) on each exact
    (QB, mates, bring-back) stack, and how many sampled lineups (bring-back or not) are behind each
    (QB, mates)."""
    rng = np.random.default_rng(seed)
    f = gpp.sim
    sampled = field_gen.sample_lineups(slate, rates, sample, gpp.weights, rng)
    share: dict[tuple[int, tuple[int, ...], bool], float] = {}
    counts: dict[tuple[int, tuple[int, ...]], int] = {}
    w_each = (1.0 - f.herd_share) / max(len(sampled), 1)
    for key in stack_keys(slate, sampled):
        share[key] = share.get(key, 0.0) + w_each
        counts[key[:2]] = counts.get(key[:2], 0) + 1
    if f.herd_share > 0 and len(f.chalk):
        ew = f.herd.expected_weights(len(f.chalk)) * f.herd_share
        for key, w in zip(stack_keys(slate, f.chalk), ew, strict=True):
            share[key] = share.get(key, 0.0) + float(w)
    return share, counts


# ---------------------------------------------------------------------------------------------------------
# The ranking
# ---------------------------------------------------------------------------------------------------------


def _name(slate: field_gen.Slate, i: int, labels: Sequence[str] | None) -> str:
    return str(labels[i]) if labels is not None else slate.names[i]


def rank_stacks(
    slate: field_gen.Slate,
    specs: Sequence,
    gpp: GppField,
    *,
    points: np.ndarray | None = None,
    n_sims: int = DEFAULT_SIMS,
    seed: int = 0,
    per_variant: int = LINEUPS_PER_VARIANT,
    rate_sample: int = RATE_SAMPLE,
    stacks: Sequence[Stack] | None = None,
    labels: Sequence[str] | None = None,
    game_info: pd.DataFrame | None = None,
) -> StackResult:
    """Rank the slate's stacks (see the module docstring). `specs[i]` is slate player i's simulator spec
    (None: scores 0); `points` (draws x players) replaces the simulated draws. `game_info` (optional) has one
    row per team with `team`, `total`, `spread`, `pace` for the games table. `labels` replace the slate names
    in the output (display names)."""
    started = time.perf_counter()
    rates = gpp.rates
    pts = (
        csim.draw_points(specs, n_sims, seed=seed) if points is None else np.asarray(points, dtype=np.float32)
    )
    rng = np.random.default_rng(seed + 7)
    line = top_lines(pts, gpp.sim, rng)
    stacks = list(stacks) if stacks is not None else candidate_stacks(slate, specs)
    variants = [(st, bb) for st in stacks for bb in (False, True)]
    lineups, owner = stack_lineups(slate, gpp.rates, variants, gpp.weights, rng, per_variant=per_variant)
    hits = hit_counts(pts, lineups, line) if len(lineups) else np.zeros(0)
    n_draws = pts.shape[0]
    n_by_variant = np.bincount(owner, minlength=len(variants))
    hit_by_variant = np.bincount(owner, weights=hits, minlength=len(variants))
    rate_of, counts = field_stack_rates(slate, gpp, gpp.rates, sample=rate_sample, seed=seed + 11)
    mix = float(rates.bringback_stacked)
    proj = np.array([s.projection if s is not None else 0.0 for s in specs])
    own = slate.own
    rows = []
    for j, st in enumerate(stacks):
        j_naked, j_bb = 2 * j, 2 * j + 1
        if n_by_variant[j_naked] < MIN_VARIANT_LINEUPS or n_by_variant[j_bb] < MIN_VARIANT_LINEUPS:
            continue
        t_naked = hit_by_variant[j_naked] / (n_by_variant[j_naked] * n_draws)
        t_bb = hit_by_variant[j_bb] / (n_by_variant[j_bb] * n_draws)
        top1 = (1.0 - mix) * t_naked + mix * t_bb
        gen = rate_of.get((st.qb, st.mates, False), 0.0) + rate_of.get((st.qb, st.mates, True), 0.0)
        enough = counts.get((st.qb, st.mates), 0) > MIN_FIELD_LINEUPS
        field_rate = gen if enough else float(np.prod([own[i] for i in (st.qb, *st.mates)]))
        boom = top1 * field_rate / TOP_FRACTION
        q = st.qb
        rows.append(
            {
                "qb": _name(slate, q, labels),
                "mates": ", ".join(_name(slate, i, labels) for i in st.mates),
                "team": str(slate.team[q]),
                "opp": str(slate.opp[q]),
                "k": len(st.mates),
                "salary": int(sum(slate.salary[i] for i in (q, *st.mates))),
                "proj": float(sum(proj[i] for i in (q, *st.mates))),
                "top1": top1,
                "top1_naked": t_naked,
                "top1_bb": t_bb,
                "bb_edge": t_bb - t_naked,
                "lift": top1 / TOP_FRACTION,
                "field": field_rate,
                "field_src": GEN_SRC if enough else OWN_SRC,
                "boom_share": boom,
                "lev": boom - field_rate,
                "qb_i": q,
                "mates_i": st.mates,
            }
        )
    table = pd.DataFrame(rows)
    if not table.empty:
        table = table.sort_values(["lev", "top1"], ascending=False, kind="stable").reset_index(drop=True)
    games = game_table(slate, specs, table, rate_of, game_info, gpp.sim.herd_share)
    meta = {
        "n_sims": n_draws,
        "seed": seed,
        "stacks": len(table),
        "lineups": int(len(lineups)),
        "top_line_mean": float(line.mean()),
        "top_line_sd": float(line.std()),
        "bringback_mix": mix,
        "herd_share": gpp.sim.herd_share,
        "seconds": time.perf_counter() - started,
    }
    return StackResult(table=table, games=games, meta=meta)


def game_table(
    slate: field_gen.Slate,
    specs: Sequence,
    table: pd.DataFrame,
    rate_of: dict,
    game_info: pd.DataFrame | None,
    herd_share: float,
) -> pd.DataFrame:
    """One row per game of the slate's QBs: the combined total and pace (from `game_info`, one row per team),
    the field's stack rate (the share of lineups that stack either team's QB, bring-back or not) and its game-
    stack rate (a stack plus a bring-back), and the best stack's leverage."""
    teams = sorted({str(t) for t, p in zip(slate.team, slate.position, strict=True) if p == "QB"})
    seen: set[frozenset] = set()
    rows = []
    info = game_info.set_index("team") if game_info is not None and not game_info.empty else None
    for t in teams:
        opp = next(
            (
                str(o)
                for tt, o, p in zip(slate.team, slate.opp, slate.position, strict=True)
                if tt == t and p == "QB"
            ),
            "",
        )
        pair = frozenset((t, opp))
        if pair in seen:
            continue
        seen.add(pair)
        teams_in = [x for x in pair if x]
        in_game = {
            i for i in range(len(slate)) if slate.position[i] == "QB" and str(slate.team[i]) in teams_in
        }
        stacked = sum(s for (q, m, _b), s in rate_of.items() if q in in_game and len(m) >= 1)
        game_stack = sum(s for (q, m, b), s in rate_of.items() if q in in_game and len(m) >= 1 and b)
        best = table[table["team"].isin(teams_in)] if not table.empty else table
        row = {
            "game": " v ".join(sorted(teams_in)),
            "teams": tuple(sorted(teams_in)),
            "stack_rate": stacked,
            "game_stack_rate": game_stack,
            "best_lev": float(best["lev"].max()) if len(best) else float("nan"),
        }
        if info is not None:
            have = [x for x in teams_in if x in info.index]
            if have:
                first = info.loc[have[0]]
                row["total"] = float(first.get("total", np.nan))
                row["pace"] = float(np.nanmean([info.loc[x].get("pace", np.nan) for x in have]))
                row["spread"] = abs(float(first.get("spread", np.nan)))
        rows.append(row)
    out = pd.DataFrame(rows)
    if not out.empty and "total" in out.columns:
        out = out.sort_values(["total", "best_lev"], ascending=False, kind="stable").reset_index(drop=True)
    return out
