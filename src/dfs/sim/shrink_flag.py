"""The projection-shrink switches for `dfs backtest`, one flag each (the curse study's R8 candidates):

- `shrink_salary = true`: each player's CalPts pulled toward his position's salary line (`a + b * salary`);
- `shrink_tffb = true`: each player's CalPts pulled toward TFFB's own projection.

The pull is the curse study's (`research.curse.fit_shrink`): one weight per position, fitted by least squares
on realized points. A backtest of week N fits it on the weeks BEFORE N only (the study held out one week and
fitted on all the others, which in a backtest would be looking ahead), so week 2 is fitted on week 1 alone.
The builder then sees the shrunk projections and derives its specs and lines from them, as `curse.run_builder`
does; Model Check and the sheet are untouched (this only exists inside the backtest).

Importing this module is the whole hook: `dfs.backtest_settings.PLUGIN_MODULES` names it. Register it ahead of
any flag that sets `ctx.lines` (the ownership switches): the specs and lines are rebuilt from the shrunk edge.
"""

from __future__ import annotations

from dfs import backtest_settings as bs
from dfs.research import curse

SALARY_FLAG = "shrink_salary"
TFFB_FLAG = "shrink_tffb"

_TRAINING: dict[int, curse.WeekInputs] = {}  # per process: the weeks already read
_SOURCES: dict = {}


def _week_inputs(week: int) -> curse.WeekInputs:
    if week not in _TRAINING:
        if not _SOURCES:
            from dfs import results_update
            from dfs.sim import retro

            _SOURCES.update(
                scored=results_update.load_all_scored(curse.SEASON), contests=retro.load_contests()
            )
        lines = curse.lines_through(curse.SEASON, week)
        _TRAINING[week] = curse.week_inputs(
            week, lines=lines, scored=_SOURCES["scored"], contests=_SOURCES["contests"]
        )
    return _TRAINING[week]


def fitted_edge(edge, week: int, prior: str, weeks: dict | None = None):  # noqa: ANN001, ANN201
    """`(shrunk edge, weights)`: `edge` shrunk toward `prior` with weights fitted on `weeks` (default: every
    week before `week` that has inputs); `({}, None)`-style callers check `weights` is None for "no earlier
    weeks"."""
    if weeks is None:
        weeks = {}
        for w in range(1, week):
            try:
                weeks[w] = _week_inputs(w)
            except (FileNotFoundError, KeyError):  # a week with no snapshot or no cash line: not trained on
                continue
    if not weeks:
        return edge, None
    rows = curse.training_rows(weeks)
    fit = curse.fit_salary(rows) if prior == "salary" else None
    lam = curse.fit_shrink(rows, curse.prior_values(rows, prior, fit))
    return curse.shrunk_edge(edge, curse.prior_values(edge, prior, fit), lam), lam


def prepare(ctx, value, *, prior: str):  # noqa: ANN001, ANN201 - backtest_settings.BuildContext
    """The context with its projections shrunk toward `prior`, and its specs and lines left to be rebuilt from
    them. A falsy `value`, or a first week (nothing to fit on), is returned unchanged."""
    if not value:
        return ctx
    edge, lam = fitted_edge(ctx.edge, int(ctx.week), prior)
    if lam is None:
        ctx.notes.append(f"shrink_{prior}: no earlier weeks to fit on; projections as is")
        return ctx
    ctx.edge = edge
    ctx.specs = None
    ctx.lines = None
    ctx.notes.append(
        "shrink_" + prior + ": weights " + ", ".join(f"{k} {v:.2f}" for k, v in sorted(lam.items()))
    )
    return ctx


FLAGS = (
    bs.register(
        bs.Flag(
            name=SALARY_FLAG,
            doc="projections pulled toward the position's salary line (weights fitted on earlier weeks)",
            prepare=lambda ctx, value: prepare(ctx, value, prior="salary"),
        )
    ),
    bs.register(
        bs.Flag(
            name=TFFB_FLAG,
            doc="projections pulled toward TFFB's own (weights fitted on earlier weeks)",
            prepare=lambda ctx, value: prepare(ctx, value, prior="tffb"),
        )
    ),
)
