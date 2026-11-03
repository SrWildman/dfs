"""Plan D item 3: the contest plan (`dfs contests plan`) and the week-close check against it."""

from __future__ import annotations

import pandas as pd
import pytest

from dfs import contest_plan as cp
from dfs.config import ContestPlanConfig
from dfs.contests import series as s

CFG = ContestPlanConfig()


def _log(rows) -> pd.DataFrame:
    """rows: (week, format, entrants, fee, winnings, prize_pool, contest_key)."""
    df = pd.DataFrame(
        rows, columns=["week", "format", "entries", "fee", "winnings", "prize_pool", "contest_key"]
    )
    df["season"] = pd.array([2026] * len(df), dtype="Int64")
    df["week"] = df["week"].astype("Int64")
    df["entries"] = df["entries"].astype("Int64")
    df["place"] = pd.array([pd.NA] * len(df), dtype="Int64")
    df["places_paid"] = pd.array([pd.NA] * len(df), dtype="Int64")
    return df


@pytest.mark.parametrize(
    ("fmt", "entrants", "bucket"),
    [
        (s.SHOWDOWN, 50, cp.SHOWDOWN),
        (s.N_MAX_GPP, 2000, cp.BIG_NMAX),
        (s.N_MAX_GPP, 1999, cp.OUTSIDE),
        (s.DOUBLE_UP, 50_000, cp.PREFERRED),
        (s.H2H, 2, cp.PREFERRED),
        (s.FIFTY_FIFTY, 100, cp.PREFERRED),
        (s.SE_GPP, 999, cp.PREFERRED),
        (s.SE_GPP, 1000, cp.OUTSIDE),
        (s.SE_GPP, None, cp.OUTSIDE),
        (s.MULTIPLIER, 100, cp.OUTSIDE),
    ],
)
def test_plan_bucket(fmt, entrants, bucket):
    assert cp.plan_bucket(fmt, entrants, CFG) == bucket


def test_the_rest_of_the_budget_goes_to_the_preferred_formats():
    lines = {line.bucket: line for line in cp.plan_lines(CFG, 250.0)}
    assert (lines[cp.SHOWDOWN].cap, lines[cp.BIG_NMAX].cap, lines[cp.PREFERRED].cap) == (5.0, 10.0, 235.0)
    assert lines[cp.OUTSIDE].cap == 0.0
    assert cp.plan_lines(CFG, None)[2].cap is None


def test_check_week_names_every_line_over_its_cap():
    log = _log(
        [
            (8, s.SHOWDOWN, 100, 20.0, 0.0, 90.0, "a"),
            (8, s.SHOWDOWN, 100, 11.0, 0.0, 90.0, "b"),
            (8, s.N_MAX_GPP, 5000, 5.0, 0.0, 9000.0, "c"),
            (8, s.DOUBLE_UP, 100, 25.0, 45.0, 2200.0, "d"),
            (8, s.MULTIPLIER, 100, 46.0, 0.0, 4000.0, "e"),
            (7, s.SHOWDOWN, 100, 99.0, 0.0, 90.0, "f"),  # another week
        ]
    )
    check = cp.check_week(log, 2026, 8, CFG, 100.0)
    assert check.total == 107.0
    assert check.spent[cp.SHOWDOWN] == 31.0 and check.spent[cp.BIG_NMAX] == 5.0
    assert check.line() == (
        "Contest plan, Week 8: spent $107.00 vs plan $100.00; over in: Showdown $31.00 (cap $5.00), "
        "Outside the plan $46.00 (cap $0.00), the total $107.00 (budget $100.00)"
    )
    ok = cp.check_week(log[log["contest_key"] == "d"], 2026, 8, CFG, 100.0)
    assert ok.line() == "Contest plan, Week 8: spent $25.00 vs plan $100.00; within the plan"
    assert "no budget set" in cp.check_week(log, 2026, 8, CFG, None).line()


def test_rake_is_what_the_site_keeps_per_contest():
    log = _log(
        [
            (8, s.DOUBLE_UP, 100, 25.0, 45.0, 2200.0, "d"),  # 12% rake
            (8, s.DOUBLE_UP, 100, 25.0, 0.0, 2200.0, "d"),  # same contest: counted once
            (8, s.DOUBLE_UP, 10, 10.0, 0.0, 90.0, "x"),  # 10%
            (8, s.SE_GPP, 500, 0.0, 0.0, 100.0, "free"),  # free: no rake, not an entry
        ]
    )
    rows = {r.label: r for r in cp.rake_by_format(log)}
    assert rows[s.DOUBLE_UP].contests == 2 and rows[s.DOUBLE_UP].rake == pytest.approx(0.11)
    assert rows[s.DOUBLE_UP].entries == 3
    assert s.SE_GPP not in rows
    bands = {r.label: r for r in cp.rake_by_field(log, frozenset({s.DOUBLE_UP}))}
    assert bands["up to 200"].contests == 2 and bands["5,001+"].contests == 0


def test_h2h_avoid_list():
    opponents = pd.DataFrame(
        {
            "opponent": ["winless3", "winless2", "poor", "fine", "few"],
            "meetings": [3, 2, 7, 7, 4],
            "wins": [0, 0, 1, 4, 1],
            "losses": [3, 2, 6, 3, 3],
            "ties": [0] * 5,
            "net": [-5.0, -3.0, -7.4, 0.6, -2.0],
        }
    )
    assert [o.name for o in cp.h2h_avoid(opponents)] == ["winless3", "poor"]
    assert cp.h2h_avoid(opponents)[1].record == "1-6"


def test_report_lines_hold_every_section():
    log = _log([(8, s.DOUBLE_UP, 100, 25.0, 45.0, 2200.0, "d"), (8, s.SHOWDOWN, 100, 10.0, 0.0, 90.0, "a")])
    log["entry_key"] = ["1", "2"]
    text = "\n".join(
        cp.report_lines(log, None, season=2026, week=8, cfg=CFG, budget=250.0, budget_source="x")
    )
    for heading in (
        "THIS WEEK'S PLAN",
        "LAST WEEK AGAINST THE PLAN (Week 8",
        "ROI BY FORMAT",
        "EFFECTIVE RAKE",
    ):
        assert heading in text
    assert "over in: Showdown $10.00 (cap $5.00)" in text
    assert "no h2h_opponents.csv yet" in text


def test_the_week_page_payload_has_the_pages_shape():
    log = _log([(8, s.SHOWDOWN, 100, 31.0, 0.0, 90.0, "a"), (8, s.DOUBLE_UP, 100, 25.0, 45.0, 2200.0, "d")])
    opp = pd.DataFrame(
        {"opponent": ["x"], "meetings": [3], "wins": [0], "losses": [3], "ties": [0], "net": [-3.0]}
    )
    p = cp.week_page_payload(log, opp, season=2026, week=8, cfg=CFG, budget=100.0)
    assert p["caps"][cp.SHOWDOWN] == 5.0 and p["spent"][cp.SHOWDOWN] == 31.0
    assert p["budget"] == 100.0 and p["total_spent"] == 56.0
    assert p["over"] == ["Showdown $31.00 (cap $5.00)"] and p["line"].startswith("Contest plan, Week 8")
    assert {r["format"] for r in p["roi"]} == {s.SHOWDOWN, s.DOUBLE_UP} and "rake" in p["roi"][0]
    assert p["h2h_avoid"] == [{"opponent": "x", "record": "0-3"}]


def test_save_input_writes_under_the_test_data_dir():
    from dfs import paths, week_inputs

    assert week_inputs.save_input("contest_plan", {"line": "x"}, season=2026, week=9)
    path = paths.DATA_DIR / "week" / "inputs" / "2026-wk09-contest_plan.json"
    assert path.exists() and week_inputs.load_input("contest_plan", 2026, 9)["line"] == "x"
