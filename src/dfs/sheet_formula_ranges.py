"""Per-row formula ranges that nothing in this repo writes -- and so nothing else guards.

Round 5 follow-up item 2 (2026-09-29). Two template regressions went unnoticed until
they were stumbled on: Results' `Cash Results`/`H2H %` formulas, and DkSalClean's row
formulas (without which PlayerPoolRaw -- and so Player Pool and Lineups -- reads blank).
Both are hand-built formula columns that `dfs setup ...` never rewrites, so a template
copy that lost them looked fine to every existing `dfs doctor` check.

This module says where such ranges are and finds gaps in them:

* a row with **no formula** (a typed value or nothing);
* a row whose formula **points at a different row** (`=DKSalRaw!A3083` on row 1000) --
  on Results, Season and DkSalClean row N reads row N, so a formula reading another
  row's cells is a leftover from an old row deletion. (Not checked on PlayerPoolRaw,
  whose identity columns read DkSalClean in a deliberately permuted order.)

`dfs doctor` reports both (read-only). `repair_formula_ranges` -- behind `dfs setup
repair-formula-ranges` -- rewrites each gap from the nearest healthy row, and clears
formulas left past the documented last row on the hub tabs.

Positions are derived, never typed: the row span comes from config / `PLAYER_POOL_RAW_BLOCK`
/ `sheet_season_view`, and columns are found by header name.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from dfs.config import Config
from dfs.derived import ZONE_LABELS
from dfs.sheet_links import PLAYER_POOL_RAW_BLOCK, PLAYER_POOL_RAW_TAB
from dfs.sheet_season_view import FIRST_ROW as SEASON_FIRST_ROW
from dfs.sheet_season_view import LAST_ROW as SEASON_LAST_ROW
from dfs.sheets import column_letter

DKSALCLEAN_TAB = "DkSalClean"

# Header names of the hand-built formula columns on Results and Season (every other
# column on those tabs is typed or written by `dfs week close`).
RESULTS_FORMULA_HEADERS = ("Cash Results", "H2H %")
SEASON_FORMULA_HEADERS = ("Total Net", "Cum. Cash", "Cum. GPP", "Cum. Betting", "Cum. Total")

# A relative A1 cell reference: `D2`, `$A2`, `DKSalRaw!H745`. Not `A$2`/`$A$2` (a
# deliberately anchored row), not a function call like `LOG10(`, not the tail of a name.
_REF = re.compile(r"(?<![A-Za-z0-9_$])(\$?[A-Z]{1,3})(\d+)(?![\d(A-Za-z_])")


@dataclass(frozen=True)
class FormulaRange:
    """Formulas expected on every row `first_row`..`last_row` of these columns."""

    tab: str
    columns: dict[int, str]  # 0-based column index -> header text
    first_row: int
    last_row: int
    clear_tail: bool = False  # hub tabs: formulas past `last_row` are stale leftovers
    # True where row N's formulas must read row N (Results, Season, DkSalClean). False on
    # PlayerPoolRaw, whose Name/Pos./Team/Opp./DK Sal cells read DkSalClean in a permuted
    # order (row 16 reads DkSalClean row 17, and so on) -- deliberate, self-consistent per
    # row -- so only "a formula is there" is checkable.
    same_row_only: bool = True


@dataclass(frozen=True)
class FormulaGap:
    tab: str
    column: str  # letter
    header: str
    rows: tuple[int, ...]
    kind: str  # "missing" | "wrong-row"


def referenced_rows(formula: str) -> set[int]:
    return {int(m.group(2)) for m in _REF.finditer(formula)}


def shift_rows(formula: str, from_row: int, to_row: int) -> str:
    """Rewrite every relative reference to `from_row` as `to_row` (the pattern row's own
    row number, wherever it appears -- `$A2`, `F2/E2`, `DKSalRaw!H2`)."""

    def _sub(m: re.Match) -> str:
        return f"{m.group(1)}{to_row}" if int(m.group(2)) == from_row else m.group(0)

    return _REF.sub(_sub, formula)


def _columns_from_header(header: list[str], names: tuple[str, ...] | None, *, skip=()) -> dict[int, str]:
    if names is None:
        return {i: h for i, h in enumerate(header) if str(h).strip() and h not in skip}
    return {i: h for i, h in enumerate(header) if h in names}


def formula_ranges(cfg: Config, headers_by_tab: dict[str, list[str]]) -> list[FormulaRange]:
    """Every range worth guarding, for the tabs present in `headers_by_tab`."""
    ranges: list[FormulaRange] = []
    hub_last_row = max(end for _, end in PLAYER_POOL_RAW_BLOCK)
    hub_first_row = min(start for start, _ in PLAYER_POOL_RAW_BLOCK)

    if cfg.results.tab in headers_by_tab:
        cols = _columns_from_header(headers_by_tab[cfg.results.tab], RESULTS_FORMULA_HEADERS)
        ranges.append(FormulaRange(cfg.results.tab, cols, cfg.results.first_row, cfg.results.last_row))
    if cfg.season.tab in headers_by_tab:
        cols = _columns_from_header(headers_by_tab[cfg.season.tab], SEASON_FORMULA_HEADERS)
        ranges.append(FormulaRange(cfg.season.tab, cols, SEASON_FIRST_ROW, SEASON_LAST_ROW))
    if DKSALCLEAN_TAB in headers_by_tab:
        cols = _columns_from_header(headers_by_tab[DKSALCLEAN_TAB], None)
        ranges.append(FormulaRange(DKSALCLEAN_TAB, cols, hub_first_row, hub_last_row, clear_tail=True))
    if PLAYER_POOL_RAW_TAB in headers_by_tab:
        # The zone-label columns (GAME, CEIL, MOVE, WX, USAGE) are header-only by design.
        cols = _columns_from_header(headers_by_tab[PLAYER_POOL_RAW_TAB], None, skip=ZONE_LABELS)
        ranges.append(
            FormulaRange(
                PLAYER_POOL_RAW_TAB, cols, hub_first_row, hub_last_row, clear_tail=True, same_row_only=False
            )
        )
    return ranges


def _grid_cell(grid: list[list], row_offset: int, col: int) -> str:
    if row_offset >= len(grid):
        return ""
    row = grid[row_offset]
    return row[col] if col < len(row) and isinstance(row[col], str) else ""


def read_range_grid(client, spec: FormulaRange) -> list[list]:
    """Formula text for the whole span of `spec`, columns A..last checked column."""
    last_col = column_letter(max(spec.columns)) if spec.columns else "A"
    return client.read_formula(spec.tab, f"A{spec.first_row}:{last_col}{spec.last_row}")


def find_gaps(client, spec: FormulaRange) -> list[FormulaGap]:
    if not spec.columns:
        return []
    grid = read_range_grid(client, spec)
    gaps: list[FormulaGap] = []
    for col, header in sorted(spec.columns.items()):
        missing: list[int] = []
        wrong: list[int] = []
        for r in range(spec.first_row, spec.last_row + 1):
            cell = _grid_cell(grid, r - spec.first_row, col)
            if not cell.startswith("="):
                missing.append(r)
            elif spec.same_row_only and referenced_rows(cell) - {r}:
                wrong.append(r)
        letter = column_letter(col)
        if missing:
            gaps.append(FormulaGap(spec.tab, letter, header, tuple(missing), "missing"))
        if wrong:
            gaps.append(FormulaGap(spec.tab, letter, header, tuple(wrong), "wrong-row"))
    return gaps


def _runs(rows: tuple[int, ...] | list[int]) -> list[tuple[int, int]]:
    runs: list[tuple[int, int]] = []
    for r in sorted(rows):
        if runs and r == runs[-1][1] + 1:
            runs[-1] = (runs[-1][0], r)
        else:
            runs.append((r, r))
    return runs


def describe_gap(gap: FormulaGap) -> str:
    spans = ", ".join(f"{a}" if a == b else f"{a}-{b}" for a, b in _runs(gap.rows))
    what = "have no formula" if gap.kind == "missing" else "hold a formula pointing at another row"
    return f"{gap.tab!r} column {gap.column} ({gap.header}): row(s) {spans} {what}"


def repair_formula_ranges(client, cfg: Config, headers_by_tab: dict[str, list[str]]) -> list[str]:
    """Rewrite every gap from the nearest healthy row above it (below it if none), then
    clear stale formulas past the last row on the hub tabs. Returns one report line per
    action. Rewrites only cells `find_gaps` flagged; healthy cells are never touched."""
    report: list[str] = []
    for spec in formula_ranges(cfg, headers_by_tab):
        gaps = find_gaps(client, spec)
        if gaps:
            grid = read_range_grid(client, spec)
            by_col: dict[str, set[int]] = {}
            for gap in gaps:
                by_col.setdefault(gap.column, set()).update(gap.rows)
            index_of = {column_letter(c): c for c in spec.columns}
            for letter, bad_rows in sorted(by_col.items()):
                col = index_of[letter]
                good = [
                    r
                    for r in range(spec.first_row, spec.last_row + 1)
                    if r not in bad_rows and _grid_cell(grid, r - spec.first_row, col).startswith("=")
                ]
                if not good:
                    report.append(f"{spec.tab}!{letter}: no healthy row to copy a pattern from -- skipped")
                    continue
                for a, b in _runs(bad_rows):
                    pattern_row = max((r for r in good if r < a), default=min(good))
                    pattern = _grid_cell(grid, pattern_row - spec.first_row, col)
                    rows = [[shift_rows(pattern, pattern_row, r)] for r in range(a, b + 1)]
                    client.update_range(spec.tab, f"{letter}{a}:{letter}{b}", rows)
                    report.append(f"{spec.tab}!{letter}{a}:{letter}{b} rewritten from row {pattern_row}")
        if spec.clear_tail:
            report.extend(_clear_stale_tail(client, spec))
    return report


def _clear_stale_tail(client, spec: FormulaRange) -> list[str]:
    """Formulas below `last_row` in the checked columns: stale, clear them -- but only
    cells that hold a formula; a typed value there is left alone and reported."""
    first = spec.last_row + 1
    last_col = column_letter(max(spec.columns))
    tail = client.read_formula(spec.tab, f"A{first}:{last_col}{client.row_count(spec.tab)}")
    to_clear: dict[int, list[int]] = {}
    left_alone: list[str] = []
    for offset, row in enumerate(tail):
        for col in spec.columns:
            cell = row[col] if col < len(row) else ""
            if cell == "" or cell is None:
                continue
            if isinstance(cell, str) and cell.startswith("="):
                to_clear.setdefault(col, []).append(first + offset)
            else:
                left_alone.append(f"{column_letter(col)}{first + offset}")
    out: list[str] = []
    for col, rows in sorted(to_clear.items()):
        ranges = [f"{column_letter(col)}{a}:{column_letter(col)}{b}" for a, b in _runs(rows)]
        client.clear_ranges(spec.tab, ranges)
        out.append(f"{spec.tab}!{column_letter(col)}: cleared stale formulas at {', '.join(ranges)}")
    if left_alone:
        out.append(
            f"{spec.tab}: typed values past row {spec.last_row} left alone: {', '.join(left_alone[:10])}"
        )
    return out
