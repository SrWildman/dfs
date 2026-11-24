"""The Lineups tab in one window: which columns stay visible, and the collapsed column groups that fold the
rest.

Audit item 1 (Nov 2026, Sam approved it for Thanksgiving on): with Lineups in one half of a split screen, only
the columns he builds from show. Every other column folds into a collapsed column group (the +/- above the
column letters), so he can open any of them by hand. **Display only:** nothing moves, nothing is renamed and
nothing is deleted.

**Visible** (`VISIBLE`): the audit's 13 (Name, Pos., Team, Opp., DK Sal, Pts, CalPts, ValAdj, Ceil, Own%,
Flags, Edge, Leverage), plus two Sam added on Nov 23 when the audit's letters turned out to hide them:

- the scoreboard's columns (`sheet_lineup_sim.SIM_SLOT_HEADERS`: Val, ValAdj, Hit3x%, Boom%). Typical, Big
  night, Cash % and Top 1% sit on each Total row in those four columns, so folding them would hide the
  scoreboard ("Keep K-N visible");
- `Issues`, the one column that says a lineup is broken ("Keep Issues visible").

**Folds** (`fold_runs`): every run of other columns, found by header, never by letter, so a column inserted
later folds with its neighbours instead of shifting the fold onto the wrong columns. Each run is one depth-1
group; the visible columns between runs keep them apart (Sheets merges touching groups of the same depth).

**The zones stay** (`group_plan`): GAME, CEIL, MOVE, WX and USAGE (`sheet_links.zone_column_ranges`) are
still groups of their own, one level deeper inside the fold that holds them, so expanding a fold shows the
zone labels with each zone still shut. `Leverage` is a CEIL zone member and stays visible, so CEIL is split
around it: `CeilPct` on its left, `OwnStatus`..`CeilM` on its right.

`sheet_lineup_columns` reads the header, applies this plan and reads it back.
"""

from __future__ import annotations

from collections.abc import Sequence

from dfs.sheet_lineup_sim import SIM_SLOT_HEADERS
from dfs.sheets import column_letter

# The audit's 13 (Nov 2026), in the tab's order.
AUDIT_VISIBLE = (
    "Name",
    "Pos.",
    "Team",
    "Opp.",
    "DK Sal",
    "Pts",
    "CalPts",
    "ValAdj",
    "Ceil",
    "Own%",
    "Flags",
    "Edge",
    "Leverage",
)
ISSUES_HEADER = "Issues"
# What stays visible: the audit's 13, the scoreboard's columns and Issues (Sam, Nov 23).
VISIBLE: tuple[str, ...] = tuple(dict.fromkeys((*AUDIT_VISIBLE, *SIM_SLOT_HEADERS, ISSUES_HEADER)))

# One group: 0-indexed first and last column (inclusive), depth (1 = outermost), collapsed.
Group = tuple[int, int, int, bool]


class LineupColumnsError(ValueError):
    """The Lineups header is missing a column that should stay visible (nothing is written)."""


def missing_visible(header: Sequence[str]) -> list[str]:
    """The `VISIBLE` names the header does not have."""
    names = {str(h).strip() for h in header}
    return [name for name in VISIBLE if name not in names]


def fold_runs(header: Sequence[str]) -> list[tuple[int, int]]:
    """Each run of columns to fold (0-indexed, inclusive): every header cell not in `VISIBLE`, blank ones
    included, grouped into contiguous runs. Raises `LineupColumnsError` when a visible column is missing,
    since the runs would then swallow it silently."""
    missing = missing_visible(header)
    if missing:
        raise LineupColumnsError(
            f"the Lineups header has no {', '.join(missing)} column; nothing folded (`dfs doctor` checks the "
            "column order)"
        )
    keep = set(VISIBLE)
    runs: list[tuple[int, int]] = []
    for i, name in enumerate(header):
        if str(name).strip() in keep:
            continue
        if runs and runs[-1][1] == i - 1:
            runs[-1] = (runs[-1][0], i)
        else:
            runs.append((i, i))
    return runs


def group_plan(header: Sequence[str], zones: Sequence[tuple[int, int]]) -> list[Group]:
    """Every column group Lineups should have: each fold run at depth 1, collapsed, and each zone's part
    inside a fold at depth 2, collapsed (a zone part that would cover its whole fold is left out: it would
    just be a second click). Zone parts that touch inside one fold are merged, as Sheets would merge them."""
    folds = fold_runs(header)
    plan: list[Group] = [(first, last, 1, True) for first, last in folds]
    inner: list[tuple[int, int]] = []
    for fold_first, fold_last in folds:
        parts = sorted(
            (max(zone_first, fold_first), min(zone_last, fold_last))
            for zone_first, zone_last in zones
            if max(zone_first, fold_first) <= min(zone_last, fold_last)
        )
        merged: list[tuple[int, int]] = []
        for first, last in parts:
            if merged and first <= merged[-1][1] + 1:
                merged[-1] = (merged[-1][0], max(last, merged[-1][1]))
            else:
                merged.append((first, last))
        inner += [part for part in merged if part != (fold_first, fold_last)]
    plan += [(first, last, 2, True) for first, last in inner]
    return sorted(plan, key=lambda g: (g[2], g[0]))


def span_text(header: Sequence[str], first: int, last: int) -> str:
    """`G AggPts` for one column, `X-AN Games..CeilPct (17)` for a run."""
    if first == last:
        return f"{column_letter(first)} {header[first]}"
    return (
        f"{column_letter(first)}-{column_letter(last)} {header[first]}..{header[last]} ({last - first + 1})"
    )


def visible_columns(header: Sequence[str], hidden: Sequence[bool]) -> list[str]:
    """The header names whose column is not hidden (`hidden` is index-aligned; a missing entry counts as
    shown)."""
    return [str(name) for i, name in enumerate(header) if not (i < len(hidden) and hidden[i])]
