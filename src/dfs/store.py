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
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pandas as pd

from dfs.kickoff import KICKOFF_TIMEZONE, kickoff_utc
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


def clear_current() -> list[str]:
    """Delete every `data/current/<source>.csv` convenience copy -- an
    explicit prune, used once at the start of a new week (`dfs week new`)
    so a source that hasn't synced yet under the new week reads as "no
    data" (`load_current` raises `FileNotFoundError`) rather than
    silently returning a previous week's now-stale numbers. Found live:
    `EdgeRaw`'s Salary/Val came out visibly wrong because `derived.
    build_edge_frame`'s projections<->salaries join was silently
    matching zero IDs -- the local `current` caches for the two sources
    were left over from different weeks, each internally valid but
    mutually inconsistent, and nothing had ever told either one to leave.
    Raw history under `data/raw/<source>/` is never touched here -- only
    the "current" pointer, which is what everything else actually reads;
    a fresh `dfs sync` right after this repopulates it from scratch."""
    ensure_data_dirs()
    removed = []
    if CURRENT_DIR.exists():
        for path in sorted(CURRENT_DIR.glob("*.csv")):
            path.unlink()
            removed.append(path.stem)
    return removed


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


GAME_HOURS = 4  # a game is over about this long after kickoff
_LOOKBACK_DAYS = 10  # how far before the week's Tuesday to look for the previous slate


@dataclass(frozen=True)
class Opening:
    """The baseline for a week's line movement: the snapshot, when it was saved (UTC; None only when the
    frame is the single current copy) and `how`, in words, for `dfs week new` to print."""

    frame: pd.DataFrame
    stamp: datetime | None
    how: str


def snapshot_paths(source_name: str) -> list[tuple[datetime, Path]]:
    """Every saved snapshot of a source, oldest first, with the UTC instant in its filename."""
    raw_dir = RAW_DIR / source_name
    out = []
    for path in sorted(raw_dir.glob("*.csv")) if raw_dir.exists() else []:
        try:
            out.append((datetime.strptime(path.stem, _SNAPSHOT_STAMP_FORMAT).replace(tzinfo=UTC), path))
        except ValueError:
            continue
    return out


def _kickoffs(frame: pd.DataFrame) -> pd.Series:
    """The `date` column of an odds snapshot (Eastern wall-clock kickoffs) as UTC instants."""
    if "date" not in frame.columns:
        return pd.Series(dtype="datetime64[ns, UTC]")
    return kickoff_utc(frame["date"]).dropna()


def opening_snapshot(source_name: str, since: date, *, as_of: datetime | None = None) -> Opening:
    """The week's opening lines: the earliest snapshot of `source_name` that shows the new week's slate.

    `since` is the week's first day (Tuesday). A snapshot qualifies when it was saved after the previous
    slate's last game was over (that game's kickoff plus `GAME_HOURS`) or when none of its games kick off
    before it, i.e. the source had already moved on to the new week. The previous slate's last kickoff is the
    latest game dated before `since` in any snapshot of the last `_LOOKBACK_DAYS` days. This does not depend
    on when `dfs week new` happens to run: a Monday or Tuesday snapshot (`dfs odds snapshot`) is the opening
    even if the sheet is only copied on Thursday.

    With no earlier slate on disk, or no qualifying snapshot, it falls back to the older rule: the earliest
    snapshot saved on/after `since`, else the very earliest on disk (a snapshot diffed against itself is a
    valid "no movement yet"). `as_of` ignores snapshots saved after that instant (the results loop's
    "as of kickoff" view)."""
    snapshots = [(ts, path) for ts, path in snapshot_paths(source_name) if as_of is None or ts <= as_of]
    if not snapshots:
        raise FileNotFoundError(f"No synced data for {source_name!r} yet -- run `dfs sync`.")
    cutoff = pd.Timestamp(since).tz_localize(KICKOFF_TIMEZONE).tz_convert("UTC")
    recent = [(ts, path) for ts, path in snapshots if ts >= cutoff - timedelta(days=_LOOKBACK_DAYS)]

    frames = {path: pd.read_csv(path) for _ts, path in recent}
    previous_kickoffs = [
        k[k < cutoff].max() for k in (_kickoffs(f) for f in frames.values()) if (k < cutoff).any()
    ]
    if previous_kickoffs:
        last_kickoff = max(previous_kickoffs)
        over = last_kickoff + timedelta(hours=GAME_HOURS)
        for ts, path in recent:
            kicks = _kickoffs(frames[path])
            only_new_slate = not kicks.empty and bool((kicks > last_kickoff).all())
            if ts >= over or only_new_slate:
                return Opening(frames[path], ts, "after the previous week's last game")
    for ts, path in snapshots:
        if ts >= cutoff:
            return Opening(pd.read_csv(path), ts, "first saved this week")
    ts, path = snapshots[0]
    return Opening(pd.read_csv(path), ts, "earliest on disk")


def load_since(source_name: str, since: date) -> pd.DataFrame:
    """The baseline frame for the week starting `since` (`opening_snapshot`), as opposed to `load_previous`'s
    "one sync ago" baseline (see `nfl_calendar.week_start_date`)."""
    return opening_snapshot(source_name, since).frame


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
