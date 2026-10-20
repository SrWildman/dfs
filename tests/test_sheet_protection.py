from dfs.sheet_columns import PLAYER_POOL_COLUMN_ORDER
from dfs.sheet_protection import FULLY_PROTECTED_TABS, UNPROTECTED_VIEW_TABS, protect_workbook
from dfs.weekly_reset import LINEUPS_NAME_BLOCKS, PLAYER_POOL_CONTROL_ROW


class FakeProtectionClient:
    def __init__(self, *, missing: set[str] | None = None):
        self._missing = missing or set()
        self.clear_calls: list[str] = []
        self.protect_calls: list[tuple[str, dict]] = []

    def tab_exists(self, tab_name: str) -> bool:
        return tab_name not in self._missing

    def read_range(self, tab_name: str, a1_range: str):
        return [list(PLAYER_POOL_COLUMN_ORDER)]  # Player Pool's header

    def clear_protected_ranges(self, tab_name: str) -> None:
        self.clear_calls.append(tab_name)

    def protect_sheet(
        self, tab_name: str, *, warning_only: bool = True, unprotected_ranges=None, description=None
    ):
        kwargs = {
            "unprotected_ranges": unprotected_ranges,
            "warning_only": warning_only,
            "description": description,
        }
        self.protect_calls.append((tab_name, kwargs))


def test_protects_every_fully_protected_tab_with_no_exceptions():
    client = FakeProtectionClient()
    protect_workbook(client, lineups_tab="Lineups")

    protected_tabs = [tab for tab, _kwargs in client.protect_calls]
    for tab in FULLY_PROTECTED_TABS:
        assert tab in protected_tabs
    for tab, kwargs in client.protect_calls:
        if tab in FULLY_PROTECTED_TABS:
            assert kwargs["unprotected_ranges"] is None
            assert kwargs["warning_only"] is True


def test_clears_before_protecting_every_tab():
    client = FakeProtectionClient()
    protect_workbook(client, lineups_tab="Lineups")

    for tab, _kwargs in client.protect_calls:
        assert tab in client.clear_calls
    # clear always precedes its own tab's protect call
    for tab in [t for t, _ in client.protect_calls]:
        assert client.clear_calls.index(tab) < len(client.clear_calls)


def test_exposure_leaves_target_and_lineup_count_cell_unprotected():
    client = FakeProtectionClient()
    protect_workbook(client, lineups_tab="Lineups")

    _tab, kwargs = next(c for c in client.protect_calls if c[0] == "Exposure")
    assert kwargs["unprotected_ranges"] == ["F:F"]  # H1 is counted by a formula now, not typed


def test_lineups_leaves_every_block_name_column_unprotected():
    client = FakeProtectionClient()
    protect_workbook(client, lineups_tab="Lineups")

    _tab, kwargs = next(c for c in client.protect_calls if c[0] == "Lineups")
    unprotected = kwargs["unprotected_ranges"]
    # No more deck controls (B1/D1/F1) to carve out -- the pool deck was
    # removed entirely; only each block's own Name column stays typeable.
    assert "B1" not in unprotected
    for start, end in LINEUPS_NAME_BLOCKS:
        assert f"A{start}:A{end}" in unprotected


def test_lineups_also_leaves_each_blocks_cash_gpp_marker_unprotected():
    """Sam, 2026-10-17: the marker (column A of each Total row, right under the nine names) is a dropdown he
    sets on every lineup; it warned like a formula cell. Its row is `weekly_reset.LINEUPS_TOTALS_ROWS`."""
    from dfs.weekly_reset import LINEUPS_TOTALS_ROWS

    client = FakeProtectionClient()
    results = protect_workbook(client, lineups_tab="Lineups")

    _tab, kwargs = next(c for c in client.protect_calls if c[0] == "Lineups")
    unprotected = kwargs["unprotected_ranges"]
    for total in LINEUPS_TOTALS_ROWS:
        assert f"A{total}" in unprotected
    assert len(unprotected) == 2 * len(LINEUPS_NAME_BLOCKS)  # nothing else was opened up
    assert "Cash/GPP marker" in kwargs["description"]
    assert any("Cash/GPP marker" in r for r in results if r.startswith("Lineups"))


def test_skips_a_missing_tab_without_erroring():
    client = FakeProtectionClient(missing={"Board"})
    results = protect_workbook(client, lineups_tab="Lineups")
    assert any("Board: not present -- skipped" == r for r in results)
    assert "Board" not in [tab for tab, _ in client.protect_calls]


def test_edgeraw_is_never_protected():
    # EdgeRaw's whole point is that Sam types into it (Pool) constantly.
    client = FakeProtectionClient()
    protect_workbook(client, lineups_tab="Lineups")
    protected_tabs = {tab for tab, _ in client.protect_calls}
    assert "EdgeRaw" not in protected_tabs


def test_player_pool_leaves_the_pool_column_and_the_add_a_player_box_unprotected():
    client = FakeProtectionClient()
    protect_workbook(client, lineups_tab="Lineups")

    _tab, kwargs = next(c for c in client.protect_calls if c[0] == "Player Pool")
    # Pool is column A and the box is the Name column's (B) cell of the control row
    assert kwargs["unprotected_ranges"] == ["A:A", f"B{PLAYER_POOL_CONTROL_ROW}"]
    assert kwargs["warning_only"] is True


def test_edge_finder_and_board_are_not_protected_and_any_old_protection_is_cleared():
    client = FakeProtectionClient()
    protect_workbook(client, lineups_tab="Lineups")

    assert set(UNPROTECTED_VIEW_TABS) == {"Edge Finder", "Board"}
    protected = {tab for tab, _ in client.protect_calls}
    for tab in UNPROTECTED_VIEW_TABS:
        assert tab not in protected and tab in client.clear_calls  # folding a group must not warn


def test_suggested_is_protected_except_the_lock_column_and_the_name_column():
    """Typed cells: the lock ticks, and the Name column (decision cells, rules box, names typed over a
    player).
    Whole columns, because the rows move with every run; not a fully protected tab any more."""
    from dfs.sheet_suggested import TAB, typed_ranges

    client = FakeProtectionClient()
    results = protect_workbook(client, lineups_tab="Lineups")
    assert TAB not in FULLY_PROTECTED_TABS
    calls = [kwargs for tab, kwargs in client.protect_calls if tab == TAB]
    assert len(calls) == 1 and calls[0]["unprotected_ranges"] == typed_ranges() == ["B:B", "C:C"]
    assert calls[0]["warning_only"] is True and client.clear_calls.count(TAB) == 1
    assert any(line.startswith(f"{TAB}:") and "lock column" in line for line in results)
    missing = FakeProtectionClient(missing={TAB})
    assert f"{TAB}: not present -- skipped" in protect_workbook(missing, lineups_tab="Lineups")
