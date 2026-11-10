"""`calls = ...` for `dfs backtest` (Plan M): builds made from the plays model's calls. Three values:

- `true` (M1): cash builds with every CORE locked and every AVOID banned (below);
- `"avoid"` (M1.5 a): every cash AVOID leaves the pool, nothing is locked (run it with `[builds] gpp = 0`);
- `"fade"` (M1.5 b): every GPP FADE leaves the pool, nothing is locked (run it with `[builds] cash = 0`).

The two ban-only values change nothing but the pool, so today's builder runs on it as is.

The week's calls are the plays backtest's (`research.plays_backtest`: the features as they stood at the lock,
the weights fitted on the weeks before it, read from `data/research/calls_weights/`; run
`python -m dfs.research.plays_backtest run` first, or the prior weights are used and the notes say so). Then:

- every AVOID leaves the pool (it is banned);
- every CORE joins it and is LOCKED into every cash build
  (`lineup_suggest.suggest_builds(cash_fixed=...)`), the best-scoring CORE first, as many as can share one
  legal lineup; if the locked set leaves no legal lineup the lowest-scoring lock is dropped and the search
  runs again.

The GPP builds are untouched (the settings that compare cash builds set `[builds] gpp = 0`). Importing this
module is the whole hook: `dfs.backtest_settings.PLUGIN_MODULES` names it.
"""

from __future__ import annotations

from dfs import backtest_settings as bs

FLAG = "calls"
_CONTEXT: dict = {}
MIN_FILL_SALARY = 3000  # dollars per open slot a locked set must leave room for


def _context():  # noqa: ANN202 - research.plays_backtest.Context, loaded once per process
    from dfs.research import plays_backtest as pb

    if "ctx" not in _CONTEXT:
        _CONTEXT["ctx"] = pb.Context.load(pb.SEASON, 18, log=lambda _m: None)
    return _CONTEXT["ctx"]


def week_calls(week: int):  # noqa: ANN201 - plays.Calls
    from dfs import plays
    from dfs.research import plays_backtest as pb

    weights = plays.load_weights(pb.SEASON, before_week=week)
    hw = pb.cached_week(_context(), week, own_lag=weights.own_lag if weights.own_lag.n else None)
    calls = plays.make_calls(hw.features, weights, season=pb.SEASON, week=week, at=hw.now, fade=hw.fade)
    return calls, weights


def feasible_locks(core: list, cap: int) -> list[str]:
    """The CORE names to lock, best score first, as many as fit one lineup's slots and the cap."""
    from dfs.late_swap_search import SwapPlayer
    from dfs.lineup_shapes import seat

    chosen: list = []
    for c in sorted(core, key=lambda c: -c.cash_score):
        trial = [*chosen, c]
        players = [SwapPlayer(x.name, x.position, x.team, x.opp, "", int(x.salary), 0.0) for x in trial]
        room = cap - sum(int(x.salary) for x in trial) - (9 - len(trial)) * MIN_FILL_SALARY
        if seat(players) is not None and room >= 0:
            chosen = trial
    return [c.name for c in chosen]


BAN_ONLY = {"avoid": ("cash", "AVOID"), "fade": ("gpp", "FADE")}


def mode(value) -> str:  # noqa: ANN001
    """`"lock"` (true), `"avoid"` or `"fade"`; anything else is refused."""
    if value is True:
        return "lock"
    if isinstance(value, str) and value.lower() in BAN_ONLY:
        return value.lower()
    raise ValueError(f'calls = {value!r}: use true, "avoid" or "fade"')


def banned(calls, value) -> set[str]:  # noqa: ANN001 - plays.Calls
    kind, label = BAN_ONLY[mode(value)]
    return {c.name for c in calls.calls if getattr(c, kind) == label}


def prepare(ctx: bs.BuildContext, value) -> bs.BuildContext:  # noqa: ANN001
    if not value:
        return ctx
    calls, weights = week_calls(int(ctx.week))
    if mode(value) != "lock":
        out = banned(calls, value)
        ctx.tags = {n: t for n, t in ctx.tags.items() if n not in out}
        ctx.notes.append(
            f"calls = {mode(value)} (weights through Week {weights.through}): banned "
            f"{len(out)}: {', '.join(sorted(out))}"
        )
        return ctx
    names = set(ctx.edge["Name"].astype(str))
    core = [c for c in calls.calls if c.cash == "CORE" and c.name in names]
    avoid = {c.name for c in calls.calls if c.cash == "AVOID"}
    tags = {n: t for n, t in ctx.tags.items() if n not in avoid}
    for c in core:
        tags.setdefault(c.name, "Cash")
    ctx.tags = tags
    locks = feasible_locks(core, ctx.cap)
    ctx.cash_fixed = locks  # type: ignore[attr-defined] - read by `build`
    ctx.notes.append(
        f"calls (weights through Week {weights.through}): locked {', '.join(locks) or 'nobody'}; "
        f"banned {len(avoid)} AVOID"
    )
    return ctx


def build(ctx: bs.BuildContext, value, inner):  # noqa: ANN001, ANN201
    """Today's builder with the CORE locks; a lock that leaves no legal cash lineup is dropped."""
    if not value or mode(value) != "lock":
        return inner(ctx)
    from dfs import backtest
    from dfs import lineup_suggest as ls

    ctx = backtest.ensure_sim(ctx)
    locks = list(getattr(ctx, "cash_fixed", []))
    s = ctx.settings
    while True:
        report = ls.suggest_builds(
            ctx.edge,
            ctx.tags,
            ctx.specs,
            now=ctx.now,
            cap=ctx.cap,
            cash_line=ctx.lines.cash_line,
            gpp_target=ctx.lines.gpp_target,
            metric=ctx.metric,
            cash_builds=s.cash,
            gpp_builds=s.gpp,
            n_sims=ctx.n_sims,
            time_limit=s.seconds,
            field=ctx.lines.field,
            rank=s.rank,
            cash_fixed=locks,
        )
        if any(b.kind == "cash" for b in report.builds) or not locks or not s.cash:
            break
        ctx.notes.append(f"calls: no legal cash lineup with {locks[-1]} locked; dropped that lock")
        locks = locks[:-1]
    ctx.notes += report.notes
    return report.builds


FLAG_DEF = bs.register(
    bs.Flag(
        name=FLAG,
        doc="builds from the plays model's calls: true = CORE locked + AVOID banned (cash); "
        '"avoid" = AVOID banned only (cash); "fade" = FADE banned only (GPP) (Plan M, shadow)',
        version="2",
        prepare=prepare,
        build=build,
    )
)
