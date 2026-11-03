"""Orchestrates fetch -> local store -> Sheets upload across the source
registry, with a real per-source result instead of a single boolean.

Replaces run_all.py/run_update.py, whose main() return value was discarded
by __main__ (always exit 0), and utils/sheets_uploader.py's overall success
being `any(results.values())` -- one tab out of eight uploading counted as
the whole run succeeding.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

import pandas as pd

from dfs import perf, sync_guard
from dfs.config import Config
from dfs.log import get_logger
from dfs.sheets import SheetsClient, SheetsError
from dfs.sources import get_source
from dfs.sources.base import SourceSkipped, SyncContext
from dfs.store import load_current, record_failure, record_success, save

log = get_logger("sync")


@dataclass
class SourceResult:
    source: str
    ok: bool
    rows: int | None
    error: str | None
    # What the game-day guard kept instead of taking the feed (`sync_guard`), one line each.
    notes: list[str] = field(default_factory=list)


def _previous(name: str) -> pd.DataFrame | None:
    """The source's last saved copy, for the guard; None when there is none or it cannot be read."""
    if name not in sync_guard.POLICIES:
        return None
    try:
        return load_current(name)
    except (FileNotFoundError, OSError, ValueError, pd.errors.EmptyDataError):
        return None


def run_sync(
    cfg: Config,
    source_names: list[str],
    ctx: SyncContext,
    *,
    upload: bool = True,
    now: datetime | None = None,
) -> list[SourceResult]:
    """Fetch, guard, save and upload each source in order. The guard (`sync_guard.guard`) merges a feed
    with the last saved copy first, so a game-day sync never blanks a started player or a whole column."""
    now = now or datetime.now(UTC)
    client = SheetsClient(cfg.google_sheets) if upload else None
    results: list[SourceResult] = []

    for name in source_names:
        try:
            source = get_source(name)
        except KeyError as e:
            results.append(SourceResult(name, False, None, str(e)))
            continue

        log.info("syncing %s", name)
        try:
            with perf.phase(f"fetch {name}"):
                df = source.fetch(ctx)
            guarded = sync_guard.guard(name, df, _previous(name), week=ctx.week, season=ctx.season, now=now)
            df = guarded.frame
            for note in guarded.notes:
                log.warning("%s", note)
            save(name, df)

            if upload and source.uploads_to_sheet:
                tab = cfg.google_sheets.tab_mappings.get(name)
                if tab is None:
                    raise SheetsError(
                        f"No tab mapped for source {name!r} in config.toml [google_sheets.tab_mappings]."
                    )
                with perf.phase(f"upload {name}"):
                    preserved = source.pre_upload(client, tab)
                    rows = source.to_sheet_rows(df)
                    client.write_tab(tab, rows)
                    source.post_upload(client, tab, df, preserved)

            record_success(name, len(df))
            results.append(SourceResult(name, True, len(df), None, guarded.notes))
        except SourceSkipped as e:
            log.info("skipped %s: %s", name, e)
            results.append(SourceResult(name, True, 0, f"skipped: {e}"))
        except Exception as e:  # noqa: BLE001 - report every failure, never swallow
            log.error("sync failed for %s: %s", name, e)
            record_failure(name, str(e))
            results.append(SourceResult(name, False, None, str(e)))

    return results
