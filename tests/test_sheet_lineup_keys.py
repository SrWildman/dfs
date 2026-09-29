from dfs.sheet_lineup_keys import (
    LINEUP_KEY_HEADER,
    lineup_key_formula,
    lineup_key_letter,
    write_lineup_keys,
)
from dfs.sheet_lineup_metrics import min_unique_formula
from dfs.sheet_names import ALIAS_TAB
from dfs.sheet_style import _slot_check_formula
from dfs.weekly_reset import LINEUPS_NAME_BLOCKS


class FakeClient:
    def __init__(self, header: list[str]):
        self.header = header
        self.updates: list[tuple[str, str, list[list[str]]]] = []

    def tab_exists(self, tab):
        return True

    def read_range(self, tab, a1):
        return [self.header]

    def update_range(self, tab, a1, rows):
        self.updates.append((tab, a1, rows))


def test_key_formula_resolves_the_typed_name_and_stays_blank_for_an_empty_slot():
    formula = lineup_key_formula(5, "EdgeRaw")
    assert formula.startswith('=IF($A5="","",')
    assert ALIAS_TAB in formula  # resolves through NameAlias / NameKey, not the typed text
    assert formula.rstrip(")").endswith("$A5")  # falls back to what was typed when nothing matches


def test_write_lineup_keys_fills_every_slot_of_every_block_in_the_found_column():
    header = ["Name", "Pos.", LINEUP_KEY_HEADER]
    client = FakeClient(header)
    write_lineup_keys(client, "Lineups", header_row=1, name_blocks=LINEUPS_NAME_BLOCKS, edge_tab="EdgeRaw")
    assert len(client.updates) == len(LINEUPS_NAME_BLOCKS)
    for (start, end), (_, a1, rows) in zip(LINEUPS_NAME_BLOCKS, client.updates, strict=True):
        assert a1 == f"C{start}:C{end}"
        assert len(rows) == end - start + 1
        assert rows[0][0] == lineup_key_formula(start, "EdgeRaw")


def test_write_lineup_keys_skips_cleanly_before_the_column_exists():
    client = FakeClient(["Name", "Pos."])
    result = write_lineup_keys(client, "Lineups", header_row=1, name_blocks=LINEUPS_NAME_BLOCKS, edge_tab="E")
    assert "not found" in result and client.updates == []
    assert lineup_key_letter(client, "Lineups") is None


def test_the_duplicate_flag_compares_the_key_column_but_blankness_uses_the_typed_column():
    formula = _slot_check_formula(2, 10, 4, "K", key_col="Z")
    assert formula.startswith('=IF($A4="","",')  # a blank slot is judged on what was typed
    assert "COUNTIF($Z$2:$Z$10,$Z4)>1" in formula  # duplicates are judged on the resolved name
    assert "COUNTIF($A$2:$A$10" not in formula


def test_the_duplicate_flag_falls_back_to_the_typed_column_by_default():
    assert "COUNTIF($A$2:$A$10,$A4)>1" in _slot_check_formula(2, 10, 4, "K")


def test_min_unique_overlaps_on_the_key_column_but_empty_block_uses_the_typed_column():
    formula = min_unique_formula(2, 10, [(13, 21)], key_col="Z")
    assert "COUNTIF($Z$13:$Z$21,$Z$2:$Z$10)" in formula  # same player, any spelling, overlaps
    assert "COUNTA($A$2:$A$10)=0" in formula  # COUNTA on a formula-blank key column would count them


def test_min_unique_without_a_key_column_is_the_old_typed_comparison():
    formula = min_unique_formula(2, 10, [(13, 21)])
    assert "COUNTIF($A$13:$A$21,$A$2:$A$10)" in formula
