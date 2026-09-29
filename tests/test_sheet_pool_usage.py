import dfs.sheet_pool_usage as sheet_pool_usage
from dfs.sheet_pool_usage import write_pool_usage_columns
from dfs.weekly_reset import LINEUPS_NAME_BLOCKS, PLAYER_POOL_NAME_BLOCKS

_HEADER_ROW = 2
_BASE_HEADER = ["Name", "Pos.", "Team"]


def _col_letters(index: int) -> str:
    letters = ""
    index += 1
    while index > 0:
        index, rem = divmod(index - 1, 26)
        letters = chr(65 + rem) + letters
    return letters


class FakeClient:
    """Header row 2 (mirrors PLAYER_POOL_HEADER_ROW); Used/In may or may
    not already be present, exercising provision_missing_columns' append
    path the same way a real still-A3-only Player Pool would."""

    def __init__(self, header: list[str], lineups_header: list[str] | None = None):
        self.header = header
        self.lineups_header = lineups_header or []
        self.update_calls: list[tuple[str, str, list[list]]] = []

    def tab_exists(self, tab_name: str) -> bool:
        return True

    def read_range(self, tab_name: str, a1_range: str):
        if tab_name == "Lineups":  # the key-column lookup reads Lineups' own header row
            return [self.lineups_header] if self.lineups_header else []
        assert a1_range == f"A{_HEADER_ROW}:{_HEADER_ROW}"
        return [self.header] if self.header else []

    def update_range(self, tab_name: str, a1_range: str, rows: list[list]):
        self.update_calls.append((tab_name, a1_range, rows))
        header_provision_start = f"{_col_letters(len(self.header))}{_HEADER_ROW}:"
        if a1_range.startswith(header_provision_start):
            self.header.extend(rows[0])

    def ensure_column_capacity(self, tab_name: str, min_cols: int) -> None:
        pass


def _find_call(client: FakeClient, a1_range: str):
    return next(c for c in client.update_calls if c[1] == a1_range)


def test_write_pool_usage_columns_provisions_used_and_in_when_missing():
    client = FakeClient(header=list(_BASE_HEADER))

    write_pool_usage_columns(client, "Player Pool", "Lineups", header_row=_HEADER_ROW)

    assert "Used" in client.header
    assert "In" in client.header


def test_write_pool_usage_columns_writes_used_formula_for_every_block_row():
    client = FakeClient(header=[*_BASE_HEADER, "Used", "In"])

    write_pool_usage_columns(client, "Player Pool", "Lineups", header_row=_HEADER_ROW)

    used_col = _col_letters(client.header.index("Used"))
    name_col = _col_letters(client.header.index("Name"))
    for start, end in PLAYER_POOL_NAME_BLOCKS:
        _, _, rows = _find_call(client, f"{used_col}{start}:{used_col}{end}")
        assert rows[0][0] == f'=IF(${name_col}{start}="","",COUNTIF(Lineups!$A:$A,${name_col}{start}))'
        assert len(rows) == end - start + 1


def test_write_pool_usage_columns_in_formula_has_one_term_per_lineups_block():
    client = FakeClient(header=[*_BASE_HEADER, "Used", "In"])

    write_pool_usage_columns(client, "Player Pool", "Lineups", header_row=_HEADER_ROW)

    in_col = _col_letters(client.header.index("In"))
    first_start, first_end = PLAYER_POOL_NAME_BLOCKS[0]
    _, _, rows = _find_call(client, f"{in_col}{first_start}:{in_col}{first_end}")
    formula = rows[0][0]
    name_col = _col_letters(client.header.index("Name"))
    assert formula.startswith(f'=IF(${name_col}{first_start}="","",IFERROR(TEXTJOIN(')
    for i, (start, end) in enumerate(LINEUPS_NAME_BLOCKS, start=1):
        assert f'"L{i}"' in formula
        assert f"Lineups!$A${start}:$A${end}" in formula


def test_write_pool_usage_columns_falls_back_to_used_alone_when_in_formula_too_long(monkeypatch):
    monkeypatch.setattr(sheet_pool_usage, "_MAX_IN_FORMULA_LEN", 10)
    client = FakeClient(header=[*_BASE_HEADER, "Used", "In"])

    result = write_pool_usage_columns(client, "Player Pool", "Lineups", header_row=_HEADER_ROW)

    in_col = _col_letters(client.header.index("In"))
    assert not any(a1.startswith(in_col) for _, a1, _ in client.update_calls)
    assert "'In' left blank" in result
    used_col = _col_letters(client.header.index("Used"))
    assert any(a1.startswith(used_col) for _, a1, _ in client.update_calls)


def test_used_and_in_count_against_lineups_player_key_when_it_exists():
    """Round 5 follow-up item 3: a player typed as "kenneth walker" still counts as used."""
    client = FakeClient(
        header=[*_BASE_HEADER, "Used", "In"],
        lineups_header=["Name", "Pos.", "Player Key"],
    )

    write_pool_usage_columns(client, "Player Pool", "Lineups", header_row=_HEADER_ROW)

    used_col = _col_letters(client.header.index("Used"))
    first = PLAYER_POOL_NAME_BLOCKS[0][0]
    used_formula = _find_call(client, f"{used_col}{first}:{used_col}{PLAYER_POOL_NAME_BLOCKS[0][1]}")[2][0][0]
    assert "Lineups!$C:$C" in used_formula and "$A:$A" not in used_formula
    in_col = _col_letters(client.header.index("In"))
    in_formula = _find_call(client, f"{in_col}{first}:{in_col}{PLAYER_POOL_NAME_BLOCKS[0][1]}")[2][0][0]
    start, end = LINEUPS_NAME_BLOCKS[0]
    assert f"Lineups!$C${start}:$C${end}" in in_formula


def test_used_falls_back_to_the_typed_column_when_there_is_no_player_key_yet():
    client = FakeClient(header=[*_BASE_HEADER, "Used", "In"], lineups_header=["Name", "Pos."])

    write_pool_usage_columns(client, "Player Pool", "Lineups", header_row=_HEADER_ROW)

    used_col = _col_letters(client.header.index("Used"))
    first = PLAYER_POOL_NAME_BLOCKS[0][0]
    formula = _find_call(client, f"{used_col}{first}:{used_col}{PLAYER_POOL_NAME_BLOCKS[0][1]}")[2][0][0]
    assert "Lineups!$A:$A" in formula
