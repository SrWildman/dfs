"""The backtest flags for the projection shrink (curse R8) and the GPP-only ownership draws."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from types import SimpleNamespace

import numpy as np
import pandas as pd

from dfs import backtest_settings as bs
from dfs.research import curse
from dfs.sim import shrink_flag as sf


def edge(n=40, seed=0):
    rng = np.random.default_rng(seed)
    sal = rng.integers(3000, 9000, n).astype(float)
    cal = sal / 700 + rng.normal(0, 1.5, n)
    return pd.DataFrame(
        {
            "Name": [f"p{i}" for i in range(n)],
            "Position": ["WR"] * n,
            "Salary": sal,
            "CalPts": cal,
            "ProjPts": cal + rng.normal(0, 1, n),
        }
    )


def weeks_with_actuals(k=3):
    out = {}
    for w in range(1, k + 1):
        e = edge(seed=w)
        actual = {n: float(s / 900) for n, s in zip(e["Name"], e["Salary"], strict=True)}
        out[w] = SimpleNamespace(edge=e, actual=actual)
    return out


def test_the_plugin_registers_both_shrink_switches():
    assert "dfs.sim.shrink_flag" in bs.PLUGIN_MODULES and bs.load_plugins() == []
    assert {"shrink_salary", "shrink_tffb", "ownership_uncertainty_gpp"} <= set(bs.FLAGS)


def test_a_week_is_shrunk_toward_the_salary_line_with_weights_from_earlier_weeks():
    weeks = weeks_with_actuals()
    e = edge(seed=9)
    shrunk, lam = sf.fitted_edge(e, 4, "salary", weeks)
    assert 0.0 <= lam["WR"] <= 1.0 and lam["WR"] > 0.3  # the actuals follow salary, the CalPts are noisy
    rows = curse.training_rows(weeks)
    a, b = curse.fit_salary(rows)["WR"]
    line = a + b * e["Salary"]
    # every player moves toward the line, never past it
    assert (abs(shrunk["CalPts"] - line) <= abs(e["CalPts"] - line) + 1e-9).all()


def test_the_first_week_has_nothing_to_fit_on():
    e = edge()
    out, lam = sf.fitted_edge(e, 1, "tffb", {})
    assert lam is None and out is e


def test_prepare_replaces_the_projections_and_leaves_the_lines_to_be_rebuilt(monkeypatch):
    shrunk = edge(seed=1).assign(CalPts=1.0)
    monkeypatch.setattr(sf, "fitted_edge", lambda e, week, prior, weeks=None: (shrunk, {"WR": 0.5}))
    ctx = SimpleNamespace(week=5, edge=edge(), specs={"x": 1}, lines="old", notes=[])
    out = sf.prepare(ctx, True, prior="salary")
    assert out.edge is shrunk and out.specs is None and out.lines is None
    assert out.notes == ["shrink_salary: weights WR 0.50"]
    assert sf.prepare(SimpleNamespace(), False, prior="salary") is not None  # a falsy value changes nothing


def test_no_earlier_weeks_leaves_the_context_alone(monkeypatch):
    monkeypatch.setattr(sf, "fitted_edge", lambda e, week, prior, weeks=None: (e, None))
    ctx = SimpleNamespace(week=1, edge=edge(), specs="s", lines="l", notes=[])
    out = sf.prepare(ctx, True, prior="tffb")
    assert out.specs == "s" and out.lines == "l" and "no earlier weeks" in out.notes[0]


@dataclass
class Ctx:
    week: int
    settings: bs.Settings
    lines: str
    notes: list = field(default_factory=list)


def test_the_gpp_only_ownership_flag_builds_cash_on_the_plain_field_and_gpp_on_the_drawn_one(monkeypatch):
    from dfs import backtest
    from dfs.sim import own_error_flag as flag

    seen = []

    def inner(c):
        seen.append((c.settings.cash, c.settings.gpp, c.lines))
        return [f"cash:{c.settings.cash}"] if c.settings.cash else [f"gpp:{c.settings.gpp}"]

    ctx = Ctx(6, bs.Settings(cash=3, gpp=12), "plain")
    monkeypatch.setattr(backtest, "ensure_sim", lambda c: c)
    monkeypatch.setattr(flag, "prepare", lambda c, v, draws=True: replace(c, lines="drawn"))
    assert flag.build_gpp_only(ctx, True, inner) == ["cash:3", "gpp:12"]
    assert seen == [(3, 0, "plain"), (0, 12, "drawn")]
    seen.clear()
    assert flag.build_gpp_only(ctx, False, inner) == ["cash:3"]  # off: today's builder, untouched
    assert seen == [(3, 12, "plain")]
