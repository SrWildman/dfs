"""Two warnings on built lineups, never a filter (Sam, 2026-10-19): a player in more than half of them, and a
GPP lineup whose total projected ownership is outside the target band.

- **Overused:** a player in more than `OVERUSE_SHARE` of the built lineups (needs `MIN_LINEUPS` built: with
  two lineups every shared player is "50%"). Week 6: Bijan and London were in 13 of 15 and sank the set.
- **Ownership meter:** a lineup MARKED GPP is warned when the nine players' summed Own% is under
  `GPP_OWN_LOW` or over `GPP_OWN_HIGH` (the top 1% of his 2026 large GPPs ran about 125-145%; the field about
  140%). Cash-marked and blank-marked lineups get no meter: he enters a cash lineup in a GPP on purpose.
  On a small slate (`small_slate.py`) the band is the slate's own (`gpp_band`): the fixed band times how much
  heavier this slate's field lineup is than a main slate's (`field_own_sum` / `MAIN_FIELD_OWN_SUM`), never
  under the fixed band, so a 3-game slate where the average lineup sums to 270% doesn't call every lineup
  chalky. A main slate's band is the fixed one, exactly as before.

Pure functions apart from `band_for`, which asks `small_slate` whether the slate is small; the sheet carries
the same two checks as Issues tokens on a lineup's Total row (`sheet_style._overuse_formula`,
`_own_meter_formula`, still the fixed band).
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

OVERUSE_SHARE = 0.5
MIN_LINEUPS = 3
GPP_OWN_LOW = 1.25
GPP_OWN_HIGH = 1.45
# The field's average lineup on a main slate: the sum of the nine players' projected Own% that a lineup drawn
# at the projected ownership has on average, `sum(Own%^2)` over the slate. Median of the 2026 main slates,
# Weeks 3-11 (the last EdgeRaw snapshot of each, 0.97-1.68): the GPP band above sits around it.
MAIN_FIELD_OWN_SUM = 1.31


@dataclass(frozen=True)
class OwnBand:
    """The GPP meter's band of summed Own%. `slate` names a small slate's own band ("3-game slate")."""

    low: float = GPP_OWN_LOW
    high: float = GPP_OWN_HIGH
    slate: str = ""

    @property
    def text(self) -> str:
        own = f" ({self.slate}'s own)" if self.slate else ""
        return f"{self.low:.0%}-{self.high:.0%} GPP target{own}"


MAIN_BAND = OwnBand()


def field_own_sum(own: Iterable[float | None]) -> float:
    """The summed Own% of the field's average lineup: `sum(own^2)` (a player is in a share `own` of lineups
    and brings `own` to each). Blanks and negatives count as 0."""
    return float(sum(o * o for o in own if o is not None and o == o and o > 0))


def gpp_band(own: Iterable[float | None], slate: str) -> OwnBand:
    """A small slate's band: the fixed band times `field_own_sum / MAIN_FIELD_OWN_SUM`, never under it."""
    scale = max(1.0, field_own_sum(own) / MAIN_FIELD_OWN_SUM)
    if scale == 1.0:
        return MAIN_BAND
    return OwnBand(GPP_OWN_LOW * scale, GPP_OWN_HIGH * scale, slate)


def band_for(edge) -> OwnBand:  # noqa: ANN001 - a DataFrame
    """The band for this slate's EdgeRaw frame: the fixed band on a main slate (or with no Own%), the slate's
    own on a small one."""
    from dfs import small_slate

    if edge is None or "Own%" not in getattr(edge, "columns", ()) or not small_slate.frame_is_small(edge):
        return MAIN_BAND
    size = small_slate.SlateSize(small_slate.games_in_frame(edge))
    own = edge.drop_duplicates("Name")["Own%"] if "Name" in edge.columns else edge["Own%"]
    import pandas as pd

    return gpp_band(pd.to_numeric(own, errors="coerce").tolist(), size.label)


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


def own_meter(own_sum: float | None, goal: str, band: OwnBand = MAIN_BAND) -> str:
    """ "" when fine or not judged; else what is off, for a lineup marked GPP only (`goal` "gpp")."""
    if goal != "gpp" or own_sum is None or own_sum != own_sum:
        return ""
    if own_sum < band.low:
        return f"Own sum {own_sum:.0%}: under the {band.text}"
    if own_sum > band.high:
        return f"Own sum {own_sum:.0%}: over the {band.text}"
    return ""
