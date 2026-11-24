"""Applies the Lineups column fold (`lineup_columns`) to a sheet: finds the columns by header, groups and
collapses them, and reads the result back.

**Idempotent.** It reads the tab's groups and hidden columns first; when they already match the plan it writes
nothing. Otherwise it clears every column group on the tab (`addDimensionGroup` nests instead of replacing),
unhides the columns that stay visible (they are outside every group by then, so unhiding them cannot desync a
group's collapsed flag), and adds the whole nested plan in one request (`SheetsClient.apply_column_groups`).

**Read back.** The groups and the hidden flags are read again after the write; a group that did not land, or a
column shown or hidden against the plan, raises `LineupColumnsError`.

**Who else writes Lineups' groups.** Only `sheet_links.link_edge_columns` (`dfs setup link-edge`, `dfs setup
reorder-columns`, `dfs setup sheet`): it clears every group on the tab and rebuilds the zones alone, so those
commands run `fold` again right after. `dfs sync`, `dfs week new`, `dfs lineups clear` and `dfs setup polish`
never touch a column group (polish unhides only columns outside every group).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from dfs.lineup_columns import (
    VISIBLE,
    Group,
    LineupColumnsError,
    group_plan,
    span_text,
    visible_columns,
)
from dfs.sheet_links import zone_column_ranges
from dfs.sheets import SheetsClient, column_letter


@dataclass
class FoldResult:
    tab: str
    changed: bool
    folds: list[str] = field(default_factory=list)
    zones: list[str] = field(default_factory=list)
    visible: list[str] = field(default_factory=list)

    def lines(self) -> list[str]:
        head = (
            f"{self.tab}: folded {len(self.folds)} column group(s)"
            if self.changed
            else f"{self.tab}: already folded, nothing written"
        )
        out = [head]
        out.append(f"  visible ({len(self.visible)}): {', '.join(self.visible)}")
        out += [f"  folded: {fold}" for fold in self.folds]
        if self.zones:
            out.append(f"  zones kept inside the folds: {'; '.join(self.zones)}")
        return out


def _state(client: SheetsClient, tab: str, header: list[str]) -> tuple[list[Group], list[bool]]:
    groups = client.read_column_groups(tab)
    widths = client.get_column_widths(tab, column_letter(len(header) - 1))
    hidden = [bool(m.get("hiddenByUser")) for m in widths]
    return groups, hidden


def _runs(indices: list[int]) -> list[tuple[int, int]]:
    runs: list[tuple[int, int]] = []
    for i in indices:
        if runs and runs[-1][1] == i - 1:
            runs[-1] = (runs[-1][0], i)
        else:
            runs.append((i, i))
    return runs


def fold(client: SheetsClient, tab: str, *, header_row: int = 1) -> FoldResult:
    """Fold `tab` (Lineups) down to `VISIBLE`, found by the header in `header_row`. Raises
    `LineupColumnsError` (before any write) when the header lacks a visible column, or after the read-back
    when the sheet does not match the plan."""
    rows = client.read_range(tab, f"A{header_row}:{header_row}")
    header = [str(h).strip() for h in (rows[0] if rows else [])]
    if not header:
        raise LineupColumnsError(f"{tab!r} has an empty header row {header_row}; nothing folded")
    plan = group_plan(header, zone_column_ranges(header))
    expected_visible = [name for name in header if name in VISIBLE]

    groups, hidden = _state(client, tab, header)
    changed = not (groups == plan and visible_columns(header, hidden) == expected_visible)
    if changed:
        client.clear_column_groups(tab)
        keep = [i for i, name in enumerate(header) if name in VISIBLE]
        for first, last in _runs(keep):
            client.hide_columns(tab, column_letter(first), column_letter(last), hidden=False)
        client.set_column_group_control_before(tab)
        client.apply_column_groups(tab, plan)

        groups, hidden = _state(client, tab, header)
        if groups != plan:
            raise LineupColumnsError(
                f"{tab!r}: read back {len(groups)} column group(s) that do not match the {len(plan)} "
                f"planned: {groups} vs {plan}"
            )
    shown = visible_columns(header, hidden)
    if shown != expected_visible:
        extra = [name for name in shown if name not in expected_visible]
        lost = [name for name in expected_visible if name not in shown]
        raise LineupColumnsError(
            f"{tab!r}: read back the wrong visible columns (showing too: {extra or 'none'}; "
            f"hidden: {lost or 'none'})"
        )
    return FoldResult(
        tab=tab,
        changed=changed,
        folds=[span_text(header, first, last) for first, last, depth, _ in plan if depth == 1],
        zones=[span_text(header, first, last) for first, last, depth, _ in plan if depth > 1],
        visible=shown,
    )
