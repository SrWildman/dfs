"""Heavy chalk in none of the lineups: the warning Sam asked for after missing a 70%-owned player."""

import pandas as pd

from dfs import chalk_watch as cw


def _edge():
    return pd.DataFrame(
        {
            "Name": ["Simone", "Mid Guy", "Hurt Guy", "Held Guy", "Late Guy"],
            "Position": ["RB", "WR", "RB", "QB", "WR"],
            "Team": ["A", "B", "C", "D", "E"],
            "Own%": [0.70, 0.18, 0.60, 0.40, 0.55],
            "Avail": ["", "", "OUT", "", ""],
        }
    )


def test_chalk_in_none_of_the_lineups_is_listed_most_owned_first_without_the_out_or_the_held():
    found = cw.missing_chalk(_edge(), [["Held Guy"], ["Held Guy", "Mid Guy"]], threshold=0.15)
    assert [c.name for c in found] == [
        "Simone",
        "Late Guy",
    ]  # Hurt Guy is OUT, Mid Guy is held, Held Guy is held
    assert found[0].own == 0.70


def test_the_gpp_threshold_is_higher_than_the_cash_one():
    lineups = [["Held Guy"]]
    cash = cw.missing_chalk(_edge(), lineups, threshold=cw.CASH_PROJECTED_OWN)
    gpp = cw.missing_chalk(_edge(), lineups, threshold=cw.GPP_PROJECTED_OWN)
    assert "Mid Guy" in [c.name for c in cash] and "Mid Guy" not in [c.name for c in gpp]


def test_a_game_that_has_started_is_not_a_warning_and_no_lineups_means_no_warning():
    assert [
        c.name for c in cw.missing_chalk(_edge(), [["Held Guy"]], threshold=0.15, started={"Simone"})
    ] == [
        "Late Guy",
        "Mid Guy",
    ]
    assert cw.missing_chalk(_edge(), [], threshold=0.15) == []


def test_the_sentence():
    line = cw.warning_line(cw.missing_chalk(_edge(), [["Held Guy"]], threshold=0.5), kind="cash")
    assert line.startswith("Heavy chalk in none of your cash lineups: Simone (RB A, 70% projected)")
    assert "chalkier than the projection" in line
    assert cw.warning_line([], kind="gpp") == ""
