"""The last `dfs lineups late-swap` run, kept on disk so the Cockpit tab can show it without a new search.

A late-swap search takes minutes; `dfs sync --live` refreshes the Cockpit in seconds. So each late-swap run
saves one small JSON file (`data/cockpit/late_swap.json`, gitignored with the rest of `data/`) holding, per
built lineup, the names it was run on, its goal, its open slots and the best change found, plus the ESPN news
the run laid over `Avail`. The Cockpit reads it back and says when it was taken: a stale or mismatched entry
(the lineup was edited since, or the run is from an earlier day) is shown as old, never as current.

**Every run is kept** (blueprint Phase C item 3): `save` also appends the run to
`data/cockpit/late_swaps.jsonl`, one JSON line per run (`to_json(saved)` plus, per lineup, `options`: up to
three changes, best first, with the ownership going out and coming in). The file is only ever opened for
append: a line once written is never rewritten (`tests/test_late_swap_store.py`). `late_swap.json` stays
"the latest" for the Cockpit and the page.

Pure except `save` / `load` / `append_run` (one file, atomic replace; one append). Never raises into a
late-swap run: the caller treats a failed save as a warning.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

from dfs.late_swap_search import LineupSuggestions, Swap
from dfs.late_swap_sim import swap_names
from dfs.paths import DATA_DIR

STORE_DIR = DATA_DIR / "cockpit"
STORE_FILE = "late_swap.json"
RUNS_FILE = "late_swaps.jsonl"  # every run, appended (blueprint Phase C item 3)
OPTIONS = 3  # changes kept per lineup in a run's line, best first
FIELD_PROJECTED = "field: projected"  # what a run without a live export says it scored against
STALE_AFTER = timedelta(hours=12)  # a late-swap run from an earlier day is not Sunday's picture
_GAIN_EPSILON = 1e-9


@dataclass(frozen=True)
class BestChange:
    """The single best change found for a lineup, in words and numbers."""

    kind: str  # "refill" | "pair" | "single"
    out: tuple[str, ...]
    into: tuple[str, ...]
    delta_pts: float
    delta_p_cash: float | None = None
    delta_p_gpp: float | None = None
    overlap: str = ""  # LEVERAGE / HOLD / EVEN (`late_swap_position`), "" where not labelled


@dataclass(frozen=True)
class Option:
    """One improving change for a lineup, as the run's log keeps it (ownership summed over out / in)."""

    out: tuple[str, ...]
    into: tuple[str, ...]
    delta_pts: float
    own_out: float | None = None
    own_in: float | None = None


@dataclass(frozen=True)
class LineupResult:
    number: int
    goal: str  # "cash" | "gpp"
    names: tuple[str, ...]  # the nine names the search ran on
    open_slots: tuple[str, ...]
    best: BestChange | None  # None: nothing beats the lineup
    position: str = ""  # a GPP lineup's place on the top-1% line, in words (`late_swap_position`)
    options: tuple[Option, ...] = ()  # up to `OPTIONS` improving changes, best first (the run log only)


@dataclass(frozen=True)
class NewsItem:
    name: str
    before: str
    after: str
    note: str = ""
    updated: str = ""  # ISO UTC time of ESPN's entry, "" when unknown


@dataclass(frozen=True)
class Saved:
    saved_at: datetime
    refreshed_injuries: bool
    lineups: tuple[LineupResult, ...] = field(default_factory=tuple)
    news: tuple[NewsItem, ...] = field(default_factory=tuple)
    field_source: str = FIELD_PROJECTED  # "field: real (<contest>, exported <time>)" when `--field` was used

    def for_lineup(self, number: int) -> LineupResult | None:
        return next((r for r in self.lineups if r.number == number), None)

    def is_stale(self, now: datetime) -> bool:
        return now - self.saved_at > STALE_AFTER


def _gain(swap: Swap, goal: str) -> float | None:
    return swap.delta_p_cash if goal == "cash" else swap.delta_p_gpp


def _candidates(suggestions: LineupSuggestions) -> list[tuple[str, Swap]]:
    out: list[tuple[str, Swap]] = []
    if suggestions.refill is not None and suggestions.refill.changes:
        out.append(("refill", suggestions.refill))
    out += [("pair", s) for s in suggestions.pairs]
    out += [("single", s) for swaps in suggestions.singles.values() for s in swaps]
    return out


def options(suggestions: LineupSuggestions, goal: str, n: int = OPTIONS) -> tuple[Option, ...]:
    """Up to `n` improving changes, best first, on `best_change`'s footing: the goal probability where the
    simulator scored them, else the projection gain. A change found twice (a pair that is also a refill) is
    kept once."""
    found = _candidates(suggestions)
    rated = [s for _, s in found if _gain(s, goal) is not None]
    if rated:
        ranked = sorted(
            (s for s in rated if _gain(s, goal) > _GAIN_EPSILON), key=lambda s: (-_gain(s, goal), -s.delta)
        )
    else:
        ranked = sorted((s for _, s in found if s.delta > _GAIN_EPSILON), key=lambda s: -s.delta)
    out: list[Option] = []
    seen: set[tuple[tuple[str, ...], tuple[str, ...]]] = set()
    for s in ranked:
        names_out, names_in = swap_names(s)
        key = (tuple(sorted(names_out)), tuple(sorted(names_in)))
        if key in seen:
            continue
        seen.add(key)
        overlap = s.overlap
        out.append(
            Option(
                tuple(names_out),
                tuple(names_in),
                s.delta,
                overlap.own_out if overlap is not None else None,
                overlap.own_in if overlap is not None else None,
            )
        )
        if len(out) == n:
            break
    return tuple(out)


def best_change(suggestions: LineupSuggestions, goal: str) -> BestChange | None:
    """The best improving change the search found for one lineup. With the simulator's numbers on the swaps
    (`delta_p_*`) the one raising the lineup's `goal` probability most wins and it must raise it; without them
    the biggest projection gain wins. Re-fills, pairs and singles compete on the same footing."""
    candidates = _candidates(suggestions)
    rated = [(k, s) for k, s in candidates if _gain(s, goal) is not None]
    if rated:
        kind, swap = max(rated, key=lambda ks: (_gain(ks[1], goal), ks[1].delta))
        if _gain(swap, goal) <= _GAIN_EPSILON:
            return None
    else:
        improving = [(k, s) for k, s in candidates if s.delta > _GAIN_EPSILON]
        if not improving:
            return None
        kind, swap = max(improving, key=lambda ks: ks[1].delta)
    out, into = swap_names(swap)
    overlap = swap.overlap.label if swap.overlap is not None else ""
    return BestChange(kind, tuple(out), tuple(into), swap.delta, swap.delta_p_cash, swap.delta_p_gpp, overlap)


def summarize(
    number: int, goal: str, names: list[str], suggestions: LineupSuggestions, position: str = ""
) -> LineupResult:
    """One lineup's result from its search."""
    return LineupResult(
        number=number,
        goal=goal,
        names=tuple(names),
        open_slots=tuple(s.slot for s in suggestions.state.open),
        best=best_change(suggestions, goal),
        position=position,
        options=options(suggestions, goal),
    )


def _path(directory: Path | None = None) -> Path:
    return (directory or STORE_DIR) / STORE_FILE


def _runs_path(directory: Path | None = None) -> Path:
    return (directory or STORE_DIR) / RUNS_FILE


def to_json(saved: Saved) -> dict:
    return {
        "saved_at": saved.saved_at.astimezone(UTC).isoformat(),
        "refreshed_injuries": saved.refreshed_injuries,
        "field_source": saved.field_source,
        "lineups": [
            {
                "number": r.number,
                "goal": r.goal,
                "names": list(r.names),
                "open_slots": list(r.open_slots),
                "position": r.position,
                "best": None
                if r.best is None
                else {
                    "kind": r.best.kind,
                    "out": list(r.best.out),
                    "into": list(r.best.into),
                    "delta_pts": r.best.delta_pts,
                    "delta_p_cash": r.best.delta_p_cash,
                    "delta_p_gpp": r.best.delta_p_gpp,
                    "overlap": r.best.overlap,
                },
                "options": [
                    {
                        "out": list(o.out),
                        "into": list(o.into),
                        "delta_pts": o.delta_pts,
                        "own_out": o.own_out,
                        "own_in": o.own_in,
                    }
                    for o in r.options
                ],
            }
            for r in saved.lineups
        ],
        "news": [
            {"name": n.name, "before": n.before, "after": n.after, "note": n.note, "updated": n.updated}
            for n in saved.news
        ],
    }


def from_json(data: dict) -> Saved:
    """Raises KeyError / ValueError on a malformed file; `load` turns that into None."""
    lineups = []
    for item in data["lineups"]:
        best = item.get("best")
        lineups.append(
            LineupResult(
                number=int(item["number"]),
                goal=str(item["goal"]),
                names=tuple(str(n) for n in item["names"]),
                open_slots=tuple(str(s) for s in item["open_slots"]),
                best=None
                if best is None
                else BestChange(
                    kind=str(best["kind"]),
                    out=tuple(best["out"]),
                    into=tuple(best["into"]),
                    delta_pts=float(best["delta_pts"]),
                    delta_p_cash=best.get("delta_p_cash"),
                    delta_p_gpp=best.get("delta_p_gpp"),
                    overlap=str(best.get("overlap") or ""),
                ),
                position=str(item.get("position") or ""),
                options=tuple(
                    Option(
                        tuple(o.get("out") or ()),
                        tuple(o.get("into") or ()),
                        float(o.get("delta_pts") or 0.0),
                        o.get("own_out"),
                        o.get("own_in"),
                    )
                    for o in item.get("options") or []
                ),
            )
        )
    news = tuple(
        NewsItem(
            name=str(n["name"]),
            before=str(n.get("before", "")),
            after=str(n.get("after", "")),
            note=str(n.get("note", "")),
            updated=str(n.get("updated", "")),
        )
        for n in data.get("news", [])
    )
    return Saved(
        saved_at=datetime.fromisoformat(data["saved_at"]),
        refreshed_injuries=bool(data.get("refreshed_injuries", False)),
        lineups=tuple(lineups),
        news=news,
        field_source=str(data.get("field_source") or FIELD_PROJECTED),
    )


def run_line(saved: Saved) -> dict:
    """One run as its `late_swaps.jsonl` line: `to_json`, which carries each lineup's `options`."""
    return to_json(saved)


def append_run(saved: Saved, directory: Path | None = None) -> Path:
    """Append the run to `late_swaps.jsonl`: opened for append, one write, never a rewrite."""
    path = _runs_path(directory)
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps(run_line(saved), separators=(",", ":")) + "\n"
    with path.open("a", encoding="utf-8") as f:
        f.write(line)
    return path


def read_runs(directory: Path | None = None) -> list[dict]:
    """Every run in the log, oldest first; a line that does not parse is skipped."""
    path = _runs_path(directory)
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            out.append(json.loads(line))
        except ValueError:
            continue
    return out


def save(saved: Saved, directory: Path | None = None) -> Path:
    """Write the file atomically (a half-written file never replaces a good one), then append the run to the
    log of every run."""
    path = _path(directory)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(to_json(saved), indent=2))
    os.replace(tmp, path)
    append_run(saved, directory)
    return path


def load(directory: Path | None = None) -> Saved | None:
    """The last saved run, or None when there is none or it cannot be read."""
    path = _path(directory)
    try:
        return from_json(json.loads(path.read_text()))
    except (OSError, ValueError, KeyError, TypeError):
        return None
