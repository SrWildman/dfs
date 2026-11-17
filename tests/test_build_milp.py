"""The HiGHS checker (`build_milp`): every rule of the builder's search as a linear constraint, and the
no-good cuts, each against an exhaustive enumeration of a small slate.

The slate is four teams (two games): small enough to list every legal lineup once, with a cap that binds."""

import sys
from itertools import combinations
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

import test_lineup_suggest as syn  # noqa: E402

from dfs import build_milp as bm  # noqa: E402
from dfs import lineup_shapes as sh  # noqa: E402
from dfs import lineup_suggest as ls  # noqa: E402
from dfs.build_exact import LeafRules, score  # noqa: E402
from dfs.late_swap_search import LineupState, OpenSlot, SwapPlayer, conflicts, top_lineups  # noqa: E402
from dfs.models import ROSTER_SLOTS  # noqa: E402
from dfs.suggest_state import StackRules  # noqa: E402

PER_TEAM = {"QB": 1, "RB": 2, "WR": 2, "TE": 1, "DST": 1}
FLEX_MIXES = ((2, 3, 2), (3, 3, 1), (2, 4, 1))  # (RB, WR, TE) counts a lineup can hold
K = 6


def small_pool(seed=3):
    frame = syn.slate(teams=4, seed=seed, per_team=PER_TEAM)
    return ls.pool_for(frame, syn.tags_for(frame), ls.CASH_TAGS, now=syn.NOW, metric="CalPts")


def every_lineup(pool):
    """Every roster-legal set of nine from `pool` (positions and the pair rules; no cap yet)."""
    by = {pos: [p for p in pool if p.position == pos] for pos in ("QB", "RB", "WR", "TE", "DST")}
    out = []
    for n_rb, n_wr, n_te in FLEX_MIXES:
        for rbs in combinations(by["RB"], n_rb):
            if any(conflicts(p, [o for o in rbs if o is not p]) for p in rbs):
                continue
            for wrs in combinations(by["WR"], n_wr):
                for tes in combinations(by["TE"], n_te):
                    for qb in by["QB"]:
                        for dst in by["DST"]:
                            if not conflicts(dst, [qb]):
                                out.append((qb, *rbs, *wrs, *tes, dst))
    return out


@pytest.fixture(scope="module")
def pool():
    return small_pool()


@pytest.fixture(scope="module")
def universe(pool):
    return every_lineup(pool)


@pytest.fixture(scope="module")
def cap(universe):
    """A cap about half of the lineups fit under, so the salary row decides."""
    totals = sorted(sum(p.salary for p in lu) for lu in universe)
    return totals[len(totals) // 2]


def best(universe, cap, k, test=lambda lu: True, holding=()):
    """The exhaustive answer: the `k` best scores among the lineups under `cap` that pass `test`."""
    held = {p.name for p in holding}
    scores = [
        round(score(lu), 9)
        for lu in universe
        if sum(p.salary for p in lu) <= cap and held <= {p.name for p in lu} and test(list(lu))
    ]
    return sorted(scores, reverse=True)[:k]


def state_for(cap, fixed=()):
    seated = sh.seat(list(fixed))
    return LineupState(
        cap=cap,
        fixed=seated,
        open=[OpenSlot(i, slot, None) for i, slot in enumerate(ROSTER_SLOTS) if i not in seated],
    )


def solve(pool, cap, *, fixed=(), k=K, **kwargs):
    """`build_milp.top_lineups` on the pool; returns the whole lineups, best first."""
    state = state_for(cap, fixed)
    names = {p.name for p in fixed}
    found, complete = bm.top_lineups(state, [p for p in pool if p.name not in names], k=k, **kwargs)
    assert complete
    return [[*state.fixed.values(), *a.values()] for a in found]


def scores(lineups):
    return [round(score(lu), 9) for lu in lineups]


def qb_of(pool, n=0):
    return sorted((p for p in pool if p.position == "QB"), key=lambda p: -p.value)[n]


# --- the roster, the cap, the pair rules, the cuts ----------------------------------------------------------


def test_the_plain_model_finds_the_best_lineups_of_the_whole_slate(pool, universe, cap):
    lineups = solve(pool, cap, k=10)
    assert scores(lineups) == best(universe, cap, 10)
    for lu in lineups:
        assert len({p.name for p in lu}) == 9 and sum(p.salary for p in lu) <= cap
        counts = {pos: sum(1 for p in lu if p.position == pos) for pos in ("QB", "RB", "WR", "TE", "DST")}
        assert counts["QB"] == 1 and counts["DST"] == 1  # one each; FLEX is the seventh RB / WR / TE
        assert (counts["RB"], counts["WR"], counts["TE"]) in FLEX_MIXES
        assert not any(conflicts(p, [o for o in lu if o is not p]) for p in lu)  # DST v QB, one RB a game


def test_each_lineup_sits_in_its_slots_with_one_flex(pool, cap):
    state = state_for(cap)
    found, _ = bm.top_lineups(state, pool, k=3)
    for assignment in found:
        assert sorted(assignment) == list(range(len(ROSTER_SLOTS)))
        for index, player in assignment.items():
            slot = ROSTER_SLOTS[index]
            assert player.position in (("RB", "WR", "TE") if slot == "FLEX" else (slot,))


def test_the_salary_cap_is_a_constraint(pool, universe, cap):
    tight = sorted(sum(p.salary for p in lu) for lu in universe)[len(universe) // 20]
    assert scores(solve(pool, tight)) == best(universe, tight, K)
    assert best(universe, tight, 1)[0] < best(universe, cap, 1)[0]  # the cap really cost something
    cheapest = min(sum(p.salary for p in lu) for lu in universe)
    assert solve(pool, cheapest - 100) == []  # nothing fits: no lineup, and the run still finished


def test_a_no_good_cut_excludes_each_lineup_found_and_nothing_else(pool, universe, cap):
    lineups = solve(pool, cap, k=25)
    sets = [frozenset(p.name for p in lu) for lu in lineups]
    assert len(set(sets)) == 25  # no lineup twice
    assert scores(lineups) == best(universe, cap, 25)  # and none skipped


def test_asking_for_more_than_exist_returns_them_all(pool, universe, cap):
    qb, dst = qb_of(pool), next(p for p in pool if p.position == "DST" and p.team != qb_of(pool).opp)
    rbs = [p for p in pool if p.position == "RB"][:1]
    wrs = [p for p in pool if p.position == "WR"][:3]
    fixed = [qb, dst, *rbs, *wrs]  # three slots left: a RB, a TE and the FLEX
    generous = 10 * cap
    lineups = solve(pool, generous, fixed=fixed, k=500)
    assert len(lineups) == len(best(universe, generous, 10**6, holding=fixed)) < 500


def test_fixed_players_are_in_every_lineup_and_the_pool_is_the_only_choice(pool, universe, cap):
    qb = qb_of(pool, 1)
    always = [qb, next(p for p in pool if p.position == "TE" and p.team != qb.team)]
    assert scores(solve(pool, cap, fixed=always)) == best(universe, cap, K, holding=always)
    never = {p.name for p in sorted(pool, key=lambda p: -p.value)[:3]}  # Never use: out of the pool
    kept = [p for p in pool if p.name not in never]
    lineups = solve(kept, cap)
    assert scores(lineups) == best(universe, cap, K, lambda lu: not never & {p.name for p in lu})


def test_a_pair_of_fixed_players_is_not_re_judged_but_nobody_joins_them(pool, cap):
    """Two fixed RBs of one game stay (the search never re-judges players it did not place) and no third RB
    of that game is added: the same answer as the branch-and-bound gives."""
    rbs = [p for p in pool if p.position == "RB" and p.game == pool[0].game][:2]
    assert conflicts(rbs[0], [rbs[1]])
    state = state_for(10 * cap, rbs)
    rest = [p for p in pool if p not in rbs]
    old, _ = top_lineups(state, rest, k=K)
    new, _ = bm.top_lineups(state, rest, k=K)
    assert [round(score(a.values()), 9) for a in new] == [round(score(a.values()), 9) for a in old]
    assert all(p.game != rbs[0].game for a in new for p in a.values() if p.position == "RB")


def test_a_name_listed_twice_is_one_player(pool, cap):
    twin = next(p for p in pool if p.position == "WR")
    lineups = solve([*pool, twin], cap)
    assert all(len({p.name for p in lu}) == 9 for lu in lineups)


# --- the shapes ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize("shape", [sh.STACK, sh.NAKED, sh.RB_STACK, sh.GAME, sh.RB_DST])
def test_each_shape_is_its_rule_as_rows(pool, universe, cap, shape):
    rule = sh.rule_for(shape)
    for n in range(2):
        qb = qb_of(pool, n)
        lineups = solve(pool, cap, fixed=[qb], shape=shape, qb=qb)
        assert lineups and all(rule(lu) for lu in lineups)
        assert scores(lineups) == best(universe, cap, K, rule, holding=[qb])


def test_rb_with_his_own_dst_holds_for_a_fixed_dst_too(pool, universe, cap):
    qb = qb_of(pool)
    dst = next(p for p in pool if p.position == "DST" and p.team not in (qb.team, qb.opp))
    rule = sh.rule_for(sh.RB_DST)
    lineups = solve(pool, cap, fixed=[qb, dst], shape=sh.RB_DST, qb=qb)
    assert lineups and all(any(p.position == "RB" and p.team == dst.team for p in lu) for lu in lineups)
    assert scores(lineups) == best(universe, cap, K, rule, holding=[qb, dst])


def test_the_contrarian_cut_is_a_row_on_the_stacks_summed_ownership(pool, universe, cap):
    qb = qb_of(pool)
    own = {p.name: 0.02 + 0.03 * (i % 7) for i, p in enumerate(pool)}
    stacks = sorted(sh.stack_ownership(lu, own) for lu in universe if lu[0] is qb and sh.is_stacked(lu))
    cut = stacks[len(stacks) // 4]

    def rule(lu):
        return sh.is_stacked(lu) and sh.stack_ownership(lu, own) <= cut + 1e-12

    lineups = solve(pool, cap, fixed=[qb], shape=sh.CONTRARIAN, qb=qb, own=own, max_stack_own=cut)
    assert lineups and all(rule(lu) for lu in lineups)
    assert scores(lineups) == best(universe, cap, K, rule, holding=[qb])
    assert scores(lineups) != best(universe, cap, K, sh.is_stacked, holding=[qb])  # the cut did something


def test_a_shape_needs_its_quarterback_fixed(pool, cap):
    with pytest.raises(ValueError, match="fixed quarterback"):
        bm.top_lineups(state_for(cap), pool, k=1, shape=sh.STACK, qb=qb_of(pool))


# --- the whole-lineup rules ---------------------------------------------------------------------------------


def test_at_least_one_of_a_set_of_names(pool, universe, cap):
    ranked = sorted((lu for lu in universe if sum(p.salary for p in lu) <= cap), key=lambda lu: -score(lu))
    in_the_best = {p.name for lu in ranked[:15] for p in lu}
    marked = frozenset([p.name for p in pool if p.name not in in_the_best][:4])  # nobody the best 15 hold

    def rule(lu):
        return any(p.name in marked for p in lu)

    lineups = solve(pool, cap, rules=LeafRules(need_one_of=marked), leaf_filter=rule)
    assert scores(lineups) == best(universe, cap, K, rule) != best(universe, cap, K)
    assert solve(pool, cap, rules=LeafRules(need_one_of=frozenset())) == []  # nobody is marked: no lineup


def test_at_most_n_from_one_game(pool, universe, cap):
    def rule(lu):
        return max(sum(1 for p in lu if p.game == g) for g in {p.game for p in lu}) <= 5

    lineups = solve(pool, cap, rules=LeafRules(max_per_game=5), leaf_filter=rule)
    assert lineups and all(rule(lu) for lu in lineups)
    assert scores(lineups) == best(universe, cap, K, rule)
    assert solve(pool, cap, rules=LeafRules(max_per_game=4)) == []  # nine players, two games


@pytest.mark.parametrize(
    "make",
    [
        lambda team: StackRules(no_stack={team}),
        lambda team: StackRules(no_bring_back={team}),
        lambda team: StackRules(only={team: {"QB+WR", "QB+RB+WR"}}),
        lambda team: StackRules(only={team: {"QB+2WR+TE"}}, no_bring_back={team}),
        lambda team: StackRules(only={team: {"QB+3K"}}),  # a make-up nobody can build: never a stack of them
    ],
)
def test_the_rules_boxes_stack_rules_bind_only_when_the_lineup_is_a_stack_of_that_team(
    pool, universe, cap, make
):
    """With no QB fixed (a cash search): a lineup without that team's QB, or with him and no teammate, or
    with four of them, is not a stack and is left alone."""
    stacks = make(qb_of(pool).team)
    lineups = solve(pool, cap, k=12, rules=LeafRules(stacks=stacks), leaf_filter=stacks.ok)
    assert all(stacks.ok(lu) for lu in lineups)
    assert scores(lineups) == best(universe, cap, 12, stacks.ok)
    assert scores(lineups) != best(universe, cap, 12)  # the rule removed something


def test_the_stack_rules_hold_inside_a_shape_search_too(pool, universe, cap):
    qb = qb_of(pool)
    rule = sh.rule_for(sh.RB_STACK)
    stacks = StackRules(no_bring_back={qb.team}, only={qb.team: {"QB+RB", "QB+RB+WR"}})

    def both(lu):
        return rule(lu) and stacks.ok(lu)

    lineups = solve(pool, cap, fixed=[qb], shape=sh.RB_STACK, qb=qb, rules=LeafRules(stacks=stacks))
    assert lineups and scores(lineups) == best(universe, cap, K, both, holding=[qb])
    banned = LeafRules(stacks=StackRules(no_stack={qb.team}))
    assert solve(pool, cap, fixed=[qb], shape=sh.STACK, qb=qb, rules=banned) == []


def test_the_make_up_labels_are_the_shape_modules_own_spelling():
    qb = SwapPlayer("q", "QB", "A", "B", "g", 5000, 1.0)
    for rb in range(4):
        for wr in range(4):
            for te in range(4):
                if not 1 <= rb + wr + te <= 3:
                    continue
                mates = [
                    SwapPlayer(f"{pos}{i}", pos, "A", "B", "g", 4000, 1.0)
                    for pos, n in (("RB", rb), ("WR", wr), ("TE", te))
                    for i in range(n)
                ]
                assert bm.composition_label(rb, wr, te) == sh.composition([qb, *mates])


# --- exact, whatever the solver's tolerances ----------------------------------------------------------------


def test_a_rule_nobody_described_is_still_enforced_lineup_by_lineup(pool, universe, cap):
    """A `leaf_filter` with no description has no rows: HiGHS proposes, the test disposes, the rejected
    lineup is cut and does not count."""
    ranked = sorted((lu for lu in universe if sum(p.salary for p in lu) <= cap), key=lambda lu: -score(lu))
    missing = {p.name for p in ranked[5]} - {p.name for lu in ranked[:5] for p in lu}
    assert missing  # someone the sixth-best lineup holds and the five better ones do not

    def rule(lu):
        return bool(missing & {p.name for p in lu})

    lineups = solve(pool, cap, k=2, leaf_filter=rule)
    assert scores(lineups) == best(universe, cap, 2, rule) != best(universe, cap, 2)


def test_a_run_that_only_finds_rejected_lineups_gives_up_and_says_so(pool, cap, monkeypatch):
    monkeypatch.setattr(bm, "MAX_REJECTED", 3)
    found, complete = bm.top_lineups(state_for(cap), pool, k=2, leaf_filter=lambda lu: False)
    assert found == [] and not complete


def test_the_search_feasibility_test_is_applied_to_every_lineup(pool, universe, cap):
    qb = qb_of(pool)
    feasible = sh.feasible_for(sh.GAME, qb)
    lineups = solve(pool, cap, fixed=[qb], feasible=feasible, k=1)  # no shape rows: only the test
    assert scores(lineups) == best(universe, cap, 1, sh.is_game_stack, holding=[qb])


def test_a_solve_that_fails_is_an_error_not_an_empty_answer(pool, cap, monkeypatch):
    import scipy.optimize

    class Failed:
        x, status, message = None, 1, "iteration limit"

    monkeypatch.setattr(scipy.optimize, "milp", lambda *a, **k: Failed())
    with pytest.raises(RuntimeError, match="iteration limit"):
        bm.top_lineups(state_for(cap), pool, k=1)
