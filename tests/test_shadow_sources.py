"""EspnPts and PropPts: the EdgeRaw columns, how the results loop scores them, the kickoff rule for props."""

from datetime import UTC, datetime
from types import SimpleNamespace

import pandas as pd

from dfs import derived, results_loop


def test_the_shadow_columns_sit_in_the_sources_group_on_the_tab_and_are_appended_to_the_frame():
    order = derived.EDGE_SHEET_ORDER
    # the regroup (2026-10-22): behind the SRC label, with the other projection sources, after the spine
    start = order.index("SRC") + 1
    assert order[start : start + 4] == ["SleeperPts", "FantasyProsPts", "EspnPts", "PropPts"]
    assert order.index("Edge") < order.index("SRC") and order.index("Val") < order.index("SRC")
    after = len(derived.FIELD_OWN_COLUMNS) + len(derived.ONE_PLACE_COLUMNS) + len(derived.EDGE_ONLY_LABELS)
    shadow = derived.EDGE_COLUMNS[-2 - after : -after]
    assert shadow == ["EspnPts", "PropPts"]  # append-only: nothing earlier moved
    assert sorted(order) == sorted(derived.EDGE_COLUMNS)


def test_the_edge_frame_leaves_the_shadow_columns_blank_until_the_sources_fill_them():
    from dfs.edge_finder import EDGE_SOURCE_COLUMNS

    assert EDGE_SOURCE_COLUMNS == ["EspnPts", "PropPts"]


def _frame():
    return pd.DataFrame(
        {
            "Id": [101, 102, 103],
            "Name": ["Josh Allen", "Puka Nacua", "Bench Guy"],
            "Team": ["BUF", "LAR", "BUF"],
            "Position": ["QB", "WR", "WR"],
        }
    )


def _inputs(**kw):
    base = {"sleeper": None, "fantasypros": None}
    return SimpleNamespace(**{**base, **kw})


def test_espn_is_joined_like_every_other_source_and_props_by_dk_id():
    espn = pd.DataFrame(
        {
            "Name": ["Josh Allen", "Puka Nacua"],
            "Team": ["BUF", "LAR"],
            "Position": ["QB", "WR"],
            "DkPts": [21.5, 14.2],
        }
    )
    props = pd.DataFrame({"Id": ["101", "102"], "PropPts": [19.0, 15.5]})  # ids as strings: a CSV round trip
    out = results_loop.source_projection_columns(_frame(), _inputs(espn=espn, props=props)).set_index("Id")
    assert (
        out.loc[101, "EspnPts"] == 21.5
        and out.loc[102, "EspnPts"] == 14.2
        and pd.isna(out.loc[103, "EspnPts"])
    )
    assert (
        out.loc[101, "PropPts"] == 19.0
        and out.loc[102, "PropPts"] == 15.5
        and pd.isna(out.loc[103, "PropPts"])
    )


def test_without_espn_or_props_the_columns_are_blank_never_zero():
    out = results_loop.source_projection_columns(_frame(), _inputs())
    assert out["EspnPts"].isna().all() and out["PropPts"].isna().all()


def test_a_blank_proppts_stays_blank():
    props = pd.DataFrame({"Id": [101], "PropPts": [float("nan")]})
    out = results_loop.source_projection_columns(_frame(), _inputs(props=props))
    assert out["PropPts"].isna().all()


def test_scored_files_carry_the_shadow_columns():
    assert results_loop.SCORED_COLUMNS[-2:] == ["EspnPts", "PropPts"]


def test_a_players_props_come_from_the_last_snapshot_before_his_own_kickoff(monkeypatch):
    """Saturday's pull has both players; Sunday's 11 am refresh re-priced A (1 pm game) but B's 9:30 game had
    started; a snapshot taken after A's kickoff never counts."""
    sat = datetime(2026, 10, 17, 15, tzinfo=UTC)
    sun_morning = datetime(2026, 10, 18, 15, 0, tzinfo=UTC)  # 11:00 ET
    sun_late = datetime(2026, 10, 18, 18, 0, tzinfo=UTC)  # 2:00 ET: after A's 1 pm kickoff
    a = {"Id": "1", "GameStart": "2026-10-18T13:00:00Z"}  # 1:00 pm ET, TFFB's wall-clock convention
    b = {"Id": "2", "GameStart": "2026-10-18T09:30:00Z"}  # 9:30 am ET: started before the 11 am refresh
    frames = {
        "sat": pd.DataFrame([{**a, "PropPts": 10.0}, {**b, "PropPts": 8.0}]),
        "sun": pd.DataFrame([{**a, "PropPts": 12.0}]),
        "late": pd.DataFrame([{**a, "PropPts": 99.0}]),
    }
    snapshots = [(sat, "sat"), (sun_morning, "sun"), (sun_late, "late")]
    monkeypatch.setattr(results_loop, "snapshot_files", lambda source: [(ts, key) for ts, key in snapshots])
    monkeypatch.setattr(results_loop, "read_snapshot", lambda key: frames[key])
    chosen = results_loop.props_for_week(6, 2026).set_index("Id")
    assert chosen.loc["1", "PropPts"] == 12.0  # Sunday's refresh, not Saturday's and not the post-kickoff one
    assert chosen.loc["2", "PropPts"] == 8.0  # Saturday's: his game had started by Sunday's refresh


def test_no_props_ever_pulled_means_no_inputs(monkeypatch):
    monkeypatch.setattr(results_loop, "snapshot_files", lambda source: [])
    assert results_loop.props_for_week(6, 2026) is None


def test_the_edge_frame_really_carries_blank_source_columns_the_merge_has_to_replace():
    """The cause of the 0-of-3 bug: `build_edge_frame` hands `score_week` these columns already, blank."""
    projections = pd.DataFrame(
        [
            {
                "Id": "1",
                "Name": "Matched Player",
                "Position": "RB",
                "Team": "DET",
                "Opp": "NO",
                "ProjPts": 15.0,
                "ProjOwn": 0,
                "Salary": 8000,
                "Ceiling": 30.0,
                "ImpPts": 25.0,
                "OU": 46.5,
                "Spread": -3.0,
                "Game": "Detroit Lions_New Orleans Saints",
                "GameStart": "2026-09-13T17:00:00Z",
                "Venue": "H",
            }
        ]
    )
    salaries = pd.DataFrame(
        [
            {
                "Position": "RB",
                "Name + ID": "",
                "Name": "",
                "ID": "1",
                "Roster Position": "RB",
                "Salary": 8000,
                "Game Info": "",
                "TeamAbbrev": "DET",
                "AvgPointsPerGame": 15.0,
                "Status": "",
            }
        ]
    )
    frame = derived.build_edge_frame(projections, salaries).frame
    for column in ("EspnPts", "PropPts", "SleeperPts", "FantasyProsPts"):
        assert column in frame.columns and frame[column].isna().all()


def test_score_week_keeps_the_sources_values_when_the_edge_frame_already_has_the_columns():
    # An ESPN snapshot as `data/raw/espn/*.csv` is written (DkPts plus the stat lines) and a props snapshot
    # as `data/raw/odds_props/*.csv` is (one row per player, DK Id, GameStart, PropPts, Missing, Flags).
    espn = pd.DataFrame(
        {
            "Name": ["Josh Allen", "Puka Nacua"],
            "Team": ["BUF", "LAR"],
            "Position": ["QB", "WR"],
            "DkPts": [26.95, 24.34],
            "pass_yd": [250.77, 0.0],
            "rush_yd": [44.06, 4.81],
        }
    )
    sleeper = espn.assign(DkPts=[24.0, 18.0])
    props = pd.DataFrame(
        {
            "Id": [101, 102],
            "Name": ["Josh Allen", "Puka Nacua"],
            "GameStart": ["2026-10-18T13:00:00Z"] * 2,
            "PropPts": [25.85, float("nan")],
            "Missing": ["", "rush_yd"],
            "Flags": ["td:yes_only", ""],
        }
    )
    blank = float("nan")
    frame = _frame().assign(EspnPts=blank, PropPts=blank, SleeperPts=blank, FantasyProsPts=blank)
    out = results_loop.attach_source_projections(
        frame, _inputs(espn=espn, props=props, sleeper=sleeper)
    ).set_index("Id")
    assert [c for c in out.columns if c.endswith(("_x", "_y"))] == []
    assert out.loc[101, "EspnPts"] == 26.95 and out.loc[102, "EspnPts"] == 24.34
    assert out.loc[101, "PropPts"] == 25.85 and pd.isna(out.loc[102, "PropPts"])
    assert out.loc[101, "SleeperPts"] == 24.0 and out.loc[102, "SleeperPts"] == 18.0
    assert pd.isna(out.loc[103, "EspnPts"])


def test_espn_comes_from_the_last_snapshot_before_each_teams_own_kickoff(monkeypatch):
    # BUF plays Thursday night, LAR on Sunday. Three ESPN snapshots: Wednesday, Thursday after BUF kicked
    # off, Sunday morning. BUF must keep Wednesday's number; LAR gets Sunday's; a team the slate does not
    # know (SEA) falls back to the latest snapshot at or before the reference moment.
    def snap(allen, nacua, seattle):
        return pd.DataFrame(
            {
                "Name": ["Josh Allen", "Puka Nacua", "Geno Smith"],
                "Team": ["BUF", "LAR", "SEA"],
                "Position": ["QB", "WR", "QB"],
                "DkPts": [allen, nacua, seattle],
            }
        )

    stamps = [
        datetime(2026, 10, 21, 15, tzinfo=UTC),  # Wednesday
        datetime(
            2026, 10, 23, 2, tzinfo=UTC
        ),  # Thursday night, BUF already kicked off (8:15 pm ET = 00:15 UTC)
        datetime(2026, 10, 25, 14, tzinfo=UTC),  # Sunday morning
    ]
    snaps = dict(
        zip(stamps, [snap(20.0, 10.0, 15.0), snap(26.0, 11.0, 16.0), snap(27.0, 12.0, 17.0)], strict=True)
    )
    monkeypatch.setattr(results_loop, "snapshot_files", lambda source: [(t, t) for t in stamps])
    monkeypatch.setattr(results_loop, "read_snapshot", lambda t: snaps[t])
    selected = pd.DataFrame(
        {
            "Team": ["BUF", "LAR"],
            "GameStart": [
                "2026-10-22T20:15:00Z",
                "2026-10-25T13:00:00Z",
            ],  # TFFB writes Eastern time with a Z
        }
    )
    out = results_loop.espn_before_kickoff(selected, datetime(2026, 10, 25, 16, 30, tzinfo=UTC))
    by_team = out.set_index("Team")["DkPts"]
    assert by_team["BUF"] == 20.0
    assert by_team["LAR"] == 12.0
    assert by_team["SEA"] == 17.0
    assert len(out) == 3
