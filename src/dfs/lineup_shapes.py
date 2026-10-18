"""Build shapes for GPP candidates: what a lineup looks like, as constraints on the exact search.

`dfs lineups suggest` used to hold one shape, the QB stack with a bring-back. A tournament is won by being
different from the field where it counts, so the candidate set covers several shapes, each a SOFT shape: a
rule a finished lineup must satisfy (and a prune that cuts branches which cannot satisfy it), searched with
the same exact branch-and-bound as everything else -- never a hard-coded pick. The simulator then scores every
candidate and the selection picks among them (`lineup_suggest`).

**A stack is the QB plus one to three of his teammates of ANY skill position** (RB, WR or TE, in any mix:
QB+RB, QB+RB+WR, QB+WR+TE all count), with or without a bring-back (a player of the other team's offense). The
bring-back is NOT required: whether it earns its place is the simulator's call, and `docs/SIM.md` ("Game
script") measures what it adds by the stacked team's spread.

- `stack`: a stack with a bring-back.
- `naked`: a stack without one (no player of the opponent's RB / WR / TE).
- `rb_stack`: a stack that holds a RB of the QB's team (QB+RB combinations; whether a back moves with his
  QB depends on how much he catches, see `docs/SIM.md`, "Team shape").
- `game`: the QB, a teammate and at least two RB / WR / TE of the other team.
- `rb_dst`: a stack, and a RB with his own team's DST.
- `contrarian`: a stack whose summed ownership is in the bottom third of the stacks.

A lineup can match several shapes. Every candidate is tagged with ALL it matches (`shapes_of`); its name
(`shape_name`) says its teammates and whether it carries a bring-back ("DET stack QB+WR+TE + ARI
bring-back", "DET naked stack QB+RB+WR").

No sheet, no network, no randomness.
"""

from __future__ import annotations

import time
from collections import Counter
from collections.abc import Callable, Mapping, Sequence

import numpy as np

from dfs.late_swap_search import LineupState, OpenSlot, SwapPlayer, top_lineups
from dfs.models import ROSTER_SLOTS

PASS_CATCHERS = ("WR", "TE")
SKILL = ("RB", "WR", "TE")
MAX_MATES = 3  # a stack is the QB and one to three teammates

STACK, NAKED, RB_STACK, GAME, RB_DST, CONTRARIAN = (
    "stack",
    "naked",
    "rb_stack",
    "game",
    "rb_dst",
    "contrarian",
)
SHAPES = (STACK, NAKED, RB_STACK, GAME, RB_DST, CONTRARIAN)
SHAPE_LABELS = {
    STACK: "stack + bring-back",
    NAKED: "naked stack",
    RB_STACK: "RB stack",
    GAME: "game stack",
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


def teammates(players: Sequence[SwapPlayer], qb: SwapPlayer) -> list[SwapPlayer]:
    """The QB's teammates at RB / WR / TE."""
    return [p for p in players if p.team == qb.team and p.position in SKILL]


def bring_backs(players: Sequence[SwapPlayer], qb: SwapPlayer) -> list[SwapPlayer]:
    """The other team's RB / WR / TE (a DST is not a bring-back)."""
    return [p for p in players if p.team == qb.opp and p.position in SKILL]


def is_stacked(players: Sequence[SwapPlayer]) -> bool:
    """The stack rule: the QB and one to `MAX_MATES` of his teammates at RB / WR / TE, in any mix. A
    bring-back is neither required nor excluded."""
    qb = _qb(players)
    return qb is not None and 1 <= len(teammates(players, qb)) <= MAX_MATES


def has_bring_back(players: Sequence[SwapPlayer]) -> bool:
    qb = _qb(players)
    return qb is not None and bool(bring_backs(players, qb))


def is_stack_with_bring_back(players: Sequence[SwapPlayer]) -> bool:
    return is_stacked(players) and has_bring_back(players)


def is_naked_stack(players: Sequence[SwapPlayer]) -> bool:
    return is_stacked(players) and not has_bring_back(players)


def is_rb_stack(players: Sequence[SwapPlayer]) -> bool:
    """A stack that holds a RB of the QB's team."""
    qb = _qb(players)
    return is_stacked(players) and any(p.team == qb.team and p.position == "RB" for p in players)


def is_game_stack(players: Sequence[SwapPlayer]) -> bool:
    """3-4 from one game on both sides: the QB, a teammate (one to three) and at least two RB / WR / TE of the
    other team."""
    qb = _qb(players)
    return is_stacked(players) and len(bring_backs(players, qb)) >= 2


def has_rb_dst(players: Sequence[SwapPlayer]) -> bool:
    """A RB and his own team's DST."""
    dsts = {p.team for p in players if p.position == "DST"}
    return any(p.position == "RB" and p.team in dsts for p in players)


def stack_core(players: Sequence[SwapPlayer]) -> tuple[SwapPlayer, ...]:
    """The correlated group of a stack: the QB, his teammates and the other team's RB / WR / TE (the
    bring-back). Empty for a lineup without a QB."""
    qb = _qb(players)
    if qb is None:
        return ()
    return tuple(
        p for p in players if p is qb or p in teammates(players, qb) or p in bring_backs(players, qb)
    )


def composition(players: Sequence[SwapPlayer]) -> str:
    """ "QB+RB+WR+WR" written "QB+RB+2WR": the QB and his teammates by position."""
    qb = _qb(players)
    if qb is None:
        return ""
    counts = Counter(p.position for p in teammates(players, qb))
    return "QB" + "".join(f"+{counts[pos] if counts[pos] > 1 else ''}{pos}" for pos in SKILL if counts[pos])


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
    if is_stack_with_bring_back(players):
        out.append(STACK)
    if is_naked_stack(players):
        out.append(NAKED)
    if is_rb_stack(players):
        out.append(RB_STACK)
    if is_game_stack(players):
        out.append(GAME)
    if is_stacked(players) and has_rb_dst(players):
        out.append(RB_DST)
    if contrarian_at is not None and own is not None and is_stacked(players):
        if stack_ownership(players, own) <= contrarian_at:
            out.append(CONTRARIAN)
    return tuple(out)


def shape_name(players: Sequence[SwapPlayer], shapes: Sequence[str] = ()) -> str:
    """The build's name: the QB's team, what is special about it, the stack's make-up and its bring-back --
    "DET stack QB+WR+TE + ARI bring-back", "DET naked stack QB+RB+WR", "DET contrarian game stack QB+WR + ARI
    bring-back", "DET stack QB+WR+WR + RB/DST". Empty for a lineup that is not a stack."""
    qb = _qb(players)
    if qb is None or not is_stacked(players):
        return ""
    words = ["contrarian"] * (CONTRARIAN in shapes) + ["game"] * (GAME in shapes)
    words.append("naked stack" if not has_bring_back(players) else "stack")
    name = f"{qb.team} {' '.join(words)} {composition(players)}"
    if has_bring_back(players):
        name += f" + {qb.opp} bring-back"
    if RB_DST in shapes:
        name += " + RB/DST"
    return name


# --- the search -----------------------------------------------------------------------------------------


def can_stack(qb: SwapPlayer, pool: Sequence[SwapPlayer]) -> bool:
    """Can ANY lineup stack this QB from `pool`: does he have at least one teammate at RB / WR / TE? A QB who
    cannot is left out of the GPP search: the search cannot see that, so it used to spend its whole share of
    the time limit on a stack that does not exist (a 2026-10-17 run of a real 63-player pool: 7 of 12 QBs,
    57 of 60 seconds, and the other five left short of time)."""
    return any(p.team == qb.team and p.position in SKILL for p in pool)


def can_reach(shape: str, qb: SwapPlayer, pool: Sequence[SwapPlayer]) -> bool:
    """Can ANY lineup from `pool` hold `shape` around this QB? (Searching when none can is wasted time.)"""
    if not can_stack(qb, pool):
        return False
    opp = sum(1 for p in pool if p.team == qb.opp and p.position in SKILL)
    if shape == STACK:
        return opp >= 1
    if shape == GAME:
        return opp >= 2
    if shape == RB_STACK:
        return any(p.team == qb.team and p.position == "RB" for p in pool)
    return True


def _slots_left(rest: Sequence[str]) -> tuple[int, int]:
    """(slots that can hold a RB: RB / FLEX, slots that can hold any skill player: RB / WR / TE / FLEX)."""
    backs = sum(1 for s in rest if s in ("RB", "FLEX"))
    skill = sum(1 for s in rest if s in ("RB", "TE", "WR", "FLEX"))
    return backs, skill


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
    least the teammates it still needs can add, is over the cut: ownership only ever adds up as a stack
    fills."""
    floor = None
    if own is not None and max_stack_own is not None:
        owned = [own.get(p.name, 0.0) for p in pool if p.team == qb.team and p.position in SKILL]
        floor = max(OWN_FLOOR, min(owned, default=OWN_FLOOR))

    def feasible(placed: list[SwapPlayer], rest: list[str]) -> bool:
        backs, skill = _slots_left(rest)
        m = sum(1 for p in placed if p.team == qb.team and p.position in SKILL)
        b = sum(1 for p in placed if p.team == qb.opp and p.position in SKILL)
        if m > MAX_MATES:
            return False
        need_m = max(0, 1 - m)
        if shape == NAKED:
            return b == 0 and need_m <= skill
        if shape == GAME:
            return need_m + max(0, 2 - b) <= skill
        if shape == STACK:
            return need_m + (0 if b else 1) <= skill
        if shape == RB_STACK:
            has_rb = any(p.team == qb.team and p.position == "RB" for p in placed)
            return (has_rb or backs >= 1) and need_m + (0 if has_rb else 1) - (
                0 if has_rb else need_m
            ) <= skill
        # rb_dst and contrarian: a stack, optionally with a bring-back
        if need_m > skill:
            return False
        if shape == RB_DST and dst is not None:
            if not any(p.team == dst.team and p.position == "RB" for p in placed) and backs < 1:
                return False
        if floor is not None:
            core = sum(max(OWN_FLOOR, own.get(p.name, 0.0)) for p in stack_core(placed))
            if core + need_m * floor > max_stack_own + 1e-12:
                return False
        return True

    return feasible


def rule_for(shape: str) -> Callable[[Sequence[SwapPlayer]], bool]:
    """The finished-lineup test of one shape (`contrarian` is a stack; its ownership cut is applied by
    `shape_lineups` from `own` / `max_stack_own`)."""
    return {
        STACK: is_stack_with_bring_back,
        NAKED: is_naked_stack,
        RB_STACK: is_rb_stack,
        GAME: is_game_stack,
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
    """The `limit` best stack cores of `qb` (him and two teammates, with or without one player of his
    opponent) whose summed ownership is within `max_stack_own`, by the cores' projection."""
    mates = [p for p in pool if p.team == qb.team and p.position in SKILL]
    backs = [p for p in pool if p.team == qb.opp and p.position in SKILL]
    cores = []
    for i, m1 in enumerate(mates):
        for m2 in mates[i + 1 :]:
            for back in (None, *backs):
                core = (qb, m1, m2) if back is None else (qb, m1, m2, back)
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
    heuristic. The time left is shared out over the searches: evenly for the plain stack, and for the other
    shapes each may use up to twice its even share or a quarter of what is left. Returns the lineups and
    whether every search finished."""
    if shape == CONTRARIAN and (own is None or max_stack_own is None):
        raise ValueError("the contrarian shape needs ownership and a cutoff")
    rule = rule_for(shape)
    quarterbacks = [p for p in pool if p.position == "QB" and can_reach(shape, p, pool)]

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
        if shape == NAKED:  # no player of the other team's offense: they are not in the pool being searched
            rest = [p for p in rest if not (p.team == qb.opp and p.position in SKILL)]
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
        if (
            shape == STACK
        ):  # the base search: an even share, so a short budget thins every QB, never drops the last
            share = None if left is None else left / (len(searches) - n)
        else:
            # Searches differ in cost, and a cheap one that follows leaves its unused time to nobody if the
            # early ones are cut at exactly 1/n: a search may use up to twice its even share, or a quarter of
            # what is left if that is more (a few slow ones cannot starve the rest: the time shrinks
            # geometrically)
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
