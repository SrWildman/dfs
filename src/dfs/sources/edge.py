"""Derived edge-layer signals (Leverage, CeilVal, GameEnv, ImpliedMove/TotMove/
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
from dfs.paths import CURRENT_DIR
from dfs.player_join import match_rate_report
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
# Part 7.10 (2026-09-18), Sam: "The pool should order players by position
# by salary high to low, but grouped by Both, Cash, GPP." Deliberately a
# SEPARATE constant from POOL_TYPE_OPTIONS above, not a re-sort of it --
# that one is the dropdown's own order, this is Player Pool's own display/
# grouping order (a `Both` player is usable in either lineup type, so he's
# core and sits first within his position). `"Both" < "Cash" < "GPP"` is
# alphabetically true too, by coincidence -- do not rely on that; rename a
# tag or add a fourth and alphabetical order silently stops matching this
# one with nothing to indicate it broke. Consumed by
# `sheet_pool_formulas.py`, which builds a Sheets MATCH() rank against
# this list's own order -- never hand-write the array into a formula
# string, so this one list is still the only place the order is decided.
POOL_TYPE_SORT_ORDER = ["Both", "Cash", "GPP"]
_ID_COLUMN = column_letter(EDGE_COLUMNS.index("Id") + EDGE_DATA_OFFSET)
# Matches write_tab's default worksheet sizing (see cli.py's
# _EDGE_FORMAT_LAST_ROW) -- the range every EdgeRaw column operation uses.
_LAST_ROW = 1000
# Same range `sheet_filters.add_basic_filters` gives EdgeRaw's basic filter
# -- duplicated here rather than imported (sheet_filters/sheet_style both
# import FROM this module, so the reverse import would be circular) but
# derived the same way, never a separate literal.
_FILTER_RANGE = f"A1:{column_letter(len(EDGE_COLUMNS) - 1 + EDGE_DATA_OFFSET)}{_LAST_ROW}"

# Matches sources/tffb_sos.py's own SOURCES registry keys -- one optional
# input per position, same graceful-degradation treatment as games/weather
# (see _try_load_current): a position whose TFFB sync failed or hasn't run
# yet just blanks that position's players in OppPosRank, not the column.
_SOS_SOURCE_BY_POSITION = {"QB": "sos_qb", "RB": "sos_rb", "WR": "sos_wr", "TE": "sos_te", "DST": "sos_dst"}


def _canonical_id(raw: object) -> str:
    """Normalizes one raw (UNFORMATTED_VALUE) Id cell reading to a stable
    string key. Google returns a purely-numeric-looking cell as an actual
    JSON number once it's been written, and a whole-number float round-
    trips through Python with a stray `.0` -- stripped here so
    `pre_upload`'s and `post_upload`'s reads of the SAME Id always
    produce the SAME key, even though they read it from two different
    physical columns (Id's position before vs. after this sync's own
    `write_tab` rewrite -- see `pre_upload`'s own docstring for why that
    matters) that may carry different inherited cell formats."""
    if raw is None or raw == "":
        return ""
    if isinstance(raw, float):
        return str(int(raw)) if raw.is_integer() else str(raw)
    return str(raw).strip()


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
    """ImpliedMove/TotMove/SpdMove: since the start of the current NFL week, not since the
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


def _report_agg_pts_joins(agg_pts_joins: dict) -> None:
    """Part C, C1's own "never fail silently" rule: print the match rate
    per source per position over the rosterable pool on every sync, and
    write each source's unmatched rosterable players to a file -- a
    missed third-string TE is noise, a missed starter is a bug, and
    nobody notices a quiet 80% match rate unless it's printed every time.
    A no-op when neither source synced this run (`agg_pts_joins` empty)."""
    if not agg_pts_joins:
        return
    report = match_rate_report(agg_pts_joins)
    if not report.empty:
        log.info("Part C match rates (rosterable pool):\n%s", report.to_string(index=False))
    for source, result in agg_pts_joins.items():
        if result.unmatched_pool_names:
            log.warning(
                "%s: %d rosterable pool player(s) with no match: %s",
                source,
                len(result.unmatched_pool_names),
                ", ".join(result.unmatched_pool_names),
            )
        pd.DataFrame({"Name": result.unmatched_pool_names}).to_csv(
            CURRENT_DIR / f"unmatched_{source}.csv", index=False
        )


class EdgeSource(Source):
    name = "edge"

    def fetch(self, ctx: SyncContext) -> pd.DataFrame:
        projections = store.load_current("projections")
        salaries = store.load_current("draftkings")
        games = _try_load_current("nflverse_games")
        weather = _try_load_current("weather")
        line_movement = _try_diff_odds(ctx)
        sos_by_position = {
            position: df
            for position, source_name in _SOS_SOURCE_BY_POSITION.items()
            if (df := _try_load_current(source_name)) is not None
        }
        sleeper = _try_load_current("sleeper")
        fantasypros = _try_load_current("fantasypros")

        result = build_edge_frame(
            projections,
            salaries,
            games=games,
            weather=weather,
            line_movement=line_movement,
            sos_by_position=sos_by_position,
            sleeper=sleeper,
            fantasypros=fantasypros,
        )
        if result.unmatched_names:
            log.warning(
                "%d projected player(s) had no DraftKings salary match on the current "
                "main slate (e.g. a Thursday/Monday-only player TFFB still projects): %s",
                len(result.unmatched_names),
                ", ".join(result.unmatched_names),
            )
        _report_agg_pts_joins(result.agg_pts_joins)
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
        docstring, and EdgeRaw is sorted by ValAdj so row order shifts
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
        exactly this was found on the template's own EdgeRaw.

        Deliberately does NOT catch a broad `Exception` around the reads
        below (a prior version did, "so a malformed/missing prior tab
        can't block a sync") -- that also silently swallowed a real
        Sheets API failure (a rate limit, mid-request network blip, etc.)
        as if the tab were merely empty, which on a real sync silently
        WIPED every Pool tick with no error and no warning (found live,
        2026-09-16: a `dfs setup link-edge --force` immediately followed
        by a second `dfs sync --only edge` lost two real ticks this way).
        The two legitimate "nothing to preserve yet" cases -- tab doesn't
        exist, or exists but has no `Id` column yet -- are both already
        handled explicitly above/below with their own early return; a
        real exception past those two checks means something is actually
        wrong and should fail the sync loudly, the same "raise, don't
        return partial data" contract this module's own docstring
        already claims for itself.

        Reads Id via `read_range_unformatted`, not `read_range` -- found
        live the SAME day, a second, independent way this exact join
        silently loses ticks: a plain formatted read bakes in whatever
        number format Id's PHYSICAL column happens to carry, which can
        turn a clean numeric Id into `"+44132966.0"` if that column
        inherited a stale signed/decimal format left over from a
        different field that used to sit at the same physical position
        before an EdgeRaw reorder (`dfs sync` rewrites values, never
        formatting). `post_upload` reads Id from a DIFFERENT physical
        column (its position AFTER this sync's reorder), which may or may
        not carry the same stale format -- when it doesn't, the two reads
        of the "same" Id mangle differently and the join silently misses,
        exactly what happened live re-syncing right after adding
        `OppPosRank` to `EDGE_COLUMNS`. `_canonical_id` normalizes both
        sides so this can't recur regardless of either column's format."""
        if not client.tab_exists(tab):
            return {}
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
        ids = client.read_range_unformatted(tab, f"{id_col}2:{id_col}{_LAST_ROW}")
        ticks = client.read_range(tab, f"{POOL_COLUMN}2:{POOL_COLUMN}{_LAST_ROW}")
        preserved: dict[str, str] = {}
        for i, id_row in enumerate(ids):
            player_id = _canonical_id(id_row[0]) if id_row else ""
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
        CONTRIBUTING.md's changelog.

        Reads Id via `read_range_unformatted` + `_canonical_id`, same as
        `pre_upload` -- see that method's own docstring for why a plain
        formatted read of Id is exactly what let a real column reorder
        silently break this join (twice, same day)."""
        preserved = preserved or {}
        last_row = len(df) + 1
        if last_row >= 2:
            client.set_basic_filter(tab, _FILTER_RANGE)
            client.set_dropdown_validation(tab, f"{POOL_COLUMN}2:{POOL_COLUMN}{last_row}", POOL_TYPE_OPTIONS)
        if not preserved:
            return
        ids = client.read_range_unformatted(tab, f"{_ID_COLUMN}2:{_ID_COLUMN}{last_row}")
        restore = [[preserved.get(_canonical_id(row[0]), "")] if row else [""] for row in ids]
        if restore:
            client.update_range(tab, f"{POOL_COLUMN}2:{POOL_COLUMN}{last_row}", restore)
