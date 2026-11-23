"""The Results tab's rows by label: a secondary slate's own row ("12 Thu") beside the main weeks' (Sam,
Nov 23: "Results and Model Check rows" for every slate).

Results is pre-built one row per week (`[results]` first_row..last_row: weeks 1-18, then one blank formula
row), with two formula columns in every row (`Cash Results`, `H2H %`). A secondary slate's row is inserted
in date order by a real row insert (`SheetsClient.insert_rows`), so Sheets moves every formula and range under
it (the averages row's `AVERAGE(B2:B20)` becomes `B2:B21`); its own two formulas are the row above's, moved
to it (`sheet_formula_ranges.shift_rows`). Nothing here holds a row number: rows are found by their label in
`A` (`label_rows`), and `span` is config's range grown by the rows inserted.

Main's writers keep matching a plain week number (`results_autofill.write_results_updates`), so a "12 Thu" row
is never written by them; with no slate row on the tab every function here reads what it read before.
"""

from __future__ import annotations

from dfs import slate, slate_chain
from dfs.config import ResultsConfig
from dfs.sheet_formula_ranges import shift_rows

LABEL_SPARE = 12  # rows read past `last_row` when looking for labels (one per slate row a season can add)
LAST_COLUMN = "J"  # Week .. the three team-colour columns (`week.RESULTS_VALUE_COLUMN_RANGES`)


class ResultsRowError(Exception):
    """The Results tab cannot take a slate's row safely (no week rows found, or the labels disagree)."""


def label_rows(client, rc: ResultsConfig) -> dict[tuple[int, str], int]:  # noqa: ANN001 - SheetsClient
    """`{(week, key): row}` for the labelled rows from `first_row` down to the first cell that is not a
    label (the blank formula row under week 18)."""
    rows = client.read_range(rc.tab, f"A{rc.first_row}:A{rc.last_row + LABEL_SPARE}")
    out: dict[tuple[int, str], int] = {}
    for i, r in enumerate(rows):
        parsed = slate_chain.parse_label(r[0] if r else "")
        if parsed is None:
            break
        out[parsed] = rc.first_row + i
    return out


def span(client, rc: ResultsConfig) -> ResultsConfig:  # noqa: ANN001
    """`rc` with `last_row` grown by the slate rows inserted above it (config's range ends one row under
    the last week; so does this one)."""
    rows = label_rows(client, rc)
    last = max(rows.values(), default=rc.last_row - 1)
    return rc.model_copy(update={"last_row": max(rc.last_row, last + 1)})


def _insert_at(rows: dict[tuple[int, str], int], week: int, key: str) -> int:
    after = [r for (w, k), r in rows.items() if slate_chain.position(w, k) > slate_chain.position(week, key)]
    return min(after) if after else max(rows.values()) + 1


def ensure_row(client, rc: ResultsConfig, week: int, key: str) -> tuple[int, list[str]]:  # noqa: ANN001
    """The row of slate `key`'s week, inserted in date order when the tab has none (a secondary slate only:
    a main week's row is pre-built). Returns `(row, what was done)`."""
    rows = label_rows(client, rc)
    if (week, key) in rows:
        return rows[(week, key)], []
    if key == slate.MAIN or not rows:
        raise ResultsRowError(f"{rc.tab}: no row for Week {week} (labels found: {len(rows)})")
    at = _insert_at(rows, week, key)
    pattern = at - 1  # a week row; after the insert it is still at - 1
    client.insert_rows(rc.tab, at_row=at, count=1)
    above = (client.read_formula(rc.tab, f"A{pattern}:{LAST_COLUMN}{pattern}") or [[]])[0]
    label = slate_chain.season_label(week, key)
    updates = {f"A{at}": [[label]]}
    for i, cell in enumerate(above):
        if isinstance(cell, str) and cell.startswith("="):
            letter = chr(ord("A") + i)
            updates[f"{letter}{at}"] = [[shift_rows(cell, pattern, at)]]
    client.update_ranges(rc.tab, updates)
    return at, [f"{rc.tab}: inserted row {at} for {label!r} (date order)"]


def write_row(client, rc: ResultsConfig, row: int, stats) -> None:  # noqa: ANN001 - results_autofill.WeekResults
    """Cash Pts (B, left alone when there is none yet) and H2H Entered / Win (E:F) into one row, as
    `results_autofill.write_results_updates` writes a week's."""
    updates = {f"E{row}:F{row}": [[stats.h2h_entered, stats.h2h_win]]}
    if stats.cash_pts is not None:
        updates[f"B{row}"] = [[stats.cash_pts]]
    client.update_ranges(rc.tab, updates)


VALUE_RANGES = ("B:C", "E:F", "H:J")  # the typed columns of a row (`week.RESULTS_VALUE_COLUMN_RANGES` less A)


def copy_row(source, target, rc: ResultsConfig, week: int, key: str) -> tuple[int, list[str]]:  # noqa: ANN001
    """Slate `key`'s week row from `source` (its own sheet) onto `target`, inserted there first when missing:
    the typed columns only, never a formula. Raises ResultsRowError when `source` has no such row."""
    have = label_rows(source, rc)
    if (week, key) not in have:
        raise ResultsRowError(f"{rc.tab}: {slate_chain.season_label(week, key)!r} is not on the source sheet")
    src_row = have[(week, key)]
    row, done = ensure_row(target, rc, week, key)
    values = (source.read_range(rc.tab, f"A{src_row}:{LAST_COLUMN}{src_row}") or [[]])[0]
    values = list(values) + [""] * (10 - len(values))
    updates = {}
    for col_range in VALUE_RANGES:
        start, _, end = col_range.partition(":")
        lo, hi = ord(start) - ord("A"), ord(end) - ord("A")
        updates[f"{start}{row}:{end}{row}"] = [values[lo : hi + 1]]
    target.update_ranges(rc.tab, updates)
    return row, done


def copy_rows(source, target, rc: ResultsConfig) -> tuple[int, list[str]]:  # noqa: ANN001
    """`dfs week new`'s Results carry when slate rows are involved: every row `source` has and `target` lacks
    is inserted first (date order), so both tabs hold the same labels in the same order, then the typed
    columns are copied row for row. Returns `(rows copied, what was done)`. Raises ResultsRowError when the
    labels still differ (nothing copied)."""
    src = label_rows(source, rc)
    done: list[str] = []
    for week, key in sorted(src, key=lambda wk: slate_chain.position(*wk)):
        if key != slate.MAIN and (week, key) not in label_rows(target, rc):
            done += ensure_row(target, rc, week, key)[1]
    dst = label_rows(target, rc)
    order = sorted(src, key=src.get)
    if sorted(dst, key=dst.get) != order:
        raise ResultsRowError(f"{rc.tab}: the rows differ after the inserts -- nothing copied")
    first, last = rc.first_row, rc.first_row + len(order) - 1
    rows = source.read_range(rc.tab, f"A{first}:{LAST_COLUMN}{last}")
    rows = [list(r) + [""] * (10 - len(r)) for r in (rows + [[] for _ in order])[: len(order)]]
    updates = {}
    for col_range in VALUE_RANGES:
        start, _, end = col_range.partition(":")
        lo, hi = ord(start) - ord("A"), ord(end) - ord("A")
        updates[f"{start}{first}:{end}{last}"] = [r[lo : hi + 1] for r in rows]
    target.update_ranges(rc.tab, updates)
    return len(order), done


def has_slate_rows(client, rc: ResultsConfig) -> bool:  # noqa: ANN001
    return any(k != slate.MAIN for _, k in label_rows(client, rc))
