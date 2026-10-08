"""Late swap scored by the simulator: the change in P(cash) and P(GPP) beside the projection delta."""

import pytest

from dfs.late_swap_report import format_suggestions
from dfs.late_swap_search import LineupState, LineupSuggestions, OpenSlot, Swap, SwapPlayer
from dfs.late_swap_sim import OVERSAMPLE, SimSettings, attach_probabilities, swap_names
from dfs.models import ROSTER_SLOTS
from dfs.sim.simulate import PlayerSpec

SLOT = {name: i for i, name in enumerate(ROSTER_SLOTS)}


def sp(name, pos, team, opp, role, projection, game="G1"):
    return PlayerSpec(name, pos, team, opp, game, role, projection, 5000)


def swp(name, pos, team="DEN", opp="KC", value=10.0):
    return SwapPlayer(name, pos, team, opp, "G1", 5000, value)


SPECS = {
    s.id: s
    for s in [
        sp("QB", "QB", "DEN", "KC", "QB1", 20.0),
        sp("RB1", "RB", "DEN", "KC", "RB1", 15.0),
        sp("RB2", "RB", "KC", "DEN", "RB1", 13.0),
        sp("WR1", "WR", "DEN", "KC", "WR1", 16.0),
        sp("WR2", "WR", "DEN", "KC", "WR2", 12.0),
        sp("WR3", "WR", "KC", "DEN", "WR1", 14.0),
        sp("TE", "TE", "KC", "DEN", "TE1", 9.0),
        sp("FLEX", "RB", "KC", "DEN", "RB2", 10.0),
        sp("DST", "DST", "DEN", "KC", "DST", 7.0),
        sp("Star WR", "WR", "DEN", "KC", "WR2", 20.0),
        sp("Good WR", "WR", "DEN", "KC", "WR3", 15.0),
        sp("Safe WR", "WR", "KC", "DEN", "WR2", 12.5),
        sp("Cheap TE", "TE", "KC", "DEN", "TE1", 11.0),
    ]
}
LINEUP = [SPECS[n] for n in ("QB", "RB1", "RB2", "WR1", "WR2", "WR3", "TE", "FLEX", "DST")]


def _suggestions():
    wr2 = swp("WR2", "WR")
    state = LineupState(cap=50000, fixed={}, open=[OpenSlot(SLOT["WR"], "WR", wr2)])
    slot = state.open[0].index
    star, good, safe = (
        swp("Star WR", "WR", value=20.0),
        swp("Good WR", "WR", value=15.0),
        swp("Safe WR", "WR", value=12.5),
    )
    singles = {
        slot: [
            Swap({slot: (wr2, safe)}, 0.5, 100),
            Swap({slot: (wr2, star)}, 8.0, 100),
            Swap({slot: (wr2, good)}, 3.0, 100),
        ]
    }
    te = swp("TE", "TE")
    pair = Swap(
        {SLOT["WR"]: (wr2, good), SLOT["TE"]: (te, swp("Cheap TE", "TE", "KC", "DEN", 11.0))}, 5.0, 200
    )
    return LineupSuggestions(state=state, refill=pair, pairs=[pair], singles=singles)


def test_swap_names_lists_who_leaves_and_who_arrives_and_an_empty_slot_has_nobody_leaving():
    sug = _suggestions()
    out, new = swap_names(sug.pairs[0])
    assert sorted(out) == ["TE", "WR2"] and sorted(new) == ["Cheap TE", "Good WR"]
    empty = Swap({0: (None, swp("Star WR", "WR"))}, 1.0, 0)
    assert swap_names(empty) == ([], ["Star WR"])


def test_every_swap_found_gets_both_changes_including_the_two_for_two_and_the_refill():
    sug = attach_probabilities(_suggestions(), LINEUP, SPECS, SimSettings(60.0, 80.0, "cash"), top=3)
    swaps = [sug.refill, *sug.pairs, *(s for lst in sug.singles.values() for s in lst)]
    assert all(s.delta_p_cash is not None and s.delta_p_gpp is not None for s in swaps)
    star = next(s for lst in sug.singles.values() for s in lst if swap_names(s)[1] == ["Star WR"])
    assert star.delta_p_cash > 0 and star.delta_p_gpp >= 0  # a 20-point WR for a 12-point one helps both
    pair = sug.pairs[0]
    assert pair.delta_p_cash != star.delta_p_cash  # the 2-for-2 is scored on its own


def test_swaps_are_ranked_by_the_goal_probability_then_the_projection_delta():
    base = attach_probabilities(_suggestions(), LINEUP, SPECS, SimSettings(60.0, 80.0, "cash"), top=3)
    singles = next(iter(base.singles.values()))
    order = [swap_names(s)[1][0] for s in singles]
    assert order[0] == "Star WR"  # the biggest P(cash) gain first, not the order the search handed over
    gains = [s.delta_p_cash for s in singles]
    assert gains == sorted(gains, reverse=True)
    gpp = attach_probabilities(_suggestions(), LINEUP, SPECS, SimSettings(60.0, 80.0, "gpp"), top=3)
    gpp_gains = [s.delta_p_gpp for s in next(iter(gpp.singles.values()))]
    assert gpp_gains == sorted(gpp_gains, reverse=True)


def test_the_lists_are_cut_to_top_after_ranking_and_oversampling_is_larger_than_one():
    sug = attach_probabilities(_suggestions(), LINEUP, SPECS, SimSettings(60.0, 80.0, "cash"), top=2)
    assert len(next(iter(sug.singles.values()))) == 2
    assert OVERSAMPLE > 1


def test_a_swap_with_an_unrated_player_keeps_none_and_sorts_after_the_rated_ones():
    sug = _suggestions()
    slot = sug.state.open[0].index
    sug.singles[slot].append(Swap({slot: (swp("WR2", "WR"), swp("Ghost", "WR", value=99.0))}, 99.0, 0))
    out = attach_probabilities(sug, LINEUP, SPECS, SimSettings(60.0, 80.0, "cash"), top=4)
    ranked = next(iter(out.singles.values()))
    assert swap_names(ranked[-1])[1] == ["Ghost"] and ranked[-1].delta_p_cash is None


def test_the_probabilities_do_not_jitter_between_runs():
    one = attach_probabilities(_suggestions(), LINEUP, SPECS, SimSettings(60.0, 80.0, "cash"), top=3)
    two = attach_probabilities(_suggestions(), LINEUP, SPECS, SimSettings(60.0, 80.0, "cash"), top=3)
    key = lambda s: [(x.delta_p_cash, x.delta_p_gpp) for x in next(iter(s.singles.values()))]  # noqa: E731
    assert key(one) == key(two)


def test_the_report_shows_both_changes_in_percentage_points_beside_the_projection_delta():
    sug = attach_probabilities(_suggestions(), LINEUP, SPECS, SimSettings(60.0, 80.0, "cash"), top=3)
    text = "\n".join(format_suggestions(sug, metric="ProjPts", goal="cash"))
    assert "Ranked by change in P(cash), then ProjPts" in text
    assert "pts, $100 left, P(cash) +" in text and "P(GPP) +" in text and "%" in text
    plain = "\n".join(format_suggestions(_suggestions(), metric="ProjPts"))
    assert "P(cash)" not in plain and "Ranked by ProjPts." in plain  # unscored swaps read as before


def test_the_changes_are_probability_fractions():
    sug = attach_probabilities(_suggestions(), LINEUP, SPECS, SimSettings(60.0, 80.0, "cash"), top=3)
    for s in next(iter(sug.singles.values())):
        assert -1.0 <= s.delta_p_cash <= 1.0 and -1.0 <= s.delta_p_gpp <= 1.0
    assert pytest.approx(0.0, abs=1.0) == sug.refill.delta_p_cash
