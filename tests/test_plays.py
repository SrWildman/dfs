"""The plays model (`dfs.plays`, Plan M M1): calls on fixture slates, the news path, the weights, the record,
and no look-ahead."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import numpy as np
import pandas as pd
import pytest

from dfs import plays, sim_inputs

NOW = datetime(2026, 10, 18, 15, 0, tzinfo=UTC)
EARLY, LATE = "2026-10-18T17:00:00Z", "2026-10-18T20:25:00Z"
TABLE = {("RB1", "RB2"): 4.27, ("RB1", "RB3+"): 2.05, ("WR1", "WR2"): 1.49, ("WR1", "QB1"): -1.35}


def slate(teams: int = 8, seed: int = 3) -> pd.DataFrame:
    """A synthetic main slate: each team a QB, 3 RB, 5 WR, 2 TE and a DST, salaries that make the cap bind,
    usage shares and both ownership columns."""
    rng = np.random.default_rng(seed)
    base = {
        "QB": (1, 14, 24, 5000, 7500),
        "RB": (3, 6, 18, 4000, 8500),
        "WR": (5, 5, 17, 3500, 8500),
        "TE": (2, 4, 12, 2800, 5500),
        "DST": (1, 3, 9, 2000, 3500),
    }
    rows, pid = [], 1
    for t in range(teams):
        game = t // 2
        team, opp = f"T{t:02d}", f"T{(t ^ 1):02d}"
        for pos, (n, lo, hi, slo, shi) in base.items():
            for k in range(n):
                skill = max(0.0, min(1.0, 1.0 - k * 0.3 + rng.normal(0, 0.1)))
                own = round(0.02 + 0.25 * skill * rng.uniform(0.5, 1.2), 3)
                rows.append(
                    {
                        "Id": pid,
                        "Name": f"{team} {pos}{k + 1}",
                        "Position": pos,
                        "Team": team,
                        "Opp": opp,
                        "GameID": f"G{game}",
                        "GameStart": EARLY if game % 2 == 0 else LATE,
                        "Salary": int(round((slo + (shi - slo) * skill) / 100) * 100),
                        "CalPts": round(lo + (hi - lo) * skill + rng.normal(0, 1.0), 1),
                        "ProjPts": round(lo + (hi - lo) * skill, 1),
                        "Avail": "",
                        "Own%": own,
                        "GppOwn": own,
                        "CashOwn": own,
                        "Edge": "",
                        "Tgt%": (0.25 - 0.06 * k)
                        if pos in ("WR", "TE")
                        else (0.05 if pos == "RB" else np.nan),
                        "Rush%": (0.55 - 0.2 * k) if pos == "RB" else np.nan,
                    }
                )
                pid += 1
    return pd.DataFrame(rows)


def typed_cash():
    return sim_inputs.cash_line_from_results([], [], before_week=7)


@pytest.fixture(scope="module")
def features() -> plays.WeekFeatures:
    edge = slate()
    edge.loc[edge["Name"] == "T00 QB1", "CashOwn"] = 0.45  # chalk
    edge.loc[edge["Name"] == "T02 TE1", "Edge"] = "FADE↓"
    return plays.week_features(
        edge, typed_cash=typed_cash(), now=NOW, n_sims=400, field_lineups=600, bump_table=TABLE
    )


def test_every_rosterable_player_gets_two_labelled_calls_with_one_reason(features):
    calls = plays.make_calls(features, plays.Weights.prior(2026), season=2026, week=7, at=NOW)
    assert len(calls.calls) == len(features.frame) > 50
    for c in calls.calls:
        assert c.cash in plays.CASH_LABELS and c.gpp in plays.GPP_LABELS
        for reason in (c.cash_reason, c.gpp_reason):
            assert reason and reason.count(". ") <= 1, reason  # one sentence
    by_pos = pd.DataFrame([{"pos": c.position, "cash": c.cash} for c in calls.calls])
    for pos in plays.POSITIONS:
        assert (by_pos[(by_pos["pos"] == pos)]["cash"] == "CORE").sum() >= plays.CORE_N[pos]


FADE = plays.FadeEffect(points=-2.5, hit=0.673, n=52, weeks="Weeks 1-8", thin=False)


def test_fade_is_its_measured_effect_on_the_projection_not_an_override(features):
    """Sam, 2026-11-09: FADE↓ lowers the projection by Model Check's record before the sim, and the label
    comes from the corrected Cash % like everyone else's."""
    edge = slate()
    edge.loc[edge["Name"] == "T00 QB1", "CashOwn"] = 0.45
    edge.loc[edge["Name"] == "T02 TE1", "Edge"] = "FADE↓"
    kw = {"typed_cash": typed_cash(), "now": NOW, "n_sims": 400, "field_lineups": 600, "bump_table": TABLE}
    faded = plays.week_features(edge, fade=FADE, **kw)
    plain = features.frame.set_index("Name")
    row = faded.frame.set_index("Name").loc["T02 TE1"]
    assert row["fade_pts"] == pytest.approx(-2.5)
    assert row["CalPts"] == pytest.approx(plain.at["T02 TE1", "CalPts"] - 2.5)
    calls = plays.make_calls(faded, plays.Weights.prior(2026), season=2026, week=7, at=NOW, fade=FADE)
    te = calls.by_name()["T02 TE1"]
    assert te.cash_reason.startswith("FADE↓ (67% hit, -2.5 pts) applied; ")
    assert ("still beats" in te.cash_reason) == (te.alt_gap is not None and te.alt_gap < -0.05)
    if te.cash == "AVOID":
        assert te.alt_gap > 0 and "now costs" in te.cash_reason
    assert not plays.contradictions(plays.call_dict(te))
    assert any("FADE↓ (67% hit, -2.5 pts) taken off" in n for n in calls.notes)
    # a thin record moves nothing, and the chip alone no longer labels anyone
    thin = plays.week_features(edge, fade=plays.FadeEffect(-3.0, 0.75, 8, "Week 1", True), **kw)
    assert thin.frame.set_index("Name").at["T02 TE1", "CalPts"] == plain.at["T02 TE1", "CalPts"]
    assert "fade" not in plays.FEATURES["cash"] and "fade" not in plays.FEATURES["gpp"]


def test_fade_effect_reads_model_checks_row():
    rec = {"n": 52, "VsProj": -1.88, "VsCal": -2.51, "HitProj": 0.673, "HitCal": 0.673}
    fx = plays.FadeEffect.from_record(rec, (1, 8))
    assert fx.applies and fx.points == pytest.approx(-2.51) and fx.weeks == "Weeks 1-8"
    assert fx.label() == "FADE↓ (67% hit, -2.5 pts)"
    assert not plays.FadeEffect.from_record({"n": 18, "VsCal": -1.9}).applies  # thin
    assert not plays.FadeEffect.from_record(None).applies


def test_heavy_chalk_is_core_unless_a_clearly_better_non_chalk_alternative(features):
    calls = plays.make_calls(features, plays.Weights.prior(2026), season=2026, week=7, at=NOW).by_name()
    qb = calls["T00 QB1"]
    assert qb.cash_own == pytest.approx(0.45)
    if qb.cash != "CORE":  # not core: a non-chalk player at his price beats him by the chalk gap
        assert qb.chalk_gap >= plays.CHALK_GAP and "chalk" in qb.cash_reason.lower()
    else:
        assert qb.chalk_gap is None or qb.chalk_gap < plays.CHALK_GAP


def small(names, salaries, values, owns, pos="WR"):
    return pd.DataFrame(
        {
            "Name": names,
            "Position": [pos] * len(names),
            "Salary": salaries,
            "cash_value": values,
            "cash_own": owns,
            "fade": [0.0] * len(names),
            "relevant": [True] * len(names),
        }
    )


def test_two_chalk_players_close_in_price_do_not_knock_each_other_down():
    """Week 9 (versions 1-2): Puka 52% and JSN 32%, $500 apart, kept each other off CORE."""
    frame = small(["Puka", "JSN", "Cheap"], [8800, 9300, 4000], [30.0, 34.0, 45.0], [0.52, 0.32, 0.02])
    labels, alts = plays.cash_labels(frame, pd.Series([-1.0, -0.5, 1.0]))
    assert labels[0] == "CORE" and labels[1] == "CORE"  # JSN beats Puka by 4, but JSN is chalk too
    assert alts.at[0, "alt_gap"] == pytest.approx(4.0) and np.isnan(alts.at[0, "chalk_gap"])


def test_chalk_beaten_by_a_non_chalk_player_by_the_gap_is_never_core():
    frame = small(
        ["A", "B", "C", "D"],
        [7000, 7200, 4000, 4100],
        [50.0, 55.0, 40.0, 30.0],
        [0.40, 0.05, 0.35, 0.01],
        pos="RB",
    )
    labels, alts = plays.cash_labels(frame, pd.Series([2.0, 1.0, -2.0, 0.0]))
    assert labels[0] == "PLAY"  # a score CORE, but B (5% owned) adds 5 points at his price
    assert alts.at[0, "chalk_gap"] == pytest.approx(5.0)
    assert labels[2] == "CORE"  # C is chalk and nobody at his price does clearly better (D: -10)


def test_a_player_who_beats_his_price_alternatives_is_never_avoid():
    """Week 9: JSN read "Costs -5.4 points of Cash % against Puka" and was AVOID."""
    names = [f"W{i}" for i in range(10)]
    sal = [9300, 8800, 8300, 8100, 6000, 5900, 4500, 4400, 3500, 3400]
    val = [34.6, 29.2, 32.2, 37.4, 40.0, 41.0, 47.0, 46.0, 30.0, 29.0]
    frame = small(names, sal, val, [0.1] * 10)
    score = pd.Series([-0.5, -0.9, -0.7, -0.3, 0.0, 0.1, 0.5, 0.4, -0.2, -0.25])
    labels, alts = plays.cash_labels(frame, score)
    for i in frame.index:
        if labels[i] == "AVOID":
            assert alts.at[i, "alt_gap"] > 0
    assert labels[0] != "AVOID"  # JSN: nobody within $500 beats him
    assert labels[3] != "AVOID"  # St. Brown beats CeeDee
    assert labels[1] == "AVOID"  # Puka: JSN beats him by 5.4


def test_avoid_is_cut_within_each_position():
    n = 20
    sal, val = [5000 + 50 * i for i in range(n)], [40.0 - i for i in range(n)]
    wr = small([f"W{i}" for i in range(n)], sal, val, [0.01] * n)
    rb = small([f"R{i}" for i in range(n)], sal, val, [0.01] * n, pos="RB")
    frame = pd.concat([wr, rb], ignore_index=True)
    score = pd.Series([10.0 - i for i in range(n)] + [-10.0 - i for i in range(n)])  # every RB below every WR
    labels, _ = plays.cash_labels(frame, score)
    assert (labels[:n] == "AVOID").sum() == 6 and (labels[n:] == "AVOID").sum() == 6  # 30% of each


@pytest.mark.parametrize("week", [2, 4, 5, 6, 7, 8])
def test_no_label_contradicts_its_reason_on_any_week(week):
    """Every call of a slate per backtest week (a different seed each) agrees with its own numbers."""
    edge = slate(seed=week)
    edge.loc[edge.index[::7], "CashOwn"] = 0.35  # plenty of chalk
    edge.loc[edge["Position"].eq("TE") & edge["Name"].str.endswith("1"), "Edge"] = "FADE↓"
    wf = plays.week_features(
        edge, typed_cash=typed_cash(), now=NOW, n_sims=300, field_lineups=400, bump_table=TABLE, fade=FADE
    )
    calls = plays.make_calls(wf, plays.Weights.prior(2026), season=2026, week=week, at=NOW, fade=FADE)
    bad = [f"{c.name}: {b}" for c in calls.calls for b in plays.contradictions(plays.call_dict(c))]
    assert not bad, bad[:5]


def test_contradictions_catch_week_9s_saved_calls():
    """The records saved before M1.6 carry no gaps: the numbers in the reason line are enough."""
    jsn = {
        "cash": "AVOID",
        "cash_own": 0.15,
        "cash_reason": "Costs -5.4 points of Cash % against Puka Nacua ($8,800) at the same price.",
        "gpp": "NEUTRAL",
        "gpp_reason": "",
    }
    puka = {
        "cash": "AVOID",
        "cash_own": 0.49,
        "cash_reason": "Fading chalk: 49% of the double-up plays him, "
        "but Jaxon Smith-Njigba ($9,300) adds 5.4 more points of Cash % at the price.",
        "gpp": "NEUTRAL",
    }
    fade = {
        "cash": "NEUTRAL",
        "gpp": "FADE",
        "gpp_reason": "Over-owned for his ceiling: 8% owned, in 12% of simulated top-1% lineups.",
    }
    assert plays.contradictions(jsn) and plays.held_back(jsn, "cash")
    assert not plays.contradictions(puka) and plays.held_back(puka, "cash")  # chalk AVOID: held back anyway
    assert plays.contradictions(fade) and plays.held_back(fade, "gpp") and not plays.held_back(fade, "cash")
    ok = {
        "cash": "AVOID",
        "cash_own": 0.05,
        "alt_gap": 2.0,
        "cash_reason": "Costs 2.0 points of Cash % against X ($5,000) at the same price.",
        "gpp": "LEVERAGE",
        "lev": 0.03,
        "gpp_reason": "Under-owned for his ceiling: 5% owned, in 8% of simulated top-1% lineups.",
    }
    assert not plays.contradictions(ok) and not plays.held_back(ok, "cash") and not plays.held_back(ok, "gpp")


def games_rows(team="T01"):
    """news_context's per-game rows: the team's QB started weeks 3-5 (100% of the snaps)."""
    rows = []
    for wk in (3, 4, 5):
        rows.append({"Name": f"{team} QB1", "Team": team, "Position": "QB", "Wk": wk, "Snap%": 1.0})
        rows.append({"Name": f"{team} QB2", "Team": team, "Position": "QB", "Wk": wk, "Snap%": 0.0})
        for k, tgt in ((1, 0.28), (2, 0.20), (3, 0.10)):
            rows.append({"Name": f"{team} WR{k}", "Team": team, "Position": "WR", "Wk": wk, "Tgt%": tgt})
        rows.append({"Name": f"{team} TE1", "Team": team, "Position": "TE", "Wk": wk, "Tgt%": 0.15})
        rows.append(
            {"Name": f"{team} RB1", "Team": team, "Position": "RB", "Wk": wk, "Tgt%": 0.13, "Snap%": 0.7}
        )
    return rows


QB_TABLE = {"WR": -0.64, "TE": -0.64}


def test_qb_downgrade_moves_his_pass_catchers_by_the_measured_points():
    edge = slate()
    edge.loc[edge["Name"] == "T01 QB1", "Avail"] = "OUT"
    backup = edge[edge["Name"] == "T01 QB1"].assign(Name="T01 QB2", Avail="", CalPts=12.0, Id=9999)
    edge = pd.concat([edge, backup], ignore_index=True)
    news = {q.name: q for q in plays.qb_news(edge, games_rows(), QB_TABLE)}
    assert set(news) == {"T01 WR1", "T01 WR2", "T01 WR3", "T01 TE1"}  # the RB's cell does not ship
    assert news["T01 WR1"].points == pytest.approx(-0.64)
    assert news["T01 WR1"].why == "QB downgrade: QB2 (backup) for QB1 (OUT)"
    wf = plays.week_features(
        edge,
        typed_cash=typed_cash(),
        now=NOW,
        n_sims=200,
        field_lineups=300,
        bump_table=TABLE,
        games=games_rows(),
        qb_table=QB_TABLE,
    )
    row = wf.frame.set_index("Name").loc["T01 WR1"]
    base = edge.set_index("Name").at["T01 WR1", "CalPts"]
    assert row["qb"] == pytest.approx(-0.64) and row["CalPts"] == pytest.approx(base - 0.64)
    calls = plays.make_calls(wf, plays.Weights.prior(2026), season=2026, week=7, at=NOW).by_name()
    assert "QB downgrade: QB2 (backup) for QB1 (OUT): -0.6 points applied" in calls["T01 WR1"].cash_reason
    # a healthy starter: no news; no games: no news
    healthy = edge.copy()
    healthy.loc[healthy["Name"] == "T01 QB1", "Avail"] = ""
    assert not plays.qb_news(healthy, games_rows(), QB_TABLE)
    assert not plays.qb_news(edge, [], QB_TABLE)


def test_news_bumps_teammates_of_a_fresh_out_once():
    edge = slate()
    edge.loc[edge["Name"] == "T01 RB1", "Avail"] = "OUT"
    earlier = slate()  # Tuesday: nobody out
    snaps = [(NOW - timedelta(days=4), earlier), (NOW - timedelta(days=1), edge)]
    news = plays.week_news(edge, snaps, TABLE)
    bumps = {b.name: b for b in news.bumps}
    assert bumps["T01 RB2"].bump == pytest.approx(4.27) and bumps["T01 RB2"].applied == pytest.approx(4.27)
    assert bumps["T01 RB3"].applied == pytest.approx(2.05)
    # the projection already moved +3: only the rest is added
    moved = edge.copy()
    moved.loc[moved["Name"] == "T01 RB2", "CalPts"] += 3.0
    news = plays.week_news(moved, snaps, TABLE)
    assert {b.name: b for b in news.bumps}["T01 RB2"].applied == pytest.approx(1.27)
    out = plays.apply_news(edge, plays.week_news(edge, snaps, TABLE))
    before = edge.set_index("Name")["CalPts"]
    assert out.set_index("Name").at["T01 RB2", "CalPts"] == pytest.approx(before["T01 RB2"] + 4.27)


def test_an_absence_carried_from_last_week_is_old_news():
    edge = slate()
    edge.loc[edge["Name"] == "T01 RB1", "Avail"] = "OUT"
    snaps = [(NOW - timedelta(days=4), slate()), (NOW - timedelta(days=1), edge)]
    assert not plays.week_news(edge, snaps, TABLE, carried={"T01 RB1"}).bumps
    assert not plays.week_news(edge, [(NOW - timedelta(days=4), edge)], TABLE).bumps  # out on the first


def test_two_regulars_out_on_one_team_get_no_bump():
    edge = slate()
    edge.loc[edge["Name"].isin(["T01 RB1", "T01 WR1"]), "Avail"] = "OUT"
    snaps = [(NOW - timedelta(days=4), slate()), (NOW - timedelta(days=1), edge)]
    assert not [b for b in plays.week_news(edge, snaps, TABLE).bumps if b.team == "T01"]


def test_own_lag_is_shrunk_toward_one():
    rows = pd.DataFrame(
        {"week": [4, 4], "fmt": ["cash", "cash"], "pred": [0.10, 0.10], "actual": [0.30, 0.30]}
    )
    lag = plays.fit_own_lag(rows)
    assert 1.0 < lag.cash < 3.0 and lag.gpp == 1.0
    assert plays.fit_own_lag(pd.DataFrame()).cash == 1.0


def test_fit_is_walk_forward_and_shrinks_to_the_prior(features):
    frame = features.frame
    noise = pd.Series(np.random.default_rng(0).normal(size=len(frame)), index=frame.index)
    w = plays.fit_weights({3: (frame, noise, None), 4: (frame, noise, None)}, season=2026, through=4)
    assert w.through == 4 and w.weeks["cash"] == [3, 4] and w.weeks["gpp"] == []
    assert w.weights["gpp"]["QB"] == plays.PRIOR["gpp"]  # nothing to fit: the prior
    for pos in plays.POSITIONS:
        for f, v in w.weights["cash"][pos].items():
            assert abs(v - plays.PRIOR["cash"][f]) < 0.5  # noise barely moves it
    with pytest.raises(ValueError):
        plays.fit_weights({5: (frame, noise, None)}, season=2026, through=4)


def test_weights_round_trip_and_load_the_latest_before_the_week(tmp_path):
    for through in (3, 5, 7):
        w = plays.Weights.prior(2026)
        w.through = through
        plays.save_weights(w, data_dir=tmp_path)
    assert plays.load_weights(2026, before_week=7, data_dir=tmp_path).through == 5
    assert plays.load_weights(2026, before_week=3, data_dir=tmp_path).through == 0  # the prior


def test_record_keeps_versions_and_reports_what_moved_up(features, tmp_path):
    w = plays.Weights.prior(2026)
    first = plays.make_calls(features, w, season=2026, week=7, at=NOW)
    rec1 = plays.save(first, data_dir=tmp_path)
    assert rec1["moved_up"] == [] and len(rec1["versions"]) == 1 and rec1["trust"] == "Shadow"
    second = plays.make_calls(features, w, season=2026, week=7, at=NOW + timedelta(hours=2))
    target = next(c for c in second.calls if c.cash == "NEUTRAL")
    target.cash = "CORE"
    rec2 = plays.save(second, data_dir=tmp_path)
    assert len(rec2["versions"]) == 2
    assert any(m["name"] == target.name and m["to"] == "CORE" for m in rec2["moved_up"])
    assert plays.moved_lines(rec2)[0].startswith("New must-plays")
    assert any("CASH" in line for line in plays.top_lines(rec2))


# ---------------------------------------------------------------------------------------------------------
# No look-ahead
# ---------------------------------------------------------------------------------------------------------


def test_input_columns_hold_nothing_from_after_the_lock():
    assert not set(plays.INPUT_COLUMNS) & set(plays.POST_LOCK_COLUMNS)


def test_post_lock_columns_change_nothing():
    edge = slate(teams=6)
    spoiled = edge.copy()
    rng = np.random.default_rng(9)
    for col in plays.POST_LOCK_COLUMNS:
        spoiled[col] = rng.uniform(0, 40, len(spoiled))
    kw = {"typed_cash": typed_cash(), "now": NOW, "n_sims": 200, "field_lineups": 300, "bump_table": TABLE}
    a = plays.week_features(edge, **kw).frame
    b = plays.week_features(spoiled, **kw).frame
    pd.testing.assert_frame_equal(a, b)


def test_snapshots_after_now_are_ignored():
    edge = slate(teams=6)
    later = edge.copy()
    later.loc[later["Name"] == "T01 RB1", "Avail"] = "OUT"
    kw = {"typed_cash": typed_cash(), "now": NOW, "n_sims": 200, "field_lineups": 300, "bump_table": TABLE}
    a = plays.week_features(edge, snapshots=[(NOW - timedelta(days=2), edge)], **kw)
    b = plays.week_features(
        edge, snapshots=[(NOW - timedelta(days=2), edge), (NOW + timedelta(hours=1), later)], **kw
    )
    pd.testing.assert_frame_equal(a.frame, b.frame)
    assert not b.news.bumps
