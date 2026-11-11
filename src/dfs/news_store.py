"""`data/news/changes.csv`: the news change log on disk. **Append-only.**

The file is only ever opened for append (`append`): a row once written is never rewritten, reordered or
removed, and `tests/test_news_store.py` checks that every write leaves the earlier bytes as they were.
Readers sort by `at`; the file's own order is the order things were logged in.

- `log_latest()` is the hook `sync.run_sync` calls after it saves an EdgeRaw snapshot (`dfs sync`, `dfs sync
  --live`, `dfs week new`): the newest snapshot against the one before it.
- `log_espn()` is the late-swap refresh's hook (`dfs lineups late-swap --refresh-injuries`).
- `backfill()` walks every snapshot in `data/raw/edge/` and appends the rows of each one the log does not
  hold yet. On an empty log that rebuilds the season; run again it appends nothing, so the file is the same.
  It also fills a hole a failed hook left.

A snapshot is "held" when the log has a row with its stem; one that changed nothing has no row and is simply
diffed again (to nothing). Nothing here reads or writes a sheet, and the hooks never raise into the command
that called them (the callers catch and warn).
"""

from __future__ import annotations

import csv
import io
from collections import Counter
from datetime import datetime
from pathlib import Path

import pandas as pd

from dfs import news_log, paths

NEWS_SUBDIR = "news"
CHANGES_FILE = "changes.csv"
EDGE_SOURCE = "edge"


def changes_path(data_dir: Path | None = None) -> Path:
    return (data_dir or paths.DATA_DIR) / NEWS_SUBDIR / CHANGES_FILE


def edge_dir(raw_dir: Path | None = None) -> Path:
    return (raw_dir or paths.RAW_DIR) / EDGE_SOURCE


def append(rows: list[dict], path: Path | None = None) -> int:
    """Append `rows` (`news_log.COLUMNS`) to the log, the header first when the file is new. One write."""
    if not rows:
        return 0
    path = path or changes_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    fresh = not path.exists() or path.stat().st_size == 0
    block = io.StringIO()
    writer = csv.DictWriter(block, fieldnames=news_log.COLUMNS, lineterminator="\n")
    if fresh:
        writer.writeheader()
    writer.writerows(rows)
    with path.open("a", encoding="utf-8", newline="") as f:
        f.write(block.getvalue())
    return len(rows)


def read(path: Path | None = None) -> pd.DataFrame:
    """The whole log as text columns (`at` sorts as text), in file order; empty when there is none."""
    path = path or changes_path()
    if not path.exists() or path.stat().st_size == 0:
        return pd.DataFrame(columns=news_log.COLUMNS)
    return pd.read_csv(path, dtype=str, keep_default_na=False)


def logged_snapshots(path: Path | None = None) -> set[str]:
    path = path or changes_path()
    if not path.exists() or path.stat().st_size == 0:
        return set()
    return set(pd.read_csv(path, usecols=["snapshot"], dtype=str, keep_default_na=False)["snapshot"])


def snapshot_files(folder: Path | None = None) -> list[Path]:
    """Every `<UTC stamp>Z.csv` in the folder, oldest first (a file with another name is not a snapshot)."""
    folder = folder or edge_dir()
    out = []
    for path in sorted(folder.glob("*.csv")) if folder.exists() else []:
        try:
            news_log.parse_stamp(path.stem)
        except ValueError:
            continue
        out.append(path)
    return out


def _load(path: Path) -> pd.DataFrame | None:
    try:
        return pd.read_csv(path, low_memory=False)
    except (OSError, ValueError, pd.errors.EmptyDataError, pd.errors.ParserError):
        return None


def _frames(files: list[Path]):
    for path in files:
        frame = _load(path)
        if (
            frame is not None
        ):  # an unreadable snapshot is skipped: the next is diffed against the last good one
            yield path.stem, frame


def log_latest(folder: Path | None = None, path: Path | None = None) -> int:
    """Log the newest EdgeRaw snapshot against the one before it; the number of rows appended (0 when it is
    the only snapshot, a new slate's first, already in the log, or nothing changed)."""
    files = snapshot_files(folder)[-2:]
    if len(files) < 2 or files[-1].stem in logged_snapshots(path):
        return 0
    rows = [row for _stem, found in news_log.replay(_frames(files)) for row in found]
    return append(rows, path)


def backfill(folder: Path | None = None, path: Path | None = None) -> Counter:
    """Append the rows of every snapshot in `folder` the log does not hold yet; {(season, week): rows
    appended}. Idempotent: a second run appends nothing."""
    held = logged_snapshots(path)
    added: Counter = Counter()
    rows: list[dict] = []
    for stem, found in news_log.replay(_frames(snapshot_files(folder))):
        if stem in held or not found:
            continue
        rows.extend(found)
        for row in found:
            added[(row["season"], row["week"])] += 1
    append(rows, path)
    return added


def log_espn(edge: pd.DataFrame, changes: list, *, at: datetime, path: Path | None = None) -> int:
    """Log a late-swap refresh's escalations (`late_swap_news.apply_news`'s changes over `edge`), each
    (player, new status) once a week."""
    if not changes:
        return 0
    season, week = news_log.week_of(edge, at)
    log = read(path)
    mine = log[
        (log["source"] == news_log.ESPN_SOURCE) & (log["season"] == str(season)) & (log["week"] == str(week))
    ]
    logged = set(zip(mine["player_id"], mine["after"], strict=True))
    return append(news_log.espn_rows(edge, changes, at=at, logged=logged), path)


def week_changes(
    season: int, week: int, *, fields: tuple[str, ...] = (), path: Path | None = None
) -> pd.DataFrame:
    """One week's rows, oldest first (`at`, then file order), cut to `fields` when given."""
    log = read(path)
    out = log[(log["season"] == str(season)) & (log["week"] == str(week))]
    if fields:
        out = out[out["field"].isin(fields)]
    return out.sort_values("at", kind="stable").reset_index(drop=True)
