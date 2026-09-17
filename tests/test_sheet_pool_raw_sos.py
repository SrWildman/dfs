from dfs.sheet_pool_raw_sos import opp_pos_rank_formula, rewrite_opp_pos_rank


class FakeClient:
    def __init__(self, header: list[str]):
        self.header = header
        self.update_calls: list[tuple[str, str, list[list]]] = []

    def read_range(self, tab_name, a1_range):
        return [self.header]

    def update_range(self, tab_name, a1_range, rows):
        self.update_calls.append((tab_name, a1_range, rows))


def test_opp_pos_rank_formula_keys_on_opponent_not_team():
    formula = opp_pos_rank_formula(5, opp_column="D", position_column="B")
    assert formula == "=VLOOKUP($D5,SoSComb!$B:$G,HLOOKUP($B5,SoSComb!$C$1:$G$2,2,false),false)"
    assert "$C5" not in formula  # the bug this replaces: Team, not Opp.


def test_rewrite_opp_pos_rank_writes_every_row_keyed_by_current_header_positions():
    # Opp. and Pos. deliberately NOT at their usual A/B slots, to prove
    # this is found by name, not assumed.
    client = FakeClient(["Name", "Salary", "Pos.", "Opp.", "OppPosRank"])

    result = rewrite_opp_pos_rank(client, "PlayerPoolRaw", last_row=4)

    assert len(client.update_calls) == 1
    tab, a1_range, rows = client.update_calls[0]
    assert tab == "PlayerPoolRaw"
    assert a1_range == "E2:E4"
    assert rows == [
        ["=VLOOKUP($D2,SoSComb!$B:$G,HLOOKUP($C2,SoSComb!$C$1:$G$2,2,false),false)"],
        ["=VLOOKUP($D3,SoSComb!$B:$G,HLOOKUP($C3,SoSComb!$C$1:$G$2,2,false),false)"],
        ["=VLOOKUP($D4,SoSComb!$B:$G,HLOOKUP($C4,SoSComb!$C$1:$G$2,2,false),false)"],
    ]
    assert "rewrote 3" in result and "not Team" in result


def test_rewrite_opp_pos_rank_is_a_noop_when_column_absent():
    client = FakeClient(["Name", "Pos.", "Opp."])

    result = rewrite_opp_pos_rank(client, "PlayerPoolRaw", last_row=10)

    assert client.update_calls == []
    assert "no 'OppPosRank' column" in result


def test_rewrite_opp_pos_rank_skips_when_opp_or_pos_missing():
    client = FakeClient(["Name", "OppPosRank"])  # no Opp./Pos. to build the formula from

    result = rewrite_opp_pos_rank(client, "PlayerPoolRaw", last_row=10)

    assert client.update_calls == []
    assert "missing" in result
