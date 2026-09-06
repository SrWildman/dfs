import pandas as pd

from dfs.derived import EDGE_COLUMNS
from dfs.sources.edge import POOL_COLUMN, POOL_HEADER, EdgeSource


class SpySheetsClient:
    """Records calls instead of touching a real sheet -- same convention as
    test_sheet_links.py's SpySheetsClient, scoped to exactly what
    EdgeSource.pre_upload/post_upload use."""

    def __init__(
        self,
        *,
        exists: bool = True,
        id_column: list[str] | None = None,
        pool_column: list[str] | None = None,
    ):
        self._exists = exists
        self._id_column = id_column or []
        self._pool_column = pool_column or []
        self.update_calls: list[tuple[str, str, list[list]]] = []
        self.checkbox_calls: list[tuple[str, str]] = []

    def tab_exists(self, tab_name: str) -> bool:
        return self._exists

    def read_range(self, tab_name: str, a1_range: str):
        col = a1_range[0]
        values = self._id_column if col == "A" else self._pool_column
        return [[v] if v else [] for v in values]

    def update_range(self, tab_name: str, a1_range: str, rows: list[list]) -> None:
        self.update_calls.append((tab_name, a1_range, rows))

    def set_checkbox_validation(self, tab_name: str, a1_range: str) -> None:
        self.checkbox_calls.append((tab_name, a1_range))


def _df(n: int) -> pd.DataFrame:
    return pd.DataFrame({c: [""] * n for c in EDGE_COLUMNS})


def test_pool_column_is_first_free_column_after_edge_columns():
    # EDGE_COLUMNS currently ends at V (index len-1 = 21); Pool must land
    # one past that, at W -- and must track EDGE_COLUMNS automatically
    # rather than being hardcoded, per the handoff's explicit warning that
    # this might have changed.
    assert POOL_COLUMN == "W"


def test_to_sheet_rows_only_labels_the_header_not_data_rows():
    source = EdgeSource()
    df = _df(2)
    rows = source.to_sheet_rows(df)
    assert rows[0][-1] == POOL_HEADER
    assert len(rows[0]) == len(EDGE_COLUMNS) + 1
    # Data rows are untouched -- Pool values are restored separately by
    # post_upload, never written here.
    assert len(rows[1]) == len(EDGE_COLUMNS)
    assert len(rows[2]) == len(EDGE_COLUMNS)


def test_pre_upload_returns_empty_when_tab_does_not_exist_yet():
    source = EdgeSource()
    client = SpySheetsClient(exists=False)
    assert source.pre_upload(client, "EdgeRaw") == {}


def test_pre_upload_keys_ticked_rows_by_id_and_ignores_untucked():
    source = EdgeSource()
    client = SpySheetsClient(
        id_column=["111", "222", "333"],
        pool_column=["TRUE", "", "true"],  # case-insensitive
    )
    assert source.pre_upload(client, "EdgeRaw") == {"111": True, "333": True}


def test_pre_upload_ignores_blank_id_rows():
    source = EdgeSource()
    client = SpySheetsClient(id_column=["", "222"], pool_column=["TRUE", "TRUE"])
    assert source.pre_upload(client, "EdgeRaw") == {"222": True}


def test_post_upload_restores_ticks_by_id_across_a_row_order_change():
    # The whole point of keying by Id: EdgeRaw is sorted by Leverage, so a
    # player's row position can (and does) change between syncs. A tick
    # preserved for "222" must land on whichever row "222" ends up at,
    # not the row it used to be at.
    source = EdgeSource()
    preserved = {"111": True, "333": True}
    df = _df(3)
    client = SpySheetsClient(id_column=["333", "444", "111"])  # reordered, "222" dropped
    source.post_upload(client, "EdgeRaw", df, preserved)

    assert client.checkbox_calls == [("EdgeRaw", f"{POOL_COLUMN}2:{POOL_COLUMN}4")]
    [(_tab, a1_range, rows)] = client.update_calls
    assert a1_range == f"{POOL_COLUMN}2:{POOL_COLUMN}4"
    assert rows == [[True], [""], [True]]


def test_post_upload_with_nothing_preserved_still_sets_validation_but_skips_restore():
    source = EdgeSource()
    df = _df(2)
    client = SpySheetsClient(id_column=["111", "222"])
    source.post_upload(client, "EdgeRaw", df, {})

    assert client.checkbox_calls == [("EdgeRaw", f"{POOL_COLUMN}2:{POOL_COLUMN}3")]
    assert client.update_calls == []


def test_post_upload_with_no_rows_skips_validation_entirely():
    source = EdgeSource()
    df = _df(0)
    client = SpySheetsClient()
    source.post_upload(client, "EdgeRaw", df, {})

    assert client.checkbox_calls == []
    assert client.update_calls == []


def test_tick_survives_a_full_simulated_sync_round_trip():
    """End-to-end: pre_upload reads the old Pool column (as it would right
    before write_tab's ws.clear() wipes it), the tab gets rewritten with a
    different row order, then post_upload restores ticks by Id -- exactly
    the sequence run_sync drives in production."""
    source = EdgeSource()
    old_client = SpySheetsClient(id_column=["111", "222"], pool_column=["TRUE", ""])
    preserved = source.pre_upload(old_client, "EdgeRaw")

    new_df = _df(2)
    new_client = SpySheetsClient(id_column=["222", "111"])  # order flipped by the new Leverage sort
    source.post_upload(new_client, "EdgeRaw", new_df, preserved)

    [(_tab, _range, rows)] = new_client.update_calls
    assert rows == [[""], [True]]  # "222" stays unticked, "111" keeps its tick at its new row
