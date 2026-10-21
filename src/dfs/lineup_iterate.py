"""`dfs lineups suggest --iterate`: rerun the Suggested builds around what Sam decided on the tab.

Nothing here touches a sheet: `cli._run_suggest` reads the tab (`sheet_suggested.parse_state`), hands the
`TabState` and the last round's `RoundRecord` in, and writes the returned report back. The engine is the same
as a fresh run's (`lineup_suggest`, `lineup_shapes`, `late_swap_search`): fixed slots, the exact search, the
simulator's odds, the same objectives (`--rank top1|share`, the duplicate model) and shapes.

**What a decision means** (the cell on each build's bar; blank is Keep):

- `Keep`: the build stays exactly as it is (his typed-in picks included) and is only rescored.
- `Rebuild`: the build is searched again, kind for kind, with its locked players fixed: the lock ticks and
  every name Sam typed over another player (an edit is a lock, and its cell gets the "your pick" tint). A GPP
  build whose QB is locked follows the usual stack rules (a bring-back optional, one to three teammates of any
  position).
- `Drop`: the build goes, and a new one may take its place (the tab is topped up to "Builds wanted").
- `Vary N`: the build stays and N alternatives that keep its locks are added, each at least
  `VARY_MIN_DIFFERENCE` players away from it and from each other. With nothing locked they keep the QB.

**An edit is checked, never guessed.** A typed name must be a player on the slate, fit the slot's position,
leave nobody in the build twice, no DST against his QB and one RB per game, and the nine must still fit the
salary cap. If not, the build is left as it was and its note says why. Locks beat the pool, the Never list and
the exposure caps: a locked player stays wherever he came from. A lock keeps the player IN the build; the FLEX
rule may seat him in another of his position's slots, so the ticks follow the name, not the row.

**The rules box** (`suggest_state.py` has the syntax) applies to every rebuilt and new build: Always-use
players are fixed in each (unless the locks leave no room), Never-use players are left out of the search, Max
exposure sets a player's cap on the GPP builds (`Stafford: 4` or `<= 4`: at most four and nothing more;
`= 4`: exactly four, new builds are steered to reach it; `>= 2`: at least two, steered; `more Stafford`: no
cap, preferred), stack rules reject a lineup, "Builds wanted" is how many of each kind
the tab should hold (blank: as many as it holds now; an empty tab: 3 cash, 8 GPP). New builds must differ from
every other build by `MIN_DIFFERENCE` players (or the `min unique N` of the Build rules line, which is never
relaxed; `max from one game N` rejects any lineup with more than N players from one game) and respect the
exposure cap (the default share, `MAX_EXPOSURE`, counted over all the GPP builds on the tab, the kept ones
included); a GPP top-up spreads over quarterbacks
(two builds a QB before a third).

**Your frame** (`my_frame.py`). The `edge` handed in is already your frame: the Adjust and Ownership lines
have changed the projections and Own% every search, simulation and duplicate estimate reads. Here they only
show: each build's note names the players in it whose numbers are yours ("Nacua +2 (yours)").

**A removed bring-back stays removed.** If Sam typed a non-bring-back over a build's bring-back, a rebuild of
it keeps none (the flag survives later rounds) unless the best build WITH one beats the best without by more
than `[suggest] bring_back_gain` (default 25%, relative, of the probability that kind ranks by); the build's
note says which.

**Speed.** Only rebuilt and new builds are searched; kept builds never are. Every candidate of the run is
simulated together in one pass, then the chosen builds and the kept ones are rescored together in a second (so
every number on the tab comes from one simulation). The searches share `ITERATE_TIME_LIMIT` seconds. A rerun
with locks takes a few seconds; a rebuild with no QB locked searches every QB (the halved `ITERATE_PLAN`).

Deterministic like the fresh run: no randomness, canonical tie-breaks, the same simulator seed."""

from __future__ import annotations

import time
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime

import pandas as pd

from dfs import lineup_shapes as shapes_mod
from dfs import lineup_suggest as ls
from dfs import my_frame
from dfs import suggest_state as st
from dfs.kickoff import parse_kickoff
from dfs.late_swap_search import SwapPlayer, conflicts, edge_players
from dfs.models import ROSTER_SLOTS
from dfs.sim import dups
from dfs.sim.field import FieldModel
from dfs.sim.simulate import PlayerSpec
from dfs.sim_inputs import N_SIMS, SEED

ITERATE_TIME_LIMIT = 12.0  # the searches of one rerun together (the simulations come on top)
BRING_BACK_GAIN = 0.25  # `[suggest] bring_back_gain`: a removed bring-back returns only for a gain this large
VARY_MIN_DIFFERENCE = 2  # a variation differs from its build (and the other variations) by this many players
DEFAULT_WANTED = {"cash": ls.DEFAULT_CASH_BUILDS, "gpp": 8}  # an empty tab with no "Builds wanted" line
# The GPP candidate plan when no QB is fixed (every QB is searched): half the lineups per QB of a fresh run.
ITERATE_PLAN = tuple((shape, max(4, per_qb // 2), weight) for shape, per_qb, weight in ls.GPP_PLAN)
KEPT, REBUILT, NEW = "kept", "rebuilt", "new"
GROUP = {KEPT: 0, REBUILT: 1, NEW: 2}  # the tab lists kept builds first, then rebuilt, then new ones
KINDS = ("cash", "gpp")


# ---------------------------------------------------------------------------------------------
# What the run works on
# ---------------------------------------------------------------------------------------------


@dataclass
class Entry:
    """One build of the new round while it is being put together."""

    kind: str  # "cash" or "gpp"
    origin: str  # KEPT, REBUILT or NEW
    players: tuple[SwapPlayer, ...]
    locked: tuple[str, ...] = ()
    yours: tuple[str, ...] = ()
    no_bring_back: bool = False
    decision: str = ""  # what the decision cell shows after this run
    was: int | None = None  # the number it had on the tab
    notes: list[str] = field(default_factory=list)  # the pieces of the "what changed" line, in order
    problem: bool = False
    adjusted: tuple[str, ...] = ()  # `Nacua +2 (yours)` for each player in it whose numbers are yours
    prior_names: tuple[str, ...] = ()  # who was in it before this run (the diff)
    prior: dict[str, float] = field(default_factory=dict)  # its numbers before this run
    scored: ls.Scored | None = None


@dataclass
class Job:
    """Something to search for: `want` new lineups of `kind` around the `fixed` players."""

    kind: str
    fixed: tuple[SwapPlayer, ...]
    want: int = 1
    entry: Entry | None = None  # a rebuild: the entry that is being replaced
    parent: Entry | None = None  # a variation: its build
    nth: int = 0  # which of the variations (1-based): for the notes
    replaces: list[int] = field(default_factory=list)  # the numbers of dropped builds this tops up

    @property
    def key(self) -> tuple[str, tuple[str, ...]]:
        """What its candidates depend on: the kind and who is fixed (jobs with one key share a search)."""
        return self.kind, tuple(sorted(p.name for p in self.fixed))


@dataclass
class Iterated:
    report: ls.SuggestReport
    records: list[st.BuildRecord]


def slot_takes(slot: str, player: SwapPlayer) -> bool:
    return player.position in shapes_mod.SKILL if slot == "FLEX" else player.position == slot


def lineup_problem(players: Sequence[SwapPlayer], cap: int) -> str | None:
    """Why nine players are not a legal build (a name twice, the cap, a DST against his QB, two RBs in a
    game), or None."""
    names = [p.name for p in players]
    if len(set(names)) != len(names):
        return "a player is in it twice"
    salary = sum(p.salary for p in players)
    if salary > cap:
        return f"the salary would be ${salary:,}, over the ${cap:,} cap"
    for p in players:
        if conflicts(p, [o for o in players if o is not p]):
            return f"{p.name} breaks a rule (a DST against his own QB, or two RBs in one game)"
    return None


def _norm(text: object) -> str:
    return " ".join(str(text or "").split()).casefold()


def _qb(players: Sequence[SwapPlayer]) -> str:
    return next((p.name for p in players if p.position == "QB"), "")


def _key(players: Sequence[SwapPlayer]) -> tuple[str, ...]:
    return tuple(sorted(p.name for p in players))


def tab_from_record(record: st.RoundRecord) -> st.TabState:
    """A round's builds as a `TabState` (for `--round N`): the names, the lock ticks and the rules text as
    they were written. The decisions are kept as written, but the run freezes every build."""
    builds = []
    for b in record.builds:
        locked = {_norm(n) for n in b.locked}
        builds.append(
            st.TabBuild(
                number=b.number,
                kind=b.kind,
                title=b.title,
                decision=b.decision,
                names=list(b.names),
                locked=[_norm(n) in locked for n in b.names],
            )
        )
    return st.TabState(round=record.round, rules=st.RulesText.from_labels(record.rules), builds=builds)


def kind_of(tb: st.TabBuild) -> str:
    kind = tb.kind.strip().casefold()
    if kind in KINDS:
        return kind
    return "gpp" if "gpp" in tb.title.casefold() else "cash"


# ---------------------------------------------------------------------------------------------
# Choosing
# ---------------------------------------------------------------------------------------------


def sort_key(key: Callable[[ls.Scored], object]) -> Callable[[ls.Scored], tuple]:
    """`key` with the canonical tie-break (`lineup_key`) on the end: the same order whatever came first."""

    def ranked(s: ls.Scored) -> tuple:
        k = key(s)
        return (*(k if isinstance(k, tuple) else (k,)), ls.lineup_key(s))

    return ranked


def pick_one(
    ordered: Sequence[ls.Scored],
    *,
    others: Sequence[tuple[Sequence[SwapPlayer], int]],
    uses: Counter,
    cap_of: Callable[[str], int | None],
    exempt: frozenset[str] = frozenset(),
    boost: Callable[[ls.Scored], int] | None = None,
    qb_counts: Counter | None = None,
    qb_max: int = 0,
) -> ls.Scored | None:
    """The first of `ordered` (best first) that differs from each of `others` by that entry's number of
    players, keeps every player (but the `exempt` ones) within his exposure cap (`cap_of`: None is no cap),
    and (with `qb_counts`) whose QB is in fewer than `qb_max` builds. With `boost`, the passing candidate with
    the most of it (the first of them): players Sam asked for more of."""
    best: ls.Scored | None = None
    best_boost = -1
    for s in ordered:
        names = {p.name for p in s.players}
        if any(len(names - {q.name for q in other}) < minimum for other, minimum in others):
            continue
        if any(
            (cap := cap_of(p.name)) is not None and uses[p.name] >= cap
            for p in s.players
            if p.name not in exempt
        ):
            continue
        if qb_counts is not None and qb_counts[_qb(s.players)] >= qb_max:
            continue
        if boost is None:
            return s
        b = boost(s)
        if b > best_boost:
            best, best_boost = s, b
    return best


# ---------------------------------------------------------------------------------------------
# The rerun
# ---------------------------------------------------------------------------------------------


def iterate_builds(
    edge: pd.DataFrame,
    tags: dict[str, str],
    specs: dict[str, PlayerSpec],
    tab: st.TabState,
    prior: st.RoundRecord | None,
    *,
    now: datetime,
    cap: int,
    cash_line: float,
    gpp_target: float,
    metric: str,
    n_sims: int = N_SIMS,
    seed: int = SEED,
    time_limit: float = ITERATE_TIME_LIMIT,
    field: FieldModel | None = None,
    gpp_entries: int = dups.DEFAULT_GPP_ENTRIES,
    double_up_entries: int = dups.DEFAULT_DOUBLE_UP_ENTRIES,
    shapes: Sequence[str] = ls.GPP_SHAPES,
    rank: str = ls.DEFAULT_RANK,
    bring_back_gain: float = BRING_BACK_GAIN,
    freeze: bool = False,
) -> Iterated:
    """The new round: `tab` is what Sam left on the Suggested tab, `prior` the last round on file (what the
    tab showed when it was written: how edits are told). `freeze` keeps every build as it is (`--round N`)."""
    started = time.monotonic()
    report = ls.SuggestReport()
    notes = report.notes

    def timed(phase: str, since: float) -> float:
        t_ = time.monotonic()
        report.phases[phase] = report.phases.get(phase, 0.0) + t_ - since
        return t_

    t = time.monotonic()
    by_name = edge_players(edge, metric)
    all_names = list(dict.fromkeys(str(n) for n in edge["Name"]))
    teams = set(edge["Team"].dropna().astype(str)) if "Team" in edge.columns else set()
    rules = st.parse_rules(tab.rules, all_names, teams)
    notes.extend(rules.problems)
    kickoff = {
        str(name): parse_kickoff(start)
        for name, start in zip(
            edge["Name"], edge.get("GameStart", pd.Series("", index=edge.index)), strict=True
        )
    }
    pools = {
        "cash": ls.pool_for(edge, tags, ls.CASH_TAGS, now=now, metric=metric, any_dst=True),
        "gpp": ls.pool_for(edge, tags, ls.GPP_TAGS, now=now, metric=metric, any_dst=True),
    }
    report.candidates = {kind: len(pool) for kind, pool in pools.items()}
    report.rules = tab.rules.by_label()
    shares = {
        "cash": ls.ownership_shares(specs, edge, field, "cash"),
        "gpp": ls.ownership_shares(specs, edge, field, "top"),
    }
    timed("candidates", t)

    entries, jobs, dropped = _read_tab(tab, prior, by_name, all_names, rules, cap, freeze, notes)
    # What the tab should hold: the line typed, else as many of each kind as it holds now (an empty tab: the
    # defaults). A rebuild replaces its own build; a dropped build is what a top-up replaces.
    held = Counter(e.kind for e in entries)
    if not freeze:
        for kind in KINDS:
            wanted = rules.wanted.get(
                kind, Counter(kind_of(b) for b in tab.builds)[kind] if tab.builds else DEFAULT_WANTED[kind]
            )
            if wanted > held[kind]:
                jobs.append(Job(kind, (), wanted - held[kind], replaces=sorted(dropped[kind])))
    always = [by_name[n] for n in rules.always if n in by_name]
    for name in rules.always:
        if name not in by_name:
            notes.append(
                f"Always use: {name} has no salary or projection on the slate, so he cannot be used."
            )
    _fix_jobs(jobs, always, cap, notes)

    # --- the searches -----------------------------------------------------------------------------------
    gpp_planned = sum(1 for e in entries if e.kind == "gpp") + sum(
        j.want for j in jobs if j.kind == "gpp" and j.entry is None
    )
    default_cap = ls.exposure_cap(max(gpp_planned, 1))
    never = frozenset(rules.never)
    keys: dict[tuple, Job] = {}
    for job in jobs:
        if job.want:
            keys.setdefault(job.key, job)
    deadline = time.monotonic() + time_limit
    candidates: dict[tuple, list[list[SwapPlayer]]] = {}
    cutoffs: list[float] = []
    for n, (key, job) in enumerate(keys.items()):
        t = time.monotonic()
        pool = _pool_with(pools[job.kind], job.fixed, never)
        box = t + max(0.0, deadline - t) / (len(keys) - n)
        leaf = _leaf(rules, tags, job.kind)
        if job.kind == "cash":
            lineups, done = ls.cash_lineups(pool, cap=cap, deadline=box, fixed=job.fixed, leaf_ok=leaf)
        else:
            plan = ls.GPP_PLAN if any(p.position == "QB" for p in job.fixed) else ITERATE_PLAN
            lineups, done, cutoff = ls.gpp_candidates(
                pool,
                cap=cap,
                deadline=box,
                leaf_ok=leaf,
                shapes=shapes,
                shares=shares["gpp"],
                notes=notes,
                fixed=job.fixed,
                plan=plan,
            )
            if cutoff is not None:
                cutoffs.append(cutoff)
        report.complete = report.complete and done
        candidates[key] = lineups
        timed("search", t)

    # --- every candidate simulated together ----------------------------------------------------------------
    t = time.monotonic()
    cutoff = max(cutoffs) if cutoffs else None

    def rate(lineups: list[list[SwapPlayer]]) -> dict[tuple[str, ...], ls.Scored]:
        if not lineups:
            return {}
        return {
            ls.lineup_key(s): s
            for s in ls.score(
                lineups,
                specs,
                kickoff,
                cash_line=cash_line,
                gpp_target=gpp_target,
                n_sims=n_sims,
                seed=seed,
                field=field,
            )
        }

    def annotated(kind: str, scored: list[ls.Scored]) -> list[ls.Scored]:
        return ls.annotate(
            scored,
            shares[kind],
            gpp_entries if kind == "gpp" else double_up_entries,
            objective=kind == "gpp",
            contrarian_at=cutoff if kind == "gpp" else None,
        )

    union = {_key(lu): lu for lineups in candidates.values() for lu in lineups}
    rated = rate(list(union.values()))
    scored_by_key = {
        key: annotated(key[0], [rated[k] for lu in lineups if (k := _key(lu)) in rated])
        for key, lineups in candidates.items()
    }
    timed("simulate", t)

    # --- choose: rebuilds first (by number), then variations, then the top-ups -----------------------------
    t = time.monotonic()
    metric_of = {
        "cash": lambda s: s.p_cash,
        "gpp": (lambda s: s.gpp_value) if rank == "share" else (lambda s: s.p_gpp),
    }
    sorting = {"cash": sort_key(ls.cash_key), "gpp": sort_key(ls.rank_key(rank))}
    final = list(entries)

    def stage(job: Job) -> tuple:
        anchor = job.entry or job.parent
        return (
            0 if job.entry else 1 if job.parent else 2,
            anchor.was or 0 if anchor else 0,
            job.kind != "cash",
            job.nth,
        )

    work = sorted((j for j in jobs if j.want), key=stage)
    waiting = {id(j.entry) for j in work if j.entry is not None}
    for job in work:
        ordered = sorted(scored_by_key.get(job.key, []), key=sorting[job.kind])
        free = len(ROSTER_SLOTS) - len(job.fixed)
        exempt = frozenset(p.name for p in job.fixed)
        replaced = job.entry
        if replaced is not None:
            waiting.discard(id(replaced))
        for _ in range(job.want):
            # Builds still waiting to be rebuilt are not in the way of this one: only their replacements are.
            current = [e for e in final if id(e) not in waiting and e is not replaced]
            uses = Counter(p.name for e in current if e.kind == job.kind for p in e.players)

            def others(
                minimum: int, current=current, free=free, job=job
            ) -> list[tuple[Sequence[SwapPlayer], int]]:
                return [(e.players, _minimum(e, job, minimum, free)) for e in current]

            def cap_of(name: str, kind=job.kind) -> int | None:
                if kind == "cash":
                    return rules.caps.get(name)  # only GPP builds are capped by default
                return None if name in rules.more else rules.caps.get(name, default_cap)

            args = {"uses": uses, "cap_of": cap_of, "exempt": exempt, "boost": _boost(rules, uses, job.kind)}

            def attempt(minimum: int, job=job, ordered=ordered, args=args, current=current, others=others):
                if job.entry is None and job.parent is None and job.kind == "gpp":
                    spread = Counter(_qb(e.players) for e in current if e.kind == "gpp")
                    got = pick_one(
                        ordered, others=others(minimum), qb_counts=spread, qb_max=ls.BUILDS_PER_QB, **args
                    )
                    if got is not None:
                        return got
                return _pick_with_bring_back(job, ordered, others(minimum), args, metric_of, bring_back_gain)

            chosen = attempt(rules.min_unique or ls.MIN_DIFFERENCE)
            relaxed = False
            # Your `min unique` is a rule, not a default: it is never relaxed.
            if (
                chosen is None and rules.min_unique is None
            ):  # the locks leave little room: still respects exposure
                chosen = attempt(0)
                relaxed = chosen is not None
            if chosen is None:
                _give_up(job, replaced, notes)
                break
            new = _entry_for(job, chosen, replaced, relaxed)
            final = [e for e in final if e is not replaced] + [new]
            replaced = None
    notes.extend(_exposure_notes(rules, final, by_name))
    for e in final:
        e.adjusted = tuple(
            text for p in e.players if (text := my_frame.tag(p.name, rules.adjust, rules.ownership))
        )
    timed("choose", t)

    # --- rescore everything together, order, number, explain ------------------------------------------------
    t = time.monotonic()
    rescored = rate([list(e.players) for e in final])
    for kind in KINDS:
        mine = [rescored[k] for e in final if e.kind == kind and (k := _key(e.players)) in rescored]
        rescored.update({ls.lineup_key(s): s for s in annotated(kind, mine)})
    for e in final:
        e.scored = rescored.get(_key(e.players)) or _unscored(e, kickoff)
    timed("rescore", t)

    def listed(e: Entry) -> tuple:
        assert e.scored is not None
        return (
            GROUP[e.origin],
            e.kind != "cash",
            _qb(e.players),
            -round(e.scored.projection, ls.VALUE_DECIMALS),
            e.scored.salary,
            ls.lineup_key(e.scored),
        )

    builds, records = [], []
    for number, e in enumerate(sorted(final, key=listed), start=1):
        assert e.scored is not None
        name = shapes_mod.shape_name(e.scored.players, e.scored.shapes) if e.kind == "gpp" else ""
        base = ls.build_from(
            number, e.kind, e.scored, name, gpp_entries=gpp_entries, double_up_entries=double_up_entries
        )
        locked = set(e.locked)
        build = replace(
            base,
            decision=e.decision,
            locked=tuple(p.name for p in e.scored.players if p.name in locked),
            yours=tuple(p.name for p in e.scored.players if p.name in set(e.yours)),
            note=_note(e, e.scored),
            problem=e.problem,
            origin=e.origin,
        )
        builds.append(build)
        records.append(_record(build, e))
    report.builds = builds
    for build in builds:
        if build.kind == "cash" and build.e_dups is not None and build.e_dups >= dups.DUP_WARN_COPIES:
            notes.append(
                f"Cash build {build.number} is expected to be one of about {build.e_dups:.0f} identical "
                f"entries in a {double_up_entries:,}-entry double-up (ownership sum {build.own_sum:.0%}). "
                "Duplicates do not change a cash result, but every copy cashes or busts with you."
            )
    if not report.complete:
        notes.append(
            "The time limit cut a search short: a better build may exist (raise it with `--seconds`)."
        )
    report.seconds = time.monotonic() - started
    return Iterated(report=report, records=records)


# ---------------------------------------------------------------------------------------------
# Reading the tab
# ---------------------------------------------------------------------------------------------


def _read_tab(
    tab: st.TabState,
    prior: st.RoundRecord | None,
    by_name: dict[str, SwapPlayer],
    all_names: list[str],
    rules: st.Rules,
    cap: int,
    freeze: bool,
    notes: list[str],
) -> tuple[list[Entry], list[Job], dict[str, list[int]]]:
    """Every build on the tab as an `Entry`, the jobs its decisions ask for (a rebuild, the variations of a
    Vary) and the numbers of the dropped builds by kind."""
    on_file: dict[int, st.BuildRecord] = {}
    if prior is not None and tab.round == prior.round:
        on_file = {b.number: b for b in prior.builds}
    elif prior is not None and tab.builds:
        notes.append(
            f"The tab says round {tab.round if tab.round is not None else '?'} but the state file's latest "
            f"is round {prior.round}: a typed name cannot be told from the last round's, so no edit counts "
            "as a lock (the ticks still do). Bring the round you mean back with `--round N`."
        )
    entries: list[Entry] = []
    jobs: list[Job] = []
    dropped: dict[str, list[int]] = {"cash": [], "gpp": []}
    for tb in sorted(tab.builds, key=lambda b: b.number):
        kind = kind_of(tb)
        entry, ok = _read_build(tb, on_file.get(tb.number), by_name, all_names, cap, kind, notes, rules)
        decision = st.parse_decision(tb.decision)
        if decision is None:
            typed = tb.decision.strip()
            notes.append(
                f"Build {tb.number}: the decision '{typed}' is not Keep, Rebuild, Drop or Vary N: the build "
                "is left as it was."
            )
            entry.notes.append(f"decision '{typed}' not understood, left as it was")
            entry.decision = typed
            entries.append(entry)
        elif freeze:
            entry.decision = tb.decision.strip()
            entries.append(entry)
        elif not ok or decision.kind == "keep":
            entry.decision = "Keep" if ok else ""
            entries.append(entry)
        elif decision.kind == "drop":
            dropped[kind].append(tb.number)
        elif decision.kind == "vary":
            entry.decision = "Keep"
            entries.append(entry)
            fixed = _fixed_players(entry, by_name) or tuple(p for p in entry.players if p.position == "QB")
            jobs.extend(Job(kind, fixed, parent=entry, nth=i + 1) for i in range(decision.n))
        else:
            entries.append(entry)
            jobs.append(Job(kind, _fixed_players(entry, by_name), entry=entry))
    return entries, jobs, dropped


def _fixed_players(entry: Entry, by_name: dict[str, SwapPlayer]) -> tuple[SwapPlayer, ...]:
    """The players of `entry` that are locked, as players (one that is not on the slate cannot be fixed)."""
    locked = set(entry.locked)
    return tuple(p for p in entry.players if p.name in locked and p.name in by_name)


def _placeholder(slot: str, name: str) -> SwapPlayer:
    return SwapPlayer(name, "RB" if slot == "FLEX" else slot, "", "", "", 0, 0.0)


def _read_build(
    tb: st.TabBuild,
    prior: st.BuildRecord | None,
    by_name: dict[str, SwapPlayer],
    all_names: list[str],
    cap: int,
    kind: str,
    notes: list[str],
    rules: st.Rules,
) -> tuple[Entry, bool]:
    """One tab build as an `Entry`: its players with Sam's valid edits applied (and counted as locks), or as
    it was when an edit is invalid (`ok` False: the build is then left alone, whatever it was told to do)."""
    typed = [str(n or "").strip() for n in tb.names]
    previous = list(prior.names) if prior is not None and len(prior.names) == len(ROSTER_SLOTS) else None
    edited = [
        i for i in range(len(ROSTER_SLOTS)) if previous is not None and _norm(previous[i]) != _norm(typed[i])
    ]

    def player(i: int, name: str) -> SwapPlayer:
        return by_name.get(name) or _placeholder(ROSTER_SLOTS[i], name)

    reasons: list[str] = []
    names = list(typed)
    for i in edited:
        slot, text = ROSTER_SLOTS[i], typed[i]
        name, why = st.resolve_name(text, all_names) if text else (None, "the cell is empty")
        found = by_name.get(name) if name is not None else None
        if name is None:
            reasons.append(f"{slot} '{text}': {why}")
        elif found is None:
            reasons.append(f"{slot} {name}: no salary or projection on the slate")
        elif not slot_takes(slot, found):
            takes = "an RB, WR or TE" if slot == "FLEX" else f"a {slot}"
            reasons.append(f"{name} is a {found.position}, and the {slot} slot takes {takes}")
        else:
            names[i] = name
    if edited and not reasons and (why := lineup_problem([player(i, n) for i, n in enumerate(names)], cap)):
        reasons.append(f"with your change {why}")
    if reasons and previous is not None:
        names = previous
    players = tuple(player(i, n) for i, n in enumerate(names))
    entry = Entry(
        kind=kind,
        origin=KEPT,
        players=players,
        was=tb.number,
        prior_names=tuple(previous or typed),
        prior=dict(prior.metrics) if prior is not None else {},
        no_bring_back=bool(prior.no_bring_back) if prior is not None else False,
    )
    ticked = {names[i] for i, v in enumerate(tb.locked[: len(ROSTER_SLOTS)]) if v}
    if reasons:
        entry.locked = tuple(sorted(ticked))
        entry.problem = True
        entry.notes.append("your change was not applied (" + "; ".join(reasons) + "): left as it was")
        notes.append(f"Build {tb.number}: " + "; ".join(reasons) + ". The build is left as it was.")
        return entry, False
    gone = [n for n in names if n not in by_name]
    if gone:
        entry.problem = True
        entry.notes.append(f"{', '.join(gone)} not on the slate: the build cannot be rescored")
        notes.append(f"Build {tb.number}: {', '.join(gone)} not on the slate; the build is left as typed.")
    mine = {names[i] for i in edited}
    locked = ticked | mine
    entry.locked = tuple(sorted(locked))
    entry.yours = tuple(sorted(((set(prior.yours) if prior is not None else set()) & locked) | mine))
    before = [by_name.get(n) for n in previous or []]
    qb = next((p for p in before if p is not None and p.position == "QB"), None)
    for i in edited:
        was = by_name.get(previous[i]) if previous is not None else None
        is_back = (
            qb is not None and was is not None and was.team == qb.opp and was.position in shapes_mod.SKILL
        )
        entry.notes.append(
            f"{'bring-back' if is_back else ROSTER_SLOTS[i]} → {names[i]} (yours, was {previous[i]})"
        )
    if edited:
        had = shapes_mod.has_bring_back([p for p in before if p is not None])
        has = shapes_mod.has_bring_back(list(players))
        entry.no_bring_back = (had and not has) or (entry.no_bring_back and not has)
    for p in players:
        if p.name in rules.never:
            entry.notes.append(f"holds {p.name}, who is on your Never use line (kept: he is in the build)")
    return entry, True


def _fix_jobs(jobs: list[Job], always: list[SwapPlayer], cap: int, notes: list[str]) -> None:
    """Add the Always-use players to each job's fixed players, the locks first, as many as seat beside them
    under the cap (locks win). A rebuild with nine locked has nothing to search."""
    unfit: set[str] = set()
    for job in jobs:
        fixed = list(job.fixed)
        for p in always:
            if p.name in {x.name for x in fixed}:
                continue
            trial = [*fixed, p]
            if shapes_mod.seat(trial) is not None and sum(x.salary for x in trial) <= cap:
                fixed.append(p)
            else:
                unfit.add(p.name)
        job.fixed = tuple(fixed)
        if job.entry is not None and len(job.fixed) >= len(ROSTER_SLOTS):
            job.want = 0
            job.entry.notes.append("all nine are fixed: nothing to rebuild")
    for name in sorted(unfit):
        notes.append(
            f"Always use {name} does not fit in every build beside its locked players (left out of those)."
        )


def _pool_with(
    pool: list[SwapPlayer], fixed: Sequence[SwapPlayer], never: frozenset[str]
) -> list[SwapPlayer]:
    """The candidates of one search: the pool minus the Never list, plus the fixed players (locks beat the
    pool and the Never list)."""
    keep = {p.name for p in fixed}
    out = [p for p in pool if p.name not in never or p.name in keep]
    have = {p.name for p in out}
    return out + [p for p in fixed if p.name not in have]


def most_from_one_game(players: Sequence[SwapPlayer]) -> int:
    """The most players of a lineup that share a game (a player with no game on file counts for none)."""
    return max(Counter(p.game for p in players if p.game).values(), default=0)


def _leaf(rules: st.Rules, tags: dict[str, str], kind: str) -> Callable[[Sequence[SwapPlayer]], bool] | None:
    """The whole-lineup test a finished candidate must pass: the stack rules, and (GPP) never nine players the
    pool marks Cash only."""
    tests: list[Callable[[Sequence[SwapPlayer]], bool]] = []
    if rules.stacks:
        tests.append(rules.stacks.ok)
    if rules.max_per_game is not None:
        tests.append(lambda players: most_from_one_game(players) <= rules.max_per_game)
    if kind == "gpp":
        tests.append(ls.not_all_cash(tags))
    if not tests:
        return None
    return lambda players: all(test(players) for test in tests)


# ---------------------------------------------------------------------------------------------
# Choosing and explaining
# ---------------------------------------------------------------------------------------------


def _minimum(other: Entry, job: Job, minimum: int, free: int) -> int:
    """How many players a new lineup must differ from `other` by: a variation and its build (and its sibling
    variations) `VARY_MIN_DIFFERENCE`, everyone else `minimum`; never more than the free slots allow."""
    family = job.parent is not None and (
        other is job.parent or (other.origin == NEW and other.was == job.parent.was)
    )
    wanted = min(minimum, VARY_MIN_DIFFERENCE) if family else minimum
    return min(wanted, free)


def _boost(rules: st.Rules, uses: Counter, kind: str) -> Callable[[ls.Scored], int] | None:
    """`more Name`, and `Name: = N` / `>= N` while he is below N: a GPP candidate holding more of those
    players is preferred. A bare `Name: N` is only a cap and steers nothing."""
    wanted = set(rules.more) | {n for n, target in rules.floors.items() if uses[n] < target}
    if not wanted or kind != "gpp":
        return None
    return lambda s: sum(1 for p in s.players if p.name in wanted)


def _pick_with_bring_back(job, ordered, others, args, metric_of, gain):  # noqa: ANN001, ANN202
    """The pick for a job. For a rebuild whose bring-back Sam removed: the best build WITHOUT one, unless the
    best with one beats it by more than `gain` (relative); the verdict goes on the entry's notes."""
    entry = job.entry
    best = pick_one(ordered, others=others, **args)
    if entry is None or not entry.no_bring_back or best is None:
        return best
    without = pick_one(
        [s for s in ordered if not shapes_mod.has_bring_back(s.players)], others=others, **args
    )
    if without is None:
        entry.notes.append("no build without a bring-back fits, so one is back")
        return best
    if not shapes_mod.has_bring_back(best.players):
        entry.notes.append("no bring-back, as you left it")
        return best
    measure = metric_of[job.kind]
    base, top = measure(without), measure(best)
    rel = (top / base - 1) if base > 0 else float("inf")
    if rel > gain:
        entry.no_bring_back = False
        entry.notes.append(
            f"the bring-back is back: the best build with one is {rel:+.0%} better than the best without, "
            f"over the {gain:.0%} it takes to overrule you"
        )
        return best
    entry.notes.append(
        f"no bring-back, as you left it (the best with one is {rel:+.0%} better; it takes {gain:.0%})"
    )
    return without


def _entry_for(job: Job, chosen: ls.Scored, replaced: Entry | None, relaxed: bool) -> Entry:
    """The `Entry` a job's pick becomes: a rebuild keeps its locks, "your picks" and bring-back flag; a
    variation keeps its build's locks; a top-up starts clean."""
    names = {p.name for p in chosen.players}
    entry = Entry(kind=job.kind, origin=NEW, players=chosen.players)
    if replaced is not None:
        locked = tuple(n for n in replaced.locked if n in names)
        entry.origin, entry.was, entry.locked = REBUILT, replaced.was, locked
        entry.yours = tuple(n for n in replaced.yours if n in locked)
        entry.no_bring_back = replaced.no_bring_back and not shapes_mod.has_bring_back(chosen.players)
        entry.prior_names, entry.prior = replaced.prior_names, replaced.prior
        fixed = len(job.fixed)
        entry.notes = [
            f"rebuilt around {fixed} fixed player{'' if fixed == 1 else 's'}"
            if fixed
            else "rebuilt, nothing fixed",
            *replaced.notes,
        ]
    elif job.parent is not None:
        entry.locked = tuple(sorted(n for n in job.parent.locked if n in names))
        entry.was = job.parent.was
        differ = len(names - {p.name for p in job.parent.players})
        entry.notes.append(f"variation {job.nth} of build {job.parent.was}: {differ} players differ")
    else:
        replacing = f", replacing dropped build {', '.join(map(str, job.replaces))}" if job.replaces else ""
        entry.notes.append("new build" + replacing)
    if relaxed:
        entry.notes.append(
            "differs from another build by fewer players than usual (the locks leave little room)"
        )
    return entry


def _give_up(job: Job, replaced: Entry | None, notes: list[str]) -> None:
    if replaced is not None:
        what, detail = "rebuild", f"build {replaced.was}"
        replaced.notes.append("could not be rebuilt: left as it was")
    elif job.parent is not None:
        what, detail = "variation", f"build {job.parent.was}"
    else:
        what, detail = "new build", f"a {job.kind.upper() if job.kind == 'gpp' else 'cash'} build"
    notes.append(
        f"No {what} for {detail}: no legal lineup keeps its fixed players and also respects the rules and "
        "the exposure caps."
    )


def _exposure_notes(rules: st.Rules, final: list[Entry], by_name: dict[str, SwapPlayer]) -> list[str]:
    out = []
    uses = Counter(p.name for e in final if e.kind == "gpp" for p in e.players)
    for name, cap in sorted(rules.caps.items()):
        if uses[name] > cap:
            out.append(
                f"{name} is in {uses[name]} GPP builds, over the {cap} you asked for (a lock or a Keep)."
            )
    for name, floor in sorted(rules.floors.items()):
        if uses[name] < floor and name in by_name:
            out.append(f"{name} is in {uses[name]} GPP builds; you asked for {floor} and no more fit.")
    return out


def _unscored(entry: Entry, kickoff: dict[str, datetime | None]) -> ls.Scored:
    """A build the simulator could not rate (a player it has no spec for): shown with its last numbers."""
    seated = (
        ls.seat(list(entry.players), kickoff) if len(entry.players) == len(ROSTER_SLOTS) else entry.players
    )
    m = entry.prior
    entry.notes.append("could not be rescored: the numbers are from the last round")
    return ls.Scored(
        players=tuple(seated),
        projection=sum(p.value for p in entry.players),
        salary=sum(p.salary for p in entry.players),
        p_cash=float(m.get("p_cash", 0.0)),
        p_gpp=float(m.get("p_gpp", 0.0)),
        typical=float(m.get("typical", 0.0)),
        big_night=float(m.get("big_night", 0.0)),
    )


def _change(old: Sequence[str], new: Sequence[str]) -> str:
    gone, came = sorted(set(old) - set(new)), sorted(set(new) - set(old))
    if not gone and not came:
        return "same nine players"

    def few(names: list[str]) -> str:
        return ", ".join(names[:3]) + (f" +{len(names) - 3} more" if len(names) > 3 else "")

    return f"out {few(gone)}; in {few(came)}"


def _note(entry: Entry, scored: ls.Scored) -> str:
    """The build's one-line "what changed": origin, your edits, the rules' effect, who moved, the odds."""
    parts = list(entry.notes)
    if entry.origin == KEPT and not parts:
        parts.append("kept as it was")
    parts.extend(entry.adjusted)
    if entry.origin == REBUILT and entry.prior_names:
        parts.append(_change(entry.prior_names, [p.name for p in scored.players]))
    old = entry.prior
    if entry.kind == "cash" and "p_cash" in old and round(old["p_cash"], 2) != round(scored.p_cash, 2):
        parts.append(f"Cash % {old['p_cash']:.0%} → {scored.p_cash:.0%}")
    if entry.kind == "gpp" and "p_gpp" in old and round(old["p_gpp"], 3) != round(scored.p_gpp, 3):
        parts.append(f"Top 1% {old['p_gpp']:.1%} → {scored.p_gpp:.1%}")
    return "; ".join(parts)


def metrics_of(build: ls.Build) -> dict[str, float]:
    """The numbers a round keeps for a build (the next round's "Cash % 61% → 64%" reads them)."""
    return {
        "p_cash": round(build.p_cash, 6),
        "p_gpp": round(build.p_gpp, 6),
        "projection": round(build.projection, 4),
        "salary": build.salary,
        "typical": round(build.typical, 3),
        "big_night": round(build.big_night, 3),
    }


def _record(build: ls.Build, entry: Entry) -> st.BuildRecord:
    return st.BuildRecord(
        number=build.number,
        kind=build.kind,
        title=build.title,
        names=[p.name for p in build.players],
        decision=build.decision,
        locked=list(build.locked),
        yours=list(build.yours),
        no_bring_back=entry.no_bring_back,
        origin=build.origin,
        note=build.note,
        metrics=metrics_of(build),
    )


def records_from_report(report: ls.SuggestReport) -> list[st.BuildRecord]:
    """A fresh run's builds as round records (no decisions, no locks)."""
    return [
        st.BuildRecord(
            number=b.number,
            kind=b.kind,
            title=b.title,
            names=[p.name for p in b.players],
            origin=b.origin,
            metrics=metrics_of(b),
        )
        for b in report.builds
    ]
