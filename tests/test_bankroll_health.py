"""Bankroll health (Plan K item 1): the Season tab's curve, the Bankroll block and the stake guide."""

from __future__ import annotations

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


def test_your_rules_win_over_the_weekly_budget_and_split_by_tier():
    guide = bh.stake_guide(StakesConfig(max_pct_bankroll_per_week=10, max_entry_fee_pct=2), 200.0, 80.0)
    assert guide.source == "your rules"
    assert guide.week == 20.0 and guide.entry == 4.0
    assert abs(sum(guide.tiers.values()) - 20.0) < 0.05
    assert bh.over_sentence(80.0, 200.0, guide) == (
        "Your Weekly Budget $80.00 is above what a $200.00 bankroll supports under your 10%-a-week rule "
        "($20.00): 4 times the guide."
    )


def test_without_rules_the_guide_is_the_weekly_budget_and_never_over():
    out = bh.payload(bh.season_rows(GRID), bh.block_values(BLOCK), StakesConfig())
    g = out["guide"]
    assert g["source"] == "Weekly Budget"
    assert g["week"] == 50.0
    assert g["pct_week"] == round(50 / 175 * 100, 1)
    assert abs(sum(g["tiers"].values()) - 50.0) < 0.05
    assert out["over"] == "" and out["budget_left"] == 38.0


def test_the_proposal_is_never_used_for_the_guide(monkeypatch):
    from dfs import stakes

    def boom(*a, **k):
        raise AssertionError("stakes.propose must not be called")

    monkeypatch.setattr(stakes, "propose", boom)
    assert bh.stake_guide(StakesConfig(), 175.0, 50.0).source == "Weekly Budget"


def test_no_budget_and_no_rules_means_no_guide():
    guide = bh.stake_guide(StakesConfig(), 175.0, None)
    assert guide.source == "none" and guide.week is None
    assert bh.over_sentence(None, 175.0, guide) == ""
    within = bh.stake_guide(StakesConfig(max_pct_bankroll_per_week=50), 200.0, 50.0)
    assert bh.over_sentence(50.0, 200.0, within) == ""


def test_an_empty_sheet_reads_as_nothing():
    assert bh.season_rows([]) == []
    assert bh.season_rows([["Not the header"]]) == []
    assert bh.curve([]) == []
    assert bh.block_values([]) == {}
