import pytest

from dfs.week import (
    extract_results_value_columns,
    parse_sheet_id_from_url,
    parse_week_from_title,
    resolve_week_title,
    rewrite_sheet_id,
)


def test_parse_week_from_title():
    assert parse_week_from_title("Week 3") == 3
    assert parse_week_from_title("Week 12") == 12


def test_parse_week_from_title_strips_whitespace():
    assert parse_week_from_title("  Week 3  ") == 3


def test_parse_week_from_title_rejects_non_matching_title():
    # The template's own title -- nobody should be scoping a bankroll
    # close against it, and this must not silently fall back to
    # current_week() (that fallback is the Fix 1 bug).
    with pytest.raises(ValueError, match="does not match"):
        parse_week_from_title("Template")


def test_parse_week_from_title_rejects_empty_string():
    with pytest.raises(ValueError, match="does not match"):
        parse_week_from_title("")


# Week 3 follow-ups, Item 1 (2026-09-23): resolve_week_title's own tests.


def test_resolve_week_title_sets_the_title_when_missing():
    # The common case: a fresh "Copy of Template" (or anything else that
    # doesn't parse as "Week <n>") gets titled freely, no conflict.
    resolution = resolve_week_title("Copy of Template", 4)
    assert resolution.target_title == "Week 4"
    assert resolution.needs_rename is True


def test_resolve_week_title_no_rename_needed_when_already_correct():
    resolution = resolve_week_title("Week 4", 4)
    assert resolution.target_title == "Week 4"
    assert resolution.needs_rename is False


def test_resolve_week_title_raises_on_a_real_mismatch_rather_than_overwriting():
    # The sheet is ALREADY titled "Week <n>" for a DIFFERENT n -- this
    # must stop and ask, not silently overwrite a title that might have
    # been deliberate.
    with pytest.raises(ValueError, match='already titled "Week 3"'):
        resolve_week_title("Week 3", 4)


def test_resolve_week_title_honours_an_explicit_week_override():
    # --week 4 passed by the caller (simulated here by just passing 4
    # directly, since the CLI's own --week-vs-current_week() choice
    # happens before this function is ever called) resolves the same way
    # regardless of what "today" would have derived.
    resolution = resolve_week_title("Copy of Template", 4)
    assert resolution.target_title == "Week 4"


def test_parse_sheet_id_from_full_url():
    url = "https://docs.google.com/spreadsheets/d/1AbC-23_xyZ/edit#gid=0"
    assert parse_sheet_id_from_url(url) == "1AbC-23_xyZ"


def test_parse_sheet_id_from_url_without_trailing_fragment():
    url = "https://docs.google.com/spreadsheets/d/1AbC-23_xyZ"
    assert parse_sheet_id_from_url(url) == "1AbC-23_xyZ"


def test_parse_sheet_id_accepts_bare_id():
    assert parse_sheet_id_from_url("1AbC-23_xyZ") == "1AbC-23_xyZ"


def test_parse_sheet_id_rejects_garbage():
    with pytest.raises(ValueError, match="Could not find a sheet ID"):
        parse_sheet_id_from_url("not a url or an id")


def test_parse_sheet_id_rejects_empty_string():
    with pytest.raises(ValueError):
        parse_sheet_id_from_url("")


def test_rewrite_sheet_id_replaces_value_and_inserts_previous():
    config_text = '[google_sheets]\nsheet_id = "old-id"\ncredentials_file = "credentials.json"\n'
    updated = rewrite_sheet_id(config_text, new_sheet_id="new-id", previous_sheet_id="old-id")

    assert 'sheet_id = "new-id"' in updated
    assert 'previous_sheet_id = "old-id"' in updated
    assert 'credentials_file = "credentials.json"' in updated


def test_rewrite_sheet_id_updates_existing_previous_sheet_id_line():
    config_text = (
        "[google_sheets]\n"
        'sheet_id = "week2-id"\n'
        'previous_sheet_id = "week1-id"\n'
        'credentials_file = "credentials.json"\n'
    )
    updated = rewrite_sheet_id(config_text, new_sheet_id="week3-id", previous_sheet_id="week2-id")

    assert 'sheet_id = "week3-id"' in updated
    assert 'previous_sheet_id = "week2-id"' in updated
    assert 'previous_sheet_id = "week1-id"' not in updated
    # only one previous_sheet_id line, not a second one appended
    assert updated.count("previous_sheet_id") == 1


def test_rewrite_sheet_id_preserves_comments_and_unrelated_lines():
    config_text = (
        "# a helpful comment\n"
        "[google_sheets]\n"
        "# the sheet id\n"
        'sheet_id = "old-id"\n'
        "\n"
        "[nfl_odds]\n"
        "# default_week = 1\n"
    )
    updated = rewrite_sheet_id(config_text, new_sheet_id="new-id", previous_sheet_id="old-id")

    assert "# a helpful comment" in updated
    assert "# the sheet id" in updated
    assert "[nfl_odds]" in updated
    assert "# default_week = 1" in updated


def test_rewrite_sheet_id_raises_when_no_sheet_id_line_present():
    with pytest.raises(ValueError, match="no `sheet_id"):
        rewrite_sheet_id('[google_sheets]\ncredentials_file = "x"\n', new_sheet_id="x", previous_sheet_id="y")


def test_extract_results_value_columns_splits_out_formula_columns():
    # A=Week, B=Cash Pts, C=Cash Line, D=Cash Results (formula), E=H2H
    # Entered, F=H2H Win, G=H2H % (formula), H=Red, I=Blue, J=Black.
    rows = [
        ["1", "124.92", "115.38", "TRUE", "10", "7", "0.7", "139.72", "98.6", "78.72"],
        ["2", "138.8", "136.24", "TRUE", "10", "6", "0.6", "102.26", "153.02", "127.64"],
    ]
    result = extract_results_value_columns(rows)

    assert result["A"] == [["1"], ["2"]]
    assert result["B:C"] == [["124.92", "115.38"], ["138.8", "136.24"]]
    assert result["E:F"] == [["10", "7"], ["10", "6"]]
    assert result["H:J"] == [["139.72", "98.6", "78.72"], ["102.26", "153.02", "127.64"]]
    # D and G (the formula columns) never appear in any group.
    all_values = [v for group in result.values() for row in group for v in row]
    assert "TRUE" not in all_values
    assert "0.7" not in all_values


def test_extract_results_value_columns_handles_short_rows():
    # A blank week's row can come back shorter than J if trailing cells are
    # empty -- must not raise an IndexError.
    rows = [["6"]]
    result = extract_results_value_columns(rows)

    assert result["A"] == [["6"]]
    assert result["B:C"] == [["", ""]]
    assert result["E:F"] == [["", ""]]
    assert result["H:J"] == [["", "", ""]]


def test_bankroll_carryover_includes_the_typed_inputs_that_persist():
    # A new week is a copy of the template, whose placeholders ($10 deposited, $100 weekly
    # budget) are not the real values -- Budget/Deposited/Withdrawn/Weekly Budget must come
    # from the outgoing sheet along with the Starting balances (found Week 4, 2026-10-01).
    from dfs.week import BANKROLL_CARRYOVER_CELLS

    pairs = dict(BANKROLL_CARRYOVER_CELLS)
    assert pairs["B2"] == "B1" and pairs["I2"] == "I1" and pairs["L2"] == "L1"
    for typed in ("D1", "D2", "D3", "B6"):
        assert pairs[typed] == typed
    # each destination is written once -- two sources into one cell would silently clobber
    destinations = [dst for _src, dst in BANKROLL_CARRYOVER_CELLS]
    assert len(destinations) == len(set(destinations))


# ---------------------------------------------------------------------------------------------
# Sam's renamed Results team-colour headers survive `dfs week new`
# ---------------------------------------------------------------------------------------------

SAMS_HEADERS = ["Red/Orange", "Blue/Green", "Black/White/Purple"]


def test_the_team_colour_range_is_one_of_the_carried_value_ranges():
    from dfs.week import RESULTS_TEAM_COLOUR_RANGE, RESULTS_VALUE_COLUMN_RANGES, range_width

    assert RESULTS_TEAM_COLOUR_RANGE in RESULTS_VALUE_COLUMN_RANGES
    assert range_width(RESULTS_TEAM_COLOUR_RANGE) == 3
    assert range_width("A") == 1 and range_width("B:C") == 2 and range_width("AA:AB") == 2


def test_renamed_headers_replace_the_templates_names():
    from dfs.week import carry_team_colour_headers

    assert carry_team_colour_headers(SAMS_HEADERS, ["Red", "Blue", "Black"]) == SAMS_HEADERS


def test_a_blank_outgoing_header_never_wipes_a_name():
    from dfs.week import carry_team_colour_headers

    assert carry_team_colour_headers(["Red/Orange", "", None], ["Red", "Blue", "Black"]) == [
        "Red/Orange",
        "Blue",
        "Black",
    ]
    assert carry_team_colour_headers([], ["Red", "Blue", "Black"]) == ["Red", "Blue", "Black"]


def test_short_rows_from_the_sheets_api_are_padded():
    from dfs.week import carry_team_colour_headers

    assert carry_team_colour_headers(["Red/Orange"], []) == ["Red/Orange", "", ""]


def test_nothing_to_write_when_the_new_sheet_already_has_them():
    from dfs.week import team_colour_headers_to_write

    assert team_colour_headers_to_write(SAMS_HEADERS, list(SAMS_HEADERS)) is None
    assert team_colour_headers_to_write(SAMS_HEADERS, ["Red", "Blue", "Black"]) == SAMS_HEADERS


class _HeaderClient:
    """Just enough of a sheet for `_carry_results_team_colour_headers`."""

    def __init__(self, headers):
        self.headers = list(headers)
        self.writes: list[tuple[str, str, list]] = []

    def read_range(self, tab, a1):
        return [list(self.headers)]

    def update_range(self, tab, a1, rows):
        self.writes.append((tab, a1, rows))
        self.headers = list(rows[0])


def test_a_simulated_week_new_carries_the_renamed_headers_onto_a_fresh_template_copy(monkeypatch):
    from dfs import cli
    from dfs.config import ResultsConfig

    restyled = []
    monkeypatch.setattr(
        cli, "style_results", lambda client, tab, *, last_row: restyled.append((tab, last_row))
    )
    outgoing = _HeaderClient(SAMS_HEADERS)  # last week's sheet, names typed by Sam
    fresh_copy = _HeaderClient(["Red", "Blue", "Black"])  # a new copy of the template

    carried = cli._carry_results_team_colour_headers(outgoing, fresh_copy, ResultsConfig())

    assert carried == SAMS_HEADERS
    assert fresh_copy.headers == SAMS_HEADERS
    assert fresh_copy.writes == [("Results", "H1:J1", [SAMS_HEADERS])]  # the team-colour header cells only
    assert outgoing.writes == []  # the outgoing sheet is only ever read
    assert restyled == [("Results", ResultsConfig().last_row)]  # widths re-applied for the longer names


def test_a_simulated_week_new_leaves_a_sheet_that_already_has_the_names_alone(monkeypatch):
    from dfs import cli
    from dfs.config import ResultsConfig

    monkeypatch.setattr(cli, "style_results", lambda *a, **k: pytest.fail("no restyle when nothing changed"))
    fresh_copy = _HeaderClient(SAMS_HEADERS)
    assert (
        cli._carry_results_team_colour_headers(_HeaderClient(SAMS_HEADERS), fresh_copy, ResultsConfig())
        is None
    )
    assert fresh_copy.writes == []


def test_a_sheets_error_while_carrying_the_headers_is_a_warning_not_a_stop():
    from dfs import cli
    from dfs.config import ResultsConfig
    from dfs.sheets import SheetsError

    class Broken(_HeaderClient):
        def read_range(self, tab, a1):
            raise SheetsError("no such tab")

    assert cli._carry_results_team_colour_headers(Broken([]), _HeaderClient([]), ResultsConfig()) is None
