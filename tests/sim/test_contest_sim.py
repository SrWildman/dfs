"""The contest simulator on fixed outcomes: placement, prizes, duplicates splitting them, the top-0.1% split,
the per-draw herd lead, the lines and sim leverage."""

import numpy as np
import pytest

from dfs.sim import contest_sim as csim
from dfs.sim import field_gen as fg
from dfs.sim import payouts as pay
from dfs.sim.simulate import PlayerSpec

N_PLAYERS = 30


def _slate() -> fg.Slate:
    pos = ["QB"] * 3 + ["RB"] * 6 + ["WR"] * 9 + ["TE"] * 6 + ["DST"] * 6
    teams = [f"T{i % 6}" for i in range(N_PLAYERS)]
    return fg.Slate(
        names=tuple(f"p{i:02d}" for i in range(N_PLAYERS)),
        position=np.array(pos),
        team=np.array(teams),
        opp=np.array(teams),
        salary=np.full(N_PLAYERS, 5000),
        own=np.full(N_PLAYERS, 0.1),
    )


def _field(sampler: np.ndarray, chalk: np.ndarray, herd: fg.HerdModel, fmt: str = "gpp") -> csim.SimField:
    slate = _slate()
    return csim.SimField(fmt=fmt, slate=slate, sampler=sampler, chalk=chalk, herd=herd, own=slate.targets())


GPP = pay.ContestKind(
    "gpp", "SE GPP", entries=1000, places_paid=200, prize_pool=8400.0, fee=10.0, min_cash=2.0
)
DU = pay.ContestKind("du", "Double Up", entries=1000, places_paid=450, prize_pool=9000.0, fee=10.0)


def test_block_payout_averages_the_places_a_lineup_and_its_copies_take():
    cum = csim._cumulative(np.array([100.0, 50.0, 20.0, 10.0]))  # noqa: SLF001
    out = csim.block_payout(cum, np.array([1.0, 1.0, 2.0, 4.0, 5.0]), np.array([0.0, 1.0, 2.0, 0.0, 0.0]))
    assert list(out) == pytest.approx([100.0, 75.0, (50 + 20 + 10) / 3, 10.0, 0.0])
    # half a copy is paid pro rata: (100 + 0.5 * 50) / 1.5
    assert csim.block_payout(cum, np.array([1.0]), np.array([0.5]))[0] == pytest.approx(125 / 1.5)


def _ranked_points(n_draws: int = 3) -> np.ndarray:
    return np.tile(np.arange(N_PLAYERS, dtype=float)[::-1], (n_draws, 1))  # p00 best ... p29 worst


def test_the_best_lineup_wins_first_and_the_worst_wins_nothing():
    field = _field(np.array([np.arange(9) + k for k in range(5, 20)]), np.zeros((0, 9), int), fg.HerdModel())
    best, worst = np.arange(9), np.arange(21, 30)
    res = csim.simulate_contests(
        {"gpp": field},
        [None] * N_PLAYERS,
        np.array([best, worst]),
        [GPP],
        labels=["best", "worst"],
        points=_ranked_points(),
    )
    t = res.table.set_index("lineup")
    cum = csim._cumulative(pay.payout_table(GPP))  # noqa: SLF001
    # first place, shared with the dup model's copies (a 30-player slate makes every lineup chalky)
    paid = csim.block_payout(cum, np.array([1.0]), np.array([t.loc["best", "e_copies"]]))[0]
    assert t.loc["best", "e_copies"] > 0
    assert t.loc["best", "e_dollars"] == pytest.approx(paid)
    assert t.loc["best", "p_cash"] == 1.0 and t.loc["best", "p_top1"] == 1.0
    assert t.loc["worst", "e_dollars"] == 0.0 and t.loc["worst", "p_cash"] == 0.0
    assert t.loc["best", "roi"] == pytest.approx(paid / 10 - 1)
    # the top 0.1% of 1,000 entries is place 1 alone: its share of the block is the "top" money
    top_table = pay.payout_table(GPP).copy()
    top_table[1:] = 0.0
    top = csim.block_payout(
        csim._cumulative(top_table), np.array([1.0]), np.array([t.loc["best", "e_copies"]])
    )[0]  # noqa: SLF001
    assert t.loc["best", "e_top"] == pytest.approx(top)
    assert t.loc["best", "e_top"] + t.loc["best", "e_dollars_ex_top"] == pytest.approx(
        t.loc["best", "e_dollars"]
    )


def test_herd_copies_split_the_prize():
    best = np.arange(9)
    herd = fg.HerdModel(share=0.1, decay=0.5, k=1)
    field = _field(np.array([np.arange(9) + k for k in range(5, 20)]), best[None, :], herd)
    res = csim.simulate_contests(
        {"gpp": field}, [None] * N_PLAYERS, best[None, :], [GPP], points=_ranked_points()
    )
    row = res.table.iloc[0]
    assert row["e_copies"] == pytest.approx(0.1 * 999)
    table = pay.payout_table(GPP)
    expected = csim.block_payout(csim._cumulative(table), np.array([1.0]), np.array([99.9]))[0]  # noqa: SLF001
    assert row["e_dollars"] == pytest.approx(expected)


def test_a_double_up_pays_flat_and_the_cash_line_is_the_last_paid_score():
    field = _field(
        np.array([np.arange(9) + k for k in range(0, 21)]), np.zeros((0, 9), int), fg.HerdModel(), fmt="cash"
    )
    cands = np.array([np.arange(9) + 4, np.arange(9) + 15])  # 4 / 15 of the 21 field lineups score more
    res = csim.simulate_contests({"cash": field}, [None] * N_PLAYERS, cands, [DU], points=_ranked_points())
    t = res.table
    assert t["p_cash"].tolist() == [1.0, 0.0]
    cum = csim._cumulative(pay.payout_table(DU))  # noqa: SLF001
    place = 1 + 4 / 21 * 999
    assert t["e_dollars"].iloc[0] == pytest.approx(
        csim.block_payout(cum, np.array([place]), np.array([t["e_copies"].iloc[0]]))[0]
    )
    assert t["e_dollars"].iloc[1] == 0.0
    lines = res.lines["du"]
    assert lines.shape == (3, 2)
    assert np.all(lines[:, 0] <= lines[:, 1])


def test_the_herd_lead_is_drawn_per_draw_and_averages_to_the_expected_weights():
    herd = fg.HerdModel(share=0.2, decay=0.4, k=5, lead_decay=0.6)
    rng = np.random.default_rng(0)
    w = herd.draw_weights(5, 20000, rng)
    assert np.allclose(w.sum(axis=1), 1.0)
    assert w.mean(axis=0) == pytest.approx(herd.expected_weights(5), abs=0.01)
    assert (w.argmax(axis=1) != 0).mean() > 0.3  # the lead is often not the first
    fixed = fg.HerdModel(share=0.2, decay=0.4, k=5).draw_weights(5, 10, rng)
    assert np.allclose(fixed, fg.HerdModel(decay=0.4).weights(5))


def test_sim_leverage_counts_the_top_one_percent_of_the_gpp_field():
    sampler = np.array([np.arange(9) + k for k in range(0, 21)])
    field = _field(sampler, np.zeros((0, 9), int), fg.HerdModel())
    res = csim.simulate_contests(
        {"gpp": field}, [None] * N_PLAYERS, sampler[:1], [GPP], points=_ranked_points()
    )
    opt = res.opt.set_index("name")
    assert opt["opt_pct"].sum() == pytest.approx(9.0, rel=0.15)  # nine players' worth of the top 1%
    assert opt.loc["p00", "opt_pct"] > opt.loc["p29", "opt_pct"]
    assert (opt["sim_lev"] == opt["opt_pct"] - opt["own"]).all()


def test_draw_points_follow_the_specs_and_the_seed():
    specs = [
        PlayerSpec("a", "QB", "T1", "T2", "g1", "QB1", 20.0),
        None,
        PlayerSpec("b", "WR", "T1", "T2", "g1", "WR1", 15.0),
    ]
    a = csim.draw_points(specs, 4000, seed=3)
    b = csim.draw_points(specs, 4000, seed=3)
    assert a.shape == (4000, 3) and np.array_equal(a, b)
    assert (a[:, 1] == 0).all()
    assert a[:, 0].mean() == pytest.approx(20.0, rel=0.1)
    assert np.corrcoef(a[:, 0], a[:, 2])[0, 1] > 0.2  # a QB and his WR1 move together


def test_scenarios_reweight_the_same_lineups_to_each_drawn_ownership():
    rng = np.random.default_rng(0)
    sampler = np.sort(np.array([rng.choice(N_PLAYERS, 9, replace=False) for _ in range(800)]), axis=1)
    field = _field(sampler, np.zeros((0, 9), int), fg.HerdModel())
    base = np.bincount(sampler.ravel(), minlength=N_PLAYERS) / len(sampler)
    tilt = base * np.exp(rng.normal(0, 0.3, (4, N_PLAYERS)))
    targets = tilt * (9.0 / tilt.sum(axis=1, keepdims=True))
    out = csim.with_scenarios(field, targets)
    assert out.scenarios.shape == (4, len(sampler))
    assert out.scenarios.sum(axis=1) == pytest.approx(1.0)
    fit = fg.scenario_fit(sampler, N_PLAYERS, out.scenarios, targets)
    unweighted = fg.scenario_fit(sampler, N_PLAYERS, np.full((4, len(sampler)), 1 / len(sampler)), targets)
    assert fit["mae"] < unweighted["mae"] / 4
    assert 50 < fit["ess"] < len(sampler)
    # the sim runs on the reweighted field, and draw d uses scenario d % 4
    res = csim.simulate_contests(
        {"gpp": out}, [None] * N_PLAYERS, sampler[:2], [GPP], points=_ranked_points(8), seed=0
    )
    assert len(res.table) == 2


def test_a_field_keyed_by_a_contest_is_used_for_that_contest_only():
    weak = _field(np.array([np.arange(9) + 21] * 4), np.zeros((0, 9), int), fg.HerdModel())  # worst lineups
    strong = _field(np.array([np.arange(9)] * 4), np.zeros((0, 9), int), fg.HerdModel())  # the best lineup
    mid = np.arange(9) + 10
    other = pay.ContestKind(
        "other", "SE GPP", entries=1000, places_paid=200, prize_pool=8400.0, fee=10.0, min_cash=2.0
    )
    res = csim.simulate_contests(
        {"gpp": weak, "other": strong},
        [None] * N_PLAYERS,
        np.array([mid]),
        [GPP, other],
        points=_ranked_points(),
    )
    by = res.table.set_index("contest")["p_cash"]
    assert by["gpp"] == 1.0 and by["other"] == 0.0  # beats the weak field, loses to its own strong one
