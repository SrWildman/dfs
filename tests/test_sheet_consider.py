"""The soft-yellow "Consider" highlight and the Board's "Model likes" list (Week 6, slice 3)."""

from dfs import edge_finder_tab as eft
from dfs import sheet_consider as sc
from dfs.sheet_style import _apply_name_flag_style
from dfs.sheets import column_letter


def test_the_favourites_reference_is_derived_from_the_edge_finders_own_constants():
    assert sc.favourites_ref() == f"'Edge Finder'!${eft.FAVOURITE_CASH_COL}:${eft.FAVOURITE_GPP_COL}"
    assert (eft.FAVOURITE_CASH_COL, eft.FAVOURITE_GPP_COL) == ("Q", "R")  # after the hidden group key in P


def test_the_other_tabs_rule_needs_pool_blank_and_the_id_on_a_list_read_through_indirect():
    formula = sc.consider_formula("$A2", "$BF2")
    assert formula == 'AND($A2="",COUNTIF(INDIRECT("\'Edge Finder\'!$Q:$R"),$BF2)>0)'
    assert sc.listed_formula("$BF2") == "COUNTIF(INDIRECT(\"'Edge Finder'!$Q:$R\"),$BF2)>0"


def test_the_edge_finders_own_rule_reads_the_rows_pool_and_do_cells():
    formula = sc.edge_finder_formula("$A9", "$M9")
    assert formula == 'AND($A9="",REGEXMATCH($M9,"^(Cash add|GPP add|GPP leverage)"))'
    assert eft.CONSIDER_VERBS == ("Cash add", "GPP add", "GPP leverage")


class _RuleClient:
    def __init__(self):
        self.rules = []

    def add_boolean_rule(self, tab, rng, *, condition_type, values, fmt):
        self.rules.append((rng, condition_type, values, fmt))


def test_edgeraw_names_get_five_mutually_exclusive_rules_when_the_id_column_is_there():
    header = ["Pool", "Name", "Id", "Flag"]
    client = _RuleClient()
    _apply_name_flag_style(client, "EdgeRaw", header, data_start=2, last_row=50, pool_column="Pool")
    formulas = [values[0] for _rng, _t, values, _f in client.rules]
    assert len(formulas) == 5 and all(rng == "B2:B50" for rng, *_ in client.rules)
    consider = [f for f in formulas if "INDIRECT" in f]
    assert len(consider) == 3  # consider+flagged, consider alone, and "not listed" on the plain flagged rule
    yellow = [f for rng, t, v, f in client.rules if f.get("backgroundColor") == sc.CONSIDER_BG]
    assert len(yellow) == 2  # one with bold (flagged), one without
    assert any(f.get("textFormat") == {"bold": True} for f in yellow)
    assert any("NOT(" in f for f in formulas)  # the plain flagged-and-unpooled rule excludes the listed ones


def test_without_an_id_column_the_name_rules_are_the_old_three():
    client = _RuleClient()
    _apply_name_flag_style(
        client, "EdgeRaw", ["Pool", "Name", "Flag"], data_start=2, last_row=9, pool_column="Pool"
    )
    assert len(client.rules) == 3 and not any("INDIRECT" in r[2][0] for r in client.rules)


def test_the_name_header_note_on_edgeraw_and_the_edge_finder_explains_the_yellow():
    from dfs import sheet_column_notes as notes

    assert "Soft yellow" in notes.edgeraw_notes()["Name"]
    assert "Soft yellow" in notes.edge_finder_tab_notes()["Name"]
    assert "Soft yellow" not in notes.player_tab_notes()["Name"]  # Player Pool and Lineups never show it
    assert "Soft yellow" in notes.BOARD_NOTES["Player"]


def test_the_edge_finder_writer_adds_the_rule_on_the_name_column_over_the_player_rows():
    import sys

    sys.path.insert(0, "tests")
    import test_edge_finder_tab as t
    import test_sheet_edge_finder as w

    client = w.FakeClient()
    w.writer.write_tab(client, t._inputs(t._cash_and_gpp_edge()), edge_tab="EdgeRaw")
    name_rules = [r for r in client.rules if r[1] == "CUSTOM_FORMULA" and r[0].startswith(eft.NAME_COL)]
    assert len(name_rules) == 1
    rng, _kind, values = name_rules[0]
    first = int(rng.split(":")[0][1:])
    assert values[0] == f"={sc.edge_finder_formula(f'${eft.POOL_COL}{first}', f'${eft.DO_COL}{first}')}"
    assert column_letter(1) == "B" and rng.startswith(f"{eft.NAME_COL}{first}:")
