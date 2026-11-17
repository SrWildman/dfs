"""Edge at lock (`edge_at_lock`): the four-source consensus, his lineups against the field, the CSV."""

from __future__ import annotations

from types import SimpleNamespace

import pandas as pd
import pytest

from dfs import edge_at_lock as eal

SLOTS = ("QB", "RB", "RB", "WR", "WR", "WR", "TE", "FLEX", "DST")


def lineup(*names: str) -> tuple[tuple[str, str], ...]:
    return tuple(zip(SLOTS, names, strict=True))


MINE = lineup("Q1", "R1", "R2", "W1", "W2", "W3", "T1", "F1", "D1")
CHALK = lineup("Q2", "R1", "R2", "W1", "W2", "W4", "T1", "F2", "D1")  # shares 6 of his 9
NEAR = lineup("Q1", "R1", "R2", "W1", "W2", "W3", "T1", "F2", "D2")  # shares 7


def frame(**over) -> pd.DataFrame:
    names = ["Q1", "Q2", "R1", "R2", "W1", "W2", "W3", "W4", "T1", "F1", "F2", "D1", "D2"]
    pts = dict.fromkeys(names, 10.0) | over
    return pd.DataFrame(
        {"calpts": [pts[n] for n in names], "consensus": [pts[n] for n in names], "n_src": 4},
        index=pd.Index([n.lower() for n in names], name="key"),
    )


def contest(lineups, *, me_rows=(0,), points=None, places_paid=2, cash_line=95.0, fmt="Double Up"):
    rows = pd.DataFrame(
        {
            "entry_id": [f"e{i}" for i in range(len(lineups))],
            "entry_name": ["srwildman" if i in me_rows else f"user{i}" for i in range(len(lineups))],
            "points": points or [100.0] * len(lineups),
            "slots": lineups,
        }
    )
    standings = SimpleNamespace(lineups=rows, profile=SimpleNamespace(cash_line=cash_line))
    return SimpleNamespace(
        contest_id="c1",
        week=9,
        format=fmt,
        fee=5.0,
        entries=len(lineups),
        places_paid=places_paid,
        standings=standings,
    )


def test_his_lineup_against_the_field_on_the_consensus():
    c = contest([MINE, CHALK, CHALK, NEAR], points=[101.0, 90.0, 90.0, 99.0])
    [row] = eal.contest_rows(c, frame(Q1=20.0), "srwildman", season=2026, snapshot="snap")
    assert row["proj_consensus"] == 100.0 and row["field_mean_consensus"] == pytest.approx(95.0)
    assert (
        row["edge"] == pytest.approx(5.0) and row["pct_in_field"] == 0.5
    )  # beats the two chalk lineups only
    assert row["modal_share"] == 0.5 and row["copies_exact"] == 0
    assert row["within_2_swaps"] == 0.5  # himself and NEAR share 7+
    assert row["realized"] == 101.0 and row["line"] == 95.0 and row["margin"] == 6.0
    assert row["paid_share"] == 0.5 and row["snapshot"] == "snap" and row["n_src"] == 4


def test_two_of_his_entries_with_one_lineup_are_one_row_and_copies_count_others_only():
    c = contest([MINE, MINE, MINE, CHALK], me_rows=(0, 1))
    [row] = eal.contest_rows(c, frame(), "srwildman", season=2026)
    assert row["entries"] == 2 and row["copies_exact"] == 1 and row["lineup_id"] == "e0"


def test_a_player_the_frame_lacks_counts_zero_and_sets_n_src_to_zero():
    missing = lineup("Q1", "R1", "R2", "W1", "W2", "W3", "T1", "F1", "Nobody")
    [row] = eal.contest_rows(contest([missing, CHALK]), frame(), "srwildman", season=2026)
    assert row["proj_consensus"] == 80.0 and row["n_src"] == 0
    assert eal.contest_rows(contest([CHALK, NEAR], me_rows=()), frame(), "srwildman", season=2026) == []


def test_the_consensus_is_the_four_sources_and_a_missing_source_is_filled_from_the_scored_rows():
    base = pd.DataFrame({"Name": ["A Player", "B Player"]})
    edge = pd.DataFrame(
        {
            "Name": ["A Player", "B Player"],
            "CalPts": [12.0, 8.0],
            "ProjPts": [10.0, 6.0],
            "EspnPts": [14.0, None],
            "PropPts": [99.0, 99.0],  # props are not in the benchmark
        }
    )
    scored = pd.DataFrame({"Name": ["A Player"], "SleeperPts": [12.0], "FantasyProsPts": [8.0]})
    f = eal.frame_from(base, edge, scored)
    assert f.loc["a player", "consensus"] == 11.0 and f.loc["a player", "n_src"] == 4
    assert f.loc["b player", "consensus"] == 6.0 and f.loc["b player", "n_src"] == 1


def test_a_recompute_replaces_only_its_week(tmp_path):
    path = tmp_path / "edge_at_lock.csv"
    c = contest([MINE, CHALK])
    nine = eal.contest_rows(c, frame(), "srwildman", season=2026)
    eight = [{**r, "week": 8, "contest_key": "c0"} for r in nine]
    eal.replace_week(eight, 2026, 8, path)
    eal.replace_week(nine, 2026, 9, path)
    eal.replace_week(nine, 2026, 9, path)
    got = eal.read(path)
    assert [(r["week"], r["contest_key"]) for r in got] == [("8", "c0"), ("9", "c1")]


def test_the_summary_is_the_cash_rows_by_week():
    rows = [
        {
            "season": "2026",
            "week": "8",
            "format": "Double Up",
            "edge": "4",
            "pct_in_field": "0.7",
            "n_src": "3",
        },
        {
            "season": "2026",
            "week": "9",
            "format": "Double Up",
            "edge": "8",
            "pct_in_field": "0.9",
            "n_src": "4",
        },
        {
            "season": "2026",
            "week": "9",
            "format": "N-max GPP",
            "edge": "-9",
            "pct_in_field": "0.1",
            "n_src": "4",
        },
    ]
    s = eal.summary(rows, 2026, 9)
    assert s["this_week"]["edge"] == 8.0 and s["this_week"]["pct"] == 0.9
    assert s["season_edge"] == 6.0 and s["n_weeks"] == 2
    assert eal.summary(rows, 2026, 8)["n_weeks"] == 1  # a later week never counts
