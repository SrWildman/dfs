"""Model Check tab: pure layout, then the writer against a recording fake."""

import re

import pandas as pd

from dfs.sheet_model_check import COLUMN_COUNT, LAST_COLUMN, MODEL_CHECK_TAB, build_layout, write_model_check


def _scored(n=40):
    rows = []
    for i in range(n):
        rows.append(
            {
                "week": 3,
                "Position": "WR" if i % 2 else "RB",
                "ProjPts": 8.0 + i % 9,
                "DkActual": 6.0 + i % 11,
                "Ceiling": 18.0 + i % 5,
                "Salary": 4000 + 100 * i,
                "Val": 2.0 + (i % 12) / 10,
                "ValAdj": float(i),
                "AggPts": 8.0 + i % 9,
                "SleeperPts": 9.0 + i % 7,
                "FantasyProsPts": 9.5 + i % 6,
                "Flags": "CHALK" if i % 10 == 0 else "",
                "RosterablePool": True,
                "Status": "scored",
            }
        )
    return pd.DataFrame(rows)


def _text(layout):
    return [str(cell) for row in layout.rows for cell in row if cell != ""]


def test_every_row_is_padded_to_the_tab_width():
    layout = build_layout(_scored(), weeks=[3])
    assert all(len(row) == COLUMN_COUNT for row in layout.rows)
    assert LAST_COLUMN == chr(ord("A") + COLUMN_COUNT - 1)


def test_sections_appear_in_the_decided_order():
    layout = build_layout(_scored(), weeks=[3])
    titles = [layout.rows[r - 1][0].split("  ")[0] for r in layout.section_rows]
    assert titles == [
        "WHAT THE MODEL LEARNED THIS WEEK",
        "PROJECTION RACE",
        "SHADOW SOURCES",
        "RELIABILITY",
        "CEILING",
        "PROJECTION ACCURACY",
        "VALADJ",
        "SOURCES COMPARED",
        "SALARY MULTIPLE",
        "FLAGS",
        "SIGNALS",
    ]
    assert layout.rows[0][0].startswith("MODEL CHECK") and "Weeks scored: 3" in layout.rows[1][0]


def test_no_text_cell_can_be_parsed_by_sheets_as_a_date():
    # "5-10" or "10-15" typed into Sheets becomes a date serial; band labels must use an en dash.
    date_like = re.compile(r"^\d{1,2}-\d{1,2}$")
    assert not [t for t in _text(build_layout(_scored(), weeks=[3])) if date_like.match(t)]


def test_thin_rows_are_marked_and_a_full_sample_is_not():
    big = build_layout(_scored(80), weeks=[3])
    small = build_layout(_scored(12), weeks=[3])
    assert len(small.thin_rows) > len(big.thin_rows)
    assert all(row in range(1, len(small.rows) + 1) for row in small.thin_rows)


def test_counts_are_whole_numbers_percentages_are_percent_and_signed_columns_get_a_scale():
    layout = build_layout(_scored(), weeks=[3])
    assert layout.count_cells and layout.percent_cells and layout.signed_ranges
    # The Ceiling hit-rate columns are percentages, never plain numbers.
    first_rows = [int(re.search(r"\d+", rng).group()) for rng in layout.percent_cells]
    assert any(layout.rows[row - 2][2] == "Beat Ceiling" for row in first_rows)


def test_the_status_line_reports_the_rosterable_pool_not_every_draftkings_row():
    scored = _scored(20)
    extra = scored.iloc[:5].assign(RosterablePool=False, Status="unmatched")
    status = build_layout(pd.concat([scored, extra]), weeks=[3]).rows[1][0]
    assert "20 scored" in status and "0 not found" in status  # the 5 non-pool unmatched rows are not counted


def test_with_nothing_scored_the_tab_says_not_enough_data_yet():
    layout = build_layout(pd.DataFrame(columns=["Status"]), weeks=[])
    assert any("Not enough data yet" in t for t in _text(layout))
    assert not layout.section_rows


class _Recorder:
    def __init__(self):
        self.calls = []

    def __getattr__(self, name):
        def record(*args, **kwargs):
            self.calls.append((name, args, kwargs))

        return record


def test_writer_resets_the_whole_tab_format_before_styling_and_mutes_thin_rows_last():
    layout = build_layout(_scored(12), weeks=[3])
    client = _Recorder()
    summary = write_model_check(client, layout)
    names = [c[0] for c in client.calls]
    assert names[0] == "write_tab" and client.calls[0][1][0] == MODEL_CHECK_TAB
    reset = next(c for c in client.calls if c[0] == "format_range")
    assert (
        reset[1][2]["numberFormat"] is None and reset[1][2]["horizontalAlignment"] is None
    )  # stale formats cleared
    last_formats = [c for c in client.calls if c[0] == "format_range"][-len(layout.thin_rows) :]
    assert all(c[1][2]["textFormat"]["italic"] for c in last_formats)  # thin rows are applied last
    assert "built" in summary and f"{len(layout.thin_rows)} thin" in summary


def test_no_cell_can_be_read_by_sheets_as_a_formula_even_when_a_note_wraps_onto_an_equals_sign():
    # A long note that happens to wrap so a line begins with "= 0.85 best" must not become `#ERROR!`.
    from dfs.sheet_model_check import _Builder

    b = _Builder()
    b.note("x" * 140 + " fits tau = 0.85 best, with more text after it so the line wraps there")
    cells = [c for row in b.layout.rows for c in row if c != ""]
    assert not [c for c in cells if isinstance(c, str) and c.startswith(("=", "+", "@"))]
    assert any(c.startswith("'=") for c in cells)  # neutralised with an (undisplayed) apostrophe


def test_notes_are_wrapped_short_enough_to_fit_across_the_tab():
    from dfs.sheet_model_check import NOTE_WRAP_CHARS

    layout = build_layout(_scored(), weeks=[3])
    assert all(len(layout.rows[r - 1][0]) <= NOTE_WRAP_CHARS + 1 for r in layout.note_rows)


# --- Week 6, slice 6: "What the model learned this week" ---------------------------------------------------


def test_the_learned_block_opens_the_tab_and_says_what_refits_and_what_is_fixed():
    layout = build_layout(_scored(80), weeks=[3])
    titles = [layout.rows[r - 1][0] for r in layout.section_rows]
    assert titles[0].startswith("WHAT THE MODEL LEARNED THIS WEEK")
    text = " ".join(str(c) for r in layout.rows for c in r)
    assert "CalPts refits every week" in text and "FIXED" in text
    assert "Week 8: recalibrate Hit3x%/Boom%/Bust% on 2026 results" in text and "No action now." in text


def test_with_one_scored_week_the_block_says_it_is_the_first_fit_and_has_nothing_to_compare():
    layout = build_layout(_scored(80), weeks=[3])
    text = " ".join(str(c) for r in layout.rows for c in r)
    assert "its first fit: nothing to compare" in text


def _two_weeks():
    frame = _scored(120)
    other = frame.copy()
    other["week"] = 4
    other["DkActual"] = other["DkActual"] + 3.0  # week 4: everyone beat their projection by more
    return pd.concat([frame, other], ignore_index=True)


def test_the_weights_and_biggest_corrections_sit_beside_last_weeks():
    from dfs import calibration
    from dfs import sheet_model_check as smc

    scored = _two_weeks()
    now = calibration.fit(scored, before_week=5)
    was = calibration.fit(scored, before_week=4)
    weights = smc.weight_table(now, was)
    assert {"Position", "TFFB", "Sleeper", "FantasyPros", "TFFB (last week)"} <= set(weights.columns)
    row = weights.iloc[0]
    assert abs(row["TFFB"] + row["Sleeper"] + row["FantasyPros"] - 1.0) < 1e-9
    assert (
        abs(row["TFFB (last week)"] + row["Sleeper (last week)"] + row["FantasyPros (last week)"] - 1.0)
        < 1e-9
    )
    changes = smc.bias_changes(now, was)
    assert 0 < len(changes) <= smc.LEARNED_BIAS_ROWS
    assert list(changes["Bias now"].abs()) == sorted(changes["Bias now"].abs(), reverse=True)
    assert not changes["Bias last week"].isna().any()
    first_fit = smc.bias_changes(now, calibration.fit(scored, before_week=3 - 1))
    assert first_fit["Bias last week"].isna().all()  # nothing before week 3: no "was"


def test_a_shadow_source_shows_in_the_weights_table_only_once_it_has_joined_the_blend():
    from dfs import calibration
    from dfs import sheet_model_check as smc

    scored = pd.concat([_two_weeks().assign(week=w) for w in (1, 2, 3)], ignore_index=True)
    scored["PropPts"] = scored["DkActual"] + 1.0
    in_shadow = calibration.fit(scored.assign(PropPts=float("nan")), before_week=4)
    assert smc.shown_sources(in_shadow, in_shadow) == smc._SOURCE_ORDER
    now = calibration.fit(scored, before_week=4)
    was = calibration.fit(scored, before_week=3)
    assert "PropPts" in now.sources and "PropPts" not in was.sources
    assert smc.shown_sources(now, was) == (*smc._SOURCE_ORDER, "PropPts")
    row = smc.weight_table(now, was).iloc[0]
    assert abs(row["TFFB"] + row["Sleeper"] + row["FantasyPros"] + row["Props"] - 1.0) < 1e-9
    assert pd.isna(row["Props (last week)"]) or row["Props (last week)"] == 0


def test_weeks_read_as_a_range_or_a_list():
    from dfs.sheet_model_check import weeks_text

    assert weeks_text((1, 2, 3, 4)) == "1–4" and weeks_text((1, 3)) == "1, 3" and weeks_text(()) == "none"
    assert weeks_text((2,)) == "2"


def test_race_leaders_name_the_lowest_mae_per_position_and_mark_early_ones():
    from dfs.sheet_model_check import race_leaders

    race = pd.DataFrame(
        [
            {"Position": "RB", "Source": "CalPts", "n": 80, "MAE": 4.6},
            {"Position": "RB", "Source": "TFFB", "n": 80, "MAE": 5.1},
            {"Position": "QB", "Source": "TFFB", "n": 20, "MAE": 5.2},
            {"Position": "QB", "Source": "CalPts", "n": 20, "MAE": 5.9},
        ]
    )
    assert race_leaders(race) == "Lowest MAE by position: QB TFFB (early), RB CalPts."
    assert race_leaders(race.iloc[0:0]) == ""


def test_signal_progress_lists_only_signals_that_fired_with_their_unflagged_baseline():
    from dfs.sheet_model_check import signal_progress

    table = pd.DataFrame(
        [
            {"Signal": "USAGE↑", "n": 40, "VsCal": 1.2, "HitCal": 0.6, "Thin": False},
            {"Signal": "FADE↓", "n": 0, "VsCal": None, "HitCal": None, "Thin": True},
            {"Signal": "Unflagged RB", "n": 300, "VsCal": 0.1, "HitCal": None, "Thin": False},
        ]
    )
    got = signal_progress(table)
    assert list(got["Signal"]) == ["USAGE↑"] and got.iloc[0]["Unflagged avg"] == 0.1
    assert signal_progress(None).empty


# --- shadow sources (ESPN, props) ------------------------------------------------------------------------


def test_the_shadow_block_always_says_where_each_source_stands_and_marks_a_rated_source_as_shadow():
    from dfs import sheet_model_check as smc

    plain = smc.build_layout(_two_weeks().assign(UmPts=float("nan")), weeks=[3, 4])
    text = " ".join(str(c) for r in plain.rows for c in r)
    assert (
        "SHADOW SOURCES" in text
        and "ESPN 0 of 3 scored weeks" in text
        and "Props 0 of 3 scored weeks" in text
    )
    assert "No shadow source has rated a scored player yet" in text

    scored = _two_weeks().assign(UmPts=float("nan"))
    scored["EspnPts"] = scored["ProjPts"] + 1.0
    layout = smc.build_layout(scored, weeks=[3, 4])
    text = " ".join(str(c) for r in layout.rows for c in r)
    assert "ESPN (shadow)" in text and "Players rated by" in text
    assert "ESPN 2 of 3 scored weeks" in text  # it rated 20+ players in each of weeks 3 and 4
    assert "Props (shadow)" not in text  # no props rated anyone: no rows, only its status


def test_a_shadow_source_never_changes_calpts_while_it_is_in_shadow():
    from dfs import calibration

    scored = _two_weeks()
    without = calibration.calpts_for_week(scored, 4, season=None, sources=calibration.CAL_SOURCES)[0]
    with_shadow = scored.assign(EspnPts=scored["DkActual"], PropPts=scored["DkActual"] + 5.0)  # absurdly good
    after = calibration.calpts_for_week(with_shadow, 4, season=None, sources=calibration.CAL_SOURCES)[0]
    pd.testing.assert_series_equal(without["CalPts"], after["CalPts"])


def _ownership():
    return pd.DataFrame(
        [
            ["gpp", "TFFB Own%", 2, 0.034, -0.055],
            ["gpp", "Ours", 2, 0.034, 0.02],
            ["cash", "Double-up curve", 2, 0.061, -0.01],
        ],
        columns=["Format", "Model", "Weeks", "MAE", "Chalk bias"],
    )


def _gaps():
    return pd.DataFrame(
        [[6, 120, -0.2, 0.1, 0.3], ["All", 480, 0.52, 0.8, -0.4]],
        columns=["Week", "n", "Gap share", "Top fifth", "Bottom fifth"],
    )


def test_the_ownership_shadow_sections_render_when_given_their_tables():
    layout = build_layout(_scored(), weeks=[3], ownership=_ownership(), ownership_gaps=_gaps())
    titles = [layout.rows[r - 1][0].split("  ")[0] for r in layout.section_rows]
    assert titles.index("OWNERSHIP") == titles.index("SHADOW SOURCES") + 1
    assert titles[titles.index("OWNERSHIP") + 1] == "WHEN WE DISAGREE"
    text = _text(layout)
    assert "TFFB Own%" in text and "Chalk bias" in text and "Gap share" in text


def test_the_ownership_shadow_sections_are_left_out_without_their_tables():
    layout = build_layout(_scored(), weeks=[3])
    titles = [layout.rows[r - 1][0].split("  ")[0] for r in layout.section_rows]
    assert "OWNERSHIP" not in titles and "WHEN WE DISAGREE" not in titles
    assert all(len(row) == COLUMN_COUNT for row in layout.rows)


def test_empty_ownership_tables_say_so_instead_of_rendering_a_blank_table():
    layout = build_layout(_scored(), weeks=[3], ownership=pd.DataFrame(), ownership_gaps=pd.DataFrame())
    assert sum("Not enough data yet" in t for t in _text(layout)) >= 2
