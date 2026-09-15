import pandas as pd

from dfs.derived import (
    CHALK_OWNERSHIP_THRESHOLD,
    EDGE_COLUMNS,
    LEV_BASIS_REAL,
    LEV_BASIS_UNPUBLISHED,
    LEVERAGE_FLAG_THRESHOLD,
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


def test_leverage_and_ownpct_are_blank_when_all_projown_is_zero():
    proj = _projections(
        [
            {"Id": "1", "Name": "Low ceiling", "Position": "RB", "Ceiling": 10.0, "ProjOwn": 0},
            {"Id": "2", "Name": "High ceiling", "Position": "RB", "Ceiling": 40.0, "ProjOwn": 0},
        ]
    )
    sal = _salaries([{"ID": "1"}, {"ID": "2"}])

    frame = build_edge_frame(proj, sal).frame

    assert (frame["LevBasis"] == LEV_BASIS_UNPUBLISHED).all()
    assert frame["Leverage"].isna().all()
    assert frame["OwnPct"].isna().all()
    # Blank Leverage isn't sortable, so the tab still ranks by CeilPct.
    assert frame["Name"].tolist() == ["High ceiling", "Low ceiling"]


def test_leverage_is_ceilpct_minus_ownpct_once_any_player_has_nonzero_projown():
    proj = _projections(
        [
            {"Id": "1", "Name": "A", "Position": "RB", "Ceiling": 40.0, "ProjOwn": 30.0},
            {"Id": "2", "Name": "B", "Position": "RB", "Ceiling": 10.0, "ProjOwn": 0},
        ]
    )
    sal = _salaries([{"ID": "1"}, {"ID": "2"}])

    frame = build_edge_frame(proj, sal).frame

    assert (frame["LevBasis"] == LEV_BASIS_REAL).all()
    for _, row in frame.iterrows():
        assert row["Leverage"] == round(row["CeilPct"] - row["OwnPct"], 1)


def test_ownpct_is_percentile_rank_of_projown_within_position_not_raw_percentage():
    # Raw ProjOwn subtraction was the bug: a percentile (0-100, mean 50) minus
    # a raw right-skewed percentage (mostly under 5, a few 25-40) centers
    # nowhere near 0. Rank-normalizing ProjOwn the same way Ceiling already
    # is fixes that -- verify OwnPct actually lands on the percentile scale.
    proj = _projections(
        [
            {"Id": "1", "Name": "A", "Position": "RB", "Ceiling": 10.0, "ProjOwn": 2.0},
            {"Id": "2", "Name": "B", "Position": "RB", "Ceiling": 20.0, "ProjOwn": 4.0},
            {"Id": "3", "Name": "C", "Position": "RB", "Ceiling": 30.0, "ProjOwn": 35.0},
            {"Id": "4", "Name": "D", "Position": "RB", "Ceiling": 40.0, "ProjOwn": 3.0},
        ]
    )
    sal = _salaries([{"ID": str(i)} for i in range(1, 5)])

    frame = build_edge_frame(proj, sal).frame
    c = frame[frame["Name"] == "C"].iloc[0]
    a = frame[frame["Name"] == "A"].iloc[0]
    assert c["OwnPct"] == 100.0  # highest ProjOwn in the group
    assert a["OwnPct"] == 25.0  # lowest ProjOwn in the group
    # C has both the highest ceiling AND the highest ownership -- real
    # leverage (a ceiling edge net of ownership) should be much lower than
    # its raw CeilPct alone, since owning C isn't contrarian.
    assert c["Leverage"] < c["CeilPct"]


def test_avail_reflects_dk_status_and_out_flag_overrides_leverage():
    proj = _projections(
        [{"Id": "1", "Name": "Hurt but leveraged", "Position": "RB", "Ceiling": 100.0, "ProjOwn": 0}]
    )
    sal = _salaries([{"ID": "1", "Status": "OUT"}])

    row = build_edge_frame(proj, sal).frame.iloc[0]

    assert row["Avail"] == "OUT"
    assert row["Flag"] == "OUT"


def test_leverage_flag_never_fires_while_ownership_is_unpublished():
    # Even the highest-ceiling player in the slate must not get flagged
    # LEVERAGE before TFFB publishes real ownership -- Leverage is blank in
    # that window (see test_leverage_and_ownpct_are_blank_when_all_projown_is_zero),
    # and a blank can never clear a threshold.
    proj = _projections(
        [
            {"Id": "1", "Name": "Highest ceiling", "Position": "RB", "Ceiling": 100.0, "ProjOwn": 0},
            {"Id": "2", "Name": "Filler", "Position": "RB", "Ceiling": 1.0, "ProjOwn": 0},
        ]
    )
    sal = _salaries([{"ID": "1"}, {"ID": "2"}])

    frame = build_edge_frame(proj, sal).frame
    top = frame[frame["Name"] == "Highest ceiling"].iloc[0]
    assert top["Flag"] == ""


def test_leverage_flag_fires_at_threshold_under_real_ownership():
    # 10 RBs, evenly spread ceiling and ownership but inversely ranked, so
    # the lowest-owned/highest-ceiling player's gap clears the real threshold.
    rows = [
        {
            "Id": str(i),
            "Name": f"Player {i}",
            "Position": "RB",
            "Ceiling": float(i * 10),
            "ProjOwn": float(110 - i * 10),
        }
        for i in range(1, 11)
    ]
    proj = _projections(rows)
    sal = _salaries([{"ID": str(i)} for i in range(1, 11)])

    frame = build_edge_frame(proj, sal).frame
    top = frame[frame["Name"] == "Player 10"].iloc[0]  # highest ceiling, lowest ownership
    assert top["Leverage"] >= LEVERAGE_FLAG_THRESHOLD
    assert top["Flag"] == "LEVERAGE"


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
    assert chalky["Leverage"] < LEVERAGE_FLAG_THRESHOLD
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


def test_frame_falls_back_to_ceilpct_sort_when_ownership_unpublished():
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


def test_frame_is_sorted_by_leverage_descending_once_ownership_is_real():
    proj = _projections(
        [
            {"Id": "1", "Name": "Low leverage", "Position": "RB", "Ceiling": 5.0, "ProjOwn": 40.0},
            {"Id": "2", "Name": "High leverage", "Position": "RB", "Ceiling": 50.0, "ProjOwn": 1.0},
            {"Id": "3", "Name": "Mid leverage", "Position": "RB", "Ceiling": 25.0, "ProjOwn": 20.0},
        ]
    )
    sal = _salaries([{"ID": "1"}, {"ID": "2"}, {"ID": "3"}])

    frame = build_edge_frame(proj, sal).frame
    assert frame["Name"].tolist() == ["High leverage", "Mid leverage", "Low leverage"]


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


def test_out_and_wind_flags_both_shown_out_first():
    proj = _projections(
        [{"Id": "1", "Name": "P", "Team": "DET", "Position": "RB", "Ceiling": 1.0, "ProjOwn": 0}]
    )
    sal = _salaries([{"ID": "1", "Status": "OUT"}])
    games = _games([{"GameId": "g1", "Away": "DET", "Home": "NO"}])
    weather = pd.DataFrame([{"GameId": "g1", "Wind": 25.0}])

    row = build_edge_frame(proj, sal, games=games, weather=weather).frame.iloc[0]
    assert row["Flag"] == "OUT WIND"


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
    assert pd.isna(row["ImpMove"])
    assert pd.isna(row["TotMove"])
    assert pd.isna(row["SpdMove"])


def test_line_move_joined_by_team_and_flagged():
    # Fix 2.2: all three of diff_odds()'s deltas are surfaced now, not
    # just the team-implied-points one (ImpMove, was "LineMove").
    proj = _projections(
        [{"Id": "1", "Name": "P", "Team": "DET", "Position": "RB", "Ceiling": 1.0, "ProjOwn": 0}]
    )
    sal = _salaries([{"ID": "1"}])
    line_movement = pd.DataFrame(
        [{"Abbr": "DET", "TeamPointsDelta": 2.5, "TotalDelta": 1.0, "SpreadDelta": -0.5}]
    )

    row = build_edge_frame(proj, sal, line_movement=line_movement).frame.iloc[0]
    assert row["ImpMove"] == 2.5
    assert row["TotMove"] == 1.0
    assert row["SpdMove"] == -0.5
    assert row["Flag"] == "LINE↑"


def test_line_move_down_flag():
    proj = _projections(
        [{"Id": "1", "Name": "P", "Team": "DET", "Position": "RB", "Ceiling": 1.0, "ProjOwn": 0}]
    )
    sal = _salaries([{"ID": "1"}])
    line_movement = pd.DataFrame(
        [{"Abbr": "DET", "TeamPointsDelta": -2.5, "TotalDelta": 0.0, "SpreadDelta": 0.0}]
    )

    row = build_edge_frame(proj, sal, line_movement=line_movement).frame.iloc[0]
    assert row["Flag"] == "LINE↓"


def test_line_move_flag_keys_off_impmove_not_totmove_or_spdmove():
    # A big TotMove/SpdMove with a flat ImpMove must NOT trigger LINE↑/↓ --
    # only ImpMove (team implied points) drives that flag (Fix 2.2).
    proj = _projections(
        [{"Id": "1", "Name": "P", "Team": "DET", "Position": "RB", "Ceiling": 1.0, "ProjOwn": 0}]
    )
    sal = _salaries([{"ID": "1"}])
    line_movement = pd.DataFrame(
        [{"Abbr": "DET", "TeamPointsDelta": 0.0, "TotalDelta": 5.0, "SpreadDelta": -5.0}]
    )

    row = build_edge_frame(proj, sal, line_movement=line_movement).frame.iloc[0]
    assert row["TotMove"] == 5.0
    assert row["SpdMove"] == -5.0
    assert row["Flag"] == ""


def test_out_and_line_move_flags_both_shown_out_first():
    proj = _projections(
        [{"Id": "1", "Name": "P", "Team": "DET", "Position": "RB", "Ceiling": 1.0, "ProjOwn": 0}]
    )
    sal = _salaries([{"ID": "1", "Status": "OUT"}])
    line_movement = pd.DataFrame(
        [{"Abbr": "DET", "TeamPointsDelta": 2.5, "TotalDelta": 0.0, "SpreadDelta": 0.0}]
    )

    row = build_edge_frame(proj, sal, line_movement=line_movement).frame.iloc[0]
    assert row["Flag"] == "OUT LINE↑"


def test_multiple_flags_shown_in_priority_order():
    # WIND, LEVERAGE and CHALK are independent conditions and can all be
    # true of the same player at once (Fix 2.1 -- first-match-wins used to
    # silently hide all but the first).
    proj = _projections(
        [
            {"Id": "1", "Name": "Leveraged", "Position": "RB", "Ceiling": 100.0, "ProjOwn": 1.0},
            {"Id": "2", "Name": "Filler", "Position": "RB", "Ceiling": 1.0, "ProjOwn": 50.0},
        ]
    )
    sal = _salaries([{"ID": "1"}, {"ID": "2"}])
    games = _games([{"GameId": "g1", "Away": "DET", "Home": "NO"}])
    weather = pd.DataFrame([{"GameId": "g1", "Wind": 25.0}])

    frame = build_edge_frame(proj, sal, games=games, weather=weather).frame
    row = frame[frame["Name"] == "Leveraged"].iloc[0]
    assert row["Flag"] == "WIND LEVERAGE"


def test_game_start_passes_through_from_projections():
    proj = _projections([{"Id": "1", "Name": "P", "GameStart": "2026-09-14T20:20:00Z"}])
    sal = _salaries([{"ID": "1"}])

    row = build_edge_frame(proj, sal).frame.iloc[0]
    assert row["GameStart"] == "2026-09-14T20:20:00Z"


def test_over_under_and_spread_surfaced_on_edgeraw():
    # Fix 2.3: OU/Spread were already used to compute GameEnv but never
    # exposed as their own EdgeRaw columns. Named OverUnder (not OU) so it
    # doesn't collide with Player Pool/Lineups' own "O/U" header, which
    # comes from a different tab (oddsFinal via PlayerPoolRaw).
    proj = _projections([{"Id": "1", "Name": "P", "OU": 47.5, "Spread": -3.5}])
    sal = _salaries([{"ID": "1"}])

    row = build_edge_frame(proj, sal).frame.iloc[0]
    assert row["OverUnder"] == 47.5
    assert row["Spread"] == -3.5
