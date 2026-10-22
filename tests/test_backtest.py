"""`dfs backtest`: scoring, entering builds, the hooks and the report, on small synthetic contests."""

from collections import Counter
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from dfs import backtest as bt
from dfs import backtest_report as rep
from dfs import backtest_settings as bs
from dfs.sim import payouts


def build(kind, number, p_cash=0.5, p_gpp=0.1, e_dups=None, proj=100.0, names=None):
    return {
        "kind": kind,
        "number": number,
        "names": names or [f"P{number}{i}" for i in range(9)],
        "stack": "",
        "proj": proj,
        "p_cash": p_cash,
        "p_gpp": p_gpp,
        "own_sum": None,
        "e_dups": e_dups,
    }


def contest(points, *, fmt="gpp", entries=None, k=1, places=3, pool=300.0, fee=10.0, copies=None, week=4):
    """An `Entered` over a synthetic field with the given final scores."""
    from dfs.sim import contest_sim as csim

    entries = entries or len(points)
    series = "NFL GIANT $10 Double Up" if fmt == "cash" else "NFL $10 Hundo [Single Entry]"
    kind = payouts.ContestKind(
        series,
        "Double Up" if fmt == "cash" else "SE GPP",
        entries,
        places,
        pool,
        fee,
        None if fmt == "cash" else 1.0,
        1,
    )
    pts = np.array(points, float)
    c = SimpleNamespace(
        week=week, contest_id="1", series=series, entries=entries, fmt=fmt, points=np.sort(pts)[::-1]
    )
    return bt.Entered(
        c=c,
        kind=kind,
        mine=[((), 0.0)] * k,
        actual=[],
        fee=fee,
        top1_line=float(np.sort(pts)[::-1][0]),
        cum=csim._cumulative(payouts.payout_table(kind)),  # noqa: SLF001
        copies=Counter(copies or {}),
        sorted_pts=np.sort(pts),
    )


# --- which builds go in ---------------------------------------------------------------------------


def test_entry_order_is_the_tools_own_ranking_per_kind():
    builds = [
        build("cash", 1, p_cash=0.5),
        build("cash", 2, p_cash=0.7),
        build("gpp", 3, p_gpp=0.02, e_dups=0.0),
        build("gpp", 4, p_gpp=0.05, e_dups=9.0),
        build("gpp", 5, p_gpp=0.04, e_dups=0.0),
    ]
    assert [b["number"] for b in bt.entry_order(builds, "cash", "top1")] == [2, 1]
    assert [b["number"] for b in bt.entry_order(builds, "gpp", "top1")] == [4, 5, 3]
    # `share` divides by 1 + the duplicates expected: the chalky 4 drops to last
    assert [b["number"] for b in bt.entry_order(builds, "gpp", "share")] == [5, 3, 4]


def test_default_assign_takes_k_of_the_contests_kind():
    builds = [
        build("cash", 1),
        build("gpp", 2, p_gpp=0.1),
        build("gpp", 3, p_gpp=0.2),
        build("gpp", 4, p_gpp=0.3),
    ]
    e = contest([100, 90, 80, 70], k=2)
    assert [b["number"] for b in bt.default_assign(builds, e)] == [4, 3]
    assert [b["number"] for b in bt.default_assign(builds, contest([1, 2], fmt="cash"))] == [1]


# --- paying a lineup in a real field --------------------------------------------------------------


def test_a_lineup_is_paid_by_its_place_among_the_real_scores():
    e = contest([100, 90, 80, 70, 60, 50, 40, 30, 20, 10], places=3, pool=300.0)
    table = payouts.payout_table(e.kind)
    assert e.pay(["a"], 105) == pytest.approx(table[0])  # above every real score: first
    assert e.pay(["a"], 95) == pytest.approx(table[1])  # only the 100 is above
    assert e.pay(["a"], 85) == pytest.approx(table[2])
    assert e.pay(["a"], 75) == 0.0  # fourth: past the paid places


def test_a_flat_double_up_pays_the_same_to_every_cashing_place():
    e = contest(list(range(100, 0, -1)), fmt="cash", places=50, pool=10 * 50 * 2.0, fee=10.0)
    assert e.pay(["a"], 100.5) == pytest.approx(20.0)
    assert e.pay(["a"], 51.5) == pytest.approx(20.0)  # 49 real scores above: place 50, the last paid
    assert e.pay(["a"], 50.5) == 0.0


def test_real_copies_share_the_place_they_tie_into_and_your_own_entry_is_not_counted_twice():
    e = contest([100, 90, 80, 70, 60], places=3, pool=300.0, copies={("a", "b"): 2})
    alone = e.pay(["a", "c"], 85)
    shared = e.pay(["b", "a"], 85)  # key is sorted
    assert shared < alone  # two other entries hold the identical lineup: the prize is split three ways
    assert e.pay(["a", "b"], 85, own=True) > shared  # one of the two copies was Sam's own entry


# --- the pieces of a week -------------------------------------------------------------------------


def test_lineup_points_prefers_the_standings_then_the_fallback_and_is_nan_when_unknown():
    pts = {"josh allen": 30.0}
    assert bt.lineup_points(["Josh Allen"], pts, {}) == 30.0
    assert bt.lineup_points(["Josh Allen", "Kyren Williams"], pts, {"Kyren Williams": 12.5}) == 42.5
    assert np.isnan(bt.lineup_points(["Josh Allen", "Nobody"], pts, {}))


def test_run_builder_applies_prepare_hooks_in_order_and_nests_the_build_hooks(monkeypatch):
    seen = []
    monkeypatch.setattr(bt, "ensure_sim", lambda ctx: ctx)

    def inner(ctx):
        seen.append(("builder", ctx.notes[:]))
        return ["base"]

    monkeypatch.setattr(bt, "default_builder", inner)
    monkeypatch.setattr(bs, "FLAGS", {})

    def prep(tag):
        def f(ctx, value):
            ctx.notes.append(f"{tag}:{value}")
            return ctx

        return f

    def wrap(tag):
        def f(ctx, value, nxt):
            return [tag, *nxt(ctx)]

        return f

    bs.register(bs.Flag("one", "d", prepare=prep("one"), build=wrap("one")))
    bs.register(bs.Flag("two", "d", prepare=prep("two"), build=wrap("two")))
    s = bs.Settings(flags={"two": 2, "one": 1, "off": False})
    ctx = bt.bs.BuildContext(
        week=1, edge=pd.DataFrame(), tags={}, now=None, cap=50000, metric="CalPts", settings=s
    )
    assert bt.run_builder(ctx) == ["one", "two", "base"]  # the first registered is the outermost wrapper
    assert seen == [("builder", ["one:1", "two:2"])]


def test_choose_hooks_run_in_order_and_only_ask_for_the_contests_when_one_is_on(monkeypatch):
    monkeypatch.setattr(bs, "FLAGS", {})
    asked = []

    def contests():
        asked.append(1)
        return [bs.WeekContest("1", "S", "gpp", None, 2), bs.WeekContest("2", "T", "gpp", None, 1)]

    ctx = bs.BuildContext(
        week=4, edge=pd.DataFrame(), tags={}, now=None, cap=50000, metric="CalPts", settings=bs.Settings()
    )
    assert bt.run_choose(ctx, [], contests) == {} and asked == []
    bs.register(bs.Flag("a", "d", choose=lambda c, b, cs, v: {x.contest_id: [["A"]] * x.entries for x in cs}))
    bs.register(bs.Flag("b", "d", choose=lambda c, b, cs, v: {"2": [["B"]]}))
    ctx.settings = bs.Settings(flags={"a": True, "b": True})
    assert bt.run_choose(ctx, [], contests) == {"1": [["A"], ["A"]], "2": [["B"]]}  # the later one wins "2"
    assert asked == [1]


def test_a_chosen_contest_enters_the_hooks_lineups_and_the_others_keep_todays(monkeypatch):
    names = {n: [f"{n}{i}" for i in range(9)] for n in ("X", "P1", "P2")}
    builds = [build("gpp", 1, p_gpp=0.2, names=names["P1"]), build("gpp", 2, p_gpp=0.1, names=names["P2"])]
    a, b = contest([100, 50], k=1), contest([100, 50], k=1)
    b.c.contest_id = "2"
    pts = {"X": 9.0, "P1": 8.0, "P2": 7.0}
    wi = SimpleNamespace(actual={n: pts[k] for k, v in names.items() for n in v})
    monkeypatch.setattr(bt, "week_points", lambda entered, week: {})
    run = {"builds": builds, "chosen": {"1": [names["X"]]}}
    out, dropped = bt.score_week(4, run, [a, b], wi, bs.Settings())
    entry = out[out["scope"] == "entry"].set_index("contest")["points"]
    assert entry["1"] == pytest.approx(81.0) and entry["2"] == pytest.approx(72.0)  # X; today's best build
    assert sorted(out[out["scope"] == "any"]["points"]) == [63.0, 63.0, 72.0, 72.0]  # every build, not X
    assert dropped == []


def test_the_cache_key_is_the_settings_and_the_code(tmp_path):
    s = bs.Settings()
    a = bt.job_path(s, "abc", 6, tmp_path)
    assert a == bt.job_path(bs.Settings(name="x"), "abc", 6, tmp_path)
    assert a != bt.job_path(s, "abd", 6, tmp_path)
    assert a.name == "w06.json"


def test_pool_for_week_uses_the_saved_pool_only_when_it_is_big_enough(tmp_path, monkeypatch):
    from dfs import pool_store

    names = [f"Player {i}" for i in range(40)]
    wi = SimpleNamespace(week=7, edge=pd.DataFrame({"Name": names}), tags={"Constructed": "Both"})
    lock = bt.week_frames.lock_utc(bt.SEASON, 7)
    monkeypatch.setattr(pool_store, "POOL_DIR", tmp_path)
    assert bt.pool_for_week(wi, bs.Settings())[1].startswith("constructed")  # nothing saved
    pool_store.save(
        bt.SEASON, 7, {n: "Both" for n in names[:5]}, source="sync", at=lock - pd.Timedelta(hours=3)
    )
    assert bt.pool_for_week(wi, bs.Settings())[1].startswith("constructed")  # five players is not a pool
    pool_store.save(
        bt.SEASON,
        7,
        {n: "Both" for n in names[:30]} | {"Not on slate": "Both"},
        source="suggest",
        at=lock - pd.Timedelta(hours=2),
    )
    tags, source = bt.pool_for_week(wi, bs.Settings())
    assert len(tags) == 30 and "saved" in source and "suggest" in source
    assert bt.pool_for_week(wi, bs.Settings(pool="constructed"))[0] == {"Constructed": "Both"}


def test_never_use_takes_the_player_out_of_the_pool_and_adjust_changes_the_projection():
    edge = pd.DataFrame(
        {
            "Name": ["Josh Allen", "Bijan Robinson", "Puka Nacua"],
            "Team": ["BUF", "ATL", "LAR"],
            "Position": ["QB", "RB", "WR"],
            "CalPts": [20.0, 18.0, 16.0],
        }
    )
    wi = SimpleNamespace(week=6, edge=edge)
    s = bs.Settings(rules={"Never use": "Bijan Robinson", "Adjust": "Puka Nacua +2"})
    out, tags, notes = bt.apply_rules(wi, s, {"Josh Allen": "Both", "Bijan Robinson": "Both"})
    assert tags == {"Josh Allen": "Both"}
    assert out.loc[out["Name"] == "Puka Nacua", "CalPts"].iloc[0] == pytest.approx(18.0)
    assert any("Nacua" in n for n in notes)
    assert bt.apply_rules(wi, bs.Settings(), {"A": "Both"}) == (edge, {"A": "Both"}, [])


def test_code_stamp_names_a_commit():
    assert isinstance(bt.code_stamp(), str) and bt.code_stamp()


# --- the numbers ----------------------------------------------------------------------------------


def rows(side, scope, spec):
    """`spec`: (week, contest, fmt, fee, paid, points, top1) per entry."""
    return pd.DataFrame(
        [
            {
                "side": side,
                "week": w,
                "contest": c,
                "series": "S",
                "fmt": f,
                "fee": fee,
                "entries": 100,
                "slot": 1,
                "scope": scope,
                "points": pts,
                "paid": paid,
                "cashed": float(paid > 0),
                "top1": float(t),
            }
            for w, c, f, fee, paid, pts, t in spec
        ],
        columns=bt.ROW_COLUMNS,
    )


def test_stats_roi_cash_rate_and_the_roi_without_the_best_ticket():
    d = rows(
        "backtest",
        "entry",
        [
            (4, "a", "gpp", 10, 0, 90, 0),
            (4, "b", "gpp", 10, 0, 91, 0),
            (5, "c", "gpp", 10, 500, 200, 1),
            (5, "d", "gpp", 10, 20, 120, 0),
        ],
    )
    s = rep.stats(d)
    assert s["entries"] == 4 and s["cost"] == 40 and s["paid"] == 520
    assert s["roi"] == pytest.approx(12.0)
    assert s["cash"] == 0.5 and s["top1"] == 0.25
    assert s["ex_top"] == pytest.approx(20 / 30 - 1)  # the $500 ticket removed


def test_paired_vs_is_by_contest_and_by_week_with_weeks_won():
    a = rows(
        "A",
        "entry",
        [
            (4, "a", "gpp", 10, 30, 1, 0),
            (4, "b", "gpp", 10, 0, 1, 0),
            (5, "c", "gpp", 10, 0, 1, 0),
            (6, "d", "gpp", 10, 40, 1, 0),
        ],
    )
    b = rows(
        "B",
        "entry",
        [
            (4, "a", "gpp", 10, 10, 1, 0),
            (4, "b", "gpp", 10, 10, 1, 0),
            (5, "c", "gpp", 10, 10, 1, 0),
            (6, "d", "gpp", 10, 0, 1, 0),
        ],
    )
    t = bt.contest_table(pd.concat([a, b]))
    p = bt.paired_vs(t, "A", "B")
    assert p["contests"] == 4 and p["weeks"] == 3
    assert p["weeks_won"] == 2  # weeks 4 (+10 vs +10 over two contests: A 30 vs B 20) and 6
    assert p["diff_week"] == pytest.approx(np.mean([(30 - 20) / 20, -10 / 10, 40 / 10]))


def test_the_verdict_needs_four_weeks_two_standard_errors_and_most_weeks():
    ok = {"diff_week": 0.5, "se_week": 0.1, "weeks": 4, "weeks_won": 4}
    assert rep.verdict(ok, 4) == "clears the bar"
    assert rep.verdict(ok, 3).startswith("not proven yet: 3 week")
    assert "within 2 SE" in rep.verdict({**ok, "se_week": 0.4}, 4)
    assert "wins 1 of 4" in rep.verdict({**ok, "weeks_won": 1}, 4)
    assert "best ticket" in rep.verdict(ok, 4, ex_top=-0.1)


def result(name="today", extra=None):
    spec = [
        (4, "a", "cash", 10, 20, 150, 0),
        (4, "b", "gpp", 10, 0, 100, 0),
        (5, "c", "gpp", 10, 90, 180, 1),
    ]
    base = pd.concat(
        [
            rows("backtest", "entry", spec),
            rows("backtest", "any", spec + [(5, "c", "gpp", 10, 0, 90, 0)]),
            rows(
                "sam",
                "entry",
                [
                    (4, "a", "cash", 10, 0, 100, 0),
                    (4, "b", "gpp", 10, 0, 90, 0),
                    (5, "c", "gpp", 10, 10, 120, 0),
                ],
            ),
            rows(
                "sam_actual",
                "entry",
                [
                    (4, "a", "cash", 10, 0, 100, 0),
                    (4, "b", "gpp", 10, 0, 90, 0),
                    (5, "c", "gpp", 10, 0, 120, 0),
                ],
            ),
        ]
    )
    if extra is not None:
        base = pd.concat([base, extra])
    return bt.Result(
        weeks=[4, 5],
        settings=bs.Settings(name=name),
        runs={
            4: {
                "pool": "constructed (57 players)",
                "notes": ["The game stack search ran out of its time box"],
                "builds": [build("cash", 1)],
                "seconds": 100.0,
            },
            5: {
                "pool": "constructed (57 players)",
                "notes": [],
                "builds": [build("gpp", 2)],
                "seconds": 99.0,
            },
        },
        rows=base,
        contests=bt.contest_table(base),
        dropped=[],
        coverage={"fees": 100.0, "covered": 85.0, "share": 0.85},
    )


def test_the_summary_is_ten_lines_or_fewer_and_says_what_matters():
    text = "\n".join(lines := rep.summary_lines(result()))
    assert len(lines) <= 10
    assert "weeks 4, 5" in text and "85%" in text
    assert "constructed in every week" in text
    assert "Builds, as entered (today)" in text and "Sam, as DraftKings paid" in text
    assert "time box" in text and "week(s) 4" in text
    assert "By week, builds / Sam as paid: W4" in text and "Builds minus Sam" in text


def test_the_report_has_every_section_and_the_comparison_when_given():
    a, b = result("new"), result("old")
    cmp = rep.compare_lines(a, b)
    text = rep.render([a, b], cmp)
    for needle in (
        "# Flashback backtest: new vs old",
        "Ship bar",
        "### Contest by contest",
        "### The builds",
        "## Comparison",
        "How to read this",
    ):
        assert needle in text
    assert "not proven yet" in "\n".join(cmp)  # two weeks cannot clear a four-week bar
    assert "A wins 0 of 2 weeks" in "\n".join(cmp)  # identical results


def test_write_report_names_the_file_by_settings(tmp_path):
    path = rep.write_report([result("My Setting!")], directory=tmp_path)
    assert path.parent == tmp_path and path.name.endswith("my-setting.md")
    assert "Flashback" in path.read_text()


def test_the_refresh_hook_returns_the_contract(monkeypatch):
    out = rep.refresh_from(result())
    assert set(out) == {"headline", "metrics", "ship_bar"}
    assert out["ship_bar"] == rep.SHIP_BAR and out["headline"][0].startswith("Flashback, weeks 4, 5")
    assert "builds any roi" in out["metrics"]
