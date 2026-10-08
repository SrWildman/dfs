"""R3: usage jumps and TD excess are prior-only; thresholds are chosen on the fit seasons only."""

import numpy as np
import pandas as pd
import pytest

from dfs.research import r3_signals as r3
from dfs.research.flags import MIN_EFFECT, effect, verdict


def _games(n=14, seed=0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    g = pd.DataFrame(
        {
            "gsis_id": "P1",
            "season": [2022] * 10 + [2023] * (n - 10),
            "week": list(range(1, 11)) + list(range(1, n - 9)),
            "target_share": rng.uniform(0.05, 0.3, n),
            "carry_share": rng.uniform(0.0, 0.5, n),
            "rz_share": rng.uniform(0.0, 0.4, n),
            "td_excess": rng.normal(0, 1, n),
        }
    )
    g["t"] = g["season"] * 100 + g["week"]
    return g


def test_usage_jump_is_last_two_against_the_six_before():
    g = _games()
    out = r3.prior_usage_features(g)
    i = 11  # twelfth game
    ts = g["target_share"].to_numpy()
    assert out["target_share_recent"].iloc[i] == pytest.approx(ts[i - 2 : i].mean())
    assert out["target_share_earlier"].iloc[i] == pytest.approx(ts[i - 8 : i - 2].mean())
    assert out["target_share_jump"].iloc[i] == pytest.approx(ts[i - 2 : i].mean() - ts[i - 8 : i - 2].mean())
    assert out["td_excess_l3"].iloc[i] == pytest.approx(g["td_excess"].to_numpy()[i - 3 : i].sum())


def test_a_jump_needs_eight_prior_games():
    out = r3.prior_usage_features(_games())
    assert out["target_share_jump"].iloc[:8].isna().all()
    assert out["target_share_jump"].iloc[8:].notna().all()
    assert out["td_excess_l3"].iloc[:3].isna().all() and out["td_excess_l3"].iloc[3:].notna().all()


@pytest.mark.parametrize("i", [8, 10, 12])
def test_usage_features_ignore_the_game_itself_and_later_games(i):
    g = _games()
    base = r3.prior_usage_features(g)
    changed = g.copy()
    for col in ("target_share", "carry_share", "rz_share", "td_excess"):
        changed.loc[i:, col] = 99.0
    out = r3.prior_usage_features(changed)
    pd.testing.assert_frame_equal(base.iloc[: i + 1], out.iloc[: i + 1])
    assert not base.iloc[i + 1].equals(out.iloc[i + 1])


def test_player_game_usage_shares_and_td_excess():
    sp = pd.DataFrame(
        {
            "player_id": ["a", "b", "c"],
            "position": ["WR", "WR", "RB"],
            "team": ["X", "X", "X"],
            "season": 2023,
            "week": 1,
            "targets": [6.0, 2.0, 2.0],
            "carries": [0.0, 0.0, 10.0],
            "receiving_tds": [2.0, 0.0, 0.0],
            "rushing_tds": [0.0, 0.0, 1.0],
            "passing_tds": [0.0, 0.0, 0.0],
        }
    )
    ep = pd.DataFrame(
        {
            "player_id": ["a", "c"],
            "season": ["2023", "2023"],  # ffopportunity ships these as text
            "week": ["1", "1"],
            "rec_touchdown_exp": [0.5, 0.0],
            "rush_touchdown_exp": [0.0, 0.4],
            "pass_touchdown_exp": [0.0, 0.0],
        }
    )
    rz = pd.DataFrame({"gsis_id": ["a"], "season": [2023], "week": [1], "rz_share": [0.5]})
    out = r3.player_game_usage(sp, ep, rz).set_index("gsis_id")
    assert out.loc["a", "target_share"] == pytest.approx(0.6)
    assert out.loc["c", "carry_share"] == pytest.approx(1.0)
    assert out.loc["a", "td_excess"] == pytest.approx(1.5)
    assert out.loc["c", "td_excess"] == pytest.approx(0.6)
    assert out.loc["b", "rz_share"] == 0.0 and out.loc["b", "td_excess"] == 0.0


def test_buy_and_fade_flags():
    f = pd.DataFrame(
        {
            "gap_abs": [3.0, 1.0, -3.0, 3.0, -4.0],
            "gap_pct": [0.30, 0.1, -0.3, 0.30, -0.4],
            "xfp_top_half": [True, True, True, False, True],
            "td_excess_l3": [0.0, 0.0, 2.0, 0.0, 0.5],
        }
    )
    assert r3.buy_flag(f, x=3.0).tolist() == [True, False, False, False, False]  # not top half -> no
    assert r3.buy_flag(f, pct=0.25).tolist() == [True, False, False, False, False]
    assert r3.fade_flag(f, x=3.0).tolist() == [False, False, True, False, True]
    assert r3.fade_flag(f, x=3.0, td=1.0).tolist() == [False, False, True, False, False]


def test_usage_flag_respects_position_and_direction():
    f = pd.DataFrame(
        {
            "position": ["WR", "QB", "RB", "RB"],
            "target_share_jump": [0.06, 0.06, -0.06, 0.01],
            "carry_share_jump": [0.0, 0.2, 0.2, -0.2],
        }
    )
    assert r3.usage_flag(f, "target_share", 0.05, "up").tolist() == [True, False, False, False]
    assert r3.usage_flag(f, "target_share", 0.05, "down").tolist() == [False, False, True, False]
    assert r3.usage_flag(f, "carry_share", 0.10, "up").tolist() == [False, False, True, False]  # RBs only


def _effect_frame(fit_effect_a, test_effect_a, fit_effect_b, test_effect_b, n=4000, seed=0):
    """Two candidate flags with chosen true effects in each period; residuals are noise plus the effect."""
    rng = np.random.default_rng(seed)
    f = pd.DataFrame(
        {
            "season": rng.choice([2018, 2019, 2023, 2024], n),
            "position": "WR",
            "gsis_id": rng.integers(0, 600, n),
            "week": 1,
        }
    )
    f["split"] = np.where(f["season"] < 2022, "fit", "test")
    a = rng.random(n) < 0.15
    b = (rng.random(n) < 0.15) & ~a
    fit = f["split"] == "fit"
    f["resid"] = rng.normal(0, 5, n)
    f.loc[a & fit, "resid"] += fit_effect_a
    f.loc[a & ~fit, "resid"] += test_effect_a
    f.loc[b & fit, "resid"] += fit_effect_b
    f.loc[b & ~fit, "resid"] += test_effect_b
    f["resid_c"] = f["resid"] - f.groupby(["position", "season"])["resid"].transform("mean")
    return f, pd.Series(a, index=f.index), pd.Series(b, index=f.index)


def test_effect_recovers_a_known_difference_and_hit_rate():
    f, a, _ = _effect_frame(3.0, 3.0, 0.0, 0.0)
    res = effect(f, a, +1)
    assert res["diff"] == pytest.approx(3.0, abs=0.7)
    assert res["lo90"] < res["diff"] < res["hi90"]
    assert res["hit_rate_flagged"] > res["hit_rate_unflagged"] + 0.1


def test_threshold_is_chosen_on_fit_seasons_only():
    # Candidate A is great in the fit seasons and nothing in the test seasons; B is the reverse.
    f, a, b = _effect_frame(fit_effect_a=3.0, test_effect_a=0.0, fit_effect_b=0.0, test_effect_b=3.0)
    f["target_share_jump"] = 0.0
    out = r3.sweep(f, [({"c": "A"}, a), ({"c": "B"}, b)], +1)
    assert out["chosen"] == {"c": "A"}  # the fit seasons decide
    assert out["recommendation"] == "drop"  # and the untouched test seasons then say it does not hold
    assert out["test"]["diff"] == pytest.approx(0.0, abs=0.9)


def test_a_stable_signal_is_kept():
    f, a, b = _effect_frame(2.0, 2.0, 0.0, 0.0, n=8000)
    out = r3.sweep(f, [({"c": "A"}, a), ({"c": "B"}, b)], +1)
    assert out["chosen"] == {"c": "A"} and out["recommendation"] == "keep"
    assert out["eligible_thresholds"] == 2


def test_verdicts():
    def res(fit, test, lo, hi):
        return {"fit": {"diff": fit}, "test": {"diff": test, "lo90": lo, "hi90": hi}}

    assert verdict(res(1.0, 1.0, 0.4, 1.6), +1)["recommendation"] == "keep"
    assert verdict(res(1.0, MIN_EFFECT - 0.1, 0.1, 0.9), +1)["recommendation"] == "borderline"
    assert verdict(res(1.0, -0.3, -0.9, 0.3), +1)["recommendation"] == "drop"  # wrong sign in test
    assert verdict(res(-0.5, 1.0, 0.4, 1.6), +1)["recommendation"] == "drop"  # wrong sign in fit
    assert verdict(res(1.0, 1.0, -0.2, 2.2), +1)["recommendation"] == "drop"  # interval includes zero
    assert verdict(res(-1.0, -1.0, -1.6, -0.4), -1)["recommendation"] == "keep"  # a down signal


def test_too_few_flagged_rows_is_never_chosen():
    f, a, _ = _effect_frame(3.0, 3.0, 0.0, 0.0, n=4000)
    tiny = pd.Series(False, index=f.index)
    tiny.iloc[:5] = True
    out = r3.sweep(f, [({"c": "tiny"}, tiny)], +1)
    assert out["chosen"] is None and out["recommendation"] == "drop"


def _signal_frame(n=40000, seed=3) -> pd.DataFrame:
    """A synthetic frame in `signal_frame`'s shape: only RBs carry a real carry-share signal (+2 points)."""
    rng = np.random.default_rng(seed)
    f = pd.DataFrame(
        {
            "season": rng.choice([2016, 2017, 2018, 2019, 2022, 2023, 2024, 2025], n),
            "position": rng.choice(["QB", "RB", "WR", "TE"], n),
            "gsis_id": rng.integers(0, 1500, n),
            "week": 1,
            "gap_abs": rng.normal(0, 3, n),
            "xfp_top_half": rng.random(n) < 0.5,
            "td_excess_l3": rng.normal(0, 1, n),
            "target_share_jump": rng.normal(0, 0.05, n),
            "carry_share_jump": rng.normal(0, 0.08, n),
            "rz_share_jump": rng.normal(0, 0.1, n),
        }
    )
    f["gap_pct"] = f["gap_abs"] / 10
    f["split"] = np.where(f["season"] < 2022, "fit", "test")
    f["resid"] = rng.normal(0, 5, n)
    rb_up = (f["position"] == "RB") & (f["carry_share_jump"] >= 0.10)
    f.loc[rb_up, "resid"] += 2.0
    f["resid_c"] = f["resid"] - f.groupby(["position", "season"])["resid"].transform("mean")
    return f


def test_a_usage_metric_is_judged_among_the_positions_it_applies_to():
    signals = r3.run_study(_signal_frame())
    res = signals["USAGE_up"]["per_metric"]["carry_share"]
    assert set(res["by_position"]) == {"RB"}  # a carry-share jump is only ever flagged on RBs
    # Against unflagged RBs the +2 shows in full; against every unflagged player it would be diluted.
    assert res["test"]["diff"] == pytest.approx(2.0, abs=0.7)
    assert res["recommendation"] == "keep"
    # a metric with no real signal is dropped
    assert signals["USAGE_up"]["per_metric"]["rz_share"]["recommendation"] == "drop"
    assert signals["salary_lag"]["tested"] is False
