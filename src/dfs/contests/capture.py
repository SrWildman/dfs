"""Finding the two DraftKings downloads and keeping them: no sheet, no network, nothing outside `data/`.

Sam downloads two kinds of file by hand and they pile up in `~/Downloads`:

- the **contest-entry history** (My Contests -> History), one CSV for the whole account. It is found by its
  **header row**, never its name (DraftKings names it whatever it likes), and the newest match wins. A copy
  is kept in `data/raw/dk_history/`, because the next export would otherwise overwrite what the sheet shows.
- the per-contest **standings** (`contest-standings-<id>.csv`, bare or in a zip of any name). DraftKings
  serves them for about **two weeks** and then sends an empty file, so they have to be grabbed promptly and
  kept: each is *moved* into `data/raw/dk_standings/`.

Nothing here is ever deleted from Downloads except by the move itself, an empty (0-byte) download is reported
and left where it is, and a contest already captured is never captured twice.
"""

from __future__ import annotations

import hashlib
import re
import shutil
import zipfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

HISTORY_HEADER = ("Entry_Key", "Contest_Key", "Contest_Date_EST", "Entry_Fee", "Places_Paid")
STANDINGS_CSV = re.compile(r"^contest-standings-(\d+)(?:\s*\(\d+\))?\.csv$", re.IGNORECASE)
_DATED = re.compile(r"\d{4}-\d{2}-\d{2}$")
STANDINGS_EXPIRY_NOTE = (
    "DraftKings only serves a contest's standings for about two weeks, then sends an empty file: download "
    "them each Monday or Tuesday, they cannot be backfilled."
)


def default_downloads_dir() -> Path:
    return Path.home() / "Downloads"


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# ---- the history export ---------------------------------------------------------------------------


def is_history_export(path: Path) -> bool:
    """True when the file's header row has the DraftKings contest-history columns (the name is ignored)."""
    try:
        with Path(path).open("rb") as handle:
            first = handle.readline(4096)
    except OSError:
        return False
    header = {col.strip().strip('"') for col in first.decode("utf-8-sig", errors="replace").split(",")}
    return all(col in header for col in HISTORY_HEADER)


def find_history_export(folder: Path) -> Path | None:
    """The newest history export (by modified time) in `folder`, found by header; None when there is none."""
    folder = Path(folder)
    if not folder.is_dir():
        return None
    found = [p for p in folder.glob("*.csv") if p.is_file() and p.stat().st_size and is_history_export(p)]
    return max(found, key=lambda p: (p.stat().st_mtime, p.name)) if found else None


def keep_history_copy(source: Path, dest_dir: Path) -> tuple[Path, bool]:
    """Copies the export into `dest_dir` unless an identical copy is already there.

    Returns `(path of the kept copy, newly copied)`. The copy is named after the export plus its modified
    date when the name has none, and a different file with the same name gets `-2`, `-3` ... (never
    overwritten)."""
    source, dest_dir = Path(source), Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    data = source.read_bytes()
    digest = _sha(data)
    for existing in dest_dir.glob("*.csv"):
        if existing.stat().st_size == len(data) and _sha(existing.read_bytes()) == digest:
            return existing, False
    stem = source.stem
    if not _DATED.search(stem):
        stem = f"{stem}-{datetime.fromtimestamp(source.stat().st_mtime):%Y-%m-%d}"
    target, n = dest_dir / f"{stem}.csv", 1
    while target.exists():
        n += 1
        target = dest_dir / f"{stem}-{n}.csv"
    shutil.copy2(source, target)
    return target, True


# ---- the standings files --------------------------------------------------------------------------


@dataclass
class StandingsCapture:
    moved: list[tuple[Path, list[str]]] = field(default_factory=list)  # (new location, contest ids)
    already: list[tuple[Path, list[str]]] = field(default_factory=list)  # left in place: ids already kept
    empty: list[Path] = field(default_factory=list)  # 0-byte (failed or expired download), left in place

    @property
    def new_ids(self) -> list[str]:
        return [cid for _, ids in self.moved for cid in ids]


def _zip_members(path: Path) -> list[tuple[str, int]] | None:
    """`[(contest id, size)]` for the standings CSVs inside a zip, or None when it is not a readable zip."""
    try:
        with zipfile.ZipFile(path) as archive:
            return [
                (m.group(1), info.file_size)
                for info in archive.infolist()
                if (m := STANDINGS_CSV.match(Path(info.filename).name))
            ]
    except (zipfile.BadZipFile, OSError):
        return None


def _kept_ids(dest_dir: Path) -> set[str]:
    """Contest ids already in `dest_dir` (bare files and zip members, any depth)."""
    ids: set[str] = set()
    if not dest_dir.is_dir():
        return ids
    for path in dest_dir.rglob("*"):
        if not path.is_file():
            continue
        if path.suffix.lower() == ".zip":
            ids.update(cid for cid, size in _zip_members(path) or [] if size)
        elif (m := STANDINGS_CSV.match(path.name)) and path.stat().st_size:
            ids.add(m.group(1))
    return ids


def candidate_standings(folder: Path) -> list[Path]:
    """Standings downloads in `folder` (not recursive): `contest-standings-<id>.csv` and any zip that holds
    one, oldest first."""
    folder = Path(folder)
    if not folder.is_dir():
        return []
    found = []
    for path in folder.iterdir():
        if not path.is_file():
            continue
        if STANDINGS_CSV.match(path.name):
            found.append(path)
        elif path.suffix.lower() == ".zip" and _zip_members(path):
            found.append(path)
    return sorted(found, key=lambda p: (p.stat().st_mtime, p.name))


def capture_standings(downloads: Path, dest_dir: Path) -> StandingsCapture:
    """Moves every new standings download into `dest_dir`; reports the empty and the already-kept ones."""
    result = StandingsCapture()
    dest_dir = Path(dest_dir)
    kept = _kept_ids(dest_dir)
    for path in candidate_standings(downloads):
        if path.suffix.lower() == ".zip":
            members = _zip_members(path) or []
            ids = [cid for cid, size in members if size]
            if not ids:
                result.empty.append(path)
                continue
        else:
            if path.stat().st_size == 0:
                result.empty.append(path)
                continue
            ids = [STANDINGS_CSV.match(path.name).group(1)]  # type: ignore[union-attr]
        if all(cid in kept for cid in ids):
            result.already.append((path, ids))
            continue
        dest_dir.mkdir(parents=True, exist_ok=True)
        target, n = dest_dir / path.name, 1
        while target.exists():
            n += 1
            target = dest_dir / f"{path.stem}-{n}{path.suffix}"
        shutil.move(str(path), str(target))
        kept.update(ids)
        result.moved.append((target, ids))
    return result


def standings_bytes(path: Path) -> list[tuple[str, bytes]]:
    """`[(contest id, raw bytes)]` of the non-empty standings in a file (a bare CSV or a zip)."""
    path = Path(path)
    if path.suffix.lower() != ".zip":
        found = STANDINGS_CSV.match(path.name)
        data = path.read_bytes()
        return [(found.group(1), data)] if found and data else []
    out = []
    with zipfile.ZipFile(path) as archive:
        for info in archive.infolist():
            found = STANDINGS_CSV.match(Path(info.filename).name)
            if found and info.file_size:
                out.append((found.group(1), archive.read(info)))
    return out


def describe(report: StandingsCapture) -> list[str]:
    """The lines `week close` prints about the standings."""
    lines = []
    for path, ids in report.moved:
        lines.append(f"  kept standings {path.name} (contest {', '.join(ids)}) in {path.parent}")
    for path, ids in report.already:
        lines.append(f"  already kept: {path.name} (contest {', '.join(ids)}); left in Downloads")
    for path in report.empty:
        lines.append(
            f"  EMPTY download, skipped: {path.name} (a failed or expired export). {STANDINGS_EXPIRY_NOTE}"
        )
    if not (report.moved or report.already or report.empty):
        lines.append(f"  no standings files in Downloads. {STANDINGS_EXPIRY_NOTE}")
    return lines
