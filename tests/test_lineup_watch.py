"""Lineups in more than half of the set, and the GPP ownership meter."""

from dfs import lineup_watch as lw
from dfs.sheet_style import _overuse_formula, _own_meter_formula, _totals_check_formula


def _lineup(*extra):
    base = ["Bijan", "London", "Q", "R2", "W1", "W2", "TE", "FX", "DST"]
    return [*extra, *base[len(extra) :]] if extra else base


def test_a_player_in_more_than_half_of_the_built_lineups_is_listed_most_used_first():
    sets = [_lineup(), _lineup(), _lineup(), ["A", "B", "C", "D", "E", "F", "G", "H", "I"]]
    over = lw.overused(sets)
    assert over[0][1:] == (3, 4) and {n for n, _, _ in over} >= {"Bijan", "London"}
    assert "A" not in {n for n, _, _ in over}
    assert "In more than half of your built lineups: " in lw.overuse_line(over)


def test_exactly_half_is_not_over_half_and_too_few_lineups_is_not_judged():
    two_of_four = [_lineup(), _lineup(), ["x"] * 9, ["y"] * 9]
    assert "Bijan" not in {n for n, _, _ in lw.overused(two_of_four)}
    assert lw.overused([_lineup(), _lineup()]) == []  # two lineups: everything shared is "100%"
    assert lw.overused([_lineup(), _lineup(), _lineup()])  # three is enough
    assert lw.overuse_line([]) == ""
    incomplete = [["Bijan"] * 3, ["Bijan"] * 3, ["Bijan"] * 3]
    assert lw.overused(incomplete) == []  # only complete lineups count


def test_the_ownership_meter_judges_gpp_lineups_only():
    assert lw.own_meter(2.07, "gpp") == "Own sum 207%: over the 125%-145% GPP target"
    assert lw.own_meter(0.90, "gpp") == "Own sum 90%: under the 125%-145% GPP target"
    assert lw.own_meter(1.30, "gpp") == ""  # inside the band
    assert lw.own_meter(4.0, "cash") == ""  # a cash lineup in a GPP is on purpose: no meter
    assert lw.own_meter(None, "gpp") == "" and lw.own_meter(float("nan"), "gpp") == ""


def test_the_sheet_formulas_carry_the_same_thresholds_and_ride_after_the_other_checks():
    blocks = [(2, 10), (15, 23), (28, 36)]
    over = _overuse_formula(2, 10, blocks)
    assert ">0.5" in over and "n<3" in over and "(COUNTA($A$15:$A$23)=9)" in over
    meter = _own_meter_formula(11, "R")
    assert '$A11="GPP"' in meter and "R11<1.25" in meter and "R11>1.45" in meter
    both = _totals_check_formula(2, 10, 11, "K", overuse=over, own_meter=meter)
    assert over in both and meter in both
    assert _totals_check_formula(2, 10, 11, "K", overuse=None, own_meter=None) == _totals_check_formula(
        2, 10, 11, "K"
    )
