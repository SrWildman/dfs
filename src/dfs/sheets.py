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

import json
from dataclasses import dataclass

import gspread
from gspread.utils import ValueInputOption, a1_range_to_grid_range

from dfs.config import GoogleSheetsConfig
from dfs.log import get_logger
from dfs.paths import credentials_path

log = get_logger("sheets")


class SheetsError(Exception):
    """Raised for any Google Sheets auth/access problem."""


def column_letter(index: int) -> str:
    """0-indexed column position -> spreadsheet column letters (0 -> "A",
    26 -> "AA")."""
    letters = ""
    n = index + 1
    while n > 0:
        n, remainder = divmod(n - 1, 26)
        letters = chr(65 + remainder) + letters
    return letters


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
                f"Google Sheets API rejected the request for sheet {self._cfg.sheet_id}: {e}\n"
                f"Share that sheet with the service account's email as an Editor: "
                f"{self._service_account_email(creds)}"
            ) from e
        except Exception as e:
            raise SheetsError(f"Could not open sheet {self._cfg.sheet_id}: {e}") from e

        return self._sheet

    @staticmethod
    def _service_account_email(creds_path) -> str:
        try:
            return json.loads(creds_path.read_text()).get("client_email", "(unknown)")
        except Exception:  # noqa: BLE001 - this only feeds an error message
            return "(unknown -- check the credentials file's client_email field)"

    def describe(self) -> tuple[str, str]:
        """(title, url) of the connected sheet -- print this before any sync/
        write so it's never ambiguous which sheet a command is about to touch."""
        sheet = self._open()
        return sheet.title, sheet.url

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
                raise SheetsError(f"Tab {tab_name!r} does not exist and create_if_missing=False.") from None
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

    def clear_ranges(self, tab_name: str, a1_ranges: list[str]) -> None:
        """Clear cell values in the given ranges -- formatting (including
        conditional formatting) is untouched, only content is removed."""
        sheet = self._open()
        try:
            ws = sheet.worksheet(tab_name)
        except gspread.WorksheetNotFound as e:
            raise SheetsError(f"Tab {tab_name!r} does not exist in the sheet.") from e
        ws.batch_clear(a1_ranges)

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

    def freeze_header(self, tab_name: str, rows: int = 1) -> None:
        """Freeze the top `rows` row(s) so the header stays visible on scroll."""
        sheet = self._open()
        try:
            ws = sheet.worksheet(tab_name)
        except gspread.WorksheetNotFound as e:
            raise SheetsError(f"Tab {tab_name!r} does not exist in the sheet.") from e
        ws.freeze(rows=rows)

    def add_color_scale(
        self,
        tab_name: str,
        a1_range: str,
        *,
        min_color: dict,
        mid_color: dict,
        max_color: dict,
    ) -> None:
        """Apply a 3-point color-scale conditional format to `a1_range` --
        like `update_range`/`clear_ranges`, this only ever touches the range
        it's given, so it's safe to call repeatedly (each call adds one more
        rule; call it once per tab as part of one-time setup, not per sync).
        Colors are {"red": .., "green": .., "blue": ..} floats in 0-1."""
        sheet = self._open()
        try:
            ws = sheet.worksheet(tab_name)
        except gspread.WorksheetNotFound as e:
            raise SheetsError(f"Tab {tab_name!r} does not exist in the sheet.") from e
        grid_range = a1_range_to_grid_range(a1_range, ws.id)
        sheet.batch_update(
            {
                "requests": [
                    {
                        "addConditionalFormatRule": {
                            "rule": {
                                "ranges": [grid_range],
                                "gradientRule": {
                                    "minpoint": {"color": min_color, "type": "MIN"},
                                    "midpoint": {"color": mid_color, "type": "PERCENTILE", "value": "50"},
                                    "maxpoint": {"color": max_color, "type": "MAX"},
                                },
                            },
                            "index": 0,
                        }
                    }
                ]
            }
        )

    def group_columns(self, tab_name: str, first_col_a1: str, last_col_a1: str) -> None:
        """Group a column range so it can be collapsed/expanded from the
        sheet UI (Data > Group columns) -- a display convenience only, does
        not touch cell values or formatting. `first_col_a1`/`last_col_a1`
        are plain column letters (e.g. "P", "Y"), not full A1 refs."""
        sheet = self._open()
        try:
            ws = sheet.worksheet(tab_name)
        except gspread.WorksheetNotFound as e:
            raise SheetsError(f"Tab {tab_name!r} does not exist in the sheet.") from e
        grid_range = a1_range_to_grid_range(f"{first_col_a1}1:{last_col_a1}1", ws.id)
        sheet.batch_update(
            {
                "requests": [
                    {
                        "addDimensionGroup": {
                            "range": {
                                "sheetId": ws.id,
                                "dimension": "COLUMNS",
                                "startIndex": grid_range["startColumnIndex"],
                                "endIndex": grid_range["endColumnIndex"],
                            }
                        }
                    }
                ]
            }
        )
