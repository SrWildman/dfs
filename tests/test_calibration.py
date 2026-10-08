"""CalPts: the shrinkage maths, weight renormalisation, the no-lookahead rule and the Week-1 fallback."""

import numpy as np
import pandas as pd
import pytest

from dfs import calibration as cal


def _rows(week, n, *, position="RB", salary=4000, proj=10.0, actual=15.0, sleeper=np.nan, fp=np.nan, **kw):
    """`n` identical scored pool rows."""
    base = {
        "season": 2026,
        "week": week,
        "Position": position,
        "Salary": salary,
        "ProjPts": proj,
        "SleeperPts": sleeper,
        "FantasyProsPts": fp,
        "AggPts": np.nanmean([proj, sleeper, fp]),
        "DkActual": actual,
        "RosterablePool": True,
        "Status": "scored",
    }
    return pd.DataFrame([{**base, **kw} for _ in range(n)])


def test_shrunk_bias_at_n_0_10_and_160():
    # residual 5 on every row: the raw mean is 5, shrinkage keeps n / (n + 30) of it.
    assert cal.shrunk_bias(0.0, 0) == 0.0
    assert cal.shrunk_bias(5 * 10, 10) == pytest.approx(1.25)  # keeps 25%
    assert cal.shrunk_bias(5 * 160, 160) == pytest.approx(5 * 160 / 190)  # keeps 84%


def test_fit_applies_the_shrinkage_per_cell():
    # n=10 cheap RBs missed by +5 and n=160 mid RBs missed by +5: the cells shrink differently.
    scored = pd.concat([_rows(1, 10, salary=4000), _rows(1, 160, salary=5000)], ignore_index=True)
    fitted = cal.fit(scored, before_week=2)
    assert fitted.bias[("ProjPts", "RB", 0)] == pytest.approx(1.25)
    assert fitted.bias[("ProjPts", "RB", 1)] == pytest.approx(5 * 160 / 190)
    assert fitted.counts[("ProjPts", "RB", 0)] == 10
    # a cell with no training rows has no entry, so predicting it adds 0
    assert ("ProjPts", "RB", 3) not in fitted.bias


def test_blended_weight_is_equal_at_zero_rows_and_tends_to_invmse():
    assert cal.blended_weight(0.9, 1 / 3, 0) == pytest.approx(1 / 3)
    assert cal.blended_weight(0.9, 1 / 3, 100) == pytest.approx((100 * 0.9 + 100 / 3) / 200)
    assert cal.blended_weight(0.9, 1 / 3, 10_000) == pytest.approx(0.9, abs=0.01)


def test_tier_edges_belong_to_the_higher_tier():
    position = pd.Series(["RB", "RB", "RB", "QB", "DST", "DST", "XX"])
    salary = pd.Series([4499, 4500, 7500, 6499, 2799, 2800, 5000])
    assert cal.tier_index(position, salary).tolist() == [0, 1, 3, 1, 0, 1, -1]
    assert cal.tier_labels("RB") == ["<$4.5k", "$4.5–6k", "$6–7.5k", "$7.5k+"]
    assert cal.tier_labels("DST") == ["<$2.8k", "$2.8k+"]


def test_weights_favour_the_more_accurate_source_and_sum_to_one():
    # Sleeper is nearly exact, TFFB and FantasyPros carry heavy noise: with plenty of rows Sleeper gets the
    # biggest weight, yet shrinkage toward equal keeps the other two well above zero.
    rng = np.random.default_rng(1)
    n = 600
    truth = rng.uniform(5, 20, n)
    scored = _rows(1, n, salary=9000, position="WR")
    scored["DkActual"] = truth
    scored["SleeperPts"] = truth + rng.normal(0, 0.5, n)
    scored["ProjPts"] = truth + rng.normal(0, 6, n)
    scored["FantasyProsPts"] = truth + rng.normal(0, 6, n)
    w = cal.fit(scored, before_week=2).weights["WR"]
    assert sum(w.values()) == pytest.approx(1.0)
    assert w["SleeperPts"] > 0.6 > w["ProjPts"] > 0.05
    assert cal.fit(scored, before_week=2).weight_rows["WR"] == n


def test_weights_renormalise_over_the_sources_a_player_has():
    scored = _rows(1, 300, proj=10.0, sleeper=12.0, fp=14.0, actual=12.0)
    fitted = cal.fit(scored, before_week=2)
    frame = pd.DataFrame(
        {
            "Position": ["RB", "RB", "RB"],
            "Salary": [4000, 4000, 4000],
            "ProjPts": [10.0, 10.0, np.nan],
            "SleeperPts": [12.0, np.nan, np.nan],
            "FantasyProsPts": [14.0, np.nan, np.nan],
            "AggPts": [12.0, 10.0, np.nan],
        }
    )
    calpts = cal.predict(frame, fitted)
    calibrated = cal.calibrated_sources(frame, fitted)
    w = fitted.weights["RB"]
    both = (
        w["ProjPts"] * calibrated.loc[0, "ProjPts"]
        + w["SleeperPts"] * calibrated.loc[0, "SleeperPts"]
        + w["FantasyProsPts"] * calibrated.loc[0, "FantasyProsPts"]
    ) / sum(w.values())
    assert calpts[0] == pytest.approx(round(both, 1))
    # one source only: that source, calibrated, with its weight renormalised to 1
    assert calpts[1] == pytest.approx(round(calibrated.loc[1, "ProjPts"], 1))
    # a player with no source at all is blank, never 0
    assert np.isnan(calpts[2])


def test_week_one_has_no_history_so_calpts_is_aggpts():
    scored = pd.concat([_rows(1, 5, proj=10.0, sleeper=12.0, fp=14.0, actual=30.0)], ignore_index=True)
    rows, fitted = cal.calpts_for_week(scored, 1)
    assert fitted.is_empty
    assert rows["CalPts"].tolist() == rows["AggPts"].tolist()


def test_fit_only_sees_weeks_strictly_before_and_never_a_dnp_or_non_pool_row():
    scored = pd.concat(
        [
            _rows(1, 20, actual=15.0),
            _rows(2, 20, actual=15.0),
            _rows(3, 20, actual=15.0),
            _rows(1, 5, actual=99.0, Status="dnp"),
            _rows(1, 5, actual=99.0, RosterablePool=False),
        ],
        ignore_index=True,
    )
    fitted = cal.fit(scored, before_week=3)
    assert fitted.weeks == (1, 2)
    assert fitted.train_rows == 40
    assert fitted.bias[("ProjPts", "RB", 0)] == pytest.approx(5 * 40 / 70)


def test_no_lookahead_week_k_calpts_ignores_week_k_actuals():
    rng = np.random.default_rng(3)
    pieces = []
    for week in (1, 2, 3, 4):
        part = _rows(week, 60, proj=10.0, sleeper=11.0, fp=12.0)
        part["Salary"] = rng.choice([3500, 5000, 6500, 8000], len(part))
        part["Position"] = rng.choice(["RB", "WR"], len(part))
        part["ProjPts"] = rng.uniform(4, 18, len(part))
        part["DkActual"] = part["ProjPts"] + rng.normal(0, 6, len(part))
        pieces.append(part)
    scored = pd.concat(pieces, ignore_index=True)
    before, _ = cal.calpts_for_week(scored, 4)
    altered = scored.copy()
    altered.loc[altered["week"] == 4, "DkActual"] = rng.uniform(0, 60, int((altered["week"] == 4).sum()))
    after, _ = cal.calpts_for_week(altered, 4)
    pd.testing.assert_series_equal(before["CalPts"], after["CalPts"])
    # and the same holds for every later week's actuals: adding week 5 changes nothing in week 4
    later = pd.concat([scored, _rows(5, 60, actual=80.0)], ignore_index=True)
    again, _ = cal.calpts_for_week(later, 4)
    pd.testing.assert_series_equal(before["CalPts"], again["CalPts"])


def test_backtest_is_expanding_window_and_skips_week_one():
    scored = pd.concat([_rows(w, 40, actual=14.0, proj=10.0) for w in (1, 2, 3)], ignore_index=True)
    frame = cal.backtest_frame(scored)
    assert sorted(frame["week"].unique()) == [2, 3]
    table = cal.backtest(scored)
    assert set(table["Source"]) == {"TFFB", "AggPts", "CalPts"}
    assert set(table["Position"]) == {"RB", "All"}
    tffb = table[(table["Source"] == "TFFB") & (table["Position"] == "All")].iloc[0]
    calp = table[(table["Source"] == "CalPts") & (table["Position"] == "All")].iloc[0]
    assert tffb["Bias"] == pytest.approx(4.0)
    assert abs(calp["Bias"]) < abs(tffb["Bias"])  # the correction closes part of the gap
    assert calp["n"] == tffb["n"] == 80


def test_backtest_by_week_has_one_row_per_week_and_projection():
    scored = pd.concat([_rows(w, 40, actual=14.0, proj=10.0) for w in (1, 2, 3)], ignore_index=True)
    trend = cal.backtest_by_week(scored)
    assert sorted(trend["Week"].unique()) == [2, 3]
    assert len(trend) == 6  # two weeks x TFFB, AggPts, CalPts


def _um_scored(weeks=(1, 2, 3), n=40):
    rng = np.random.default_rng(5)
    pieces = []
    for week in weeks:
        part = _rows(week, n, actual=14.0, proj=10.0, sleeper=11.0, fp=12.0)
        part["DkActual"] = 14.0 + rng.normal(0, 1, n)
        part["UmPts"] = np.where(np.arange(n) % 2 == 0, 13.0, np.nan)  # UM rates half the players
        pieces.append(part)
    return pd.concat(pieces, ignore_index=True)


def test_um_is_not_a_calpts_source_so_a_um_value_changes_nothing():
    scored = _um_scored()
    assert "UmPts" not in cal.CAL_SOURCES
    fitted = cal.fit(scored, before_week=3)
    assert "UmPts" not in fitted.sources
    frame = pd.DataFrame(
        {
            "Position": ["RB", "RB"],
            "Salary": [4000, 4000],
            "ProjPts": [10.0, 10.0],
            "SleeperPts": [11.0, 11.0],
            "FantasyProsPts": [12.0, 12.0],
            "UmPts": [13.0, np.nan],
            "AggPts": [11.0, 11.0],
        }
    )
    rated, unrated = cal.predict(frame, fitted).tolist()
    assert rated == unrated  # the rated player's number is the same: UM does not enter the blend


def test_with_um_backtest_keeps_only_players_um_rates_and_adds_its_row():
    scored = _um_scored()
    table = cal.backtest(scored, with_um=True)
    assert set(table["Source"]) == {"TFFB", "AggPts", "UM", "CalPts"}  # UM is a race row, not a blend source
    assert (table[table["Position"] == "All"]["n"] == 40).all()  # 20 UM-rated players in each of 2 weeks
    plain = cal.backtest(scored, with_um=False)
    assert (plain[plain["Position"] == "All"]["n"] == 80).all()  # everyone, no UM column
    assert "UM" not in set(plain["Source"])


def test_no_lookahead_holds_with_um_too():
    scored = _um_scored(weeks=(1, 2, 3, 4))
    before, _ = cal.calpts_for_week(scored, 4)
    altered = scored.copy()
    altered.loc[altered["week"] == 4, "DkActual"] = 99.0
    after, _ = cal.calpts_for_week(altered, 4)
    pd.testing.assert_series_equal(before["CalPts"], after["CalPts"])
