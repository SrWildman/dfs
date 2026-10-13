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


# ---------------------------------------------------------------------------------------------
# Shadow sources: scored from their first week, kept out of the blend until they have enough weeks
# ---------------------------------------------------------------------------------------------


def _varied(week, n=60, *, seed=0, props=False, props_rows=None, espn=False):
    """A week of varied pool rows with the three standing sources, optionally PropPts and EspnPts columns."""
    rng = np.random.default_rng(seed * 100 + week)
    part = _rows(week, n, proj=10.0, sleeper=11.0, fp=12.0)
    part["Position"] = rng.choice(["QB", "RB", "WR", "TE"], n)
    part["Salary"] = rng.choice([3500, 5000, 6500, 8000], n)
    truth = rng.uniform(4, 22, n)
    part["ProjPts"] = truth + rng.normal(1.0, 3.0, n)
    part["SleeperPts"] = truth + rng.normal(-0.5, 4.0, n)
    part["FantasyProsPts"] = truth + rng.normal(0.0, 3.5, n)
    part["AggPts"] = part[["ProjPts", "SleeperPts", "FantasyProsPts"]].mean(axis=1)
    part["DkActual"] = truth + rng.normal(0.0, 6.0, n)
    if props:
        # a much better source than the others, so a promotion would visibly move CalPts
        values = part["DkActual"] + rng.normal(0.0, 1.0, n)
        if props_rows is not None:
            values = values.where(np.arange(n) < props_rows)
        part["PropPts"] = values
    if espn:
        part["EspnPts"] = truth + rng.normal(0.0, 3.0, n)
    return part


def _shadow_table(prop_weeks, *, weeks=(1, 2, 3, 4), props_rows=None, espn_weeks=()):
    """Weeks `weeks`, with PropPts rated in `prop_weeks` and EspnPts in `espn_weeks` (NaN elsewhere)."""
    pieces = []
    for week in weeks:
        part = _varied(week, props=True, props_rows=props_rows, espn=week in espn_weeks)
        if week not in prop_weeks:
            part["PropPts"] = np.nan
        if "EspnPts" in part.columns and week not in espn_weeks:
            part["EspnPts"] = np.nan
        pieces.append(part)
    return pd.concat(pieces, ignore_index=True)


def _without(frame, *columns):
    return frame.drop(columns=[c for c in columns if c in frame.columns])


def _bytes(series):
    return series.to_numpy(dtype=float).tobytes()


def test_calpts_is_byte_identical_with_and_without_a_shadow_source_while_it_is_in_shadow():
    # PropPts is rated in weeks 1 and 2 only: two scored weeks, one short of the three it needs for week 4.
    with_props = _shadow_table(prop_weeks=(1, 2))
    plain = _without(with_props, "PropPts")
    assert with_props["PropPts"].notna().sum() > 0
    # week 4's own rows carry values too, so predict() has a populated column it must ignore
    with_props.loc[with_props["week"] == 4, "PropPts"] = 25.0

    rows_with, fit_with = cal.calpts_for_week(with_props, 4)
    rows_plain, fit_plain = cal.calpts_for_week(plain, 4)

    assert _bytes(rows_with["CalPts"]) == _bytes(rows_plain["CalPts"])
    pd.testing.assert_series_equal(rows_with["CalPts"], rows_plain["CalPts"])
    assert rows_with["CalPts"].to_csv() == rows_plain["CalPts"].to_csv()
    # and the fitted object itself is the same: no source, bias, count or weight for the shadow column
    assert fit_with == fit_plain
    assert "PropPts" not in fit_with.sources
    assert not any(key[0] == "PropPts" for key in fit_with.bias)


def test_a_shadow_source_with_too_few_rated_players_per_week_never_counts():
    # three weeks of PropPts, but only 10 rated players each: under SHADOW_MIN_ROWS_PER_WEEK
    thin = _shadow_table(prop_weeks=(1, 2, 3), props_rows=10)
    plain = _without(thin, "PropPts")
    assert cal.shadow_scored_weeks(cal.training_rows(thin, 4), "PropPts") == 0
    rows_thin, fit_thin = cal.calpts_for_week(thin, 4)
    rows_plain, _ = cal.calpts_for_week(plain, 4)
    assert "PropPts" not in fit_thin.sources
    assert _bytes(rows_thin["CalPts"]) == _bytes(rows_plain["CalPts"])


def test_two_shadow_sources_in_shadow_leave_calpts_untouched():
    both = _shadow_table(prop_weeks=(1, 2), espn_weeks=(2, 3))
    plain = _without(both, "PropPts", "EspnPts")
    rows_both, fit_both = cal.calpts_for_week(both, 4)
    rows_plain, _ = cal.calpts_for_week(plain, 4)
    assert _bytes(rows_both["CalPts"]) == _bytes(rows_plain["CalPts"])
    assert fit_both.sources == cal.CAL_SOURCES


def test_a_shadow_source_joins_after_the_required_scored_weeks_and_goes_through_the_normal_weighting():
    table = _shadow_table(prop_weeks=(1, 2, 3))
    plain = _without(table, "PropPts")
    # before week 4 it has weeks 1-2 only (before_week=3): still in shadow
    assert "PropPts" not in cal.fit(table, before_week=3).sources
    # for week 4 it has three scored weeks: it is in
    fitted = cal.fit(table, before_week=4)
    assert fitted.sources == (*cal.CAL_SOURCES, "PropPts")
    assert any(key[0] == "PropPts" for key in fitted.bias)
    for weights in fitted.weights.values():
        assert set(weights) == set(fitted.sources)
        assert sum(weights.values()) == pytest.approx(1.0)
    # the far better source gets the largest weight at a position with enough rows, and it moves CalPts
    wr = fitted.weights["WR"]
    assert wr["PropPts"] == max(wr.values())
    rows, _ = cal.calpts_for_week(table, 4)
    base, _ = cal.calpts_for_week(plain, 4)
    assert not np.allclose(rows["CalPts"], base["CalPts"], equal_nan=True)


def test_a_player_without_a_shadow_value_is_renormalised_over_the_sources_he_has():
    table = _shadow_table(prop_weeks=(1, 2, 3))
    week4 = table[table["week"] == 4].copy()
    fitted = cal.fit(table, before_week=4)
    week4.loc[week4.index[:5], "PropPts"] = np.nan
    value = cal.predict(week4, fitted)
    blank = week4.index[:5]
    cal_sources = cal.calibrated_sources(week4, fitted)
    for idx in blank:
        position = week4.loc[idx, "Position"]
        present = [s for s in cal.CAL_SOURCES if pd.notna(cal_sources.loc[idx, s])]
        weights = {s: fitted.weights[position][s] for s in present}
        expected = sum(weights[s] * cal_sources.loc[idx, s] for s in present) / sum(weights.values())
        assert value[idx] == pytest.approx(round(expected, 1))


def test_shadow_min_weeks_is_configurable_and_the_gate_can_be_switched_off():
    table = _shadow_table(prop_weeks=(1,))
    assert "PropPts" not in cal.fit(table, before_week=4).sources
    one_week = cal.ShadowPolicy(min_weeks=1)
    assert "PropPts" in cal.fit(table, before_week=4, shadow=one_week).sources
    # a policy that names no shadow source: an explicitly listed source is the caller's business
    off = cal.ShadowPolicy(sources=())
    assert (
        "PropPts" in cal.fit(table, before_week=4, sources=(*cal.CAL_SOURCES, "PropPts"), shadow=off).sources
    )


def test_a_shadow_source_passed_explicitly_is_still_held_back_until_it_has_earned_its_place():
    table = _shadow_table(prop_weeks=(1, 2))
    explicit = cal.fit(table, before_week=4, sources=(*cal.CAL_SOURCES, "PropPts"))
    assert explicit.sources == cal.CAL_SOURCES


def test_a_shadow_source_with_no_column_is_simply_never_active():
    plain = _without(_shadow_table(prop_weeks=()), "PropPts")
    fitted = cal.fit(plain, before_week=4)
    assert fitted.sources == cal.CAL_SOURCES
    assert cal.shadow_scored_weeks(cal.training_rows(plain, 4), "EspnPts") == 0


def test_shadow_gating_keeps_the_no_lookahead_rule():
    # a promoted shadow source is fitted on earlier weeks only: week 4's own actuals move nothing in week 4
    table = _shadow_table(prop_weeks=(1, 2, 3), weeks=(1, 2, 3, 4))
    before, fitted = cal.calpts_for_week(table, 4)
    assert "PropPts" in fitted.sources
    altered = table.copy()
    altered.loc[altered["week"] == 4, "DkActual"] = 99.0
    after, _ = cal.calpts_for_week(altered, 4)
    assert _bytes(before["CalPts"]) == _bytes(after["CalPts"])


def test_configured_shadow_falls_back_to_the_defaults_without_a_config(monkeypatch):
    from dfs import config

    def no_config():
        raise config.ConfigError("none")

    monkeypatch.setattr(config, "load_config", no_config)
    assert cal.configured_shadow() == cal.DEFAULT_SHADOW
    assert cal.DEFAULT_SHADOW.sources == ("EspnPts", "PropPts")
    assert cal.DEFAULT_SHADOW.min_weeks == 3


def test_configured_shadow_reads_the_projection_section(monkeypatch):
    from dfs import config

    cfg = config.Config.model_validate(
        {
            "google_sheets": {"sheet_id": "x", "credentials_file": "c.json", "tab_mappings": {}},
            "projection": {"shadow_sources": ["PropPts"], "shadow_min_weeks": 5},
        }
    )
    monkeypatch.setattr(config, "load_config", lambda: cfg)
    assert cal.configured_shadow() == cal.ShadowPolicy(sources=("PropPts",), min_weeks=5)
