"""R6 study: thresholds come from the fit seasons only, folds are whole seasons, the verdict rules, the
overlap rule, and the shuffle baseline (deterministic under a seed, permuting only within
position-season-week)."""

import numpy as np
import pandas as pd
import pytest

from dfs.research import r6_usage as R
from dfs.research.common import FIT_SEASONS, OFFENSE_POSITIONS, TEST_SEASONS
from dfs.research.stats import cluster_diff

SEASONS = [2019, 2020, 2021, 2022, 2023]  # three fit seasons, two test seasons
FEATURES = sorted({col for c in R.CANDIDATES for shape in c.shapes for col in R.spec_features(c, shape)})


def make_frame(seed=0, players=90, weeks=17, plant=None) -> pd.DataFrame:
    """A synthetic UM-population frame: every feature the candidates read, pure-noise residuals, and
    (optionally) one planted effect: `plant = (feature, position, threshold, effect)` adds `effect`
    points to every row of `position` whose `feature` is at or below `threshold`."""
    rng = np.random.default_rng(seed)
    rows = []
    for pos in OFFENSE_POSITIONS:
        for p in range(players):
            for season in SEASONS:
                for week in range(1, weeks + 1):
                    rows.append((f"{pos}{p}", pos, season, week))
    f = pd.DataFrame(rows, columns=["gsis_id", "position", "season", "week"])
    for col in FEATURES:
        f[col] = np.abs(rng.normal(0, 1.5, len(f))) if col.endswith("_l3") else rng.normal(0, 1.5, len(f))
    f["split"] = np.where(f["season"].isin(FIT_SEASONS), "fit", "test")
    noise = rng.normal(0, 4, len(f))
    if plant:
        feature, pos, threshold, effect = plant
        noise = noise + np.where((f["position"] == pos) & (f[feature] <= threshold), effect, 0.0)
    f["resid"] = noise
    f["resid_c"] = f["resid"] - f.groupby(["position", "season"])["resid"].transform("mean")
    f["resid_m"] = f["resid_c"]
    f["resid_l8_c"] = f["resid_c"]
    return f


@pytest.fixture(scope="module")
def noise_frame():
    return make_frame(seed=1)


@pytest.fixture(scope="module")
def planted_frame():
    return make_frame(seed=2, plant=("tgt_pg_chg", "TE", -2.0, 1.5))


# --------------------------------------------------------------------------------------------------------
# The statistics
# --------------------------------------------------------------------------------------------------------


def test_the_precomputed_cluster_diff_matches_stats_cluster_diff():
    rng = np.random.default_rng(3)
    x = rng.normal(0, 3, 400)
    flag = rng.random(400) < 0.3
    players = rng.integers(0, 40, 400)
    codes, uniques = pd.factorize(players)
    fast = R.diff_stats(x, flag, codes, len(uniques))
    slow = cluster_diff(x, flag, players)
    for fast_key, slow_key in (("diff", "diff"), ("se", "se"), ("lo", "lo"), ("hi", "hi"), ("t", "t")):
        assert fast[fast_key] == pytest.approx(slow[slow_key])
    assert fast["n_flag"] == slow["n_flag"] and fast["n_other"] == slow["n_other"]


def test_an_empty_group_gives_nan_not_an_error():
    out = R.diff_stats(np.arange(5.0), np.zeros(5, dtype=bool), np.arange(5), 5)
    assert out["n_flag"] == 0 and np.isnan(out["diff"])


# --------------------------------------------------------------------------------------------------------
# Folds are by season; thresholds are chosen on the fit seasons only
# --------------------------------------------------------------------------------------------------------


def test_the_split_is_by_season_and_no_season_is_in_both():
    assert set(FIT_SEASONS).isdisjoint(TEST_SEASONS)
    base = pd.DataFrame(
        {
            "gsis_id": [f"p{i}" for i in range(40)],
            "position": ["RB"] * 40,
            "season": [2020, 2021, 2022, 2023] * 10,
            "week": 1,
            "t": [2020 * 100 + 1, 2021 * 100 + 1, 2022 * 100 + 1, 2023 * 100 + 1] * 10,
            "um": np.arange(40, dtype=float),
            "dk": np.arange(40, dtype=float) + 1,
            "resid": np.ones(40),
            "resid_l8": np.ones(40),
        }
    )
    feats = base[["gsis_id", "season", "week"]].assign(team="AAA")
    f = R.signal_frame(base, feats)
    by_season = f.groupby("season")["split"].agg(lambda s: set(s))
    assert all(len(v) == 1 for v in by_season)
    assert {s: next(iter(v)) for s, v in by_season.items()} == {
        2020: "fit",
        2021: "fit",
        2022: "test",
        2023: "test",
    }


def test_the_um_matched_residual_has_mean_zero_within_every_position_season_um_decile():
    n = 400
    rng = np.random.default_rng(5)
    base = pd.DataFrame(
        {
            "gsis_id": [f"p{i}" for i in range(n)],
            "position": "TE",
            "season": 2021,
            "week": 1,
            "t": 202101,
            "um": rng.normal(10, 3, n),
            "resid": rng.normal(0, 4, n),
            "resid_l8": rng.normal(0, 4, n),
        }
    )
    base["dk"] = base["um"] + base["resid"]
    f = R.signal_frame(base, base[["gsis_id", "season", "week"]].assign(team="AAA"))
    assert f["um_decile"].nunique() == R.UM_BINS
    means = f.groupby(["position", "season", "um_decile"])["resid_m"].mean()
    assert np.allclose(means, 0.0, atol=1e-9)


def test_grids_and_the_chosen_threshold_use_the_fit_seasons_only(planted_frame):
    f = planted_frame
    spec = R.build_spec(f, R.CANDIDATE_BY_KEY["tgt_pg"], "TE", "change_down")
    before = R.evaluate_spec(spec, f["resid_c"].to_numpy())
    # Wreck the test seasons: absurd feature values and absurd residuals.
    wrecked = f.copy()
    test_rows = wrecked["split"] == "test"
    wrecked.loc[test_rows, "tgt_pg_chg"] = 99.0
    wrecked.loc[test_rows, "resid_c"] = 50.0
    spec2 = R.build_spec(wrecked, R.CANDIDATE_BY_KEY["tgt_pg"], "TE", "change_down")
    after = R.evaluate_spec(spec2, wrecked["resid_c"].to_numpy())
    assert spec.rules == spec2.rules  # the thresholds are fit-season quantiles
    assert before["chosen"] == after["chosen"]
    for key in ("diff", "t", "n_flag"):
        assert before["thresholds"][before["chosen"]]["fit"][key] == pytest.approx(
            after["thresholds"][after["chosen"]]["fit"][key]
        )


def test_a_threshold_needs_enough_flagged_fit_rows_to_be_chosen(noise_frame):
    spec = R.build_spec(noise_frame, R.CANDIDATE_BY_KEY["tgt_pg"], "TE", "level_hi")
    ev = R.evaluate_spec(spec, noise_frame["resid_c"].to_numpy())
    for row in ev["thresholds"]:
        assert row["eligible"] == (
            row["fit"]["n_flag"] >= R.MIN_FLAGGED and row["fit"]["n_other"] >= R.MIN_FLAGGED
        )
    tiny = noise_frame.groupby("position", group_keys=False).head(120)
    spec = R.build_spec(tiny, R.CANDIDATE_BY_KEY["tgt_pg"], "TE", "level_hi")
    assert R.evaluate_spec(spec, tiny["resid_c"].to_numpy())["chosen"] is None


def test_rules_that_flag_everyone_or_no_one_are_not_thresholds():
    f = make_frame(seed=4, players=40, weeks=10)
    f["tgt_pg_chg"] = 0.0  # a metric that never moves: every quantile is 0
    spec = R.build_spec(f, R.CANDIDATE_BY_KEY["tgt_pg"], "WR", "change_up")
    assert spec.flags.shape[1] == 0


# --------------------------------------------------------------------------------------------------------
# Verdicts
# --------------------------------------------------------------------------------------------------------


def stat(diff, lo, hi, n):
    return {"diff": diff, "lo": lo, "hi": hi, "n_flag": n}


def test_keep_needs_the_same_sign_the_size_the_interval_and_the_sample():
    fit = stat(0.8, 0.2, 1.4, 900)
    assert R.classify(fit, stat(0.9, 0.3, 1.5, 300))["verdict"] == "keep"
    assert R.classify(fit, stat(-0.9, -1.5, -0.3, 300))["verdict"] == "drop"  # opposite sign
    small = R.classify(fit, stat(0.4, 0.1, 0.7, 300))
    assert small["verdict"] == "context" and "smaller" in small["reasons"][0]
    wide = R.classify(fit, stat(0.9, -0.1, 1.9, 300))
    assert wide["verdict"] == "context" and "includes 0" in wide["reasons"][0]
    thin = R.classify(fit, stat(0.9, 0.3, 1.5, 120))
    assert thin["verdict"] == "context" and "120" in thin["reasons"][0]
    assert R.classify(fit, stat(0.4, -0.1, 0.9, 300))["verdict"] == "drop"  # two misses is not a near-miss
    assert R.classify(stat(-0.8, -1.4, -0.2, 900), stat(-0.5, -0.9, -0.1, 150))["verdict"] == "keep"


# --------------------------------------------------------------------------------------------------------
# Overlap
# --------------------------------------------------------------------------------------------------------


def test_jaccard():
    a = np.array([1, 1, 1, 0, 0], dtype=bool)
    b = np.array([0, 1, 1, 1, 0], dtype=bool)
    assert R.jaccard(a, b) == pytest.approx(2 / 4)
    assert R.jaccard(np.zeros(3, bool), np.zeros(3, bool)) == 0.0


def test_the_stronger_of_two_signals_flagging_the_same_players_survives():
    n = 100
    base = np.zeros(n, dtype=bool)
    base[:40] = True
    near_copy = base.copy()
    near_copy[:3] = False
    near_copy[50:53] = True  # 37 of 43 in common: Jaccard > 0.5
    other = np.zeros(n, dtype=bool)
    other[60:90] = True
    flags = {"a": base, "b": near_copy, "c": other, "d": base}
    recs = [
        {"id": "a", "position": "TE", "t": 3.0},
        {"id": "b", "position": "TE", "t": 5.0},  # stronger than a: b survives, a is redundant
        {"id": "c", "position": "TE", "t": 2.0},  # does not overlap either
        {"id": "d", "position": "WR", "t": 1.0},  # identical flags but a different position: not compared
    ]
    kept, redundant, pairs = R.resolve_overlap(recs, flags, lambda r: r["t"])
    assert [r["id"] for r in kept] == ["b", "c", "d"]
    assert redundant == {"a": {"with": "b", "jaccard": pytest.approx(R.jaccard(base, near_copy))}}
    assert all({p["a"], p["b"]} != {"d", "a"} for p in pairs)


# --------------------------------------------------------------------------------------------------------
# A planted signal is found; noise is not
# --------------------------------------------------------------------------------------------------------


def test_a_planted_effect_is_kept_in_both_periods_and_in_the_matched_residual(planted_frame):
    res = R.run_study(planted_frame, n_shuffles=2, seed=1)
    rec = {r["id"]: r for r in res["signals"]}["tgt_pg|TE|change_down"]
    assert rec["verdict"] == "keep" and rec["survives_um_matching"]
    assert rec["fit"]["diff"] > 0.5 and rec["test"]["diff"] > 0.5
    assert rec["test"]["lo90"] > 0 and rec["test"]["n_flagged"] >= R.MIN_TEST_N
    assert rec["um_matched"]["verdict"] == "keep"
    assert "tgt_pg|TE|change_down" in res["keeps"]


def test_pure_noise_keeps_few_signals_and_the_shuffle_baseline_says_how_few(noise_frame):
    res = R.run_study(noise_frame, n_shuffles=6, seed=7)
    n_specs = len(res["signals"])
    kept = len(res["keeps"])
    assert kept <= 0.15 * n_specs
    assert res["shuffle"]["brief_rule"]["expected_keeps_by_chance"] < 0.15 * n_specs


# --------------------------------------------------------------------------------------------------------
# The shuffle
# --------------------------------------------------------------------------------------------------------


def test_permute_within_only_moves_values_inside_their_group():
    rng = np.random.default_rng(0)
    values = np.arange(60, dtype=float)
    groups = np.repeat(np.arange(6), 10)
    out = R.permute_within(values, groups, rng)
    for g in range(6):
        assert sorted(out[groups == g]) == sorted(values[groups == g])
    assert not np.array_equal(out, values)


def test_shuffle_groups_are_position_season_week_never_across_seasons_or_weeks(noise_frame, monkeypatch):
    seen = {}
    real = R.permute_within

    def spy(values, groups, rng):
        seen["groups"] = groups.copy()
        return real(values, groups, rng)

    monkeypatch.setattr(R, "permute_within", spy)
    specs = R.build_specs(noise_frame)[:2]
    R.shuffle_baseline(noise_frame, specs, n_shuffles=1, seed=3)
    labels = (
        noise_frame["position"]
        + "|"
        + noise_frame["season"].astype(str)
        + "|"
        + noise_frame["week"].astype(str)
    )
    assert pd.Series(seen["groups"]).nunique() == labels.nunique()
    assert (pd.crosstab(seen["groups"], labels.to_numpy()) > 0).sum(axis=1).eq(1).all()


def test_the_shuffle_baseline_is_deterministic_under_a_seed(noise_frame):
    specs = R.build_specs(noise_frame)[:12]
    a = R.shuffle_baseline(noise_frame, specs, n_shuffles=5, seed=11)
    b = R.shuffle_baseline(noise_frame, specs, n_shuffles=5, seed=11)
    assert a == b
    c = R.shuffle_baseline(noise_frame, specs, n_shuffles=5, seed=12)
    assert a["per_spec_keep_rate"] != c["per_spec_keep_rate"] or a["brief_rule"] != c["brief_rule"]


def test_the_shuffle_keeps_the_flags_and_severs_only_their_link_to_the_outcome(planted_frame):
    """On data with a planted effect the shuffled keep rate for that spec collapses toward chance."""
    specs = [s for s in R.build_specs(planted_frame) if s.id == "tgt_pg|TE|change_down"]
    real = R.passes_both(specs[0], planted_frame["resid_c"].to_numpy(), planted_frame["resid_m"].to_numpy())
    assert real == (True, True)
    out = R.shuffle_baseline(planted_frame, specs, n_shuffles=20, seed=5)
    assert out["per_spec_keep_rate"]["tgt_pg|TE|change_down"] <= 0.25
