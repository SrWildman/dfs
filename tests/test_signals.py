"""The signal table end to end on a tiny league: tokens, outs, beneficiaries, priced-in, no lookahead."""

import numpy as np
import pandas as pd
import pytest

from dfs import injury_beneficiaries as ib
from dfs import signals, xfp


def _ffo_weeks(season=2026, weeks=(1, 2, 3), star_missed=()):
    """DEN's passing game: Star WR1, Wr Two, Wr Three, Te One, Rb One. xFP/targets are fixed per game."""
    spec = {
        "s": ("Star", "WR", 10.0, 0.0, 12.0, 14.0),
        "w2": ("Wr Two", "WR", 6.0, 0.0, 6.0, 6.0),
        "w3": ("Wr Three", "WR", 3.0, 0.0, 3.0, 3.0),
        "t1": ("Te One", "TE", 5.0, 0.0, 5.0, 5.0),
        "r1": ("Rb One", "RB", 2.0, 12.0, 10.0, 11.0),
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
                    "rec_xfp": x if pos != "RB" else 2.0,
                    "rush_xfp": 0.0 if pos != "RB" else 8.0,
                    "pass_xfp": 0.0,
                    "xfp": x,
                    "td": 0.0,
                    "td_exp": 0.0,
                    "dk_actual": actual,
                }
            )
    return pd.DataFrame(rows)


def _depth():
    ranks = {"s": ("WR", 1), "w2": ("WR", 2), "w3": ("WR", 3), "t1": ("TE", 1), "r1": ("RB", 1)}
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


def _frame(avail_star="OUT", proj_w2=11.0):
    rows = [
        ("Star", "WR", 8000, 15.0, avail_star, "s"),
        ("Wr Two", "WR", 5000, proj_w2, "", "w2"),
        ("Wr Three", "WR", 3500, 5.0, "", "w3"),
        ("Te One", "TE", 4000, 7.0, "", "t1"),
        ("Rb One", "RB", 6000, 14.0, "", "r1"),
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


def test_a_player_out_per_draftkings_makes_his_depth_chart_successor_a_beneficiary():
    out = signals.build_signals(_frame(), _data(), week=4, depth_dt="2026-10-05T00:00:00Z")
    assert out.outs["Name"].tolist() == ["Star"] and out.outs.iloc[0]["Source"] == "DraftKings Avail"
    top = out.beneficiaries.iloc[0]
    assert top["Name"] == "Wr Two" and top["Method"] == ib.METHOD_DEPTH
    players = out.players.set_index("Name")
    assert players.loc["Wr Two", "Edge"] == "INJ+"
    assert players.loc["Wr Two", "InjFrom"] == ib.STATUS_OUT
    assert players.loc["Wr Two", "InjGain"] == pytest.approx(7.2)  # 60% of Star's 12 receiving xFP
    assert players.loc["Star", "Edge"] == ""  # the out player himself is not tagged


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
    assert signals.find_outs(_frame(avail_star=""), _data(injuries=injuries), week=5).empty


def test_buy_token_appears_when_actual_runs_well_under_expected():
    weeks = _ffo_weeks()
    weeks.loc[weeks["GsisId"] == "w2", "dk_actual"] = 2.0  # xFP 6.0/G, scored 2.0/G: a 4-point gap
    out = signals.build_signals(_frame(avail_star=""), _data(ffo_weeks=weeks), week=4)
    players = out.players.set_index("Name")
    assert xfp.TOKEN_BUY in players.loc["Wr Two", "Edge"]
    assert players.loc["Wr Two", "Games"] == 3 and players.loc["Wr Two", "xFP/G"] == pytest.approx(6.0)
    assert players.loc["Wr Three", "Edge"] == ""


def test_tokens_are_joined_in_a_fixed_order():
    joined = signals._join_tokens(pd.Series(["INJ+"]), pd.Series(["BUY↑"]), pd.Series(["USAGE↑"]))
    assert joined.iloc[0] == "INJ+ BUY↑ USAGE↑"
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


def test_missing_sources_blank_only_their_own_signals():
    out = signals.build_signals(_frame(avail_star=""), signals.SeasonData(season=2026), week=4)
    assert (out.players["Edge"] == "").all() and out.beneficiaries.empty and out.matchups.empty
    assert out.players["xFP/G"].isna().all()
    assert len(out.players) == 5  # the table itself never shrinks


# ---- priced in --------------------------------------------------------------------------------


def _dk(status):
    return pd.DataFrame([{"Name": "Star", "TeamAbbrev": "DEN", "Status": status}])


def _proj(value):
    return pd.DataFrame([{"Name": "Wr Two", "Team": "DEN", "ProjPts": value}])


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
    dk = [("20261001T120000Z", _dk(np.nan)), ("20261003T120000Z", _dk("OUT"))]
    # gain is 7.2 xFP/G, so 70% = 5.04: a 3.0-point rise is not enough, 6.0 is
    for now, expected in ((11.0, ib.PRICED_NO), (14.0, ib.PRICED_YES)):
        out = signals.build_signals(
            _frame(proj_w2=now),
            _data(),
            week=4,
            depth_dt="2026-10-05T00:00:00Z",
            dk_snapshots=dk,
            projection_snapshots=[("20261001T110000Z", _proj(8.0))],
        )
        row = out.beneficiaries.set_index("Name").loc["Wr Two"]
        assert row["PricedIn"] == expected, now
    # no snapshots, or no earlier one: unknown, never guessed
    unknown = signals.build_signals(_frame(), _data(), week=4, depth_dt="2026-10-05T00:00:00Z")
    assert unknown.beneficiaries.set_index("Name").loc["Wr Two", "PricedIn"] == ib.PRICED_UNKNOWN
    no_early = signals.build_signals(
        _frame(),
        _data(),
        week=4,
        depth_dt="2026-10-05T00:00:00Z",
        dk_snapshots=dk,
        projection_snapshots=[("20261002T000000Z", _proj(8.0))],
    )
    assert no_early.beneficiaries.set_index("Name").loc["Wr Two", "PricedIn"] == ib.PRICED_UNKNOWN


def test_implied_totals_come_from_the_projections_per_team():
    projections = pd.DataFrame({"Team": ["LA", "LA", "DEN"], "ImpPts": [24.0, 24.0, 20.5]})
    implied = signals.implied_by_team(projections)
    assert implied["LAR"] == 24.0 and implied["DEN"] == 20.5
    assert signals.implied_by_team(None).empty
