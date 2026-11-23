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
The cash post-mortem (`week close`) reads it to compare the entered cash lineup to the nearest build.

**Recording again after late swap is the normal path** (Sam's Week 11 note): a record replaces the week's,
so the file always holds the final lineups, and every record saved is also appended to
`<season>-wkNN.history.jsonl` beside it (append-only, never rewritten), so the version from before lock is
kept and late-swap decisions can be scored (`versions`, `before_lock`). `changes` says what a new record
changed against the one it replaces, lineup by lineup."""

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


H2H_CHOICES = ("posted", "joined", "both")
H2H_WORDS = {"posted": "ones you posted", "joined": "ones you joined", "both": "some posted, some joined"}


def record_path(season: int, week: int) -> Path:
    """`data/entered/<season>-wk<NN>.json`; a secondary slate's own under `data/slates/<key>/entered/`."""
    from dfs import slate

    return slate.path(ENTERED_DIR, "entered") / f"{season}-wk{week:02d}.json"


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
    # Blueprint Phase B (the ledger): whether this week's H2Hs were ones he posted, joined or both ("" when
    # not said), and the fees of the entries the file held but skipped (a Showdown, another slate), by reason
    h2h: str = ""
    skipped_fees: dict[str, float] = field(default_factory=dict)
    # the slate's first kickoff (ISO, from the entries file's player table; carried over from the record it
    # replaces when read from the Lineups tab): a record saved after it holds the lineups after lock
    first_kickoff: str = ""

    @property
    def after_lock(self) -> bool:
        lock = _instant(self.first_kickoff)
        at = _instant(self.recorded_at)
        return lock is not None and at is not None and at >= lock


def _instant(text: str) -> datetime | None:
    try:
        t = datetime.fromisoformat(str(text))
    except (TypeError, ValueError):
        return None
    return t if t.tzinfo else t.replace(tzinfo=UTC)


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


def history_path(path: Path) -> Path:
    """`<season>-wkNN.history.jsonl` beside the week's record: every version saved, oldest first."""
    return path.with_name(path.stem + ".history.jsonl")


def save(record: EnteredRecord, path: Path | None = None) -> Path:
    """The week's record, replaced; and the same record appended to its history (append-only)."""
    path = path or record_path(record.season, record.week)
    path.parent.mkdir(parents=True, exist_ok=True)
    with history_path(path).open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(asdict(record), ensure_ascii=False) + "\n")
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(asdict(record), indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, path)
    return path


def versions(season: int, week: int, path: Path | None = None) -> list[EnteredRecord]:
    """Every record saved for the week, oldest first (a line that cannot be read is skipped). A week recorded
    before the history existed has only its record file: that one version."""
    path = path or record_path(season, week)
    out: list[EnteredRecord] = []
    try:
        lines = history_path(path).read_text(encoding="utf-8").splitlines()
    except OSError:
        lines = []
    for line in lines:
        rec = _parse(line) if line.strip() else None
        if rec is not None:
            out.append(rec)
    if not out:
        rec = load(season, week, path)
        return [rec] if rec is not None else []
    return out


def before_lock(season: int, week: int, path: Path | None = None) -> EnteredRecord | None:
    """The last version recorded before the slate locked (the lineups as entered, before any late swap), or
    None when every version was recorded after lock or the lock time is not known."""
    pre = [r for r in versions(season, week, path) if r.first_kickoff and not r.after_lock]
    return pre[-1] if pre else None


def load(season: int, week: int, path: Path | None = None) -> EnteredRecord | None:
    """The week's record, or None when there is none (or it cannot be read)."""
    path = path or record_path(season, week)
    try:
        return _parse(path.read_text(encoding="utf-8"))
    except OSError:
        return None


def _parse(text: str) -> EnteredRecord | None:
    try:
        raw = json.loads(text)
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
            h2h=str(raw.get("h2h", "") or ""),
            skipped_fees={str(k): float(v) for k, v in (raw.get("skipped_fees") or {}).items()},
            first_kickoff=str(raw.get("first_kickoff", "") or ""),
        )
    except (ValueError, KeyError, TypeError, AttributeError):
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
        fees = record.skipped_fees.get(reason)
        out.append(f"  skipped {n}: {reason}" + (f" (${fees:,.2f} in fees)" if fees else ""))
    if record.h2h:
        out.append(f"  this week's H2Hs: {H2H_WORDS.get(record.h2h, record.h2h)}")
    for c in record.blank:
        n = len(c.get("entry_ids") or [])
        out.append(f"  no lineup yet: {n} entr{'y' if n == 1 else 'ies'} in {c.get('name', '')}")
    builds = sum(lu.source is not None for lu in record.lineups)
    out.append(
        f"{builds} of {len(record.lineups)} came from a Suggested build; "
        f"{len(record.lineups) - builds} your own."
    )
    return out


LABEL_GROUPS = 3  # contest kinds named in a lineup's label; the rest are "+N more"


def lineup_label(lu: EnteredLineup, i: int) -> str:
    """`L3` from the Lineups tab; `#2 (H2H $2 x20, Double Up $5 x2 +3 more)` from the entries file: the
    entries it holds, by format and fee, most first."""
    if lu.label:
        return lu.label
    count: dict[str, int] = {}
    for c in lu.contests:
        key = f"{c.format} {c.entry_fee}"
        count[key] = count.get(key, 0) + len(c.entry_ids)
    kinds = sorted(count, key=lambda k: -count[k])
    where = ", ".join(k + (f" x{count[k]}" if count[k] > 1 else "") for k in kinds[:LABEL_GROUPS])
    if len(kinds) > LABEL_GROUPS:
        where += f" +{len(kinds) - LABEL_GROUPS} more"
    return f"#{i}" + (f" ({where})" if where else "")


def _swaps(was: Sequence[str], now: Sequence[str]) -> list[str]:
    """Who went out and who came in: slot for slot where both lineups are in DK's slot order (a late swap
    replaces a player in his slot: `WR Tre' Harris -> Malik Nabers`), the rest in order."""
    from dfs.models import ROSTER_SLOTS

    keys_was, keys_now = {name_key(x) for x in was if x}, {name_key(x) for x in now if x}
    out = [x for x in was if x and name_key(x) not in keys_now]
    into = [x for x in now if x and name_key(x) not in keys_was]
    lines = []
    if len(was) == len(now) == len(ROSTER_SLOTS):
        for slot, a, b in zip(ROSTER_SLOTS, was, now, strict=True):
            if a in out and b in into:
                lines.append(f"{slot} {a} -> {b}")
                out.remove(a)
                into.remove(b)
    lines += [f"{a} -> {b}" for a, b in zip(out, into, strict=False)]
    lines += [f"{a} out" for a in out[len(into) :]] + [f"{b} in" for b in into[len(out) :]]
    return lines


def _entry_ids(lu: EnteredLineup) -> set[str]:
    return {i for c in lu.contests for i in c.entry_ids}


def _pair(
    old: list[EnteredLineup], new: list[EnteredLineup]
) -> list[tuple[EnteredLineup | None, EnteredLineup | None]]:
    """Each new lineup with the old one it replaces: the one holding most of its DK entries (a late swap
    keeps the entry, changes the players); without entries (the Lineups tab), the same label; else the one
    sharing most players (at least 5). Old lineups left unpaired come last."""
    left = list(range(len(old)))
    out: list[tuple[EnteredLineup | None, EnteredLineup | None]] = []
    for lu in new:

        def score(j: int, lu: EnteredLineup = lu) -> tuple[int, int, int]:
            o = old[j]
            names = len({name_key(n) for n in o.names if n} & {name_key(n) for n in lu.names if n})
            return (len(_entry_ids(o) & _entry_ids(lu)), int(bool(lu.label) and o.label == lu.label), names)

        best = max(left, key=score, default=None)
        if best is not None and (score(best)[0] or score(best)[1] or score(best)[2] >= 5):
            out.append((old[best], lu))
            left.remove(best)
        else:
            out.append((None, lu))
    out += [(old[j], None) for j in left]
    return out


def changes(old: EnteredRecord, new: EnteredRecord) -> list[str]:
    """What `new` changes against the record it replaces, lineup by lineup: who went out and who came in,
    "unchanged", a lineup newly entered or no longer there. Empty when nothing changed."""
    lines: list[str] = []
    changed = False
    for i, (o, n) in enumerate(_pair(old.lineups, new.lineups), start=1):
        if n is None:
            changed = True
            lines.append(f"  no longer entered: {', '.join(x for x in o.names if x)}")
            continue
        label = lineup_label(n, i)
        if o is None:
            changed = True
            lines.append(f"  {label}: new, {', '.join(x for x in n.names if x)}")
            continue
        swaps = _swaps(o.names, n.names)
        if not swaps:
            lines.append(f"  {label}: unchanged")
            continue
        changed = True
        lines.append(f"  {label}: " + "; ".join(swaps))
    return lines if changed else []


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


# Once games are under way DK's player table says "Final" / "In Progress" where the kickoff was, so a file
# downloaded after lock may have no readable kickoff at all (Sam's Week 11 file: none of 519). DK's player ids
# are per draft group, so a file whose players are this week's synced slate is this week's.
ON_SLATE_MIN = 0.9  # of the file's players on the synced slate (a sync may have dropped a row or two)


def synced_rows(path: Path | None = None) -> list[dict]:
    """The synced slate (`data/current/edge.csv`, the slate's own on a secondary slate); [] without one."""
    import csv

    from dfs import store

    target = path or store.current_dir() / "edge.csv"
    try:
        with target.open(newline="", encoding="utf-8") as fh:
            return list(csv.DictReader(fh))
    except OSError:
        return []


def on_synced_slate(players, rows: Sequence[dict]) -> bool:  # noqa: ANN001 - dfs.entries.TablePlayer
    """Whether the entries file's player table is the synced slate's (by DK id, `ON_SLATE_MIN` of them)."""
    ids = {str(p.dk_id) for p in players if p.dk_id}
    slate_ids = {str(r.get("Id", "")).strip() for r in rows} - {""}
    return bool(ids) and len(ids & slate_ids) >= ON_SLATE_MIN * len(ids)


def first_kickoff(players, rows: Sequence[dict] = (), previous: EnteredRecord | None = None) -> str:  # noqa: ANN001
    """The slate's first kickoff, ISO: the earliest of the file's readable kickoffs, the synced slate's
    `GameStart` and the record being replaced (after lock the file shows none for a game under way)."""
    from dfs.kickoff import parse_kickoff

    times = [p.kickoff for p in players if p.kickoff is not None]
    times += [t for t in (parse_kickoff(r.get("GameStart")) for r in rows) if t is not None]
    if previous is not None and _instant(previous.first_kickoff) is not None:
        times.append(_instant(previous.first_kickoff))
    return min(times).astimezone(UTC).isoformat(timespec="seconds") if times else ""
