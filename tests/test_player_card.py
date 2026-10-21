"""The Player card (`player_card.py`): the spec, formulas, last-five-games log, raw rows and text."""

import pandas as pd
import pytest

from dfs import player_card as pc
from dfs.derived import EDGE_SHEET_ORDER, edge_sheet_letter
from dfs.usage_metrics import red_zone_counts
from dfs.weekly_reset import LINEUPS_NAME_BLOCKS

EXPOSURE_HEADER = ["Name", "Pos", "Salary", "# Lineups", "Exposure", "Target", "vs Target"]


def _layout(exposure=True, name=""):
    return pc.build_layout(
        edge_tab="EdgeRaw",
        lineups_tab="Lineups",
        exposure_tab="Exposure",
        exposure=pc.exposure_columns(EXPOSURE_HEADER) if exposure else None,
        lineup_count_cell="H1",
        name=name,
    )


def _all_fields():
    stripes = [pc.HEADER_STRIPE, pc.OWNERSHIP_STRIPE, *pc.PROJECTION_STRIPES, *pc.MATCHUP_STRIPES]
    return [f for stripe in stripes for f in stripe]


# ---- the spec -------------------------------------------------------------------------------


def test_every_edge_field_is_an_edgeraw_column_and_every_raw_field_a_raw_column():
    for spec in _all_fields():
        if spec.source == pc.EDGE:
            assert spec.column in EDGE_SHEET_ORDER, spec
        elif spec.source == pc.RAW:
            assert spec.column in pc.RAW_COLUMNS, spec
    assert "Edge" in EDGE_SHEET_ORDER and "Flags" in EDGE_SHEET_ORDER


def test_edge_formulas_use_the_derived_column_letter_not_a_typed_one():
    for spec in _all_fields():
        if spec.source != pc.EDGE:
            continue
        letter = edge_sheet_letter(spec.column)
        assert f"EdgeRaw!${letter}$2:${letter}" in pc.formula(spec, "EdgeRaw")


def test_raw_positions_follow_the_column_lists():
    assert pc.raw_letter("NameKey") == "A"
    assert pc.raw_letter(pc.RAW_COLUMNS[-1]) == pc._letter(len(pc.RAW_COLUMNS) - 1)
    assert pc.rows_index("Key") == 1 and pc.rows_index("C") == 4
    spec = pc.Field("Sleeper", pc.RAW, "Sleeper", pc.PTS)
    letter = pc.raw_letter("Sleeper")
    assert f"{pc.RAW_TAB}!${letter}$2:${letter}" in pc.formula(spec, "EdgeRaw")


def test_implied_is_total_minus_the_teams_own_spread_over_two():
    player = pd.Series({"OverUnder": 53.5, "Spread": 3.0})
    assert pc.value(pc.IMPLIED, player, None) == pytest.approx(25.25)
    assert pc.value(pc.IMPLIED, pd.Series({"OverUnder": 53.5, "Spread": float("nan")}), None) is None
    formula = pc.formula(pc.IMPLIED, "EdgeRaw")
    assert "/2" in formula and edge_sheet_letter("OverUnder") in formula


def test_format_value_blanks_missing_and_formats_the_rest():
    assert pc.format_value(pc.PTS, float("nan")) == "-"
    assert pc.format_value(pc.PTS, None) == "-"
    assert pc.format_value(pc.PCT100, 61.8) == "61.8%"
    assert pc.format_value(pc.PCT1, 0.1234) == "12.3%"
    assert pc.format_value(pc.MONEY, 6000) == "$6,000"
    assert pc.format_value(pc.TEXT, "") == "-"


# ---- the layout ------------------------------------------------------------------------------


def test_helpers_sit_in_row_1_and_every_field_formula_reads_them():
    lay = _layout(name="Jordan Love")
    row1 = lay.rows[0]
    assert row1[1] == "Jordan Love"  # B1 keeps the typed name through a rebuild
    assert row1[7].startswith("=IFERROR(MATCH($B$1,EdgeRaw!$B$2:$B,0)")
    assert edge_sheet_letter("NameKey") in row1[8] and "H1" in row1[8]
    assert pc.ROW_CELL == "H1" and pc.KEY_CELL == "I1" and pc.NAME_CELL == "B1"
    assert pc.unprotected_ranges() == ["B1"]


def test_every_row_is_exactly_the_tab_width():
    assert {len(r) for r in _layout().rows} == {pc.COLUMN_COUNT}


def test_usage_block_has_five_game_rows_and_an_average_row():
    lay = _layout()
    header = lay.rows.index(pc.USAGE_HEADER + [""] * (pc.COLUMN_COUNT - len(pc.USAGE_HEADER)))
    games = lay.rows[header + 1 : header + 1 + pc.GAMES]
    assert all(r[0].startswith("=IFERROR(IF(INDEX(PlayerCardRaw!") for r in games)
    avg = lay.rows[header + 1 + pc.GAMES]
    assert avg[0] == "Average" and avg[2].startswith("=IFERROR(AVERAGE(C")
    assert avg[1] == ""  # the opponent column is text: no average


def test_lineups_block_counts_one_term_per_name_block():
    lay = _layout()
    in_lineups = pc.in_lineups_formula("Lineups")
    assert in_lineups.count("COUNTIF(") == len(LINEUPS_NAME_BLOCKS)
    which = pc.which_lineups_formula("Lineups")
    assert which.count("COUNTIF(") == len(LINEUPS_NAME_BLOCKS)
    assert '"L1"' in which and f'"L{len(LINEUPS_NAME_BLOCKS)}"' in which
    start, end = LINEUPS_NAME_BLOCKS[0]
    assert f"Lineups!$A${start}:$A${end}" in in_lineups
    last = next(r for r in lay.rows if r[0] == in_lineups)
    assert last[4].startswith("=IF(OR(C")  # vs Target = exposure - target


def test_exposure_columns_come_from_its_own_header_and_a_missing_one_gives_none():
    cols = pc.exposure_columns(EXPOSURE_HEADER)
    assert cols == {"Name": "A", "# Lineups": "D", "Exposure": "E", "Target": "F"}
    assert pc.exposure_columns(["Name", "Salary"]) is None
    shifted = pc.exposure_columns(["Name", "Pos", "Position", "Salary", "# Lineups", "Exposure", "Target"])
    assert shifted["Target"] == "G"  # an inserted column moves it; nothing is typed
    lay = _layout(exposure=False)
    assert any("(no Exposure tab)" in r for row in lay.rows for r in [row[2]])


def test_number_patterns_cover_every_value_cell():
    lay = _layout()
    assert all(pattern for _rng, pattern in lay.patterns)
    ranges = [rng for rng, _ in lay.patterns]
    assert len(ranges) == len(set(ranges))


# ---- the last five games ---------------------------------------------------------------------


def _stat(pid, name, pos, team, week, opp, targets=0, carries=0, rec_yd=0, team_air=0):
    return {
        "player_id": pid,
        "player_display_name": name,
        "position": pos,
        "season_type": "REG",
        "week": week,
        "team": team,
        "opponent_team": opp,
        "targets": targets,
        "carries": carries,
        "receptions": targets // 2,
        "receiving_yards": rec_yd,
        "receiving_air_yards": 0,
        "passing_air_yards": team_air,
        "passing_yards": 0,
        "fantasy_points_ppr": 0.0,
    }


def _stats_frame(weeks=(1, 2, 3, 4, 5, 6, 7)):
    rows = []
    for w in weeks:
        rows += [
            _stat("w1", "Wide One", "WR", "AAA", w, "BBB", targets=6, rec_yd=60),
            _stat("w2", "Wide Two", "WR", "AAA", w, "BBB", targets=4),
            _stat("q1", "Quarter Back", "QB", "AAA", w, "BBB", carries=2, team_air=100),
        ]
    return pd.DataFrame(rows)


def _pbp(plays):
    base = {
        "season_type": "REG",
        "play_type": "pass",
        "pass": 1,
        "rush": 0,
        "two_point_attempt": 0,
        "play_deleted": 0,
        "receiver_player_id": None,
        "rusher_player_id": None,
    }
    return pd.DataFrame([{**base, **p} for p in plays])


def test_goal_line_option_adds_a_column_and_the_default_frame_is_unchanged():
    pbp = _pbp(
        [
            {"week": 1, "yardline_100": 4, "receiver_player_id": "w1"},
            {"week": 1, "yardline_100": 5, "receiver_player_id": "w1"},
            {"week": 1, "yardline_100": 6, "receiver_player_id": "w1"},
        ]
    )
    assert list(red_zone_counts(pbp).columns) == ["GsisId", "week", "rz", "hvt"]
    counts = red_zone_counts(pbp, goal_line=5).set_index("GsisId")
    assert (counts.loc["w1", "rz"], counts.loc["w1", "gl"]) == (3, 2)  # the 5 is inclusive


def test_usage_log_keeps_the_latest_five_games_played_most_recent_first():
    log = pc.usage_log(_stats_frame(), None, None)
    mine = log[log["Name"] == "Wide One"].sort_values("Back")
    assert list(mine["Back"]) == [1, 2, 3, 4, 5]
    assert list(mine["Wk"]) == [7, 6, 5, 4, 3]
    assert (mine["Tgt"] == 6).all() and mine["Tgt%"].iloc[0] == pytest.approx(0.6)  # 6 of the team's 10
    assert mine["Opp"].iloc[0] == "BBB"
    assert mine["RZ"].isna().all() and mine["Snap%"].isna().all()  # no pbp / snaps: blank, not 0


def test_a_missed_week_is_skipped_never_a_zero():
    stats = _stats_frame()
    stats = stats[~((stats["player_id"] == "w1") & (stats["week"] == 5))]
    log = pc.usage_log(stats, None, None)
    weeks = list(log[log["Name"] == "Wide One"].sort_values("Back")["Wk"])
    assert weeks == [7, 6, 4, 3, 2]


def test_a_quarterback_has_no_target_share_but_keeps_his_carries():
    log = pc.usage_log(_stats_frame(), None, None)
    qb = log[log["Name"] == "Quarter Back"]
    assert qb["Tgt"].isna().all() and qb["Tgt%"].isna().all()
    assert (qb["Car"] == 2).all()


def test_red_zone_inside_five_and_snaps_are_joined_per_game():
    pbp = _pbp(
        [
            {"week": 7, "yardline_100": 3, "receiver_player_id": "w1"},
            {"week": 7, "yardline_100": 15, "receiver_player_id": "w1"},
            {"week": 6, "yardline_100": 40, "receiver_player_id": "w1"},
        ]
    )
    snaps = pd.DataFrame(
        {
            "player": ["Wide One", "Wide One"],
            "week": [7, 6],
            "offense_pct": [0.91, 0.88],
        }
    )
    log = pc.usage_log(_stats_frame(), pbp, pc.snap_log_frame(snaps))
    mine = log[log["Name"] == "Wide One"].set_index("Wk")
    assert (mine.loc[7, "RZ"], mine.loc[7, "In5"]) == (2, 1)
    assert (mine.loc[6, "RZ"], mine.loc[6, "In5"]) == (0, 0)  # played, no looks: a real 0
    assert mine.loc[7, "Snap%"] == pytest.approx(0.91)
    assert pd.isna(mine.loc[5, "Snap%"])


def test_usage_log_of_nothing_is_an_empty_frame_with_the_columns():
    log = pc.usage_log(pd.DataFrame(), None, None)
    assert log.empty and list(log.columns) == pc.LOG_COLUMNS


# ---- the raw rows ----------------------------------------------------------------------------


def _edge():
    return pd.DataFrame(
        {
            "Name": ["Wide One", "Quarter Back"],
            "Position": ["WR", "QB"],
            "Team": ["AAA", "AAA"],
            "Opp": ["BBB", "BBB"],
            "GameStart": ["2026-10-25T16:25:00Z", "2026-10-25T16:25:00Z"],
            "Salary": [7000, 6000],
            "ProjPts": [15.0, 20.0],
            "CalPts": [14.0, 19.0],
            "OverUnder": [48.0, 48.0],
            "Spread": [-3.0, -3.0],
            "xFP/G": [18.0, 21.0],
            "Edge": ["USAGE↑ Proj ▼", ""],
            "Flags": ["", "TFFB↑"],
            "PropPts": [float("nan"), float("nan")],
        }
    )


def test_raw_frame_has_one_row_per_player_with_the_projection_sources_and_games():
    edge = _edge()
    sleeper = pd.DataFrame({"Name": ["Wide One"], "Position": ["WR"], "DkPts": [16.2]})
    log = pc.usage_log(_stats_frame(), None, None)
    raw = pc.raw_frame(edge, sleeper=sleeper, fantasypros=None, log=log)
    assert list(raw.columns) == pc.RAW_COLUMNS and len(raw) == 2
    one = raw[raw["NameKey"] == "wide one"].iloc[0]
    assert one["Sleeper"] == pytest.approx(16.2) and pd.isna(one["FantasyPros"])
    assert one["Kickoff"].startswith("Sun") and "ET" in one["Kickoff"]
    assert one["Wk1"] == 7 and one["Wk5"] == 3 and one["Tgt1"] == 6


def test_a_player_with_no_games_has_blank_game_cells():
    raw = pc.raw_frame(_edge(), log=pd.DataFrame(columns=pc.LOG_COLUMNS))
    assert raw[[c for c in pc.RAW_COLUMNS if c.endswith("1") and c != "NameKey"]].isna().all().all()


# ---- props and signals -----------------------------------------------------------------------


def test_props_rows_order_the_markets_and_a_td_market_has_no_line():
    consensus = pd.DataFrame(
        {
            "name_key": ["wide one"] * 3,
            "stat": ["td", "rec_yd", "rec"],
            "line": [float("nan"), 64.5, 5.5],
            "p": [0.41, 0.52, 0.55],
            "n_books": [5, 6, 6],
        }
    )
    rows = pc.props_rows(consensus)
    assert [r["A"] for r in rows] == ["Receptions", "Receiving yards", "Anytime TD"]
    assert [r["Key"] for r in rows] == [f"wide one|prop|{n}" for n in (1, 2, 3)]
    assert rows[2]["B"] is None and rows[1]["B"] == 64.5


def _table():
    return pd.DataFrame(
        {
            "Signal": ["USAGE↑", "Proj ▼ (any R6 signal)", "FADE↓"],
            "n": [62, 322, 0],
            "VsProj": [0.0, -1.37, float("nan")],
            "VsCal": [0.5, -0.41, float("nan")],
            "HitProj": [0.435, 0.668, float("nan")],
            "HitCal": [0.419, 0.621, float("nan")],
            "Thin": [False, False, True],
        }
    )


def test_signal_tokens_map_to_their_model_check_row():
    assert pc.signal_group("USAGE↑") == "USAGE↑"
    assert pc.signal_group("INJ+") == "INJ+ confirmed"
    assert pc.signal_group("Proj ▼?").startswith("Proj ▼")
    assert pc.signal_group("Props ▲ +4.2").startswith("Props ▲")
    assert pc.signal_group("MOVE↑") is None


def test_record_line_quotes_model_check_and_says_when_there_is_none():
    line = pc.record_line("USAGE↑", _table())
    assert line == "n=62, +0.5 vs CalPts, +0.0 vs TFFB, 42% on the signal's side"
    assert "−1.4 vs TFFB" in pc.record_line("Proj ▼ (any R6 signal)", _table())
    assert pc.record_line("FADE↓", _table()) == "has not fired in a scored game yet"
    assert pc.record_line("USAGE↓", _table()) == "no record yet"
    assert pc.record_line("USAGE↑", None) == "no record yet"
    assert pc.record_line(None, _table()) == "no Model Check record for this signal"
    thin = _table().assign(Thin=True)
    assert pc.record_line("USAGE↑", thin).endswith("thin sample")


def test_signal_rows_follow_each_players_edge_tokens_with_detail_and_record():
    rows = pc.signal_rows(_edge(), _table())
    assert [r["Key"] for r in rows] == ["wide one|sig|1", "wide one|sig|2"]
    first = rows[0]
    assert first["A"] == "USAGE↑" and "18.0" in first["B"] and "15.0" in first["B"]
    assert first["C"].startswith("n=62")
    assert "too high" in rows[1]["B"]


def test_a_players_signals_are_capped():
    edge = _edge()
    edge.loc[0, "Edge"] = "USAGE↑ USAGE↓ FADE↓ INJ+ Proj ▼"
    assert len(pc.signal_rows(edge, _table())) == pc.MAX_SIGNALS


# ---- the text ---------------------------------------------------------------------------------


def _frames():
    edge = _edge()
    log = pc.usage_log(_stats_frame(), None, None)
    raw = pc.raw_frame(edge, log=log)
    rows = pc.rows_frame(
        [], pc.signal_rows(edge, _table()), props_note="No props.", signals_note="As of today."
    )
    return edge, raw, rows


def test_render_text_shows_every_block_from_the_same_spec_as_the_sheet():
    edge, raw, rows = _frames()
    text = pc.render_text("wide one", edge, raw, rows)
    for heading in ("PROJECTIONS", "OWNERSHIP", "USAGE, LAST 5 GAMES", "MATCHUP AND GAME", "PROPS"):
        assert heading in text
    assert "SIGNALS AND FLAGS" in text and "IN YOUR LINEUPS" in text
    for spec in _all_fields():
        assert spec.label in text
    assert "Wk 7" in text and "Implied (team)" in text and "25.5" in text  # (48 + 3) / 2
    assert "USAGE↑" in text and "record: n=62" in text


def test_render_text_for_an_unknown_name_says_so():
    edge, raw, rows = _frames()
    assert "not on this week's EdgeRaw" in pc.render_text("Nobody", edge, raw, rows)
