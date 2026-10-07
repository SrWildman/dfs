"""R1: absences exclude byes; shares come from prior games only; the redistribution accounting balances."""

import numpy as np
import pandas as pd
import pytest

from dfs.research import r1_redistribution as r1
from tests.research.conftest import SEASON, TEAM_CARRIES, TEAM_TARGETS, make_games, make_injuries, make_stats

# AAA's week 5 is a bye, so its games are weeks 1,2,3,4,6,7,8,9 -> game index 5 is week 6.
BYE = 5


def _prepare(missing=None, scale=None, bye=BYE):
    games = make_games(bye_week=bye)
    sp = make_stats(games, missing=missing, scale=scale)
    tg = r1.team_game_index(games)
    rows = r1.offense_rows(sp, tg)
    return games, sp, tg, rows


def test_game_index_skips_the_bye():
    tg = r1.team_game_index(make_games())
    aaa = tg[tg["team"] == "AAA"].sort_values("week")
    assert aaa["week"].tolist() == [1, 2, 3, 4, 6, 7, 8, 9]
    assert aaa["gidx"].tolist() == [1, 2, 3, 4, 5, 6, 7, 8]
    ccc = tg[tg["team"] == "CCC"]
    assert ccc["gidx"].max() == 9  # a team with no bye counts every week


def test_a_regular_missing_the_bye_week_is_not_absent():
    # AAA-WR1 has no row in week 5 -- because there was no game. He plays every game that exists.
    _, _, tg, rows = _prepare()
    assert not ((rows["team"] == "AAA") & (rows["week"] == BYE)).any()
    prior = r1.prior_windows(rows)
    absences = r1.find_absences(prior, rows, tg)
    assert absences.empty


def test_absence_is_found_on_the_game_after_the_bye_and_only_there():
    # WR1 misses week 6 (AAA's first game back from the bye) and plays everything else.
    _, _, tg, rows = _prepare(missing={("AAA", "WR1", 6)})
    absences = r1.find_absences(r1.prior_windows(rows), rows, tg)
    got = absences[["team", "gsis_id", "week"]].to_records(index=False).tolist()
    assert got == [("AAA", "AAA-WR1", 6)]
    # The window for week 6 is games 2-4 (weeks 2, 3, 4): the bye is not a zero-usage game in it.
    prior = r1.prior_windows(rows)
    w = prior[(prior["team"] == "AAA") & (prior["tidx"] == 5) & (prior["gsis_id"] == "AAA-WR1")].iloc[0]
    assert w["p_targets"] == 3 * 12
    assert w["share_t"] == pytest.approx(12 / TEAM_TARGETS)


def test_first_three_games_of_a_season_have_no_window():
    _, _, tg, rows = _prepare(missing={("CCC", "WR1", 2)})  # absent in game 2: no 3-game history yet
    prior = r1.prior_windows(rows)
    assert prior["tidx"].min() == 4
    assert r1.find_absences(prior, rows, tg).empty


def test_shares_ignore_the_game_being_explained():
    """Leakage: adding the current game (and any later game) to the data must not change a single prior."""
    games, sp, tg, rows = _prepare()
    target_idx = 6
    before = rows[rows["gidx"] < target_idx]
    p_without = r1.prior_windows(before, team_games=tg)
    p_with = r1.prior_windows(rows, team_games=tg)
    a = p_without[p_without["tidx"] == target_idx].reset_index(drop=True)
    b = p_with[p_with["tidx"] == target_idx].reset_index(drop=True)
    pd.testing.assert_frame_equal(a, b)

    # Absurd values in the current and later games leave this game's windows untouched ...
    scale = {(team, slot, wk): 100 for team in ("AAA", "BBB") for slot in ("WR1", "RB1") for wk in (7, 8, 9)}
    sp_big = make_stats(games, scale=scale)
    big = r1.prior_windows(r1.offense_rows(sp_big, tg), team_games=tg)
    c = big[big["tidx"] == target_idx].reset_index(drop=True)
    pd.testing.assert_frame_equal(b, c)
    # ... and do reach the game after (so the check above is not vacuous).
    nxt = target_idx + 1
    assert (
        not p_with[p_with["tidx"] == nxt]
        .reset_index(drop=True)
        .equals(big[big["tidx"] == nxt].reset_index(drop=True))
    )


def test_regular_thresholds():
    _, _, tg, rows = _prepare(missing={("AAA", "WR3", 6), ("AAA", "RB2", 6)})
    absences = r1.find_absences(r1.prior_windows(rows), rows, tg)
    # WR3: 5/36 = 13.9% of targets -> not a regular. RB2: 7/25 = 28% of carries -> not a regular either.
    assert absences.empty
    _, _, tg, rows = _prepare(missing={("AAA", "WR2", 6), ("AAA", "RB1", 6)})
    absences = r1.find_absences(r1.prior_windows(rows), rows, tg)
    assert set(absences["gsis_id"]) == {"AAA-WR2", "AAA-RB1"}  # 22% of targets; 72% of carries


def test_multi_absence_games_are_excluded_from_clean_events():
    _, sp, tg, rows = _prepare(missing={("AAA", "WR1", 6), ("AAA", "RB1", 6), ("AAA", "WR2", 7)})
    prior = r1.prior_windows(rows)
    absences = r1.find_absences(prior, rows, tg)
    rot = r1.rank_rotation(prior)
    labelled = absences.merge(
        rot[["team", "season", "tidx", "gsis_id", "label", "rank"]], on=["team", "season", "tidx", "gsis_id"]
    )
    events = r1.clean_events(labelled, r1.team_volume(rows))
    # week 6 has two regulars out (excluded); week 7 has WR2 out, with WR1 back and RB1 back -> clean
    assert events[["team", "week", "abs_id"]].to_records(index=False).tolist() == [("AAA", 7, "AAA-WR2")]


def test_rank_labels():
    assert r1.rank_label("WR", 1) == "WR1"
    assert r1.rank_label("WR", 4) == "WR4+" and r1.rank_label("WR", 9) == "WR4+"
    assert r1.rank_label("RB", 3) == "RB3+" and r1.rank_label("TE", 2) == "TE2+"


def _study(missing, scale=None, injuries=None):
    games = make_games(bye_week=BYE)
    sp = make_stats(games, missing=missing, scale=scale)
    return r1.run_study(sp, games, injuries if injuries is not None else make_injuries())


def test_the_accounting_identity_holds_for_every_event_and_channel():
    # WR1 out in week 7 with a newcomer, a missing fringe player and a team-volume change in the game.
    games = make_games(bye_week=BYE)
    sp = make_stats(games, missing={("AAA", "WR1", 7), ("AAA", "WR3", 7)})
    extra = pd.DataFrame(
        [
            {
                "player_id": "AAA-NEW",
                "player_display_name": "AAA NEW",
                "position": "WR",
                "team": "AAA",
                "season": SEASON,
                "week": 7,
                "season_type": "REG",
                "targets": 4,
                "carries": 0,
                "attempts": 0,
            }
        ]
    )
    sp = pd.concat([sp, extra], ignore_index=True)
    res = r1.run_study(sp, games, make_injuries())
    gains = res["gains"]
    assert len(res["events"]) == 1  # WR1 is the one regular out; WR3 (14% of targets) is not a regular
    for _, g in gains[gains["channel"] == "targets"].groupby("event_id"):
        by_item = g.set_index("item")["gain"]
        lhs = by_item["ROTATION"] + by_item["NEW"]
        rhs = g["v"].iloc[0] + by_item["OTHER_MISSING"] + by_item["TEAM_VOLUME"]
        assert lhs == pytest.approx(rhs)
        assert by_item["NOWHERE"] == pytest.approx(g["v"].iloc[0] - by_item["ROTATION"])


def test_vacated_volume_and_a_teammate_gain_are_computed_from_the_prior_window():
    # WR1 (12 of 36 targets) is out in week 7; WR2 gets 6 extra targets that day.
    games = make_games(bye_week=BYE)
    sp = make_stats(games, missing={("AAA", "WR1", 7)})
    sp.loc[(sp["player_id"] == "AAA-WR2") & (sp["week"] == 7), "targets"] = 14  # was 8
    res = r1.run_study(sp, games, make_injuries())
    ev = res["events"]
    assert len(ev) == 1 and ev.iloc[0]["abs_label"] == "WR1"
    assert ev.iloc[0]["v_t"] == pytest.approx(12.0)  # 12/36 of a 36-target team
    gains = res["gains"]
    g = gains[(gains["channel"] == "targets") & (gains["item"] == "WR2")].iloc[0]
    assert g["gain"] == pytest.approx(6.0) and g["frac"] == pytest.approx(0.5)


def test_reason_tags_come_from_the_injury_report():
    inj = make_injuries([("AAA-WR1", 7, "Knee"), ("AAA-RB1", 8, "Suspension"), ("AAA-WR2", 9, "Personal")])
    games = make_games(bye_week=BYE)
    sp = make_stats(
        games, missing={("AAA", "WR1", 7), ("AAA", "RB1", 8), ("AAA", "WR2", 9), ("AAA", "TE1", 6)}
    )
    res = r1.run_study(sp, games, inj)
    reasons = res["events"].set_index("abs_id")["reason_detail"].to_dict()
    assert reasons["AAA-WR1"] == "injury"
    assert reasons["AAA-RB1"] == "suspension"
    assert reasons["AAA-WR2"] == "listed_non_injury"
    assert reasons["AAA-TE1"] == "unlisted"
    assert res["events"].set_index("abs_id")["reason"].to_dict()["AAA-WR2"] == "other"


def test_hand_set_rule_allocation():
    played = pd.DataFrame(
        {
            "gsis_id": ["wr2", "wr3", "te1", "te2"],
            "position": ["WR", "WR", "TE", "TE"],
            "rank": [2, 3, 1, 2],
            "share_t": [0.2, 0.1, 0.3, 0.1],
        }
    )
    alloc = r1.rule_allocation("WR", 1, "targets", played)
    assert alloc["wr2"] == pytest.approx(0.60)  # the next WR up the chart
    assert alloc["te1"] == pytest.approx(0.25 * 0.75) and alloc["te2"] == pytest.approx(0.25 * 0.25)
    assert sum(alloc.values()) == pytest.approx(0.85)
    # carries have no spill, and a WR out vacates no carries to speak of
    assert r1.rule_allocation("RB", 1, "carries", played.assign(position="RB")).get("te1") is None
    # WR2 out: the NEXT man up is WR3 under the default reading, WR2's slot's best remaining under the other
    played2 = played.assign(rank=[1, 3, 1, 2])
    assert r1.rule_allocation("WR", 2, "targets", played2)["wr3"] == pytest.approx(0.60)
    assert r1.rule_allocation("WR", 2, "targets", played2, variant="top_remaining")["wr2"] == pytest.approx(
        0.60
    )


def test_control_games_have_no_regular_out():
    res = _study(missing={("AAA", "WR1", 7)})
    controls = res["controls"]
    absent_games = res["absences"][["team", "season", "tidx"]]
    merged = controls.merge(absent_games, on=["team", "season", "tidx"])
    assert merged.empty
    assert (controls["v_t"] == 0).all() and (controls["event_id"] >= r1.CONTROL_ID_OFFSET).all()


def test_team_volume_and_baselines_are_whole_team_numbers():
    games, sp, tg, rows = _prepare()
    vol = r1.team_volume(rows)
    assert (vol["t_targets"] == TEAM_TARGETS).all() and (vol["t_carries"] == TEAM_CARRIES).all()
    prior = r1.prior_windows(rows)
    assert np.allclose(prior["tb_t"], TEAM_TARGETS) and np.allclose(prior["tb_c"], TEAM_CARRIES)
