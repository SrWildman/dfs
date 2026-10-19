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
a stack: the QB and one to three of his teammates at any skill position (RB, WR or TE, in any mix), with or
without a bring-back (Sam, 2026-10-18: the bring-back is not forced, the simulator decides). The candidates
come from several soft shapes (`lineup_shapes.py`): the stack with a bring-back, the naked stack, a stack that
holds a RB, a game stack, a stack with a RB and his own DST and a contrarian stack, each searched exactly
for every QB that can reach it. A branch-and-bound that cannot see a stack would spend its whole budget on
lineups without one, so each QB is searched on his own, cutting every branch that can no longer reach the
shape.
Candidates are ranked by `--rank`: `top1` (the default) by their `Top 1%`, the simulator's chance of reaching
the large-field top-1% line, the field's average score plus 63 points (with `--fixed-lines` the old fixed GPP
target); `share` by their Top 1% share (`Scored.gpp_value`: that chance divided by 1 + the identical entries
expected, `sim/dups.py`). Sam, 2026-10-18: the share is not the default until he has seen it retro-tested
on his own weeks. Either way each pick differs from the earlier builds by at least `MIN_DIFFERENCE` players,
no player is in more than `MAX_EXPOSURE` of the builds, and a build's title names its shape, its make-up and
the copies expected ("DET stack QB+WR+TE + ARI bring-back  ·  ~0.3 dupes").

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
from dataclasses import dataclass, field, replace
from datetime import datetime

import pandas as pd

from dfs import lineup_shapes as shapes_mod
from dfs.flex_order import BASE_SLOTS
from dfs.kickoff import parse_kickoff
from dfs.late_swap_search import (
    LineupState,
    OpenSlot,
    SwapPlayer,
    swap_pool,
    top_lineups,
)
from dfs.lineup_shapes import can_stack, is_stacked  # noqa: F401 - re-exported: callers and tests use both
from dfs.models import ROSTER_SLOTS
from dfs.sim import dups
from dfs.sim.field import FieldModel, build_field, has_ownership
from dfs.sim.simulate import PlayerSpec
from dfs.sim_inputs import N_SIMS, SEED, lineup_from_names, ownership_by_name

DEFAULT_CASH_BUILDS = 3
SECOND_BUILD_MIN_DIFFERENCE = 2  # a QB's second build need only differ from the others by this many players
BUILDS_PER_QB = 2  # GPP builds: every QB with a stack gets one build, and ideally this many
MIN_DIFFERENCE = 3  # each build differs from every earlier one by at least this many players
MAX_EXPOSURE = 0.6  # no player in more than this share of the GPP builds
CASH_TOP_K = 300  # how many of the best cash lineups by projection are simulated
PER_QB = 80  # stacked lineups kept per quarterback while building the GPP candidates
TIME_LIMIT_SECONDS = 120.0  # the whole search, cash and GPP together (a real slate needs 53-152 s)
CASH_SHARE_OF_TIME = 0.4  # the cash search may use this share of it; GPP gets the rest
GPP_SHAPES = shapes_mod.SHAPES  # the shapes the GPP candidates are drawn from (lineup_shapes.py)
RANKS = ("top1", "share")  # `--rank`: what orders the GPP candidates (see `rank_key`)
DEFAULT_RANK = "top1"  # the share becomes the default only after Sam has seen it retro-tested on his weeks
# GPP candidate searches, in the order they run: (shape, lineups kept per quarterback, time weight). The
# stack with a bring-back goes first and has the biggest share; each shape's time box is its weight's share of
# what is left when it starts (so time a shape does not use rolls forward to the next).
GPP_PLAN = (
    (shapes_mod.STACK, 40, 6.0),
    (shapes_mod.NAKED, 20, 2.0),
    (shapes_mod.RB_STACK, 20, 1.5),
    (shapes_mod.GAME, 10, 1.0),
    (shapes_mod.RB_DST, 10, 1.0),
    (shapes_mod.CONTRARIAN, 10, 0.5),
)

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
    stack: str = ""  # a GPP build's shape name ("DET game stack")
    shapes: tuple[str, ...] = ()  # every shape the lineup matches (lineup_shapes.SHAPES)
    own_sum: float | None = None  # the nine players' ownership added up (1.16 = 116%); None: no ownership yet
    own_product: float | None = None  # ... and multiplied: the uniqueness measure (sim/dups.py)
    e_dups: float | None = (
        None  # expected identical entries: GPP builds in a `entries`-entry GPP, cash builds in a double-up
    )
    entries: int = 0  # the contest size e_dups assumes
    objective: float | None = None  # a GPP build's Top 1% share: p_gpp / (1 + e_dups); None without ownership
    # The iterate rerun's state (`lineup_iterate`; blank on a plain run): what Sam decided, what he locked,
    # which players he typed in himself, why the build is what it is and where it came from.
    decision: str = ""  # "Keep", "Rebuild" or "" (blank: Rebuild); the bar's decision cell
    locked: tuple[str, ...] = ()  # names that stay in this build (the lock column's ticks)
    yours: tuple[str, ...] = ()  # locked names Sam typed in over another player ("your pick")
    note: str = ""  # the one-line "what changed" under the build
    problem: bool = False  # the note is a refusal (an invalid edit), shown in red
    origin: str = "new"  # "kept", "rebuilt" or "new"


@dataclass
class SuggestReport:
    """What a run produced and what it could not: shown in the terminal and as the tab's status line."""

    builds: list[Build] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    seconds: float = 0.0
    candidates: dict[str, int] = field(default_factory=dict)  # "cash" / "gpp" -> players considered
    complete: bool = True  # False when the time limit cut a search short
    phases: dict[str, float] = field(default_factory=dict)  # phase -> seconds, in the order they ran
    rank: str = DEFAULT_RANK  # what ordered the GPP candidates ("top1" or "share")
    # The iterate state shown on the tab (`sheet_suggested`): which round this is, the typed rules box (label
    # -> text, carried from the tab) and the one-line history of every round.
    round: int = 0
    rules: dict[str, str] = field(default_factory=dict)
    history: list[str] = field(default_factory=list)


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


def not_all_cash(tags: dict[str, str]):
    """The GPP test on a finished lineup: at least `GPP_MIN_NOT_CASH_ONLY` of its players are marked GPP or
    Both, so a GPP build is never nine Cash-marked players. A defense nobody pooled is not marked GPP either:
    it does not count toward the minimum."""

    def ok(players: Sequence[SwapPlayer]) -> bool:
        return sum(1 for p in players if tags.get(p.name) in GPP_MARKS) >= GPP_MIN_NOT_CASH_ONLY

    return ok


def stack_name(players: Sequence[SwapPlayer]) -> str:
    """ "DET stack QB+WR+TE + ARI bring-back" for a stacked lineup, "" for one without a stack."""
    return shapes_mod.shape_name(players)


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
    pool: list[SwapPlayer],
    *,
    cap: int,
    k: int = CASH_TOP_K,
    deadline: float | None = None,
    fixed: Sequence[SwapPlayer] = (),
    leaf_ok: Callable[[Sequence[SwapPlayer]], bool] | None = None,
) -> tuple[list[list[SwapPlayer]], bool]:
    """The best `k` legal lineups from `pool` by projection (any shape), best first. `fixed` players (an
    iterate rerun's locks) are in every lineup; `leaf_ok` is an extra test a finished lineup must pass."""
    remaining = None if deadline is None else max(0.0, deadline - time.monotonic())
    if not fixed:
        found, complete = top_lineups(
            _all_open_state(cap), pool, k=k, leaf_filter=leaf_ok, time_limit=remaining
        )
        return [[a[i] for i in range(len(ROSTER_SLOTS))] for a in found], complete
    seated = shapes_mod.seat(list(fixed))
    if seated is None:
        return [], True  # they cannot share a lineup
    state = LineupState(
        cap=cap,
        fixed=seated,
        open=[OpenSlot(i, slot, None) for i, slot in enumerate(ROSTER_SLOTS) if i not in seated],
    )
    names = {p.name for p in fixed}
    found, complete = top_lineups(
        state,
        [p for p in pool if p.name not in names],
        k=k,
        leaf_filter=leaf_ok,
        time_limit=remaining,
    )
    return [[*seated.values(), *a.values()] for a in found], complete


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
    The search is `lineup_shapes.shape_lineups` for the `stack` shape."""
    ranked, complete = shapes_mod.shape_lineups(
        pool, shapes_mod.STACK, cap=cap, per_qb=per_qb, deadline=deadline, leaf_ok=leaf_ok
    )
    return (ranked if k is None else ranked[:k]), complete


PROB_DECIMALS = 9  # probabilities and projections compare at this precision (draw counts are far coarser)
VALUE_DECIMALS = 6


def lineup_names(players: Sequence[SwapPlayer]) -> tuple[str, ...]:
    """A lineup's canonical identity: its players' names, sorted. The last tie-break of every ordering."""
    return tuple(sorted(p.name for p in players))


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
    shapes: tuple[str, ...] = ()
    own_sum: float | None = None
    own_product: float | None = None
    e_dups: float | None = None
    objective: float | None = (
        None  # GPP: p_gpp / (1 + e_dups); None when there is no ownership to know dups from
    )

    @property
    def gpp_value(self) -> float:
        """What ranks a GPP candidate: its Top 1% share, or the plain Top 1% chance without ownership."""
        return self.p_gpp if self.objective is None else self.objective


def annotate(
    scored: list[Scored],
    shares: dict[str, float] | None,
    entries: int,
    *,
    objective: bool,
    contrarian_at: float | None = None,
    dup_model: dups.DupModel = dups.DupModel(),
) -> list[Scored]:
    """`scored` with each lineup's ownership sum and product, its expected duplicates among `entries` entries
    (`dups.expected_dups`), the shapes it matches and, for a GPP candidate (`objective`), its Top 1% share.
    Without `shares` (no ownership published) only the shapes are filled in."""
    out = []
    for s in scored:
        matched = shapes_mod.shapes_of(s.players, contrarian_at=contrarian_at, own=shares)
        if shares is None:
            out.append(replace(s, shapes=matched))
            continue
        own = dups.shares_for([p.name for p in s.players], shares)
        e = dup_model.expected(own, entries)
        out.append(
            replace(
                s,
                shapes=matched,
                own_sum=dups.ownership_sum(own),
                own_product=dups.ownership_product(own),
                e_dups=e,
                objective=dups.top1_share(s.p_gpp, e) if objective else None,
            )
        )
    return out


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
    """Best first, `--rank share`: the Top 1% share (`Scored.gpp_value`: the Top 1% chance discounted by the
    duplicates expected; the plain chance when no ownership is published), then the Top 1% chance itself,
    projection, the cheaper lineup, then names."""
    return (
        -round(s.gpp_value, PROB_DECIMALS),
        -round(s.p_gpp, PROB_DECIMALS),
        -round(s.projection, VALUE_DECIMALS),
        s.salary,
        lineup_key(s),
    )


def top1_key(s: Scored):
    """Best first, `--rank top1` (the default): the Top 1% chance alone, then projection, the cheaper lineup,
    then names. The duplicates expected are still worked out and shown, they just do not rank."""
    return (
        -round(s.p_gpp, PROB_DECIMALS),
        -round(s.projection, VALUE_DECIMALS),
        s.salary,
        lineup_key(s),
    )


def rank_key(rank: str):
    """The GPP sort key for `rank` ("top1" or "share")."""
    if rank not in RANKS:
        raise ValueError(f"rank must be one of {', '.join(RANKS)}, not {rank!r}")
    return gpp_key if rank == "share" else top1_key


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


def ownership_shares(
    specs: dict[str, PlayerSpec], edge: pd.DataFrame, field: FieldModel | None, contest: str
) -> dict[str, float] | None:
    """`{name: share of the contest's lineups holding him}` (sums to 9), the ownership the duplicate estimates
    use: the run's own field when it has one (`contest` "top" = a large-field GPP, "cash" = a double-up, with
    the config's ownership curves), else built from the edge's `Own%` with no curve. None when ownership has
    not published (then there is no duplicate estimate and the GPP objective is the plain Top 1% chance)."""
    if field is not None:
        return field.shares(contest)
    own = ownership_by_name(edge)
    if not has_ownership(own):
        return None
    built = build_field(specs.values(), own, cash_offset=0.0, top_offset=0.0)
    return None if built is None else built.shares(contest)


def gpp_candidates(
    pool: list[SwapPlayer],
    *,
    cap: int,
    deadline: float,
    leaf_ok: Callable[[Sequence[SwapPlayer]], bool],
    shapes: Sequence[str],
    shares: dict[str, float] | None,
    notes: list[str],
    fixed: Sequence[SwapPlayer] = (),
    plan: Sequence[tuple[str, int, float]] | None = None,
) -> tuple[list[list[SwapPlayer]], bool, float | None]:
    """Every GPP candidate: the best lineups of each shape in `shapes`, merged (a lineup that two shapes both
    produce is kept once). The searches run in `GPP_PLAN` order, each inside its share of the time left, and
    the contrarian cut is a third of the ownership of the stacks the earlier searches found. The plain stack
    search (with a bring-back) is the one that must finish; the others are best-effort inside their boxes (a
    shape that does not finish keeps what it had found and says so in `notes`). Returns the lineups, whether
    the plain stack search finished and the contrarian cutoff (summed stack ownership; None when there is no
    ownership or no stack). `fixed` players are in every lineup (`shape_lineups`); `plan` replaces `GPP_PLAN`
    (the rerun searches fewer lineups per quarterback)."""
    found: dict[frozenset[str], list[SwapPlayer]] = {}
    complete = True
    cutoff = None
    plan = [step for step in (GPP_PLAN if plan is None else plan) if step[0] in shapes]
    for n, (shape, per_qb, weight) in enumerate(plan):
        now = time.monotonic()
        weights_left = sum(w for _, _, w in plan[n:])
        box = now + max(0.0, deadline - now) * weight / weights_left
        kwargs = {}
        if shape == shapes_mod.CONTRARIAN:
            cutoff = (
                shapes_mod.contrarian_cutoff(list(found.values()), shares) if shares is not None else None
            )
            if shares is None or cutoff is None:
                continue  # no ownership published: the one note about it is written by suggest_builds
            kwargs = {"own": shares, "max_stack_own": cutoff}
        lineups, done = shapes_mod.shape_lineups(
            pool, shape, cap=cap, per_qb=per_qb, deadline=box, leaf_ok=leaf_ok, fixed=fixed, **kwargs
        )
        if shape == shapes_mod.STACK:
            complete = done
        elif not done:
            notes.append(
                f"The {shapes_mod.SHAPE_LABELS[shape]} search ran out of its time box and kept the best "
                "it had found (a longer `--seconds` lets it finish)."
            )
        for lu in lineups:
            found.setdefault(frozenset(p.name for p in lu), lu)
    if shares is not None and cutoff is None:
        cutoff = shapes_mod.contrarian_cutoff(list(found.values()), shares)
    return list(found.values()), complete, cutoff


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
    gpp_entries: int = dups.DEFAULT_GPP_ENTRIES,
    double_up_entries: int = dups.DEFAULT_DOUBLE_UP_ENTRIES,
    shapes: Sequence[str] = GPP_SHAPES,
    rank: str = DEFAULT_RANK,
    gpp_dup_model: dups.DupModel = dups.DUP_MODEL_GPP,
    cash_dup_model: dups.DupModel = dups.DUP_MODEL_CASH,
) -> SuggestReport:
    """Everything `dfs lineups suggest` computes. `tags` maps a name to his pool tag (Cash / GPP / Both).
    `field` (from `sim_inputs.resolve_lines`) scores against lines that move with the slate; without it the
    builds are scored against the fixed `cash_line` / `gpp_target`.

    `gpp_entries` is the size of the GPP the builds are for (the duplicates it expects are read against it);
    `double_up_entries` the size of a double-up, for the cash builds' duplication warning; `shapes` which
    GPP shapes to search (`lineup_shapes.SHAPES`); `rank` what orders the GPP candidates: "top1" (the
    default) the Top 1% chance alone, "share" that chance divided by 1 + the duplicates expected."""
    gpp_order = rank_key(rank)
    started = time.monotonic()
    deadline = started + time_limit
    cash_deadline = started + time_limit * CASH_SHARE_OF_TIME  # the GPP search gets the rest
    report = SuggestReport(rank=rank)
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
        # The Week 4 lesson, as a warning and never a filter: a double-up build many entries share.
        picks = annotate(
            picks,
            ownership_shares(specs, edge, field, "cash"),
            double_up_entries,
            objective=False,
            dup_model=cash_dup_model,
        )
        picked.extend(("cash", pick, "") for pick in picks)
        if len(picks) < cash_builds:
            report.notes.append(
                f"Only {len(picks)} cash build(s): the cash pool ({len(cash_pool)} players) does not hold "
                f"{cash_builds} legal lineups that differ by {MIN_DIFFERENCE}+ players."
            )

    if gpp_builds != 0:
        t = time.monotonic()
        shares = ownership_shares(specs, edge, field, "top")
        if shares is None:
            report.notes.append(
                "Ownership has not published, so GPP builds are ranked by Top 1% alone (no duplicate "
                "estimate, no contrarian stacks)."
            )
        lineups, done, cutoff = gpp_candidates(
            gpp_pool,
            cap=cap,
            deadline=deadline,
            leaf_ok=not_all_cash(tags),
            shapes=shapes,
            shares=shares,
            notes=report.notes,
        )
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
        scored = annotate(
            scored, shares, gpp_entries, objective=True, contrarian_at=cutoff, dup_model=gpp_dup_model
        )
        if gpp_builds is None:
            picks = choose_per_qb(scored, key=gpp_order)
        else:
            picks = choose(scored, gpp_builds, key=gpp_order, max_per_player=exposure_cap(gpp_builds))
        timed("choose", t)
        picked.extend(("gpp", pick, shapes_mod.shape_name(pick.players, pick.shapes)) for pick in picks)
        if not lineups:
            report.notes.append(
                "No stacked GPP lineup fits: a stack needs a QB and one of his teammates (a RB, WR or TE) "
                f"from your pool ({len(gpp_pool)} players)."
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

    report.builds = number_builds(picked, gpp_entries=gpp_entries, double_up_entries=double_up_entries)
    for build in report.builds:
        if build.kind == "cash" and build.e_dups is not None and build.e_dups >= dups.DUP_WARN_COPIES:
            report.notes.append(
                f"Cash build {build.number} is expected to be one of about {build.e_dups:.0f} identical "
                f"entries in a {double_up_entries:,}-entry double-up (ownership sum {build.own_sum:.0%}). "
                "Duplicates do not change a cash result, but every copy cashes or busts with you."
            )

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


def number_builds(
    picked: Sequence[tuple[str, Scored, str]],
    *,
    gpp_entries: int = dups.DEFAULT_GPP_ENTRIES,
    double_up_entries: int = dups.DEFAULT_DOUBLE_UP_ENTRIES,
) -> list[Build]:
    """The chosen lineups as `Build`s in display order (`display_key`), numbered 1..n down that order (Sam,
    2026-10-17: Copy builds' "1,4,6" must match what the Suggested tab shows). Which lineups were chosen is
    decided by the simulator's odds; this only orders and numbers them. A GPP build's title names its shape
    and the copies expected ("GPP build 5  ·  DET game stack  ·  ~0.3 dupes")."""
    return [
        build_from(number, kind, pick, name, gpp_entries=gpp_entries, double_up_entries=double_up_entries)
        for number, (kind, pick, name) in enumerate(sorted(picked, key=display_key), start=1)
    ]


def build_from(
    number: int,
    kind: str,
    pick: Scored,
    name: str,
    *,
    gpp_entries: int = dups.DEFAULT_GPP_ENTRIES,
    double_up_entries: int = dups.DEFAULT_DOUBLE_UP_ENTRIES,
) -> Build:
    """One `Build` with its title: "Cash build 2", or "GPP build 5  ·  DET game stack  ·  ~0.3 dupes" (`name`
    is the GPP build's shape name; a lineup with no stack says so)."""
    if kind == "cash":
        return _build(number, kind, f"Cash build {number}", pick, entries=double_up_entries)
    title = f"GPP build {number}  ·  {name or 'no stack'}" + (
        f"  ·  {dups.dups_label(pick.e_dups)}" if pick.e_dups is not None else ""
    )
    return _build(number, kind, title, pick, stack=name, entries=gpp_entries)


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


def _build(number: int, kind: str, title: str, pick: Scored, stack: str = "", entries: int = 0) -> Build:
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
        shapes=pick.shapes,
        own_sum=pick.own_sum,
        own_product=pick.own_product,
        e_dups=pick.e_dups,
        entries=entries if pick.e_dups is not None else 0,
        objective=pick.objective,
    )
