"""Files dropped onto the Week page: the DK entries file at lock, the contest-history CSV and the standings
zips at close.

Measurement only works if these files get in every week, so a drop zone beats remembering to move them. What
is accepted, and how it is checked:

- **Only `.csv` and `.zip`.** The name is reduced to a safe file name (no folders, no odd characters); the
  type that counts is what the content turns out to be, never the name.
- **Size limits** before anything is read (`MAX_CSV_BYTES`, `MAX_ZIP_BYTES`), and for a zip before anything is
  opened: the member count, each member's size (`standings.MAX_MEMBER_BYTES`, the same limit `standings.py`
  uses), the total uncompressed size and the compression ratio (a zip bomb is refused from its directory, not
  after it has been unpacked).
- **Parsed with the existing parsers only:** `entries.read_entries_file` for DK's entries file,
  `capture.is_history_export` for a contest-history export, `standings.scan_standings` for standings (a zip's
  members are read in memory, never extracted). Nothing in a file is executed, imported or opened as anything
  but data.
- **Recognised, then filed.** A file that is none of the three is refused and not kept. A recognised file is
  saved into the Downloads folder (`[contests] downloads_dir`), where `dfs entries record` and
  `dfs week close` already look, under its own name (a number is added rather than overwrite anything).
  Files are never deleted and nothing here runs a command: the page shows what was recognised and offers the
  matching command to run.

For the entries file the page also shows the **spend so far by tier** (`contest_tiers`) against the week's
plan, so what is over can still be withdrawn before lock.
"""

from __future__ import annotations

import csv
import io
import re
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pandas as pd

from dfs import contest_tiers, entered, paths
from dfs import entries as ent
from dfs.contests import capture, history, standings

MAX_CSV_BYTES = 25 * 1024 * 1024
MAX_ZIP_BYTES = 150 * 1024 * 1024
ZIP_MAX_MEMBERS = 2000
ZIP_MAX_TOTAL_BYTES = 400 * 1024 * 1024
ZIP_MAX_RATIO = 200  # uncompressed : compressed, for any one member
ALLOWED_SUFFIXES = (".csv", ".zip")
NAME_CHARS = re.compile(r"[^A-Za-z0-9._ ()\-]+")


class Rejected(ValueError):
    """The file was refused; the message says why. `status` is the HTTP status to answer with."""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


@dataclass
class Dropped:
    kind: str  # entries | history | standings
    saved: Path
    summary: list[str]
    runs: list[dict[str, Any]] = field(default_factory=list)  # {"label", "command", "values"}
    tiers: dict[str, Any] | None = None
    warnings: list[str] = field(default_factory=list)

    def as_json(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "saved": str(self.saved),
            "name": self.saved.name,
            "summary": self.summary,
            "runs": self.runs,
            "tiers": self.tiers,
            "warnings": self.warnings,
        }


def max_upload_bytes(filename: str) -> int:
    return MAX_ZIP_BYTES if filename.lower().endswith(".zip") else MAX_CSV_BYTES


def safe_name(filename: str) -> str:
    """The file name alone (no folders), restricted to ordinary characters, never starting with a dot."""
    base = Path(str(filename).replace("\\", "/")).name.strip()
    base = NAME_CHARS.sub("_", base).lstrip(".").strip()
    return base[:120]


def check_name_and_size(filename: str, size: int) -> str:
    """The safe name, or `Rejected` for a type that is not allowed or a file over its limit."""
    name = safe_name(filename)
    if not name or not name.lower().endswith(ALLOWED_SUFFIXES):
        raise Rejected("Only .csv and .zip files can be dropped here.", 415)
    if size <= 0:
        raise Rejected("That file is empty.")
    if size > max_upload_bytes(name):
        raise Rejected(
            f"That file is too large ({size / 1e6:.0f} MB; the limit is "
            f"{max_upload_bytes(name) / 1e6:.0f} MB).",
            413,
        )
    return name


def check_zip(data: bytes) -> None:
    """Refuse a zip that could be a bomb or is not a zip at all, from its directory alone."""
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise Rejected("That is not a readable zip file.") from exc
    with archive:
        infos = archive.infolist()
        if len(infos) > ZIP_MAX_MEMBERS:
            raise Rejected(f"That zip holds {len(infos)} files; the limit is {ZIP_MAX_MEMBERS}.")
        total = 0
        for info in infos:
            if info.is_dir():
                continue
            if info.file_size > standings.MAX_MEMBER_BYTES:
                raise Rejected(f"{info.filename} claims {info.file_size:,} bytes: refused.")
            if info.compress_size and info.file_size / info.compress_size > ZIP_MAX_RATIO:
                raise Rejected(
                    f"{info.filename} is compressed {info.file_size // max(1, info.compress_size)}:1: "
                    "refused."
                )
            total += info.file_size
        if total > ZIP_MAX_TOTAL_BYTES:
            raise Rejected(
                f"That zip unpacks to {total / 1e6:.0f} MB; the limit is {ZIP_MAX_TOTAL_BYTES / 1e6:.0f} MB."
            )


def unique_path(folder: Path, name: str) -> Path:
    """`folder/name`, or `name (2).csv` and so on: a drop never overwrites a file that is there."""
    target = folder / name
    n = 2
    stem, suffix = Path(name).stem, Path(name).suffix
    while target.exists():
        target = folder / f"{stem} ({n}){suffix}"
        n += 1
    return target


def _first_row(data: bytes) -> list[str]:
    try:
        text = data[:20000].decode("utf-8-sig", errors="replace")
        return next(csv.reader(io.StringIO(text)), [])
    except csv.Error:
        return []


def _is_history_header(data: bytes) -> bool:
    header = {c.strip().strip('"') for c in _first_row(data)}
    return all(col in header for col in capture.HISTORY_HEADER)


def classify_content(name: str, data: bytes) -> str:
    """`entries`, `history` or `standings`; `Rejected` when it is none of them."""
    if name.lower().endswith(".zip"):
        check_zip(data)
        return "standings"
    if ent.looks_like_entries_header(_first_row(data)):
        return "entries"
    if _is_history_header(data):
        return "history"
    if standings.contest_id_from_name(name):
        return "standings"
    raise Rejected(
        "That is not one of the three files this page takes: DraftKings' entries file, your contest-history "
        "export, or the standings (a .csv named contest-standings-<id>, or the zip of them).",
        422,
    )


def money(text: str) -> float:
    try:
        return float(str(text).replace("$", "").replace(",", "").strip() or 0)
    except ValueError:
        return 0.0


# -- what each kind says ---------------------------------------------------------------------------------


def describe_entries(
    path: Path,
    *,
    season: int,
    week: int,
    budget: float | None,
    cfg_plan: Any,
    typed: pd.DataFrame | None,
    opponents: pd.DataFrame | None,
) -> tuple[list[str], list[str], dict[str, Any] | None]:
    """`(summary lines, warnings, spend by tier)` for an entries file already saved at `path`."""
    try:
        parsed = ent.read_entries_file(path)
    except (ent.EntriesFileError, OSError, ValueError) as exc:
        raise Rejected(f"DraftKings' entries file could not be read: {exc}", 422) from exc
    kept, skipped = ent.classify_entries(parsed)
    lineups = entered.from_entries(kept, parsed.players)
    contests = {e.file.contest_id for e in parsed.entries}
    summary = [
        f"entries for {len(lineups)} lineup{'s' if len(lineups) != 1 else ''}, "
        f"{len(contests)} contest{'s' if len(contests) != 1 else ''} "
        f"({len(parsed.entries)} entries in the file; {len(kept)} Classic main-slate)"
    ]
    if skipped:
        summary.append(
            "not counted as Classic main slate: " + ", ".join(f"{k} {v}" for k, v in skipped.items())
        )
    warnings = []
    found = entered.file_week(parsed.players)
    if found != (season, week):
        seen = f"Week {found[1]} of {found[0]}" if found and found[1] else "no week"
        warnings.append(
            f"This file is for {seen}, not Week {week} of {season}: `entries record` will refuse it."
        )

    pairs = [(e.file.contest_name, ent.entry_format(e.file), money(e.file.entry_fee)) for e in parsed.entries]
    avoid = [] if opponents is None else _avoid_names(opponents)
    history_sizes = contest_tiers.history_by_series(typed) if typed is not None and len(typed) else {}
    tiered = contest_tiers.classify_entered(pairs, history=history_sizes, avoid_opponents=avoid)
    tiers = None
    total = sum(fee for _n, _f, fee in pairs)
    plan = (
        contest_tiers.plan_dollars(
            budget, showdown_max=cfg_plan.showdown_max, big_multi_max=cfg_plan.big_nmax_max
        )
        if budget
        else None
    )
    if plan is not None:
        spend = contest_tiers.spend_by_tier([(t.verdict, t.fee) for t in tiered], plan)
        tiers = {
            "plan": {k: plan.dollars(k) for k in ("A", "B", "C")},
            "spent": spend.spent,
            "entries": spend.entries,
            "over": spend.over,
            "line": spend.line(),
            "budget": budget,
            "total": total,
        }
    else:
        spent = dict.fromkeys(contest_tiers.TIERS, 0.0)
        for t in tiered:
            spent[t.verdict.tier] += t.fee
        tiers = {"plan": None, "spent": spent, "over": [], "line": "", "budget": None, "total": total}
    tiers["contests"] = contest_tiers.verdict_rows(tiered)
    return summary, warnings, tiers


def _avoid_names(opponents: pd.DataFrame) -> list[str]:
    from dfs import contest_plan

    return contest_plan.avoid_names(opponents)


def describe_history(path: Path) -> list[str]:
    try:
        frame = history.read_export(path)
    except Exception as exc:  # noqa: BLE001 - whatever the parser says, the file is not usable
        raise Rejected(f"The contest-history export could not be read: {exc}", 422) from exc
    return [f"contest-history export: {len(frame):,} entries"]


def describe_standings(path: Path) -> list[str]:
    scan = standings.scan_standings(path)
    if not scan.sources:
        extra = f" ({len(scan.empty)} empty)" if scan.empty else ""
        raise Rejected(f"No standings files were found in that upload{extra}.", 422)
    lines = [f"standings for {len(scan.sources)} contest{'s' if len(scan.sources) != 1 else ''}"]
    for label, found in (
        ("empty", scan.empty),
        ("not identified", scan.unidentified),
        ("repeated", scan.duplicates),
    ):
        if found:
            lines.append(f"{len(found)} {label}")
    return lines


# -- the whole drop --------------------------------------------------------------------------------------


def receive(
    filename: str,
    data: bytes,
    *,
    downloads: Path,
    season: int,
    week: int,
    budget: float | None = None,
    cfg_plan: Any = None,
    typed: pd.DataFrame | None = None,
    opponents: pd.DataFrame | None = None,
) -> Dropped:
    """Check, recognise, file and describe one dropped file. Raises `Rejected` (nothing is kept) otherwise."""
    name = check_name_and_size(filename, len(data))
    kind = classify_content(name, data)
    downloads = Path(downloads).expanduser()
    downloads.mkdir(parents=True, exist_ok=True)
    target = unique_path(downloads, name)
    target.write_bytes(data)
    try:
        warnings: list[str] = []
        tiers = None
        if kind == "entries":
            summary, warnings, tiers = describe_entries(
                target,
                season=season,
                week=week,
                budget=budget,
                cfg_plan=cfg_plan,
                typed=typed,
                opponents=opponents,
            )
            runs = [
                {
                    "label": "Record these entries",
                    "command": "dfs entries record",
                    "values": {"csv": str(target)},
                }
            ]
        elif kind == "history":
            summary = describe_history(target)
            runs = [
                {
                    "label": "Close the week with this export",
                    "command": "dfs week close",
                    "values": {"csv": str(target)},
                },
                {
                    "label": "File it only (no sheet)",
                    "command": "dfs contests update",
                    "values": {"csv": str(target), "no_sheet": True},
                },
            ]
        else:
            summary = describe_standings(target)
            runs = [
                {"label": "Close the week", "command": "dfs week close", "values": {}},
                {
                    "label": "File them only (no sheet)",
                    "command": "dfs contests update",
                    "values": {"no_sheet": True},
                },
            ]
    except Rejected:
        # our own copy of a file we are refusing: nothing of Sam's is deleted
        target.unlink(missing_ok=True)
        raise
    return Dropped(kind, target, summary, runs, tiers, warnings)


def default_downloads(configured: str | None) -> Path:
    return Path(configured).expanduser() if configured else capture.default_downloads_dir()


def load_typed() -> pd.DataFrame | None:
    path = paths.CONTESTS_DIR / "contest_log.csv"
    if not path.exists():
        return None
    try:
        return history.typed(history.read_log(path))
    except Exception:  # noqa: BLE001
        return None


def load_opponents() -> pd.DataFrame | None:
    path = paths.CONTESTS_DIR / "h2h_opponents.csv"
    try:
        return pd.read_csv(path) if path.exists() else None
    except Exception:  # noqa: BLE001
        return None
