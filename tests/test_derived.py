import pandas as pd

from dfs import derived
from dfs.derived import (
    CHALK_OWNERSHIP_THRESHOLD,
    EDGE_COLUMNS,
    LEVERAGE_FLAG_THRESHOLD,
    LINE_MOVE_FLAG_THRESHOLD,
    OWN_STATUS_REAL,
    OWN_STATUS_UNPUBLISHED,
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
    assert top["Leverage"] >= LEVERAGE_FLAG_THRESHOLD
    assert top["Flags"] == "LEVERAGE"


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
    assert chalky["Leverage"] < LEVERAGE_FLAG_THRESHOLD
    assert chalky["Flags"] == "CHALK"


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
    assert row["Flags"] == "LINE↑"


def test_line_move_down_flag():
    proj = _projections(
        [{"Id": "1", "Name": "P", "Team": "DET", "Position": "RB", "Ceiling": 1.0, "ProjOwn": 0}]
    )
    sal = _salaries([{"ID": "1"}])
    line_movement = pd.DataFrame(
        [{"Abbr": "DET", "TeamPointsDelta": -LINE_MOVE_FLAG_THRESHOLD, "TotalDelta": 0.0, "SpreadDelta": 0.0}]
    )

    row = build_edge_frame(proj, sal, line_movement=line_movement).frame.iloc[0]
    assert row["Flags"] == "LINE↓"


def test_line_move_flag_keys_off_impmove_not_totmove_or_spdmove():
    # A big TotMove/SpdMove with a flat ImpliedMove must NOT trigger LINE↑/↓ --
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
    assert row["Flags"] == "OUT LINE↑"


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
