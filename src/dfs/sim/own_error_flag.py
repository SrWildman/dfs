"""The ownership switches for `dfs backtest` (session J's Flashback registry), one flag each:

- `ownership_mean = true`: the field's ownership is the fitted mean (TFFB's Own% through the stake and
  field-size curve, `own_error.MeanModel`) instead of Own% as is; no draws;
- `ownership_uncertainty = true`: the same mean, and the field's ownership drawn per simulation around it
  (`dfs.sim.own_error`, `docs/SIM.md` "Ownership uncertainty").

A backtest of week N uses the model fitted on the weeks BEFORE N only (`own_error_retro.model_before`, cached
per week), so it never sees its own week's ownership. They match `[sim.lines] ownership_mean` /
`ownership_uncertainty`.

Importing this module is the whole hook: `dfs.backtest_settings.PLUGIN_MODULES` names it.
"""

from __future__ import annotations

from dataclasses import replace

from dfs import backtest_settings as bs
from dfs import sim_inputs
from dfs.sim.field import with_ownership_draws

MEAN_FLAG = "ownership_mean"
DRAWS_FLAG = "ownership_uncertainty"


def prepare(ctx, value, *, draws: bool = True):  # noqa: ANN001, ANN201 - backtest_settings.BuildContext
    """The context with its field's ownership as the fitted mean and (`draws`) drawn per simulation. A falsy
    `value`, or a context with no field (ownership not published), is returned unchanged."""
    from dfs import backtest
    from dfs.sim import own_error_retro

    name = DRAWS_FLAG if draws else MEAN_FLAG
    if not value:
        return ctx
    if ctx.lines is None:  # the backtest derives the lines after the hooks; the field is needed now
        ctx = backtest.ensure_sim(ctx)
    lines = ctx.lines
    if lines is None or lines.field is None:
        return ctx
    model = own_error_retro.model_before(int(ctx.week))
    if model is None:
        ctx.notes.append(f"{name}: no earlier weeks to fit on; ownership taken as known")
        return ctx
    own = sim_inputs.ownership_by_name(ctx.edge)
    field = with_ownership_draws(lines.field, own, model, seed=sim_inputs.SEED, draws=draws)
    ctx.lines = replace(lines, field=field)
    ctx.notes.append(f"{name}: model fitted on weeks {list(model.weeks)}")
    return ctx


def prepare_mean(ctx, value):  # noqa: ANN001, ANN201
    return prepare(ctx, value, draws=False)


FLAGS = (
    bs.register(
        bs.Flag(
            name=MEAN_FLAG,
            doc="the field's ownership is the fitted mean (stake and field size), not Own% as is",
            prepare=prepare_mean,
        )
    ),
    bs.register(
        bs.Flag(
            name=DRAWS_FLAG,
            doc="the fitted mean, and the field's ownership drawn per simulation around it",
            prepare=prepare,
        )
    ),
)
