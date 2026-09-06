"""One-time structural change: insert 7 frozen rows at the top of `Lineups`
holding Player Pool's roster laid out horizontally by position, so it stays
on screen (split-screen style, but in one window) through all twenty lineup
blocks below.

This is deliberately NOT a `sheet_style.py` "polish" function. Everything
in that module is scoped to things that cannot move a row or column;
`add_bench` does exactly that -- it inserts real rows via
`SheetsClient.insert_rows` (a Sheets API `insertDimension` request), which
is what makes Sheets itself shift `Lineups`' existing formulas and
conditional-format ranges down with it. Rewriting the tab (`write_tab`)
would not, and would silently corrupt every one of the twenty blocks --
see CONTRIBUTING.md's Phase 8 postmortem and the "Bench" changelog entry
this feature added alongside it.

`weekly_reset.LINEUPS_NAME_BLOCKS`, `doctor._check_lineups_header_repeats`
and every Lineups row constant downstream of it were updated by +7 at the
same time this shipped -- they are not derived from `BENCH_ROWS` here
because they describe the *sheet's actual current layout*, which this
module only mutates once, on a sheet that doesn't have a bench yet.

Idempotent: `add_bench` checks A1 for `BENCH_TITLE` before touching
anything, so running it again against a sheet that already has a bench is
a safe no-op rather than a second, stacked 7-row insert.
"""

from __future__ import annotations

from dfs.sheets import SheetsClient
from dfs.weekly_reset import PLAYER_POOL_NAME_BLOCKS

BENCH_ROWS = 7
BENCH_TITLE = "BENCH — your Player Pool, by position. Lineup blocks start at row 8."

# Row-in-bench -> position label, paired positionally with
# PLAYER_POOL_NAME_BLOCKS (QB, RB, WR, TE, DST, in that order -- see
# weekly_reset.py). Row 7 is left as a blank visual separator.
_BENCH_POSITIONS = ["QB", "RB", "WR", "TE", "DST"]


def _bench_formula(pool_tab: str, start: int, end: int) -> str:
    """TRANSPOSE(FILTER(...)) spills one position's typed names horizontally
    across the row. Sourced from `PLAYER_POOL_NAME_BLOCKS` rather than a
    literal range, so a re-measured Player Pool layout moves this formula
    along with it instead of silently reading the wrong rows."""
    rng = f"'{pool_tab}'!$A${start}:$A${end}"
    return f'=IFERROR(TRANSPOSE(FILTER({rng},{rng}<>"")),"")'


def add_bench(client: SheetsClient, *, lineups_tab: str, pool_tab: str) -> str:
    """Insert the bench at the top of `lineups_tab` and freeze it. Skips
    (reporting so) if a bench is already present, rather than inserting a
    second one."""
    existing = client.read_range(lineups_tab, "A1")
    if existing and existing[0] and existing[0][0] == BENCH_TITLE:
        return f"{lineups_tab}: bench already present -- skipped"

    if len(PLAYER_POOL_NAME_BLOCKS) != len(_BENCH_POSITIONS):
        raise ValueError(
            f"PLAYER_POOL_NAME_BLOCKS has {len(PLAYER_POOL_NAME_BLOCKS)} block(s), "
            f"expected {len(_BENCH_POSITIONS)} ({_BENCH_POSITIONS}) -- add_bench's "
            f"row/position pairing would be wrong."
        )

    client.insert_rows(lineups_tab, at_row=1, count=BENCH_ROWS)

    rows = [[BENCH_TITLE, ""]]
    for pos, (start, end) in zip(_BENCH_POSITIONS, PLAYER_POOL_NAME_BLOCKS, strict=True):
        rows.append([pos, _bench_formula(pool_tab, start, end)])
    rows.append(["", ""])  # row 7: blank separator
    client.update_range(lineups_tab, f"A1:B{BENCH_ROWS}", rows)

    client.freeze(lineups_tab, rows=BENCH_ROWS)

    return f"{lineups_tab}: inserted {BENCH_ROWS}-row bench sourced from {pool_tab!r}, frozen"
