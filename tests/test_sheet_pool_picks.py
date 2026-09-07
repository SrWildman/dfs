from dfs.sheet_pool_picks import _HEADER, _LAST_ROW, create_pool_picks_tab


class FakePoolPicksClient:
    def __init__(self, *, present: bool = False):
        self._present = present
        self.write_tab_calls: list[tuple[str, list[list]]] = []
        self.update_calls: list[tuple[str, list[list]]] = []
        self.validation_calls: list[tuple[str, str]] = []
        self.format_calls: list[tuple[str, dict]] = []
        self.freeze_calls: list[tuple] = []
        self.width_calls: list[dict] = []
        self.clear_cf_calls: list[str] = []
        self.boolean_rule_calls: list[tuple[str, dict]] = []

    def tab_exists(self, tab_name: str) -> bool:
        return self._present

    def write_tab(self, tab_name: str, rows: list[list], **_kwargs) -> int:
        self.write_tab_calls.append((tab_name, rows))
        self._present = True
        return len(rows)

    def update_range(self, tab_name: str, a1_range: str, rows: list[list]) -> None:
        self.update_calls.append((a1_range, rows))

    def set_range_dropdown_validation(
        self, tab_name: str, a1_range: str, *, source: str, strict: bool = False
    ) -> None:
        self.validation_calls.append((a1_range, source))

    def format_range(self, tab_name: str, a1_range: str, fmt: dict) -> None:
        self.format_calls.append((a1_range, fmt))

    def freeze(self, tab_name: str, *, rows=None, cols=None) -> None:
        self.freeze_calls.append((tab_name, rows, cols))

    def set_column_widths(self, tab_name: str, widths: dict[str, int]) -> None:
        self.width_calls.append(widths)

    def clear_conditional_formats(self, tab_name: str, *, column=None, row_range=None) -> None:
        self.clear_cf_calls.append(column)

    def add_boolean_rule(self, tab_name: str, a1_range: str, *, condition_type, values, fmt) -> None:
        rule = {"condition_type": condition_type, "values": values, "fmt": fmt}
        self.boolean_rule_calls.append((a1_range, rule))


def test_create_pool_picks_tab_writes_the_header_via_write_tab_when_new():
    client = FakePoolPicksClient(present=False)
    create_pool_picks_tab(client, edge_tab="EdgeRaw")
    assert client.write_tab_calls == [("Pool Picks", [_HEADER])]
    assert ("A1:J1", [_HEADER]) not in client.update_calls  # not double-written


def test_create_pool_picks_tab_only_updates_the_header_row_when_already_present():
    # Re-running against an existing tab must never call write_tab -- that
    # would clear() the whole sheet first, erasing every typed pick in
    # column A rows 2-101.
    client = FakePoolPicksClient(present=True)
    create_pool_picks_tab(client, edge_tab="EdgeRaw")
    assert client.write_tab_calls == []
    assert ("A1:J1", [_HEADER]) in client.update_calls


def test_create_pool_picks_tab_never_touches_column_a_data_rows():
    client = FakePoolPicksClient(present=True)
    create_pool_picks_tab(client, edge_tab="EdgeRaw")
    for a1_range, _rows in client.update_calls:
        assert not a1_range.startswith("A2")
        assert a1_range != f"A1:A{_LAST_ROW}"


def test_create_pool_picks_tab_writes_formulas_for_every_row():
    client = FakePoolPicksClient(present=True)
    create_pool_picks_tab(client, edge_tab="EdgeRaw")
    body_call = next(c for c in client.update_calls if c[0] == f"B2:J{_LAST_ROW}")
    rows = body_call[1]
    assert len(rows) == _LAST_ROW - 1
    assert all(len(row) == 9 for row in rows)  # B..J = 9 columns


def test_create_pool_picks_tab_status_formula_flags_not_on_slate():
    client = FakePoolPicksClient(present=True)
    create_pool_picks_tab(client, edge_tab="EdgeRaw")
    body_call = next(c for c in client.update_calls if c[0] == f"B2:J{_LAST_ROW}")
    status_formula = body_call[1][0][-1]  # row 2's last column (J = Status)
    assert 'IF($A2="","",' in status_formula
    assert "NOT ON SLATE" in status_formula
    assert '"added"' in status_formula


def test_create_pool_picks_tab_validates_column_a_against_edgeraw_name_range():
    client = FakePoolPicksClient(present=True)
    create_pool_picks_tab(client, edge_tab="EdgeRaw")
    a1_range, source = client.validation_calls[0]
    assert a1_range == f"A2:A{_LAST_ROW}"
    assert source.startswith("EdgeRaw!$")


def test_create_pool_picks_tab_freezes_header_row():
    client = FakePoolPicksClient(present=True)
    create_pool_picks_tab(client, edge_tab="EdgeRaw")
    assert ("Pool Picks", 1, None) in client.freeze_calls


def test_create_pool_picks_tab_number_formats_salary_pts_ceil_columns():
    client = FakePoolPicksClient(present=True)
    result = create_pool_picks_tab(client, edge_tab="EdgeRaw")
    formatted_ranges = {a1 for a1, _fmt in client.format_calls}
    # D=Salary, E=Pts, F=Ceil, G=CeilVal, H=Leverage
    assert f"D2:D{_LAST_ROW}" in formatted_ranges
    assert f"E2:E{_LAST_ROW}" in formatted_ranges
    assert f"G2:G{_LAST_ROW}" in formatted_ranges
    assert "5 column(s) number-formatted" in result


def test_create_pool_picks_tab_sets_a_width_for_every_column():
    client = FakePoolPicksClient(present=True)
    create_pool_picks_tab(client, edge_tab="EdgeRaw")
    widths = client.width_calls[0]
    assert set(widths) == set("ABCDEFGHIJ")
    assert widths["A"] == 165


def test_create_pool_picks_tab_chips_the_flag_column():
    from dfs.sheet_style import FLAG_CHIPS

    client = FakePoolPicksClient(present=True)
    result = create_pool_picks_tab(client, edge_tab="EdgeRaw")
    assert client.clear_cf_calls == ["I"]  # Flag is column I
    flag_rules = [r for a1, r in client.boolean_rule_calls if a1 == f"I2:I{_LAST_ROW}"]
    assert len(flag_rules) == len(FLAG_CHIPS)
    assert "Flag chipped" in result
