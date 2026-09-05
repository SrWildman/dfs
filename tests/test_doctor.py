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

_LINEUPS_HEADER_ROWS = [1] + [start - 1 for start, _ in LINEUPS_NAME_BLOCKS[1:]]
_LINEUPS_HEADER_LAST_ROW = max(_LINEUPS_HEADER_ROWS)


def _good_lineups_rows() -> list[list[str]]:
    rows = [[""] for _ in range(_LINEUPS_HEADER_LAST_ROW)]
    for row_num in _LINEUPS_HEADER_ROWS:
        rows[row_num - 1] = ["Name"]
    return rows


def test_run_doctor_reports_nothing_wrong_on_a_correct_sheet():
    cfg = _base_config()
    client = FakeDoctorClient(
        tabs=_ALL_GOOD_TABS,
        rows={
            ("Lineups", f"A1:A{_LINEUPS_HEADER_LAST_ROW}"): _good_lineups_rows(),
        },
    )
    assert run_doctor(client, cfg) == []


def test_run_doctor_flags_missing_tab():
    tabs = dict(_ALL_GOOD_TABS)
    del tabs["Scratch"]
    cfg = _base_config()
    client = FakeDoctorClient(
        tabs=tabs, rows={("Lineups", f"A1:A{_LINEUPS_HEADER_LAST_ROW}"): _good_lineups_rows()}
    )

    issues = run_doctor(client, cfg)
    assert any(i.check == "tab-exists" and "Scratch" in i.detail for i in issues)


def test_run_doctor_flags_edgeraw_header_mismatch():
    tabs = dict(_ALL_GOOD_TABS)
    tabs["EdgeRaw"] = [*EDGE_COLUMNS[:-1], "SomethingElse"]
    cfg = _base_config()
    client = FakeDoctorClient(
        tabs=tabs, rows={("Lineups", f"A1:A{_LINEUPS_HEADER_LAST_ROW}"): _good_lineups_rows()}
    )

    issues = run_doctor(client, cfg)
    assert any(i.check == "edgeraw-header" for i in issues)


def test_run_doctor_flags_missing_linked_edge_columns():
    tabs = dict(_ALL_GOOD_TABS)
    tabs["Lineups"] = ["Name", "Pos."]  # never linked
    cfg = _base_config()
    client = FakeDoctorClient(
        tabs=tabs, rows={("Lineups", f"A1:A{_LINEUPS_HEADER_LAST_ROW}"): _good_lineups_rows()}
    )

    issues = run_doctor(client, cfg)
    assert any(i.check == "linked-edge-columns" and "'Lineups'" in i.detail for i in issues)


def test_run_doctor_flags_duplicated_linked_edge_columns():
    # The exact bug class the idempotency-guard fix targets: two copies of
    # LINKED_EDGE_COLUMNS in the same header.
    tabs = dict(_ALL_GOOD_TABS)
    tabs["Player Pool"] = ["Name", *LINKED_EDGE_COLUMNS, "Venue", "Ceil", *LINKED_EDGE_COLUMNS]
    cfg = _base_config()
    client = FakeDoctorClient(
        tabs=tabs, rows={("Lineups", f"A1:A{_LINEUPS_HEADER_LAST_ROW}"): _good_lineups_rows()}
    )

    issues = run_doctor(client, cfg)
    assert any(
        i.check == "linked-edge-columns" and "'Player Pool'" in i.detail and "2 times" in i.detail
        for i in issues
    )


def test_run_doctor_flags_lineups_header_repeats_drift():
    cfg = _base_config()
    bad_rows = _good_lineups_rows()
    bad_rows[_LINEUPS_HEADER_ROWS[1] - 1] = ["Aaron Rodgers"]  # a stale pick sitting where a header should be
    client = FakeDoctorClient(
        tabs=_ALL_GOOD_TABS, rows={("Lineups", f"A1:A{_LINEUPS_HEADER_LAST_ROW}"): bad_rows}
    )

    issues = run_doctor(client, cfg)
    assert any(i.check == "lineups-header-repeats" for i in issues)


def test_run_doctor_flags_blank_bankroll_header_row():
    cfg = _base_config(
        bankroll={
            "tab": "Bankroll",
            "cash": {"header_row": 16, "first_row": 17, "last_row": 59},
        }
    )
    client = FakeDoctorClient(
        tabs=_ALL_GOOD_TABS,
        rows={
            ("Lineups", f"A1:A{_LINEUPS_HEADER_LAST_ROW}"): _good_lineups_rows(),
            ("Bankroll", "A16:16"): [],
        },
    )

    issues = run_doctor(client, cfg)
    assert any(i.check == "bankroll-header-row" and "cash" in i.detail for i in issues)


def test_run_doctor_passes_bankroll_header_row_when_present():
    cfg = _base_config(
        bankroll={
            "tab": "Bankroll",
            "cash": {"header_row": 16, "first_row": 17, "last_row": 59},
        }
    )
    client = FakeDoctorClient(
        tabs=_ALL_GOOD_TABS,
        rows={
            ("Lineups", f"A1:A{_LINEUPS_HEADER_LAST_ROW}"): _good_lineups_rows(),
            ("Bankroll", "A16:16"): [["Entry name"]],
        },
    )

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
