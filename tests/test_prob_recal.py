"""Recalibrating Hit3x% / Boom% / Bust% on a season's results: the outcomes, the identity case, the shrinkage
and the held-out comparison. Synthetic rows drawn from the engine's own distribution; no results files."""

import numpy as np
import pandas as pd

from dfs import prob_recal as pr
from dfs import probabilities


def make_rows(n=900, scale=1.0, seed=0, weeks=(1, 2, 3, 4, 5, 6)):
    """WR / RB rows whose `DkActual` is a draw from the engine's distribution at `scale * CalPts`, with the
    probabilities as the sheet shows them (so scale=1 is a perfectly calibrated season)."""
    from dfs.model import distribution
    from dfs.sim import marginal

    rng = np.random.default_rng(seed)
    rows = []
    for position in ("WR", "RB"):
        proj = rng.uniform(5, 20, n // 2)
        salary = np.round(proj * 400 + rng.uniform(1500, 2500, n // 2), -2)
        m = marginal.marginals(position, proj * scale)
        u = rng.uniform(size=n // 2)
        actual = np.array([marginal.ppf(m, i, np.array([u[i]]))[0] for i in range(n // 2)])
        shown = pd.DataFrame(index=range(n // 2), columns=["CalPts", "Salary", "Position"])
        shown["CalPts"], shown["Salary"], shown["Position"] = proj, salary, position
        probs = probabilities.outcome_columns(shown)
        part = pd.DataFrame(
            {
                "week": rng.choice(weeks, n // 2),
                "Position": position,
                "Team": rng.choice(["KC", "DEN", "BUF", "SEA"], n // 2),
                "CalPts": proj,
                "Salary": salary,
                "DkActual": np.round(actual, 1),
            }
        ).join(probs[["Hit3x%", "Boom%", "Bust%"]])
        rows.append(part)
    del distribution
    return pd.concat(rows, ignore_index=True)


def test_outcomes_are_the_three_thresholds_per_thousand_dollars():
    f = pd.DataFrame({"DkActual": [12.0, 20.0, 5.0, np.nan], "Salary": [4000, 4000, 4000, 4000]})
    y = pr.outcomes(f)
    assert y["y_Hit3x%"].tolist()[:3] == [1.0, 1.0, 0.0]  # 3x4 = 12
    assert y["y_Boom%"].tolist()[:3] == [0.0, 1.0, 0.0]  # 4x4 = 16
    assert y["y_Bust%"].tolist()[:3] == [0.0, 0.0, 1.0]  # under 2x4 = 8
    assert y.iloc[3].isna().all()


def test_no_scale_and_no_platt_leave_the_shown_probabilities_alone():
    f = make_rows(200)
    shown = f[["Hit3x%", "Boom%", "Bust%"]]
    assert np.allclose(pr.apply_scale(f, {}).to_numpy(), shown.to_numpy(), atol=0.051)
    assert pr.apply_platt(shown, {}).equals(shown)
    same = pr.apply_platt(shown, {"Hit3x%": (0.0, 1.0, -1.5)})
    assert np.allclose(same["Hit3x%"], shown["Hit3x%"], atol=0.051)


def test_on_a_calibrated_season_the_fit_stays_near_no_change():
    f = pr.rated(make_rows(12000))
    fit = pr.fit(f)
    assert all(0.95 < c < 1.05 for c in fit.scale.values())
    cols = ["Hit3x%", "Boom%", "Bust%"]
    assert (pr.apply_platt(f[cols], fit.platt) - f[cols]).abs().mean().max() < 1.0  # points of probability


def test_a_projection_that_runs_high_gets_a_scale_below_one_and_shrinkage_keeps_it_short_of_the_raw_fit():
    rows = pr.rated(make_rows(1600, scale=0.8))
    raw = pr.fit(rows, prior_n=0)
    shrunk = pr.fit(rows, prior_n=pr.PRIOR_N)
    for position in ("WR", "RB"):
        assert raw.scale[position] < 0.9
        assert raw.scale[position] < shrunk.scale[position] < 1.0
    assert shrunk.n["scale:WR"] == (rows["Position"] == "WR").sum()


def test_the_scale_lowers_the_boom_and_raises_the_bust_the_way_a_high_projection_should():
    f = make_rows(200)
    low = pr.apply_scale(f, {"WR": 0.8, "RB": 0.8})
    assert (low["Boom%"] <= f["Boom%"] + 0.05).all() and (low["Bust%"] >= f["Bust%"] - 0.05).all()
    assert low["Boom%"].sum() < f["Boom%"].sum() and low["Bust%"].sum() > f["Bust%"].sum()


def test_evaluate_scores_the_later_weeks_on_a_fit_of_the_earlier_ones():
    f = make_rows(1800, scale=0.8)
    res = pr.evaluate(f, [([1, 2, 3, 4], [5, 6])])
    assert res["n"] == (f["week"].isin([5, 6])).sum()
    assert set(res["kinds"]) == {"scale", "platt", "scale_global"}
    # a season that is really 20% lower than projected: the scale correction helps on the held-out weeks
    assert res["kinds"]["scale"]["Bust%"]["better"] > 0
    assert res["kinds"]["scale"]["Hit3x%"]["better"] > 0
    text = pr.render(res, "split")
    assert "scale" in text and "Hit3x%" in text


def test_on_a_calibrated_season_no_correction_beats_the_shown_probabilities_by_much():
    res = pr.evaluate(make_rows(1800), pr.ROLLING_PLAN)
    for kind in res["kinds"].values():
        for m in kind.values():
            assert m["better"] < 2 * m["se"] + 1e-3


def test_the_fit_round_trips_through_its_json():
    fit = pr.fit(pr.rated(make_rows(600)))
    again = pr.Recal.from_json(fit.to_json())
    assert again.scale == fit.scale and again.n == fit.n
    assert {k: tuple(v) for k, v in again.platt.items()} == {k: tuple(v) for k, v in fit.platt.items()}
