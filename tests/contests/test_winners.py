"""`winners`: what the top 1% of a GPP did that the field did not, on the hand-built 10-entry contest.

Worked by hand from the ten lineups (see `test_field.py` for the table). E1 and E2 are the same lineup and the
only top-1% entries (the best `max(1, ceil(1% of 10))` = 1 place, ties kept).

    E1/E2  QB Love(GB) + Watson, Golden, Kraft + RB Jacobs-side; bring-back yes; DST Jets; left $1,200
    E4     QB Williams(CHI) + Moore, Odunze, Kmet; no bring-back; FLEX Warren; DST Packers; left $5,600
    E5     QB Goff(DET) + St. Brown, LaPorta; RB Braelon Allen with DST Jets (his own team); left $400
    E8     QB Goff, no stack: nobody of DET with him
"""

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from dfs.contests import standings as S
from dfs.contests import winners as W

DIR = Path(__file__).parent / "fixtures"


@pytest.fixture(scope="module")
def lineups():
    parsed = S.parse_standings(
        (DIR / "standings" / "contest-standings-900000001.csv").read_bytes(), contest_id="900000001"
    )
    players = pd.read_csv(DIR / "players_week.csv")
    saved = W.MIN_ENTRIES
    W.MIN_ENTRIES = 1  # a ten-lineup contest would otherwise be left out
    try:
        result = W.contest_lineups(parsed, players)
    finally:
        W.MIN_ENTRIES = saved
    return result


def test_the_top_one_percent_is_the_best_place_with_ties_kept(lineups):
    assert list(lineups["top1"]) == [True, True] + [False] * 8


def test_stacks_use_any_teammate_and_the_old_pass_catcher_definition_side_by_side(lineups):
    e1, e5, e8 = lineups.iloc[0], lineups.iloc[4], lineups.iloc[7]
    assert (e1.stack_any1, e1.stack_any2, e1.stack_wrte2) == (1, 1, 1)
    assert (e5.stack_any1, e5.stack_any2) == (1, 1)  # Goff + St. Brown + LaPorta
    assert (e8.stack_any1, e8.stack_wrte1) == (0, 0)  # Goff alone


def test_bring_back_game_stack_and_rb_with_his_own_dst(lineups):
    e1, e4, e5 = lineups.iloc[0], lineups.iloc[3], lineups.iloc[4]
    assert e1.bringback == 1 and e4.bringback == 0
    assert e4.game_stack == 0
    assert e5.rb_own_dst == 1  # Braelon Allen and the Jets
    assert e1.rb_own_dst == 0 and e4.rb_own_dst == 0


def test_salary_left_and_ownership(lineups):
    assert list(lineups["salary_left"][[0, 3, 4]]) == [1200, 5600, 400]
    assert lineups["own_sum"][0] == pytest.approx(5.5)  # 10 entries: each player's share of 10
    assert lineups["own_product"][0] > lineups["own_product"][7]  # the winner is the chalkier lineup here


def test_a_contest_below_the_minimum_size_or_without_players_gives_no_rows():
    parsed = S.parse_standings(
        (DIR / "standings" / "contest-standings-900000001.csv").read_bytes(), contest_id="900000001"
    )
    assert W.contest_lineups(parsed, pd.read_csv(DIR / "players_week.csv")).empty  # 10 < 700
    assert W.contest_lineups(parsed, None).empty


def test_summarize_pools_lineups_and_reports_differences_and_counts():
    frame = pd.DataFrame(
        {
            "contest_id": ["a"] * 4 + ["b"] * 4,
            "points": range(8),
            "top1": [True, False, False, False] * 2,
            **{k: np.nan for k, _, _ in W.MEASURES},
        }
    )
    frame["bringback"] = [1, 0, 1, 0, 1, 0, 0, 0]
    frame["own_product"] = [1e-8, 1e-6, 4e-6, 1e-4, 3e-8, 2e-6, 5e-6, 1e-4]
    out = W.summarize(frame)
    row = out.table.set_index("measure")
    assert (out.top1_lineups, out.field_lineups, out.contests) == (2, 8, 2)
    assert row.loc["Stack + a bring-back", "top1"] == 1.0
    assert row.loc["Stack + a bring-back", "field"] == pytest.approx(3 / 8)
    assert row.loc["Stack + a bring-back", "diff"] == pytest.approx(5 / 8)
    assert row.loc["Stack + a bring-back", "n_top1"] == 2
    # a skewed product is summarised by its median, not its mean
    assert row.loc["Ownership product of the nine (median)", "field"] == pytest.approx(3.0e-6)
    assert "QB + 2 or more teammates (any position)" not in row.index  # a measure with no data is left out


def test_an_empty_input_is_an_empty_table():
    assert W.summarize(pd.DataFrame()).table.empty
