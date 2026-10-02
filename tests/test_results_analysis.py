"""The 3a-3e tables: thin marker, intervals, pinball loss, and each question's table on toy data."""

import math

import numpy as np
import pandas as pd
import pytest

from dfs import results_analysis as ra


def _row(position="WR", proj=10.0, actual=10.0, **kw):
    base = {
        "week": 3,
        "Position": position,
        "ProjPts": proj,
        "DkActual": actual,
        "Ceiling": proj * 1.8,
        "Salary": 5000,
        "Val": proj / 5,
        "ValAdj": 50.0,
        "AggPts": proj,
        "SleeperPts": float("nan"),
        "FantasyProsPts": float("nan"),
        "Flags": "",
        "RosterablePool": True,
        "Status": "scored",
    }
    return {**base, **kw}


def _frame(rows):
    return pd.DataFrame(rows)


def test_thin_is_marked_below_thirty_and_not_at_thirty():
    assert ra.is_thin(29) is True
    assert ra.is_thin(30) is False
    for n, thin in ((29, True), (30, False)):
        table = ra.accuracy_by_position(_frame([_row(proj=10 + i % 5, actual=9 + i % 7) for i in range(n)]))
        assert bool(table[table["Position"] == "WR"].iloc[0]["Thin"]) is thin


def test_wilson_interval_known_values_and_empty():
    # 15/100 at 90% (z = 1.6449), by hand: centre = (0.15 + z^2/200) / (1 + z^2/100) = 0.1592,
    # half-width = z*sqrt(0.15*0.85/100 + z^2/40000) / (1 + z^2/100) = 0.0587  ->  0.1005 to 0.2179.
    low, high = ra.wilson_interval(15, 100)
    assert low == pytest.approx(0.1005, abs=0.0005) and high == pytest.approx(0.2179, abs=0.0005)
    assert all(math.isnan(v) for v in ra.wilson_interval(0, 0))
    low0, high0 = ra.wilson_interval(0, 20)
    assert low0 == 0.0 and 0.0 < high0 < 0.15  # a zero count still has an upper bound


def test_pinball_loss_on_a_toy_set_is_computed_by_hand():
    actual = pd.Series([10.0, 20.0, 30.0])
    prediction = pd.Series([15.0, 15.0, 15.0])
    # tau 0.8: errors y-q = -5, +5, +15 -> losses 0.2*5=1, 0.8*5=4, 0.8*15=12 -> mean 17/3
    assert ra.pinball_loss(actual, prediction, 0.8) == pytest.approx(17 / 3)
    # tau 0.5 is half the mean absolute error
    assert ra.pinball_loss(actual, prediction, 0.5) == pytest.approx(0.5 * (5 + 5 + 15) / 3)


def test_pinball_skill_is_scale_free_and_peaks_at_the_quantile_the_ceiling_really_is():
    rng = np.random.default_rng(7)
    actual = pd.Series(rng.gamma(4.0, 3.0, 4000))
    ceiling_85 = pd.Series(float(np.quantile(actual, 0.85)), index=actual.index)
    skills = {tau: ra.pinball_skill(actual, ceiling_85, tau) for tau in ra.PINBALL_TAUS}
    # A constant ceiling equal to the 85th percentile IS the best constant 0.85-quantile: skill 0 there,
    # and negative at the other levels (it is a worse 0.80 / 0.90 predictor than their own constants).
    assert skills[0.85] == pytest.approx(0.0, abs=1e-6)
    assert skills[0.80] < 0 and skills[0.90] < 0


def test_ceiling_report_hit_rate_implied_quantile_and_best_tau():
    # 100 players, 15 of whom beat their ceiling: implied quantile 0.85.
    rows = [_row(proj=10.0, actual=25.0 if i < 15 else 8.0, Ceiling=20.0) for i in range(100)]
    table = ra.ceiling_report(_frame(rows)).set_index("Position")
    wr = table.loc["WR"]
    assert wr["n"] == 100 and wr["HitRate"] == 0.15 and wr["ImpliedQuantile"] == 0.85
    assert wr["Low90"] < 0.15 < wr["High90"]
    assert wr["BestTau"] == pytest.approx(0.85)  # skill, not raw loss, picks the level
    assert table.loc["All", "n"] == 100


def test_only_scored_players_with_a_positive_projection_count_and_dnp_is_not_a_zero():
    rows = [_row(actual=10.0), _row(actual=float("nan"), Status="dnp"), _row(proj=0.0, actual=5.0)]
    assert len(ra.usable(_frame(rows))) == 1
    assert ra.dnp_counts(_frame(rows)) == {"scored": 2, "dnp": 1, "unmatched": 0}


def test_accuracy_bias_mae_slope_and_perfect_calibration():
    # actual = projection exactly: bias 0, MAE 0, slope 1, R2 1, rank correlation 1.
    rows = [_row(proj=float(i), actual=float(i)) for i in range(5, 15)]
    wr = ra.accuracy_by_position(_frame(rows)).set_index("Position").loc["WR"]
    assert (wr["Bias"], wr["MAE"], wr["Slope"], wr["R2"], wr["Spearman"]) == (0.0, 0.0, 1.0, 1.0, 1.0)


def test_bias_is_actual_minus_projected_so_negative_means_the_projection_ran_high():
    rows = [_row(proj=12.0, actual=9.0) for _ in range(10)]
    assert ra.accuracy_by_position(_frame(rows)).set_index("Position").loc["WR", "Bias"] == -3.0


def test_calibration_bucket_labels_use_an_en_dash_so_sheets_cannot_read_them_as_dates():
    rows = [_row(proj=p, actual=p) for p in (3.0, 7.0, 12.0, 17.0, 25.0)]
    labels = ra.calibration_buckets(_frame(rows))["Projected"].tolist()
    assert labels == ["0–5", "5–10", "10–15", "15–20", "20+"]
    assert not any("-" in label for label in labels)


def test_salary_tier_table_splits_by_position_and_tier():
    rows = [_row("RB", proj=10, actual=7, Salary=4000) for _ in range(3)] + [
        _row("RB", proj=10, actual=11, Salary=8000) for _ in range(2)
    ]
    table = ra.accuracy_by_salary_tier(_frame(rows)).set_index(["Position", "Salary"])
    assert table.loc[("RB", "0–4500"), "Bias"] == -3.0 and table.loc[("RB", "0–4500"), "n"] == 3
    assert table.loc[("RB", "7500–9000"), "Bias"] == 1.0


def test_source_comparison_needs_all_three_sources_and_names_the_weeks():
    rows = [
        _row(proj=10, actual=12, SleeperPts=11.0, FantasyProsPts=13.0, AggPts=11.33, week=3),
        _row(proj=10, actual=12, week=2),  # no external sources: excluded from the comparison
    ]
    table, head = ra.source_comparison(_frame(rows))
    assert table["n"].tolist() == [1, 1, 1, 1] and head["weeks"] == [3]
    assert ra.source_comparison(_frame([_row()]))[1]["note"] == ra.NOT_ENOUGH


def test_head_to_head_counts_who_landed_closer_and_ignores_exact_ties():
    rows = [
        _row(proj=10, actual=12, SleeperPts=12.0, FantasyProsPts=12.0, AggPts=11.5),  # Agg closer
        _row(proj=10, actual=10, SleeperPts=14.0, FantasyProsPts=14.0, AggPts=12.0),  # TFFB closer
        _row(proj=10, actual=10, SleeperPts=10.0, FantasyProsPts=10.0, AggPts=10.0),  # tie: not decided
    ]
    _, head = ra.source_comparison(_frame(rows))
    assert (head["n"], head["agg_wins"]) == (2, 1)


def test_valadj_quintiles_are_within_position_and_report_n_and_rank_correlation():
    rows = []
    for position in ("QB", "RB"):
        for i in range(10):
            rows.append(_row(position, proj=10.0, Salary=4000 + 500 * i, ValAdj=float(i), actual=5.0 + i))
    quintiles, rho = ra.valadj_quintiles(_frame(rows))
    assert quintiles["n"].sum() == 20 and len(quintiles) == 5
    assert rho["n"] == 20 and set(rho) >= {"All", "QB", "RB"}


def test_flag_report_tokens_unflagged_comparison_and_zero_counts():
    rows = [_row("WR", proj=10, actual=7, Flags="LEVERAGE WIND") for _ in range(3)] + [
        _row("WR", proj=10, actual=11, Flags="") for _ in range(4)
    ]
    table = ra.flag_report(_frame(rows)).set_index("Flag")
    assert table.loc["LEVERAGE", "n"] == 3 and table.loc["LEVERAGE", "MeanError"] == -3.0
    assert table.loc["WIND", "n"] == 3  # a player with two flags counts under both
    assert (table.loc["LEVERAGE", "UnflaggedN"], table.loc["LEVERAGE", "UnflaggedMean"]) == (4, 1.0)
    assert table.loc["IMPL↑", "n"] == 0 and bool(table.loc["IMPL↑", "Thin"])


def test_salary_multiple_hit_rates_by_projected_val_band():
    rows = [_row(proj=15, Val=3.2, Salary=5000, actual=a) for a in (16.0, 21.0, 10.0, 25.0)]
    table = ra.salary_multiple_hits(_frame(rows)).set_index("ProjectedVal")
    row = table.loc["3–3.5"]
    # 3x salary/1000 = 15 pts: 16, 21, 25 reach it; 4x = 20 pts: 21, 25 reach it.
    assert (row["n"], row["Hit3x"], row["Hit4x"]) == (4, 0.75, 0.5)


def test_consistency_says_needs_four_weeks_until_there_are_four():
    three = _frame([_row(week=w) for w in (1, 2, 3) for _ in range(3)])
    assert ra.consistency(three) == "needs 4+ weeks"
    four = _frame([_row(proj=10, actual=10 + w, week=w) for w in (1, 2, 3, 4) for _ in range(3)])
    table = ra.consistency(four)
    assert isinstance(table, pd.DataFrame) and table.iloc[0]["Weeks"] == 4
