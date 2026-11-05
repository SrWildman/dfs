"""Bankroll health (Plan K item 1): the Season tab's curve, the Bankroll block and the stake guide."""

from __future__ import annotations

import pandas as pd

from dfs import bankroll_health as bh
from dfs.config import StakesConfig

# The Season tab's header in a shuffled order: columns are found by header, never by position.
HEADER = ["Week", "GPP Net", "Cash Net", "Ending Bankroll", "Betting Net", "Other Net", "Cash Risked",
          "GPP Risked", "Betting Risked", "Other Risked", "Total Net"]  # fmt: skip
GRID = [
    HEADER,
    [1, "$10.00", "($5.00)", "$205.00", 0, 0, 20, 30, 0, 0, 5],
    [2, -40, 10, 175, "$5.00", "-$5.00", 20, 40, 10, 5, -30],
    [3, "", "", "", "", "", "", "", "", "", ""],  # a week not played yet
]
BLOCK = [
    ["Starting Bankroll", "$175.00", "", "Weekly Budget", "$50.00"],
    ["Weekly Cost", "$12.00", "", "% Budget Remaining", "80%"],
]


def test_season_rows_are_found_by_header_and_empty_weeks_dropped():
    rows = bh.season_rows(GRID)
    assert [r["week"] for r in rows] == [1, 2]
    assert rows[0]["cash_net"] == -5.0 and rows[0]["gpp_net"] == 10.0
    assert rows[1]["other_net"] == -5.0


def test_the_curve_is_cumulative_per_series_with_spend_as_a_share():
    curve = bh.curve(bh.season_rows(GRID))
    assert curve[0]["cum"] == {"dfs": 5.0, "betting": 0.0, "other": 0.0, "total": 5.0}
    assert curve[1]["cum"] == {"dfs": -25.0, "betting": 5.0, "other": -5.0, "total": -25.0}
    assert curve[0]["spend_share"] is None  # no bankroll known before Week 1
    assert curve[1]["start"] == 205.0
    assert abs(curve[1]["spend_share"] - 75 / 205) < 1e-9


def test_block_values_are_read_by_label():
    block = bh.block_values(BLOCK)
    assert block == {
        "Starting Bankroll": 175.0,
        "Weekly Budget": 50.0,
        "Weekly Cost": 12.0,
        "% Budget Remaining": 0.8,
    }


def test_your_rules_win_over_the_proposal_and_split_by_tier():
    guide = bh.stake_guide(StakesConfig(max_pct_bankroll_per_week=10, max_entry_fee_pct=2), None, 200.0)
    assert guide.source == "your rules"
    assert guide.week == 20.0 and guide.entry == 4.0
    assert abs(sum(guide.tiers.values()) - 20.0) < 0.05


def test_without_rules_the_guide_is_proposed_and_labelled_so(monkeypatch):
    from dfs import stakes

    class Proposal:
        max_pct_bankroll_per_week = 5.0
        max_entry_fee_pct = 1.0
        lines = ["arithmetic"]

    monkeypatch.setattr(stakes, "propose", lambda typed, bankroll: Proposal())
    out = bh.payload(bh.season_rows(GRID), bh.block_values(BLOCK), StakesConfig(), pd.DataFrame())
    assert out["guide"]["source"] == "proposed"
    assert out["guide"]["week"] == 8.75
    assert out["budget_left"] == 38.0
    assert out["over"].startswith("Your Weekly Budget $50.00 is above what a $175.00 bankroll supports")
    assert "proposed 5%-a-week rule" in out["over"] and "6 times the guide" in out["over"]


def test_no_over_sentence_within_the_guide_or_when_unknown():
    guide = bh.stake_guide(StakesConfig(max_pct_bankroll_per_week=50), None, 200.0)
    assert bh.over_sentence(50.0, 200.0, guide) == ""
    assert bh.over_sentence(None, 200.0, guide) == ""
    nothing = bh.stake_guide(StakesConfig(max_pct_bankroll_per_week=5), None, None)
    assert nothing.week is None and bh.over_sentence(50.0, None, nothing) == ""


def test_an_empty_sheet_reads_as_nothing():
    assert bh.season_rows([]) == []
    assert bh.season_rows([["Not the header"]]) == []
    assert bh.curve([]) == []
    assert bh.block_values([]) == {}
