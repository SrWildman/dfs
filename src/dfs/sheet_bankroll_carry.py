"""`dfs bankroll carry`: a sheet's Starting Bankroll is the Ending Bankroll of the slate before it, by date
(`slate_chain`; Sam, Nov 23). Week 12 main can be set up before Thanksgiving is played: its start is then
Thanksgiving's start, and this re-reads Thanksgiving's Ending once that slate is closed.

Only the three Ending -> Starting pairs `dfs week new` carries (`week.BANKROLL_CARRYOVER_CELLS`: DraftKings,
PrizePicks, Underdog). The typed inputs that persist (Budget, Deposited, Withdrawn, Weekly Budget) are this
sheet's own and are never touched. Reads the previous sheet, never writes it. Every write is one batch, to the
same cells `week new` uses, and is read back.
"""

from __future__ import annotations

from dataclasses import dataclass

from dfs.sheets import SheetsClient
from dfs.week import BANKROLL_CARRYOVER_CELLS

# (previous sheet's Ending cell, this sheet's Starting cell): the carry-forward pairs whose cells differ.
STARTING_PAIRS: list[tuple[str, str]] = [(old, new) for old, new in BANKROLL_CARRYOVER_CELLS if old != new]


class CarryError(Exception):
    """The read-back after the write did not match what was written."""


@dataclass(frozen=True)
class CellCarry:
    cell: str  # this sheet's Starting cell
    source_cell: str  # the previous sheet's Ending cell
    before: float | None
    after: float | None  # read back after the write (== before when nothing was written)
    source: float | None

    @property
    def changed(self) -> bool:
        return self.source is not None and self.before != self.source


def _number(client: SheetsClient, tab: str, cell: str) -> float | None:
    rows = client.read_range_unformatted(tab, cell)
    v = rows[0][0] if rows and rows[0] else None
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else None


def carry_starting(
    source: SheetsClient, target: SheetsClient, tab: str, *, write: bool = True
) -> list[CellCarry]:
    """Copy `source`'s Ending balances to `target`'s Starting balances (`STARTING_PAIRS`), in one batch, and
    read them back. A source cell that is not a number is left alone on the target. `write=False` only reports
    what would change. Raises CarryError when the read-back differs from what was written."""
    plan = []
    for old, new in STARTING_PAIRS:
        plan.append((old, new, _number(source, tab, old), _number(target, tab, new)))
    updates = {new: [[src]] for old, new, src, before in plan if src is not None and before != src}
    if write and updates:
        target.update_ranges(tab, updates)
    out = []
    for old, new, src, before in plan:
        written = write and new in updates
        after = _number(target, tab, new) if written else before
        if written and (after is None or abs(after - src) > 0.005):
            raise CarryError(f"{tab}!{new} reads {after!r} after writing {src!r}")
        out.append(CellCarry(new, old, before, after, src))
    return out


def describe(carries: list[CellCarry]) -> list[str]:
    """One line per Starting cell: `B1 Starting 150.00 -> 162.40 (from B2)`, or that it already matched."""

    def money(v: float | None) -> str:
        return "blank" if v is None else f"{v:,.2f}"

    lines = []
    for c in carries:
        if c.source is None:
            lines.append(
                f"{c.cell}: the previous sheet's {c.source_cell} is not a number -- left at {money(c.before)}"
            )
        elif c.changed:
            lines.append(
                f"{c.cell}: {money(c.before)} -> {money(c.after)} (the previous sheet's {c.source_cell})"
            )
        else:
            lines.append(f"{c.cell}: {money(c.before)}, already the previous sheet's {c.source_cell}")
    return lines
