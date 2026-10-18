"""Team shape as a modifier on a pair: the features (no leakage), the scan with its shuffled-feature
control, the tercile lookup layered on the pooled value and the simulator hook -- on synthetic frames with
a planted effect."""

from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from dfs.sim import script, script_bringback, script_shape
from dfs.sim.correlation import CorrelationLookup
from dfs.sim.simulate import PlayerSpec, correlation_matrix

TEST = ("same_team", "QB1", "TE1", "b", "tshare")


def make_frame(n_games=2500, seed=0, planted=True):
    """QB1 and TE1 per team; the TE's target share (his `tshare`) sets how much he moves with the QB when
    `planted` (0.05 for the lowest shares up to 0.55 for the highest), else not at all."""
    rng = np.random.default_rng(seed)
    rows = []
    for g in range(n_games):
        season = 2014 + g % 12
        total = float(np.round(rng.normal(45, 4) * 2) / 2)
        for home, team, opp in ((1, "AAA", "BBB"), (0, "BBB", "AAA")):
            share = float(rng.uniform(0.05, 0.25))
            rho = 0.05 + 2.0 * (share - 0.05) if planted else 0.0
            qb, noise = rng.standard_normal(2)
            te = rho * qb + np.sqrt(1 - rho**2) * noise
            conc, proe = float(rng.uniform(0.2, 0.4)), float(rng.normal(0, 0.05))
            for role, position, z, ts in (("QB1", "QB", qb, np.nan), ("TE1", "TE", te, share)):
                rows.append(
                    {
                        "game_id": f"G{g}",
                        "season": season,
                        "week": 1 + g % 17,
                        "team": team,
                        "opp": opp,
                        "home": home,
                        "role": role,
                        "position": position,
                        "gsis_id": f"{team}{g}{role}",
                        "pred": 10.0,
                        "dk": 10.0 + 4 * z,
                        "z": z,
                        "spread": 0.0,
                        "implied": total / 2,
                        "total": total,
                        "tshare": ts,
                        "qb_rush": 0.1,
                        "proe": proe,
                        "conc": conc,
                    }
                )
    return pd.DataFrame(rows)


def pooled(rho):
    return script.lookup_from_rhos({("same_team", "QB1", "TE1"): rho})


def test_the_features_use_only_the_three_previous_games():
    def row(player, t, share, carries=0):
        return {
            "gsis_id": player,
            "t": t,
            "season": 2024,
            "week": t,
            "game_id": f"G{t}",
            "team": "AAA",
            "targets": 5,
            "carries": carries,
            "target_share": share,
        }

    pg = pd.DataFrame([row("p", t, s) for t, s in zip(range(1, 6), (0.1, 0.2, 0.3, 0.4, 0.5), strict=True)])
    history = SimpleNamespace(player_games=pg)
    proe = pd.DataFrame(
        {"game_id": [f"G{t}" for t in range(1, 6)], "team": "AAA", "proe": [0.0, 0.1, 0.2, 0.3, 0.4]}
    )
    frame = pd.DataFrame(
        {
            "gsis_id": "p",
            "season": 2024,
            "week": list(range(1, 6)),
            "game_id": [f"G{t}" for t in range(1, 6)],
            "team": "AAA",
        }
    )
    out = script_shape.shape_features(frame, history, proe).sort_values("week")
    assert np.isnan(out["tshare"].iloc[0]) and np.isnan(out["proe"].iloc[0])  # no prior game, no feature
    assert out["tshare"].iloc[3] == pytest.approx(0.2)  # mean of games 1-3, not game 4 itself
    assert out["tshare"].iloc[4] == pytest.approx(0.3)  # games 2-4
    assert out["proe"].iloc[4] == pytest.approx(0.2)


def test_a_test_reads_the_feature_of_the_side_it_names():
    f = make_frame(200)
    rows = script_shape.test_rows(f, TEST)
    assert len(rows) == 400 and rows["x"].between(0.05, 0.25).all()
    qb_side = script_shape.test_rows(f, ("same_team", "QB1", "TE1", "a", "proe"))
    assert qb_side["x"].between(-0.3, 0.3).all()


def test_the_scan_finds_a_planted_target_share_effect_and_not_a_null_one():
    real = script_shape.scan(make_frame(), tests=(TEST,), n_shuffle=20)
    row = real.table.iloc[0]
    assert row["t"] > 5 and row["r_high"] > row["r_low"] + 0.25 and row["p_adj"] < 0.1
    null = script_shape.scan(make_frame(seed=3, planted=False), tests=(TEST,), n_shuffle=20)
    assert abs(null.table.iloc[0]["t"]) < 3.5


def test_shuffling_features_keeps_their_distribution_and_breaks_the_link():
    f = make_frame(1500)
    sh = script_shape.shuffled_features(f, np.random.default_rng(0))
    assert sorted(sh["tshare"].dropna()) == pytest.approx(sorted(f["tshare"].dropna()))
    assert (sh["role"] == f["role"]).all() and (sh["season"] == f["season"]).all()
    assert abs(script_shape._t(script_shape.test_rows(sh, TEST))) < 4
    assert script_shape._t(script_shape.test_rows(f, TEST)) > 5


def test_the_tercile_lookup_adds_a_deviation_to_the_pooled_value_and_uses_the_right_players_feature():
    f = make_frame()
    train = f
    sc = script_shape.scan(train, tests=(TEST,), n_shuffle=0)
    lk = script_shape.ShapeLookup(pooled(0.3), sc.buckets, sc.cuts)
    assert lk.rho("same_team", "QB1", "TE1") == pytest.approx(0.3)  # no feature: pooled
    low = lk.rho("same_team", "QB1", "TE1", {}, {"tshare": 0.06})
    high = lk.rho("same_team", "QB1", "TE1", {}, {"tshare": 0.24})
    assert low < 0.3 < high
    assert lk.rho("same_team", "TE1", "QB1", {"tshare": 0.24}, {}) == pytest.approx(high)  # asked backwards
    assert lk.rho("same_team", "QB1", "TE1", {"tshare": 0.24}, {}) == pytest.approx(
        0.3
    )  # QB's share: not this test
    arr = lk.rho_array("same_team", "QB1", "TE1", {}, {"tshare": np.array([0.06, 0.24, np.nan])}, 3)
    assert arr[0] == pytest.approx(low) and arr[1] == pytest.approx(high) and arr[2] == pytest.approx(0.3)
    only = script_shape.ShapeLookup(pooled(0.3), sc.buckets, sc.cuts, use=("proe",))
    assert only.rho("same_team", "QB1", "TE1", {}, {"tshare": 0.24}) == pytest.approx(0.3)  # feature not used


def test_the_held_out_gain_is_positive_for_a_real_effect_and_the_shuffled_table_loses():
    f = make_frame(5000)
    train, test = f[f.season <= 2022], f[f.season > 2022]
    pooled_rho = float(
        script.shrink(np.corrcoef(script_shape.test_rows(train, TEST)[["z_a", "z_b"]].T)[0, 1], 3000)
    )
    sc = script_shape.scan(train, tests=(TEST,), n_shuffle=0)
    gain = script_shape.summarize(
        script_shape.shape_gain(
            test, script_shape.ShapeLookup(pooled(pooled_rho), sc.buckets, sc.cuts), tests=(TEST,)
        )
    )
    assert gain["per_1000"] > 3 * gain["se_per_1000"] > 0
    sh = script_shape.shuffled_features(train, np.random.default_rng(1))
    sc2 = script_shape.scan(sh, tests=(TEST,), n_shuffle=0)
    lost = script_shape.summarize(
        script_shape.shape_gain(
            test, script_shape.ShapeLookup(pooled(pooled_rho), sc2.buckets, sc2.cuts), tests=(TEST,)
        )
    )
    assert lost["per_1000"] < gain["per_1000"] / 3


def test_the_simulator_hook_reads_each_players_shape_off_his_spec():
    f = make_frame()
    sc = script_shape.scan(f, tests=(TEST,), n_shuffle=0)
    base = CorrelationLookup(
        pd.DataFrame(
            [("same_team", "QB1", "TE1", "all", 0.3, 100, 0.3, 0.3)],
            columns=["relation", "role_a", "role_b", "total_bucket", "rho", "n", "ci_lo", "ci_hi"],
        )
    )
    lk = script_shape.ShapeLookup(base, sc.buckets, sc.cuts)

    def players(share):
        qb = PlayerSpec("qb", "QB", "AAA", "BBB", "G", "QB1", 20.0)
        te = PlayerSpec("te", "TE", "AAA", "BBB", "G", "TE1", 8.0, tshare=share)
        return [qb, te]

    receiving, blocking = (
        correlation_matrix(players(0.24), lk)[0][0, 1],
        correlation_matrix(players(0.06), lk)[0][0, 1],
    )
    assert receiving > 0.3 > blocking
    assert correlation_matrix(players(None), lk)[0][0, 1] == pytest.approx(0.3)
    assert correlation_matrix(players(0.24), base)[0][0, 1] == pytest.approx(
        0.3
    )  # the pooled table ignores it


# --- the bring-back lift ----------------------------------------------------------------------------------


def lift_frame(n=4000, link=0.5, seed=0):
    """Per game: QB1, WR1 on each side and the opposing WR1 linked to the QB by `link` (in z)."""
    rng = np.random.default_rng(seed)
    rows = []
    for g in range(n):
        spread = float(rng.choice([-10, -3, 3, 10]))
        z = rng.standard_normal(2)
        for k, (team, opp, sp) in enumerate((("AAA", "BBB", spread), ("BBB", "AAA", -spread))):
            qb = z[k]
            wr1 = rng.standard_normal()
            for role, dk in (("QB1", 18 + 6 * qb), ("WR1", 12 + 5 * wr1)):
                rows.append(
                    {
                        "game_id": f"G{g}",
                        "team": team,
                        "opp": opp,
                        "season": 2014 + g % 12,
                        "spread": sp,
                        "role": role,
                        "dk": dk,
                    }
                )
        # the opposing WR1 of team k is team (1-k)'s WR1: overwrite so it carries the link to team k's QB
        for k in range(2):
            idx = len(rows) - 4 + 2 * (1 - k) + 1  # that other team's WR1 row
            rows[idx]["dk"] = 12 + 5 * (link * z[k] + np.sqrt(1 - link**2) * rng.standard_normal())
    return pd.DataFrame(rows)


def test_the_bring_back_lift_is_above_one_when_it_moves_with_the_stack_and_one_when_it_does_not():
    linked = script_bringback.lift_table(
        lift_frame(link=0.6), stacks={"QB+WR1": ("QB1", "WR1")}, backs=("WR1",), n_draws=40, n_boot=100
    )
    t = linked.table
    top5 = t[(t["tail"] == "top 5%") & (t["bucket"] == "all")].iloc[0]
    assert top5["lift"] > 1.1 and top5["lo"] > 1.0
    flat = script_bringback.lift_table(
        lift_frame(link=0.0, seed=2),
        stacks={"QB+WR1": ("QB1", "WR1")},
        backs=("WR1",),
        n_draws=40,
        n_boot=100,
    )
    f5 = flat.table[(flat.table["tail"] == "top 5%") & (flat.table["bucket"] == "all")].iloc[0]
    assert 0.85 < f5["lift"] < 1.15 and f5["lo"] < 1.0 < f5["hi"]
    assert set(t["bucket"]) == {"BD", "SD", "SF", "BF", "all"}
