"""Results' Cash Line: a blank cell is filled from the standings, a typed one is never overwritten."""

import pandas as pd

from dfs.config import ResultsConfig
from dfs.contests_cash_line import DEFAULT_SERIES, fill_cash_lines, standings_cash_lines

HEADER = ["Week", "Cash Pts", "Cash Line", "Cash Results", "H2H Entered", "H2H Win"]


class FakeClient:
    def __init__(self, rows):
        self.rows = rows
        self.written: dict[str, list[list]] = {}

    def read_range_unformatted(self, tab, a1):
        return [HEADER, *self.rows]

    def update_ranges(self, tab, updates):
        self.written.update(updates)


def profiles(*items):
    return pd.DataFrame(
        [
            {
                "contest_id": str(i),
                "season": 2026,
                "week": week,
                "format": "Double Up",
                "series": series,
                "kind": "Classic",
                "entrants": entrants,
                "cash_line": cash,
                "complete": True,
            }
            for i, (week, series, entrants, cash) in enumerate(items)
        ]
    )


def test_the_standings_cash_line_is_the_configured_series_only():
    table = profiles(
        (4, DEFAULT_SERIES, 2758, 105.98),
        (4, "NFL GIANT $10 Double Up [Single Entry]", 4597, 108.4),
        (5, DEFAULT_SERIES, 2758, 153.06),
    )
    assert standings_cash_lines(table, 2026) == {4: 105.98, 5: 153.06}
    assert standings_cash_lines(table, 2025) == {}
    assert standings_cash_lines(pd.DataFrame(), 2026) == {}


def test_two_files_of_one_series_in_a_week_take_the_larger_field():
    table = profiles((5, DEFAULT_SERIES, 100, 140.0), (5, DEFAULT_SERIES, 2758, 153.06))
    assert standings_cash_lines(table, 2026) == {5: 153.06}


def test_a_blank_cash_line_is_filled_and_written_to_the_column_found_by_header():
    client = FakeClient([[4, 66.0, "", "", 0, 0], [5, 70.0, "", "", 0, 0]])
    result = fill_cash_lines(client, ResultsConfig(), {5: 153.06})
    assert result.filled == {5: 153.06}
    assert client.written == {"C3": [[153.06]]}  # Week 5 is the second data row; Cash Line is column C


def test_a_typed_cash_line_is_never_overwritten_and_a_match_is_reported():
    client = FakeClient([[4, 66.0, 105.98, "", 0, 0]])
    result = fill_cash_lines(client, ResultsConfig(), {4: 106.0})
    assert not client.written and result.agreed == {4: 105.98} and not result.disagreed


def test_a_typed_cash_line_that_disagrees_is_kept_and_both_numbers_are_shown():
    client = FakeClient([[4, 66.0, 100.0, "", 0, 0]])
    result = fill_cash_lines(client, ResultsConfig(), {4: 106.0})
    assert not client.written and result.disagreed == {4: (100.0, 106.0)}
    assert "typed Cash Line 100 kept; the standings say 106" in " ".join(result.lines())


def test_a_week_with_no_results_row_is_reported():
    client = FakeClient([[4, 66.0, "", "", 0, 0]])
    result = fill_cash_lines(client, ResultsConfig(), {9: 120.0})
    assert result.no_row == [9] and not client.written
