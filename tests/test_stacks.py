"""The stack ranker (`dfs.stacks`) on a synthetic six-team slate: which stacks exist, that the lineups built
around them are exactly the stack, the arithmetic of the ranking, and that a stack that booms ranks first."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from dfs import stacks
from dfs.sim import contest_sim as csim
from dfs.sim import field_gen
from dfs.sim.simulate import PlayerSpec

TEAMS = ["AAA", "BBB", "CCC", "DDD", "EEE", "FFF"]
OPP = {"AAA": "BBB", "BBB": "AAA", "CCC": "DDD", "DDD": "CCC", "EEE": "FFF", "FFF": "EEE"}
RATES = field_gen.FieldRates(
    stack1=0.6,
    stack2=0.2,
    bringback_stacked=0.5,
    bringback_unstacked=0.3,
    unspent=(0.0, 100.0, 300.0, 500.0, 800.0, 1200.0, 2000.0),
)


def make_slate(seed: int = 0):
    """Six teams, each QB / 3 RB / 4 WR / 2 TE / DST. Returns (slate, specs)."""
    rng = np.random.default_rng(seed)
    rows, specs = [], []
    layout = (
        ("QB", ["QB1"], (5200, 7200), (17, 22)),
        ("RB", ["RB1", "RB2", "RB3"], (4000, 8000), (6, 16)),
        ("WR", ["WR1", "WR2", "WR3", "WR4"], (3200, 8400), (6, 17)),
        ("TE", ["TE1", "TE2"], (2800, 5600), (4, 13)),
        ("DST", ["DST"], (2200, 3400), (4, 9)),
    )
    for team in TEAMS:
        for pos, roles, (lo, hi), (plo, phi) in layout:
            salaries = np.sort(rng.integers(lo // 100, hi // 100, size=len(roles)))[::-1] * 100
            projs = np.sort(rng.uniform(plo, phi, size=len(roles)))[::-1]
            for k, role in enumerate(roles):
                name = f"{team} {role}"
                rows.append(
                    {
                        "name": name,
                        "position": pos,
                        "team": team,
                        "opp": OPP[team],
                        "salary": int(salaries[k]),
                        "own": float(rng.uniform(0.01, 0.15)),
                    }
                )
                specs.append(
                    {
                        "id": name,
                        "position": pos,
                        "team": team,
                        "opp": OPP[team],
                        "game_id": "-".join(sorted((team, OPP[team]))),
                        "role": role,
                        "projection": float(projs[k]),
                        "salary": float(salaries[k]),
                        "tshare": float(0.30 - 0.03 * TEAMS.index(team) - 0.08 * k)
                        if pos == "RB"
                        else (0.2 if pos != "QB" else None),
                    }
                )
    slate = field_gen.Slate.from_frame(pd.DataFrame(rows))
    by_name = {s["id"]: PlayerSpec(**s) for s in specs}
    return slate, [by_name[n] for n in slate.names]


@pytest.fixture(scope="module")
def world():
    slate, specs = make_slate()
    gpp = stacks.build_gpp_field(slate, RATES, field_gen.HerdModel(share=0.0), size=400, seed=1)
    return slate, specs, gpp


def idx(slate, name):
    return slate.names.index(name)


# ---- which stacks ----------------------------------------------------------------------------------------


def test_a_back_catches_when_he_is_in_the_top_third_of_the_slates_rb1_and_rb2(world):
    slate, specs, _ = world
    got = {slate.names[i] for i in stacks.catching_backs(slate, specs)}
    assert got  # some
    assert all(n.endswith(("RB1", "RB2")) for n in got)  # RB3 never
    rb12 = [s for s in specs if s.role in ("RB1", "RB2")]
    assert len(got) <= len(rb12) / 3 + 1
    best = max(rb12, key=lambda s: s.tshare)
    assert best.id in got


def test_candidate_stacks_are_one_or_two_mates_of_the_teams_qb_with_at_most_one_back(world):
    slate, specs, _ = world
    out = stacks.candidate_stacks(slate, specs)
    assert {len(s.mates) for s in out} == {1, 2}
    assert len({s.qb for s in out}) == len(TEAMS)
    rbs = stacks.catching_backs(slate, specs)
    for s in out:
        team = slate.team[s.qb]
        assert slate.position[s.qb] == "QB"
        assert all(slate.team[m] == team and slate.position[m] in ("RB", "WR", "TE") for m in s.mates)
        assert sum(slate.position[m] == "RB" for m in s.mates) <= 1
        assert all(m in rbs for m in s.mates if slate.position[m] == "RB")
    assert len({(s.qb, s.mates) for s in out}) == len(out)


def test_candidate_stacks_use_a_teams_best_catchers_only(world):
    slate, specs, _ = world
    proj = {slate.names[i]: s.projection for i, s in enumerate(specs)}
    out = stacks.candidate_stacks(slate, specs, catchers=2, backs=0)
    for s in out:
        team = slate.team[s.qb]
        wr_te = sorted(
            (n for n in slate.names if n.startswith(team) and n.split()[1][:2] in ("WR", "TE")),
            key=lambda n: -proj[n],
        )[:2]
        assert {slate.names[m] for m in s.mates} <= set(wr_te)


# ---- lineups built around a stack -----------------------------------------------------------------------


def test_every_lineup_built_on_a_stack_is_exactly_that_stack(world):
    slate, specs, gpp = world
    qb = idx(slate, "AAA QB1")
    wr = idx(slate, "AAA WR1")
    te = idx(slate, "AAA TE1")
    variants = [
        (stacks.Stack(qb, (wr,)), False),
        (stacks.Stack(qb, (wr,)), True),
        (stacks.Stack(qb, tuple(sorted((wr, te)))), False),
        (stacks.Stack(qb, tuple(sorted((wr, te)))), True),
    ]
    rng = np.random.default_rng(3)
    lineups, owner = stacks.stack_lineups(slate, RATES, variants, gpp.weights, rng, per_variant=25)
    assert set(owner) == {0, 1, 2, 3}
    assert len(lineups) == 100
    needs = {"QB": 1, "RB": 2, "WR": 3, "TE": 1, "DST": 1}
    for row, v in zip(lineups, owner, strict=True):
        st, bring = variants[v]
        assert len(set(row)) == 9
        assert slate.salary[row].sum() <= field_gen.SALARY_CAP
        pos = slate.position[row]
        for p, k in needs.items():
            assert (pos == p).sum() >= k
        assert list(row).count(qb) == 1
        mates = tuple(
            sorted(int(i) for i in row if slate.team[i] == "AAA" and slate.position[i] in ("RB", "WR", "TE"))
        )
        assert mates == st.mates
        facing = [i for i in row if slate.team[i] == "BBB" and slate.position[i] in ("RB", "WR", "TE")]
        assert (len(facing) >= 1) == bring and len(facing) <= 1
    keys = stacks.stack_keys(slate, lineups)
    assert all(
        k[0] == qb and k[1] == variants[v][0].mates and k[2] == variants[v][1]
        for k, v in zip(keys, owner, strict=True)
    )


def test_an_unaffordable_stack_gets_no_lineups(world):
    slate, specs, gpp = world
    # every QB and pass catcher of one team at once costs more than the cap leaves room to fill
    qb = idx(slate, "AAA QB1")
    pricey = tuple(
        sorted(i for i in range(len(slate)) if slate.team[i] == "AAA" and slate.position[i] != "QB")
    )[:6]
    rng = np.random.default_rng(0)
    lineups, owner = stacks.stack_lineups(
        slate, RATES, [(stacks.Stack(qb, pricey), False)], gpp.weights, rng, per_variant=10
    )
    assert len(lineups) == 0 and len(owner) == 0


def test_stack_keys_reads_qb_exact_mates_and_bringback(world):
    slate, _, _ = world
    names = [
        "AAA QB1", "AAA WR1", "AAA TE1", "BBB WR2", "CCC RB1", "DDD RB2", "EEE WR3", "FFF WR4", "CCC DST",
    ]  # fmt: skip
    row = np.sort([idx(slate, n) for n in names])
    (key,) = stacks.stack_keys(slate, row.reshape(1, -1))
    assert key == (idx(slate, "AAA QB1"), tuple(sorted((idx(slate, "AAA WR1"), idx(slate, "AAA TE1")))), True)
    no_qb = np.sort(
        [
            idx(slate, n)
            for n in [
                "AAA WR1",
                "AAA WR2",
                "AAA WR3",
                "AAA RB1",
                "AAA RB2",
                "AAA TE1",
                "AAA TE2",
                "AAA RB3",
                "AAA DST",
            ]
        ]
    )
    assert stacks.stack_keys(slate, no_qb.reshape(1, -1)) == [(-1, (), False)]


# ---- the field's top-1% line -----------------------------------------------------------------------------


def test_the_top_line_is_the_score_with_one_percent_of_the_field_above_it(world):
    slate, _, gpp = world
    rng = np.random.default_rng(5)
    points = rng.uniform(0, 30, size=(40, len(slate))).astype(np.float32)
    line = stacks.top_lines(points, gpp.sim, np.random.default_rng(1))
    scores = points[:, gpp.sim.sampler].sum(axis=2)
    above = (scores > line[:, None]).mean(axis=1)
    assert (above <= stacks.TOP_FRACTION + 1e-9).all()
    assert (above >= stacks.TOP_FRACTION - 0.01).all()  # 400 lineups: one lineup is 0.25%


def test_the_herds_lineups_move_the_line_when_they_hold_a_big_share():
    slate, _ = make_slate()
    herd = field_gen.HerdModel(share=0.2, decay=0.5, k=3)
    gpp = stacks.build_gpp_field(slate, RATES, herd, size=300, seed=2)
    assert len(gpp.sim.chalk) == 3
    # the chalk lineup 1 scores far above the field in every draw: with 20% of the field on the herd, the
    # top-1% line is the herd lead's own score when the lead holds more than 1% of the field
    points = np.zeros((20, len(slate)), dtype=np.float32)
    points[:, gpp.sim.chalk[0]] = 100.0
    line = stacks.top_lines(points, gpp.sim, np.random.default_rng(0))
    assert (line >= 100.0).all()


def test_hit_counts_equal_a_direct_count(world):
    slate, _, gpp = world
    rng = np.random.default_rng(2)
    points = rng.uniform(0, 25, size=(30, len(slate))).astype(np.float32)
    line = rng.uniform(60, 90, size=30)
    got = stacks.hit_counts(points, gpp.sim.sampler[:50], line)
    want = (points[:, gpp.sim.sampler[:50]].sum(axis=2) > line[:, None]).sum(axis=0)
    np.testing.assert_array_equal(got, want)


# ---- the ranking -----------------------------------------------------------------------------------------


def small_rank(world, **kw):
    slate, specs, gpp = world
    chosen = [s for s in stacks.candidate_stacks(slate, specs) if slate.team[s.qb] in ("AAA", "CCC")]
    args = dict(n_sims=300, per_variant=25, rate_sample=3000, stacks=chosen, seed=4)
    args.update(kw)
    return stacks.rank_stacks(slate, specs, gpp, **args)


def test_the_ranking_arithmetic(world):
    res = small_rank(world)
    t = res.table
    assert len(t) > 10
    np.testing.assert_allclose(t["boom_share"], t["top1"] * t["field"] / stacks.TOP_FRACTION)
    np.testing.assert_allclose(t["lev"], t["boom_share"] - t["field"])
    np.testing.assert_allclose(t["lift"], t["top1"] / stacks.TOP_FRACTION)
    np.testing.assert_allclose(t["bb_edge"], t["top1_bb"] - t["top1_naked"])
    mix = res.meta["bringback_mix"]
    np.testing.assert_allclose(t["top1"], (1 - mix) * t["top1_naked"] + mix * t["top1_bb"])
    assert t["lev"].is_monotonic_decreasing
    assert set(t["field_src"]) <= {stacks.GEN_SRC, stacks.OWN_SRC}
    assert t["salary"].between(5_000, 50_000).all()
    assert (t["k"] == t["mates"].str.count(",") + 1).all()


def test_the_ranking_is_deterministic_in_the_seed(world):
    a, b = small_rank(world), small_rank(world)
    pd.testing.assert_frame_equal(a.table.drop(columns="mates_i"), b.table.drop(columns="mates_i"))
    c = small_rank(world, seed=9)
    assert not np.allclose(a.table["top1"].to_numpy()[:10], c.table["top1"].to_numpy()[:10])


def test_field_rates_of_the_generated_field_sum_to_at_most_the_stacked_share(world):
    slate, _, gpp = world
    rate_of, counts = stacks.field_stack_rates(slate, gpp, RATES, sample=3000, seed=1)
    total = sum(rate_of.values())
    assert total == pytest.approx(1.0, abs=1e-6)  # every lineup has exactly one key
    stacked = sum(v for (q, m, _b), v in rate_of.items() if q >= 0 and len(m) >= 1)
    assert 0.4 < stacked < 0.9  # stack1 is 0.6 of the field
    assert sum(counts.values()) == 3000


def test_a_stack_that_booms_ranks_first_by_top1(world):
    slate, specs, gpp = world
    n = 600
    rng = np.random.default_rng(0)
    proj = np.array([s.projection for s in specs])
    points = (proj[None, :] * rng.uniform(0.6, 1.4, size=(n, len(slate)))).astype(np.float32)
    boom = rng.random(n) < 0.5
    qb, wr = idx(slate, "AAA QB1"), idx(slate, "AAA WR1")
    points[np.ix_(boom, [qb, wr])] += 60.0
    chosen = [s for s in stacks.candidate_stacks(slate, specs) if slate.team[s.qb] in ("AAA", "CCC", "EEE")]
    res = stacks.rank_stacks(
        slate, specs, gpp, points=points, per_variant=30, rate_sample=2000, stacks=chosen
    )
    best = res.table.sort_values("top1", ascending=False).iloc[0]
    assert best["qb"] == "AAA QB1" and "AAA WR1" in best["mates"]
    # a stack that never carries the boom is well below it
    other = res.table[(res.table["qb"] == "CCC QB1")]["top1"].max()
    assert best["top1"] > 3 * other


def test_a_rare_stack_falls_back_to_the_ownership_product(world):
    slate, specs, gpp = world
    qb = idx(slate, "AAA QB1")
    odd = stacks.Stack(qb, tuple(sorted((idx(slate, "AAA TE2"), idx(slate, "AAA WR4")))))
    res = stacks.rank_stacks(
        slate, specs, gpp, n_sims=200, per_variant=20, rate_sample=400, stacks=[odd], seed=0
    )
    row = res.table.iloc[0]
    if row["field_src"] == stacks.OWN_SRC:
        want = float(np.prod([slate.own[i] for i in (qb, *odd.mates)]))
        assert row["field"] == pytest.approx(want)


def test_the_games_table_has_totals_and_the_field_stack_rate(world):
    slate, specs, gpp = world
    info = pd.DataFrame(
        {
            "team": TEAMS,
            "total": [50.5, 50.5, 44.0, 44.0, 38.5, 38.5],
            "spread": [-3.0, 3.0, 1.0, -1.0, 7.0, -7.0],
            "pace": [33.0, 35.0, 31.0, 31.0, 30.0, 32.0],
        }
    )
    res = small_rank(world, game_info=info)
    g = res.games
    assert list(g["game"]) == ["AAA v BBB", "CCC v DDD", "EEE v FFF"]
    assert g["total"].tolist() == [50.5, 44.0, 38.5]
    assert g["pace"].iloc[0] == pytest.approx(34.0)
    assert g["spread"].iloc[0] == pytest.approx(3.0)
    assert (g["stack_rate"] > 0).all() and (g["game_stack_rate"] <= g["stack_rate"]).all()
    assert g["best_lev"].iloc[0] == res.table[res.table["team"].isin(["AAA", "BBB"])]["lev"].max()


def test_draws_come_from_the_contest_simulators_copula(world):
    slate, specs, _ = world
    pts = csim.draw_points(specs, 50, seed=0)
    assert pts.shape == (50, len(slate))
