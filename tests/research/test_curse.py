"""The optimizer's-curse study: its pure parts (no files, no builder runs)."""

import numpy as np
import pandas as pd
import pytest

from dfs.research import curse


def _rows(actual, cal, prior, position="WR"):
    return pd.DataFrame(
        {"Position": position, "CalPts": cal, "actual": actual, "Salary": 5000.0, "ProjPts": prior}
    ), pd.Series(prior, dtype=float)


def test_fit_shrink_recovers_the_weight():
    rng = np.random.default_rng(0)
    cal = rng.uniform(5, 25, 4000)
    prior = np.full_like(cal, 12.0)
    actual = 0.7 * cal + 0.3 * prior + rng.normal(0, 1, cal.size)
    rows, p = _rows(actual, cal, prior)
    assert curse.fit_shrink(rows, p)["WR"] == pytest.approx(0.3, abs=0.02)


def test_fit_shrink_is_clipped_to_zero_and_one():
    cal = np.array([10.0, 20.0, 30.0])
    rows, p = _rows(cal * 1.5 - 5, cal, np.full(3, 20.0))  # actual further from the prior than CalPts
    assert curse.fit_shrink(rows, p)["WR"] == 0.0
    rows, p = _rows(np.full(3, 20.0), cal, np.full(3, 20.0))
    assert curse.fit_shrink(rows, p)["WR"] == 1.0


def test_fit_salary_line_per_position():
    rows = pd.DataFrame(
        {
            "Position": ["QB"] * 3 + ["RB"] * 3,
            "Salary": [5000, 6000, 7000] * 2,
            "actual": [15, 17, 19, 5, 10, 15],
        }
    )
    fit = curse.fit_salary(rows)
    assert fit["QB"][1] == pytest.approx(0.002)
    assert fit["RB"][0] + fit["RB"][1] * 6000 == pytest.approx(10.0)


def test_shrunk_edge_moves_each_position_by_its_weight_and_keeps_blanks():
    edge = pd.DataFrame({"Position": ["QB", "WR", "WR"], "CalPts": [20.0, 10.0, np.nan]})
    out = curse.shrunk_edge(edge, pd.Series([10.0, 20.0, 5.0]), {"QB": 0.5, "WR": 0.1})
    assert out["CalPts"].tolist()[:2] == pytest.approx([15.0, 11.0])
    assert np.isnan(out["CalPts"].iloc[2])


def test_noisy_edge_is_mean_one_and_positive():
    edge = pd.DataFrame({"CalPts": np.full(20000, 10.0)})
    out = curse.noisy_edge(edge, 0.2, np.random.default_rng(1))
    assert out["CalPts"].mean() == pytest.approx(10.0, rel=0.01)
    assert (out["CalPts"] > 0).all()


def test_platt_identity_on_calibrated_odds_and_shrinks_overconfidence():
    rng = np.random.default_rng(2)
    p = rng.uniform(0.05, 0.95, 20000)
    a, b = curse.fit_platt(p, rng.uniform(size=p.size) < p)
    assert (a, b) == (pytest.approx(0.0, abs=0.06), pytest.approx(1.0, abs=0.06))
    y = rng.uniform(size=p.size) < 0.5 * p  # promised p, delivered half of it
    shown = curse.apply_platt(np.array([0.7]), curse.fit_platt(p, y))
    assert shown[0] == pytest.approx(0.35, abs=0.05)


def test_brier_ignores_unknown_outcomes():
    assert curse.brier(np.array([0.5, 1.0, 0.2]), np.array([1.0, 1.0, np.nan])) == pytest.approx(0.125)


def test_paired_and_ship_bar():
    per_week = pd.DataFrame(
        {
            "week": [1, 2, 3, 4] * 2,
            "variant": ["today"] * 4 + ["fix"] * 4,
            "kind": "cash",
            "realized": [100, 110, 120, 130, 105, 116, 124, 136],
        }
    )
    c = curse.paired(per_week, "realized", "fix")
    assert c["mean"] == pytest.approx(5.25) and c["better"] == 4
    assert curse.passes(c)
    assert not curse.passes({**c, "better": 2})
    assert curse.paired(per_week, "realized", "missing") == {}


def test_held_out_noise_never_picks_with_its_own_week():
    per_week = pd.DataFrame(
        {
            "week": [1, 1, 2, 2],
            "variant": ["noise_0.10", "noise_0.20"] * 2,
            "kind": "cash",
            "realized": [200.0, 100.0, 100.0, 110.0],  # week 1 alone would pick 0.10, week 2 alone 0.20
        }
    )
    out = curse.held_out_noise(per_week, "cash").set_index("week")
    assert out.loc[1, "chosen"] == "noise_0.20"
    assert out.loc[2, "chosen"] == "noise_0.10"


def test_with_projection_floors_at_a_positive_value():
    edge = pd.DataFrame({"CalPts": [5.0, 3.0]})
    assert curse.with_projection(edge, [-1.0, 4.0])["CalPts"].tolist() == [0.1, 4.0]


def test_prior_values_needs_a_salary_fit():
    with pytest.raises(ValueError):
        curse.prior_values(pd.DataFrame({"CalPts": [1.0]}), "salary")


def test_held_out_noise_ignores_the_replication():
    per_week = pd.DataFrame(
        {
            "week": [1, 1, 1, 2, 2, 2],
            "variant": ["noise_0.10", "noise_0.20", "noise_0.10_rep"] * 2,
            "kind": "cash",
            "realized": [100.0, 110.0, 999.0, 100.0, 90.0, 999.0],
        }
    )
    out = curse.held_out_noise(per_week, "cash")
    assert set(out["chosen"]) <= {"noise_0.10", "noise_0.20"}


def _summary():
    weeks = [1, 2, 3, 4]
    gaps = {"today": [-0.2, -0.1, -0.15, -0.2], "random": [-0.1, -0.1, -0.1, -0.1]}
    per_week = pd.DataFrame(
        [
            {"week": w, "variant": v, "kind": "cash", "cash_gap": g, "pts_gap": 10 * g}
            for v, gs in gaps.items()
            for w, g in zip(weeks, gs, strict=True)
        ]
    )
    good = {"weeks": 4, "mean": 0.1, "se": 0.02, "z": 5.0, "better": 4, "by_week": {}}
    flat = {**good, "mean": 0.0, "z": 0.0, "better": 2}
    fixes = {("cash", "shrink_salary"): {"realized": flat, "cashed": good}}
    fixes[("gpp", "shrink_salary")] = {"realized": flat, "top1": flat}
    return {"per_week": per_week, "fixes": fixes, "calibration": {"cash": {"gain": {"shift": flat}}}}


def test_refresh_from_reports_the_curse_the_selection_part_and_each_fix():
    out = curse.refresh_from(_summary(), [1, 2, 3, 4])
    m = out["metrics"]
    assert m["weeks"] == 4 and m["cash builds cash gap"] == pytest.approx(-0.1625)
    assert m["random cash gap"] == pytest.approx(-0.1)
    assert m["selection cash gap"] == pytest.approx(-0.0625)
    assert m["shrink_salary clears"] is True and m["display shift clears"] is False
    assert "shrink_tffb cash pts" not in m  # a fix with no runs is left out, not reported as zero
    assert "shrink_salary (cash rate)" in out["headline"][0]
    assert out["ship_bar"] == curse.SHIP_BAR


def test_refresh_summary_is_wired_for_its_season_only():
    out = curse.refresh_summary(curse.SEASON - 1, 6)
    assert out["metrics"] == {} and str(curse.SEASON) in out["headline"][0]


def test_a_noise_pass_counts_only_when_its_rerun_clears_too():
    s = _summary()
    good, flat = s["fixes"][("cash", "shrink_salary")]["cashed"], s["fixes"][("gpp", "shrink_salary")]["top1"]
    s["fixes"][("cash", "noise_cv")] = {"realized": flat, "cashed": good}
    s["fixes"][("cash", "noise_0.10_rep")] = {"realized": flat, "cashed": flat}
    out = curse.refresh_from(s, [1, 2, 3, 4])
    assert out["metrics"]["noise_cv clears"] is False
    assert any("its rerun does not" in line for line in out["headline"])
    assert "noise_cv" not in out["headline"][0]


def test_a_study_takes_its_cash_lines_from_the_cache_and_the_standings(monkeypatch):
    # Week 7 closed after the typed-lines cache was written: the cache alone made `Study` raise KeyError: 7.
    from dfs import results_update
    from dfs.sim import retro

    monkeypatch.setattr(results_update, "load_all_scored", lambda season: pd.DataFrame())
    monkeypatch.setattr(retro, "load_contests", lambda *a, **k: [])
    monkeypatch.setattr(curse, "results_lines", lambda *a, **k: {6: 128.38})
    monkeypatch.setattr(curse, "lines_through", lambda season, through: {6: 128.38, 7: 114.06})
    seen = {}

    def fake_week_inputs(week, *, lines, scored, contests):
        seen[week] = lines[week]
        return week

    monkeypatch.setattr(curse, "week_inputs", fake_week_inputs)
    curse.Study([6, 7])
    assert seen == {6: 128.38, 7: 114.06}
