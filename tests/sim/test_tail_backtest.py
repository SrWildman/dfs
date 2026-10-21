"""The Top 1% replay's reading of its results: rates in percent and each adjustment's paired Brier gain."""

import numpy as np
import pandas as pd

from dfs.sim import tail_backtest as tb


def make_results(n=400, seed=0):
    rng = np.random.default_rng(seed)
    realized = rng.normal(125, 18, n)
    out = pd.DataFrame(
        {
            "season": 2024,
            "week": rng.integers(1, 18, n),
            "stacked": rng.random(n) < 0.5,
            "realized": realized,
            "projected": 125.0,
        }
    )
    for line in tb.LINES:
        true = 1 - 0.5 * (1 + np.vectorize(__import__("math").erf)((line - 125) / (18 * 2**0.5)))
        out[f"p{line:g}"] = true
        out[f"q{line:g}"] = np.clip(true * 1.4, 0, 1)  # an adjustment that over-states the tail
        out[f"d{line:g}"] = true
    return out


def test_summarize_reports_predicted_and_realized_rates_for_stacked_unstacked_and_all():
    s = tb.summarize(make_results())
    assert set(s["lineups"]) == {"stacked", "unstacked", "all"} and set(s["line"]) == set(tb.LINES)
    row = s[(s["line"] == 150.0) & (s["lineups"] == "all")].iloc[0]
    assert abs(row["shipped%"] - row["realized%"]) < 6
    assert row["all roles%"] > row["shipped%"]


def test_an_adjustment_that_over_states_the_tail_has_a_negative_brier_gain_and_an_identical_one_none():
    s = tb.summarize(make_results(2000)).set_index(["line", "lineups"])
    assert s.loc[(170.0, "all"), "all roles Brier gain x1e4"] < 0
    assert s.loc[(170.0, "all"), "depth roles Brier gain x1e4"] == 0
