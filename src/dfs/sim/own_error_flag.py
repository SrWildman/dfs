"""The `ownership_uncertainty` switch for `dfs backtest` (session J's Flashback registry).

`[flags] ownership_uncertainty = true` in a backtest settings file scores the week's builds against fields
whose ownership is drawn per simulation (`dfs.sim.own_error`, `docs/SIM.md` "Ownership uncertainty"), with
the model fitted on the weeks BEFORE the backtested one only (`own_error_retro.model_before`, cached per
week), so a backtest never sees its own week's ownership. `ownership_uncertainty = "mean"` swaps in the
fitted mean without the draws (to tell the two effects apart).

The registry (`dfs.backtest_settings`) lives on the Flashback branch; on a branch without it this module
registers nothing. Importing it is the whole hook: the Flashback `PLUGIN_MODULES` names
`dfs.sim.own_error_flag`.
"""

from __future__ import annotations

from dataclasses import replace

from dfs import sim_inputs
from dfs.sim.field import with_ownership_draws

FLAG_NAME = "ownership_uncertainty"


def prepare(ctx, value):  # noqa: ANN001, ANN201 - backtest_settings.BuildContext in, the same out
    """Swap the context's field for one drawn per simulation (`value` true), or for the fitted mean only
    (`value` "mean"). A context with no field (ownership not published) is returned unchanged."""
    from dfs.sim import own_error_retro

    lines = getattr(ctx, "lines", None)
    if not value or lines is None or lines.field is None:
        return ctx
    model = own_error_retro.model_before(int(ctx.week))
    if model is None:
        ctx.notes.append("ownership_uncertainty: no earlier weeks to fit on; ownership taken as known")
        return ctx
    own = sim_inputs.ownership_by_name(ctx.edge)
    field = with_ownership_draws(lines.field, own, model, seed=sim_inputs.SEED, draws=value != "mean")
    ctx.lines = replace(lines, field=field)
    ctx.notes.append(f"ownership_uncertainty ({value}): model fitted on weeks {list(model.weeks)}")
    return ctx


try:  # the Flashback registry is on session J's branch
    from dfs import backtest_settings as _bs
except ImportError:  # pragma: no cover - a branch without `dfs backtest`
    FLAG = None
else:
    FLAG = _bs.register(
        _bs.Flag(
            name=FLAG_NAME,
            doc="the field's ownership drawn per simulation around the fitted mean (true), or the mean only "
            "(mean)",
            prepare=prepare,
        )
    )
