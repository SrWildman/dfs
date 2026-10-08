"""The signal table end to end on a tiny league: tokens, outs, beneficiaries, priced-in, no lookahead."""

import numpy as np
import pandas as pd
import pytest

from dfs import injury_beneficiaries as ib
from dfs import research_constants as rc
from dfs import signals, xfp


def _ffo_weeks(season=2026, weeks=(1, 2, 3), star_missed=()):
    """DEN: Star WR1, Wr Two, Wr Three, Te One, Rb One (lead back), Rb Two. Volume is fixed per game."""
    spec = {
        "s": ("Star", "WR", 10.0, 0.0, 12.0, 14.0),
        "w2": ("Wr Two", "WR", 6.0, 0.0, 6.0, 6.0),
        "w3": ("Wr Three", "WR", 3.0, 0.0, 3.0, 3.0),
        "t1": ("Te One", "TE", 5.0, 0.0, 5.0, 5.0),
        "r1": ("Rb One", "RB", 2.0, 12.0, 10.0, 11.0),
        "r2": ("Rb Two", "RB", 1.0, 5.0, 4.0, 5.0),
    }
    rows = []
    for week in weeks:
        for gsis, (name, pos, tgt, car, x, actual) in spec.items():
            if gsis == "s" and week in star_missed:
                continue
            rows.append(
                {
                    "GsisId": gsis,
                    "Name": name,
                    "Team": "DEN",
                    "Position": pos,
                    "season": season,
                    "week": week,
                    "targets": tgt,
                    "carries": car,
                    "team_targets": 30.0,
                    "team_carries": 25.0,
                    "rec_xfp": x if pos != "RB" else tgt,  # a back's receiving xFP is 1 per target here
                    "rush_xfp": 0.0 if pos != "RB" else x - tgt,
                    "pass_xfp": 0.0,
                    "xfp": x,
                    "td": 0.0,
                    "td_exp": 0.0,
                    "dk_actual": actual,
                }
            )
    return pd.DataFrame(rows)


def _depth():
    ranks = {
        "s": ("WR", 1),
        "w2": ("WR", 2),
        "w3": ("WR", 3),
        "t1": ("TE", 1),
        "r1": ("RB", 1),
        "r2": ("RB", 2),
    }
    return pd.DataFrame(
        [
            {
                "dt": "2026-10-04T10:00:00Z",
                "Team": "DEN",
                "GsisId": g,
                "Name": g,
                "Position": p,
                "pos_rank": r,
            }
            for g, (p, r) in ranks.items()
        ]
    )


def _frame(avail_star="", proj_w2=11.0, avail_rb="OUT"):
    rows = [
        ("Star", "WR", 8000, 15.0, avail_star, "s"),
        ("Wr Two", "WR", 5000, proj_w2, "", "w2"),
        ("Wr Three", "WR", 3500, 5.0, "", "w3"),
        ("Te One", "TE", 4000, 7.0, "", "t1"),
        ("Rb One", "RB", 6000, 14.0, avail_rb, "r1"),
        ("Rb Two", "RB", 4500, 8.0, "", "r2"),
    ]
    return pd.DataFrame(
        [
            {
                "Id": 100 + i,
                "Name": n,
                "Team": "DEN",
                "Opp": "KC",
                "Position": p,
                "Salary": sal,
                "ProjPts": proj,
                "Avail": avail,
                "RosterablePool": True,
                "GsisId": g,
            }
            for i, (n, p, sal, proj, avail, g) in enumerate(rows)
        ]
    )


def _data(**kw):
    base = {"season": 2026, "ffo_weeks": _ffo_weeks(), "depth": _depth()}
    return signals.SeasonData(**{**base, **kw})


def test_a_back_out_per_draftkings_gives_his_carries_to_the_next_back_by_the_measured_table():
    out = signals.build_signals(_frame(), _data(), week=4, depth_dt="2026-10-05T00:00:00Z")
    assert out.outs["Name"].tolist() == ["Rb One"] and out.outs.iloc[0]["Source"] == "DraftKings Avail"
    top = out.beneficiaries.iloc[0]
    assert top["Name"] == "Rb Two" and top["Method"] == ib.METHOD_TABLE
    players = out.players.set_index("Name")
    assert players.loc["Rb Two", "Edge"] == "INJ+"
    assert players.loc["Rb Two", "InjFrom"] == ib.STATUS_OUT
    # 12 carries a game x the measured next-up share, valued at his 8 rushing xFP over 12 carries
    assert players.loc["Rb Two", "InjGain"] == pytest.approx(rc.carry_shares("RB1").next_up * 8.0)
    assert players.loc["Rb One", "Edge"] == ""  # the out player himself is not tagged


def test_a_receiver_out_is_listed_as_an_absence_but_moves_no_points_and_fires_no_inj():
    out = signals.build_signals(
        _frame(avail_star="OUT", avail_rb=""), _data(), week=4, depth_dt="2026-10-05T00:00:00Z"
    )
    assert out.outs["Name"].tolist() == ["Star"]
    assert out.beneficiaries.empty
    assert (out.players["Edge"] == "").all() and out.players["InjGain"].isna().all()
    assert out.absences["Name"].tolist() == ["Star"] and out.absences.iloc[0]["Role"] == "WR1"
    assert "no single teammate gains much" in out.absences.iloc[0]["History"]


def test_the_injury_report_decides_when_draftkings_says_nothing_and_draftkings_wins_on_conflict():
    injuries = pd.DataFrame(
        [
            {
                "season": 2026,
                "week": 4,
                "Team": "DEN",
                "GsisId": "s",
                "Name": "Star",
                "Position": "WR",
                "report_status": "Out",
            },
            {
                "season": 2026,
                "week": 4,
                "Team": "DEN",
                "GsisId": "t1",
                "Name": "Te One",
                "Position": "TE",
                "report_status": "Out",
            },
            {
                "season": 2026,
                "week": 4,
                "Team": "DEN",
                "GsisId": "gone",
                "Name": "Not Listed",
                "Position": "WR",
                "report_status": "Questionable",
            },
        ]
    )
    frame = _frame(avail_star="Q")  # DraftKings says questionable, the report says out: DraftKings wins
    outs = signals.find_outs(frame, _data(injuries=injuries), week=4).set_index("Name")
    assert (
        outs.loc["Star", "Status"] == ib.STATUS_QUESTIONABLE
        and outs.loc["Star", "Source"] == "DraftKings Avail"
    )
    assert outs.loc["Te One", "Status"] == ib.STATUS_OUT and outs.loc["Te One", "Source"] == "injury report"
    # a report-only player DraftKings no longer lists is still found
    assert outs.loc["Not Listed", "Status"] == ib.STATUS_QUESTIONABLE
    # another week's report never counts
    assert signals.find_outs(_frame(avail_rb=""), _data(injuries=injuries), week=5).empty


def test_fade_fires_for_a_te_two_points_over_expected_and_never_for_a_wr():
    weeks = _ffo_weeks()
    weeks.loc[weeks["GsisId"] == "t1", "dk_actual"] = 8.0  # xFP 5.0/G, scored 8.0/G: +3.0
    weeks.loc[weeks["GsisId"] == "w2", "dk_actual"] = 20.0  # a WR far over expected: not faded
    out = signals.build_signals(_frame(avail_rb=""), _data(ffo_weeks=weeks), week=4)
    players = out.players.set_index("Name")
    assert xfp.TOKEN_FADE in players.loc["Te One", "Edge"]
    assert players.loc["Te One", "Games"] == 3 and players.loc["Te One", "xFP/G"] == pytest.approx(5.0)
    assert players.loc["Wr Two", "Edge"] == "" and players.loc["Wr Three", "Edge"] == ""
    assert "BUY↑" not in set(" ".join(players["Edge"]).split())  # BUY is gone for good


def test_buy_is_not_a_token_any_more_even_when_a_te_runs_far_under_expected():
    weeks = _ffo_weeks()
    weeks.loc[weeks["GsisId"] == "t1", "dk_actual"] = 0.0
    out = signals.build_signals(_frame(avail_rb=""), _data(ffo_weeks=weeks), week=4)
    assert (out.players["Edge"] == "").all()


def test_usage_fires_on_a_back_carry_share_jump_and_needs_eight_prior_games():
    weeks = _ffo_weeks(weeks=range(1, 9))
    last_two = weeks["GsisId"].eq("r1") & weeks["week"].isin([7, 8])
    weeks.loc[last_two, "carries"] = 18.0  # 18/25 = 72% against 48%: +24 points
    frame = _frame(avail_rb="")
    up = signals.build_signals(frame, _data(ffo_weeks=weeks), week=9).players.set_index("Name")
    assert up.loc["Rb One", "Edge"] == xfp.TOKEN_USAGE_UP and up.loc["Rb One", "UsageN"] == 8
    assert up.loc["Wr Two", "Edge"] == ""
    short = signals.build_signals(frame, _data(ffo_weeks=weeks[weeks["week"] <= 7]), week=8)
    assert short.players.set_index("Name").loc["Rb One", "Edge"] == ""  # seven prior games: no signal


def test_tokens_are_joined_in_a_fixed_order():
    joined = signals._join_tokens(pd.Series(["INJ+"]), pd.Series(["FADE↓"]), pd.Series(["USAGE↑"]))
    assert joined.iloc[0] == "INJ+ FADE↓ USAGE↑"
    swapped = signals._join_tokens(pd.Series(["USAGE↑"]), pd.Series(["INJ+"]))
    assert swapped.iloc[0] == "INJ+ USAGE↑"
    assert signals._join_tokens(pd.Series([""]), pd.Series([""])).iloc[0] == ""


def test_nothing_from_the_slate_week_or_later_changes_the_signal_no_lookahead():
    base = signals.build_signals(_frame(), _data(), week=4, depth_dt="2026-10-05T00:00:00Z")
    # add week 4 AND week 5 games, actuals, injuries and a later depth chart: week 4's signal must not move
    later_weeks = pd.concat(
        [_ffo_weeks(weeks=(1, 2, 3)), _ffo_weeks(weeks=(4, 5)).assign(xfp=99.0, dk_actual=99.0, targets=40.0)]
    )
    later_depth = pd.concat(
        [_depth(), _depth().assign(dt="2026-10-10T10:00:00Z", pos_rank=lambda d: 5 - d["pos_rank"])]
    )
    injuries = pd.DataFrame(
        [
            {
                "season": 2026,
                "week": 5,
                "Team": "DEN",
                "GsisId": "t1",
                "Name": "Te One",
                "Position": "TE",
                "report_status": "Out",
            }
        ]
    )
    again = signals.build_signals(
        _frame(),
        _data(ffo_weeks=later_weeks, depth=later_depth, injuries=injuries),
        week=4,
        depth_dt="2026-10-05T00:00:00Z",
    )
    pd.testing.assert_frame_equal(base.players.reset_index(drop=True), again.players.reset_index(drop=True))
    pd.testing.assert_frame_equal(base.beneficiaries, again.beneficiaries)
    pd.testing.assert_frame_equal(base.absences, again.absences)


def _dk_frame():
    return pd.DataFrame(
        [
            {
                "Id": "1",
                "Name": "Nick Westbrook-Ikhine",
                "Team": "IND",
                "Position": "WR",
                "RosterablePool": True,
            },
            {"Id": "2", "Name": "Star", "Team": "DEN", "Position": "WR", "RosterablePool": True},
            {"Id": "3", "Name": "Colts", "Team": "IND", "Position": "DST", "RosterablePool": True},
            {"Id": "4", "Name": "Other Player", "Team": "DEN", "Position": "WR", "RosterablePool": True},
        ]
    )


def _identity():
    return pd.DataFrame(
        [
            {"GsisId": "w", "Name": "Nick Westbrook-Ikhine", "Team": "MIA", "Position": "WR"},  # stale team
            {"GsisId": "s", "Name": "Star", "Team": "DEN", "Position": "WR"},
        ]
    )


def test_a_stale_identity_team_is_rescued_by_the_usage_crosswalk_by_draftkings_id_only():
    frame = _dk_frame()
    plain, join = signals.attach_gsis(frame, _identity())
    assert plain.set_index("Id").loc["2", "GsisId"] == "s" and pd.isna(
        plain.set_index("Id").loc["1", "GsisId"]
    )
    assert join.pool_matched == 1 and join.pool_total == 3
    crosswalk = pd.DataFrame(
        {"Id": ["1", "9"], "GsisId": ["00-W", "00-X"], "Name": ["Nick Westbrook-Ikhine", "Other Player"]}
    )
    fixed, join = signals.attach_gsis(frame, _identity(), crosswalk=crosswalk)
    by_id = fixed.set_index("Id")["GsisId"]
    assert by_id["1"] == "00-W" and by_id["2"] == "s"  # the crosswalk fills only what the join missed
    assert pd.isna(by_id["3"])  # a DST never gets one
    assert pd.isna(by_id["4"])  # a name match with a DIFFERENT DraftKings id is not a match: never by name
    assert join.pool_matched == 2 and join.pool_total == 3
    assert (
        "Nick Westbrook-Ikhine" not in join.unmatched_pool_names
        and "Other Player" in join.unmatched_pool_names
    )
    assert join.by_position["WR"] == (2, 3)


def test_the_crosswalk_never_overwrites_an_id_the_identity_join_found():
    crosswalk = pd.DataFrame({"Id": ["2"], "GsisId": ["WRONG"], "Name": ["Star"]})
    fixed, _ = signals.attach_gsis(_dk_frame(), _identity(), crosswalk=crosswalk)
    assert fixed.set_index("Id").loc["2", "GsisId"] == "s"


def test_nothing_from_matchups_flows_into_calpts():
    import inspect

    from dfs import calibration, probabilities

    for module in (calibration, probabilities):
        assert "matchups" not in inspect.getsource(module).replace("matchup_", "")  # never imported or used
    frame = _frame().assign(CalPts=[11.0, 9.0, 4.0, 6.0, 12.0, 7.0])
    metrics = pd.DataFrame({"Team": ["DEN", "KC"], "PaceNeutral": [27.0, 30.0], "PROE": [0.05, -0.02]})
    with_matchups = signals.build_signals(frame, _data(), week=4, team_metrics=metrics)
    without = signals.build_signals(frame, _data(), week=4)
    pd.testing.assert_series_equal(with_matchups.players["CalPts"], without.players["CalPts"])
    assert with_matchups.players["CalPts"].tolist() == frame["CalPts"].tolist()  # passed through untouched


def test_missing_sources_blank_only_their_own_signals():
    out = signals.build_signals(_frame(avail_star=""), signals.SeasonData(season=2026), week=4)
    assert (out.players["Edge"] == "").all() and out.beneficiaries.empty and out.matchups.empty
    assert out.players["xFP/G"].isna().all()
    assert len(out.players) == 6  # the table itself never shrinks


# ---- priced in --------------------------------------------------------------------------------


def _dk(status, name="Star"):
    return pd.DataFrame([{"Name": name, "TeamAbbrev": "DEN", "Status": status}])


def _proj(value, name="Wr Two"):
    return pd.DataFrame([{"Name": name, "Team": "DEN", "ProjPts": value}])


def test_designation_baseline_is_the_last_snapshot_before_he_was_listed_out():
    snaps = [
        ("20261001T120000Z", _dk(np.nan)),
        ("20261002T120000Z", _dk(np.nan)),
        ("20261003T120000Z", _dk("OUT")),
    ]
    assert signals.designation_baseline("Star", "DEN", snaps) == "20261002T120000Z"
    assert (
        signals.designation_baseline("Star", "DEN", [("20261003T120000Z", _dk("OUT"))]) is None
    )  # already out
    assert (
        signals.designation_baseline("Star", "DEN", [("20261001T120000Z", _dk(np.nan))]) is None
    )  # never out
    assert signals.designation_baseline("Nobody", "DEN", snaps) is None
    assert signals.designation_baseline("Star", "DEN", [("a", _dk(np.nan)), ("b", _dk("IR"))]) == "a"


def test_projection_at_reads_the_latest_snapshot_at_or_before_the_stamp():
    snaps = [("20261001T100000Z", _proj(8.0)), ("20261002T100000Z", _proj(9.5))]
    assert signals.projection_at("Wr Two", "DEN", "20261001T120000Z", snaps) == 8.0
    assert signals.projection_at("Wr Two", "DEN", "20261002T100000Z", snaps) == 9.5
    assert signals.projection_at("Wr Two", "DEN", "20260930T000000Z", snaps) is None
    assert signals.projection_at("Wr Two", "DEN", None, snaps) is None


def test_priced_in_compares_now_with_the_snapshot_before_the_designation():
    dk = [("20261001T120000Z", _dk(np.nan, "Rb One")), ("20261003T120000Z", _dk("OUT", "Rb One"))]
    gain = rc.carry_shares("RB1").next_up * 8.0  # Rb Two's gained xFP/G; priced in = 70% of it
    low, high = 8.0 + 0.5 * gain, 8.0 + 0.9 * gain
    for now, expected in ((low, ib.PRICED_NO), (high, ib.PRICED_YES)):
        frame = _frame().assign(ProjPts=lambda d, now=now: d["ProjPts"].where(d["Name"] != "Rb Two", now))
        out = signals.build_signals(
            frame,
            _data(),
            week=4,
            depth_dt="2026-10-05T00:00:00Z",
            dk_snapshots=dk,
            projection_snapshots=[("20261001T110000Z", _proj(8.0, "Rb Two"))],
        )
        assert out.beneficiaries.set_index("Name").loc["Rb Two", "PricedIn"] == expected, now
    # no snapshots, or no earlier one: BLANK, never guessed and never the word "unknown"
    unknown = signals.build_signals(_frame(), _data(), week=4, depth_dt="2026-10-05T00:00:00Z")
    assert unknown.beneficiaries.set_index("Name").loc["Rb Two", "PricedIn"] == ib.PRICED_UNKNOWN == ""
    no_early = signals.build_signals(
        _frame(),
        _data(),
        week=4,
        depth_dt="2026-10-05T00:00:00Z",
        dk_snapshots=dk,
        projection_snapshots=[("20261002T000000Z", _proj(8.0, "Rb Two"))],
    )
    assert no_early.beneficiaries.set_index("Name").loc["Rb Two", "PricedIn"] == ib.PRICED_UNKNOWN


def test_a_designation_that_predates_the_first_snapshot_cannot_be_priced():
    # the real Week 5 situation: the back was already OUT in the first DraftKings snapshot we hold
    dk = [("20261006T133000Z", _dk("OUT", "Rb One")), ("20261007T132100Z", _dk("OUT", "Rb One"))]
    out = signals.build_signals(
        _frame(),
        _data(),
        week=4,
        depth_dt="2026-10-05T00:00:00Z",
        dk_snapshots=dk,
        projection_snapshots=[("20261006T133500Z", _proj(8.0, "Rb Two"))],
    )
    assert out.beneficiaries.set_index("Name").loc["Rb Two", "PricedIn"] == ""


def test_implied_totals_come_from_the_projections_per_team():
    projections = pd.DataFrame({"Team": ["LA", "LA", "DEN"], "ImpPts": [24.0, 24.0, 20.5]})
    implied = signals.implied_by_team(projections)
    assert implied["LAR"] == 24.0 and implied["DEN"] == 20.5
    assert signals.implied_by_team(None).empty
