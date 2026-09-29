from dfs.nfl_calendar import MAX_WEEK
from dfs.sheet_season_view import (
    FIRST_ROW,
    HEADER,
    LAST_ROW,
    YTD_BETTING_ROW,
    YTD_CASH_ROW,
    YTD_GPP_ROW,
    YTD_TOTAL_ROW,
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
    assert cash_row[3] == f"=B{YTD_CASH_ROW}/C{YTD_CASH_ROW}"

    gpp_row = grid[YTD_GPP_ROW - 1]
    assert gpp_row[0] == "GPP"

    betting_row = grid[YTD_BETTING_ROW - 1]
    assert betting_row[0] == "Betting"
    assert "SUM(J" in betting_row[4]  # record uses wins/losses/pushes
    assert "vs" in betting_row[5]  # expected vs actual wins

    total_row = grid[YTD_TOTAL_ROW - 1]
    assert total_row[0] == "Total"
