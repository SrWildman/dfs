"""Field stats on the hand-built 10-entry contest (`contest-standings-900000001.csv`).

The expected numbers below are worked out by hand from the ten lineups, not read back from the code.
Per entry: rank, QB (team) and the pass catchers he is stacked with, FLEX, DST, salary left, bring-back (bb).

    E1   1  Love(GB)+Watson,Golden,Kraft     FLEX Warren RB     DST Jets     left 1,200  bb yes
    E2   1  (the same nine players as E1: a duplicate)
    E3   3  Love(GB)+Watson                  FLEX St. Brown WR  DST Bears    left   500  bb yes
    E4   4  Williams(CHI)+Moore,Odunze,Kmet  FLEX Warren RB     DST Packers  left 5,600  bb no
    E5   5  Goff(DET)+St. Brown,LaPorta      FLEX Swift RB      DST Jets     left   400  bb no
    E6   6  Love(GB)+Watson,Golden,Kraft     FLEX Swift RB      DST Packers  left 2,000  bb yes  (me 1/2)
    E7   7  Williams(CHI)+Moore,Odunze       FLEX Odunze WR     DST Jets     left 6,000  bb no
    E8   8  Goff(DET), no stack               FLEX Kraft TE      DST Bears    left 8,100  bb no
    E9   9  Love(GB)+Watson,Kraft,Golden     FLEX Golden WR     DST Jets     left 1,100  bb yes  (me 2/2)
    E10 10  Williams(CHI)+Moore,Odunze,Kmet  FLEX Warren RB     DST Packers  left   100  bb yes  (Jacobs)

Ownership out of 10 entries: Swift 9, Warren 7, Watson 7, Jacobs 6, Moore 6, then 5s (Love, Golden, Wilson,
Kraft, Jets, Odunze). The five most owned are Swift, Warren, Watson, Jacobs, Moore: a clean cut.
"""

from pathlib import Path

import pandas as pd
import pytest

from dfs.contests import field as F
from dfs.contests import standings as S

DIR = Path(__file__).parent / "fixtures"
SMALL = DIR / "standings" / "contest-standings-900000001.csv"


@pytest.fixture(scope="module")
def parsed():
    return S.parse_standings(SMALL.read_bytes(), contest_id="900000001", places_paid=4)


@pytest.fixture(scope="module")
def players():
    return pd.read_csv(DIR / "players_week.csv")


@pytest.fixture(scope="module")
def result(parsed, players):
    return F.field_stats(parsed, players, me="test_me")


def group(result, name):
    return result.rows.set_index("group").loc[name]


def test_the_five_most_owned_players(parsed):
    assert set(F.chalk_players(parsed.ownership)) == {
        "D'Andre Swift",
        "Jaylen Warren",
        "Christian Watson",
        "Josh Jacobs",
        "DJ Moore",
    }


def test_chalk_ties_are_broken_by_name_so_the_set_is_stable():
    own = pd.DataFrame({"player": ["Zed", "Amy", "Bob", "Cat"], "pct_drafted": [0.5, 0.5, 0.5, 0.1]})
    assert F.chalk_players(own, n=2) == ["Amy", "Bob"]
    assert F.chalk_players(own.iloc[::-1].reset_index(drop=True), n=2) == ["Amy", "Bob"]
    assert F.chalk_players(pd.DataFrame(columns=["player", "pct_drafted"])) == []


def test_all_entries(result):
    row = group(result, "all")
    assert row["n"] == 10 and row["n_unparsed"] == 0 and row["n_resolved"] == 10 and row["n_salary"] == 10
    # QB + 1 pass catcher: everyone but E8 (9 of 10); QB + 2: all but E3 and E8 (8 of 10)
    assert row["qb_stack1"] == pytest.approx(0.9) and row["qb_stack2"] == pytest.approx(0.8)
    # bring-back: E1, E2, E3, E6, E9, E10
    assert row["bringback"] == pytest.approx(0.6)
    # unspent: 1200+1200+500+5600+400+2000+6000+8100+1100+100 = 26,200
    assert row["avg_unspent"] == pytest.approx(2620.0)
    assert row["share_unspent_1k"] == pytest.approx(0.7)  # all but E3 (500), E5 (400), E10 (100)
    # FLEX: RB in E1, E2, E4, E5, E6, E10; WR in E3, E7, E9; TE in E8
    assert (row["flex_rb"], row["flex_wr"], row["flex_te"]) == pytest.approx((0.6, 0.3, 0.1))
    # chalk held: 5+5+3+3+3+4+2+2+4+4 = 35
    assert row["avg_chalk"] == pytest.approx(3.5)
    assert row["dst_with_qb"] == pytest.approx(0.1)  # E6: Packers with Love
    assert row["dst_vs_qb"] == pytest.approx(0.3)  # E3 Bears v Love, E4 and E10 Packers v Williams
    assert row["dup_entries"] == 2 and row["dup_groups"] == 1 and row["dup_share"] == pytest.approx(0.2)
    assert row["top_stacks"] == "GB:50%;CHI:30%;DET:10%"


def test_top_10_percent_is_the_two_copies_of_the_winning_lineup(result):
    row = group(result, "top10")
    assert row["n"] == 2  # ceil(10% of 10) = 1st place, and its tie
    assert (row["qb_stack1"], row["qb_stack2"], row["bringback"]) == (1.0, 1.0, 1.0)
    assert row["avg_unspent"] == 1200 and row["share_unspent_1k"] == 1.0
    assert (row["flex_rb"], row["flex_wr"], row["flex_te"]) == (1.0, 0.0, 0.0)
    assert row["avg_chalk"] == 5.0 and row["dst_with_qb"] == 0.0 and row["dst_vs_qb"] == 0.0
    assert row["dup_entries"] == 2 and row["dup_groups"] == 1 and row["dup_share"] == 1.0
    assert row["top_stacks"] == "GB:100%"


def test_my_lineups_are_measured_the_same_way(result):
    row = group(result, "me")
    assert row["n"] == 2  # test_me (1/2) and (2/2), counters stripped
    assert (row["qb_stack1"], row["qb_stack2"], row["bringback"]) == (1.0, 1.0, 1.0)
    assert row["avg_unspent"] == pytest.approx(1550.0) and row["share_unspent_1k"] == 1.0
    assert (row["flex_rb"], row["flex_wr"], row["flex_te"]) == (0.5, 0.5, 0.0)  # E6 Swift, E9 Golden
    assert row["avg_chalk"] == 4.0
    assert row["dst_with_qb"] == 0.5 and row["dst_vs_qb"] == 0.0
    assert row["dup_entries"] == 0 and row["dup_groups"] == 0


def test_the_me_group_is_absent_when_i_did_not_play_the_contest(parsed, players):
    assert "me" not in set(F.field_stats(parsed, players, me="nobody").rows["group"])
    assert "me" not in set(F.field_stats(parsed, players).rows["group"])


def test_stack_table_lists_teams_by_popularity(result):
    stacks = result.stacks
    allg = stacks[stacks["group"] == "all"].set_index("team")
    assert (allg.loc["GB", "n"], allg.loc["CHI", "n"], allg.loc["DET", "n"]) == (5, 3, 1)
    assert allg.loc["GB", "share"] == pytest.approx(0.5)


def test_nothing_unmatched_when_the_frame_has_everyone(result):
    assert result.unmatched.empty and result.skipped == ""


def test_per_lineup_features_can_be_read_back(result):
    feats = result.features.set_index("entry_id")
    assert feats.loc["9000000108", "unspent"] == 8100  # E8
    assert feats.loc["9000000108", "flex_pos"] == "TE"
    assert bool(feats.loc["9000000101", "duplicated"]) and bool(feats.loc["9000000102", "duplicated"])
    assert not bool(feats.loc["9000000103", "duplicated"])


# ---- joins and what is missing -------------------------------------------------------------------


def test_a_player_missing_from_the_frame_is_reported_and_only_costs_the_measures_that_need_him(
    parsed, players
):
    """Wan'Dale Robinson is in E7 and E8 only (both WR slots)."""
    res = F.field_stats(parsed, players[players["Name"] != "Wan'Dale Robinson"])
    missing = res.unmatched
    assert list(missing["player"]) == ["Wan'Dale Robinson"] and list(missing["slot"]) == ["WR"]
    assert "not in the players frame" in missing["reason"].iloc[0]
    row = res.rows.set_index("group").loc["all"]
    assert row["n"] == 10 and row["n_resolved"] == 8 and row["n_salary"] == 8
    # E7 and E8 drop out. Of the other eight, everyone has QB + 1 (E8 was the only lineup without), all but E3
    # have QB + 2, and E1, E2, E3, E6, E9, E10 have a bring-back.
    assert row["qb_stack1"] == pytest.approx(1.0) and row["qb_stack2"] == pytest.approx(7 / 8)
    assert row["bringback"] == pytest.approx(6 / 8)
    # chalk needs only names, so it is unaffected by the miss
    assert row["avg_chalk"] == pytest.approx(3.5)
    # E7's FLEX is Odunze and E8's is Kraft, both resolved: FLEX shares still cover all ten
    assert row["flex_rb"] + row["flex_wr"] + row["flex_te"] == pytest.approx(1.0)


def test_two_players_with_one_name_and_position_are_ambiguous_not_guessed(parsed, players):
    twin = pd.concat(
        [
            players,
            pd.DataFrame(
                [{"Name": "DJ Moore", "Team": "BUF", "Position": "WR", "Opponent": "MIA", "Salary": 5000}]
            ),
        ]
    )
    res = F.field_stats(parsed, twin)
    assert set(res.unmatched["player"]) == {"DJ Moore"}
    assert "ambiguous" in res.unmatched["reason"].iloc[0] and "BUF" in res.unmatched["reason"].iloc[0]


def test_position_disambiguates_a_shared_name(parsed, players):
    twin = pd.concat(
        [
            players,
            pd.DataFrame(
                [{"Name": "DJ Moore", "Team": "BUF", "Position": "RB", "Opponent": "MIA", "Salary": 5000}]
            ),
        ]
    )
    res = F.field_stats(parsed, twin)
    assert res.unmatched.empty  # his WR slot only ever matches the WR


def test_a_flex_player_takes_his_position_from_his_other_slots(parsed, players):
    """Kraft is TE in E1 and FLEX in E8; the ownership table's non-FLEX row says TE."""
    flex = F.field_stats(parsed, players).features.set_index("entry_id").loc["9000000108", "flex_pos"]
    assert flex == "TE"


def test_without_a_players_frame_every_team_measure_is_blank_but_chalk_and_dups_remain(parsed):
    row = F.field_stats(parsed, None).rows.set_index("group").loc["all"]
    for column in ["qb_stack1", "qb_stack2", "bringback", "avg_unspent", "dst_with_qb", "dst_vs_qb"]:
        assert pd.isna(row[column]), column
    assert pd.isna(row["flex_rb"]) and row["top_stacks"] == ""
    assert row["avg_chalk"] == pytest.approx(3.5) and row["dup_entries"] == 2


def test_without_salaries_only_the_salary_measures_are_blank(parsed, players):
    row = F.field_stats(parsed, players.drop(columns="Salary")).rows.set_index("group").loc["all"]
    assert pd.isna(row["avg_unspent"]) and pd.isna(row["share_unspent_1k"]) and row["n_salary"] == 0
    assert row["qb_stack1"] == pytest.approx(0.9)


def test_a_frame_without_the_required_columns_is_an_error(parsed):
    with pytest.raises(ValueError, match="needs column"):
        F.field_stats(parsed, pd.DataFrame({"Name": ["x"]}))


def test_a_showdown_contest_is_skipped_with_a_reason():
    st = S.parse_standings(
        (DIR / "standings" / "contest-standings-900000004.csv").read_bytes(), contest_id="4"
    )
    res = F.field_stats(st, None)
    assert res.rows.empty and "Showdown" in res.skipped


def test_unparsed_lineups_are_counted_but_not_measured():
    st = S.parse_standings(
        (DIR / "standings" / "contest-standings-900000003.csv").read_bytes(), contest_id="3"
    )
    res = F.field_stats(st, None)
    row = res.rows.set_index("group").loc["all"]
    assert row["n"] == 3 and row["n_unparsed"] == 3
    assert row["dup_entries"] == 2  # tied_two (1/3) and (2/3) are the same nine players


def test_names_match_through_player_join_normalisation(parsed, players):
    """Suffixes, punctuation and case differences between the standings and the frame do not break a join."""
    renamed = players.copy()
    renamed["Name"] = renamed["Name"].replace(
        {"Amon-Ra St. Brown": "amon-ra st brown jr.", "DJ Moore": "D.J. Moore"}
    )
    res = F.field_stats(parsed, renamed)
    assert res.unmatched.empty


def test_edgeraws_own_column_spelling_is_accepted(parsed, players):
    edge = players.rename(columns={"Opponent": "Opp"})
    assert F.field_stats(parsed, edge).rows.equals(F.field_stats(parsed, players).rows)
