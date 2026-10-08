"""Injury beneficiaries: the measured carries table, target absences as context, and the priced-in blank."""

import json
import shutil

import numpy as np
import pandas as pd
import pytest

from dfs import injury_beneficiaries as ib
from dfs import research_constants as rc


def _vacated(tgt=10.0, car=0.0, rec_xfp=12.0, rush_xfp=0.0):
    return ib.Vacated(tgt_g=tgt, car_g=car, rec_xfp_g=rec_xfp, rush_xfp_g=rush_xfp, games=3)


def test_status_is_draftkings_avail_first_then_the_report():
    assert ib.classify_status("OUT", None) == ib.STATUS_OUT
    assert ib.classify_status("IR", "Questionable") == ib.STATUS_OUT  # Avail wins over the report
    assert ib.classify_status("Q", "Out") == ib.STATUS_QUESTIONABLE  # ... in both directions
    assert ib.classify_status("D", None) == ib.STATUS_OUT  # DraftKings' Doubtful counts as out
    assert ib.classify_status(np.nan, "Out") == ib.STATUS_OUT
    assert ib.classify_status("", "Doubtful") == ib.STATUS_OUT
    assert ib.classify_status(None, "Questionable") == ib.STATUS_QUESTIONABLE
    assert ib.classify_status(np.nan, np.nan) is None


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


IDS = ["rb1", "rb2", "rb3", "star", "wr2", "wr3", "te1"]
NAMES = ["Rb One", "Rb Two", "Rb Three", "Star", "Wr Two", "Wr Three", "Te One"]
POSITIONS = ["RB", "RB", "RB", "WR", "WR", "WR", "TE"]
TARGETS = dict(zip(IDS, [2.0, 1.0, 0.5, 10.0, 6.0, 3.0, 5.0], strict=True))  # per game
CARRIES = dict(zip(IDS, [14.0, 5.0, 2.0, 0.0, 0.0, 0.0, 0.0], strict=True))
RUSH_XFP = dict(zip(IDS, [3.5, 3.0, 1.0, 0.0, 0.0, 0.0, 0.0], strict=True))  # rb1: 0.25 xFP a carry


def _league(missed_games=frozenset(), out="rb1", carries=None, weeks=(1, 2, 3, 4)):
    """DEN, `weeks` of play-by-play: back rb1 (two thirds of the carries) with rb2 and rb3 behind him; WRs
    star, wr2, wr3 and a TE. `out` is the player on the injury list and `missed_games` the weeks he missed
    (he is absent from those games; rb2 picks up 4 carries). Returns (outs, history, depth)."""
    car = {**CARRIES, **(carries or {})}
    rows = []
    for w in weeks:
        absent = w in missed_games
        week_rows = []
        for gsis, name, pos in zip(IDS, NAMES, POSITIONS, strict=True):
            if gsis == out and absent:
                continue
            carried = car[gsis] + (4.0 if absent and gsis == "rb2" else 0.0)
            week_rows.append((gsis, name, pos, TARGETS[gsis], carried, TARGETS[gsis], RUSH_XFP[gsis]))
        team_t = sum(r[3] for r in week_rows)
        team_c = sum(r[4] for r in week_rows)
        for gsis, name, pos, tgt, carried, rec, rush in week_rows:
            rows.append(
                {
                    "GsisId": gsis,
                    "Name": name,
                    "Team": "DEN",
                    "Position": pos,
                    "season": 2026,
                    "week": w,
                    "targets": tgt,
                    "carries": carried,
                    "team_targets": team_t,
                    "team_carries": team_c,
                    "rec_xfp": rec,
                    "rush_xfp": rush,
                    "xfp": rec + rush,
                }
            )
    who = dict(zip(IDS, zip(NAMES, POSITIONS, strict=True), strict=True))[out]
    outs = pd.DataFrame([{"GsisId": out, "Name": who[0], "Team": "DEN", "Position": who[1], "Status": "out"}])
    depth = pd.DataFrame(
        [
            {"dt": "2026-10-04T10:00:00Z", "Team": "DEN", "GsisId": g, "Position": p, "pos_rank": r}
            for g, p, r in (
                ("rb1", "RB", 1),
                ("rb2", "RB", 2),
                ("rb3", "RB", 3),
                ("star", "WR", 1),
                ("wr2", "WR", 2),
                ("wr3", "WR", 3),
                ("te1", "TE", 1),
            )
        ]
    )
    return outs, pd.DataFrame(rows), ib.latest_depth(depth, None)


def _beneficiaries(missed=frozenset(), out="rb1", carries=None):
    outs, history, depth = _league(missed, out, carries)
    return ib.beneficiaries(outs, history, depth, before=(2026, 5))


def test_rb1_out_gives_the_measured_fractions_of_his_carries_from_the_research_files():
    shares = rc.carry_shares("RB1")
    table = _beneficiaries().set_index("Name")
    assert set(table["Method"]) == {ib.METHOD_TABLE}
    assert table.loc["Rb Two", "car_gain"] == pytest.approx(shares.next_up * 14.0)
    assert table.loc["Rb Three", "car_gain"] == pytest.approx(shares.each_other * 14.0)
    assert list(table.columns).count("tgt_gain") == 0  # carries only
    assert shares.next_up == pytest.approx(0.47, abs=0.01) and shares.each_other == pytest.approx(
        0.22, abs=0.01
    )


def test_the_unassigned_share_is_never_handed_out():
    shares = rc.carry_shares("RB1")
    table = _beneficiaries()
    given = table["car_gain"].sum() / 14.0
    assert given == pytest.approx(shares.next_up + shares.each_other)
    assert given <= 1.0 - shares.unassigned + 1e-9  # ~19% of his carries stay unassigned


def test_shares_are_normalised_only_when_they_would_exceed_one_minus_unassigned():
    shares = rc.CarryShares("RB1", "RB2", next_up=0.47, each_other=0.22, unassigned=0.19, n=300)
    few = ib.table_shares(["a", "b"], shares)
    assert few.tolist() == pytest.approx([0.47, 0.22])  # 0.69 <= 0.81: untouched
    many = ib.table_shares(["a", "b", "c", "d", "e"], shares)  # 0.47 + 4 x 0.22 = 1.35 > 0.81
    assert many.sum() == pytest.approx(0.81)
    assert many["a"] / many["b"] == pytest.approx(0.47 / 0.22)  # the proportions survive


def test_the_carries_table_is_read_from_the_json_at_run_time(tmp_path, monkeypatch):
    shutil.copytree(rc.RESEARCH_DIR, tmp_path / "research")
    folder = tmp_path / "research"
    data = json.loads((folder / rc.REDISTRIBUTION_JSON).read_text())
    data["recommended"]["carries:RB1"]["next_lower_same_position"]["net_mean_frac"] = 0.40
    (folder / rc.REDISTRIBUTION_JSON).write_text(json.dumps(data))
    table = pd.read_csv(folder / rc.REDISTRIBUTION_CSV)
    mask = (
        (table["channel"] == "carries")
        & (table["absent"] == "RB1")
        & (table["item"] == "RB2")
        & (table["seasons"] == "all")
    )
    table.loc[mask, "net_mean_frac"] = 0.40
    table.to_csv(folder / rc.REDISTRIBUTION_CSV, index=False)
    monkeypatch.setattr(rc, "RESEARCH_DIR", folder)
    assert _beneficiaries().set_index("Name").loc["Rb Two", "car_gain"] == pytest.approx(0.40 * 14.0)


def test_a_missing_research_file_fails_loudly_instead_of_guessing(tmp_path, monkeypatch):
    monkeypatch.setattr(rc, "RESEARCH_DIR", tmp_path)
    with pytest.raises(rc.ResearchConstantsError, match="missing"):
        _beneficiaries()


def test_rb2_out_uses_its_own_cells():
    shares = rc.carry_shares("RB2")
    assert shares.next_up_label == "RB1"
    table = _beneficiaries(out="rb2", carries={"rb2": 9.0}).set_index("Name")  # 9 of 25 carries: a regular
    assert table.loc["Rb One", "car_gain"] == pytest.approx(shares.next_up * 9.0)
    assert table.loc["Rb Three", "car_gain"] == pytest.approx(shares.each_other * 9.0)


def test_an_rb3_out_has_no_cell_and_a_bit_part_back_is_not_an_event():
    assert rc.carry_shares("RB3+") is None
    assert _beneficiaries(out="rb3").empty  # 2 of 21 carries: not a regular either


def test_target_absences_produce_no_points_and_no_inj_plus():
    for out in ("star", "wr2", "te1"):
        assert _beneficiaries(out=out).empty  # no beneficiaries at all, so no INJ+ from a receiver being out
    assert "tgt_gain" not in ib.BENEFICIARY_COLUMNS
    assert not hasattr(ib, "NEXT_UP_SHARE") and not hasattr(ib, "depth_gains")


def test_a_rb_with_or_without_split_overrides_the_table_for_carries():
    table = _beneficiaries(missed={3, 4}).set_index("Name")
    assert set(table["Method"]) == {ib.METHOD_WITH_WITHOUT} and table.loc["Rb Two", "n"] == 2
    assert table.loc["Rb Two", "car_gain"] == pytest.approx(4.0)  # 9 carries without him, 5 with
    assert "Rb Three" not in table.index  # no measured change, nothing to hand him


def test_with_or_without_hands_carries_to_backs_only():
    outs, history, depth = _league({3, 4})
    history.loc[(history["GsisId"] == "wr2") & (history["week"] >= 3), "carries"] = 3.0  # end-arounds
    table = ib.beneficiaries(outs, history, depth, before=(2026, 5))
    assert set(table["Position"]) == {"RB"} and "Wr Two" not in set(table["Name"])


def test_roles_follow_prior_usage_not_the_depth_chart_which_demotes_an_injured_starter():
    outs, history, depth = _league()
    chart = depth.assign(pos_rank=lambda d: d["pos_rank"].where(d["GsisId"] != "rb1", 9))  # starter dropped
    window = ib.rotation_window(history, "DEN", before=(2026, 5))
    assert ib.position_order(window, "RB", chart, "DEN") == ["rb1", "rb2", "rb3"]
    assert ib.role_label("RB", 1) == "RB1" and ib.role_label("RB", 3) == "RB3+"
    table = ib.beneficiaries(outs, history, chart, before=(2026, 5)).set_index("Name")
    assert table.loc["Rb Two", "car_gain"] == pytest.approx(rc.carry_shares("RB1").next_up * 14.0)


def test_the_window_is_his_last_three_games_played_with_shares_of_those_games_and_a_missed_count():
    outs, history, _ = _league(missed_games={4})  # rb1 sat out Week 4
    window = ib.rotation_window(history, "DEN", before=(2026, 5))
    rb1 = window.loc["rb1"]
    assert rb1["Games"] == 3 and rb1["Missed"] == 1 and rb1["LastKey"] == (2026, 3)  # weeks 1-3: not a zero
    assert rb1["car_g"] == 14.0 and rb1["car_share"] == pytest.approx(14 / 21)
    assert window.loc["rb1", "InRotation"]  # he played in the team's last three games (Weeks 2 and 3)
    vacated = ib.vacated_from_row(rb1)
    assert vacated.car_g == 14.0 and vacated.games == 3
    early = ib.rotation_window(history, "DEN", before=(2026, 2))  # fewer than three games: whatever exists
    assert early.loc["rb1", "Games"] == 1


def test_a_regular_who_has_missed_more_than_two_team_games_is_no_event():
    outs, history, depth = _league(missed_games={3, 4})  # missed 2: still counts
    assert not ib.beneficiaries(outs, history, depth, before=(2026, 5)).empty
    outs, history, depth = _league(missed_games={2, 3, 4})  # missed 3: old news
    assert ib.beneficiaries(outs, history, depth, before=(2026, 5)).empty
    assert ib.absences(outs, history, depth, before=(2026, 5)).empty


def test_one_missed_game_is_not_enough_so_the_table_decides():
    table = _beneficiaries(missed={4})
    assert set(table["Method"]) == {ib.METHOD_TABLE}
    assert table.set_index("Name").loc["Rb Two", "car_gain"] == pytest.approx(
        rc.carry_shares("RB1").next_up * 14.0
    )  # his per-game volume over the 3 games he played


def test_the_out_player_and_other_confirmed_outs_are_never_beneficiaries():
    outs, history, depth = _league()
    outs = pd.concat(
        [
            outs,
            pd.DataFrame(
                [{"GsisId": "rb2", "Name": "Rb Two", "Team": "DEN", "Position": "RB", "Status": "out"}]
            ),
        ],
        ignore_index=True,
    )
    table = ib.beneficiaries(outs, history, depth, before=(2026, 5))
    assert "Rb Two" not in set(table["Name"]) and "Rb One" not in set(table["Name"])
    assert table[table["Name"] == "Rb Three"].iloc[0]["OutPlayers"] == "Rb One"  # rb2's own cell is no event


def test_questionable_backs_get_their_own_rows_and_never_mix_into_the_confirmed_list():
    outs, history, depth = _league()
    outs.loc[0, "Status"] = ib.STATUS_QUESTIONABLE
    table = ib.beneficiaries(outs, history, depth, before=(2026, 5))
    assert set(table["OutStatus"]) == {ib.STATUS_QUESTIONABLE}


def test_inj_token_needs_a_rushing_gain_of_at_least_the_threshold():
    table = _beneficiaries().set_index("Name")
    assert (
        table.loc["Rb Two", "xfp_gain"] >= ib.INJ_MIN_GAINED_XFP
        and table.loc["Rb Two", "Token"] == ib.TOKEN_INJ
    )
    assert table.loc["Rb Three", "xfp_gain"] < ib.INJ_MIN_GAINED_XFP and table.loc["Rb Three", "Token"] == ""


def test_a_qb_out_is_not_redistributed():
    outs, history, depth = _league()
    outs.loc[0, "Position"] = "QB"
    assert ib.beneficiaries(outs, history, depth, before=(2026, 5)).empty


def _absences(out="star", missed=frozenset(), status="out"):
    outs, history, depth = _league(missed, out)
    outs["Status"] = status
    return ib.absences(outs, history, depth, before=(2026, 5))


def test_every_confirmed_absence_of_a_regular_is_listed_as_context_with_the_historical_line():
    table = _absences("star")
    row = table.iloc[0]
    assert row["Name"] == "Star" and row["Role"] == "WR1" and "36% of team targets" in row["Regular"]
    assert row["tgt_g"] == 10.0
    assert "no single teammate gains much" in row["History"] and "goes nowhere" in row["History"]
    ctx = rc.target_context("WR1")
    assert f"{ctx.next_up_label} +{round(100 * ctx.next_up)}%" in row["History"]  # numbers come from the JSON
    assert f"~{round(100 * ctx.unassigned)}% goes nowhere" in row["History"]


def test_absences_cover_backs_too_and_skip_bit_parts_and_questionable_players():
    assert _absences("rb1").iloc[0]["Role"] == "RB1" and "carries" in _absences("rb1").iloc[0]["History"]
    assert _absences("rb3").empty  # 10% of the carries and 2% of the targets: not a regular
    assert _absences("star", status="questionable").empty  # only CONFIRMED absences are listed


def test_the_absence_with_or_without_split_is_display_only_and_needs_two_games():
    outs, history, depth = _league({3, 4}, "rb1")
    table = ib.absences(outs, history, depth, before=(2026, 5))
    assert "with-or-without (2 g): Rb Two +4.0 carries/G" in table.iloc[0]["WithWithout"]
    assert table.iloc[0]["GamesMissed"] == 2
    assert _absences("rb1", missed={4}).iloc[0]["WithWithout"] == ""


def test_priced_in_logic():
    # gained 4 xFP/G: priced in once the projection rose by 70% of that (2.8)
    assert ib.priced_in(11.0, 8.0, 4.0) == ib.PRICED_YES  # +3.0
    assert ib.priced_in(10.8, 8.0, 4.0) == ib.PRICED_YES  # exactly +2.8
    assert ib.priced_in(10.7, 8.0, 4.0) == ib.PRICED_NO
    assert ib.priced_in(8.0, 8.0, 4.0) == ib.PRICED_NO
    assert ib.priced_in(9.0, None, 4.0) == ib.PRICED_UNKNOWN == ""  # no earlier snapshot: BLANK, not a word
    assert ib.priced_in(np.nan, 8.0, 4.0) == ib.PRICED_UNKNOWN
    assert ib.priced_in(11.0, 8.0, 0.0) == ib.PRICED_UNKNOWN  # no positive gain to be priced against


def test_two_players_sharing_a_pos_rank_are_ordered_by_current_volume():
    # nflverse lists a player once per formation group, each with its own rank 1: the fullback and the starter
    depth = pd.DataFrame(
        [
            {"dt": "2026-10-05T10:00:00Z", "Team": "SEA", "GsisId": g, "Position": "RB", "pos_rank": r}
            for g, r in (("fb", 1), ("starter", 1), ("backup", 2), ("starter", 3))
        ]
    )
    volume = pd.Series({"fb": 2.0, "starter": 20.0, "backup": 8.0})
    assert ib.depth_order(depth, "SEA", "RB", volume) == [
        "starter",
        "fb",
        "backup",
    ]  # best rank per player, ties by volume
    assert ib.depth_order(depth, "SEA", "RB") == [
        "fb",
        "starter",
        "backup",
    ]  # no volume: the id breaks the tie
