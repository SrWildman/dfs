"""`data/week/decisions.jsonl`: every decision Sam makes on the Week page, one JSON object per line.
**Append-only** (blueprint Phase D item 1; `SPECS_A_TO_D.md` sections 0 item 8 and 5).

One file for every week. A line is never edited: an undo is a new line with `undo_of` set, and a reason given
afterwards is a new line with `reason_for` set. `read` folds both in. The schema:

    {"id": "2026-11-0007", "at": "2026-11-21T23:41:10Z", "season": 2026, "week": 11,
     "state": "build" | "lock" | "late", "kind": one of KINDS, "lineup": "Cash", "slot": "FLEX",
     "chosen": {"id", "name"}, "alternatives": [{"id", "name", "d_points", "d_salary", "cash_own"}],
     "default_at_time": {"id", "name"}, "reason": "", "note": "", "source": "page", "undo_of": null}

`id` is the season, the week and a running number within the week. `default_at_time` is who the tool had in
that slot at that moment (for a Lock-state `swap`, `keep` or `later`: the lineup's current player). Pool chip
clicks are not decisions (`data/week/edits.log` keeps them).

`append` opens the file for append and writes one line; nothing here rewrites it
(`tests/test_decisions_log.py` checks the earlier bytes after every write).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from dfs.paths import DATA_DIR

PATH = DATA_DIR / "week" / "decisions.jsonl"
KINDS = ("core_fade", "open_spot", "pivot", "stack", "swap", "keep", "later")
REASONS = ("projection", "news", "source", "gut", "price", "correlation", "ownership")
STATES = ("build", "lock", "late")


def _path(path: Path | None) -> Path:
    return path or PATH


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def lines(path: Path | None = None) -> list[dict]:
    """Every line as written, oldest first; a line that does not parse is skipped."""
    p = _path(path)
    if not p.exists():
        return []
    out = []
    for text in p.read_text(encoding="utf-8").splitlines():
        try:
            item = json.loads(text)
        except ValueError:
            continue
        if isinstance(item, dict):
            out.append(item)
    return out


def next_id(season: int, week: int, path: Path | None = None) -> str:
    prefix = f"{season}-{week:02d}-"
    n = sum(1 for item in lines(path) if str(item.get("id", "")).startswith(prefix))
    return f"{prefix}{n + 1:04d}"


def _write(item: dict, path: Path | None) -> None:
    p = _path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as f:
        f.write(json.dumps(item, separators=(",", ":"), default=str) + "\n")


def append(record: dict, path: Path | None = None) -> str:
    """Append one decision (`season` and `week` required; `kind` one of `KINDS`); returns its id."""
    season, week = int(record["season"]), int(record["week"])
    kind = str(record.get("kind") or "")
    if kind not in KINDS:
        raise ValueError(f"unknown decision kind {kind!r}")
    item = {
        "id": next_id(season, week, path),
        "at": record.get("at") or _now(),
        "season": season,
        "week": week,
        "state": record.get("state") or "build",
        "kind": kind,
        "lineup": record.get("lineup", ""),
        "slot": record.get("slot", ""),
        "chosen": record.get("chosen"),
        "alternatives": list(record.get("alternatives") or []),
        "default_at_time": record.get("default_at_time"),
        "reason": record.get("reason", ""),
        "note": record.get("note", ""),
        "source": record.get("source", "page"),
        "undo_of": record.get("undo_of"),
    }
    for k, v in record.items():  # extra context (the Lock row's key, its lineups) kept as given
        item.setdefault(k, v)
    _write(item, path)
    return item["id"]


def undo(of: str, *, season: int, week: int, path: Path | None = None) -> str:
    """Undo decision `of`: a new line, `undo_of` set; the original line stays."""
    item = {"id": next_id(season, week, path), "at": _now(), "season": season, "week": week, "undo_of": of}
    _write(item, path)
    return item["id"]


def read(season: int | None = None, week: int | None = None, path: Path | None = None) -> list[dict]:
    """The decisions (not the undo or reason lines), with undone ones left out and the latest reason folded
    in, oldest first; cut to `season` / `week` when given."""
    items = lines(path)
    undone = {str(i["undo_of"]) for i in items if i.get("undo_of")}
    reasons: dict[str, tuple[str, str]] = {}
    for i in items:
        if i.get("reason_for"):
            reasons[str(i["reason_for"])] = (str(i.get("reason") or ""), str(i.get("note") or ""))
    out = []
    for i in items:
        if i.get("undo_of") or i.get("reason_for") or not i.get("kind") or str(i.get("id")) in undone:
            continue
        if season is not None and int(i.get("season") or 0) != season:
            continue
        if week is not None and int(i.get("week") or 0) != week:
            continue
        if str(i.get("id")) in reasons:
            i = {
                **i,
                "reason": reasons[str(i["id"])][0],
                "note": reasons[str(i["id"])][1] or i.get("note", ""),
            }
        out.append(i)
    return out
