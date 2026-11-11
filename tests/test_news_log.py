"""The news change log's diff (`news_log`): pure, one row per changed field."""

from datetime import UTC, datetime
from types import SimpleNamespace

import pandas as pd

from dfs import news_log as n

AT = datetime(2026, 11, 8, 16, 57, 2, tzinfo=UTC)
STEM = "20261108T165702Z"


def edge(**overrides) -> pd.DataFrame:
    """A four-player, two-game Week 9 slate; `overrides` replaces whole columns."""
    frame = pd.DataFrame(
        {
            "Id": [101, 102, 103, 104],
            "Name": ["Puka Nacua", "Kyren Williams", "George Kittle", "Brock Purdy"],
            "Position": ["WR", "RB", "TE", "QB"],
            "Team": ["LAR", "LAR", "SF", "SF"],
            "Opp": ["SEA", "SEA", "LV", "LV"],
            "GameID": ["2026_09_LAR_SEA", "2026_09_LAR_SEA", "2026_09_LV_SF", "2026_09_LV_SF"],
            "Avail": ["Q", "", "", ""],
            "ProjPts": [20.0, 16.5, 15.0, 19.2],
            "CalPts": [19.1, 15.9, 17.1, 18.8],
            "Own%": [0.21, 0.30, 0.32, 0.08],
            "CashOwn": [0.30, 0.45, 0.69, 0.10],
            "GppOwn": [0.22, 0.28, 0.42, 0.07],
            "Spread": [-3.5, -3.5, -9.5, -9.5],
            "OverUnder": [47.5, 47.5, 51.5, 51.5],
        }
    )
    for column, values in overrides.items():
        frame[column] = values
    return frame


def rows_of(previous, current, **kwargs):
    return n.diff(previous, current, at=AT, snapshot=STEM, **kwargs)


def brief(rows):
    return [(r["name"] or r["team"], r["field"], r["before"], r["after"]) for r in rows]


def test_an_unchanged_snapshot_logs_nothing():
    assert rows_of(edge(), edge()) == []


def test_a_status_change_is_one_row_with_the_time_the_player_the_values_and_the_source():
    rows = rows_of(edge(), edge(Avail=["OUT", "", "", ""]))
    assert rows == [
        {
            "at": "2026-11-08T16:57:02Z",
            "season": 2026,
            "week": 9,
            "player_id": "101",
            "name": "Puka Nacua",
            "position": "WR",
            "team": "LAR",
            "field": "status",
            "before": "Q",
            "after": "OUT",
            "source": "dk",
            "snapshot": STEM,
        }
    ]
    assert list(rows[0]) == n.COLUMNS


def test_every_field_the_spec_names_is_diffed_with_its_source():
    new = edge(
        Avail=["Q", "D", "", ""],
        ProjPts=[20.0, 12.0, 15.0, 19.2],
        CalPts=[19.1, 11.4, 17.1, 18.8],
        CashOwn=[0.30, 0.20, 0.69, 0.10],
        GppOwn=[0.22, 0.15, 0.42, 0.07],
    )
    new["Own%"] = [0.21, 0.18, 0.32, 0.08]
    rows = rows_of(edge(), new)
    assert [(r["field"], r["before"], r["after"], r["source"]) for r in rows] == [
        ("status", "", "D", "dk"),
        ("proj_tffb", "16.50", "12.00", "tffb"),
        ("calpts", "15.90", "11.40", "model"),
        ("own_tffb", "0.3000", "0.1800", "tffb"),
        ("cash_own", "0.4500", "0.2000", "model"),
        ("gpp_own", "0.2800", "0.1500", "model"),
    ]
    assert {r["name"] for r in rows} == {"Kyren Williams"}


def test_each_projection_source_has_its_own_field():
    extra = {
        "SleeperPts": [18.0, 15.0, 14.0, 18.0],
        "FantasyProsPts": [19.0, 16.0, 15.5, 19.0],
        "EspnPts": [17.0, 14.0, 13.0, 20.0],
        "PropPts": [18.5, 15.5, 14.5, 18.5],
        "AggPts": [19.0, 15.8, 14.8, 18.7],
    }
    moved = {name: [v + 1.0 for v in values] for name, values in extra.items()}
    rows = rows_of(edge(**extra), edge(**moved))
    nacua = [(r["field"], r["source"]) for r in rows if r["name"] == "Puka Nacua"]
    assert nacua == [
        ("proj_sleeper", "sleeper"),
        ("proj_fantasypros", "fantasypros"),
        ("proj_espn", "espn"),
        ("proj_props", "props"),
        ("proj_agg", "model"),
    ]


def test_spread_and_total_are_one_row_per_team_with_no_player():
    new = edge(Spread=[-4.5, -4.5, -9.5, -9.5], OverUnder=[47.5, 47.5, 50.5, 50.5])
    rows = rows_of(edge(), new)
    assert brief(rows) == [("LAR", "spread", "-3.5", "-4.5"), ("SF", "total", "51.5", "50.5")]
    assert all(r["player_id"] == "" and r["name"] == "" and r["source"] == "odds" for r in rows)


def test_a_difference_below_the_stored_precision_is_not_a_change():
    rows = rows_of(edge(), edge(ProjPts=[20.0000001, 16.5, 15.0, 19.2], CalPts=[19.1, 15.9, 17.1, 18.804]))
    assert rows == []
    assert brief(rows_of(edge(), edge(ProjPts=[20.01, 16.5, 15.0, 19.2]))) == [
        ("Puka Nacua", "proj_tffb", "20.00", "20.01")
    ]


def test_blank_to_a_value_is_a_change_and_blank_to_blank_is_not():
    unpublished = edge(CashOwn=[None, None, None, None], GppOwn=[float("nan")] * 4)
    assert rows_of(unpublished, unpublished.copy()) == []
    rows = rows_of(unpublished, edge(GppOwn=[float("nan")] * 4))
    assert brief(rows)[:2] == [
        ("Puka Nacua", "cash_own", "", "0.3000"),
        ("Kyren Williams", "cash_own", "", "0.4500"),
    ]
    assert len(rows) == 4
    assert brief(rows_of(edge(), edge(Avail=["", "", "", ""]))) == [("Puka Nacua", "status", "Q", "")]


def test_a_column_missing_from_either_snapshot_logs_nothing_for_that_field():
    old = edge().drop(columns=["CalPts", "CashOwn"])
    assert rows_of(old, edge()) == []  # the code grew two columns: not news
    assert rows_of(edge(), old) == []


def test_a_player_new_to_the_slate_or_gone_from_it_is_not_a_field_change():
    fewer = edge().iloc[:3]
    assert rows_of(fewer, edge()) == []
    assert rows_of(edge(), fewer) == []


def test_ids_read_back_as_floats_or_text_still_match():
    old = edge(Id=[101.0, 102.0, 103.0, 104.0])
    new = edge(Id=["101", "102", "103", "104"], Avail=["OUT", "", "", ""])
    assert brief(rows_of(old, new)) == [("Puka Nacua", "status", "Q", "OUT")]


def test_tffb_ownership_under_its_first_header_is_put_on_the_fraction_scale():
    old = edge().drop(columns=["Own%"])
    old["ProjOwn"] = [21.0, 30.0, 32.0, 8.0]  # 0-100 until 2026-09-17
    assert rows_of(old, edge()) == []
    new = edge()
    new["Own%"] = [0.25, 0.30, 0.32, 0.08]
    assert brief(rows_of(old, new)) == [("Puka Nacua", "own_tffb", "0.2100", "0.2500")]


def test_no_previous_snapshot_and_a_new_slate_are_baselines_with_no_rows():
    assert rows_of(None, edge()) == []
    next_week = edge(
        Opp=["ARI", "ARI", "DAL", "DAL"],
        GameID=["2026_10_ARI_LAR"] * 2 + ["2026_10_DAL_SF"] * 2,
        ProjPts=[1.0, 2.0, 3.0, 4.0],
        Avail=["", "OUT", "", ""],
    )
    assert n.same_slate(edge(), edge()) and not n.same_slate(edge(), next_week)
    assert rows_of(edge(), next_week) == []


def test_the_week_is_the_slates_own_and_the_calendar_only_without_game_ids():
    assert n.week_of(edge(), AT) == (2026, 9)
    late_stamp = datetime(2026, 11, 10, 15, 0, tzinfo=UTC)  # a Tuesday: Week 10 by the calendar
    assert n.week_of(edge(), late_stamp) == (2026, 9)
    assert n.week_of(edge().drop(columns=["GameID"]), late_stamp) == (2026, 10)
    # Monday night Eastern is already Tuesday in UTC: the Eastern day decides
    monday_night = datetime(2026, 11, 10, 3, 0, tzinfo=UTC)
    assert n.week_of(edge().drop(columns=["GameID"]), monday_night) == (2026, 9)


def test_replay_walks_snapshots_in_order_and_starts_over_at_a_new_slate():
    week10 = edge(Opp=["ARI", "ARI", "DAL", "DAL"], GameID=["2026_10_ARI_LAR"] * 2 + ["2026_10_DAL_SF"] * 2)
    walked = list(
        n.replay(
            [
                ("20261107T230109Z", edge()),
                ("20261108T165702Z", edge(Avail=["OUT", "", "", ""])),
                ("20261110T150000Z", week10),
                ("20261111T150000Z", week10.assign(ProjPts=[21.0, 16.5, 15.0, 19.2])),
            ]
        )
    )
    assert [(stem, brief(rows)) for stem, rows in walked] == [
        ("20261107T230109Z", []),
        ("20261108T165702Z", [("Puka Nacua", "status", "Q", "OUT")]),
        ("20261110T150000Z", []),
        ("20261111T150000Z", [("Puka Nacua", "proj_tffb", "20.00", "21.00")]),
    ]
    assert walked[1][1][0]["week"] == 9 and walked[3][1][0]["week"] == 10
    assert walked[1][1][0]["at"] == "2026-11-08T16:57:02Z"


def test_replay_keeps_the_week_of_an_old_slate_still_showing_on_tuesday_without_game_ids():
    legacy = edge().drop(columns=["GameID"])
    walked = dict(
        n.replay(
            [
                ("20261108T165702Z", legacy),  # Sunday of Week 9
                ("20261110T150000Z", legacy.assign(ProjPts=[0.0, 16.5, 15.0, 19.2])),  # Tuesday, same games
            ]
        )
    )
    assert [r["week"] for r in walked["20261110T150000Z"]] == [9]


def test_espn_rows_are_status_rows_once_per_player_and_status():
    updated = datetime(2026, 11, 8, 17, 15, tzinfo=UTC)
    changes = [
        SimpleNamespace(name="Puka Nacua", before="Q", after="OUT", updated=updated, note="hamstring"),
        SimpleNamespace(name="Brock Purdy", before="", after="Q", updated=None, note=""),
        SimpleNamespace(name="Nobody Here", before="", after="OUT", updated=None, note=""),
    ]
    rows = n.espn_rows(edge(), changes, at=AT)
    assert [(r["name"], r["before"], r["after"], r["source"], r["snapshot"]) for r in rows] == [
        ("Puka Nacua", "Q", "OUT", "espn_injuries", "espn:20261108T171500Z"),
        ("Brock Purdy", "", "Q", "espn_injuries", "espn"),
    ]
    assert rows[0]["player_id"] == "101" and rows[0]["week"] == 9 and rows[0]["field"] == "status"
    again = n.espn_rows(edge(), changes, at=AT, logged={("101", "OUT")})
    assert [r["name"] for r in again] == ["Brock Purdy"]
