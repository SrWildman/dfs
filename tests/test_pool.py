from dfs.pool import EdgePlayer, clear_all, find_matches, read_players, set_pool
from dfs.sources.edge import POOL_COLUMN


class FakePoolClient:
    def __init__(self, rows: list[list]):
        self._rows = rows
        self.update_calls: list[tuple[str, list[list]]] = []

    def read_range(self, tab_name: str, a1_range: str):
        return self._rows

    def update_range(self, tab_name: str, a1_range: str, rows: list[list]) -> None:
        self.update_calls.append((a1_range, rows))


# Columns A..G: Pool, Id, Name, Position, Team, Opp, Salary. Pool is a
# blank/Cash/GPP/Both dropdown (Fix 2.11), not a TRUE/FALSE checkbox.
_ROWS = [
    ["Both", "1", "Chase Brown", "RB", "CIN", "BAL", "6800"],
    ["", "2", "Ja'Marr Chase", "WR", "CIN", "BAL", "9200"],
    ["", "3", "Chase Edmonds", "RB", "TB", "NO", "4200"],
    ["", "4", "Justin Jefferson", "WR", "MIN", "GB", "9000"],
]


def test_read_players_skips_blank_name_rows():
    client = FakePoolClient([*_ROWS, ["", "", "", "", "", "", ""]])
    players = read_players(client, "EdgeRaw")
    assert len(players) == 4
    assert players[0].row == 2  # first data row is sheet row 2


def test_read_players_reads_pool_and_salary_correctly():
    client = FakePoolClient(_ROWS)
    players = read_players(client, "EdgeRaw")
    chase_brown = next(p for p in players if p.name == "Chase Brown")
    assert chase_brown.pool_value == "Both"
    assert chase_brown.pooled is True
    assert chase_brown.salary == "6800"
    jefferson = next(p for p in players if p.name == "Justin Jefferson")
    assert jefferson.pool_value == ""
    assert jefferson.pooled is False


def test_pooled_is_true_for_any_non_blank_pool_value():
    # Cash-only or GPP-only still counts as "in the pool" -- only a blank
    # cell means "not pooled" (Fix 2.11).
    for value in ("Cash", "GPP", "Both"):
        player = EdgePlayer(row=2, name="P", position="RB", salary="6800", pool_value=value)
        assert player.pooled is True
    assert EdgePlayer(row=2, name="P", position="RB", salary="6800", pool_value="").pooled is False


def test_find_matches_exact_wins_over_substring():
    players = [
        EdgePlayer(row=2, name="Chase Brown", position="RB", salary="6800"),
        EdgePlayer(row=3, name="Ja'Marr Chase", position="WR", salary="9200"),
        EdgePlayer(row=4, name="Chase Edmonds", position="RB", salary="4200"),
    ]
    # "Chase Brown" substring-matches all three but exact-matches only one.
    matches = find_matches(players, "Chase Brown")
    assert len(matches) == 1
    assert matches[0].name == "Chase Brown"


def test_find_matches_is_case_insensitive():
    players = [EdgePlayer(row=2, name="Chase Brown", position="RB", salary="6800")]
    assert find_matches(players, "chase brown") == players


def test_find_matches_returns_every_substring_candidate_when_ambiguous():
    players = [
        EdgePlayer(row=2, name="Chase Brown", position="RB", salary="6800"),
        EdgePlayer(row=3, name="Chase Edmonds", position="RB", salary="4200"),
        EdgePlayer(row=4, name="Justin Jefferson", position="WR", salary="9000"),
    ]
    matches = find_matches(players, "chase")
    assert {p.name for p in matches} == {"Chase Brown", "Chase Edmonds"}


def test_find_matches_empty_query_returns_nothing():
    players = [EdgePlayer(row=2, name="Chase Brown", position="RB", salary="6800")]
    assert find_matches(players, "   ") == []


def test_set_pool_writes_to_the_players_own_row():
    client = FakePoolClient(_ROWS)
    player = EdgePlayer(row=5, name="Someone", position="QB", salary="7000")
    set_pool(client, "EdgeRaw", player, "Both")
    assert client.update_calls == [(f"{POOL_COLUMN}5", [["Both"]])]


def test_set_pool_can_write_a_single_contest_type():
    client = FakePoolClient(_ROWS)
    player = EdgePlayer(row=5, name="Someone", position="QB", salary="7000")
    set_pool(client, "EdgeRaw", player, "Cash")
    assert client.update_calls == [(f"{POOL_COLUMN}5", [["Cash"]])]


def test_set_pool_blank_removes_from_the_pool():
    client = FakePoolClient(_ROWS)
    player = EdgePlayer(row=5, name="Someone", position="QB", salary="7000", pool_value="Both")
    set_pool(client, "EdgeRaw", player, "")
    assert client.update_calls == [(f"{POOL_COLUMN}5", [[""]])]


def test_clear_all_only_writes_currently_pooled_rows():
    client = FakePoolClient(_ROWS)
    players = read_players(client, "EdgeRaw")
    count = clear_all(client, "EdgeRaw", players)
    assert count == 1  # only Chase Brown was pooled
    assert client.update_calls == [(f"{POOL_COLUMN}2", [[""]])]


def test_clear_all_is_a_no_op_when_nothing_is_pooled():
    client = FakePoolClient([r[:0] + ["", *r[1:]] for r in _ROWS])  # blank out every Pool cell
    players = read_players(client, "EdgeRaw")
    count = clear_all(client, "EdgeRaw", players)
    assert count == 0
    assert client.update_calls == []
