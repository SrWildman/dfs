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
from datetime import datetime, timezone

import pandas as pd

from dfs.paths import CURRENT_DIR, MANIFEST_FILE, RAW_DIR, ensure_data_dirs


def _now_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


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
        "synced_at": datetime.now(timezone.utc).isoformat(),
        "rows": rows,
        "error": None,
    }
    _write_manifest(manifest)


def record_failure(source_name: str, error: str) -> None:
    manifest = read_manifest()
    manifest[source_name] = {
        "synced_at": datetime.now(timezone.utc).isoformat(),
        "rows": None,
        "error": error,
    }
    _write_manifest(manifest)
