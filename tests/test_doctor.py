from dataclasses import dataclass

from dfs.config import Config
from dfs.derived import EDGE_COLUMNS
from dfs.doctor import run_doctor
from dfs.sheet_links import LINKED_EDGE_COLUMNS, PLAYER_POOL_RAW_TAB
from dfs.weekly_reset import LINEUPS_NAME_BLOCKS


@dataclass
class _FakeTab:
    title: str
    header: list[str]


class FakeDoctorClient:
    """Fakes just the two SheetsClient methods doctor.py calls -- list_tabs()
    for tab existence + header rows, read_range() for the row-specific
    checks (Lineups header repeats, Bankroll header rows)."""

    def __init__(
        self, tabs: dict[str, list[str]], rows: dict[tuple[str, str], list[list[str]]] | None = None
    ):
        self._tabs = tabs
        self._rows = rows or {}

    def list_tabs(self):
        return [_FakeTab(title=title, header=header) for title, header in self._tabs.items()]

    def read_range(self, tab_name: str, a1_range: str) -> list[list[str]]:
        return self._rows.get((tab_name, a1_range), [])


def _base_config(**overrides) -> Config:
    data = {
        "google_sheets": {
            "sheet_id": "abc",
            "credentials_file": "creds.json",
            "tab_mappings": {"edge": "EdgeRaw"},
        },
        "lineups": {
            "upload_tab": "DK Upload",
            "scratch_tab": "Scratch",
            "builder_tab": "Lineups",
            "player_pool_tab": "Player Pool",
        },
        "bankroll": {"tab": "Bankroll"},
    }
    data.update(overrides)
    return Config.model_validate(data)


_ALL_GOOD_TABS = {
    "EdgeRaw": EDGE_COLUMNS,
    "DK Upload": ["Entry ID"],
    "Scratch": [],
    "Lineups": ["Name", "Pos.", *LINKED_EDGE_COLUMNS],
    "Player Pool": ["Name", "Pos.", *LINKED_EDGE_COLUMNS],
    PLAYER_POOL_RAW_TAB: ["Name", "Pos.", *LINKED_EDGE_COLUMNS],
    "Bankroll": [],
    "Results": [],
}

_LINEUPS_HEADER_ROWS = [start - 1 for start, _ in LINEUPS_NAME_BLOCKS]
_LINEUPS_HEADER_LAST_ROW = max(_LINEUPS_HEADER_ROWS)
_LINEUPS_HEADER_ROW = _LINEUPS_HEADER_ROWS[0]  # row 8: Lineups' real header, post-Bench

# run_doctor re-reads Lineups' header from this exact row (see doctor.py's
# override of list_tabs()'s always-row-1 header) rather than trusting
# _ALL_GOOD_TABS["Lineups"] the way every other tab's check does -- so
# every test that wants the linked-edge-columns check to see Lineups as
# correctly linked must supply this row too, not just tabs["Lineups"].
_LINEUPS_HEADER_RANGE = f"A{_LINEUPS_HEADER_ROW}:{_LINEUPS_HEADER_ROW}"


def _good_lineups_rows() -> list[list[str]]:
    rows = [[""] for _ in range(_LINEUPS_HEADER_LAST_ROW)]
    for row_num in _LINEUPS_HEADER_ROWS:
        rows[row_num - 1] = ["Name"]
    return rows


def _lineups_rows(header: list[str] | None = None) -> dict[tuple[str, str], list[list[str]]]:
    rows = {("Lineups", f"A1:A{_LINEUPS_HEADER_LAST_ROW}"): _good_lineups_rows()}
    if header is not None:
        rows[("Lineups", _LINEUPS_HEADER_RANGE)] = [header]
    return rows


def test_run_doctor_reports_nothing_wrong_on_a_correct_sheet():
    cfg = _base_config()
    client = FakeDoctorClient(
        tabs=_ALL_GOOD_TABS,
        rows=_lineups_rows(_ALL_GOOD_TABS["Lineups"]),
    )
    assert run_doctor(client, cfg) == []


def test_run_doctor_flags_missing_tab():
    tabs = dict(_ALL_GOOD_TABS)
    del tabs["Scratch"]
    cfg = _base_config()
    client = FakeDoctorClient(tabs=tabs, rows=_lineups_rows(tabs["Lineups"]))

    issues = run_doctor(client, cfg)
    assert any(i.check == "tab-exists" and "Scratch" in i.detail for i in issues)


def test_run_doctor_flags_edgeraw_header_mismatch():
    tabs = dict(_ALL_GOOD_TABS)
    tabs["EdgeRaw"] = [*EDGE_COLUMNS[:-1], "SomethingElse"]
    cfg = _base_config()
    client = FakeDoctorClient(tabs=tabs, rows=_lineups_rows(tabs["Lineups"]))

    issues = run_doctor(client, cfg)
    assert any(i.check == "edgeraw-header" for i in issues)


def test_run_doctor_passes_when_edgeraw_has_a_correct_pool_column():
    tabs = dict(_ALL_GOOD_TABS)
    tabs["EdgeRaw"] = [*EDGE_COLUMNS, "Pool"]
    cfg = _base_config()
    client = FakeDoctorClient(tabs=tabs, rows=_lineups_rows(tabs["Lineups"]))

    assert run_doctor(client, cfg) == []


def test_run_doctor_flags_a_wrong_label_in_edgeraw_pool_column():
    tabs = dict(_ALL_GOOD_TABS)
    tabs["EdgeRaw"] = [*EDGE_COLUMNS, "SomethingElse"]
    cfg = _base_config()
    client = FakeDoctorClient(tabs=tabs, rows=_lineups_rows(tabs["Lineups"]))

    issues = run_doctor(client, cfg)
    assert any(i.check == "edgeraw-header" and "Pool" in i.detail for i in issues)


def test_run_doctor_flags_missing_linked_edge_columns():
    tabs = dict(_ALL_GOOD_TABS)
    tabs["Lineups"] = ["Name", "Pos."]  # never linked
    cfg = _base_config()
    client = FakeDoctorClient(tabs=tabs, rows=_lineups_rows(tabs["Lineups"]))

    issues = run_doctor(client, cfg)
    assert any(i.check == "linked-edge-columns" and "'Lineups'" in i.detail for i in issues)


def test_run_doctor_flags_missing_linked_edge_columns_reading_lineups_real_header_row():
    # Regression for the bug that shipped alongside the Bench feature:
    # Lineups' header moved to row 8, but the linked-edge-columns check
    # used to trust list_tabs()'s row-1-only header for every tab. A
    # Lineups row 1 (the Bench title, one cell) that looks nothing like
    # LINKED_EDGE_COLUMNS must not be mistaken for "not linked" when row 8
    # actually has it -- and, conversely (this test), row 8 genuinely not
    # having it must still be caught even though tabs["Lineups"] here
    # (row-1-shaped, unused by the real check) looks irrelevant.
    tabs = dict(_ALL_GOOD_TABS)
    tabs["Lineups"] = ["BENCH title -- irrelevant to this check now"]
    cfg = _base_config()
    client = FakeDoctorClient(tabs=tabs, rows=_lineups_rows(["Name", "Pos."]))  # row 8: never linked

    issues = run_doctor(client, cfg)
    assert any(i.check == "linked-edge-columns" and "'Lineups'" in i.detail for i in issues)


def test_run_doctor_passes_linked_edge_columns_reading_lineups_real_header_row():
    tabs = dict(_ALL_GOOD_TABS)
    tabs["Lineups"] = ["BENCH title -- irrelevant to this check now"]
    cfg = _base_config()
    client = FakeDoctorClient(tabs=tabs, rows=_lineups_rows(["Name", "Pos.", *LINKED_EDGE_COLUMNS]))

    issues = run_doctor(client, cfg)
    assert not any(i.check == "linked-edge-columns" and "'Lineups'" in i.detail for i in issues)


def test_run_doctor_flags_duplicated_linked_edge_columns():
    # The exact bug class the idempotency-guard fix targets: two copies of
    # LINKED_EDGE_COLUMNS in the same header.
    tabs = dict(_ALL_GOOD_TABS)
    tabs["Player Pool"] = ["Name", *LINKED_EDGE_COLUMNS, "Venue", "Ceil", *LINKED_EDGE_COLUMNS]
    cfg = _base_config()
    client = FakeDoctorClient(tabs=tabs, rows=_lineups_rows(tabs["Lineups"]))

    issues = run_doctor(client, cfg)
    assert any(
        i.check == "linked-edge-columns" and "'Player Pool'" in i.detail and "2 times" in i.detail
        for i in issues
    )


def test_run_doctor_flags_lineups_header_repeats_drift():
    cfg = _base_config()
    bad_rows = _good_lineups_rows()
    bad_rows[_LINEUPS_HEADER_ROWS[1] - 1] = ["Aaron Rodgers"]  # a stale pick sitting where a header should be
    rows = _lineups_rows(_ALL_GOOD_TABS["Lineups"])
    rows[("Lineups", f"A1:A{_LINEUPS_HEADER_LAST_ROW}")] = bad_rows
    client = FakeDoctorClient(tabs=_ALL_GOOD_TABS, rows=rows)

    issues = run_doctor(client, cfg)
    assert any(i.check == "lineups-header-repeats" for i in issues)


def test_run_doctor_flags_blank_bankroll_header_row():
    cfg = _base_config(
        bankroll={
            "tab": "Bankroll",
            "cash": {"header_row": 16, "first_row": 17, "last_row": 59},
        }
    )
    rows = _lineups_rows(_ALL_GOOD_TABS["Lineups"])
    rows[("Bankroll", "A16:16")] = []
    client = FakeDoctorClient(tabs=_ALL_GOOD_TABS, rows=rows)

    issues = run_doctor(client, cfg)
    assert any(i.check == "bankroll-header-row" and "cash" in i.detail for i in issues)


def test_run_doctor_passes_bankroll_header_row_when_present():
    cfg = _base_config(
        bankroll={
            "tab": "Bankroll",
            "cash": {"header_row": 16, "first_row": 17, "last_row": 59},
        }
    )
    rows = _lineups_rows(_ALL_GOOD_TABS["Lineups"])
    rows[("Bankroll", "A16:16")] = [["Entry name"]]
    client = FakeDoctorClient(tabs=_ALL_GOOD_TABS, rows=rows)

    issues = run_doctor(client, cfg)
    assert not any(i.check == "bankroll-header-row" for i in issues)


def test_run_doctor_skips_dependent_checks_for_a_missing_tab():
    # A missing Lineups tab should report tab-exists once, not also crash
    # or double-report from the header-repeats check.
    tabs = dict(_ALL_GOOD_TABS)
    del tabs["Lineups"]
    cfg = _base_config()
    client = FakeDoctorClient(tabs=tabs)

    issues = run_doctor(client, cfg)
    assert any(i.check == "tab-exists" and "Lineups" in i.detail for i in issues)
    assert not any(i.check == "lineups-header-repeats" for i in issues)
