"""Blank instead of an error when there is nothing to compute yet.

Round 5 cleanup item 2 (2026-09-29, Sam approved). A week with no H2H entries, an empty
ledger or no risked money used to show `#DIV/0!` on Results, Bankroll and Season -- the
divisions and averages there were built by hand with no guard. This module says what a
guarded formula looks like (`guard_formula`) and finds/repairs the unguarded ones on
those three tabs (`find_unguarded` / `repair_unguarded`, behind `dfs doctor` and `dfs
setup guard-empty-states`).

Two shapes are guarded, each returning `""` when its inputs are empty:

* a division by a cell -- `=F2/E2` becomes `=IF(E2="","",IFERROR(F2/E2,""))` (a blank
  denominator gives `""`; a denominator that is 0 falls to the `IFERROR`);
* a bare `=AVERAGE(range)` -- `=IF(COUNT(range)=0,"",AVERAGE(range))`.

A formula that already starts with `IF(`/`IFERROR(` counts as guarded, so re-running is a
no-op and hand-built guards (Bankroll's `Odds` column) are left alone. Division by a
literal (`/100`) is never an error and is left alone too.

Positions are derived: the tabs come from config and the scan covers each tab's whole
used range, so nothing here names a row or a column.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from dfs.config import Config
from dfs.sheets import column_letter

# A division whose denominator is a cell reference: `/E2`, `/$B$7`. Not `/100`, not `/SUM(`.
_DIVIDE_BY_CELL = re.compile(r"/\s*(\$?[A-Z]{1,3}\$?\d+)(?![\d(A-Za-z_])")
_BARE_AVERAGE = re.compile(r"^=AVERAGE\(([^()]+)\)$", re.IGNORECASE)
_ALREADY_GUARDED = re.compile(r"^=\s*(IFERROR|IF)\(", re.IGNORECASE)

SCAN_LAST_COLUMN = "Z"


def guard_formula(formula: str) -> str:
    """`formula` with its empty-state guard, or unchanged if it needs none or has one."""
    text = formula.strip()
    if not text.startswith("=") or _ALREADY_GUARDED.match(text):
        return formula
    average = _BARE_AVERAGE.match(text)
    if average:
        rng = average.group(1)
        return f'=IF(COUNT({rng})=0,"",AVERAGE({rng}))'
    denominators = {m.group(1) for m in _DIVIDE_BY_CELL.finditer(text)}
    if not denominators:
        return formula
    body = text[1:]
    if len(denominators) == 1:
        return f'=IF({next(iter(denominators))}="","",IFERROR({body},""))'
    return f'=IFERROR({body},"")'


def is_unguarded(formula: str) -> bool:
    return guard_formula(formula) != formula


@dataclass(frozen=True)
class UnguardedCells:
    tab: str
    column: str  # letter
    rows: tuple[int, ...]


def guarded_tabs(cfg: Config) -> list[str]:
    return [cfg.results.tab, cfg.bankroll.tab, cfg.season.tab]


def _skip_rows(cfg: Config, tab: str) -> tuple[int, int] | None:
    """Rows another check already owns: Results' per-row range is checked (and repaired)
    by `sheet_formula_ranges`, which expects the guarded text there."""
    return (cfg.results.first_row, cfg.results.last_row) if tab == cfg.results.tab else None


def _scan(client, cfg: Config, tab: str) -> dict[int, list[tuple[int, str]]]:
    """`{column index: [(row, formula), ...]}` for every unguarded formula on `tab`."""
    skip = _skip_rows(cfg, tab)
    grid = client.read_formula(tab, f"A1:{SCAN_LAST_COLUMN}{client.row_count(tab)}")
    found: dict[int, list[tuple[int, str]]] = {}
    for r, row in enumerate(grid, start=1):
        if skip and skip[0] <= r <= skip[1]:
            continue
        for c, cell in enumerate(row):
            if isinstance(cell, str) and is_unguarded(cell):
                found.setdefault(c, []).append((r, cell))
    return found


def find_unguarded(client, cfg: Config, tab_titles: set[str] | None = None) -> list[UnguardedCells]:
    found: list[UnguardedCells] = []
    for tab in guarded_tabs(cfg):
        if tab_titles is not None and tab not in tab_titles:
            continue
        for col, cells in sorted(_scan(client, cfg, tab).items()):
            found.append(UnguardedCells(tab, column_letter(col), tuple(r for r, _ in cells)))
    return found


def describe(cells: UnguardedCells) -> str:
    rows = ", ".join(str(r) for r in cells.rows[:8]) + (" ..." if len(cells.rows) > 8 else "")
    return (
        f"{cells.tab!r} column {cells.column}: row(s) {rows} divide or average with no empty-state "
        "guard (would show #DIV/0! on an empty week) -- run `dfs setup guard-empty-states`"
    )


def _runs(rows: list[int]) -> list[tuple[int, int]]:
    runs: list[tuple[int, int]] = []
    for r in sorted(rows):
        if runs and r == runs[-1][1] + 1:
            runs[-1] = (runs[-1][0], r)
        else:
            runs.append((r, r))
    return runs


def repair_unguarded(client, cfg: Config) -> list[str]:
    """Rewrite every unguarded formula in place, one write per contiguous run of rows in a
    column. Only the cells `find_unguarded` would flag are written."""
    report: list[str] = []
    for tab in guarded_tabs(cfg):
        if not client.tab_exists(tab):
            continue
        for col, cells in sorted(_scan(client, cfg, tab).items()):
            letter = column_letter(col)
            formulas = dict(cells)
            for first, last in _runs(list(formulas)):
                new = [[guard_formula(formulas[r])] for r in range(first, last + 1)]
                client.update_range(tab, f"{letter}{first}:{letter}{last}", new)
                report.append(f"{tab}!{letter}{first}:{letter}{last} guarded, e.g. {new[0][0]}")
    return report
