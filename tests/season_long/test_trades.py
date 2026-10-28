import numpy as np
import pandas as pd
import pytest

from dfs.season_long import consensus as cs
from dfs.season_long import finder as fd
from dfs.season_long import objective
from dfs.season_long import trade as tr

from . import synthetic as syn


def test_finder_trades_sams_wr_surplus_for_the_rb_team_needs(monkeypatch):
    v = syn.view()
    ev = syn.evaluator(v, monkeypatch)
    ideas = fd.find_trades(v, ev, syn.market(v), fd.Filters(), top=20)
    assert ideas, "Sam (deep WR, thin RB) and team 2 (deep RB, thin WR) should find a trade"
    best = ideas[0].analysis
    assert (
        best.me.delta > 0 and best.them.delta > 0 and best.fair_ratio >= 1 - fd.tr.DEFAULT_MAX_CONSENSUS_LOSS
    )
    assert all(i.analysis.me.delta <= best.me.delta for i in ideas)
    assert any(p.startswith("rb") for p in best.get.players) and any(
        p.startswith("wr") for p in best.give.players
    )


def test_finder_filters(monkeypatch):
    v = syn.view()
    ev = syn.evaluator(v, monkeypatch)
    m = syn.market(v)
    kept = fd.find_trades(v, ev, m, fd.Filters(keep=["WR1"]))
    assert all("wr1" not in i.analysis.give.players for i in kept)
    ones = fd.find_trades(v, ev, m, fd.Filters(shapes=("1-1",)))
    assert all(len(i.analysis.give.players) == len(i.analysis.get.players) == 1 for i in ones)
    only3 = fd.find_trades(v, ev, m, fd.Filters(partner="Team 3"))
    assert all(i.analysis.partner == 3 for i in only3)
    rb = fd.find_trades(v, ev, m, fd.Filters(get=["RB"]))
    assert rb and all(any(p.startswith("rb") for p in i.analysis.get.players) for i in rb)


def test_market_unfair_trade_is_dropped_or_sweetened_with_faab(monkeypatch):
    v = syn.view()
    ev = syn.evaluator(v, monkeypatch)
    # The market rates team 2's RBs far above Sam's WRs: a trade needs FAAB from Sam to look fair.
    cons = {**{p: 10.0 for p in v.ros.summary.index}, "rb2": 60.0, "rb3": 50.0, "rb4": 40.0}
    ideas = fd.find_trades(v, ev, syn.market(v, cons), fd.Filters(partner="Team 2"))
    for i in ideas:
        a = i.analysis
        assert a.fair_ratio >= 1 - tr.DEFAULT_MAX_CONSENSUS_LOSS
        assert a.give.faab <= ev.faab[1]


def test_analyzer_parses_names_and_faab_and_keeps_rosters_legal(monkeypatch):
    v = syn.view()
    ev = syn.evaluator(v, monkeypatch)
    a = tr.analyze(v, ev, syn.market(v), "WR2, wr3 + $10 FAAB", "RB2")
    assert (
        a.give.players == ["wr2", "wr3"] and a.give.faab == 10 and a.get.players == ["rb2"] and a.partner == 2
    )
    # Team 2 gets two for one and must drop to its roster size; Sam frees a spot and adds the free agent.
    assert len(a.them.dropped) == 1 and a.me.added == ["fa1"]
    assert a.verdict
    with pytest.raises(tr.TradeError):
        tr.analyze(v, ev, syn.market(v), "WR2 + $500 FAAB", "RB2")
    with pytest.raises(tr.TradeError):
        tr.analyze(v, ev, syn.market(v), "WR2", "RB2, WR5")


def test_settle_never_drops_the_only_player_for_a_dedicated_slot():
    v = syn.view()
    roster, dropped, _ = tr.settle(["qb1", "rb1", "wr1", "wr2", "wr3", "wr4"], 5, v, [])
    assert dropped == ["wr4"] and "rb1" in roster


def test_playoff_odds_follow_records_and_strength():
    normals = np.random.default_rng(0).standard_normal((2000, 2, 4))
    mean = np.array([[100.0, 120, 110, 90]] * 2)
    sd = np.full_like(mean, 15.0)
    p = objective.playoff_odds(
        [1, 2, 3, 4], mean, sd, normals, [[(1, 2), (3, 4)], [(1, 3), (2, 4)]],
        np.array([6, 6, 2, 2]), np.array([800.0] * 4), False, 2,
    )  # fmt: skip
    assert p.sum() == pytest.approx(2.0)
    assert p[0] > 0.9 and p[1] > 0.9 and p[3] < 0.05


def test_median_game_adds_a_win_for_the_top_half():
    normals = np.zeros((1, 1, 4))
    p = objective.playoff_odds(
        [1, 2, 3, 4], np.array([[130.0, 120, 110, 100]]), np.ones((1, 4)), normals, [[]],
        np.array([5, 5, 5, 6]), np.zeros(4), True, 3,
    )  # fmt: skip
    # The median game lifts 1 and 2 to 6 wins; 3 stays on 5 and, without a head-to-head, is out on wins.
    assert list(p) == [1.0, 1.0, 0.0, 1.0]


def test_chopped_pickups_reward_faab(monkeypatch):
    v = syn.view(chopped=True)
    ev = syn.evaluator(v, monkeypatch)
    base = ev.evaluate()
    richer = ev.evaluate(faab={1: 1000})
    assert richer[1].value >= base[1].value
    assert all(0.0 <= o.p_last_standing <= 1.0 for o in base.values())
    assert sum(o.p_last_standing for o in base.values()) == pytest.approx(1.0)


def test_consensus_maps_rank_to_our_value_curve_over_waiver():
    ours = pd.Series({"a": 100.0, "b": 80.0, "c": 50.0, "d": 10.0})
    ranks = pd.Series({"a": 2, "b": 1, "c": 3})
    out = cs.consensus_values(ours, ranks, rostered=3)
    assert out.to_dict() == {"a": 30.0, "b": 50.0, "c": 0.0, "d": 0.0}


def test_parse_ecr_and_page_choice():
    html = (
        'x\nvar ecrData = {"players": [{"player_name": "A B", "player_team_id": "JAC", '
        '"player_position_id": "DST", "rank_ecr": 7}]};\n'
    )
    frame = cs.parse_ecr(html)
    assert frame.to_dict("records") == [{"name": "A B", "team": "JAC", "position": "DEF", "ecr": 7}]
    assert cs.page_for({"rec": 0.5}) == "ros-half-point-ppr-overall" and cs.page_for({}) == "ros-overall"
    with pytest.raises(cs.ConsensusError):
        cs.parse_ecr("<html></html>")


def test_shapes():
    assert fd.parse_shapes("1-1, 2-1") == ("1-1", "2-1")
    with pytest.raises(tr.TradeError):
        fd.parse_shapes("3-1")


def test_a_player_list_sorts_into_sells_and_buys_per_league(monkeypatch):
    v = syn.view()
    ev = syn.evaluator(v, monkeypatch)
    sells, buys = fd.players_in(
        ["WR3", "RB2", "Nobody Here"], ev.rosters[1], [p for r in (2, 3) for p in ev.rosters[r]], v
    )
    assert sells == {"wr3"} and buys == {"rb2"}
    ideas = fd.find_trades(v, ev, syn.market(v), fd.Filters(players=["WR3", "RB2"]))
    assert ideas and all("wr3" in i.analysis.give.players or "rb2" in i.analysis.get.players for i in ideas)
    assert fd.find_trades(v, ev, syn.market(v), fd.Filters(players=["Nobody Here"])) == []


def test_a_sell_list_skips_names_not_on_the_roster_and_never_widens_to_everything(monkeypatch):
    v = syn.view()
    ev = syn.evaluator(v, monkeypatch)
    notes: list[str] = []
    ideas = fd.find_trades(v, ev, syn.market(v), fd.Filters(give=["WR3", "Somebody Else"]), notes=notes)
    assert ideas and all("wr3" in i.analysis.give.players for i in ideas)
    assert notes == ["not on your roster here, skipped: Somebody Else"]
    assert fd.find_trades(v, ev, syn.market(v), fd.Filters(give=["Somebody Else"])) == []


def test_targets_are_the_buy_list_or_the_weakest_spot(monkeypatch):
    v = syn.view()
    ev = syn.evaluator(v, monkeypatch)
    notes: list[str] = []
    # Sam is thinnest at RB; his buy list names a WR on team 3 and one of his own players (skipped).
    ideas = fd.find_trades(v, ev, syn.market(v), fd.Filters(targets=["WR5", "WR1"]), notes=notes)
    assert ideas
    for i in ideas:
        got = i.analysis.get.players
        assert "wr5" in got or any(p.startswith("rb") for p in got)
    assert notes[0].startswith("targets: your buy list (WR5) and your weakest spot, RB")
