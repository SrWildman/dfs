"""One snapshot of every team's standing in the league per scored week: the odds history and the power
rankings' week-over-week move.

`data/season/history/<league>.json` maps "through" (the last scored week the snapshot is as of) to each
team's power rank (rank by team value), value and odds. A `dfs season sync` / `outlook` writes the current
week's (replacing it); `dfs season outlook --backfill` rebuilds the earlier weeks from the league's own
matchup history (`outlook_check`), tagged `backfill`. Nothing here is ever sent anywhere.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from dfs.paths import DATA_DIR

HISTORY_DIR = DATA_DIR / "season" / "history"
TEAM_FIELDS = (
    "rank", "value", "p_playoffs", "p_bye", "p_division", "exp_wins", "p_survive", "exp_weeks", "p_last",
)  # fmt: skip


def _path(league: str) -> Path:
    return HISTORY_DIR / f"{league}.json"


def load(league: str) -> dict[int, dict]:
    """through -> {"source", "taken", "teams": {roster_id: {field: value}}}."""
    try:
        raw = json.loads(_path(league).read_text())
    except (OSError, ValueError):
        return {}
    return {
        int(k): {**v, "teams": {int(r): t for r, t in (v.get("teams") or {}).items()}} for k, v in raw.items()
    }


def save_snapshot(
    league: str, through: int, teams: dict[int, dict], source: str, *, keep_sync: bool = True
) -> bool:
    """Write week `through`'s snapshot. A backfill never replaces a snapshot a real sync wrote (`keep_sync`).
    Returns whether it wrote."""
    data = load(league)
    if keep_sync and source == "backfill" and data.get(through, {}).get("source") == "sync":
        return False
    data[through] = {
        "source": source,
        "taken": datetime.now().isoformat(timespec="minutes"),
        "teams": {int(r): {k: _clean(v) for k, v in t.items() if k in TEAM_FIELDS} for r, t in teams.items()},
    }
    HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    _path(league).write_text(json.dumps({str(k): v for k, v in sorted(data.items())}, indent=0))
    return True


def _clean(v: object) -> object:
    if isinstance(v, float):
        return None if v != v else round(v, 4)
    return v


def previous(data: dict[int, dict], through: int) -> tuple[int, dict[int, dict]] | None:
    """The latest snapshot before `through`: (its week, teams) or None."""
    earlier = [k for k in data if k < through]
    if not earlier:
        return None
    k = max(earlier)
    return k, data[k]["teams"]


def series(data: dict[int, dict], roster_id: int, field: str) -> list[tuple[int, float | None]]:
    """(through, value) for one team, oldest first."""
    return [(k, data[k]["teams"].get(roster_id, {}).get(field)) for k in sorted(data)]


def rank_move(data: dict[int, dict], through: int, roster_id: int, rank_now: int) -> int | None:
    """Places gained since the previous snapshot (positive = moved up); None with no earlier snapshot."""
    prev = previous(data, through)
    if prev is None:
        return None
    before = prev[1].get(roster_id, {}).get("rank")
    return None if before is None else int(before) - rank_now


def snapshot_teams(base: dict, chopped: bool, view=None) -> dict[int, dict]:
    """The snapshot dict for `save_snapshot` from an evaluator's `base()` outcomes: power rank = rank by team
    value (the same rank the sheet's Team value rank shows)."""
    order = sorted(base, key=lambda r: -base[r].value)
    out = {}
    for i, rid in enumerate(order):
        o = base[rid]
        out[rid] = {
            "rank": i + 1,
            "value": o.value,
            "p_playoffs": None if chopped else o.p_playoffs,
            "p_bye": None if chopped else o.p_bye,
            "p_division": None if chopped else o.p_division,
            "exp_wins": None if chopped else o.exp_wins,
            "p_survive": o.p_survive_now if chopped else None,
            "exp_weeks": o.expected_weeks if chopped else None,
            "p_last": o.p_last_standing if chopped else None,
        }
    return out
