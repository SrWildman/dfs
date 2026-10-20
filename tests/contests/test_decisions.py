"""The Monday post-mortem on the hand-built 10-entry contest (`contest-standings-900000001.csv`).

Sam's stand-in `test_me` has two entries: E6 (170.00, place 6) and E9 (140.00, place 9). The contest is a
Double Up paying 4 places, so the cash line is the 4th score, 190.50. Worked by hand:

- E6 misses by 20.5 (170 - 190.5), E9 by 50.5; neither lineup is held by anyone else.
- The biggest swing is E9's DJ Moore (WR $5,800, 60% owned, 13.6 points) against the field's most-owned
  receiver within $400, Christian Watson ($5,700, 70%, 27.2 points): -13.6 points.
"""

from dataclasses import replace
from pathlib import Path

import pandas as pd
import pytest

from dfs.contests import decisions as D
from dfs.contests import standings as S

DIR = Path(__file__).parent / "fixtures"
SERIES = "NFL Tiny $2 Double Up [Single Entry]"


@pytest.fixture(scope="module")
def parsed():
    return S.parse_standings(
        (DIR / "standings" / "contest-standings-900000001.csv").read_bytes(),
        contest_id="900000001",
        places_paid=4,
    )


def _input(parsed, *, contest_id="900000001", places_paid=4, fmt="Double Up", lineups=None):
    standings = parsed
    if lineups is not None:  # a sibling: the same field minus Sam's entries, profiled at its own places paid
        entries = parsed.entries[~parsed.entries["entry_name"].str.startswith("test_me")]
        standings = replace(
            parsed,
            contest_id=contest_id,
            lineups=lineups,
            profile=S.score_profile(entries, contest_id=contest_id, places_paid=places_paid),
        )
    return D.ContestInput(standings, 2025, 6, SERIES, fmt, places_paid, pd.read_csv(DIR / "players_week.csv"))


def test_format_class_and_short_series():
    assert [D.format_class(f) for f in ("H2H", "50/50", "Double Up", "SE GPP", "N-max GPP")] == [
        "cash",
        "cash",
        "cash",
        "gpp",
        "gpp",
    ]
    assert D.format_class("Multiplier") == "" and D.format_class("Showdown") == ""
    assert D.short_series("NFL GIANT $25 Double Up [Single Entry]") == "GIANT $25 Double Up"
    assert D.short_series("NFL Single Entry $5 Double Up") == "Single Entry $5 Double Up"


def test_each_of_his_entries_is_scored_against_the_cash_line_and_the_field(parsed):
    rows = D.entry_rows([_input(parsed)], "test_me").set_index("points")
    e6, e9 = rows.loc[170.0], rows.loc[140.0]
    assert (e6.place, e6.pct, e6.cashed, e6.margin, e6.copies) == (6, 0.6, False, -20.5, 0)
    assert (e9.place, e9.pct, e9.cashed, e9.margin) == (9, 0.9, False, -50.5)
    assert (e6.cls, e6.entrants, e6.places_paid, e6.cash_line) == ("cash", 10, 4, 190.5)


def test_copies_count_the_other_entries_holding_exactly_his_nine(parsed):
    # E1 and E2 are the same nine players; give Sam that lineup twice: four identical, three others each
    lineups = parsed.lineups.copy()
    first = lineups.iloc[0]["slots"]
    lineups.loc[lineups["entry_name"] == "test_me", "slots"] = pd.Series(
        [first, first], index=lineups.index[lineups["entry_name"] == "test_me"]
    )
    rows = D.entry_rows([_input(replace(parsed, lineups=lineups))], "test_me")
    assert list(rows["copies"]) == [3, 3]


def test_a_contest_that_is_not_graded_or_not_classic_is_skipped(parsed):
    assert D.entry_rows([_input(parsed, fmt="Multiplier")], "test_me").empty
    assert D.entry_rows([_input(parsed, fmt="Double Up")], "nobody_here").empty


def test_the_contest_choice_columns_count_the_other_contests_the_lineup_would_have_cashed_in(parsed):
    others = parsed.lineups[~parsed.lineups["entry_name"].str.startswith("test_me")]
    sibling = _input(parsed, contest_id="900000009", places_paid=6, lineups=others)
    rows = D.entry_rows([_input(parsed), sibling], "test_me").set_index("points")
    # E6's 170 places 6th among the other eight (five score more): inside 6 paid places. E9's 140 is 9th.
    assert (rows.loc[170.0, "sib_total"], rows.loc[170.0, "sib_cashed"]) == (1, 1)
    assert (rows.loc[140.0, "sib_total"], rows.loc[140.0, "sib_cashed"]) == (1, 0)
    assert len(rows) == 2  # the sibling holds none of his entries


def test_the_biggest_swing_is_the_largest_gap_to_the_fields_favourite_in_the_price_band(parsed):
    swings = D.biggest_swing([_input(parsed)], "test_me")
    assert len(swings) == 1
    s = swings.iloc[0]
    assert (s.player, s.alt) == ("DJ Moore", "Christian Watson")
    assert (s.player_own, s.alt_own) == pytest.approx((0.6, 0.7))
    assert (s.player_pts, s.alt_pts, s.swing) == pytest.approx((13.6, 27.2, -13.6))


def test_a_player_who_is_the_fields_favourite_has_no_swing_and_no_frame_means_no_swing(parsed):
    assert D.biggest_swing([replace(_input(parsed), players=None)], "test_me").empty


def test_pool_hit_row_counts_who_beat_calpts():
    scored = pd.DataFrame(
        {
            "season": 2025,
            "week": 6,
            "Status": "scored",
            "Name": ["Josh Allen", "Puka Nacua", "Bench Guy", "Sleeper Pick"],
            "DkActual": [30.0, 10.0, 5.0, 20.0],
            "CalPts": [20.0, 15.0, 4.0, 12.0],
        }
    )
    row = D.pool_hit_row(
        2025, 6, scored, {"Josh Allen", "Puka Nacua", "Bench Guy"}, {"Sleeper Pick", "Absent"}
    )
    assert row == {"season": 2025, "week": 6, "pooled": 3, "pooled_hits": 2, "likes": 1, "likes_hits": 1}
    assert D.pool_hit_row(2025, 7, scored, set(), set()) is None  # an unscored week
