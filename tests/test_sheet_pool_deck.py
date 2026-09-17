from dfs.sheet_pool_deck import DECK_ROWS, POOL_SORT_TAB, remove_pool_deck


class FakeClient:
    def __init__(self, *, row1: str = "", pool_sort_exists: bool = True):
        self._row1 = row1
        self._pool_sort_exists = pool_sort_exists
        self.delete_rows_calls: list[tuple[str, int, int]] = []
        self.delete_tab_calls: list[str] = []

    def read_range(self, tab_name: str, a1_range: str):
        assert a1_range == "A1"
        return [[self._row1]] if self._row1 else []

    def delete_rows(self, tab_name: str, *, at_row: int, count: int) -> None:
        self.delete_rows_calls.append((tab_name, at_row, count))

    def tab_exists(self, tab_name: str) -> bool:
        return self._pool_sort_exists

    def delete_tab(self, tab_name: str) -> None:
        self.delete_tab_calls.append(tab_name)


def test_remove_pool_deck_deletes_the_deck_rows_and_pool_sort_tab():
    client = FakeClient(row1="Position")  # deck's own B1-dropdown control row, not "Name"

    result = remove_pool_deck(client, "Lineups")

    assert client.delete_rows_calls == [("Lineups", 1, DECK_ROWS)]
    assert client.delete_tab_calls == [POOL_SORT_TAB]
    assert "pool deck removed" in result
    assert "PoolSort" in result


def test_remove_pool_deck_is_a_noop_when_header_already_at_row_one():
    client = FakeClient(row1="Name")

    result = remove_pool_deck(client, "Lineups")

    assert client.delete_rows_calls == []
    assert client.delete_tab_calls == []
    assert "no pool deck present" in result


def test_remove_pool_deck_skips_pool_sort_deletion_when_already_gone():
    client = FakeClient(row1="Position", pool_sort_exists=False)

    result = remove_pool_deck(client, "Lineups")

    assert client.delete_rows_calls == [("Lineups", 1, DECK_ROWS)]
    assert client.delete_tab_calls == []
    assert "PoolSort" not in result
