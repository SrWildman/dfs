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

**Settled at the close** (`settle`): each decision's points = the chosen player's actual DK points minus
`default_at_time`'s, null when either has no result (a postponed game, a Fade with no one named in his place);
`by_reason` counts and averages them per reason. The callers: the Lock state's Swap / Keep / Later
(`week_server.lock_answer`, Phase C2); the Week page's decisions (`/api/decide`, through
`week_actions.decision_record`: open spots, cash core Play / Fade, the GPP stack, a Questionable star's
pivot or Wait); and `/api/reason` (`set_reason`).
"""

from __future__ import annotations

import json
from collections.abc import Mapping
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


def _week_of(decision_id: str) -> tuple[int, int]:
    season, week, _n = str(decision_id).split("-")
    return int(season), int(week)


def set_reason(of: str, reason: str, note: str = "", path: Path | None = None) -> str:
    """Give decision `of` its reason afterwards (one of `REASONS`, or "" to clear it): a new line with
    `reason_for` set; `read` folds the latest one in. Returns the new line's id."""
    if reason and reason not in REASONS:
        raise ValueError(f"unknown reason {reason!r}")
    season, week = _week_of(of)
    item = {
        "id": next_id(season, week, path),
        "at": _now(),
        "season": season,
        "week": week,
        "reason_for": of,
        "reason": reason,
        "note": note,
    }
    _write(item, path)
    return item["id"]


def latest_for(row: str, season: int, week: int, path: Path | None = None) -> dict | None:
    """The newest standing decision recorded for page row `row` (the key the page answered), or None."""
    found = [d for d in read(season, week, path) if str(d.get("row") or "") == row]
    return found[-1] if found else None


def _pts(who: object, points: Mapping[str, float]) -> float | None:
    from dfs.player_join import normalize_name

    if not isinstance(who, dict):
        return None
    for k in (str(who.get("id") or ""), normalize_name(str(who.get("name") or ""))):
        if k and k in points:
            v = points[k]
            return None if v is None or v != v else float(v)
    return None


def settle(decisions: list[dict], points: Mapping[str, float]) -> list[dict]:
    """Each decision with `points` = chosen minus `default_at_time` in actual DK points (`points` keyed by
    DK id or normalised name); null when either side has no result. A decision whose chosen IS the default
    (Keep) settles at 0."""
    out = []
    for d in decisions:
        a, b = _pts(d.get("chosen"), points), _pts(d.get("default_at_time"), points)
        same = isinstance(d.get("chosen"), dict) and d.get("chosen") == d.get("default_at_time")
        diff = 0.0 if same and a is not None else (None if a is None or b is None else round(a - b, 2))
        out.append({**d, "points": diff})
    return out


def by_reason(settled: list[dict]) -> list[dict]:
    """Per reason (blank = "none given"): decisions, how many settled, their total and mean points; in
    `REASONS` order, then none given. Decisions with no result are counted, never averaged."""
    order = [*REASONS, ""]
    groups: dict[str, list[dict]] = {}
    for d in settled:
        groups.setdefault(str(d.get("reason") or ""), []).append(d)
    out = []
    for r in order:
        g = groups.get(r)
        if not g:
            continue
        pts = [float(d["points"]) for d in g if d.get("points") is not None]
        out.append(
            {
                "reason": r or "none given",
                "decisions": len(g),
                "settled": len(pts),
                "total": round(sum(pts), 2) if pts else None,
                "mean": round(sum(pts) / len(pts), 2) if pts else None,
            }
        )
    return out
