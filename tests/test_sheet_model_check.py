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
        "PROJECTION RACE",
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
    first_percent_row = int(re.search(r"\d+", layout.percent_cells[0]).group())
    assert layout.rows[first_percent_row - 2][2] == "Beat Ceiling"


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
