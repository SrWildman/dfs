"""Task 5.2 -- a typo guard on Lineups' typed Name column (column A within
each `LINEUPS_NAME_BLOCKS` range): a live type-ahead dropdown against
PlayerPoolRaw's own Name column, non-strict (a name that doesn't match
gets a dismissible warning, not a hard block -- Sam might legitimately
type a name before this week's slate has synced, or PlayerPoolRaw is
momentarily stale).

Found already partially in place on the live sheet -- ONE_OF_RANGE
validation against `PlayerPoolRaw!$A:$A` already existed -- but as
`strict=true` with no `showCustomUi`, the opposite of what this task
asks for: a hard-reject with no dropdown arrow, instead of a warn-only
type-ahead. `setDataValidation` replaces whatever rule is already on a
range rather than stacking, so re-applying this is safe and idempotent
regardless of that prior state.
"""

from __future__ import annotations

from dfs.sheet_links import PLAYER_POOL_RAW_TAB
from dfs.sheet_style import POOL_RAW_ROWS
from dfs.sheets import SheetsClient

_SOURCE = f"{PLAYER_POOL_RAW_TAB}!$A$2:$A${POOL_RAW_ROWS}"


def add_lineups_typo_guard(client: SheetsClient, lineups_tab: str, name_blocks: list[tuple[int, int]]) -> str:
    if not client.tab_exists(lineups_tab):
        return f"{lineups_tab}: not present -- skipped"

    for start, end in name_blocks:
        client.set_range_dropdown_validation(lineups_tab, f"A{start}:A{end}", source=_SOURCE, strict=False)

    return (
        f"{lineups_tab}: typo-guard dropdown (non-strict, against {PLAYER_POOL_RAW_TAB}'s Name "
        f"column) applied to column A across {len(name_blocks)} lineup block(s)"
    )
