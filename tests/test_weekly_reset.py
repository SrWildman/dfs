from dfs.config import EntryTableConfig
from dfs.weekly_reset import (
    DK_UPLOAD_RANGE,
    ENTRIES_RAW_RANGE,
    ENTRIES_RAW_TAB,
    LINEUPS_NAME_BLOCKS,
    PLAYER_POOL_NAME_BLOCKS,
    SCRATCH_RANGE,
    clear_previous_week,
    clear_synced_tabs,
)


class SpySheetsClient:
    """Records clear_ranges calls instead of touching a real sheet."""

    def __init__(
        self,
        player_pool_a1_formula: str = "",
        entries_raw_exists: bool = True,
        player_pool_header: list[str] | None = None,
    ):
        self.calls: list[tuple[str, list[str]]] = []
        self._player_pool_a1_formula = player_pool_a1_formula
        self._entries_raw_exists = entries_raw_exists
        # "Added" at column Z by default -- any fixed position works,
        # since clear_previous_week finds it by header name, not letter.
        self._player_pool_header = player_pool_header or ["Name"] + [""] * 24 + ["Added"]

    def clear_ranges(self, tab_name, a1_ranges):
        self.calls.append((tab_name, list(a1_ranges)))

    def read_formula(self, tab_name, a1_range):
        return [[self._player_pool_a1_formula]]

    def read_range(self, tab_name, a1_range):
        return [self._player_pool_header]

    def tab_exists(self, tab_name):
        return tab_name != ENTRIES_RAW_TAB or self._entries_raw_exists


def test_clear_previous_week_targets_each_configured_tab():
    client = SpySheetsClient()
    clear_previous_week(
        client,
        lineups_tab="Lineups",
        player_pool_tab="Player Pool",
        scratch_tab="Scratch",
        dk_upload_tab="DK Upload",
    )
    tabs_touched = [tab for tab, _ in client.calls]
    # Player Pool appears three times: the add-a-player control cell
    # (always cleared, a plain typed value -- A3), the accumulated
    # add-a-player list (A6), and the Name column (only when it isn't
    # formula-driven, see the "skips" test).
    assert tabs_touched == [
        "Lineups",
        "Player Pool",
        "Player Pool",
        "Player Pool",
        "Scratch",
        "DK Upload",
        "EntriesRaw",
    ]


def test_clear_previous_week_clears_entries_raw_when_present():
    client = SpySheetsClient()
    clear_previous_week(client, "Lineups", "Player Pool", "Scratch", "DK Upload")
    calls = dict(client.calls)
    assert calls[ENTRIES_RAW_TAB] == [ENTRIES_RAW_RANGE]


def test_clear_previous_week_skips_entries_raw_when_tab_absent():
    client = SpySheetsClient(entries_raw_exists=False)
    clear_previous_week(client, "Lineups", "Player Pool", "Scratch", "DK Upload")
    tabs_touched = [tab for tab, _ in client.calls]
    assert ENTRIES_RAW_TAB not in tabs_touched


def test_clear_previous_week_clears_the_accumulated_add_a_player_list():
    # A6: "Added" found by header name (column Z in the fake's header --
    # see SpySheetsClient), never hardcoded.
    client = SpySheetsClient()
    clear_previous_week(client, "Lineups", "Player Pool", "Scratch", "DK Upload")
    calls = [ranges for tab, ranges in client.calls if tab == "Player Pool"]
    assert ["Z3:Z52"] in calls


def test_clear_previous_week_skips_the_added_list_when_column_is_absent():
    client = SpySheetsClient(player_pool_header=["Name"])
    clear_previous_week(client, "Lineups", "Player Pool", "Scratch", "DK Upload")
    calls = [ranges for tab, ranges in client.calls if tab == "Player Pool"]
    assert not any(r[0].startswith("Z") for r in calls)


def test_clear_previous_week_only_clears_column_a_for_name_columns():
    client = SpySheetsClient()
    clear_previous_week(client, "Lineups", "Player Pool", "Scratch", "DK Upload")
    calls = dict(client.calls)

    assert calls["Lineups"] == [f"A{s}:A{e}" for s, e in LINEUPS_NAME_BLOCKS]
    assert calls["Player Pool"] == [f"A{s}:A{e}" for s, e in PLAYER_POOL_NAME_BLOCKS]
    assert all(r.startswith("A") and ":A" in r for r in calls["Lineups"])
    assert all(r.startswith("A") and ":A" in r for r in calls["Player Pool"])


def test_clear_previous_week_clears_full_grid_for_scratch_and_dk_upload():
    client = SpySheetsClient()
    clear_previous_week(client, "Lineups", "Player Pool", "Scratch", "DK Upload")
    calls = dict(client.calls)

    assert calls["Scratch"] == [SCRATCH_RANGE]
    assert calls["DK Upload"] == [DK_UPLOAD_RANGE]


def test_clear_previous_week_skips_player_pool_when_name_column_is_a_formula():
    # Task K 4.3: once Player Pool's Name column is SORT/FILTER-driven off
    # EdgeRaw, clearing it on `dfs week new` would destroy the feature.
    client = SpySheetsClient(player_pool_a1_formula='=IFERROR(ARRAY_CONSTRAIN(SORT(FILTER(...)),"")')
    clear_previous_week(client, "Lineups", "Player Pool", "Scratch", "DK Upload")
    tabs_touched = [tab for tab, _ in client.calls]

    # The Name column is skipped (still formula-driven), but the
    # add-a-player control cell (a plain typed value, A3) and the
    # accumulated add-a-player list (A6) are cleared regardless -- neither
    # is part of the formula-driven-ness check.
    assert tabs_touched == ["Lineups", "Player Pool", "Player Pool", "Scratch", "DK Upload", "EntriesRaw"]
    assert client.calls[1] == ("Player Pool", ["B1"])


def test_clear_previous_week_still_clears_player_pool_when_it_holds_typed_values():
    client = SpySheetsClient(player_pool_a1_formula="Patrick Mahomes")
    clear_previous_week(client, "Lineups", "Player Pool", "Scratch", "DK Upload")
    calls = dict(client.calls)

    assert calls["Player Pool"] == [f"A{s}:A{e}" for s, e in PLAYER_POOL_NAME_BLOCKS]


def test_clear_previous_week_skips_bankroll_when_not_configured():
    client = SpySheetsClient()
    clear_previous_week(client, "Lineups", "Player Pool", "Scratch", "DK Upload")
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
        "Scratch",
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


def test_clear_previous_week_skips_a_bucket_that_is_configured_none():
    cash = EntryTableConfig(header_row=16, first_row=17, last_row=59, entry_key_column="L")
    client = SpySheetsClient()

    clear_previous_week(
        client,
        "Lineups",
        "Player Pool",
        "Scratch",
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
