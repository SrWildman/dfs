"""One contest's "export full standings" CSV (`contest-standings-<contest_id>.csv`), bare or inside a zip.

The file holds two unrelated tables on the same rows (see `ownership.py`): columns A-F are the entry
leaderboard (`Rank, EntryId, EntryName, TimeRemaining, Points, Lineup`), columns H-K the per-player ownership
table. They have different lengths -- in a small contest the player table runs past the last entry (A-F
blank), in a big one the entries run past the player table (the trailing cells are short or missing) -- so a
row is padded, never assumed full. The ownership half is `ownership.parse_ownership_export`, called as is.

Things this module does NOT do: guess. A lineup that does not parse into a valid roster (Classic: DST, FLEX,
QB, RB, RB, TE, WR, WR, WR = 9 slots; Showdown: CPT + 5 FLEX) is reported in `Standings.lineup_errors`, with
the reason, and left out of `Standings.lineups`. A 0-byte file (a failed download) is skipped and reported.

DraftKings serves a contest's standings for only about two weeks, so a directory of these is the only record
there will ever be: nothing here deletes or rewrites a source file.
"""

from __future__ import annotations

import io
import math
import re
import zipfile
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

import numpy as np
import pandas as pd

from dfs import ownership

CLASSIC_SLOTS = Counter({"DST": 1, "FLEX": 1, "QB": 1, "RB": 2, "TE": 1, "WR": 3})
SHOWDOWN_SLOTS = Counter({"CPT": 1, "FLEX": 5})
_SLOT_TOKEN = re.compile(r"\b(CPT|DST|FLEX|QB|RB|TE|WR)\b")
_COUNTER = re.compile(r"^(.*?)\s*\((\d+)/(\d+)\)\s*$")
_CONTEST_ID = re.compile(r"contest-standings-(\d+)", re.IGNORECASE)
_ANY_ID = re.compile(r"(\d{5,})")
ENTRY_COLUMNS = ["Rank", "EntryId", "EntryName", "TimeRemaining", "Points", "Lineup"]
OWNERSHIP_COLUMNS = ["Player", "Roster Position", "%Drafted", "FPTS"]
MAX_MEMBER_BYTES = 400 * 1024 * 1024  # a standings CSV is a few MB; refuse a zip member claiming more


class StandingsError(Exception):
    """A standings file that is empty, not a standings file, or malformed."""


def split_entry_name(raw: object) -> tuple[str, int | None, int | None]:
    """`someuser (1/3)` -> `("someuser", 1, 3)`; a name without a counter -> `(name, None, None)`."""
    text = "" if raw is None or (isinstance(raw, float) and math.isnan(raw)) else str(raw).strip()
    found = _COUNTER.match(text)
    if not found:
        return text, None, None
    return found.group(1).strip(), int(found.group(2)), int(found.group(3))


def parse_lineup(text: object) -> tuple[str, tuple[tuple[str, str], ...] | None, str]:
    """`(kind, ((slot, player), ...), "")` for a valid lineup, `("", None, reason)` for one that is not.

    The text is split on the slot tokens as whole words (`DST Jets  FLEX Braelon Allen QB ...`): names keep
    their apostrophes, periods and suffixes, the DST is a nickname. `kind` is `Classic` or `Showdown`."""
    if text is None or (isinstance(text, float) and math.isnan(text)) or not str(text).strip():
        return "", None, "blank lineup"
    parts = _SLOT_TOKEN.split(str(text))
    if parts[0].strip():
        return "", None, f"text before the first slot: {parts[0].strip()[:40]!r}"
    slots = []
    for slot, name in zip(parts[1::2], parts[2::2], strict=True):
        player = " ".join(name.split())
        if not player:
            return "", None, f"slot {slot} has no player"
        slots.append((slot, player))
    counts = Counter(slot for slot, _ in slots)
    if counts == CLASSIC_SLOTS:
        return "Classic", tuple(slots), ""
    if counts == SHOWDOWN_SLOTS:
        return "Showdown", tuple(slots), ""
    return (
        "",
        None,
        f"{len(slots)} slots ({dict(sorted(counts.items()))}) is not a Classic (9) or Showdown (6) roster",
    )


@dataclass(frozen=True)
class ScoreProfile:
    """The score distribution of one contest. Every score is a DK point total.

    - `cash_line`: the score at the last paid place (`places_paid` is passed in, from the history log);
    - `top10` / `top1`: the score of the last entry inside the top 10% / 1% (entry number
      `ceil(0.10 * entrants)` / `max(1, ceil(0.01 * entrants))` in descending score order);
    - `win`: the winning score; `median`: the median score.
    `complete` is False when any entry still has time remaining (a live or unfinished contest)."""

    contest_id: str
    entrants: int
    places_paid: int | None
    cash_line: float
    top10: float
    top1: float
    win: float
    median: float
    complete: bool

    def curve(self) -> list[tuple[float, float]]:
        """`(finish percentile, score)` points on this contest's score-to-finish curve, one per statistic
        that exists: the winner, top 1%, top 10%, the cash line and the median. Percentiles are mid-rank,
        `(place - 0.5) / entrants`, the same convention `toughness.py` uses for an entry's own finish."""
        n = self.entrants
        places = {
            "win": (1, self.win),
            "top1": (max(1, math.ceil(0.01 * n)), self.top1),
            "top10": (math.ceil(0.10 * n), self.top10),
            "median": (n / 2 + 0.5, self.median),
        }
        if self.places_paid and not math.isnan(self.cash_line):
            places["cash"] = (min(self.places_paid, n), self.cash_line)
        return [((place - 0.5) / n, score) for place, score in places.values() if not math.isnan(score)]


def score_profile(entries: pd.DataFrame, *, contest_id: str, places_paid: int | None) -> ScoreProfile:
    pts = np.sort(entries["points"].to_numpy(dtype=float))[::-1]
    n = len(pts)
    if n == 0:
        raise StandingsError("no entries to profile")

    def at(position: int) -> float:
        return float(pts[min(max(position, 1), n) - 1])

    cash = float("nan") if not places_paid or places_paid < 1 else at(int(places_paid))
    remaining = pd.to_numeric(entries["time_remaining"], errors="coerce").fillna(0)
    return ScoreProfile(
        contest_id=str(contest_id),
        entrants=n,
        places_paid=None if not places_paid else int(places_paid),
        cash_line=cash,
        top10=at(math.ceil(0.10 * n)),
        top1=at(max(1, math.ceil(0.01 * n))),
        win=at(1),
        median=float(np.median(pts)),
        complete=bool((remaining == 0).all()),
    )


@dataclass
class Standings:
    contest_id: str
    kind: str  # Classic / Showdown / Unknown (no lineup parsed)
    entries: pd.DataFrame  # rank, entry_id, entry_name, entry_no, entry_max, time_remaining, points, lineup
    profile: ScoreProfile
    lineups: pd.DataFrame  # entry_id, rank, points, entry_name, entry_no, kind, slots
    lineup_errors: pd.DataFrame  # entry_id, rank, entry_name, error, lineup
    ownership: pd.DataFrame  # ownership.parse_ownership_export: player, dk_position, pct_drafted, fpts, ...
    warnings: list[str] = field(default_factory=list)


def _read_table(raw: bytes) -> pd.DataFrame:
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as e:
        raise StandingsError(f"not UTF-8 text: {e}") from e
    try:
        return pd.read_csv(
            io.StringIO(text),
            dtype={"EntryId": str, "EntryName": str, "Lineup": str, "Player": str, "Roster Position": str},
            keep_default_na=False,
            na_values=[""],
        )
    except (pd.errors.ParserError, pd.errors.EmptyDataError) as e:
        raise StandingsError(f"could not read the CSV: {e}") from e


def parse_standings(raw: bytes, *, contest_id: str, places_paid: int | None = None) -> Standings:
    """Parses one standings CSV (as bytes). `places_paid` comes from the history log (the standings file does
    not say how many places pay); without it the cash line is NaN."""
    if not raw.strip():
        raise StandingsError("the file is empty (0 bytes): a failed download")
    table = _read_table(raw)
    missing = [c for c in ENTRY_COLUMNS + OWNERSHIP_COLUMNS if c not in table.columns]
    if missing:
        raise StandingsError(f"not a DraftKings standings export: missing column(s) {missing}")

    leaderboard = table[table["Rank"].notna() & table["EntryId"].notna()].copy()
    if leaderboard.empty:
        raise StandingsError("the standings have no entries")
    names = leaderboard["EntryName"].map(split_entry_name)
    entries = pd.DataFrame(
        {
            "rank": pd.to_numeric(leaderboard["Rank"], errors="coerce").astype("Int64"),
            "entry_id": leaderboard["EntryId"].astype(str).str.strip(),
            "entry_name": [n[0] for n in names],
            "entry_no": pd.array([n[1] for n in names], dtype="Int64"),
            "entry_max": pd.array([n[2] for n in names], dtype="Int64"),
            "time_remaining": pd.to_numeric(leaderboard["TimeRemaining"], errors="coerce"),
            "points": pd.to_numeric(leaderboard["Points"], errors="coerce"),
            "lineup": leaderboard["Lineup"],
        }
    ).reset_index(drop=True)
    warnings: list[str] = []
    bad_points = int(entries["points"].isna().sum())
    if bad_points:
        warnings.append(
            f"{bad_points} entr{'y' if bad_points == 1 else 'ies'} without a readable score were ignored"
        )
        entries = entries[entries["points"].notna()].reset_index(drop=True)
    entries = entries.sort_values(["rank", "points"], ascending=[True, False], kind="stable").reset_index(
        drop=True
    )

    lineup_rows, error_rows = [], []
    for rec in entries.itertuples(index=False):
        kind, slots, reason = parse_lineup(rec.lineup)
        if slots is None:
            error_rows.append(
                {
                    "entry_id": rec.entry_id,
                    "rank": rec.rank,
                    "entry_name": rec.entry_name,
                    "error": reason,
                    "lineup": rec.lineup,
                }
            )
        else:
            lineup_rows.append(
                {
                    "entry_id": rec.entry_id,
                    "rank": rec.rank,
                    "points": rec.points,
                    "entry_name": rec.entry_name,
                    "entry_no": rec.entry_no,
                    "kind": kind,
                    "slots": slots,
                }
            )
    lineups = pd.DataFrame(
        lineup_rows, columns=["entry_id", "rank", "points", "entry_name", "entry_no", "kind", "slots"]
    )
    errors = pd.DataFrame(error_rows, columns=["entry_id", "rank", "entry_name", "error", "lineup"])

    kinds = set(lineups["kind"]) if len(lineups) else set()
    if len(kinds) > 1:
        warnings.append(f"lineups of more than one kind in one contest: {sorted(kinds)}")
    kind = next(iter(kinds)) if len(kinds) == 1 else "Unknown"
    if len(errors):
        warnings.append(f"{len(errors)} lineup(s) did not parse and were left out of the field analysis")

    try:
        owned = ownership.parse_ownership_export(table)
    except (KeyError, ValueError, AttributeError) as e:
        owned = pd.DataFrame(columns=["player", "dk_position", "pct_drafted", "fpts", "contest_entries"])
        warnings.append(f"ownership table not read: {e}")

    return Standings(
        contest_id=str(contest_id),
        kind=kind,
        entries=entries,
        profile=score_profile(entries, contest_id=contest_id, places_paid=places_paid),
        lineups=lineups,
        lineup_errors=errors,
        ownership=owned,
        warnings=warnings,
    )


@dataclass(frozen=True)
class StandingsSource:
    """One standings CSV found on disk: its contest id, where it came from, and its bytes."""

    contest_id: str
    origin: str  # path, or "archive.zip!member.csv"
    raw: bytes


@dataclass
class Scan:
    sources: list[StandingsSource] = field(default_factory=list)
    empty: list[str] = field(default_factory=list)  # 0-byte files (failed downloads)
    unidentified: list[str] = field(default_factory=list)  # a CSV whose contest id could not be read
    duplicates: list[str] = field(default_factory=list)  # a contest id seen a second time (ignored)
    skipped: list[str] = field(default_factory=list)  # unreadable or oversized archives and members


def contest_id_from_name(name: str) -> str | None:
    base = PurePosixPath(name.replace("\\", "/")).name
    found = _CONTEST_ID.search(base) or _ANY_ID.search(base)
    return found.group(1) if found else None


def scan_standings(path: str | Path) -> Scan:
    """Every standings CSV under `path` (a file, a zip, or a directory searched recursively): bare
    `.csv` files and `.csv` members of any `.zip`, whatever the zip is called. 0-byte files are listed in
    `Scan.empty`. Nothing is extracted to disk and no file is modified."""
    root = Path(path)
    files = sorted(p for p in ([root] if root.is_file() else root.rglob("*")) if p.is_file())
    scan = Scan()
    seen: set[str] = set()

    def add(contest_id: str | None, origin: str, raw: bytes) -> None:
        if not raw.strip():
            scan.empty.append(origin)
        elif contest_id is None:
            scan.unidentified.append(origin)
        elif contest_id in seen:
            scan.duplicates.append(origin)
        else:
            seen.add(contest_id)
            scan.sources.append(StandingsSource(contest_id, origin, raw))

    for file in files:
        suffix = file.suffix.lower()
        if suffix == ".csv":
            add(contest_id_from_name(file.name), str(file), file.read_bytes())
        elif suffix == ".zip":
            try:
                with zipfile.ZipFile(file) as archive:
                    for info in sorted(archive.infolist(), key=lambda i: i.filename):
                        if info.is_dir() or not info.filename.lower().endswith(".csv"):
                            continue
                        origin = f"{file}!{info.filename}"
                        if info.file_size > MAX_MEMBER_BYTES:
                            scan.skipped.append(f"{origin} (claims {info.file_size} bytes)")
                            continue
                        add(contest_id_from_name(info.filename), origin, archive.read(info))
            except (zipfile.BadZipFile, OSError) as e:
                scan.skipped.append(f"{file} ({e})")
    return scan
