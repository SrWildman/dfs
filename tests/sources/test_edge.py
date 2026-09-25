import pandas as pd

from dfs.derived import EDGE_COLUMNS
from dfs.player_join import JoinResult
from dfs.sources.edge import (
    _FILTER_RANGE,
    POOL_COLUMN,
    POOL_HEADER,
    POOL_TYPE_OPTIONS,
    EdgeSource,
    _canonical_id,
    _report_source_joins,
)


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
        header: list[str] | None = None,
    ):
        self._exists = exists
        self._id_column = id_column or []
        self._pool_column = pool_column or []
        # Minimal default: Pool at A, Id at B -- matches every existing
        # test's implicit assumption. Override to prove `pre_upload` finds
        # Id by reading THIS header, not by trusting EDGE_COLUMNS' target
        # position (see test_pre_upload_finds_id_at_its_current_sheet_
        # position_not_the_target_one below).
        self._header = header or ["Pool", "Id"]
        self.update_calls: list[tuple[str, str, list[list]]] = []
        self.dropdown_calls: list[tuple[str, str, list[str]]] = []
        self.basic_filter_calls: list[tuple[str, str]] = []
        self.call_order: list[str] = []

    def tab_exists(self, tab_name: str) -> bool:
        return self._exists

    def _column_values(self, a1_range: str) -> list[list]:
        # Column letters can be more than one character (e.g. "AA") once
        # EDGE_COLUMNS grows past 26 entries -- take the leading letters of
        # the range's first cell only, not just its first character (that
        # misreads "AA" as "A") and not every letter in the whole range
        # string (that picks up the "AA" a second time from the far end).
        first_cell = a1_range.split(":")[0]
        col = "".join(ch for ch in first_cell if ch.isalpha())
        # Pool lives at column A; any other column letter is treated as
        # wherever the test says Id currently sits.
        values = self._pool_column if col == POOL_COLUMN else self._id_column
        return [[v] if v else [] for v in values]

    def read_range(self, tab_name: str, a1_range: str):
        if a1_range == "A1:1":
            return [self._header]
        return self._column_values(a1_range)

    def read_range_unformatted(self, tab_name: str, a1_range: str):
        # Production code only ever calls this for the Id column -- real
        # Sheets would return raw JSON numbers/strings here, not
        # formatted display text; tests that need to exercise a mangled
        # formatted-vs-unformatted mismatch pass id_column values that
        # already look like what UNFORMATTED_VALUE would actually return
        # (an int/float), same as `read_range` would for any other column.
        return self._column_values(a1_range)

    def update_range(self, tab_name: str, a1_range: str, rows: list[list]) -> None:
        self.update_calls.append((tab_name, a1_range, rows))

    def set_dropdown_validation(self, tab_name: str, a1_range: str, options: list[str]) -> None:
        self.dropdown_calls.append((tab_name, a1_range, options))
        self.call_order.append("dropdown")

    def set_basic_filter(self, tab_name: str, a1_range: str) -> None:
        self.basic_filter_calls.append((tab_name, a1_range))
        self.call_order.append("basic_filter")


def _df(n: int) -> pd.DataFrame:
    return pd.DataFrame({c: [""] * n for c in EDGE_COLUMNS})


def test_pool_column_is_the_very_first_column():
    # Pool sits ahead of EDGE_COLUMNS (column A), not appended after it --
    # so it's visually beside Name once Id, immediately after it, is
    # hidden. See derived.EDGE_DATA_OFFSET for what every other module
    # adds to account for this.
    assert POOL_COLUMN == "A"


def test_to_sheet_rows_labels_the_header_and_blanks_every_data_row():
    source = EdgeSource()
    df = _df(2)
    rows = source.to_sheet_rows(df)
    assert rows[0][0] == POOL_HEADER
    assert len(rows[0]) == len(EDGE_COLUMNS) + 1
    # Every data row gets an explicit blank Pool placeholder too -- unlike
    # an *appended* column, a column at the front can't rely on an
    # implicit shorter row, or every subsequent value would silently land
    # one column too far left. Real values are restored separately by
    # post_upload, never written here.
    assert rows[1][0] == ""
    assert rows[2][0] == ""
    assert len(rows[1]) == len(EDGE_COLUMNS) + 1
    assert len(rows[2]) == len(EDGE_COLUMNS) + 1


def test_pre_upload_returns_empty_when_tab_does_not_exist_yet():
    source = EdgeSource()
    client = SpySheetsClient(exists=False)
    assert source.pre_upload(client, "EdgeRaw") == {}


def test_pre_upload_keys_pooled_rows_by_id_and_ignores_blank():
    source = EdgeSource()
    client = SpySheetsClient(
        id_column=["111", "222", "333"],
        pool_column=["Cash", "", "Both"],
    )
    assert source.pre_upload(client, "EdgeRaw") == {"111": "Cash", "333": "Both"}


def test_pre_upload_ignores_blank_id_rows():
    source = EdgeSource()
    client = SpySheetsClient(id_column=["", "222"], pool_column=["Both", "GPP"])
    assert source.pre_upload(client, "EdgeRaw") == {"222": "GPP"}


def test_pre_upload_finds_id_at_its_current_sheet_position_not_the_target_one():
    # The exact live incident this guards against: right before a reorder
    # of EDGE_COLUMNS takes effect, the sheet still has Id at its OLD
    # position while the Python-side EDGE_COLUMNS constant already
    # reflects the NEW one. `pre_upload` runs BEFORE `write_tab` rewrites
    # the tab, so it must read Id from wherever the header ACTUALLY says
    # it is right now -- here, column D, nowhere near Id's usual spot --
    # not from a position derived off EDGE_COLUMNS. Getting this wrong
    # silently wiped every real Pool tick on the live sheet once.
    source = EdgeSource()
    client = SpySheetsClient(
        header=["Pool", "Name", "Team", "Id"],
        id_column=["111", "222"],
        pool_column=["Cash", "GPP"],
    )
    assert source.pre_upload(client, "EdgeRaw") == {"111": "Cash", "222": "GPP"}


def test_pre_upload_returns_empty_when_id_is_not_in_the_header_at_all():
    source = EdgeSource()
    client = SpySheetsClient(header=["Pool", "Name", "Team"], id_column=["111"], pool_column=["Cash"])
    assert source.pre_upload(client, "EdgeRaw") == {}


def test_pre_upload_migrates_a_leftover_true_checkbox_value_to_both():
    # A sheet copied from a template last rebuilt before Fix 2.11 can
    # still carry the old TRUE/FALSE checkbox values on EdgeRaw's Pool
    # column -- confirmed live on the actual template. TRUE (checked)
    # migrates to "Both", the closest equivalent.
    source = EdgeSource()
    client = SpySheetsClient(id_column=["111"], pool_column=["TRUE"])
    assert source.pre_upload(client, "EdgeRaw") == {"111": "Both"}


def test_pre_upload_drops_a_leftover_false_checkbox_value_instead_of_preserving_it():
    # FALSE (unchecked) meant "not pooled" under the old checkbox -- it is
    # NOT one of POOL_TYPE_OPTIONS, so preserving the literal string
    # "FALSE" would silently perpetuate an invalid dropdown value forever.
    # Found live: the template's own EdgeRaw had "FALSE" in every row.
    source = EdgeSource()
    client = SpySheetsClient(id_column=["111", "222"], pool_column=["FALSE", "false"])
    assert source.pre_upload(client, "EdgeRaw") == {}


def test_post_upload_restores_pool_value_by_id_across_a_row_order_change():
    # The whole point of keying by Id: EdgeRaw is sorted by Leverage, so a
    # player's row position can (and does) change between syncs. A value
    # preserved for "222" must land on whichever row "222" ends up at,
    # not the row it used to be at.
    source = EdgeSource()
    preserved = {"111": "Cash", "333": "Both"}
    df = _df(3)
    client = SpySheetsClient(id_column=["333", "444", "111"])  # reordered, "222" dropped
    source.post_upload(client, "EdgeRaw", df, preserved)

    assert client.basic_filter_calls == [("EdgeRaw", _FILTER_RANGE)]
    assert client.dropdown_calls == [("EdgeRaw", f"{POOL_COLUMN}2:{POOL_COLUMN}4", POOL_TYPE_OPTIONS)]
    # The filter reset must land BEFORE the dropdown validation write --
    # found live: a setDataValidation call silently no-ops on most of its
    # range when the tab's basic filter still has an active sort (see
    # post_upload's own docstring and CONTRIBUTING.md's changelog).
    assert client.call_order == ["basic_filter", "dropdown"]
    [(_tab, a1_range, rows)] = client.update_calls
    assert a1_range == f"{POOL_COLUMN}2:{POOL_COLUMN}4"
    assert rows == [["Both"], [""], ["Cash"]]


def test_post_upload_with_nothing_preserved_still_sets_validation_but_skips_restore():
    source = EdgeSource()
    df = _df(2)
    client = SpySheetsClient(id_column=["111", "222"])
    source.post_upload(client, "EdgeRaw", df, {})

    assert client.basic_filter_calls == [("EdgeRaw", _FILTER_RANGE)]
    assert client.dropdown_calls == [("EdgeRaw", f"{POOL_COLUMN}2:{POOL_COLUMN}3", POOL_TYPE_OPTIONS)]
    assert client.update_calls == []


def test_post_upload_with_no_rows_skips_validation_entirely():
    source = EdgeSource()
    df = _df(0)
    client = SpySheetsClient()
    source.post_upload(client, "EdgeRaw", df, {})

    assert client.basic_filter_calls == []
    assert client.dropdown_calls == []
    assert client.update_calls == []


def test_pool_value_survives_a_full_simulated_sync_round_trip():
    """End-to-end: pre_upload reads the old Pool column (as it would right
    before write_tab's ws.clear() wipes it), the tab gets rewritten with a
    different row order, then post_upload restores values by Id -- exactly
    the sequence run_sync drives in production."""
    source = EdgeSource()
    old_client = SpySheetsClient(id_column=["111", "222"], pool_column=["GPP", ""])
    preserved = source.pre_upload(old_client, "EdgeRaw")

    new_df = _df(2)
    new_client = SpySheetsClient(id_column=["222", "111"])  # order flipped by the new Leverage sort
    source.post_upload(new_client, "EdgeRaw", new_df, preserved)

    [(_tab, _range, rows)] = new_client.update_calls
    assert rows == [[""], ["GPP"]]  # "222" stays blank, "111" keeps its value at its new row


def test_canonical_id_strips_a_whole_number_floats_stray_decimal():
    # Google returns a purely-numeric-looking cell as an actual JSON
    # number once written -- a whole-number float round-trips through
    # Python with a stray ".0" that a raw string comparison would choke on.
    assert _canonical_id(44132966.0) == "44132966"


def test_canonical_id_leaves_a_genuinely_fractional_number_alone():
    assert _canonical_id(44132966.5) == "44132966.5"


def test_canonical_id_passes_through_a_plain_string_unchanged():
    assert _canonical_id("111") == "111"


def test_canonical_id_blank_for_none_or_empty():
    assert _canonical_id(None) == ""
    assert _canonical_id("") == ""


def test_pool_value_survives_when_id_is_read_back_as_a_real_number():
    # The actual bug (2026-09-16): read_range's FORMATTED rendering can
    # bake a stale per-column number format into a clean numeric Id (e.g.
    # "+44132966.0" instead of "44132966") after an EdgeRaw column
    # reorder, silently breaking this exact join. pre_upload/post_upload
    # now read Id via read_range_unformatted, which (like the real Sheets
    # API) hands back a raw Python float for a numeric-looking cell, not
    # a pre-formatted string -- this proves the join still matches.
    source = EdgeSource()
    old_client = SpySheetsClient(id_column=[44132966.0, 44133446.0], pool_column=["GPP", "Cash"])
    preserved = source.pre_upload(old_client, "EdgeRaw")
    assert preserved == {"44132966": "GPP", "44133446": "Cash"}

    new_df = _df(2)
    new_client = SpySheetsClient(id_column=[44133446.0, 44132966.0])  # order flipped
    source.post_upload(new_client, "EdgeRaw", new_df, preserved)

    [(_tab, _range, rows)] = new_client.update_calls
    assert rows == [["Cash"], ["GPP"]]


def test_report_source_joins_is_a_noop_with_no_sources(tmp_path, monkeypatch):
    monkeypatch.setattr("dfs.sources.edge.CURRENT_DIR", tmp_path)
    _report_source_joins({})
    assert list(tmp_path.iterdir()) == []


def test_report_source_joins_writes_one_unmatched_file_per_source(tmp_path, monkeypatch):
    monkeypatch.setattr("dfs.sources.edge.CURRENT_DIR", tmp_path)
    joins = {
        "sleeper": JoinResult(
            matched=pd.DataFrame(),
            pool_total=2,
            pool_matched=1,
            unmatched_pool_names=["Missed Guy"],
            by_position={"RB": (1, 2)},
        ),
        "fantasypros": JoinResult(
            matched=pd.DataFrame(),
            pool_total=2,
            pool_matched=2,
            unmatched_pool_names=[],
            by_position={"RB": (2, 2)},
        ),
    }

    _report_source_joins(joins)

    sleeper_csv = pd.read_csv(tmp_path / "unmatched_sleeper.csv")
    assert sleeper_csv["Name"].tolist() == ["Missed Guy"]
    fantasypros_csv = pd.read_csv(tmp_path / "unmatched_fantasypros.csv")
    assert fantasypros_csv.empty
