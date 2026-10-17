"""Suggested lineups: complete DraftKings builds from Sam's own pool, scored by the lineup simulator.

`dfs lineups suggest` writes them to the `Suggested` tab (`sheet_suggested.py`); nothing here touches a
sheet. Sam's own lineups are never overwritten: he copies a build into an empty Lineups block himself
(Lineup Tools > Copy builds to Lineups).

**Candidates.** The players in his pool (`Cash` and `Both` for cash builds, `GPP` and `Both` for GPP
builds) plus EVERY defense on the slate, pooled or not (Sam, 2026-10-17: the DST is the cheapest slot to
get wrong and the least worth hand-picking), nobody listed OUT or IR, nobody whose game has started
(`late_swap_search.swap_pool`), ranked by the projection (`CalPts`, `ProjPts` where CalPts is blank). The
rules are late swap's: the salary cap, the roster slots (FLEX takes an RB, WR or TE), no DST against his own
QB and at most one RB per game.

**Cash builds** (default 3). The best `CASH_TOP_K` lineups by projection (the branch-and-bound of
`late_swap_search`, `top_lineups`) are all simulated, and the builds are the ones with the highest `Cash %`
(the simulator's chance of beating the double-up cash line, which moves with the slate: the field's average
score plus a fixed margin; `dfs.sim.field`), each differing from the builds before it by at least
`MIN_DIFFERENCE` players. Against a FIXED line above a lineup's median, maximising P(score > line) would
reward variance, the opposite of a cash build; measuring against the field is what stops that.

**GPP builds** (every QB stack, ideally `BUILDS_PER_QB` = 2 each; `--gpp N` asks for the N best
instead; any pool player may be used, but never a lineup of only Cash-marked ones). Every candidate holds
a stack: the QB, at least two of his pass catchers (WR /
TE) and at least one player from the opponent (the bring-back). A branch-and-bound that cannot see a stack
would spend its whole budget on lineups without one, so each QB is searched on his own (`stacked_lineups`),
cutting every branch that can no longer reach a stack.
Candidates are ranked by `Top 1%` (the simulator's chance of reaching the large-field top-1% line: the
field's average score plus 63 points; with `--fixed-lines` the old fixed GPP target), each differing from the
earlier builds by at least `MIN_DIFFERENCE` players, and no player is in more than `MAX_EXPOSURE` of the
builds. A build's title names its stack ("DET stack + ARI bring-back").

**Order.** The builds are listed Cash first, then GPP; inside each by quarterback (name, A to Z), then the
highest `CalPts` total first (`display_key`), and numbered 1..n down that list (`number_builds`). Which
lineups are chosen is decided by the odds above; the order only changes how they are shown.

**FLEX** is always the player with the latest kickoff among the position that has one more player than its
own slots (`flex_order`; Sam's rule), so a build is ready to enter as it stands.

**Deterministic**: the search has no randomness and the simulator runs a fixed number of draws with a fixed
seed (`sim_inputs.N_SIMS`, `SEED`), so the same pool and the same data give the same builds. Probabilities
are counts of draws, so many lineups TIE exactly (on a quiet slate most GPP lineups reach the target in 0-5 of
1,500 draws); every ordering therefore ends in a canonical tie-break (`lineup_key`, the sorted names) and
compares probabilities at a fixed precision (`PROB_DECIMALS`), never on the last bits of a float. The choice
among tied lineups can then not depend on iteration order or on how the machine rounded a sum."""

from __future__ import annotations

import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime

import pandas as pd

from dfs.flex_order import BASE_SLOTS
from dfs.kickoff import parse_kickoff
from dfs.late_swap_search import (
    LineupState,
    OpenSlot,
    SwapPlayer,
    swap_pool,
    top_lineups,
)
from dfs.models import ROSTER_SLOTS
from dfs.sim.field import FieldModel
from dfs.sim.simulate import PlayerSpec
from dfs.sim_inputs import N_SIMS, SEED, lineup_from_names

DEFAULT_CASH_BUILDS = 3
SECOND_BUILD_MIN_DIFFERENCE = 2  # a QB's second build need only differ from the others by this many players
BUILDS_PER_QB = 2  # GPP builds: every QB with a stack gets one build, and ideally this many
MIN_DIFFERENCE = 3  # each build differs from every earlier one by at least this many players
MAX_EXPOSURE = 0.6  # no player in more than this share of the GPP builds
CASH_TOP_K = 300  # how many of the best cash lineups by projection are simulated
PER_QB = 80  # stacked lineups kept per quarterback while building the GPP candidates
TIME_LIMIT_SECONDS = 60.0  # the whole search, cash and GPP together
CASH_SHARE_OF_TIME = 0.4  # the cash search may use this share of it; GPP gets the rest
PASS_CATCHERS = ("WR", "TE")

CASH_TAGS = frozenset({"Cash", "Both"})
GPP_TAGS = frozenset({"Cash", "GPP", "Both"})  # a GPP build may use anyone in the pool, whatever the mark
GPP_MARKS = frozenset({"GPP", "Both"})  # the pool marks that count as "GPP" for the rule below
GPP_MIN_NOT_CASH_ONLY = 1  # ...but not a lineup of nothing but Cash-marked players (small-field contests)


@dataclass(frozen=True)
class Build:
    """One suggested lineup: nine players in `ROSTER_SLOTS` order, what they cost and project, the simulator's
    odds, and (for a GPP build) the stack that names it."""

    number: int
    kind: str  # "cash" or "gpp"
    title: str
    players: tuple[SwapPlayer, ...]
    salary: int
    projection: float
    p_cash: float
    p_gpp: float
    typical: float  # the simulated median score
    big_night: float  # the simulated 90th percentile
    stack: str = ""


@dataclass
class SuggestReport:
    """What a run produced and what it could not: shown in the terminal and as the tab's status line."""

    builds: list[Build] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    seconds: float = 0.0
    candidates: dict[str, int] = field(default_factory=dict)  # "cash" / "gpp" -> players considered
    complete: bool = True  # False when the time limit cut a search short
    phases: dict[str, float] = field(default_factory=dict)  # phase -> seconds, in the order they ran


# ---------------------------------------------------------------------------------------------
# Candidates and rules
# ---------------------------------------------------------------------------------------------


def _all_open_state(cap: int) -> LineupState:
    return LineupState(
        cap=cap, fixed={}, open=[OpenSlot(i, slot, None) for i, slot in enumerate(ROSTER_SLOTS)]
    )


def slate_dsts(edge: pd.DataFrame) -> set[str]:
    """Every defense on the slate (the names of EdgeRaw's DST rows), pooled or not."""
    if "Position" not in edge.columns:
        return set()
    return {str(name) for name in edge.loc[edge["Position"] == "DST", "Name"]}


def pool_for(
    edge: pd.DataFrame,
    tags: dict[str, str],
    wanted: frozenset[str],
    *,
    now: datetime,
    metric: str,
    any_dst: bool = False,
) -> list[SwapPlayer]:
    """The pool players tagged with one of `wanted` who can still be used: not OUT or IR, game not started.
    With `any_dst`, every defense on the slate too (Sam, 2026-10-17: any DST, pooled or not)."""
    names = {name for name, tag in tags.items() if tag in wanted}
    if any_dst and tags:  # an empty pool stays empty: there is nothing to build around
        names |= slate_dsts(edge)
    return swap_pool(edge, now=now, metric=metric, pool_names=names)


def is_stacked(players: Sequence[SwapPlayer]) -> bool:
    """The GPP rule: the QB, at least two of his pass catchers and at least one player from his opponent."""
    qb = next((p for p in players if p.position == "QB"), None)
    if qb is None:
        return False
    mates = sum(1 for p in players if p.team == qb.team and p.position in PASS_CATCHERS)
    return mates >= 2 and any(p.team == qb.opp and p.position != "DST" for p in players)


def not_all_cash(tags: dict[str, str]):
    """The GPP test on a finished lineup: at least `GPP_MIN_NOT_CASH_ONLY` of its players are marked GPP or
    Both, so a GPP build is never nine Cash-marked players. A defense nobody pooled is not marked GPP either:
    it does not count toward the minimum."""

    def ok(players: Sequence[SwapPlayer]) -> bool:
        return sum(1 for p in players if tags.get(p.name) in GPP_MARKS) >= GPP_MIN_NOT_CASH_ONLY

    return ok


def stack_name(players: Sequence[SwapPlayer]) -> str:
    """ "DET stack + ARI bring-back" for a stacked lineup, "" for one without a stack."""
    qb = next((p for p in players if p.position == "QB"), None)
    if qb is None or not is_stacked(players):
        return ""
    return f"{qb.team} stack + {qb.opp} bring-back"


def seat(players: Sequence[SwapPlayer], kickoff: dict[str, datetime | None]) -> tuple[SwapPlayer, ...]:
    """Nine players in `ROSTER_SLOTS` order with the FLEX rule applied: of the position that has one more
    player than its own slots, the one with the latest kickoff is the FLEX (a tie, or no kickoffs, puts the
    lowest projected there). Within a position the earlier kickoffs come first, then the higher projection."""

    def start(p: SwapPlayer):
        k = kickoff.get(p.name)
        return (k is None, k, -p.value, p.name)

    by_position: dict[str, list[SwapPlayer]] = {}
    for p in players:
        by_position.setdefault(p.position, []).append(p)
    flex_position = next(
        (pos for pos, base in BASE_SLOTS.items() if len(by_position.get(pos, [])) > base), None
    )
    flex = None
    if flex_position is not None:
        group = by_position[flex_position]
        known = [kickoff[p.name] for p in group if kickoff.get(p.name) is not None]
        latest = max(known) if known else None
        last = [p for p in group if latest is not None and kickoff.get(p.name) == latest] or group
        flex = min(last, key=lambda p: (p.value, p.name))
        by_position[flex_position] = [p for p in group if p is not flex]
    queues = {pos: iter(sorted(group, key=start)) for pos, group in by_position.items()}
    return tuple(flex if slot == "FLEX" else next(queues[slot]) for slot in ROSTER_SLOTS)


# ---------------------------------------------------------------------------------------------
# Candidate lineups
# ---------------------------------------------------------------------------------------------


def cash_lineups(
    pool: list[SwapPlayer], *, cap: int, k: int = CASH_TOP_K, deadline: float | None = None
) -> tuple[list[list[SwapPlayer]], bool]:
    """The best `k` legal lineups from `pool` by projection (any shape), best first."""
    state = _all_open_state(cap)
    remaining = None if deadline is None else max(0.0, deadline - time.monotonic())
    found, complete = top_lineups(state, pool, k=k, time_limit=remaining)
    return [[a[i] for i in range(len(ROSTER_SLOTS))] for a in found], complete


def can_stack(qb: SwapPlayer, pool: Sequence[SwapPlayer]) -> bool:
    """Can ANY lineup stack this QB from `pool`: at least two of his pass catchers and one non-DST player from
    his opponent (the `is_stacked` rule)? A QB who cannot is left out of the GPP search: the search cannot see
    that, so it used to spend its whole share of the time limit on a stack that does not exist (a 2026-10-17
    run of a real 63-player pool: 7 of 12 QBs, 57 of 60 seconds, and the other five left short of time)."""
    mates = sum(1 for p in pool if p.team == qb.team and p.position in PASS_CATCHERS)
    back = any(p.team == qb.opp and p.position not in ("DST", "QB") for p in pool)
    return mates >= 2 and back


def _stack_feasible(qb: SwapPlayer):
    """A `top_lineups(feasible=...)` for one QB's stack: from a partial lineup, can the slots still to fill
    hold the pass catchers and the bring-back it still needs? (Never says no to a lineup that can pass.)"""

    def feasible(placed: list[SwapPlayer], rest: list[str]) -> bool:
        mates = sum(1 for p in placed if p.team == qb.team and p.position in PASS_CATCHERS)
        back = any(p.team == qb.opp and p.position != "DST" for p in placed)
        need_mates, need_back = max(0, 2 - mates), 0 if back else 1
        receiver_slots = sum(1 for slot in rest if slot in ("TE", "WR", "FLEX"))
        skill_slots = sum(1 for slot in rest if slot in ("RB", "TE", "WR", "FLEX"))
        return need_mates <= receiver_slots and need_mates + need_back <= skill_slots

    return feasible


def stacked_lineups(
    pool: list[SwapPlayer],
    *,
    cap: int,
    k: int | None = None,
    per_qb: int = PER_QB,
    deadline: float | None = None,
    leaf_ok: Callable[[Sequence[SwapPlayer]], bool] | None = None,
) -> tuple[list[list[SwapPlayer]], bool]:
    """The best stacked lineups from `pool` by projection, best first: `per_qb` for each quarterback (all of
    them, or the best `k` of those when `k` is given). `leaf_ok` is an extra test a finished lineup must pass.

    One search per QB: he is fixed in the QB slot and the other eight slots are searched exactly for the best
    `per_qb` lineups that hold two of his pass catchers and one of his opponent's players (`is_stacked`),
    cutting every branch that can no longer reach a stack (`_stack_feasible`). The time left is shared out
    evenly, so a short budget thins every QB's list instead of dropping the last QBs."""
    quarterbacks = [qb for qb in pool if qb.position == "QB" and can_stack(qb, pool)]
    qb_slot = ROSTER_SLOTS.index("QB")
    found: dict[frozenset[str], list[SwapPlayer]] = {}
    complete = True
    for n, qb in enumerate(quarterbacks):
        left = None if deadline is None else max(0.0, deadline - time.monotonic())
        share = None if left is None else left / (len(quarterbacks) - n)
        state = LineupState(
            cap=cap,
            fixed={qb_slot: qb},
            open=[OpenSlot(i, slot, None) for i, slot in enumerate(ROSTER_SLOTS) if i != qb_slot],
        )
        rest = [p for p in pool if p.name != qb.name and p.position != "QB"]
        assignments, done = top_lineups(
            state,
            rest,
            k=per_qb,
            leaf_filter=lambda ps: is_stacked(ps) and (leaf_ok is None or leaf_ok(ps)),
            feasible=_stack_feasible(qb),
            time_limit=share,
        )
        complete = complete and done
        for assignment in assignments:
            lineup = [qb, *assignment.values()]
            found.setdefault(frozenset(p.name for p in lineup), lineup)
    return _best_first(found, k), complete


PROB_DECIMALS = 9  # probabilities and projections compare at this precision (draw counts are far coarser)
VALUE_DECIMALS = 6


def lineup_names(players: Sequence[SwapPlayer]) -> tuple[str, ...]:
    """A lineup's canonical identity: its players' names, sorted. The last tie-break of every ordering."""
    return tuple(sorted(p.name for p in players))


def _best_first(found: dict[frozenset[str], list[SwapPlayer]], k: int | None) -> list[list[SwapPlayer]]:
    ranked = sorted(
        found.values(),
        key=lambda lu: (
            -round(sum(p.value for p in lu), VALUE_DECIMALS),
            sum(p.salary for p in lu),
            lineup_names(lu),
        ),
    )
    return ranked if k is None else ranked[:k]


# ---------------------------------------------------------------------------------------------
# Scoring and choosing
# ---------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Scored:
    players: tuple[SwapPlayer, ...]  # in ROSTER_SLOTS order, FLEX rule applied
    projection: float
    salary: int
    p_cash: float
    p_gpp: float
    typical: float
    big_night: float


def score(
    lineups: list[list[SwapPlayer]],
    specs: dict[str, PlayerSpec],
    kickoff: dict[str, datetime | None],
    *,
    cash_line: float,
    gpp_target: float,
    n_sims: int = N_SIMS,
    seed: int = SEED,
    field: FieldModel | None = None,
) -> list[Scored]:
    """Simulate every lineup in ONE run (a player in two lineups has the same draws in both) and return them
    with their odds. A lineup with a player the simulator has no spec for is left out. With a `field` the
    odds are against the field's lines (Cash % and Top 1%), else against `cash_line` / `gpp_target`."""
    from dfs.sim.simulate import simulate_lineups

    seated = [seat(lineup, kickoff) for lineup in lineups]
    rated = [
        (players, specs_list)
        for players in seated
        if (specs_list := lineup_from_names([p.name for p in players], specs)) is not None
    ]
    if not rated:
        return []
    result = simulate_lineups(
        [s for _, s in rated],
        n_sims=n_sims,
        seed=seed,
        cash_line=cash_line,
        gpp_target=gpp_target,
        field=field,
    )
    return [
        Scored(
            players=players,
            projection=sum(p.value for p in players),
            salary=sum(p.salary for p in players),
            p_cash=float(stats.p_cash),
            p_gpp=float(stats.p_gpp),
            typical=float(stats.quantiles[0.5]),
            big_night=float(stats.quantiles[0.9]),
        )
        for (players, _), stats in zip(rated, result.lineups, strict=True)
    ]


def different_enough(a: Sequence[SwapPlayer], b: Sequence[SwapPlayer], minimum: int = MIN_DIFFERENCE) -> bool:
    """True when the two lineups differ by at least `minimum` players."""
    return len({p.name for p in a} - {p.name for p in b}) >= minimum


def exposure_cap(builds: int, share: float = MAX_EXPOSURE) -> int:
    """The most builds one player may be in: about `share` of them, never less than one."""
    return max(1, round(share * builds))


BACKTRACK_NODES = 20000  # placements the exposure-cap search may make before it gives up (never time)


def lineup_key(scored: Scored) -> tuple[str, ...]:
    return lineup_names(scored.players)


def cash_key(s: Scored):
    """Best first: the chance of beating the cash line, then projection, the cheaper lineup, then names."""
    return (
        -round(s.p_cash, PROB_DECIMALS),
        -round(s.projection, VALUE_DECIMALS),
        s.salary,
        lineup_key(s),
    )


def gpp_key(s: Scored):
    """Best first: the Top 1% chance (`p_gpp`), then projection, the cheaper lineup, then names."""
    return (
        -round(s.p_gpp, PROB_DECIMALS),
        -round(s.projection, VALUE_DECIMALS),
        s.salary,
        lineup_key(s),
    )


def _fits(
    candidate: Scored, chosen: list[Scored], uses: dict[str, int], min_difference: int, cap: int | None
):
    if not all(different_enough(candidate.players, c.players, min_difference) for c in chosen):
        return False
    return cap is None or not any(uses.get(p.name, 0) >= cap for p in candidate.players)


def choose(
    scored: list[Scored],
    count: int,
    *,
    key,  # noqa: ANN001 - Scored -> sort key, best first
    min_difference: int = MIN_DIFFERENCE,
    max_per_player: int | None = None,
) -> list[Scored]:
    """The best `count` by `key`, each differing from the ones already chosen by `min_difference` players and
    (when `max_per_player` is set) with no player in more than that many of them.

    Ordered by `key`, then by `lineup_key` (so a tie is never broken by input order). Greedy in that order.
    When the exposure cap leaves it short, EVERY candidate is searched for a set of `count` that satisfies
    every rule, and the first such set in order is returned. The search is exact: after each pick it keeps
    only the candidates that still fit with everything picked, and stops a branch the moment too few remain,
    so the count of placements (`BACKTRACK_NODES`), not the clock, is the only limit and the answer is the
    same on every machine. Fewer than `count` come back only when no such set exists (or the limit is hit)."""

    def ranked(s: Scored) -> tuple:
        k = key(s)
        return (*(k if isinstance(k, tuple) else (k,)), lineup_key(s))

    ordered = sorted(scored, key=ranked)
    chosen: list[Scored] = []
    uses: dict[str, int] = {}
    for candidate in ordered:
        if len(chosen) == count:
            break
        if _fits(candidate, chosen, uses, min_difference, max_per_player):
            chosen.append(candidate)
            for p in candidate.players:
                uses[p.name] = uses.get(p.name, 0) + 1
    if len(chosen) == count or max_per_player is None:
        return chosen

    names = [frozenset(p.name for p in c.players) for c in ordered]
    placements = 0

    def search(remaining: list[int], picked: list[int], used: dict[str, int]) -> list[int] | None:
        nonlocal placements
        need = count - len(picked)
        if need == 0:
            return list(picked)
        for position, i in enumerate(remaining):
            if len(remaining) - position < need:
                return None  # too few left to finish, whichever is picked
            placements += 1
            if placements > BACKTRACK_NODES:
                return None
            for name in names[i]:
                used[name] = used.get(name, 0) + 1
            still = [
                j
                for j in remaining[position + 1 :]
                if len(names[j] - names[i]) >= min_difference
                and not any(used.get(name, 0) >= max_per_player for name in names[j])
            ]
            picked.append(i)
            found = search(still, picked, used) if len(still) >= need - 1 else None
            picked.pop()
            for name in names[i]:
                used[name] -= 1
            if found is not None:
                return found
        return None

    found = search(list(range(len(ordered))), [], {})
    return [ordered[i] for i in found] if found is not None else chosen


def choose_per_qb(
    scored: list[Scored],
    *,
    key,  # noqa: ANN001 - Scored -> sort key, best first
    min_difference: int = MIN_DIFFERENCE,
    share: float = MAX_EXPOSURE,
) -> list[Scored]:
    """GPP builds that cover every QB stack: one round gives each quarterback his best lineup, a second round
    a second one (`BUILDS_PER_QB`; it needs only `SECOND_BUILD_MIN_DIFFERENCE` different players),
    quarterbacks in order of their best lineup, each build differing from the
    earlier ones by `min_difference` players and (from the second round on) no player past the exposure cap:
    every QB gets his first build whatever the cap says. The cap is a share of the
    number of builds, which is only known at the end, so it is re-run until the cap fits the builds it made.
    Returned best first by `key`."""

    def ranked(s: Scored) -> tuple:
        k = key(s)
        return (*(k if isinstance(k, tuple) else (k,)), lineup_key(s))

    by_qb: dict[str, list[Scored]] = {}
    for candidate in sorted(scored, key=ranked):
        qb = next((p.name for p in candidate.players if p.position == "QB"), None)
        if qb is not None:
            by_qb.setdefault(qb, []).append(candidate)
    order = sorted(by_qb, key=lambda qb: ranked(by_qb[qb][0]))

    def run(cap: int) -> list[Scored]:
        chosen: list[Scored] = []
        uses: dict[str, int] = {}
        for round_ in range(BUILDS_PER_QB):
            limit = None if round_ == 0 else cap  # a QB's first build is never given up to the exposure cap
            # The best lineups of one stack are variants of each other (a cheap player swapped), so a second
            # build of it would almost never clear the usual difference: it needs one fewer player.
            need = min_difference if round_ == 0 else min(min_difference, SECOND_BUILD_MIN_DIFFERENCE)
            for qb in order:
                for candidate in by_qb[qb]:
                    if candidate not in chosen and _fits(candidate, chosen, uses, need, limit):
                        chosen.append(candidate)
                        for p in candidate.players:
                            uses[p.name] = uses.get(p.name, 0) + 1
                        break
        return chosen

    def cap_for(builds: int) -> int:
        # The core players of every stack are in each QB's first build, so a cap below the number of QBs plus
        # one would forbid any second build at all: the cap is never less than that.
        return max(exposure_cap(builds, share), len(order) + 1)

    cap = cap_for(BUILDS_PER_QB * len(order))
    chosen = run(cap)
    while chosen and cap_for(len(chosen)) < cap:
        cap = cap_for(len(chosen))
        chosen = run(cap)
    return sorted(chosen, key=ranked)


def suggest_builds(
    edge: pd.DataFrame,
    tags: dict[str, str],
    specs: dict[str, PlayerSpec],
    *,
    now: datetime,
    cap: int,
    cash_line: float,
    gpp_target: float,
    metric: str,
    cash_builds: int = DEFAULT_CASH_BUILDS,
    gpp_builds: int | None = None,  # None: every QB stack, `BUILDS_PER_QB` builds each; 0: no GPP builds
    n_sims: int = N_SIMS,
    seed: int = SEED,
    time_limit: float = TIME_LIMIT_SECONDS,
    field: FieldModel | None = None,
) -> SuggestReport:
    """Everything `dfs lineups suggest` computes. `tags` maps a name to his pool tag (Cash / GPP / Both).
    `field` (from `sim_inputs.resolve_lines`) scores against lines that move with the slate; without it the
    builds are scored against the fixed `cash_line` / `gpp_target`."""
    started = time.monotonic()
    deadline = started + time_limit
    cash_deadline = started + time_limit * CASH_SHARE_OF_TIME  # the GPP search gets the rest
    report = SuggestReport()
    kickoff = {
        str(name): parse_kickoff(start)
        for name, start in zip(
            edge["Name"], edge.get("GameStart", pd.Series("", index=edge.index)), strict=True
        )
    }
    picked: list[tuple[str, Scored, str]] = []  # (kind, pick, stack name), in the order chosen

    def timed(phase: str, since: float) -> float:
        """Add the time since `since` to `phase` (a phase that runs twice, cash and GPP, adds up)."""
        now_ = time.monotonic()
        report.phases[phase] = report.phases.get(phase, 0.0) + now_ - since
        return now_

    t = time.monotonic()
    cash_pool = pool_for(edge, tags, CASH_TAGS, now=now, metric=metric, any_dst=True) if cash_builds else []
    gpp_pool = pool_for(edge, tags, GPP_TAGS, now=now, metric=metric, any_dst=True) if gpp_builds != 0 else []
    report.candidates = {"cash": len(cash_pool), "gpp": len(gpp_pool)}
    timed("candidates", t)

    if cash_builds:
        t = time.monotonic()
        lineups, done = cash_lineups(cash_pool, cap=cap, deadline=cash_deadline)
        report.complete = report.complete and done
        t = timed("search", t)
        scored = score(
            lineups,
            specs,
            kickoff,
            cash_line=cash_line,
            gpp_target=gpp_target,
            n_sims=n_sims,
            seed=seed,
            field=field,
        )
        t = timed("simulate", t)
        picks = choose(scored, cash_builds, key=cash_key)
        timed("choose", t)
        picked.extend(("cash", pick, "") for pick in picks)
        if len(picks) < cash_builds:
            report.notes.append(
                f"Only {len(picks)} cash build(s): the cash pool ({len(cash_pool)} players) does not hold "
                f"{cash_builds} legal lineups that differ by {MIN_DIFFERENCE}+ players."
            )

    if gpp_builds != 0:
        t = time.monotonic()
        lineups, done = stacked_lineups(gpp_pool, cap=cap, deadline=deadline, leaf_ok=not_all_cash(tags))
        report.complete = report.complete and done
        t = timed("search", t)
        scored = score(
            lineups,
            specs,
            kickoff,
            cash_line=cash_line,
            gpp_target=gpp_target,
            n_sims=n_sims,
            seed=seed,
            field=field,
        )
        t = timed("simulate", t)
        if gpp_builds is None:
            picks = choose_per_qb(scored, key=gpp_key)
        else:
            picks = choose(scored, gpp_builds, key=gpp_key, max_per_player=exposure_cap(gpp_builds))
        timed("choose", t)
        picked.extend(("gpp", pick, stack_name(pick.players)) for pick in picks)
        if not lineups:
            report.notes.append(
                "No stacked GPP lineup fits: a stack needs a QB, two of his pass catchers and one of his "
                f"opponent's players from your pool ({len(gpp_pool)} players)."
            )
        else:
            stacked_qbs = {p.name for s in scored for p in s.players if p.position == "QB"}
            built = {p.name for pick in picks for p in pick.players if p.position == "QB"}
            counts = {qb: sum(1 for pick in picks if any(p.name == qb for p in pick.players)) for qb in built}
            missing = sorted(stacked_qbs - built)
            thin = sorted(qb for qb, n in counts.items() if n < BUILDS_PER_QB)
            if gpp_builds is None and missing:
                report.notes.append(
                    f"No GPP build for {', '.join(missing)}: every legal lineup with that stack sat within "
                    f"{MIN_DIFFERENCE - 1} players of a better build, or over the exposure cap."
                )
            elif gpp_builds is None and thin:
                report.notes.append("Only one GPP build for " + ", ".join(thin) + ".")
            elif gpp_builds is not None and len(picks) < gpp_builds:
                report.notes.append(
                    f"Only {len(picks)} GPP build(s): {len(lineups)} stacked lineups, but not {gpp_builds} "
                    f"that differ by {MIN_DIFFERENCE}+ players with no player in more than "
                    f"{exposure_cap(gpp_builds)} builds."
                )

    report.builds = number_builds(picked)

    report.seconds = time.monotonic() - started
    if not report.complete:
        report.notes.append(
            "The time limit cut the search short: a better build may exist (raise it with `--seconds`)."
        )
    return report


def display_key(kind_and_pick: tuple[str, Scored, str]):
    """The order builds are listed in: Cash before GPP, then by quarterback (name, A to Z), then the highest
    `CalPts` total first, then the cheaper lineup and the names, so the order never depends on input order."""
    kind, pick, _ = kind_and_pick
    qb = next((p.name for p in pick.players if p.position == "QB"), "")
    return (
        kind != "cash",
        qb,
        -round(pick.projection, VALUE_DECIMALS),
        pick.salary,
        lineup_key(pick),
    )


def number_builds(picked: Sequence[tuple[str, Scored, str]]) -> list[Build]:
    """The chosen lineups as `Build`s in display order (`display_key`), numbered 1..n down that order (Sam,
    2026-10-17: Copy builds' "1,4,6" must match what the Suggested tab shows). Which lineups were chosen is
    decided by the simulator's odds; this only orders and numbers them."""
    builds = []
    for number, (kind, pick, name) in enumerate(sorted(picked, key=display_key), start=1):
        title = f"Cash build {number}" if kind == "cash" else f"GPP build {number}  ·  {name}"
        builds.append(_build(number, kind, title, pick, stack=name))
    return builds


def phase_lines(phases: dict[str, tuple[float, int]]) -> list[str]:
    """The run's phase times for the terminal, in the order they ran, one per line: seconds, the share of the
    total and (where the phase talks to the sheet) the API requests it made, then a total. `phases` maps a
    name to (seconds, requests); a phase that made no sheet request shows none."""
    total = sum(seconds for seconds, _ in phases.values())
    lines = ["Phase times:"]
    for name, (seconds, requests) in phases.items():
        share = f"{seconds / total:>4.0%}" if total > 0 else "   -"
        calls = f"  {requests} request{'' if requests == 1 else 's'}" if requests else ""
        lines.append(f"  {name:<18}{seconds:>6.1f} s  {share}{calls}")
    lines.append(f"  {'total':<18}{total:>6.1f} s")
    return lines


def _build(number: int, kind: str, title: str, pick: Scored, stack: str = "") -> Build:
    return Build(
        number=number,
        kind=kind,
        title=title,
        players=pick.players,
        salary=pick.salary,
        projection=pick.projection,
        p_cash=pick.p_cash,
        p_gpp=pick.p_gpp,
        typical=pick.typical,
        big_night=pick.big_night,
        stack=stack,
    )
