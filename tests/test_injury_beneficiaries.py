"""Injury beneficiaries: who is out, who inherits what, and whether TFFB has already priced it in."""

import numpy as np
import pandas as pd
import pytest

from dfs import injury_beneficiaries as ib


def _vacated(tgt=10.0, car=0.0, rec_xfp=12.0, rush_xfp=0.0):
    return ib.Vacated(tgt_g=tgt, car_g=car, rec_xfp_g=rec_xfp, rush_xfp_g=rush_xfp, games=3)


def _mates(rows):
    """rows: (gsis, position, tgt_g, car_g)"""
    return pd.DataFrame(
        [{"GsisId": g, "Position": p, "tgt_g": t, "car_g": c} for g, p, t, c in rows]
    ).set_index("GsisId")


def test_status_is_draftkings_avail_first_then_the_report():
    assert ib.classify_status("OUT", None) == ib.STATUS_OUT
    assert ib.classify_status("IR", "Questionable") == ib.STATUS_OUT  # Avail wins over the report
    assert ib.classify_status("Q", "Out") == ib.STATUS_QUESTIONABLE  # ... in both directions
    assert ib.classify_status("D", None) == ib.STATUS_OUT  # DraftKings' Doubtful counts as out
    assert ib.classify_status(np.nan, "Out") == ib.STATUS_OUT
    assert ib.classify_status("", "Doubtful") == ib.STATUS_OUT
    assert ib.classify_status(None, "Questionable") == ib.STATUS_QUESTIONABLE
    assert ib.classify_status(np.nan, np.nan) is None


def test_a_wr1_out_sends_sixty_percent_of_his_targets_to_the_wr2_by_depth_chart():
    mates = _mates([("wr2", "WR", 6.0, 0.0), ("wr3", "WR", 3.0, 0.0), ("te1", "TE", 5.0, 0.0)])
    order = {"WR": ["wr2", "wr3"], "TE": ["te1"], "RB": []}
    gains = ib.depth_gains("WR", _vacated(tgt=10.0), mates, order)
    assert gains.loc["wr2", "tgt_gain"] == pytest.approx(6.0)  # 60% of 10, spill or not
    # the other 40%: 25% spills to the TE (1.0), the rest (3.0) to the other WR
    assert gains.loc["te1", "tgt_gain"] == pytest.approx(1.0)
    assert gains.loc["wr3", "tgt_gain"] == pytest.approx(3.0)
    assert gains["tgt_gain"].sum() == pytest.approx(10.0)  # conserved
    assert gains.loc["wr2", "xfp_gain"] == pytest.approx(0.6 * 12.0)  # receiving xFP follows targets


def test_next_up_is_the_next_pos_rank_not_the_biggest_share():
    mates = _mates([("wr2", "WR", 1.0, 0.0), ("wr3", "WR", 9.0, 0.0), ("te1", "TE", 4.0, 0.0)])
    gains = ib.depth_gains("WR", _vacated(tgt=10.0), mates, {"WR": ["wr2", "wr3"]})
    assert gains.loc["wr2", "tgt_gain"] == pytest.approx(6.0)  # depth chart says wr2, whatever the share
    assert gains.loc["wr3", "tgt_gain"] == pytest.approx(3.0)


def test_an_empty_bucket_hands_its_weight_to_the_others():
    only_wr2 = _mates([("wr2", "WR", 6.0, 0.0)])  # no other WR and no TE: he takes it all
    gains = ib.depth_gains("WR", _vacated(tgt=10.0), only_wr2, {"WR": ["wr2"]})
    assert gains.loc["wr2", "tgt_gain"] == pytest.approx(10.0)
    no_tight_end = _mates([("wr2", "WR", 6.0, 0.0), ("wr3", "WR", 2.0, 0.0)])
    gains = ib.depth_gains("WR", _vacated(tgt=10.0), no_tight_end, {"WR": ["wr2", "wr3"]})
    # buckets 0.6 (next-up) and 0.3 (other WRs) renormalise to 2/3 and 1/3
    assert gains.loc["wr2", "tgt_gain"] == pytest.approx(10 * 2 / 3)
    assert gains.loc["wr3", "tgt_gain"] == pytest.approx(10 / 3)
    assert gains["tgt_gain"].sum() == pytest.approx(10.0)


def test_rb_carries_never_reach_wrs_and_rb_targets_split_half_and_half():
    mates = _mates(
        [("rb2", "RB", 2.0, 5.0), ("rb3", "RB", 1.0, 2.0), ("wr1", "WR", 8.0, 0.0), ("te1", "TE", 4.0, 0.0)]
    )
    gains = ib.depth_gains(
        "RB", _vacated(tgt=4.0, car=15.0, rec_xfp=4.0, rush_xfp=9.0), mates, {"RB": ["rb2", "rb3"]}
    )
    assert gains.loc[["wr1", "te1"], "car_gain"].sum() == 0.0
    assert gains["car_gain"].sum() == pytest.approx(15.0)
    assert gains.loc["rb2", "car_gain"] == pytest.approx(9.0)  # next-up 60%
    rb_targets = gains.loc[["rb2", "rb3"], "tgt_gain"].sum()
    assert rb_targets == pytest.approx(2.0)  # 50% of 4 targets to the backs
    assert gains.loc[["wr1", "te1"], "tgt_gain"].sum() == pytest.approx(2.0)  # 50% to the pass catchers
    assert gains.loc["wr1", "tgt_gain"] == pytest.approx(2.0 * 8 / 12)  # by current target share
    # rushing xFP follows carries only: a WR gets none of it
    assert gains.loc["wr1", "xfp_gain"] == pytest.approx(gains.loc["wr1", "tgt_gain"] * 1.0)


def test_no_depth_chart_falls_back_to_the_biggest_current_share():
    mates = _mates([("a", "WR", 2.0, 0.0), ("b", "WR", 7.0, 0.0), ("t", "TE", 3.0, 0.0)])
    gains = ib.depth_gains("WR", _vacated(tgt=10.0), mates, {})
    assert gains.loc["b", "tgt_gain"] == pytest.approx(6.0)


def test_depth_order_and_latest_snapshot_cutoff():
    depth = pd.DataFrame(
        [
            {"dt": "2026-10-04T10:00:00Z", "Team": "DEN", "GsisId": "a", "Position": "WR", "pos_rank": 1},
            {"dt": "2026-10-04T10:00:00Z", "Team": "DEN", "GsisId": "b", "Position": "WR", "pos_rank": 2},
            {"dt": "2026-10-05T10:00:00Z", "Team": "DEN", "GsisId": "b", "Position": "WR", "pos_rank": 1},
            {"dt": "2026-10-05T10:00:00Z", "Team": "DEN", "GsisId": "a", "Position": "WR", "pos_rank": 2},
        ]
    )
    assert ib.depth_order(ib.latest_depth(depth, "2026-10-04T23:00:00Z"), "DEN", "WR") == ["a", "b"]
    assert ib.depth_order(ib.latest_depth(depth, None), "DEN", "WR") == ["b", "a"]  # newest by default
    assert ib.latest_depth(depth, "2026-10-01T00:00:00Z").empty  # nothing before the cutoff
    assert ib.latest_depth(None, None).empty


def _history(rows):
    """rows: (gsis, team, week, targets, carries, xfp); season 2026"""
    return pd.DataFrame(
        [
            {"GsisId": g, "Team": t, "season": 2026, "week": w, "targets": tg, "carries": c, "xfp": x}
            for g, t, w, tg, c, x in rows
        ]
    )


def test_with_without_uses_games_he_missed_and_counts_only_same_team_games():
    rows = []
    for w in (1, 2, 3, 4):  # star plays 1, 2 and misses 3, 4 (a teammate's games continue)
        rows.append(("mate", "DEN", w, 4.0 if w <= 2 else 9.0, 0.0, 8.0 if w <= 2 else 15.0))
    rows += [("star", "DEN", 1, 10.0, 0.0, 14.0), ("star", "DEN", 2, 10.0, 0.0, 14.0)]
    rows.append(("star", "KC", 0, 10.0, 0.0, 14.0))  # a game with another team never counts
    gains, n_missed = ib.with_without_gains(_history(rows), "star", "DEN", before=(2026, 5), exclude=set())
    assert n_missed == 2
    assert gains.loc["mate", "tgt_gain"] == pytest.approx(5.0)  # 9 without, 4 with
    assert gains.loc["mate", "xfp_gain"] == pytest.approx(7.0)


def test_games_before_his_first_game_with_the_team_are_not_absences():
    rows = [("mate", "DEN", w, 5.0, 0.0, 7.0) for w in (1, 2, 3)] + [("star", "DEN", 3, 8.0, 0.0, 10.0)]
    gains, n_missed = ib.with_without_gains(_history(rows), "star", "DEN", before=(2026, 4), exclude=set())
    assert n_missed == 0 and gains.empty


def test_gains_are_conserved_so_noise_cannot_invent_volume():
    gains = pd.DataFrame(
        {"tgt_gain": [6.0, 6.0], "car_gain": [0.0, 0.0], "xfp_gain": [20.0, 20.0]}, index=["a", "b"]
    )
    out = ib.conserve(gains, _vacated(tgt=8.0, rec_xfp=10.0))
    assert out["tgt_gain"].sum() == pytest.approx(8.0)
    assert out["xfp_gain"].sum() == pytest.approx(10.0)
    # already within the limit: untouched
    ok = ib.conserve(gains, _vacated(tgt=20.0, rec_xfp=50.0))
    pd.testing.assert_frame_equal(ok, gains)


def _scenario(missed_games):
    """DEN: star WR out, wr2 and wr3 behind him, a TE and an RB. `missed_games` weeks he missed."""
    windows = pd.DataFrame(
        {
            "Games": [3, 3, 3, 3, 3],
            "tgt_g": [10.0, 6.0, 3.0, 5.0, 2.0],
            "car_g": [0.0, 0.0, 0.0, 0.0, 12.0],
            "rec_xfp_g": [12.0, 6.0, 3.0, 5.0, 2.0],
            "rush_xfp_g": [0.0, 0.0, 0.0, 0.0, 8.0],
        },
        index=["star", "wr2", "wr3", "te1", "rb1"],
    )
    identity = pd.DataFrame(
        {
            "GsisId": ["star", "wr2", "wr3", "te1", "rb1"],
            "Name": ["Star", "Wr Two", "Wr Three", "Te One", "Rb One"],
            "Team": ["DEN"] * 5,
            "Position": ["WR", "WR", "WR", "TE", "RB"],
        }
    )
    outs = pd.DataFrame(
        [{"GsisId": "star", "Name": "Star", "Team": "DEN", "Position": "WR", "Status": ib.STATUS_OUT}]
    )
    rows = []
    for w in (1, 2, 3, 4):
        absent = w in missed_games
        if not absent:
            rows.append(("star", "DEN", w, 10.0, 0.0, 12.0))
        for gsis, tg, x in (("wr2", 6.0, 6.0), ("wr3", 3.0, 3.0), ("te1", 5.0, 5.0)):
            rows.append(
                (
                    gsis,
                    "DEN",
                    w,
                    tg + (4.0 if absent and gsis == "wr3" else 0.0),
                    0.0,
                    x + (4.0 if absent and gsis == "wr3" else 0.0),
                )
            )
    depth = pd.DataFrame(
        [
            {"dt": "2026-10-04T10:00:00Z", "Team": "DEN", "GsisId": g, "Position": p, "pos_rank": r}
            for g, p, r in (
                ("star", "WR", 1),
                ("wr2", "WR", 2),
                ("wr3", "WR", 3),
                ("te1", "TE", 1),
                ("rb1", "RB", 1),
            )
        ]
    )
    return outs, windows, identity, _history(rows), ib.latest_depth(depth, None)


def test_with_or_without_beats_the_depth_chart_when_he_missed_enough_games():
    outs, windows, identity, history, depth = _scenario(missed_games={3, 4})
    table = ib.beneficiaries(outs, windows, identity, history, depth, before=(2026, 5))
    top = table.iloc[0]
    assert top["Name"] == "Wr Three" and top["Method"] == ib.METHOD_WITH_WITHOUT and top["n"] == 2
    assert top["tgt_gain"] == pytest.approx(4.0)  # the measured change, not the 3rd-string depth share
    assert set(table["Method"]) == {ib.METHOD_WITH_WITHOUT}


def test_one_missed_game_is_not_enough_so_the_depth_chart_decides():
    outs, windows, identity, history, depth = _scenario(missed_games={4})
    table = ib.beneficiaries(outs, windows, identity, history, depth, before=(2026, 5))
    assert set(table["Method"]) == {ib.METHOD_DEPTH}
    assert table.iloc[0]["Name"] == "Wr Two"  # next-up by depth chart
    assert table.iloc[0]["tgt_gain"] == pytest.approx(6.0)


def test_the_out_player_and_other_confirmed_outs_are_never_beneficiaries():
    outs, windows, identity, history, depth = _scenario(missed_games=set())
    outs = pd.concat(
        [
            outs,
            pd.DataFrame(
                [
                    {
                        "GsisId": "wr2",
                        "Name": "Wr Two",
                        "Team": "DEN",
                        "Position": "WR",
                        "Status": ib.STATUS_OUT,
                    }
                ]
            ),
        ],
        ignore_index=True,
    )
    table = ib.beneficiaries(outs, windows, identity, history, depth, before=(2026, 5))
    assert "Wr Two" not in set(table["Name"]) and "Star" not in set(table["Name"])
    assert table[table["Name"] == "Wr Three"].iloc[0]["OutPlayers"] == "Star, Wr Two"


def test_questionable_players_get_their_own_rows_and_never_mix_into_the_confirmed_list():
    outs, windows, identity, history, depth = _scenario(missed_games=set())
    outs.loc[0, "Status"] = ib.STATUS_QUESTIONABLE
    table = ib.beneficiaries(outs, windows, identity, history, depth, before=(2026, 5))
    assert set(table["OutStatus"]) == {ib.STATUS_QUESTIONABLE}
    # a questionable player is assumed to play, so he is still a legal beneficiary of someone else's absence
    both = pd.concat(
        [
            outs,
            pd.DataFrame(
                [
                    {
                        "GsisId": "te1",
                        "Name": "Te One",
                        "Team": "DEN",
                        "Position": "TE",
                        "Status": ib.STATUS_OUT,
                    }
                ]
            ),
        ],
        ignore_index=True,
    )
    mixed = ib.beneficiaries(both, windows, identity, history, depth, before=(2026, 5))
    assert {"out", "questionable"} >= set(mixed["OutStatus"])
    assert (mixed["OutStatus"] == ib.STATUS_QUESTIONABLE).any() and (
        mixed["OutStatus"] == ib.STATUS_OUT
    ).any()


def test_inj_token_needs_a_gain_of_at_least_the_threshold():
    outs, windows, identity, history, depth = _scenario(missed_games=set())
    table = ib.beneficiaries(outs, windows, identity, history, depth, before=(2026, 5))
    token = table.set_index("Name")["Token"]
    gain = table.set_index("Name")["xfp_gain"]
    assert (token[gain >= ib.INJ_MIN_GAINED_XFP] == ib.TOKEN_INJ).all()
    assert (token[gain < ib.INJ_MIN_GAINED_XFP] == "").all()


def test_a_qb_out_is_not_redistributed():
    outs, windows, identity, history, depth = _scenario(missed_games=set())
    outs.loc[0, ["Position"]] = "QB"
    assert ib.beneficiaries(outs, windows, identity, history, depth, before=(2026, 5)).empty


def test_priced_in_logic():
    # gained 4 xFP/G: priced in once the projection rose by 70% of that (2.8)
    assert ib.priced_in(11.0, 8.0, 4.0) == ib.PRICED_YES  # +3.0
    assert ib.priced_in(10.8, 8.0, 4.0) == ib.PRICED_YES  # exactly +2.8
    assert ib.priced_in(10.7, 8.0, 4.0) == ib.PRICED_NO
    assert ib.priced_in(8.0, 8.0, 4.0) == ib.PRICED_NO
    assert ib.priced_in(9.0, None, 4.0) == ib.PRICED_UNKNOWN  # no earlier snapshot
    assert ib.priced_in(np.nan, 8.0, 4.0) == ib.PRICED_UNKNOWN
    assert ib.priced_in(11.0, 8.0, 0.0) == ib.PRICED_UNKNOWN  # no positive gain to be priced against
