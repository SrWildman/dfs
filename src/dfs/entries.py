"""Fill DraftKings' entries file from the lineups Sam built: `dfs entries fill` and `dfs entries print`.

Sam downloads `DraftKings Entries (N).csv` himself (My Contests > Edit entries; the tool never fetches from
DK). It lists every entry of a draft group (`Entry ID, Contest Name, Contest ID, Entry Fee`, then the nine
roster slots, then `Instructions`) with DK's player table to the right (`Position, Name + ID, Name, ID, Roster
Position, Salary, Game Info, TeamAbbrev, AvgPointsPerGame`). A reserved entry has blank slots; a slot takes
`Name (ID)` or the ID.

**Sam decides which lineup goes in which contest; nothing here assigns.** The `Lineup` dropdown on DK Upload
is his alone. The `Suggested` column is the tool's guess beside it, by contest format (`suggest_pick`), grey,
never copied into the dropdown and never read by the export. `suggest_pick` is the hook Contests Phase 2's
"Best in" replaces: `plan_fill(..., suggester=...)` takes any function of the same signature.

Everything here is pure (files in, plans out); the sheet reads and writes are `sheet_dk_upload.py`, the
commands `cli.py`. **A started player is never touched** (`merge_started`): a slot whose current player's
game has begun keeps him, and a started player is never put into a slot. Kickoffs come from the player
table's own `Game Info` (DK's clock, Eastern), so the file is self-contained.
"""

from __future__ import annotations

import csv
import re
from collections import defaultdict
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path

import pandas as pd

from dfs import flex_order
from dfs.contests import series
from dfs.kickoff import KICKOFF_TIMEZONE, parse_kickoff
from dfs.lineups import DK_UPLOAD_HEADER, ParsedEntry
from dfs.models import FLEX_ELIGIBLE, ROSTER_SLOTS
from dfs.player_join import normalize_name

ENTRY_HEADER = DK_UPLOAD_HEADER[:4]  # Entry ID, Contest Name, Contest ID, Entry Fee
SHOWDOWN_FIRST_SLOT = "CPT"
TABLE_HEADER = ("Position", "Name + ID", "Name", "ID", "Roster Position", "Salary", "Game Info", "TeamAbbrev")
LINEUP_LABEL = re.compile(r"^\s*L\s*(\d+)\s*$", re.IGNORECASE)
_ID_AT_END = re.compile(r"\((\d+)\)\s*$")
_GAME_INFO = re.compile(r"(\d{1,2}/\d{1,2}/\d{4})\s+(\d{1,2}:\d{2}\s*[AP]M)\s*ET", re.IGNORECASE)
MAIN_SLATE_FIRST_HOUR, MAIN_SLATE_LAST_HOUR = 12, 17  # a Sunday kickoff 12:00-17:59 ET (`dk_salaries`)

FORMAT_SHORT = {
    series.H2H: "H2H",
    series.FIFTY_FIFTY: "50/50",
    series.DOUBLE_UP: "DU",
    series.SE_GPP: "GPP",
    series.N_MAX_GPP: "GPP",
    series.MULTIPLIER: "Mult",
    series.FREEROLL: "Free",
    series.PRIVATE: "Private",
    series.OTHER: "Other",
}
GPP_FORMATS = frozenset({series.SE_GPP, series.N_MAX_GPP, series.MULTIPLIER, series.FREEROLL})
# Cash- and GPP-marked lineups (the marker on Lineups' Total row) pair with these formats; anything else gets
# no suggestion (a Private contest has no rule).
CASH_MARKER, GPP_MARKER = "Cash", "GPP"
FLEX_POSITIONS = frozenset(p.value for p in FLEX_ELIGIBLE)


class EntriesFileError(Exception):
    pass


# -- the entries file ---------------------------------------------------------------------------------------


@dataclass(frozen=True)
class TablePlayer:
    """One row of DK's player table."""

    position: str
    name: str
    dk_id: str
    roster_position: str
    salary: int
    game_info: str
    team: str
    kickoff: datetime | None  # a real UTC instant, None when `Game Info` has no time

    @property
    def cell(self) -> str:
        return f"{self.name} ({self.dk_id})"


@dataclass(frozen=True)
class FileEntry:
    entry_id: str
    contest_name: str
    contest_id: str
    entry_fee: str
    cells: tuple[str, ...]  # the file's own slot cells, raw, one per roster slot

    @property
    def ids(self) -> tuple[str, ...]:
        return tuple(cell_id(c) for c in self.cells)

    @property
    def blank(self) -> bool:
        return not any(self.ids)


@dataclass
class EntriesFile:
    path: Path
    kind: str  # "classic" or "showdown" (by the roster slots in its header)
    entries: list[FileEntry]
    players: list[TablePlayer]


def cell_id(cell: str) -> str:
    """The DK id in a slot cell: `Name (ID)` or a bare ID; "" for a blank cell."""
    text = (cell or "").strip()
    found = _ID_AT_END.search(text)
    if found:
        return found.group(1)
    return text if text.isdigit() else ""


def parse_game_info(text: str) -> datetime | None:
    """`CAR@PHI 10/18/2026 01:00PM ET` -> the kickoff as a UTC instant; None when it has no clock time (DK
    shows other text once a game is under way)."""
    found = _GAME_INFO.search(text or "")
    if not found:
        return None
    stamp = datetime.strptime(
        f"{found.group(1)} {found.group(2).replace(' ', '').upper()}", "%m/%d/%Y %I:%M%p"
    )
    return parse_kickoff(stamp.strftime("%Y-%m-%dT%H:%M:%SZ"))  # Eastern wall-clock, see kickoff.py


def looks_like_entries_header(header: Sequence[str]) -> bool:
    cells = [c.strip() for c in header]
    return tuple(cells[:4]) == ENTRY_HEADER and "Instructions" in cells


def _first_row(path: Path) -> list[str]:
    try:
        with open(path, encoding="utf-8-sig", newline="") as f:
            return next(csv.reader(f), [])
    except (OSError, UnicodeDecodeError, csv.Error):
        return []


def find_entries_file(downloads: Path) -> Path | None:
    """The newest `*.csv` in `downloads` whose header row is DK's entries header (the name is never trusted:
    `DraftKings Entries (6).csv` is whatever the browser called it)."""
    folder = Path(downloads).expanduser()
    if not folder.is_dir():
        return None
    files = sorted(folder.glob("*.csv"), key=lambda p: p.stat().st_mtime, reverse=True)
    return next((p for p in files if looks_like_entries_header(_first_row(p))), None)


def resolve_entries_file(csv_path: Path | None, downloads: Path) -> tuple[Path | None, str]:
    """`(path, how it was chosen)`: the file given, else the newest entries file in `downloads`."""
    if csv_path is not None:
        return Path(csv_path), "given with --csv"
    found = find_entries_file(downloads)
    if found is None:
        return None, f"no DraftKings entries file found in {downloads}"
    return found, f"newest entries file in {downloads} (found by its header row)"


def read_entries_file(path: Path) -> EntriesFile:
    with open(path, encoding="utf-8-sig", newline="") as f:
        rows = list(csv.reader(f))
    if not rows or not looks_like_entries_header(rows[0]):
        raise EntriesFileError(f"{path} is not a DraftKings entries file (its header row is not DK's)")
    header = [c.strip() for c in rows[0]]
    slots = header[4 : header.index("") if "" in header else header.index("Instructions")]
    if tuple(slots) == tuple(ROSTER_SLOTS):
        kind = "classic"
    elif slots and slots[0] == SHOWDOWN_FIRST_SLOT:
        kind = "showdown"
    else:
        raise EntriesFileError(f"{path}: roster slots {slots} are neither Classic nor Showdown")

    table_at, columns = None, {}
    for i, row in enumerate(rows):
        names = [c.strip() for c in row]
        if all(h in names for h in TABLE_HEADER):
            table_at = i
            first = names.index(TABLE_HEADER[0])
            columns = {h: names.index(h, first) for h in (*TABLE_HEADER, "ID")}
            break
    if table_at is None:
        raise EntriesFileError(f"{path}: DK's player table (Position, Name + ID, ...) was not found")

    players = []
    for row in rows[table_at + 1 :]:

        def at(name: str, row: list[str] = row) -> str:
            i = columns[name]
            return row[i].strip() if i < len(row) else ""

        if not at("ID"):
            continue
        try:
            salary = int(float(at("Salary")))
        except ValueError:
            salary = 0
        players.append(
            TablePlayer(
                position=at("Position"),
                name=at("Name"),
                dk_id=at("ID"),
                roster_position=at("Roster Position"),
                salary=salary,
                game_info=at("Game Info"),
                team=at("TeamAbbrev"),
                kickoff=parse_game_info(at("Game Info")),
            )
        )

    width = len(slots)
    entries = []
    for row in rows[1:]:
        if not row or not row[0].strip():
            continue  # the player table's own rows carry no entry
        cells = tuple((row[4 + j] if len(row) > 4 + j else "").strip() for j in range(width))
        entries.append(
            FileEntry(
                entry_id=row[0].strip(),
                contest_name=row[1].strip() if len(row) > 1 else "",
                contest_id=row[2].strip() if len(row) > 2 else "",
                entry_fee=row[3].strip() if len(row) > 3 else "",
                cells=cells,
            )
        )
    return EntriesFile(path=Path(path), kind=kind, entries=entries, players=players)


def is_main_slate(players: Sequence[TablePlayer]) -> bool:
    """The repo's main slate is Sunday-afternoon games only (`sources.dk_salaries`). A file whose player table
    has a game with a known kickoff outside that window (a Thursday, Sunday-night or Monday game) is another
    slate. A table with no readable kickoff is not judged: nothing says it is another slate."""
    kickoffs = [p.kickoff for p in players if p.kickoff is not None]
    for kickoff in kickoffs:
        local = pd.Timestamp(kickoff).tz_convert(KICKOFF_TIMEZONE)
        if local.weekday() != 6 or not MAIN_SLATE_FIRST_HOUR <= local.hour <= MAIN_SLATE_LAST_HOUR:
            return False
    return True


def _fee(text: str) -> Decimal | None:
    try:
        return Decimal(text.replace("$", "").replace(",", "").strip() or "0")
    except InvalidOperation:
        return None


def entry_format(entry: FileEntry) -> str:
    """The contest's format (`contests.series`), from its name and fee. A Showdown contest in a Classic file
    (its name carries a matchup or says so) reads as `Showdown`."""
    name = entry.contest_name
    if "showdown" in name.casefold() or series.normalize_name(name)[1]:
        return series.SHOWDOWN
    info = series.classify("Classic", name, fee=_fee(entry.entry_fee), contest_entries=None, places_paid=None)
    return info.format


@dataclass(frozen=True)
class Entry:
    """A Classic main-slate entry: what the file says and the format it reads as."""

    file: FileEntry
    format: str

    @property
    def entry_id(self) -> str:
        return self.file.entry_id

    @property
    def contest_id(self) -> str:
        return self.file.contest_id


def classify_entries(parsed: EntriesFile) -> tuple[list[Entry], dict[str, int]]:
    """`(the Classic main-slate entries, skipped counts by reason)`. A Showdown file, another slate and a
    Showdown or other-game contest inside a Classic file are skipped and counted, never filled."""
    if parsed.kind == "showdown":
        return [], {"Showdown file": len(parsed.entries)}
    if not is_main_slate(parsed.players):
        return [], {"another slate (not Sunday afternoon only)": len(parsed.entries)}
    entries: list[Entry] = []
    skipped: dict[str, int] = defaultdict(int)
    for raw in parsed.entries:
        fmt = entry_format(raw)
        if fmt in (series.SHOWDOWN, series.OTHER):
            skipped[fmt] += 1
            continue
        entries.append(Entry(raw, fmt))
    return entries, dict(skipped)


# -- the lineups on the sheet --------------------------------------------------------------------------------


@dataclass(frozen=True)
class SheetLineup:
    """One Lineups block as the sheet shows it: the nine typed names, the Cash / GPP marker and the Total
    row's numbers (None where blank)."""

    number: int  # 1-based, the `L<number>` of the dropdown
    names: tuple[str, ...]
    marker: str = ""  # "Cash", "GPP" or "" (both)
    cash_pct: float | None = None
    top1: float | None = None
    cal_pts: float | None = None
    proj_pts: float | None = None
    own: float | None = None  # the Total row's sum of the nine Own% fractions

    @property
    def label(self) -> str:
        return f"L{self.number}"

    @property
    def built(self) -> bool:
        return any(n.strip() for n in self.names)


@dataclass
class ResolvedLineup:
    """A lineup's names matched to DK's own player table, in `ROSTER_SLOTS` order."""

    lineup: SheetLineup
    players: list[TablePlayer | None]
    problems: list[str] = field(default_factory=list)

    @property
    def complete(self) -> bool:
        return not self.problems and all(self.players)


def _fits(slot: str, player: TablePlayer) -> bool:
    return player.position in FLEX_POSITIONS if slot == "FLEX" else player.position == slot


def resolve_lineup(lineup: SheetLineup, players: Sequence[TablePlayer]) -> ResolvedLineup:
    """Match each typed name to DK's table (spelling-tolerant: `player_join.normalize_name`; a name two
    players share is settled by the slot's position). A slot that is blank, unknown, ambiguous or filled by
    the wrong position is a problem, and the lineup is then incomplete (it is reported, never repaired)."""
    by_name: dict[str, list[TablePlayer]] = defaultdict(list)
    for p in players:
        by_name[normalize_name(p.name)].append(p)
    resolved: list[TablePlayer | None] = []
    problems: list[str] = []
    for slot, typed in zip(ROSTER_SLOTS, lineup.names, strict=True):
        text = typed.strip()
        if not text:
            resolved.append(None)
            problems.append(f"{slot} is empty")
            continue
        candidates = by_name.get(normalize_name(text), [])
        fitting = [p for p in candidates if _fits(slot, p)]
        if not candidates:
            resolved.append(None)
            problems.append(f"{slot}: {text} is not in this file's player table")
        elif len(fitting) != 1:
            resolved.append(None)
            problems.append(
                f"{slot}: {text} is {'/'.join(sorted({p.position for p in candidates}))}, not a {slot}"
                if not fitting
                else f"{slot}: {text} matches more than one player"
            )
        else:
            resolved.append(fitting[0])
    ids = [p.dk_id for p in resolved if p]
    if len(set(ids)) != len(ids):
        problems.append("a player is used twice")
    return ResolvedLineup(lineup, resolved, problems)


@dataclass(frozen=True)
class DkSlots:
    """A lineup in DK's slot order with the FLEX rule applied."""

    players: list[TablePlayer]
    flex_note: str  # what the FLEX rule did or could not do, "" when nothing to say
    started: list[str]  # "WR Name" for each slot whose game has already begun

    @property
    def cells(self) -> list[str]:
        return [p.cell for p in self.players]


def dk_slots(resolved: ResolvedLineup, now: datetime) -> DkSlots:
    """The complete lineup's nine players with the FLEX rule applied (`flex_order`: the latest kickoff among
    the flex position's players goes in FLEX, never moving a started player), and which slots have started."""
    players = [p for p in resolved.players if p is not None]
    seats = [
        flex_order.Seat(s, p.name, p.position, p.kickoff) for s, p in zip(ROSTER_SLOTS, players, strict=True)
    ]
    plan = flex_order.plan_flex(seats, now)
    note = ""
    if plan.moves:
        players = flex_order.apply_swap(players, plan, seats)
        note = flex_order.describe(plan)
    elif plan.status == "locked":
        note = "FLEX left as it is (a player's game has started)"
    elif plan.status == "unknown":
        note = "FLEX not checked (a kickoff time is missing)"
    elif plan.status == "tie":
        note = "FLEX left as it is (two games kick off together)"
    started = [f"{s} {p.name}" for s, p in zip(ROSTER_SLOTS, players, strict=True) if has_started(p, now)]
    return DkSlots(players, note, started)


def has_started(player: TablePlayer | None, now: datetime) -> bool:
    return player is not None and player.kickoff is not None and player.kickoff <= now


def merge_started(
    target: Sequence[TablePlayer],
    current_cells: Sequence[str],
    by_id: Mapping[str, TablePlayer],
    now: datetime,
) -> tuple[list[str], list[str]]:
    """`(the nine cells to write, notes)`. `target` is the lineup Sam picked; `current_cells` what the entry
    holds now. **A started player is never touched**: a slot whose current player's game has begun keeps him,
    and a started player is never put in (the slot keeps what it has, or stays blank)."""
    cells: list[str] = []
    notes: list[str] = []
    for slot, want, have in zip(ROSTER_SLOTS, target, current_cells, strict=True):
        have_id = cell_id(have)
        current = by_id.get(have_id)
        if have_id == want.dk_id:
            cells.append(want.cell)
        elif have_id and (has_started(current, now) or has_started(want, now)):
            keep = current.cell if current else have
            who = current.name if has_started(current, now) else want.name
            cells.append(keep)
            notes.append(f"{slot} {who} started, so it keeps {current.name if current else have_id}")
        elif not have_id and has_started(want, now):
            cells.append("")
            notes.append(f"{slot} {want.name} started, so it stays blank")
        else:
            cells.append(want.cell)
    return cells, notes


# -- the pick hook -------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Suggestion:
    lineup: int | None  # the lineup number, None when there is no pick
    why: str

    @property
    def label(self) -> str:
        return f"L{self.lineup}" if self.lineup else ""


Suggester = Callable[[Entry, Sequence[SheetLineup], frozenset[int]], Suggestion]


def suggest_pick(entry: Entry, lineups: Sequence[SheetLineup], taken: frozenset[int]) -> Suggestion:
    """The tool's guess for one entry, by format. **The hook Contests Phase 2's "Best in" replaces**: same
    arguments (the entry, the complete lineups, the lineup numbers already in this contest) and result.

    Cash formats (H2H, 50/50, Double Up): the Cash-marked lineup with the best Cash %. GPP formats (SE/N-max
    GPP, Multiplier, Freeroll): the GPP-marked lineup with the best Top 1%. A lineup in `taken` is never
    repeated in one contest (a multi-entry contest gets the next best). With no lineup of the right marker
    the unmarked ones stand in and the reason says so; a Private or other contest has no rule."""
    if entry.format in series.CASH_FORMATS:
        marker, metric, label = CASH_MARKER, "cash_pct", "Cash %"
    elif entry.format in GPP_FORMATS:
        marker, metric, label = GPP_MARKER, "top1", "Top 1%"
    else:
        return Suggestion(None, f"no rule for a {FORMAT_SHORT.get(entry.format, entry.format)} contest")

    def ranked(pool: Sequence[SheetLineup]) -> list[SheetLineup]:
        return sorted(pool, key=lambda ln: (-(getattr(ln, metric) or -1.0), -(ln.cal_pts or -1.0), ln.number))

    marked = ranked([ln for ln in lineups if ln.marker == marker])
    pool, standin = (
        (marked, "") if marked else (ranked([ln for ln in lineups if ln.marker == ""]), " (unmarked)")
    )
    if not pool:
        return Suggestion(None, f"no {marker}-marked lineup")
    free = [ln for ln in pool if ln.number not in taken]
    if not free:
        return Suggestion(None, f"every {marker}-marked lineup is already in this contest")
    pick = free[0]
    value = getattr(pick, metric)
    rank = pool.index(pick)
    how = "best" if rank == 0 else f"{_ordinal(rank + 1)}-best"
    text = f"{how} {label}{standin}" + (f" ({value:.1%})" if value is not None else "")
    if rank and pool[0].number in taken:
        text += f"; {pool[0].label} is already in this contest"
    return Suggestion(pick.number, text)


def _ordinal(n: int) -> str:
    return f"{n}{'th' if 10 <= n % 100 <= 20 else {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')}"


# -- the fill ------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class TabRow:
    """A DK Upload row as the tab holds it now."""

    entry_id: str
    lineup: str  # "L3" or ""
    cells: tuple[str, ...]
    row: int = 0  # the sheet row it sits on
    raw: tuple[str, ...] = ()  # the whole row as read, so a row the file no longer lists can be written back


@dataclass
class RowPlan:
    entry: Entry
    lineup: int | None  # Sam's choice
    suggested: str = ""
    why: str = ""
    cells: list[str] = field(default_factory=lambda: [""] * len(ROSTER_SLOTS))
    changed: bool = False  # the slots on the tab differ from what the tab held (a lineup moved)
    upload: bool = False  # goes in the upload file: set, complete, and different from what DK holds
    notes: list[str] = field(default_factory=list)


@dataclass
class FillPlan:
    rows: list[RowPlan]
    skipped: dict[str, int]
    checks: list[str]  # reported, never fixed
    summary: str
    unchanged_at_dk: int = 0  # set rows whose entry already holds exactly this lineup
    flex_notes: list[str] = field(default_factory=list)  # what the FLEX rule did, once per lineup

    @property
    def upload_rows(self) -> list[RowPlan]:
        return [r for r in self.rows if r.upload]


def lineup_choice(label: str, count: int) -> int | None:
    found = LINEUP_LABEL.match(label or "")
    n = int(found.group(1)) if found else 0
    return n if 1 <= n <= count else None


def plan_fill(
    parsed: EntriesFile,
    tab_rows: Mapping[str, TabRow],
    lineups: Sequence[SheetLineup],
    *,
    now: datetime,
    again: bool = False,
    suggester: Suggester = suggest_pick,
) -> FillPlan:
    """What `entries fill` writes. One row per Classic main-slate entry in file order; the Lineup is whatever
    `tab_rows` (matched by Entry ID) holds, and is never chosen here. A set row gets its nine slots from that
    lineup (FLEX rule, started players untouched); the Suggested/Why columns are the `suggester`'s pick.

    `again` (after a late swap changed a lineup): only the rows whose lineup now gives different slots from
    the ones on the tab are rewritten; every other row keeps its slots, and no suggestion is recomputed."""
    entries, skipped = classify_entries(parsed)
    by_id = {p.dk_id: p for p in parsed.players}
    resolved = {ln.number: resolve_lineup(ln, parsed.players) for ln in lineups if ln.built}
    complete = [ln for ln in lineups if ln.number in resolved and resolved[ln.number].complete]

    rows: list[RowPlan] = []
    for e in entries:
        tab = tab_rows.get(e.entry_id)
        rows.append(RowPlan(e, lineup_choice(tab.lineup, len(lineups)) if tab else None))
    chosen_in: dict[str, set[int]] = defaultdict(set)
    for r in rows:
        if r.lineup:
            chosen_in[r.entry.contest_id].add(r.lineup)

    if not again:
        suggested_in: dict[str, set[int]] = defaultdict(set)
        for r in rows:
            others = chosen_in[r.entry.contest_id] - ({r.lineup} if r.lineup else set())
            taken = frozenset(others | suggested_in[r.entry.contest_id])
            pick = suggester(r.entry, complete, taken)
            r.suggested, r.why = pick.label, pick.why
            if pick.lineup:
                suggested_in[r.entry.contest_id].add(pick.lineup)

    checks: list[str] = []
    unusable: dict[int, list[str]] = defaultdict(list)
    flex_notes: dict[int, str] = {}
    for r in rows:
        tab = tab_rows.get(r.entry.entry_id)
        previous = tuple(tab.cells) if tab else ("",) * len(ROSTER_SLOTS)
        if r.lineup is None:
            r.cells = [""] * len(ROSTER_SLOTS)
            r.changed = any(previous)
            continue
        res = resolved.get(r.lineup)
        where = f"{r.entry.file.contest_name} ({r.entry.entry_id})"
        if res is None or not res.complete:
            unusable[r.lineup].append(where)
            r.cells, r.changed = [""] * len(ROSTER_SLOTS), any(previous)
            continue
        slots = dk_slots(res, now)
        cells, notes = merge_started(slots.players, r.entry.file.cells, by_id, now)
        r.notes = notes
        if slots.flex_note:
            flex_notes.setdefault(r.lineup, f"L{r.lineup}: {slots.flex_note}")
        r.cells = cells
        r.changed = [cell_id(c) for c in cells] != [cell_id(c) for c in previous]
        if again and not r.changed:
            continue
        ids = [cell_id(c) for c in cells]
        problem = ""
        if not all(ids):
            problem = "a slot would stay blank, so it is not uploaded"
        elif len(set(ids)) != len(ids):
            problem = "a kept started player would be in twice, so it is not uploaded"
        elif tuple(ids) != r.entry.file.ids:
            r.upload = True
        if notes or problem:
            checks.append(f"{where} <- L{r.lineup}: " + "; ".join([*notes, *([problem] if problem else [])]))

    for number, where in sorted(unusable.items()):
        res = resolved.get(number)
        why = "empty" if res is None else "incomplete: " + "; ".join(res.problems)
        noun = "entry" if len(where) == 1 else "entries"
        checks.append(f"L{number} is {why}; {len(where)} {noun} using it not written ({where[0]}...)")

    set_rows = [r for r in rows if r.lineup]
    in_contest: dict[tuple[str, int], list[RowPlan]] = defaultdict(list)
    for r in set_rows:
        in_contest[(r.entry.contest_id, r.lineup)].append(r)
    for (_contest, number), twice in in_contest.items():
        if len(twice) > 1:
            checks.append(
                f"{twice[0].entry.file.contest_name}: L{number} is in this contest {len(twice)} times "
                f"(entries {', '.join(r.entry.entry_id for r in twice)}); a duplicate entry"
            )
    for r in set_rows:
        res = resolved.get(r.lineup)
        if res is None:
            continue
        marker = res.lineup.marker
        where = f"{r.entry.file.contest_name} ({r.entry.entry_id})"
        if marker == GPP_MARKER and r.entry.format in series.CASH_FORMATS:
            checks.append(
                f"{where}: L{r.lineup} is marked GPP but this is a {FORMAT_SHORT[r.entry.format]} contest"
            )
        if marker == CASH_MARKER and r.entry.format in GPP_FORMATS:
            checks.append(
                f"{where}: L{r.lineup} is marked Cash but this is a {FORMAT_SHORT[r.entry.format]} contest"
            )

    blanks = [r for r in rows if r.lineup is None]
    if blanks:
        by_contest: dict[str, int] = defaultdict(int)
        for r in blanks:
            by_contest[r.entry.file.contest_name] += 1
        listed = ", ".join(f"{name} x{n}" if n > 1 else name for name, n in by_contest.items())
        noun = "entry" if len(blanks) == 1 else "entries"
        checks.append(f"{len(blanks)} {noun} with no Lineup (they do not go): {listed}")

    unchanged = sum(
        1
        for r in set_rows
        if all(r.cells) and not r.upload and tuple(map(cell_id, r.cells)) == r.entry.file.ids
    )
    return FillPlan(
        rows, skipped, checks, summary_line(rows), unchanged, [flex_notes[n] for n in sorted(flex_notes)]
    )


def summary_line(rows: Sequence[RowPlan]) -> str:
    """`27 entries: 24 set (20 H2H + 3 DU <- L1; 1 GPP <- L4), 3 blank`."""
    set_rows = [r for r in rows if r.lineup]
    by_lineup: dict[int, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for r in set_rows:
        by_lineup[r.lineup][FORMAT_SHORT.get(r.entry.format, r.entry.format)] += 1
    groups = []
    for number in sorted(by_lineup):
        counts = by_lineup[number]
        groups.append(" + ".join(f"{n} {label}" for label, n in counts.items()) + f" <- L{number}")
    detail = f" ({'; '.join(groups)})" if groups else ""
    return f"{len(rows)} entries: {len(set_rows)} set{detail}, {len(rows) - len(set_rows)} blank"


def upload_entries(plan: FillPlan) -> list[ParsedEntry]:
    """The rows for the upload file (`lineups.export_csv`): only the entries Sam set that DK does not already
    hold exactly so."""
    return [
        ParsedEntry(
            row_number=i,
            entry_id=r.entry.entry_id,
            contest_name=r.entry.file.contest_name,
            contest_id=r.entry.contest_id,
            entry_fee=r.entry.file.entry_fee,
            slot_cells=list(r.cells),
        )
        for i, r in enumerate(plan.upload_rows, start=2)
    ]


# -- every lineup in DK's format (`entries print`) ----------------------------------------------------------


PRINT_HEADER = ("Lineup", "CalPts", "ProjPts", "Own%", *ROSTER_SLOTS, "Note")


@dataclass
class PrintRow:
    lineup: SheetLineup
    cells: list[str]  # nine slots as `Name (ID)`; "" for a blank slot, `?name` for one DK's table lacks
    notes: list[str]

    def as_record(self) -> list[str]:
        ln = self.lineup
        return [
            ln.label,
            f"{ln.cal_pts:.1f}" if ln.cal_pts is not None else "",
            f"{ln.proj_pts:.1f}" if ln.proj_pts is not None else "",
            f"{ln.own:.1%}" if ln.own is not None else "",
            *self.cells,
            "; ".join(self.notes),
        ]


def print_rows(
    lineups: Sequence[SheetLineup], players: Sequence[TablePlayer], now: datetime
) -> list[PrintRow]:
    """Every built lineup in DK slot order as `Name (ID)` (FLEX rule applied), with its totals in front. Needs
    no entry pairing. A started player is flagged and stays where he is; a lineup that does not resolve
    (a blank or unknown name) is still printed, as far as it goes, with the reason in its note."""
    out = []
    for ln in lineups:
        if not ln.built:
            continue
        res = resolve_lineup(ln, players)
        notes = list(res.problems)
        if res.complete:
            slots = dk_slots(res, now)
            cells = slots.cells
            notes += [f"started: {s}" for s in slots.started]
            if slots.flex_note:
                notes.append(slots.flex_note)
        else:
            cells = [
                p.cell if p else (f"?{t.strip()}" if t.strip() else "")
                for p, t in zip(res.players, ln.names, strict=True)
            ]
        out.append(PrintRow(ln, cells, notes))
    return out


def write_print_csv(rows: Sequence[PrintRow], path: Path) -> int:
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(PRINT_HEADER)
        for r in rows:
            writer.writerow(r.as_record())
    return len(rows)
