"""The entry plan's checks (Plan K item 4): kind, entry limits, spend by tier, every contest has a lineup."""

from __future__ import annotations

from dfs import entry_plan as ep


def contest(cid, name, fee, fmt, ids) -> dict:
    return {"contest_id": cid, "name": name, "entry_fee": fee, "format": fmt, "entry_ids": ids}


BUILDS = [
    {"number": 1, "kind": "cash", "names": ["a"] * 9, "metrics": {"p_cash": 0.60, "p_gpp": 0.001}},
    {"number": 2, "kind": "cash", "names": ["b"] * 9, "metrics": {"p_cash": 0.70, "p_gpp": 0.002}},
    {"number": 3, "kind": "gpp", "names": ["c"] * 9, "metrics": {"p_cash": 0.40, "p_gpp": 0.020}},
    {"number": 4, "kind": "gpp", "names": ["d"] * 9, "metrics": {"p_cash": 0.30, "p_gpp": 0.030}},
]


def lineup(number, kind, contests) -> dict:
    return {"names": ["x"] * 9, "contests": contests, "source": {"number": number, "kind": kind, "round": 1}}


def clean_record() -> dict:
    return {
        "lineups": [
            lineup(2, "cash", [contest("1", "NFL $5 Double Up [Single Entry]", "$5", "Double Up", ["e1"])]),
            lineup(4, "gpp", [contest("2", "NFL $3 Mini [3 Entry Max]", "$3", "N-max GPP", ["e2", "e3"])]),
        ],
        "blank": [],
    }


def test_a_clean_week_passes_every_check_and_hints_the_best_build():
    plan = ep.make(budget=100.0, builds=BUILDS, record=clean_record())
    assert plan.entered and plan.problems == []
    assert all(ok for _, ok in plan.checks)
    hints = {c.name: c.hint for c in plan.contests}
    assert hints["NFL $5 Double Up [Single Entry]"].startswith("build 2:")  # best Cash %
    assert hints["NFL $3 Mini [3 Entry Max]"].startswith("build 4:")  # best Top 1%
    assert set(plan.tiers) == {"A", "B", "C"}


def test_a_cash_build_in_a_gpp_is_named():
    record = clean_record()
    record["lineups"].append(
        lineup(1, "cash", [contest("2", "NFL $3 Mini [3 Entry Max]", "$3", "N-max GPP", ["e4"])])
    )
    plan = ep.make(budget=100.0, builds=BUILDS, record=record)
    assert plan.problems == ["Cash build 1 (cash) in NFL $3 Mini [3 Entry Max], a N-max GPP"]


def test_a_lineup_of_his_own_is_not_judged_on_kind():
    record = {
        "lineups": [{"names": ["x"], "contests": [contest("9", "NFL Big GPP", "$1", "N-max GPP", ["e"])]}]
    }
    assert ep.make(budget=100.0, builds=BUILDS, record=record).problems == []


def test_single_entry_and_entry_max_are_enforced_counting_blank_entries():
    record = clean_record()
    record["lineups"].append(
        lineup(1, "cash", [contest("1", "NFL $5 Double Up [Single Entry]", "$5", "Double Up", ["e9"])])
    )
    record["blank"] = [contest("2", "NFL $3 Mini [3 Entry Max]", "$3", "N-max GPP", ["e5", "e6"])]
    plan = ep.make(budget=100.0, builds=BUILDS, record=record)
    assert "NFL $5 Double Up [Single Entry]: 2 entries, the contest allows 1" in plan.problems
    assert "NFL $3 Mini [3 Entry Max]: 4 entries, the contest allows 3" in plan.problems
    assert "NFL $3 Mini [3 Entry Max]: 2 entries with no lineup" in plan.problems


def test_an_h2h_counts_as_single_entry():
    record = {"lineups": [lineup(2, "cash", [contest("7", "NFL $2 Head-to-Head", "$2", "H2H", ["a", "b"])])]}
    plan = ep.make(budget=100.0, builds=BUILDS, record=record)
    assert plan.problems == ["NFL $2 Head-to-Head: 2 entries, the contest allows 1"]


def test_spend_over_a_tier_is_a_problem_and_no_budget_says_so():
    record = {
        "lineups": [
            lineup(
                4, "gpp", [contest("3", "NFL $25 Mini [3 Entry Max]", "$25", "N-max GPP", ["a", "b", "c"])]
            )
        ]
    }
    over = ep.make(budget=50.0, builds=BUILDS, record=record)
    assert any(p.startswith("Over: ") for p in over.problems)
    tier = over.contests[0].tier
    assert over.spent[tier] == 75.0  # the tier is contest_tiers' call; the spend is three $25 entries
    none = ep.make(budget=None, builds=BUILDS, record=record)
    assert none.tiers is None
    assert ("Spend by tier: no Weekly Budget, so no plan to check against", False) in none.checks


def test_before_entering_only_the_plan_and_where_builds_go():
    plan = ep.make(budget=100.0, builds=BUILDS, record=None)
    assert not plan.entered and plan.contests == [] and plan.checks == []
    assert [b["kind"] for b in plan.builds] == ["cash", "cash", "gpp", "gpp"]


def test_no_builds_means_no_hint():
    plan = ep.make(budget=100.0, builds=[], record=clean_record())
    assert all(c.hint == "" for c in plan.contests)
