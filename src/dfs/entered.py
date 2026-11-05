"""Plan D item 5: what was entered, recorded at lock (`dfs entries record`).

The audit could not tell, for most weeks, which lineup went in which contest or whether it was one of the
Suggested builds or one Sam made himself, so nothing could be scored against what he actually played. This
keeps one file per week, `data/entered/<season>-wk<NN>.json`:

- every distinct lineup entered (its nine names and, from DK's entries file, the DK ids),
- the contest(s) it went into (contest id, name, fee, format, entry ids),
- its source: the Suggested build it matches (at least `MATCH_MIN` of its nine players in one saved build
  of the week's `data/suggest_state/<week>.json`, any round) or "own".

The input is DK's entries file (Edit entries, downloaded by hand after entering, found in Downloads like
`dfs entries fill` finds it) or, when there is none, the Lineups tab as it stands (lineups, no contests).
The cash post-mortem (`week close`) reads it to compare the entered cash lineup to the nearest build."""

from __future__ import annotations

import json
import os
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from dfs import suggest_state
from dfs.paths import DATA_DIR
from dfs.player_join import normalize_name

ENTERED_DIR = DATA_DIR / "entered"
MATCH_MIN = 8  # of 9 players shared with a saved build: that build is the source
OWN = "own"


def record_path(season: int, week: int) -> Path:
    return ENTERED_DIR / f"{season}-wk{week:02d}.json"


@dataclass(frozen=True)
class Contest:
    contest_id: str
    name: str
    entry_fee: str
    format: str
    entry_ids: list[str]


@dataclass(frozen=True)
class SourceBuild:
    round: int
    number: int
    kind: str  # "cash" / "gpp"
    title: str
    shared: int  # players in common (8 or 9)


@dataclass
class EnteredLineup:
    names: list[str]
    ids: list[str] = field(default_factory=list)  # DK ids, slot order; [] when read from the Lineups tab
    label: str = ""  # "L3" when read from the Lineups tab
    contests: list[Contest] = field(default_factory=list)
    source: SourceBuild | None = None  # None: "own"

    @property
    def source_text(self) -> str:
        if self.source is None:
            return OWN
        s = self.source
        return f"build {s.number} of round {s.round} ({s.kind}, {s.shared}/9)"


@dataclass
class EnteredRecord:
    season: int
    week: int
    recorded_at: str
    source: str  # "entries file <name>" / "Lineups tab of <sheet title>"
    lineups: list[EnteredLineup]
    note: str = ""  # e.g. "Week 6 has no suggest history ..."
    skipped: dict[str, int] = field(default_factory=dict)
    # Entries the file held with no lineup yet, one dict per contest: contest_id, name, entry_fee, format,
    # entry_ids (Plan K item 4: the entry plan's "every contest has a lineup" check).
    blank: list[dict] = field(default_factory=list)


def name_key(name: str) -> str:
    return normalize_name(name)


def match_build(names: Sequence[str], state: suggest_state.StateFile) -> SourceBuild | None:
    """The saved build sharing the most of `names` (at least `MATCH_MIN`), latest round first on a tie."""
    keys = {name_key(n) for n in names if n and n.strip()}
    best: SourceBuild | None = None
    for rnd in state.rounds:
        for b in rnd.builds:
            shared = len(keys & {name_key(n) for n in b.names})
            if shared < MATCH_MIN:
                continue
            if best is None or shared > best.shared or (shared == best.shared and rnd.round >= best.round):
                best = SourceBuild(rnd.round, b.number, b.kind, b.title, shared)
    return best


def from_entries(entries, players) -> list[EnteredLineup]:  # noqa: ANN001 - dfs.entries.Entry / TablePlayer
    """One `EnteredLineup` per distinct lineup in the file's Classic entries (blank entries left out), with
    the contests it went into, in the order first seen."""
    by_id = {p.dk_id: p for p in players}
    lineups: dict[tuple[str, ...], EnteredLineup] = {}
    contests: dict[tuple[tuple[str, ...], str], dict] = {}
    for e in entries:
        if e.file.blank:
            continue
        ids = tuple(e.file.ids)
        key = tuple(sorted(i for i in ids if i))
        if key not in lineups:
            names = [by_id[i].name.strip() if i in by_id else "" for i in ids]
            lineups[key] = EnteredLineup(names=names, ids=list(ids))
        c = contests.setdefault(
            (key, e.contest_id),
            {
                "contest_id": e.contest_id,
                "name": e.file.contest_name,
                "entry_fee": e.file.entry_fee,
                "format": e.format,
                "entry_ids": [],
            },
        )
        c["entry_ids"].append(e.entry_id)
    for (key, _), c in contests.items():
        lineups[key].contests.append(Contest(**c))
    return list(lineups.values())


def blank_entries(entries) -> list[dict]:  # noqa: ANN001 - dfs.entries.Entry
    """The Classic entries the file holds with no lineup, grouped by contest."""
    out: dict[str, dict] = {}
    for e in entries:
        if not e.file.blank:
            continue
        c = out.setdefault(
            e.contest_id,
            {
                "contest_id": e.contest_id,
                "name": e.file.contest_name,
                "entry_fee": e.file.entry_fee,
                "format": e.format,
                "entry_ids": [],
            },
        )
        c["entry_ids"].append(e.entry_id)
    return list(out.values())


def from_sheet(lineups) -> list[EnteredLineup]:  # noqa: ANN001 - dfs.entries.SheetLineup
    """The Lineups tab's built lineups (no contests: the tab does not know them)."""
    return [EnteredLineup(names=[n.strip() for n in sl.names], label=sl.label) for sl in lineups if sl.built]


def attach_sources(lineups: list[EnteredLineup], state: suggest_state.StateFile) -> None:
    for lineup in lineups:
        lineup.source = match_build(lineup.names, state)


def save(record: EnteredRecord, path: Path | None = None) -> Path:
    path = path or record_path(record.season, record.week)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(asdict(record), indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, path)
    return path


def load(season: int, week: int, path: Path | None = None) -> EnteredRecord | None:
    """The week's record, or None when there is none (or it cannot be read)."""
    path = path or record_path(season, week)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        lineups = [
            EnteredLineup(
                names=list(lu["names"]),
                ids=list(lu.get("ids", [])),
                label=lu.get("label", ""),
                contests=[Contest(**c) for c in lu.get("contests", [])],
                source=SourceBuild(**lu["source"]) if lu.get("source") else None,
            )
            for lu in raw["lineups"]
        ]
        return EnteredRecord(
            season=int(raw["season"]),
            week=int(raw["week"]),
            recorded_at=str(raw["recorded_at"]),
            source=str(raw["source"]),
            lineups=lineups,
            note=str(raw.get("note", "")),
            skipped=dict(raw.get("skipped", {})),
            blank=list(raw.get("blank", [])),
        )
    except (OSError, ValueError, KeyError, TypeError):
        return None


def make_record(season: int, week: int, source: str, lineups: list[EnteredLineup], **extra) -> EnteredRecord:
    state = suggest_state.load_state(week)
    attach_sources(lineups, state)
    return EnteredRecord(
        season=season,
        week=week,
        recorded_at=datetime.now(tz=UTC).isoformat(timespec="seconds"),
        source=source,
        lineups=lineups,
        note=suggest_state.missing_history_note(week),
        **extra,
    )


def summary_lines(record: EnteredRecord) -> list[str]:
    out = [f"Week {record.week}: {len(record.lineups)} lineup(s) from the {record.source}."]
    if record.note:
        out.append(record.note + ": every lineup reads as your own.")
    for i, lu in enumerate(record.lineups, start=1):
        label = lu.label or f"#{i}"
        where = (
            "; ".join(f"{c.format} {c.entry_fee} x{len(c.entry_ids)} ({c.name})" for c in lu.contests)
            or "contests not known (Lineups tab)"
        )
        out.append(f"  {label}: {lu.source_text}; {where}")
        out.append(f"      {', '.join(lu.names)}")
    for reason, n in record.skipped.items():
        out.append(f"  skipped {n}: {reason}")
    for c in record.blank:
        n = len(c.get("entry_ids") or [])
        out.append(f"  no lineup yet: {n} entr{'y' if n == 1 else 'ies'} in {c.get('name', '')}")
    builds = sum(lu.source is not None for lu in record.lineups)
    out.append(
        f"{builds} of {len(record.lineups)} came from a Suggested build; "
        f"{len(record.lineups) - builds} your own."
    )
    return out


def missing_warning(season: int, week: int) -> str:
    """`week close`'s warning when the week has no record ("" when it has one)."""
    if record_path(season, week).exists():
        return ""
    return (
        f"Week {week} has no record of what was entered ({record_path(season, week).name}): run "
        "`dfs entries record` at lock next week (after entering, with DK's entries file downloaded)."
    )


def file_week(players) -> tuple[int, int | None] | None:  # noqa: ANN001 - dfs.entries.TablePlayer
    """`(season, week)` of an entries file, from its player table's kickoffs (the latest one), or None."""
    from dfs.contests.history import season_week

    kickoffs = [p.kickoff for p in players if p.kickoff is not None]
    if not kickoffs:
        return None
    return season_week(max(kickoffs).astimezone(UTC).date())
