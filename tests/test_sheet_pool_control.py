from dfs.derived import EDGE_COLUMNS, EDGE_DATA_OFFSET
from dfs.sheet_pool_control import ensure_pool_control_row
from dfs.sheets import column_letter
from dfs.weekly_reset import PLAYER_POOL_CONTROL_ROW

_EDGE_NAME_COL = column_letter(EDGE_COLUMNS.index("Name") + EDGE_DATA_OFFSET)


class SpyClient:
    def __init__(self, label_cell_value: str = ""):
        self._label_cell_value = label_cell_value
        self.insert_calls: list[tuple[str, int, int]] = []
        self.format_calls: list[tuple[str, dict]] = []
        self.clear_validation_calls: list[str] = []
        self.update_calls: list[tuple[str, list[list]]] = []
        self.dropdown_calls: list[tuple[str, str]] = []

    def read_range(self, tab_name: str, a1_range: str):
        if a1_range == f"A{PLAYER_POOL_CONTROL_ROW}":
            return [[self._label_cell_value]] if self._label_cell_value else []
        raise AssertionError(f"unexpected read_range: {a1_range!r}")

    def insert_rows(self, tab_name: str, *, at_row: int, count: int) -> None:
        self.insert_calls.append((tab_name, at_row, count))

    def format_range(self, tab_name: str, a1_range: str, fmt: dict) -> None:
        self.format_calls.append((a1_range, fmt))

    def clear_data_validation(self, tab_name: str, a1_range: str) -> None:
        self.clear_validation_calls.append(a1_range)

    def update_range(self, tab_name: str, a1_range: str, rows: list[list]) -> None:
        self.update_calls.append((a1_range, rows))

    def set_range_dropdown_validation(self, tab_name: str, a1_range: str, *, source: str) -> None:
        self.dropdown_calls.append((a1_range, source))


def test_fresh_sheet_inserts_one_row_at_the_top():
    client = SpyClient()
    result = ensure_pool_control_row(client, "Player Pool", "EdgeRaw")

    assert client.insert_calls == [("Player Pool", PLAYER_POOL_CONTROL_ROW, 1)]
    assert "inserted" in result


def test_already_migrated_sheet_does_not_insert_again():
    client = SpyClient(label_cell_value="Add a player")
    result = ensure_pool_control_row(client, "Player Pool", "EdgeRaw")

    assert client.insert_calls == []
    assert "refresh" in result


def test_writes_the_label_and_validated_input_every_time():
    client = SpyClient()
    ensure_pool_control_row(client, "Player Pool", "EdgeRaw")

    assert (f"A{PLAYER_POOL_CONTROL_ROW}", [["Add a player"]]) in client.update_calls
    a1, source = client.dropdown_calls[0]
    assert a1 == f"B{PLAYER_POOL_CONTROL_ROW}"
    assert source == f"EdgeRaw!${_EDGE_NAME_COL}$2:${_EDGE_NAME_COL}"


def test_input_cell_gets_the_input_background():
    client = SpyClient()
    ensure_pool_control_row(client, "Player Pool", "EdgeRaw")

    input_cell = f"B{PLAYER_POOL_CONTROL_ROW}"
    fmt = next(fmt for a1, fmt in client.format_calls if a1 == input_cell)
    assert "backgroundColor" in fmt


def test_fresh_insert_resets_inherited_formatting_and_validation():
    client = SpyClient()
    ensure_pool_control_row(client, "Player Pool", "EdgeRaw")

    control_row_range = f"A{PLAYER_POOL_CONTROL_ROW}:Z{PLAYER_POOL_CONTROL_ROW}"
    assert any(a1 == control_row_range for a1, _fmt in client.format_calls)
    assert control_row_range in client.clear_validation_calls


def test_refresh_does_not_touch_inherited_formatting_reset():
    # Only the fresh (just-inserted) path needs the reset -- an
    # already-migrated sheet's row 1 is this module's own content, not
    # something inherited from insert_rows that needs clearing.
    client = SpyClient(label_cell_value="Add a player")
    ensure_pool_control_row(client, "Player Pool", "EdgeRaw")

    control_row_range = f"A{PLAYER_POOL_CONTROL_ROW}:Z{PLAYER_POOL_CONTROL_ROW}"
    assert control_row_range not in client.clear_validation_calls
