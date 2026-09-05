"""Pure logic for `dfs lineups late-swap`: cross-references a built
lineup's typed player names against EdgeRaw's current numbers and each
player's real kickoff time (`GameStart`, added to EdgeRaw in Phase 5
specifically for this), so a player whose game hasn't started yet shows up
as swappable and one who's already locked doesn't.

Slot order within one Lineups block follows `models.ROSTER_SLOTS` exactly.
See `weekly_reset.py`'s `LINEUPS_NAME_BLOCKS` docstring for how a block's
`(start, end)` maps to 9 player rows followed by a trailing salary-total
row -- callers pass this module exactly the 9 player-row names, in
`ROSTER_SLOTS` order, already sliced out of that range.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import pandas as pd

from dfs.models import FLEX_ELIGIBLE, ROSTER_SLOTS


@dataclass
class SlotStatus:
    slot: str  # QB/RB/.../FLEX/DST, from ROSTER_SLOTS
    name: str  # as typed in the sheet; "" for an unfilled slot
    found: bool  # matched a row in EdgeRaw by Name
    locked: bool | None  # None when GameStart is missing/unparseable
    position: str | None = None
    team: str | None = None
    proj_pts: float | None = None
    leverage: float | None = None
    flag: str | None = None


def _parse_game_start(value) -> datetime | None:
    if value is None or (isinstance(value, float) and pd.isna(value)) or not str(value).strip():
        return None
    try:
        return pd.Timestamp(value, tz="UTC").to_pydatetime()
    except (ValueError, TypeError):
        return None


def lineup_slot_status(names: list[str], edge: pd.DataFrame, *, now: datetime) -> list[SlotStatus]:
    """`names` must be `len(ROSTER_SLOTS)` long, in `ROSTER_SLOTS` order.
    A blank slot or a name with no match in `edge` (a bye-week leftover, a
    typo, a slot not filled in yet) comes back with `found=False` and
    `locked=None` rather than raising -- checking a half-built lineup
    mid-week is a normal thing to do, not an error."""
    if len(names) != len(ROSTER_SLOTS):
        raise ValueError(f"expected {len(ROSTER_SLOTS)} names (one per ROSTER_SLOTS), got {len(names)}")

    by_name = edge.set_index("Name")
    statuses = []
    for slot, raw_name in zip(ROSTER_SLOTS, names, strict=True):
        name = (raw_name or "").strip()
        if not name or name not in by_name.index:
            statuses.append(SlotStatus(slot=slot, name=name, found=False, locked=None))
            continue

        row = by_name.loc[name]
        if isinstance(row, pd.DataFrame):  # duplicate Name in EdgeRaw -- take the first, don't crash
            row = row.iloc[0]

        game_start = _parse_game_start(row.get("GameStart"))
        locked = None if game_start is None else now >= game_start
        statuses.append(
            SlotStatus(
                slot=slot,
                name=name,
                found=True,
                locked=locked,
                position=row.get("Position"),
                team=row.get("Team"),
                proj_pts=row.get("ProjPts"),
                leverage=row.get("Leverage"),
                flag=(row.get("Flag") or None),
            )
        )
    return statuses


def swap_candidates(
    edge: pd.DataFrame, slot: str, exclude_names: set[str], *, now: datetime, top: int = 5
) -> pd.DataFrame:
    """Players eligible for `slot` (FLEX allows RB/WR/TE) whose game hasn't
    started yet, excluding anyone already rostered in this lineup, ranked
    by Leverage descending -- worth a look for a late swap into this slot.
    A player with no parseable `GameStart` is treated as NOT eligible
    (excluded, not included) -- better to under-suggest than to recommend
    a swap into a player whose lock status can't actually be confirmed."""
    eligible_positions = {p.value for p in FLEX_ELIGIBLE} if slot == "FLEX" else {slot}
    pool = edge[edge["Position"].isin(eligible_positions) & ~edge["Name"].isin(exclude_names)].copy()

    game_starts = pool["GameStart"].apply(_parse_game_start)
    still_open = game_starts.notna() & (game_starts > now)
    pool = pool[still_open]

    return pool.sort_values("Leverage", ascending=False, na_position="last").head(top).reset_index(drop=True)
