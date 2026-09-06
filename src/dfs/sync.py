"""Orchestrates fetch -> local store -> Sheets upload across the source
registry, with a real per-source result instead of a single boolean.

Replaces run_all.py/run_update.py, whose main() return value was discarded
by __main__ (always exit 0), and utils/sheets_uploader.py's overall success
being `any(results.values())` -- one tab out of eight uploading counted as
the whole run succeeding.
"""

from __future__ import annotations

from dataclasses import dataclass

from dfs.config import Config
from dfs.log import get_logger
from dfs.sheets import SheetsClient, SheetsError
from dfs.sources import get_source
from dfs.sources.base import SyncContext
from dfs.store import record_failure, record_success, save

log = get_logger("sync")


@dataclass
class SourceResult:
    source: str
    ok: bool
    rows: int | None
    error: str | None


def run_sync(
    cfg: Config,
    source_names: list[str],
    ctx: SyncContext,
    *,
    upload: bool = True,
) -> list[SourceResult]:
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
            df = source.fetch(ctx)
            save(name, df)

            if upload:
                tab = cfg.google_sheets.tab_mappings.get(name)
                if tab is None:
                    raise SheetsError(
                        f"No tab mapped for source {name!r} in config.toml [google_sheets.tab_mappings]."
                    )
                preserved = source.pre_upload(client, tab)
                rows = source.to_sheet_rows(df)
                client.write_tab(tab, rows)
                source.post_upload(client, tab, df, preserved)

            record_success(name, len(df))
            results.append(SourceResult(name, True, len(df), None))
        except Exception as e:  # noqa: BLE001 - report every failure, never swallow
            log.error("sync failed for %s: %s", name, e)
            record_failure(name, str(e))
            results.append(SourceResult(name, False, None, str(e)))

    return results
