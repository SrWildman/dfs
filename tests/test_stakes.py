"""Stake sizing math on synthetic logs: stats by format, the shrunk default split and its floor, each rule's
warning, unset rules, the proposal, and an empty history. No sheet and no real contest data."""

import pandas as pd
import pytest

from dfs import stakes
from dfs.config import StakesConfig
from dfs.stakes import CASH, GPP, Stat


def entries(*rows):
    """A `contests.history.typed`-shaped frame: (season, week, format, fee, winnings, place, places_paid)."""
    frame = pd.DataFrame(
        rows, columns=["season", "week", "format", "fee", "winnings", "place", "places_paid"]
    )
    for col in ("season", "week", "place", "places_paid"):
        frame[col] = frame[col].astype("Int64")
    return frame


def stat(entries_, roi):
    return Stat(entries_, 100.0, 100.0 * (roi or 0), roi, 0.5, 10.0, 8)


def by_label(rows):
    return {r.label: r for r in rows}


# ---- his numbers by format -----------------------------------------------------------------------


def test_roi_cash_rate_volatility_and_entries_by_format_this_season_and_all_seasons():
    typed = entries(
        (2025, 1, "Double Up", 10.0, 20.0, 3, 5),  # cashed, +10
        (2025, 2, "Double Up", 10.0, 0.0, 9, 5),  # missed, -10
        (2026, 1, "Double Up", 10.0, 20.0, 1, 5),  # cashed, +10
        (2026, 2, "Double Up", 10.0, 20.0, 2, 5),  # cashed, +10
        (2026, 2, "SE GPP", 20.0, 0.0, 50, 10),
    )
    rows = by_label(stakes.build_stats(typed, season=2026))
    du = rows["Double Up"]
    assert (du.season.entries, du.overall.entries) == (2, 4)
    assert du.season.roi == pytest.approx(20 / 20)
    assert du.overall.roi == pytest.approx(20 / 40)
    assert du.season.cash_rate == 1.0
    assert du.overall.cash_rate == pytest.approx(0.75)
    # weekly nets over all seasons: 2025w1 +10, 2025w2 -10, 2026w1 +10, 2026w2 +10 -> sample SD
    assert du.overall.weekly_sd == pytest.approx(pd.Series([10, -10, 10, 10]).std(ddof=1))
    assert du.season.weekly_sd == pytest.approx(0.0)  # two weeks of +10 and +10
    assert rows["SE GPP"].overall.roi == -1.0


def test_totals_add_up_the_formats_into_cash_and_gpp():
    typed = entries(
        (2026, 1, "H2H", 5.0, 10.0, 1, 1),
        (2026, 1, "Double Up", 10.0, 0.0, 9, 5),
        (2026, 1, "SE GPP", 20.0, 40.0, 1, 3),
        (2026, 2, "Showdown", 10.0, 0.0, 9, 3),
    )
    rows = by_label(stakes.build_stats(typed, 2026))
    assert rows["Cash"].overall.entries == 2
    assert rows["Cash"].overall.net == pytest.approx(5.0 - 10.0)
    assert rows["GPP"].overall.entries == 2
    assert rows["GPP"].overall.net == pytest.approx(20.0 - 10.0)
    assert rows["All formats"].overall.entries == 4
    assert [r.label for r in stakes.build_stats(typed, 2026)][-3:] == ["Cash", "GPP", "All formats"]


def test_missing_data_is_none_never_a_guess():
    typed = entries(
        (2026, 1, "Freeroll", 0.0, 5.0, 1, 10),  # no fee: ROI undefined
        (2026, 1, "SE GPP", 10.0, 0.0, 5, 2),  # one week: no standard deviation
    )
    rows = by_label(stakes.build_stats(typed, 2026))
    assert rows["Freeroll"].overall.roi is None
    assert rows["SE GPP"].overall.weekly_sd is None
    assert rows["SE GPP"].overall.roi == -1.0
    assert rows["H2H" if "H2H" in rows else "Cash"].overall.entries == 0
    assert rows["Cash"].overall.roi is None and rows["Cash"].overall.cash_rate is None


def test_entries_outside_a_regular_week_count_in_roi_but_not_in_a_weekly_sd():
    typed = entries(
        (2026, 1, "SE GPP", 10.0, 20.0, 1, 3),
        (2026, 2, "SE GPP", 10.0, 0.0, 9, 3),
    )
    typed = pd.concat(
        [typed, entries((2026, None, "SE GPP", 10.0, 100.0, 1, 3))], ignore_index=True
    )  # a playoff entry: no NFL week
    s = by_label(stakes.build_stats(typed, 2026))["SE GPP"].overall
    assert s.entries == 3 and s.weeks == 2
    assert s.roi == pytest.approx((120 - 30) / 30)
    assert s.weekly_sd == pytest.approx(pd.Series([10.0, -10.0]).std(ddof=1))


def test_an_empty_history_builds_totals_of_nothing():
    rows = stakes.build_stats(entries(), 2026)
    assert [r.label for r in rows] == ["Cash", "GPP", "All formats"]
    assert all(r.overall.entries == 0 and r.overall.roi is None for r in rows)
    assert stakes.propose(entries(), 100.0).max_pct_bankroll_per_week is None


# ---- the default split ---------------------------------------------------------------------------


def test_a_bucket_over_100_entries_is_split_purely_by_roi():
    d = {x.bucket: x for x in stakes.default_split({CASH: stat(200, 0.30), GPP: stat(150, 0.10)})}
    assert d[CASH].weight == d[GPP].weight == 1.0
    assert d[CASH].share == pytest.approx(0.30 / 0.40)
    assert d[GPP].share == pytest.approx(0.10 / 0.40)


def test_a_thin_bucket_is_shrunk_toward_even_by_how_far_it_is_from_100_entries():
    d = {x.bucket: x for x in stakes.default_split({CASH: stat(50, 0.30), GPP: stat(400, 0.10)})}
    assert d[CASH].weight == pytest.approx(0.5)
    # raw = w * (roi / total) + (1 - w) * 1/2, then renormalised because the weights differ
    cash_raw = 0.5 * 0.75 + 0.5 * 0.5
    gpp_raw = 1.0 * 0.25 + 0.0 * 0.5
    assert d[CASH].share == pytest.approx(cash_raw / (cash_raw + gpp_raw))
    assert d[GPP].share == pytest.approx(gpp_raw / (cash_raw + gpp_raw))
    assert d[CASH].share + d[GPP].share == pytest.approx(1.0)


def test_two_buckets_under_100_entries_with_equal_weights_move_toward_even():
    d = {x.bucket: x for x in stakes.default_split({CASH: stat(10, 0.50), GPP: stat(10, 0.0)})}
    assert d[CASH].share == pytest.approx(0.1 * 1.0 + 0.9 * 0.5)
    assert d[GPP].share == pytest.approx(0.1 * 0.0 + 0.9 * 0.5)


def test_the_floor_a_negative_all_seasons_roi_gets_nothing_whatever_the_sample():
    d = {x.bucket: x for x in stakes.default_split({CASH: stat(300, 0.05), GPP: stat(2000, -0.001)})}
    assert not d[GPP].eligible and d[GPP].share == 0.0
    assert "floor" in d[GPP].reason
    assert d[CASH].share == pytest.approx(1.0)


def test_exactly_zero_roi_clears_the_floor_and_a_zero_total_falls_back_to_even():
    d = {x.bucket: x for x in stakes.default_split({CASH: stat(200, 0.0), GPP: stat(200, 0.0)})}
    assert d[CASH].eligible and d[GPP].eligible
    assert d[CASH].share == d[GPP].share == pytest.approx(0.5)


def test_a_bucket_with_no_history_is_left_out_rather_than_guessed():
    none = Stat(0, 0.0, 0.0, None, None, None, 0)
    d = {x.bucket: x for x in stakes.default_split({CASH: stat(300, 0.1), GPP: none})}
    assert not d[GPP].eligible and d[GPP].reason == "no history"
    assert d[CASH].share == 1.0


# ---- this week's plan ----------------------------------------------------------------------------

BOTH_POSITIVE = {CASH: stat(300, 0.30), GPP: stat(300, 0.10)}


def plan(cfg=None, **kw):
    args = {"budget": 200.0, "bankroll": 1000.0, "stats": BOTH_POSITIVE} | kw
    return stakes.make_plan(cfg or StakesConfig(betting_pct=0), **args)


def test_rules_unset_the_plan_is_the_budget_split_by_roi_with_no_limits_and_no_warnings():
    p = plan()
    assert p.spend == 200.0
    assert p.week_cap is None and p.entry_cap is None
    assert p.amounts[CASH] == pytest.approx(150.0) and p.amounts[GPP] == pytest.approx(50.0)
    assert p.warnings == []
    assert (
        stakes.plan_line(p)
        == "Your rules give: cash $150.00, GPP $50.00, betting $0.00; largest entry no limit set"
    )
    assert stakes.unset_rules(StakesConfig(betting_pct=0)) == [
        "max_pct_bankroll_per_week",
        "max_entry_fee_pct",
    ]


def test_max_entry_fee_pct_gives_the_largest_entry():
    p = plan(StakesConfig(betting_pct=0, max_entry_fee_pct=2.5))
    assert p.entry_cap == pytest.approx(25.0)
    assert stakes.plan_line(p).endswith("largest entry $25.00")
    assert p.arithmetic["entry_cap"] == "2.5% x $1,000.00"


def test_a_weekly_cap_below_the_budget_lowers_the_spend_and_warns():
    p = plan(StakesConfig(betting_pct=0, max_pct_bankroll_per_week=10))
    assert p.week_cap == pytest.approx(100.0) and p.spend == pytest.approx(100.0)
    assert p.amounts[CASH] == pytest.approx(75.0)
    assert any("above your max_pct_bankroll_per_week" in w and "$100.00" in w for w in p.warnings)


def test_a_weekly_cap_above_the_budget_changes_nothing():
    p = plan(StakesConfig(betting_pct=0, max_pct_bankroll_per_week=50))
    assert p.spend == 200.0 and p.warnings == []


def test_this_weeks_ledgers_over_the_plan_or_over_the_entry_rule_warn():
    p = plan(StakesConfig(betting_pct=0, max_entry_fee_pct=1), spent_so_far=260.0, largest_entry_so_far=12.0)
    text = " ".join(p.warnings)
    assert "already hold $260.00" in text and "above $200.00" in text
    assert "largest entry fee $12.00 is above your max_entry_fee_pct rule" in text
    ok = plan(StakesConfig(betting_pct=0, max_entry_fee_pct=1), spent_so_far=200.0, largest_entry_so_far=10.0)
    assert ok.warnings == []


def test_a_split_override_replaces_the_default_and_warns_only_when_it_funds_a_floored_bucket():
    cfg = StakesConfig(betting_pct=0, split={"cash": 40, "gpp": 60})
    p = plan(cfg)
    assert p.split_source == "your split"
    assert p.amounts[CASH] == pytest.approx(80.0) and p.amounts[GPP] == pytest.approx(120.0)
    assert p.warnings == []
    losing = {CASH: stat(300, 0.30), GPP: stat(300, -0.20)}
    w = plan(cfg, stats=losing).warnings
    assert len(w) == 1 and "puts 60% on GPP" in w[0] and "-20.0%" in w[0]
    zero = plan(StakesConfig(betting_pct=0, split={"cash": 100, "gpp": 0}), stats=losing)
    assert zero.warnings == []


def test_no_bucket_above_the_floor_allocates_nothing_and_says_how_to_override():
    losing = {CASH: stat(300, -0.1), GPP: stat(300, -0.2)}
    p = plan(stats=losing)
    assert p.amounts == {CASH: 0.0, GPP: 0.0, stakes.BETTING: 0.0}
    assert any("set [stakes] split" in w for w in p.warnings)


def test_a_rule_set_with_no_bankroll_is_reported_not_guessed():
    p = plan(StakesConfig(betting_pct=0, max_pct_bankroll_per_week=5, max_entry_fee_pct=1), bankroll=None)
    assert p.week_cap is None and p.entry_cap is None and p.spend == 200.0
    assert sum("Starting Bankroll" in w for w in p.warnings) == 2


def test_a_blank_budget_is_reported_and_leaves_the_amounts_blank():
    p = plan(budget=None)
    assert p.spend is None and p.amounts == {CASH: None, GPP: None, stakes.BETTING: None}
    assert any("Weekly Budget cell is blank" in w for w in p.warnings)
    assert stakes.plan_line(p) == "Your rules give: cash –, GPP –, betting –; largest entry no limit set"


def test_a_blank_budget_with_a_weekly_rule_plans_the_rule():
    p = plan(StakesConfig(betting_pct=0, max_pct_bankroll_per_week=10), budget=None)
    assert p.spend == pytest.approx(100.0)


# ---- the proposal --------------------------------------------------------------------------------


def weekly_log(nets, fee=100.0):
    return entries(*[(2025, w, "SE GPP", fee, fee + net, 1, 3) for w, net in enumerate(nets, start=1)])


def test_the_proposal_is_the_formula_applied_to_his_weekly_volatility():
    nets = [50, -50, 50, -50, 50, -50, 50, -50]
    p = stakes.propose(weekly_log(nets), bankroll=2000.0)
    sd = pd.Series(nets).std(ddof=1)
    k = sd / 100.0
    raw = stakes.BAD_WEEK_LOSS_PCT / (stakes.BAD_WEEK_Z * k)
    assert p.weekly_sd == pytest.approx(sd) and p.mean_spend == pytest.approx(100.0)
    assert p.sd_per_dollar == pytest.approx(k)
    assert raw == pytest.approx(9.355, abs=0.001)  # 10% / (2 x 0.5345)
    assert p.max_pct_bankroll_per_week == 9.0  # rounded DOWN to the 0.5 step
    assert p.max_entry_fee_pct == 1.8  # 9% / 5 entries
    text = "\n".join(p.lines)
    assert "k = " in text and "rounded down" in text and "not financial advice" in text
    assert "$2,000.00" in text  # the dollar translation uses the bankroll


def test_a_calmer_history_proposes_a_bigger_weekly_share_capped_at_100():
    calm = stakes.propose(weekly_log([1, -1, 1, -1, 1, -1, 1, -1]), None)
    wild = stakes.propose(weekly_log([90, -90, 90, -90, 90, -90, 90, -90]), None)
    assert calm.max_pct_bankroll_per_week > wild.max_pct_bankroll_per_week
    assert calm.max_pct_bankroll_per_week <= 100
    assert "Bankroll not read" in "\n".join(calm.lines)


def test_too_few_weeks_gives_no_numbers_and_says_so():
    p = stakes.propose(weekly_log([10, -10, 5]), 500.0)
    assert p.max_pct_bankroll_per_week is None and p.max_entry_fee_pct is None
    assert "Not enough history" in p.lines[0]


def test_betting_is_its_own_budget_line_and_the_contest_split_shares_the_rest():
    p = plan(StakesConfig())  # betting_pct defaults to 20
    assert p.shares[stakes.BETTING] == pytest.approx(0.20)
    assert p.shares[stakes.CASH] + p.shares[stakes.GPP] == pytest.approx(0.80)
    assert p.amounts[stakes.BETTING] == pytest.approx(0.20 * p.spend)
    assert sum(p.amounts.values()) == pytest.approx(p.spend)  # the three lines add up to the budget
    assert "betting " in stakes.plan_line(p)


def test_a_betting_share_of_zero_leaves_the_contest_split_whole_and_an_override_is_scaled_too():
    p = plan(StakesConfig(betting_pct=0, split={"cash": 40, "gpp": 60}))
    assert p.amounts[stakes.BETTING] == 0 and p.shares[stakes.CASH] == pytest.approx(0.4)
    q = plan(StakesConfig(betting_pct=50, split={"cash": 40, "gpp": 60}))
    assert q.shares[stakes.CASH] == pytest.approx(0.2) and q.shares[stakes.GPP] == pytest.approx(0.3)


def test_fifty_fifty_counts_with_double_up_in_the_cash_bucket_and_h2h_does_not():
    import pandas as pd

    typed = pd.DataFrame(
        {
            "format": ["Double Up", "50/50", "H2H", "SE GPP", "N-max GPP"],
            "fee": [10.0, 10.0, 10.0, 10.0, 10.0],
            "winnings": [0.0, 20.0, 50.0, 0.0, 100.0],
            "season": [2026] * 5,
            "week": [1.0] * 5,
        }
    )
    s = stakes.bucket_stats(typed)
    assert s[CASH].entries == 2 and s[CASH].net == 10.0  # Double Up + 50/50, not H2H
    assert s[GPP].entries == 1  # SE GPP only, not the N-max GPP
