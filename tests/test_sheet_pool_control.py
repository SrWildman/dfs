import pytest

from dfs.derived import edge_sheet_letter
from dfs.sheet_columns import PLAYER_POOL_COLUMN_ORDER
from dfs.sheet_pool_control import (
    add_typed_player_to_pool,
    control_cells,
    ensure_pool_control_row,
    retire_added_list,
)
from dfs.sheets import column_letter
from dfs.weekly_reset import PLAYER_POOL_CONTROL_ROW, PLAYER_POOL_HEADER_ROW

_EDGE_NAME_COL = edge_sheet_letter("Name")
_ROW = PLAYER_POOL_CONTROL_ROW
_HEADER = list(PLAYER_POOL_COLUMN_ORDER)  # the designed header: Pool, Name, Pos., ...


class SpyClient:
    def __init__(self, label_in_row_one: bool = False, header: list[str] | None = None):
        self._label_in_row_one = label_in_row_one
        # Wider than Z (26 columns) by default -- proves the reset range is derived from the tab's own width.
        self._header = header if header is not None else _HEADER
        self.insert_calls: list[tuple[str, int, int]] = []
        self.format_calls: list[tuple[str, dict]] = []
        self.clear_validation_calls: list[str] = []
        self.update_calls: list[tuple[str, list[list]]] = []
        self.dropdown_calls: list[tuple[str, list[str]]] = []
        self.range_dropdown_calls: list[tuple[str, str]] = []
        self.notes: dict[str, str] = {}

    def read_range(self, tab_name: str, a1_range: str):
        if a1_range == f"A{_ROW}:{_ROW}":
            return [["", "Add a player"]] if self._label_in_row_one else []
        if a1_range == f"A{PLAYER_POOL_HEADER_ROW}:{PLAYER_POOL_HEADER_ROW}":
            return [self._header]
        raise AssertionError(f"unexpected read_range: {a1_range!r}")

    def insert_rows(self, tab_name: str, *, at_row: int, count: int) -> None:
        self.insert_calls.append((tab_name, at_row, count))

    def format_range(self, tab_name: str, a1_range: str, fmt: dict) -> None:
        self.format_calls.append((a1_range, fmt))

    def clear_data_validation(self, tab_name: str, a1_range: str) -> None:
        self.clear_validation_calls.append(a1_range)

    def update_range(self, tab_name: str, a1_range: str, rows: list[list]) -> None:
        self.update_calls.append((a1_range, rows))

    def set_dropdown_validation(self, tab_name: str, a1_range: str, options: list[str]) -> None:
        self.dropdown_calls.append((a1_range, options))

    def set_range_dropdown_validation(self, tab_name: str, a1_range: str, *, source: str) -> None:
        self.range_dropdown_calls.append((a1_range, source))

    def set_note(self, tab_name: str, a1: str, note: str) -> None:
        self.notes[a1] = note


def test_the_control_cells_follow_the_name_column_which_sits_right_of_pool():
    cells = control_cells(_HEADER)
    assert _HEADER[:3] == ["Pool", "Name", "Pos."]
    # the type dropdown sits in the Pool column, the label in Name's, the box beside it
    assert (cells["type"], cells["label"], cells["input"]) == (f"A{_ROW}", f"B{_ROW}", f"C{_ROW}")
    assert cells["resolved"] == f"{column_letter(_HEADER.index('Id'))}{_ROW}"  # the hidden Id column
    # an older header (Name still in A) cannot put the dropdown left of column A
    assert control_cells(["Name", "Pos."])["type"] == f"A{_ROW}"


def test_fresh_sheet_inserts_one_row_at_the_top():
    client = SpyClient()
    result = ensure_pool_control_row(client, "Player Pool", "EdgeRaw")

    assert client.insert_calls == [("Player Pool", PLAYER_POOL_CONTROL_ROW, 1)]
    assert "inserted" in result


def test_already_migrated_sheet_does_not_insert_again_wherever_the_label_now_sits():
    # The label moved from A to B when the Pool column moved to the front: still "migrated".
    client = SpyClient(label_in_row_one=True)
    result = ensure_pool_control_row(client, "Player Pool", "EdgeRaw")

    assert client.insert_calls == []
    assert "refresh" in result


def test_writes_the_label_type_dropdown_defaulting_to_both_and_the_validated_box_every_time():
    client = SpyClient()
    ensure_pool_control_row(client, "Player Pool", "EdgeRaw")

    cells = control_cells(_HEADER)
    assert (cells["label"], [["Add a player"]]) in client.update_calls
    assert (cells["type"], [["Both"]]) in client.update_calls
    assert client.dropdown_calls == [(cells["type"], ["Cash", "GPP", "Both"])]
    assert cells["type"] in client.notes
    a1, source = client.range_dropdown_calls[0]
    assert a1 == cells["input"] and source == f"EdgeRaw!${_EDGE_NAME_COL}$2:${_EDGE_NAME_COL}"
    resolved = next(rows for a1, rows in client.update_calls if a1 == cells["resolved"])
    assert resolved[0][0].startswith(f'=IF({cells["input"]}="","",')  # the sheet's own name resolver


def test_type_and_input_cells_get_the_input_background():
    client = SpyClient()
    ensure_pool_control_row(client, "Player Pool", "EdgeRaw")

    cells = control_cells(_HEADER)
    for cell in (cells["type"], cells["input"]):
        fmt = next(fmt for a1, fmt in client.format_calls if a1 == cell)
        assert "backgroundColor" in fmt


def test_fresh_insert_resets_inherited_formatting_and_validation():
    client = SpyClient(header=[f"Col{i}" for i in range(36)])
    client._header[0], client._header[1] = "Pool", "Name"
    ensure_pool_control_row(client, "Player Pool", "EdgeRaw")

    control_row_range = f"A{PLAYER_POOL_CONTROL_ROW}:AJ{PLAYER_POOL_CONTROL_ROW}"
    assert any(a1 == control_row_range for a1, _fmt in client.format_calls)
    assert control_row_range in client.clear_validation_calls


def test_reset_range_never_shrinks_below_the_original_a_to_z_width():
    client = SpyClient(header=["Pool", "Name", "Pos."])
    ensure_pool_control_row(client, "Player Pool", "EdgeRaw")

    control_row_range = f"A{PLAYER_POOL_CONTROL_ROW}:Z{PLAYER_POOL_CONTROL_ROW}"
    assert any(a1 == control_row_range for a1, _fmt in client.format_calls)


class AddSpyClient:
    """Fake for `add_typed_player_to_pool`: the box, the type beside it, and EdgeRaw's Name column."""

    def __init__(self, typed: str = "", pool_type: str = "", edge_names: list[str] | None = None):
        self._typed, self._type = typed, pool_type
        self._edge_names = edge_names if edge_names is not None else ["Cooper Kupp", "Kenneth Walker III"]
        self.update_calls: list[tuple[str, str, list[list]]] = []
        self.clear_calls: list[list[str]] = []

    def read_range(self, tab_name: str, a1_range: str):
        cells = control_cells(_HEADER)
        if a1_range == f"A{PLAYER_POOL_HEADER_ROW}:{PLAYER_POOL_HEADER_ROW}":
            return [_HEADER]
        if a1_range == cells["input"]:
            return [[self._typed]] if self._typed else []
        if a1_range == cells["type"]:
            return [[self._type]] if self._type else []
        if a1_range == f"{_EDGE_NAME_COL}2:{_EDGE_NAME_COL}":
            return [[n] for n in self._edge_names]
        raise AssertionError(f"unexpected read_range: {a1_range!r}")

    def update_range(self, tab_name: str, a1_range: str, rows: list[list]) -> None:
        self.update_calls.append((tab_name, a1_range, rows))

    def clear_ranges(self, tab_name: str, a1_ranges: list[str]) -> None:
        self.clear_calls.append(list(a1_ranges))


def test_nothing_is_added_when_the_box_is_blank():
    client = AddSpyClient()
    assert "no pending" in add_typed_player_to_pool(client, "Player Pool", "EdgeRaw")
    assert client.update_calls == [] and client.clear_calls == []


def test_a_typed_name_is_set_on_edgeraw_pool_as_the_chosen_type_and_the_box_clears():
    client = AddSpyClient(typed="kenneth walker", pool_type="GPP")
    result = add_typed_player_to_pool(client, "Player Pool", "EdgeRaw")

    # EdgeRaw's Pool column is column A; the player is the second name, so sheet row 3
    assert client.update_calls == [("EdgeRaw", "A3", [["GPP"]])]
    assert client.clear_calls == [[control_cells(_HEADER)["input"]]]
    assert "Kenneth Walker III" in result and "GPP" in result


def test_the_type_defaults_to_both_when_blank_or_not_a_pool_value():
    for pool_type in ("", "Remove", "nonsense"):
        client = AddSpyClient(typed="Cooper Kupp", pool_type=pool_type)
        add_typed_player_to_pool(client, "Player Pool", "EdgeRaw")
        assert client.update_calls == [("EdgeRaw", "A2", [["Both"]])]


def test_a_name_edgeraw_does_not_have_is_left_in_the_box():
    client = AddSpyClient(typed="Nobody Real", pool_type="Cash")
    result = add_typed_player_to_pool(client, "Player Pool", "EdgeRaw")

    assert client.update_calls == [] and client.clear_calls == []
    assert "left in the box" in result


class RetireSpyClient:
    """Fake for `retire_added_list`: a header carrying (or not) the `Added` column, and what it holds."""

    def __init__(self, added: list[str] | None = None, with_column: bool = True):
        self._header = [*_HEADER, "Added"] if with_column else list(_HEADER)
        self._added = added or []
        self.deleted: list[int] = []

    def read_range(self, tab_name: str, a1_range: str):
        if a1_range == f"A{PLAYER_POOL_HEADER_ROW}:{PLAYER_POOL_HEADER_ROW}":
            return [self._header]
        return [[name] for name in self._added]

    def delete_columns(self, tab_name: str, *, at_index: int, count: int) -> None:
        self.deleted.append(at_index)


def test_retiring_the_added_list_is_a_no_op_once_the_column_is_gone():
    client = RetireSpyClient(with_column=False)
    assert "already retired" in retire_added_list(client, "Player Pool", "EdgeRaw")
    assert client.deleted == []


def test_retiring_the_added_list_refuses_while_it_still_holds_names():
    client = RetireSpyClient(added=["Cooper Kupp"])
    with pytest.raises(ValueError, match="Cooper Kupp"):
        retire_added_list(client, "Player Pool", "EdgeRaw")
    assert client.deleted == []  # nothing a person typed is lost


def test_retiring_an_empty_added_list_rewrites_the_formulas_first_then_deletes_the_column(monkeypatch):
    import dfs.sheet_pool_control as control

    order: list[str] = []
    monkeypatch.setattr(control, "write_pool_formulas", lambda *a, **k: order.append("formulas") or [])
    client = RetireSpyClient()
    client.delete_columns = lambda tab, *, at_index, count: order.append(f"delete@{at_index}")
    retire_added_list(client, "Player Pool", "EdgeRaw")
    assert order == ["formulas", f"delete@{len(_HEADER)}"]  # the formulas stop reading it before it goes
