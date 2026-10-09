from dfs.config import EntryTableConfig
from dfs.weekly_reset import (
    DK_UPLOAD_RANGE,
    LINEUPS_NAME_BLOCKS,
    PLAYER_POOL_NAME_BLOCKS,
    clear_previous_week,
    clear_synced_tabs,
)

_HEADER = ["Pool", "Name", "Pos."]
_CONTROL_ROW = ["Both", "Add a player", ""]  # the type dropdown, the label, the (blank) box


class SpySheetsClient:
    """Records clear_ranges / update_range calls instead of touching a real sheet."""

    def __init__(
        self,
        player_pool_name_formula: str = "",
        player_pool_header: list[str] | None = None,
        control_row: list[str] | None = None,
    ):
        self.calls: list[tuple[str, list[str]]] = []
        self.updates: list[tuple[str, str, list[list]]] = []
        self.formula_reads: list[str] = []
        self._name_formula = player_pool_name_formula
        self._header = player_pool_header or _HEADER
        self._control_row = control_row if control_row is not None else _CONTROL_ROW

    def clear_ranges(self, tab_name, a1_ranges):
        self.calls.append((tab_name, list(a1_ranges)))

    def update_range(self, tab_name, a1_range, rows):
        self.updates.append((tab_name, a1_range, rows))

    def read_formula(self, tab_name, a1_range):
        self.formula_reads.append(a1_range)
        return [[self._name_formula]]

    def read_range(self, tab_name, a1_range):
        if a1_range == "A1:1":
            return [self._control_row]
        return [self._header]

    def tab_exists(self, tab_name):
        return True


def test_clear_previous_week_targets_each_configured_tab():
    client = SpySheetsClient()
    clear_previous_week(
        client,
        lineups_tab="Lineups",
        player_pool_tab="Player Pool",
        dk_upload_tab="DK Upload",
    )
    tabs_touched = [tab for tab, _ in client.calls]
    # Player Pool appears twice: the add-a-player box (always cleared, a plain typed value) and the Name
    # column (only when it isn't formula-driven, see the "skips" test).
    assert tabs_touched == ["Lineups", "Player Pool", "Player Pool", "DK Upload"]


def test_clear_previous_week_clears_the_add_a_player_box_and_resets_its_type_to_both():
    # The box sits right of the label and the type dropdown left of it, found from the label's own place in
    # row 1 (a column move shifts them), never hardcoded.
    client = SpySheetsClient(control_row=["GPP", "Add a player", "Kupp"])
    clear_previous_week(client, "Lineups", "Player Pool", "DK Upload")
    assert ("Player Pool", ["C1"]) in client.calls
    assert ("Player Pool", "A1", [["Both"]]) in client.updates


def test_clear_previous_week_leaves_the_control_row_alone_when_it_is_not_there():
    client = SpySheetsClient(control_row=[])
    clear_previous_week(client, "Lineups", "Player Pool", "DK Upload")
    assert client.updates == []
    assert not any(ranges == ["C1"] or ranges == ["B1"] for _, ranges in client.calls)


def test_clear_previous_week_only_clears_the_name_columns():
    client = SpySheetsClient()
    clear_previous_week(client, "Lineups", "Player Pool", "DK Upload")
    calls = {tab: ranges for tab, ranges in client.calls if ranges != ["C1"]}

    assert calls["Lineups"] == [f"A{s}:A{e}" for s, e in LINEUPS_NAME_BLOCKS]
    # Player Pool's Name column is B now (Pool sits left of it), found from the header
    assert calls["Player Pool"] == [f"B{s}:B{e}" for s, e in PLAYER_POOL_NAME_BLOCKS]
    assert client.formula_reads == [f"B{PLAYER_POOL_NAME_BLOCKS[0][0]}"]


def test_clear_previous_week_clears_full_grid_for_dk_upload():
    client = SpySheetsClient()
    clear_previous_week(client, "Lineups", "Player Pool", "DK Upload")
    calls = dict(client.calls)

    assert calls["DK Upload"] == [DK_UPLOAD_RANGE]


def test_clear_previous_week_skips_player_pool_when_name_column_is_a_formula():
    # Task K 4.3: once Player Pool's Name column is SORT/FILTER-driven off
    # EdgeRaw, clearing it on `dfs week new` would destroy the feature.
    client = SpySheetsClient(player_pool_name_formula='=IFERROR(ARRAY_CONSTRAIN(SORT(FILTER(...)),"")')
    clear_previous_week(client, "Lineups", "Player Pool", "DK Upload")
    tabs_touched = [tab for tab, _ in client.calls]

    # The Name column is skipped (still formula-driven), but the add-a-player box (a plain typed value) is
    # cleared regardless.
    assert tabs_touched == ["Lineups", "Player Pool", "DK Upload"]
    assert client.calls[1] == ("Player Pool", ["C1"])


def test_clear_previous_week_still_clears_player_pool_when_it_holds_typed_values():
    client = SpySheetsClient(player_pool_name_formula="Patrick Mahomes")
    clear_previous_week(client, "Lineups", "Player Pool", "DK Upload")
    calls = dict(client.calls)

    assert calls["Player Pool"] == [f"B{s}:B{e}" for s, e in PLAYER_POOL_NAME_BLOCKS]


def test_clear_previous_week_skips_bankroll_when_not_configured():
    client = SpySheetsClient()
    clear_previous_week(client, "Lineups", "Player Pool", "DK Upload")
    tabs_touched = [tab for tab, _ in client.calls]
    assert "Bankroll" not in tabs_touched


def test_clear_previous_week_clears_bankroll_typed_columns_only():
    cash = EntryTableConfig(header_row=16, first_row=17, last_row=59, entry_key_column="L")
    gpp = EntryTableConfig(header_row=63, first_row=64, last_row=149, entry_key_column="L")
    client = SpySheetsClient()

    summary = clear_previous_week(
        client,
        "Lineups",
        "Player Pool",
        "DK Upload",
        bankroll_tab="Bankroll",
        bankroll_cash=cash,
        bankroll_gpp=gpp,
    )

    bankroll_calls = [ranges for tab, ranges in client.calls if tab == "Bankroll"]
    assert bankroll_calls == [
        ["A17:H59", "L17:L59"],
        ["A64:H149", "L64:L149"],
    ]
    # Never the formula columns (I "% Paid", J "Place %") or the
    # Starting/Ending balance cells `BANKROLL_CARRYOVER_CELLS` already
    # carried forward (rows 1-2, well outside 17-59/64-149).
    for ranges in bankroll_calls:
        for r in ranges:
            assert not r.startswith("I") and not r.startswith("J")
    assert any("cash" in line and "17-59" in line for line in summary)
    assert any("GPP" in line and "64-149" in line for line in summary)


def test_clear_previous_week_clears_bets_typed_columns_only():
    # Round 5, item 7: Betting's typed columns are A/B/D/E, NOT contiguous
    # -- C (Odds) and F (Net) are formulas sitting between them, unlike
    # Cash/GPP's single A-H block.
    bets = EntryTableConfig(header_row=16, first_row=17, last_row=56)
    client = SpySheetsClient()

    summary = clear_previous_week(
        client,
        "Lineups",
        "Player Pool",
        "DK Upload",
        bankroll_tab="Bankroll",
        bankroll_bets=bets,
    )

    bankroll_calls = [ranges for tab, ranges in client.calls if tab == "Bankroll"]
    assert bankroll_calls == [["A17:B56", "D17:E56"]]
    for ranges in bankroll_calls:
        for r in ranges:
            assert not r.startswith("C") and not r.startswith("F")
    assert any("betting" in line and "17-56" in line for line in summary)


def test_clear_previous_week_skips_a_bucket_that_is_configured_none():
    cash = EntryTableConfig(header_row=16, first_row=17, last_row=59, entry_key_column="L")
    client = SpySheetsClient()

    clear_previous_week(
        client,
        "Lineups",
        "Player Pool",
        "DK Upload",
        bankroll_tab="Bankroll",
        bankroll_cash=cash,
        bankroll_gpp=None,
    )

    bankroll_calls = [ranges for tab, ranges in client.calls if tab == "Bankroll"]
    assert len(bankroll_calls) == 1


def test_lineups_blocks_skip_the_repeated_sub_header_row():
    # First block has no sub-header (the tab's own header, immediately
    # above it, covers it); every later block must start one row after
    # where it'd naively be measured, so the sub-header row's own "Name"
    # label in column A never gets cleared. First block starts at row 2
    # (row 1 is the header) -- the pool deck that used to sit above the
    # header (pushing it to row 11) was removed entirely; see
    # CONTRIBUTING.md's changelog.
    #
    # `end` is now the last REAL roster row (Fix 2.4 -- it used to also be
    # that block's totals row), so the gap from one block's `end` to the
    # next block's `start` is 5, not 4: totals row, the "avg salary
    # remaining per unfilled slot" row directly below it, one blank
    # spacer, then the next block's repeated sub-header.
    first_start, _ = LINEUPS_NAME_BLOCKS[0]
    assert first_start == 2
    for (_, prev_end), (start, _) in zip(LINEUPS_NAME_BLOCKS, LINEUPS_NAME_BLOCKS[1:], strict=False):
        assert start == prev_end + 5


class SpyTabClient:
    def __init__(self):
        self.write_tab_calls: list[tuple[str, list, bool]] = []

    def write_tab(self, tab_name, rows, *, create_if_missing: bool = True, clear_first: bool = True):
        self.write_tab_calls.append((tab_name, rows, clear_first))
        return len(rows)


def test_clear_synced_tabs_blanks_every_mapped_source_tab():
    client = SpyTabClient()
    mappings = {"projections": "TFFBOptoRaw", "draftkings": "DKSalRaw", "edge": "EdgeRaw"}

    summary = clear_synced_tabs(client, mappings, ["projections", "draftkings", "edge"])

    assert {tab for tab, _rows, _clear in client.write_tab_calls} == {
        "TFFBOptoRaw",
        "DKSalRaw",
        "EdgeRaw",
    }
    assert all(rows == [] for _tab, rows, _clear in client.write_tab_calls)
    assert all(clear_first is True for _tab, _rows, clear_first in client.write_tab_calls)
    assert len(summary) == 3


def test_clear_synced_tabs_skips_a_source_with_no_tab_mapping():
    client = SpyTabClient()
    summary = clear_synced_tabs(client, {"projections": "TFFBOptoRaw"}, ["projections", "unmapped_source"])

    assert [tab for tab, _rows, _clear in client.write_tab_calls] == ["TFFBOptoRaw"]
    assert len(summary) == 1
