"""The `portfolio` flag for `dfs backtest`: its options, its hold-out and what it hands the backtest."""

from types import SimpleNamespace

import pandas as pd
import pytest

from dfs import backtest_report as rep
from dfs import backtest_settings as bs
from dfs import portfolio_inputs as pi
from dfs.sim import portfolio_flag as flag


def ctx(week=5, flags=None):
    return bs.BuildContext(
        week=week,
        edge=pd.DataFrame(),
        tags={},
        now=None,
        cap=50000,
        metric="CalPts",
        settings=bs.Settings(flags=flags or {"portfolio": True}),
    )


def test_it_is_registered_through_the_plugin_list():
    assert "dfs.sim.portfolio_flag" in bs.PLUGIN_MODULES and bs.load_plugins() == []
    assert bs.FLAGS["portfolio"].choose is flag.choose and bs.FLAGS["portfolio"].assign is None


def test_options_default_to_the_retros_lead_and_refuse_typos():
    assert flag.options(True) == ("robust", flag.DEFAULT_WIDE, pi.DEFAULT_SIMS)
    assert flag.options({"objective": "top1", "wide": 0, "sims": 500}) == ("top1", 0, 500)
    with pytest.raises(bs.SettingsError, match="objective"):
        flag.options({"objective": "max"})
    with pytest.raises(bs.SettingsError, match="size"):
        flag.options({"size": 20})


def test_a_week_with_no_earlier_gpp_field_keeps_todays_pick(monkeypatch):
    monkeypatch.setattr(flag, "field_model_before", lambda week: None)
    c = ctx(week=2)
    gpp = bs.WeekContest("9", "S", "gpp", None, 3)
    assert flag.choose(c, [], [gpp], True) == {}
    assert any("no earlier week" in n for n in c.notes)
    assert flag.choose(ctx(), [], [gpp], False) == {}  # off
    assert flag.choose(ctx(), [], [bs.WeekContest("1", "D", "cash", None, 1)], True) == {}  # cash only


def test_it_plans_the_gpp_contests_on_the_earlier_weeks_field(monkeypatch):
    model = SimpleNamespace(weeks=(2, 4))
    monkeypatch.setattr(flag, "field_model_before", lambda week: model)
    seen = {}

    def plan(**kw):
        seen.update(kw)
        p = pi.PortfolioPlan(kw["week"], kw["objective"], "now")
        p.contests = [
            pi.PlannedContest("9", "S", 10.0, [["a"] * 9, None], [1, None]),
            pi.PlannedContest("8", "T", 0.0, [], [], why="skipped"),
        ]
        p.notes = ["one unfilled"]
        return p

    monkeypatch.setattr(flag.pi, "plan_portfolio", plan)
    c = ctx(flags={"portfolio": {"wide": 0, "objective": "top1"}})
    cs = [bs.WeekContest("9", "S", "gpp", "K", 2), bs.WeekContest("1", "D", "cash", "C", 1)]
    assert flag.choose(c, ["builds"], cs, {"wide": 0, "objective": "top1"}) == {"9": [["a"] * 9]}
    assert seen["covered"] == {"9": ("S", "K", 2)} and seen["model"] is model
    assert seen["wide"] == () and seen["objective"] == "top1" and seen["builds"] == ["builds"]
    assert any("weeks [2, 4]" in n for n in c.notes) and any("one unfilled" in n for n in c.notes)


def test_the_verdict_judges_an_entry_choosing_flag_as_entered():
    on = SimpleNamespace(settings=bs.Settings(flags={"portfolio": True}))
    assert rep.enters_differently(on)
    assert not rep.enters_differently(SimpleNamespace(settings=bs.Settings()))
