"""Score-to-finish curves with week effects: the joint fit, exact shrinkage of series levels and week effects
on balanced designs, recovery of known truths, the three-way leave-one-week-out comparison, H2H."""

import math

import numpy as np
import pandas as pd
import pytest

from dfs.contests import history
from dfs.contests import series as S
from dfs.contests import toughness as T
from dfs.contests.standings import ScoreProfile

ALPHA, BETA = 7.5, -0.05  # logit(q) = 7.5 - 0.05 x: q = 50% at x = 150, 20% at x = 178


def row(fmt, series, season, week, x, y, *, idx=None, key=None, weight=1.0, kind="entry", points=None):
    return {
        "kind": kind,
        "format": fmt,
        "series": series,
        "season": season,
        "week": week,
        "wgroup": T.SHOWDOWN_GROUP if fmt == S.SHOWDOWN else T.CLASSIC_GROUP,
        "contest_key": key or f"{series}-{season}-{week}-{x:.3f}",
        "x_raw": x,
        "x_idx": x if idx is None else idx,
        "y": float(np.clip(y, -T.LOGIT_CLIP, T.LOGIT_CLIP)),
        "weight": weight,
        "group": "",
        "q_cash": 0.2,
        "points": round(x, 2) if points is None else points,
    }


def random_world(
    deltas, *, weeks=40, week_sd=0.0, noise=0.0, fmt="SE GPP", seed=1, skip=0.0, season=2025, shocks=None
):
    """Observations from a known truth: `y = ALPHA + delta_series + week shock + BETA x + noise`. Returns
    `(obs, shocks)` with `shocks[week]` the true week effect."""
    rng = np.random.default_rng(seed)
    shocks = shocks if shocks is not None else {w: rng.normal(0, week_sd) for w in range(1, weeks + 1)}
    rows = []
    for week in range(1, weeks + 1):
        for name, delta in deltas.items():
            if rng.random() < skip:
                continue
            x = rng.normal(150, 20)
            y = ALPHA + delta + shocks[week] + BETA * x + rng.normal(0, noise)
            rows.append(row(fmt, name, season, week, x, y))
    return pd.DataFrame(rows, columns=T.OBS_COLUMNS), shocks


def balanced_world(deltas, shocks, *, fmt="SE GPP"):
    """Every series in every week with the same two scores (130 and 170), noiseless: the design is balanced
    and the scores are orthogonal to the series and week indicators, so ridge shrinkage is exact."""
    rows = []
    for week, u in shocks.items():
        for name, delta in deltas.items():
            for x in (130.0, 170.0):
                rows.append(
                    row(fmt, name, 2025, week, x, ALPHA + delta + u + BETA * x, key=f"{name}{week}{x}")
                )
    return pd.DataFrame(rows, columns=T.OBS_COLUMNS)


# ---- the curve and the exact shrinkage -----------------------------------------------------------


def test_the_format_curve_is_recovered_exactly_without_noise_or_effects():
    obs, _ = random_world({"A": 0.0, "B": 0.0}, weeks=20)
    model = T.Design(obs, "raw").model(k=1e9, k_w=1e9)
    fit = model.formats["SE GPP"]
    assert fit.alpha == pytest.approx(ALPHA) and fit.beta == pytest.approx(BETA) and fit.reliable
    assert model.predict_q("SE GPP", "A", 150.0) == pytest.approx(1 / (1 + math.exp(-(ALPHA + BETA * 150))))
    assert model.predict_q("SE GPP", "A", 200.0) < model.predict_q("SE GPP", "A", 100.0)


def test_series_levels_are_shrunk_by_exactly_n_over_n_plus_k():
    shocks = dict.fromkeys(range(1, 16), 0.0)
    obs = balanced_world({"hard": 1.0, "easy": -1.0}, shocks)
    n = 15 * 2  # each series has 30 entries
    for k in (0.0, 10.0, 90.0):
        model = T.Design(obs, "raw").model(k=k, k_w=1e9)
        factor = n / (n + k)
        assert model.deltas[("SE GPP", "hard")] == pytest.approx(factor, abs=1e-6)
        assert model.deltas[("SE GPP", "easy")] == pytest.approx(-factor, abs=1e-6)


def test_week_effects_are_shrunk_by_exactly_n_over_n_plus_kw_and_series_by_n_over_n_plus_k():
    weeks = 20
    shocks = {w: (1.0 if w % 2 else -1.0) for w in range(1, weeks + 1)}  # sums to zero
    obs = balanced_world({"hard": 0.5, "easy": -0.5}, shocks)
    n_week, n_series = 4, weeks * 2  # 2 series x 2 scores per week; 20 weeks x 2 scores per series
    for k, k_w in ((0.0, 0.0), (5.0, 8.0), (40.0, 2.0)):
        model = T.Design(obs, "weeks").model(k=k, k_w=k_w)
        assert model.weeks[(T.CLASSIC_GROUP, 2025, 1)] == pytest.approx(
            n_week / (n_week + k_w) * 1.0, abs=1e-6
        )
        assert model.weeks[(T.CLASSIC_GROUP, 2025, 2)] == pytest.approx(-n_week / (n_week + k_w), abs=1e-6)
        assert model.deltas[("SE GPP", "hard")] == pytest.approx(n_series / (n_series + k) * 0.5, abs=1e-6)
    off = T.Design(obs, "weeks").model(k=0.0, k_w=1e9)
    assert off.weeks[(T.CLASSIC_GROUP, 2025, 1)] == 0.0  # an infinite penalty is "no week effects"


def test_a_series_with_few_entries_is_shrunk_harder_than_one_with_many():
    rows = []
    for week in range(1, 101):
        for x in (130.0, 170.0):
            rows.append(row("SE GPP", "big", 2025, week, x, ALPHA + 1.0 + BETA * x, key=f"b{week}{x}"))
    for week in range(1, 6):
        for x in (130.0, 170.0):
            rows.append(row("SE GPP", "small", 2025, week, x, ALPHA + 1.0 + BETA * x, key=f"s{week}{x}"))
    for week in range(1, 101):
        for x in (130.0, 170.0):
            rows.append(row("SE GPP", "anchor", 2025, week, x, ALPHA + BETA * x, key=f"a{week}{x}"))
    model = T.Design(pd.DataFrame(rows), "raw").model(k=10.0, k_w=1e9)
    big, small = model.deltas[("SE GPP", "big")], model.deltas[("SE GPP", "small")]
    anchor = model.deltas[("SE GPP", "anchor")]
    # all three levels are relative to the format's average, so compare the gaps to the unshifted anchor
    assert anchor < small < big
    assert (big - anchor) < 1.0


def test_with_zero_penalties_the_effects_are_the_plain_least_squares_ones():
    obs = balanced_world({"A": 0.7, "B": 0.0, "C": -0.7}, {w: 0.0 for w in range(1, 11)})
    model = T.Design(obs, "raw").model(k=0.0, k_w=1e9)
    got = [model.deltas[("SE GPP", s)] for s in "ABC"]
    assert got == pytest.approx([0.7, 0.0, -0.7], abs=1e-6)


def test_a_slope_that_is_not_negative_is_flagged_and_predicts_nothing():
    obs, _ = random_world({"A": 0.0}, weeks=20)
    obs["y"] = -obs["y"]
    model = T.Design(obs, "raw").model(k=10.0, k_w=1e9)
    assert not model.formats["SE GPP"].reliable and "not negative" in model.formats["SE GPP"].note
    assert math.isnan(model.predict_q("SE GPP", "A", 150.0))


def test_a_format_with_too_little_data_or_no_spread_is_not_fitted():
    few, _ = random_world({"A": 0.0}, weeks=T.MIN_FORMAT_N - 1)
    assert T.Design(few, "raw").formats == []
    flat, _ = random_world({"A": 0.0}, weeks=20)
    flat["x_raw"] = 150.0
    assert T.Design(flat, "raw").formats == []


def test_an_unknown_mode_is_an_error():
    obs, _ = random_world({"A": 0.0}, weeks=10)
    with pytest.raises(ValueError, match="mode must be"):
        T.Design(obs, "bogus")


# ---- week effects --------------------------------------------------------------------------------


def test_week_effects_recover_the_weekly_shocks_and_the_series_gaps_better_than_ignoring_them():
    deltas = {"hard": 0.5, "mid": 0.0, "easy": -0.5, "x": 0.2, "y": -0.2}
    obs, shocks = random_world(deltas, weeks=80, week_sd=1.0, noise=0.1, seed=5, skip=0.35)
    with_weeks = T.Design(obs, "weeks").model(k=1.0, k_w=0.0)
    without = T.Design(obs, "raw").model(k=1.0, k_w=1e9)
    est = np.array([with_weeks.weeks.get((T.CLASSIC_GROUP, 2025, w), 0.0) for w in sorted(shocks)])
    truth = np.array([shocks[w] for w in sorted(shocks)])
    assert np.corrcoef(est, truth)[0, 1] > 0.97

    def gap_error(model):
        return sum(abs(model.deltas[("SE GPP", s)] - d) for s, d in deltas.items())

    assert gap_error(with_weeks) < gap_error(without) / 2


def test_one_week_effect_is_shared_by_every_classic_format_and_showdown_has_its_own():
    a, shocks = random_world({"A": 0.0}, weeks=12, week_sd=0.8, seed=2, fmt="SE GPP")
    b, _ = random_world({"B": 0.0}, weeks=12, shocks=shocks, seed=3, fmt="Double Up")
    c, _ = random_world({"C": 0.0}, weeks=12, shocks=shocks, seed=4, fmt="Showdown")
    design = T.Design(pd.concat([a, b, c], ignore_index=True), "weeks")
    classic = [k for k in design.week_keys if k[0] == T.CLASSIC_GROUP]
    showdown = [k for k in design.week_keys if k[0] == T.SHOWDOWN_GROUP]
    assert len(classic) == 12 and len(showdown) == 12  # not 24 + 12: SE GPP and Double Up share their weeks


def test_showdown_is_fitted_on_its_own_and_never_pooled_with_classic():
    classic, _ = random_world({"A": 0.0}, weeks=30, seed=1, fmt="SE GPP")
    showdown, _ = random_world({"B": 0.0}, weeks=30, seed=2, fmt="Showdown")
    showdown["x_raw"] = showdown["x_raw"] / 2  # raw Showdown points: another scale
    showdown["y"] = ALPHA + 1.0 + BETA * showdown["x_raw"] * 2  # and another curve entirely
    both = pd.concat([classic, showdown], ignore_index=True)
    together = T.Design(both, "weeks").model(k=5.0, k_w=5.0)
    alone = T.Design(classic, "weeks").model(k=5.0, k_w=5.0)
    assert together.formats["SE GPP"].alpha == pytest.approx(alone.formats["SE GPP"].alpha)
    assert together.formats["SE GPP"].beta == pytest.approx(alone.formats["SE GPP"].beta)
    assert together.formats["Showdown"].alpha != pytest.approx(together.formats["SE GPP"].alpha, abs=0.1)
    assert ("Showdown", "B") in together.deltas and ("SE GPP", "B") not in together.deltas


# ---- leave-one-week-out --------------------------------------------------------------------------


def test_a_held_out_week_never_influences_its_own_prediction():
    obs, _ = random_world({"A": 1.0, "B": -1.0}, weeks=12, week_sd=0.5, noise=0.3, seed=8)
    distorted = obs.copy()
    distorted.loc[distorted["week"] == 6, "y"] += 3.0  # change week 6's own outcomes
    for mode in ("raw", "weeks"):
        a = T.Design(obs, mode).solve(5.0, 5.0, exclude=(2025, 6))
        b = T.Design(distorted, mode).solve(5.0, 5.0, exclude=(2025, 6))
        assert np.allclose(a, b, atol=1e-9), mode
    full_a, full_b = T.Design(obs, "raw").solve(5.0, 5.0), T.Design(distorted, "raw").solve(5.0, 5.0)
    assert not np.allclose(full_a, full_b)  # while the fit on all the data does learn from it


def test_every_entry_is_scored_exactly_once_across_the_folds():
    obs, _ = random_world({"A": 1.0, "B": -1.0}, weeks=12, noise=0.3, seed=8)
    for mode in ("raw", "weeks"):
        errors = T.leave_one_week_out(obs, mode, grid_k=(5.0,), grid_kw=(5.0,))
        assert errors["n_scored"].tolist() == [len(obs)]


def test_leave_one_week_out_prefers_little_series_shrinkage_when_series_really_differ():
    obs, _ = random_world({"A": 1.5, "B": -1.5, "C": 0.7, "D": -0.7}, weeks=40, noise=0.5, seed=2)
    errors = T.leave_one_week_out(obs, "raw")
    k, _, _ = T.best_penalties(errors)
    assert k <= 2.0
    by_k = errors.set_index("k")["mae"]
    assert by_k[1e9] > 2 * by_k[0.0]  # collapsing every series onto the format is far worse


def test_leave_one_week_out_tolerates_heavy_shrinkage_when_series_do_not_differ():
    obs, _ = random_world({"A": 0.0, "B": 0.0, "C": 0.0}, weeks=40, noise=0.5, seed=7)
    by_k = T.leave_one_week_out(obs, "raw").set_index("k")["mae"]
    assert by_k[1e9] <= by_k[0.0] * 1.03


def test_ties_between_penalties_go_to_the_larger_ones():
    errors = pd.DataFrame(
        {"k": [0.0, 10.0, 10.0], "k_w": [5.0, 5.0, 20.0], "mae": [0.1, 0.1, 0.1], "n_scored": [9, 9, 9]}
    )
    assert T.best_penalties(errors) == (10.0, 20.0, 0.1)
    assert T.best_penalties(pd.DataFrame(columns=["k", "k_w", "mae", "n_scored"])) is None


# ---- the three treatments of the week, compared --------------------------------------------------


def test_big_week_effects_that_the_index_cannot_see_are_found_by_the_week_model():
    """Weeks differ by sd 1.0 on the logit (about 20 points); the index is unrelated noise."""
    rng = np.random.default_rng(987654)  # independent of the shocks' stream (seed 11)
    obs, _ = random_world(
        {"A": 0.6, "B": -0.4, "C": 0.0, "D": 0.3}, weeks=60, week_sd=1.0, noise=0.3, seed=11
    )
    scale = {w: float(np.exp(rng.normal(0, 0.08))) for w in obs["week"].unique()}
    obs["x_idx"] = obs["x_raw"] / obs["week"].map(scale)
    table = T.compare_models(obs).set_index("mode")
    assert set(table.index) == set(T.MODES)
    assert table.loc["weeks", "mae"] <= table.loc["raw", "mae"] + 1e-9  # its grid contains raw
    assert table.loc["weeks", "mae"] < table.loc["index", "mae"]
    assert table["n_scored"].nunique() == 1  # all three scored on the same rows


def test_the_index_wins_when_scores_really_scale_with_it():
    """Points are the true score times the week's scale and nothing else differs between weeks."""
    rng = np.random.default_rng(3)
    rows = []
    scale = {w: float(np.exp(rng.normal(0, 0.15))) for w in range(1, 61)}
    for week in range(1, 61):
        for name, delta in {"A": 0.5, "B": -0.5, "C": 0.0}.items():
            x_true = rng.normal(150, 20)
            y = ALPHA + delta + BETA * x_true + rng.normal(0, 0.1)
            rows.append(row("SE GPP", name, 2025, week, x_true * scale[week], y, idx=x_true))
    obs = pd.DataFrame(rows, columns=T.OBS_COLUMNS)
    table = T.compare_models(obs).set_index("mode")
    assert table.loc["index", "mae"] < table.loc["raw", "mae"]
    assert T.choose_mode(table.reset_index()) == "index"


def test_choose_mode_takes_the_lowest_error_and_ties_go_to_the_simplest():
    frame = pd.DataFrame({"mode": ["weeks", "index", "raw"], "mae": [0.10, 0.12, 0.11]})
    assert T.choose_mode(frame) == "weeks"
    tied = pd.DataFrame({"mode": ["weeks", "raw"], "mae": [0.1, 0.1]})
    assert T.choose_mode(tied) == "raw"


def test_the_index_comparator_is_dropped_when_there_is_no_index():
    obs, _ = random_world({"A": 0.5, "B": -0.5}, weeks=30, week_sd=0.5, noise=0.3)
    obs["x_idx"] = math.nan
    assert set(T.compare_models(obs)["mode"]) == {"raw", "weeks"}


def test_compare_models_is_empty_with_too_little_data():
    obs, _ = random_world({"A": 0.5}, weeks=4)
    assert T.compare_models(obs).empty


# ---- fit_toughness end to end --------------------------------------------------------------------


def log_from_obs(obs, *, places_paid=200, entries=1000):
    """A `history.typed`-shaped frame from synthetic observations (place derived from the finish)."""
    place = np.clip(np.round(1 / (1 + np.exp(-obs["y"])) * entries + 0.5), 1, entries).astype(int)
    return pd.DataFrame(
        {
            "format": obs["format"],
            "series": obs["series"],
            "season": obs["season"],
            "week": obs["week"],
            "contest_key": obs["contest_key"],
            "slate": "main",
            "place": place,
            "entries": entries,
            "places_paid": places_paid,
            "points": obs["x_raw"],
        }
    )


def test_fit_toughness_reports_series_formats_weeks_and_the_comparison():
    obs, shocks = random_world(
        {"hard": 0.8, "easy": -0.8, "mid": 0.0}, weeks=60, week_sd=0.9, noise=0.2, seed=5, skip=0.2
    )
    index = pd.DataFrame({"season": 2025, "week": range(1, 61), "index": 1.0})
    res = T.fit_toughness(log_from_obs(obs), index)
    assert res.mode in T.MODES and set(res.comparison["mode"]) == set(T.MODES)
    table = res.series_table.set_index("series")
    assert table.loc["hard", "toughness"] > 0 > table.loc["easy", "toughness"]
    assert table.loc["hard", "expected_cash_line"] > table.loc["easy", "expected_cash_line"]
    delta, beta = table.loc["hard", "delta"], res.model.formats["SE GPP"].beta
    assert table.loc["hard", "toughness"] == pytest.approx(-delta / beta)
    assert table.loc["hard", "expected_cash_line"] - table.loc["hard", "format_cash_line"] == pytest.approx(
        table.loc["hard", "toughness"]
    )
    fmt = res.format_table.set_index("format").loc["SE GPP"]
    assert fmt["reliable"] and fmt["beta"] < 0 and fmt["n_entries"] == len(obs)


def test_expected_cash_line_is_the_score_that_finishes_at_the_payout_percentile_in_an_average_week():
    obs, _ = random_world({"A": 0.0, "B": 0.0}, weeks=40, week_sd=1.0, seed=6)
    res = T.fit_toughness(log_from_obs(obs, places_paid=200), None, mode="weeks", k=1e9, k_w=1.0)
    fit = res.model.formats["SE GPP"]
    q = (200 - 0.5) / 1000
    expected = (math.log(q / (1 - q)) - fit.alpha) / fit.beta  # w = 0: no week term
    assert res.format_table.set_index("format").loc["SE GPP", "expected_cash_line"] == pytest.approx(expected)
    # about 178 = (7.5 - logit(0.2)) / 0.05: the truth for an average week
    assert expected == pytest.approx((ALPHA - math.log(0.25)) / 0.05, abs=6)


def test_the_weeks_table_has_effects_in_logit_and_points_and_the_index_as_context():
    obs, shocks = random_world({"A": 0.5, "B": -0.5}, weeks=40, week_sd=1.0, noise=0.1, seed=9)
    index = pd.DataFrame({"season": 2025, "week": range(1, 41), "index": np.linspace(0.9, 1.1, 40)})
    res = T.fit_toughness(log_from_obs(obs), index, mode="weeks", k=1.0, k_w=0.0)
    weeks = res.weeks_table
    assert list(weeks.columns) == T.WEEK_COLUMNS and len(weeks) == 40 and set(weeks["group"]) == {"classic"}
    # a tougher week (positive effect) takes more points for the same finish
    assert (np.sign(weeks["w"]) == np.sign(weeks["w_points"])).all()
    assert weeks["w_points"].iloc[0] == pytest.approx(-weeks["w"].iloc[0] / res.model.formats["SE GPP"].beta)
    assert weeks["slate_index"].iloc[0] == pytest.approx(0.9)
    truth = np.array([shocks[w] for w in weeks["week"]])
    assert np.corrcoef(weeks["w"], truth)[0, 1] > 0.95


def test_other_modes_have_no_week_table_and_the_index_mode_divides_by_the_index():
    obs, _ = random_world({"A": 0.5, "B": -0.5}, weeks=30, noise=0.2, seed=2)
    index = pd.DataFrame({"season": 2025, "week": range(1, 31), "index": 1.25})
    raw = T.fit_toughness(log_from_obs(obs), index, mode="raw", k=5.0, k_w=0.0)
    idx = T.fit_toughness(log_from_obs(obs), index, mode="index", k=5.0, k_w=0.0)
    assert raw.weeks_table.empty and idx.weeks_table.empty and raw.mode == "raw" and idx.mode == "index"
    # dividing every score by 1.25 rescales the slope by 1.25 and leaves the intercept
    assert idx.model.formats["SE GPP"].beta == pytest.approx(raw.model.formats["SE GPP"].beta * 1.25)
    assert idx.model.formats["SE GPP"].alpha == pytest.approx(raw.model.formats["SE GPP"].alpha)


def test_too_little_data_uses_the_default_penalties_and_says_so():
    obs, _ = random_world({"A": 0.5, "B": -0.5}, weeks=5, noise=0.2)
    res = T.fit_toughness(log_from_obs(obs), None)
    assert (res.k, res.k_w) == (T.DEFAULT_K, T.DEFAULT_KW) and "too little data" in res.penalty_source
    assert res.mode == "weeks" and res.comparison.empty


def test_pairs_are_counted_but_need_no_special_weighting():
    obs, _ = random_world({"A": 0.0, "B": 0.0}, weeks=10, seed=3)
    twin = obs[obs["series"] == "A"].copy()
    twin["series"], twin["contest_key"] = "B", twin["contest_key"] + "-b"  # the same lineup in B too
    both = pd.concat([obs[obs["series"] == "A"], twin], ignore_index=True)
    grouped = T.assign_pair_groups(both)
    assert (grouped["group"] != "").sum() == len(both)
    solo = obs.copy()
    solo["points"] = np.arange(len(solo)) + 0.5
    assert (T.assign_pair_groups(solo)["group"] == "").all()
    same_contest = both.copy()
    same_contest["contest_key"] = "one"
    assert (T.assign_pair_groups(same_contest)["group"] == "").all()
    res = T.fit_toughness(log_from_obs(both), None, mode="raw", k=1.0, k_w=0.0)
    assert res.series_table.set_index("series")["pair_entries"].tolist() == [10, 10]


# ---- profiles, formats, the data going in --------------------------------------------------------


def make_profile():
    return ScoreProfile(
        "c1", 1000, 200, cash_line=150.0, top10=170.0, top1=190.0, win=215.0, median=130.0, complete=True
    )


def _typed_rows(rows):
    columns = [
        "format",
        "series",
        "season",
        "week",
        "contest_key",
        "slate",
        "place",
        "entries",
        "places_paid",
        "points",
    ]
    df = pd.DataFrame(rows, columns=columns)
    for c in ("place", "entries", "places_paid", "season", "week"):
        df[c] = df[c].astype("Int64")
    df["points"] = df["points"].astype("Float64")
    return df


def test_a_profile_adds_five_half_weight_points_to_its_format():
    obs, skipped = T.build_observations(_typed_rows([]), None, [(make_profile(), "SE GPP", "S", 2025, 6)])
    assert len(obs) == 5 and set(obs["kind"]) == {"profile"} and (obs["weight"] == T.PROFILE_WEIGHT).all()
    cash = obs.iloc[4]  # the last point is the cash line
    assert cash["x_raw"] == 150.0 and cash["y"] == pytest.approx(float(T.logit((200 - 0.5) / 1000)))
    assert (
        math.isnan(cash["x_idx"]) and skipped["no_index"] == 0
    )  # no index given: only the index model cares


def test_the_index_column_divides_the_score_but_the_raw_column_never_does():
    index = pd.DataFrame({"season": [2025], "week": [6], "index": [1.25]})
    obs, _ = T.build_observations(_typed_rows([]), index, [(make_profile(), "SE GPP", "S", 2025, 6)])
    assert obs.iloc[4]["x_raw"] == 150.0 and obs.iloc[4]["x_idx"] == pytest.approx(150.0 / 1.25)


def test_rows_are_skipped_and_counted_by_reason():
    rows = [
        ("SE GPP", "ok", 2025, 6, "c1", "main", 100, 1000, 200, 150.0),
        ("SE GPP", "thursday", 2025, 6, "c2", "other", 100, 1000, 200, 150.0),
        ("SE GPP", "no index week", 2025, 9, "c3", "main", 100, 1000, 200, 150.0),
        ("SE GPP", "unscored", 2025, 6, "c4", "main", None, 1000, 200, 150.0),
        ("H2H", "H2H $1", 2025, 6, "c5", "main", 1, 2, 1, 150.0),
        ("Other", "best ball", 2025, 6, "c6", "main", 5, 100, 20, 900.0),
        ("Showdown", "sd", 2025, 9, "c7", "other", 100, 1000, 200, 120.0),
    ]
    index = pd.DataFrame({"season": [2025], "week": [6], "index": [1.0]})
    obs, skipped = T.build_observations(_typed_rows(rows), index)
    assert sorted(obs["series"]) == ["no index week", "ok", "sd"]  # a missing index no longer drops the row
    assert skipped == {"unscored": 1, "not_fitted_format": 2, "other_slate": 1, "no_index": 1, "no_week": 0}
    kept = obs.set_index("series")
    assert math.isnan(kept.loc["no index week", "x_idx"]) and kept.loc["sd", "x_idx"] == 120.0


def test_scores_are_raw_and_finish_is_mid_rank():
    rows = [("SE GPP", "s", 2025, 6, "c1", "main", 1, 1000, 200, 180.0)]
    index = pd.DataFrame({"season": [2025], "week": [6], "index": [1.2]})
    obs, _ = T.build_observations(_typed_rows(rows), index)
    assert obs.iloc[0]["x_raw"] == 180.0 and obs.iloc[0]["x_idx"] == pytest.approx(150.0)
    assert obs.iloc[0]["y"] == pytest.approx(float(T.logit(0.5 / 1000)))  # a winner is finite, not -inf
    assert obs.iloc[0]["q_cash"] == pytest.approx(199.5 / 1000)
    assert obs.iloc[0]["wgroup"] == T.CLASSIC_GROUP


def test_fit_toughness_on_nothing_reports_nothing_to_fit():
    res = T.fit_toughness(_typed_rows([]), None)
    assert res.series_table.empty and res.notes == ["no scored entries to fit"]


def test_a_format_below_the_minimum_is_named_in_the_notes():
    rows = [("SE GPP", "s", 2025, 6, f"c{i}", "main", 100 + i, 1000, 200, 150.0 + i) for i in range(3)]
    res = T.fit_toughness(_typed_rows(rows), None)
    assert any("fewer than" in n for n in res.notes) and res.model.formats == {}


# ---- head to head --------------------------------------------------------------------------------


def h2h_log(rows):
    """rows: (opponent, points, winnings, week, slate)"""
    return pd.DataFrame(
        {
            "format": S.H2H,
            "opponent": [r[0] for r in rows],
            "points": [r[1] for r in rows],
            "winnings": [r[2] for r in rows],
            "fee": 1.0,
            "place": [1 if r[2] > 1 else 2 for r in rows],
            "season": 2025,
            "week": [r[3] for r in rows],
            "slate": [r[4] for r in rows],
        }
    )


def test_h2h_win_rate_by_score_band_and_the_record_per_opponent():
    rows = [
        ("ann", 90.0, 0.0, 1, "main"),  # <100: L
        ("ann", 150.0, 1.8, 2, "main"),  # 140-160: W
        ("bob", 155.0, 1.8, 3, "main"),  # 140-160: W
        ("bob", 145.0, 0.0, 4, "main"),  # 140-160: L
        ("bob", 165.0, 1.8, 5, "main"),  # 160-180: W
        ("cat", 190.0, 1.8, 6, "main"),  # >=180: W
        ("cat", 120.0, 1.0, 7, "main"),  # 120-140: refunded fee: a tie
        ("dan", 150.0, 0.0, 8, "other"),  # Thursday: no band, but still a record
    ]
    bands, opp = T.h2h_report(h2h_log(rows))
    by = bands.set_index("band")
    assert by.loc["<100", ["n", "wins", "losses"]].tolist() == [1, 0, 1]
    assert by.loc["140-160", ["n", "wins", "losses", "ties"]].tolist() == [3, 2, 1, 0]
    assert by.loc["140-160", "win_rate"] == pytest.approx(2 / 3)
    assert by.loc["120-140", "ties"] == 1 and by.loc["120-140", "win_rate"] == 0.5
    assert by.loc[">=180", "win_rate"] == 1.0
    assert "dan" not in set(opp["opponent"]) and by["n"].sum() == 7  # the Thursday game has no band
    records = opp.set_index("opponent")
    assert set(records.index) == {"ann", "bob", "cat"}  # 2+ meetings only (dan met once)
    assert records.loc["bob", "record"] == "2-1" and records.loc["ann", "record"] == "1-1"
    assert records.loc["cat", "record"] == "1-0-1" and records.loc["bob", "meetings"] == 3
    assert records.loc["bob", "net"] == pytest.approx(0.8 - 1.0 + 0.8)


def test_bands_are_in_score_order_and_empty_when_there_is_no_h2h():
    bands, opp = T.h2h_report(h2h_log([]))
    assert bands.empty and opp.empty
    rows = [("a", 190.0, 1.8, 1, "main"), ("b", 90.0, 0.0, 2, "main"), ("c", 130.0, 1.8, 3, "main")]
    bands, _ = T.h2h_report(h2h_log(rows))
    assert list(bands["band"]) == ["<100", "120-140", ">=180"]


def test_h2h_bands_use_raw_points_and_ignore_the_slate_index():
    rows = [("a", 180.0, 1.8, 1, "main")]
    bands, _ = T.h2h_report(h2h_log(rows))
    assert list(bands["band"]) == [">=180"]  # nothing divides by an index


def test_h2h_through_the_real_history_log(tmp_path):
    from pathlib import Path

    export = Path(__file__).parent / "fixtures" / "history_export.csv"
    history.merge_into(tmp_path / "log.csv", export)
    typed = history.typed(history.read_log(tmp_path / "log.csv"))
    bands, opp = T.h2h_report(typed)
    # three H2H rows: 150 beat rival_one, 130 lost to rival_one, 155 beat rival_two
    assert bands.set_index("band")[["n", "wins"]].to_dict("index") == {
        "120-140": {"n": 1, "wins": 0},
        "140-160": {"n": 2, "wins": 2},
    }
    assert list(opp["opponent"]) == ["rival_one"] and list(opp["record"]) == ["1-1"]
