"""Late swap, scored by the lineup simulator: what each candidate swap does to P(cash) and P(GPP).

`dfs lineups late-swap` finds swaps by projection (`late_swap_search`); this adds, for every swap it found
(the full re-fill, the 2-for-2 swaps and the 1-for-1 swaps), the change in the lineup's chance of reaching the
cash line and the GPP target, from `sim_inputs.swap_deltas` (the lineup and all its candidate versions
simulated in one run, so a difference is the swap's own effect). `--goal cash|gpp` then ranks the swaps by
the matching change, projection delta breaking ties. Because the search keeps only the best few by
projection, it is asked for `OVERSAMPLE` times as many when ranking by probability, and the list is cut back
to `top` after the sort.

Pure: the specs, the cash line and the target come in as arguments. A swap whose players the simulator cannot
rate (a name with no spec) keeps `delta_p_*` of None and sorts after the rated ones.
"""

from __future__ import annotations

from dataclasses import dataclass

from dfs.late_swap_search import LineupSuggestions, Swap
from dfs.sim.simulate import PlayerSpec
from dfs.sim_inputs import swap_deltas

GOALS = ("cash", "gpp")
OVERSAMPLE = 4


@dataclass(frozen=True)
class SimSettings:
    cash_line: float
    gpp_target: float
    goal: str = "cash"


def swap_names(swap: Swap) -> tuple[list[str], list[str]]:
    """(names going out, names coming in) for a swap; an empty slot has nobody going out."""
    out = [old.name for old, _ in swap.changes.values() if old is not None]
    new = [incoming.name for _, incoming in swap.changes.values()]
    return out, new


def _goal_delta(swap: Swap, goal: str) -> float | None:
    return swap.delta_p_cash if goal == "cash" else swap.delta_p_gpp


def _rank_key(swap: Swap, goal: str) -> tuple[int, float, float]:
    delta = _goal_delta(swap, goal)
    return (0 if delta is not None else 1, -(delta if delta is not None else 0.0), -swap.delta)


def attach_probabilities(
    suggestions: LineupSuggestions,
    lineup: list[PlayerSpec],
    specs: dict[str, PlayerSpec],
    settings: SimSettings,
    *,
    top: int,
) -> LineupSuggestions:
    """Fill `delta_p_cash` / `delta_p_gpp` on every swap in `suggestions`, re-rank pairs and singles by
    `settings.goal` and cut each list to `top`. Returns the same object."""
    rated: list[tuple[Swap, list[str], list[PlayerSpec]]] = []
    everything = [s for s in (suggestions.refill, *suggestions.pairs) if s is not None and s.changes]
    everything += [s for swaps in suggestions.singles.values() for s in swaps]
    for swap in everything:
        out, new = swap_names(swap)
        if all(n in specs for n in new):
            rated.append((swap, out, [specs[n] for n in new]))
    deltas = swap_deltas(
        lineup,
        [(out, ins) for _, out, ins in rated],
        cash_line=settings.cash_line,
        gpp_target=settings.gpp_target,
    )
    for (swap, _out, _ins), d in zip(rated, deltas, strict=True):
        swap.delta_p_cash, swap.delta_p_gpp = d.delta_p_cash, d.delta_p_gpp
    suggestions.pairs = sorted(suggestions.pairs, key=lambda s: _rank_key(s, settings.goal))[:top]
    suggestions.singles = {
        index: sorted(swaps, key=lambda s: _rank_key(s, settings.goal))[:top]
        for index, swaps in suggestions.singles.items()
    }
    return suggestions
