"""Build shapes for GPP candidates: what a lineup looks like, as constraints on the exact search.

`dfs lineups suggest` used to hold one shape, the QB stack with a bring-back. A tournament is won by being
different from the field where it counts, so the candidate set now covers several shapes, each a SOFT shape:
a rule a finished lineup must satisfy (and a prune that cuts branches which cannot satisfy it), searched with
the same exact branch-and-bound as everything else -- never a hard-coded pick. The simulator then scores every
candidate and the selection picks among them (`lineup_suggest`).

- `stack`: the QB, at least two of his pass catchers and one player from his opponent (a bring-back).
  Evidence: QB1-WR1/WR2/TE1 +0.25 to +0.31 and QB1-opposing WR1 +0.08, all held out of sample.
- `game`: the QB, one of his pass catchers and at least two of the other team's RB/WR/TE. Evidence: the
  bring-back pairs, QB1-QB1 +0.19, and tail dependence of 1.3-1.6x a Gaussian.
- `double`: the QB, at least two of his pass catchers and a RB of his team. Evidence: QB1-RB1 +0.07 (+0.08
  out of sample); weak.
- `rb_dst`: a stack, and a RB with his own team's DST. Evidence: RB1-DST +0.06 (+0.07 out of sample); weak,
  and not larger for favourites.
- `contrarian`: a stack whose summed ownership is in the bottom third of the stacks. About the field, not
  the correlation.

(`docs/SIM.md`, "Game script", has the numbers and what did not survive: nothing is conditioned on the
spread.)

A lineup can match several shapes. Every candidate is tagged with ALL it matches (`shapes_of`) and named by
the most specific (`SPECIFICITY`).

No sheet, no network, no randomness.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping, Sequence

import numpy as np

from dfs.late_swap_search import LineupState, OpenSlot, SwapPlayer, top_lineups
from dfs.models import ROSTER_SLOTS

PASS_CATCHERS = ("WR", "TE")
SKILL = ("RB", "WR", "TE")

STACK, GAME, DOUBLE, RB_DST, CONTRARIAN = "stack", "game", "double", "rb_dst", "contrarian"
SHAPES = (STACK, GAME, DOUBLE, RB_DST, CONTRARIAN)
# The name a shape gives a build, most specific first (a contrarian stack that also pairs a RB with his DST is
# still named for what makes it different from the field).
SPECIFICITY = (CONTRARIAN, RB_DST, DOUBLE, GAME, STACK)
SHAPE_LABELS = {
    STACK: "stack + {opp} bring-back",
    GAME: "game stack",
    DOUBLE: "double stack",
    RB_DST: "stack + RB/DST",
    CONTRARIAN: "contrarian stack",
}
CONTRARIAN_SHARE = 1 / 3  # "the bottom third of stacks" by summed ownership
OWN_FLOOR = 0.005  # a player with no ownership number counts at this share when a stack's ownership is summed
# The `rb_dst` shape tries each QB with his best few DSTs (DST + his team's best RB), the `contrarian` shape
# with his best few stack cores under the ownership cut: which anchors are tried is the heuristic.
RB_DST_OPTIONS = 3
CONTRARIAN_CORES = 3


# --- rules ----------------------------------------------------------------------------------------------


def _qb(players: Sequence[SwapPlayer]) -> SwapPlayer | None:
    return next((p for p in players if p.position == "QB"), None)


def own_catchers(players: Sequence[SwapPlayer], qb: SwapPlayer) -> list[SwapPlayer]:
    return [p for p in players if p.team == qb.team and p.position in PASS_CATCHERS]


def is_stacked(players: Sequence[SwapPlayer]) -> bool:
    """The stack rule: the QB, at least two of his pass catchers and at least one player from his opponent
    (a DST is not a bring-back)."""
    qb = _qb(players)
    if qb is None:
        return False
    return len(own_catchers(players, qb)) >= 2 and any(
        p.team == qb.opp and p.position != "DST" for p in players
    )


def is_game_stack(players: Sequence[SwapPlayer]) -> bool:
    """3-4 from one game on both sides: the QB, at least one of his pass catchers and at least two RB / WR
    / TE of the other team."""
    qb = _qb(players)
    if qb is None:
        return False
    return (
        len(own_catchers(players, qb)) >= 1
        and sum(1 for p in players if p.team == qb.opp and p.position in SKILL) >= 2
    )


def is_double_stack(players: Sequence[SwapPlayer]) -> bool:
    """The QB, at least two of his pass catchers and a RB of his own team."""
    qb = _qb(players)
    if qb is None:
        return False
    return len(own_catchers(players, qb)) >= 2 and any(
        p.team == qb.team and p.position == "RB" for p in players
    )


def has_rb_dst(players: Sequence[SwapPlayer]) -> bool:
    """A RB and his own team's DST."""
    dsts = {p.team for p in players if p.position == "DST"}
    return any(p.position == "RB" and p.team in dsts for p in players)


def stack_core(players: Sequence[SwapPlayer]) -> tuple[SwapPlayer, ...]:
    """The correlated group of a stack: the QB, his pass catchers and the other team's non-DST players of his
    game (the bring-back). Empty for a lineup without a QB."""
    qb = _qb(players)
    if qb is None:
        return ()
    return tuple(
        p
        for p in players
        if p is qb
        or (p.team == qb.team and p.position in PASS_CATCHERS)
        or (p.team == qb.opp and p.position != "DST")
    )


def stack_ownership(players: Sequence[SwapPlayer], own: Mapping[str, float]) -> float:
    """The summed ownership of a stack's core (players without a number count at `OWN_FLOOR`)."""
    return float(sum(max(OWN_FLOOR, own.get(p.name, 0.0)) for p in stack_core(players)))


def contrarian_cutoff(lineups: Sequence[Sequence[SwapPlayer]], own: Mapping[str, float]) -> float | None:
    """The summed-ownership value that separates the bottom third of the stacks from the rest: the
    `CONTRARIAN_SHARE` quantile of `stack_ownership` over the DISTINCT stacks (the same core in several
    lineups counts once) among `lineups`. None when there are no stacked lineups."""
    cores = {
        frozenset(p.name for p in stack_core(lu)): stack_ownership(lu, own)
        for lu in lineups
        if is_stacked(lu)
    }
    if not cores:
        return None
    return float(np.quantile(list(cores.values()), CONTRARIAN_SHARE))


def shapes_of(
    players: Sequence[SwapPlayer],
    *,
    contrarian_at: float | None = None,
    own: Mapping[str, float] | None = None,
) -> tuple[str, ...]:
    """Every shape the lineup matches, in `SHAPES` order. `contrarian` needs the cutoff and the ownership."""
    out = []
    if is_stacked(players):
        out.append(STACK)
    if is_game_stack(players):
        out.append(GAME)
    if is_double_stack(players):
        out.append(DOUBLE)
    if is_stacked(players) and has_rb_dst(players):
        out.append(RB_DST)
    if contrarian_at is not None and own is not None and is_stacked(players):
        if stack_ownership(players, own) <= contrarian_at:
            out.append(CONTRARIAN)
    return tuple(out)


def primary_shape(shapes: Sequence[str]) -> str:
    """The most specific of `shapes` ('' when there are none)."""
    return next((s for s in SPECIFICITY if s in shapes), "")


def shape_name(players: Sequence[SwapPlayer], shapes: Sequence[str]) -> str:
    """ "DET game stack", "DET stack + ARI bring-back": the QB's team and the primary shape."""
    qb = _qb(players)
    shape = primary_shape(shapes)
    if qb is None or not shape:
        return ""
    return f"{qb.team} " + SHAPE_LABELS[shape].format(opp=qb.opp)


# --- the search -----------------------------------------------------------------------------------------


def _slots_left(rest: Sequence[str]) -> tuple[int, int, int]:
    """(slots that can hold a pass catcher: TE / WR / FLEX, slots that can hold a RB: RB / FLEX, skill "
    "slots)."""
    receivers = sum(1 for s in rest if s in ("TE", "WR", "FLEX"))
    backs = sum(1 for s in rest if s in ("RB", "FLEX"))
    skill = sum(1 for s in rest if s in ("RB", "TE", "WR", "FLEX"))
    return receivers, backs, skill


def feasible_for(
    shape: str,
    qb: SwapPlayer,
    *,
    dst: SwapPlayer | None = None,
    own: Mapping[str, float] | None = None,
    max_stack_own: float | None = None,
    pool: Sequence[SwapPlayer] = (),
) -> Callable[[list[SwapPlayer], list[str]], bool]:
    """A `top_lineups(feasible=...)` for one QB's `shape`: from a partial lineup, can the slots still to fill
    hold what the shape still needs? Never says no to a lineup that can pass (the search relies on it).

    `dst` (the `rb_dst` shape searches each DST on its own) adds "a RB of that DST's team". With `own` and
    `max_stack_own` (the `contrarian` cut) a branch is also cut once its stack's summed ownership, plus the
    least the players it still needs can add, is over the cut: ownership only ever adds up as a stack
    fills."""
    floor = {}
    if own is not None and max_stack_own is not None:
        mates = [own.get(p.name, 0.0) for p in pool if p.team == qb.team and p.position in PASS_CATCHERS]
        backs = [own.get(p.name, 0.0) for p in pool if p.team == qb.opp and p.position != "DST"]
        floor = {
            "mate": max(OWN_FLOOR, min(mates, default=OWN_FLOOR)),
            "back": max(OWN_FLOOR, min(backs, default=OWN_FLOOR)),
        }

    def mates(placed) -> int:
        return sum(1 for p in placed if p.team == qb.team and p.position in PASS_CATCHERS)

    def opp_skill(placed) -> int:
        return sum(1 for p in placed if p.team == qb.opp and p.position in SKILL)

    def stack_needs(placed) -> tuple[int, int]:
        back = any(p.team == qb.opp and p.position != "DST" for p in placed)
        return max(0, 2 - mates(placed)), 0 if back else 1

    def feasible(placed: list[SwapPlayer], rest: list[str]) -> bool:
        receivers, backs, skill = _slots_left(rest)
        if shape in (STACK, CONTRARIAN, RB_DST):
            need_mates, need_back = stack_needs(placed)
            if not (need_mates <= receivers and need_mates + need_back <= skill):
                return False
            if shape == RB_DST and dst is not None:
                if not any(p.team == dst.team and p.position == "RB" for p in placed) and backs < 1:
                    return False
            if floor:
                core = sum(max(OWN_FLOOR, own.get(p.name, 0.0)) for p in stack_core(placed))
                if core + need_mates * floor["mate"] + need_back * floor["back"] > max_stack_own + 1e-12:
                    return False
            return True
        if shape == GAME:
            need_mates, need_opp = max(0, 1 - mates(placed)), max(0, 2 - opp_skill(placed))
            return need_mates <= receivers and need_mates + need_opp <= skill
        if shape == DOUBLE:
            need_mates = max(0, 2 - mates(placed))
            need_rb = 0 if any(p.team == qb.team and p.position == "RB" for p in placed) else 1
            return need_mates <= receivers and need_rb <= backs and need_mates + need_rb <= skill
        raise ValueError(f"unknown shape {shape!r}")

    return feasible


def rule_for(shape: str) -> Callable[[Sequence[SwapPlayer]], bool]:
    """The finished-lineup test of one shape (`contrarian` is a stack; its ownership cut is applied by
    `shape_lineups` from `own` / `max_stack_own`)."""
    return {
        STACK: is_stacked,
        GAME: is_game_stack,
        DOUBLE: is_double_stack,
        RB_DST: lambda ps: is_stacked(ps) and has_rb_dst(ps),
        CONTRARIAN: is_stacked,
    }[shape]


def seat(players: Sequence[SwapPlayer]) -> dict[int, SwapPlayer] | None:
    """`players` fixed into free `ROSTER_SLOTS` (a position's own slot first, then FLEX for an RB / WR / TE);
    None when they do not fit."""
    taken: dict[int, SwapPlayer] = {}
    for p in players:
        free = [i for i, slot in enumerate(ROSTER_SLOTS) if slot == p.position and i not in taken]
        if not free and p.position in SKILL:
            free = [i for i, slot in enumerate(ROSTER_SLOTS) if slot == "FLEX" and i not in taken]
        if not free:
            return None
        taken[free[0]] = p
    return taken


def stack_cores(
    qb: SwapPlayer, pool: Sequence[SwapPlayer], own: Mapping[str, float], max_stack_own: float, limit: int
) -> list[tuple[SwapPlayer, ...]]:
    """The `limit` best stack cores of `qb` (him, two of his pass catchers, one non-DST player of his
    opponent) whose summed ownership is within `max_stack_own`, by the cores' projection."""
    mates = [p for p in pool if p.team == qb.team and p.position in PASS_CATCHERS]
    backs = [p for p in pool if p.team == qb.opp and p.position != "DST"]
    cores = []
    for i, m1 in enumerate(mates):
        for m2 in mates[i + 1 :]:
            for back in backs:
                core = (qb, m1, m2, back)
                total = sum(max(OWN_FLOOR, own.get(p.name, 0.0)) for p in core)
                if total <= max_stack_own + 1e-12:
                    cores.append((-sum(p.value for p in core), tuple(sorted(p.name for p in core)), core))
    return [core for _, _, core in sorted(cores)[:limit]]


def shape_lineups(
    pool: list[SwapPlayer],
    shape: str,
    *,
    cap: int,
    per_qb: int,
    deadline: float | None = None,
    leaf_ok: Callable[[Sequence[SwapPlayer]], bool] | None = None,
    own: Mapping[str, float] | None = None,
    max_stack_own: float | None = None,
    rb_dst_options: int = RB_DST_OPTIONS,
    contrarian_cores: int = CONTRARIAN_CORES,
) -> tuple[list[list[SwapPlayer]], bool]:
    """The best lineups of `shape` from `pool` by projection: about `per_qb` for each quarterback, best first
    (ties by salary, then names). One exact search per QB, as `lineup_suggest.stacked_lineups` always did: he
    is fixed in the QB slot and the other eight are searched for the best lineups that satisfy the shape (and
    `leaf_ok`, an extra whole-lineup rule), cutting every branch that can no longer reach it.

    A rule the search's bound cannot see (a RB with his own DST, a stack under an ownership cut) makes that
    search slow, because most branches only die at the leaf. Those two shapes therefore fix more than the QB:
    `rb_dst` searches each QB with his best `rb_dst_options` DSTs and, for each, a RB of the DST's team fixed
    (DST value plus his team's best RB ranks the DSTs); `contrarian` (which needs `own` and `max_stack_own`,
    the cut on a stack's summed ownership) searches each QB with his best `contrarian_cores` stack cores
    under the cut fixed. Inside each such anchor the search is exact; which anchors are tried is the
    heuristic. The time left is shared out over the searches (each may use up to twice its even share or a
    quarter of what is left). Returns the lineups and whether every search finished."""
    if shape == CONTRARIAN and (own is None or max_stack_own is None):
        raise ValueError("the contrarian shape needs ownership and a cutoff")
    rule = rule_for(shape)
    quarterbacks = [p for p in pool if p.position == "QB"]

    best_rb: dict[str, float] = {}
    for p in pool:
        if p.position == "RB":
            best_rb[p.team] = max(best_rb.get(p.team, float("-inf")), p.value)
    dsts = [p for p in pool if p.position == "DST" and p.team in best_rb]  # (rb_dst) needs a RB on his team

    def search(fixed_players, qb, **feasible_kwargs):
        fixed = seat(fixed_players)
        if fixed is None:
            return None
        names = {p.name for p in fixed_players}
        rest = [p for p in pool if p.name not in names and p.position != "QB"]
        if any(p.position == "DST" for p in fixed_players):
            rest = [p for p in rest if p.position != "DST"]
        return fixed, rest, feasible_for(shape, qb, **feasible_kwargs)

    anchors: list[list] = []  # per QB, his searches: (fixed slots, players to search among, feasibility)
    for qb in quarterbacks:
        if shape == RB_DST:
            options = sorted(
                (d for d in dsts if d.team != qb.opp),
                key=lambda d: (-(d.value + best_rb.get(d.team, 0.0)), d.name),
            )[:rb_dst_options]
            found_ = [
                search([qb, d, r], qb, dst=d)
                for d in options
                for r in pool
                if r.position == "RB" and r.team == d.team
            ]
        elif shape == CONTRARIAN:
            found_ = [
                search(list(core), qb, own=own, max_stack_own=max_stack_own, pool=pool)
                for core in stack_cores(qb, pool, own, max_stack_own, contrarian_cores)
            ]
        else:
            found_ = [search([qb], qb, own=own, max_stack_own=max_stack_own, pool=pool)]
        if found_ := [a for a in found_ if a is not None]:
            anchors.append(found_)

    searches = [(*a, max(2, -(-per_qb // len(group)))) for group in anchors for a in group]
    found: dict[frozenset[str], list[SwapPlayer]] = {}
    complete = True
    for n, (fixed, rest, feasible, k) in enumerate(searches):
        left = None if deadline is None else max(0.0, deadline - time.monotonic())
        # Searches differ in cost, and a cheap one that follows leaves its unused time to nobody if the early
        # ones are cut at exactly 1/n: a search may use up to twice its even share, or a quarter of what is
        # left if that is more (a few slow ones cannot starve the rest: the time left shrinks geometrically)
        share = None if left is None else min(left, max(2 * left / (len(searches) - n), 0.25 * left))
        state = LineupState(
            cap=cap,
            fixed=fixed,
            open=[OpenSlot(i, slot, None) for i, slot in enumerate(ROSTER_SLOTS) if i not in fixed],
        )
        assignments, done = top_lineups(
            state,
            rest,
            k=k if shape in (RB_DST, CONTRARIAN) else per_qb,
            leaf_filter=lambda ps: rule(ps) and (leaf_ok is None or leaf_ok(ps)),
            feasible=feasible,
            time_limit=share,
        )
        complete = complete and done
        for assignment in assignments:
            lineup = [*fixed.values(), *assignment.values()]
            qb = next(p for p in lineup if p.position == "QB")
            lineup = [qb, *sorted((p for p in lineup if p is not qb), key=lambda p: p.name)]
            found.setdefault(frozenset(p.name for p in lineup), lineup)
    ranked = sorted(
        found.values(),
        key=lambda lu: (
            -round(sum(p.value for p in lu), 6),
            sum(p.salary for p in lu),
            tuple(sorted(p.name for p in lu)),
        ),
    )
    return ranked, complete
