"""Offline tests for SheetsClient, using a fake gspread backend."""

from __future__ import annotations

import gspread
import pytest

from dfs.config import GoogleSheetsConfig
from dfs.sheets import SheetsClient, SheetsError


class FakeWorksheet:
    def __init__(self, title: str, rows: list[list[str]] | None = None):
        self.title = title
        self.row_count = 1000
        self.col_count = 26
        self._rows = rows or []

    def row_values(self, n: int) -> list[str]:
        idx = n - 1
        return self._rows[idx] if idx < len(self._rows) else []

    def get_all_values(self) -> list[list[str]]:
        return self._rows

    def clear(self) -> None:
        self._rows = []

    def update(self, range_name: str, values, value_input_option=None) -> None:
        self._rows = [list(map(str, row)) for row in values]

    def format(self, a1_range: str, fmt: dict) -> None:
        pass


class FakeSpreadsheet:
    def __init__(self):
        self._worksheets: dict[str, FakeWorksheet] = {}

    def worksheets(self) -> list[FakeWorksheet]:
        return list(self._worksheets.values())

    def worksheet(self, title: str) -> FakeWorksheet:
        try:
            return self._worksheets[title]
        except KeyError:
            raise gspread.WorksheetNotFound(title)

    def add_worksheet(self, title: str, rows: int, cols: int) -> FakeWorksheet:
        ws = FakeWorksheet(title)
        self._worksheets[title] = ws
        return ws


@pytest.fixture
def cfg() -> GoogleSheetsConfig:
    return GoogleSheetsConfig(
        sheet_id="fake-sheet-id",
        credentials_file="creds.json",
        tab_mappings={"projections": "Projections"},
    )


def _client_with_fake_sheet(cfg, monkeypatch, tmp_path) -> tuple[SheetsClient, FakeSpreadsheet]:
    creds = tmp_path / "creds.json"
    creds.write_text("{}")
    cfg = cfg.model_copy(update={"credentials_file": str(creds)})

    fake_sheet = FakeSpreadsheet()
    monkeypatch.setattr("gspread.service_account", lambda filename: type(
        "C", (), {"open_by_key": lambda self, key: fake_sheet}
    )())

    return SheetsClient(cfg), fake_sheet


def test_missing_credentials_file_raises_sheets_error(cfg):
    client = SheetsClient(cfg)
    with pytest.raises(SheetsError, match="Credentials file not found"):
        client.list_tabs()


def test_write_then_read_round_trip(cfg, monkeypatch, tmp_path):
    client, _ = _client_with_fake_sheet(cfg, monkeypatch, tmp_path)
    rows = [["a", "b"], ["1", "2"]]
    written = client.write_tab("NewTab", rows)
    assert written == 2
    assert client.read_tab("NewTab") == rows


def test_read_missing_tab_raises_sheets_error(cfg, monkeypatch, tmp_path):
    client, _ = _client_with_fake_sheet(cfg, monkeypatch, tmp_path)
    with pytest.raises(SheetsError, match="does not exist"):
        client.read_tab("NoSuchTab")


def test_list_tabs_reports_header_rows(cfg, monkeypatch, tmp_path):
    client, fake_sheet = _client_with_fake_sheet(cfg, monkeypatch, tmp_path)
    fake_sheet._worksheets["Projections"] = FakeWorksheet(
        "Projections", rows=[["Id", "Name", "Position"]]
    )
    tabs = client.list_tabs()
    assert len(tabs) == 1
    assert tabs[0].title == "Projections"
    assert tabs[0].header == ["Id", "Name", "Position"]
