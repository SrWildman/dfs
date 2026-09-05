"""Local persistence for synced source data: raw history + a "current" copy
+ a manifest `dfs status` reads.

Replaces utils/manage_downloads.py's ~/Downloads content-sniffing (the
thing that filed a DraftKings contest-history export into the salary slot
because its filename matched the substring 'draftkings') -- there is no
sniffing here because each source already returns data under its own
known name. Also replaces csv_cleanup.py's `clear_old_csvs()`, which
unlinked every CSV *before* scraping (run_all.py:78), so a failed run
left you with nothing; here, new data is written as new, old data is
kept until something explicitly asks to prune it.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime

import pandas as pd

from dfs.paths import CURRENT_DIR, MANIFEST_FILE, RAW_DIR, ensure_data_dirs

_SNAPSHOT_STAMP_FORMAT = "%Y%m%dT%H%M%SZ"


def _now_stamp() -> str:
    return datetime.now(UTC).strftime(_SNAPSHOT_STAMP_FORMAT)


def save(source_name: str, df: pd.DataFrame) -> None:
    ensure_data_dirs()
    raw_dir = RAW_DIR / source_name
    raw_dir.mkdir(parents=True, exist_ok=True)

    df.to_csv(raw_dir / f"{_now_stamp()}.csv", index=False)
    df.to_csv(CURRENT_DIR / f"{source_name}.csv", index=False)


def load_current(source_name: str) -> pd.DataFrame:
    path = CURRENT_DIR / f"{source_name}.csv"
    if not path.exists():
        raise FileNotFoundError(f"No synced data for {source_name!r} yet -- run `dfs sync`.")
    return pd.read_csv(path)


def load_previous(source_name: str) -> pd.DataFrame:
    """The snapshot before the most recent one -- i.e. what `load_current`
    would have returned right before the last sync. Snapshot filenames are
    UTC timestamps (`_now_stamp`), so sorting them as strings already sorts
    them chronologically. Used for line-movement diffing: this needs no new
    storage, since `save()` has always kept every snapshot under
    `data/raw/<source>/`."""
    raw_dir = RAW_DIR / source_name
    snapshots = sorted(raw_dir.glob("*.csv")) if raw_dir.exists() else []
    if len(snapshots) < 2:
        raise FileNotFoundError(
            f"Only {len(snapshots)} synced snapshot(s) of {source_name!r} on disk -- "
            "need at least two (sync again later to get a second) before there's a "
            "previous one to diff against."
        )
    return pd.read_csv(snapshots[-2])


def load_since(source_name: str, since: date) -> pd.DataFrame:
    """The earliest snapshot of `source_name` saved on/after `since` -- the
    "start of the week" baseline for tracking a full week's drift (see
    `nfl_calendar.week_start_date`), as opposed to `load_previous`'s
    "one sync ago" baseline. Falls back to the very earliest snapshot on
    disk if none qualify (e.g. `since` is today and only today's sync has
    happened yet), so a fresh week's first sync still returns something
    sensible -- diffing a snapshot against itself is a valid "no movement
    yet" answer, not an error."""
    raw_dir = RAW_DIR / source_name
    snapshots = sorted(raw_dir.glob("*.csv")) if raw_dir.exists() else []
    if not snapshots:
        raise FileNotFoundError(f"No synced data for {source_name!r} yet -- run `dfs sync`.")

    for path in snapshots:
        stamp = datetime.strptime(path.stem, _SNAPSHOT_STAMP_FORMAT).replace(tzinfo=UTC)
        if stamp.date() >= since:
            return pd.read_csv(path)
    return pd.read_csv(snapshots[0])


def read_manifest() -> dict:
    if not MANIFEST_FILE.exists():
        return {}
    return json.loads(MANIFEST_FILE.read_text())


def _write_manifest(manifest: dict) -> None:
    ensure_data_dirs()
    MANIFEST_FILE.write_text(json.dumps(manifest, indent=2, sort_keys=True))


def record_success(source_name: str, rows: int) -> None:
    manifest = read_manifest()
    manifest[source_name] = {
        "synced_at": datetime.now(UTC).isoformat(),
        "rows": rows,
        "error": None,
    }
    _write_manifest(manifest)


def record_failure(source_name: str, error: str) -> None:
    manifest = read_manifest()
    manifest[source_name] = {
        "synced_at": datetime.now(UTC).isoformat(),
        "rows": None,
        "error": error,
    }
    _write_manifest(manifest)
