"""Plain-text report for one lineup's late-swap suggestions (`late_swap_search.LineupSuggestions`).

Order, as asked: (a) the best full re-fill, (b) the best 2-for-2 swaps, (c) the best 1-for-1 swaps per open
slot. Every suggestion shows who goes out and who comes in, the points gained, the salary left and, when it
adds or breaks a stack, bring-back or DST/same-team pair, a note saying so. Only improvements are listed;
when nothing beats the current lineup the report says so.
"""

from __future__ import annotations

from dfs.late_swap_search import LineupSuggestions, Swap, SwapPlayer
from dfs.models import ROSTER_SLOTS


def _money(amount: int) -> str:
    return f"${amount:,}"


def _who(player: SwapPlayer | None) -> str:
    if player is None:
        return "(empty)"
    avail = f" [{player.avail}]" if player.avail else ""
    return f"{player.name} ({player.team}, {_money(player.salary)}, {player.value:.1f}){avail}"


def _swap_lines(swap: Swap, indent: str) -> list[str]:
    lines = []
    for index in sorted(swap.changes):
        out, new = swap.changes[index]
        lines.append(f"{indent}{ROSTER_SLOTS[index]:<5} {_who(out)}  ->  {_who(new)}")
    for note in swap.notes:
        lines.append(f"{indent}note: {note}")
    return lines


def _headline(swap: Swap) -> str:
    return f"{swap.delta:+.1f} pts, {_money(swap.salary_left)} left"


def format_suggestions(suggestions: LineupSuggestions, *, metric: str) -> list[str]:
    """The report for one lineup as lines of text (no colour, no markup)."""
    state = suggestions.state
    open_names = ", ".join(s.slot for s in state.open) or "none"
    lines = [
        f"Open slots: {open_names}.  Locked salary {_money(state.fixed_salary)}, "
        f"{_money(state.cap - state.fixed_salary)} for the open slots.  Ranked by {metric}."
    ]
    if not state.open:
        return lines + ["Nothing left to swap: every slot is locked."]

    refill = suggestions.refill
    if refill is not None and refill.delta > 0 and refill.changes:
        cut = "" if refill.exact else "  (search cut off at its time limit: a better one may exist)"
        lines.append(f"a. Best full re-fill: {_headline(refill)}{cut}")
        lines += _swap_lines(refill, "     ")
    else:
        lines.append("a. Best full re-fill: none -- your open slots are already the best fit.")
    if suggestions.pairs:
        lines.append("b. Best 2-for-2 swaps:")
        for swap in suggestions.pairs:
            lines.append(f"   {_headline(swap)}")
            lines += _swap_lines(swap, "     ")
    else:
        lines.append("b. Best 2-for-2 swaps: none that fit the cap and beat your lineup.")
    if suggestions.singles:
        lines.append("c. Best 1-for-1 swaps:")
        for slot in state.open:
            swaps = suggestions.singles.get(slot.index)
            if not swaps:
                continue
            lines.append(f"   {slot.slot} (now {_who(slot.current)}):")
            for swap in swaps:
                _, new = swap.changes[slot.index]
                note = f"  -- {'; '.join(swap.notes)}" if swap.notes else ""
                lines.append(f"     {_headline(swap)}:  {_who(new)}{note}")
    else:
        lines.append("c. Best 1-for-1 swaps: none that fit the cap and beat your lineup.")
    if suggestions.nothing_better:
        lines.append(f"Nothing beats this lineup on {metric} (within the salary cap and the lineup rules).")
    return lines
