import numpy as np
import pandas as pd
import pytest

from dfs.sim import backtest as B
from dfs.sim import report
from dfs.sim.correlation import CorrelationLookup


def legal_rows(week, ids):
    return week[week["gsis_id"].isin(ids)]


def test_generated_lineups_are_all_legal(synth_week):
    lineups = B.generate_lineups(synth_week, 120, np.random.default_rng(0))
    assert len(lineups) == 120
    for lu in lineups:
        assert B.is_legal(lu.rows), lu.rows[["position", "team", "game_id"]]
        assert len(lu.rows) == 9
    assert sum(lu.stacked for lu in lineups) == 60


def test_every_slot_is_filled_with_at_most_one_flex(synth_week):
    for lu in B.generate_lineups(synth_week, 60, np.random.default_rng(1)):
        n = lu.rows["position"].value_counts()
        assert (n["QB"], n["DST"]) == (1, 1)
        assert n.get("RB", 0) >= 2 and n.get("WR", 0) >= 3 and n.get("TE", 0) >= 1
        assert n.get("RB", 0) + n.get("WR", 0) + n.get("TE", 0) == 7
        assert (n.get("RB", 0) - 2) + (n.get("WR", 0) - 3) + (n.get("TE", 0) - 1) == 1
        assert lu.rows["gsis_id"].is_unique


def test_a_stack_is_qb_two_teammates_and_a_bring_back(synth_week):
    stacks = [lu for lu in B.generate_lineups(synth_week, 80, np.random.default_rng(2)) if lu.stacked]
    assert stacks
    for lu in stacks:
        rows = lu.rows
        qb = rows[rows["position"] == "QB"].iloc[0]
        catchers = rows[rows["position"].isin(["WR", "TE"]) & (rows["game_id"] == qb["game_id"])]
        assert (catchers["team"] == qb["team"]).sum() >= 2
        assert (catchers["team"] != qb["team"]).sum() >= 1


def test_generation_is_deterministic_in_the_rng(synth_week):
    a = B.generate_lineups(synth_week, 10, np.random.default_rng(5))
    b = B.generate_lineups(synth_week, 10, np.random.default_rng(5))
    assert [lu.rows["gsis_id"].tolist() for lu in a] == [lu.rows["gsis_id"].tolist() for lu in b]


def _take(week, n_each):
    return pd.concat([week[week["position"] == p].head(k) for p, k in n_each.items()])


@pytest.mark.parametrize(
    "counts,legal",
    [
        ({"QB": 1, "RB": 2, "WR": 3, "TE": 1, "DST": 1}, False),  # eight players
        ({"QB": 1, "RB": 3, "WR": 3, "TE": 1, "DST": 1}, True),  # RB flex
        ({"QB": 1, "RB": 2, "WR": 4, "TE": 1, "DST": 1}, True),  # WR flex
        ({"QB": 1, "RB": 2, "WR": 3, "TE": 2, "DST": 1}, True),  # TE flex
        ({"QB": 1, "RB": 4, "WR": 3, "TE": 1, "DST": 1}, False),  # two flex
        ({"QB": 2, "RB": 2, "WR": 3, "TE": 1, "DST": 1}, False),  # two QBs
        ({"QB": 1, "RB": 2, "WR": 3, "TE": 1, "DST": 2}, False),  # two DST
        ({"QB": 1, "RB": 1, "WR": 4, "TE": 2, "DST": 1}, False),  # one RB
    ],
)
def test_is_legal_checks_slots(synth_week, counts, legal):
    week = synth_week.sort_values(["game_id", "team", "role"]).reset_index(drop=True)
    rows = pd.concat([week[week["position"] == p].iloc[::3].head(k) for p, k in counts.items()])
    assert B.is_legal(rows) is legal


def test_is_legal_needs_two_games_and_a_player_once(synth_week):
    one_game = synth_week[synth_week["game_id"] == "2023_01_G0"]
    base = pd.concat(
        [one_game[one_game["position"] == p].head(k) for p, k in {"QB": 1, "RB": 2, "WR": 3, "TE": 1}.items()]
    )
    flex = one_game[(one_game["position"] == "WR") & ~one_game["gsis_id"].isin(base["gsis_id"])].head(1)
    dst_same = one_game[one_game["position"] == "DST"].head(1)
    dst_other = synth_week[(synth_week["position"] == "DST") & (synth_week["game_id"] == "2023_01_G1")].head(
        1
    )
    assert not B.is_legal(pd.concat([base, flex, dst_same]))  # nine players but a single game
    ok = pd.concat([base, flex, dst_other])
    assert B.is_legal(ok)
    assert not B.is_legal(pd.concat([ok.iloc[:8], ok.iloc[[0]]]))  # a player twice


def _look():
    table = pd.DataFrame(
        {
            "relation": ["same_team"],
            "role_a": ["QB1"],
            "role_b": ["WR1"],
            "total_bucket": "all",
            "rho": 0.3,
            "n": 99,
            "ci_lo": 0.1,
            "ci_hi": 0.5,
        }
    )
    return CorrelationLookup(table, (43.5, 47.0))


def test_run_backtest_end_to_end_on_a_small_frame(synth_week):
    frame = pd.concat([synth_week.assign(week=w) for w in (1, 2)], ignore_index=True)
    frame["game_id"] = frame["game_id"] + "_" + frame["week"].astype(str)
    variants = [B.Variant(name, None, _look()) for name in ("shipped", "train-only")]
    res = B.run_backtest(frame, variants=variants, lineups_per_week=6, n_sims=400, seasons=(2023,), seed=1)
    assert len(res) == 12 and res["stacked"].sum() == 6
    for col in (
        "realized",
        "shipped.corr.mean",
        "shipped.indep.p90",
        "train-only.corr.p_gpp",
        "shipped.repaired",
    ):
        assert col in res.columns
    assert (res["shipped.corr.p10"] <= res["shipped.corr.p90"]).all()
    again = B.run_backtest(frame, variants=variants, lineups_per_week=6, n_sims=400, seasons=(2023,), seed=1)
    pd.testing.assert_frame_equal(res, again)
    parallel = B.run_backtest(
        frame, variants=variants, lineups_per_week=6, n_sims=400, seasons=(2023,), seed=1, n_jobs=2
    )
    pd.testing.assert_frame_equal(res, parallel)  # the result does not depend on the worker count
    text = report.backtest_report(res)
    assert "Coverage" in text and "Reliability" in text and "Verdict" in text


def test_coverage_and_reliability_measure_what_they_say():
    rng = np.random.default_rng(0)
    n = 4000
    realized = rng.normal(100, 20, n)
    # a perfectly calibrated predictor: its quantiles are the true normal quantiles
    from scipy.stats import norm

    res = pd.DataFrame({"realized": realized})
    for q in B.COVERAGE_LEVELS:
        res[f"v.corr.p{round(q * 100)}"] = norm.ppf(q, 100, 20)
        res[f"v.indep.p{round(q * 100)}"] = norm.ppf(q, 100, 20) + 10  # a biased one
    res["v.corr.p_cash"] = 1 - norm.cdf(120, 100, 20)
    cov = B.coverage(res, "v", "corr")
    assert cov["gap"].abs().max() < 2.0
    assert (
        B.coverage(res, "v", "indep")["gap"].iloc[2] > 10
    )  # a +10 shift puts far too much mass below the median
    rel = B.reliability(res, "v", "corr", "p_cash", 120.0)
    assert rel["n"].sum() == n and abs(rel["gap"]).max() < 6
    assert B.verdict(cov, {"cash": rel})["coverage_ok"]


def test_pinball_brier_and_the_paired_difference():
    res = pd.DataFrame(
        {
            "season": [2023] * 4,
            "week": [1, 1, 2, 2],
            "realized": [100.0, 120.0, 90.0, 150.0],
        }
    )
    for q in B.COVERAGE_LEVELS:
        res[f"v.corr.p{round(q * 100)}"] = 100.0  # a sharp, correct-ish prediction
        res[f"v.indep.p{round(q * 100)}"] = 60.0  # far too low everywhere
    corr, indep = B.pinball(res, "v", "corr"), B.pinball(res, "v", "indep")
    assert corr.mean() < indep.mean()
    assert corr.iloc[0] == 0.0  # realized == predicted at every level
    res["v.corr.p_cash"], res["v.indep.p_cash"] = [0.0, 1.0, 0.0, 1.0], 0.5
    b_corr, b_ind = B.brier(res, "v", "corr", "p_cash", 110.0), B.brier(res, "v", "indep", "p_cash", 110.0)
    assert b_corr.sum() == 0 and b_ind.mean() == 0.25
    diff, se = B.paired_difference(res, b_ind, b_corr)
    assert diff == pytest.approx(0.25) and se == pytest.approx(0.0)
