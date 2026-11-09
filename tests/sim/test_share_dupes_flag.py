"""Plan S check 3: the `share_dupes_fitted` backtest flag and the builder hooks it uses."""

from __future__ import annotations

from types import SimpleNamespace

import pandas as pd

from dfs import backtest
from dfs import backtest_settings as bs
from dfs import lineup_suggest as ls
from dfs.sim import dups
from dfs.sim import share_dupes_flag as sdf


def test_the_flag_is_registered_and_off_by_default():
    assert "dfs.sim.share_dupes_flag" in bs.PLUGIN_MODULES and bs.load_plugins() == []
    assert sdf.FLAG in bs.FLAGS
    assert sdf.FLAG not in bs.load(None).flags  # today's settings never turn it on
    ctx = SimpleNamespace(notes=[])
    assert sdf.prepare(ctx, False) is ctx and ctx.notes == []


def context(rank="share"):
    settings = SimpleNamespace(rank=rank)
    return SimpleNamespace(week=6, edge=pd.DataFrame({"Name": ["A", "B"]}), specs={"A": 1, "B": 2}, lines="l",
                           notes=[], settings=settings, builder_kwargs={})  # fmt: skip


def test_prepare_hands_the_builder_the_fitted_model_and_gppown(monkeypatch):
    model = dups.DupModel(scale=0.6, exponent=0.56)
    monkeypatch.setattr(sdf, "fit_before", lambda week, season: ("fo", model, 40000))
    monkeypatch.setattr(sdf, "gpp_own_by_name", lambda edge, fo: {"a": 0.3, "b": 0.1})
    monkeypatch.setattr(backtest, "ensure_sim", lambda ctx: ctx)
    out = sdf.prepare(context(), True)
    assert out.builder_kwargs == {"gpp_dup_model": model, "gpp_dup_shares": {"A": 0.3, "B": 0.1}}
    assert "fitted on 40,000 GPP entries before week 6" in out.notes[0]
    note = sdf.prepare(context(rank="top1"), True).notes[-1]
    assert "only show" in note


def test_nothing_to_fit_on_keeps_todays_model(monkeypatch):
    monkeypatch.setattr(sdf, "fit_before", lambda week, season: (None, None, 0))
    monkeypatch.setattr(backtest, "ensure_sim", lambda ctx: ctx)
    out = sdf.prepare(context(), True)
    assert out.builder_kwargs == {} and "DUP_MODEL_GPP" in out.notes[0]


def test_the_default_builder_passes_the_hooks_kwargs(monkeypatch):
    seen = {}

    def fake(*args, **kwargs):
        seen.update(kwargs)
        return SimpleNamespace(notes=[], builds=[])

    monkeypatch.setattr(ls, "suggest_builds", fake)
    s = SimpleNamespace(cash=0, gpp=None, seconds=1.0, rank="share")
    lines = SimpleNamespace(cash_line=1.0, gpp_target=2.0, field=None)
    ctx = SimpleNamespace(edge=None, tags={}, specs={}, now=None, cap=1, metric="m", n_sims=10, settings=s,
                          lines=lines, notes=[], builder_kwargs={"gpp_dup_shares": {"A": 0.1}})  # fmt: skip
    backtest.default_builder(ctx)
    assert seen["gpp_dup_shares"] == {"A": 0.1} and seen["rank"] == "share"
    ctx.builder_kwargs = {}
    seen.clear()
    backtest.default_builder(ctx)
    assert "gpp_dup_shares" not in seen  # today's builder is called exactly as before


def test_annotate_reads_copies_from_the_dup_shares_and_shapes_from_the_field():
    players = [SimpleNamespace(name=n, position="WR", team="T") for n in "ABCDEFGHI"]
    scored = [_scored(players)]
    field_shares = {n: 0.5 for n in "ABCDEFGHI"}
    own_shares = {n: 0.1 for n in "ABCDEFGHI"}
    model = dups.DupModel(scale=1.0, exponent=1.0)
    plain = ls.annotate(scored, field_shares, 1000, objective=True, dup_model=model)[0]
    other = ls.annotate(scored, field_shares, 1000, objective=True, dup_model=model, dup_shares=own_shares)[0]
    assert plain.e_dups == dups.expected_dups([0.5] * 9, 1000, exponent=1.0)
    assert other.e_dups == dups.expected_dups([0.1] * 9, 1000, exponent=1.0)
    assert other.objective > plain.objective  # fewer copies, the larger share
    assert other.shapes == plain.shapes


def _scored(players):
    import dataclasses

    fields = {f.name: f for f in dataclasses.fields(ls.Scored)}
    values = {}
    for name, f in fields.items():
        if name == "players":
            values[name] = players
        elif f.default is not dataclasses.MISSING:
            values[name] = f.default
        elif f.default_factory is not dataclasses.MISSING:  # type: ignore[misc]
            values[name] = f.default_factory()  # type: ignore[misc]
        else:
            values[name] = 0.01 if name.startswith("p_") else 0.0
    return ls.Scored(**values)


def test_the_retro_splits_single_entry_and_small_field_gpps():
    from dfs.sim import share_rank_retro as srr

    def rows(side_paid):
        out = []
        for week, contest, series, entries in (
            (4, "c1", "NFL $10 Hundo [Single Entry]", 100),
            (4, "c2", "NFL $4K Singleback [5 Entry Max]", 4756),
            (5, "c3", "NFL $8K Fair Catch [Single Entry]", 784),
            (5, "c4", "NFL GIANT $10 Double Up [Single Entry]", 3000),
        ):
            fmt = "cash" if "Double" in series else "gpp"
            for scope in ("entry", "any"):
                out.append(
                    {"side": "backtest", "week": week, "contest": contest, "series": series, "fmt": fmt,
                     "fee": 10.0, "entries": entries, "slot": 1, "scope": scope, "points": 150.0,
                     "paid": side_paid(contest), "cashed": 1.0, "top1": 0.0}
                )  # fmt: skip
        return pd.DataFrame(out)

    a = SimpleNamespace(rows=rows(lambda c: 20.0 if c != "c2" else 0.0))
    b = SimpleNamespace(rows=rows(lambda c: 10.0))
    t = srr.paired_table(a, b).set_index(["group", "scope"])
    assert t.loc[("all GPP", "as entered"), "contests"] == 3  # the double-up is left out
    assert t.loc[("single-entry", "as entered"), "contests"] == 2
    assert t.loc[("single-entry", "as entered"), "by week"] == 1.0  # +100% ROI points in both weeks
    assert t.loc[("small field", "any build"), "contests"] == 2
    assert t.loc[("all GPP", "as entered"), "weeks won"] == 1  # week 4: +100% and -100% average to 0
