"""Two warnings on built lineups, never a filter (Sam, 2026-10-19): a player in more than half of them, and a
GPP lineup whose total projected ownership is outside the target band.

- **Overused:** a player in more than `OVERUSE_SHARE` of the built lineups (needs `MIN_LINEUPS` built: with
  two lineups every shared player is "50%"). Week 6: Bijan and London were in 13 of 15 and sank the set.
- **Ownership meter:** a lineup MARKED GPP is warned when the nine players' summed Own% is under
  `GPP_OWN_LOW` or over `GPP_OWN_HIGH` (the top 1% of his 2026 large GPPs ran about 125-145%; the field about
  140%). Cash-marked and blank-marked lineups get no meter: he enters a cash lineup in a GPP on purpose.

Pure functions; the sheet carries the same two checks as Issues tokens on a lineup's Total row
(`sheet_style._overuse_formula`, `_own_meter_formula`).
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence

OVERUSE_SHARE = 0.5
MIN_LINEUPS = 3
GPP_OWN_LOW = 1.25
GPP_OWN_HIGH = 1.45


def overused(lineups: Sequence[Sequence[str]]) -> list[tuple[str, int, int]]:
    """`(name, lineups holding him, lineups built)` for every player in more than `OVERUSE_SHARE` of the built
    (complete, nine-name) lineups, most used first. Empty below `MIN_LINEUPS`."""
    built = [[n for n in lineup if n] for lineup in lineups]
    built = [lineup for lineup in built if len(lineup) == 9]
    if len(built) < MIN_LINEUPS:
        return []
    uses = Counter(name for lineup in built for name in set(lineup))
    over = [(n, c, len(built)) for n, c in uses.items() if c / len(built) > OVERUSE_SHARE]
    return sorted(over, key=lambda t: (-t[1], t[0]))


def overuse_line(over: list[tuple[str, int, int]]) -> str:
    if not over:
        return ""
    shown = ", ".join(f"{n} ({c} of {total})" for n, c, total in over[:6])
    more = f" and {len(over) - 6} more" if len(over) > 6 else ""
    return f"In more than half of your built lineups: {shown}{more}. One bad game sinks them all together."


def own_meter(own_sum: float | None, goal: str) -> str:
    """ "" when fine or not judged; else what is off, for a lineup marked GPP only (`goal` "gpp")."""
    if goal != "gpp" or own_sum is None or own_sum != own_sum:
        return ""
    if own_sum < GPP_OWN_LOW:
        return f"Own sum {own_sum:.0%}: under the {GPP_OWN_LOW:.0%}-{GPP_OWN_HIGH:.0%} GPP target"
    if own_sum > GPP_OWN_HIGH:
        return f"Own sum {own_sum:.0%}: over the {GPP_OWN_LOW:.0%}-{GPP_OWN_HIGH:.0%} GPP target"
    return ""
