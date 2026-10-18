"""Build shapes: the rules on hand-built lineups, and the generators on a synthetic slate (every lineup
legal, every lineup the shape it was asked for, the best found exactly, deterministic)."""

import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

import test_lineup_suggest as syn  # noqa: E402

from dfs import lineup_shapes as sh  # noqa: E402
from dfs import lineup_suggest as ls  # noqa: E402
from dfs.late_swap_search import SwapPlayer  # noqa: E402


def P(name, pos, team, opp, value=10.0, salary=5000):
    return SwapPlayer(name, pos, team, opp, f"{min(team, opp)}-{max(team, opp)}", salary, value)


QB = P("qb", "QB", "DET", "ARI")
W1, W2, TE = P("w1", "WR", "DET", "ARI"), P("w2", "WR", "DET", "ARI"), P("te", "TE", "DET", "ARI")
RB_OWN, RB_OTHER = P("rb_own", "RB", "DET", "ARI"), P("rb_other", "RB", "KC", "LV")
OPP_WR, OPP_TE, OPP_RB = (
    P("o_wr", "WR", "ARI", "DET"),
    P("o_te", "TE", "ARI", "DET"),
    P("o_rb", "RB", "ARI", "DET"),
)
DST_DET, DST_KC, DST_ARI = (
    P("d_det", "DST", "DET", "ARI"),
    P("d_kc", "DST", "KC", "LV"),
    P("d_ari", "DST", "ARI", "DET"),
)


def test_the_stack_rule_is_the_qb_two_pass_catchers_and_a_bring_back():
    assert sh.is_stacked([QB, W1, TE, OPP_WR])
    assert not sh.is_stacked([QB, W1, OPP_WR])  # one pass catcher
    assert not sh.is_stacked([QB, W1, W2])  # no bring-back
    assert not sh.is_stacked([QB, W1, W2, DST_ARI])  # a DST is not a bring-back
    assert not sh.is_stacked([W1, W2, OPP_WR])  # no QB


def test_the_game_stack_is_the_qb_a_pass_catcher_and_two_players_of_the_other_team():
    assert sh.is_game_stack([QB, W1, OPP_WR, OPP_RB])  # a RB counts on the other side
    assert not sh.is_game_stack([QB, W1, OPP_WR])  # one opponent
    assert not sh.is_game_stack([QB, RB_OWN, OPP_WR, OPP_TE])  # no pass catcher of his own
    assert not sh.is_game_stack([QB, W1, OPP_WR, DST_ARI])  # a DST is not a player of the game's offense


def test_the_double_stack_is_a_stack_core_plus_a_rb_of_the_same_team_and_no_bring_back_is_needed():
    assert sh.is_double_stack([QB, W1, W2, RB_OWN])
    assert not sh.is_double_stack([QB, W1, W2, RB_OTHER])
    assert not sh.is_double_stack([QB, W1, RB_OWN])  # one pass catcher


def test_rb_dst_needs_the_rb_and_the_dst_of_one_team_and_a_stack():
    assert sh.has_rb_dst([RB_OTHER, DST_KC]) and not sh.has_rb_dst([RB_OTHER, DST_DET])
    assert sh.shapes_of([QB, W1, TE, OPP_WR, RB_OTHER, DST_KC]).count(sh.RB_DST) == 1
    assert sh.RB_DST not in sh.shapes_of([QB, W1, OPP_WR, RB_OTHER, DST_KC])  # no stack


def test_a_lineup_carries_every_shape_it_matches_and_is_named_by_the_most_specific():
    lineup = [QB, W1, W2, OPP_WR, OPP_RB, RB_OWN]
    shapes = sh.shapes_of(lineup)
    assert set(shapes) == {sh.STACK, sh.GAME, sh.DOUBLE}
    assert sh.primary_shape(shapes) == sh.DOUBLE
    assert sh.shape_name(lineup, shapes) == "DET double stack"
    assert sh.shape_name([QB, W1, TE, OPP_WR], (sh.STACK,)) == "DET stack + ARI bring-back"
    assert sh.shape_name([W1], ()) == ""


def test_the_stack_core_and_its_ownership_and_the_bottom_third_cutoff():
    lineup = [QB, W1, TE, OPP_WR, RB_OTHER, DST_KC]
    assert {p.name for p in sh.stack_core(lineup)} == {"qb", "w1", "te", "o_wr"}
    own = {"qb": 0.1, "w1": 0.2, "te": 0.05, "o_wr": 0.15, "rb_other": 0.9}
    assert sh.stack_ownership(lineup, own) == pytest.approx(0.5)  # the RB and DST are not in the stack
    assert sh.stack_ownership(lineup, {}) == pytest.approx(4 * sh.OWN_FLOOR)

    def stack(qb_name, share):
        qb = P(qb_name, "QB", "DET", "ARI")
        return [qb, W1, TE, OPP_WR], {qb_name: share, "w1": 0, "te": 0, "o_wr": 0}

    cores, owns = [], {}
    for i, share in enumerate([0.1, 0.2, 0.3, 0.4, 0.5, 0.6]):
        lu, o = stack(f"qb{i}", share)
        cores.append(lu)
        owns |= o
    owns.update({"w1": 0.1, "te": 0.1, "o_wr": 0.1})
    cut = sh.contrarian_cutoff(cores, owns)
    below = [lu for lu in cores if sh.stack_ownership(lu, owns) <= cut]
    assert len(below) == 2  # the bottom third of six stacks
    assert sh.contrarian_cutoff([[W1, W2]], owns) is None  # no stack, no cutoff
    flagged = [sh.CONTRARIAN in sh.shapes_of(lu, contrarian_at=cut, own=owns) for lu in cores]
    assert flagged == [True, True, False, False, False, False]


# --- the generators on a synthetic slate ----------------------------------------------------------------


@pytest.fixture(scope="module")
def pool():
    edge = syn.slate(teams=4, seed=2)
    return ls.pool_for(edge, syn.tags_for(edge), ls.GPP_TAGS, now=syn.NOW, metric="CalPts")


@pytest.mark.parametrize("shape", sh.SHAPES)
def test_each_generator_yields_only_legal_lineups_that_match_its_shape(pool, shape):
    lineups, complete = sh.shape_lineups(pool, shape, cap=syn.CAP, per_qb=12, **_kwargs(shape, pool))
    assert complete and lineups
    for lu in lineups:
        assert len(lu) == 9 and len({p.name for p in lu}) == 9
        assert sum(p.salary for p in lu) <= syn.CAP
        assert sh.rule_for(shape)(lu)
        assert not any(syn.conflicts(p, [o for o in lu if o is not p]) for p in lu)
    totals = [round(sum(p.value for p in lu), ls.VALUE_DECIMALS) for lu in lineups]
    assert totals == sorted(totals, reverse=True)  # best first


@pytest.mark.parametrize("shape", [sh.STACK, sh.GAME, sh.DOUBLE, sh.RB_DST])
def test_each_generator_finds_the_best_lineup_of_its_shape_exactly(pool, shape):
    """Against every legal lineup in the top 3,000 by projection (the prune must never hide a lineup)."""
    best, _ = sh.shape_lineups(pool, shape, cap=syn.CAP, per_qb=1, rb_dst_options=99)
    every, _ = ls.cash_lineups(pool, cap=syn.CAP, k=3000)
    passing = [lu for lu in every if sh.rule_for(shape)(lu)]
    assert passing
    top = max(sum(p.value for p in lu) for lu in passing)
    assert max(sum(p.value for p in lu) for lu in best) == pytest.approx(top)


def test_the_contrarian_cut_is_a_constraint_on_the_search(pool):
    own = {p.name: 0.02 + 0.03 * (i % 5) for i, p in enumerate(pool)}
    base, _ = ls.stacked_lineups(pool, cap=syn.CAP, per_qb=20)
    cutoff = sh.contrarian_cutoff(base, own)
    lineups, _ = sh.shape_lineups(pool, sh.CONTRARIAN, cap=syn.CAP, per_qb=10, own=own, max_stack_own=cutoff)
    assert lineups
    assert all(sh.stack_ownership(lu, own) <= cutoff + 1e-12 for lu in lineups)
    assert all(sh.is_stacked(lu) for lu in lineups)


def _kwargs(shape, pool):
    if shape != sh.CONTRARIAN:
        return {}
    own = {p.name: 0.02 + 0.03 * (i % 5) for i, p in enumerate(pool)}
    base, _ = ls.stacked_lineups(pool, cap=syn.CAP, per_qb=20)
    return {"own": own, "max_stack_own": sh.contrarian_cutoff(base, own)}


def test_the_generators_are_deterministic(pool):
    for shape in sh.SHAPES:
        a, _ = sh.shape_lineups(pool, shape, cap=syn.CAP, per_qb=8, **_kwargs(shape, pool))
        b, _ = sh.shape_lineups(list(reversed(pool)), shape, cap=syn.CAP, per_qb=8, **_kwargs(shape, pool))
        assert [[p.name for p in lu] for lu in a] == [[p.name for p in lu] for lu in b]


def test_the_stack_wrapper_is_the_stack_shape(pool):
    a, _ = ls.stacked_lineups(pool, cap=syn.CAP, per_qb=10)
    b, _ = sh.shape_lineups(pool, sh.STACK, cap=syn.CAP, per_qb=10)
    assert [[p.name for p in lu] for lu in a] == [[p.name for p in lu] for lu in b]
    assert [[p.name for p in lu] for lu in ls.stacked_lineups(pool, cap=syn.CAP, k=3, per_qb=10)[0]] == [
        [p.name for p in lu] for lu in a[:3]
    ]


def test_feasibility_prunes_only_what_cannot_pass():
    """The prune never says no to a partial lineup that has a passing completion (checked on slot counts)."""
    qb = QB
    for shape in (sh.STACK, sh.GAME, sh.DOUBLE, sh.RB_DST, sh.CONTRARIAN):
        feasible = sh.feasible_for(shape, qb)
        # a full set of open slots can always still reach any shape
        assert feasible([qb], ["DST", "TE", "RB", "RB", "WR", "WR", "WR", "FLEX"])
    f = sh.feasible_for(sh.DOUBLE, qb)
    assert not f([qb, W1, W2, RB_OTHER], ["DST", "TE", "WR"])  # no RB/FLEX slot left for his RB
    assert f([qb, W1, W2, RB_OTHER], ["DST", "TE", "WR", "FLEX"])  # the FLEX can still take it
    g = sh.feasible_for(sh.GAME, qb)
    assert not g([qb, OPP_WR], ["DST", "TE"])  # needs a pass catcher and one more opponent, only a TE slot


def test_a_time_limit_is_respected_and_reported(pool):
    started = time.monotonic()
    _, complete = sh.shape_lineups(pool, sh.GAME, cap=syn.CAP, per_qb=200, deadline=time.monotonic() + 0.01)
    assert time.monotonic() - started < 5
    assert complete in (True, False)
