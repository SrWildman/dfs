import re

from dfs.sheet_lineup_tints import (
    BRING_BACK_TINT,
    OTHER_TINT,
    STACK_TINT,
    apply_lineup_tints,
    tint_formulas,
)

_HEADER = ["Name", "Pos.", "Team", "Opp.", "DK Sal", "GameID"]
_BLOCKS = [(2, 10), (15, 23)]


class SpyClient:
    def __init__(self, header):
        self._header = header
        self.rules: list[dict] = []

    def tab_exists(self, tab):
        return True

    def read_range(self, tab, rng):
        return [self._header]

    def add_boolean_rules(self, tab, specs):
        self.rules.extend(specs)


def _f(start=2, end=10):
    return tint_formulas(start, end, name_col="A", pos_col="B", team_col="C", opp_col="D", gameid_col="F")


def test_three_formulas_are_custom_formulas_starting_with_equals():
    assert all(f.startswith("=AND(") for f in _f().values())


def test_every_formula_skips_dsts_and_blank_slots():
    for f in _f().values():
        assert '$B2<>"DST"' in f and '$A2<>""' in f


def test_stack_and_bring_back_key_off_the_qb_row_within_the_block():
    f = _f(15, 23)
    assert 'MATCH("QB",$B$15:$B$23,0)' in f["stack"]
    assert "INDEX($C$15:$C$23" in f["stack"] and "$C15=" in f["stack"]
    assert "INDEX($D$15:$D$23" in f["bring_back"]  # the QB's OPPONENT


def test_other_game_rule_excludes_the_qbs_game_and_needs_two_non_dst_players():
    f = _f()["other"]
    assert "$F2<>IFERROR(INDEX($F$2:$F$10" in f  # not the QB's game
    assert 'COUNTIFS($F$2:$F$10,$F2,$B$2:$B$10,"<>DST",$A$2:$A$10,"<>")>=2' in f


def test_no_formula_reaches_outside_its_own_block():
    for start, end in _BLOCKS:
        rows = {
            int(n)
            for f in tint_formulas(
                start, end, name_col="A", pos_col="B", team_col="C", opp_col="D", gameid_col="F"
            ).values()
            for n in re.findall(r"\$[A-Z]+\$?(\d+)", f)
        }
        assert rows <= set(range(start, end + 1))


def test_apply_adds_three_rules_per_block_over_the_identity_cells_only():
    client = SpyClient(_HEADER)
    result = apply_lineup_tints(client, "Lineups", header_row=1, name_blocks=_BLOCKS)
    assert len(client.rules) == 3 * len(_BLOCKS)
    assert {r["a1_range"] for r in client.rules} == {"A2:C10", "A15:C23"}  # Name..Team, no numeric columns
    assert all(r["condition_type"] == "CUSTOM_FORMULA" for r in client.rules)
    assert {tuple(r["fmt"]["backgroundColor"].items()) for r in client.rules} == {
        tuple(t.items()) for t in (STACK_TINT, BRING_BACK_TINT, OTHER_TINT)
    }
    assert "6 rule(s)" in result


def test_apply_skips_cleanly_when_a_column_is_missing():
    client = SpyClient([h for h in _HEADER if h != "GameID"])
    result = apply_lineup_tints(client, "Lineups", header_row=1, name_blocks=_BLOCKS)
    assert "GameID" in result and client.rules == []
