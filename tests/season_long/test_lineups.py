import pandas as pd

from dfs.season_long import lineups as lu

from . import synthetic as syn


def _set(v, pid, week, pts=None, sd=None):
    w = v.ros.weekly
    row = (w["player_id"] == pid) & (w["week"] == week)
    if pts is not None:
        w.loc[row, "pts"] = pts
    if sd is not None:
        w.loc[row, "sd"] = sd


def test_plan_is_the_best_lineup_with_ranges(monkeypatch):
    v = syn.view()
    ev = syn.evaluator(v, monkeypatch)
    p = lu.plan(v, ev)
    assert p.week == 8 and [s.player_id for s in p.starters] == ["qb1", "rb1", "wr1", "wr2"]
    assert p.mean == 63.0 and p.opponent == "Team 2" and 0 < p.win_prob < 1
    s = p.starters[0]
    assert s.floor < s.pts < s.ceiling and s.floor >= 0


def test_an_underdog_takes_the_ceiling_and_a_favourite_the_floor(monkeypatch):
    v = syn.view()
    _set(v, "wr2", 8, pts=16, sd=1.0)  # the safe one, starting at FLEX
    _set(v, "wr3", 8, pts=15, sd=15.0)  # one point less, much wider
    _set(v, "rb2", 8, pts=45)  # team 2 (this week's opponent) far ahead: Sam is a big underdog
    p = lu.plan(v, syn.evaluator(v, monkeypatch))
    assert "wr3" in [s.player_id for s in p.starters] and p.win_prob < 0.3
    v = syn.view()
    _set(v, "wr2", 8, pts=16, sd=1.0)
    _set(v, "wr3", 8, pts=15, sd=15.0)
    _set(v, "rb2", 8, pts=2)  # now Sam is the big favourite
    p = lu.plan(v, syn.evaluator(v, monkeypatch))
    assert "wr2" in [s.player_id for s in p.starters] and p.win_prob > 0.7
    assert any(s.close for s in p.starters)


def test_check_flags_empty_out_and_bye_with_the_swap(monkeypatch):
    v = syn.view()
    ev = syn.evaluator(v, monkeypatch)
    me = v.league.me
    me.starters = ["qb1", "rb1", "wr1", ""]  # FLEX left empty
    alerts = lu.check(v, ev, news=None)
    assert [(a.cue, a.slot) for a in alerts] == [("SWAP", "FLEX")] and alerts[0].swap.startswith("start WR2")
    me.starters = ["qb1", "rb1", "wr1", "wr2"]
    v.table.loc["wr1", "injury_status"] = "Out"
    _set(v, "rb1", 8, pts=0.0)  # on bye
    alerts = {a.slot: a for a in lu.check(v, ev, news=None)}
    assert (
        alerts["WR"].cue == "SWAP"
        and alerts["WR"].reason == "Out"
        and alerts["WR"].swap.startswith("start WR3")
    )
    assert alerts["RB"].cue == "SWAP" and "bye" in alerts["RB"].reason


def test_check_reads_espn_news_over_sleeper(monkeypatch):
    v = syn.view()
    ev = syn.evaluator(v, monkeypatch)
    v.league.me.starters = ["qb1", "rb1", "wr1", "wr2"]
    when = pd.Timestamp("2026-11-01", tz="UTC")
    news = pd.DataFrame({"Name": ["QB1"], "Status": ["Out"], "Updated": [when], "Note": [""]})
    alerts = lu.check(v, ev, news)
    assert alerts[0].slot == "QB" and alerts[0].reason == "Out (ESPN)"


def test_no_k_or_def_slot_means_no_streams(monkeypatch):
    v = syn.view()
    assert lu.streams(v, syn.evaluator(v, monkeypatch)) == {}
