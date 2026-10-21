"""The search budget is counted in search work, not wall seconds: same inputs, same builds."""

import time

import pytest
import test_lineup_suggest as syn

from dfs import search_clock as clock
from dfs.late_swap_search import LineupState, OpenSlot, top_lineups
from dfs.models import ROSTER_SLOTS


def test_now_moves_with_search_work_and_not_with_the_wall():
    before = clock.now()
    time.sleep(0.05)
    assert clock.now() == before  # idle time costs no budget
    clock.advance(clock.CHECK_EVERY)
    assert clock.now() - before == pytest.approx(clock.CHECK_EVERY / clock.NODES_PER_SECOND)


def test_wall_clock_switches_to_real_seconds_and_back():
    with clock.wall_clock():
        a = clock.now()
        time.sleep(0.02)
        assert clock.now() - a >= 0.02
    before = clock.now()
    time.sleep(0.02)
    assert clock.now() == before


def test_a_search_is_cut_by_its_budget_at_the_same_node_every_time():
    edge = syn.slate(teams=6, seed=7)
    pool = [
        p for p in syn.ls.pool_for(edge, syn.tags_for(edge), syn.ls.CASH_TAGS, now=syn.NOW, metric="CalPts")
    ]
    state = LineupState(
        cap=syn.CAP, fixed={}, open=[OpenSlot(i, slot, None) for i, slot in enumerate(ROSTER_SLOTS)]
    )
    budget = 0.0  # spent at the first check, after 512 nodes
    first, done_first = top_lineups(state, pool, k=50, time_limit=budget)
    time.sleep(0.1)  # a slower, busier machine between the runs changes nothing
    second, done_second = top_lineups(state, pool, k=50, time_limit=budget)
    assert not done_first and not done_second
    names = lambda found: [sorted(p.name for p in a.values()) for a in found]  # noqa: E731
    assert names(first) == names(second)


def test_the_same_slate_gives_the_same_builds_when_the_search_is_cut_short():
    edge = syn.slate(teams=6, seed=3)

    def builds():
        r = syn.run(edge, cash_builds=2, gpp_builds=6, n_sims=300, time_limit=0.05)  # far less than it needs
        return [[p.name for p in b.players] for b in r.builds], r.complete, r.notes

    first = builds()
    time.sleep(0.2)
    assert first[1] is False or first[2]  # it really was cut (or said so)
    assert builds() == first
