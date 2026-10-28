import pandas as pd

from dfs.season_long import activity as act
from dfs.season_long import sleeper_league as sl

from . import synthetic as syn


def test_manager_activity_counts_moves_trades_and_lineup_misses(monkeypatch):
    v = syn.view()
    trade = {
        "type": "trade",
        "status": "complete",
        "roster_ids": [2, 3],
        "adds": {"rb5": 2, "wr4": 3},
        "leg": 2,
    }
    claim = {"type": "waiver", "status": "complete", "roster_ids": [1], "adds": {"fa1": 1}, "leg": 6}
    failed = {"type": "free_agent", "status": "failed", "roster_ids": [3], "adds": {"fa1": 3}, "leg": 6}
    txs = {2: [trade], 6: [claim, failed]}
    monkeypatch.setattr(sl, "fetch_transactions", lambda lid, w, final: txs.get(w, []))
    # Week 7: team 2 leaves a slot empty and starts a player whose team did not play.
    v.table.loc["qb2", "team"] = "BYE"
    matchups = [
        {"roster_id": 2, "starters": ["qb2", "0", "rb2", "rb3"]},
        {"roster_id": 1, "starters": ["qb1"]},
    ]
    monkeypatch.setattr(sl, "fetch_matchups", lambda lid, w, final: matchups if w == 7 else [])
    proj = pd.DataFrame(
        {"player_id": ["x"], "week": [7], "proj": [1.0], "team": ["NFL"], "opponent": ["OPP"]}
    )
    monkeypatch.setattr(act.ros, "projections", lambda *a, **k: proj)
    acts = act.manager_activity(v)
    assert acts[1].moves == 1 and acts[1].last_move == 6 and acts[1].lineup_misses == 0
    assert acts[2].trades == 1 and acts[2].traded_positions == {"RB": 1} and acts[2].lineup_misses == 2
    assert acts[3].moves == 0  # a failed claim is not a move
    assert "inactive" in acts[2].label(current_week=8)  # last move Week 2, and lineup misses
    assert act.is_inactive(acts, 2, 8) and not act.is_inactive(acts, 1, 8)


def test_playoff_schedule_names_opponents_and_ease(monkeypatch):
    v = syn.view()
    v.league.playoff_week_start = 10
    v.league.playoff_teams = 4  # two rounds: Weeks 10-11
    proj = pd.DataFrame({"player_id": ["a", "b"], "week": [10, 11], "proj": 1.0, "team": "NFL",
                         "opponent": ["DAL", ""]})  # fmt: skip
    monkeypatch.setattr(act.ros, "projections", lambda *a, **k: proj)
    w = v.ros.weekly
    w.loc[(w["player_id"] == "wr1") & (w["week"].isin([10, 11])), "pts"] = 27.0  # 18 a week otherwise
    sched = act.playoff_schedule(v)
    assert sched.at["wr1", "PlayoffOpp"] == "DAL, BYE"
    assert sched.at["wr1", "PlayoffEase"] > 1.0
