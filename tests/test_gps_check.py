import pandas as pd

from dfs.gps_check import GPS_IMPLIED_MISMATCH_PTS, find_gps_mismatches


def _games(rows):
    return pd.DataFrame(rows, columns=["Away", "Home", "Spread", "Total"])


def _gps(rows):
    return pd.DataFrame(rows, columns=["Team", "ImpliedTotal", "GPS"])


# Vegas: NE @ JAX total 46.5, JAX -11.5 (home favoured => positive spread) -> JAX 29.0, NE 17.5
# Vegas: KC @ MIA total 47.5, KC -1.5 (home MIA is the dog => negative) -> KC 24.5, MIA 23.0
GAMES = _games([("NE", "JAX", 5.75, 46.5), ("KC", "MIA", -0.75, 47.5)])
# Worksheet agrees with Vegas exactly for both:
GPS_OK = _gps([("JAX", 26.125, 3), ("NE", 20.375, 3), ("MIA", 24.125, 3), ("KC", 23.375, 3)])


def test_threshold_is_the_specified_1_5_points():
    assert GPS_IMPLIED_MISMATCH_PTS == 1.5


def test_a_worksheet_that_matches_vegas_flags_nothing():
    assert find_gps_mismatches(GPS_OK, GAMES) == []


def test_two_swapped_games_are_both_flagged_and_named():
    """The real failure: the article swapped two pairs of rows, so each game's
    implied totals belong to the OTHER game."""
    swapped = _gps([("JAX", 24.125, 3), ("NE", 23.375, 3), ("MIA", 26.125, 3), ("KC", 20.375, 3)])
    found = find_gps_mismatches(swapped, GAMES)
    assert {m.game for m in found} == {"NE @ JAX", "KC @ MIA"}
    assert all(m.worst_miss > 1.5 for m in found)


def test_a_miss_at_or_under_the_threshold_is_not_flagged():
    near = _gps([("JAX", 26.125 + 1.5, 3), ("NE", 20.375, 3), ("MIA", 24.125, 3), ("KC", 23.375, 3)])
    assert find_gps_mismatches(near, GAMES) == []
    just_over = _gps([("JAX", 26.125 + 1.6, 3), ("NE", 20.375, 3), ("MIA", 24.125, 3), ("KC", 23.375, 3)])
    assert [m.game for m in find_gps_mismatches(just_over, GAMES)] == ["NE @ JAX"]


def test_a_game_missing_a_team_or_a_line_is_skipped_not_flagged():
    partial = _gps([("JAX", 99.0, 3)])  # no NE row
    assert find_gps_mismatches(partial, GAMES) == []
    no_line = _games([("NE", "JAX", None, None)])
    assert find_gps_mismatches(GPS_OK, no_line) == []


def test_team_code_drift_is_normalized():
    gps = _gps([("JAC", 26.125, 3), ("NE", 20.375, 3)])
    games = _games([("NE", "JAX", 5.75, 46.5)])
    assert find_gps_mismatches(gps, games) == []
