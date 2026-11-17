"""Suggested lineups once ownership has published (`owned_report`): shapes, duplicates, the Top 1% share, the
rank option and the contest size. The rest of the builder's tests are in `test_lineup_suggest.py` and
`test_lineup_suggest_report.py`."""

import pytest
from builder_helpers import (
    assert_legal,
    owned,
    run,
    slate,
)

from dfs import lineup_shapes as shapes_mod
from dfs import lineup_suggest as ls
from dfs.late_swap_search import SwapPlayer
from dfs.sim_inputs import build_specs

pytestmark = pytest.mark.slow


@pytest.fixture(scope="module")
def edge():
    return slate()


@pytest.fixture(scope="module")
def owned_report(edge):
    return run(owned(edge))


def _cand(names_values, p_gpp, e_dups=None):
    players = tuple(SwapPlayer(n, "WR", "T", "U", "G", 5000, 10.0) for n in names_values)
    objective = None if e_dups is None else p_gpp / (1 + e_dups)
    return ls.Scored(players, 90.0, 45000, 0.5, p_gpp, 100.0, 130.0, (), None, None, e_dups, objective)


def test_the_gpp_objective_ranks_equal_top_1_percent_by_fewer_duplicates():
    unique, chalk, no_info = _cand("a", 0.02, 0.1), _cand("b", 0.02, 5.0), _cand("c", 0.02)
    ranked = sorted([chalk, unique], key=ls.gpp_key)
    assert [s.players[0].name for s in ranked] == ["a", "b"]
    # a higher Top 1% loses to enough extra copies; without ownership the plain chance decides
    assert ls.gpp_key(_cand("d", 0.021, 5.0)) > ls.gpp_key(_cand("e", 0.020, 0.1))
    assert no_info.gpp_value == pytest.approx(0.02)
    assert ls.gpp_key(_cand("f", 0.03)) < ls.gpp_key(_cand("g", 0.02))


def test_top1_ranks_by_the_chance_alone_and_share_discounts_the_duplicates():
    unique, chalk = _cand("a", 0.020, 0.1), _cand("b", 0.021, 5.0)
    assert [s.players[0].name for s in sorted([unique, chalk], key=ls.top1_key)] == ["b", "a"]
    assert [s.players[0].name for s in sorted([unique, chalk], key=ls.gpp_key)] == ["a", "b"]
    # with the chance tied, the projection decides under top1 (the duplicates are only shown)
    tied = [_cand("c", 0.02, 5.0), _cand("d", 0.02, 0.1)]
    assert ls.top1_key(tied[0])[:3] == ls.top1_key(tied[1])[:3]  # the chance, projection and salary tie


def test_the_default_rank_is_top1_and_an_unknown_rank_is_refused():
    assert ls.DEFAULT_RANK == "top1" and ls.RANKS == ("top1", "share")
    assert ls.rank_key("top1") is ls.top1_key and ls.rank_key("share") is ls.gpp_key
    with pytest.raises(ValueError, match="rank must be one of top1, share"):
        ls.rank_key("ev")


def test_a_gpp_run_defaults_to_top1_and_either_rank_still_reports_the_duplicates(edge, owned_report):
    explicit = run(owned(edge), rank="top1")
    assert [_build_key(b) for b in explicit.builds] == [_build_key(b) for b in owned_report.builds]
    shared = run(owned(edge), rank="share")
    for report in (explicit, shared):
        gpp = [b for b in report.builds if b.kind == "gpp"]
        assert gpp and all(b.e_dups is not None and b.objective is not None for b in gpp)
        for b in report.builds:
            assert_legal(b)
    # the same lineups are scored either way; only the order they are picked in can differ
    assert {b.p_cash for b in shared.builds if b.kind == "cash"} == {
        b.p_cash for b in explicit.builds if b.kind == "cash"
    }
    with pytest.raises(ValueError):
        run(owned(edge), rank="bogus")


def test_every_gpp_build_reports_sum_product_dupes_and_the_top_1_share(owned_report):
    from dfs.sim import dups

    edge = owned(slate())
    specs = build_specs(edge)
    from dfs.sim_inputs import ownership_by_name

    shares = ls.ownership_shares(specs, edge, None, "top")
    assert shares and sum(shares.values()) == pytest.approx(9.0)
    assert ownership_by_name(edge)  # the shares come from the edge's Own%
    gpp = [b for b in owned_report.builds if b.kind == "gpp"]
    assert gpp
    for b in gpp:
        own = dups.shares_for([p.name for p in b.players], shares)
        assert b.own_sum == pytest.approx(dups.ownership_sum(own))
        assert b.own_product == pytest.approx(dups.ownership_product(own))
        assert b.e_dups == pytest.approx(dups.DUP_MODEL_GPP.expected(own, dups.DEFAULT_GPP_ENTRIES))
        assert b.entries == dups.DEFAULT_GPP_ENTRIES
        assert b.objective == pytest.approx(b.p_gpp / (1 + b.e_dups))
        assert dups.dups_label(b.e_dups) in b.title and b.stack in b.title
        assert b.shapes


def test_the_contest_size_is_an_input(edge, owned_report):
    small = run(owned(edge), cash_builds=0, gpp_entries=800)
    base = [b for b in owned_report.builds if b.kind == "gpp"]
    assert {x.entries for x in small.builds} == {800} and {x.entries for x in base} == {1000}
    # the same lineup has proportionally more copies in a bigger field
    by_players = {tuple(p.name for p in b.players): b for b in base}
    shared = [b for b in small.builds if tuple(p.name for p in b.players) in by_players]
    assert shared
    for b in shared:
        assert by_players[tuple(p.name for p in b.players)].e_dups == pytest.approx(b.e_dups * 1000 / 800)


def test_cash_builds_are_not_changed_by_ownership_only_read_out(edge, owned_report):
    plain = [b for b in run(edge, gpp_builds=0).builds if b.kind == "cash"]  # no ownership
    with_own = [b for b in owned_report.builds if b.kind == "cash"]
    assert [[p.name for p in b.players] for b in plain] == [[p.name for p in b.players] for b in with_own]
    assert [b.p_cash for b in plain] == [b.p_cash for b in with_own]
    assert all(b.e_dups is None for b in plain) and all(b.e_dups is not None for b in with_own)
    assert all(b.objective is None for b in with_own)  # nothing ranks on it


def test_a_cash_build_many_entries_share_gets_a_warning_and_is_still_built(edge):
    loud = run(owned(edge), cash_builds=3, gpp_builds=0, double_up_entries=5_000_000)
    assert len([b for b in loud.builds if b.kind == "cash"]) == 3  # never a filter
    assert any("identical entries" in n for n in loud.notes)
    quiet = run(owned(edge), gpp_builds=0, double_up_entries=10)
    assert not any("identical entries" in n for n in quiet.notes)


def test_the_candidates_cover_the_shapes_and_the_builds_are_tagged(edge, owned_report):
    seen = {s for b in owned_report.builds if b.kind == "gpp" for s in b.shapes}
    assert shapes_mod.STACK in seen and len(seen) > 1
    stack_only = run(owned(edge), cash_builds=0, shapes=(shapes_mod.STACK,))
    assert all(b.shapes and shapes_mod.STACK in b.shapes for b in stack_only.builds)


def test_the_shaped_builds_are_deterministic_under_a_seed(edge, owned_report):
    again = run(owned(edge))
    assert [_build_key(b) for b in again.builds] == [_build_key(b) for b in owned_report.builds]


def _build_key(b):
    return (b.title, [p.name for p in b.players], b.p_cash, b.p_gpp, b.shapes, b.e_dups, b.objective)


def test_the_shaped_search_stays_inside_the_time_budget(owned_report):
    assert owned_report.complete and owned_report.seconds < ls.TIME_LIMIT_SECONDS
