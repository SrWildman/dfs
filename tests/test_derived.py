import pandas as pd

from dfs import derived
from dfs.derived import (
    CHALK_OWNERSHIP_THRESHOLD,
    EDGE_COLUMNS,
    LEVERAGE_FLAG_TOP_SHARE,
    LINE_MOVE_FLAG_THRESHOLD,
    OWN_STATUS_REAL,
    OWN_STATUS_UNPUBLISHED,
    PLAYER_METRIC_PCT_COLUMNS,
    ZONE_LABELS,
    _percentile_against_pool,
    _percentile_within,
    _rosterable_pool_mask,
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

    assert (frame["OwnStatus"] == OWN_STATUS_UNPUBLISHED).all()
    assert frame["Leverage"].isna().all()


def test_leverage_is_ceilpct_minus_ownpct_once_ownership_is_published_for_most_of_the_slate():
    # Part 7.9 dropped OwnPct from the sheet-facing frame (its only
    # consumer anywhere was this one subtraction) -- recompute it here,
    # the same way `build_edge_frame` does internally, to verify Leverage
    # independently rather than reading a value the frame no longer has.
    proj = _projections(
        [
            {"Id": "1", "Name": "A", "Position": "RB", "Ceiling": 40.0, "ProjOwn": 30.0},
            {"Id": "2", "Name": "B", "Position": "RB", "Ceiling": 10.0, "ProjOwn": 5.0},
            {"Id": "3", "Name": "C", "Position": "RB", "Ceiling": 20.0, "ProjOwn": 0},
        ]
    )
    sal = _salaries([{"ID": "1"}, {"ID": "2"}, {"ID": "3"}])

    frame = build_edge_frame(proj, sal).frame
    assert "OwnPct" not in frame.columns

    by_id = proj.set_index("Id")
    expected_own_pct = _percentile_within(by_id["ProjOwn"] / 100, by_id["Position"])
    assert (frame["OwnStatus"] == OWN_STATUS_REAL).all()
    for _, row in frame.iterrows():
        assert row["Leverage"] == round(row["CeilPct"] - expected_own_pct.round(1)[row["Id"]], 1)


def test_a_single_early_nonzero_projown_does_not_flip_the_whole_slate_to_real():
    # Phase 6, Part 1.4: `.any()` used to mean one early-published (or
    # glitched) non-zero ProjOwn switched the ENTIRE slate to "real" --
    # reproduced live: OwnStatus (LevBasis at the time) read "real" while
    # every other ProjOwn on EdgeRaw still read 0.0% and every Leverage
    # cell was blank. A share threshold (more than half the slate)
    # requires ownership to be genuinely published, not just present for
    # one player.
    proj = _projections(
        [
            {"Id": "1", "Name": "A", "Position": "RB", "Ceiling": 40.0, "ProjOwn": 30.0},
            {"Id": "2", "Name": "B", "Position": "RB", "Ceiling": 10.0, "ProjOwn": 0},
            {"Id": "3", "Name": "C", "Position": "RB", "Ceiling": 20.0, "ProjOwn": 0},
            {"Id": "4", "Name": "D", "Position": "RB", "Ceiling": 30.0, "ProjOwn": 0},
        ]
    )
    sal = _salaries([{"ID": "1"}, {"ID": "2"}, {"ID": "3"}, {"ID": "4"}])

    frame = build_edge_frame(proj, sal).frame

    assert (frame["OwnStatus"] == OWN_STATUS_UNPUBLISHED).all()
    assert frame["Leverage"].isna().all()


def test_ownership_published_share_is_measured_against_the_rosterable_pool_not_the_whole_list():
    # Week 3 follow-ups, Item 4: TFFB only ever publishes ownership for
    # players who'll actually be rostered, so a share measured over every
    # player DK lists tops out around 38% and can never cross 0.5 (the
    # real symptom, live all season: OwnStatus stuck "unpublished" and
    # Leverage blank even with ownership clearly out). Reproduced here with
    # a shrunk RB cap (3): 2 of the 3 pool players have real ProjOwn (a
    # 66.7% pool share, published), but 3 zero-ProjOwn backups sitting
    # outside the pool dilute the whole-list share to 2/6 = 33.3% --
    # exactly the shape that used to read "unpublished."
    proj = _projections(
        [
            {
                "Id": "1",
                "Name": "Pool A",
                "Position": "RB",
                "ProjPts": 30.0,
                "Ceiling": 40.0,
                "ProjOwn": 25.0,
            },
            {
                "Id": "2",
                "Name": "Pool B",
                "Position": "RB",
                "ProjPts": 25.0,
                "Ceiling": 20.0,
                "ProjOwn": 10.0,
            },
            {"Id": "3", "Name": "Pool C", "Position": "RB", "ProjPts": 20.0, "Ceiling": 15.0, "ProjOwn": 0},
            {"Id": "4", "Name": "Backup A", "Position": "RB", "ProjPts": 2.0, "Ceiling": 5.0, "ProjOwn": 0},
            {"Id": "5", "Name": "Backup B", "Position": "RB", "ProjPts": 1.5, "Ceiling": 4.0, "ProjOwn": 0},
            {"Id": "6", "Name": "Backup C", "Position": "RB", "ProjPts": 1.0, "Ceiling": 3.0, "ProjOwn": 0},
        ]
    )
    sal = _salaries([{"ID": str(i)} for i in range(1, 7)])

    original = derived.VAL_ADJ_ROSTERABLE_TOP_N
    try:
        derived.VAL_ADJ_ROSTERABLE_TOP_N = {**original, "RB": 3}
        frame = build_edge_frame(proj, sal).frame
    finally:
        derived.VAL_ADJ_ROSTERABLE_TOP_N = original

    assert (frame["OwnStatus"] == OWN_STATUS_REAL).all()
    assert not frame["Leverage"].isna().all()


def test_ownership_published_share_still_reads_unpublished_when_the_whole_pool_is_zero():
    # The all-zeros case must still read unpublished once the denominator
    # is pool-scoped -- this isn't just "a smaller list always passes."
    proj = _projections(
        [
            {"Id": "1", "Name": "A", "Position": "RB", "ProjPts": 30.0, "Ceiling": 40.0, "ProjOwn": 0},
            {"Id": "2", "Name": "B", "Position": "RB", "ProjPts": 25.0, "Ceiling": 20.0, "ProjOwn": 0},
            {"Id": "3", "Name": "C", "Position": "RB", "ProjPts": 20.0, "Ceiling": 15.0, "ProjOwn": 0},
        ]
    )
    sal = _salaries([{"ID": "1"}, {"ID": "2"}, {"ID": "3"}])

    original = derived.VAL_ADJ_ROSTERABLE_TOP_N
    try:
        derived.VAL_ADJ_ROSTERABLE_TOP_N = {**original, "RB": 3}
        frame = build_edge_frame(proj, sal).frame
    finally:
        derived.VAL_ADJ_ROSTERABLE_TOP_N = original

    assert (frame["OwnStatus"] == OWN_STATUS_UNPUBLISHED).all()
    assert frame["Leverage"].isna().all()


def test_ownpct_is_percentile_rank_of_projown_within_position_not_raw_percentage():
    # Raw ProjOwn subtraction was the bug: a percentile (0-100, mean 50) minus
    # a raw right-skewed percentage (mostly under 5, a few 25-40) centers
    # nowhere near 0. Rank-normalizing ProjOwn the same way Ceiling already
    # is fixes that -- verify Leverage against an independently-computed
    # OwnPct (Part 7.9 dropped OwnPct itself from the sheet-facing frame;
    # its only consumer anywhere was this one subtraction).
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
    assert "OwnPct" not in frame.columns

    by_id = proj.set_index("Id")
    expected_own_pct = _percentile_within(by_id["ProjOwn"] / 100, by_id["Position"])
    assert expected_own_pct["3"] == 100.0  # highest ProjOwn in the group (C)
    assert expected_own_pct["1"] == 25.0  # lowest ProjOwn in the group (A)

    c = frame[frame["Name"] == "C"].iloc[0]
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
    assert row["Flags"] == "OUT"


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
    assert top["Flags"] == ""


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
    assert top["Flags"] == "LEVERAGE"
    # 10-player pool, top 7% rounds up to 1 -- only the single best player
    # by Leverage should ever be flagged here.
    assert (frame["Flags"] == "LEVERAGE").sum() == 1


def test_chalk_flag_set_for_high_ownership_under_real_basis():
    # 5 RBs so Chalky's rock-bottom ceiling lands at the 20th percentile,
    # keeping Leverage (CeilPct - ProjOwn) far from the top of the pool
    # despite high ownership -- otherwise LEVERAGE would win first.
    proj = _projections(
        [
            {
                "Id": "1",
                "Name": "Chalky",
                "Position": "RB",
                "Ceiling": 5.0,
                # Raw TFFB-style input (a percentage-as-number, matching the
                # other rows' "1.0" meaning 1%) -- build_edge_frame divides
                # this by 100 before comparing against
                # CHALK_OWNERSHIP_THRESHOLD, which is on the resulting
                # fraction scale (0.20, not 20.0).
                "ProjOwn": (CHALK_OWNERSHIP_THRESHOLD + 0.05) * 100,
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
    assert chalky["Flags"] == "CHALK"


def test_leverage_flag_never_fires_outside_the_rosterable_pool():
    # PROMPT_LEVERAGE_FLAG.md's real bug: CeilPct/OwnPct (and so Leverage)
    # are percentiles over EVERY player DK lists, not just the pool -- so a
    # cheap backup with a tiny ProjPts can still post the single highest
    # Leverage on the whole slate. It must never be flagged; only pool
    # members are eligible, whatever their Leverage.
    proj = _projections(
        [
            {
                "Id": "1",
                "Name": "Pool A",
                "Position": "RB",
                "ProjPts": 30.0,
                "Ceiling": 20.0,
                "ProjOwn": 15.0,
            },
            {
                "Id": "2",
                "Name": "Pool B",
                "Position": "RB",
                "ProjPts": 25.0,
                "Ceiling": 15.0,
                "ProjOwn": 20.0,
            },
            {
                "Id": "3",
                "Name": "Backup with highest Leverage",
                "Position": "RB",
                "ProjPts": 1.0,
                "Ceiling": 100.0,
                "ProjOwn": 0.1,
            },
        ]
    )
    sal = _salaries([{"ID": "1"}, {"ID": "2"}, {"ID": "3"}])

    original = derived.VAL_ADJ_ROSTERABLE_TOP_N
    try:
        derived.VAL_ADJ_ROSTERABLE_TOP_N = {**original, "RB": 2}
        frame = build_edge_frame(proj, sal).frame
    finally:
        derived.VAL_ADJ_ROSTERABLE_TOP_N = original

    backup = frame[frame["Name"] == "Backup with highest Leverage"].iloc[0]
    assert backup["Leverage"] == frame["Leverage"].max()
    assert "LEVERAGE" not in backup["Flags"]
    assert (frame["Flags"] == "LEVERAGE").sum() == 1


def test_leverage_flag_fires_for_exactly_the_top_share_of_the_pool():
    # 200 pool players with strictly increasing Leverage -- top 7% is
    # exactly ceil(200 * 0.07) = 14 players, no ties to complicate it.
    n = 200
    rows = [
        {
            "Id": str(i),
            "Name": f"Player {i}",
            "Position": "RB",
            "ProjPts": 10.0 + i * 0.01,  # keeps rank order stable, all in pool
            "Ceiling": float(i),
            "ProjOwn": float(n + 1 - i),
        }
        for i in range(1, n + 1)
    ]
    proj = _projections(rows)
    sal = _salaries([{"ID": str(i)} for i in range(1, n + 1)])

    original = derived.VAL_ADJ_ROSTERABLE_TOP_N
    try:
        derived.VAL_ADJ_ROSTERABLE_TOP_N = {**original, "RB": n}
        frame = build_edge_frame(proj, sal).frame
    finally:
        derived.VAL_ADJ_ROSTERABLE_TOP_N = original

    import math

    expected_count = math.ceil(n * LEVERAGE_FLAG_TOP_SHARE)
    flagged = frame[frame["Flags"] == "LEVERAGE"]
    assert len(flagged) == expected_count
    expected_names = {f"Player {i}" for i in range(n - expected_count + 1, n + 1)}
    assert set(flagged["Name"]) == expected_names


def test_leverage_flag_includes_ties_at_the_cutoff():
    # Two pool players tied for the single highest Leverage -- both must be
    # flagged even though the top share alone (ceil(5 * 0.07) = 1) would
    # only ask for one.
    proj = _projections(
        [
            {"Id": "1", "Name": "Tied A", "Position": "RB", "ProjPts": 30.0, "Ceiling": 50.0, "ProjOwn": 5.0},
            {"Id": "2", "Name": "Tied B", "Position": "RB", "ProjPts": 29.0, "Ceiling": 50.0, "ProjOwn": 5.0},
            {"Id": "3", "Name": "C", "Position": "RB", "ProjPts": 20.0, "Ceiling": 30.0, "ProjOwn": 15.0},
            {"Id": "4", "Name": "D", "Position": "RB", "ProjPts": 15.0, "Ceiling": 20.0, "ProjOwn": 25.0},
            {"Id": "5", "Name": "E", "Position": "RB", "ProjPts": 10.0, "Ceiling": 10.0, "ProjOwn": 35.0},
        ]
    )
    sal = _salaries([{"ID": str(i)} for i in range(1, 6)])

    original = derived.VAL_ADJ_ROSTERABLE_TOP_N
    try:
        derived.VAL_ADJ_ROSTERABLE_TOP_N = {**original, "RB": 5}
        frame = build_edge_frame(proj, sal).frame
    finally:
        derived.VAL_ADJ_ROSTERABLE_TOP_N = original

    flagged = frame[frame["Flags"] == "LEVERAGE"]
    assert set(flagged["Name"]) == {"Tied A", "Tied B"}


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


def test_team_metrics_blank_when_pbp_unavailable():
    proj = _projections([{"Id": "1", "Name": "A", "Team": "DET"}])
    sal = _salaries([{"ID": "1"}])
    frame = build_edge_frame(proj, sal, team_metrics=None).frame
    row = frame[frame["Name"] == "A"].iloc[0]
    assert pd.isna(row["Pace"])
    assert pd.isna(row["PROE"])
    assert pd.isna(row["Expl%"])


def test_team_metrics_attached_by_team():
    proj = _projections(
        [
            {"Id": "1", "Name": "A", "Team": "DET"},
            {"Id": "2", "Name": "B", "Team": "NO"},
        ]
    )
    sal = _salaries([{"ID": "1"}, {"ID": "2"}])
    team_metrics = pd.DataFrame(
        {"Team": ["DET", "NO"], "Pace": [30.0, 40.0], "PROE": [5.0, -5.0], "Expl%": [10.0, 8.0]}
    )
    frame = build_edge_frame(proj, sal, team_metrics=team_metrics).frame
    det = frame[frame["Name"] == "A"].iloc[0]
    no = frame[frame["Name"] == "B"].iloc[0]
    assert det["Pace"] == 30.0
    assert det["PROE"] == 5.0
    assert det["Expl%"] == 10.0
    assert no["Pace"] == 40.0
    assert no["PROE"] == -5.0
    assert no["Expl%"] == 8.0


def test_game_env_without_team_metrics_matches_pre_c7_formula():
    # Fail-soft: missing team_metrics entirely must renormalize to exactly
    # the old 50/50 total/spread-tightness formula, not merely "close."
    proj = _projections(
        [
            {"Id": "1", "Name": "Shootout", "Game": "G1", "OU": 55.0, "Spread": -1.0, "Team": "DET"},
            {"Id": "2", "Name": "Blowout", "Game": "G2", "OU": 38.0, "Spread": -14.0, "Team": "NO"},
        ]
    )
    sal = _salaries([{"ID": "1"}, {"ID": "2"}])
    frame = build_edge_frame(proj, sal).frame
    shootout = frame[frame["Name"] == "Shootout"].iloc[0]
    blowout = frame[frame["Name"] == "Blowout"].iloc[0]
    # Only two games -> pandas' rank(pct=True) over 2 values gives the
    # loser 1/2=50% and the winner 2/2=100%, not 0%/100%. Shootout wins
    # both total (ou_pct 100) and tightness (tightness_pct 50, since its
    # own |spread|=1 is the tighter of the two) -> (100+50)/2 = 75; the
    # exact pre-C7 formula's own answer for these same two axes.
    assert shootout["GameEnv"] == 75.0
    assert blowout["GameEnv"] == 25.0


def test_model_implied_is_gone_from_the_edge_frame():
    """Round 5 item 5c: GPS's "Implied Total" is Vegas, not a model, so the
    per-player `ModelImplied` column was removed (see `gps_check.py`)."""
    proj = _projections([{"Id": "1", "Name": "A", "Team": "DET"}])
    sal = _salaries([{"ID": "1"}])
    frame = build_edge_frame(proj, sal).frame
    assert "ModelImplied" not in frame.columns
    assert "ModelImplied" not in EDGE_COLUMNS


def test_game_env_uses_combined_team_pace_and_proe_not_player_weighted():
    # Two games, identical OU/Spread, so any GameEnv difference must come
    # from Pace/PROE alone. Game A's two teams are both fast/pass-heavy
    # (good for GameEnv); Game B's are both slow/run-heavy.
    proj = _projections(
        [
            {"Id": "1", "Name": "A1", "Game": "GameA", "OU": 45.0, "Spread": -3.0, "Team": "DET"},
            {"Id": "2", "Name": "A2", "Game": "GameA", "OU": 45.0, "Spread": -3.0, "Team": "NO"},
            {"Id": "3", "Name": "B1", "Game": "GameB", "OU": 45.0, "Spread": -3.0, "Team": "KC"},
            {"Id": "4", "Name": "B2", "Game": "GameB", "OU": 45.0, "Spread": -3.0, "Team": "DEN"},
        ]
    )
    sal = _salaries([{"ID": "1"}, {"ID": "2"}, {"ID": "3"}, {"ID": "4"}])
    team_metrics = pd.DataFrame(
        {
            "Team": ["DET", "NO", "KC", "DEN"],
            "Pace": [28.0, 28.0, 40.0, 40.0],
            "PROE": [10.0, 10.0, -10.0, -10.0],
            "Expl%": [10.0, 10.0, 10.0, 10.0],
        }
    )
    frame = build_edge_frame(proj, sal, team_metrics=team_metrics).frame
    game_a = frame[frame["Name"] == "A1"].iloc[0]
    game_b = frame[frame["Name"] == "B1"].iloc[0]
    assert game_a["GameEnv"] > game_b["GameEnv"]


def test_frame_is_sorted_by_valadj_descending_regardless_of_ownership_status():
    # Part 7.1/7.2: Leverage is no longer a primary sort anywhere, and its
    # old CeilPct fallback (for the ownership-unpublished window) goes
    # with it -- ValAdj is EdgeRaw's one default sort now, and (unlike
    # Leverage) never depends on ownership having published at all. Same
    # ProjPts/Salary combination -- with real, non-uniform Salary so each
    # position's own regression actually has something to fit against --
    # produces the identical order whether ProjOwn is all zero
    # (unpublished) or varied (published): "Overperformer" (Salary 5500,
    # ProjPts 20) projects well above what this trio's own pricing
    # implies for RB; "Underperformer" (Salary 6000, ProjPts 11) is the
    # most expensive and the weakest projected -- clearly worst value;
    # "Baseline" (Salary 4000, ProjPts 8) sits in between.
    # DK's own Salary (from `sal`, below) is authoritative and overrides
    # TFFB's own Salary field wherever both exist -- so the distinct
    # salaries that matter here are set on `sal`, not on `rows`.
    rows = [
        {"Id": "1", "Name": "Baseline", "Position": "RB", "ProjPts": 8.0},
        {"Id": "2", "Name": "Overperformer", "Position": "RB", "ProjPts": 20.0},
        {"Id": "3", "Name": "Underperformer", "Position": "RB", "ProjPts": 11.0},
    ]
    sal = _salaries(
        [
            {"ID": "1", "Salary": 4000},
            {"ID": "2", "Salary": 5500},
            {"ID": "3", "Salary": 6000},
        ]
    )

    unpublished = build_edge_frame(_projections([{**r, "ProjOwn": 0} for r in rows]), sal).frame
    assert unpublished["Name"].tolist() == ["Overperformer", "Baseline", "Underperformer"]

    published = build_edge_frame(
        _projections([{**r, "ProjOwn": own} for r, own in zip(rows, [40.0, 1.0, 20.0], strict=True)]),
        sal,
    ).frame
    assert published["Name"].tolist() == ["Overperformer", "Baseline", "Underperformer"]


def test_valadj_still_differentiates_when_a_positions_salary_never_varies():
    # A position with every row at the same Salary has no slope to fit --
    # `_val_adj_residual_within_position` deliberately reads that as "no
    # signal" (residual 0 for both rows here), never a fabricated
    # regression. But A3's blend also folds in raw ProjPts' own
    # percentile, so ValAdj still differentiates B (the better raw
    # projection) from A even though their EdgePct ties at 75 (both
    # residuals are 0, so they share the average rank of a tie): B's
    # higher PtsPct (100 vs A's 50) pulls its ValAdj above A's --
    # 0.5*100+0.5*75=87.5 vs 0.5*50+0.5*75=62.5. Real case this matters
    # for: a thin position (DST, or a short slate) where DK happens to
    # price every rostered player identically -- ValAdj should still
    # reward the better projection, not go flat.
    proj = _projections(
        [
            {"Id": "1", "Name": "A", "Position": "TE", "ProjPts": 5.0},
            {"Id": "2", "Name": "B", "Position": "TE", "ProjPts": 9.0},
        ]
    )
    sal = _salaries([{"ID": "1", "Salary": 3000}, {"ID": "2", "Salary": 3000}])

    frame = build_edge_frame(proj, sal).frame
    a = frame[frame["Name"] == "A"].iloc[0]
    b = frame[frame["Name"] == "B"].iloc[0]
    assert a["ValAdj"] == 62.5
    assert b["ValAdj"] == 87.5


def test_rosterable_pool_mask_keeps_only_top_n_by_projpts_within_position():
    position = pd.Series(["RB", "RB", "RB", "RB", "WR"])
    proj_pts = pd.Series([25.0, 15.0, 10.0, 2.0, 8.0])

    original = derived.VAL_ADJ_ROSTERABLE_TOP_N
    try:
        derived.VAL_ADJ_ROSTERABLE_TOP_N = {**original, "RB": 3}
        mask = _rosterable_pool_mask(proj_pts, position)
    finally:
        derived.VAL_ADJ_ROSTERABLE_TOP_N = original

    # Top 3 RBs by ProjPts (25, 15, 10) are in the pool; the 2.0 backup
    # isn't. The lone WR is under its own (real, much larger) cap, so
    # it's in by default.
    assert mask.tolist() == [True, True, True, False, True]


def test_rosterable_pool_mask_includes_everyone_when_position_is_thinner_than_its_cap():
    # DST's real cap (32) comfortably exceeds a normal week's DST count
    # (Fix 2's spec case: 26 real DST on the 2026-09-23 slate) -- the
    # pool is simply everyone at that position.
    position = pd.Series(["DST", "DST", "DST"])
    proj_pts = pd.Series([9.0, 7.0, 5.0])
    mask = _rosterable_pool_mask(proj_pts, position)
    assert mask.tolist() == [True, True, True]


def test_percentile_against_pool_scores_non_pool_rows_without_blanks():
    # Fix 2: "score every player against the pool's distribution ...
    # players outside the pool still get a score, ranked against the
    # pool, so true backups sort naturally to the bottom -- no blanks."
    group = pd.Series(["RB"] * 5)
    values = pd.Series([25.0, 15.0, 10.0, 2.0, 0.5])
    pool_mask = pd.Series([True, True, True, False, False])

    pct = _percentile_against_pool(values, group, pool_mask)

    assert not pct.isna().any()
    # Pool members rank against each other exactly as a plain
    # percentile-of-3 would: 25 is the max (100), 10 is the min (100/3).
    assert pct.iloc[0] == 100.0
    assert round(pct.iloc[2], 1) == round(100 / 3, 1)
    # Non-pool rows (2.0, 0.5) are both below every pool member -- a
    # rank-based percentile can't distinguish two values that are both
    # below the whole reference set, so they tie at the same low score,
    # but it's a real low score, never a blank/0-by-fiat.
    assert pct.iloc[3] < pct.iloc[2]
    assert pct.iloc[4] == pct.iloc[3]
    assert pct.iloc[3] > 0


def test_valadj_no_longer_inflated_by_a_position_s_backup_pile():
    # Fix 2 regression, reproducing the exact failure mode Sam flagged:
    # a mid-tier player's PtsPct/EdgePct climbing as MORE near-zero
    # backups get added below him, even though nothing about his own
    # projection or price changed -- "the metric measures better than
    # the backup pile, not a good play." Monkeypatch a small RB cap (3)
    # so a handful of synthetic backups is enough to exercise it.
    original = derived.VAL_ADJ_ROSTERABLE_TOP_N
    try:
        derived.VAL_ADJ_ROSTERABLE_TOP_N = {**original, "RB": 3}

        few_backups = _projections(
            [
                {"Id": "1", "Name": "Star", "Position": "RB", "ProjPts": 25.0},
                {"Id": "2", "Name": "Mid", "Position": "RB", "ProjPts": 15.0},
                {"Id": "3", "Name": "Low", "Position": "RB", "ProjPts": 10.0},
                {"Id": "4", "Name": "Backup1", "Position": "RB", "ProjPts": 2.0},
            ]
        )
        sal_few = _salaries(
            [
                {"ID": "1", "Salary": 8000},
                {"ID": "2", "Salary": 6000},
                {"ID": "3", "Salary": 4000},
                {"ID": "4", "Salary": 3000},
            ]
        )

        many_backups = _projections(
            [
                {"Id": "1", "Name": "Star", "Position": "RB", "ProjPts": 25.0},
                {"Id": "2", "Name": "Mid", "Position": "RB", "ProjPts": 15.0},
                {"Id": "3", "Name": "Low", "Position": "RB", "ProjPts": 10.0},
                {"Id": "4", "Name": "Backup1", "Position": "RB", "ProjPts": 2.0},
                {"Id": "5", "Name": "Backup2", "Position": "RB", "ProjPts": 1.5},
                {"Id": "6", "Name": "Backup3", "Position": "RB", "ProjPts": 1.0},
                {"Id": "7", "Name": "Backup4", "Position": "RB", "ProjPts": 0.5},
            ]
        )
        sal_many = _salaries(
            [
                {"ID": "1", "Salary": 8000},
                {"ID": "2", "Salary": 6000},
                {"ID": "3", "Salary": 4000},
                {"ID": "4", "Salary": 3000},
                {"ID": "5", "Salary": 3000},
                {"ID": "6", "Salary": 3000},
                {"ID": "7", "Salary": 3000},
            ]
        )

        frame_few = build_edge_frame(few_backups, sal_few).frame
        frame_many = build_edge_frame(many_backups, sal_many).frame

        mid_few = frame_few[frame_few["Name"] == "Mid"].iloc[0]["ValAdj"]
        mid_many = frame_many[frame_many["Name"] == "Mid"].iloc[0]["ValAdj"]
        assert mid_few == mid_many
    finally:
        derived.VAL_ADJ_ROSTERABLE_TOP_N = original


def test_valadj_stays_blank_when_projpts_is_missing_even_in_a_degenerate_group():
    # Same degenerate (constant-Salary) group as above, but one row's
    # ProjPts is genuinely missing -- it must stay NaN (blank), never
    # coerced to the group's 0.0 fallback. "Blank is not zero" applies to
    # ValAdj same as everywhere else in this codebase.
    proj = _projections(
        [
            {"Id": "1", "Name": "Has points", "Position": "TE", "ProjPts": 5.0},
            {"Id": "2", "Name": "No points", "Position": "TE", "ProjPts": float("nan")},
        ]
    )
    sal = _salaries([{"ID": "1", "Salary": 3000}, {"ID": "2", "Salary": 3000}])

    frame = build_edge_frame(proj, sal).frame
    has_points = frame[frame["Name"] == "Has points"].iloc[0]
    no_points = frame[frame["Name"] == "No points"].iloc[0]
    # The lone valid row is the only usable value in its position group for
    # both PtsPct and EdgePct, so each percentile-ranks to 100 -- ValAdj
    # 100.0, not the old raw-residual 0.0.
    assert has_points["ValAdj"] == 100.0
    assert pd.isna(no_points["ValAdj"])


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
    assert pd.isna(row["OppPosRank"])


def test_aggpts_equals_projpts_when_no_external_source_available():
    proj = _projections([{"Id": "1", "Name": "P", "Team": "DET", "ProjPts": 15.0}])
    sal = _salaries([{"ID": "1"}])

    row = build_edge_frame(proj, sal).frame.iloc[0]
    assert row["AggPts"] == 15.0


def test_aggpts_is_equal_weight_mean_of_every_available_source():
    proj = _projections([{"Id": "1", "Name": "Player One", "Team": "DET", "ProjPts": 12.0}])
    sal = _salaries([{"ID": "1"}])
    sleeper = pd.DataFrame([{"Name": "Player One", "Team": "DET", "Position": "RB", "DkPts": 15.0}])
    fantasypros = pd.DataFrame([{"Name": "Player One", "Team": "DET", "Position": "RB", "DkPts": 18.0}])

    row = build_edge_frame(proj, sal, sleeper=sleeper, fantasypros=fantasypros).frame.iloc[0]
    assert row["AggPts"] == 15.0  # mean(12, 15, 18)


def test_aggpts_averages_over_whatever_is_available_when_one_source_is_missing():
    proj = _projections([{"Id": "1", "Name": "Player One", "Team": "DET", "ProjPts": 10.0}])
    sal = _salaries([{"ID": "1"}])
    sleeper = pd.DataFrame([{"Name": "Player One", "Team": "DET", "Position": "RB", "DkPts": 20.0}])

    row = build_edge_frame(proj, sal, sleeper=sleeper).frame.iloc[0]
    assert row["AggPts"] == 15.0  # mean(10, 20), fantasypros not synced


def test_aggpts_ignores_a_source_with_no_real_projection_for_this_player():
    proj = _projections([{"Id": "1", "Name": "Player One", "Team": "DET", "ProjPts": 10.0}])
    sal = _salaries([{"ID": "1"}])
    # Sleeper matches the player but has no real projection this week --
    # a NaN DkPts, same shape sleeper_projections.py's own fix produces.
    sleeper = pd.DataFrame([{"Name": "Player One", "Team": "DET", "Position": "RB", "DkPts": float("nan")}])

    row = build_edge_frame(proj, sal, sleeper=sleeper).frame.iloc[0]
    assert row["AggPts"] == 10.0  # falls back to ProjPts alone


def test_aggpts_unmatched_source_row_does_not_affect_other_players():
    proj = _projections(
        [
            {"Id": "1", "Name": "Matched Guy", "Team": "DET", "ProjPts": 10.0},
            {"Id": "2", "Name": "No Sleeper Data", "Team": "DET", "ProjPts": 20.0},
        ]
    )
    sal = _salaries([{"ID": "1"}, {"ID": "2"}])
    sleeper = pd.DataFrame([{"Name": "Matched Guy", "Team": "DET", "Position": "RB", "DkPts": 14.0}])

    frame = build_edge_frame(proj, sal, sleeper=sleeper).frame
    assert frame.set_index("Name").loc["Matched Guy", "AggPts"] == 12.0  # mean(10, 14)
    assert frame.set_index("Name").loc["No Sleeper Data", "AggPts"] == 20.0  # ProjPts alone


def test_aggpts_join_results_exposed_on_edge_build_result_for_match_rate_reporting():
    proj = _projections([{"Id": "1", "Name": "Matched Guy", "Team": "DET", "ProjPts": 10.0}])
    sal = _salaries([{"ID": "1"}])
    sleeper = pd.DataFrame([{"Name": "Matched Guy", "Team": "DET", "Position": "RB", "DkPts": 14.0}])

    result = build_edge_frame(proj, sal, sleeper=sleeper)
    assert set(result.source_joins) == {"sleeper"}
    assert result.source_joins["sleeper"].pool_matched == 1


def _uniform_gap_backups(
    position: str, n: int, gap: float, start_proj: float = 2.0
) -> tuple[list, list, list]:
    """`n` pool players at `position`, evenly spaced `ProjPts`, each with
    the SAME `gap` (mean(Sleeper, FantasyPros) - ProjPts) -- an OLS fit of
    gap~ProjPts over a population like this has ~zero slope and intercept
    ~= gap, so every one of these backups gets a residual near zero (the
    "uniform backup gap alone never fires" case). Returns (proj_rows,
    sleeper_rows, fantasypros_rows)."""
    proj_rows, sleeper_rows, fantasypros_rows = [], [], []
    for i in range(n):
        proj_pts = start_proj + i * (18.0 / max(n - 1, 1))
        name = f"{position} Backup {i}"
        proj_rows.append(
            {
                "Id": f"{position}-bk-{i}",
                "Name": name,
                "Position": position,
                "Team": "DET",
                "ProjPts": proj_pts,
            }
        )
        other = proj_pts + gap
        sleeper_rows.append({"Name": name, "Team": "DET", "Position": position, "DkPts": other})
        fantasypros_rows.append({"Name": name, "Team": "DET", "Position": position, "DkPts": other})
    return proj_rows, sleeper_rows, fantasypros_rows


def _split_pool(position: str, n_backups: int, backup_gap: float, extra_rows: list[dict] | None = None):
    """Assembles a full `build_edge_frame` input for SPLIT tests: `n_backups`
    uniform-gap players at `position` (see `_uniform_gap_backups`) plus any
    `extra_rows` (each a dict with `proj`/`sleeper`/`fantasypros` sub-dicts
    for one additional player), with `VAL_ADJ_ROSTERABLE_TOP_N[position]`
    set to include everyone passed in as a pool member."""
    proj_rows, sleeper_rows, fantasypros_rows = _uniform_gap_backups(position, n_backups, backup_gap)
    for extra in extra_rows or []:
        proj_rows.append(extra["proj"])
        if "sleeper" in extra:
            sleeper_rows.append(extra["sleeper"])
        if "fantasypros" in extra:
            fantasypros_rows.append(extra["fantasypros"])
    proj = _projections(proj_rows)
    sal = _salaries([{"ID": r["Id"]} for r in proj_rows])
    sleeper = pd.DataFrame(sleeper_rows)
    fantasypros = pd.DataFrame(fantasypros_rows)
    return proj, sal, sleeper, fantasypros, len(proj_rows)


def test_split_flag_uniform_backup_gap_does_not_fire_alone():
    # Every backup carries the SAME -3 gap -- a real, stable per-position
    # difference (exactly what C2 found for real RB/WR), not a reason to
    # flag any one of them individually.
    proj, sal, sleeper, fantasypros, n = _split_pool("RB", 20, backup_gap=-3.0)
    original = derived.VAL_ADJ_ROSTERABLE_TOP_N
    try:
        derived.VAL_ADJ_ROSTERABLE_TOP_N = {**original, "RB": n}
        frame = build_edge_frame(proj, sal, sleeper=sleeper, fantasypros=fantasypros).frame
    finally:
        derived.VAL_ADJ_ROSTERABLE_TOP_N = original

    assert not frame["Flags"].str.contains("TFFB").any()


def test_split_flag_fires_on_a_starter_off_the_positions_own_trend():
    # 20 backups all at gap -3; one "starter" at a similar ProjPts level
    # to several backups but with a real gap of +3 -- six points off what
    # this position's own trend line predicts for him. He should be
    # flagged TFFB↓ (others higher than usual relative to TFFB); nobody
    # else should be.
    starter = {
        "proj": {"Id": "starter", "Name": "Starter", "Position": "RB", "Team": "DET", "ProjPts": 15.0},
        "sleeper": {"Name": "Starter", "Team": "DET", "Position": "RB", "DkPts": 18.0},
        "fantasypros": {"Name": "Starter", "Team": "DET", "Position": "RB", "DkPts": 18.0},
    }
    proj, sal, sleeper, fantasypros, n = _split_pool("RB", 20, backup_gap=-3.0, extra_rows=[starter])
    original = derived.VAL_ADJ_ROSTERABLE_TOP_N
    try:
        derived.VAL_ADJ_ROSTERABLE_TOP_N = {**original, "RB": n}
        frame = build_edge_frame(proj, sal, sleeper=sleeper, fantasypros=fantasypros).frame
    finally:
        derived.VAL_ADJ_ROSTERABLE_TOP_N = original

    flagged = frame[frame["Flags"].str.contains("TFFB")]
    assert set(flagged["Name"]) == {"Starter"}
    assert flagged.iloc[0]["Flags"] == "TFFB↓"


def test_split_flag_fires_down_in_the_opposite_direction():
    starter = {
        "proj": {"Id": "starter", "Name": "Starter", "Position": "RB", "Team": "DET", "ProjPts": 15.0},
        "sleeper": {"Name": "Starter", "Team": "DET", "Position": "RB", "DkPts": 3.0},
        "fantasypros": {"Name": "Starter", "Team": "DET", "Position": "RB", "DkPts": 3.0},
    }
    proj, sal, sleeper, fantasypros, n = _split_pool("RB", 20, backup_gap=3.0, extra_rows=[starter])
    original = derived.VAL_ADJ_ROSTERABLE_TOP_N
    try:
        derived.VAL_ADJ_ROSTERABLE_TOP_N = {**original, "RB": n}
        frame = build_edge_frame(proj, sal, sleeper=sleeper, fantasypros=fantasypros).frame
    finally:
        derived.VAL_ADJ_ROSTERABLE_TOP_N = original

    flagged = frame[frame["Flags"].str.contains("TFFB")]
    assert set(flagged["Name"]) == {"Starter"}
    assert flagged.iloc[0]["Flags"] == "TFFB↑"


def test_split_flag_never_fires_with_no_other_source_data():
    proj = _projections([{"Id": "1", "Name": "Player One", "Team": "DET", "ProjPts": 10.0}])
    sal = _salaries([{"ID": "1"}])

    row = build_edge_frame(proj, sal).frame.iloc[0]
    assert "TFFB" not in row["Flags"]


def test_split_flag_not_eligible_outside_the_rosterable_pool():
    # A huge residual on a player the pool mask excludes must never fire,
    # no matter how large the disagreement.
    outsider = {
        "proj": {"Id": "outsider", "Name": "Outsider", "Position": "RB", "Team": "DET", "ProjPts": 0.5},
        "sleeper": {"Name": "Outsider", "Team": "DET", "Position": "RB", "DkPts": 15.5},
        "fantasypros": {"Name": "Outsider", "Team": "DET", "Position": "RB", "DkPts": 15.5},
    }
    proj, sal, sleeper, fantasypros, n = _split_pool("RB", 20, backup_gap=-3.0, extra_rows=[outsider])
    original = derived.VAL_ADJ_ROSTERABLE_TOP_N
    try:
        # The pool mask ranks by ProjPts, top N -- "Outsider"'s ProjPts
        # (0.5) is the lowest of anyone here, so a pool sized to the 20
        # backups alone (all >= 2.0) excludes him regardless of his own
        # huge disagreement.
        derived.VAL_ADJ_ROSTERABLE_TOP_N = {**original, "RB": n - 1}
        frame = build_edge_frame(proj, sal, sleeper=sleeper, fantasypros=fantasypros).frame
    finally:
        derived.VAL_ADJ_ROSTERABLE_TOP_N = original

    outsider_row = frame[frame["Name"] == "Outsider"].iloc[0]
    assert "TFFB" not in outsider_row["Flags"]


def test_split_flag_skips_a_position_with_too_few_fit_players_and_warns():
    # Only 3 pool RBs with a source -- below SPLIT_MIN_FIT_PLAYERS (8) --
    # even though one of them disagrees by a lot. No fit, no flag, and the
    # position is reported back so the caller can warn.
    starter = {
        "proj": {"Id": "starter", "Name": "Starter", "Position": "RB", "Team": "DET", "ProjPts": 15.0},
        "sleeper": {"Name": "Starter", "Team": "DET", "Position": "RB", "DkPts": 30.0},
        "fantasypros": {"Name": "Starter", "Team": "DET", "Position": "RB", "DkPts": 30.0},
    }
    proj, sal, sleeper, fantasypros, n = _split_pool("RB", 2, backup_gap=-3.0, extra_rows=[starter])
    original = derived.VAL_ADJ_ROSTERABLE_TOP_N
    try:
        derived.VAL_ADJ_ROSTERABLE_TOP_N = {**original, "RB": n}
        result = build_edge_frame(proj, sal, sleeper=sleeper, fantasypros=fantasypros)
    finally:
        derived.VAL_ADJ_ROSTERABLE_TOP_N = original

    assert not result.frame["Flags"].str.contains("TFFB").any()
    assert "RB" in result.split_skipped_positions


def test_split_flag_residual_under_the_floor_does_not_fire():
    # A tight, clean fit (tiny noise, well under SPLIT_MIN_RESIDUAL) --
    # even the single largest |residual| in the pool must not fire just to
    # fill the top-share quota.
    proj_rows, sleeper_rows, fantasypros_rows = _uniform_gap_backups("RB", 20, gap=-3.0)
    # Nudge one backup's gap by +1 point -- real disagreement, but nowhere
    # near the 2.0-point floor.
    fantasypros_rows[5]["DkPts"] += 1.0
    proj = _projections(proj_rows)
    sal = _salaries([{"ID": r["Id"]} for r in proj_rows])
    sleeper = pd.DataFrame(sleeper_rows)
    fantasypros = pd.DataFrame(fantasypros_rows)

    original = derived.VAL_ADJ_ROSTERABLE_TOP_N
    try:
        derived.VAL_ADJ_ROSTERABLE_TOP_N = {**original, "RB": len(proj_rows)}
        frame = build_edge_frame(proj, sal, sleeper=sleeper, fantasypros=fantasypros).frame
    finally:
        derived.VAL_ADJ_ROSTERABLE_TOP_N = original

    assert not frame["Flags"].str.contains("TFFB").any()


def test_split_flag_compares_against_other_sources_mean_not_aggpts():
    # AggPts blends TFFB in too, which would understate the real gap by a
    # third -- SPLIT's own `gap` must come from mean(Sleeper, FantasyPros)
    # alone. Backups agree with TFFB exactly (gap 0); the starter's other-
    # sources-only gap is +11, which must survive into a real flag.
    starter = {
        "proj": {"Id": "starter", "Name": "Starter", "Position": "RB", "Team": "DET", "ProjPts": 15.0},
        "sleeper": {"Name": "Starter", "Team": "DET", "Position": "RB", "DkPts": 26.0},
        "fantasypros": {"Name": "Starter", "Team": "DET", "Position": "RB", "DkPts": 26.0},
    }
    proj, sal, sleeper, fantasypros, n = _split_pool("RB", 20, backup_gap=0.0, extra_rows=[starter])
    original = derived.VAL_ADJ_ROSTERABLE_TOP_N
    try:
        derived.VAL_ADJ_ROSTERABLE_TOP_N = {**original, "RB": n}
        frame = build_edge_frame(proj, sal, sleeper=sleeper, fantasypros=fantasypros).frame
    finally:
        derived.VAL_ADJ_ROSTERABLE_TOP_N = original

    starter_row = frame[frame["Name"] == "Starter"].iloc[0]
    assert starter_row["Flags"] == "TFFB↓"


def test_split_flag_never_masks_a_higher_priority_flag_in_the_singular_flag_column():
    starter = {
        "proj": {
            "Id": "starter",
            "Name": "Windy Split Guy",
            "Position": "RB",
            "Team": "DET",
            "ProjPts": 15.0,
        },
        "sleeper": {"Name": "Windy Split Guy", "Team": "DET", "Position": "RB", "DkPts": 3.0},
        "fantasypros": {"Name": "Windy Split Guy", "Team": "DET", "Position": "RB", "DkPts": 3.0},
    }
    proj, sal, sleeper, fantasypros, n = _split_pool("RB", 20, backup_gap=3.0, extra_rows=[starter])
    games = _games([{"GameId": "g1", "Away": "DET", "Home": "NO"}])
    weather = pd.DataFrame([{"GameId": "g1", "Wind": 25.0}])

    original = derived.VAL_ADJ_ROSTERABLE_TOP_N
    try:
        derived.VAL_ADJ_ROSTERABLE_TOP_N = {**original, "RB": n}
        frame = build_edge_frame(
            proj, sal, games=games, weather=weather, sleeper=sleeper, fantasypros=fantasypros
        ).frame
    finally:
        derived.VAL_ADJ_ROSTERABLE_TOP_N = original

    row = frame[frame["Name"] == "Windy Split Guy"].iloc[0]
    assert row["Flags"] == "WIND TFFB↑"
    assert row["Flag"] == "WIND"


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
        # Part 7.4: GameID used to be computed as an internal join key
        # (GameId) then dropped before this -- now kept, renamed to match
        # its own header text.
        assert row["GameID"] == "g1"


def test_gameid_blank_when_games_not_synced():
    proj = _projections([{"Id": "1", "Name": "P", "Team": "DET"}])
    sal = _salaries([{"ID": "1"}])

    row = build_edge_frame(proj, sal).frame.iloc[0]
    assert pd.isna(row["GameID"])


def test_tmrank_is_salary_rank_within_team_and_position():
    proj = _projections(
        [
            {"Id": "1", "Name": "WR1 candidate", "Position": "WR", "Team": "DET"},
            {"Id": "2", "Name": "WR2 candidate", "Position": "WR", "Team": "DET"},
            # Different team, same position -- must not affect DET's own ranks.
            {"Id": "3", "Name": "Other team WR", "Position": "WR", "Team": "NO"},
            # Different position, same team -- must not affect WR ranks.
            {"Id": "4", "Name": "Team's RB1", "Position": "RB", "Team": "DET"},
        ]
    )
    sal = _salaries(
        [
            {"ID": "1", "Salary": 8000},
            {"ID": "2", "Salary": 5000},
            {"ID": "3", "Salary": 9000},
            {"ID": "4", "Salary": 7000},
        ]
    )

    frame = build_edge_frame(proj, sal).frame
    by_name = frame.set_index("Name")
    assert by_name.loc["WR1 candidate", "TmRank"] == 1
    assert by_name.loc["WR2 candidate", "TmRank"] == 2
    assert by_name.loc["Other team WR", "TmRank"] == 1  # own team's WR1, despite higher salary
    assert by_name.loc["Team's RB1", "TmRank"] == 1  # own position group, unaffected by DET's WRs


def test_tmrank_breaks_a_salary_tie_by_name_not_row_order():
    # Two players at the identical salary and team/position must still get
    # a deterministic (not arbitrary/row-order-dependent) 1/2 split --
    # broken by Name ascending.
    proj = _projections(
        [
            {"Id": "1", "Name": "Zeb Backup", "Position": "QB", "Team": "DET"},
            {"Id": "2", "Name": "Andy Backup", "Position": "QB", "Team": "DET"},
        ]
    )
    sal = _salaries([{"ID": "1", "Salary": 4000}, {"ID": "2", "Salary": 4000}])

    frame = build_edge_frame(proj, sal).frame
    by_name = frame.set_index("Name")
    assert by_name.loc["Andy Backup", "TmRank"] == 1
    assert by_name.loc["Zeb Backup", "TmRank"] == 2


def test_wind_joined_from_weather_by_game_and_flagged_over_threshold():
    proj = _projections(
        [{"Id": "1", "Name": "P", "Team": "DET", "Position": "RB", "Ceiling": 1.0, "ProjOwn": 0}]
    )
    sal = _salaries([{"ID": "1"}])
    games = _games([{"GameId": "g1", "Away": "DET", "Home": "NO"}])
    weather = pd.DataFrame([{"GameId": "g1", "Wind": 25.0}])

    row = build_edge_frame(proj, sal, games=games, weather=weather).frame.iloc[0]
    assert row["Wind"] == 25.0
    assert row["Flags"] == "WIND"


def test_the_wind_flag_fires_at_fifteen_mph_not_twenty():
    def flags(wind):
        proj = _projections(
            [{"Id": "1", "Name": "P", "Team": "DET", "Position": "RB", "Ceiling": 1.0, "ProjOwn": 0}]
        )
        games = _games([{"GameId": "g1", "Away": "DET", "Home": "NO"}])
        weather = pd.DataFrame([{"GameId": "g1", "Wind": wind}])
        return build_edge_frame(proj, _salaries([{"ID": "1"}]), games=games, weather=weather).frame.iloc[0][
            "Flags"
        ]

    assert flags(15.0) == "WIND" and flags(17.0) == "WIND"  # the old 20 mph line would have missed these
    assert flags(14.9) == ""


def test_out_and_wind_flags_both_shown_out_first():
    proj = _projections(
        [{"Id": "1", "Name": "P", "Team": "DET", "Position": "RB", "Ceiling": 1.0, "ProjOwn": 0}]
    )
    sal = _salaries([{"ID": "1", "Status": "OUT"}])
    games = _games([{"GameId": "g1", "Away": "DET", "Home": "NO"}])
    weather = pd.DataFrame([{"GameId": "g1", "Wind": 25.0}])

    row = build_edge_frame(proj, sal, games=games, weather=weather).frame.iloc[0]
    assert row["Flags"] == "OUT WIND"
    # Part 7.9: "Flag" (singular, hidden) is just the first/highest-priority
    # token -- "Flags" (visible) is every matching condition.
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
    assert pd.isna(row["ImpliedMove"])
    assert pd.isna(row["TotMove"])
    assert pd.isna(row["SpdMove"])


def test_line_move_joined_by_team_and_flagged():
    # Fix 2.2: all three of diff_odds()'s deltas are surfaced now, not
    # just the team-implied-points one (ImpliedMove, was "LineMove").
    proj = _projections(
        [{"Id": "1", "Name": "P", "Team": "DET", "Position": "RB", "Ceiling": 1.0, "ProjOwn": 0}]
    )
    sal = _salaries([{"ID": "1"}])
    delta = LINE_MOVE_FLAG_THRESHOLD
    line_movement = pd.DataFrame(
        [{"Abbr": "DET", "TeamPointsDelta": delta, "TotalDelta": 1.0, "SpreadDelta": -0.5}]
    )

    row = build_edge_frame(proj, sal, line_movement=line_movement).frame.iloc[0]
    assert row["ImpliedMove"] == delta
    assert row["TotMove"] == 1.0
    assert row["SpdMove"] == -0.5
    assert row["Flags"] == "IMPL↑"


def test_line_move_down_flag():
    proj = _projections(
        [{"Id": "1", "Name": "P", "Team": "DET", "Position": "RB", "Ceiling": 1.0, "ProjOwn": 0}]
    )
    sal = _salaries([{"ID": "1"}])
    line_movement = pd.DataFrame(
        [{"Abbr": "DET", "TeamPointsDelta": -LINE_MOVE_FLAG_THRESHOLD, "TotalDelta": 0.0, "SpreadDelta": 0.0}]
    )

    row = build_edge_frame(proj, sal, line_movement=line_movement).frame.iloc[0]
    assert row["Flags"] == "IMPL↓"


def test_line_move_flag_keys_off_impmove_not_totmove_or_spdmove():
    # A big TotMove/SpdMove with a flat ImpliedMove must NOT trigger IMPL↑/↓ --
    # only ImpliedMove (team implied points) drives that flag (Fix 2.2).
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
    assert row["Flags"] == ""


def test_out_and_line_move_flags_both_shown_out_first():
    proj = _projections(
        [{"Id": "1", "Name": "P", "Team": "DET", "Position": "RB", "Ceiling": 1.0, "ProjOwn": 0}]
    )
    sal = _salaries([{"ID": "1", "Status": "OUT"}])
    line_movement = pd.DataFrame(
        [{"Abbr": "DET", "TeamPointsDelta": LINE_MOVE_FLAG_THRESHOLD, "TotalDelta": 0.0, "SpreadDelta": 0.0}]
    )

    row = build_edge_frame(proj, sal, line_movement=line_movement).frame.iloc[0]
    assert row["Flags"] == "OUT IMPL↑"


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
    assert row["Flags"] == "WIND LEVERAGE"
    # Part 7.9: "Flag" (singular, hidden) is just the first/highest-priority
    # token of the same list -- "Flags" (visible) is everything.
    assert row["Flag"] == "WIND"


def test_flag_is_hidden_single_top_priority_token_flags_is_everything():
    # Part 7.9: verified live that "Flag" already held every matching
    # condition space-separated, not the single first-match value 7.9's
    # own spec assumed (Fix 2.1, an earlier session) -- so this split had
    # to be built for real rather than just renamed. No conditions at all
    # -> both columns blank, not "" vs NaN inconsistency.
    proj = _projections([{"Id": "1", "Name": "P", "Position": "RB", "Ceiling": 1.0, "ProjOwn": 0}])
    sal = _salaries([{"ID": "1"}])

    row = build_edge_frame(proj, sal).frame.iloc[0]
    assert row["Flag"] == ""
    assert row["Flags"] == ""


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


def _sos(rows: list[dict]) -> pd.DataFrame:
    # sources/tffb_sos.py's own output shape: Team is the full name (not
    # used for this join), Team.1 is the DK-compatible abbreviation this
    # join keys on, Rank is the schedule rank this join actually reads.
    base = {"Team": "", "Team.1": "", "Rank": 1, "FPA": 0.0, "Opp": ""}
    return pd.DataFrame([{**base, **r} for r in rows])


def test_opp_pos_rank_blank_when_no_sos_data_synced_at_all():
    proj = _projections([{"Id": "1", "Name": "P", "Position": "RB", "Opp": "NO"}])
    sal = _salaries([{"ID": "1"}])

    row = build_edge_frame(proj, sal).frame.iloc[0]
    assert pd.isna(row["OppPosRank"])


def test_opp_pos_rank_reads_the_opponents_rank_not_the_players_own_team():
    # The exact bug PlayerPoolRaw's own hand-typed formula had (see
    # sheet_pool_raw_sos.py): this must key on Opp, never Team.
    proj = _projections([{"Id": "1", "Name": "P", "Position": "RB", "Team": "DET", "Opp": "NO"}])
    sal = _salaries([{"ID": "1"}])
    sos_rb = _sos([{"Team.1": "DET", "Rank": 30}, {"Team.1": "NO", "Rank": 4}])

    row = build_edge_frame(proj, sal, sos_by_position={"RB": sos_rb}).frame.iloc[0]
    assert row["OppPosRank"] == 4  # NO's rank (the opponent), not DET's (30, the player's own team)


def test_opp_pos_rank_uses_the_row_own_position_sos_frame():
    proj = _projections(
        [
            {"Id": "1", "Name": "RB player", "Position": "RB", "Opp": "NO"},
            {"Id": "2", "Name": "WR player", "Position": "WR", "Opp": "NO"},
        ]
    )
    sal = _salaries([{"ID": "1"}, {"ID": "2"}])
    sos_rb = _sos([{"Team.1": "NO", "Rank": 4}])
    sos_wr = _sos([{"Team.1": "NO", "Rank": 17}])

    frame = build_edge_frame(proj, sal, sos_by_position={"RB": sos_rb, "WR": sos_wr}).frame
    assert frame.loc[frame["Name"] == "RB player", "OppPosRank"].iloc[0] == 4
    assert frame.loc[frame["Name"] == "WR player", "OppPosRank"].iloc[0] == 17


def test_opp_pos_rank_blank_for_just_the_positions_missing_their_own_sos_sync():
    # One position's TFFB sync failing shouldn't hide every other
    # position's real data -- same graceful-degradation contract as
    # games/weather.
    proj = _projections(
        [
            {"Id": "1", "Name": "RB player", "Position": "RB", "Opp": "NO"},
            {"Id": "2", "Name": "WR player", "Position": "WR", "Opp": "NO"},
        ]
    )
    sal = _salaries([{"ID": "1"}, {"ID": "2"}])
    sos_rb = _sos([{"Team.1": "NO", "Rank": 4}])

    frame = build_edge_frame(proj, sal, sos_by_position={"RB": sos_rb}).frame
    assert frame.loc[frame["Name"] == "RB player", "OppPosRank"].iloc[0] == 4
    assert pd.isna(frame.loc[frame["Name"] == "WR player", "OppPosRank"].iloc[0])


def test_opp_pos_rank_blank_when_opponent_not_found_in_its_sos_frame():
    proj = _projections([{"Id": "1", "Name": "P", "Position": "RB", "Opp": "ZZ"}])
    sal = _salaries([{"ID": "1"}])
    sos_rb = _sos([{"Team.1": "NO", "Rank": 4}])

    row = build_edge_frame(proj, sal, sos_by_position={"RB": sos_rb}).frame.iloc[0]
    assert pd.isna(row["OppPosRank"])


def test_snap_pct_blank_when_not_synced():
    proj = _projections([{"Id": "1", "Name": "Player One", "Team": "DET"}])
    sal = _salaries([{"ID": "1"}])

    row = build_edge_frame(proj, sal).frame.iloc[0]
    assert pd.isna(row["Snap%"])


def test_snap_pct_joined_by_name_team_position():
    proj = _projections([{"Id": "1", "Name": "Player One", "Team": "DET", "Position": "RB"}])
    sal = _salaries([{"ID": "1"}])
    snaps = pd.DataFrame([{"Name": "Player One", "Team": "DET", "Position": "RB", "Snap%": 0.65}])

    row = build_edge_frame(proj, sal, snaps=snaps).frame.iloc[0]
    assert row["Snap%"] == 0.65


def test_snap_pct_keeps_a_real_recorded_zero_not_blank():
    proj = _projections([{"Id": "1", "Name": "Player One", "Team": "DET", "Position": "RB"}])
    sal = _salaries([{"ID": "1"}])
    snaps = pd.DataFrame([{"Name": "Player One", "Team": "DET", "Position": "RB", "Snap%": 0.0}])

    row = build_edge_frame(proj, sal, snaps=snaps).frame.iloc[0]
    assert row["Snap%"] == 0.0


def test_snap_pct_blank_for_a_player_absent_from_the_snaps_source():
    proj = _projections(
        [
            {"Id": "1", "Name": "Has Snaps", "Team": "DET", "Position": "RB"},
            {"Id": "2", "Name": "No Snaps Data", "Team": "DET", "Position": "RB"},
        ]
    )
    sal = _salaries([{"ID": "1"}, {"ID": "2"}])
    snaps = pd.DataFrame([{"Name": "Has Snaps", "Team": "DET", "Position": "RB", "Snap%": 0.5}])

    frame = build_edge_frame(proj, sal, snaps=snaps).frame
    assert frame.set_index("Name").loc["Has Snaps", "Snap%"] == 0.5
    assert pd.isna(frame.set_index("Name").loc["No Snaps Data", "Snap%"])


def test_snap_pct_join_result_exposed_on_edge_build_result():
    proj = _projections([{"Id": "1", "Name": "Player One", "Team": "DET", "Position": "RB"}])
    sal = _salaries([{"ID": "1"}])
    snaps = pd.DataFrame([{"Name": "Player One", "Team": "DET", "Position": "RB", "Snap%": 0.5}])

    result = build_edge_frame(proj, sal, snaps=snaps)
    assert "snaps" in result.source_joins
    assert result.source_joins["snaps"].pool_matched == 1


def _usage(rows):
    base = {
        "GsisId": "00-0000001",
        "Name": "Player One",
        "Team": "DET",
        "Position": "WR",
        "Tgt%": 0.25,
        "WOPR": 0.6,
        "Rush%": float("nan"),
        "RZ/G": 1.33,
        "HVT/G": float("nan"),
        "Games": 3,
        "ThroughWeek": 3,
    }
    return pd.DataFrame([{**base, **r} for r in rows])


def test_usage_columns_blank_when_not_synced_or_empty():
    from dfs.usage_metrics import USAGE_METRIC_COLUMNS, empty_usage_frame

    proj = _projections([{"Id": "1", "Name": "Player One", "Team": "DET", "Position": "WR"}])
    sal = _salaries([{"ID": "1"}])
    for usage in (None, empty_usage_frame()):
        result = build_edge_frame(proj, sal, usage=usage)
        row = result.frame.iloc[0]
        assert row[USAGE_METRIC_COLUMNS].isna().all()  # blank, never 0
        assert "usage" not in result.source_joins


def test_usage_joined_by_name_team_position_and_blank_where_a_metric_does_not_apply():
    proj = _projections([{"Id": "1", "Name": "Player One", "Team": "DET", "Position": "WR"}])
    sal = _salaries([{"ID": "1"}])

    result = build_edge_frame(proj, sal, usage=_usage([{}]))
    row = result.frame.iloc[0]
    assert (row["Tgt%"], row["WOPR"], row["RZ/G"]) == (0.25, 0.6, 1.33)
    assert pd.isna(row["Rush%"]) and pd.isna(row["HVT/G"])  # RB/QB-only: blank for a WR
    assert result.source_joins["usage"].pool_matched == 1


def test_usage_join_keeps_the_gsis_id_for_the_crosswalk():
    proj = _projections([{"Id": "1", "Name": "Player One", "Team": "DET", "Position": "WR"}])
    sal = _salaries([{"ID": "1"}])

    matched = build_edge_frame(proj, sal, usage=_usage([{}])).source_joins["usage"].matched
    assert matched.loc[0, "Id"] == "1"
    assert matched.loc[0, "GsisId"] == "00-0000001"


def test_usage_blank_for_a_player_nflverse_has_no_row_for():
    proj = _projections(
        [
            {"Id": "1", "Name": "Player One", "Team": "DET", "Position": "WR"},
            {"Id": "2", "Name": "Rookie Debut", "Team": "DET", "Position": "WR"},
        ]
    )
    sal = _salaries([{"ID": "1"}, {"ID": "2"}])

    frame = build_edge_frame(proj, sal, usage=_usage([{}])).frame.set_index("Name")
    assert frame.loc["Player One", "Tgt%"] == 0.25
    assert pd.isna(frame.loc["Rookie Debut", "Tgt%"])


def test_usage_percentile_helpers_exist_and_are_within_position():
    from dfs.derived import PLAYER_METRIC_PCT_COLUMNS

    assert PLAYER_METRIC_PCT_COLUMNS["Tgt%"] == "Tgt%ile"
    rows = [
        {"Id": str(i), "Name": f"WR {i}", "Team": "DET", "Position": "WR", "ProjPts": 10 + i}
        for i in range(1, 6)
    ]
    proj = _projections(rows)
    sal = _salaries([{"ID": str(i)} for i in range(1, 6)])
    usage = _usage([{"GsisId": f"g{i}", "Name": f"WR {i}", "Tgt%": 0.05 * i} for i in range(1, 6)])

    frame = build_edge_frame(proj, sal, usage=usage).frame.set_index("Name")
    ranks = frame["Tgt%ile"]
    assert ranks["WR 5"] > ranks["WR 3"] > ranks["WR 1"]  # a higher share is a higher percentile


def test_zone_labels_present_and_blank_for_every_row():
    # Zone labels (GAME/CEIL/MOVE/WX) carry no per-row data -- the text
    # lives in the header only (`sheet_style._apply_zone_label_style`
    # writes it there); every real player row must read blank, not a
    # repeated copy of the label word.
    proj = _projections(
        [
            {"Id": "1", "Name": "A", "Position": "RB"},
            {"Id": "2", "Name": "B", "Position": "WR"},
        ]
    )
    sal = _salaries([{"ID": "1"}, {"ID": "2"}])

    frame = build_edge_frame(proj, sal).frame
    assert list(frame.columns) == EDGE_COLUMNS
    for label in ZONE_LABELS:
        assert (frame[label] == "").all()


# --- Round 5 item 3: within-position percentile helpers ---------------------------------------------


def _pct_frame():
    proj = _projections(
        [
            {"Id": "1", "Name": "RB1", "Position": "RB", "ProjPts": 10.0},
            {"Id": "2", "Name": "RB2", "Position": "RB", "ProjPts": 20.0},
            {"Id": "3", "Name": "RB3", "Position": "RB", "ProjPts": 30.0},
            {"Id": "4", "Name": "RB0", "Position": "RB", "ProjPts": 0.0},
            {"Id": "5", "Name": "QB1", "Position": "QB", "ProjPts": 18.0},
            {"Id": "6", "Name": "QB2", "Position": "QB", "ProjPts": 24.0},
        ]
    )
    sal = _salaries(
        [{"ID": str(i), "Position": p} for i, p in enumerate(["RB", "RB", "RB", "RB", "QB", "QB"], 1)]
    )
    return build_edge_frame(proj, sal).frame.set_index("Name")


def test_percentile_helpers_exist_on_the_edge_frame_for_every_player_metric():
    frame = _pct_frame()
    for pct_column in PLAYER_METRIC_PCT_COLUMNS.values():
        assert pct_column in frame.columns and pct_column in EDGE_COLUMNS


def test_percentile_is_within_position_not_across_positions():
    frame = _pct_frame()
    # RB3 (30) tops the RBs; QB2 (24) tops the QBs even though 24 < 30 -- each position on its own.
    assert frame.loc["RB3", "ProjPts%ile"] > frame.loc["RB2", "ProjPts%ile"] > frame.loc["RB1", "ProjPts%ile"]
    assert frame.loc["QB2", "ProjPts%ile"] > frame.loc["QB1", "ProjPts%ile"]
    assert frame.loc["QB2", "ProjPts%ile"] == frame.loc["RB3", "ProjPts%ile"]  # both the best in their group


def test_a_zero_or_blank_metric_gets_no_percentile_and_does_not_skew_the_others():
    frame = _pct_frame()
    assert pd.isna(frame.loc["RB0", "ProjPts%ile"])  # zero -> blank -> never coloured
    # the same RB percentiles with and without the zero row present
    without = _pct_frame().drop(index="RB0")
    assert frame.loc["RB1", "ProjPts%ile"] == without.loc["RB1", "ProjPts%ile"]
