import pytest

from dfs.sheet_columns import PLAYER_POOL_RAW_COLUMN_ORDER
from dfs.sheet_native_links import (
    NATIVE_LOOKUP_COLUMNS,
    native_lookup_formula,
    rewrite_native_lookup_columns,
)
from dfs.sheets import column_letter


class SpySheetsClient:
    def __init__(self, header_row: list[str]):
        self.header_row = header_row
        self.update_calls: list[tuple[str, str, list[list]]] = []

    def read_range(self, tab_name: str, a1_range: str):
        return [self.header_row] if self.header_row else []

    def update_range(self, tab_name, a1_range, rows):
        self.update_calls.append((tab_name, a1_range, rows))


def test_native_lookup_formula_derives_range_and_index_from_column_order():
    index = PLAYER_POOL_RAW_COLUMN_ORDER.index("Team") + 1
    col = column_letter(index - 1)
    assert native_lookup_formula(5, "Team") == (
        f'=IF($A5="","",VLOOKUP($A5,PlayerPoolRaw!$A:${col},{index},false))'
    )


def test_native_lookup_formula_wraps_only_the_optional_columns_in_ifna():
    assert "IFNA(VLOOKUP(" in native_lookup_formula(2, "O/U")
    assert "IFNA(" not in native_lookup_formula(2, "Team")


def test_native_lookup_formula_guards_against_a_blank_name():
    # Same live-sheet finding as sheet_links.edge_lookup_formula: a blank
    # Name cell (an unfilled slot) must resolve to blank, not #N/A.
    formula = native_lookup_formula(5, "Team")
    assert formula.startswith('=IF($A5="","",')
    assert formula.endswith(")")


def test_rewrite_native_lookup_columns_writes_every_column_for_every_block():
    client = SpySheetsClient(header_row=PLAYER_POOL_RAW_COLUMN_ORDER)
    result = rewrite_native_lookup_columns(client, "Player Pool", [(2, 3), (5, 5)])

    assert "12 native" in result
    total_cells_written = sum(len(rows) * len(rows[0]) for _, _, rows in client.update_calls)
    assert total_cells_written == len(NATIVE_LOOKUP_COLUMNS) * 3  # 2 rows + 1 row, 12 columns each


def test_rewrite_native_lookup_columns_finds_each_column_by_header_name():
    # Team sits at whatever index the header actually has it, not a
    # hardcoded position -- shuffle the header and confirm the write still
    # lands on the right column letter.
    header = ["Name", "Extra1", "Extra2", *NATIVE_LOOKUP_COLUMNS]
    client = SpySheetsClient(header_row=header)
    rewrite_native_lookup_columns(client, "Player Pool", [(2, 2)])

    team_col = column_letter(header.index("Team"))
    team_calls = [a1 for _, a1, _ in client.update_calls if a1.startswith(f"{team_col}2")]
    assert team_calls, f"expected a write starting at {team_col}2"


def test_rewrite_native_lookup_columns_raises_on_missing_column():
    header = [name for name in PLAYER_POOL_RAW_COLUMN_ORDER if name != "Team"]
    client = SpySheetsClient(header_row=header)
    with pytest.raises(ValueError, match="Team"):
        rewrite_native_lookup_columns(client, "Player Pool", [(2, 3)])
