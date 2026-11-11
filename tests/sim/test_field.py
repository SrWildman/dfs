"""Relative lines: the field mean in every simulation, the lines measured from it, the ownership curve, the
tail, determinism. Hand-built ownership tables; only the committed distribution tables are needed."""

import numpy as np
import pandas as pd
import pytest

from dfs.sim import field as F
from dfs.sim import simulate as S
from dfs.sim.correlation import CorrelationLookup


def lookup(*rows):
    """A tiny correlation table: (relation, role_a, role_b, rho)."""
    table = pd.DataFrame(
        {
            "relation": [r[0] for r in rows],
            "role_a": [r[1] for r in rows],
            "role_b": [r[2] for r in rows],
            "total_bucket": "all",
            "rho": [r[3] for r in rows],
            "n": 1000,
            "ci_lo": [r[3] - 0.05 for r in rows],
            "ci_hi": [r[3] + 0.05 for r in rows],
        }
    )
    return CorrelationLookup(table)


def P(pid, position, role, projection, team=None, game=None):
    """Each player on his own team in his own game unless told otherwise: independent of everyone."""
    return S.PlayerSpec(pid, position, team or f"T_{pid}", "ZZZ", game or f"G_{pid}", role, projection)


# --- the identity: field mean = sum own_i * points_i ------------------------------------------------


def hand_built():
    specs = [
        P("qb_a", "QB", "QB1", 20.0),
        P("qb_b", "QB", "QB1", 16.0),
        P("rb_a", "RB", "RB1", 15.0),
        P("rb_b", "RB", "RB1", 11.0),
        P("wr_a", "WR", "WR1", 14.0),
        P("wr_b", "WR", "WR1", 9.0),
    ]
    own = {"qb_a": 0.30, "qb_b": 0.10, "rb_a": 0.25, "rb_b": 0.05, "wr_a": 0.20, "wr_b": 0.10}
    return specs, own


def test_ownership_is_normalised_to_one_lineups_worth():
    w = F.field_weights(np.array([0.30, 0.10, 0.25, 0.05, 0.20, 0.10]))
    assert w.sum() == pytest.approx(9.0)
    np.testing.assert_allclose(
        w / 9.0, np.array([0.30, 0.10, 0.25, 0.05, 0.20, 0.10]) / 1.0
    )  # shares sum to 1
    with pytest.raises(ValueError):
        F.normalise(np.zeros(3))


def test_the_field_mean_is_exactly_the_ownership_weighted_sum_of_the_draws():
    specs, own = hand_built()
    fld = F.build_field(specs, own, cash_offset=0.6, top_offset=63.2, coverage=1.0)  # no tail: all simulated
    assert len(fld.cover) == len(fld.players)
    # one single-player lineup per player: the scores ARE each player's draws
    res = S.simulate_lineups(
        [[p] for p in fld.players], n_sims=3000, seed=4, correlations=lookup(), keep_scores=True, field=fld
    )
    expected = res.scores @ fld.cash_weights  # sum_i own_i * X_i, own normalised to sum 9
    np.testing.assert_allclose(res.cash_lines - 0.6, expected, rtol=0, atol=1e-9)
    np.testing.assert_allclose(res.top_lines - 63.2, res.scores @ fld.top_weights, rtol=0, atol=1e-9)
    assert res.lines == "relative" and res.field_extra_players == 0
    assert res.field_mean == pytest.approx(expected.mean())
    # the field mean is on the scale of nine players' points: nine players' worth of mean projection
    assert 80 < res.field_mean < 140


def test_the_tail_enters_at_expected_points_so_the_field_mean_is_unbiased():
    specs, own = hand_built()
    full = F.build_field(specs, own, cash_offset=0, top_offset=0, coverage=1.0)
    cut = F.build_field(specs, own, cash_offset=0, top_offset=0, coverage=0.7)
    assert 0 < len(cut.cover) < len(cut.players)  # a real tail
    a = S.simulate_lineups([[specs[0]]], n_sims=60000, seed=2, correlations=lookup(), field=full)
    b = S.simulate_lineups([[specs[0]]], n_sims=60000, seed=2, correlations=lookup(), field=cut)
    assert a.field_mean == pytest.approx(b.field_mean, abs=0.8)  # the same slate to within sampling noise
    assert b.field_extra_players == len({p.id for p in cut.cover_players} - {specs[0].id})
    assert any("more at their expected points" in n for n in b.notes)
    assert not any("more at their expected points" in n for n in a.notes)


def test_expected_points_is_the_mean_of_the_simulated_distribution():
    specs, _ = hand_built()
    exp = F.expected_points(specs)
    res = S.simulate_lineups([[p] for p in specs], n_sims=80000, seed=3, correlations=lookup())
    for e, stats in zip(exp, res.lineups, strict=True):
        assert e == pytest.approx(stats.mean, rel=0.01)
    assert F.expected_points([P("zero", "WR", "WR1", 0.0)])[0] == 0.0


# --- the lines move with the slate ---------------------------------------------------------------------


def test_a_lineup_equal_to_the_fields_chalk_build_is_a_coin_flip_at_offset_zero():
    # six interchangeable players per position, each owned 1/2 of a slot: the chalk build is one of each pair.
    specs, own, chalk = [], {}, []
    for pos, role, proj, slots in (
        ("QB", "QB1", 18.0, 1),
        ("RB", "RB1", 13.0, 3),
        ("WR", "WR1", 13.0, 3),
        ("TE", "TE1", 9.0, 1),
        ("DST", "DST", 7.0, 1),
    ):
        for k in range(slots * 2):
            p = P(f"{pos}{k}", pos, role, proj)
            specs.append(p)
            own[p.id] = 0.5 / 9  # each of a slot's two interchangeable players is owned half the time
            if k % 2 == 0:
                chalk.append(p)
    fld = F.build_field(specs, own, cash_offset=0.0, top_offset=0.0, coverage=1.0)
    assert fld.cash_weights.sum() == pytest.approx(9.0)
    res = S.simulate_lineups([chalk], n_sims=40000, seed=7, correlations=lookup(), field=fld)
    # lineup - field mean = sum over slots of (X_chosen - X_other) / 2: symmetric about 0 whatever the shape
    assert res.lineups[0].p_cash == pytest.approx(0.5, abs=0.015)
    assert res.lineups[0].p_gpp == pytest.approx(0.5, abs=0.015)  # offset 0 for the top line too


def test_the_offsets_move_the_lines_one_for_one():
    specs, own = hand_built()
    lineup = [specs[0], specs[2], specs[4]]
    # (a three-player lineup against a nine-player field: negative offsets keep the chances off zero)
    base = F.build_field(specs, own, cash_offset=-70.0, top_offset=-70.0, coverage=1.0)
    moved = F.build_field(specs, own, cash_offset=-60.0, top_offset=-45.0, coverage=1.0)
    kw = dict(n_sims=4000, seed=1, correlations=lookup(), keep_scores=True)
    a = S.simulate_lineups([lineup], field=base, **kw)
    b = S.simulate_lineups([lineup], field=moved, **kw)
    np.testing.assert_allclose(b.cash_lines - a.cash_lines, 10.0)
    np.testing.assert_allclose(b.top_lines - a.top_lines, 25.0)
    assert 0 < b.lineups[0].p_cash < a.lineups[0].p_cash < 1 and b.lineups[0].p_gpp < a.lineups[0].p_gpp


def steady_and_boom():
    """The same nine marginals twice: independent (steady) and as three correlated QB-WR pairs (boom). Equal
    means, the boom lineup has the larger spread."""

    def build(tag, stacked):
        out = []
        for k in range(3):
            qb_game = f"{tag}G{k}" if stacked else f"{tag}Gq{k}"
            wr_game = f"{tag}G{k}" if stacked else f"{tag}Gw{k}"
            out.append(P(f"{tag}qb{k}", "QB", "QB1", 18.0, team=f"{tag}Q{k}", game=qb_game))
            out.append(P(f"{tag}wr{k}", "WR", "WR1", 14.0, team=f"{tag}Q{k}", game=wr_game))
            out.append(P(f"{tag}rb{k}", "RB", "RB1", 12.0, team=f"{tag}R{k}", game=f"{tag}Gr{k}"))
        return out

    return build("s", False), build("b", True)


def test_variance_is_rewarded_by_a_fixed_line_above_the_median_but_not_by_the_relative_line():
    steady, boom = steady_and_boom()
    look = lookup(("same_team", "QB1", "WR1", 0.6))
    kw = dict(n_sims=40000, seed=5, correlations=look)
    base = S.simulate_lineups([steady, boom], cash_line=1e9, gpp_target=1e9, **kw)
    s0, b0 = base.lineups
    assert s0.mean == pytest.approx(b0.mean, abs=1.0) and b0.sd > s0.sd + 3  # equal mean, more spread
    # the old way: a line well above the median (like Sam's 147.6 against a typical 129) pays the risk-taker
    old = S.simulate_lineups([steady, boom], cash_line=s0.mean + 25, gpp_target=1e9, **kw)
    assert old.lineups[1].p_cash > old.lineups[0].p_cash + 0.03
    # the field the same size and shape as either lineup, independent of both
    fld_specs = [
        S.PlayerSpec(f"f_{p.id}", p.position, p.team + "f", "ZZZ", "F_" + p.id, p.role, p.projection)
        for p in steady
    ]
    fld = F.build_field(fld_specs, {p.id: 1 / 9 for p in fld_specs}, cash_offset=0.0, top_offset=63.2)
    new = S.simulate_lineups([steady, boom], field=fld, **kw)
    steady_p, boom_p = (s.p_cash for s in new.lineups)
    assert steady_p == pytest.approx(0.5, abs=0.02)
    assert boom_p <= steady_p + 0.01  # the high-variance lineup no longer beats the low-variance one


def test_a_lineup_sharing_players_with_the_field_moves_with_the_line():
    specs, own = hand_built()
    fld = F.build_field(specs, own, cash_offset=0.0, top_offset=0.0, coverage=1.0)
    held = [specs[0], specs[2], specs[4]]  # the chalk QB, RB and WR: all in the field
    strangers = [  # the same marginals, but players the field does not hold
        S.PlayerSpec(f"x_{p.id}", p.position, p.team + "x", "ZZZ", "X_" + p.id, p.role, p.projection)
        for p in held
    ]
    res = S.simulate_lineups(
        [held, strangers], n_sims=40000, seed=9, correlations=lookup(), keep_scores=True, field=fld
    )
    gap = res.scores - res.cash_lines[:, None]  # lineup minus its line, per draw
    # a shared player's draw is in both the score and the line, so his noise partly cancels
    assert gap[:, 0].std() < gap[:, 1].std() - 2.0
    assert res.scores[:, 0].std() == pytest.approx(res.scores[:, 1].std(), rel=0.03)  # the same lineup spread


# --- the ownership curve, per contest type -------------------------------------------------------------


def test_an_empty_curve_is_the_identity_and_a_curve_maps_projected_to_expected_ownership():
    own = np.array([0.02, 0.10, 0.15, 0.30])
    np.testing.assert_allclose(F.apply_curve(own), own)
    curve = [(0.05, 0.05), (0.15, 0.39)]  # the chalk is much chalkier in a double-up
    mapped = F.apply_curve(own, curve)
    assert mapped[2] == pytest.approx(0.39) and mapped[0] == pytest.approx(
        0.02
    )  # below the first knot: toward (0, 0)
    assert mapped[3] > mapped[2] > mapped[1] > mapped[0]  # monotone
    assert mapped[-1] <= 1.0
    with pytest.raises(ValueError):
        F.apply_curve(own, [(0.2, 0.3), (0.2, 0.4)])  # a projected value twice
    with pytest.raises(ValueError):
        F.apply_curve(own, [(0.1, 0.5), (0.2, 0.3)])  # expected must not decrease


def test_each_contest_type_has_its_own_weights_and_so_its_own_line():
    specs, own = hand_built()
    fld = F.build_field(
        specs,
        own,
        cash_offset=0.6,
        top_offset=63.2,
        cash_curve=[(0.1, 0.35), (0.3, 0.9)],  # double-ups: far chalkier
        coverage=1.0,
    )
    assert not np.allclose(fld.cash_weights, fld.top_weights)
    assert fld.cash_weights.sum() == pytest.approx(9.0) and fld.top_weights.sum() == pytest.approx(9.0)
    res = S.simulate_lineups(
        [[specs[0]]], n_sims=4000, seed=1, correlations=lookup(), keep_scores=True, field=fld
    )
    assert not np.allclose(res.cash_lines - 0.6, res.top_lines - 63.2)


def test_the_cover_holds_at_least_the_asked_share_of_the_ownership_highest_first():
    w = F.field_weights(np.array([0.30, 0.25, 0.20, 0.10, 0.05, 0.04, 0.03, 0.02, 0.01]))
    chosen = F.cover_indices(w, 0.95)
    assert w[chosen].sum() / w.sum() >= 0.95
    assert chosen == sorted(chosen, key=lambda i: -w[i]) and chosen == list(range(len(chosen)))
    assert w[chosen[:-1]].sum() / w.sum() < 0.95  # no more than needed
    assert F.cover_indices(w, 1.0) == list(range(len(w)))


def test_players_without_a_spec_or_an_ownership_number_are_left_out_not_guessed():
    specs, own = hand_built()
    own = {**own, "ghost": 0.4, "wr_b": float("nan")}  # a name with no spec; a blank for a rated player
    fld = F.build_field(specs, own, cash_offset=0, top_offset=0)
    assert {p.id for p in fld.players} == {"qb_a", "qb_b", "rb_a", "rb_b", "wr_a"}
    assert fld.cash_weights.sum() == pytest.approx(9.0)
    assert F.build_field(specs, {}, cash_offset=0, top_offset=0) is None
    assert F.build_field(specs, dict.fromkeys(own, 0.0), cash_offset=0, top_offset=0) is None
    assert not F.has_ownership({"a": float("nan"), "b": 0.0, "c": None}) and F.has_ownership({"a": 0.01})


def test_the_model_does_not_depend_on_the_order_the_specs_came_in():
    specs, own = hand_built()
    a = F.build_field(specs, own, cash_offset=0, top_offset=0)
    b = F.build_field(list(reversed(specs)), own, cash_offset=0, top_offset=0)
    assert [p.id for p in a.players] == [p.id for p in b.players]
    np.testing.assert_array_equal(a.cash_weights, b.cash_weights)


# --- determinism --------------------------------------------------------------------------------------


def test_relative_lines_are_deterministic_under_the_seed_and_move_with_it():
    specs, own = hand_built()
    fld = F.build_field(specs, own, cash_offset=-60.0, top_offset=-45.0, coverage=0.8)
    lineup = [specs[0], specs[2], specs[4]]

    def run(seed):
        return S.simulate_lineups(
            [lineup, specs[1:4]], n_sims=5000, seed=seed, correlations=lookup(), field=fld
        )

    one, two, other = run(3), run(3), run(4)
    assert [(s.p_cash, s.p_gpp, s.mean) for s in one.lineups] == [
        (s.p_cash, s.p_gpp, s.mean) for s in two.lineups
    ]
    assert (one.field_mean, one.p_any_cash, one.expected_cashing) == (
        two.field_mean,
        two.p_any_cash,
        two.expected_cashing,
    )
    assert 0 < one.lineups[0].p_cash < 1
    assert one.lineups[0].p_cash != other.lineups[0].p_cash


def test_without_a_field_nothing_changes_and_the_result_says_fixed():
    specs, _ = hand_built()
    res = S.simulate_lineups([specs[:3]], n_sims=2000, seed=1, correlations=lookup(), cash_line=40.0)
    assert (
        res.lines == "fixed"
        and res.field_mean is None
        and res.cash_line == 40.0
        and res.field_extra_players == 0
    )
    assert res.cash_lines is None and res.top_lines is None


def test_a_field_player_who_differs_from_the_lineups_copy_of_him_is_refused():
    specs, own = hand_built()
    fld = F.build_field(specs, own, cash_offset=0, top_offset=0)
    changed = S.PlayerSpec("qb_a", "QB", "T_qb_a", "ZZZ", "G_qb_a", "QB1", 25.0)
    with pytest.raises(ValueError, match="appears twice"):
        S.simulate_lineups([[changed]], n_sims=100, correlations=lookup(), field=fld)
