"""Exposure against the field (Plan K item 8): each player's share of Sam's GPP entries beside the field's
ownership of him. GPP only: cash is one lineup.

- **Before the close** the field's number is our predicted GPP ownership (EdgeRaw `GppOwn`) and the entries
  are what `dfs entries record` saved (`data/entered/`), counted per entry (a lineup in three contests counts
  three times, the way DraftKings counts exposure).
- **After the close** it is the actual ownership (`%Drafted` in the GPP standings he entered, averaged over
  those contests weighted by their size) and his entries are his own rows in those standings.

A player is an **overweight** when Sam's exposure is `GAP` (15 points) or more above the field's, an
**underweight** when it is that much below (a chalk player he has none of is the usual one). Pure.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass

GAP = 0.15
SHOWN = 8


@dataclass(frozen=True)
class Row:
    name: str
    mine: float  # share of his GPP entries
    field: float | None  # predicted or actual ownership
    count: int  # entries holding him

    @property
    def diff(self) -> float | None:
        return None if self.field is None else self.mine - self.field


def rows(entries: list[tuple[str, ...]], field: dict[str, float], *, key=lambda n: n) -> list[Row]:  # noqa: ANN001
    """Every player in his entries, and every player the field owns at `GAP` or more (so a chalk player he
    skipped shows up), with `key` mapping a display name to the `field` dict's key."""
    n = len(entries)
    if n == 0:
        return []
    counts = Counter(name for lineup in entries for name in set(lineup))
    names = {key(name): name for name in counts}
    out = [Row(name, c / n, field.get(key(name)), c) for name, c in counts.items()]
    for k, share in field.items():
        if k not in names and share is not None and share >= GAP:
            out.append(Row(k, 0.0, share, 0))
    return out


def view(entries: list[tuple[str, ...]], field: dict[str, float], *, key=lambda n: n, display=None) -> dict:  # noqa: ANN001
    """The page's block: how many entries, the biggest overweights and underweights, and every row."""
    all_rows = rows(entries, field, key=key)
    if display:
        all_rows = [Row(display.get(r.name, r.name), r.mine, r.field, r.count) for r in all_rows]
    over = sorted(
        (r for r in all_rows if r.diff is not None and r.diff >= GAP), key=lambda r: (-r.diff, r.name)
    )
    under = sorted(
        (r for r in all_rows if r.diff is not None and r.diff <= -GAP), key=lambda r: (r.diff, r.name)
    )
    return {
        "entries": len(entries),
        "over": [asdict(r) | {"diff": r.diff} for r in over[:SHOWN]],
        "under": [asdict(r) | {"diff": r.diff} for r in under[:SHOWN]],
        "rows": [
            asdict(r) | {"diff": r.diff}
            for r in sorted(all_rows, key=lambda r: (-r.mine, -(r.field or 0), r.name))
        ],
        "unknown": sorted(r.name for r in all_rows if r.field is None),
    }
