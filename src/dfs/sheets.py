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
from gspread.utils import ValueInputOption, ValueRenderOption, a1_range_to_grid_range

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


def _gradient_rule(
    grid_range: dict,
    *,
    min_color: dict,
    mid_color: dict,
    max_color: dict,
    mid_type: str,
    mid_value: str,
    min_type: str,
    min_value: str | None,
    max_type: str,
    max_value: str | None,
) -> dict:
    """Builds one `ConditionalFormatRule` dict for a gradient (colour
    scale) rule -- shared by `add_color_scale` (one HTTP call) and
    `add_color_scales` (many rules, one call) so the two never drift."""
    minpoint = {"color": min_color, "type": min_type}
    if min_value is not None:
        minpoint["value"] = min_value
    maxpoint = {"color": max_color, "type": max_type}
    if max_value is not None:
        maxpoint["value"] = max_value
    return {
        "ranges": [grid_range],
        "gradientRule": {
            "minpoint": minpoint,
            "midpoint": {"color": mid_color, "type": mid_type, "value": mid_value},
            "maxpoint": maxpoint,
        },
    }


def _boolean_rule(grid_range: dict, *, condition_type: str, values: list[str], fmt: dict) -> dict:
    """Builds one `ConditionalFormatRule` dict for a boolean rule --
    shared by `add_boolean_rule` and `add_boolean_rules` (see
    `_gradient_rule`'s own docstring for why)."""
    return {
        "ranges": [grid_range],
        "booleanRule": {
            "condition": {"type": condition_type, "values": [{"userEnteredValue": v} for v in values]},
            "format": fmt,
        },
    }


@dataclass
class TabInfo:
    title: str
    rows: int
    cols: int
    header: list[str]
    frozen_rows: int


class SheetsClient:
    def __init__(self, cfg: GoogleSheetsConfig) -> None:
        self._cfg = cfg
        self._sheet: gspread.Spreadsheet | None = None
        # gspread's `Spreadsheet.worksheet(title)` re-fetches the WHOLE
        # spreadsheet's metadata (a full read) to resolve one tab by name,
        # every single time it's called -- there's no caching in gspread
        # itself. A command like `dfs setup polish`, which can call a
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
            # backoff instead of raising straight away. `dfs setup polish`
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
            infos.append(
                TabInfo(
                    title=ws.title,
                    rows=ws.row_count,
                    cols=ws.col_count,
                    header=header,
                    frozen_rows=ws.frozen_row_count,
                )
            )
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

    def batch_read_ranges(self, specs: list[tuple[str, str]]) -> list[list[list[str]]]:
        """Read several ranges (each `(tab_name, a1_range)`) in ONE Sheets
        API round trip via `values_batch_get`, in the order given -- built
        for the bare `dfs` launcher (Task 2.5), which needs pool ticks AND
        lineup fill counts to render and has a 2-second budget, so two
        separate `read_range` calls (two round trips, each paying Sheets'
        latency and the client's own backoff-retry overhead) isn't good
        enough. Returns one values-grid per spec, same shape as
        `read_range`, in the same order -- a range with no data back comes
        back as `[]`, matching `read_range`'s own behaviour for a blank
        range."""
        sheet = self._open()
        ranges = [f"'{tab}'!{a1}" for tab, a1 in specs]
        response = sheet.values_batch_get(ranges)
        # Google echoes back a fully-qualified, requoted range string, so
        # matching results to requests by POSITION (the API preserves
        # request order) is simpler and safer than re-parsing that string
        # back into (tab, a1).
        value_ranges = response.get("valueRanges", [])
        return [value_ranges[i].get("values", []) if i < len(value_ranges) else [] for i in range(len(specs))]

    def read_range_unformatted(self, tab_name: str, a1_range: str) -> list[list]:
        """Like `read_range`, but returns each cell's raw underlying
        value (a JSON number/string/bool) rather than its FORMATTED
        display text. Needed for anything read back and used as an exact
        match key across a sync -- `read_range`'s default (formatted)
        rendering bakes in whatever number format the cell's PHYSICAL
        position happens to carry, which can silently change a purely
        numeric-looking string (a DraftKings player Id, say) into
        something like `"+44132966.0"` if that column ever inherits a
        stale signed/decimal format left over from a different field that
        used to occupy the same physical column before a reorder (`dfs
        sync` rewrites values, never formatting) -- found live,
        2026-09-16, silently breaking `sources/edge.py`'s Pool-tick
        preserve-by-Id join across an EdgeRaw column reorder. Values come
        back as Python `int`/`float`/`str`/`bool`, not pre-stringified --
        callers that need a stable string key should normalize (e.g.
        `int(x)` before `str()`, to drop a `.0` a whole-number float would
        otherwise carry)."""
        _, ws = self._ws(tab_name)
        return ws.get(a1_range, value_render_option=ValueRenderOption.unformatted)

    def read_formula(self, tab_name: str, a1_range: str) -> list[list[str]]:
        """Like `read_range`, but returns the literal formula text (e.g.
        "=SUM(A1:A2)") instead of the resolved value for any formula cell --
        needed to tell "a typed value" from "a formula that happens to
        currently resolve to the same-looking text" (see
        `weekly_reset.clear_previous_week`'s Player Pool formula check)."""
        _, ws = self._ws(tab_name)
        return ws.get(a1_range, value_render_option=ValueRenderOption.formula)

    def get_cell_formats(self, tab_name: str, a1_range: str) -> list[list[dict]]:
        """Read back the resolved `userEnteredFormat` for every cell in
        `a1_range` -- the read-side counterpart to `format_range`, needed
        by `dfs setup audit-style` to check what a tab actually looks
        like rather than trusting a styling command's own "OK" output.
        Never used by a writing command."""
        sheet, ws = self._ws(tab_name)
        meta = sheet.fetch_sheet_metadata(
            params={
                "ranges": [f"'{tab_name}'!{a1_range}"],
                "fields": "sheets(data(rowData(values(userEnteredFormat))))",
            }
        )
        sheets_data = meta.get("sheets", [])
        if not sheets_data or not sheets_data[0].get("data"):
            return []
        row_data = sheets_data[0]["data"][0].get("rowData", [])
        return [[cell.get("userEnteredFormat", {}) for cell in row.get("values", [])] for row in row_data]

    def get_column_widths(self, tab_name: str, last_col_a1: str) -> list[dict]:
        """Read back `columnMetadata` (pixelSize, hiddenByUser) for every
        column from A through `last_col_a1`, index-aligned (index 0 = A).
        Sheets reports a `pixelSize` of 100 for a column that was never
        explicitly widened -- that is the *default*, not a real choice --
        so a caller checking "was this column actually set" should treat
        exactly 100 as "no width set" rather than trusting the key's mere
        presence, which every column has regardless."""
        sheet, ws = self._ws(tab_name)
        meta = sheet.fetch_sheet_metadata(
            params={
                "ranges": [f"'{tab_name}'!A1:{last_col_a1}1"],
                "fields": "sheets(data(columnMetadata))",
            }
        )
        sheets_data = meta.get("sheets", [])
        if not sheets_data or not sheets_data[0].get("data"):
            return []
        return sheets_data[0]["data"][0].get("columnMetadata", [])

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
        mid_type: str = "PERCENTILE",
        mid_value: str = "50",
        min_type: str = "MIN",
        min_value: str | None = None,
        max_type: str = "MAX",
        max_value: str | None = None,
    ) -> None:
        """Apply a 3-point color-scale conditional format to `a1_range` --
        like `update_range`/`clear_ranges`, this only ever touches the range
        it's given, so it's safe to call repeatedly (each call adds one more
        rule; call it once per tab as part of one-time setup, not per sync).
        Colors are {"red": .., "green": .., "blue": ..} floats in 0-1.

        `mid_type`/`mid_value` default to the statistical median (50th
        percentile) -- right for a plain "more is better" scale. Pass
        `mid_type="NUMBER", mid_value="0"` for a signed-delta column where
        zero, not the median, is the meaningful midpoint (e.g. ImpliedMove) --
        a true diverging scale rather than one that happens to have three
        colors.

        `min_type`/`min_value` (and `max_type`/`max_value`, same shape)
        default to the range's own actual minimum/maximum. Pass
        `min_type="NUMBER", min_value="=MINIFS(...)"` (Sheets accepts a
        formula for a NUMBER-type interpolation point's value) to anchor
        an endpoint somewhere other than `a1_range`'s own true min/max --
        e.g. Fix 2.7's zero-exclusion (min anchored past a real 0), or
        Phase 4's per-group scaling (both endpoints anchored to a
        DIFFERENT range than `a1_range` itself -- verified live that a
        gradient's min/mid/max are each evaluated ONCE, from a fixed
        reference, not per-cell-relative like a custom boolean formula
        would be; a formula-anchored endpoint can point anywhere, but one
        rule still can't self-scope independently across multiple groups
        within its own range -- see CONTRIBUTING.md's Phase 4 changelog).
        """
        sheet, ws = self._ws(tab_name)
        grid_range = a1_range_to_grid_range(a1_range, ws.id)
        rule = _gradient_rule(
            grid_range,
            min_color=min_color,
            mid_color=mid_color,
            max_color=max_color,
            mid_type=mid_type,
            mid_value=mid_value,
            min_type=min_type,
            min_value=min_value,
            max_type=max_type,
            max_value=max_value,
        )
        sheet.batch_update({"requests": [{"addConditionalFormatRule": {"rule": rule, "index": 0}}]})

    def add_color_scales(self, tab_name: str, specs: list[dict]) -> None:
        """Same rule as `add_color_scale`, one call per item in `specs`
        (each a kwargs dict matching `add_color_scale`'s own signature,
        keyed `a1_range`/`min_color`/.../`max_value`) -- but issued as
        ONE `batchUpdate` covering every rule, not one HTTP round-trip
        per rule. Needed once `apply_grouped_color_scales` (Phase 4)
        started generating a rule per `(column, group)` pair: 14 scaled
        columns x 20 Lineups blocks is 280 gradient rules (plus ~20 more
        zero-exclusion boolean rules), and `add_color_scale`'s one-
        request-per-call shape -- fine for a handful of whole-tab rules
        -- would mean hundreds of sequential round-trips through
        `BackOffHTTPClient`'s retry/backoff, each one a real chance to
        eat the write-quota window `dfs setup polish` already runs close
        to. All rules land at `index: 0` -- correct since a single
        `batchUpdate`'s requests apply in order (so the *last* rule here
        ends up at index 0, earlier ones pushed down), and safe since
        none of Phase 4's per-group rules overlap in range with each
        other, so their relative priority against one another never
        matters -- only priority against a DIFFERENT rule that already
        exists (e.g. a zero-exclusion chip added afterward) does, and
        that ordering is unaffected by how many rules land in between.
        """
        if not specs:
            return
        _, ws = self._ws(tab_name)
        requests = []
        for spec in specs:
            grid_range = a1_range_to_grid_range(spec["a1_range"], ws.id)
            rule = _gradient_rule(
                grid_range,
                min_color=spec["min_color"],
                mid_color=spec["mid_color"],
                max_color=spec["max_color"],
                mid_type=spec.get("mid_type", "PERCENTILE"),
                mid_value=spec.get("mid_value", "50"),
                min_type=spec.get("min_type", "MIN"),
                min_value=spec.get("min_value"),
                max_type=spec.get("max_type", "MAX"),
                max_value=spec.get("max_value"),
            )
            requests.append({"addConditionalFormatRule": {"rule": rule, "index": 0}})
        sheet, _ = self._ws(tab_name)
        sheet.batch_update({"requests": requests})

    def insert_rows(self, tab_name: str, *, at_row: int, count: int) -> None:
        """Insert `count` blank rows starting at `at_row` (1-indexed) via a
        real Sheets API `insertDimension` request -- unlike `write_tab`,
        this makes Sheets shift every existing formula range and
        conditional-format range on the tab down along with the insert.
        Rewriting a tab to "make room" does not do that and will silently
        corrupt whatever depended on the old row positions (see
        CONTRIBUTING.md's Phase 8 postmortem); this is the only safe way to
        add rows above content a sheet already depends on.

        `inheritFromBefore=False` is not "inherit nothing" -- there is no
        such option. It means "inherit from the dimension *after* the
        insertion point" (`True` means "before", and is invalid at
        `at_row=1` since there is no row above it). Inserting at the very
        top therefore always inherits the formatting of whatever row used
        to be first and is now pushed below the new rows -- if that row
        was formatted (e.g. a dark header fill), every newly inserted row
        picks up the same fill. Callers that need blank-looking new rows
        must explicitly reset their formatting afterward; this method only
        does the structural part. (Found the hard way: see
        CONTRIBUTING.md's changelog on the pool deck's inherited dark
        background.)
        """
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

    def delete_rows(self, tab_name: str, *, at_row: int, count: int) -> None:
        """Delete `count` rows starting at `at_row` (1-indexed) via a real
        Sheets API `deleteDimension` request -- everything below shifts up
        to fill the gap, same shifting guarantee as `insert_rows` in
        reverse."""
        sheet, ws = self._ws(tab_name)
        sheet.batch_update(
            {
                "requests": [
                    {
                        "deleteDimension": {
                            "range": {
                                "sheetId": ws.id,
                                "dimension": "ROWS",
                                "startIndex": at_row - 1,
                                "endIndex": at_row - 1 + count,
                            }
                        }
                    }
                ]
            }
        )

    def delete_columns(self, tab_name: str, *, at_index: int, count: int) -> None:
        """Delete `count` columns starting at `at_index` (0-based) via a
        real Sheets API `deleteDimension` request -- everything to the
        right shifts left to fill the gap, same shifting guarantee as
        `delete_rows`'s row-dimension equivalent.

        Found live migrating Part 7.9's `OwnPct` removal: a raw
        `deleteDimension` request bypasses gspread's own dimension-
        changing methods (`resize`/`add_cols`), which are the only things
        that update the cached `Worksheet._properties["gridProperties"]
        ["columnCount"]` gspread's own `col_count` property reads --
        gspread's own docs even warn `col_count` "is not dynamically
        updated when adding columns, yet". Left uncorrected, a column
        actually shrinks by `count` while this client's cached `ws` object
        (held for the rest of this `SheetsClient` instance's lifetime, see
        `_ws`'s own docstring) still reports the OLD, now-too-wide count --
        so a later `ensure_column_capacity` call in the same script
        compares against stale data, concludes the grid is already wide
        enough, skips growing it, and the next write past the new
        (shrunk) edge fails outright ("exceeds grid limits"). Updating the
        cache here, the same way gspread's own `resize()` does after a
        successful call, keeps every later `col_count`/`ensure_column_
        capacity` read in this same script correct."""
        sheet, ws = self._ws(tab_name)
        sheet.batch_update(
            {
                "requests": [
                    {
                        "deleteDimension": {
                            "range": {
                                "sheetId": ws.id,
                                "dimension": "COLUMNS",
                                "startIndex": at_index,
                                "endIndex": at_index + count,
                            }
                        }
                    }
                ]
            }
        )
        ws._properties["gridProperties"]["columnCount"] -= count

    def move_columns(self, tab_name: str, *, from_index: int, to_index: int) -> None:
        """Move the single column at `from_index` (0-based) to `to_index`
        (0-based, in `list.pop`/`list.insert` terms: "as if `from_index`
        were removed first, then inserted at `to_index`") via a real
        Sheets API `moveDimension` request.

        `moveDimension`'s own `destinationIndex` is NOT the same number as
        `to_index` -- it's specified in the ORIGINAL, pre-removal index
        space, so it must be shifted by +1 when moving something later in
        the sheet (removing the source column first shifts everything
        after it left by one, so "insert before old-index N" and "insert
        before new-index N" point at different places once N > from_index).
        Verified empirically against a disposable scratch tab before this
        was ever trusted against a real tab's structure (Phase 3): moving
        column index 1 to index 3 in a 4-column sheet requires
        `destinationIndex=4`, not `3` -- `to_index + 1` when moving right,
        `to_index` unchanged when moving left (`to_index <= from_index`).

        Like `insert_rows`/`delete_rows`, this makes Sheets shift every
        formula RANGE reference and conditional-format range elsewhere in
        the workbook to follow the moved column -- it does NOT rewrite a
        hardcoded integer argument inside a formula (e.g. a VLOOKUP
        index), which still needs `sheet_pool_formulas.py`'s own
        regeneration step.
        """
        sheet, ws = self._ws(tab_name)
        destination_index = to_index + 1 if to_index > from_index else to_index
        sheet.batch_update(
            {
                "requests": [
                    {
                        "moveDimension": {
                            "source": {
                                "sheetId": ws.id,
                                "dimension": "COLUMNS",
                                "startIndex": from_index,
                                "endIndex": from_index + 1,
                            },
                            "destinationIndex": destination_index,
                        }
                    }
                ]
            }
        )

    def get_grouped_column_indices(self, tab_name: str) -> set[int]:
        """Every 0-indexed column currently covered by ANY column group on
        `tab_name`, collapsed or not. Used to tell "this column is
        deliberately hidden as part of a collapsed group" apart from "this
        column is stray-hidden and shouldn't be" -- a blanket unhide (see
        `polish_edge`/`polish_builder_tab`'s own reset-before-hide fix)
        must skip the first kind or it desyncs the group: verified live
        that explicitly unhiding a grouped range's columns makes them
        visible while the group's own metadata still reports
        `collapsed: true`, so the UI shows an expand control for a group
        that's already expanded."""
        sheet, ws = self._ws(tab_name)
        meta = sheet.fetch_sheet_metadata(params={"fields": "sheets(properties(sheetId),columnGroups)"})
        indices: set[int] = set()
        for s in meta.get("sheets", []):
            if s.get("properties", {}).get("sheetId") == ws.id:
                for group in s.get("columnGroups", []) or []:
                    rng = group["range"]
                    indices.update(range(rng["startIndex"], rng["endIndex"]))
                break
        return indices

    def clear_column_groups(self, tab_name: str) -> None:
        """Delete every existing column group on a tab before re-adding one
        with `group_columns` -- without this, `addDimensionGroup` doesn't
        replace an existing group over the same range, it stacks a new,
        deeper nested one on top (Sheets caps nesting at depth 8, which is
        exactly what a `dfs setup polish` re-run without this ended up
        doing for real: 8 identical nested groups over EdgeRaw's Id column
        alone, visible in the UI as a wall of collapse controls above the
        header with no way to tell they're all the same group). One
        `deleteDimensionGroup` call per depth level actually present,
        read from the sheet's own metadata rather than assumed."""
        sheet, ws = self._ws(tab_name)
        meta = sheet.fetch_sheet_metadata(params={"fields": "sheets(properties(sheetId),columnGroups)"})
        groups = []
        for s in meta.get("sheets", []):
            if s.get("properties", {}).get("sheetId") == ws.id:
                groups = s.get("columnGroups", []) or []
                break
        if not groups:
            return
        requests = [{"deleteDimensionGroup": {"range": group["range"]}} for group in groups]
        sheet.batch_update({"requests": requests})

    def group_columns(
        self, tab_name: str, first_col_a1: str, last_col_a1: str, *, collapsed: bool = False
    ) -> None:
        """Group a column range so it can be collapsed/expanded from the
        sheet UI (Data > Group columns) -- a display convenience only, does
        not touch cell values or formatting. `first_col_a1`/`last_col_a1`
        are plain column letters (e.g. "P", "Y"), not full A1 refs.

        Does not replace an existing group over the same range -- it nests
        a new, deeper one on top (see `clear_column_groups`). Callers that
        re-run this on every `polish` pass must call `clear_column_groups`
        first.

        `collapsed=True` (Fix 2.9) also folds the group shut immediately --
        `addDimensionGroup` alone only creates the +/- control, still
        expanded, so a column meant to default to hidden (e.g. weather on
        Lineups/Player Pool) would otherwise sit open until someone clicks
        it once by hand. `updateDimensionGroup` requires `depth` to say
        *which* nested group to fold (Sheets rejected a request without
        one: "dimensionGroup.depth must be > 0") -- hardcoded to 1 here,
        correct as long as the caller clears any existing group over this
        exact range first (both current call sites do), so the group this
        just added is the only, outermost one."""
        sheet, ws = self._ws(tab_name)
        grid_range = a1_range_to_grid_range(f"{first_col_a1}1:{last_col_a1}1", ws.id)
        dimension_range = {
            "sheetId": ws.id,
            "dimension": "COLUMNS",
            "startIndex": grid_range["startColumnIndex"],
            "endIndex": grid_range["endColumnIndex"],
        }
        requests = [{"addDimensionGroup": {"range": dimension_range}}]
        if collapsed:
            requests.append(
                {
                    "updateDimensionGroup": {
                        "dimensionGroup": {"range": dimension_range, "depth": 1, "collapsed": True},
                        "fields": "collapsed",
                    }
                }
            )
        sheet.batch_update({"requests": requests})

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
        every tab in the sheet, not just `tab_name` -- `dfs setup polish`
        calls this once per tab it might style, so by the second call the
        rest of the sheet's tabs are already resolved for free."""
        sheet = self._open()
        found = False
        for ws in sheet.worksheets():
            self._ws_cache[ws.title] = ws
            if ws.title == tab_name:
                found = True
        return found

    def ensure_column_capacity(self, tab_name: str, min_cols: int) -> None:
        """Grows `tab_name`'s actual grid (via gspread's `add_cols`, an
        `appendDimension` request) to at least `min_cols` columns -- a
        no-op if it's already wide enough. A tab's provisioned grid size
        is independent of what's actually written into it; appending a
        genuinely new column past the current width (`provision_missing_
        columns`' fallback path) writes into a column the grid may not
        have yet, which Sheets rejects outright ("exceeds grid limits")
        rather than silently growing to fit -- found live provisioning
        Player Pool's "Edge ↗" column past its 37-column grid."""
        _, ws = self._ws(tab_name)
        if ws.col_count < min_cols:
            ws.add_cols(min_cols - ws.col_count)

    def tab_gid(self, tab_name: str) -> int:
        """The tab's stable `sheetId` (what Sheets calls a "gid" in URLs) --
        for building a same-spreadsheet `HYPERLINK("#gid=...&range=...")`
        formula (A3's "Edge ↗" column). Stable for the tab's lifetime;
        only changes if the tab is deleted and recreated."""
        _, ws = self._ws(tab_name)
        return ws.id

    def delete_tab(self, tab_name: str) -> None:
        """Permanently deletes `tab_name` -- a one-time tab retirement (A3:
        Pool Picks absorbed into Player Pool), never part of a regular
        sync/polish path. No-op if the tab is already gone, so it's safe
        to re-run."""
        if not self.tab_exists(tab_name):
            return
        sheet, ws = self._ws(tab_name)
        sheet.del_worksheet(ws)
        self._ws_cache.pop(tab_name, None)

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

    def hide_columns(
        self, tab_name: str, first_col_a1: str, last_col_a1: str, *, hidden: bool = True
    ) -> None:
        """Hide (or unhide) a column range from the sheet UI -- a real
        Sheets hide (`hiddenByUser`), not a zero-width column: still fully
        readable/writable through the API, same as `set_tab_properties`'
        tab-level `hidden`. Unlike a column *group* (`group_columns`),
        there's no expand control left in the UI at all."""
        sheet, ws = self._ws(tab_name)
        grid_range = a1_range_to_grid_range(f"{first_col_a1}1:{last_col_a1}1", ws.id)
        sheet.batch_update(
            {
                "requests": [
                    {
                        "updateDimensionProperties": {
                            "range": {
                                "sheetId": ws.id,
                                "dimension": "COLUMNS",
                                "startIndex": grid_range["startColumnIndex"],
                                "endIndex": grid_range["endColumnIndex"],
                            },
                            "properties": {"hiddenByUser": hidden},
                            "fields": "hiddenByUser",
                        }
                    }
                ]
            }
        )

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

    def set_number_range_validation(
        self, tab_name: str, a1_range: str, *, minimum: int, maximum: int, strict: bool = False
    ) -> None:
        """Restrict `a1_range` to a number between `minimum` and `maximum`
        (Sheets' NUMBER_BETWEEN data validation). `strict=False` (the
        default) warns rather than rejects a value outside the range --
        matching `set_range_dropdown_validation`'s reasoning: a caller that
        clamps this value in its own formula (rather than trusting the cell
        is always in-range) should let an out-of-range typed value stay
        editable and visible, not get silently rejected."""
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
                                    "type": "NUMBER_BETWEEN",
                                    "values": [
                                        {"userEnteredValue": str(minimum)},
                                        {"userEnteredValue": str(maximum)},
                                    ],
                                },
                                "strict": strict,
                            },
                        }
                    }
                ]
            }
        )

    def set_range_dropdown_validation(
        self, tab_name: str, a1_range: str, *, source: str, strict: bool = False
    ) -> None:
        """Restrict `a1_range` to values found in `source` (a full A1
        reference like `"EdgeRaw!$C$2:$C$1000"`) -- Sheets' ONE_OF_RANGE
        data validation. Unlike `set_dropdown_validation`'s fixed list,
        this renders a live type-ahead search box against whatever's
        actually in that range right now, with no hardcoded option list
        to keep in sync as the source tab's rows change week to week.
        `strict=False` (the default here, opposite of
        `set_dropdown_validation`'s) warns rather than rejects a value
        outside the range -- a name that doesn't match yet (not on this
        week's slate, a typo) should stay editable, not get locked out."""
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
                                    "type": "ONE_OF_RANGE",
                                    "values": [{"userEnteredValue": f"={source}"}],
                                },
                                "showCustomUi": True,
                                "strict": strict,
                            },
                        }
                    }
                ]
            }
        )

    def set_checkbox_validation(self, tab_name: str, a1_range: str) -> None:
        """Restrict `a1_range` to a real Sheets checkbox (`BOOLEAN` data
        validation) -- renders as a clickable checkbox and round-trips
        TRUE/FALSE. Per lesson learned this session: the Sheets API accepts
        (and silently no-ops on) a malformed validation rule just as easily
        as a correct one, so a caller relying on this for a hard guarantee
        (e.g. EdgeRaw's Pool column driving Player Pool's formulas) should
        still read the rule back and/or have it confirmed in the browser --
        don't just trust that this call didn't raise."""
        sheet, ws = self._ws(tab_name)
        grid_range = a1_range_to_grid_range(a1_range, ws.id)
        sheet.batch_update(
            {
                "requests": [
                    {
                        "setDataValidation": {
                            "range": grid_range,
                            "rule": {
                                "condition": {"type": "BOOLEAN"},
                                "strict": True,
                            },
                        }
                    }
                ]
            }
        )

    def clear_data_validation(self, tab_name: str, a1_range: str) -> None:
        """Remove any data-validation rule from `a1_range` (a `setDataValidation`
        request with no `rule` clears whatever's there). Needed alongside
        `insert_rows`' formatting reset for the same reason: a new row
        inserted at the top inherits not just the fill/text color of the
        row pushed below it, but any data-validation rule on it too. A
        Lineups column A that restricts entries to a real player name
        (looked up against PlayerPoolRaw) is exactly the kind of rule
        that would otherwise silently follow every inserted row into a
        zone meant to hold something else entirely -- caught only when a
        person tried to type into one of those cells in the browser and
        got rejected; a script-driven write doesn't enforce `strict` the
        way the interactive UI does, so this was invisible to every
        API-level check. See CONTRIBUTING.md's changelog.
        """
        sheet, ws = self._ws(tab_name)
        grid_range = a1_range_to_grid_range(a1_range, ws.id)
        sheet.batch_update({"requests": [{"setDataValidation": {"range": grid_range}}]})

    def clear_filter_view(self, tab_name: str, title: str) -> None:
        """Delete any existing filter view on this tab with this exact
        title. Sheets doesn't replace a filter view by title on a second
        `addFilterView` call -- it adds a second view with the same name
        -- so a re-runnable caller must clear first, same clear-then-add
        shape as column groups/banding elsewhere in this file."""
        sheet, ws = self._ws(tab_name)
        meta = sheet.fetch_sheet_metadata(params={"fields": "sheets(properties(sheetId),filterViews)"})
        for s in meta.get("sheets", []):
            if s.get("properties", {}).get("sheetId") != ws.id:
                continue
            for fv in s.get("filterViews", []) or []:
                if fv.get("title") == title:
                    sheet.batch_update({"requests": [{"deleteFilterView": {"filterId": fv["filterViewId"]}}]})
            break

    def add_filter_view(
        self,
        tab_name: str,
        *,
        title: str,
        a1_range: str,
        criteria: dict[int, dict] | None = None,
    ) -> None:
        """Add a named, per-user filter view (Data > Filter views) over
        `a1_range`. Unlike the plain "Create a filter" button (one filter
        per sheet, visible and applied for every viewer, and destructive
        on a formula-driven tab whose cells a filter would reorder), a
        filter view sorts/filters within the view only -- the underlying
        cells, and any spilled-array formula among them, are untouched.

        `criteria` maps a 0-indexed column number to a Sheets
        `FilterCriteria` dict (e.g. `{5: {"condition": {"type": "TEXT_EQ",
        "values": [{"userEnteredValue": "LEVERAGE"}]}}}`) -- omit for a
        view that's just a sortable/filterable window with no preset.
        Callers that re-run this must call `clear_filter_view` with the
        same title first; this method only ever adds."""
        sheet, ws = self._ws(tab_name)
        grid_range = a1_range_to_grid_range(a1_range, ws.id)
        filter_view: dict = {"title": title, "range": grid_range}
        if criteria:
            filter_view["criteria"] = {str(col): crit for col, crit in criteria.items()}
        sheet.batch_update({"requests": [{"addFilterView": {"filter": filter_view}}]})

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

    def frozen_rows(self, tab_name: str) -> int:
        """How many rows are currently frozen -- the read-side counterpart
        to `freeze`, for a caller (`dfs setup audit-style`) that needs to
        check rather than set it."""
        _, ws = self._ws(tab_name)
        return ws.frozen_row_count

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
        rule = _boolean_rule(grid_range, condition_type=condition_type, values=values, fmt=fmt)
        sheet.batch_update({"requests": [{"addConditionalFormatRule": {"rule": rule, "index": 0}}]})

    def add_boolean_rules(self, tab_name: str, specs: list[dict]) -> None:
        """Same rule as `add_boolean_rule`, one call per item in `specs`
        (each keyed `a1_range`/`condition_type`/`values`/`fmt`), issued as
        ONE `batchUpdate` -- see `add_color_scales`' docstring for why
        (Phase 4's per-group zero-exclusion chips, one per scaled block,
        are exactly the same "many small rules from one polish run"
        shape)."""
        if not specs:
            return
        sheet, ws = self._ws(tab_name)
        requests = [
            {
                "addConditionalFormatRule": {
                    "rule": _boolean_rule(
                        a1_range_to_grid_range(spec["a1_range"], ws.id),
                        condition_type=spec["condition_type"],
                        values=spec["values"],
                        fmt=spec["fmt"],
                    ),
                    "index": 0,
                }
            }
            for spec in specs
        ]
        sheet.batch_update({"requests": requests})

    def clear_banding(self, tab_name: str) -> None:
        """Delete every existing banded range on a tab before re-adding one
        with `add_row_banding` -- Sheets rejects a new banded range that
        overlaps an existing one rather than replacing it, so a re-runnable
        caller must clear first, same pattern as `clear_column_groups`."""
        sheet, ws = self._ws(tab_name)
        meta = sheet.fetch_sheet_metadata(params={"fields": "sheets(properties(sheetId),bandedRanges)"})
        bandings = []
        for s in meta.get("sheets", []):
            if s.get("properties", {}).get("sheetId") == ws.id:
                bandings = s.get("bandedRanges", []) or []
                break
        if not bandings:
            return
        requests = [{"deleteBanding": {"bandedRangeId": b["bandedRangeId"]}} for b in bandings]
        sheet.batch_update({"requests": requests})

    def add_row_banding(
        self,
        tab_name: str,
        a1_range: str,
        *,
        first_band_color: dict,
        second_band_color: dict,
        header_color: dict | None = None,
    ) -> None:
        """Apply alternating row banding to `a1_range` (Sheets' own Data >
        Alternating colors, `addBanding`). Colors are {"red": .., "green":
        .., "blue": ..} floats in 0-1, same convention as `add_color_scale`.
        Callers that re-run this on every `polish` pass must call
        `clear_banding` first -- Sheets errors on an overlapping banded
        range rather than replacing it."""
        sheet, ws = self._ws(tab_name)
        grid_range = a1_range_to_grid_range(a1_range, ws.id)
        row_properties: dict = {
            "firstBandColor": first_band_color,
            "secondBandColor": second_band_color,
        }
        if header_color is not None:
            row_properties["headerColor"] = header_color
        sheet.batch_update(
            {
                "requests": [
                    {
                        "addBanding": {
                            "bandedRange": {
                                "range": grid_range,
                                "rowProperties": row_properties,
                            }
                        }
                    }
                ]
            }
        )

    def clear_conditional_formats(
        self,
        tab_name: str,
        *,
        column: str | None = None,
        row_range: tuple[int, int] | None = None,
    ) -> None:
        """Delete conditional-format rules on a tab. Needed to make the
        styling commands re-runnable -- without it, running twice stacks a
        second identical set of rules on top of the first, and Sheets
        applies the most recently added matching rule, so the tab slowly
        accumulates dead rules that are hard to reason about.

        With `column` and `row_range` both omitted, deletes every rule on
        the tab -- correct when one function owns all of a tab's
        conditional formatting (EdgeRaw, the four view tabs, Bankroll).
        Lineups is not one of those: `sheet_links.link_edge_columns` owns
        color-scale rules on its linked block's rows, so `polish_guardrails`
        passes `column="O"` alone to delete every rule confined entirely to
        that one column regardless of row (no other function ever touches
        O, and it spans many different row ranges -- one per lineup
        block -- so a single row_range can't cover them all), and
        `row_range` alone (no current caller -- the pool deck that used to
        pass it, e.g. `(4, 9)` for its own window rows, was removed
        entirely, Phase 5, 2026-09-16) deletes rules confined to that row
        band regardless of column; kept as a mode since a future narrow
        row-band owner could need it again, same reasoning `column` alone
        serves `polish_guardrails` today.

        Passing BOTH `column` and `row_range` together (as
        `apply_field_color_scales` does -- Fix A2) narrows to a rule
        matching both: found live as the actual cause of "highlighting
        missing from Lineups" -- the pool deck's own narrow window (rows
        4-9) used to clear by `column` ALONE before adding its own scale,
        which deleted the SAME column's real, already-correct gradient
        covering the 20 lineup blocks below (rows 12-268) moments after
        `polish_builder_tab` had just written it -- on every single
        `dfs setup polish` run, not a rare edge case. `column` alone or
        `row_range` alone keep their original (deliberately wider) meaning
        for every other caller; only a caller that passes both gets the
        narrower, exact-range behavior. Blindly clearing more than a
        function owns would silently destroy real, already-correct
        formatting on every re-run -- exactly the kind of mistake
        CONTRIBUTING.md's changelog now has an incident for.
        """
        sheet, ws = self._ws(tab_name)
        rules = self._conditional_format_rules(sheet, ws)
        if column is None and row_range is None:
            indexes = list(range(len(rules)))
        else:
            col_index = (
                a1_range_to_grid_range(f"{column}1:{column}1")["startColumnIndex"]
                if column is not None
                else None
            )
            row_bounds = (row_range[0] - 1, row_range[1]) if row_range is not None else None

            def _range_matches(r: dict) -> bool:
                if col_index is not None and not (
                    r.get("startColumnIndex") == col_index and r.get("endColumnIndex") == col_index + 1
                ):
                    return False
                if row_bounds is not None and not (
                    r.get("startRowIndex") == row_bounds[0] and r.get("endRowIndex") == row_bounds[1]
                ):
                    return False
                return True

            indexes = [
                i
                for i, rule in enumerate(rules)
                if rule.get("ranges") and all(_range_matches(r) for r in rule["ranges"])
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

    def has_chip_rule(self, tab_name: str, column_a1: str, values: list[str]) -> bool:
        """Whether `column_a1` carries at least one TEXT_EQ or
        TEXT_CONTAINS conditional-format rule matching one of `values` --
        used by `dfs setup audit-style` to check a Flag/Avail column
        actually has its chips, not just that some conditional format
        exists somewhere on the tab. Both condition types are accepted
        since Flag (Fix 2.1: can hold more than one space-separated token)
        uses TEXT_CONTAINS while Avail/Source/Venue still use TEXT_EQ."""
        sheet, ws = self._ws(tab_name)
        rules = self._conditional_format_rules(sheet, ws)
        col_index = a1_range_to_grid_range(f"{column_a1}1:{column_a1}1")["startColumnIndex"]
        for rule in rules:
            ranges = rule.get("ranges", [])
            if not any(r.get("startColumnIndex") == col_index for r in ranges):
                continue
            condition = rule.get("booleanRule", {}).get("condition", {})
            if condition.get("type") not in ("TEXT_EQ", "TEXT_CONTAINS"):
                continue
            rule_values = [v.get("userEnteredValue") for v in condition.get("values", [])]
            if any(v in values for v in rule_values):
                return True
        return False

    def clear_protected_ranges(self, tab_name: str) -> None:
        """Delete every existing protected range on this tab, so
        `protect_sheet` is re-runnable without stacking duplicate
        protections (each one would show its own redundant warning
        dialog on the same edit) -- same clear-then-add shape as column
        groups/banding/filter views elsewhere in this file."""
        sheet, ws = self._ws(tab_name)
        meta = sheet.fetch_sheet_metadata(params={"fields": "sheets(properties(sheetId),protectedRanges)"})
        for s in meta.get("sheets", []):
            if s.get("properties", {}).get("sheetId") != ws.id:
                continue
            requests = [
                {"deleteProtectedRange": {"protectedRangeId": pr["protectedRangeId"]}}
                for pr in s.get("protectedRanges", []) or []
            ]
            if requests:
                sheet.batch_update({"requests": requests})
            break

    def protect_sheet(
        self,
        tab_name: str,
        *,
        warning_only: bool = True,
        unprotected_ranges: list[str] | None = None,
        description: str | None = None,
    ) -> None:
        """Protect the ENTIRE tab (Sheets' own "Protect sheet... except
        certain cells" mechanism) -- `unprotectedRanges` is only honored
        by the API when a protected range spans a whole sheet, which
        this always does, so it's the only way to say "protect
        everything except these specific cells" in one rule rather than
        constructing the geometric complement by hand.

        `warning_only=True` (the default, and what this project always
        wants) shows a dismissible "you're editing a protected range"
        warning rather than a hard lock -- a mistake stays reversible,
        it just isn't silent. A warning-only protection never blocks an
        editor (including this client's own service account), so it's
        safe to leave on every formula-driven tab without risking a
        future `dfs sync`/`dfs setup polish` write being rejected."""
        sheet, ws = self._ws(tab_name)
        protected_range: dict = {"range": {"sheetId": ws.id}, "warningOnly": warning_only}
        if description:
            protected_range["description"] = description
        if unprotected_ranges:
            protected_range["unprotectedRanges"] = [
                a1_range_to_grid_range(r, ws.id) for r in unprotected_ranges
            ]
        sheet.batch_update({"requests": [{"addProtectedRange": {"protectedRange": protected_range}}]})

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

    def set_basic_filter(self, tab_name: str, a1_range: str) -> None:
        """Data > Create a filter over `a1_range` -- a visible dropdown
        arrow in every header cell of the range, the discoverable sort/
        search mechanism filter views (see `add_filter_view`) don't give
        you without opening Data > Filter views first. Only ever safe on a
        tab of plain values (see `sheet_filters.py`'s own callers for
        which); a basic filter physically reorders the tab's stored rows
        for every viewer, which would corrupt a positional block.

        A sheet can have at most one basic filter, and `setBasicFilter`
        always replaces whatever's there -- unlike filter views/banding/
        column groups elsewhere in this file, no separate clear-then-add
        is needed for this to be re-runnable."""
        sheet, ws = self._ws(tab_name)
        grid_range = a1_range_to_grid_range(a1_range, ws.id)
        sheet.batch_update({"requests": [{"setBasicFilter": {"filter": {"range": grid_range}}}]})

    def clear_basic_filter(self, tab_name: str) -> None:
        """Remove a tab's basic filter entirely, if it has one."""
        sheet, ws = self._ws(tab_name)
        sheet.batch_update({"requests": [{"clearBasicFilter": {"sheetId": ws.id}}]})

    def set_note(self, tab_name: str, cell_a1: str, note: str) -> None:
        """Attach a cell note (Insert > Note) -- pure metadata: no cell
        value, formula or format is touched, so this is safe on a typed
        cell (EdgeRaw's Pool header) as readily as a computed one."""
        sheet, ws = self._ws(tab_name)
        grid_range = a1_range_to_grid_range(cell_a1, ws.id)
        sheet.batch_update(
            {
                "requests": [
                    {
                        "updateCells": {
                            "range": grid_range,
                            "rows": [{"values": [{"note": note}]}],
                            "fields": "note",
                        }
                    }
                ]
            }
        )
