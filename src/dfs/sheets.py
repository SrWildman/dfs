"""Google Sheets client: read and write, not just write.

Replaces utils/sheets_uploader.py. The old SheetsUploader was upload-only,
resolved credentials relative to CWD (see paths.py's docstring), defaulted
to gspread's DEFAULT_TAB_MAPPINGS which was missing all five SoS keys, and
reported success as `any(results.values())` -- one tab out of eight
succeeding counted as the whole upload succeeding. This client exposes
plain list/read/write primitives; the "is this actually current" judgment
belongs to the caller (dfs sync / dfs status), not buried in here.
"""

from __future__ import annotations

from dataclasses import dataclass

import gspread
from gspread.utils import ValueInputOption

from dfs.config import GoogleSheetsConfig
from dfs.log import get_logger
from dfs.paths import credentials_path

log = get_logger("sheets")


class SheetsError(Exception):
    """Raised for any Google Sheets auth/access problem."""


@dataclass
class TabInfo:
    title: str
    rows: int
    cols: int
    header: list[str]


class SheetsClient:
    def __init__(self, cfg: GoogleSheetsConfig) -> None:
        self._cfg = cfg
        self._sheet: gspread.Spreadsheet | None = None

    def _open(self) -> gspread.Spreadsheet:
        if self._sheet is not None:
            return self._sheet

        creds = credentials_path(self._cfg.credentials_file)
        if not creds.exists():
            raise SheetsError(
                f"Credentials file not found: {creds}\n"
                f"See docs/SHEETS_SETUP.md to create a service account key."
            )

        try:
            client = gspread.service_account(filename=str(creds))
            self._sheet = client.open_by_key(self._cfg.sheet_id)
        except gspread.exceptions.APIError as e:
            raise SheetsError(
                f"Google Sheets API rejected the request: {e}\n"
                f"Check that the sheet is shared with the service account's "
                f"client_email."
            ) from e
        except Exception as e:
            raise SheetsError(f"Could not open sheet {self._cfg.sheet_id}: {e}") from e

        return self._sheet

    def list_tabs(self) -> list[TabInfo]:
        sheet = self._open()
        infos: list[TabInfo] = []
        for ws in sheet.worksheets():
            header: list[str] = []
            try:
                header = ws.row_values(1)
            except Exception as e:  # noqa: BLE001 - report, don't hide
                log.warning("could not read header row for tab %r: %s", ws.title, e)
            infos.append(TabInfo(title=ws.title, rows=ws.row_count, cols=ws.col_count, header=header))
        return infos

    def read_tab(self, tab_name: str) -> list[list[str]]:
        sheet = self._open()
        try:
            ws = sheet.worksheet(tab_name)
        except gspread.WorksheetNotFound as e:
            raise SheetsError(f"Tab {tab_name!r} does not exist in the sheet.") from e
        return ws.get_all_values()

    def write_tab(
        self,
        tab_name: str,
        rows: list[list],
        *,
        create_if_missing: bool = True,
        clear_first: bool = True,
    ) -> int:
        """Overwrite `tab_name` with `rows` (header included). Returns row count written."""
        sheet = self._open()
        try:
            ws = sheet.worksheet(tab_name)
        except gspread.WorksheetNotFound:
            if not create_if_missing:
                raise SheetsError(f"Tab {tab_name!r} does not exist and create_if_missing=False.")
            log.info("creating missing tab %r", tab_name)
            ws = sheet.add_worksheet(title=tab_name, rows=max(1000, len(rows) + 10), cols=26)

        if clear_first:
            ws.clear()
        if rows:
            ws.update(range_name="A1", values=rows, value_input_option=ValueInputOption.user_entered)
        return len(rows)

    def format_number_range(self, tab_name: str, a1_range: str, pattern: str = "0.0#") -> None:
        sheet = self._open()
        ws = sheet.worksheet(tab_name)
        ws.format(a1_range, {"numberFormat": {"type": "NUMBER", "pattern": pattern}})

    def read_range(self, tab_name: str, a1_range: str) -> list[list[str]]:
        """Read a sub-range without touching anything outside it."""
        sheet = self._open()
        try:
            ws = sheet.worksheet(tab_name)
        except gspread.WorksheetNotFound as e:
            raise SheetsError(f"Tab {tab_name!r} does not exist in the sheet.") from e
        return ws.get(a1_range)

    def update_range(self, tab_name: str, a1_range: str, rows: list[list]) -> None:
        """Write into a sub-range only -- never clears the tab, never touches
        cells outside `a1_range` (so existing formulas in adjacent columns
        are left alone)."""
        sheet = self._open()
        try:
            ws = sheet.worksheet(tab_name)
        except gspread.WorksheetNotFound as e:
            raise SheetsError(f"Tab {tab_name!r} does not exist in the sheet.") from e
        ws.update(range_name=a1_range, values=rows, value_input_option=ValueInputOption.user_entered)
