from dfs.sheet_protection import FULLY_PROTECTED_TABS, protect_workbook
from dfs.weekly_reset import LINEUPS_NAME_BLOCKS


class FakeProtectionClient:
    def __init__(self, *, missing: set[str] | None = None):
        self._missing = missing or set()
        self.clear_calls: list[str] = []
        self.protect_calls: list[tuple[str, dict]] = []

    def tab_exists(self, tab_name: str) -> bool:
        return tab_name not in self._missing

    def clear_protected_ranges(self, tab_name: str) -> None:
        self.clear_calls.append(tab_name)

    def protect_sheet(
        self, tab_name: str, *, warning_only: bool = True, unprotected_ranges=None, description=None
    ):
        kwargs = {"unprotected_ranges": unprotected_ranges, "warning_only": warning_only}
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


def test_exposure_leaves_target_column_unprotected():
    client = FakeProtectionClient()
    protect_workbook(client, lineups_tab="Lineups")

    _tab, kwargs = next(c for c in client.protect_calls if c[0] == "Exposure")
    assert kwargs["unprotected_ranges"] == ["F:F"]


def test_lineups_leaves_deck_controls_and_every_block_name_column_unprotected():
    client = FakeProtectionClient()
    protect_workbook(client, lineups_tab="Lineups")

    _tab, kwargs = next(c for c in client.protect_calls if c[0] == "Lineups")
    unprotected = kwargs["unprotected_ranges"]
    assert "B1" in unprotected
    assert "D1" in unprotected
    assert "F1" in unprotected
    for start, end in LINEUPS_NAME_BLOCKS:
        assert f"A{start}:A{end}" in unprotected


def test_skips_a_missing_tab_without_erroring():
    client = FakeProtectionClient(missing={"Board"})
    results = protect_workbook(client, lineups_tab="Lineups")
    assert any("Board: not present -- skipped" == r for r in results)
    assert "Board" not in [tab for tab, _ in client.protect_calls]


def test_edgeraw_and_pool_picks_are_never_protected():
    # EdgeRaw's whole point is that Sam types into it (Pool); Pool Picks
    # is nothing but a typed column with lookups alongside it.
    client = FakeProtectionClient()
    protect_workbook(client, lineups_tab="Lineups")
    protected_tabs = {tab for tab, _ in client.protect_calls}
    assert "EdgeRaw" not in protected_tabs
    assert "Pool Picks" not in protected_tabs
