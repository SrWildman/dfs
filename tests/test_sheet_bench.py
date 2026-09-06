from dfs.sheet_bench import BENCH_ROWS, BENCH_TITLE, add_bench
from dfs.weekly_reset import PLAYER_POOL_NAME_BLOCKS


class FakeBenchClient:
    def __init__(self, *, already_present: bool = False):
        self.already_present = already_present
        self.insert_calls: list[tuple[str, int, int]] = []
        self.update_calls: list[tuple[str, str, list[list]]] = []
        self.freeze_calls: list[tuple] = []

    def read_range(self, tab_name: str, a1_range: str):
        return [[BENCH_TITLE]] if self.already_present else [[]]

    def insert_rows(self, tab_name: str, *, at_row: int, count: int) -> None:
        self.insert_calls.append((tab_name, at_row, count))

    def update_range(self, tab_name: str, a1_range: str, rows: list[list]) -> None:
        self.update_calls.append((tab_name, a1_range, rows))

    def freeze(self, tab_name: str, *, rows=None, cols=None) -> None:
        self.freeze_calls.append((tab_name, rows, cols))


def test_add_bench_inserts_seven_rows_writes_formulas_by_position_and_freezes():
    client = FakeBenchClient()

    result = add_bench(client, lineups_tab="Lineups", pool_tab="Player Pool")

    assert client.insert_calls == [("Lineups", 1, BENCH_ROWS)]

    assert len(client.update_calls) == 1
    tab, a1_range, rows = client.update_calls[0]
    assert tab == "Lineups"
    assert a1_range == f"A1:B{BENCH_ROWS}"
    assert len(rows) == BENCH_ROWS

    assert rows[0] == [BENCH_TITLE, ""]
    # Position labels and source ranges follow PLAYER_POOL_NAME_BLOCKS
    # positionally (QB, RB, WR, TE, DST) -- not a hardcoded range, so a
    # re-measured Player Pool layout moves these formulas with it.
    for (pos_row, pos_label), (start, end) in zip(
        enumerate(["QB", "RB", "WR", "TE", "DST"], start=1), PLAYER_POOL_NAME_BLOCKS, strict=True
    ):
        label, formula = rows[pos_row]
        assert label == pos_label
        source = f"'Player Pool'!$A${start}:$A${end}"
        assert formula == f'=IFERROR(TRANSPOSE(FILTER({source},{source}<>"")),"")'

    assert rows[-1] == ["", ""]  # row 7: blank separator

    assert client.freeze_calls == [("Lineups", BENCH_ROWS, None)]
    assert "inserted" in result


def test_add_bench_skips_without_touching_anything_when_already_present():
    client = FakeBenchClient(already_present=True)

    result = add_bench(client, lineups_tab="Lineups", pool_tab="Player Pool")

    assert client.insert_calls == []
    assert client.update_calls == []
    assert client.freeze_calls == []
    assert "already present" in result
