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
