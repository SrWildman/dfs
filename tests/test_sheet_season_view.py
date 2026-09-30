from dfs.nfl_calendar import MAX_WEEK
from dfs.sheet_season_view import (
    FIRST_ROW,
    HEADER,
    LAST_ROW,
    YTD_BETTING_ROW,
    YTD_CASH_ROW,
    YTD_GPP_ROW,
    YTD_TOTAL_ROW,
    add_season_chart,
    build_season_grid,
)


def test_grid_has_one_row_per_week_plus_header_and_ytd_block():
    grid = build_season_grid()
    assert grid[0] == HEADER
    assert LAST_ROW - FIRST_ROW + 1 == MAX_WEEK


def test_week_rows_are_numbered_1_through_max_week():
    grid = build_season_grid()
    week_rows = grid[1 : 1 + MAX_WEEK]
    assert [row[0] for row in week_rows] == list(range(1, MAX_WEEK + 1))


def test_total_net_formula_sums_cash_gpp_betting_for_its_own_row():
    grid = build_season_grid()
    row_5 = grid[5]  # week 5, sheet row FIRST_ROW + 5 - 1
    sheet_row = FIRST_ROW + 5 - 1
    assert row_5[4] == f"=B{sheet_row}+C{sheet_row}+D{sheet_row}"


def test_cumulative_columns_are_running_sums_from_first_row():
    grid = build_season_grid()
    row_3 = grid[3]  # week 3
    sheet_row = FIRST_ROW + 3 - 1
    assert row_3[13] == f"=SUM($B${FIRST_ROW}:B{sheet_row})"
    assert row_3[16] == f"=N{sheet_row}+O{sheet_row}+P{sheet_row}"


def test_ytd_rows_sum_the_full_week_range():
    grid = build_season_grid()
    cash_row = grid[YTD_CASH_ROW - 1]
    assert cash_row[0] == "Cash"
    assert cash_row[1] == f"=SUM(B{FIRST_ROW}:B{LAST_ROW})"
    # guarded: a season with nothing risked yet reads blank, not #DIV/0!
    assert cash_row[3] == f'=IF(C{YTD_CASH_ROW}="","",IFERROR(B{YTD_CASH_ROW}/C{YTD_CASH_ROW},""))'

    gpp_row = grid[YTD_GPP_ROW - 1]
    assert gpp_row[0] == "GPP"

    betting_row = grid[YTD_BETTING_ROW - 1]
    assert betting_row[0] == "Betting"
    assert "SUM(J" in betting_row[4]  # record uses wins/losses/pushes
    assert "vs" in betting_row[5]  # expected vs actual wins

    total_row = grid[YTD_TOTAL_ROW - 1]
    assert total_row[0] == "Total"


def test_every_ytd_roi_cell_is_guarded_so_an_empty_season_reads_blank():
    grid = build_season_grid()
    for row in (YTD_CASH_ROW, YTD_GPP_ROW, YTD_BETTING_ROW, YTD_TOTAL_ROW):
        assert grid[row - 1][3] == f'=IF(C{row}="","",IFERROR(B{row}/C{row},""))'


class _ChartClient:
    def __init__(self):
        self.calls = []

    def update_range(self, *a, **k):
        self.calls.append(("update_range", a))

    def format_range(self, *a, **k):
        pass

    def delete_charts(self, *a, **k):
        self.calls.append(("delete_charts", a))

    def add_line_chart(self, *a, **k):
        self.calls.append(("add_line_chart", k))

    def hide_columns(self, tab, first, last, **k):
        self.calls.append(("hide_columns", (tab, first, last)))


def test_the_chart_helper_block_is_hidden_and_the_chart_still_plots_it():
    client = _ChartClient()
    add_season_chart(client, "Season")
    chart = next(k for name, k in client.calls if name == "add_line_chart")
    assert chart["plot_hidden_data"] is True  # else hiding S:W would blank the chart
    assert ("hide_columns", ("Season", "S", "W")) in client.calls
