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

from dfs import store
from dfs.derived import build_edge_frame
from dfs.line_movement import LineMovementError, diff_odds
from dfs.log import get_logger
from dfs.sources.base import Source, SyncContext

log = get_logger("sources.edge")


def _try_load_current(source_name: str) -> pd.DataFrame | None:
    """Like store.load_current, but None (not a raised error) when that
    source hasn't been synced -- games/weather are optional enhancements to
    the edge layer, not required inputs (unlike projections/draftkings)."""
    try:
        return store.load_current(source_name)
    except FileNotFoundError:
        log.info("%s not synced yet -- edge will compute without it", source_name)
        return None


def _try_diff_odds() -> pd.DataFrame | None:
    """Line movement needs two nfl_odds snapshots -- None (not raised) on
    the first sync of the week, when only one exists yet."""
    try:
        previous = store.load_previous("nfl_odds")
        current = store.load_current("nfl_odds")
    except FileNotFoundError:
        log.info("not enough nfl_odds snapshots yet for line movement -- edge will compute without it")
        return None
    try:
        return diff_odds(previous, current)
    except LineMovementError as e:
        log.warning("line movement diff failed: %s -- edge will compute without it", e)
        return None


class EdgeSource(Source):
    name = "edge"

    def fetch(self, ctx: SyncContext) -> pd.DataFrame:
        # ctx unused: inputs are already this week's synced CSVs.
        projections = store.load_current("projections")
        salaries = store.load_current("draftkings")
        games = _try_load_current("nflverse_games")
        weather = _try_load_current("weather")
        line_movement = _try_diff_odds()

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
