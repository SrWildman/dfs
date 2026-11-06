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


def test_fade_chip_is_avoid_and_fade_with_its_record(features):
    calls = plays.make_calls(
        features, plays.Weights.prior(2026), season=2026, week=7, at=NOW, fade_record="FADE↓ 35-17 (67%)"
    ).by_name()
    te = calls["T02 TE1"]
    assert te.cash == "AVOID" and te.gpp == "FADE"
    assert "67%" in te.cash_reason and "67%" in te.gpp_reason


def test_heavy_chalk_is_core_unless_a_clearly_better_alternative(features):
    calls = plays.make_calls(features, plays.Weights.prior(2026), season=2026, week=7, at=NOW).by_name()
    qb = calls["T00 QB1"]
    assert qb.cash_own == pytest.approx(0.45)
    if qb.cash != "CORE":  # faded: the reason must state the gap, and it must be at least CHALK_GAP
        assert "Fading chalk" in qb.cash_reason
    else:
        assert "chalk" in qb.cash_reason.lower()


def test_chalk_rule_on_a_small_frame():
    frame = pd.DataFrame(
        {
            "Name": ["A", "B", "C", "D"],
            "Position": ["RB"] * 4,
            "Salary": [7000, 7200, 4000, 4100],
            "cash_value": [50.0, 55.0, 40.0, 30.0],
            "cash_own": [0.40, 0.05, 0.35, 0.01],
            "fade": [0.0, 0.0, 0.0, 0.0],
            "relevant": [True] * 4,
        }
    )
    score = pd.Series([-1.0, 1.0, -2.0, 0.0])
    labels, gap = plays.cash_labels(frame, score)
    assert labels[0] == "PLAY"  # A trails B by 5 points at his price: chalk, but not forced to CORE
    assert gap[0] == pytest.approx(5.0)
    assert labels[2] == "CORE"  # C is chalk and nobody at his price does clearly better (D: -10)


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
