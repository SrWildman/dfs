"""Score-to-finish curves: the fit, the n/(n+k) shrinkage on data whose truth is known, leave-one-week-out k,
same-week pairs, Showdown kept apart from Classic, H2H."""

import math

import numpy as np
import pandas as pd
import pytest

from dfs.contests import history
from dfs.contests import series as S
from dfs.contests import toughness as T
from dfs.contests.standings import ScoreProfile

ALPHA, BETA = 7.5, -0.05  # logit(q) = 7.5 - 0.05 x: q = 50% at x = 150, 20% at x = 178


def synthetic_obs(deltas, *, weeks=30, noise=0.0, week_noise=0.0, fmt="SE GPP", seed=1, entries=1000, per=1):
    """Observations from a known truth: `logit q = ALPHA + delta_series + BETA x + week effect + noise`."""
    rng = np.random.default_rng(seed)
    rows = []
    for week in range(1, weeks + 1):
        u = rng.normal(0, week_noise)
        for name, delta in deltas.items():
            for j in range(per):
                x = rng.normal(150, 20)
                y = ALPHA + delta + BETA * x + u + rng.normal(0, noise)
                rows.append(
                    {
                        "kind": "entry",
                        "format": fmt,
                        "series": name,
                        "season": 2025,
                        "week": week,
                        "contest_key": f"{name}-{week}-{j}",
                        "x": x,
                        "y": float(np.clip(y, -T.LOGIT_CLIP, T.LOGIT_CLIP)),
                        "weight": 1.0,
                        "group": "",
                        "q_cash": 0.2,
                        "points": round(x, 2),
                    }
                )
    return pd.DataFrame(rows)


# ---- the format curve ----------------------------------------------------------------------------


def test_the_format_fit_recovers_a_noiseless_curve():
    fit = T.fit_format(synthetic_obs({"A": 0.0, "B": 0.0}), "SE GPP")
    assert fit.alpha == pytest.approx(ALPHA) and fit.beta == pytest.approx(BETA) and fit.reliable


def test_a_format_with_too_little_data_or_no_spread_has_no_curve():
    assert T.fit_format(synthetic_obs({"A": 0.0}, weeks=3), "SE GPP") is None
    flat = synthetic_obs({"A": 0.0}, weeks=20)
    flat["x"] = 150.0
    assert T.fit_format(flat, "SE GPP") is None


def test_a_positive_slope_is_flagged_unreliable_and_produces_no_cash_line():
    obs = synthetic_obs({"A": 0.0})
    obs["y"] = -obs["y"]  # more points, worse finish: nonsense
    fit = T.fit_format(obs, "SE GPP")
    assert not fit.reliable and "not negative" in fit.note
    model = T.fit_model(obs, 10.0)
    assert math.isnan(model.predict_q("SE GPP", "A", 150.0))


def test_the_curve_is_a_logistic_in_score():
    model = T.fit_model(synthetic_obs({"A": 0.0}), 10.0)
    assert model.predict_q("SE GPP", "A", 150.0) == pytest.approx(0.5, abs=1e-6)
    assert model.predict_q("SE GPP", "A", 178.0) == pytest.approx(1 / (1 + math.exp(-(ALPHA + BETA * 178))))
    assert model.predict_q("SE GPP", "A", 200.0) < model.predict_q("SE GPP", "A", 100.0)


# ---- shrinkage on a set where the truth is known -------------------------------------------------


def test_series_deltas_are_shrunk_by_exactly_n_over_n_plus_k():
    """Noiseless data, the format curve held at the truth: delta_hat = n / (n + k) * delta_true."""
    deltas = {"hard": 1.0, "easy": -1.0}
    obs = synthetic_obs(deltas, weeks=30)
    fit = T.FormatFit("SE GPP", ALPHA, BETA, 60, 0, True)
    for k in (0.0, 10.0, 90.0):
        got = T.solve_series(obs, fit, k)
        factor = 30 / (30 + k)
        assert got["hard"] == pytest.approx(1.0 * factor) and got["easy"] == pytest.approx(-1.0 * factor)


def test_a_series_with_few_entries_is_shrunk_harder_than_one_with_many():
    big, small = synthetic_obs({"big": 1.0}, weeks=100), synthetic_obs({"small": 1.0}, weeks=5)
    obs = pd.concat([big, small], ignore_index=True)
    fit = T.FormatFit("SE GPP", ALPHA, BETA, 105, 0, True)
    got = T.solve_series(obs, fit, 10.0)
    assert got["big"] == pytest.approx(100 / 110) and got["small"] == pytest.approx(5 / 15)
    assert got["small"] < got["big"] < 1.0


def test_with_k_zero_the_series_level_is_its_own_mean_residual():
    obs = synthetic_obs({"A": 0.7, "B": 0.0, "C": -0.7}, weeks=20)
    fit = T.FormatFit("SE GPP", ALPHA, BETA, 60, 0, True)
    got = T.solve_series(obs, fit, 0.0)
    assert (got["A"], got["B"], got["C"]) == pytest.approx((0.7, 0.0, -0.7))


def test_end_to_end_recovery_with_noise_and_the_direction_of_toughness():
    truth = {"hard": 0.8, "mid": 0.0, "easy": -0.8}
    obs = synthetic_obs(truth, weeks=150, noise=0.5, seed=3)
    model = T.fit_model(obs, 10.0)
    for name, delta in truth.items():
        shrink = 150 / 160
        assert model.deltas[("SE GPP", name)] == pytest.approx(delta * shrink, abs=0.15)
    assert (
        model.deltas[("SE GPP", "hard")] > model.deltas[("SE GPP", "mid")] > model.deltas[("SE GPP", "easy")]
    )


def log_from_obs(obs, *, places_paid=200, entries=1000):
    """A `history.typed`-shaped frame for `fit_toughness` from synthetic observations."""
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
            "points": obs["x"],
        }
    )


def flat_index(obs):
    weeks = obs[["season", "week"]].drop_duplicates()
    return weeks.assign(index=1.0)


def test_toughness_table_orders_series_and_reports_n_cash_line_and_toughness():
    obs = synthetic_obs({"hard": 0.8, "easy": -0.8}, weeks=60, noise=0.2, seed=5)
    res = T.fit_toughness(history_like(obs), flat_index(obs), k=5.0)
    table = res.series_table.set_index("series")
    assert table.loc["hard", "n"] == 60 and table.loc["easy", "n"] == 60
    assert table.loc["hard", "toughness"] > 0 > table.loc["easy", "toughness"]
    assert table.loc["hard", "expected_cash_line"] > table.loc["easy", "expected_cash_line"]
    # toughness is -delta / beta points, and the series cash line is the format's plus that
    delta = table.loc["hard", "delta"]
    assert table.loc["hard", "toughness"] == pytest.approx(-delta / res.model.formats["SE GPP"].beta)
    assert table.loc["hard", "expected_cash_line"] - table.loc["hard", "format_cash_line"] == pytest.approx(
        table.loc["hard", "toughness"]
    )
    assert table.loc["hard", "shrink_weight"] == pytest.approx(60 / 65)
    fmt = res.format_table.set_index("format").loc["SE GPP"]
    assert fmt["reliable"] and fmt["beta"] < 0 and fmt["n_entries"] == 120


def history_like(obs, **kw):
    """`fit_toughness` reads `history.typed` columns; place is derived from the synthetic finish."""
    return log_from_obs(obs, **kw)


# ---- leave-one-week-out k ------------------------------------------------------------------------


def test_leave_one_week_out_prefers_little_shrinkage_when_series_really_differ():
    obs = synthetic_obs({"A": 1.5, "B": -1.5, "C": 0.7, "D": -0.7}, weeks=40, noise=0.5, seed=2)
    k, errors, source = T.choose_k(obs)
    assert k <= 2.0 and "leave-one-week-out" in source
    by_k = errors.set_index("k")["mae"]
    assert by_k[1e9] > 2 * by_k[0.0]  # collapsing every series onto the format is far worse


def test_leave_one_week_out_tolerates_heavy_shrinkage_when_series_do_not_differ():
    obs = synthetic_obs({"A": 0.0, "B": 0.0, "C": 0.0}, weeks=40, noise=0.5, seed=7)
    _, errors, _ = T.choose_k(obs)
    by_k = errors.set_index("k")["mae"]
    assert by_k[1e9] <= by_k[0.0] * 1.03  # shrinking everything to the format costs (almost) nothing


def test_the_chosen_k_is_the_grid_minimum_and_ties_go_to_the_larger_k():
    obs = synthetic_obs({"A": 1.5, "B": -1.5}, weeks=40, noise=0.4, seed=4)
    k, errors, _ = T.choose_k(obs)
    best = errors["mae"].min()
    assert errors.set_index("k").loc[k, "mae"] == pytest.approx(best)
    assert k == errors[errors["mae"] <= best + 1e-12]["k"].max()


def test_too_little_data_falls_back_to_the_default_k_and_says_so():
    obs = synthetic_obs({"A": 0.5}, weeks=6)
    k, errors, source = T.choose_k(obs)
    assert k == T.DEFAULT_K and errors.empty and "too little" in source


def test_a_held_out_week_never_influences_its_own_prediction():
    obs = synthetic_obs({"A": 1.0, "B": -1.0}, weeks=12, noise=0.3, seed=8)
    before = T.fold_predictions(obs, 2025, 6, grid=(5.0,))[5.0]
    distorted = obs.copy()
    distorted.loc[distorted["week"] == 6, "y"] += 3.0  # change week 6's own outcomes
    after = T.fold_predictions(distorted, 2025, 6, grid=(5.0,))[5.0]
    assert [round(p, 12) for p, _ in before] == [round(p, 12) for p, _ in after]  # same predictions...
    assert [a for _, a in before] != [a for _, a in after]  # ...against different actuals
    # while another week's fold does learn from week 6
    other = T.fold_predictions(distorted, 2025, 7, grid=(5.0,))[5.0]
    assert [round(p, 9) for p, _ in other] != [
        round(p, 9) for p, _ in T.fold_predictions(obs, 2025, 7, grid=(5.0,))[5.0]
    ]


def test_every_entry_is_scored_exactly_once_across_the_folds():
    obs = synthetic_obs({"A": 1.0, "B": -1.0}, weeks=12, noise=0.3, seed=8)
    assert T.leave_one_week_out(obs, grid=(5.0,))["n_scored"].iloc[0] == len(obs)


# ---- same-week pairs -----------------------------------------------------------------------------


def paired_obs(*, weeks=60, week_noise=1.0, noise=0.1, seed=11):
    """Each week one lineup (one score) is entered in both series `hard` and `easy`; a big week-level shock
    hits both. Truth: hard = +0.5, easy = -0.5."""
    rng = np.random.default_rng(seed)
    rows = []
    for week in range(1, weeks + 1):
        u, x = rng.normal(0, week_noise), rng.normal(150, 20)
        for name, delta in (("hard", 0.5), ("easy", -0.5)):
            rows.append(
                {
                    "kind": "entry",
                    "format": "SE GPP",
                    "series": name,
                    "season": 2025,
                    "week": week,
                    "contest_key": f"{name}-{week}",
                    "x": x,
                    "y": ALPHA + delta + BETA * x + u + rng.normal(0, noise),
                    "weight": 1.0,
                    "group": "",
                    "q_cash": 0.2,
                    "points": round(x, 2),
                }
            )
    return pd.DataFrame(rows)


def test_pair_groups_are_lineups_entered_in_two_contests_of_one_format_in_one_week():
    obs = paired_obs(weeks=5)
    obs.loc[len(obs)] = {**obs.iloc[0].to_dict(), "contest_key": "same-contest-again"}  # a third copy
    grouped = T.assign_pair_groups(obs)
    assert (grouped["group"] != "").sum() == 11  # the 10 pairs + the extra copy of week 1's lineup
    solo = obs.copy()
    solo["points"] = np.arange(len(solo)) + 0.5  # distinct lineups: no groups
    assert (T.assign_pair_groups(solo)["group"] == "").all()
    one_contest = obs.copy()
    one_contest["contest_key"] = "same"  # same score twice in ONE contest is not a pair across series
    assert (T.assign_pair_groups(one_contest)["group"] == "").all()


def test_pairs_cancel_the_week_shock_and_recover_the_series_gap_better():
    obs = T.assign_pair_groups(paired_obs())
    fit = T.fit_format(obs, "SE GPP")
    with_pairs = T.solve_series(obs, fit, 0.0, use_pairs=True)
    without = T.solve_series(obs, fit, 0.0, use_pairs=False)
    gap_with = with_pairs["hard"] - with_pairs["easy"]
    gap_without = without["hard"] - without["easy"]
    assert abs(gap_with - 1.0) < 0.1
    assert abs(gap_with - 1.0) <= abs(gap_without - 1.0) + 1e-9
    assert T.estimate_lambda(obs, fit) > 1.0  # the week shock makes the paired deviations the cleaner signal


def test_lambda_is_one_without_enough_pair_groups():
    obs = T.assign_pair_groups(paired_obs(weeks=T.MIN_PAIR_GROUPS - 1))
    fit = T.FormatFit("SE GPP", ALPHA, BETA, len(obs), 0, True)
    assert T.estimate_lambda(obs, fit) == 1.0


def test_without_pairs_the_solution_is_exactly_the_shrinkage_formula():
    obs = synthetic_obs({"A": 1.0}, weeks=20)
    fit = T.FormatFit("SE GPP", ALPHA, BETA, 20, 0, True)
    assert T.solve_series(obs, fit, 5.0, use_pairs=True)["A"] == pytest.approx(
        T.solve_series(obs, fit, 5.0, use_pairs=False)["A"]
    )


# ---- profiles, formats, the data going in --------------------------------------------------------


def make_profile():
    return ScoreProfile(
        "c1", 1000, 200, cash_line=150.0, top10=170.0, top1=190.0, win=215.0, median=130.0, complete=True
    )


def test_a_profile_adds_five_half_weight_points_to_its_format():
    index = pd.DataFrame({"season": [2025], "week": [6], "index": [1.0]})
    obs, skipped = T.build_observations(_typed_rows([]), index, [(make_profile(), "SE GPP", "S", 2025, 6)])
    assert len(obs) == 5 and set(obs["kind"]) == {"profile"} and (obs["weight"] == T.PROFILE_WEIGHT).all()
    assert skipped["no_index"] == 0
    cash = obs.iloc[4]  # the last point is the cash line
    assert cash["x"] == 150.0 and cash["y"] == pytest.approx(float(T.logit((200 - 0.5) / 1000)))


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


def test_profile_points_are_divided_by_the_slate_index():
    index = pd.DataFrame({"season": [2025], "week": [6], "index": [1.25]})
    obs, _ = T.build_observations(_typed_rows([]), index, [(make_profile(), "SE GPP", "S", 2025, 6)])
    assert obs.iloc[4]["x"] == pytest.approx(150.0 / 1.25)


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
    assert sorted(obs["series"]) == ["ok", "sd"]  # Showdown needs no main-slate index and any slate
    assert skipped == {"unscored": 1, "not_fitted_format": 2, "other_slate": 1, "no_index": 1, "no_week": 0}


def test_scores_are_divided_by_the_weeks_slate_index_and_finish_is_mid_rank():
    rows = [("SE GPP", "s", 2025, 6, "c1", "main", 1, 1000, 200, 180.0)]
    index = pd.DataFrame({"season": [2025], "week": [6], "index": [1.2]})
    obs, _ = T.build_observations(_typed_rows(rows), index)
    assert obs.iloc[0]["x"] == pytest.approx(150.0)
    assert obs.iloc[0]["y"] == pytest.approx(float(T.logit(0.5 / 1000)))  # a winner is finite, not -inf
    assert obs.iloc[0]["q_cash"] == pytest.approx(199.5 / 1000)


def test_showdown_is_fitted_on_its_own_and_never_pooled_with_classic():
    classic = synthetic_obs({"A": 0.0}, weeks=30, seed=1, fmt="SE GPP")
    showdown = synthetic_obs({"B": 0.0}, weeks=30, seed=2, fmt="Showdown")
    showdown["x"] = showdown["x"] / 2  # a different scale: raw Showdown points
    showdown["y"] = ALPHA + 1.0 + BETA * showdown["x"] * 2  # and a different curve entirely
    both = pd.concat([classic, showdown], ignore_index=True)
    fits = {fmt: T.fit_format(both, fmt) for fmt in ("SE GPP", "Showdown")}
    alone = T.fit_format(classic, "SE GPP")
    assert fits["SE GPP"].alpha == pytest.approx(alone.alpha) and fits["SE GPP"].beta == pytest.approx(
        alone.beta
    )
    assert fits["Showdown"].alpha != pytest.approx(fits["SE GPP"].alpha, abs=0.1)
    model = T.fit_model(both, 10.0)
    assert ("Showdown", "B") in model.deltas and ("SE GPP", "B") not in model.deltas
    assert ("SE GPP", "A") in model.deltas and ("Showdown", "A") not in model.deltas


def test_showdown_scores_use_raw_points_and_no_slate_index():
    rows = [("Showdown", "sd", 2025, 9, "c1", "other", 100, 1000, 200, 120.0)] * 1
    obs, _ = T.build_observations(_typed_rows(rows), pd.DataFrame(columns=["season", "week", "index"]))
    assert obs.iloc[0]["x"] == 120.0


def test_fit_toughness_on_nothing_reports_nothing_to_fit():
    res = T.fit_toughness(_typed_rows([]), None)
    assert res.series_table.empty and res.notes == ["no scored entries to fit"]


def test_a_format_below_the_minimum_is_named_in_the_notes():
    rows = [("SE GPP", "s", 2025, 6, f"c{i}", "main", 100 + i, 1000, 200, 150.0 + i) for i in range(3)]
    index = pd.DataFrame({"season": [2025], "week": [6], "index": [1.0]})
    res = T.fit_toughness(_typed_rows(rows), index)
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
    index = pd.DataFrame({"season": 2025, "week": range(1, 9), "index": 1.0})
    bands, opp = T.h2h_report(h2h_log(rows), index)
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
    bands, opp = T.h2h_report(h2h_log([]), None)
    assert bands.empty and opp.empty
    rows = [("a", 190.0, 1.8, 1, "main"), ("b", 90.0, 0.0, 2, "main"), ("c", 130.0, 1.8, 3, "main")]
    index = pd.DataFrame({"season": 2025, "week": [1, 2, 3], "index": 1.0})
    bands, _ = T.h2h_report(h2h_log(rows), index)
    assert list(bands["band"]) == ["<100", "120-140", ">=180"]


def test_h2h_scores_are_put_on_the_average_slate_footing():
    rows = [("a", 180.0, 1.8, 1, "main")]
    index = pd.DataFrame({"season": [2025], "week": [1], "index": [1.2]})
    bands, _ = T.h2h_report(h2h_log(rows), index)
    assert list(bands["band"]) == ["140-160"]  # 180 / 1.2 = 150


def test_h2h_through_the_real_history_log(tmp_path):
    from pathlib import Path

    export = Path(__file__).parent / "fixtures" / "history_export.csv"
    history.merge_into(tmp_path / "log.csv", export)
    typed = history.typed(history.read_log(tmp_path / "log.csv"))
    index = pd.DataFrame({"season": [2025], "week": [6], "index": [1.0]})
    bands, opp = T.h2h_report(typed, index)
    # three H2H rows: 150 beat rival_one, 130 lost to rival_one, 155 beat rival_two
    assert bands.set_index("band")[["n", "wins"]].to_dict("index") == {
        "120-140": {"n": 1, "wins": 0},
        "140-160": {"n": 2, "wins": 2},
    }
    assert list(opp["opponent"]) == ["rival_one"] and list(opp["record"]) == ["1-1"]
