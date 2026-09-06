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
        # gspread's `Spreadsheet.worksheet(title)` re-fetches the WHOLE
        # spreadsheet's metadata (a full read) to resolve one tab by name,
        # every single time it's called -- there's no caching in gspread
        # itself. A command like `dfs sheets polish`, which can call a
        # presentation primitive 100+ times across ~10 tabs in one run,
        # was reissuing that same full-metadata read before nearly every
        # call and blowing through Sheets' read-request-per-minute quota
        # well before finishing. `_ws()` below resolves each tab once per
        # `SheetsClient` instance (i.e. once per CLI command) and every
        # other method that needs a worksheet routes through it, rather
        # than calling `sheet.worksheet(tab_name)` inline.
        self._ws_cache: dict[str, gspread.Worksheet] = {}

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
            # BackOffHTTPClient retries a 429/408/5xx with exponential
            # backoff instead of raising straight away. `dfs sheets polish`
            # can issue 100+ individual write requests in one run (each
            # format/freeze/colour-scale/boolean-rule call is its own
            # batchUpdate) -- comfortably past the default 60-per-minute
            # write quota on any real sheet, even after the `_ws` cache
            # above cut the matching read-request multiplication. Retrying
            # is the honest fix here (the request always succeeds once
            # quota frees up); there is no smaller number of requests to
            # send without merging every style call in a run into one
            # giant batchUpdate, which would lose the "one call = one
            # documented effect" shape the rest of this file relies on.
            client = gspread.service_account(
                filename=str(creds), http_client=gspread.http_client.BackOffHTTPClient
            )
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

    def _ws(self, tab_name: str) -> tuple[gspread.Spreadsheet, gspread.Worksheet]:
        """Resolve `tab_name` to a `Worksheet`, from this client's cache
        after the first lookup. See the cache's docstring in `__init__`."""
        sheet = self._open()
        ws = self._ws_cache.get(tab_name)
        if ws is None:
            try:
                ws = sheet.worksheet(tab_name)
            except gspread.WorksheetNotFound as e:
                raise SheetsError(f"Tab {tab_name!r} does not exist in the sheet.") from e
            self._ws_cache[tab_name] = ws
        return sheet, ws

    def describe(self) -> tuple[str, str]:
        """(title, url) of the connected sheet -- print this before any sync/
        write so it's never ambiguous which sheet a command is about to touch."""
        sheet = self._open()
        return sheet.title, sheet.url

    def list_tabs(self) -> list[TabInfo]:
        sheet = self._open()
        infos: list[TabInfo] = []
        for ws in sheet.worksheets():
            self._ws_cache[ws.title] = ws
            header: list[str] = []
            try:
                header = ws.row_values(1)
            except Exception as e:  # noqa: BLE001 - report, don't hide
                log.warning("could not read header row for tab %r: %s", ws.title, e)
            infos.append(TabInfo(title=ws.title, rows=ws.row_count, cols=ws.col_count, header=header))
        return infos

    def read_tab(self, tab_name: str) -> list[list[str]]:
        _, ws = self._ws(tab_name)
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
            _, ws = self._ws(tab_name)
        except SheetsError:
            if not create_if_missing:
                raise SheetsError(f"Tab {tab_name!r} does not exist and create_if_missing=False.") from None
            log.info("creating missing tab %r", tab_name)
            ws = sheet.add_worksheet(title=tab_name, rows=max(1000, len(rows) + 10), cols=26)
            self._ws_cache[tab_name] = ws

        if clear_first:
            ws.clear()
        if rows:
            ws.update(range_name="A1", values=rows, value_input_option=ValueInputOption.user_entered)
        return len(rows)

    def format_number_range(self, tab_name: str, a1_range: str, pattern: str = "0.0#") -> None:
        _, ws = self._ws(tab_name)
        ws.format(a1_range, {"numberFormat": {"type": "NUMBER", "pattern": pattern}})

    def read_range(self, tab_name: str, a1_range: str) -> list[list[str]]:
        """Read a sub-range without touching anything outside it."""
        _, ws = self._ws(tab_name)
        return ws.get(a1_range)

    def clear_ranges(self, tab_name: str, a1_ranges: list[str]) -> None:
        """Clear cell values in the given ranges -- formatting (including
        conditional formatting) is untouched, only content is removed."""
        _, ws = self._ws(tab_name)
        ws.batch_clear(a1_ranges)

    def update_range(self, tab_name: str, a1_range: str, rows: list[list]) -> None:
        """Write into a sub-range only -- never clears the tab, never touches
        cells outside `a1_range` (so existing formulas in adjacent columns
        are left alone)."""
        _, ws = self._ws(tab_name)
        ws.update(range_name=a1_range, values=rows, value_input_option=ValueInputOption.user_entered)

    def freeze_header(self, tab_name: str, rows: int = 1) -> None:
        """Freeze the top `rows` row(s) so the header stays visible on scroll."""
        _, ws = self._ws(tab_name)
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
        sheet, ws = self._ws(tab_name)
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

    def insert_rows(self, tab_name: str, *, at_row: int, count: int) -> None:
        """Insert `count` blank rows starting at `at_row` (1-indexed) via a
        real Sheets API `insertDimension` request -- unlike `write_tab`,
        this makes Sheets shift every existing formula range and
        conditional-format range on the tab down along with the insert.
        Rewriting a tab to "make room" does not do that and will silently
        corrupt whatever depended on the old row positions (see
        CONTRIBUTING.md's Phase 8 postmortem); this is the only safe way to
        add rows above content a sheet already depends on."""
        sheet, ws = self._ws(tab_name)
        sheet.batch_update(
            {
                "requests": [
                    {
                        "insertDimension": {
                            "range": {
                                "sheetId": ws.id,
                                "dimension": "ROWS",
                                "startIndex": at_row - 1,
                                "endIndex": at_row - 1 + count,
                            },
                            "inheritFromBefore": False,
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
        sheet, ws = self._ws(tab_name)
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

    # ------------------------------------------------------------------
    # Presentation primitives.
    #
    # Everything below changes only how cells LOOK or where the viewport
    # sits -- widths, freeze, fills, number formats, banding, tab colour,
    # tab order, tab visibility. None of it moves a cell, renames a tab or
    # writes a value, so none of it can invalidate a hardcoded column index
    # or row range (see CONTRIBUTING.md's Phase 8 postmortem). The policy
    # that decides *what* to apply lives in sheet_style.py; these are just
    # the verbs.
    # ------------------------------------------------------------------

    def tab_exists(self, tab_name: str) -> bool:
        """True if `tab_name` is present. Lets callers skip work for tabs a
        given sheet doesn't have (the template and the live copy don't
        always agree) instead of raising. Also warms `_ws`'s cache for
        every tab in the sheet, not just `tab_name` -- `dfs sheets polish`
        calls this once per tab it might style, so by the second call the
        rest of the sheet's tabs are already resolved for free."""
        sheet = self._open()
        found = False
        for ws in sheet.worksheets():
            self._ws_cache[ws.title] = ws
            if ws.title == tab_name:
                found = True
        return found

    def set_column_widths(self, tab_name: str, widths: dict[str, int]) -> None:
        """`widths` maps a column letter to a pixel width, e.g. {"B": 160}."""
        sheet, ws = self._ws(tab_name)
        requests = []
        for col, px in widths.items():
            grid = a1_range_to_grid_range(f"{col}1:{col}1", ws.id)
            requests.append(
                {
                    "updateDimensionProperties": {
                        "range": {
                            "sheetId": ws.id,
                            "dimension": "COLUMNS",
                            "startIndex": grid["startColumnIndex"],
                            "endIndex": grid["endColumnIndex"],
                        },
                        "properties": {"pixelSize": px},
                        "fields": "pixelSize",
                    }
                }
            )
        if requests:
            sheet.batch_update({"requests": requests})

    def set_row_heights(self, tab_name: str, *, start_row: int, end_row: int, pixel_size: int) -> None:
        """Set a pixel height for rows `start_row`..`end_row` (inclusive,
        1-indexed) -- e.g. compacting a frozen control zone so it doesn't
        eat too much vertical space."""
        sheet, ws = self._ws(tab_name)
        sheet.batch_update(
            {
                "requests": [
                    {
                        "updateDimensionProperties": {
                            "range": {
                                "sheetId": ws.id,
                                "dimension": "ROWS",
                                "startIndex": start_row - 1,
                                "endIndex": end_row,
                            },
                            "properties": {"pixelSize": pixel_size},
                            "fields": "pixelSize",
                        }
                    }
                ]
            }
        )

    def set_dropdown_validation(self, tab_name: str, a1_range: str, options: list[str]) -> None:
        """Restrict `a1_range` to a dropdown of `options` (Sheets'
        ONE_OF_LIST data validation) -- a typed value outside the list is
        rejected rather than silently accepted."""
        sheet, ws = self._ws(tab_name)
        grid_range = a1_range_to_grid_range(a1_range, ws.id)
        sheet.batch_update(
            {
                "requests": [
                    {
                        "setDataValidation": {
                            "range": grid_range,
                            "rule": {
                                "condition": {
                                    "type": "ONE_OF_LIST",
                                    "values": [{"userEnteredValue": v} for v in options],
                                },
                                "showCustomUi": True,
                                "strict": True,
                            },
                        }
                    }
                ]
            }
        )

    def format_range(self, tab_name: str, a1_range: str, fmt: dict) -> None:
        """Apply a raw CellFormat dict to a range (fills, fonts, alignment,
        number formats). Passed straight through to the Sheets API."""
        _, ws = self._ws(tab_name)
        ws.format(a1_range, fmt)

    def freeze(self, tab_name: str, *, rows: int | None = None, cols: int | None = None) -> None:
        """Freeze `rows` from the top and/or `cols` from the left. Unlike
        freeze_header this can pin columns, which is what keeps a player's
        Name on screen while you scroll right through 22 columns."""
        _, ws = self._ws(tab_name)
        ws.freeze(rows=rows, cols=cols)

    def add_boolean_rule(
        self,
        tab_name: str,
        a1_range: str,
        *,
        condition_type: str,
        values: list[str],
        fmt: dict,
    ) -> None:
        """Add one conditional-format rule to `a1_range`. `condition_type`
        is a Sheets ConditionType (TEXT_EQ, NUMBER_LESS, CUSTOM_FORMULA...),
        `values` its userEnteredValue arguments, `fmt` the CellFormat to
        apply on match. Like add_color_scale, each call adds one more rule:
        this is one-time setup, not something to run per sync."""
        sheet, ws = self._ws(tab_name)
        grid_range = a1_range_to_grid_range(a1_range, ws.id)
        sheet.batch_update(
            {
                "requests": [
                    {
                        "addConditionalFormatRule": {
                            "rule": {
                                "ranges": [grid_range],
                                "booleanRule": {
                                    "condition": {
                                        "type": condition_type,
                                        "values": [{"userEnteredValue": v} for v in values],
                                    },
                                    "format": fmt,
                                },
                            },
                            "index": 0,
                        }
                    }
                ]
            }
        )

    def clear_conditional_formats(self, tab_name: str, *, column: str | None = None) -> None:
        """Delete conditional-format rules on a tab. Needed to make the
        styling commands re-runnable -- without it, running twice stacks a
        second identical set of rules on top of the first, and Sheets
        applies the most recently added matching rule, so the tab slowly
        accumulates dead rules that are hard to reason about.

        With `column` omitted, deletes every rule on the tab -- correct
        when one function owns all of a tab's conditional formatting
        (EdgeRaw, the four view tabs, Bankroll). Lineups is not one of
        those: `sheet_links.link_edge_columns` already owns color-scale
        rules on other columns there, so `polish_guardrails` passes
        `column="O"` to only ever delete rules confined entirely to that
        one column, leaving every other rule on the tab untouched. Blindly
        clearing the whole tab there would silently destroy real,
        already-correct formatting on every re-run -- exactly the kind of
        mistake CONTRIBUTING.md's changelog now has an incident for.
        """
        sheet, ws = self._ws(tab_name)
        rules = self._conditional_format_rules(sheet, ws)
        if column is None:
            indexes = list(range(len(rules)))
        else:
            col_index = a1_range_to_grid_range(f"{column}1:{column}1")["startColumnIndex"]
            indexes = [
                i
                for i, rule in enumerate(rules)
                if rule.get("ranges")
                and all(
                    r.get("startColumnIndex") == col_index and r.get("endColumnIndex") == col_index + 1
                    for r in rule["ranges"]
                )
            ]
        if not indexes:
            return
        # Delete from the end backwards: each delete reindexes the rest.
        requests = [
            {"deleteConditionalFormatRule": {"sheetId": ws.id, "index": i}} for i in reversed(indexes)
        ]
        sheet.batch_update({"requests": requests})

    @staticmethod
    def _conditional_format_rules(sheet, ws) -> list[dict]:
        meta = sheet.fetch_sheet_metadata(params={"fields": "sheets(properties(sheetId),conditionalFormats)"})
        for s in meta.get("sheets", []):
            if s.get("properties", {}).get("sheetId") == ws.id:
                return s.get("conditionalFormats", []) or []
        return []

    def set_tab_properties(
        self,
        tab_name: str,
        *,
        color: dict | None = None,
        index: int | None = None,
        hidden: bool | None = None,
    ) -> None:
        """Tab colour / position in the strip / visibility. A hidden tab is
        still fully readable and writable through the API, so hiding a
        staging tab does not affect `dfs sync`."""
        sheet, ws = self._ws(tab_name)
        props: dict = {"sheetId": ws.id}
        fields = []
        if color is not None:
            props["tabColor"] = color
            fields.append("tabColor")
        if index is not None:
            props["index"] = index
            fields.append("index")
        if hidden is not None:
            props["hidden"] = hidden
            fields.append("hidden")
        if not fields:
            return
        sheet.batch_update(
            {"requests": [{"updateSheetProperties": {"properties": props, "fields": ",".join(fields)}}]}
        )
