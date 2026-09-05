import pytest

from dfs.week import extract_results_value_columns, parse_sheet_id_from_url, rewrite_sheet_id


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
