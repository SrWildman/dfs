"""The plays model's backtest plumbing (`dfs.research.plays_backtest`) and its Flashback flag
(`dfs.plays_flag`)."""

from __future__ import annotations

import collections
from datetime import UTC, datetime

import numpy as np
import pandas as pd
import pytest

from dfs import plays
from dfs.research import plays_backtest as pb
from dfs.sim import retro


def contest(week=5, entries=1000, fmt="gpp"):
    """A field of `entries` lineups: 10 copies of a top lineup (A..I), the rest of a weak one (J..R)."""
    top = tuple(sorted("abcdefghi"))
    weak = tuple(sorted("jklmnopqr"))
    fpts = {k: 20.0 for k in top} | {k: 5.0 for k in weak}
    copies = collections.Counter({top: 10, weak: entries - 10})
    points = np.sort(np.array([180.0] * 10 + [45.0] * (entries - 10)))[::-1]
    own = {k: 10 / entries for k in top} | {k: (entries - 10) / entries for k in weak}
    return retro.ContestField("c1", week, "S", fmt, entries, entries // 5, points, fpts, copies, own)


def test_top1_shares_count_the_real_top_one_percent():
    shares = pb.top1_shares([contest()], 5)
    assert shares.loc["a", "top1_share"] == pytest.approx(1.0)
    assert shares.loc["j", "top1_share"] == pytest.approx(0.0)
    assert shares.loc["a", "own"] == pytest.approx(0.01)
    assert pb.top1_shares([contest(entries=500)], 5) is None  # too small to count
    assert pb.top1_shares([contest()], 6) is None


def test_cash_gap_and_matched_excess():
    frame = pd.DataFrame(
        {
            "Position": ["RB"] * 6,
            "Salary": [5000, 5100, 5200, 5000, 5100, 9000],
        }
    )
    labels = pd.Series(["CORE", "PLAY", "AVOID", "AVOID", "NEUTRAL", "CORE"])
    target = pd.Series([3.0, 2.0, 1.0, 0.0, 1.0, np.nan])
    gap, n_good, n_bad = pb.cash_gap(frame, labels, target)
    assert gap == pytest.approx(2.5 - 0.5) and (n_good, n_bad) == (2, 2)
    chosen = pd.Series([True, False, False, False, False, True])
    excess = pd.Series([0.10, 0.0, 0.02, -0.02, 0.0, 0.5])
    d, _se, n = pb.matched_excess(frame, chosen, excess)
    assert n == 1  # the $9,000 back has nobody at his price
    assert d == pytest.approx(0.10 - 0.0)


def test_stouffer_combines_one_sided():
    z, p = pb.stouffer([2.0, 2.0])
    assert z == pytest.approx(2.0 * 2 / np.sqrt(2)) and p < 0.01
    assert np.isnan(pb.stouffer([])[0])


def fake_result(week, gap, cal, tffb, gdiff=None, gz=None, gcal=None, gtffb=None):
    r = pb.WeekResult(week, calls=None, frame=pd.DataFrame(), weights=plays.Weights.prior(2026))
    r.cash = {"gap": gap, "base_cal": cal, "base_tffb": tffb, "n CORE": 2, "hit CORE": 0.5}
    if gdiff is not None:
        r.gpp = {"diff": gdiff, "z": gz, "base_cal": gcal, "base_tffb": gtffb}
    r.held_out = {"cash rho": 0.2}
    return r


def test_summarize_applies_the_ship_bar():
    strong = {w: fake_result(w, 2.0 + 0.1 * w, 0.5, 0.4, 0.05, 2.5, 0.0, 0.0) for w in (2, 4, 5, 6, 7, 8)}
    s = pb.summarize(strong)
    assert s["cash clears"] and s["gpp"]["clears"]
    weak = {
        w: fake_result(w, 0.5 + (0.3 if w % 2 else -0.3), 0.5, 0.5, 0.0, 0.1, 0.0, 0.0) for w in (2, 4, 5, 6)
    }
    s = pb.summarize(weak)
    assert not s["cash clears"] and not s["gpp"]["clears"]
    lines = pb.report(weak, s, news=None, lag_rows=pd.DataFrame(), weights=plays.Weights.prior(2026))
    assert "not proven yet" in "\n".join(lines)


def hist_week():
    feats = pd.DataFrame(
        {
            "Name": ["Ann", "Bo", "Cy", "Di"],
            "Position": ["RB", "RB", "WR", "WR"],
        }
    )
    wf = plays.WeekFeatures(feats, 1.0, 0.5, "relative", {}, plays.News(), {})
    detail = pd.DataFrame({"top1_share": [0.5, 0.0, 0.2, 0.0], "own_actual": [0.1, 0.3, 0.3, 0.05]})
    return pb.HistWeek(
        5,
        datetime(2026, 10, 11, 16, tzinfo=UTC),
        wf,
        {"Ann": 25.0, "Bo": 4.0, "Cy": 18.0, "Di": 3.0},
        None,
        None,
        detail,
        {},
        "x.csv",
    )


def calls_list():
    base = {"team": "T", "cash_score": 0.0, "gpp_score": 0.0, "cash_own": 0.1, "gpp_own": 0.1}
    return [
        base | {"name": "Ann", "position": "RB", "salary": 5000, "cash": "CORE", "gpp": "TARGET"},
        base | {"name": "Bo", "position": "RB", "salary": 5000, "cash": "AVOID", "gpp": "FADE"},
        base | {"name": "Cy", "position": "WR", "salary": 6000, "cash": "PLAY", "gpp": "LEVERAGE"},
        base | {"name": "Di", "position": "WR", "salary": 4000, "cash": "NEUTRAL", "gpp": "NEUTRAL"},
    ]


def test_outcome_rows_score_each_call_and_history_appends_idempotently(tmp_path):
    frame = pb.outcome_rows(2026, 5, "live", "2026-10-11T16:00:00+00:00", calls_list(), hist_week())
    by = frame.set_index("name")
    assert by.at["Ann", "cash_hit"] == 1.0 and by.at["Bo", "cash_hit"] == 1.0  # Bo AVOID and below median
    assert np.isnan(by.at["Di", "cash_hit"])  # NEUTRAL is not scored
    assert by.at["Ann", "gpp_hit"] == 1.0 and by.at["Cy", "gpp_hit"] == 0.0 and by.at["Bo", "gpp_hit"] == 1.0
    path = tmp_path / "calls_history.csv"
    pb.append_history(frame, path)
    pb.append_history(frame, path)  # the same week again replaces, never duplicates
    assert len(pd.read_csv(path)) == 4
    assert pb.label_lines(frame) == [
        "cash: CORE 1 of 1, PLAY 1 of 1, AVOID 1 of 1",
        "gpp: TARGET 1 of 1, LEVERAGE 0 of 1, FADE 1 of 1",
    ]
    assert list(pd.read_csv(path).columns) == pb.HISTORY_COLUMNS


def test_saved_calls_at_lock_takes_the_last_version_before_the_lock(tmp_path):
    from dfs import week_inputs

    rec = {
        "versions": [
            {"at": "2026-10-11T12:00:00+00:00", "calls": [{"name": "early"}]},
            {"at": "2026-10-11T16:30:00+00:00", "calls": [{"name": "last"}]},
            {"at": "2026-10-11T18:00:00+00:00", "calls": [{"name": "after lock"}]},
        ]
    }
    week_inputs.save_input(plays.INPUT_NAME, rec, season=2026, week=5, data_dir=tmp_path)
    at, calls = pb.saved_calls_at_lock(2026, 5, data_dir=tmp_path)
    assert calls == [{"name": "last"}] and at.startswith("2026-10-11T16:30")


def test_the_flag_locks_a_legal_core_set():
    from dfs import backtest_settings as bs
    from dfs import plays_flag

    assert plays_flag.FLAG in bs.FLAGS
    mk = lambda n, p, s, sc: plays.Call(n, p, "T", "O", s, 10.0, 0.1, 0.1, "CORE", sc, "", "NEUTRAL", 0.0, "")  # noqa: E731
    core = [
        mk("Q1", "QB", 8000, 3.0),
        mk("Q2", "QB", 7000, 2.0),
        mk("R1", "RB", 9000, 1.0),
        mk("T1", "TE", 30000, 0.5),
    ]
    locks = plays_flag.feasible_locks(core, 50_000)
    assert locks == ["Q1", "R1"]  # one QB seat; the $30K tight end leaves no room


def test_ban_only_modes_drop_the_banned_from_the_pool_and_lock_nothing(monkeypatch):
    from types import SimpleNamespace

    from dfs import plays_flag

    mk = lambda n, cash, gpp: plays.Call(n, "WR", "T", "O", 5000, 10.0, 0.1, 0.1, cash, 0.0, "", gpp, 0.0, "")  # noqa: E731
    calls = plays.Calls(
        2026,
        5,
        datetime(2026, 10, 11, tzinfo=UTC),
        [mk("A", "AVOID", "NEUTRAL"), mk("F", "PLAY", "FADE"), mk("C", "CORE", "TARGET")],
        4,
    )
    monkeypatch.setattr(plays_flag, "week_calls", lambda week: (calls, plays.Weights.prior(2026)))
    tags = {"A": "Cash", "F": "Both", "C": "GPP"}
    ctx = plays_flag.prepare(SimpleNamespace(week=5, tags=dict(tags), notes=[]), "avoid")
    assert ctx.tags == {"F": "Both", "C": "GPP"} and not hasattr(ctx, "cash_fixed")
    ctx = plays_flag.prepare(SimpleNamespace(week=5, tags=dict(tags), notes=[]), "fade")
    assert ctx.tags == {"A": "Cash", "C": "GPP"}
    assert "banned 1: F" in ctx.notes[0]
    built = plays_flag.build(ctx, "fade", lambda c: ["today's builder"])
    assert built == ["today's builder"]
    assert plays_flag.mode(True) == "lock" and plays_flag.mode("FADE") == "fade"
    with pytest.raises(ValueError):
        plays_flag.mode("core")


def test_m15_settings_files_load():
    from dfs import backtest_settings as bs
    from dfs.paths import REPO_ROOT

    folder = REPO_ROOT / "research" / "settings"
    a = bs.load(folder / "calls_avoid_cash.toml")
    b = bs.load(folder / "calls_fade_gpp.toml")
    assert a.flags["calls"] == "avoid" and a.gpp == 0
    assert b.flags["calls"] == "fade" and b.cash == 0
    assert bs.load(folder / "today_gpp.toml").cash == 0
