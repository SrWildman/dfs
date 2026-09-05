import pandas as pd

from dfs.derived import (
    CHALK_OWNERSHIP_THRESHOLD,
    EDGE_COLUMNS,
    LEV_BASIS_PROXY,
    LEV_BASIS_REAL,
    LEVERAGE_FLAG_THRESHOLD_PROXY,
    LEVERAGE_FLAG_THRESHOLD_REAL,
    build_edge_frame,
)


def _projections(rows: list[dict]) -> pd.DataFrame:
    base = {
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
    return pd.DataFrame([{**base, **r} for r in rows])


def _salaries(rows: list[dict]) -> pd.DataFrame:
    base = {
        "Position": "RB",
        "Name + ID": "",
        "Name": "",
        "ID": "",
        "Roster Position": "RB",
        "Salary": 8000,
        "Game Info": "",
        "TeamAbbrev": "DET",
        "AvgPointsPerGame": 15.0,
        "Status": "",
    }
    if not rows:
        return pd.DataFrame(columns=list(base.keys()))
    return pd.DataFrame([{**base, **r} for r in rows])


def test_join_is_exact_on_id_and_unmatched_players_are_reported_not_dropped():
    proj = _projections(
        [
            {"Id": "1", "Name": "Matched Player", "ProjPts": 20.0},
            {"Id": "2", "Name": "Thursday Only Player", "ProjPts": 10.0},
        ]
    )
    sal = _salaries([{"ID": "1", "Salary": 8000}])

    result = build_edge_frame(proj, sal)

    assert result.unmatched_names == ["Thursday Only Player"]
    assert len(result.frame) == 2  # nobody dropped
    assert "Thursday Only Player" in result.frame["Name"].tolist()


def test_unmatched_player_falls_back_to_projections_own_salary():
    proj = _projections([{"Id": "2", "Name": "No DK Match", "ProjPts": 10.0, "Salary": 4500}])
    sal = _salaries([])

    result = build_edge_frame(proj, sal)

    assert result.frame.iloc[0]["Salary"] == 4500


def test_val_and_ceilval_are_points_per_thousand_salary():
    proj = _projections([{"Id": "1", "Name": "P", "ProjPts": 20.0, "Ceiling": 30.0, "Salary": 8000}])
    sal = _salaries([{"ID": "1", "Salary": 8000}])

    row = build_edge_frame(proj, sal).frame.iloc[0]

    assert row["Val"] == 2.5
    assert row["CeilVal"] == 3.75


def test_ceilpct_is_blank_when_ceiling_missing():
    proj = _projections(
        [
            {"Id": "1", "Name": "Has ceiling", "Ceiling": 30.0},
            {"Id": "2", "Name": "No ceiling", "Ceiling": float("nan")},
        ]
    )
    sal = _salaries([{"ID": "1"}, {"ID": "2"}])

    frame = build_edge_frame(proj, sal).frame

    no_ceil_row = frame[frame["Name"] == "No ceiling"].iloc[0]
    assert pd.isna(no_ceil_row["CeilPct"])


def test_leverage_degenerates_to_ceilpct_when_all_projown_is_zero():
    proj = _projections(
        [
            {"Id": "1", "Name": "Low ceiling", "Position": "RB", "Ceiling": 10.0, "ProjOwn": 0},
            {"Id": "2", "Name": "High ceiling", "Position": "RB", "Ceiling": 40.0, "ProjOwn": 0},
        ]
    )
    sal = _salaries([{"ID": "1"}, {"ID": "2"}])

    frame = build_edge_frame(proj, sal).frame

    assert (frame["LevBasis"] == LEV_BASIS_PROXY).all()
    for _, row in frame.iterrows():
        assert row["Leverage"] == row["CeilPct"]


def test_leverage_uses_real_ownership_once_any_player_has_nonzero_projown():
    proj = _projections(
        [
            {"Id": "1", "Name": "A", "Position": "RB", "Ceiling": 40.0, "ProjOwn": 30.0},
            {"Id": "2", "Name": "B", "Position": "RB", "Ceiling": 10.0, "ProjOwn": 0},
        ]
    )
    sal = _salaries([{"ID": "1"}, {"ID": "2"}])

    frame = build_edge_frame(proj, sal).frame

    assert (frame["LevBasis"] == LEV_BASIS_REAL).all()
    a = frame[frame["Name"] == "A"].iloc[0]
    assert a["Leverage"] == round(a["CeilPct"] - 30.0, 1)


def test_avail_reflects_dk_status_and_out_flag_overrides_leverage():
    proj = _projections(
        [{"Id": "1", "Name": "Hurt but leveraged", "Position": "RB", "Ceiling": 100.0, "ProjOwn": 0}]
    )
    sal = _salaries([{"ID": "1", "Status": "OUT"}])

    row = build_edge_frame(proj, sal).frame.iloc[0]

    assert row["Avail"] == "OUT"
    assert row["Flag"] == "OUT"


def test_leverage_flag_set_above_proxy_threshold_when_ownership_is_all_zero():
    proj = _projections(
        [
            {"Id": "1", "Name": "Leveraged", "Position": "RB", "Ceiling": 100.0, "ProjOwn": 0},
            {"Id": "2", "Name": "Filler", "Position": "RB", "Ceiling": 1.0, "ProjOwn": 0},
        ]
    )
    sal = _salaries([{"ID": "1"}, {"ID": "2"}])

    frame = build_edge_frame(proj, sal).frame
    top = frame.iloc[0]
    assert top["Name"] == "Leveraged"
    assert top["Leverage"] >= LEVERAGE_FLAG_THRESHOLD_PROXY
    assert top["Flag"] == "LEVERAGE"


def test_leverage_flag_uses_a_much_higher_bar_under_proxy_than_real_basis():
    # In proxy mode Leverage == CeilPct (0..100, centered ~50): 10 RBs so
    # "Merely above average" (60th percentile ceiling) sits well above the
    # median but must NOT get flagged, or every sync before TFFB computes
    # real ownership would flag most of the slate.
    rows = [
        {"Id": str(i), "Name": f"Filler {i}", "Position": "RB", "Ceiling": float(i * 10), "ProjOwn": 0}
        for i in range(1, 10)
        if i != 6
    ]
    rows.append({"Id": "6", "Name": "Merely above average", "Position": "RB", "Ceiling": 60.0, "ProjOwn": 0})
    proj = _projections(rows)
    sal = _salaries([{"ID": str(i)} for i in range(1, 10)])

    frame = build_edge_frame(proj, sal).frame
    top = frame[frame["Name"] == "Merely above average"].iloc[0]
    assert top["Leverage"] < LEVERAGE_FLAG_THRESHOLD_PROXY
    assert top["Flag"] == ""


def test_chalk_flag_set_for_high_ownership_under_real_basis():
    # 5 RBs so Chalky's rock-bottom ceiling lands at the 20th percentile,
    # keeping Leverage (CeilPct - ProjOwn) well under LEVERAGE_FLAG_THRESHOLD_REAL
    # despite high ownership -- otherwise LEVERAGE would win first.
    proj = _projections(
        [
            {
                "Id": "1",
                "Name": "Chalky",
                "Position": "RB",
                "Ceiling": 5.0,
                "ProjOwn": CHALK_OWNERSHIP_THRESHOLD + 5,
            },
            {"Id": "2", "Name": "B", "Position": "RB", "Ceiling": 20.0, "ProjOwn": 1.0},
            {"Id": "3", "Name": "C", "Position": "RB", "Ceiling": 30.0, "ProjOwn": 1.0},
            {"Id": "4", "Name": "D", "Position": "RB", "Ceiling": 40.0, "ProjOwn": 1.0},
            {"Id": "5", "Name": "E", "Position": "RB", "Ceiling": 50.0, "ProjOwn": 1.0},
        ]
    )
    sal = _salaries([{"ID": str(i)} for i in range(1, 6)])

    frame = build_edge_frame(proj, sal).frame
    chalky = frame[frame["Name"] == "Chalky"].iloc[0]
    assert chalky["Leverage"] < LEVERAGE_FLAG_THRESHOLD_REAL
    assert chalky["Flag"] == "CHALK"


def test_game_env_scores_higher_total_and_tighter_spread_higher():
    proj = _projections(
        [
            {
                "Id": "1",
                "Name": "Shootout",
                "Game": "High total, tight spread",
                "OU": 55.0,
                "Spread": -1.0,
            },
            {
                "Id": "2",
                "Name": "Blowout",
                "Game": "Low total, wide spread",
                "OU": 38.0,
                "Spread": -14.0,
            },
        ]
    )
    sal = _salaries([{"ID": "1"}, {"ID": "2"}])

    frame = build_edge_frame(proj, sal).frame
    shootout = frame[frame["Name"] == "Shootout"].iloc[0]
    blowout = frame[frame["Name"] == "Blowout"].iloc[0]
    assert shootout["GameEnv"] > blowout["GameEnv"]


def test_frame_is_sorted_by_leverage_descending():
    proj = _projections(
        [
            {"Id": "1", "Name": "Low", "Position": "RB", "Ceiling": 5.0, "ProjOwn": 0},
            {"Id": "2", "Name": "High", "Position": "RB", "Ceiling": 50.0, "ProjOwn": 0},
            {"Id": "3", "Name": "Mid", "Position": "RB", "Ceiling": 25.0, "ProjOwn": 0},
        ]
    )
    sal = _salaries([{"ID": "1"}, {"ID": "2"}, {"ID": "3"}])

    frame = build_edge_frame(proj, sal).frame
    assert frame["Name"].tolist() == ["High", "Mid", "Low"]


def _games(rows: list[dict]) -> pd.DataFrame:
    base = {"GameId": "g1", "Away": "DET", "Home": "NO", "Stadium": "Caesars Superdome", "Roof": "dome"}
    return pd.DataFrame([{**base, **r} for r in rows])


def test_columns_present_and_blank_when_games_and_weather_not_synced():
    proj = _projections([{"Id": "1", "Name": "P", "Team": "DET"}])
    sal = _salaries([{"ID": "1"}])

    frame = build_edge_frame(proj, sal).frame
    assert list(frame.columns) == EDGE_COLUMNS
    row = frame.iloc[0]
    assert pd.isna(row["Stadium"])
    assert pd.isna(row["Roof"])
    assert pd.isna(row["Wind"])


def test_stadium_and_roof_joined_from_games_by_team_code():
    proj = _projections(
        [
            {"Id": "1", "Name": "Away player", "Team": "DET"},
            {"Id": "2", "Name": "Home player", "Team": "NO"},
        ]
    )
    sal = _salaries([{"ID": "1"}, {"ID": "2"}])
    games = _games([{"Away": "DET", "Home": "NO", "Stadium": "Caesars Superdome", "Roof": "dome"}])

    frame = build_edge_frame(proj, sal, games=games).frame
    for _, row in frame.iterrows():
        assert row["Stadium"] == "Caesars Superdome"
        assert row["Roof"] == "dome"


def test_wind_joined_from_weather_by_game_and_flagged_over_threshold():
    proj = _projections(
        [{"Id": "1", "Name": "P", "Team": "DET", "Position": "RB", "Ceiling": 1.0, "ProjOwn": 0}]
    )
    sal = _salaries([{"ID": "1"}])
    games = _games([{"GameId": "g1", "Away": "DET", "Home": "NO"}])
    weather = pd.DataFrame([{"GameId": "g1", "Wind": 25.0}])

    row = build_edge_frame(proj, sal, games=games, weather=weather).frame.iloc[0]
    assert row["Wind"] == 25.0
    assert row["Flag"] == "WIND"


def test_out_flag_takes_priority_over_wind_flag():
    proj = _projections(
        [{"Id": "1", "Name": "P", "Team": "DET", "Position": "RB", "Ceiling": 1.0, "ProjOwn": 0}]
    )
    sal = _salaries([{"ID": "1", "Status": "OUT"}])
    games = _games([{"GameId": "g1", "Away": "DET", "Home": "NO"}])
    weather = pd.DataFrame([{"GameId": "g1", "Wind": 25.0}])

    row = build_edge_frame(proj, sal, games=games, weather=weather).frame.iloc[0]
    assert row["Flag"] == "OUT"


def test_dst_name_rewritten_to_dk_nickname_for_downstream_joins():
    proj = _projections(
        [
            {
                "Id": "1",
                "Name": "Jacksonville Jaguars",
                "Position": "DST",
                "Team": "JAX",
                "Opp": "CLE",
            }
        ]
    )
    sal = _salaries([{"ID": "1"}])

    row = build_edge_frame(proj, sal).frame.iloc[0]
    assert row["Name"] == "Jaguars"


def test_non_dst_names_are_left_untouched():
    proj = _projections([{"Id": "1", "Name": "Ja'Marr Chase", "Position": "WR"}])
    sal = _salaries([{"ID": "1"}])

    row = build_edge_frame(proj, sal).frame.iloc[0]
    assert row["Name"] == "Ja'Marr Chase"


def test_line_move_blank_when_not_provided():
    proj = _projections([{"Id": "1", "Name": "P", "Team": "DET"}])
    sal = _salaries([{"ID": "1"}])

    row = build_edge_frame(proj, sal).frame.iloc[0]
    assert pd.isna(row["LineMove"])


def test_line_move_joined_by_team_and_flagged():
    proj = _projections(
        [{"Id": "1", "Name": "P", "Team": "DET", "Position": "RB", "Ceiling": 1.0, "ProjOwn": 0}]
    )
    sal = _salaries([{"ID": "1"}])
    line_movement = pd.DataFrame([{"Abbr": "DET", "TeamPointsDelta": 2.5}])

    row = build_edge_frame(proj, sal, line_movement=line_movement).frame.iloc[0]
    assert row["LineMove"] == 2.5
    assert row["Flag"] == "LINE↑"


def test_line_move_down_flag():
    proj = _projections(
        [{"Id": "1", "Name": "P", "Team": "DET", "Position": "RB", "Ceiling": 1.0, "ProjOwn": 0}]
    )
    sal = _salaries([{"ID": "1"}])
    line_movement = pd.DataFrame([{"Abbr": "DET", "TeamPointsDelta": -2.5}])

    row = build_edge_frame(proj, sal, line_movement=line_movement).frame.iloc[0]
    assert row["Flag"] == "LINE↓"


def test_out_flag_takes_priority_over_line_move_flag():
    proj = _projections(
        [{"Id": "1", "Name": "P", "Team": "DET", "Position": "RB", "Ceiling": 1.0, "ProjOwn": 0}]
    )
    sal = _salaries([{"ID": "1", "Status": "OUT"}])
    line_movement = pd.DataFrame([{"Abbr": "DET", "TeamPointsDelta": 2.5}])

    row = build_edge_frame(proj, sal, line_movement=line_movement).frame.iloc[0]
    assert row["Flag"] == "OUT"
