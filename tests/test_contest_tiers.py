"""Contest tiers (`contest_tiers.py`): one real contest from `contest_log.csv` per tier and Avoid case, the
rules no real contest has hit yet built by hand, the dollars by tier and the spend against them."""

from __future__ import annotations

import pandas as pd
import pytest

from dfs import contest_tiers as ct
from dfs.contests import series as sr

# (format, fee, entrants, entry max, prize pool, name): real rows of data/contests/contest_log.csv.
REAL = {
    "h2h": (sr.H2H, 3.0, 2, 1, 5.4, "NFL $3 srwildman vs. mlmwn"),  # 2024 wk 3
    "fifty": (sr.FIFTY_FIFTY, 3.0, 10, None, 27.0, "Beginner NFL $3 50-50"),  # 2022 wk 9
    "se_double_up": (
        sr.DOUBLE_UP,
        10.0,
        2873,
        1,
        25000.0,
        "NFL GIANT $10 Double Up [Single Entry]",
    ),  # 2026 wk 4
    "three_max": (
        sr.N_MAX_GPP,
        15.0,
        784,
        3,
        10000.0,
        "NFL $10K Screen Pass [3 Entry Max] (1/3)",
    ),  # 2026 wk 4
    "se_gpp": (sr.SE_GPP, 5.0, 1783, 1, 7500.0, "NFL $7.5K Huddle [Single Entry]"),  # 2022 wk 12
    "showdown": (sr.SHOWDOWN, 12.0, 210025, None, 2142264.75, "NFL $2.12M Return of the GOAT Millionaire"),
    "big_multi": (
        sr.N_MAX_GPP,
        0.5,
        297265,
        150,
        125000.0,
        "NFL $125K mini-MAX [150 Entry Max]",
    ),  # 2022 wk 11
    "big_se_gpp": (sr.SE_GPP, 5.0, 11890, 1, 50000.0, "NFL $50K Huddle [Single Entry] (Thu)"),  # 2022 wk 12
    "big_double_up": (sr.DOUBLE_UP, 5.0, 13021, 1, 75000.0, "NFL GIANT $5 Double Up [Single Entry]"),  # 2022
    "worst_bucket": (sr.N_MAX_GPP, 8.0, 2941, 5, 20000.0, "NFL $20K Engage Eight [5 Entry Max] (Sun) (1/5)"),
}


def real(key: str, **over) -> ct.Contest:
    fmt, fee, n, emax, pool, _ = REAL[key]
    base = {"format": fmt, "fee": fee, "entrants": n, "entry_max": emax, "prize_pool": pool}
    return ct.Contest(**{**base, **over})


@pytest.mark.parametrize(
    ("key", "tier"),
    [
        ("h2h", ct.A),
        ("fifty", ct.A),
        ("se_double_up", ct.A),
        ("three_max", ct.B),
        ("se_gpp", ct.B),
        ("showdown", ct.C),
        ("big_multi", ct.C),
        ("big_se_gpp", ct.C),
        ("big_double_up", ct.C),
        ("worst_bucket", ct.AVOID),
    ],
)
def test_real_contests_land_in_their_tier(key, tier):
    verdict = ct.classify(real(key))
    assert verdict.tier == tier, verdict.line()
    assert verdict.reason  # always says why


def test_real_log_rows_classify_the_same_as_hand_built_contests():
    row = pd.Series(
        {
            "format": sr.N_MAX_GPP,
            "fee": 8.0,
            "entries": 2941,
            "entry_max": 5,
            "prize_pool": 20000.0,
            "entry": REAL["worst_bucket"][5],
            "opponent": "",
        }
    )
    assert ct.classify(ct.contest_from_log_row(row)) == ct.classify(real("worst_bucket"))
    # entry max falls back to the name's counter, and a single-entry format is 1
    row = row.drop("entry_max")
    assert ct.contest_from_log_row(row).entry_max == 5
    se = pd.Series(
        {"format": sr.SE_GPP, "fee": 5.0, "entries": 1783, "prize_pool": 7500.0, "entry": "NFL Huddle"}
    )
    assert ct.contest_from_log_row(se).entry_max == 1


def test_the_effective_rake_is_one_minus_prizes_over_fees():
    assert ct.effective_rake(real("three_max")) == pytest.approx(1 - 10000 / (784 * 15))
    assert ct.effective_rake(ct.Contest(sr.SE_GPP, 5.0, entrants=1000)) is None  # no prize pool given
    assert ct.effective_rake(ct.Contest(sr.SE_GPP, 0.0, entrants=1000, prize_pool=1000)) is None  # free
    # an overlaid contest (a guaranteed pool the entries did not fill) has a negative rake
    assert ct.effective_rake(ct.Contest(sr.SE_GPP, 5.0, entrants=500, prize_pool=3000)) < 0


# --- the rules no real contest has hit yet -----------------------


def test_rake_above_seventeen_percent_is_avoid():
    # prizes are 78% of the fees: a 22% rake
    verdict = ct.classify(ct.Contest(sr.SE_GPP, 5.0, entrants=1000, entry_max=1, prize_pool=3900.0))
    assert verdict.tier == ct.AVOID and "rake 22%" in verdict.reason and verdict.rake == pytest.approx(0.22)


def test_a_multi_entry_double_up_or_fifty_fifty_is_avoid():
    for fmt in (sr.DOUBLE_UP, sr.FIFTY_FIFTY):
        verdict = ct.classify(ct.Contest(fmt, 5.0, entrants=2000, entry_max=3))
        assert verdict.tier == ct.AVOID and "multi-entry" in verdict.reason


def test_a_gpp_with_min_cash_under_two_times_is_avoid():
    avoid = ct.classify(real("three_max", min_cash=1.5))
    assert avoid.tier == ct.AVOID and "min-cash 1.5x" in avoid.reason
    assert ct.classify(real("three_max", min_cash=2.0)).tier == ct.B  # 2x is enough
    assert "min-cash 2x" in ct.classify(real("three_max", min_cash=2.0)).reason


def test_an_h2h_regular_is_avoid_and_anyone_else_is_a_tier_a():
    avoid = ["coletrain3193"]
    contest = ct.Contest(sr.H2H, 2.0, entrants=2, entry_max=1, opponent="ColeTrain3193")
    assert ct.classify(contest, avoid_opponents=avoid).tier == ct.AVOID
    assert (
        ct.classify(ct.Contest(sr.H2H, 2.0, entrants=2, opponent="azabern2"), avoid_opponents=avoid).tier
        == ct.A
    )


def test_three_max_with_a_mid_size_field_is_b_but_twenty_max_is_not():
    """Tier B takes up to 3 entries a user; the Avoid bucket (1,000-5,000 entrants) takes 4 or more."""
    assert ct.classify(real("three_max", entrants=2000, prize_pool=None)).tier == ct.B
    assert ct.classify(real("three_max", entrants=2000, entry_max=20, prize_pool=None)).tier == ct.AVOID
    assert (
        ct.classify(real("three_max", entrants=800, entry_max=20, prize_pool=None)).tier == ct.C
    )  # small multi-entry: lottery
    assert ct.classify(real("three_max", entrants=9000, entry_max=20, prize_pool=None)).tier == ct.C


def test_tier_b_limits_field_fee_and_rake():
    assert ct.classify(real("se_gpp", entrants=2600, prize_pool=None)).tier == ct.C  # past 2,500
    assert ct.classify(real("se_gpp", fee=2.0)).tier == ct.C  # under $3
    over = ct.classify(real("se_gpp", fee=25.0, prize_pool=7500.0 * 5))
    assert over.tier == ct.C  # a $25 fee needs a rake of 13% or less
    low_rake = ct.classify(ct.Contest(sr.SE_GPP, 25.0, entrants=1000, entry_max=1, prize_pool=22000.0))  # 12%
    assert low_rake.tier == ct.B
    assert (
        ct.classify(ct.Contest(sr.SE_GPP, 5.0, entrants=1000, entry_max=1, prize_pool=4200.0)).tier == ct.C
    )  # 16.0+%


def test_cash_contests_outside_the_band_are_tier_c_not_a():
    assert ct.classify(ct.Contest(sr.H2H, 25.0, entrants=2, entry_max=1)).tier == ct.C
    assert ct.classify(ct.Contest(sr.DOUBLE_UP, 50.0, entrants=500, entry_max=1)).tier == ct.C
    assert ct.classify(ct.Contest(sr.FIFTY_FIFTY, 1.0, entrants=500, entry_max=1)).tier == ct.C


def test_unknown_formats_count_as_the_lottery_and_free_entries_cost_nothing():
    assert ct.classify(ct.Contest(sr.MULTIPLIER, 5.0)).tier == ct.C
    assert ct.classify(ct.Contest(sr.FREEROLL, 0.0)).tier == ct.A


# --- flags -------------------------------------------------------


def test_missing_inputs_are_flagged_not_guessed():
    verdict = ct.classify(ct.Contest(sr.SE_GPP, 5.0))
    assert verdict.tier == ct.B
    assert "field size unknown" in verdict.flags and "entry max unknown" in verdict.flags


def test_an_unfilled_guaranteed_contest_is_an_overlay_flag():
    verdict = ct.classify(real("three_max", entered=400))  # 400 of 784: 51% full
    assert any(f.startswith("overlay possible: 51% full") for f in verdict.flags)
    full = ct.classify(real("three_max", entered=700))
    assert not any("overlay" in f for f in full.flags)
    assert not any("overlay" in f for f in ct.classify(real("three_max")).flags)


def test_late_release_and_smaller_sibling_flags():
    verdict = ct.classify(real("three_max", late_release=True, family_sizes=(235, 784, 9803)))
    assert "late release" in verdict.flags
    assert "smaller sibling exists (235 entrants)" in verdict.flags
    assert "smaller sibling exists" not in " ".join(ct.classify(real("three_max", entrants=235)).flags)


def test_the_verdict_line_reads_as_one_sentence():
    assert ct.classify(real("three_max")).line().startswith("Tier B: 3-max GPP, $15, 784 entrants, rake 15%")


# --- the week's money ------------------------------------------------------------------------------------


def test_the_budget_is_split_sixty_thirty_ten():
    plan = ct.plan_dollars(50.0)
    assert (plan.a, plan.b, plan.c) == (30.0, 15.0, 5.0)
    assert plan.a + plan.b + plan.c == plan.budget


def test_tier_c_is_capped_at_fifteen_dollars_whatever_the_budget():
    plan = ct.plan_dollars(300.0)
    assert plan.c == 15.0 and plan.a + plan.b + plan.c == pytest.approx(300.0)
    assert plan.a == pytest.approx(2 * plan.b, abs=0.02)


def test_the_two_tier_c_lines_have_their_own_caps():
    plan = ct.plan_dollars(200.0)
    assert (plan.c_showdown, plan.c_big_multi) == (5.0, 10.0)
    small = ct.plan_dollars(20.0)  # C is $2: the sub-caps cannot exceed it
    assert small.c == 2.0 and small.c_showdown == 2.0 and small.c_big_multi == 2.0


def test_spend_by_tier_names_what_is_over_the_plan():
    plan = ct.plan_dollars(100.0)  # A 60, B 30, C 10 (Showdown 5, big multi 10)
    entries = [
        (ct.classify(real("h2h")), 3.0),
        (ct.classify(real("se_double_up")), 10.0),
        (ct.classify(real("three_max")), 15.0),
        (ct.classify(real("three_max")), 15.0),
        (ct.classify(real("three_max")), 15.0),  # B: 45 against 30
        (ct.classify(real("showdown")), 12.0),  # C: Showdown 12 against 5
        (ct.classify(real("worst_bucket")), 8.0),  # Avoid
    ]
    spend = ct.spend_by_tier(entries, plan)
    assert spend.spent == {"A": 13.0, "B": 45.0, "C": 12.0, "Avoid": 8.0}
    assert spend.entries == {"A": 2, "B": 3, "C": 1, "Avoid": 1}
    assert spend.total == 78.0
    assert "Tier B $45 (plan $30)" in spend.over
    assert "Tier C $12 (plan $10)" in spend.over
    assert "Showdown $12 (cap $5)" in spend.over
    assert "Avoid $8 (the plan is $0)" in spend.over
    assert not any(o.startswith("Tier A") for o in spend.over)
    assert spend.line() == "A $13 of $60, B $45 of $30 (over), C $12 of $10 (over); Avoid $8"


# --- contests as an entries file describes them ------------------


def test_entry_max_is_read_from_the_name():
    assert ct.entry_max_from_name("NFL $10K Screen Pass [3 Entry Max] (2/3)") == 3
    assert ct.entry_max_from_name("NFL $5 Huddle [Single Entry]") == 1
    assert ct.entry_max_from_name("NFL $4K Safety (1/6)") == 6
    assert ct.entry_max_from_name("NFL $50K Pylon") is None


def _typed_log() -> pd.DataFrame:
    rows = []
    for ck, name, fee, n, pool in [
        ("c1", "NFL $10K Screen Pass [3 Entry Max] (1/3)", 15.0, 784, 10000.0),
        ("c2", "NFL $10K Screen Pass [3 Entry Max] (1/3)", 15.0, 800, 10000.0),
        ("c3", "NFL $7.5K Huddle [Single Entry]", 5.0, 1783, 7500.0),
    ]:
        info = sr.classify("Classic", name, fee=fee, contest_entries=n, places_paid=None)
        rows.append(
            {
                "contest_key": ck,
                "fee": fee,
                "entries": n,
                "prize_pool": pool,
                "series": info.series,
                "format": info.format,
            }
        )
    return pd.DataFrame(rows)


def test_an_entries_file_is_tiered_from_the_series_history():
    history = ct.history_by_series(_typed_log())
    entered = ct.classify_entered(
        [
            ("NFL $10K Screen Pass [3 Entry Max] (2/3)", sr.N_MAX_GPP, 15.0),
            ("NFL $3 srwildman vs. coletrain3193", sr.H2H, 3.0),
            ("NFL $2 Single Entry Double Up", sr.DOUBLE_UP, 2.0),  # no history: the field is unknown
            ("NFL Showdown $5 Tuesday Special (BUF @ LAR)", sr.SHOWDOWN, 5.0),
        ],
        history=history,
        avoid_opponents=["coletrain3193"],
    )
    tiers = [(e.fee, e.verdict.tier, e.sized_from_history) for e in entered]
    assert tiers == [(15.0, "B", True), (3.0, "Avoid", False), (2.0, "A", False), (5.0, "C", False)]
    assert "field size unknown" in entered[2].verdict.flags
    assert "regular" in entered[1].verdict.reason


# --- the check form -----------------------------------------------------------------------------------------


def test_the_form_reads_five_quick_fields():
    c = ct.contest_from_form(
        {"format": "GPP", "fee": "$15", "entrants": "1,234 / 2,450", "entry_max": "3", "prize": "30k 2x"}
    )
    assert (c.format, c.fee, c.entrants, c.entered, c.entry_max) == (sr.N_MAX_GPP, 15.0, 2450, 1234, 3)
    assert (c.prize_pool, c.min_cash) == (30000.0, 2.0)


def test_a_gpp_is_single_entry_when_the_entry_max_is_one():
    assert ct.contest_from_form({"format": "gpp", "fee": "5", "entry_max": "1"}).format == sr.SE_GPP
    assert ct.contest_from_form({"format": "gpp", "fee": "5"}).format == sr.N_MAX_GPP  # unknown: N-max
    assert ct.contest_from_form({"format": "H2H", "fee": "2"}).entry_max == 1


def test_the_form_takes_a_bare_maximum_a_pool_alone_or_a_min_cash_alone():
    assert ct.contest_from_form({"format": "50/50", "fee": "3", "entrants": "5000"}).entered is None
    assert ct.contest_from_form({"format": "50/50", "fee": "3", "prize": "$12,500"}).prize_pool == 12500.0
    only = ct.contest_from_form({"format": "gpp", "fee": "3", "prize": "2x"})
    assert only.min_cash == 2.0 and only.prize_pool is None


@pytest.mark.parametrize(
    ("form", "message"),
    [
        ({"format": "lottery", "fee": "3"}, "format"),
        ({"format": "H2H"}, "fee: required"),
        ({"format": "H2H", "fee": "abc"}, "fee"),
        ({"format": "gpp", "fee": "3", "entrants": "900 / 800"}, "larger than the maximum"),
        ({"format": "gpp", "fee": "3", "entrants": "1 / 2 / 3"}, "entrants"),
        ({"format": "gpp", "fee": "-3"}, "negative"),
    ],
)
def test_the_form_names_the_field_it_cannot_read(form, message):
    with pytest.raises(ct.FormError, match=message):
        ct.contest_from_form(form)


def test_the_form_gives_the_verdict_the_page_shows():
    contest = ct.contest_from_form(
        {"format": "gpp", "fee": "15", "entrants": "400 / 784", "entry_max": "3", "prize": "10000 2x"}
    )
    payload = ct.verdict_payload(contest, ct.classify(contest))
    assert payload["tier"] == "B" and payload["rake"] == pytest.approx(1 - 10000 / (784 * 15))
    assert any(f.startswith("overlay possible: 51% full") for f in payload["flags"])
    assert payload["line"].startswith("Tier B:")
