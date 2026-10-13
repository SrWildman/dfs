"""The FLEX rule: the player with the latest kickoff belongs in the FLEX slot.

DraftKings locks a roster slot when its player's game starts, so the later a player's game, the longer the
slot stays open to a swap. The FLEX can hold an RB, WR or TE, so it should hold whichever of them plays last.

**The rule** (Sam, Week 6). A legal lineup has one RB/WR/TE more than the base slots need (RB beyond 2, WR
beyond 3, or TE beyond 1): that position is the *flex position*, and the FLEX slot belongs to one of its
players. Among the players of that position, the one with the LATEST kickoff goes in FLEX and the others fill
the position's own slots. This module only ever SWAPS two players (the one who should be in FLEX with whoever
is in it now), so the other slots never move.

It leaves a lineup alone when it cannot be sure, and says why (`FlexPlan.status`):

- `ok`: the FLEX already holds the latest kickoff (or nothing needs deciding);
- `move`: swap `to_flex` with `from_flex`;
- `tie`: the latest kickoff is shared by two or more players, so there is no right answer;
- `locked`: either player's game has started, and a started player never moves;
- `unknown`: a kickoff is missing for someone at the flex position (nothing synced, an unmatched name);
- `incomplete`: fewer than nine players, or a shape that is not a legal lineup.

Pure: the callers (`dfs export`, the sheet's `Issues` formula and the Apps Script's "Fix FLEX order") each
read their own kickoffs and apply the result. `Code.gs` has the same rule in JavaScript
(`planFlexSwap`), and `tests/test_flex_order.py` / `tests/js` pin the two to the same cases.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

import pandas as pd

from dfs.kickoff import KICKOFF_TIMEZONE, format_et
from dfs.models import ROSTER_SLOTS

FLEX_SLOT = "FLEX"
# RB / WR / TE: the players each position's own slots hold before the FLEX (DK Classic).
BASE_SLOTS = {"RB": 2, "WR": 3, "TE": 1}
SUNDAY_NIGHT_FROM_HOUR = 19  # a Sunday kickoff at or after 7 pm Eastern is "SNF"


@dataclass(frozen=True)
class Seat:
    """One lineup slot: where he sits, who he is, what he plays and when his game kicks off (a real instant,
    `kickoff.parse_kickoff`; None when unknown)."""

    slot: str
    name: str
    position: str
    kickoff: datetime | None


@dataclass(frozen=True)
class FlexPlan:
    status: str
    to_flex: Seat | None = None
    from_flex: Seat | None = None

    @property
    def moves(self) -> bool:
        return self.status == "move"


def flex_position(seats: Sequence[Seat]) -> str | None:
    """The position with one player more than its base slots (RB beyond 2, WR beyond 3, TE beyond 1), or None
    when the lineup is not shaped like that (a missing player, two surplus positions)."""
    counts = {position: sum(1 for s in seats if s.position == position) for position in BASE_SLOTS}
    surplus = [p for p, base in BASE_SLOTS.items() if counts[p] == base + 1]
    others_exact = all(counts[p] == base for p, base in BASE_SLOTS.items() if p not in surplus)
    return surplus[0] if len(surplus) == 1 and others_exact else None


def plan_flex(seats: Sequence[Seat], now: datetime) -> FlexPlan:
    """What the FLEX rule says to do with these nine seats (in `ROSTER_SLOTS` order) at `now`."""
    if len(seats) != len(ROSTER_SLOTS) or any(not s.name or not s.position for s in seats):
        return FlexPlan("incomplete")
    position = flex_position(seats)
    flex = next((s for s in seats if s.slot == FLEX_SLOT), None)
    if position is None or flex is None or flex.position != position:
        return FlexPlan("incomplete")
    pool = [s for s in seats if s.position == position]
    if any(s.kickoff is None for s in pool):
        return FlexPlan("unknown")
    latest = max(s.kickoff for s in pool)
    last = [s for s in pool if s.kickoff == latest]
    if len(last) > 1:
        return FlexPlan("tie")  # no unique latest game: leave it as it is
    target = last[0]
    if target.slot == FLEX_SLOT:
        return FlexPlan("ok")
    if target.kickoff <= now or flex.kickoff <= now:
        return FlexPlan("locked", target, flex)
    return FlexPlan("move", target, flex)


def apply_swap(slots: Sequence[str], plan: FlexPlan, seats: Sequence[Seat]) -> list[str]:
    """`slots` (any per-slot values, in `ROSTER_SLOTS` order, e.g. the DK Upload cells) with the two players
    of `plan` exchanged. Unchanged when the plan does not move anyone."""
    out = list(slots)
    if not plan.moves:
        return out
    a = list(seats).index(plan.to_flex)
    b = next(i for i, s in enumerate(seats) if s.slot == FLEX_SLOT)
    out[a], out[b] = out[b], out[a]
    return out


def plays_label(kickoff: datetime) -> str:
    """Where a kickoff falls, the way Sam says it: `TNF`, `SNF`, `MNF`, otherwise `Sun 1:00 pm` (Eastern)."""
    local = pd.Timestamp(kickoff).tz_convert(KICKOFF_TIMEZONE)
    if local.weekday() == 3:
        return "TNF"
    if local.weekday() == 0:
        return "MNF"
    if local.weekday() == 6 and local.hour >= SUNDAY_NIGHT_FROM_HOUR:
        return "SNF"
    return format_et(kickoff, suffix=False)


def describe(plan: FlexPlan) -> str:
    """One plain sentence for a plan, as `dfs export` prints it: `Moved Puka Nacua to FLEX (plays SNF), Cooper
    Kupp to WR`."""
    if not plan.moves or plan.to_flex is None or plan.from_flex is None:
        return ""
    when = plays_label(plan.to_flex.kickoff) if plan.to_flex.kickoff else "later"
    return f"Moved {plan.to_flex.name} to FLEX (plays {when}), {plan.from_flex.name} to {plan.to_flex.slot}"
