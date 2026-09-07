"""Task 4.4 -- `dfs pool` CLI group: add/remove/list/clear a player's
EdgeRaw Pool tick by name from the terminal, without opening the sheet.

Matching is deliberately conservative: an exact (case-insensitive) name
match wins outright; otherwise every name CONTAINING the query is a
candidate. On more than one candidate, this never guesses -- it reports
every candidate (with position and salary, so they're distinguishable)
and does nothing, the same "don't guess, ask" contract this project
already applies to ambiguous names in `weekly_reset.py`/`late_swap.py`.

Reuses the Id-keyed preserve/restore built for `dfs sync` (see
`sources/edge.py`) only indirectly: this writes the Pool cell in place by
row, so a tick made here survives the next `dfs sync` exactly the way a
tick made by hand in the sheet does -- no separate persistence needed.
"""

from __future__ import annotations

from dataclasses import dataclass

from dfs.derived import EDGE_COLUMNS, EDGE_DATA_OFFSET
from dfs.sheets import SheetsClient, column_letter
from dfs.sources.edge import POOL_COLUMN

_NAME_IDX = EDGE_COLUMNS.index("Name") + EDGE_DATA_OFFSET
_POSITION_IDX = EDGE_COLUMNS.index("Position") + EDGE_DATA_OFFSET
_SALARY_IDX = EDGE_COLUMNS.index("Salary") + EDGE_DATA_OFFSET
_LAST_NEEDED_COL = column_letter(max(_NAME_IDX, _POSITION_IDX, _SALARY_IDX))

# Generous provisioned depth, same reasoning as EDGE_ROWS elsewhere.
_MAX_ROWS = 1000


@dataclass
class EdgePlayer:
    row: int  # 1-indexed real sheet row
    name: str
    position: str
    salary: str
    pooled: bool


def read_players(client: SheetsClient, edge_tab: str) -> list[EdgePlayer]:
    """Every real player row on EdgeRaw (blank Name = past the data,
    stops nothing but simply isn't included)."""
    rows = client.read_range(edge_tab, f"A2:{_LAST_NEEDED_COL}{_MAX_ROWS}")
    players = []
    for i, row in enumerate(rows):
        name = row[_NAME_IDX] if len(row) > _NAME_IDX else ""
        if not name:
            continue
        pool_val = row[0] if row else ""
        position = row[_POSITION_IDX] if len(row) > _POSITION_IDX else ""
        salary = row[_SALARY_IDX] if len(row) > _SALARY_IDX else ""
        players.append(
            EdgePlayer(
                row=i + 2,
                name=name,
                position=position,
                salary=salary,
                pooled=pool_val.strip().upper() == "TRUE",
            )
        )
    return players


def find_matches(players: list[EdgePlayer], query: str) -> list[EdgePlayer]:
    """Exact (case-insensitive) match wins outright; otherwise every name
    containing `query` is a candidate. Never fuzzier than substring --
    this writes to a real sheet on a single match, so a looser match
    (e.g. edit distance) risks ticking the wrong player silently."""
    q = query.strip().lower()
    if not q:
        return []
    exact = [p for p in players if p.name.lower() == q]
    if exact:
        return exact
    return [p for p in players if q in p.name.lower()]


def set_pool(client: SheetsClient, edge_tab: str, player: EdgePlayer, value: bool) -> None:
    client.update_range(edge_tab, f"{POOL_COLUMN}{player.row}", [[value]])


def clear_all(client: SheetsClient, edge_tab: str, players: list[EdgePlayer]) -> int:
    """Untick every currently-ticked player. Returns how many were
    cleared. Callers should confirm with the user first -- this touches
    every ticked row at once."""
    ticked = [p for p in players if p.pooled]
    if not ticked:
        return 0
    rows = [[False] for _ in ticked]
    # Cheaper as individual writes only if scattered; ticked rows are
    # rarely contiguous, so this stays one call per row like set_pool --
    # simplicity over a fragile "detect contiguous runs" optimization for
    # what's at most a couple hundred cells.
    for player in ticked:
        client.update_range(edge_tab, f"{POOL_COLUMN}{player.row}", [[False]])
    return len(rows)
