"""Offline tests for SheetsClient, using a fake gspread backend."""

from __future__ import annotations

import gspread
import pytest
from gspread.utils import a1_range_to_grid_range

from dfs.config import GoogleSheetsConfig
from dfs.sheets import SheetsClient, SheetsError


class FakeWorksheet:
    """A grid-backed fake. Deliberately simpler than real Sheets: `get()`
    returns the exact rectangle requested (no trimming of trailing blank
    rows/columns the way gspread does), which is fine since our code never
    relies on that trimming -- callers always know the shape they wrote."""

    _next_id = 1

    def __init__(self, title: str, rows: list[list[str]] | None = None):
        self.title = title
        self.id = FakeWorksheet._next_id
        FakeWorksheet._next_id += 1
        self.row_count = 1000
        self.col_count = 26
        self._rows: list[list[str]] = [list(r) for r in (rows or [])]
        self.frozen_rows = 0
        self.color_scale_calls: list[dict] = []
        self.dimension_group_calls: list[dict] = []
        self.dimension_group_update_calls: list[dict] = []
        self.column_groups: list[dict] = []
        self.insert_dimension_calls: list[dict] = []
        self.delete_dimension_calls: list[dict] = []
        self.move_dimension_calls: list[dict] = []
        self.conditional_formats: list[dict] = []
        self.data_validation_calls: list[dict] = []
        self.filter_views: list[dict] = []
        self.protected_ranges: list[dict] = []
        self.basic_filter: dict | None = None
        self.notes: dict[tuple[int, int], str] = {}

    def _cell(self, row: int, col: int) -> str:
        if row - 1 < len(self._rows) and col - 1 < len(self._rows[row - 1]):
            return self._rows[row - 1][col - 1]
        return ""

    def row_values(self, n: int) -> list[str]:
        idx = n - 1
        return self._rows[idx] if idx < len(self._rows) else []

    def get_all_values(self) -> list[list[str]]:
        return self._rows

    def get(self, a1_range: str, value_render_option=None) -> list[list[str]]:
        grid = a1_range_to_grid_range(a1_range)
        row_start = grid.get("startRowIndex", 0)
        row_end = grid.get("endRowIndex", len(self._rows))
        col_start = grid.get("startColumnIndex", 0)
        col_end = grid.get("endColumnIndex", max((len(r) for r in self._rows), default=0))
        return [
            [self._cell(r + 1, c + 1) for c in range(col_start, col_end)] for r in range(row_start, row_end)
        ]

    def clear(self) -> None:
        self._rows = []

    def batch_clear(self, a1_ranges: list[str]) -> None:
        for a1_range in a1_ranges:
            grid = a1_range_to_grid_range(a1_range)
            row_start = grid.get("startRowIndex", 0)
            row_end = grid.get("endRowIndex", len(self._rows))
            col_start = grid.get("startColumnIndex", 0)
            col_end = grid.get("endColumnIndex", col_start + 1)
            for r in range(row_start, min(row_end, len(self._rows))):
                for c in range(col_start, min(col_end, len(self._rows[r]))):
                    self._rows[r][c] = ""

    def update(self, range_name: str, values, value_input_option=None) -> None:
        if range_name == "A1" and len(self._rows) <= len(values):
            # Whole-tab overwrite (write_tab's usage pattern).
            self._rows = [list(map(str, row)) for row in values]
            return
        grid = a1_range_to_grid_range(range_name)
        row_start = grid.get("startRowIndex", 0)
        col_start = grid.get("startColumnIndex", 0)
        needed_rows = row_start + len(values)
        while len(self._rows) < needed_rows:
            self._rows.append([])
        for i, row_values in enumerate(values):
            row = self._rows[row_start + i]
            needed_cols = col_start + len(row_values)
            if len(row) < needed_cols:
                row.extend([""] * (needed_cols - len(row)))
            for j, value in enumerate(row_values):
                row[col_start + j] = str(value)

    def format(self, a1_range: str, fmt: dict) -> None:
        pass

    def add_cols(self, n: int) -> None:
        self.col_count += n

    def freeze(self, rows: int | None = None, cols: int | None = None) -> None:
        if rows is not None:
            self.frozen_rows = rows

    @property
    def frozen_row_count(self) -> int:
        """Real gspread's actual attribute name for this -- `list_tabs`
        reads it directly, so the fake must expose the same name, not just
        the `frozen_rows` this file already tracked internally."""
        return self.frozen_rows


class FakeSpreadsheet:
    def __init__(self):
        self._worksheets: dict[str, FakeWorksheet] = {}
        self.batch_update_calls: list[dict] = []
        self.worksheet_lookup_calls: list[str] = []
        self._next_filter_id = 0
        self._next_protection_id = 0

    def worksheets(self) -> list[FakeWorksheet]:
        return list(self._worksheets.values())

    def worksheet(self, title: str) -> FakeWorksheet:
        # Real gspread re-fetches the whole spreadsheet's metadata here --
        # this counter is what test_ws_caches_a_tab_across_calls uses to
        # pin that SheetsClient stops doing that after the first lookup.
        self.worksheet_lookup_calls.append(title)
        try:
            return self._worksheets[title]
        except KeyError:
            raise gspread.WorksheetNotFound(title) from None

    def values_batch_get(self, ranges: list[str]) -> dict:
        value_ranges = []
        for full_range in ranges:
            tab_part, _, a1_part = full_range.rpartition("!")
            title = tab_part.strip("'")
            ws = self.worksheet(title)
            value_ranges.append({"range": full_range, "values": ws.get(a1_part)})
        return {"valueRanges": value_ranges}

    def add_worksheet(self, title: str, rows: int, cols: int) -> FakeWorksheet:
        ws = FakeWorksheet(title)
        self._worksheets[title] = ws
        return ws

    def del_worksheet(self, ws: FakeWorksheet) -> None:
        del self._worksheets[ws.title]

    def batch_update(self, body: dict) -> None:
        self.batch_update_calls.append(body)
        for request in body.get("requests", []):
            if "addConditionalFormatRule" in request:
                rule = request["addConditionalFormatRule"]["rule"]
                ws = self._ws_by_id(rule["ranges"][0]["sheetId"])
                ws.color_scale_calls.append(request["addConditionalFormatRule"])
                # Real Sheets always inserts at the given index (0 here,
                # per add_color_scale/add_boolean_rule), pushing existing
                # rules down -- matching that is what let the corruption
                # incident's repair correctly reconstruct which rules were
                # the newly-added ones by index.
                ws.conditional_formats.insert(request["addConditionalFormatRule"]["index"], rule)
            if "deleteConditionalFormatRule" in request:
                d = request["deleteConditionalFormatRule"]
                del self._ws_by_id(d["sheetId"]).conditional_formats[d["index"]]
            if "addDimensionGroup" in request:
                rng = request["addDimensionGroup"]["range"]
                ws = self._ws_by_id(rng["sheetId"])
                ws.dimension_group_calls.append(request["addDimensionGroup"])
                depth = 1 + sum(1 for g in ws.column_groups if g["range"] == rng)
                ws.column_groups.append({"range": rng, "depth": depth, "collapsed": False})
            if "updateDimensionGroup" in request:
                update = request["updateDimensionGroup"]
                # Real Sheets rejects this request with no `depth` (or
                # depth <= 0): "dimensionGroup.depth must be > 0" -- found
                # live, modeled here so a regression fails a test instead
                # of a real sheet.
                if not update["dimensionGroup"].get("depth", 0) > 0:
                    raise AssertionError("updateDimensionGroup requires dimensionGroup.depth > 0")
                rng = update["dimensionGroup"]["range"]
                ws = self._ws_by_id(rng["sheetId"])
                ws.dimension_group_update_calls.append(update)
                matches = [g for g in ws.column_groups if g["range"] == rng]
                if matches:
                    deepest = max(matches, key=lambda g: g["depth"])
                    deepest["collapsed"] = update["dimensionGroup"]["collapsed"]
            if "deleteDimensionGroup" in request:
                rng = request["deleteDimensionGroup"]["range"]
                ws = self._ws_by_id(rng["sheetId"])
                matches = [g for g in ws.column_groups if g["range"] == rng]
                if matches:
                    deepest = max(matches, key=lambda g: g["depth"])
                    ws.column_groups.remove(deepest)
            if "insertDimension" in request:
                sheet_id = request["insertDimension"]["range"]["sheetId"]
                self._ws_by_id(sheet_id).insert_dimension_calls.append(request["insertDimension"])
            if "moveDimension" in request:
                move = request["moveDimension"]
                sheet_id = move["source"]["sheetId"]
                ws = self._ws_by_id(sheet_id)
                ws.move_dimension_calls.append(move)
                # Real column-shift semantics, empirically verified against
                # a live scratch tab before being trusted here (Phase 3):
                # destinationIndex is in the PRE-removal index space.
                start, end = move["source"]["startIndex"], move["source"]["endIndex"]
                dest = move["destinationIndex"]
                insert_at = dest if dest <= start else dest - (end - start)
                for row in ws._rows:
                    if len(row) < end:
                        continue
                    moved = row[start:end]
                    del row[start:end]
                    row[insert_at:insert_at] = moved
            if "deleteDimension" in request:
                sheet_id = request["deleteDimension"]["range"]["sheetId"]
                self._ws_by_id(sheet_id).delete_dimension_calls.append(request["deleteDimension"])
            if "setDataValidation" in request:
                sheet_id = request["setDataValidation"]["range"]["sheetId"]
                self._ws_by_id(sheet_id).data_validation_calls.append(request["setDataValidation"])
            if "addFilterView" in request:
                fv = dict(request["addFilterView"]["filter"])
                ws = self._ws_by_id(fv["range"]["sheetId"])
                self._next_filter_id += 1
                fv["filterViewId"] = self._next_filter_id
                ws.filter_views.append(fv)
            if "deleteFilterView" in request:
                filter_id = request["deleteFilterView"]["filterId"]
                for ws in self._worksheets.values():
                    ws.filter_views = [fv for fv in ws.filter_views if fv["filterViewId"] != filter_id]
            if "setBasicFilter" in request:
                rng = request["setBasicFilter"]["filter"]["range"]
                self._ws_by_id(rng["sheetId"]).basic_filter = rng
            if "clearBasicFilter" in request:
                self._ws_by_id(request["clearBasicFilter"]["sheetId"]).basic_filter = None
            if "updateCells" in request:
                upd = request["updateCells"]
                rng = upd["range"]
                ws = self._ws_by_id(rng["sheetId"])
                note = upd["rows"][0]["values"][0].get("note")
                ws.notes[(rng["startRowIndex"], rng["startColumnIndex"])] = note
            if "addProtectedRange" in request:
                pr = dict(request["addProtectedRange"]["protectedRange"])
                ws = self._ws_by_id(pr["range"]["sheetId"])
                self._next_protection_id += 1
                pr["protectedRangeId"] = self._next_protection_id
                ws.protected_ranges.append(pr)
            if "deleteProtectedRange" in request:
                protected_id = request["deleteProtectedRange"]["protectedRangeId"]
                for ws in self._worksheets.values():
                    ws.protected_ranges = [
                        pr for pr in ws.protected_ranges if pr["protectedRangeId"] != protected_id
                    ]

    def _ws_by_id(self, sheet_id: int) -> FakeWorksheet:
        return next(ws for ws in self._worksheets.values() if ws.id == sheet_id)

    def fetch_sheet_metadata(self, params: dict | None = None) -> dict:
        return {
            "sheets": [
                {
                    "properties": {"sheetId": ws.id},
                    "conditionalFormats": ws.conditional_formats,
                    "columnGroups": [{"range": g["range"], "depth": g["depth"]} for g in ws.column_groups],
                    "filterViews": ws.filter_views,
                    "protectedRanges": ws.protected_ranges,
                }
                for ws in self._worksheets.values()
            ]
        }


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
    monkeypatch.setattr(
        "gspread.service_account",
        lambda filename, **_kwargs: type("C", (), {"open_by_key": lambda self, key: fake_sheet})(),
    )

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
    fake_sheet._worksheets["Projections"] = FakeWorksheet("Projections", rows=[["Id", "Name", "Position"]])
    fake_sheet._worksheets["Projections"].frozen_rows = 3
    tabs = client.list_tabs()
    assert len(tabs) == 1
    assert tabs[0].title == "Projections"
    assert tabs[0].header == ["Id", "Name", "Position"]
    assert tabs[0].frozen_rows == 3


def test_tab_gid_returns_the_worksheet_id(cfg, monkeypatch, tmp_path):
    client, fake_sheet = _client_with_fake_sheet(cfg, monkeypatch, tmp_path)
    fake_sheet._worksheets["EdgeRaw"] = FakeWorksheet("EdgeRaw")
    assert client.tab_gid("EdgeRaw") == fake_sheet._worksheets["EdgeRaw"].id


def test_delete_tab_removes_the_worksheet(cfg, monkeypatch, tmp_path):
    client, fake_sheet = _client_with_fake_sheet(cfg, monkeypatch, tmp_path)
    fake_sheet._worksheets["Pool Picks"] = FakeWorksheet("Pool Picks")
    client.delete_tab("Pool Picks")
    assert "Pool Picks" not in fake_sheet._worksheets
    assert client.tab_exists("Pool Picks") is False


def test_delete_tab_is_a_noop_when_already_gone(cfg, monkeypatch, tmp_path):
    client, fake_sheet = _client_with_fake_sheet(cfg, monkeypatch, tmp_path)
    client.delete_tab("Never Existed")  # must not raise
    assert fake_sheet._worksheets == {}


def test_ensure_column_capacity_grows_a_too_narrow_grid(cfg, monkeypatch, tmp_path):
    client, fake_sheet = _client_with_fake_sheet(cfg, monkeypatch, tmp_path)
    ws = FakeWorksheet("Player Pool")
    ws.col_count = 37
    fake_sheet._worksheets["Player Pool"] = ws

    client.ensure_column_capacity("Player Pool", 38)
    assert ws.col_count == 38


def test_ensure_column_capacity_is_a_noop_when_already_wide_enough(cfg, monkeypatch, tmp_path):
    client, fake_sheet = _client_with_fake_sheet(cfg, monkeypatch, tmp_path)
    ws = FakeWorksheet("Player Pool")
    ws.col_count = 40
    fake_sheet._worksheets["Player Pool"] = ws

    client.ensure_column_capacity("Player Pool", 38)
    assert ws.col_count == 40


def test_read_range_returns_only_the_requested_rectangle(cfg, monkeypatch, tmp_path):
    client, fake_sheet = _client_with_fake_sheet(cfg, monkeypatch, tmp_path)
    fake_sheet._worksheets["T"] = FakeWorksheet("T", rows=[["A", "B", "C"], ["1", "2", "3"], ["4", "5", "6"]])
    assert client.read_range("T", "B1:C2") == [["B", "C"], ["2", "3"]]


def test_read_formula_returns_the_requested_rectangle(cfg, monkeypatch, tmp_path):
    client, fake_sheet = _client_with_fake_sheet(cfg, monkeypatch, tmp_path)
    fake_sheet._worksheets["T"] = FakeWorksheet("T", rows=[["=SUM(A1:A2)", "plain"]])
    assert client.read_formula("T", "A1:B1") == [["=SUM(A1:A2)", "plain"]]


def test_read_range_unformatted_returns_the_requested_rectangle(cfg, monkeypatch, tmp_path):
    client, fake_sheet = _client_with_fake_sheet(cfg, monkeypatch, tmp_path)
    fake_sheet._worksheets["T"] = FakeWorksheet("T", rows=[["A", "B"], [44132966.0, "GPP"]])
    assert client.read_range_unformatted("T", "A2:B2") == [[44132966.0, "GPP"]]


def test_batch_read_ranges_reads_multiple_tabs_in_request_order(cfg, monkeypatch, tmp_path):
    client, fake_sheet = _client_with_fake_sheet(cfg, monkeypatch, tmp_path)
    fake_sheet._worksheets["T1"] = FakeWorksheet("T1", rows=[["a"], ["b"]])
    fake_sheet._worksheets["T2"] = FakeWorksheet("T2", rows=[["1", "2"]])
    result = client.batch_read_ranges([("T2", "A1:B1"), ("T1", "A1:A2")])
    assert result == [[["1", "2"]], [["a"], ["b"]]]


def test_update_range_does_not_touch_cells_outside_the_range(cfg, monkeypatch, tmp_path):
    client, fake_sheet = _client_with_fake_sheet(cfg, monkeypatch, tmp_path)
    fake_sheet._worksheets["T"] = FakeWorksheet("T", rows=[["keep", "keep", "keep"]])
    client.update_range("T", "C1:C1", [["new"]])
    assert fake_sheet._worksheets["T"].row_values(1) == ["keep", "keep", "new"]


def test_clear_ranges_only_clears_given_cells(cfg, monkeypatch, tmp_path):
    client, fake_sheet = _client_with_fake_sheet(cfg, monkeypatch, tmp_path)
    fake_sheet._worksheets["T"] = FakeWorksheet("T", rows=[["keep", "wipe"], ["keep2", "wipe2"]])
    client.clear_ranges("T", ["B1:B2"])
    assert fake_sheet._worksheets["T"].get_all_values() == [["keep", ""], ["keep2", ""]]


def test_freeze_header_sets_frozen_rows(cfg, monkeypatch, tmp_path):
    client, fake_sheet = _client_with_fake_sheet(cfg, monkeypatch, tmp_path)
    fake_sheet._worksheets["T"] = FakeWorksheet("T", rows=[["h"]])
    client.freeze_header("T", rows=1)
    assert fake_sheet._worksheets["T"].frozen_rows == 1


def test_add_color_scale_targets_only_the_given_range(cfg, monkeypatch, tmp_path):
    client, fake_sheet = _client_with_fake_sheet(cfg, monkeypatch, tmp_path)
    fake_sheet._worksheets["T"] = FakeWorksheet("T", rows=[["h"]])
    client.add_color_scale(
        "T",
        "M2:M100",
        min_color={"red": 1, "green": 0, "blue": 0},
        mid_color={"red": 1, "green": 1, "blue": 0},
        max_color={"red": 0, "green": 1, "blue": 0},
    )
    ws = fake_sheet._worksheets["T"]
    assert len(ws.color_scale_calls) == 1
    grid_range = ws.color_scale_calls[0]["rule"]["ranges"][0]
    assert grid_range["startColumnIndex"] == 12  # column M, 0-indexed
    assert grid_range["startRowIndex"] == 1  # row 2, 0-indexed


def test_add_color_scales_issues_one_batch_update_for_every_rule(cfg, monkeypatch, tmp_path):
    # Phase 4: apply_grouped_color_scales can generate hundreds of rules
    # (14 columns x 20 Lineups blocks) -- must be one HTTP round-trip,
    # not one per rule.
    client, fake_sheet = _client_with_fake_sheet(cfg, monkeypatch, tmp_path)
    fake_sheet._worksheets["T"] = FakeWorksheet("T", rows=[["h"]])
    specs = [
        {
            "a1_range": f"{col}2:{col}10",
            "min_color": {"red": 1, "green": 0, "blue": 0},
            "mid_color": {"red": 1, "green": 1, "blue": 0},
            "max_color": {"red": 0, "green": 1, "blue": 0},
        }
        for col in ("B", "C", "D")
    ]
    calls_before = len(fake_sheet.batch_update_calls)
    client.add_color_scales("T", specs)

    assert len(fake_sheet.batch_update_calls) == calls_before + 1
    ws = fake_sheet._worksheets["T"]
    assert len(ws.color_scale_calls) == 3


def test_add_color_scales_with_min_max_formulas_carries_them_through(cfg, monkeypatch, tmp_path):
    client, fake_sheet = _client_with_fake_sheet(cfg, monkeypatch, tmp_path)
    fake_sheet._worksheets["T"] = FakeWorksheet("T", rows=[["h"]])
    client.add_color_scales(
        "T",
        [
            {
                "a1_range": "B2:B10",
                "min_color": {"red": 1, "green": 0, "blue": 0},
                "mid_color": {"red": 1, "green": 1, "blue": 0},
                "max_color": {"red": 0, "green": 1, "blue": 0},
                "min_type": "NUMBER",
                "min_value": "=MIN('PoolSort'!$B$2:$B$80)",
                "max_type": "NUMBER",
                "max_value": "=MAX('PoolSort'!$B$2:$B$80)",
            }
        ],
    )
    rule = fake_sheet._worksheets["T"].color_scale_calls[0]["rule"]["gradientRule"]
    assert rule["minpoint"]["value"] == "=MIN('PoolSort'!$B$2:$B$80)"
    assert rule["maxpoint"]["value"] == "=MAX('PoolSort'!$B$2:$B$80)"


def test_add_color_scales_is_a_noop_on_an_empty_list(cfg, monkeypatch, tmp_path):
    client, fake_sheet = _client_with_fake_sheet(cfg, monkeypatch, tmp_path)
    fake_sheet._worksheets["T"] = FakeWorksheet("T", rows=[["h"]])
    calls_before = len(fake_sheet.batch_update_calls)
    client.add_color_scales("T", [])
    assert len(fake_sheet.batch_update_calls) == calls_before


def test_add_boolean_rules_issues_one_batch_update_for_every_rule(cfg, monkeypatch, tmp_path):
    client, fake_sheet = _client_with_fake_sheet(cfg, monkeypatch, tmp_path)
    fake_sheet._worksheets["T"] = FakeWorksheet("T", rows=[["h"]])
    specs = [
        {
            "a1_range": f"{col}2:{col}10",
            "condition_type": "NUMBER_EQ",
            "values": ["0"],
            "fmt": {"backgroundColor": {"red": 0.9, "green": 0.9, "blue": 0.9}},
        }
        for col in ("B", "C")
    ]
    calls_before = len(fake_sheet.batch_update_calls)
    client.add_boolean_rules("T", specs)

    assert len(fake_sheet.batch_update_calls) == calls_before + 1
    ws = fake_sheet._worksheets["T"]
    assert len(ws.color_scale_calls) == 2  # the fake tracks boolean rules on the same list


def test_add_boolean_rules_is_a_noop_on_an_empty_list(cfg, monkeypatch, tmp_path):
    client, fake_sheet = _client_with_fake_sheet(cfg, monkeypatch, tmp_path)
    fake_sheet._worksheets["T"] = FakeWorksheet("T", rows=[["h"]])
    calls_before = len(fake_sheet.batch_update_calls)
    client.add_boolean_rules("T", [])
    assert len(fake_sheet.batch_update_calls) == calls_before


def test_clear_conditional_formats_with_no_column_deletes_every_rule(cfg, monkeypatch, tmp_path):
    client, fake_sheet = _client_with_fake_sheet(cfg, monkeypatch, tmp_path)
    fake_sheet._worksheets["T"] = FakeWorksheet("T", rows=[["h"]])
    for col in ("B", "D", "F"):
        client.add_color_scale(
            "T",
            f"{col}2:{col}10",
            min_color={"red": 1, "green": 0, "blue": 0},
            mid_color={"red": 1, "green": 1, "blue": 0},
            max_color={"red": 0, "green": 1, "blue": 0},
        )

    client.clear_conditional_formats("T")

    assert fake_sheet._worksheets["T"].conditional_formats == []


def test_clear_conditional_formats_with_column_only_deletes_rules_confined_to_it(cfg, monkeypatch, tmp_path):
    # Lineups carries link_edge_columns' color scales on other columns
    # alongside Guardrails' own column-O rules -- clearing "O" must never
    # touch those other, unrelated rules. This is the regression for the
    # corruption incident: a blind clear-then-readd on a shared tab
    # silently destroys formatting nothing here is meant to touch.
    client, fake_sheet = _client_with_fake_sheet(cfg, monkeypatch, tmp_path)
    fake_sheet._worksheets["T"] = FakeWorksheet("T", rows=[["h"]])
    client.add_color_scale(
        "T",
        "Q2:Q10",
        min_color={"red": 1, "green": 0, "blue": 0},
        mid_color={"red": 1, "green": 1, "blue": 0},
        max_color={"red": 0, "green": 1, "blue": 0},
    )
    client.add_boolean_rule(
        "T", "O2:O10", condition_type="TEXT_EQ", values=["OK"], fmt={"backgroundColor": {"red": 0}}
    )
    client.add_boolean_rule(
        "T",
        "O2:O10",
        condition_type="TEXT_CONTAINS",
        values=["DUPLICATE"],
        fmt={"backgroundColor": {"red": 1}},
    )

    client.clear_conditional_formats("T", column="O")

    ws = fake_sheet._worksheets["T"]
    assert len(ws.conditional_formats) == 1
    remaining_range = ws.conditional_formats[0]["ranges"][0]
    assert remaining_range["startColumnIndex"] == 16  # column Q, 0-indexed -- the survivor


def test_clear_conditional_formats_with_row_range_finds_a_rule_regardless_of_column(
    cfg, monkeypatch, tmp_path
):
    # polish_pool_deck's own regression: an exact-range clear (column AND
    # row) failed to find its own rule once the column it lived on
    # changed between runs (header drift moved which column held a given
    # field), leaving the old rule orphaned. A row-range clear finds it
    # on whichever column it's actually on.
    client, fake_sheet = _client_with_fake_sheet(cfg, monkeypatch, tmp_path)
    fake_sheet._worksheets["T"] = FakeWorksheet("T", rows=[["h"]])
    client.add_color_scale(
        "T",
        "P4:P9",
        min_color={"red": 1, "green": 0, "blue": 0},
        mid_color={"red": 1, "green": 1, "blue": 0},
        max_color={"red": 0, "green": 1, "blue": 0},
    )
    # A same-column, different-row rule (e.g. the real lineup block's own
    # scale further down) must survive.
    client.add_color_scale(
        "T",
        "P12:P21",
        min_color={"red": 1, "green": 0, "blue": 0},
        mid_color={"red": 1, "green": 1, "blue": 0},
        max_color={"red": 0, "green": 1, "blue": 0},
    )

    client.clear_conditional_formats("T", row_range=(4, 9))

    ws = fake_sheet._worksheets["T"]
    assert len(ws.conditional_formats) == 1
    remaining_range = ws.conditional_formats[0]["ranges"][0]
    assert remaining_range["startRowIndex"] == 11  # row 12, 0-indexed -- the survivor


def test_clear_conditional_formats_with_column_and_row_range_only_matches_both(cfg, monkeypatch, tmp_path):
    # Fix A2, the actual root cause behind "highlighting missing from
    # Lineups": apply_field_color_scales used to clear by `column` alone
    # before adding its own scale. Called for the pool deck's own narrow
    # window (rows 4-9), that deleted the SAME column's already-correct
    # gradient covering the real lineup blocks below (rows 12-268) that
    # `polish_builder_tab` had just written moments earlier -- on every
    # single `dfs setup polish` run. Passing column AND row_range together
    # must narrow to a rule matching both, leaving a same-column,
    # different-row rule (or a different-column, same-row rule) alone.
    client, fake_sheet = _client_with_fake_sheet(cfg, monkeypatch, tmp_path)
    fake_sheet._worksheets["T"] = FakeWorksheet("T", rows=[["h"]])
    scale = dict(
        min_color={"red": 1, "green": 0, "blue": 0},
        mid_color={"red": 1, "green": 1, "blue": 0},
        max_color={"red": 0, "green": 1, "blue": 0},
    )
    client.add_color_scale("T", "P4:P9", **scale)  # the deck's own window -- about to be cleared
    client.add_color_scale("T", "P12:P268", **scale)  # same column, real blocks below -- must survive
    client.add_color_scale("T", "Q4:Q9", **scale)  # different column, same rows -- must survive

    client.clear_conditional_formats("T", column="P", row_range=(4, 9))

    ws = fake_sheet._worksheets["T"]
    assert len(ws.conditional_formats) == 2
    surviving_ranges = {
        (r["ranges"][0]["startColumnIndex"], r["ranges"][0]["startRowIndex"]) for r in ws.conditional_formats
    }
    assert surviving_ranges == {(15, 11), (16, 3)}  # P12 (col 15, row 11) and Q4 (col 16, row 3)


def test_move_columns_destination_index_rightward_move(cfg, monkeypatch, tmp_path):
    # Empirically verified against a live scratch tab (Phase 3): moving
    # column index 1 to index 3 in a 4-column row (["A","B","C","D"])
    # produces ["A","C","D","B"] -- destinationIndex must be to_index+1
    # for a rightward move (it's in the PRE-removal index space).
    client, fake_sheet = _client_with_fake_sheet(cfg, monkeypatch, tmp_path)
    fake_sheet._worksheets["T"] = FakeWorksheet("T", rows=[["A", "B", "C", "D"]])

    client.move_columns("T", from_index=1, to_index=3)

    ws = fake_sheet._worksheets["T"]
    assert ws.move_dimension_calls[0]["destinationIndex"] == 4
    assert ws.get("A1:D1")[0] == ["A", "C", "D", "B"]


def test_move_columns_destination_index_leftward_move(cfg, monkeypatch, tmp_path):
    # Same empirical check, leftward: destinationIndex equals to_index
    # unchanged when moving something earlier in the sheet.
    client, fake_sheet = _client_with_fake_sheet(cfg, monkeypatch, tmp_path)
    fake_sheet._worksheets["T"] = FakeWorksheet("T", rows=[["A", "C", "D", "B"]])

    client.move_columns("T", from_index=3, to_index=0)

    ws = fake_sheet._worksheets["T"]
    assert ws.move_dimension_calls[0]["destinationIndex"] == 0
    assert ws.get("A1:D1")[0] == ["B", "A", "C", "D"]


def test_move_columns_full_reorder_sequence_matches_compute_column_moves(cfg, monkeypatch, tmp_path):
    from dfs.column_reorder import compute_column_moves

    client, fake_sheet = _client_with_fake_sheet(cfg, monkeypatch, tmp_path)
    current = ["Name", "Pos", "Team", "Salary", "Pts", "Val"]
    fake_sheet._worksheets["T"] = FakeWorksheet("T", rows=[list(current)])
    target = ["Val", "Name", "Salary", "Pos", "Pts", "Team"]

    for from_i, to_i in compute_column_moves(current, target):
        client.move_columns("T", from_index=from_i, to_index=to_i)

    ws = fake_sheet._worksheets["T"]
    assert ws.get("A1:F1")[0] == target


def test_group_columns_groups_only_the_given_columns(cfg, monkeypatch, tmp_path):
    client, fake_sheet = _client_with_fake_sheet(cfg, monkeypatch, tmp_path)
    fake_sheet._worksheets["T"] = FakeWorksheet("T", rows=[["h"]])
    client.group_columns("T", "P", "Y")
    ws = fake_sheet._worksheets["T"]
    assert len(ws.dimension_group_calls) == 1
    r = ws.dimension_group_calls[0]["range"]
    assert (r["startIndex"], r["endIndex"]) == (15, 25)  # P..Y, 0-indexed half-open


def test_group_columns_collapsed_folds_the_group_shut(cfg, monkeypatch, tmp_path):
    # Fix 2.9: addDimensionGroup alone leaves the group expanded -- a
    # column meant to default to hidden (weather on Lineups/Player Pool)
    # needs the follow-up updateDimensionGroup this collapsed=True sends.
    client, fake_sheet = _client_with_fake_sheet(cfg, monkeypatch, tmp_path)
    fake_sheet._worksheets["T"] = FakeWorksheet("T", rows=[["h"]])
    client.group_columns("T", "P", "Y", collapsed=True)
    ws = fake_sheet._worksheets["T"]
    assert len(ws.dimension_group_update_calls) == 1
    # Real Sheets rejects this request without a `depth` -- caught live
    # ("dimensionGroup.depth must be > 0") because the fake didn't model
    # the requirement; asserted explicitly now so a regression fails here
    # instead of on a real sheet.
    assert ws.dimension_group_update_calls[0]["dimensionGroup"]["depth"] == 1
    assert ws.dimension_group_update_calls[0]["dimensionGroup"]["collapsed"] is True
    assert ws.column_groups[0]["collapsed"] is True


def test_group_columns_not_collapsed_by_default(cfg, monkeypatch, tmp_path):
    client, fake_sheet = _client_with_fake_sheet(cfg, monkeypatch, tmp_path)
    fake_sheet._worksheets["T"] = FakeWorksheet("T", rows=[["h"]])
    client.group_columns("T", "P", "Y")
    ws = fake_sheet._worksheets["T"]
    assert ws.dimension_group_update_calls == []
    assert ws.column_groups[0]["collapsed"] is False


def test_group_columns_stacks_a_deeper_group_on_repeat_calls(cfg, monkeypatch, tmp_path):
    # addDimensionGroup does not replace an existing group over the same
    # range -- it nests a new, deeper one on top. Real Sheets caps this at
    # depth 8; this pins that the fake (and therefore clear_column_groups,
    # tested below) models that stacking behaviour realistically.
    client, fake_sheet = _client_with_fake_sheet(cfg, monkeypatch, tmp_path)
    fake_sheet._worksheets["T"] = FakeWorksheet("T", rows=[["h"]])
    client.group_columns("T", "P", "Y")
    client.group_columns("T", "P", "Y")
    ws = fake_sheet._worksheets["T"]
    assert len(ws.dimension_group_calls) == 2
    assert [g["depth"] for g in ws.column_groups] == [1, 2]


def test_clear_column_groups_removes_every_stacked_level(cfg, monkeypatch, tmp_path):
    client, fake_sheet = _client_with_fake_sheet(cfg, monkeypatch, tmp_path)
    fake_sheet._worksheets["T"] = FakeWorksheet("T", rows=[["h"]])
    for _ in range(8):
        client.group_columns("T", "A", "A")
    ws = fake_sheet._worksheets["T"]
    assert len(ws.column_groups) == 8

    client.clear_column_groups("T")
    assert ws.column_groups == []


def test_clear_column_groups_is_a_no_op_when_nothing_is_grouped(cfg, monkeypatch, tmp_path):
    client, fake_sheet = _client_with_fake_sheet(cfg, monkeypatch, tmp_path)
    fake_sheet._worksheets["T"] = FakeWorksheet("T", rows=[["h"]])
    client.clear_column_groups("T")  # must not raise
    assert fake_sheet._worksheets["T"].column_groups == []


def test_hide_columns_sets_hidden_by_user_on_the_given_range(cfg, monkeypatch, tmp_path):
    client, fake_sheet = _client_with_fake_sheet(cfg, monkeypatch, tmp_path)
    fake_sheet._worksheets["T"] = FakeWorksheet("T", rows=[["h"]])

    client.hide_columns("T", "B", "B")

    request = fake_sheet.batch_update_calls[-1]["requests"][0]["updateDimensionProperties"]
    assert request["properties"] == {"hiddenByUser": True}
    assert request["fields"] == "hiddenByUser"
    assert (request["range"]["startIndex"], request["range"]["endIndex"]) == (1, 2)  # B, 0-indexed


def test_hide_columns_can_unhide(cfg, monkeypatch, tmp_path):
    client, fake_sheet = _client_with_fake_sheet(cfg, monkeypatch, tmp_path)
    fake_sheet._worksheets["T"] = FakeWorksheet("T", rows=[["h"]])

    client.hide_columns("T", "B", "B", hidden=False)

    request = fake_sheet.batch_update_calls[-1]["requests"][0]["updateDimensionProperties"]
    assert request["properties"] == {"hiddenByUser": False}


def test_insert_rows_issues_an_insert_dimension_request_not_a_rewrite(cfg, monkeypatch, tmp_path):
    # insertDimension (not write_tab/clear) is what makes Sheets itself
    # shift existing formula ranges down -- see sheet_pool_deck.py's
    # docstring and CONTRIBUTING.md's Phase 8 postmortem.
    client, fake_sheet = _client_with_fake_sheet(cfg, monkeypatch, tmp_path)
    fake_sheet._worksheets["T"] = FakeWorksheet("T", rows=[["existing"]])
    client.insert_rows("T", at_row=1, count=7)
    ws = fake_sheet._worksheets["T"]
    assert len(ws.insert_dimension_calls) == 1
    req = ws.insert_dimension_calls[0]
    assert req["inheritFromBefore"] is False
    r = req["range"]
    assert (r["dimension"], r["startIndex"], r["endIndex"]) == ("ROWS", 0, 7)
    # Not a rewrite: existing content untouched by insert_rows itself.
    assert ws.get_all_values() == [["existing"]]


def test_delete_rows_issues_a_delete_dimension_request(cfg, monkeypatch, tmp_path):
    client, fake_sheet = _client_with_fake_sheet(cfg, monkeypatch, tmp_path)
    fake_sheet._worksheets["T"] = FakeWorksheet("T", rows=[["existing"]])
    client.delete_rows("T", at_row=10, count=4)
    ws = fake_sheet._worksheets["T"]
    assert len(ws.delete_dimension_calls) == 1
    r = ws.delete_dimension_calls[0]["range"]
    assert (r["dimension"], r["startIndex"], r["endIndex"]) == ("ROWS", 9, 13)


def test_delete_columns_issues_a_delete_dimension_request(cfg, monkeypatch, tmp_path):
    client, fake_sheet = _client_with_fake_sheet(cfg, monkeypatch, tmp_path)
    fake_sheet._worksheets["T"] = FakeWorksheet("T", rows=[["existing"]])
    client.delete_columns("T", at_index=5, count=2)
    ws = fake_sheet._worksheets["T"]
    assert len(ws.delete_dimension_calls) == 1
    r = ws.delete_dimension_calls[0]["range"]
    assert (r["dimension"], r["startIndex"], r["endIndex"]) == ("COLUMNS", 5, 7)


def test_set_dropdown_validation_targets_only_the_given_cell(cfg, monkeypatch, tmp_path):
    client, fake_sheet = _client_with_fake_sheet(cfg, monkeypatch, tmp_path)
    fake_sheet._worksheets["T"] = FakeWorksheet("T", rows=[["h"]])
    client.set_dropdown_validation("T", "B1", ["ALL", "QB"])
    ws = fake_sheet._worksheets["T"]
    assert len(ws.data_validation_calls) == 1
    call = ws.data_validation_calls[0]
    assert call["range"]["startColumnIndex"] == 1  # column B, 0-indexed
    assert call["rule"]["condition"]["type"] == "ONE_OF_LIST"
    assert call["rule"]["strict"] is True


def test_set_checkbox_validation_uses_boolean_condition(cfg, monkeypatch, tmp_path):
    client, fake_sheet = _client_with_fake_sheet(cfg, monkeypatch, tmp_path)
    fake_sheet._worksheets["T"] = FakeWorksheet("T", rows=[["h"]])
    client.set_checkbox_validation("T", "W2:W10")
    ws = fake_sheet._worksheets["T"]
    assert len(ws.data_validation_calls) == 1
    call = ws.data_validation_calls[0]
    assert call["range"]["startColumnIndex"] == 22  # column W, 0-indexed
    assert call["rule"]["condition"]["type"] == "BOOLEAN"
    assert call["rule"]["strict"] is True


def test_set_number_range_validation_uses_number_between_condition(cfg, monkeypatch, tmp_path):
    client, fake_sheet = _client_with_fake_sheet(cfg, monkeypatch, tmp_path)
    fake_sheet._worksheets["T"] = FakeWorksheet("T", rows=[["h"]])
    client.set_number_range_validation("T", "H1", minimum=1, maximum=20)
    ws = fake_sheet._worksheets["T"]
    assert len(ws.data_validation_calls) == 1
    call = ws.data_validation_calls[0]
    assert call["range"]["startColumnIndex"] == 7  # column H, 0-indexed
    assert call["rule"]["condition"]["type"] == "NUMBER_BETWEEN"
    assert call["rule"]["condition"]["values"] == [
        {"userEnteredValue": "1"},
        {"userEnteredValue": "20"},
    ]
    assert call["rule"]["strict"] is False  # warn, not reject -- caller clamps in its own formula


def test_clear_data_validation_sends_no_rule(cfg, monkeypatch, tmp_path):
    # A setDataValidation request with no `rule` is how the Sheets API
    # clears an existing validation -- e.g. a rule inherited onto a new
    # row by insert_rows that doesn't belong there (see
    # SheetsClient.clear_data_validation's docstring).
    client, fake_sheet = _client_with_fake_sheet(cfg, monkeypatch, tmp_path)
    fake_sheet._worksheets["T"] = FakeWorksheet("T", rows=[["h"]])
    client.clear_data_validation("T", "A1:Z10")
    ws = fake_sheet._worksheets["T"]
    assert len(ws.data_validation_calls) == 1
    assert "rule" not in ws.data_validation_calls[0]


def test_ws_caches_a_tab_across_calls_instead_of_re_resolving_every_time(cfg, monkeypatch, tmp_path):
    # `dfs setup polish` calls many presentation primitives against the
    # same handful of tabs in one run (widths, freeze, N number formats, a
    # handful of colour scales, boolean rules...). Each used to resolve
    # its tab via `sheet.worksheet(tab_name)`, which re-fetches the WHOLE
    # spreadsheet's metadata every time -- multiplying one command's read
    # requests by however many formatting calls it makes, which is exactly
    # what blew through Sheets' per-minute read quota running this against
    # a real sheet. Resolving "EdgeRaw" five different ways here must only
    # hit the fake's `worksheet()` once.
    client, fake_sheet = _client_with_fake_sheet(cfg, monkeypatch, tmp_path)
    fake_sheet._worksheets["EdgeRaw"] = FakeWorksheet("EdgeRaw", rows=[["h"]])

    assert client.tab_exists("EdgeRaw") is True
    client.freeze_header("EdgeRaw")
    client.read_range("EdgeRaw", "A1:A1")
    client.format_number_range("EdgeRaw", "A2:A10")
    client.add_color_scale(
        "EdgeRaw",
        "M2:M10",
        min_color={"red": 1, "green": 0, "blue": 0},
        mid_color={"red": 1, "green": 1, "blue": 0},
        max_color={"red": 0, "green": 1, "blue": 0},
    )

    # tab_exists() warms the cache via worksheets(), not worksheet(), so
    # the fake's worksheet() should never have been called at all here.
    assert fake_sheet.worksheet_lookup_calls == []


def test_ws_resolves_and_caches_on_first_direct_lookup(cfg, monkeypatch, tmp_path):
    client, fake_sheet = _client_with_fake_sheet(cfg, monkeypatch, tmp_path)
    fake_sheet._worksheets["EdgeRaw"] = FakeWorksheet("EdgeRaw", rows=[["h"]])

    client.freeze_header("EdgeRaw")  # first touch: no tab_exists() call first
    client.read_range("EdgeRaw", "A1:A1")
    client.format_number_range("EdgeRaw", "A2:A10")

    assert fake_sheet.worksheet_lookup_calls == ["EdgeRaw"]


def test_add_filter_view_creates_a_named_view_over_the_given_range(cfg, monkeypatch, tmp_path):
    client, fake_sheet = _client_with_fake_sheet(cfg, monkeypatch, tmp_path)
    fake_sheet._worksheets["T"] = FakeWorksheet("T", rows=[["h"]])

    client.add_filter_view("T", title="Pool picking", a1_range="A1:W1000")

    ws = fake_sheet._worksheets["T"]
    assert len(ws.filter_views) == 1
    fv = ws.filter_views[0]
    assert fv["title"] == "Pool picking"
    assert "criteria" not in fv
    assert fv["range"]["startColumnIndex"] == 0


def test_add_filter_view_with_criteria_uses_string_column_index_keys(cfg, monkeypatch, tmp_path):
    client, fake_sheet = _client_with_fake_sheet(cfg, monkeypatch, tmp_path)
    fake_sheet._worksheets["T"] = FakeWorksheet("T", rows=[["h"]])

    client.add_filter_view(
        "T",
        title="Leverage plays",
        a1_range="A1:W1000",
        criteria={19: {"condition": {"type": "TEXT_EQ", "values": [{"userEnteredValue": "LEVERAGE"}]}}},
    )

    fv = fake_sheet._worksheets["T"].filter_views[0]
    # The Sheets API's criteria map is keyed by string column index in JSON.
    assert list(fv["criteria"]) == ["19"]


def test_clear_filter_view_removes_only_the_matching_title(cfg, monkeypatch, tmp_path):
    client, fake_sheet = _client_with_fake_sheet(cfg, monkeypatch, tmp_path)
    fake_sheet._worksheets["T"] = FakeWorksheet("T", rows=[["h"]])
    client.add_filter_view("T", title="Pool picking", a1_range="A1:W1000")
    client.add_filter_view("T", title="In my pool", a1_range="A1:W1000")

    client.clear_filter_view("T", "Pool picking")

    remaining = fake_sheet._worksheets["T"].filter_views
    assert [fv["title"] for fv in remaining] == ["In my pool"]


def test_clear_filter_view_is_a_no_op_when_no_view_has_that_title(cfg, monkeypatch, tmp_path):
    client, fake_sheet = _client_with_fake_sheet(cfg, monkeypatch, tmp_path)
    fake_sheet._worksheets["T"] = FakeWorksheet("T", rows=[["h"]])
    client.add_filter_view("T", title="Pool picking", a1_range="A1:W1000")

    client.clear_filter_view("T", "Nonexistent")

    assert len(fake_sheet._worksheets["T"].filter_views) == 1


def test_add_filter_view_is_re_runnable_via_clear_then_add(cfg, monkeypatch, tmp_path):
    # The real-world bug this guards against: clear_filter_view once read
    # the wrong dict key (filterId instead of filterViewId) off the
    # FilterView object and raised on every call, silently breaking
    # re-runnability entirely -- caught only by exercising the pair
    # together, not either method alone.
    client, fake_sheet = _client_with_fake_sheet(cfg, monkeypatch, tmp_path)
    fake_sheet._worksheets["T"] = FakeWorksheet("T", rows=[["h"]])

    client.clear_filter_view("T", "Pool picking")
    client.add_filter_view("T", title="Pool picking", a1_range="A1:W1000")
    client.clear_filter_view("T", "Pool picking")
    client.add_filter_view("T", title="Pool picking", a1_range="A1:W1000")

    assert len(fake_sheet._worksheets["T"].filter_views) == 1


def test_set_basic_filter_puts_sort_search_arrows_on_the_given_range(cfg, monkeypatch, tmp_path):
    client, fake_sheet = _client_with_fake_sheet(cfg, monkeypatch, tmp_path)
    fake_sheet._worksheets["T"] = FakeWorksheet("T", rows=[["h"]])

    client.set_basic_filter("T", "A1:W1000")

    rng = fake_sheet._worksheets["T"].basic_filter
    assert rng is not None
    assert rng["startColumnIndex"] == 0
    assert rng["endColumnIndex"] == 23


def test_set_basic_filter_is_re_runnable_without_a_separate_clear(cfg, monkeypatch, tmp_path):
    # Unlike filter views, a sheet has at most one basic filter and
    # setBasicFilter always replaces it -- no clear-then-add needed.
    client, fake_sheet = _client_with_fake_sheet(cfg, monkeypatch, tmp_path)
    fake_sheet._worksheets["T"] = FakeWorksheet("T", rows=[["h"]])

    client.set_basic_filter("T", "A1:B10")
    client.set_basic_filter("T", "A1:D20")

    rng = fake_sheet._worksheets["T"].basic_filter
    assert rng["endColumnIndex"] == 4


def test_clear_basic_filter_removes_it(cfg, monkeypatch, tmp_path):
    client, fake_sheet = _client_with_fake_sheet(cfg, monkeypatch, tmp_path)
    fake_sheet._worksheets["T"] = FakeWorksheet("T", rows=[["h"]])
    client.set_basic_filter("T", "A1:B10")

    client.clear_basic_filter("T")

    assert fake_sheet._worksheets["T"].basic_filter is None


def test_set_note_attaches_metadata_without_touching_the_cell_value(cfg, monkeypatch, tmp_path):
    client, fake_sheet = _client_with_fake_sheet(cfg, monkeypatch, tmp_path)
    fake_sheet._worksheets["T"] = FakeWorksheet("T", rows=[["Pool"]])

    client.set_note("T", "A1", "Tick to add this player to your pool.")

    ws = fake_sheet._worksheets["T"]
    assert ws.notes[(0, 0)] == "Tick to add this player to your pool."
    assert ws.get("A1:A1") == [["Pool"]]  # the cell's own value is untouched


def test_protect_sheet_protects_the_whole_sheet_with_no_range_bounds(cfg, monkeypatch, tmp_path):
    client, fake_sheet = _client_with_fake_sheet(cfg, monkeypatch, tmp_path)
    fake_sheet._worksheets["T"] = FakeWorksheet("T", rows=[["h"]])

    client.protect_sheet("T", description="test")

    ws = fake_sheet._worksheets["T"]
    assert len(ws.protected_ranges) == 1
    pr = ws.protected_ranges[0]
    assert pr["range"] == {"sheetId": ws.id}  # no row/col bounds -- the whole sheet
    assert pr["warningOnly"] is True
    assert pr["description"] == "test"
    assert "unprotectedRanges" not in pr


def test_protect_sheet_with_unprotected_ranges_converts_each_to_a_grid_range(cfg, monkeypatch, tmp_path):
    client, fake_sheet = _client_with_fake_sheet(cfg, monkeypatch, tmp_path)
    fake_sheet._worksheets["T"] = FakeWorksheet("T", rows=[["h"]])

    client.protect_sheet("T", unprotected_ranges=["B1", "F:F"])

    pr = fake_sheet._worksheets["T"].protected_ranges[0]
    assert len(pr["unprotectedRanges"]) == 2
    assert pr["unprotectedRanges"][0]["startColumnIndex"] == 1  # B1


def test_clear_protected_ranges_removes_every_existing_protection(cfg, monkeypatch, tmp_path):
    client, fake_sheet = _client_with_fake_sheet(cfg, monkeypatch, tmp_path)
    fake_sheet._worksheets["T"] = FakeWorksheet("T", rows=[["h"]])
    client.protect_sheet("T")
    client.protect_sheet("T", unprotected_ranges=["B1"])

    client.clear_protected_ranges("T")

    assert fake_sheet._worksheets["T"].protected_ranges == []


def test_clear_protected_ranges_is_a_no_op_when_nothing_is_protected(cfg, monkeypatch, tmp_path):
    client, fake_sheet = _client_with_fake_sheet(cfg, monkeypatch, tmp_path)
    fake_sheet._worksheets["T"] = FakeWorksheet("T", rows=[["h"]])
    client.clear_protected_ranges("T")  # must not raise
    assert fake_sheet._worksheets["T"].protected_ranges == []


def test_protect_sheet_is_re_runnable_via_clear_then_add(cfg, monkeypatch, tmp_path):
    client, fake_sheet = _client_with_fake_sheet(cfg, monkeypatch, tmp_path)
    fake_sheet._worksheets["T"] = FakeWorksheet("T", rows=[["h"]])

    client.clear_protected_ranges("T")
    client.protect_sheet("T")
    client.clear_protected_ranges("T")
    client.protect_sheet("T")

    assert len(fake_sheet._worksheets["T"].protected_ranges) == 1
