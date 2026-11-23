"""Sam's Week 11 note: two cheap players from one team in a cash build must earn their place, and say so
(`cash_pairs`). The definition, the Default's choice, the flag's line, and the rule held the same way by the
search, the exact search and the HiGHS checker against every lineup of a small slate."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

import test_build_milp as small  # noqa: E402

from dfs import build_exact as be  # noqa: E402
from dfs import cash_pairs as cp  # noqa: E402
from dfs import lineup_suggest as ls  # noqa: E402
from dfs.cash_core import projection  # noqa: E402
from dfs.config import CashConfig  # noqa: E402
from dfs.late_swap_search import SwapPlayer  # noqa: E402

pool = small.pool
universe = small.universe
cap = small.cap
K = 8


def P(name, pos, team, salary, value=10.0):  # noqa: N802
    return SwapPlayer(name, pos, team, "OPP", f"{team}@OPP", salary, value)


AYOMANOR = P("Elic Ayomanor", "WR", "TEN", 3700)
TATE = P("Carnell Tate", "WR", "TEN", 5500)  # Week 11's: $5,500, so the line is $6,000


# --- the pair -----------------------------------------------------------------------------------------------


def test_a_pair_is_two_cheap_skill_players_from_one_team():
    assert cp.pairs([AYOMANOR, TATE]) == [(AYOMANOR, TATE)]
    assert cp.pairs([AYOMANOR, P("TEN TE", "TE", "TEN", 5999)])  # any two of RB, WR, TE
    assert not cp.pairs([AYOMANOR, P("Pricier", "WR", "TEN", 6000)])  # "under" $6,000: $6,000 is not
    assert not cp.pairs([AYOMANOR, P("Other", "WR", "NYJ", 3000)])  # another team
    # never the QB, never the DST (Sam: skill players only; nearly every DST costs under the line)
    assert not cp.pairs([AYOMANOR, P("TEN QB", "QB", "TEN", 4800), P("Titans", "DST", "TEN", 2500)])
    assert not cp.pairs([AYOMANOR, TATE], cheap_salary=0)  # 0 turns the rule off
    assert cp.pairs([AYOMANOR, TATE], cheap_salary=5000) == []  # Tate is not cheap under $5,000


def test_the_config_defaults_are_sams():
    assert CashConfig() == CashConfig(cheap_salary=6000, cheap_pair_margin=1.5)
    assert (cp.CHEAP_SALARY, cp.PAIR_MARGIN) == (6000, 1.5)


def test_the_flag_is_one_line_naming_the_pair_and_what_it_earns():
    pair = cp.CheapPair((AYOMANOR, TATE), True, cp.EARNS, 2.1)
    assert pair.text == (
        "Two cheap TEN WRs (Elic Ayomanor $3,700, Carnell Tate $5,500): "
        "+2.1 pts over the best lineup without them"
    )
    kept = cp.CheapPair((AYOMANOR, TATE), True, cp.EARNS, 2.1, keep=TATE)
    assert kept.text.endswith(": +2.1 pts over the best lineup without Elic Ayomanor")
    assert cp.CheapPair((AYOMANOR, TATE), True, cp.PICKED, 0.4).text.endswith(
        ": your pick, +0.4 pts over the best lineup without them"
    )
    assert "no legal lineup without them" in cp.CheapPair((AYOMANOR, TATE), True, cp.NO_LINEUP, None).text
    own = {"Carnell Tate": 0.6028, "Elic Ayomanor": 0.3547}
    avoided = cp.CheapPair((AYOMANOR, TATE), False, cp.EARNS, 0.2, keep=TATE, own=own).text
    assert avoided == (
        "Two cheap TEN WRs (Elic Ayomanor $3,700, Carnell Tate $5,500): Elic Ayomanor out, Carnell Tate kept "
        "(cash own Carnell Tate 60%, Elic Ayomanor 35%); the pair was only +0.2 pts over the best lineup "
        "without Elic Ayomanor"
    )
    plain = cp.CheapPair((AYOMANOR, TATE), False, cp.EARNS, 0.9).text
    assert plain.endswith(": swapped out, the pair was only +0.9 pts over the best lineup without them")
    mixed = cp.CheapPair((AYOMANOR, P("TEN TE", "TE", "TEN", 4000)), True, cp.EARNS, 2.0)
    assert mixed.what.startswith("Two cheap TEN players (")


def test_the_most_owned_player_of_the_pair_is_the_one_kept():
    own = {"Carnell Tate": 0.6, "Elic Ayomanor": 0.35}
    assert cp.keeper((AYOMANOR, TATE), own) == TATE
    assert cp.keeper((AYOMANOR, TATE), {"Carnell Tate": 0.6}) is None  # one unknown: no guess
    tie = {"Carnell Tate": 0.4, "Elic Ayomanor": 0.4}
    high = P("Elic Ayomanor", "WR", "TEN", 3700, 12.0)
    assert cp.keeper((high, TATE), tie) == high  # a tie: the higher projection


def test_the_pair_free_lineup_is_a_difference():
    a, b = P("A", "WR", "X", 6000), P("B", "WR", "Y", 6100)
    default = [AYOMANOR, TATE, a]
    alt = [AYOMANOR, a, b]
    assert cp.swaps(default, alt) == [(TATE, b)]


# --- the Default's choice -----------------------------------------------------------------------------------


def lineup(*players):
    return list(players)


def filler(n, value=10.0):
    return [P(f"F{i}", "RB", f"T{i}", 6000, value) for i in range(n)]


def test_the_default_keeps_a_pair_only_when_it_beats_the_lineup_without_by_more_than_the_margin():
    rest = filler(7)
    a, b = P("A", "WR", "TEN", 3700, 12.0), P("B", "WR", "TEN", 4300, 12.0)
    with_pair = lineup(a, b, *rest)
    calls = []

    def free_at(points):
        def run(keep):
            calls.append(tuple(p.name for p in keep))
            return [lineup(*keep, P("C", "WR", "NYJ", 6000, points), P("D", "WR", "BUF", 6000, 12.0), *rest)]

        return run

    # no ownership yet: the best pair-free lineup, nobody kept on purpose
    chosen, pair = cp.choose([with_pair], free_at(10.5))
    assert calls[-1] == () and pair.keep is None
    # the pair's lineup is 1.5 better: not MORE than the margin, so the pair-free lineups are the candidates
    assert not pair.kept and pair.gain == pytest.approx(1.5) and cp.pairs(chosen[0]) == []
    assert pair.paired == tuple(with_pair)
    # 1.6 better: it earns its place, kept and flagged
    chosen, pair = cp.choose([with_pair], free_at(10.4))
    assert pair.kept and pair.why == cp.EARNS and pair.gain == pytest.approx(1.6) and chosen == [with_pair]
    assert [p.name for p in pair.alternative][:2] == ["C", "D"]
    # with ownership the lineup without the pair keeps its most-owned player (Sam: B had the higher roster%)
    chosen, pair = cp.choose([with_pair], free_at(0.0), own={"A": 0.2, "B": 0.5})
    assert calls[-1] == ("B",) and pair.keep == b and [p.name for p in pair.out] == ["A"]
    # a pair Sam put there (one of them on Always use) is his: kept, flagged as his pick
    chosen, pair = cp.choose([with_pair], free_at(12.0), fixed=["A"])
    assert pair.kept and pair.why == cp.PICKED and chosen == [with_pair]
    # no lineup without one: kept, and it says so
    chosen, pair = cp.choose([with_pair], lambda keep: [])
    assert pair.kept and pair.why == cp.NO_LINEUP and pair.gain is None


def test_when_no_lineup_keeps_the_most_owned_one_the_best_pair_free_lineup_stands_in():
    rest = filler(7)
    with_pair = lineup(P("A", "WR", "TEN", 3700, 12.0), P("B", "WR", "TEN", 4300, 12.0), *rest)
    free = [lineup(P("C", "WR", "NYJ", 6000, 12.0), P("D", "WR", "BUF", 6000, 12.0), *rest)]
    chosen, pair = cp.choose([with_pair], lambda keep: [] if keep else free, own={"A": 0.2, "B": 0.5})
    assert chosen == free and pair.keep is None and not pair.kept


def test_no_second_search_when_the_best_lineup_holds_no_pair():
    plain = lineup(P("A", "WR", "TEN", 3700), P("B", "WR", "NYJ", 4300), *filler(7))
    paired = lineup(P("C", "WR", "TEN", 3700, 1.0), P("D", "WR", "TEN", 4300, 1.0), *filler(7))

    def boom(keep):
        raise AssertionError("searched again")

    assert cp.choose([plain, paired], boom) == ([plain, paired], None)
    assert cp.choose([paired], boom, cheap_salary=0) == ([paired], None)  # the rule off


def test_the_week_input_carries_the_flag_and_the_difference():
    rest = filler(7)
    a, b = P("A", "WR", "TEN", 3700, 12.0), P("B", "WR", "TEN", 4300, 12.0)
    c = P("C", "WR", "NYJ", 5900, 11.8)
    with_pair, without = lineup(a, b, *rest), lineup(b, c, *rest)
    _, avoided = cp.choose([with_pair], lambda keep: [without], own={"A": 0.2, "B": 0.5})
    got = cp.payload(avoided, without, 6000)
    assert got["cheap_salary"] == 6000 and got["pair"]["keep"] == "B" and not got["pair"]["kept"]
    change = got["pair"]["change"]
    assert change["from_projection"] == pytest.approx(94.0) and change["projection"] == pytest.approx(93.8)
    assert change["swaps"] == [{"out": "A", "in": "C", "position": "WR", "d_points": -0.2, "d_salary": -2200}]
    assert cp.payload(None, without, 6000) == {"cheap_salary": 6000, "pair": None}


def test_the_cash_ownership_is_cashown_then_own_percent():
    import pandas as pd

    edge = pd.DataFrame({"Name": ["A", "B", "C"], "CashOwn": [0.4, None, ""], "Own%": [0.1, 0.2, None]})
    assert ls.cash_ownership(edge) == {"A": 0.4, "B": 0.2}


# --- the same rule in every solver, against every lineup --------------------------------------------------


def exhaustive(universe, cap, k, cheap=cp.CHEAP_SALARY):
    return small.best(universe, cap, k, lambda lu: not cp.pairs(lu, cheap))


def scores(lineups):
    return [round(be.score(lu), 9) for lu in lineups]


def pair_free(pool, cap, k=K, cheap=cp.CHEAP_SALARY):
    found, done = ls.cash_lineups(pool, cap=cap, k=k, leaf_ok=cp.leaf(cheap), feasible=cp.feasible(cheap))
    assert done
    return found


def test_the_fixture_s_best_cash_lineup_holds_a_pair(universe, cap):
    fits = [lu for lu in universe if sum(p.salary for p in lu) <= cap]
    best = max(fits, key=be.score)
    assert cp.pairs(best)  # so the tests below test something


@pytest.mark.parametrize("solver", [be.SEARCH, be.EXACT])
def test_the_pair_free_search_is_the_exhaustive_answer(pool, universe, cap, solver):
    with be.use(solver):
        found = pair_free(pool, cap)
    assert all(not cp.pairs(lu) for lu in found)
    assert scores(found) == exhaustive(universe, cap, K)


@pytest.mark.slow
@pytest.mark.parametrize("cheap", [5000, 6000])
def test_the_milp_checker_holds_the_same_rule(pool, universe, cap, cheap):
    with be.use(be.MILP):
        found = pair_free(pool, cap, cheap=cheap)
    with be.use(be.EXACT):
        exact = pair_free(pool, cap, cheap=cheap)
    assert scores(found) == scores(exact) == exhaustive(universe, cap, K, cheap)


def test_the_rule_is_described_for_the_exact_solvers_on_top_of_a_rerun_s_own():
    base = be.described(lambda lu: True, be.LeafRules(max_per_game=5))
    assert be.rules_of(cp.leaf(5000, base)) == be.LeafRules(max_per_game=5, cheap_salary=5000)
    assert be.rules_of(cp.leaf(4500)) == be.LeafRules(cheap_salary=4500)


@pytest.mark.slow
@pytest.mark.parametrize("margin", [0.0, 100.0])
def test_the_cash_candidates_follow_the_margin(pool, universe, cap, margin):
    lineups, done, pair = ls.cash_candidates(pool, cap=cap, pair_margin=margin)
    assert done and pair is not None
    fits = [lu for lu in universe if sum(p.salary for p in lu) <= cap]
    best = max(fits, key=be.score)
    best_free = max((lu for lu in fits if not cp.pairs(lu)), key=be.score)
    assert pair.gain == pytest.approx(projection(best) - projection(best_free))
    if margin == 0.0:  # any gain earns it: the Default is the plain best, the pair kept and flagged
        assert pair.kept and cp.pairs(lineups[0])
    else:  # nothing earns 100 points: the pair-free lineups are the candidates
        assert not pair.kept and not any(cp.pairs(lu) for lu in lineups)
        assert round(be.score(lineups[0]), 9) == exhaustive(universe, cap, 1)[0]


@pytest.mark.slow
def test_the_lineup_without_the_pair_keeps_its_most_owned_player_exhaustively(pool, universe, cap):
    fits = [lu for lu in universe if sum(p.salary for p in lu) <= cap]
    best = max(fits, key=be.score)
    pair = cp.pairs(best)[0]
    own = {p.name: 0.1 * (i + 1) for i, p in enumerate(pair)}  # the last of the pair is the most owned
    _, done, got = ls.cash_candidates(pool, cap=cap, pair_margin=100.0, own=own)
    assert done and got.keep == pair[-1] and not got.kept
    keeps = [lu for lu in fits if not cp.pairs(lu) and pair[-1].name in {p.name for p in lu}]
    assert round(be.score(got.alternative), 9) == round(be.score(max(keeps, key=be.score)), 9)
    assert all(p.name not in {q.name for q in got.alternative} for p in pair[:-1])
