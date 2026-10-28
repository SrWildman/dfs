from datetime import date

import pandas as pd
import pytest

from dfs.season_long import consensus as cs
from dfs.season_long import consensus_check as cc
from dfs.season_long import finder as fd
from dfs.season_long import sleeper_league as sl
from dfs.season_long import waivers as wv

from . import synthetic as syn


def _claims(monkeypatch, bids):
    """Completed claims in week 5: (player_id, bid)."""
    txs = [{"type": "waiver", "status": "complete", "settings": {"waiver_bid": b}, "adds": {p: 2}, "leg": 5}
           for p, b in bids]  # fmt: skip
    monkeypatch.setattr(sl, "fetch_transactions", lambda lid, w, final: txs if w == 5 else [])
    proj = pd.DataFrame(
        {"player_id": [p for p, _ in bids], "week": 5, "proj": 4.0, "team": "NFL", "opponent": "X"}
    )
    monkeypatch.setattr(wv.ros, "projections", lambda *a, **k: proj)


def test_pickup_adds_value_and_drops_the_player_missed_least(monkeypatch):
    v = syn.view()
    ev = syn.evaluator(v, monkeypatch)
    _claims(monkeypatch, [("wr4", 10), ("wr5", 20), ("wr6", 30)])
    # A named free agent is always reported, with the roster spot he takes (Sam's roster is full).
    picks = wv.rank_pickups(v, ev, only=["fa1"])
    assert len(picks) == 1 and picks[0].drop in ev.rosters[1]
    # The top list never recommends a pickup that makes Sam worse.
    assert all(p.value > 0 for p in wv.rank_pickups(v, ev, top=5))


def test_bid_guide_uses_similar_claims_and_names_rivals(monkeypatch):
    v = syn.view()
    _claims(monkeypatch, [("wr4", 10), ("wr5", 20), ("wr6", 30)])
    history = wv.claims(v.league)
    values = wv.claim_values(v.league, history)
    guide = wv.bid_guide(v, history, values, "fa1", worth=25)
    assert guide.comps == 3 and guide.low == 20 and guide.high == 26
    text = guide.text(have=50)
    assert "$20-26 to win (3 similar claims)" in text and "worth up to $25" in text


def test_bid_guide_without_comps_says_so(monkeypatch):
    v = syn.view()
    _claims(monkeypatch, [])
    guide = wv.bid_guide(v, wv.claims(v.league), pd.Series(dtype=float), "fa1", worth=None)
    assert guide.comps == 0 and guide.text(50).startswith("no paid claims")


def test_handcuff_value_needs_an_rb2_behind_a_starter(monkeypatch):
    v = syn.view()
    ev = syn.evaluator(v, monkeypatch)
    v.cfg.starter_miss_rate = 0.1  # the synthetic league turns the miss rate off
    v.table["depth"] = 3
    v.table.loc["rb4", "depth"] = 2
    v.table.loc["rb2", "depth"] = 1
    value, rb1 = wv.handcuff_value(v, ev, "rb4", [*ev.rosters[1], "rb4"], bump=5.0)
    assert rb1 == "rb2" and value > 0
    assert wv.handcuff_value(v, ev, "wr4", [*ev.rosters[1], "wr4"], bump=5.0) == (0.0, "")


def test_chop_plan_bid_range():
    plan = wv.ChopPlan("T", 0.6, [], faab=300, rivals=[])
    assert plan.bid(worth=500, price=200) == "bid $200-300"
    assert plan.bid(worth=100, price=200) == "bid $100"
    assert plan.bid(worth=0, price=200).startswith("no bid")


def test_lopsided_ideas_are_hidden_unless_asked(monkeypatch):
    v = syn.view()
    ev = syn.evaluator(v, monkeypatch)
    # The market loves what Sam gives: every trade is lopsided for the partner.
    cons = {**{p: 5.0 for p in v.ros.summary.index}, "wr2": 80.0, "wr3": 80.0}
    m = syn.market(v, cons)
    assert all(not i.lopsided for i in fd.find_trades(v, ev, m, fd.Filters()))
    shown = fd.find_trades(v, ev, m, fd.Filters(show_lopsided=True))
    assert any(i.lopsided for i in shown)
    assert all("FAAB" in i.why for i in shown if i.lopsided)


def test_consensus_check_waits_for_a_week_then_scores_both_views(monkeypatch):
    table = pd.DataFrame(
        {"name": [f"P{i}" for i in range(10)], "team": "NFL", "position": "WR"},
        index=[f"p{i}" for i in range(10)],
    )
    day = date(2026, 10, 28)
    ours = table.assign(ros_pts=[100 - 5 * i for i in range(10)], games_left=10)
    cc.archive_ours("L", ours, day)
    # The market has the order backwards.
    cs.archive("ros-ppr-overall", pd.DataFrame({"name": table["name"][::-1], "team": "NFL", "position": "WR",
                                                "ecr": range(1, 11)}), day)  # fmt: skip
    early = cc.score("L", {"rec": 1.0}, 2026, 8, table)
    assert early.snapshots == 0 and "no week played" in early.line()
    act = pd.DataFrame(
        {"player_id": list(table.index), "week": 9, "pts": [20 - i for i in range(10)], "played": True}
    )
    monkeypatch.setattr(cc.ros, "actuals", lambda *a, **k: act)
    monkeypatch.setattr(cc.cs, "match_to_sleeper", lambda ecr, t: pd.Series(dict(zip(ecr["name"].map(
        {n: p for p, n in table["name"].items()}), ecr["ecr"], strict=True))))  # fmt: skip
    later = cc.score("L", {"rec": 1.0}, 2026, 9, table)
    assert later.snapshots == 1 and later.ours == pytest.approx(1.0) and later.market == pytest.approx(-1.0)
    assert "ours ahead" in later.line()


def test_weeks_after_a_snapshot():
    assert cc.weeks_after(date(2026, 10, 28), 2026, 9) == [8, 9]
    assert cc.weeks_after(date(2026, 10, 31), 2026, 9) == [9]
