"""The exact search (`build_exact`): the solver switch, the case split against exhaustive enumeration and
against the old search run to its end, and the HiGHS checker against it on a fixture (the test that keeps a
later change from breaking exactness unnoticed)."""

import sys
from itertools import combinations
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

import test_build_milp as small  # noqa: E402
import test_lineup_suggest as syn  # noqa: E402

from dfs import backtest_settings as bs  # noqa: E402
from dfs import build_exact as be  # noqa: E402
from dfs import cli, lineup_iterate  # noqa: E402
from dfs import lineup_shapes as sh  # noqa: E402
from dfs import lineup_suggest as ls  # noqa: E402
from dfs import suggest_state as st  # noqa: E402
from dfs.config import BuildsConfig  # noqa: E402
from dfs.late_swap_search import LineupState, OpenSlot  # noqa: E402
from dfs.models import ROSTER_SLOTS  # noqa: E402
from dfs.suggest_state import StackRules  # noqa: E402

K = 6
SHAPES = (sh.STACK, sh.NAKED, sh.RB_STACK, sh.GAME, sh.RB_DST)

pool = small.pool
universe = small.universe
cap = small.cap


def exact(pool, cap, *, fixed, k=K, **kwargs):
    state = small.state_for(cap, fixed)
    names = {p.name for p in fixed}
    found, complete = be.exact_top_lineups(state, [p for p in pool if p.name not in names], k=k, **kwargs)
    assert complete
    return [[*state.fixed.values(), *a.values()] for a in found]


def shape_test(shape, leaf_ok=None):
    rule = sh.rule_for(shape)
    return lambda lu: rule(lu) and (leaf_ok is None or leaf_ok(lu))


def by_qb(lineups):
    """(projection sum, salary) of each lineup, best first, per quarterback: equal lists, equal up to ties."""
    groups = {}
    for lu in lineups:
        qb = next(p.name for p in lu if p.position == "QB")
        groups.setdefault(qb, []).append((round(sum(p.value for p in lu), 6), sum(p.salary for p in lu)))
    return {qb: sorted(v, key=lambda t: (-t[0], t[1])) for qb, v in groups.items()}


# --- the switch -------------------------------------------------------------------------------------------


def test_the_flag_is_off_by_default_and_a_block_puts_it_back():
    assert be.solver() == be.DEFAULT_SOLVER == "search"
    with be.use("exact"):
        assert be.solver() == "exact"
        with be.use("milp"):
            assert be.solver() == "milp"
        assert be.solver() == "exact"
    assert be.solver() == "search"
    with pytest.raises(ValueError, match="solver must be one of"):
        with be.use("simplex"):
            pass
    assert be.solver() == "search"  # a bad name changes nothing


def test_with_the_flag_off_the_search_is_todays_call_for_call(pool, cap, monkeypatch):
    seen = []
    monkeypatch.setattr(be, "budgeted_top_lineups", lambda *a, **k: seen.append((a, k)) or ([], False))
    state = small.state_for(cap)
    out = be.top_lineups(state, pool, k=3, leaf_filter=None, time_limit=1.5, shape=sh.STACK, qb=pool[0])
    assert out == ([], False)
    assert seen == [((state, pool), {"k": 3, "leaf_filter": None, "feasible": None, "time_limit": 1.5})]


def test_the_config_key_and_the_backtest_key_name_the_solver():
    assert BuildsConfig().solver == "search"
    assert BuildsConfig(solver="exact").solver == "exact"
    with pytest.raises(ValueError):
        BuildsConfig(solver="fast")
    assert cli._builds_solver(type("Cfg", (), {})()) == "search"  # a config without the section
    assert cli._builds_solver(type("Cfg", (), {"builds": BuildsConfig(solver="exact")})()) == "exact"
    today, exact_ = bs.Settings(), bs.from_dict({"builds": {"solver": "exact"}})
    assert today.solver == "search" and exact_.solver == "exact"
    assert today.fingerprint() != exact_.fingerprint()  # saved builds are never shared between solvers
    assert "exact search (no time box)" in exact_.describe()[0] and "s a week" in today.describe()[0]
    with pytest.raises(bs.SettingsError, match="solver must be one of"):
        bs.from_dict({"builds": {"solver": "fast"}})


# --- the cases cover every lineup once ------------------------------------------------------------------


def test_the_bring_back_cases_hold_every_choice_exactly_once():
    backs = list("abcde")
    for need in range(4):
        cases = list(be.back_cases(backs, need))
        for size in range(len(backs) + 1):
            for chosen in combinations(backs, size):
                homes = [
                    1
                    for held, free in cases
                    if set(held) <= set(chosen) and set(chosen) - set(held) <= set(free)
                ]
                assert len(homes) == (1 if size >= need else 0)


@pytest.mark.parametrize("shape", SHAPES)
def test_the_exact_search_is_the_exhaustive_answer_for_each_shape(pool, universe, cap, shape):
    for n in range(4):
        qb = small.qb_of(pool, n)
        for feasible in (None, sh.feasible_for(shape, qb)):  # the cases alone, then with the search's prune
            lineups = exact(
                pool, cap, fixed=[qb], shape=shape, qb=qb, leaf_filter=shape_test(shape), feasible=feasible
            )
            assert small.scores(lineups) == small.best(universe, cap, K, sh.rule_for(shape), holding=[qb])
            assert len({frozenset(p.name for p in lu) for lu in lineups}) == len(lineups)  # none twice


def test_the_contrarian_cut_still_prunes_inside_the_cases(pool, universe, cap):
    qb = small.qb_of(pool)
    own = {p.name: 0.02 + 0.03 * (i % 7) for i, p in enumerate(pool)}
    stacks = sorted(sh.stack_ownership(lu, own) for lu in universe if lu[0] is qb and sh.is_stacked(lu))
    cut = stacks[len(stacks) // 4]
    feasible = sh.feasible_for(sh.CONTRARIAN, qb, own=own, max_stack_own=cut, pool=pool)
    lineups = exact(
        pool, cap, fixed=[qb], shape=sh.CONTRARIAN, qb=qb, leaf_filter=sh.is_stacked, feasible=feasible
    )

    def rule(lu):
        return sh.is_stacked(lu) and sh.stack_ownership(lu, own) <= cut + 1e-12

    assert lineups and small.scores(lineups) == small.best(universe, cap, K, rule, holding=[qb])


def test_locked_players_are_part_of_every_case(pool, universe, cap):
    qb = small.qb_of(pool)
    mate = next(p for p in pool if p.team == qb.team and p.position == "WR")
    back = next(p for p in pool if p.team == qb.opp and p.position == "TE")
    for shape in (sh.STACK, sh.GAME, sh.RB_STACK):
        lineups = exact(pool, cap, fixed=[qb, mate, back], shape=shape, qb=qb, leaf_filter=shape_test(shape))
        assert small.scores(lineups) == small.best(
            universe, cap, K, sh.rule_for(shape), holding=[qb, mate, back]
        )
    # a held bring-back leaves no naked stack at all
    assert exact(pool, cap, fixed=[qb, back], shape=sh.NAKED, qb=qb, leaf_filter=shape_test(sh.NAKED)) == []


@pytest.mark.parametrize(
    "make",
    [
        lambda team: StackRules(no_bring_back={team}),
        lambda team: StackRules(only={team: {"QB+WR", "QB+RB+WR"}}),
        lambda team: StackRules(only={team: {"QB+RB+WR"}}, no_bring_back={team}),
    ],
)
def test_the_rules_boxes_stack_rules_choose_the_cases(pool, universe, cap, make):
    qb = small.qb_of(pool)
    stacks = make(qb.team)
    rules = be.LeafRules(stacks=stacks)
    for shape in (sh.RB_STACK, sh.RB_DST):
        test = shape_test(shape, stacks.ok)
        lineups = exact(pool, cap, fixed=[qb], shape=shape, qb=qb, leaf_filter=test, rules=rules)
        assert small.scores(lineups) == small.best(universe, cap, K, test, holding=[qb])
    banned = be.LeafRules(stacks=StackRules(no_stack={qb.team}))
    assert exact(pool, cap, fixed=[qb], shape=sh.STACK, qb=qb, leaf_filter=sh.is_stacked, rules=banned) == []
    if qb.team in stacks.no_bring_back:  # a stack WITH a bring-back cannot exist for that team
        test = shape_test(sh.STACK, stacks.ok)
        assert exact(pool, cap, fixed=[qb], shape=sh.STACK, qb=qb, leaf_filter=test, rules=rules) == []


def test_a_few_gpp_marked_players_become_cases_and_many_are_left_to_the_test(pool, universe, cap):
    qb = small.qb_of(pool)
    ranked = sorted((lu for lu in universe if sum(p.salary for p in lu) <= cap), key=lambda lu: -be.score(lu))
    in_the_best = {p.name for lu in ranked[:40] for p in lu}
    few = {p.name: "GPP" for p in [p for p in pool if p.name not in in_the_best][:3]}
    for tags in (few, {**dict.fromkeys((p.name for p in pool), "Cash"), **few}):
        leaf = ls.not_all_cash(tags)
        assert be.rules_of(leaf).need_one_of == frozenset(few)
        test = shape_test(sh.STACK, leaf)
        lineups = exact(
            pool, cap, fixed=[qb], shape=sh.STACK, qb=qb, leaf_filter=test, rules=be.rules_of(leaf)
        )
        assert all(any(p.name in few for p in lu) for lu in lineups)
        assert small.scores(lineups) == small.best(universe, cap, K, test, holding=[qb])
    everyone = ls.not_all_cash(dict.fromkeys((p.name for p in pool), "Both"))  # more than SCARCE marked
    assert len(be.rules_of(everyone).need_one_of) > be.SCARCE
    test = shape_test(sh.STACK, everyone)
    lineups = exact(
        pool, cap, fixed=[qb], shape=sh.STACK, qb=qb, leaf_filter=test, rules=be.rules_of(everyone)
    )
    assert small.scores(lineups) == small.best(universe, cap, K, test, holding=[qb])


def test_max_from_one_game_stops_a_branch_early_and_changes_nothing_else(pool, universe, cap):
    qb = small.qb_of(pool)

    def within(lu):
        return be.most_from_one_game(lu) <= 5

    test = shape_test(sh.STACK, within)
    lineups = exact(
        pool, cap, fixed=[qb], shape=sh.STACK, qb=qb, leaf_filter=test, rules=be.LeafRules(max_per_game=5)
    )
    assert lineups and small.scores(lineups) == small.best(universe, cap, K, test, holding=[qb])


def test_the_rerun_s_whole_lineup_test_says_what_it_checks():
    rules = st.Rules(max_per_game=5, stacks=StackRules(no_stack={"T00"}))
    tags = {"A": "GPP", "B": "Cash", "C": "Both"}
    gpp = be.rules_of(lineup_iterate._leaf(rules, tags, "gpp"))
    assert gpp == be.LeafRules(need_one_of=frozenset({"A", "C"}), max_per_game=5, stacks=rules.stacks)
    cash = be.rules_of(lineup_iterate._leaf(rules, tags, "cash"))
    assert cash.need_one_of is None and cash.max_per_game == 5
    assert lineup_iterate._leaf(st.Rules(), tags, "cash") is None  # nothing to test, nothing to describe


def test_the_cash_search_is_the_old_search_without_a_budget(pool, cap):
    state = small.state_for(cap)
    with be.use("exact"):
        new, complete = be.top_lineups(state, pool, k=40, time_limit=0.0)  # the budget is ignored
    old, _ = be.budgeted_top_lineups(state, pool, k=40)
    assert complete and new == old  # the same lineups in the same order


def test_the_circuit_breaker_reports_a_cut_search_instead_of_hanging(pool, cap):
    qb = small.qb_of(pool)
    state = small.state_for(cap, [qb])
    rest = [p for p in pool if p.name != qb.name]
    _, complete = be.exact_top_lineups(
        state, rest, k=K, shape=sh.STACK, qb=qb, leaf_filter=shape_test(sh.STACK), safety=0.0
    )
    assert (
        complete
    )  # this slate is too small to reach a check (512 nodes): the breaker only bites a long search
    wide = ls.pool_for(syn.slate(), syn.tags_for(syn.slate()), ls.GPP_TAGS, now=syn.NOW, metric="CalPts")
    qb = small.qb_of(wide)
    state = LineupState(
        cap=syn.CAP, fixed={0: qb}, open=[OpenSlot(i, s, None) for i, s in enumerate(ROSTER_SLOTS) if i]
    )
    rest = [p for p in wide if p.position != "QB"]
    found, complete = be.exact_top_lineups(
        state, rest, k=40, shape=sh.STACK, qb=qb, leaf_filter=shape_test(sh.STACK), safety=0.0
    )
    assert not complete


# --- against the old search run to its end, and against HiGHS ---------------------------------------------


@pytest.fixture(scope="module")
def wide_pool():
    frame = syn.slate()
    return ls.pool_for(frame, syn.tags_for(frame), ls.GPP_TAGS, now=syn.NOW, metric="CalPts")


@pytest.mark.parametrize("shape", sh.SHAPES)
def test_every_shape_matches_the_old_search_run_to_its_end_up_to_ties(wide_pool, shape):
    """Six teams, every quarterback, the real per-quarterback counts: the exact search and the old search with
    no budget hold the same (projection, salary) lists, quarterback by quarterback."""
    own = {p.name: 0.01 + 0.02 * (i % 9) for i, p in enumerate(wide_pool)}
    kwargs = {"own": own, "max_stack_own": 0.35} if shape == sh.CONTRARIAN else {}
    per_qb = dict((s, n) for s, n, _ in ls.GPP_PLAN)[shape]
    old, done = sh.shape_lineups(wide_pool, shape, cap=syn.CAP, per_qb=per_qb, **kwargs)
    with be.use("exact"):
        new, complete = sh.shape_lineups(wide_pool, shape, cap=syn.CAP, per_qb=per_qb, **kwargs)
    assert done and complete and old
    assert by_qb(new) == by_qb(old)
    rule = sh.rule_for(shape)
    assert all(rule(lu) for lu in new)


@pytest.mark.parametrize("shape", [None, *sh.SHAPES])
def test_the_milp_checker_agrees_with_the_exact_search(pool, cap, shape):
    """The guard on exactness: HiGHS, which shares no search code with the branch-and-bound, finds the same
    (projection, salary) lists on the small slate, for the cash search (`shape` None) and every GPP shape."""
    own = {p.name: 0.02 + 0.03 * (i % 7) for i, p in enumerate(pool)}
    leaf = ls.not_all_cash({p.name: ("GPP" if i % 3 == 0 else "Cash") for i, p in enumerate(pool)})

    def run():
        if shape is None:
            return ls.cash_lineups(pool, cap=cap, k=12)
        kwargs = {"own": own, "max_stack_own": 0.3} if shape == sh.CONTRARIAN else {}
        return sh.shape_lineups(pool, shape, cap=cap, per_qb=4, leaf_ok=leaf, **kwargs)

    with be.use("exact"):
        exact_, done_exact = run()
    with be.use("milp"):
        milp, done_milp = run()
    assert done_exact and done_milp and exact_
    assert by_qb(milp) == by_qb(exact_)


# --- a whole build ----------------------------------------------------------------------------------------


def test_a_build_with_the_flag_on_finishes_whatever_the_budget():
    """A budget that cuts today's search short does not touch the exact one: no "cut short" note, every build
    legal, and the same builds on a second run."""
    edge = syn.slate(teams=4)
    short = syn.run(edge, time_limit=0.01, n_sims=300)
    assert not short.complete and any("time limit cut the search short" in n for n in short.notes)
    with be.use("exact"):
        first = syn.run(edge, time_limit=0.01, n_sims=300)
        second = syn.run(edge, time_limit=0.01, n_sims=300)
    assert first.complete
    assert not any("time limit" in n or "time box" in n for n in first.notes)
    assert [b.kind for b in first.builds].count("cash") == ls.DEFAULT_CASH_BUILDS
    assert any(b.kind == "gpp" for b in first.builds)
    for build in first.builds:
        syn.assert_legal(build)
    assert [[p.name for p in b.players] for b in first.builds] == [
        [p.name for p in b.players] for b in second.builds
    ]


def test_the_exact_build_s_candidates_lead_with_the_finished_old_search_s(pool, cap):
    """The builds come from the simulator's ranking of the candidates, so compare the candidates: every
    quarterback's best candidates are the same as an old search given a budget large enough to finish. (The
    whole merged set can differ by a tie: a lineup one shape keeps at its boundary and another does not.)"""
    kwargs = {"cap": cap, "deadline": float("inf"), "leaf_ok": None, "shapes": sh.SHAPES, "shares": None}
    old, done, _ = ls.gpp_candidates(pool, notes=[], **kwargs)
    notes = []
    with be.use("exact"):
        new, complete, _ = ls.gpp_candidates(pool, notes=notes, **kwargs)
    assert done and complete and not notes
    assert {qb: v[:5] for qb, v in by_qb(new).items()} == {qb: v[:5] for qb, v in by_qb(old).items()}
