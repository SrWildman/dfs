from dfs import sheet_clipping as sc


def test_a_text_wider_than_its_column_with_a_filled_neighbour_is_cut():
    rows = [["Name", "Pos"], ["Christian McCaffrey", "RB"]]
    clips = sc.find_clipped(rows, [80, 40], set())
    assert [(c.letter, c.row, c.text) for c in clips] == [("A", 2, "Christian McCaffrey")]


def test_a_text_overflows_into_an_empty_neighbour_and_is_not_cut():
    rows = [["A very long explanation of something", ""]]
    assert sc.find_clipped(rows, [80, 100], set()) == []


def test_a_number_never_overflows_even_with_an_empty_neighbour():
    rows = [["$1,234,567.00", ""], ["12345678", ""], ["33.3%", ""]]
    assert [c.text for c in sc.find_clipped(rows, [50, 100], set())] == ["$1,234,567.00", "12345678"]


def test_hidden_columns_are_neither_checked_nor_blocking():
    rows = [["This text is long enough to need room", "x-hidden", ""]]
    # B is hidden, so C (empty) is the next visible cell and the text overflows
    assert sc.find_clipped(rows, [60, 60, 100], {1}) == []
    # a hidden column is never reported itself
    assert sc.find_clipped([["", "this is long and hidden", "z"]], [60, 20, 100], {1}) == []


def test_headers_are_bold_so_they_need_a_little_more():
    plain = sc.estimate_px("Hit3x%", per_char=sc.DETECT_PX_PER_CHAR, padding=sc.DETECT_PADDING_PX)
    bold = sc.estimate_px("Hit3x%", per_char=sc.DETECT_PX_PER_CHAR, padding=sc.DETECT_PADDING_PX, bold=True)
    assert bold > plain
    rows = [["Hit3x%", "Boom%"]]
    width = plain  # fits as plain text, not as a bold header
    assert sc.find_clipped(rows, [width, 90], set()) == []
    assert [c.text for c in sc.find_clipped(rows, [width, 90], set(), bold_rows=frozenset({1}))] == ["Hit3x%"]


def test_a_smaller_font_needs_less_room():
    rows = [["Philadelphia Eagles", "x"]]
    assert sc.find_clipped(rows, [105, 50], set(), font_pt=10)
    assert sc.find_clipped(rows, [105, 50], set(), font_pt=8) == []


def test_the_fitting_estimate_is_at_least_the_detecting_one_so_a_fitted_column_never_trips_the_check():
    for text in ("Jonathan Taylor", "ValAdj", "$7,800", "62%", "Context only"):
        detect = sc.estimate_px(text, per_char=sc.DETECT_PX_PER_CHAR, padding=sc.DETECT_PADDING_PX)
        fit = sc.estimate_px(text, per_char=sc.FIT_PX_PER_CHAR, padding=sc.FIT_PADDING_PX)
        assert fit >= detect


def test_summarize_names_the_worst_cell_per_column_widest_first():
    clips = [sc.Clip(0, 5, "Short thing", 90, 80), sc.Clip(2, 7, "Much longer text indeed", 160, 80)]
    text = sc.summarize(clips, {2: "Why"})
    assert text.index("C ('Why')") < text.index("A:")
    assert "needs ~160px, has 80px" in text


def test_multiline_cells_are_as_wide_as_their_widest_line():
    assert sc.estimate_px("ab\nabcdefgh", per_char=7, padding=0) == sc.estimate_px(
        "abcdefgh", per_char=7, padding=0
    )


def test_fitting_widens_only_the_cut_columns_to_their_longest_cell_and_never_past_the_cap():
    rows = [["Name", "Pos", "Note"], ["Christian McCaffrey", "RB", "x"], ["Jonathan Taylor", "RB", "y"]]
    out = sc.fitted_widths(rows, [80, 40, 40], set())
    assert set(out) == {0}  # only the Name column was cut
    assert out[0] >= sc.estimate_px(
        "Christian McCaffrey", per_char=sc.FIT_PX_PER_CHAR, padding=sc.FIT_PADDING_PX
    )
    capped = sc.fitted_widths(rows, [80, 40, 40], set(), maxima={0: 100})
    assert capped == {0: 100}
    # never narrower than it already is, even when the cap is lower
    assert sc.fitted_widths(rows, [150, 40, 40], set(), maxima={0: 100}) == {}


def test_a_fitted_column_no_longer_trips_the_check():
    rows = [["Name", "Pos"], ["Christian McCaffrey", "RB"], ["$1,234,567", "RB"]]
    widths = [60, 30]
    fitted = sc.fitted_widths(rows, widths, set(), bold_rows=frozenset({1}))
    for column, px in fitted.items():
        widths[column] = px
    assert sc.find_clipped(rows, widths, set(), bold_rows=frozenset({1})) == []


def test_fitting_ignores_hidden_columns_and_text_that_can_overflow():
    rows = [["A long label that spills right", "", "x"]]
    assert sc.fitted_widths(rows, [60, 60, 60], set()) == {}  # the next cell is empty: no need to widen
    assert (
        sc.fitted_widths([["Too long for here", "y"]], [40, 40], {0}) == {}
    )  # a hidden column is left alone
