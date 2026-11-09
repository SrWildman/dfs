"""`share_dupes_fitted`: the Top 1% share ranked with the `dfs contests dupes` copies model, for `dfs
backtest`.

Plan S, check 3. `--rank share` ranks a GPP build by `Top 1% / (1 + expected copies)`, and today the copies
are `dups.DUP_MODEL_GPP` (fitted 2026-10-18 on Weeks 2, 4 and 5 against the field's projected `top` shares).
The newer fit (`sim/dup_calibration.py`, `dfs contests dupes`) is on `GppOwn` and every week with standings.
This switch makes the backtest's builder use THAT kind of model, with no lookahead: for week N,

1. `field_own` is fitted on the standings of the weeks before N only
   (`field_own.training_frame(through=N-1)`);
2. its `GppOwn` is attached to each earlier week's pre-lock snapshot, and the copies model (`DupModel`,
   Poisson maximum likelihood, `dup_calibration.fit`) is fitted on every entry of those weeks' GPP standings;
3. week N's builds read their copies from that model on week N's own `GppOwn` (`suggest_builds(gpp_dup_model=,
   gpp_dup_shares=)`); the shapes and the field are untouched.

A week with nothing before it to fit on (no earlier GPP standings, or `field_own` cannot fit: it needs both a
cash and a GPP week) keeps today's `DUP_MODEL_GPP` and says so in the run's notes. Use it with `[builds] rank
= "share"`: the flag changes the copies, the rank decides whether copies matter. Default off; nothing live
reads it.

Importing this module is the whole hook (`backtest_settings.PLUGIN_MODULES` names it).
"""

from __future__ import annotations

import pandas as pd

from dfs import backtest_settings as bs
from dfs.player_join import normalize_name
from dfs.sim import dups

FLAG = "share_dupes_fitted"
MIN_ENTRIES = 1000  # fewer earlier GPP entries than this do not identify a (scale, exponent) fit

_CACHE: dict[tuple[int, int], tuple[object, dups.DupModel | None, int]] = {}


def gpp_own_by_name(edge: pd.DataFrame, model) -> dict[str, float]:  # noqa: ANN001 - field_own.FieldOwnModel
    """`{normalised name: GppOwn}` for an edge frame under `model` (blank rows left out)."""
    from dfs import field_own

    attached = field_own.attach(edge, model)
    col = pd.to_numeric(attached[field_own.GPP_COLUMN], errors="coerce")
    return {normalize_name(n): float(v) for n, v in zip(attached["Name"], col, strict=True) if v == v}


def fit_before(week: int, season: int) -> tuple[object, dups.DupModel | None, int]:
    """`(field_own model, copies model, entries fitted on)` from the standings of the weeks before `week`
    only; `(None, None, 0)` when there is nothing to fit on."""
    key = (season, week)
    if key in _CACHE:
        return _CACHE[key]
    from dfs import field_own, results_update
    from dfs.sim import contest_retro as cr
    from dfs.sim import dup_calibration as dc
    from dfs.sim import retro

    out: tuple[object, dups.DupModel | None, int] = (None, None, 0)
    train, _ = field_own.training_frame(season, through=week - 1)
    fo = field_own.fit(train) if not train.empty else None
    contests = [c for c in cr.load_real_contests(set(range(1, week))) if c.fmt == "gpp" and c.week < week]
    if fo is not None and contests:
        scored = results_update.load_all_scored(season)
        parts = []
        for w in sorted({c.week for c in contests}):
            path, _ = retro.edge_snapshot_before(w, season)
            own = gpp_own_by_name(retro.with_calpts(pd.read_csv(path), scored, w, season), fo)
            for c in (c for c in contests if c.week == w):
                keys = [dc._key(s) for s in c.standings.lineups["slots"]]  # noqa: SLF001
                parts.append(dc.entry_rows(keys, own, c.entries))
        rows = pd.concat(parts, ignore_index=True)
        if len(rows) >= MIN_ENTRIES:
            out = (fo, dc.fit(rows), len(rows))
    _CACHE[key] = out
    return out


def shares_for_specs(edge: pd.DataFrame, model, spec_ids) -> dict[str, float]:  # noqa: ANN001
    """`{spec id: GppOwn}` for the builder (a spec id is the player's edge `Name`); a player with no number is
    left out, and the copies estimate floors him (`dups.OWN_FLOOR`)."""
    own = gpp_own_by_name(edge, model)
    return {sid: own[normalize_name(sid)] for sid in spec_ids if normalize_name(sid) in own}


def prepare(ctx: bs.BuildContext, value) -> bs.BuildContext:  # noqa: ANN001
    if not value:
        return ctx
    from dfs.backtest import SEASON, ensure_sim

    ctx = ensure_sim(ctx)
    fo, model, n = fit_before(int(ctx.week), SEASON)
    if model is None:
        ctx.notes.append(f"{FLAG}: nothing before week {ctx.week} to fit on; today's DUP_MODEL_GPP")
        return ctx
    shares = shares_for_specs(ctx.edge, fo, ctx.specs.keys())
    if not shares:
        ctx.notes.append(f"{FLAG}: no GppOwn on week {ctx.week}'s snapshot; today's DUP_MODEL_GPP")
        return ctx
    ctx.builder_kwargs = {**ctx.builder_kwargs, "gpp_dup_model": model, "gpp_dup_shares": shares}
    ctx.notes.append(
        f"{FLAG}: copies = {model.scale:.3g} x entries x product^{model.exponent:.3f} on GppOwn "
        f"(fitted on {n:,} GPP entries before week {ctx.week})"
    )
    if ctx.settings.rank != "share":
        ctx.notes.append(f"{FLAG}: rank is {ctx.settings.rank!r}, so the copies only show, they do not rank")
    return ctx


FLAGS = (
    bs.register(
        bs.Flag(
            name=FLAG,
            doc="GPP copies from the contests-dupes model on GppOwn, fitted on earlier weeks (rank share)",
            prepare=prepare,
        )
    ),
)
