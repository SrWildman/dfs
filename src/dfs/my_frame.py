"""Your frame: the slate as Sam sees it, from the rules box on Suggested. Pure: no sheet, no network.

`suggest_state` reads the `Adjust` and `Ownership` lines into `Adjustment`s and ownership fractions; `apply`
turns the synced EdgeRaw frame into "your" frame, one copy that every builder and judge reads (`lineups
suggest` and `--iterate`, `lineups late-swap`, the simulator's field and odds, chalk watch, the Cockpit):

- **MyPts** is the projection a run uses (`CalPts`, TFFB's `ProjPts` where CalPts is blank) with the player's
  adjustment: points added (`+2`), a percent of it (`-15%`) or a value set outright (`24`), never below zero.
  The copy carries it in a `MyPts` column and in the projection columns the builders read (`CalPts`, and the
  run's own metric), so nothing downstream needs to know the difference.
- **Own%** of an overridden player is his typed share (`40%` is 0.40). The field model, the duplicate
  estimates, chalk watch and the Lineups meter all read it from this column.

The copy is never saved: `data/current/edge.csv`, EdgeRaw and Model Check keep the raw numbers, so the
projection track record scores the model, not Sam's opinion of it."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

import pandas as pd

MY_PTS = "MyPts"
FALLBACK_METRIC = "ProjPts"  # stands in where CalPts is blank (the same fallback as `late_swap_search`)
ADD, PERCENT, SET = "add", "percent", "set"


@dataclass(frozen=True)
class Adjustment:
    """One `Adjust` item: `kind` ADD (points, signed), PERCENT (a signed percent of his projection, so
    -15 is -15%) or SET (the value itself)."""

    kind: str
    amount: float

    def apply(self, base: float | None) -> float | None:
        """His MyPts from the projection he had; None when there is none to adjust (a SET needs none)."""
        if self.kind == SET:
            return max(0.0, self.amount)
        if base is None:
            return None
        value = base + self.amount if self.kind == ADD else base * (1 + self.amount / 100)
        return max(0.0, value)

    def text(self) -> str:
        """How the item reads back: `+2`, `-15%`, `24`."""
        if self.kind == SET:
            return f"{self.amount:g}"
        sign = "+" if self.amount >= 0 else "-"
        return f"{sign}{abs(self.amount):g}{'%' if self.kind == PERCENT else ''}"


@dataclass
class MyFrame:
    """Your frame: the copy of the slate, and what was applied to it (for notes and the Cockpit)."""

    edge: pd.DataFrame
    adjust: dict[str, Adjustment] = field(default_factory=dict)  # only the ones that landed
    ownership: dict[str, float] = field(default_factory=dict)  # fractions: 0.4 is 40%
    notes: list[str] = field(default_factory=list)  # what could not be applied, and why

    def __bool__(self) -> bool:
        return bool(self.adjust or self.ownership)

    def lines(self) -> list[str]:
        """The active items, one short phrase each: `Nacua +2`, `Kyren -15%`, `Kyren 40% owned`."""
        return describe(self.adjust, self.ownership)

    def tag(self, name: str) -> str:
        """The note text for a player in a build: `Nacua +2 (yours)`; blank when none of his is yours."""
        return tag(name, self.adjust, self.ownership)


def tag(name: str, adjust: Mapping[str, Adjustment], ownership: Mapping[str, float]) -> str:
    """`Nacua +2 (yours)` / `Kyren 40% owned (yours)` for a player with either; blank for anyone else."""
    parts = []
    if name in adjust:
        parts.append(f"{name} {adjust[name].text()} (yours)")
    if name in ownership:
        parts.append(f"{name} {ownership[name]:.0%} owned (yours)")
    return "; ".join(parts)


def describe(adjust: Mapping[str, Adjustment], ownership: Mapping[str, float]) -> list[str]:
    """`Nacua +2`, `Kyren -15%`, `Stafford 24`, `Kyren 40% owned`: adjustments then ownership, by name."""
    return [f"{name} {adj.text()}" for name, adj in sorted(adjust.items())] + [
        f"{name} {share:.0%} owned" for name, share in sorted(ownership.items())
    ]


def _number(value: object) -> float | None:
    number = pd.to_numeric(value, errors="coerce")
    return None if pd.isna(number) else float(number)


def apply(
    edge: pd.DataFrame,
    adjust: Mapping[str, Adjustment],
    ownership: Mapping[str, float],
    metric: str = "CalPts",
) -> MyFrame:
    """`edge` as your frame. A name on neither list changes nothing, so with no adjustments the copy equals
    `edge` plus a `MyPts` column. An adjustment on a player with no projection to adjust (a bump or percent
    on a blank), and ownership while Own% is blank for the whole slate (no field to put it in: the other
    players' shares are unknown), are left out and say so in `notes`."""
    mine = edge.copy()

    def column(name: str) -> pd.Series:
        if name in mine.columns:
            return pd.to_numeric(mine[name], errors="coerce")
        return pd.Series(float("nan"), index=mine.index)

    # What a run projects for each player before your hand: the run's metric, CalPts falling back to ProjPts.
    base = column(metric) if metric != "CalPts" else column("CalPts").fillna(column(FALLBACK_METRIC))
    frame = MyFrame(edge=mine)
    mypts = base.copy()
    for name, adj in adjust.items():
        rows = mine["Name"].astype(str) == name
        if not rows.any():
            frame.notes.append(f"Adjust: {name} is not on the slate")
            continue
        value = adj.apply(_number(base[rows].iloc[0]))
        if value is None:
            frame.notes.append(f"Adjust: {name} has no projection to adjust (write a value: `{name} 12`)")
            continue
        mypts[rows] = value
        frame.adjust[name] = adj
    mine[MY_PTS] = mypts
    changed = mine["Name"].astype(str).isin(frame.adjust)
    if changed.any():
        for target in dict.fromkeys(("CalPts", metric)):  # the columns the builders and the simulator read
            values = column(target)
            values[changed] = mypts[changed]
            mine[target] = values
    if ownership:
        own = pd.to_numeric(mine["Own%"], errors="coerce") if "Own%" in mine.columns else None
        if own is None or not own.notna().any():
            frame.notes.append(
                "Ownership: Own% has not published yet, so there is no field to put your numbers in; "
                "they count once it does."
            )
        else:
            mine["Own%"] = own
            for name, share in ownership.items():
                rows = mine["Name"].astype(str) == name
                if not rows.any():
                    frame.notes.append(f"Ownership: {name} is not on the slate")
                    continue
                mine.loc[rows, "Own%"] = share
                frame.ownership[name] = share
    return frame
