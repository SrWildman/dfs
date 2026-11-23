"""The Results tab's rows by label (`results_rows`): a secondary slate's row ("12 Thu") inserted in date order
with the week rows' own formulas, carried and re-read by label, never by a row number."""

from __future__ import annotations

import re

import pytest

from dfs import results_rows
from dfs.config import ResultsConfig
from dfs.sheets import column_index, column_letter

_REF = re.compile(r"(\$?)([A-Z]+)(\$?)(\d+)")


class Tab:
    """One Results tab: weeks 1-18 in rows 2-19, a blank formula row, the averages at 23, as the template."""

    def __init__(self):
        self.cells: dict[str, object] = {}
        for r in range(2, 21):
            if r <= 19:
                self.cells[f"A{r}"] = str(r - 1)
            self.cells[f"D{r}"] = f'=IF(B{r}, B{r}>=C{r}, "")'
            self.cells[f"G{r}"] = f'=IF(E{r}="","",IFERROR(F{r}/E{r},""))'
        self.cells["B23"] = '=IF(COUNT(B2:B20)=0,"",AVERAGE(B2:B20))'
        self.inserted: list[int] = []

    def _read(self, a1):
        m = re.fullmatch(r"([A-Z]+)(\d+):([A-Z]+)(\d+)", a1)
        c1, r1, c2, r2 = column_index(m[1]), int(m[2]), column_index(m[3]), int(m[4])
        rows = [
            [self.cells.get(f"{column_letter(c)}{r}", "") for c in range(c1, c2 + 1)]
            for r in range(r1, r2 + 1)
        ]
        while rows and not any(v != "" for v in rows[-1]):
            rows.pop()
        return rows

    def read_range(self, tab, a1):
        return [[str(v) for v in row] for row in self._read(a1)]

    read_formula = read_range

    def update_ranges(self, tab, updates):
        for a1, rows in updates.items():
            start = re.match(r"([A-Z]+)(\d+)", a1)
            c1, r1 = column_index(start[1]), int(start[2])
            for i, row in enumerate(rows):
                for j, v in enumerate(row):
                    key = f"{column_letter(c1 + j)}{r1 + i}"
                    if v in ("", None):
                        self.cells.pop(key, None)
                    else:
                        self.cells[key] = v

    def insert_rows(self, tab, *, at_row, count):
        self.inserted.append(at_row)

        def shift(m):
            row = int(m[4])
            return f"{m[1]}{m[2]}{m[3]}{row + count if row >= at_row else row}"

        moved = {}
        for key, v in self.cells.items():
            col, row = re.fullmatch(r"([A-Z]+)(\d+)", key).groups()
            if isinstance(v, str) and v.startswith("="):
                v = _REF.sub(shift, v)
            moved[f"{col}{int(row) + count if int(row) >= at_row else row}"] = v
        self.cells = moved

    def labels(self):
        return [self.cells[f"A{r}"] for r in range(2, 40) if f"A{r}" in self.cells]


RC = ResultsConfig(tab="Results", first_row=2, last_row=20)


def test_a_slate_row_goes_in_date_order_with_its_own_formulas():
    tab = Tab()
    row, done = results_rows.ensure_row(tab, RC, 12, "thu")
    assert (
        row == 13 and tab.inserted == [13] and done == ["Results: inserted row 13 for '12 Thu' (date order)"]
    )
    assert tab.labels()[10:13] == ["11", "12 Thu", "12"]
    assert tab.cells["D13"] == '=IF(B13, B13>=C13, "")'
    assert tab.cells["G14"] == '=IF(E14="","",IFERROR(F14/E14,""))'  # week 12's, moved down with it
    assert tab.cells["B24"] == '=IF(COUNT(B2:B21)=0,"",AVERAGE(B2:B21))'  # the averages take it in
    assert results_rows.ensure_row(tab, RC, 12, "thu") == (13, [])  # once
    assert results_rows.span(tab, RC).last_row == 21
    assert results_rows.label_rows(tab, RC)[(12, "main")] == 14


def test_a_monday_slate_goes_after_its_sunday_and_a_main_week_is_never_inserted():
    tab = Tab()
    assert results_rows.ensure_row(tab, RC, 18, "mon")[0] == 20  # under week 18, above the blank row
    assert tab.labels()[-2:] == ["18", "18 Mon"]
    with pytest.raises(results_rows.ResultsRowError):
        results_rows.ensure_row(_blank(), RC, 12, "main")


def _blank():
    tab = Tab()
    tab.cells = {}
    return tab


def test_rows_are_carried_and_re_read_by_label():
    thu, w12 = Tab(), Tab()
    results_rows.ensure_row(thu, RC, 12, "thu")
    thu.update_ranges(
        "Results", {"B12:C12": [["101.5", "98"]], "B13:C13": [["120.5", "110"]], "H13": [["x"]]}
    )
    copied, done = results_rows.copy_rows(thu, w12, RC)
    assert copied == 19 and done and w12.labels() == thu.labels()
    assert (w12.cells["B12"], w12.cells["B13"], w12.cells["H13"]) == ("101.5", "120.5", "x")
    assert w12.cells["D13"] == '=IF(B13, B13>=C13, "")'  # a formula is never copied over, only kept
    thu.update_ranges("Results", {"E13:F13": [["2", "1"]]})
    assert results_rows.copy_row(thu, w12, RC, 12, "thu") == (13, [])
    assert (w12.cells["E13"], w12.cells["F13"]) == ("2", "1")
    assert results_rows.has_slate_rows(w12, RC) and not results_rows.has_slate_rows(Tab(), RC)
    with pytest.raises(results_rows.ResultsRowError):
        results_rows.copy_row(Tab(), w12, RC, 12, "thu")
