"""Derived edge-layer signals (Leverage, CeilVal, GameEnv, ImpMove/TotMove/
SpdMove, Avail, Flag) computed locally from already-synced sources -- no
network call of its own.

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
from dfs.derived import EDGE_COLUMNS, EDGE_DATA_OFFSET, build_edge_frame
from dfs.line_movement import LineMovementError, diff_odds
from dfs.log import get_logger
from dfs.sheets import SheetsClient, column_letter
from dfs.sources.base import Source, SyncContext

log = get_logger("sources.edge")

# Column A -- ahead of EDGE_COLUMNS (which start at EDGE_DATA_OFFSET), not
# appended after them. Ticking is the thing Sam does every week; Id is
# meaningless to look at and gets hidden right next to it (see
# sheet_style.polish_edge), so Pool ends up visually beside Name once Id's
# hidden. Deliberately NOT added to EDGE_COLUMNS itself: every linked
# VLOOKUP elsewhere hardcodes column-index integers against
# EdgeRaw!$<Name>:$<end> (see sheet_links.py's docstring and
# CONTRIBUTING.md's Phase 8 postmortem) -- coupling the tick to that range
# for no reason would coincidentally be harmless today but coupling it
# anyway is the exact hazard class that's bitten this project more than
# once. EDGE_DATA_OFFSET (derived.py) is what every *other* module adds
# when turning an EDGE_COLUMNS index into a real EdgeRaw column letter.
POOL_COLUMN = column_letter(0)
POOL_HEADER = "Pool"
# Fix 2.11: a dropdown, not a checkbox -- blank/Cash/GPP/Both instead of
# FALSE/TRUE, so ticking a player also says which contest type(s) they're
# in the pool for. Any non-blank value means "in the pool" for Player
# Pool's own formulas (sheet_pool_formulas.py); a blank cell still means
# "not pooled", same as an unticked checkbox did. Blank is listed first
# so it's the dropdown's own "clear this" option, not just an absence.
POOL_TYPE_OPTIONS = ["", "Cash", "GPP", "Both"]
_ID_COLUMN = column_letter(EDGE_COLUMNS.index("Id") + EDGE_DATA_OFFSET)
# Matches write_tab's default worksheet sizing (see cli.py's
# _EDGE_FORMAT_LAST_ROW) -- the range every EdgeRaw column operation uses.
_LAST_ROW = 1000
# Same range `sheet_filters.add_basic_filters` gives EdgeRaw's basic filter
# -- duplicated here rather than imported (sheet_filters/sheet_style both
# import FROM this module, so the reverse import would be circular) but
# derived the same way, never a separate literal.
_FILTER_RANGE = f"A1:{column_letter(len(EDGE_COLUMNS) - 1 + EDGE_DATA_OFFSET)}{_LAST_ROW}"


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
    """ImpMove/TotMove/SpdMove: since the start of the current NFL week, not since the
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
        """Pool prepended ahead of EDGE_COLUMNS on every row -- header gets
        the real label, every data row gets an explicit blank Pool cell
        (not simply a shorter row relying on Sheets' implicit trailing
        blank, the way an *appended* column could -- a column at the
        front needs every subsequent value pushed over for real, or Id's
        value would silently land in column A under Pool's header).
        Values are never the real tick here (pre_upload/post_upload
        restore ticks by Id after write_tab clears the tab), only the
        blank placeholder, so the column exists and is labeled even on a
        brand-new EdgeRaw tab."""
        rows = super().to_sheet_rows(df)
        header, *data = rows
        return [[POOL_HEADER, *header]] + [["", *row] for row in data]

    def pre_upload(self, client: SheetsClient, tab: str) -> dict[str, str]:
        """Read each player's current Pool value (Fix 2.11: blank/Cash/
        GPP/Both, not a TRUE/FALSE checkbox), keyed by Id (not row
        position or Name -- DST names aren't unique, see derived.py's own
        docstring, and EdgeRaw is sorted by Leverage so row order shifts
        between syncs) before write_tab's `ws.clear()` wipes both the Id
        and Pool columns. Same preserve-by-key pattern as
        sheet_views.build_exposure's Target column.

        Migrates a still-live TRUE/FALSE checkbox value from before Fix
        2.11 (found on a sheet copied from a template last rebuilt before
        this shipped): TRUE -> "Both", the closest equivalent to the old
        checked state; FALSE is dropped, not preserved -- an unticked box
        meant "not pooled", not a real dropdown selection, and preserving
        the literal string "FALSE" forever would silently perpetuate a
        value that was never one of POOL_TYPE_OPTIONS. Confirmed live:
        exactly this was found on the template's own EdgeRaw."""
        if not client.tab_exists(tab):
            return {}
        try:
            # Found by reading the sheet's OWN current header, never
            # `_ID_COLUMN` -- that constant reflects EDGE_COLUMNS' TARGET
            # order, which is exactly wrong here the moment that order
            # changes: `pre_upload` runs BEFORE `write_tab` rewrites the
            # tab to match, so the sheet still has Id at its OLD position
            # at the instant this reads. Trusting the target position
            # here silently harvested a real live-sheet incident: every
            # Pool tick was captured as blank (the module-level constant
            # pointed at whatever field used to sit at Id's NEW position
            # under the OLD layout) and none were restored after the
            # rewrite. See CONTRIBUTING.md's Phase 3 changelog entry.
            header_rows = client.read_range(tab, "A1:1")
            header = header_rows[0] if header_rows else []
            if "Id" not in header:
                return {}
            id_col = column_letter(header.index("Id"))
            ids = client.read_range(tab, f"{id_col}2:{id_col}{_LAST_ROW}")
            ticks = client.read_range(tab, f"{POOL_COLUMN}2:{POOL_COLUMN}{_LAST_ROW}")
        except Exception:  # noqa: BLE001 - a malformed/missing prior tab must not block a sync
            return {}
        preserved: dict[str, str] = {}
        for i, id_row in enumerate(ids):
            player_id = id_row[0].strip() if id_row and id_row[0] else ""
            if not player_id:
                continue
            tick = ticks[i][0] if i < len(ticks) and ticks[i] else ""
            value = str(tick).strip()
            if value.upper() == "FALSE":
                continue
            if value.upper() == "TRUE":
                value = "Both"
            if value:
                preserved[player_id] = value
        return preserved

    def post_upload(self, client: SheetsClient, tab: str, df: pd.DataFrame, preserved: object | None) -> None:
        """Restore each Id from `preserved` to its previous Pool value
        (players no longer on the slate drop out silently -- correct,
        they're gone from the sheet entirely; new players start blank),
        then (re)apply the dropdown validation -- cheap and idempotent, and
        guards against a first-ever sync leaving Pool with no validation at
        all.

        Resets EdgeRaw's basic filter (clearing any sort/hidden-position
        filter a person left active) before touching data validation --
        found live: a `setDataValidation` batch write silently no-ops on
        most of its range (reproduced down to a clean 9-row range) whenever
        the tab's basic filter has an active sort. Re-running `dfs setup
        add-filters` restores the filter to its plain state after every
        sync; a person's sort/hide choice doesn't survive a sync anyway,
        since write_tab always rewrites the whole tab fresh. See
        CONTRIBUTING.md's changelog."""
        preserved = preserved or {}
        last_row = len(df) + 1
        if last_row >= 2:
            client.set_basic_filter(tab, _FILTER_RANGE)
            client.set_dropdown_validation(tab, f"{POOL_COLUMN}2:{POOL_COLUMN}{last_row}", POOL_TYPE_OPTIONS)
        if not preserved:
            return
        ids = client.read_range(tab, f"{_ID_COLUMN}2:{_ID_COLUMN}{last_row}")
        restore = [[preserved.get(row[0].strip(), "")] if row and row[0] else [""] for row in ids]
        if restore:
            client.update_range(tab, f"{POOL_COLUMN}2:{POOL_COLUMN}{last_row}", restore)
