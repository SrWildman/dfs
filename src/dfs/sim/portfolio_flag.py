"""The portfolio switch for `dfs backtest` (session J's Flashback registry): `portfolio = true` chooses the
week's GPP entries as one set (`dfs lineups suggest --portfolio`, `portfolio_inputs.plan_portfolio`) instead
of the best build in every contest.

    [flags]
    portfolio = true                                     # robust, wide 8: the retro's lead candidate
    portfolio = { objective = "robust", wide = 8, sims = 4000 }

`objective` is one of `portfolio.OBJECTIVES`, `wide` the builder's noisy runs whose lineups join the builds
(`--portfolio-wide`; 0 = the builds only), `sims` the draws it chooses on. The defaults are the retro's
`robust_wide` arm with the live hook's wide pool (8 runs of `portfolio_inputs.WIDE_SECONDS`, not the curse
study's 24 longer ones). It is a `choose` hook: it runs once per week, right after the builds, on the same
context (the frame, the specs, the other flags' prepared field), and covers the GPP contests only (cash
entries keep today's pick, as live). The generated GPP field is fitted on the real fields of the weeks BEFORE
the backtested one (`field_model_before`), so a week never sees its own standings; a week with none before
it keeps today's pick and its notes say so.

Importing this module is the whole hook: `dfs.backtest_settings.PLUGIN_MODULES` names it.
"""

from __future__ import annotations

import functools
from collections.abc import Mapping

from dfs import backtest_settings as bs
from dfs import portfolio as pf
from dfs import portfolio_inputs as pi

FLAG = pi.FLAG
DEFAULT_OBJECTIVE = "robust"
DEFAULT_WIDE = 8
OPTION_KEYS = ("objective", "wide", "sims")


def options(value) -> tuple[str, int, int]:  # noqa: ANN001 - true or a table
    """`(objective, wide runs, sims)` from the flag's value: `true` for the defaults, or a table of them."""
    opts = dict(value) if isinstance(value, Mapping) else {}
    bad = set(opts) - set(OPTION_KEYS)
    if bad:
        raise bs.SettingsError(f"unknown portfolio option(s) {sorted(bad)}; allowed: {list(OPTION_KEYS)}")
    objective = str(opts.get("objective", DEFAULT_OBJECTIVE))
    if objective not in pf.OBJECTIVES:
        raise bs.SettingsError(f"portfolio objective must be one of {pf.OBJECTIVES}, not {objective!r}")
    return objective, int(opts.get("wide", DEFAULT_WIDE)), int(opts.get("sims", pi.DEFAULT_SIMS))


@functools.lru_cache(maxsize=8)
def field_model_before(week: int):  # noqa: ANN201 - sim.stacks_retro.FieldModel | None
    """The GPP field's construction rates and herd fitted on the real GPP fields of the weeks before `week`
    (None when there are none)."""
    from dfs.sim import contest_retro as cr
    from dfs.sim import stacks_retro

    earlier = [c for c in cr.load_real_contests() if c.week < week]
    if not any(c.fmt == "gpp" for c in earlier):
        return None
    _chalks, keys = stacks_retro.chalk_by_week(earlier)
    return stacks_retro.fit_field_model(earlier, keys)


def wide_pool(ctx: bs.BuildContext, runs: int) -> list[list]:
    """The builder's GPP lineups on `runs` noisy copies of the week's projections (the live `--portfolio-wide`
    pool, on the backtest's context)."""
    from dfs import lineup_suggest as ls

    def builder(noisy_edge, noisy_specs):  # noqa: ANN001, ANN202
        return ls.suggest_builds(
            noisy_edge, ctx.tags, noisy_specs, now=ctx.now, cap=ctx.cap, cash_line=ctx.lines.cash_line,
            gpp_target=ctx.lines.gpp_target, metric=ctx.metric, cash_builds=0, gpp_builds=20,
            n_sims=ctx.n_sims, time_limit=pi.WIDE_SECONDS, field=pi.with_specs(ctx.lines.field, noisy_specs),
            rank=ctx.settings.rank,
        ).builds  # fmt: skip

    return pi.wide_lineups(ctx.edge, ctx.specs, ctx.metric, builder, runs)


def choose(ctx, builds, contests, value):  # noqa: ANN001, ANN201 - see backtest_settings.Choose
    """`{contest id: [lineups]}` for the week's GPP contests, chosen as one set; {} when the flag is off or
    the week cannot be planned (its notes say why)."""
    if not value:
        return {}
    objective, runs, sims = options(value)
    gpp = [c for c in contests if c.fmt == "gpp"]
    if not gpp:
        return {}
    model = field_model_before(int(ctx.week))
    if model is None:
        ctx.notes.append(f"{FLAG}: no earlier week's GPP field to fit on; today's pick")
        return {}
    from dfs.sim import retro

    plan = pi.plan_portfolio(
        week=int(ctx.week), builds=builds, specs=ctx.specs, edge=ctx.edge, season=retro.SEASON,
        objective=objective, n_sims=sims, now=ctx.now, wide=wide_pool(ctx, runs) if runs > 0 else (),
        covered={c.contest_id: (c.name, c.kind, c.entries) for c in gpp}, model=model,
    )  # fmt: skip
    ctx.notes.append(f"{FLAG}: {objective}, wide {runs}, field fitted on weeks {list(model.weeks)}")
    ctx.notes += [f"{FLAG}: {n}" for n in plan.notes]
    if plan.portfolio:
        p, t = plan.portfolio, plan.today
        ctx.notes.append(
            f"{FLAG}: model E[$] {p['e_dollars']:.2f} vs today's {t['e_dollars']:.2f}, P(a top-1%) "
            f"{p['p_any_top1']:.1%} vs {t['p_any_top1']:.1%}"
        )
    return {c.contest_id: [n for n in c.lineups if n is not None] for c in plan.contests if not c.why}


FLAG_SPEC = bs.register(
    bs.Flag(
        name=FLAG,
        doc="the week's GPP entries chosen as one set (robust, wide pool: the portfolio retro's lead)",
        choose=choose,
    )
)
