"""The exact builder search (blueprint Phase E) and the switch that chooses a solver.

`dfs lineups suggest` finds its candidates with the branch-and-bound of `late_swap_search`, inside a budget
(`search_clock`). For the cash lineups that search finishes in seconds. For the GPP shapes it does not: a
stack, a bring-back or a RB with his own DST is a rule its bound cannot see, so most branches only die at the
leaf, the budget runs out, and a build is partly "what the clock let it find" (120 s was cut short in Weeks
4-6; so was every Flashback verdict built on it).

**Three solvers, one switch.** `use("search")` (the default: today's budgeted search, unchanged),
`use("exact")` and `use("milp")` set which one `top_lineups` here runs; `lineup_suggest` and `lineup_shapes`
call this `top_lineups` in place of `late_swap_search`'s, and nothing else about a build changes: the same
pool, the same rules, the same objective (the projection, the cheaper lineup among exact ties), the same
anchors for the `rb_dst` and `contrarian` shapes, the same simulator and selection afterwards. `[builds]
solver` in config.toml and in a `dfs backtest` settings file turn it on. Late swap never comes through here.

**`exact`: the same branch-and-bound, with the stack made visible.** A stack search for one quarterback is
split into cases that together cover every lineup exactly once, and in each case the shape holds by
construction, so the bound is tight and nothing dies at a leaf:

- "at least one teammate": the cases are "his first teammate in pool order is this one, the ones before him
  are out" (for `rb_stack`, his first RB). The others stay in the pool, and "at most three" is the prune
  `lineup_shapes.feasible_for` already makes the moment a fourth is placed. Under a `QB+RB+WR only` stack rule
  every allowed set of teammates is a case instead, held fixed with the rest of them out;
- the bring-back: `naked` has the other team's offense out of the pool; `stack` needs one and `game` two, and
  those cases are "the first bring-back(s) in pool order are these, the ones before them are out";
- "never nine Cash-marked players" only matters when few players are marked GPP: then the cases are "the
  first marked player is this one".

Each case is the old search on what is left (`late_swap_search._Search`), told the score the best `k` so far
already reach, so a case that cannot beat them ends at once. The whole-lineup test (`leaf_filter`) and the
feasibility test still run on every lineup: the cases are an accelerator, the tests stay the definition of a
legal lineup. There is no budget; `SAFETY_SECONDS` (about twenty million nodes for ONE search, more than the
old search needed for a whole week run to its end) is a circuit breaker that reports the search as cut short
rather than hang.

**`milp`: the independent checker.** `build_milp.top_lineups` states the same search as a mixed-integer
program for HiGHS (`scipy.optimize.milp`), every rule a linear constraint, the next-best lineup from a no-good
cut. It shares no search code with the above, which is its value: Weeks 2-9 are checked three ways (the old
search run to completion, `exact`, `milp`: `scripts/exact_equivalence.py`, results in `docs/SIM.md`) and
`tests/test_build_exact.py` keeps the two agreeing on a fixture. It is far too slow to build with: HiGHS needs
70-400 ms for each lineup and more as the cuts pile up, 15-20 minutes for a week's 1,000-1,300 candidates
where `exact` takes seconds.

No sheet, no network, no randomness.
"""

from __future__ import annotations

import itertools
from collections import Counter
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any

from dfs import search_clock as clock
from dfs.late_swap_search import (
    _SALARY_TIEBREAK,
    LineupState,
    OpenSlot,
    SwapPlayer,
    _assign_to_slots,
    _Search,
    conflicts,
)
from dfs.late_swap_search import top_lineups as budgeted_top_lineups
from dfs.models import ROSTER_SLOTS

SEARCH, EXACT, MILP = "search", "exact", "milp"
SOLVERS = (SEARCH, EXACT, MILP)
DEFAULT_SOLVER = SEARCH  # the flag is off: today's budgeted search

# One search may visit this much before it is reported as cut short (budget seconds, `search_clock`: about
# twenty million nodes). The old search run to its end needed 92-315 budget seconds for a WHOLE week.
SAFETY_SECONDS = 600.0
# "At least one of these players" is split into cases only when this few of them are in the pool; with more,
# almost every lineup holds one and the whole-lineup test is enough.
SCARCE = 12
TIE = 1e-9  # scores closer than this are a tie (the smallest real step is the cheaper-lineup nudge, 1e-7)

_solver = DEFAULT_SOLVER


def solver() -> str:
    """The solver `top_lineups` runs right now."""
    return _solver


def check_solver(name: str) -> str:
    if name not in SOLVERS:
        raise ValueError(f"solver must be one of {', '.join(SOLVERS)}, not {name!r}")
    return name


@contextmanager
def use(name: str) -> Iterator[None]:
    """Run every builder search inside the block with solver `name`."""
    global _solver
    before, _solver = _solver, check_solver(name)
    try:
        yield
    finally:
        _solver = before


# ---------------------------------------------------------------------------------------------
# What a whole-lineup test checks, as data
# ---------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class LeafRules:
    """What a whole-lineup test (`leaf_ok`) requires, for a solver that can use it before a lineup is
    finished. It must never be stricter than the test itself: the test still runs on every lineup, so a
    description that leaves something out only costs time."""

    need_one_of: frozenset[str] | None = None  # at least one of these names (None: no such rule)
    max_per_game: int | None = None  # at most this many players from one game
    stacks: Any = None  # `suggest_state.StackRules` (no_stack / no_bring_back / only), or None


def described(test: Callable, rules: LeafRules) -> Callable:
    """`test` carrying its own description (`rules_of` reads it back)."""
    test.leaf_rules = rules  # type: ignore[attr-defined]
    return test


def rules_of(test: Callable | None) -> LeafRules | None:
    return getattr(test, "leaf_rules", None)


# ---------------------------------------------------------------------------------------------
# The switch
# ---------------------------------------------------------------------------------------------


def top_lineups(
    state: LineupState,
    candidates: list[SwapPlayer],
    *,
    k: int,
    leaf_filter: Callable[[list[SwapPlayer]], bool] | None = None,
    feasible: Callable[[list[SwapPlayer], list[str]], bool] | None = None,
    time_limit: float | None = None,
    shape: str | None = None,
    qb: SwapPlayer | None = None,
    own: Any = None,
    max_stack_own: float | None = None,
    rules: LeafRules | None = None,
) -> tuple[list[dict[int, SwapPlayer]], bool]:
    """`late_swap_search.top_lineups` by the solver in use: the best `k` lineups over the open slots, best
    first, each as `slot index -> player`, and whether the search finished.

    `shape` and `qb` say what `leaf_filter` and `feasible` are for (a `lineup_shapes` shape around that
    quarterback, who is in `state.fixed`), `own` / `max_stack_own` the contrarian cut and `rules` the
    whole-lineup test as data (default: what `leaf_filter` carries). The budgeted search ignores them; the
    exact solvers ignore `time_limit`."""
    if _solver == SEARCH:
        return budgeted_top_lineups(
            state, candidates, k=k, leaf_filter=leaf_filter, feasible=feasible, time_limit=time_limit
        )
    rules = rules if rules is not None else rules_of(leaf_filter)
    if _solver == MILP:
        from dfs import build_milp

        return build_milp.top_lineups(
            state,
            candidates,
            k=k,
            leaf_filter=leaf_filter,
            feasible=feasible,
            shape=shape,
            qb=qb,
            own=own,
            max_stack_own=max_stack_own,
            rules=rules,
        )
    return exact_top_lineups(
        state, candidates, k=k, leaf_filter=leaf_filter, feasible=feasible, shape=shape, qb=qb, rules=rules
    )


# ---------------------------------------------------------------------------------------------
# The exact search
# ---------------------------------------------------------------------------------------------


def score(players: Sequence[SwapPlayer]) -> float:
    """What the search maximises over `players`: the projection, less the nudge that prefers the cheaper
    lineup among exact ties (`late_swap_search._Search`). Summed in name order, so a lineup has one score."""
    return sum(p.value - _SALARY_TIEBREAK * p.salary / 100 for p in sorted(players, key=lambda p: p.name))


def most_from_one_game(players: Sequence[SwapPlayer]) -> int:
    return max(Counter(p.game for p in players if p.game).values(), default=0)


def back_cases(
    backs: list[SwapPlayer], need: int
) -> Iterator[tuple[tuple[SwapPlayer, ...], list[SwapPlayer]]]:
    """Every way to hold at least `need` of `backs` (the bring-backs, or the teammates), each lineup in
    exactly one case: `(held, still free)`. The held ones are the first `need` of the lineup's in list order,
    so the players before the last of them that are not held are out, and the ones after it stay free."""
    if need <= 0:
        yield (), backs
        return
    for combo in itertools.combinations(range(len(backs)), need):
        yield tuple(backs[i] for i in combo), backs[combo[-1] + 1 :]


def stack_cases(
    held: list[SwapPlayer],
    candidates: list[SwapPlayer],
    qb: SwapPlayer,
    shape: str,
    rules: LeafRules | None,
) -> Iterator[tuple[tuple[SwapPlayer, ...], list[SwapPlayer]]]:
    """The cases of one quarterback's `shape` search: `(players to fix on top of `held`, the pool for the
    other slots)`. Every lineup that can pass the shape's rule (and the stack rules in `rules`) is in exactly
    one case; a case may still hold lineups that fail another rule, which the whole-lineup test removes."""
    from dfs import lineup_shapes as sh

    def mate(p: SwapPlayer) -> bool:
        return p.team == qb.team and p.position in sh.SKILL

    def back(p: SwapPlayer) -> bool:
        return p.team == qb.opp and p.position in sh.SKILL and not mate(p)

    mates = [p for p in candidates if mate(p)]
    backs = [p for p in candidates if back(p)]
    others = [p for p in candidates if not mate(p) and not back(p)]
    held_mates = [p for p in held if mate(p)]
    held_backs = [p for p in held if back(p)]
    stacks = rules.stacks if rules is not None else None
    if stacks and qb.team in stacks.no_stack:
        return
    need = {sh.STACK: 1, sh.GAME: 2}.get(shape, 0)
    if shape == sh.NAKED or (stacks and qb.team in stacks.no_bring_back):
        if need or held_backs:
            return  # a bring-back is required, or already held, where none is allowed
        backs = []
    need = max(0, need - len(held_backs))
    allowed = stacks.only.get(qb.team) if stacks else None
    room = sh.MAX_MATES - len(held_mates)
    wants_rb = shape == sh.RB_STACK and not any(p.position == "RB" for p in held_mates)
    if allowed:  # the make-up is a rule: every allowed set of teammates is a case, the rest of them out
        mate_cases = [
            (chosen, [])
            for n in range(room + 1)
            for chosen in itertools.combinations(mates, n)
            if len(held_mates) + n >= 1
            and not (wants_rb and not any(p.position == "RB" for p in chosen))
            and sh.composition([qb, *held_mates, *chosen]) in allowed
        ]
    elif wants_rb:  # his first RB; his other teammates stay in the pool
        rest = [p for p in mates if p.position != "RB"]
        mate_cases = [
            (first, [*later, *rest])
            for first, later in back_cases([p for p in mates if p.position == "RB"], 1)
        ]
    else:  # his first teammate (none needed when one is already held)
        mate_cases = list(back_cases(mates, 0 if held_mates else 1))
    for chosen, free_mates in mate_cases:
        if len(chosen) > room:
            continue  # a fourth teammate: not a stack
        for taken, free in back_cases(backs, need):
            yield (*chosen, *taken), [*others, *free_mates, *free]


def marked_cases(
    new: tuple[SwapPlayer, ...],
    pool: list[SwapPlayer],
    held: list[SwapPlayer],
    rules: LeafRules | None,
) -> Iterator[tuple[tuple[SwapPlayer, ...], list[SwapPlayer]]]:
    """`(new, pool)` split by "at least one of `rules.need_one_of`" when few of them are left to choose from
    (`SCARCE`): one case per marked player, him fixed and the marked players before him out."""
    marked = rules.need_one_of if rules is not None else None
    if marked is None or any(p.name in marked for p in (*held, *new)):
        yield new, pool
        return
    left = [p for p in pool if p.name in marked]
    if len(left) > SCARCE:
        yield new, pool
        return
    for j, p in enumerate(left):
        gone = {q.name for q in left[: j + 1]}
        yield (*new, p), [q for q in pool if q.name not in gone]


class _FlooredSearch(_Search):
    """`_Search` that starts with a score to beat: a lineup at or under `floor` is not kept, and the bound
    prunes against it from the first node (the plain search only learns its floor once it holds `k`)."""

    def __init__(self, *args: Any, floor: float, **kwargs: Any):
        super().__init__(*args, **kwargs)
        self.floor = floor
        self.best_obj = floor

    def _offer(self, obj: float, chosen: list[SwapPlayer]) -> None:
        if obj > self.floor:
            super()._offer(obj, chosen)


def _within_game_limit(
    feasible: Callable[[list[SwapPlayer], list[str]], bool] | None, limit: int
) -> Callable[[list[SwapPlayer], list[str]], bool]:
    """`feasible` that also stops a branch already over `max from one game N` (players only ever add up)."""

    def test(placed: list[SwapPlayer], rest: list[str]) -> bool:
        return most_from_one_game(placed) <= limit and (feasible is None or feasible(placed, rest))

    return test


def exact_top_lineups(
    state: LineupState,
    candidates: list[SwapPlayer],
    *,
    k: int,
    leaf_filter: Callable[[list[SwapPlayer]], bool] | None = None,
    feasible: Callable[[list[SwapPlayer], list[str]], bool] | None = None,
    shape: str | None = None,
    qb: SwapPlayer | None = None,
    rules: LeafRules | None = None,
    safety: float | None = SAFETY_SECONDS,
) -> tuple[list[dict[int, SwapPlayer]], bool]:
    """The best `k` lineups over the open slots, searched to the end (module docstring). Without a `shape`
    (the cash search) it is the old search with no budget, lineup for lineup; with one, the quarterback's
    cases, best first by score and then by names."""
    from dfs import lineup_shapes as sh

    if not state.open:
        return [], True
    held = list(state.fixed.values())
    if rules is not None and rules.max_per_game is not None:
        feasible = _within_game_limit(feasible, rules.max_per_game)
    whole = len(state.fixed) + len(state.open) == len(ROSTER_SLOTS)
    if shape is None or qb is None or not whole or not any(p.name == qb.name for p in held):
        return budgeted_top_lineups(
            state, candidates, k=k, leaf_filter=leaf_filter, feasible=feasible, time_limit=safety
        )
    deadline = None if safety is None else clock.now() + safety
    best: list[tuple[float, tuple[str, ...], list[SwapPlayer]]] = []  # (score, names, players), best first
    complete = True
    for stacked, stack_pool in stack_cases(held, candidates, qb, shape, rules):
        for new, pool in marked_cases(stacked, stack_pool, held, rules):
            fixed = [*held, *new]
            if any(conflicts(p, [o for o in fixed if o is not p]) for p in new):
                continue
            seated = sh.seat(fixed)
            if seated is None or sum(p.salary for p in fixed) > state.cap:
                continue
            sub = LineupState(
                cap=state.cap,
                fixed=seated,
                open=[OpenSlot(i, slot, None) for i, slot in enumerate(ROSTER_SLOTS) if i not in seated],
            )
            if feasible is not None and not feasible(fixed, [s.slot for s in sub.open]):
                continue
            if not sub.open:  # the case fixed all nine
                found = [[]] if leaf_filter is None or leaf_filter(fixed) else []
            else:
                left = None if deadline is None else max(0.0, deadline - clock.now())
                # nothing at or under the k-th best so far matters to this case
                floor = best[k - 1][0] - score(new) - TIE if len(best) >= k else float("-inf")
                search = _FlooredSearch(
                    sub, pool, left, k=k, leaf_filter=leaf_filter, feasible=feasible, floor=floor
                )
                search.run()
                complete = complete and not search.timed_out
                found = [players for _, players in search.top()]
            for players in found:
                chosen = [*new, *players]
                best.append((round(score(chosen), 9), tuple(sorted(p.name for p in chosen)), chosen))
            best.sort(key=lambda entry: (-entry[0], entry[1]))
            del best[k:]
    return [_assign_to_slots(state, players) for _, _, players in best], complete
