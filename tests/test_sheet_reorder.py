import re

from dfs.sheet_columns import LINKED_COLUMNS, PLAYER_POOL_RAW_COLUMN_ORDER
from dfs.sheet_links import edge_lookup_formula
from dfs.sheet_reorder import (
    migrate_tab_to_designed_order,
    provision_missing_columns,
    reorder_tab_columns,
    resync_header_repeats,
)

_START_CELL_RE = re.compile(r"^([A-Z]*)(\d+)")


def _col_index(letter: str) -> int:
    value = 0
    for ch in letter:
        value = value * 26 + (ord(ch) - ord("A") + 1)
    return value - 1


def _start_cell(a1_range: str) -> tuple[str, int]:
    match = _START_CELL_RE.match(a1_range)
    return match.group(1), int(match.group(2))


class SpySheetsClient:
    def __init__(self, header_row: list[str]):
        self.header_row = header_row
        self.update_calls: list[tuple[str, str, list[list]]] = []
        self.move_calls: list[tuple[str, int, int]] = []

    def read_range(self, tab_name: str, a1_range: str):
        return [self.header_row] if self.header_row else []

    def update_range(self, tab_name, a1_range, rows):
        self.update_calls.append((tab_name, a1_range, rows))
        # Simulate row 1 (the header) actually growing/changing in place,
        # the way a real sheet read would reflect on the next call --
        # regardless of which caller (provision_missing_columns vs.
        # link_edge_columns) wrote it, or in what order.
        start_col, start_row = _start_cell(a1_range)
        if start_row == 1 and rows:
            start_idx = _col_index(start_col)
            needed = start_idx + len(rows[0])
            while len(self.header_row) < needed:
                self.header_row.append("")
            for i, value in enumerate(rows[0]):
                self.header_row[start_idx + i] = value

    def move_columns(self, tab_name, *, from_index, to_index):
        self.move_calls.append((tab_name, from_index, to_index))
        name = self.header_row.pop(from_index)
        self.header_row.insert(to_index, name)

    def add_color_scale(self, tab_name, a1_range, **_colors):
        pass

    def group_columns(self, tab_name, first_col_a1, last_col_a1, *, collapsed=False):
        pass

    def clear_column_groups(self, tab_name):
        pass


def test_provision_missing_columns_creates_nothing_already_present():
    client = SpySheetsClient(header_row=["A", "B", "C"])
    created = provision_missing_columns(client, "T", ["A", "B", "C"])
    assert created == []
    assert client.update_calls == []


def test_provision_missing_columns_appends_only_whats_missing_in_target_order():
    client = SpySheetsClient(header_row=["A", "C"])
    created = provision_missing_columns(client, "T", ["A", "B", "C", "D"])
    assert created == ["B", "D"]
    assert client.update_calls == [("T", "C1:D1", [["B", "D"]])]


def test_provision_missing_columns_repeats_header_at_given_rows():
    client = SpySheetsClient(header_row=["A"])
    provision_missing_columns(client, "T", ["A", "B"], header_repeats_at=[5, 9])
    written_ranges = [a1 for _, a1, _ in client.update_calls]
    assert "B1:B1" in written_ranges
    assert "B5:B5" in written_ranges
    assert "B9:B9" in written_ranges


def test_reorder_tab_columns_applies_moves_and_returns_them():
    client = SpySheetsClient(header_row=["C", "A", "B"])
    moves = reorder_tab_columns(client, "T", ["A", "B", "C"])
    assert client.header_row == ["A", "B", "C"]
    assert moves  # at least one move happened
    assert client.move_calls == [("T", from_idx, to_idx) for from_idx, to_idx in moves]


def test_reorder_tab_columns_is_a_noop_when_already_in_order():
    client = SpySheetsClient(header_row=["A", "B", "C"])
    moves = reorder_tab_columns(client, "T", ["A", "B", "C"])
    assert moves == []
    assert client.move_calls == []


def test_reorder_tab_columns_raises_on_mismatched_column_set():
    client = SpySheetsClient(header_row=["A", "B", "Extra"])
    try:
        reorder_tab_columns(client, "T", ["A", "B", "C"])
    except ValueError as e:
        assert "Extra" in str(e) or "C" in str(e)
    else:
        raise AssertionError("expected ValueError for a header that doesn't match target_order")


def test_provision_then_reorder_end_to_end():
    client = SpySheetsClient(header_row=["A", "C"])
    provision_missing_columns(client, "T", ["A", "B", "C"])
    assert client.header_row == ["A", "C", "B"]  # appended, not yet in place
    reorder_tab_columns(client, "T", ["A", "B", "C"])
    assert client.header_row == ["A", "B", "C"]


class _MultiRowFakeClient:
    """Tracks distinct content per row number -- SpySheetsClient above
    only ever models row 1, which can't represent a repeat row that's
    drifted from the primary header."""

    def __init__(self, rows: dict[int, list[str]]):
        self.rows = rows
        self.update_calls: list[tuple[str, str, list[list]]] = []

    def read_range(self, tab_name: str, a1_range: str):
        _, row_num = _start_cell(a1_range)
        return [self.rows.get(row_num, [])]

    def update_range(self, tab_name, a1_range, rows):
        self.update_calls.append((tab_name, a1_range, rows))
        _, row_num = _start_cell(a1_range)
        self.rows[row_num] = list(rows[0])


def test_resync_header_repeats_overwrites_every_repeat_row_with_the_primary():
    client = _MultiRowFakeClient(
        rows={
            11: ["Name", "Pos.", "Team"],
            24: ["Name", "STALE", "DRIFTED"],  # simulates the drift found on the template
            37: ["Name", "Pos."],  # even shorter -- also just gets overwritten
        }
    )
    resynced = resync_header_repeats(client, "Lineups", header_row=11, header_repeats_at=[24, 37])
    assert resynced == 2
    assert client.rows[24] == ["Name", "Pos.", "Team"]
    assert client.rows[37] == ["Name", "Pos.", "Team"]


def test_resync_header_repeats_is_a_noop_without_repeat_rows():
    client = _MultiRowFakeClient(rows={11: ["Name", "Pos."]})
    assert resync_header_repeats(client, "Lineups", header_row=11, header_repeats_at=None) == 0
    assert client.update_calls == []


def test_migrate_tab_to_designed_order_lands_on_the_target_header():
    # Old-style header: only 24 of PLAYER_POOL_RAW_COLUMN_ORDER's 34 names
    # exist yet, in the OLD append-only order (the ten already-linked
    # columns bunched together at the tail) -- the real PlayerPoolRaw's
    # actual shape right before Phase 3's live migration.
    _new_linked_names = ("Id", "OwnPct", "ImpMove", "TotMove", "SpdMove", "GameStart")
    old_header = [name for name in PLAYER_POOL_RAW_COLUMN_ORDER if name not in LINKED_COLUMNS] + [
        name for name in LINKED_COLUMNS if name not in _new_linked_names
    ]
    client = SpySheetsClient(header_row=old_header)

    migrate_tab_to_designed_order(
        client,
        "PlayerPoolRaw",
        PLAYER_POOL_RAW_COLUMN_ORDER,
        name_blocks=[(2, 3)],
        edge_tab="EdgeRaw",
        rewrite_native=False,
    )

    assert client.header_row == PLAYER_POOL_RAW_COLUMN_ORDER


def test_migrate_tab_to_designed_order_refreshes_every_linked_formula_not_just_new_ones():
    # The trap this module exists to avoid: CeilVal was ALREADY linked
    # under the old order, with a formula baked to the OLD (now wrong)
    # EDGE_COLUMNS index. Six other linked names (Id, OwnPct, ...) are
    # genuinely new. Because something's missing, every linked column's
    # formula -- including CeilVal's, already-present -- must be
    # rewritten with the CURRENT correct index, not silently left stale.
    old_header = [name for name in PLAYER_POOL_RAW_COLUMN_ORDER if name not in LINKED_COLUMNS] + ["CeilVal"]
    client = SpySheetsClient(header_row=old_header)

    migrate_tab_to_designed_order(
        client,
        "PlayerPoolRaw",
        PLAYER_POOL_RAW_COLUMN_ORDER,
        name_blocks=[(2, 3)],
        edge_tab="EdgeRaw",
        rewrite_native=False,
    )

    expected_formula = edge_lookup_formula(2, "EdgeRaw", "CeilVal")
    written_formulas = {
        cell for _, a1, rows in client.update_calls if not a1.endswith("1") for row in rows for cell in row
    }
    assert expected_formula in written_formulas
