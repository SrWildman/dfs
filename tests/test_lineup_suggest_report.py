"""Suggested lineups on the shared synthetic slate's default run (`report`: one cash build and the GPP builds,
no ownership): legality, diversity, shapes, the listing order, FLEX, determinism. The rest of the builder's
tests are in `test_lineup_suggest.py` and `test_lineup_suggest_owned.py`."""

from itertools import combinations

import pytest
from builder_helpers import (
    NOW,
    assert_legal,
    run,
    slate,
    tags_for,
)

from dfs import lineup_shapes as shapes_mod
from dfs import lineup_suggest as ls
from dfs.kickoff import parse_kickoff
from dfs.models import ROSTER_SLOTS

pytestmark = pytest.mark.slow


@pytest.fixture(scope="module")
def edge():
    return slate()


@pytest.fixture(scope="module")
def report(edge):
    return run(edge)


def _qb(build):
    return next(p.name for p in build.players if p.position == "QB")


def test_the_defaults_are_one_cash_build_and_a_gpp_build_for_every_qb_stack_two_when_they_fit(report, edge):
    kinds = [b.kind for b in report.builds]
    assert kinds[:1] == ["cash"] and set(kinds[1:]) == {"gpp"}
    assert [b.number for b in report.builds] == list(range(1, len(report.builds) + 1))
    quarterbacks = {
        p.name
        for p in ls.pool_for(edge, tags_for(edge), ls.GPP_TAGS, now=NOW, metric="CalPts")
        if p.position == "QB"
    }
    per_qb = {}
    for build in (b for b in report.builds if b.kind == "gpp"):
        per_qb[_qb(build)] = per_qb.get(_qb(build), 0) + 1
    assert set(per_qb) == quarterbacks  # every QB stack has a build...
    assert all(1 <= n <= ls.BUILDS_PER_QB for n in per_qb.values())  # ...and at most two each
    assert any(n == ls.BUILDS_PER_QB for n in per_qb.values())
    assert report.complete


def test_every_build_is_a_legal_lineup(report):
    for build in report.builds:
        assert_legal(build)


def test_every_build_differs_from_the_earlier_ones_of_its_kind_by_at_least_three_players(report):
    for kind in ("cash", "gpp"):
        group = [b for b in report.builds if b.kind == kind]
        floor = (
            ls.MIN_DIFFERENCE if kind == "cash" else ls.SECOND_BUILD_MIN_DIFFERENCE
        )  # a QB's 2nd GPP build
        for a, b in combinations(group, 2):
            assert len({p.name for p in a.players} - {p.name for p in b.players}) >= floor


def test_every_gpp_build_has_a_shape_it_really_matches_and_its_title_names_it(report):
    for build in (b for b in report.builds if b.kind == "gpp"):
        qb = next(p for p in build.players if p.position == "QB")
        mates = [p for p in build.players if p.team == qb.team and p.position in ("RB", "WR", "TE")]
        assert 1 <= len(mates) <= 3  # every shape ties the QB to one to three teammates
        assert build.shapes and set(build.shapes) <= set(shapes_mod.SHAPES)
        assert tuple(shapes_mod.shapes_of(build.players)) == tuple(
            s for s in build.shapes if s != shapes_mod.CONTRARIAN
        )
        assert build.stack == shapes_mod.shape_name(build.players, build.shapes)
        assert build.stack.startswith(qb.team) and build.stack in build.title


def test_builds_are_listed_cash_then_gpp_by_qb_then_calpts_total_and_numbered_down_the_list(report):
    """Sam, 2026-10-17: ordered by QB, then CalPts total, within Cash and GPP; Build N follows that order."""
    assert [b.number for b in report.builds] == list(range(1, len(report.builds) + 1))
    kinds = [b.kind for b in report.builds]
    assert kinds == sorted(kinds, key=lambda k: k != "cash")  # every cash build before every GPP build
    for kind in ("cash", "gpp"):
        part = [b for b in report.builds if b.kind == kind]
        keys = [(_qb(b), -round(b.projection, 6)) for b in part]
        assert keys == sorted(keys), kind  # QB name A-Z, the higher CalPts total first within a QB
    for b in report.builds:  # the title carries the number it is listed under
        assert b.title.startswith(f"{'Cash' if b.kind == 'cash' else 'GPP'} build {b.number}")


def test_the_flex_of_every_build_is_the_latest_kickoff_of_its_surplus_position(report, edge):
    kickoff = dict(zip(edge["Name"], edge["GameStart"].map(parse_kickoff), strict=True))
    for build in report.builds:
        flex = build.players[ROSTER_SLOTS.index("FLEX")]
        same = [p for p in build.players if p.position == flex.position]
        assert kickoff[flex.name] == max(kickoff[p.name] for p in same)


def test_the_same_pool_and_data_give_the_same_builds(edge, report):
    again = run(edge)
    assert [(b.title, [p.name for p in b.players], b.p_cash, b.p_gpp) for b in again.builds] == [
        (b.title, [p.name for p in b.players], b.p_cash, b.p_gpp) for b in report.builds
    ]


def test_without_ownership_the_gpp_objective_is_the_plain_top_1_percent(report):
    assert any("Ownership has not published" in n for n in report.notes)
    for b in (b for b in report.builds if b.kind == "gpp"):
        assert b.objective is None and b.e_dups is None and "dupes" not in b.title
