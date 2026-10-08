import numpy as np
import pandas as pd
import pytest

from dfs.sim import correlation as C
from dfs.sim.roles import ROLES


def test_shrink_pulls_toward_zero_by_the_prior_weight():
    # n - 3 = 200 observations against a prior worth 200: exactly halfway in Fisher-z space
    r = np.tanh(0.5)
    assert C.shrink(r, 203) == pytest.approx(np.tanh(0.25))
    assert abs(C.shrink(0.3, 6000)) > 0.29  # lots of data barely moves
    assert abs(C.shrink(0.3, 20)) < 0.04  # little data is mostly prior
    assert C.shrink(0.4, 3) == 0.0
    assert C.shrink(-0.4, 1000) == pytest.approx(-C.shrink(0.4, 1000))


def test_tercile_cutoffs_and_buckets():
    frame = pd.DataFrame(
        {"game_id": [f"g{i}" for i in range(9)], "total": [40, 41, 42, 44, 45, 46, 48, 49, 50.0]}
    )
    lo, hi = C.tercile_cutoffs(frame)
    assert C.total_bucket(39, (lo, hi)) == "low"
    assert C.total_bucket(lo, (lo, hi)) == "mid"
    assert C.total_bucket(hi, (lo, hi)) == "high"


def test_fit_recovers_a_known_correlation_and_finds_nothing_elsewhere(synth_frame):
    fit = C.fit_correlations(synth_frame(rho_qb_wr=0.5, rho_qb_dst=-0.3), n_boot=100, seed=1)
    d = fit.detail
    pooled = d[d["total_bucket"] == "all"].set_index(["relation", "role_a", "role_b"])

    qb_wr = pooled.loc[("same_team", "QB1", "WR1")]
    assert qb_wr["n"] == 3000
    assert qb_wr["r_raw"] == pytest.approx(0.5, abs=0.06)
    assert qb_wr["rho"] == pytest.approx(C.shrink(qb_wr["r_raw"], qb_wr["n"]))
    assert qb_wr["ci_lo"] < qb_wr["rho"] < qb_wr["ci_hi"]

    qb_dst = pooled.loc[("opp", "QB1", "DST")]
    assert qb_dst["r_raw"] == pytest.approx(-0.3, abs=0.06)

    for key in [
        ("same_team", "RB1", "WR2"),
        ("opp", "WR1", "WR1"),
        ("same_team", "QB1", "DST"),
        ("opp", "QB1", "QB1"),
    ]:
        assert abs(pooled.loc[key, "r_raw"]) < 0.07, key


def test_fit_covers_every_role_pair_it_has_data_for(synth_frame):
    fit = C.fit_correlations(synth_frame(n_games=300), n_boot=50)
    shipped = fit.shipped
    pooled = shipped[shipped["total_bucket"] == "all"]
    assert list(shipped.columns) == C.CSV_COLUMNS
    # roles the synthetic frame has: 8 of them; every same-team pair and every opposing pair among them
    assert len(pooled[pooled["relation"] == "same_team"]) == 8 * 7 // 2
    assert len(pooled[pooled["relation"] == "opp"]) == 8 * 9 // 2
    assert (shipped["total_bucket"] == "all").all()  # only pooled values ship


def test_tercile_analysis_is_report_only_and_nothing_but_pooled_ships(synth_frame):
    # No dependence on the game total anywhere. Two 90% intervals failing to overlap is a ~2% event per
    # comparison and each pair has three comparisons, so about 6% of pairs are flagged by chance alone.
    fit = C.fit_correlations(synth_frame(), n_boot=100, seed=2)
    pairs = fit.detail.drop_duplicates(["relation", "role_a", "role_b"])
    flagged = fit.detail[fit.detail["conditional"]].drop_duplicates(["relation", "role_a", "role_b"])
    assert len(flagged) <= 0.2 * len(pairs)
    assert set(fit.detail["total_bucket"]) == {"all", "low", "mid", "high"}  # the analysis is kept ...
    assert set(fit.shipped["total_bucket"]) == {"all"}  # ... but not shipped


def test_even_a_real_total_dependence_ships_only_the_pooled_value(synth_frame):
    frame = synth_frame(n_games=2400, rho_qb_wr={38.5: 0.1, 44.5: 0.4, 50.5: 0.7}, seed=3)
    fit = C.fit_correlations(frame, n_boot=100, seed=3)
    key = (
        (fit.detail["role_a"] == "QB1")
        & (fit.detail["role_b"] == "WR1")
        & (fit.detail["relation"] == "same_team")
    )
    by = fit.detail[key].set_index("total_bucket")["rho"]
    assert by["low"] < by["mid"] < by["high"] and by["low"] < 0.2 and by["high"] > 0.55  # seen in the report
    assert fit.detail[key & (fit.detail["total_bucket"] != "all")]["conditional"].all()
    shipped = fit.shipped[(fit.shipped["role_a"] == "QB1") & (fit.shipped["role_b"] == "WR1")]
    assert shipped["total_bucket"].tolist().count("all") == len(shipped) == 2  # same_team and opp
    assert fit.lookup().rho("same_team", "QB1", "WR1") == by["all"]


def test_conditional_flag_needs_non_overlapping_intervals():
    d = pd.DataFrame(
        {
            "relation": ["opp"] * 6,
            "role_a": ["QB1"] * 3 + ["WR1"] * 3,
            "role_b": ["DST"] * 3 + ["WR2"] * 3,
            "total_bucket": ["low", "mid", "high"] * 2,
            "raw_lo": [0.30, 0.10, 0.00, 0.10, 0.12, 0.08],
            "raw_hi": [0.40, 0.25, 0.20, 0.30, 0.31, 0.25],
        }
    )
    flags = C._flag_conditional(d)
    assert flags.tolist() == [True] * 3 + [False] * 3  # 0.40 vs 0.00..0.20 do not overlap; the others do


def test_fit_is_deterministic_in_its_seed(synth_frame):
    f = synth_frame(n_games=200)
    a = C.fit_correlations(f, n_boot=50, seed=3)
    b = C.fit_correlations(f, n_boot=50, seed=3)
    c = C.fit_correlations(f, n_boot=50, seed=4)
    pd.testing.assert_frame_equal(a.detail, b.detail)
    assert not a.detail["ci_lo"].equals(c.detail["ci_lo"])


def test_normal_scores_are_deterministic_and_standard_normal(synth_frame):
    frame = synth_frame(n_games=1500, rho_qb_wr=0.0, rho_qb_dst=0.0)
    z1 = C.normal_scores(frame, seed=1)
    z2 = C.normal_scores(frame, seed=1)
    pd.testing.assert_series_equal(z1, z2)
    assert abs(z1.mean()) < 0.04 and abs(z1.std() - 1) < 0.06
    assert z1.between(*C.ndtri(list(C.U_CLIP))).all()


def test_cross_game_pairs_are_uncorrelated(synth_frame):
    frame = synth_frame(n_games=1200, rho_qb_wr=0.6, rho_qb_dst=-0.4)
    frame["z"] = C.normal_scores(frame, seed=0)
    cg = C.cross_game_check(frame, seed=0)
    assert len(cg) == len(set(frame["role"])) * (len(set(frame["role"])) + 1) // 2
    assert cg["r"].abs().mean() < C.CROSS_GAME_LIMIT
    # a check that can fail: if "different games" were really the same game, it would see the correlation
    same = C.pair_rows(frame, "same_team", "QB1", "WR1")
    assert np.corrcoef(same["za"], same["zb"])[0, 1] > 0.4


def _table(buckets=("all", "all")):
    return pd.DataFrame(
        {
            "relation": ["same_team", "opp"],
            "role_a": ["QB1", "QB1"],
            "role_b": ["WR1", "DST"],
            "total_bucket": list(buckets),
            "rho": [0.3, -0.3],
            "n": [100, 100],
            "ci_lo": [0.2, -0.4],
            "ci_hi": [0.4, -0.2],
        }
    )


def test_lookup_is_symmetric_and_defaults_to_zero():
    look = C.CorrelationLookup(_table())
    assert look.rho("same_team", "WR1", "QB1") == look.rho("same_team", "QB1", "WR1") == 0.3
    assert look.rho("opp", "DST", "QB1") == -0.3
    assert look.rho("same_team", "RB1", "RB2") == 0.0  # never observed
    assert look.has("opp", "DST", "QB1") and not look.has("opp", "RB1", "RB2")


def test_a_table_with_total_bucket_rows_is_refused():
    # a stale file from before only pooled values shipped must not be half-used
    with pytest.raises(C.CorrelationError, match="dfs sim fit"):
        C.CorrelationLookup(_table(("all", "high")))


def test_a_malformed_table_is_refused():
    with pytest.raises(C.CorrelationError):
        C.CorrelationLookup(pd.DataFrame({"relation": []}))


# --- the table that ships ---------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def shipped():
    return C.load_correlations()


def test_the_shipped_table_has_every_role_pair_in_both_relations():
    table = pd.read_csv(C.SIM_DIR / C.CORRELATIONS_FILE)
    assert list(table.columns) == C.CSV_COLUMNS
    pooled = table[table["total_bucket"] == "all"]
    assert len(pooled[pooled["relation"] == "same_team"]) == len(ROLES) * (len(ROLES) - 1) // 2
    assert len(pooled[pooled["relation"] == "opp"]) == len(ROLES) * (len(ROLES) + 1) // 2
    assert table["rho"].between(-1, 1).all() and (table["ci_lo"] <= table["rho"]).all()
    assert (table["rho"] <= table["ci_hi"]).all() and (table["n"] > 0).all()


def test_the_two_signs_the_literature_agrees_on(shipped):
    assert shipped.rho("same_team", "QB1", "WR1") > 0.2
    assert shipped.rho("opp", "QB1", "DST") < -0.2
    assert shipped.rho("opp", "QB1", "QB1") > 0.1


def test_the_shipped_cross_game_check_passed():
    import json

    meta = json.loads((C.SIM_DIR / C.META_FILE).read_text())
    assert meta["cross_game"]["mean_abs_r"] < C.CROSS_GAME_LIMIT
    assert len(meta["total_cutoffs"]) == 2  # kept for the report-only tercile analysis
    assert (pd.read_csv(C.SIM_DIR / C.CORRELATIONS_FILE)["total_bucket"] == "all").all()


def test_save_and_load_round_trip(synth_frame, tmp_path):
    fit = C.fit_correlations(synth_frame(n_games=300), n_boot=50)
    C.save_fit(fit, tmp_path, extra_meta={"note": "x"})
    assert sorted(p.name for p in tmp_path.iterdir()) == [C.CORRELATIONS_FILE, C.DETAIL_FILE, C.META_FILE]
    loaded = C.load_correlations(tmp_path)
    assert loaded.rho("same_team", "QB1", "WR1") == pytest.approx(
        fit.lookup().rho("same_team", "QB1", "WR1"), abs=1e-5
    )
    with pytest.raises(C.CorrelationError):
        C.load_correlations(tmp_path / "nowhere")


def test_fit_report_text_names_the_literature_pairs(synth_frame):
    from dfs.sim import report

    fit = C.fit_correlations(synth_frame(n_games=400), n_boot=50)
    checks = report.fit_checks(fit, seed=0)
    assert checks["cross_game"]["mean_abs_r"] < C.CROSS_GAME_LIMIT
    text = report.fit_summary(fit, checks)
    for pair in ("QB1-WR1", "QB1-TE1", "QB1-RB1", "QB1-QB1", "QB1-DST"):
        assert pair in text
    assert "Cross-game check" in text and "Total-conditional" in text
    assert report.pooled_table(fit, "opp").count("\n") > 10
    assert "Pair" in report.conditional_table(fit) or "no pair" in report.conditional_table(fit)
