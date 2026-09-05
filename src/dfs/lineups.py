"""Read paired-up DK entries from the sheet, validate them, export DK's CSV.

Per the agreed workflow, pairing finished lineups to specific contest
entries stays a manual step in the sheet (the "DK Upload" tab, which
already mirrors DraftKings' own "bulk edit entries" export: Entry ID,
Contest Name, Contest ID, Entry Fee, then the 9 roster slot columns). This
module's job is entirely downstream of that: parse what's there, validate
it against real current salary data, and emit exactly the CSV DraftKings
expects -- catching cap/roster/duplicate problems locally instead of
finding out from DraftKings after uploading.
"""

from __future__ import annotations

import csv
import re
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from dfs.models import FLEX_ELIGIBLE, ROSTER_SLOTS, Player, Position

_ENTRY_COLUMNS = 4  # Entry ID, Contest Name, Contest ID, Entry Fee
_CELL_RE = re.compile(r"^(.*?)\s*\((\d+)\)$")


class LineupExportError(Exception):
    pass


@dataclass
class ParsedEntry:
    row_number: int  # 1-indexed sheet row, for error messages
    entry_id: str
    contest_name: str
    contest_id: str
    entry_fee: str
    slot_cells: list[str]  # len(ROSTER_SLOTS), raw "Name (dkId)" strings


@dataclass
class ValidationResult:
    entry: ParsedEntry
    players: list[Player] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors

    @property
    def salary_total(self) -> int:
        return sum(p.salary for p in self.players)


def parse_player_cell(cell: str) -> tuple[str, str] | None:
    """"Name (dkId)" -> (name, dkId). Blank cell -> None."""
    cell = (cell or "").strip()
    if not cell:
        return None
    m = _CELL_RE.match(cell)
    if not m:
        return None
    return m.group(1).strip(), m.group(2)


def parse_entries(rows: list[list[str]]) -> list[ParsedEntry]:
    """rows includes the header row (row 1); entries start at row 2.
    Column layout is positional, not by name -- the header has duplicate
    names (RB, RB, WR, WR, WR), so a name-keyed dict would collide."""
    entries = []
    for i, row in enumerate(rows[1:], start=2):
        entry_id = (row[0] if len(row) > 0 else "").strip()
        if not entry_id:
            continue  # blank template/reservation row
        slot_cells = [
            row[_ENTRY_COLUMNS + j] if len(row) > _ENTRY_COLUMNS + j else ""
            for j in range(len(ROSTER_SLOTS))
        ]
        entries.append(
            ParsedEntry(
                row_number=i,
                entry_id=entry_id,
                contest_name=row[1] if len(row) > 1 else "",
                contest_id=row[2] if len(row) > 2 else "",
                entry_fee=row[3] if len(row) > 3 else "",
                slot_cells=slot_cells,
            )
        )
    return entries


def build_salary_lookup(df: pd.DataFrame) -> dict[str, Player]:
    lookup: dict[str, Player] = {}
    for _, row in df.iterrows():
        try:
            position = Position(str(row["Position"]).strip())
        except ValueError:
            continue  # e.g. a stray row DK's CSV sometimes includes; not a real player
        dk_id = str(row["ID"])
        lookup[dk_id] = Player(
            dk_id=dk_id,
            name=str(row["Name"]),
            position=position,
            team=str(row["TeamAbbrev"]),
            salary=int(row["Salary"]),
        )
    return lookup


def validate_entry(
    entry: ParsedEntry,
    salary_lookup: dict[str, Player],
    *,
    salary_cap: int = 50000,
) -> ValidationResult:
    result = ValidationResult(entry=entry)
    seen_ids: set[str] = set()

    for slot, cell in zip(ROSTER_SLOTS, entry.slot_cells):
        parsed = parse_player_cell(cell)
        if parsed is None:
            result.errors.append(f"{slot} slot is empty or unparseable ({cell!r})")
            continue

        name, dk_id = parsed
        player = salary_lookup.get(dk_id)
        if player is None:
            result.errors.append(
                f"{slot}: {name} ({dk_id}) not in current salary data "
                f"-- may be from a different week/slate, run `dfs sync` first"
            )
            continue

        if dk_id in seen_ids:
            result.errors.append(f"{slot}: {name} is used twice in this lineup")
        seen_ids.add(dk_id)

        if slot == "FLEX":
            if player.position not in FLEX_ELIGIBLE:
                result.errors.append(f"FLEX: {name} is {player.position.value}, must be RB/WR/TE")
        elif player.position.value != slot:
            result.errors.append(f"{slot}: {name} is {player.position.value}, not {slot}")

        result.players.append(player)

    if result.players and result.salary_total > salary_cap:
        result.errors.append(f"salary {result.salary_total} exceeds cap {salary_cap}")

    return result


def export_csv(valid_entries: list[ParsedEntry], path: Path) -> int:
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["Entry ID", "Contest Name", "Contest ID", "Entry Fee", *ROSTER_SLOTS])
        for e in valid_entries:
            writer.writerow([e.entry_id, e.contest_name, e.contest_id, e.entry_fee, *e.slot_cells])
    return len(valid_entries)
