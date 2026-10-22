"""Sam's real Player Pool, saved at every `dfs sync` and `dfs lineups suggest` so a later backtest can build
from what he actually picked instead of a constructed pool (`sim/retro.pool_tags`).

One file per week, `data/pools/<season>-wk<NN>.json` (under `data/`, so never in git): a list of snapshots,
each `{"at": ISO UTC, "source": "sync" | "suggest", "sheet": title, "pool": {name: "Cash" | "GPP" | "Both"},
"rules": {label: text}}`. A snapshot identical to the latest one (pool and rules) is not stored again, so
the file grows only when he changes something. `pool_at(season, week, before)` is the snapshot a backtest
uses: the last one saved at or before the lock. Saving never fails the command that calls it.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

POOL_DIR = Path("data/pools")
MAX_SNAPSHOTS = 200  # a week of syncs and builds is a few dozen; this only stops a runaway file


@dataclass(frozen=True)
class PoolSnapshot:
    at: datetime
    source: str
    pool: dict[str, str]
    rules: dict[str, str] = field(default_factory=dict)
    sheet: str = ""

    def record(self) -> dict:
        return {
            "at": self.at.astimezone(UTC).isoformat(timespec="seconds"),
            "source": self.source,
            "sheet": self.sheet,
            "pool": dict(sorted(self.pool.items())),
            "rules": dict(self.rules),
        }

    @classmethod
    def from_record(cls, rec: Mapping) -> PoolSnapshot:
        return cls(
            at=datetime.fromisoformat(str(rec["at"])),
            source=str(rec.get("source", "")),
            pool={str(k): str(v) for k, v in dict(rec.get("pool", {})).items()},
            rules={str(k): str(v) for k, v in dict(rec.get("rules", {})).items()},
            sheet=str(rec.get("sheet", "")),
        )


def pool_path(season: int, week: int, directory: Path | None = None) -> Path:
    return (directory or POOL_DIR) / f"{season}-wk{week:02d}.json"


def load(season: int, week: int, directory: Path | None = None) -> list[PoolSnapshot]:
    """Every snapshot saved for the week, oldest first (empty when none, or the file is unreadable)."""
    path = pool_path(season, week, directory)
    try:
        records = json.loads(path.read_text())
        out = [PoolSnapshot.from_record(r) for r in records]
    except (OSError, ValueError, KeyError, TypeError):
        return []
    return sorted(out, key=lambda s: s.at)


def save(
    season: int,
    week: int,
    pool: Mapping[str, str],
    *,
    source: str,
    sheet: str = "",
    rules: Mapping[str, str] | None = None,
    at: datetime | None = None,
    directory: Path | None = None,
) -> Path | None:
    """Append a snapshot of the pool (`{name: tag}`, only the pooled players) unless it is the latest one
    again. Returns the file written, or None when nothing was stored (an empty pool, a repeat, or a write
    error)."""
    pool = {str(k): str(v) for k, v in pool.items() if str(v).strip()}
    if not pool:
        return None
    try:
        existing = load(season, week, directory)
        # a repeat is judged on the pool alone when the caller has no rules (a sync does not read the box)
        snap = PoolSnapshot(at or datetime.now(UTC), source, pool, dict(rules or {}), sheet)
        if existing:
            last = existing[-1]
            same_rules = rules is None or last.rules == snap.rules
            if last.pool == snap.pool and same_rules:
                return None
            if rules is None:  # carry the box the last snapshot had: a sync does not read it
                snap = PoolSnapshot(snap.at, source, pool, dict(last.rules), sheet)
        existing.append(snap)
        path = pool_path(season, week, directory)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps([s.record() for s in existing[-MAX_SNAPSHOTS:]], indent=1))
        tmp.replace(path)
        return path
    except OSError:
        return None


def pool_at(season: int, week: int, before: datetime, directory: Path | None = None) -> PoolSnapshot | None:
    """The last snapshot saved at or before `before` (the lock), else None: the backtest then constructs a
    pool."""
    best = None
    for snap in load(season, week, directory):
        if snap.at <= before:
            best = snap
    return best
