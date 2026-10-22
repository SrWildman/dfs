"""The clock the lineup searches measure their budgets in.

A search budget used to be wall-clock seconds, so the same slate gave different builds when the machine was
busy (another session, a sync, a test run): at 120 s two runs of one week agreed on 13 of 14 builds, and
under load the same week's best build changed. This clock counts the search's own work instead.
`late_swap_search._Search` reports every 512 nodes it visits (`advance`), and `now()` is those nodes divided
by `NODES_PER_SECOND`, so a "second" is a fixed amount of search, the cut-offs land on the same node every
run and the same inputs always give the same builds, on any machine and under any load. Only how long that
takes on the wall changes: about 120 s of budget is about 2 minutes of search on Sam's Mac (the calibration
is in `NODES_PER_SECOND`).

Every place that turned a budget into a deadline (`now() + seconds`) or measured what is left of one
(`deadline - now()`) uses `now()`. The phase timers that report how long a step took still read the wall
clock.

`wall_clock()` switches back to real seconds (for a test of the wall-clock path, or a caller that wants a real
limit). The counter only grows; a deadline is always relative to `now()` at the moment it is made, so it
does not matter where the counter stands.
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from contextlib import contextmanager

# Search nodes visited per budget second. Measured on Sam's Mac: a 120 s wall-clock search visits about this
# many nodes per second (see docs/SIM.md "Deterministic search budget").
NODES_PER_SECOND = 33_000.0
CHECK_EVERY = 512  # nodes between reports (`_Search._descend`)

_nodes = 0
_wall = False


def advance(nodes: int = CHECK_EVERY) -> None:
    """Record `nodes` more nodes of search work."""
    global _nodes
    _nodes += nodes


def total_nodes() -> int:
    return _nodes


def now() -> float:
    """Budget seconds: real time inside `wall_clock()`, else the search work done so far."""
    return time.monotonic() if _wall else _nodes / NODES_PER_SECOND


@contextmanager
def wall_clock() -> Iterator[None]:
    """Measure budgets in real seconds inside the block."""
    global _wall
    before, _wall = _wall, True
    try:
        yield
    finally:
        _wall = before
