"""Read-only view tabs built on top of what the sheet already holds:
Board, Slate Grid, Exposure and Movement.

Every tab here is additive and derived. They read EdgeRaw / GamesRaw /
WeatherRaw / Lineups through formulas and are written to by nothing --
not by `dfs sync`, not by `link-edge`, not by `weekly_reset`. No existing
cell, column, row or tab is touched when these are created, so none of the
hardcoded positions elsewhere in the codebase can be invalidated by them.

Like `sheet_style.py`, EdgeRaw column references are derived from
`derived.EDGE_COLUMNS` rather than written as literal letters, so a change
to that list moves these formulas with it instead of silently pointing them
at the wrong column.

Re-runnable: each builder overwrites its own tab. Two pieces of typed
user input across all four are read back and restored before the
rewrite, so re-running never costs them: Exposure's Target column, and
`LINEUP_COUNT_CELL` below.
"""

from __future__ import annotations

import pandas as pd

from dfs.derived import EDGE_COLUMNS, EDGE_DATA_OFFSET, SHOOTOUT_TOTAL_THRESHOLD
from dfs.gps_check import GPS_IMPLIED_MISMATCH_PTS
from dfs.sheet_columns import PLAYER_POOL_COLUMN_ORDER
from dfs.sheet_lineup_keys import LINEUP_KEY_HEADER
from dfs.sheets import SheetsClient, column_letter
from dfs.sources.edge import POOL_COLUMN, _canonical_id
from dfs.sources.nflverse_games import GAMES_COLUMNS
from dfs.sources.nflverse_pbp import TEAM_METRIC_COLUMNS
from dfs.sources.tffb_gps import GPS_COLUMNS
from dfs.sources.weather import WEATHER_COLUMNS
from dfs.weekly_reset import PLAYER_POOL_NAME_BLOCKS

# PROMPT_BOARD_FIXES.md item 1: GamesRaw's own column order, for anything
# that needs a GamesRaw letter without hardcoding one -- `GAMES_COLUMNS`
# is keyed by nflverse's raw field name; this is keyed by the tab's own
# header text (what every caller here actually has in hand), in the same
# order.
_GAMES_HEADER = list(GAMES_COLUMNS.values())


def _games_col(name: str) -> str:
    if name not in _GAMES_HEADER:
        raise KeyError(f"{name!r} is not in GAMES_COLUMNS -- cannot build a view referencing it.")
    return column_letter(_GAMES_HEADER.index(name))


BOARD_TAB = "Board"
SLATE_TAB = "Slate Grid"
EXPOSURE_TAB = "Exposure"
MOVEMENT_TAB = "Movement"

# How far down the source tabs the formulas look. EdgeRaw runs ~743 rows.
_EXPOSURE_ROWS = 180

# Phase 5A (originally) / Phase 5-removal (relocated here, 2026-09-16):
# "how many lineups are you building this week" -- Exposure's own
# divisor, typed by Sam, defaulting to 6. Originally lived on `Lineups!H1`
# (the one free cell in the pool deck's row-1 control strip); moved here
# when the deck was removed entirely -- it was never really about the
# deck, just parked in its row 1 for lack of anywhere better, and
# Exposure is the one tab that actually reads it. H1 is free on Exposure
# too (row 1 is all column headers -- A "Name" through G "vs Target", I
# "Slots filled" -- H sits between G and I with nothing of its own).
LINEUP_COUNT_CELL = "H1"
DEFAULT_LINEUP_COUNT = 6


def _q(tab: str) -> str:
    """Quote a tab name for use in a formula if it needs it."""
    return f"'{tab}'" if (" " in tab or "-" in tab) else tab


def _col(name: str) -> str | None:
    if name not in EDGE_COLUMNS:
        return None
    return column_letter(EDGE_COLUMNS.index(name) + EDGE_DATA_OFFSET)


def _rng(edge_tab: str, name: str) -> str:
    """`EdgeRaw!$M$2:$M` for a named EdgeRaw column."""
    letter = _col(name)
    if letter is None:
        raise KeyError(f"{name!r} is not in EDGE_COLUMNS -- cannot build a view referencing it.")
    return f"{_q(edge_tab)}!${letter}$2:${letter}"


def _gps_mismatch_formula(away_implied: str, home_implied: str, *, total_ref: str, spread_ref: str) -> str:
    """TRUE when either team's GPS worksheet implied total is more than
    `GPS_IMPLIED_MISMATCH_PTS` off its Vegas implied total, blank when GPS
    (or the line) is missing -- the sheet-side twin of
    `gps_check.find_gps_mismatches`. `spread_ref` is the SIGNED spread from the
    home team's perspective (positive = home favoured), so Vegas implied is
    (total - spread)/2 for the away team and (total + spread)/2 for home."""
    t = GPS_IMPLIED_MISMATCH_PTS
    return (
        f'IF(OR({away_implied}="",{home_implied}="",{total_ref}="",{spread_ref}=""),"",'
        f"OR(ABS({away_implied}-({total_ref}-{spread_ref})/2)>{t},"
        f"ABS({home_implied}-({total_ref}+{spread_ref})/2)>{t}))"
    )


def _edge_team_pair_mean(edge_tab: str, metric: str, away_ref: str, home_ref: str) -> str:
    """Mean of both teams' EdgeRaw value for `metric` (Pace/PROE/Expl%/GameEnv are per-team columns
    there, so both sides are looked up and averaged -- "combined per game"). The column letter and
    VLOOKUP index are derived from EDGE_COLUMNS, never typed. One helper for the Board's Slate
    shape and Slate Grid's game rows, so the two can never disagree."""
    e = _q(edge_tab)
    team_col = column_letter(EDGE_COLUMNS.index("Team") + EDGE_DATA_OFFSET)
    metric_col = column_letter(EDGE_COLUMNS.index(metric) + EDGE_DATA_OFFSET)
    idx = EDGE_COLUMNS.index(metric) - EDGE_COLUMNS.index("Team") + 1
    return (
        f"IFERROR(AVERAGE("
        f"VLOOKUP({away_ref},{e}!${team_col}:${metric_col},{idx},FALSE),"
        f'VLOOKUP({home_ref},{e}!${team_col}:${metric_col},{idx},FALSE)),"")'
    )


# ---------------------------------------------------------------------------
# Board (Phase 6, Part 3 + 7.6 rebuild)
# ---------------------------------------------------------------------------

# One shared source of truth for the Board's row layout -- both
# `build_board` (what gets written) and `sheet_style.style_board` (how
# it's grouped/coloured) import these, so the two can't silently drift
# the way EdgeRaw's own column order once did (CONTRIBUTING.md's Phase 8
# postmortem). Every section is: one always-visible header row, one
# column-header row, then a collapsible body -- each row number computed
# from the one before it, never re-counted by hand.
BOARD_TITLE_ROW = 1
BOARD_BANNER_ROW = 2
BOARD_FRESHNESS_ROW = 3

BOARD_QUEUE_HEADER_ROW = 5
BOARD_QUEUE_COLHEADER_ROW = BOARD_QUEUE_HEADER_ROW + 1
BOARD_QUEUE_FIRST_ROW = BOARD_QUEUE_COLHEADER_ROW + 1
BOARD_QUEUE_ROWS = 20
BOARD_QUEUE_LAST_ROW = BOARD_QUEUE_FIRST_ROW + BOARD_QUEUE_ROWS - 1
# Checked against the live column-header row before trusting whatever
# sits below it as real Queue data to preserve (see build_board's own
# docstring) -- a sheet still on the pre-rebuild 3-panel Board has
# TOP LEVERAGE's own data occupying these exact rows by coincidence,
# and reading that forward into the new Queue section on the very first
# post-rebuild `build-views` run produced garbage (found live,
# 2026-09-22, against the template).
BOARD_QUEUE_COLHEADER = ["Player", "Pos", "Team", "What changed"]

BOARD_SLATE_HEADER_ROW = BOARD_QUEUE_LAST_ROW + 2
BOARD_SLATE_COLHEADER_ROW = BOARD_SLATE_HEADER_ROW + 1
BOARD_SLATE_FIRST_ROW = BOARD_SLATE_COLHEADER_ROW + 1
BOARD_SLATE_ROWS = 16
BOARD_SLATE_LAST_ROW = BOARD_SLATE_FIRST_ROW + BOARD_SLATE_ROWS - 1
# PROMPT_BOARD_FIXES.md item 1: `Fav`/`Spread` inserted right after
# `Total` -- two columns, not one "KC -3.5" text cell, since text can't be
# colour-scaled (item 6 scales `Spread`). `Pace` (Part C, C7) stays where
# it was relative to Wind/Shootout, just shifted two further right.
# Round 5 item 5b: `PROE`/`Expl%`/`GameEnv` join `Pace` (each the mean of both
# teams' values, the same "combined per game" shape Pace already had) and item
# 5c dropped `Tot Δ` (GPS's "Implied Total" is Vegas, not a model -- see
# `gps_check.py`). Total stays the sort key.
BOARD_SLATE_COLHEADER = [
    "Matchup",
    "Total",
    "Fav",
    "Spread",
    "Pace",
    "PROE",
    "Expl%",
    "GameEnv",
    "Wind",
    "Shootout?",
    "GPS",
]

BOARD_LEADERS_HEADER_ROW = BOARD_SLATE_LAST_ROW + 2
# PROMPT_BOARD_FIXES.md item 2: a sub-label row naming each block's own
# sort -- nothing on the sheet said so before.
BOARD_LEADERS_SUBLABEL_ROW = BOARD_LEADERS_HEADER_ROW + 1
BOARD_LEADERS_COLHEADER_ROW = BOARD_LEADERS_SUBLABEL_ROW + 1
BOARD_LEADERS_FIRST_ROW = BOARD_LEADERS_COLHEADER_ROW + 1
_POSITIONS = ("QB", "RB", "WR", "TE", "DST")
# PROMPT_BOARD_FIXES.md item 3: replaces the old flat `_LEADERS_ROWS_PER_
# POSITION`/`_PUNT_ROWS_PER_POSITION` ints (both were 2/1) -- every
# section is collapsible now, so length is cheap, and each position gets
# sized to its own real depth (QB is shallow, WR is deep). Both the
# leaders blocks AND punt finder share this same distribution -- Sam's
# own table gives them identical counts.
BOARD_ROWS_PER_POSITION = {"QB": 5, "RB": 8, "WR": 10, "TE": 5, "DST": 5}
BOARD_LEADERS_COLHEADER = ["Player", "Pos", "Salary", "ValAdj", "", "Player", "Pos", "Salary", "ProjPts"]
BOARD_LEADERS_ROWS = sum(BOARD_ROWS_PER_POSITION.values())
BOARD_LEADERS_LAST_ROW = BOARD_LEADERS_FIRST_ROW + BOARD_LEADERS_ROWS - 1


def _position_block_rows(first_row: int) -> dict[str, tuple[int, int]]:
    """Each position's own `(start_row, end_row)` within a stacked
    per-position block beginning at `first_row` (Leaders or Punt finder,
    both use `BOARD_ROWS_PER_POSITION`'s counts in `_POSITIONS` order) --
    shared by `build_board` (writing the stacked array) and `style_board`
    (the top-border/colour-scale ranges item 3/6 need), so the two can't
    silently drift apart the way EdgeRaw's own column order once did."""
    rows: dict[str, tuple[int, int]] = {}
    r = first_row
    for p in _POSITIONS:
        n = BOARD_ROWS_PER_POSITION[p]
        rows[p] = (r, r + n - 1)
        r += n
    return rows


BOARD_PUNT_HEADER_ROW = BOARD_LEADERS_LAST_ROW + 2
BOARD_PUNT_COLHEADER_ROW = BOARD_PUNT_HEADER_ROW + 1
BOARD_PUNT_FIRST_ROW = BOARD_PUNT_COLHEADER_ROW + 1
BOARD_PUNT_COLHEADER = ["Player", "Pos", "Salary", "ValAdj"]
BOARD_PUNT_ROWS = sum(BOARD_ROWS_PER_POSITION.values())
BOARD_PUNT_LAST_ROW = BOARD_PUNT_FIRST_ROW + BOARD_PUNT_ROWS - 1
# PROMPT_BOARD_FIXES.md item 4: the old `PUNT_SALARY_CEILING` (a flat
# $4,000) is DraftKings' own FLOOR for QB/RB, so "under $4,000" could
# never match either position. Replaced with a window measured off each
# position's own cheapest salary ON THIS SLATE (a live MINIFS in the
# formula, never a hardcoded floor per position).
PUNT_SALARY_WINDOW = 1000

BOARD_STACK_HEADER_ROW = BOARD_PUNT_LAST_ROW + 2
BOARD_STACK_COLHEADER_ROW = BOARD_STACK_HEADER_ROW + 1
BOARD_STACK_FIRST_ROW = BOARD_STACK_COLHEADER_ROW + 1
# PROMPT_BOARD_FIXES.md item 3: "at least 8 games (16 teams); all games if
# the slate is smaller." A smaller slate leaves the tail blank (same
# fallback every other section here already uses); a bigger one shows the
# 8 highest-total games, matching this section's own "highest-total games
# first" framing.
_STACK_GAMES = 8
BOARD_STACK_COLHEADER = [
    "Team",
    "Total",
    "QB",
    "Sal",
    "WR1",
    "Sal",
    "WR2",
    "Sal",
    "WR3",
    "Sal",
    "TE1",
    "Sal",
    "RB1",
    "Sal",
]
BOARD_STACK_ROWS = _STACK_GAMES * 2  # two teams per game
BOARD_STACK_LAST_ROW = BOARD_STACK_FIRST_ROW + BOARD_STACK_ROWS - 1

BOARD_POOL_HEADER_ROW = BOARD_STACK_LAST_ROW + 2
BOARD_POOL_NOTICE_ROW = BOARD_POOL_HEADER_ROW + 1
BOARD_POOL_COLHEADER_ROW = BOARD_POOL_NOTICE_ROW + 1
BOARD_POOL_FIRST_ROW = BOARD_POOL_COLHEADER_ROW + 1
BOARD_POOL_POSITION_ROWS = len(_POSITIONS)
BOARD_POOL_SUMMARY_ROW = BOARD_POOL_FIRST_ROW + BOARD_POOL_POSITION_ROWS
BOARD_POOL_LAST_ROW = BOARD_POOL_SUMMARY_ROW
BOARD_POOL_COLHEADER = [
    "Pos",
    "Min Sal",
    "Max Sal",
    "Avg Sal",
    "Cheapest",
    "Cheapest Sal",
    "Chalk#",
    "Leverage#",
    "Gap",
]

BOARD_CHALK_HEADER_ROW = BOARD_POOL_LAST_ROW + 2
BOARD_CHALK_PLACEHOLDER_ROW = BOARD_CHALK_HEADER_ROW + 1
BOARD_LAST_ROW = BOARD_CHALK_PLACEHOLDER_ROW

# PROMPT_BOARD_FIXES.md item 5: the hidden Slate-shape join-key columns
# (GameId, then Part C's Away/Home) must sit past the rightmost column
# ANY section uses -- Stack candidates' own 14 columns (A-N) are now the
# widest, wider than they were when column J was originally past
# everything. Derived from every section's own header width, not a
# second hardcoded literal, so a future width change anywhere just moves
# this automatically.
BOARD_MAX_VISIBLE_COL_INDEX = (
    max(
        len(BOARD_SLATE_COLHEADER),
        len(BOARD_LEADERS_COLHEADER),
        len(BOARD_STACK_COLHEADER),
        len(BOARD_POOL_COLHEADER),
    )
    - 1
)
BOARD_SLATE_GAMEID_COL_INDEX = BOARD_MAX_VISIBLE_COL_INDEX + 1
BOARD_SLATE_AWAY_COL_INDEX = BOARD_SLATE_GAMEID_COL_INDEX + 1
BOARD_SLATE_HOME_COL_INDEX = BOARD_SLATE_GAMEID_COL_INDEX + 2
# Hidden per-row helper: TRUE when this game's GPS worksheet implied totals are
# more than `GPS_IMPLIED_MISMATCH_PTS` off Vegas (a conditional-format rule on
# the visible GPS cell reads it -- CF can't reference GPSRaw directly).
BOARD_SLATE_GPSCHK_COL_INDEX = BOARD_SLATE_GAMEID_COL_INDEX + 3
BOARD_SLATE_GAMEID_COL = column_letter(BOARD_SLATE_GAMEID_COL_INDEX)
BOARD_SLATE_AWAY_COL = column_letter(BOARD_SLATE_AWAY_COL_INDEX)
BOARD_SLATE_HOME_COL = column_letter(BOARD_SLATE_HOME_COL_INDEX)
BOARD_SLATE_GPSCHK_COL = column_letter(BOARD_SLATE_GPSCHK_COL_INDEX)
# Hidden helper (Round 5 item 3): the `ProjPts%ile` of each "Highest projection"
# leader, looked up by name, so the Board's ProjPts column is banded by the SAME
# within-position percentile every other tab uses. Spills down from the first
# Leaders row, aligned with the ProjPts block.
BOARD_LEADERS_PCT_COL_INDEX = BOARD_SLATE_GPSCHK_COL_INDEX + 1
BOARD_LEADERS_PCT_COL = column_letter(BOARD_LEADERS_PCT_COL_INDEX)

# Slate Grid's hidden GPS sanity-check column (see `build_slate_grid`).
SLATE_GPS_CHECK_HEADER = "GPS off Vegas"
# Round 5 follow-up item 1: TRUE when at least one of the game's teams has a player on
# EdgeRaw (i.e. the game is on the DK salary file's slate). Hidden helper; the dimming
# rule on every other Slate Grid cell reads it (a rule can't look at another tab).
SLATE_ON_SLATE_HEADER = "On DK slate"
SLATE_HEADER = [
    "Matchup",
    "Kickoff",
    "Total",
    "Spread",
    "Roof",
    "Wind",
    "Gust",
    "Rest (A/H)",
    "Div",
    "Stadium",
    "Total move",
    "Spread move",
    "GPS",
    # Usage work (2026-10-02): the same combined-game values the Board's Slate shape shows
    # (`_edge_team_pair_mean`: both teams' EdgeRaw value, averaged), so the two tabs agree.
    "GameEnv",
    "Pace",
    "PROE",
    "Expl%",
    SLATE_GPS_CHECK_HEADER,
    SLATE_ON_SLATE_HEADER,
]
SLATE_GPS_CHECK_COL_INDEX = SLATE_HEADER.index(SLATE_GPS_CHECK_HEADER)
SLATE_ON_SLATE_COL_INDEX = SLATE_HEADER.index(SLATE_ON_SLATE_HEADER)
SLATE_ON_SLATE_COL = column_letter(SLATE_ON_SLATE_COL_INDEX)
SLATE_GPS_CHECK_COL = column_letter(SLATE_GPS_CHECK_COL_INDEX)
# One letter per header name, for styling code that must not type a column letter.
SLATE_COL = {name: column_letter(i) for i, name in enumerate(SLATE_HEADER)}

# Slate Grid's game rows (one per GamesRaw row, unsorted), then the TEAMS section below.
SLATE_GAME_FIRST_ROW = 2
SLATE_GAME_ROWS = 18
SLATE_GAME_LAST_ROW = SLATE_GAME_FIRST_ROW + SLATE_GAME_ROWS - 1
# TEAMS: one row per team on the week's schedule, sorted by implied total, highest first. Two
# teams per game, so the slot count is twice the game slots (32 teams in a normal week).
SLATE_TEAMS_HEADER_ROW = SLATE_GAME_LAST_ROW + 2
SLATE_TEAMS_COLHEADER_ROW = SLATE_TEAMS_HEADER_ROW + 1
SLATE_TEAMS_FIRST_ROW = SLATE_TEAMS_COLHEADER_ROW + 1
SLATE_TEAM_ROWS = 2 * SLATE_GAME_ROWS
SLATE_TEAMS_LAST_ROW = SLATE_TEAMS_FIRST_ROW + SLATE_TEAM_ROWS - 1
# Team, Opp and Implied come from ONE sorted spill (columns A:C, in this order); every other
# column looks the team (or, for the "Opp Def" columns, the opponent) up in `TeamMetricsRaw`.
# Header text -> the `TEAM_METRIC_COLUMNS` field it reads, and whose team (own or opponent's).
SLATE_TEAMS_COLHEADER = [
    "Team",
    "Opp",
    "Implied",
    "Pace",
    "PROE",
    "Expl%",
    "Off EPA/play",
    "Off EPA/pass",
    "Off EPA/rush",
    "Opp Def EPA/pass",
    "Opp Def EPA/rush",
]
SLATE_TEAMS_LOOKUPS = {
    "Pace": ("Pace", "own"),
    "PROE": ("PROE", "own"),
    "Expl%": ("Expl%", "own"),
    "Off EPA/play": ("OffEPA/Play", "own"),
    "Off EPA/pass": ("OffEPA/Pass", "own"),
    "Off EPA/rush": ("OffEPA/Rush", "own"),
    "Opp Def EPA/pass": ("DefEPA/Pass", "opp"),
    "Opp Def EPA/rush": ("DefEPA/Rush", "opp"),
}
SLATE_TEAMS_TITLE = (
    "TEAMS  —  every team on the schedule, by implied total (Opp Def = what the opponent's defense allows)"
)


def _pp_col(name: str) -> str:
    if name not in PLAYER_POOL_COLUMN_ORDER:
        raise KeyError(f"{name!r} is not in PLAYER_POOL_COLUMN_ORDER -- cannot build a view referencing it.")
    return column_letter(PLAYER_POOL_COLUMN_ORDER.index(name))


def _pp_rng(pool_tab: str, name: str, start_row: int, end_row: int) -> str:
    """`Player Pool!$D$3:$D$12` for a named Player Pool column, restricted
    to one position's own fixed row block (`weekly_reset.
    PLAYER_POOL_NAME_BLOCKS`) -- Player Pool's header names differ from
    EdgeRaw's own for the native columns (`DK Sal` not `Salary`, `Pts` not
    `ProjPts`), so this is a separate lookup from `_rng`/`_col`, not a
    reuse of them."""
    letter = _pp_col(name)
    return f"{_q(pool_tab)}!${letter}${start_row}:${letter}${end_row}"


def build_board(
    client: SheetsClient,
    *,
    edge_tab: str,
    games_tab: str,
    weather_tab: str,
    gps_tab: str,
    player_pool_tab: str,
) -> str:
    """Phase 6, Part 3 (2026-09-22): rebuilt from three ranked player
    panels (a question Sam already answered the moment he ticked his
    pool -- "it ranks 744 players") into one tab, seven sections, each a
    collapsible row group (all open by default, since
    2026-09-30). See `docs/planning/PROMPT_PHASE6.md` Part 3 and
    7.6 for the spec; row positions come from the `BOARD_*` constants
    above, shared with `sheet_style.style_board`.

    Every EdgeRaw-derived panel is regenerated fresh every call against
    the CURRENT `EDGE_COLUMNS` layout via `_rng`/`_col` (same contract
    the pre-rebuild Board already had, and the same reason it must be
    re-run after any EdgeRaw column reorder -- see CONTRIBUTING.md's
    Phase 3 changelog entry for what happens when it isn't). Player
    Pool-derived panels (Pool diagnostics) are the same idea against
    `sheet_columns.PLAYER_POOL_COLUMN_ORDER` instead, via `_pp_rng`.

    Queue's body (`BOARD_QUEUE_FIRST_ROW..LAST_ROW`) is populated by
    `write_queue_section`, called from `dfs sync --live`/`dfs go`, NOT by
    this function -- a Sheets formula cannot see yesterday's values, only
    a Python diff against the last snapshot can. Read back and restored
    here before the rewrite (same "typed/live input survives a rebuild"
    pattern `sheet_views.py`'s own module docstring already documents for
    Exposure's Target and `LINEUP_COUNT_CELL`), so a routine `dfs setup
    build-views` re-run (e.g. after an EdgeRaw reorder) doesn't wipe
    whatever Queue was showing until the next live sync repopulates it.
    """
    existing_queue = []
    if client.tab_exists(BOARD_TAB):
        colheader_rows = client.read_range(
            BOARD_TAB, f"A{BOARD_QUEUE_COLHEADER_ROW}:D{BOARD_QUEUE_COLHEADER_ROW}"
        )
        colheader = colheader_rows[0] if colheader_rows else []
        if colheader == BOARD_QUEUE_COLHEADER:
            existing_queue = client.read_range(BOARD_TAB, f"A{BOARD_QUEUE_FIRST_ROW}:D{BOARD_QUEUE_LAST_ROW}")

    name = _rng(edge_tab, "Name")
    pos = _rng(edge_tab, "Position")
    team = _rng(edge_tab, "Team")
    salary = _rng(edge_tab, "Salary")
    projpts = _rng(edge_tab, "ProjPts")
    valadj = _rng(edge_tab, "ValAdj")
    avail = _rng(edge_tab, "Avail")
    flag = _rng(edge_tab, "Flags")
    basis = _rng(edge_tab, "OwnStatus")
    overunder = _rng(edge_tab, "OverUnder")
    tmrank = _rng(edge_tab, "TmRank")

    g = _q(games_tab)
    w = _q(weather_tab)
    e = _q(edge_tab)
    gp = _q(gps_tab)

    live = f'{name}<>""'
    not_out = f'NOT(ISNUMBER(SEARCH("OUT",{flag})))'

    # ---- Summary banner (rows 2-3, unchanged from the pre-rebuild Board) --
    # NOT `COUNTA(FILTER(...))` -- verified live (2026-09-22, template
    # sheet, empty GamesRaw): when FILTER finds zero matching rows it
    # returns #N/A, and COUNTA/COUNTIF do NOT propagate that error the
    # way MIN/MAX/AVERAGE/ARRAY_CONSTRAIN do -- they count a single
    # error value as "1 item present," so `IFERROR(COUNTA(FILTER(...)),
    # 0)` never actually reaches its 0 fallback and reads "1" on a
    # genuinely empty GamesRaw. SUMPRODUCT never errors in the first
    # place (it multiplies a boolean array, never touches FILTER), so
    # this is the correct empty-safe row count -- same fix applied below
    # to `pool_empty_notice`/`pool_concentration`, which hit the exact
    # same COUNTA/COUNTIF-on-an-erroring-FILTER trap.
    # PROMPT_BOARD_FIXES.md item 1: every GamesRaw letter below is derived
    # from `GAMES_COLUMNS` (via `_games_col`) rather than hardcoded --
    # `$B`/`$C`/`$M` happened to be Away/Home/Total today, but nothing
    # tied them to those fields; a future GamesRaw column insert would
    # have silently pointed these at the wrong data with no error.
    games_id_col = _games_col("GameId")
    games_away_col = _games_col("Away")
    games_home_col = _games_col("Home")
    games_total_col = _games_col("Total")
    games_spread_col = _games_col("Spread")
    games_id_range = f"{g}!${games_id_col}$2:${games_id_col}$40"
    games_away_range = f"{g}!${games_away_col}$2:${games_away_col}$40"
    games_home_range = f"{g}!${games_home_col}$2:${games_home_col}$40"
    games_total_range = f"{g}!${games_total_col}$2:${games_total_col}$40"
    games_spread_range = f"{g}!${games_spread_col}$2:${games_spread_col}$40"
    games_live = f'{games_id_range}<>""'
    # Round 5 follow-up item 1: a game with no players on EdgeRaw isn't on the DK slate, so
    # it doesn't belong in Slate shape -- and the banner above it describes the same set
    # (Games, Highest total, Max wind), or the header would contradict the table under it.
    # ONE condition, shared by the banner and all four Slate shape spills so they can never
    # disagree about which games count. Slate Grid keeps every game, dimmed.
    slate_live = (
        f"({games_live})*"
        f"((ISNUMBER(MATCH({games_away_range},{team},0))"
        f"+ISNUMBER(MATCH({games_home_range},{team},0)))>0)"
    )

    games = f"=SUMPRODUCT({slate_live}*1)"
    top_total = (
        f'=IFERROR(INDEX(SORT(FILTER({{{games_away_range}&" / "&{games_home_range},{games_total_range}}},'
        f'{slate_live}),2,FALSE),1,1)&"  "&'
        f'TEXT(MAX(FILTER({games_total_range},{slate_live})),"0.0"),"--")'
    )
    # WeatherRaw is keyed on GameId: only the slate's own games count toward the max.
    slate_gameids = f"FILTER({games_id_range},{slate_live})"
    max_wind = (
        f'=IFERROR(MAX(FILTER({w}!$F$2:$F$40,{w}!$A$2:$A$40<>"",'
        f'ISNUMBER(MATCH({w}!$A$2:$A$40,{slate_gameids},0))))&" mph","--")'
    )
    injuries = (
        f'=COUNTIF({avail},"OUT")&" out  /  "&COUNTIF({avail},"IR")&" IR  /  "&COUNTIF({avail},"Q")&" Q"'
    )
    # Fix 6.1 (Week 3 fixes, 2026-09-23): the old text described a ranked
    # leverage panel that Part 7.6's Board rebuild removed entirely
    # ("ranked by ceiling percentile instead" no longer describes
    # anything on this tab). Replaced with Part 7.1's own caveat, present
    # regardless of publish status: TFFB's ownership projection is
    # large-field, Sam plays small-field, so Leverage (wherever it's
    # still shown, e.g. Per-position leaders) is directional at best --
    # see docs/CALCULATIONS.md's "Sort order" note for the same wording.
    freshness_banner = (
        f'=IF(COUNTIF({basis},"unpublished")>0,'
        f'"UNPUBLISHED  —  ownership not out yet, so Leverage is blank. Once it is, '
        f"remember ownership is a large-field projection used in small-field contests "
        f'— treat it as directional.",'
        f'"Leverage is running on real ownership — still a large-field projection used '
        f'in small-field contests, so treat it as directional.")'
    )

    # ---- Section 3: per-position leaders (ValAdj / ProjPts, 7.6) --------
    def _ranked_position_block(position: str, *, sort_metric: str, extra: str = "") -> str:
        # Ranked WITHIN position, not across the whole slate -- this is
        # the actual fix for the 11-of-12-QBs bug (a flat sort by a
        # salary ratio isn't comparable across positions). Stacking a
        # FIXED number of rows per position (PROMPT_BOARD_FIXES.md item 3:
        # `BOARD_ROWS_PER_POSITION`, no longer a single flat count), rather
        # than cutting one combined ranking to a total row count,
        # guarantees every position is represented every time instead of
        # whichever one happens to sort first crowding out the rest.
        is_position = f'{pos}="{position}"'
        filters = f"{live},{not_out},{is_position}" + (f",{extra}" if extra else "")
        rows_per_position = BOARD_ROWS_PER_POSITION[position]
        return (
            f"IFERROR(ARRAY_CONSTRAIN(SORT(FILTER("
            f'{{{name},{pos}&" "&{team},{salary},{sort_metric}}},{filters}),4,FALSE),'
            f'{rows_per_position},4),{{"","","",""}})'
        )

    best_valadj = "={" + ";".join(_ranked_position_block(p, sort_metric=valadj) for p in _POSITIONS) + "}"
    highest_proj = "={" + ";".join(_ranked_position_block(p, sort_metric=projpts) for p in _POSITIONS) + "}"

    # ---- Section 4: punt finder ------------------------------------------
    # PROMPT_BOARD_FIXES.md item 4: `PUNT_SALARY_CEILING` (a flat $4,000)
    # was DraftKings' own FLOOR for QB/RB, so "under $4,000" could never
    # match either position -- a punt is now "within `PUNT_SALARY_WINDOW`
    # of this position's own cheapest salary on this slate," computed live
    # via MINIFS (scoped to the position, across the whole slate, not just
    # live/available players -- DK's own price floor doesn't care about
    # injury status).
    def _punt_salary_extra(position: str) -> str:
        floor = f'MINIFS({salary},{pos},"{position}")'
        return f"{salary}<={floor}+{PUNT_SALARY_WINDOW}"

    punt_finder = (
        "={"
        + ";".join(
            _ranked_position_block(p, sort_metric=valadj, extra=_punt_salary_extra(p)) for p in _POSITIONS
        )
        + "}"
    )

    # ---- Section 5: stack candidates (replaces the old leverage panel, 7.6) --
    # Two teams sharing a game share the identical OverUnder value, so
    # sorting individual teams by it is enough to keep them adjacent --
    # no need to join back through GamesRaw for a game grouping.
    # PROMPT_BOARD_FIXES.md item 5: constrained to 2 columns now (not 1) so
    # the same spilling-array trick Slate shape uses fills `Total` (the
    # game's own OverUnder, sorted descending) directly into column B,
    # rather than discarding it after sorting by it.
    team_list = (
        f"=IFERROR(ARRAY_CONSTRAIN(SORT(UNIQUE(FILTER({{{team},{overunder}}},{live})),2,FALSE),"
        f'{BOARD_STACK_ROWS},2),"")'
    )

    def _stack_row_formulas(row: int) -> list[str]:
        # PROMPT_BOARD_FIXES.md item 5: WR2/WR3 (TmRank 2/3) and RB1 (RB,
        # TmRank 1) join WR1/TE1 -- RB1 is display only, no guardrail (Part
        # 7.4's "no QB+RB stack rule" still stands).
        team_cell = f"$A{row}"

        def _slot_filter(position: str, tm_rank: int) -> str:
            return f'({team}={team_cell})*({pos}="{position}")*({tmrank}={tm_rank})'

        slots = [
            _slot_filter("QB", 1),
            _slot_filter("WR", 1),
            _slot_filter("WR", 2),
            _slot_filter("WR", 3),
            _slot_filter("TE", 1),
            _slot_filter("RB", 1),
        ]
        formulas = []
        for slot_filter in slots:
            formulas.append(f'=IFERROR(INDEX(FILTER({name},{slot_filter}),1),"")')
            formulas.append(f'=IFERROR(INDEX(FILTER({salary},{slot_filter}),1),"")')
        return formulas

    # ---- Section 6: pool diagnostics (reads Player Pool, not EdgeRaw) ----
    pp_all_names = (
        "{" + ";".join(_pp_rng(player_pool_tab, "Name", s, e) for s, e in PLAYER_POOL_NAME_BLOCKS) + "}"
    )
    # NOT `COUNTA(pp_all_names)=0` -- verified live (2026-09-22, template
    # sheet, nobody pooled): each of the 5 stacked blocks' own first cell
    # is an `IFERROR(ARRAY_CONSTRAIN(...),"")`-driven formula, and a
    # formula that resolves to the empty STRING "" still counts as
    # present under COUNTA (only a truly untouched cell doesn't) -- so
    # COUNTA saw 5 "non-blank" cells even with an empty pool and the
    # notice never fired. SUMPRODUCT correctly treats a `""` result the
    # same as a genuinely blank cell, since it compares VALUE (`<>""`),
    # not presence.
    pool_empty_notice = (
        f'=IF(SUMPRODUCT(({pp_all_names}<>"")*1)=0,"Tick players into your pool to see diagnostics.","")'
    )

    def _pool_diagnostics_row(position: str, start: int, end: int) -> list[str]:
        pp_name = _pp_rng(player_pool_tab, "Name", start, end)
        pp_salary = _pp_rng(player_pool_tab, "DK Sal", start, end)
        pp_flags = _pp_rng(player_pool_tab, "Flags", start, end)
        pooled = f'{pp_name}<>""'
        cheapest = f"SORT(FILTER({{{pp_name},{pp_salary}}},{pooled}),2,TRUE)"
        # PROMPT_BOARD_FIXES.md item 4: same per-position window as Punt
        # finder (`PUNT_SALARY_WINDOW` off this position's own slate-wide
        # cheapest salary via EdgeRaw's `salary`/`pos`, not a hardcoded
        # $4,000 floor that could never fire for QB/RB).
        punt_floor = f'MINIFS({salary},{pos},"{position}")'
        gap_threshold = f"{punt_floor}+{PUNT_SALARY_WINDOW}"
        return [
            position,
            f'=IFERROR(MIN(FILTER({pp_salary},{pooled})),"")',
            f'=IFERROR(MAX(FILTER({pp_salary},{pooled})),"")',
            f'=IFERROR(AVERAGE(FILTER({pp_salary},{pooled})),"")',
            f'=IFERROR(INDEX({cheapest},1,1),"")',
            f'=IFERROR(INDEX({cheapest},1,2),"")',
            f'=COUNTIF({pp_flags},"*CHALK*")',
            f'=COUNTIF({pp_flags},"*LEVERAGE*")',
            f'=IF(COUNTIF(FILTER({pp_salary},{pooled}),"<="&({gap_threshold}))=0,'
            f'"No {position} within ${PUNT_SALARY_WINDOW:,} of the slate min","")',
        ]

    pp_all_gameids = (
        "{" + ";".join(_pp_rng(player_pool_tab, "GameID", s, e) for s, e in PLAYER_POOL_NAME_BLOCKS) + "}"
    )
    _pp_pooled_gameids = f'FILTER({pp_all_gameids},{pp_all_names}<>"")'
    # NOT a bare `IFERROR(...,"")` around `MAX(COUNTIF(FILTER(...),
    # FILTER(...)))` -- verified live (2026-09-22, template sheet,
    # nobody pooled): FILTER-of-nothing returns #N/A, and COUNTIF (like
    # COUNTA above) does not propagate that error, it counts the single
    # #N/A as "1 matching item" -- so the outer IFERROR never catches
    # anything and this read "Most pooled players sharing one game: 1"
    # with an empty pool. Gated on the same SUMPRODUCT emptiness check as
    # `pool_empty_notice` instead, so the FILTER-of-nothing case is never
    # reached at all.
    pool_concentration = (
        f'=IF(SUMPRODUCT(({pp_all_names}<>"")*1)=0,"",'
        f'"Most pooled players sharing one game: "&'
        f"MAX(COUNTIF({_pp_pooled_gameids},{_pp_pooled_gameids})))"
    )

    # ---- Assemble ---------------------------------------------------------
    rows: list[list[str]] = [[] for _ in range(BOARD_LAST_ROW)]

    def _set(row: int, values: list[str], start_col: int = 0) -> None:
        r = rows[row - 1]
        needed = start_col + len(values)
        if len(r) < needed:
            r.extend([""] * (needed - len(r)))
        for i, v in enumerate(values):
            r[start_col + i] = v

    _set(BOARD_TITLE_ROW, ["THIS WEEK'S BOARD"])
    _set(
        BOARD_BANNER_ROW,
        ["Games", games, "Highest total", top_total, "Max wind", max_wind, "Injuries", injuries],
    )
    _set(BOARD_FRESHNESS_ROW, [freshness_banner])

    _set(BOARD_QUEUE_HEADER_ROW, ["QUEUE  —  changes since the last sync, pooled players only"])
    _set(BOARD_QUEUE_COLHEADER_ROW, BOARD_QUEUE_COLHEADER)
    for i, existing_row in enumerate(existing_queue):
        _set(BOARD_QUEUE_FIRST_ROW + i, list(existing_row))

    _set(BOARD_SLATE_HEADER_ROW, ["SLATE SHAPE  —  where do I want exposure this week"])
    _set(BOARD_SLATE_COLHEADER_ROW, BOARD_SLATE_COLHEADER)
    wind_end_col = column_letter(WEATHER_COLUMNS.index("Wind"))
    wind_idx = WEATHER_COLUMNS.index("Wind") + 1

    # Part C, C7 (2026-09-25): "the Board's Slate shape section ranks games
    # by total 'and pace'; it can now use real pace. Update it" -- read as
    # "surface the newly-available Pace signal," not "change the sort key"
    # (Total stays the sort, unchanged; PROMPT_BOARD_FIXES.md's own item 1
    # keeps building on top of Total too). Each game's own combined Pace
    # (mean of both teams' EdgeRaw Pace, the same "combined pace of both
    # offenses" _game_env_scores already computes) is looked up via two
    # more VLOOKUPs against EdgeRaw, keyed by team code -- Pace is a
    # per-TEAM column there, shared identically by both teams' rows isn't
    # true (each team has its OWN Pace), so both sides are looked up and
    # averaged, unlike Wind below (one game-level value, keyed by GameId).
    def _team_pair_mean(metric: str, away_ref: str, home_ref: str) -> str:
        return _edge_team_pair_mean(edge_tab, metric, away_ref, home_ref)

    # GPS's 1-5 score (and the sanity-check inputs) read GPSRaw directly (one
    # row per team, `sources/tffb_gps.py`) -- GPS is a per-GAME score and was
    # never an EdgeRaw column.
    gps_end_col = column_letter(len(GPS_COLUMNS) - 1)
    gps_implied_idx = GPS_COLUMNS.index("ImpliedTotal") + 1
    gps_score_idx = GPS_COLUMNS.index("GPS") + 1
    # PROMPT_BOARD_FIXES.md item 1: `Fav`/`Spread` sourced from the SAME
    # place `Total` already is (GamesRaw, nflverse `spread_line` -- positive
    # means the HOME team is favoured, confirmed against `nflverse_games.py`'s
    # own module docstring) so the three agree. `Fav` is computed
    # elementwise inside the array literal below (a team code, or "PK" for
    # a pick'em); `Spread` itself displays the absolute line (e.g. 3.5),
    # per Sam's own spec -- the sign only decides who's favoured.
    fav_expr = (
        f'IF({games_spread_range}=0,"PK",IF({games_spread_range}>0,{games_home_range},{games_away_range}))'
    )
    abs_spread_expr = f"ABS({games_spread_range})"
    # One spilling SORT (now four columns: Matchup/Total/Fav/Spread), not a
    # per-row passthrough of GamesRaw's own (unsorted) row order -- "games
    # ranked by total" is the actual ask. Three more independent SORTs on
    # the exact same key (Total) fill parallel GameId/Away/Home columns
    # starting at BOARD_SLATE_GAMEID_COL, well past every other section's
    # rightmost visible column so hiding them (style_board) can't hide
    # real content elsewhere -- GameId is the join key for the per-row
    # Wind lookup below (WeatherRaw is keyed on GameId, not the sorted
    # Matchup text); Away/Home are the join keys for the per-row Pace
    # lookup (EdgeRaw is keyed on Team). Verified live (2026-09-22,
    # template) that multiple SORTs on the same key preserve identical
    # relative order for tied values, so all four formulas stay row-aligned.
    # `slate_live` (defined with the banner above) is the one shared filter for all four
    # spills, so Matchup/GameId/Away/Home can never disagree about which rows survive.
    slate_sorted = (
        f"=IFERROR(ARRAY_CONSTRAIN(SORT(FILTER("
        f'{{{games_away_range}&" @ "&{games_home_range},{games_total_range},'
        f"{fav_expr},{abs_spread_expr}}},"
        f'{slate_live}),2,FALSE),{BOARD_SLATE_ROWS},4),"")'
    )
    slate_gameid = (
        f"=IFERROR(ARRAY_CONSTRAIN(SORT(FILTER("
        f"{{{games_id_range},{games_total_range}}},"
        f'{slate_live}),2,FALSE),{BOARD_SLATE_ROWS},1),"")'
    )
    slate_away = (
        f"=IFERROR(ARRAY_CONSTRAIN(SORT(FILTER("
        f"{{{games_away_range},{games_total_range}}},"
        f'{slate_live}),2,FALSE),{BOARD_SLATE_ROWS},1),"")'
    )
    slate_home = (
        f"=IFERROR(ARRAY_CONSTRAIN(SORT(FILTER("
        f"{{{games_home_range},{games_total_range}}},"
        f'{slate_live}),2,FALSE),{BOARD_SLATE_ROWS},1),"")'
    )
    _set(BOARD_SLATE_FIRST_ROW, [slate_sorted])
    _set(BOARD_SLATE_FIRST_ROW, [slate_gameid], start_col=BOARD_SLATE_GAMEID_COL_INDEX)
    _set(BOARD_SLATE_FIRST_ROW, [slate_away], start_col=BOARD_SLATE_AWAY_COL_INDEX)
    _set(BOARD_SLATE_FIRST_ROW, [slate_home], start_col=BOARD_SLATE_HOME_COL_INDEX)
    for i in range(BOARD_SLATE_ROWS):
        r = BOARD_SLATE_FIRST_ROW + i
        guard = f'IF($A{r}="","",'
        away_ref, home_ref = f"${BOARD_SLATE_AWAY_COL}{r}", f"${BOARD_SLATE_HOME_COL}{r}"
        away_implied = f'IFERROR(VLOOKUP({away_ref},{gp}!$A:${gps_end_col},{gps_implied_idx},FALSE),"")'
        home_implied = f'IFERROR(VLOOKUP({home_ref},{gp}!$A:${gps_end_col},{gps_implied_idx},FALSE),"")'
        # The Board shows |spread| (col D) and the favourite (col C), so the
        # signed home-perspective spread `gps_check.py` needs is rebuilt from
        # them: +|spread| when the home team is the favourite, -|spread| when
        # the away team is, 0 for a pick'em.
        home_spread = f"IF($C{r}={home_ref},$D{r},IF($C{r}={away_ref},-$D{r},0))"
        _set(
            r,
            [
                f"={guard}{_team_pair_mean('Pace', away_ref, home_ref)})",
                f"={guard}{_team_pair_mean('PROE', away_ref, home_ref)})",
                f"={guard}{_team_pair_mean('Expl%', away_ref, home_ref)})",
                f"={guard}{_team_pair_mean('GameEnv', away_ref, home_ref)})",
                f"={guard}IFERROR(VLOOKUP(${BOARD_SLATE_GAMEID_COL}{r},{w}!$A:${wind_end_col},"
                f'{wind_idx},FALSE),""))',
                f'={guard}IF($B{r}>={SHOOTOUT_TOTAL_THRESHOLD},"Shootout",""))',
                f'={guard}IFERROR(VLOOKUP({home_ref},{gp}!$A:${gps_end_col},{gps_score_idx},FALSE),""))',
            ],
            start_col=4,
        )
        _set(
            r,
            [
                f"={guard}"
                + _gps_mismatch_formula(
                    away_implied, home_implied, total_ref=f"$B{r}", spread_ref=home_spread
                )
                + ")"
            ],
            start_col=BOARD_SLATE_GPSCHK_COL_INDEX,
        )

    _set(BOARD_LEADERS_HEADER_ROW, ["PER-POSITION LEADERS  —  ranked within position, never across it"])
    # PROMPT_BOARD_FIXES.md item 2: a sub-label row naming each block's own
    # sort -- nothing on the sheet said so before.
    _set(BOARD_LEADERS_SUBLABEL_ROW, ["Best value — sorted by ValAdj, high to low"], start_col=0)
    _set(BOARD_LEADERS_SUBLABEL_ROW, ["Highest projection — sorted by ProjPts, high to low"], start_col=5)
    _set(BOARD_LEADERS_COLHEADER_ROW, BOARD_LEADERS_COLHEADER)
    _set(BOARD_LEADERS_FIRST_ROW, [best_valadj], start_col=0)
    _set(BOARD_LEADERS_FIRST_ROW, [highest_proj], start_col=5)
    # Round 5 item 3: hidden per-row percentile for the ProjPts leaders (names are column F).
    edge_name_col = column_letter(EDGE_COLUMNS.index("Name") + EDGE_DATA_OFFSET)
    edge_pct_col = column_letter(EDGE_COLUMNS.index("ProjPts%ile") + EDGE_DATA_OFFSET)
    pct_idx = EDGE_COLUMNS.index("ProjPts%ile") - EDGE_COLUMNS.index("Name") + 1
    proj_names = f"F{BOARD_LEADERS_FIRST_ROW}:F{BOARD_LEADERS_LAST_ROW}"
    _set(
        BOARD_LEADERS_FIRST_ROW,
        [
            f'=ARRAYFORMULA(IFERROR(VLOOKUP({proj_names},{e}!${edge_name_col}:${edge_pct_col},{pct_idx},FALSE),""))'
        ],
        start_col=BOARD_LEADERS_PCT_COL_INDEX,
    )

    _set(
        BOARD_PUNT_HEADER_ROW,
        [f"PUNT FINDER  —  within ${PUNT_SALARY_WINDOW:,} of each position's minimum, by ValAdj"],
    )
    _set(BOARD_PUNT_COLHEADER_ROW, BOARD_PUNT_COLHEADER)
    _set(BOARD_PUNT_FIRST_ROW, [punt_finder])

    _set(BOARD_STACK_HEADER_ROW, ["STACK CANDIDATES  —  QB + top pass-catchers, highest-total games first"])
    _set(BOARD_STACK_COLHEADER_ROW, BOARD_STACK_COLHEADER)
    _set(BOARD_STACK_FIRST_ROW, [team_list])
    for i in range(BOARD_STACK_ROWS):
        r = BOARD_STACK_FIRST_ROW + i
        _set(r, _stack_row_formulas(r), start_col=2)

    _set(BOARD_POOL_HEADER_ROW, ["POOL DIAGNOSTICS  —  reads your pool, not the slate"])
    _set(BOARD_POOL_NOTICE_ROW, [pool_empty_notice])
    _set(BOARD_POOL_COLHEADER_ROW, BOARD_POOL_COLHEADER)
    for i, (position, (start, end)) in enumerate(zip(_POSITIONS, PLAYER_POOL_NAME_BLOCKS, strict=True)):
        _set(BOARD_POOL_FIRST_ROW + i, _pool_diagnostics_row(position, start, end))
    _set(BOARD_POOL_SUMMARY_ROW, [pool_concentration])

    _set(BOARD_CHALK_HEADER_ROW, ["CHALK MAP  —  deferred"])
    _set(
        BOARD_CHALK_PLACEHOLDER_ROW,
        [
            "Where the field concentrates -- only meaningful once ownership publishes "
            "(TFFB's ProjOwn reads 0 pre-midweek). See docs/planning/PROMPT_DATA.md's Move 2 / "
            "7.8's actual-ownership logging."
        ],
    )

    client.write_tab(BOARD_TAB, rows)
    return (
        f"{BOARD_TAB}: built (Queue, Slate shape, Per-position leaders, Punt finder, "
        "Stack candidates, Pool diagnostics, Chalk map placeholder)"
    )


def write_queue_section(client: SheetsClient, changes: pd.DataFrame, edge_tab: str) -> str:
    """Populates Board's Queue body (`BOARD_QUEUE_FIRST_ROW..LAST_ROW`)
    from `live_diff.diff_queue_changes`' output, filtered to players
    currently ticked into the pool. Called from `dfs sync --live`/`dfs
    go` right after the diff is computed -- NOT from `build_board`, since
    a Sheets formula can't see yesterday's values, only this Python diff
    can. `changes` is expected to carry `Id`/`Name`/`Position`/`Team`/
    `Reason` columns, `diff_queue_changes`'s own output shape.

    The pool tick itself lives only on the live sheet, never in the local
    diff dataframe (`sources/edge.py`'s `fetch()` never includes it --
    see that module's `pre_upload`/`post_upload` docstrings for why), so
    it's read here the same way `pre_upload` reads it: by Id, off
    EdgeRaw's own current header and Pool column, never assumed from
    `EDGE_DATA_OFFSET`'s TARGET layout (this can run between an EdgeRaw
    reorder's `write_tab` and the next `dfs setup polish`, same hazard
    `pre_upload`'s own docstring documents at length).
    """
    if not client.tab_exists(BOARD_TAB):
        return f"{BOARD_TAB}: not present -- skipped"
    if not client.tab_exists(edge_tab):
        return f"{BOARD_TAB}: Queue skipped -- {edge_tab} not present"

    header_rows = client.read_range(edge_tab, "A1:1")
    header = header_rows[0] if header_rows else []
    if "Id" not in header:
        return f"{BOARD_TAB}: Queue skipped -- {edge_tab} has no Id column yet"
    id_col = column_letter(header.index("Id"))
    ids = client.read_range_unformatted(edge_tab, f"{id_col}2:{id_col}1000")
    ticks = client.read_range(edge_tab, f"{POOL_COLUMN}2:{POOL_COLUMN}1000")
    pooled_ids = {
        _canonical_id(ids[i][0])
        for i in range(len(ids))
        if ids[i] and ids[i][0] != "" and i < len(ticks) and ticks[i] and ticks[i][0]
    }

    if changes.empty:
        pooled_changes = changes
    else:
        pooled_changes = changes[changes["Id"].map(_canonical_id).isin(pooled_ids)]

    body = [
        [row["Name"], row["Position"], row["Team"], row["Reason"]]
        for _, row in pooled_changes.head(BOARD_QUEUE_ROWS).iterrows()
    ]
    overflow = len(pooled_changes) - len(body)
    if body and overflow > 0:
        body[-1][3] = f"{body[-1][3]} (+{overflow} more not shown)"
    if not body:
        body = [["No changes since the last sync for pooled players.", "", "", ""]]
    while len(body) < BOARD_QUEUE_ROWS:
        body.append(["", "", "", ""])

    client.update_range(BOARD_TAB, f"A{BOARD_QUEUE_FIRST_ROW}:D{BOARD_QUEUE_LAST_ROW}", body)
    return f"{BOARD_TAB}: Queue updated ({len(pooled_changes)} pooled change(s))"


# ---------------------------------------------------------------------------
# Slate Grid (Direction F)
# ---------------------------------------------------------------------------


def build_slate_grid(
    client: SheetsClient,
    *,
    games_tab: str,
    weather_tab: str,
    edge_tab: str,
    gps_tab: str,
    team_metrics_tab: str,
) -> str:
    """One row per game instead of one row per player.

    Surfaces AwayRest/HomeRest and DivGame, which `nflverse_games` already
    syncs into GamesRaw and which nothing in the sheet currently displays
    anywhere.

    A9 (2026-09-22): the Wind/Gust VLOOKUPs against `weather_tab` used to
    hardcode both the lookup RANGE's end column and the result INDEX (6
    and 7) -- the last surviving instance of the hardcoded-index bug class
    CLAUDE.md's central hazard section warns about (correct only because
    `sources.weather.WEATHER_COLUMNS`' order happens to match today; a
    reorder there would silently pull the wrong field with no error).
    Both are now derived from `WEATHER_COLUMNS` itself.

    A9's second ask, same day: per-game line movement, appended as `Total
    move`/`Spread move` (same header text `style_movement`'s own
    `_MOVEMENT_SCALED_COLUMNS` already uses). Sourced from `edge_tab`'s
    already-computed per-PLAYER `TotMove`/`SpdMove` -- both are actually
    team-level joins (`derived._attach_line_movement`, keyed by `Team`),
    so any one player on a team carries that team's own value; the HOME
    team's row is used for both, consistently, since `SpdMove` is
    directional (a team's own spread moving one way is the opponent's
    moving the other) and `GamesRaw!$L` (`Spread`, this tab's existing
    column) is already reported from the home team's perspective --
    matching that convention rather than picking a side arbitrarily.
    `TotMove` (the game's total) is identical either way. Confirmed with
    Sam (2026-09-22) that "over the week" means since the slate opened,
    not since the last sync -- which this inherits for free: `edge_tab`'s
    own `TotMove`/`SpdMove` already diff against `nfl_calendar.
    week_start_date` (`sources/edge.py`), not the last sync, so nothing
    new needed building here beyond surfacing the existing columns.

    GPS: `GPS` (the 1-5 score) is read per game from `gps_tab` (`sources/
    tffb_gps.py`'s `GPSRaw`, one row per team) via the HOME team's row --
    it is a per-GAME score, never an EdgeRaw column. Round 5 item 5c REMOVED
    `Model Tot`/`Tot Δ`/`Model Spd`/`Spd Δ`: the worksheet's `Implied Total`
    is Vegas, not a model (see `gps_check.py`), so those four columns only
    ever restated the market. The implied totals are kept as a SANITY
    CHECK: a hidden `GPS off Vegas` column is TRUE when either team's
    implied total is more than `GPS_IMPLIED_MISMATCH_PTS` off Vegas, and a
    conditional-format chip on the visible `GPS` cell reads it (a row swap
    in the source means that game's GPS describes the wrong game).
    """
    g, w, e, gp = _q(games_tab), _q(weather_tab), _q(edge_tab), _q(gps_tab)
    wind_end_col = column_letter(WEATHER_COLUMNS.index("Wind"))
    wind_idx = WEATHER_COLUMNS.index("Wind") + 1
    gust_end_col = column_letter(WEATHER_COLUMNS.index("Gust"))
    gust_idx = WEATHER_COLUMNS.index("Gust") + 1
    team_col = column_letter(EDGE_COLUMNS.index("Team") + EDGE_DATA_OFFSET)
    tot_move_end_col = column_letter(EDGE_COLUMNS.index("TotMove") + EDGE_DATA_OFFSET)
    tot_move_idx = EDGE_COLUMNS.index("TotMove") - EDGE_COLUMNS.index("Team") + 1
    spd_move_end_col = column_letter(EDGE_COLUMNS.index("SpdMove") + EDGE_DATA_OFFSET)
    spd_move_idx = EDGE_COLUMNS.index("SpdMove") - EDGE_COLUMNS.index("Team") + 1
    gps_end_col = column_letter(len(GPS_COLUMNS) - 1)
    gps_implied_idx = GPS_COLUMNS.index("ImpliedTotal") + 1
    gps_score_idx = GPS_COLUMNS.index("GPS") + 1
    games_total_col = _games_col("Total")
    games_spread_col = _games_col("Spread")
    rows = [list(SLATE_HEADER)]
    for r in range(SLATE_GAME_FIRST_ROW, SLATE_GAME_LAST_ROW + 1):
        guard = f'IF({g}!$A{r}="","",'
        away_implied = f'IFERROR(VLOOKUP({g}!$B{r},{gp}!$A:${gps_end_col},{gps_implied_idx},FALSE),"")'
        home_implied = f'IFERROR(VLOOKUP({g}!$C{r},{gp}!$A:${gps_end_col},{gps_implied_idx},FALSE),"")'
        rows.append(
            [
                f'={guard}{g}!$B{r}&" @ "&{g}!$C{r})',
                f"={guard}"
                f'IFERROR(TEXT({g}!$D{r},"ddd")&" "&TEXT({g}!$E{r},"h:mm am/pm"),'
                f'{g}!$D{r}&" "&{g}!$E{r}))',
                f"={guard}{g}!${games_total_col}{r})",
                f"={guard}{g}!${games_spread_col}{r})",
                f"={guard}{g}!$G{r})",
                f'={guard}IFERROR(VLOOKUP({g}!$A{r},{w}!$A:${wind_end_col},{wind_idx},FALSE),""))',
                f'={guard}IFERROR(VLOOKUP({g}!$A{r},{w}!$A:${gust_end_col},{gust_idx},FALSE),""))',
                f'={guard}{g}!$I{r}&" / "&{g}!$J{r})',
                f'={guard}IF({g}!$K{r}=1,"DIV",""))',
                f"={guard}{g}!$F{r})",
                f"={guard}IFERROR(VLOOKUP({g}!$C{r},{e}!${team_col}:${tot_move_end_col},"
                f'{tot_move_idx},FALSE),""))',
                f"={guard}IFERROR(VLOOKUP({g}!$C{r},{e}!${team_col}:${spd_move_end_col},"
                f'{spd_move_idx},FALSE),""))',
                f'={guard}IFERROR(VLOOKUP({g}!$C{r},{gp}!$A:${gps_end_col},{gps_score_idx},FALSE),""))',
                # GameEnv / Pace / PROE / Expl%: both teams' values averaged, the same helper the
                # Board's Slate shape uses (`_edge_team_pair_mean`), away team then home team.
                *[
                    f"={guard}{_edge_team_pair_mean(edge_tab, metric, f'{g}!$B{r}', f'{g}!$C{r}')})"
                    for metric in ("GameEnv", "Pace", "PROE", "Expl%")
                ],
                f"={guard}"
                + _gps_mismatch_formula(
                    away_implied,
                    home_implied,
                    total_ref=f"{g}!${games_total_col}{r}",
                    spread_ref=f"{g}!${games_spread_col}{r}",
                )
                + ")",
                # Round 5 follow-up item 1: on the DK slate iff either team has a player.
                f"={guard}(COUNTIF({e}!${team_col}:${team_col},{g}!$B{r})"
                f"+COUNTIF({e}!${team_col}:${team_col},{g}!$C{r}))>0)",
            ]
        )

    # ---- TEAMS (below the games): one row per team on the week's schedule -------------------
    tm = _q(team_metrics_tab)
    tm_end_col = column_letter(len(TEAM_METRIC_COLUMNS) - 1)
    games_id_range = f"{g}!$A$2:$A$40"
    away_rng = f"{g}!${_games_col('Away')}$2:${_games_col('Away')}$40"
    home_rng = f"{g}!${_games_col('Home')}$2:${_games_col('Home')}$40"
    total_rng = f"{g}!${games_total_col}$2:${games_total_col}$40"
    spread_rng = f"{g}!${games_spread_col}$2:${games_spread_col}$40"
    live = f'{games_id_range}<>""'
    # Vegas implied totals, the same convention `_gps_mismatch_formula` documents: spread is
    # signed from the HOME team's view (positive = home favoured), so home = (total + spread)/2
    # and away = (total - spread)/2. Each game contributes two rows (away block stacked on home
    # block), then one SORT by implied total, highest first, spills Team | Opp | Implied.
    team_pairs = (
        f"{{{away_rng},{home_rng},({total_rng}-{spread_rng})/2;"
        f"{home_rng},{away_rng},({total_rng}+{spread_rng})/2}}"
    )
    teams_spill = (
        f"=IFERROR(ARRAY_CONSTRAIN(SORT(FILTER({team_pairs},{{{live};{live}}}),3,FALSE),"
        f'{SLATE_TEAM_ROWS},3),"")'
    )
    while len(rows) < SLATE_TEAMS_HEADER_ROW - 1:  # blank spacer row(s) under the last game row
        rows.append([""] * len(SLATE_HEADER))
    rows.append([SLATE_TEAMS_TITLE])
    rows.append(list(SLATE_TEAMS_COLHEADER))
    on_slate_col = SLATE_ON_SLATE_COL_INDEX
    for i in range(SLATE_TEAM_ROWS):
        r = SLATE_TEAMS_FIRST_ROW + i
        row = [""] * len(SLATE_HEADER)
        if i == 0:
            row[0] = teams_spill  # spills over A:C of every row below
        for header, (field, whose) in SLATE_TEAMS_LOOKUPS.items():
            key = f"$B{r}" if whose == "opp" else f"$A{r}"
            idx = TEAM_METRIC_COLUMNS.index(field) + 1
            row[SLATE_TEAMS_COLHEADER.index(header)] = (
                f'=IF($A{r}="","",IFERROR(VLOOKUP({key},{tm}!$A:${tm_end_col},{idx},FALSE),""))'
            )
        # On the DK slate iff the team has a player on EdgeRaw (hidden helper the dimming rule reads).
        row[on_slate_col] = f'=IF($A{r}="","",COUNTIF({e}!${team_col}:${team_col},$A{r})>0)'
        rows.append(row)

    client.write_tab(SLATE_TAB, rows)
    return (
        f"{SLATE_TAB}: built ({SLATE_GAME_ROWS} game rows off GamesRaw + WeatherRaw + EdgeRaw + GPSRaw, "
        f"and a TEAMS section off GamesRaw + {team_metrics_tab})"
    )


# ---------------------------------------------------------------------------
# Exposure (Direction E)
# ---------------------------------------------------------------------------


def build_exposure(
    client: SheetsClient,
    *,
    edge_tab: str,
    lineups_tab: str,
    lineup_count: int,
    lineups_header_row: int = 1,
) -> str:
    """Fills the Exposure tab, which has been a documented feature and a
    single empty cell since the sheet was built.

    Counts each player across the whole of Lineups column A, which holds
    only typed names plus the repeated "Name" header -- a literal string
    that can never collide with a real player name, so this works
    regardless of how the twenty blocks are laid out or move.

    Two typed inputs survive a rebuild: Target (column F, read back and
    restored before the rewrite) and `LINEUP_COUNT_CELL` (`H1`, read back
    and re-placed into the new row 1 below).

    Exposure's divisor is `LINEUP_COUNT_CELL` ("how many lineups this
    week," Sam's own typed number, defaulting to `DEFAULT_LINEUP_COUNT`),
    not `lineup_count` (the sheet's fixed capacity,
    `len(LINEUPS_NAME_BLOCKS)`). Dividing by capacity instead of actual
    usage was a live bug -- a player rostered in every one of a 6-lineup
    build read as 30% (6/20) instead of 100%. `lineup_count` is kept as
    the divisor's fallback (a blank or zero `H1` must never produce
    `#DIV/0!` or silently divide by 1).

    Part 7.5's portfolio-level headline: distinct QBs used and distinct
    games represented, across the WHOLE build (not per-lineup, which is
    Part 7.5's other half, `sheet_lineup_metrics.py`), plus a plain
    Yes/No flag when two lineups share a QB. Found by reading `Lineups`'
    OWN header (`lineups_header_row`) for its real `Pos.`/`GameID`
    column letters -- never assumed to be EdgeRaw's own letters, since
    the two tabs' column orders differ.
    """
    name = _rng(edge_tab, "Name")
    pos = _rng(edge_tab, "Position")
    salary = _rng(edge_tab, "Salary")
    lu_typed = f"{_q(lineups_tab)}!$A$1:$A"

    lineups_header = client.read_range(lineups_tab, f"A{lineups_header_row}:{lineups_header_row}")
    lineups_header = lineups_header[0] if lineups_header else []
    # Round 5 follow-up item 3: every "is this the same player?" count reads the hidden
    # `Player Key` (DK's canonical name for whatever was typed), so two spellings of one
    # player are one player. Falls back to the typed column when the key column doesn't
    # exist yet. Emptiness tests below stay on the typed column, which is genuinely blank.
    lu = (
        f"{_q(lineups_tab)}!${column_letter(lineups_header.index(LINEUP_KEY_HEADER))}$1:"
        f"${column_letter(lineups_header.index(LINEUP_KEY_HEADER))}"
        if LINEUP_KEY_HEADER in lineups_header
        else lu_typed
    )
    portfolio_cols: dict[str, str] = {}
    if lineups_header:
        for col_name in ("Pos.", "GameID"):
            if col_name in lineups_header:
                portfolio_cols[col_name] = column_letter(lineups_header.index(col_name))

    usage_cell = f"${LINEUP_COUNT_CELL[0]}${LINEUP_COUNT_CELL[1:]}"
    divisor = f"IF(N({usage_cell})>0,N({usage_cell}),{lineup_count})"

    # Preserve any targets already typed, keyed by player name, and the
    # typed lineup count -- both read back before the rewrite below.
    existing: dict[str, str] = {}
    lineup_count_value: str = str(DEFAULT_LINEUP_COUNT)
    if client.tab_exists(EXPOSURE_TAB):
        try:
            current = client.read_range(EXPOSURE_TAB, f"A2:F{_EXPOSURE_ROWS}")
            for row in current:
                if len(row) >= 6 and row[0] and row[5]:
                    existing[row[0].strip()] = row[5]
        except Exception:  # noqa: BLE001 - a malformed old tab must not block a rebuild
            existing = {}
        try:
            current_count = client.read_range(EXPOSURE_TAB, LINEUP_COUNT_CELL)
            if current_count and current_count[0] and current_count[0][0].strip():
                lineup_count_value = current_count[0][0]
        except Exception:  # noqa: BLE001 - a malformed old tab must not block a rebuild
            pass

    roster = f'=IFERROR(SORT(FILTER({{{name},{pos},{salary}}},{name}<>"",COUNTIF({lu},{name})>0),3,FALSE),"")'

    # Part 7.5: portfolio headline, K1:P1 -- blank (not a broken formula)
    # if Lineups hasn't been through `dfs setup reorder-columns` yet and
    # doesn't have Pos./GameID linked.
    if "Pos." in portfolio_cols and "GameID" in portfolio_cols:
        lu_pos = f"{_q(lineups_tab)}!${portfolio_cols['Pos.']}$1:${portfolio_cols['Pos.']}"
        lu_gameid = f"{_q(lineups_tab)}!${portfolio_cols['GameID']}$1:${portfolio_cols['GameID']}"
        # `key<>""` because the key column holds formula-blanks, which COUNTA would count;
        # ROWS (not COUNTA) so a FILTER of nothing degrades to 0 instead of counting #N/A.
        distinct_qb_count = f'IFERROR(ROWS(UNIQUE(FILTER({lu},{lu_pos}="QB",{lu}<>""))),0)'
        distinct_qbs = f"={distinct_qb_count}"
        # Found live (2026-09-19), same static-label pitfall as Lineups'
        # stale "DEF" bug: `Pos.` is the FIXED slot label, always "QB" for
        # one row per block regardless of whether a name is typed there,
        # so `COUNTIF(lu_pos,"QB")` was always 20 (the block count), never
        # "how many QB slots are actually filled." That made `shared_qb`
        # read "Yes" even with zero real QBs rostered anywhere (20 > 0).
        # `lu,"<>"` requires the Name cell itself (typed by hand, so
        # genuinely blank when empty -- not a formula-blank like GameID)
        # to be non-blank too.
        qb_slots_filled = f'COUNTIFS({lu_pos},"QB",{lu_typed},"<>")'
        shared_qb = f'=IF({qb_slots_filled}>{distinct_qb_count},"Yes","No")'
        # Same header-repeat-text gotcha "Slots filled" (above) already
        # guards against: `lu_gameid` spans every block including each
        # one's own repeated header row, whose GameID cell reads the
        # literal text "GameID" -- a real, non-blank string that passed
        # the old `<>""` filter and always counted as one phantom
        # "distinct game" even with zero real lineups built. Found live
        # (2026-09-19) right after fixing that: with the header text
        # correctly excluded, zero real games in progress makes FILTER's
        # own result set genuinely empty, which FILTER errors on (`#N/A`)
        # rather than returning nothing -- and plain `IFERROR(COUNTA(...),
        # 0)` does NOT catch it, since `COUNTA` absorbs the error into a
        # valid count of 1 (an error value still "counts" as present)
        # *before* IFERROR ever sees an error to catch -- confirmed this
        # was ALSO silently wrong in the already-shipped per-lineup
        # version of this exact idiom (`sheet_lineup_metrics.
        # distinct_games_formula`), fixed there too. `ROWS` does not
        # absorb the error -- it propagates it, so `IFERROR(ROWS(...),0)`
        # genuinely degrades to 0.
        distinct_games = (
            f'=IFERROR(ROWS(UNIQUE(FILTER({lu_gameid},{lu_gameid}<>"",{lu_gameid}<>"GameID"))),0)'
        )
    else:
        distinct_qbs = shared_qb = distinct_games = ""

    rows = [
        [
            "Name",
            "Pos",
            "Salary",
            "# Lineups",
            "Exposure",
            "Target",
            "vs Target",
            lineup_count_value,
            "Slots filled",
            f'=COUNTIF({lu_typed},"?*")-COUNTIF({lu_typed},"Name")',
            "Distinct QBs",
            distinct_qbs,
            "Shared QB?",
            shared_qb,
            "Distinct games",
            distinct_games,
        ],
        [roster, "", "", f'=IF($A2="","",COUNTIF({lu},$A2))', f'=IF($A2="","",D2/({divisor}))', "", ""],
    ]
    for r in range(3, _EXPOSURE_ROWS + 1):
        rows.append(
            [
                "",
                "",
                "",
                f'=IF($A{r}="","",COUNTIF({lu},$A{r}))',
                f'=IF($A{r}="","",D{r}/({divisor}))',
                "",
                "",
            ]
        )

    # Re-apply preserved targets and the vs-Target formula.
    for i, row in enumerate(rows[1:], start=2):
        row[6] = f'=IF(OR($A{i}="",$F{i}=""),"",E{i}-F{i})'
    client.write_tab(EXPOSURE_TAB, rows)

    if existing:
        names = client.read_range(EXPOSURE_TAB, f"A2:A{_EXPOSURE_ROWS}")
        restored = [[existing.get((r[0] if r else "").strip(), "")] for r in names]
        while len(restored) < _EXPOSURE_ROWS - 1:
            restored.append([""])
        client.update_range(EXPOSURE_TAB, f"F2:F{_EXPOSURE_ROWS}", restored)

    # H1 has no room for a separate label cell in this header row -- a
    # note stands in for one, same reasoning as the deck's old H1 had.
    # Kept non-strict (warn, not reject): the divisor above already
    # clamps a blank/zero/out-of-range H1 to full capacity rather than
    # dividing by it, so a temporarily "wrong" H1 degrades to a safe
    # number, not a broken one.
    client.set_note(
        EXPOSURE_TAB,
        LINEUP_COUNT_CELL,
        "How many lineups are you building this week? Exposure divides by this, not by capacity.",
    )
    client.set_number_range_validation(EXPOSURE_TAB, LINEUP_COUNT_CELL, minimum=1, maximum=lineup_count)

    note = f", {len(existing)} target(s) preserved" if existing else ""
    portfolio_note = (
        ", portfolio headline (Distinct QBs/Shared QB?/Distinct games)"
        if distinct_qbs
        else ", portfolio headline skipped -- Lineups' Pos./GameID not linked yet"
    )
    return (
        f"{EXPOSURE_TAB}: built off {lineups_tab} "
        f"(divisor: {EXPOSURE_TAB}!{LINEUP_COUNT_CELL}, falling back to {lineup_count}-lineup capacity)"
        f"{note}{portfolio_note}"
    )


# ---------------------------------------------------------------------------
# Movement (Direction H)
# ---------------------------------------------------------------------------


def build_movement(client: SheetsClient, *, edge_tab: str) -> str:
    """The hour before lock: players ranked by how far their team's implied
    total has moved since the start of the NFL week, with kickoff alongside.

    Sorted/filtered on ImpliedMove alone (team implied points move --
    "LineMove" before Fix 2.2, and the one `derived._flag_for_row`'s
    IMPL↑/IMPL↓ actually keys off) -- TotMove/SpdMove ride along as extra
    DISPLAY columns once a row already qualifies, not a second way to
    qualify one, so "biggest movers" keeps one unambiguous meaning. This
    is a VIEW, so its headers are prose ("Implied move"/"Total move"/
    "Spread move") rather than the EDGE_COLUMNS contract names Section F
    renamed -- a reader here shouldn't need to know that EdgeRaw's own
    header spells it `ImpliedMove`.

    ImpliedMove is blank until at least one `nfl_odds` sync has happened
    this week, so an unsynced sheet says so rather than showing a page of
    convincing-looking zeros.
    """
    name = _rng(edge_tab, "Name")
    pos = _rng(edge_tab, "Position")
    team = _rng(edge_tab, "Team")

    if "ImpliedMove" not in EDGE_COLUMNS:
        return f"{MOVEMENT_TAB}: skipped -- this version of EDGE_COLUMNS has no ImpliedMove column"

    move = _rng(edge_tab, "ImpliedMove")
    tot_move = _rng(edge_tab, "TotMove") if "TotMove" in EDGE_COLUMNS else None
    spd_move = _rng(edge_tab, "SpdMove") if "SpdMove" in EDGE_COLUMNS else None
    start = _rng(edge_tab, "GameStart") if "GameStart" in EDGE_COLUMNS else None
    # Part 7.9: "Flags" (everything that fired), not the hidden,
    # top-priority-only "Flag".
    flag = _rng(edge_tab, "Flags")

    # An unmoved line is 0.0, not blank, so filtering on <>"" alone lets
    # a whole page of zeros through and the empty-state message never
    # fires. Require actual movement -- ImpliedMove specifically, per the
    # docstring above.
    cond = f'{name}<>"",{move}<>"",ABS({move})>0'

    header = ["Player", "Pos", "Implied move"]
    col_terms = [name, f'{pos}&" "&{team}', move]
    if tot_move:
        header.append("Total move")
        col_terms.append(tot_move)
    if spd_move:
        header.append("Spread move")
        col_terms.append(spd_move)
    if start:
        header.append("Kickoff (UTC)")
        col_terms.append(
            f"IFERROR(TEXT(DATEVALUE(LEFT({start},10))+TIMEVALUE(MID({start},12,8)),"
            f'"ddd h:mm")&" UTC",{start})'
        )
    header.append("Flags")
    col_terms.append(flag)
    cols = "{" + ",".join(col_terms) + "}"

    body = (
        f"=IFERROR(ARRAY_CONSTRAIN(SORT(FILTER({cols},{cond}),"
        f"ABS(FILTER({move},{cond})),FALSE),40,{len(header)}),"
        f'"No line movement recorded yet — run dfs sync at least once this week.")'
    )

    rows = [
        ["MOVEMENT DESK — biggest line moves since the start of the NFL week"],
        [],
        header,
        [body],
    ]
    client.write_tab(MOVEMENT_TAB, rows)
    return f"{MOVEMENT_TAB}: built (top 40 by absolute implied-move, {len(header)} column(s))"
