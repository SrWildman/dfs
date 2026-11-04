import math

import numpy as np
import pandas as pd

from dfs.season_long import across, calibration, history, outlook, outlook_check, outlook_run
from dfs.season_long import lineups as lu
from dfs.season_long import season_tabs as st
from dfs.season_long import sleeper_league as sl
from dfs.season_long import trade as tr

from . import synthetic as syn


def _sim(mean, pairs, wins, pf, rules, draws=50, sd=0.0):
    n = len(wins)
    weeks = len(pairs)
    ids = list(range(1, n + 1))
    return outlook.simulate(
        ids,
        np.array(mean, float).reshape(weeks, n),
        np.full((weeks, n), sd),
        np.random.default_rng(1).standard_normal((draws, weeks, n)),
        pairs,
        np.array(wins),
        np.array(pf),
        rules,
    )


def test_byes_follow_the_bracket():
    assert [outlook.byes_for(n) for n in (1, 4, 6, 7, 8, 12)] == [0, 0, 2, 1, 0, 4]


def test_a_certain_week_gives_certain_odds_and_median_wins_count():
    # Team 1 beats team 2 and the median game; team 3 beats team 4 but only the top two clear the median.
    r = outlook.Rules(spots=2, byes=1, median_game=True)
    st_ = _sim([[50, 10, 30, 20]], [[(1, 2), (3, 4)]], [0, 0, 0, 0], [0, 0, 0, 0], r)
    assert st_.p_playoffs.tolist() == [1.0, 0.0, 1.0, 0.0] and st_.p_bye.tolist() == [1.0, 0, 0, 0]
    assert st_.exp_wins.tolist() == [2.0, 0.0, 2.0, 0.0]


def test_with_no_regular_games_left_the_standings_decide():
    r = outlook.Rules(spots=1)
    out = _sim(np.zeros((0, 2)), [], [4, 3], [10.0, 99.0], r)
    assert out.p_playoffs.tolist() == [1.0, 0.0] and out.exp_wins.tolist() == [4.0, 3.0]


def test_points_for_breaks_a_tie_in_wins():
    r = outlook.Rules(spots=1)
    out = _sim([[10, 10]], [[]], [3, 3], [100.0, 120.0], r)
    assert out.p_playoffs.tolist() == [0.0, 1.0]


def test_division_leaders_are_seeded_first_when_the_rules_say_so():
    wins = np.array([[5.0, 4.0, 3.0, 3.0]])
    pf = np.array([[100.0, 100.0, 100.0, 90.0]])
    ids = [1, 2, 3, 4]
    div = {1: 1, 2: 1, 3: 2, 4: 2}  # team 3 leads division 2 with worse wins than team 2
    plain = outlook.Rules(spots=3, division=div)
    first = outlook.Rules(spots=3, division=div, division_winners_first=True)
    assert outlook.seed_rank(wins, pf, ids, plain)[0].tolist() == [0, 1, 2, 3]
    assert outlook.seed_rank(wins, pf, ids, first)[0].tolist() == [0, 2, 1, 3]
    assert outlook.division_leaders(wins, pf, ids, first)[0].tolist() == [True, False, True, False]


def test_rules_read_the_leagues_settings():
    lg = syn.league()
    lg.playoff_teams = 6
    lg.raw_settings = {"league_average_match": 1, "playoff_seed_type": 1}
    lg.teams[0].division, lg.teams[1].division = 1, 2
    r = outlook.rules_for(lg)
    assert (r.spots, r.byes, r.median_game) == (6, 2, True)
    assert r.division == {1: 1, 2: 2} and r.division_winners_first


def test_opponent_strength_is_the_opponents_average_over_the_league():
    mean = np.array([[30.0, 10.0, 20.0, 20.0]])
    out = outlook.opponent_strength([1, 2, 3, 4], mean, [[(1, 2), (3, 4)]])
    assert out[1] == 10.0 - 20.0 and out[2] == 30.0 - 20.0 and out[3] == 0.0


def _proj():
    rows = []
    # Three RBs facing DEF-A (soft) in some weeks and DEF-B (tough) in others.
    for pid in ("a", "b", "c"):
        for w, opp, pts in ((1, "A", 15.0), (2, "B", 9.0), (3, "A", 15.0), (4, "B", 9.0)):
            rows.append({"player_id": pid, "week": w, "proj": pts, "team": "X", "opponent": opp})
    return pd.DataFrame(rows)


def test_defense_index_and_player_schedule():
    pos = pd.Series({"a": "RB", "b": "RB", "c": "RB"})
    idx = outlook.defense_index(_proj(), pos)
    assert idx.at["A", "RB"] > 1.0 > idx.at["B", "RB"]
    summary = pd.DataFrame({"team": ["X"], "position": ["RB"]}, index=["a"])
    soft = outlook.player_sos(_proj(), summary, idx, [1, 3])["a"]
    hard = outlook.player_sos(_proj(), summary, idx, [2, 4])["a"]
    assert soft > 1.0 > hard
    assert math.isnan(outlook.player_sos(_proj(), summary, idx, [9])["a"])
    weights = pd.Series({"a": 10.0})
    assert math.isnan(outlook.team_sos(["z"], pd.Series({"a": 1.1}), weights))


# -- calibration -----------------------------------------------------------------------------------------


def _misses(spread, n=80, seed=3):
    rng = np.random.default_rng(seed)
    return pd.DataFrame(
        {
            "league": "L",
            "week": np.repeat(np.arange(1, 9), n // 8),
            "roster_id": range(n),
            "actual": 100 + spread * 10 * rng.standard_normal(n),
            "mean": 100.0,
            "sd": 10.0,
        }
    )


def test_spread_scale_measures_the_real_spread_over_the_modelled():
    s = calibration.fit_scale(_misses(0.85))
    assert 0.7 < s.scale < 1.0 and s.team_weeks == 80 and 0.95 < s.mean_ratio < 1.05
    assert calibration.fit_scale(_misses(0.85), through=3).through == 3
    assert calibration.fit_scale(_misses(0.1)).scale == calibration.SCALE_CLIP[0]  # clipped
    assert calibration.fit_scale(_misses(0.85).head(20)).scale == 1.0  # too few team-weeks to trust


# -- history ---------------------------------------------------------------------------------------------


def test_history_snapshots_moves_and_a_backfill_never_replaces_a_sync(tmp_path, monkeypatch):
    monkeypatch.setattr(history, "HISTORY_DIR", tmp_path)
    team = {"rank": 3, "value": 10.0, "p_playoffs": 0.4}
    assert history.save_snapshot("L", 5, {1: team}, "backfill")
    assert history.save_snapshot("L", 6, {1: {**team, "rank": 2}}, "sync")
    assert not history.save_snapshot("L", 6, {1: {**team, "rank": 9}}, "backfill")
    data = history.load("L")
    assert data[6]["teams"][1]["rank"] == 2 and data[5]["source"] == "backfill"
    assert history.rank_move(data, 6, 1, 2) == 1 and history.rank_move(data, 5, 1, 3) is None
    assert history.series(data, 1, "p_playoffs") == [(5, 0.4), (6, 0.4)]


# -- the state at an earlier week -----------------------------------------------------------------------


def _matchups(weeks):
    def fetch(league_id, week, final):
        return weeks.get(week, [])

    return fetch


def test_state_after_counts_wins_median_wins_and_survivors(monkeypatch):
    lg = syn.league()
    lg.raw_settings = {"league_average_match": 1}
    w1 = [
        {"roster_id": 1, "matchup_id": 1, "points": 100.0},
        {"roster_id": 2, "matchup_id": 1, "points": 90.0},
        {"roster_id": 3, "matchup_id": 2, "points": 80.0},
    ]
    monkeypatch.setattr(sl, "fetch_matchups", _matchups({1: w1}))
    s = outlook_check.state_after(lg, 1)
    # Team 1 beat 2 and the median (90); team 2 lost and sat on the median; team 3 lost the median game.
    assert s.wins == {1: 2.0, 2: 0.0, 3: 0.0} and s.points_for[1] == 100.0 and s.scores[2] == [90.0]
    chopped = syn.league(chopped=True)
    monkeypatch.setattr(sl, "fetch_matchups", _matchups({1: w1}))
    assert outlook_check.state_after(chopped, 1).alive == {1, 2}


def test_record_and_scoring_baselines():
    lg = syn.league()
    s = outlook_check.State(
        2,
        {1: 2.0, 2: 0.0, 3: 1.0},
        {},
        {1: 2, 2: 2, 3: 2},
        {1, 2, 3},
        {1: [120.0, 130.0], 2: [90.0, 80.0], 3: [100.0, 100.0]},
    )
    r = outlook_check.record_ratings([1, 2, 3], s)
    assert r[0] > r[2] > r[1]
    mean, sd = outlook_check.scoring_arrays([1, 2, 3], s, 2)
    assert mean.shape == (2, 3) and (sd >= 5.0).all()
    assert (
        mean[0][0] > mean[0][2] > mean[0][1] and 85.0 <= mean[0][1] and mean[0][0] <= 125.0
    )  # shrunk, same order
    assert lg  # the synthetic league builds


def test_brier_report_scores_a_better_model_lower():
    rows = []
    for i in range(40):
        y = float(i % 2)
        rows.append(("L", 3, "game", "ours", 0.8 if y else 0.2, y))
        rows.append(("L", 3 + i % 2, "game", "coin", 0.5, y))
    df = pd.DataFrame(rows, columns=outlook_check.EVENT_COLUMNS)
    b = outlook_check.brier(df).set_index(["kind", "model"])["brier"]
    assert abs(b[("game", "ours")] - 0.04) < 1e-9 and abs(b[("game", "coin")] - 0.25) < 1e-9
    assert any("ours - coin" in line for line in outlook_check.report_lines(df))


# -- the sheet's view of it -----------------------------------------------------------------------------


def _run(monkeypatch, alerts=()):
    v = syn.view()
    v.spread_note = "team spread 0.97x"
    ev = syn.evaluator(v, monkeypatch)
    monkeypatch.setattr(
        outlook_run,
        "schedule_strength",
        lambda view: (pd.Series(dtype=float), pd.Series(dtype=float), pd.DataFrame()),
    )
    run = st.LeagueRun(v, ev, syn.market(v), [], tr.free_move(v, ev, 1, []))
    run.alerts = list(alerts)
    run.outlook = outlook_run.compute(run, {})
    return run


def test_outlook_ranks_teams_and_fills_the_raw_columns(monkeypatch):
    run = _run(monkeypatch)
    o = run.outlook
    assert [t.rank for t in o.teams] == [1, 2, 3] and sum(t.mine for t in o.teams) == 1
    assert all(0.0 <= t.p_playoffs <= 1.0 for t in o.teams) and "of 3 teams" in o.rules
    teams = st.teams_frame([run])
    assert list(teams.columns) == st.TEAM_COLUMNS and teams["ByeOdds"].notna().all()
    block = st.outlook_block([run])
    assert block.rows[block.title_rows[0] - 1][0].startswith("Outlook") and len(block.header_rows) == 1
    assert sum(1 for r in block.rows if r and r[0] in (1, 2, 3)) == 3


def test_across_leagues_puts_swaps_before_looks_before_moves(monkeypatch):
    alerts = [
        lu.Alert("Test", "LOOK", "FLEX", "Q Guy", "Questionable"),
        lu.Alert("Test", "SWAP", "WR", "Out Guy", "OUT", "Bench Guy"),
    ]
    run = _run(monkeypatch, alerts)
    listed = across.items([run])
    assert [i.cue for i in listed if i.cue] == ["SWAP", "LOOK"] and listed[0].cue == "SWAP"
    rows = across.rows([run])
    assert rows[0] == across.ACROSS_HEADER and rows[1][3].startswith("SWAP WR Out Guy")


def test_overview_has_the_cross_league_block_and_the_odds_history(monkeypatch, tmp_path):
    monkeypatch.setattr(history, "HISTORY_DIR", tmp_path)
    run = _run(monkeypatch, [lu.Alert("Test", "SWAP", "WR", "Out Guy", "OUT", "Bench Guy")])
    for week, p in ((5, 0.3), (6, 0.45)):
        history.save_snapshot("Test", week, {1: {"rank": 1, "p_playoffs": p}}, "backfill")
    rows = st.overview_rows([run])
    titles, headers, pct = st.overview_marks(rows)
    assert rows[1] == st.OVERVIEW_HEADER and ["Player", "Pos", "Leagues", "Which"] in rows
    hist_header = next(i for i, r in enumerate(rows) if r[:2] == st.HISTORY_FIXED)
    row = rows[hist_header + 1]
    assert row[:4] == ["Test", "Playoffs", 0.3, 0.45]
    n = hist_header + 2  # 1-based row of the league's history line
    assert row[-1] == f'=SPARKLINE(C{n}:D{n},{{"charttype","line"}})'
    assert st.literal_rows([row])[0][-1] == row[-1]  # the one formula the sheet is allowed to evaluate
    assert (hist_header + 2, hist_header + 2, 3) in pct and len(titles) == 4 and hist_header + 1 in headers
