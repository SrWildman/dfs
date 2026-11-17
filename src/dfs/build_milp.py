"""The builder's search as a mixed-integer program: the independent checker of `build_exact`.

`top_lineups` answers the same question as `late_swap_search.top_lineups` (the best `k` lineups over a
lineup's open slots by the projection, the cheaper lineup among exact ties) with HiGHS, through
`scipy.optimize.milp` as `sim/field_gen.chalk_lineups` already does. One 0/1 variable per candidate; every
rule the search enforces is a linear constraint; after each solve the lineup found is excluded by a no-good
cut ("not all of these again") and the next solve gives the next best.

**Why it is the checker and not the builder.** It shares no code with the branch-and-bound, so when the two
agree the answer does not rest on either one's search. But HiGHS needs 70-400 ms for each lineup of a real
week (measured, Week 9; it grows with the cuts): 15-20 minutes for a week's 1,000-1,300 candidates, where
`build_exact` takes seconds. `[builds] solver = "milp"` runs it all the same; `scripts/exact_equivalence.py`
and `tests/test_build_exact.py` are what it is for.

**The rules, as rows** (`x` = 1 when the player is in; a fixed player counts as a constant):

- roster: each open position filled exactly, RB / WR / TE each between their own open slots and one more
  when FLEX is open, and together their slots plus FLEX; a position with no open slot is out;
- salary: the open slots' salaries within the cap less the fixed players';
- nobody twice (a name that appears twice among the candidates is one player);
- no DST against his own QB (`QBs of team t + DSTs facing t <= 1`), at most one RB per game. As in the
  search, a pair that is BOTH fixed is not re-judged, and a candidate that clashes with a fixed player is out;
- the pool, Always use and Never use are the candidate list and the fixed players themselves;
- a stack (`shape`, around the fixed `qb`): one to three of his teammates at RB / WR / TE; `stack` at least
  one RB / WR / TE of the other team, `naked` none, `game` at least two, `rb_stack` a RB among the
  teammates, `rb_dst` a RB with his own DST (one helper variable per DST: "this DST and a RB of his team"),
  and with `own` / `max_stack_own` (the contrarian cut) the summed ownership of the QB, his teammates and the
  bring-backs within the cut;
- the whole-lineup rules (`build_exact.LeafRules`): at least one of a set of names (never nine Cash-marked
  players), at most N from one game, and the rules box's stack rules. Those say "IF the lineup is a stack of
  team T" (its QB and one to three teammates), so each affected QB gets two helper variables, "no teammates"
  and "four or more", and the rule binds only when he is in and neither is on: `no stacks` forbids that,
  `no bring-back` then allows none of the other team's offense, and `stacks must be QB+RB+WR or ...` picks
  one of the allowed make-ups by a helper variable each.

Exposure caps and "differs from the other builds by N players" are not here: they were never the search's
rules. `lineup_suggest.choose` applies them when it picks builds from the simulated candidates, whichever
solver found the candidates.

**Exact, not approximately.** Every lineup HiGHS returns is checked with the search's own Python tests (the
cap, the pair rules, `leaf_filter`, `feasible`) before it counts; one that fails (a float tolerance on the
ownership cut, or a rule of a `leaf_filter` nobody described) is cut and does not count. The rows are never
stricter than the tests (`OWN_SLACK`), so nothing legal is lost. The objective is scaled (`SCALE`) so the
solver's absolute gap, 1e-6, is 1e-9 points, below the cheaper-lineup nudge.

No sheet, no network, no randomness.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Mapping, Sequence

import numpy as np

from dfs.build_exact import LeafRules, score
from dfs.late_swap_search import (
    _SALARY_TIEBREAK,
    FLEX_POSITIONS,
    LineupState,
    SwapPlayer,
    _assign_to_slots,
    conflicts,
)

SCALE = 1000.0  # the objective handed to HiGHS is the score times this (module docstring)
OWN_SLACK = 1e-9  # the ownership cut's row is this much looser than the search's test, which then decides
BIG = 9.0  # more players than a lineup holds: switches a row off
MAX_REJECTED = 500  # lineups HiGHS may return that fail the search's own tests before the run gives up
POSITIONS = ("QB", "RB", "WR", "TE", "DST")
MATE_ORDER = ("RB", "WR", "TE")  # `lineup_shapes.SKILL`, the order a stack's make-up is written in


def composition_label(rb: int, wr: int, te: int) -> str:
    """ "QB+RB+2WR" for that many teammates by position: `lineup_shapes.composition`'s spelling."""
    counts = dict(zip(MATE_ORDER, (rb, wr, te), strict=True))
    return "QB" + "".join(
        f"+{counts[pos] if counts[pos] > 1 else ''}{pos}" for pos in MATE_ORDER if counts[pos]
    )


class Infeasible(Exception):
    """A row that no choice of players can satisfy (found while the model is built)."""


class Expr:
    """A linear expression over the model's variables plus a constant (what the fixed players add)."""

    def __init__(self) -> None:
        self.terms: dict[int, float] = {}
        self.const = 0.0

    def add(self, var: int | None, coef: float = 1.0) -> Expr:
        """`coef` times variable `var`; `var` None is a fixed player, so the term is a constant."""
        if var is None:
            self.const += coef
        else:
            self.terms[var] = self.terms.get(var, 0.0) + coef
        return self


class Model:
    """Variables, rows and bounds of one search. The first `len(players)` variables are the candidates."""

    def __init__(self, players: list[SwapPlayer]):
        self.players = players
        self.n = len(players)
        self.upper = [1.0] * self.n
        self.rows: list[tuple[dict[int, float], float, float]] = []

    def helper(self) -> int:
        """A new 0/1 variable that is not a player."""
        self.upper.append(1.0)
        return len(self.upper) - 1

    def out(self, var: int) -> None:
        self.upper[var] = 0.0

    def row(self, expr: Expr, lo: float = -np.inf, hi: float = np.inf) -> None:
        """`lo <= expr <= hi`. A row with no variable left is checked here and now."""
        lo, hi = lo - expr.const, hi - expr.const
        terms = {v: c for v, c in expr.terms.items() if c != 0.0}
        if not terms:
            if lo > 1e-9 or hi < -1e-9:
                raise Infeasible
            return
        self.rows.append((terms, lo, hi))

    def count(self, group: Sequence[int], const: float = 0.0) -> Expr:
        e = Expr()
        e.const = const
        for var in group:
            e.add(var)
        return e

    def solve(self) -> np.ndarray | None:
        """The best choice under the rows so far (True per variable), or None when there is none."""
        from scipy.optimize import Bounds, LinearConstraint, milp

        size = len(self.upper)
        a = np.zeros((len(self.rows), size))
        for r, (terms, _, _) in enumerate(self.rows):
            for var, coef in terms.items():
                a[r, var] = coef
        cost = np.zeros(size)
        cost[: self.n] = [-SCALE * (p.value - _SALARY_TIEBREAK * p.salary / 100) for p in self.players]
        result = milp(
            cost,
            constraints=LinearConstraint(a, [lo for _, lo, _ in self.rows], [hi for _, _, hi in self.rows]),
            integrality=np.ones(size),
            bounds=Bounds(np.zeros(size), np.array(self.upper)),
            options={"mip_rel_gap": 0.0},
        )
        if result.x is None:
            if result.status != 2:  # 2: infeasible. Anything else is a failed solve, not "no lineup"
                raise RuntimeError(f"HiGHS did not solve the lineup model: {result.message}")
            return None
        return result.x > 0.5


def build_model(
    state: LineupState,
    candidates: Sequence[SwapPlayer],
    *,
    shape: str | None = None,
    qb: SwapPlayer | None = None,
    own: Mapping[str, float] | None = None,
    max_stack_own: float | None = None,
    rules: LeafRules | None = None,
) -> Model | None:
    """The search over `state`'s open slots as a `Model` (module docstring), or None when a rule already
    shows there is no lineup."""
    held = list(state.fixed.values())
    held_names = {p.name for p in held}
    slots = [s.slot for s in state.open]
    need = {pos: slots.count(pos) for pos in POSITIONS}
    flex = slots.count("FLEX")
    players = [
        p
        for p in candidates
        if p.name not in held_names and (need.get(p.position, 0) or (flex and p.position in FLEX_POSITIONS))
    ]
    m = Model(players)
    index = range(m.n)

    def where(test: Callable[[SwapPlayer], bool]) -> list[int]:
        return [i for i in index if test(players[i])]

    def held_count(test: Callable[[SwapPlayer], bool]) -> int:
        return sum(1 for p in held if test(p))

    try:
        # roster
        for pos in POSITIONS:
            group = where(lambda p, pos=pos: p.position == pos)
            extra = flex if pos in FLEX_POSITIONS else 0
            m.row(m.count(group), need[pos], need[pos] + extra)
        if flex:
            eligible = where(lambda p: p.position in FLEX_POSITIONS)
            total = sum(need[pos] for pos in FLEX_POSITIONS) + flex
            m.row(m.count(eligible), total, total)
        # salary (in hundreds, so the row's numbers stay small)
        spend = Expr()
        for i in index:
            spend.add(i, players[i].salary / 100)
        m.row(spend, hi=(state.cap - state.fixed_salary) / 100)
        # nobody twice
        for name, times in Counter(p.name for p in players).items():
            if times > 1:
                m.row(m.count(where(lambda p, name=name: p.name == name)), hi=1)
        # a DST against his own QB; two RBs in one game. A candidate that clashes with a fixed player is out.
        for i in index:
            if conflicts(players[i], held):
                m.out(i)
        for team in {p.team for p in players if p.position == "QB"}:
            pair = where(
                lambda p, team=team: (
                    (p.position == "QB" and p.team == team) or (p.position == "DST" and p.opp == team)
                )
            )
            m.row(m.count(pair), hi=1)
        for game in {p.game for p in players if p.position == "RB" and p.game}:
            m.row(m.count(where(lambda p, game=game: p.position == "RB" and p.game == game)), hi=1)
        if shape is not None:
            _shape_rows(m, held, shape, qb, own, max_stack_own)
        if rules is not None:
            _rule_rows(m, held, rules)
    except Infeasible:
        return None
    return m


def _mates_and_backs(
    m: Model, held: list[SwapPlayer], team: str, opp: str
) -> tuple[list[int], list[SwapPlayer], list[int], list[SwapPlayer]]:
    """(candidate teammates, fixed teammates, candidate bring-backs, fixed bring-backs) of a QB of `team`
    facing `opp`: RB / WR / TE only, as `lineup_shapes.teammates` / `bring_backs`."""

    def mate(p: SwapPlayer) -> bool:
        return p.team == team and p.position in MATE_ORDER

    def back(p: SwapPlayer) -> bool:
        return p.team == opp and p.position in MATE_ORDER

    return (
        [i for i in range(m.n) if mate(m.players[i])],
        [p for p in held if mate(p)],
        [i for i in range(m.n) if back(m.players[i])],
        [p for p in held if back(p)],
    )


def _shape_rows(
    m: Model,
    held: list[SwapPlayer],
    shape: str,
    qb: SwapPlayer | None,
    own: Mapping[str, float] | None,
    max_stack_own: float | None,
) -> None:
    from dfs import lineup_shapes as sh

    if qb is None or not any(p.name == qb.name for p in held):
        raise ValueError("a shape is searched around a fixed quarterback")
    mates, held_mates, backs, held_backs = _mates_and_backs(m, held, qb.team, qb.opp)
    m.row(m.count(mates, len(held_mates)), 1, sh.MAX_MATES)  # a stack: one to three teammates
    if shape == sh.STACK:
        m.row(m.count(backs, len(held_backs)), lo=1)
    elif shape == sh.NAKED:
        m.row(m.count(backs, len(held_backs)), hi=0)
    elif shape == sh.GAME:
        m.row(m.count(backs, len(held_backs)), lo=2)
    elif shape == sh.RB_STACK:
        rbs = [i for i in mates if m.players[i].position == "RB"]
        m.row(m.count(rbs, sum(1 for p in held_mates if p.position == "RB")), lo=1)
    elif shape == sh.RB_DST:
        _rb_dst_rows(m, held)
    elif shape != sh.CONTRARIAN:
        raise ValueError(f"unknown shape {shape!r}")
    if own is not None and max_stack_own is not None:
        core = Expr()
        for p in (qb, *held_mates, *held_backs):
            core.add(None, max(sh.OWN_FLOOR, own.get(p.name, 0.0)))
        for i in (*mates, *backs):
            core.add(i, max(sh.OWN_FLOOR, own.get(m.players[i].name, 0.0)))
        m.row(core, hi=max_stack_own + OWN_SLACK)


def _rb_dst_rows(m: Model, held: list[SwapPlayer]) -> None:
    """A RB with his own team's DST (`lineup_shapes.has_rb_dst`): for each DST that can be in the lineup, a
    helper "he is in AND a RB of his team is"; at least one helper is on."""

    def rbs_of(team: str) -> Expr:
        group = [i for i in range(m.n) if m.players[i].position == "RB" and m.players[i].team == team]
        return m.count(group, sum(1 for p in held if p.position == "RB" and p.team == team))

    pairs = Expr()
    for dst in (p for p in held if p.position == "DST"):
        backs = rbs_of(dst.team)
        if backs.const >= 1:
            return  # a fixed DST with a fixed RB of his team: the rule already holds
        pair = m.helper()
        m.row(backs.add(pair, -1.0), lo=0)  # pair <= RBs of his team
        pairs.add(pair)
    for i in range(m.n):
        if m.players[i].position != "DST":
            continue
        pair = m.helper()
        m.row(Expr().add(pair).add(i, -1.0), hi=0)  # pair <= the DST
        m.row(rbs_of(m.players[i].team).add(pair, -1.0), lo=0)  # pair <= RBs of his team
        pairs.add(pair)
    m.row(pairs, lo=1)


def _rule_rows(m: Model, held: list[SwapPlayer], rules: LeafRules) -> None:
    if rules.need_one_of is not None:
        marked = [i for i in range(m.n) if m.players[i].name in rules.need_one_of]
        m.row(m.count(marked, sum(1 for p in held if p.name in rules.need_one_of)), lo=1)
    if rules.max_per_game is not None:
        fixed = Counter(p.game for p in held if p.game)
        for game in {p.game for p in m.players if p.game} | set(fixed):
            group = [i for i in range(m.n) if m.players[i].game == game]
            m.row(m.count(group, fixed[game]), hi=rules.max_per_game)
    stacks = rules.stacks
    if not stacks:
        return
    teams = (
        set(stacks.no_stack)
        | set(stacks.no_bring_back)
        | {t for t, allowed in stacks.only.items() if allowed}
    )
    quarterbacks: list[tuple[int | None, SwapPlayer]] = [(None, p) for p in held if p.position == "QB"]
    quarterbacks += [(i, m.players[i]) for i in range(m.n) if m.players[i].position == "QB"]
    for var, qb in quarterbacks:
        if qb.team in teams:
            _stack_rule_rows(m, held, stacks, var, qb)


def _stack_rule_rows(m: Model, held: list[SwapPlayer], stacks, var: int | None, qb: SwapPlayer) -> None:  # noqa: ANN001
    """The rules box's stack rules for one QB (`suggest_state.StackRules.violation`). They bind only when the
    lineup is a stack of his team: he is in (`var`; None: he is fixed) with one to three teammates. Helpers
    `none` (no teammate) and `many` (four or more) say when it is not one: stacked = QB - none - many."""
    mates, held_mates, backs, held_backs = _mates_and_backs(m, held, qb.team, qb.opp)
    none, many = m.helper(), m.helper()

    def mate_count() -> Expr:
        return m.count(mates, len(held_mates))

    def stacked(coef: float, into: Expr) -> Expr:
        return into.add(var, coef).add(none, -coef).add(many, -coef)

    m.row(Expr().add(none).add(many).add(var, -1.0), hi=0)  # only a QB who is in has either helper on
    m.row(mate_count().add(none, BIG), hi=BIG)  # none: no teammates
    m.row(mate_count().add(many, -4.0), lo=0)  # many: four or more
    m.row(stacked(-1.0, mate_count()), lo=0)  # stacked: at least one teammate
    m.row(mate_count().add(many, -5.0).add(var, 8.0), hi=11)  # stacked: at most three
    allowed = stacks.only.get(qb.team)
    labels = (
        []
        if not allowed
        else [
            (rb, wr, te)
            for rb in range(4)
            for wr in range(4)
            for te in range(4)
            if 1 <= rb + wr + te <= 3 and composition_label(rb, wr, te) in allowed
        ]
    )
    if qb.team in stacks.no_stack or (allowed and not labels):
        m.row(
            stacked(1.0, Expr()), hi=0
        )  # never a stack of this team (or none of the allowed make-ups exists)
        return
    if qb.team in stacks.no_bring_back:
        m.row(stacked(BIG, m.count(backs, len(held_backs))), hi=BIG)  # stacked: no bring-back
    if allowed:
        picks = [m.helper() for _ in labels]
        chosen = Expr()
        for pick in picks:
            chosen.add(pick)
        m.row(stacked(-1.0, chosen), 0, 0)  # stacked: exactly one allowed make-up
        for n, pos in enumerate(MATE_ORDER):
            group = [i for i in mates if m.players[i].position == pos]
            fixed = sum(1 for p in held_mates if p.position == pos)

            def gap(group: list[int] = group, fixed: int = fixed, n: int = n) -> Expr:
                e = m.count(group, fixed)  # his teammates at this position, less what the make-up says
                for pick, label in zip(picks, labels, strict=True):
                    e.add(pick, -float(label[n]))
                return e

            m.row(stacked(BIG, gap()), hi=BIG)  # stacked: the two are equal
            m.row(stacked(-BIG, gap()), lo=-BIG)


def passes(
    state: LineupState,
    chosen: list[SwapPlayer],
    leaf_filter: Callable[[list[SwapPlayer]], bool] | None,
    feasible: Callable[[list[SwapPlayer], list[str]], bool] | None,
) -> bool:
    """The search's own tests on a lineup HiGHS returned: nobody twice, the cap, the pair rules for every
    player put in, then `leaf_filter` and `feasible` as the search applies them to a finished lineup."""
    placed = [*state.fixed.values(), *chosen]
    if len({p.name for p in placed}) != len(placed) or sum(p.salary for p in placed) > state.cap:
        return False
    if any(conflicts(p, [o for o in placed if o is not p]) for p in chosen):
        return False
    if leaf_filter is not None and not leaf_filter(placed):
        return False
    return feasible is None or feasible(placed, [])


def top_lineups(
    state: LineupState,
    candidates: Sequence[SwapPlayer],
    *,
    k: int,
    leaf_filter: Callable[[list[SwapPlayer]], bool] | None = None,
    feasible: Callable[[list[SwapPlayer], list[str]], bool] | None = None,
    shape: str | None = None,
    qb: SwapPlayer | None = None,
    own: Mapping[str, float] | None = None,
    max_stack_own: float | None = None,
    rules: LeafRules | None = None,
) -> tuple[list[dict[int, SwapPlayer]], bool]:
    """The best `k` lineups over `state`'s open slots, best first (by score, then names), each as `slot index
    -> player`, and whether the run finished: False only when HiGHS kept returning lineups the search's own
    tests reject (`MAX_REJECTED`; a `leaf_filter` whose rule nobody described in `rules`)."""
    if not state.open:
        return [], True
    model = build_model(
        state, candidates, shape=shape, qb=qb, own=own, max_stack_own=max_stack_own, rules=rules
    )
    if model is None:
        return [], True
    found: list[list[SwapPlayer]] = []
    rejected = 0
    complete = True
    while len(found) < k:
        picked = model.solve()
        if picked is None:
            break
        chosen_vars = [i for i in range(model.n) if picked[i]]
        chosen = [model.players[i] for i in chosen_vars]
        model.row(model.count(chosen_vars), hi=len(chosen_vars) - 1)  # the no-good cut: not these again
        if passes(state, chosen, leaf_filter, feasible):
            found.append(chosen)
        else:
            rejected += 1
            if rejected > MAX_REJECTED:
                complete = False
                break
    found.sort(key=lambda players: (-round(score(players), 9), tuple(sorted(p.name for p in players))))
    return [_assign_to_slots(state, players) for players in found], complete
