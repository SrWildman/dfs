"""Common random numbers (`simulate.py`): every simulated lineup gets the same draws of player outcomes, so a
lineup's simulated numbers depend only on the lineup (and the universe of players, and the seed), never on the
order the lineups arrive in or which other lineups were simulated beside it. Phase E found the GPP Top 1%
ranking moved when tied lineups were handed draws in a different order; these tests pin that it cannot."""

import random
import zlib
from datetime import UTC, datetime

import numpy as np
import pytest

from dfs import lineup_suggest as ls
from dfs.late_swap_search import SwapPlayer
from dfs.sim import field as field_model
from dfs.sim import simulate as S

ROLES = {"QB": ["QB1"], "RB": ["RB1", "RB2"], "WR": ["WR1", "WR2", "WR3"], "TE": ["TE1"], "DST": ["DST"]}
MEAN = {"QB": 19.0, "RB": 12.0, "WR": 11.0, "TE": 8.0, "DST": 7.0}
SALARY = {"QB": 6500, "RB": 6000, "WR": 5800, "TE": 4500, "DST": 3000}
N_SIMS = 3000


def specs_for(teams=6, seed=3):
    """`teams` teams in `teams // 2` games, a QB, two RBs, three WRs, a TE and a DST each."""
    rng = np.random.default_rng(seed)
    out = {}
    for t in range(teams):
        game, team, opp = f"G{t // 2}", f"T{t:02d}", f"T{t ^ 1:02d}"
        for pos, roles in ROLES.items():
            for role in roles:
                name = f"{team} {role}"
                proj = round(MEAN[pos] * rng.uniform(0.6, 1.4), 1)
                out[name] = S.PlayerSpec(name, pos, team, opp, game, role, proj, SALARY[pos])
    return out


SPECS = specs_for()


def lineups_from(specs, n, seed=0):
    """`n` distinct DK-shaped lineups (QB, 2 RB, 3 WR, TE, FLEX, DST), as names in slot order."""
    rng = random.Random(seed)
    by_pos = {pos: [s.id for s in specs.values() if s.position == pos] for pos in ROLES}
    seen, out = set(), []
    while len(out) < n:
        rb = rng.sample(by_pos["RB"], 3)
        wr = rng.sample(by_pos["WR"], 3)
        te = rng.choice(by_pos["TE"])
        names = [rng.choice(by_pos["QB"]), rb[0], rb[1], *wr, te, rb[2], rng.choice(by_pos["DST"])]
        if frozenset(names) not in seen:
            seen.add(frozenset(names))
            out.append(names)
    return out


def a_field(specs):
    own = {name: 0.05 + 0.4 * ((i * 37) % 11) / 11 for i, name in enumerate(sorted(specs))}
    return field_model.build_field(specs.values(), own, cash_offset=0.6, top_offset=63.2)


def sim(names_list, *, universe=(), field=None, seed=0):
    return S.simulate_lineups(
        [[SPECS[n] for n in names] for names in names_list],
        n_sims=N_SIMS,
        seed=seed,
        field=field,
        universe=universe,
        keep_scores=True,
    )


def assert_same(a: S.LineupStats, b: S.LineupStats):
    assert (a.mean, a.sd, a.p_cash, a.p_gpp) == (b.mean, b.sd, b.p_cash, b.p_gpp)
    assert a.quantiles == b.quantiles


@pytest.mark.parametrize("universe", [(), tuple(SPECS.values())], ids=["lineups only", "universe"])
@pytest.mark.parametrize("with_field", [False, True], ids=["fixed lines", "field"])
def test_shuffling_the_lineups_changes_no_simulated_number(universe, with_field):
    field = a_field(SPECS) if with_field else None
    lineups = lineups_from(SPECS, 40)
    base = sim(lineups, universe=universe, field=field)
    for k in range(3):
        order = list(range(len(lineups)))
        random.Random(k).shuffle(order)
        again = sim([lineups[i] for i in order], universe=universe, field=field)
        for pos, i in enumerate(order):
            assert_same(again.lineups[pos], base.lineups[i])
            np.testing.assert_array_equal(again.scores[:, pos], base.scores[:, i])
        assert (again.p_any_cash, again.p_any_gpp, again.expected_cashing) == (
            base.p_any_cash,
            base.p_any_gpp,
            base.expected_cashing,
        )
        assert again.cash_line == base.cash_line and again.gpp_target == base.gpp_target
        np.testing.assert_allclose(again.score_correlation, base.score_correlation[np.ix_(order, order)])


def test_the_seat_order_inside_a_lineup_does_not_matter():
    lineups = lineups_from(SPECS, 5)
    base = sim(lineups)
    turned = sim([list(reversed(lu)) for lu in lineups])
    for a, b in zip(base.lineups, turned.lineups, strict=True):
        assert_same(a, b)


def test_with_a_universe_a_lineups_numbers_do_not_depend_on_the_other_lineups():
    field = a_field(SPECS)
    lineups = lineups_from(SPECS, 30)
    universe = tuple(SPECS.values())
    alone = sim(lineups[:1], universe=universe, field=field)
    crowd = sim(lineups, universe=universe, field=field)
    other_crowd = sim([lineups[0], *lineups_from(SPECS, 12, seed=9)], universe=universe, field=field)
    assert_same(crowd.lineups[0], alone.lineups[0])
    assert_same(other_crowd.lineups[0], alone.lineups[0])
    np.testing.assert_array_equal(crowd.cash_lines, alone.cash_lines)  # the field's lines too


def test_with_a_universe_the_field_lines_do_not_depend_on_the_lineups():
    """The field's cover players are drawn for its mean and the tail enters at expected points, even when a
    lineup holds a tail player: lineups with and without tail players see the same lines, draw for draw."""
    field = a_field(SPECS)
    cover = set(field.cover)
    tail = {p.id for k, p in enumerate(field.players) if k not in cover}
    assert tail  # the synthetic field has a tail
    lineups = lineups_from(SPECS, 40)
    with_tail = [lu for lu in lineups if any(n in tail for n in lu)][:5]
    without = [lu for lu in lineups if not any(n in tail for n in lu)][:5]
    assert with_tail and without
    universe = tuple(SPECS.values())
    a, b = sim(with_tail, universe=universe, field=field), sim(without, universe=universe, field=field)
    np.testing.assert_array_equal(a.cash_lines, b.cash_lines)
    np.testing.assert_array_equal(a.top_lines, b.top_lines)


def test_a_universe_player_the_simulator_cannot_rate_is_left_out_not_an_error():
    broken = S.PlayerSpec("nobody", "K", "T00", "T01", "G0", "K", 5.0)
    lineups = lineups_from(SPECS, 3)
    with_bad = sim(lineups, universe=(*SPECS.values(), broken))
    clean = sim(lineups, universe=tuple(SPECS.values()))
    for a, b in zip(with_bad.lineups, clean.lineups, strict=True):
        assert_same(a, b)


def test_each_game_is_drawn_from_its_own_seed_the_same_in_every_process():
    assert S._game_seed(0, "G1") == [0, zlib.crc32(b"G1")] == [0, 2_940_193_543]
    assert S._game_seed(7, "G1") != S._game_seed(0, "G1") and S._game_seed(0, "G2") != S._game_seed(0, "G1")
    # adding a game leaves the other games' draws alone
    universe_two = {n: s for n, s in SPECS.items() if s.game_id != "G2"}
    two = lineups_from(universe_two, 3)
    universe_two = tuple(universe_two.values())
    a = sim(two, universe=universe_two)
    b = sim(two, universe=tuple(SPECS.values()))
    for x, y in zip(a.lineups, b.lineups, strict=True):
        assert_same(x, y)


# --- the builder -------------------------------------------------------------------------------------------

KICKOFF = dict.fromkeys(SPECS, datetime(2026, 10, 18, 17, 0, tzinfo=UTC))


def swap_players(names):
    return [
        SwapPlayer(n, s.position, s.team, s.opp, s.game_id, 5000, s.projection)
        for n in names
        if (s := SPECS[n])
    ]


def test_the_builder_scores_and_ranks_the_same_lineups_the_same_way_whatever_order_they_arrive_in():
    """`lineup_suggest.score` passes every spec as the universe: shuffled candidates get the same Cash % and
    Top 1%, so the per-QB pick is the same, and a lineup scored among a different set of candidates (a tie
    kept differently) keeps its numbers."""
    field = a_field(SPECS)
    lineups = [swap_players(lu) for lu in lineups_from(SPECS, 60)]

    def scored(lus):
        return ls.score(lus, SPECS, KICKOFF, cash_line=0, gpp_target=0, n_sims=N_SIMS, seed=0, field=field)

    base = {ls.lineup_key(s): s for s in scored(lineups)}
    picks = [ls.lineup_key(s) for s in ls.choose_per_qb(list(base.values()), key=ls.top1_key)]
    for k in range(3):
        order = list(lineups)
        random.Random(k).shuffle(order)
        again = scored(order)
        assert {ls.lineup_key(s): s for s in again} == base
        assert [ls.lineup_key(s) for s in ls.choose_per_qb(again, key=ls.top1_key)] == picks
    subset = scored(lineups[::3])
    for s in subset:
        assert s == base[ls.lineup_key(s)]
