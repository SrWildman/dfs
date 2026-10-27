"""Late swap by position: where a GPP lineup stands on the top-1% line, and what each swap does to its overlap
with the field.

"Late swap isn't always about best projection. I swap when I'm behind and need pieces to get different,
or when my different pieces already hit and I want to get on chalk to hold position." (Sam, 2026-10-25.) So
a swap is labelled by what it does to the lineup's overlap with the field, read from ownership:

- **LEVERAGE**: the players coming in are less owned than the ones going out (the lineup gets more different);
- **HOLD**: they are more owned (the lineup moves toward the field and keeps its place);
- **EVEN**: the swap moves the lineup's summed ownership by under a point either way.

and each GPP lineup gets its **position** from the live state (`late_swap_field.LiveLines`, the draws with the
started games known): its median final score against the top-1% line in the same draws, the chance it finishes
above the line, and, as a finish, either the real field's own order statistics (`LiveLines.ladder`, read from
the export: "top 12%") or, with no export, an approximation from the measured offsets of the lines
(`finish_anchors`: the field mean + 63.2 is the top 1%, + 31.9 the top 10%, + 0.6 the double-up line at about
45%), marked "about".

The position is bucketed by its chance of finishing above the top-1% line (`bucket_of`): `ahead` at 10% or
more, `near` from 1% to 10%, `behind` below 1% (by the gap in the line's own spread nearly every lineup is
"behind" at 1 pm: the median sampled GPP lineup is 60 points under the line and the spread of the gap is a few
points); `late_swap_retro` reports, per bucket, how often the swap that maximises Top 1% is a LEVERAGE one.

Pure: the draws, the lines and the ownership come in as arguments.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

import numpy as np

from dfs.config import SimLinesConfig

if TYPE_CHECKING:
    from dfs.late_swap_field import LiveLines
    from dfs.late_swap_search import Swap
    from dfs.sim.simulate import PlayerSpec

LEVERAGE = "LEVERAGE"
HOLD = "HOLD"
EVEN = "EVEN"
EVEN_BELOW = 0.01  # a swap that moves the lineup's summed ownership by under a point is neither

BEHIND = "behind"
NEAR = "near"
AHEAD = "ahead"
BUCKETS = (BEHIND, NEAR, AHEAD)
AHEAD_P = 0.10  # at least this chance of finishing above the top-1% line is "ahead"
NEAR_P = 0.01  # at least this (and under AHEAD_P) is "near"

# The finish ladder `LiveLines` is built with from a real export: the top X of the field, as fractions.
FINISH_LADDER = (0.01, 0.02, 0.03, 0.05, 0.075, 0.10, 0.15, 0.20, 0.30, 0.40, 0.50)
DOUBLE_UP_FRACTION = 0.45  # a Double Up pays about this share of its entrants (`late_swap_field`)


# ---------------------------------------------------------------------------------------------------------
# A swap's overlap
# ---------------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class SwapOverlap:
    """What a swap does to the lineup's overlap with the field. Ownership as fractions (0.146 is 14.6%),
    summed over the players going out and coming in; `label` is "" when any of them has no ownership."""

    label: str
    own_out: float | None = None
    own_in: float | None = None

    @property
    def delta(self) -> float | None:
        return None if self.own_out is None or self.own_in is None else self.own_in - self.own_out


def swap_overlap(swap: Swap, own: Mapping[str, float]) -> SwapOverlap:
    """LEVERAGE, HOLD or EVEN for `swap` from `own` (`{name: ownership fraction}`); no label where a player
    has no ownership (`own` is empty until TFFB publishes it)."""
    out = [old.name for old, _ in swap.changes.values() if old is not None]
    ins = [new.name for _, new in swap.changes.values()]
    if not own or any(n not in own for n in (*out, *ins)):
        return SwapOverlap("")
    own_out = float(sum(own[n] for n in out))
    own_in = float(sum(own[n] for n in ins))
    move = own_in - own_out
    label = EVEN if abs(move) < EVEN_BELOW else (HOLD if move > 0 else LEVERAGE)
    return SwapOverlap(label, own_out, own_in)


def overlap_text(o: SwapOverlap | None) -> str:
    """`LEVERAGE (out 31.0% -> in 8.0%)`, or "" for a swap with no label."""
    if o is None or not o.label:
        return ""
    return f"{o.label} (own {o.own_out:.0%} -> {o.own_in:.0%})"


def attach_overlap(swaps: Sequence[Swap], own: Mapping[str, float]) -> None:
    for swap in swaps:
        swap.overlap = swap_overlap(swap, own)


# ---------------------------------------------------------------------------------------------------------
# A lineup's position
# ---------------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Position:
    gap: float  # median (lineup score - top-1% line) over the draws, in points; negative = behind
    spread: float  # the standard deviation of that gap over the draws
    p_top1: float  # the share of draws the lineup is at or above the line
    finish: float | None  # the median finish as a fraction of the field (0.12 = top 12%); None: outside it
    exact: bool  # True: from the real field's order statistics; False: from the lines' offsets

    @property
    def bucket(self) -> str:
        return bucket_of(self.p_top1)


def bucket_of(p_top1: float) -> str:
    """`behind` / `near` / `ahead` of the top-1% line, by the lineup's chance of finishing above it."""
    return AHEAD if p_top1 >= AHEAD_P else (NEAR if p_top1 >= NEAR_P else BEHIND)


def finish_anchors(lines: SimLinesConfig | None = None) -> list[tuple[float, float]]:
    """`(points below the top-1% line, finish fraction)` from the measured line offsets, closest first."""
    cfg = lines or SimLinesConfig()
    return [
        (0.0, 0.01),
        (cfg.gpp_top1_offset - cfg.gpp_top10_offset, 0.10),
        (cfg.gpp_top1_offset - cfg.double_up_offset, DOUBLE_UP_FRACTION),
    ]


def approx_finish(gap: float, lines: SimLinesConfig | None = None) -> float | None:
    """The finish fraction for a score `gap` points from the top-1% line (negative = below), interpolated
    between the anchors in log-fraction; at or above the line is the top 1%; below the double-up line None."""
    anchors = finish_anchors(lines)
    below = -gap
    if below <= 0:
        return anchors[0][1]
    if below >= anchors[-1][0]:
        return None
    for (d0, f0), (d1, f1) in zip(anchors, anchors[1:], strict=False):
        if below <= d1:
            t = (below - d0) / (d1 - d0)
            return float(math.exp(math.log(f0) + t * (math.log(f1) - math.log(f0))))
    return None


def ladder_finish(scores: np.ndarray, fractions: Sequence[float], ladder: np.ndarray) -> float | None:
    """The median over draws of the first ladder fraction the lineup's score reaches (`ladder[k, d]`: the
    score at the field's `fractions[k]` place in draw `d`); a draw below the whole ladder counts as 1.0. None
    when the median draw is outside the ladder (worse than the top half)."""
    beat = scores[None, :] >= ladder
    first = beat.argmax(axis=0)
    frac = np.where(beat.any(axis=0), np.asarray(fractions, dtype=float)[first], 1.0)
    median = float(np.median(frac))
    return None if median >= 1.0 else median


def lineup_position(live: LiveLines, lineup: Sequence[PlayerSpec]) -> Position:
    """Where `lineup` stands against `live`'s top-1% line. A player the draws do not hold raises KeyError
    (`LiveLines.scores`)."""
    scores = live.scores(lineup)
    gap = scores - live.top_line
    finish: float | None
    if live.ladder is not None and live.ladder_fractions:
        finish, exact = ladder_finish(scores, live.ladder_fractions, live.ladder), True
    else:
        finish, exact = approx_finish(float(np.median(gap))), False
    return Position(
        gap=float(np.median(gap)),
        spread=float(gap.std()),
        p_top1=float((gap >= 0).mean()),
        finish=finish,
        exact=exact,
    )


def position_text(p: Position) -> str:
    """`top 12%, 18.0 pts behind the top-1% line (Top 1% 3.2%)`; `about top 12%` where the finish is
    approximated, `outside the money places` where it falls below the double-up line."""
    if p.finish is None:
        finish = "outside the top half" if p.exact else "below the double-up line"
    else:
        finish = f"{'' if p.exact else 'about '}top {100 * p.finish:.0f}%"
    where = (
        f"{abs(p.gap):.0f} pts {'behind' if p.gap < 0 else 'ahead of'} the top-1% line"
        if abs(p.gap) >= 0.5
        else "on the top-1% line"
    )
    return f"{finish}, {where} (Top 1% {p.p_top1:.1%})"
