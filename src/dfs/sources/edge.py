"""Derived edge-layer signals (Leverage, CeilVal, GameEnv, LineMove, Avail,
Flag) computed locally from already-synced sources -- no network call of
its own.

Registered last in `SOURCES` (see `sources/__init__.py`) so a full `dfs
sync` computes this off the CSVs the earlier sources in that same run just
saved to `data/current/`. `dfs sync --only edge` recomputes offline from
whatever's already on disk and raises a clear `FileNotFoundError` (via
`store.load_current`) if projections or draftkings haven't been synced yet
-- the same "raise, don't return partial data" contract as every other
source.
"""

from __future__ import annotations

import pandas as pd

from dfs import nfl_calendar, store
from dfs.derived import EDGE_COLUMNS, build_edge_frame
from dfs.line_movement import LineMovementError, diff_odds
from dfs.log import get_logger
from dfs.sheets import SheetsClient, column_letter
from dfs.sources.base import Source, SyncContext

log = get_logger("sources.edge")

# First free column after EDGE_COLUMNS (currently V) -- computed rather
# than hardcoded "W" so it tracks EDGE_COLUMNS automatically if that list
# ever grows. Deliberately NOT added to EDGE_COLUMNS itself: every linked
# VLOOKUP elsewhere hardcodes column-index integers against
# EdgeRaw!$B:$V (see sheet_links.py's docstring and CONTRIBUTING.md's
# Phase 8 postmortem) -- coupling the tick to that range for no reason
# would coincidentally be harmless today but coupling it anyway is the
# exact hazard class that's bitten this project more than once.
POOL_COLUMN = column_letter(len(EDGE_COLUMNS))
POOL_HEADER = "Pool"
# Matches write_tab's default worksheet sizing (see cli.py's
# _EDGE_FORMAT_LAST_ROW) -- the range every EdgeRaw column operation uses.
_LAST_ROW = 1000


def _try_load_current(source_name: str) -> pd.DataFrame | None:
    """Like store.load_current, but None (not a raised error) when that
    source hasn't been synced -- games/weather are optional enhancements to
    the edge layer, not required inputs (unlike projections/draftkings)."""
    try:
        return store.load_current(source_name)
    except FileNotFoundError:
        log.info("%s not synced yet -- edge will compute without it", source_name)
        return None


def _try_diff_odds(ctx: SyncContext) -> pd.DataFrame | None:
    """LineMove: since the start of the current NFL week, not since the
    last sync -- diffing against the last sync made the number depend on
    how often `dfs sync` happens to get run, which isn't a real signal.
    `store.load_since` falls back to the earliest snapshot on disk when
    the week just started, so this only returns None when there's no
    nfl_odds data at all yet."""
    try:
        baseline = store.load_since("nfl_odds", nfl_calendar.week_start_date(ctx.week, ctx.season))
        current = store.load_current("nfl_odds")
    except FileNotFoundError:
        log.info("no nfl_odds snapshots yet for line movement -- edge will compute without it")
        return None
    try:
        return diff_odds(baseline, current)
    except LineMovementError as e:
        log.warning("line movement diff failed: %s -- edge will compute without it", e)
        return None


class EdgeSource(Source):
    name = "edge"

    def fetch(self, ctx: SyncContext) -> pd.DataFrame:
        projections = store.load_current("projections")
        salaries = store.load_current("draftkings")
        games = _try_load_current("nflverse_games")
        weather = _try_load_current("weather")
        line_movement = _try_diff_odds(ctx)

        result = build_edge_frame(
            projections, salaries, games=games, weather=weather, line_movement=line_movement
        )
        if result.unmatched_names:
            log.warning(
                "%d projected player(s) had no DraftKings salary match on the current "
                "main slate (e.g. a Thursday/Monday-only player TFFB still projects): %s",
                len(result.unmatched_names),
                ", ".join(result.unmatched_names),
            )
        return result.frame

    def to_sheet_rows(self, df: pd.DataFrame) -> list[list]:
        """EDGE_COLUMNS plus an extra, blank Pool header cell -- values are
        never written here (pre_upload/post_upload restore ticks by Id
        after write_tab clears the tab), only the header, so the column
        exists and is labeled even on a brand-new EdgeRaw tab."""
        rows = super().to_sheet_rows(df)
        rows[0] = [*rows[0], POOL_HEADER]
        return rows

    def pre_upload(self, client: SheetsClient, tab: str) -> dict[str, bool]:
        """Read which players are currently ticked, keyed by Id (not row
        position or Name -- DST names aren't unique, see derived.py's own
        docstring, and EdgeRaw is sorted by Leverage so row order shifts
        between syncs) before write_tab's `ws.clear()` wipes both the Id
        and Pool columns. Same preserve-by-key pattern as
        sheet_views.build_exposure's Target column."""
        if not client.tab_exists(tab):
            return {}
        try:
            ids = client.read_range(tab, f"A2:A{_LAST_ROW}")
            ticks = client.read_range(tab, f"{POOL_COLUMN}2:{POOL_COLUMN}{_LAST_ROW}")
        except Exception:  # noqa: BLE001 - a malformed/missing prior tab must not block a sync
            return {}
        preserved: dict[str, bool] = {}
        for i, id_row in enumerate(ids):
            player_id = id_row[0].strip() if id_row and id_row[0] else ""
            if not player_id:
                continue
            tick = ticks[i][0] if i < len(ticks) and ticks[i] else ""
            if str(tick).strip().upper() == "TRUE":
                preserved[player_id] = True
        return preserved

    def post_upload(self, client: SheetsClient, tab: str, df: pd.DataFrame, preserved: object | None) -> None:
        """Restore ticks for any Id from `preserved` still present after the
        rewrite (players no longer on the slate drop out silently -- correct,
        they're gone from the sheet entirely; new players start unticked),
        then (re)apply the checkbox validation -- cheap and idempotent, and
        guards against a first-ever sync leaving Pool with no validation at
        all."""
        preserved = preserved or {}
        last_row = len(df) + 1
        if last_row >= 2:
            client.set_checkbox_validation(tab, f"{POOL_COLUMN}2:{POOL_COLUMN}{last_row}")
        if not preserved:
            return
        ids = client.read_range(tab, f"A2:A{last_row}")
        restore = [[True] if row and row[0].strip() in preserved else [""] for row in ids]
        if restore:
            client.update_range(tab, f"{POOL_COLUMN}2:{POOL_COLUMN}{last_row}", restore)
