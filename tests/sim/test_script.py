"""Game script: the buckets, the clustered slope, the scan with its shuffled-context control, the conditional
lookup, the out-of-sample score and the simulator hook -- on synthetic frames with a planted dependence."""

import numpy as np
import pandas as pd
import pytest

from dfs.sim import script, script_pbp
from dfs.sim.correlation import CorrelationLookup
from dfs.sim.simulate import PlayerSpec, correlation_matrix


def make_frame(n_games=3000, seed=0, planted=None):
    """A frame of `n_games` games (2014-2025, spread by game) with QB1 / WR1 / RB1 / DST on each side.
    `planted(spread_of_team) -> rho` sets the same-team QB1-WR1 correlation; everything else is
    independent."""
    rng = np.random.default_rng(seed)
    rows = []
    for g in range(n_games):
        season = 2014 + g % 12
        home_spread = float(np.round(rng.normal(0, 6) * 2) / 2)
        total = float(np.round(rng.normal(45, 4) * 2) / 2)
        for home, team, opp in ((1, "AAA", "BBB"), (0, "BBB", "AAA")):
            spread = home_spread if home else -home_spread
            implied = total / 2 + spread / 2
            rho = planted(spread) if planted else 0.0
            qb, noise = rng.standard_normal(2)
            wr = rho * qb + np.sqrt(1 - rho**2) * noise
            for role, position, z in (
                ("QB1", "QB", qb),
                ("WR1", "WR", wr),
                ("RB1", "RB", rng.standard_normal()),
                ("DST", "DST", rng.standard_normal()),
            ):
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
                        "dk": 10.0,
                        "z": z,
                        "spread": spread,
                        "implied": implied,
                        "total": total,
                    }
                )
    return pd.DataFrame(rows)


def test_spread_buckets_are_team_relative_with_the_touchdown_line_at_seven():
    assert list(script.spread_bucket([10, 7, 6.5, 0, -0.5, -6.5, -7, -10])) == [
        "BF",
        "BF",
        "SF",
        "SF",  # a pick'em is a small favourite on both sides
        "SD",
        "SD",
        "BD",
        "BD",
    ]


def test_implied_buckets_use_the_training_terciles():
    cut = script.implied_cutoffs(np.arange(30.0))
    assert list(script.implied_bucket([5, 15, 25], cut)) == ["low", "mid", "high"]
    with pytest.raises(ValueError):
        script.bucket_of("implied", [20.0])


def test_the_clustered_slope_recovers_a_slope_and_a_null_is_not_significant():
    rng = np.random.default_rng(1)
    n = 6000
    cluster = rng.integers(0, 2000, n)
    x = rng.normal(size=n)
    slope, se = script.clustered_slope(0.3 * x + rng.normal(size=n), x, cluster)
    assert slope == pytest.approx(0.3, abs=4 * se) and abs(slope / se) > 8
    slope, se = script.clustered_slope(rng.normal(size=n), x, cluster)
    assert abs(slope / se) < 4
    assert np.isnan(script.clustered_slope(np.ones(5), np.arange(5.0), np.arange(5))[0])


def test_the_scan_finds_a_planted_spread_dependence_and_not_a_null_one():
    def planted(spread):
        return 0.45 if spread >= 7 else 0.05

    real = script.scan(
        make_frame(planted=planted),
        n_shuffle=30,
        n_boot=20,
        pairs=[("same_team", "QB1", "WR1"), ("same_team", "QB1", "RB1")],
    )
    t = real.table.set_index("role_b")
    assert t.loc["WR1", "t_spread"] > 4 and t.loc["WR1", "p_adj_spread"] < 0.1
    assert abs(t.loc["RB1", "t_spread"]) < 3.5  # the unplanted pair
    buckets = real.buckets.query("role_b == 'WR1' and variable == 'spread'").set_index("bucket")["r"]
    assert buckets["BF"] > buckets["SD"] + 0.2
    null = script.scan(make_frame(seed=3), n_shuffle=30, n_boot=20, pairs=[("same_team", "QB1", "WR1")])
    assert abs(null.table["t_spread"].iloc[0]) < 3.5
    summary = script.chance_summary(null.table, null)
    assert set(summary["variable"]) == {"spread", "implied"}


def test_shuffling_contexts_keeps_the_distribution_and_the_two_sides_consistent():
    frame = make_frame(n_games=400)
    shuffled = script.shuffled_frame(frame, np.random.default_rng(0))
    assert sorted(shuffled["total"].unique()) == sorted(frame["total"].unique())
    g = shuffled.drop_duplicates(["game_id", "team"]).groupby("game_id")
    assert (g["spread"].sum().abs() < 1e-9).all()  # the two sides' spreads still cancel
    both = shuffled.drop_duplicates(["game_id", "team"])
    assert np.allclose(both["implied"], both["total"] / 2 + both["spread"] / 2)
    assert (shuffled["season"].to_numpy() == frame["season"].to_numpy()).all()
    changed = (
        shuffled.drop_duplicates("game_id")["total"].to_numpy()
        != frame.drop_duplicates("game_id")["total"].to_numpy()
    ).mean()
    assert changed > 0.5


def base_lookup(rho):
    return script.lookup_from_rhos({("same_team", "QB1", "WR1"): rho})


def test_a_bucket_is_shrunk_toward_the_pooled_value_not_toward_zero():
    # a bucket worth n = 203 pairs at r = 0.5 against a pooled 0.1: halfway in Fisher-z space
    assert script.shrink_toward(0.5, 203, 0.1, 200) == pytest.approx(
        np.tanh((np.arctanh(0.5) + np.arctanh(0.1)) / 2)
    )
    assert script.shrink_toward(0.5, 3, 0.1, 200) == pytest.approx(0.1)  # no data: the pooled value
    assert script.shrink_toward(0.5, 1e9, 0.1, 200) == pytest.approx(0.5, abs=1e-3)


def test_the_conditional_lookup_adds_a_bucket_deviation_to_the_pooled_value():
    buckets = pd.DataFrame(
        [
            ("same_team", "QB1", "WR1", "spread", "BF", 1000, 0.5, 0.4, 0.6),
            ("same_team", "QB1", "WR1", "spread", "BD", 1000, 0.1, 0.0, 0.2),
        ],
        columns=["relation", "role_a", "role_b", "variable", "bucket", "n", "r", "lo", "hi"],
    )
    lk = script.ContextLookup(base_lookup(0.3), buckets, (21.0, 24.5), use=("spread",))
    assert lk.rho("same_team", "QB1", "WR1") == pytest.approx(0.3)  # no context: pooled
    assert lk.rho("same_team", "QB1", "WR1", (10.0, 28.0)) > 0.3
    assert lk.rho("same_team", "QB1", "WR1", (-10.0, 18.0)) < 0.3
    assert lk.rho("same_team", "QB1", "WR1", (3.0, 24.0)) == pytest.approx(0.3)  # a bucket with no row
    assert lk.rho("same_team", "WR1", "QB1", (10.0, 28.0)) == pytest.approx(
        lk.rho("same_team", "QB1", "WR1", (10.0, 28.0))
    )
    assert lk.rho("opp", "QB1", "QB1", (10.0, 28.0)) == pytest.approx(
        0.0
    )  # a pair it never saw falls to the base
    vec = lk.rho_array("same_team", "QB1", "WR1", [10.0, -10.0, 3.0], [28.0, 18.0, 24.0])
    assert vec == pytest.approx(
        [lk.rho("same_team", "QB1", "WR1", c) for c in ((10.0, 28.0), (-10.0, 18.0), (3.0, 24.0))]
    )
    limited = script.ContextLookup(
        base_lookup(0.3), buckets, (21.0, 24.5), use=("spread",), pairs={("opp", "QB1", "WR1")}
    )
    assert limited.rho("same_team", "QB1", "WR1", (10.0, 28.0)) == pytest.approx(0.3)


def test_an_opposing_pair_is_oriented_by_role_a_and_flips_to_the_other_team_when_asked_backwards():
    buckets = pd.DataFrame(
        [("opp", "QB1", "RB1", "spread", "BF", 1000, 0.5, 0.4, 0.6)],
        columns=["relation", "role_a", "role_b", "variable", "bucket", "n", "r", "lo", "hi"],
    )
    base = script.lookup_from_rhos({("opp", "QB1", "RB1"): 0.0})
    lk = script.ContextLookup(base, buckets, (21.0, 24.5), use=("spread",))
    favourite_qb, dog_rb = (10.0, 28.0), (-10.0, 18.0)
    up = lk.rho("opp", "QB1", "RB1", favourite_qb, dog_rb)
    assert up > 0.05
    assert lk.rho("opp", "RB1", "QB1", dog_rb, favourite_qb) == pytest.approx(up)  # the QB's team supplies it


def test_the_out_of_sample_score_rewards_a_real_dependence_and_penalises_a_planted_noise():
    def planted(spread):
        return 0.5 if spread >= 7 else (0.05 if spread <= -7 else 0.25)

    frame = make_frame(n_games=6000, planted=planted)
    train, test = frame[frame.season <= 2022], frame[frame.season > 2022]
    pairs = [("same_team", "QB1", "WR1")]
    sc = script.scan(train, n_shuffle=0, n_boot=5, pairs=pairs)
    base = script.lookup_from_rhos(script.pooled_rhos(train, pairs))
    lk = script.ContextLookup(base, sc.buckets, sc.cutoffs, use=("spread",))
    gain = script.summarize_gain(script.loglik_gain(test, lk, pairs))
    assert gain["gain"] > 0 and gain["per_1000"] > 3 * gain["se_per_1000"]
    # the same table on a frame with no dependence loses to the pooled value
    flat = make_frame(n_games=6000, seed=5)
    tr, te = flat[flat.season <= 2022], flat[flat.season > 2022]
    sc2 = script.scan(tr, n_shuffle=0, n_boot=5, pairs=pairs)
    lk2 = script.ContextLookup(
        script.lookup_from_rhos(script.pooled_rhos(tr, pairs)),
        sc2.buckets,
        sc2.cutoffs,
        prior_n=20,
        use=("spread",),
    )
    assert script.summarize_gain(script.loglik_gain(te, lk2, pairs))["gain"] < 5


def test_the_simulator_hook_reads_each_players_team_context_and_defaults_to_the_pooled_value():
    buckets = pd.DataFrame(
        [("same_team", "QB1", "WR1", "spread", "BF", 1000, 0.6, 0.5, 0.7)],
        columns=["relation", "role_a", "role_b", "variable", "bucket", "n", "r", "lo", "hi"],
    )
    pooled = CorrelationLookup(
        pd.DataFrame(
            [("same_team", "QB1", "WR1", "all", 0.3, 100, 0.3, 0.3)],
            columns=["relation", "role_a", "role_b", "total_bucket", "rho", "n", "ci_lo", "ci_hi"],
        )
    )
    lk = script.ContextLookup(pooled, buckets, (21.0, 24.5), use=("spread",))

    def players(spread):
        qb = PlayerSpec("qb", "QB", "AAA", "BBB", "G", "QB1", 20.0, spread=spread, implied=26.0)
        wr = PlayerSpec("wr", "WR", "AAA", "BBB", "G", "WR1", 14.0, spread=spread, implied=26.0)
        return [qb, wr]

    favoured, _ = correlation_matrix(players(10.0), lk)
    plain, _ = correlation_matrix(players(None), lk)
    assert favoured[0, 1] > 0.3 and plain[0, 1] == pytest.approx(0.3)
    assert correlation_matrix(players(10.0), pooled)[0][0, 1] == pytest.approx(
        0.3
    )  # the pooled table ignores it


def test_tail_dependence_of_a_gaussian_pair_is_about_one():
    frame = make_frame(n_games=4000, planted=lambda s: 0.4)
    t = script.tail_dependence(frame, [("same_team", "QB1", "WR1")], cuts=(1.0,), n_boot=50)
    assert 0.85 < t["ratio"].iloc[0] < 1.15 and t["ratio_lo"].iloc[0] < 1.0 < t["ratio_hi"].iloc[0] + 0.1


# --- the play-by-play reduction ----------------------------------------------------------------------------


def test_state_buckets_cover_every_score():
    labels = script_pbp.state_bucket([-20, -10, -3, 0, 3, 10, 20])
    assert list(labels) == list(script_pbp.STATE_LABELS)


def test_the_reduction_keeps_designed_passes_and_runs_with_the_offense_score():
    raw = pd.DataFrame(
        {
            "game_id": ["2023_01_A_B"] * 6,
            "posteam": ["A", "A", "A", "A", "A", None],
            "season_type": ["REG"] * 6,
            "qtr": [1, 1, 2, 2, 3, 3],
            "score_differential": [0, 3, 3, -4, 0, 0],
            "qb_dropback": [1, 0, 1, 1, 0, 0],
            "qb_spike": [0, 0, 0, 1, 0, 0],
            "qb_kneel": [0, 0, 0, 0, 1, 0],
            "qb_scramble": [0, 0, 0, 0, 0, 0],
            "rush": [0, 1, 0, 0, 1, 0],
            "game_seconds_remaining": [3000.0] * 6,
            "pass_oe": [1.0, 2.0, 3.0, 4.0, 5.0, 6.0],
        }
    )
    out = script_pbp.reduce_pbp(raw)
    assert list(out["pass"]) == [1, 0, 1]  # the spike, the kneel and the no-offense row are gone
    assert list(out["diff"]) == [0.0, 3.0, 3.0]
    assert list(out["pass_oe"]) == [1.0, 2.0, 3.0]
    proe = script_pbp.team_game_proe(out.assign(team="A"))
    assert proe["proe"].iloc[0] == pytest.approx(0.02)  # mean pass_oe of 2 points, as a fraction
