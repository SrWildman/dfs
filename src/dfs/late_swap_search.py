"""Pure search behind `dfs lineups late-swap`: given a lineup's locked and open slots, find the swaps that
raise its projected points without breaking DraftKings' rules or Sam's own.

Three views of the same open slots, best first:

1. **Best full re-fill** (`best_refill`): the best combination for ALL open slots together.
2. **Best 2-for-2 swaps** (`two_for_two`): two open slots change at once; this is what finds "expensive RB out
   + cheap TE out, mid RB in + better TE in" when no single swap does.
3. **Best 1-for-1 swaps** (`one_for_one`): one open slot changes, salary-checked.

Ranked by a metric (`ProjPts` by default, `AggPts` on request) -- never Leverage. Only improvements are
listed.

**Constraints, all enforced on the whole lineup** (locked players included):

- total salary <= the cap, counting the locked players' salaries;
- slot positions, with FLEX taking an RB, WR or TE; nobody twice;
- locked players never move (a player whose lock status can't be read counts as locked);
- no DST against your own QB (the DST's opponent is the QB's team) and at most one RB per game -- the same
  two rules as the sheet's `Issues` guardrails (`sheet_style._stack_check_formula`), checked for every pair
  that involves a player this search puts in. A pair who are both already in the lineup and untouched is not
  re-judged: the sheet's `Issues` reports it, and it is no reason to refuse every swap.

**Why branch-and-bound, not a solver library.** At most nine open slots over a rosterable pool of ~250. The
search bounds each branch with a Lagrangian relaxation of the salary cap (`_Bound`): for any price per salary
dollar `lam`, no lineup can score more than `lam * budget + the best sum of (value - lam * salary)`, ignoring
the no-duplicates and pairwise rules (which only ever remove options). A good `lam` makes that bound tight, so
almost every branch is cut. `tests/test_late_swap_search.py` pins the time budget (9 open slots, full pool,
under 2 s) and checks the search against brute force.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime

import pandas as pd

from dfs.derived import _rosterable_pool_mask
from dfs.kickoff import parse_kickoff
from dfs.models import FLEX_ELIGIBLE, ROSTER_SLOTS

METRICS = ("ProjPts", "AggPts")
DEFAULT_METRIC = "ProjPts"
# A player who is not going to play can't be a swap target. `Q` stays: it is a coin flip, and the Avail
# column is shown next to the name so the call is Sam's.
NOT_PLAYING = frozenset({"OUT", "IR"})
FLEX_POSITIONS = frozenset(p.value for p in FLEX_ELIGIBLE)
# Slots of one position are interchangeable, so the search fills them in a fixed order (see `_Search`).
_STAGE_ORDER = ("DST", "QB", "TE", "RB", "WR", "FLEX")
_GAIN_EPSILON = 1e-9  # a swap must beat the current lineup by more than float noise
# Prefer the cheaper lineup among exact ties (ProjPts has one decimal, so ties are common); the nudge is far
# below the smallest real difference (0.01 pt) over any lineup's total salary.
_SALARY_TIEBREAK = 1e-7


@dataclass(frozen=True)
class SwapPlayer:
    name: str
    position: str
    team: str
    opp: str
    game: str
    salary: int
    value: float  # the ranking metric
    avail: str = ""


@dataclass
class OpenSlot:
    index: int  # position in ROSTER_SLOTS
    slot: str
    current: SwapPlayer | None  # None: the slot is empty (or holds a name edge cannot find)


@dataclass
class LineupState:
    """One lineup at a point in time: who is locked, who can still move, and the salary cap."""

    cap: int
    fixed: dict[int, SwapPlayer]  # ROSTER_SLOTS index -> locked player
    open: list[OpenSlot]

    @property
    def fixed_salary(self) -> int:
        return sum(p.salary for p in self.fixed.values())

    def current_players(self) -> list[SwapPlayer]:
        return [*self.fixed.values(), *(s.current for s in self.open if s.current)]

    @property
    def current_value(self) -> float:
        """Metric total over the open slots' current players (the locked part is common to every option)."""
        return sum(s.current.value for s in self.open if s.current)

    @property
    def salary_left(self) -> int:
        return self.cap - sum(p.salary for p in self.current_players())


@dataclass
class Swap:
    """Some open slots changed: `changes` is `slot index -> (player out or None, player in)`."""

    changes: dict[int, tuple[SwapPlayer | None, SwapPlayer]]
    delta: float  # metric gained over the current lineup
    salary_left: int
    notes: list[str] = field(default_factory=list)
    exact: bool = True  # False: a time limit cut the search short, so a better lineup may exist


# ---------------------------------------------------------------------------------------------
# Building the inputs from EdgeRaw
# ---------------------------------------------------------------------------------------------


def _player(row: pd.Series, metric: str) -> SwapPlayer | None:
    """One EdgeRaw row as a `SwapPlayer`, or None when its salary or metric is missing."""
    salary = pd.to_numeric(row.get("Salary"), errors="coerce")
    value = pd.to_numeric(row.get(metric), errors="coerce")
    if pd.isna(salary) or pd.isna(value):
        return None
    game = row.get("GameID")
    avail = row.get("Avail")
    return SwapPlayer(
        name=str(row["Name"]),
        position=str(row["Position"]),
        team=str(row.get("Team") or ""),
        opp=str(row.get("Opp") or ""),
        game=str(game) if pd.notna(game) else "",
        salary=int(salary),
        value=float(value),
        avail=str(avail) if pd.notna(avail) else "",
    )


def lineup_state(
    statuses: list, edge: pd.DataFrame, *, cap: int, metric: str = DEFAULT_METRIC
) -> LineupState:
    """Split a lineup's `SlotStatus` list into locked and open slots.

    A slot is OPEN when its player is found and his game has not started, or when it is empty / holds a name
    EdgeRaw cannot find (nothing to lose by filling it). Everything else is locked: a started game, and also a
    player whose kickoff can't be read (better to leave him alone than to guess)."""
    by_name = edge.drop_duplicates("Name").set_index("Name", drop=False)
    fixed: dict[int, SwapPlayer] = {}
    open_slots: list[OpenSlot] = []
    for index, status in enumerate(statuses):
        player = _player(by_name.loc[status.name], metric) if status.found else None
        if status.found and status.locked is not False and player is not None:
            fixed[index] = player
        elif status.found and status.locked is False and player is not None:
            open_slots.append(OpenSlot(index, ROSTER_SLOTS[index], player))
        elif status.found and status.locked is not False:
            # Locked (or unreadable) but with no salary/metric on file: it still holds the slot.
            fixed[index] = SwapPlayer(
                status.name, str(status.position or ""), str(status.team or ""), "", "", 0, 0.0
            )
        else:
            open_slots.append(OpenSlot(index, ROSTER_SLOTS[index], None))
    return LineupState(cap=cap, fixed=fixed, open=open_slots)


def swap_pool(
    edge: pd.DataFrame,
    *,
    now: datetime,
    metric: str = DEFAULT_METRIC,
    pool_names: set[str] | None = None,
    all_players: bool = False,
) -> list[SwapPlayer]:
    """Players who can still be swapped IN: game not started (a kickoff that can't be read counts as not
    swappable), not ruled out, and in Sam's Player Pool -- or, with `all_players`, also in the rosterable pool
    (`derived._rosterable_pool_mask`: the top players by ProjPts per position)."""
    frame = edge.reset_index(drop=True)
    kickoff = frame["GameStart"].apply(parse_kickoff)
    open_game = kickoff.notna() & kickoff.apply(lambda k: k is not None and k > now)
    avail = frame["Avail"] if "Avail" in frame else pd.Series("", index=frame.index)
    playing = ~avail.fillna("").astype(str).isin(NOT_PLAYING)
    wanted = frame["Name"].isin(pool_names or set())
    if all_players:
        wanted = wanted | _rosterable_pool_mask(frame["ProjPts"], frame["Position"])
    players = (_player(row, metric) for _, row in frame[open_game & playing & wanted].iterrows())
    return [p for p in players if p is not None]


# ---------------------------------------------------------------------------------------------
# Rules
# ---------------------------------------------------------------------------------------------


def _fits(slot: str, player: SwapPlayer) -> bool:
    return player.position in FLEX_POSITIONS if slot == "FLEX" else player.position == slot


def conflicts(player: SwapPlayer, placed: list[SwapPlayer]) -> bool:
    """True when putting `player` next to `placed` breaks a hard rule: a DST against your own QB, or a second
    RB in one game (a FLEX RB counts: `position` is the player's real one, not the slot's)."""
    if player.position == "DST":
        return any(o.position == "QB" and o.team == player.opp for o in placed)
    if player.position == "QB":
        return any(o.position == "DST" and o.opp == player.team for o in placed)
    if player.position == "RB" and player.game:
        return any(o.position == "RB" and o.game == player.game for o in placed)
    return False


# ---------------------------------------------------------------------------------------------
# 1. Best full re-fill: branch-and-bound
# ---------------------------------------------------------------------------------------------


class _Bound:
    """The Lagrangian bound for one candidate list at one price `lam` per salary dollar.

    For the stages still to fill it returns the best possible sum of `value - lam * salary` -- the top `n`
    unused players of each position plus the best leftover for FLEX -- ignoring everything except "not
    already used" and the same-position ordering. Any real completion scores at most
    `lam * budget_left + that sum`."""

    def __init__(self, by_position: dict[str, list[SwapPlayer]], lam: float):
        self.lam = lam
        # (adjusted value, index in the position's value-sorted list), best first.
        self.ranked = {
            pos: sorted(
                (
                    (p.value - _SALARY_TIEBREAK * p.salary / 100 - lam * p.salary, i)
                    for i, p in enumerate(players)
                ),
                reverse=True,
            )
            for pos, players in by_position.items()
        }

    def best_sum(
        self,
        need: dict[str, int],
        flex: bool,
        used: set[str],
        floor: dict[str, int],
        by_position: dict[str, list[SwapPlayer]],
    ) -> float:
        total = 0.0
        leftovers: list[float] = []
        for pos, count in need.items():
            taken = 0
            for adj, i in self.ranked.get(pos, ()):
                if i <= floor.get(pos, -1) or by_position[pos][i].name in used:
                    continue
                if taken < count:
                    total += adj
                    taken += 1
                elif pos in FLEX_POSITIONS:
                    leftovers.append(adj)
                    break
            if taken < count:
                return float("-inf")  # not enough players of this position left
        if flex:
            # FLEX also draws on positions with no open slot of their own.
            for pos in FLEX_POSITIONS - need.keys():
                for adj, i in self.ranked.get(pos, ()):
                    if i > floor.get(pos, -1) and by_position[pos][i].name not in used:
                        leftovers.append(adj)
                        break
            if not leftovers:
                return float("-inf")
            total += max(leftovers)
        return total


class _Search:
    """Depth-first branch-and-bound over the open slots, in a fixed order, canonical within a position."""

    def __init__(self, state: LineupState, candidates: list[SwapPlayer], time_limit: float | None):
        self.state = state
        self.by_position: dict[str, list[SwapPlayer]] = {}
        for p in sorted(candidates, key=lambda p: (-p.value, p.salary, p.name)):
            self.by_position.setdefault(p.position, []).append(p)
        slots = [s.slot for s in state.open]
        # Stages: the open slots in `_STAGE_ORDER`. Same-position slots are interchangeable, so each stage may
        # only pick a player further down the position's list than the previous stage of that position (and
        # FLEX further than every regular slot of the position it ends up taking): every SET of players is
        # visited once, not once per ordering.
        self.stages = [slot for pos in _STAGE_ORDER for slot in slots if slot == pos]
        self.need = {pos: slots.count(pos) for pos in ("QB", "RB", "WR", "TE", "DST") if slots.count(pos)}
        self.flex = "FLEX" in slots
        self.deadline = None if time_limit is None else time.monotonic() + time_limit
        self.timed_out = False
        self.budget = state.cap - state.fixed_salary
        self.best_obj = float("-inf")
        self.best: list[SwapPlayer] | None = None
        self.nodes = 0
        self.bound = _Bound(self.by_position, self._best_lambda())

    def _best_lambda(self) -> float:
        """The price per salary dollar that gives the tightest root bound (a convex function of the price)."""
        ratios = [p.value / p.salary for ps in self.by_position.values() for p in ps if p.salary > 0]
        if not ratios:
            return 0.0
        lo, hi = 0.0, 2 * max(ratios)
        for _ in range(48):
            m1, m2 = lo + (hi - lo) / 3, hi - (hi - lo) / 3
            if self._root_bound(m1) <= self._root_bound(m2):
                hi = m2
            else:
                lo = m1
        return (lo + hi) / 2

    def _root_bound(self, lam: float) -> float:
        bound = _Bound(self.by_position, lam)
        used = {p.name for p in self.state.fixed.values()}
        return lam * self.budget + bound.best_sum(self.need, self.flex, used, {}, self.by_position)

    def run(self) -> None:
        used = {p.name for p in self.state.fixed.values()}
        self._descend(0, [*self.state.fixed.values()], used, self.budget, 0.0, [], {})

    def _descend(self, depth, placed, used, budget, obj, chosen, floor) -> None:
        self.nodes += 1
        if self.deadline is not None and self.nodes % 512 == 0 and time.monotonic() > self.deadline:
            self.timed_out = True
        if self.timed_out:
            return
        if depth == len(self.stages):
            if obj > self.best_obj:
                self.best_obj, self.best = obj, list(chosen)
            return
        slot = self.stages[depth]
        positions = sorted(FLEX_POSITIONS) if slot == "FLEX" else [slot]
        for pos in positions:
            players = self.by_position.get(pos, [])
            for i in range(floor.get(pos, -1) + 1, len(players)):
                player = players[i]
                if player.name in used or player.salary > budget or conflicts(player, placed):
                    continue
                new_obj = obj + player.value - _SALARY_TIEBREAK * player.salary / 100
                new_budget = budget - player.salary
                # FLEX goes last, so a regular slot's floor never needs the FLEX pick.
                new_floor = floor if slot == "FLEX" else {**floor, pos: i}
                rest = self.stages[depth + 1 :]
                rest_need = {p: rest.count(p) for p in self.need if rest.count(p)}
                bound = (
                    new_obj
                    + self.bound.lam * new_budget
                    + self.bound.best_sum(
                        rest_need, "FLEX" in rest, used | {player.name}, new_floor, self.by_position
                    )
                )
                if bound <= self.best_obj:
                    # Players are visited best-first, so a later one in this list scores less -- but it may be
                    # cheaper, and the bound already prices salary, so keep scanning rather than stopping.
                    continue
                chosen.append(player)
                placed.append(player)
                used.add(player.name)
                self._descend(depth + 1, placed, used, new_budget, new_obj, chosen, new_floor)
                used.discard(player.name)
                placed.pop()
                chosen.pop()


def best_refill(
    state: LineupState, candidates: list[SwapPlayer], *, time_limit: float | None = None
) -> Swap | None:
    """The best lineup over ALL open slots, as a `Swap` against the current lineup; None when the open slots
    cannot be filled at all. The current open players should be in `candidates` (they may stay).

    Returns the best lineup even when it is no better than the current one (`delta <= 0`): the caller decides
    whether to show it."""
    if not state.open:
        return None
    search = _Search(state, candidates, time_limit)
    search.run()
    if search.best is None:
        return None
    return _swap_from_lineup(state, _assign_to_slots(state, search.best), exact=not search.timed_out)


def _assign_to_slots(state: LineupState, chosen: list[SwapPlayer]) -> dict[int, SwapPlayer]:
    """Put the chosen players into the open slots: a kept player stays in his own slot where he still fits,
    everyone else takes the remaining slots in order, FLEX last."""
    assignment: dict[int, SwapPlayer] = {}
    pending = list(chosen)
    for slot in state.open:
        if slot.current is not None and slot.current in pending and _fits(slot.slot, slot.current):
            assignment[slot.index] = slot.current
            pending.remove(slot.current)
    for slot in sorted((s for s in state.open if s.index not in assignment), key=lambda s: s.slot == "FLEX"):
        fit = next(p for p in pending if _fits(slot.slot, p))
        assignment[slot.index] = fit
        pending.remove(fit)
    return assignment


def _swap_from_lineup(state: LineupState, assignment: dict[int, SwapPlayer], *, exact: bool = True) -> Swap:
    changes = {
        slot.index: (slot.current, assignment[slot.index])
        for slot in state.open
        if assignment[slot.index] != slot.current
    }
    new_total = sum(p.value for p in assignment.values())
    new_salary = state.fixed_salary + sum(p.salary for p in assignment.values())
    return Swap(
        changes=changes,
        delta=new_total - state.current_value,
        salary_left=state.cap - new_salary,
        exact=exact,
    )


# ---------------------------------------------------------------------------------------------
# 2 and 3. One-for-one and two-for-two swaps
# ---------------------------------------------------------------------------------------------


def _options(state: LineupState, candidates: list[SwapPlayer]) -> dict[int, list[SwapPlayer]]:
    """For each open slot, the players who could go there instead of whoever is there now: the right position,
    not already in the lineup, best first."""
    taken = {p.name for p in state.current_players()}
    ranked = sorted(candidates, key=lambda p: (-p.value, p.salary, p.name))
    return {
        slot.index: [p for p in ranked if p.name not in taken and _fits(slot.slot, p)] for slot in state.open
    }


def one_for_one(state: LineupState, candidates: list[SwapPlayer], *, top: int = 3) -> dict[int, list[Swap]]:
    """Per open slot, the `top` best single swaps that fit the cap and break no rule, improvements only."""
    options = _options(state, candidates)
    current = state.current_players()
    result: dict[int, list[Swap]] = {}
    for slot in state.open:
        others = [p for p in current if p != slot.current]
        base_salary = sum(p.salary for p in others)
        old_value = slot.current.value if slot.current else 0.0
        found: list[Swap] = []
        for player in options[slot.index]:
            delta = player.value - old_value
            if delta <= _GAIN_EPSILON:
                break  # best-first: nobody further down improves the lineup
            if base_salary + player.salary > state.cap or conflicts(player, others):
                continue
            found.append(
                Swap({slot.index: (slot.current, player)}, delta, state.cap - base_salary - player.salary)
            )
            if len(found) == top:
                break
        if found:
            result[slot.index] = found
    return result


def two_for_two(state: LineupState, candidates: list[SwapPlayer], *, top: int = 3) -> list[Swap]:
    """The `top` best swaps that change exactly two open slots at once, improvements only. Swaps that bring
    the same players in and send the same players out (differing only in which of two interchangeable slots
    a player takes) count once."""
    options = _options(state, candidates)
    current = state.current_players()
    best: list[Swap] = []  # kept sorted best-first, at most `top` long
    seen: set[tuple[frozenset[str], frozenset[str]]] = set()
    slots = state.open
    for a in range(len(slots)):
        for b in range(a + 1, len(slots)):
            sa, sb = slots[a], slots[b]
            others = [p for p in current if p not in (sa.current, sb.current)]
            base_salary = sum(p.salary for p in others)
            old = (sa.current.value if sa.current else 0.0) + (sb.current.value if sb.current else 0.0)
            list_a, list_b = options[sa.index], options[sb.index]
            if not list_a or not list_b:
                continue
            for pa in list_a:
                # Everything further down both lists is worth less: stop once even the best partner can't
                # beat the worst swap already kept (or the current lineup).
                floor = best[-1].delta if len(best) == top else _GAIN_EPSILON
                if pa.value + list_b[0].value - old <= floor:
                    break
                if base_salary + pa.salary > state.cap or conflicts(pa, others):
                    continue
                for pb in list_b:
                    floor = best[-1].delta if len(best) == top else _GAIN_EPSILON
                    delta = pa.value + pb.value - old
                    if delta <= floor:
                        break
                    if pb.name == pa.name or base_salary + pa.salary + pb.salary > state.cap:
                        continue
                    if conflicts(pb, [*others, pa]):
                        continue
                    key = (
                        frozenset((pa.name, pb.name)),
                        frozenset(p.name for p in (sa.current, sb.current) if p),
                    )
                    if key in seen:
                        continue
                    seen.add(key)
                    best.append(
                        Swap(
                            {sa.index: (sa.current, pa), sb.index: (sb.current, pb)},
                            delta,
                            state.cap - base_salary - pa.salary - pb.salary,
                        )
                    )
                    best.sort(key=lambda sw: (-sw.delta, -sw.salary_left))
                    del best[top:]
    return best


# ---------------------------------------------------------------------------------------------
# Correlation notes
# ---------------------------------------------------------------------------------------------


def _shape(players: list[SwapPlayer]) -> dict[str, set[str]]:
    """The three correlation groups of a lineup, as sets of player names:

    - `stack`: the QB's non-DST teammates, plus his team's DST;
    - `bring-back`: non-DST players on the QB's opponent;
    - `DST pair`: non-DST players on the DST's own team, when that DST is on neither the QB's team nor his
      opponent (a DST on the QB's team is already in the stack).

    The same grouping as the Lineups tints (`sheet_lineup_tints`)."""
    qb = next((p for p in players if p.position == "QB"), None)
    dst = next((p for p in players if p.position == "DST"), None)
    stack: set[str] = set()
    bring_back: set[str] = set()
    pair: set[str] = set()
    if qb is not None:
        stack = {p.name for p in players if p.team == qb.team and p is not qb and p.position != "DST"}
        if stack and dst is not None and dst.team == qb.team:
            stack.add(dst.name)
        bring_back = {p.name for p in players if p.team == qb.opp and p.position != "DST"}
    if dst is not None and (qb is None or dst.team not in (qb.team, qb.opp)):
        pair = {p.name for p in players if p.team == dst.team and p.position != "DST"}
    return {"QB stack": stack, "bring-back": bring_back, "DST pair": pair}


def correlation_notes(before: list[SwapPlayer], after: list[SwapPlayer]) -> list[str]:
    """What a swap does to the lineup's stack, bring-back and DST/same-team pair, in words. Empty when it
    changes none of them."""
    old, new = _shape(before), _shape(after)
    notes = []
    for label in ("QB stack", "bring-back", "DST pair"):
        gone, came = old[label] - new[label], new[label] - old[label]
        if not gone and not came:
            continue
        if came and not old[label]:
            notes.append(f"adds a {label}: {', '.join(sorted(came))}")
        elif gone and not new[label]:
            notes.append(f"breaks the {label}: loses {', '.join(sorted(gone))}")
        elif came and gone:
            notes.append(f"changes the {label}: {', '.join(sorted(gone))} out, {', '.join(sorted(came))} in")
        elif came:
            notes.append(f"grows the {label}: adds {', '.join(sorted(came))}")
        else:
            notes.append(f"shrinks the {label}: loses {', '.join(sorted(gone))}")
    return notes


def swap_after(state: LineupState, swap: Swap) -> list[SwapPlayer]:
    """The whole lineup's players once `swap` is applied."""
    out = {o.name for o, _ in swap.changes.values() if o}
    return [p for p in state.current_players() if p.name not in out] + [i for _, i in swap.changes.values()]


def annotate(state: LineupState, swap: Swap) -> Swap:
    """Fill `swap.notes` with the correlation changes it causes."""
    swap.notes = correlation_notes(state.current_players(), swap_after(state, swap))
    return swap


# ---------------------------------------------------------------------------------------------
# Everything for one lineup
# ---------------------------------------------------------------------------------------------


@dataclass
class LineupSuggestions:
    state: LineupState
    refill: Swap | None  # None: no open slot, or the open slots cannot be filled
    pairs: list[Swap]
    singles: dict[int, list[Swap]]

    @property
    def nothing_better(self) -> bool:
        return not (self.refill and self.refill.delta > _GAIN_EPSILON) and not self.pairs and not self.singles


def suggest(
    state: LineupState,
    pool: list[SwapPlayer],
    *,
    top: int = 3,
    time_limit: float | None = None,
) -> LineupSuggestions:
    """Run all three searches for one lineup. The current open players join the candidate pool, so keeping
    them is always one of the options the re-fill weighs."""
    candidates = {p.name: p for p in pool}
    for slot in state.open:
        if slot.current is not None:
            candidates[slot.current.name] = slot.current
    everyone = list(candidates.values())
    refill = best_refill(state, everyone, time_limit=time_limit)
    if refill is not None:
        annotate(state, refill)
    # Swaps bring in outsiders only: a player already in the lineup (kept or locked) is not a "swap in".
    pairs = [annotate(state, s) for s in two_for_two(state, pool, top=top)]
    singles = {
        i: [annotate(state, s) for s in swaps] for i, swaps in one_for_one(state, pool, top=top).items()
    }
    return LineupSuggestions(state=state, refill=refill, pairs=pairs, singles=singles)
