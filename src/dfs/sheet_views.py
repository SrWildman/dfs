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
from dfs.sheet_columns import PLAYER_POOL_COLUMN_ORDER
from dfs.sheets import SheetsClient, column_letter
from dfs.sources.edge import POOL_COLUMN, _canonical_id
from dfs.sources.weather import WEATHER_COLUMNS
from dfs.weekly_reset import PLAYER_POOL_NAME_BLOCKS

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
# A hidden join-key column (GameId, for the per-row Wind VLOOKUP -- see
# build_board's Slate shape section) -- column J, one past every other
# section's rightmost visible column (I, Per-position leaders/Pool
# diagnostics), so hiding it can't hide real content belonging to a
# DIFFERENT section that happens to share the same column letter.
BOARD_SLATE_GAMEID_COL = "J"
BOARD_SLATE_GAMEID_COL_INDEX = 9

BOARD_LEADERS_HEADER_ROW = BOARD_SLATE_LAST_ROW + 2
BOARD_LEADERS_COLHEADER_ROW = BOARD_LEADERS_HEADER_ROW + 1
BOARD_LEADERS_FIRST_ROW = BOARD_LEADERS_COLHEADER_ROW + 1
_POSITIONS = ("QB", "RB", "WR", "TE", "DST")
_LEADERS_ROWS_PER_POSITION = 2
BOARD_LEADERS_ROWS = _LEADERS_ROWS_PER_POSITION * len(_POSITIONS)
BOARD_LEADERS_LAST_ROW = BOARD_LEADERS_FIRST_ROW + BOARD_LEADERS_ROWS - 1

BOARD_PUNT_HEADER_ROW = BOARD_LEADERS_LAST_ROW + 2
BOARD_PUNT_COLHEADER_ROW = BOARD_PUNT_HEADER_ROW + 1
BOARD_PUNT_FIRST_ROW = BOARD_PUNT_COLHEADER_ROW + 1
_PUNT_ROWS_PER_POSITION = 1
BOARD_PUNT_ROWS = _PUNT_ROWS_PER_POSITION * len(_POSITIONS)
BOARD_PUNT_LAST_ROW = BOARD_PUNT_FIRST_ROW + BOARD_PUNT_ROWS - 1
PUNT_SALARY_CEILING = 4000

BOARD_STACK_HEADER_ROW = BOARD_PUNT_LAST_ROW + 2
BOARD_STACK_COLHEADER_ROW = BOARD_STACK_HEADER_ROW + 1
BOARD_STACK_FIRST_ROW = BOARD_STACK_COLHEADER_ROW + 1
_STACK_GAMES = 5
BOARD_STACK_ROWS = _STACK_GAMES * 2  # two teams per game
BOARD_STACK_LAST_ROW = BOARD_STACK_FIRST_ROW + BOARD_STACK_ROWS - 1

BOARD_POOL_HEADER_ROW = BOARD_STACK_LAST_ROW + 2
BOARD_POOL_NOTICE_ROW = BOARD_POOL_HEADER_ROW + 1
BOARD_POOL_COLHEADER_ROW = BOARD_POOL_NOTICE_ROW + 1
BOARD_POOL_FIRST_ROW = BOARD_POOL_COLHEADER_ROW + 1
BOARD_POOL_POSITION_ROWS = len(_POSITIONS)
BOARD_POOL_SUMMARY_ROW = BOARD_POOL_FIRST_ROW + BOARD_POOL_POSITION_ROWS
BOARD_POOL_LAST_ROW = BOARD_POOL_SUMMARY_ROW

BOARD_CHALK_HEADER_ROW = BOARD_POOL_LAST_ROW + 2
BOARD_CHALK_PLACEHOLDER_ROW = BOARD_CHALK_HEADER_ROW + 1
BOARD_LAST_ROW = BOARD_CHALK_PLACEHOLDER_ROW


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
    client: SheetsClient, *, edge_tab: str, games_tab: str, weather_tab: str, player_pool_tab: str
) -> str:
    """Phase 6, Part 3 (2026-09-22): rebuilt from three ranked player
    panels (a question Sam already answered the moment he ticked his
    pool -- "it ranks 744 players") into one tab, seven sections, each a
    collapsible row group (Queue and Slate shape open by default,
    everything else collapsed). See `docs/planning/PROMPT_PHASE6.md` Part 3 and
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
    games = f'=SUMPRODUCT(({g}!$A$2:$A$40<>"")*1)'
    top_total = (
        f'=IFERROR(INDEX(SORT(FILTER({{{g}!$B$2:$B$40&" / "&{g}!$C$2:$C$40,{g}!$M$2:$M$40}},'
        f'{g}!$A$2:$A$40<>""),2,FALSE),1,1)&"  "&'
        f'TEXT(MAX(FILTER({g}!$M$2:$M$40,{g}!$A$2:$A$40<>"")),"0.0"),"--")'
    )
    max_wind = f'=IFERROR(MAX(FILTER({w}!$F$2:$F$40,{w}!$A$2:$A$40<>""))&" mph","--")'
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
    def _ranked_position_block(
        position: str, *, sort_metric: str, rows_per_position: int, extra: str = ""
    ) -> str:
        # Ranked WITHIN position, not across the whole slate -- this is
        # the actual fix for the 11-of-12-QBs bug (a flat sort by a
        # salary ratio isn't comparable across positions). Stacking a
        # FIXED number of rows per position, rather than cutting one
        # combined ranking to a total row count, guarantees every
        # position is represented every time instead of whichever one
        # happens to sort first crowding out the rest.
        is_position = f'{pos}="{position}"'
        filters = f"{live},{not_out},{is_position}" + (f",{extra}" if extra else "")
        return (
            f"IFERROR(ARRAY_CONSTRAIN(SORT(FILTER("
            f'{{{name},{pos}&" "&{team},{salary},{sort_metric}}},{filters}),4,FALSE),'
            f'{rows_per_position},4),{{"","","",""}})'
        )

    best_valadj = (
        "={"
        + ";".join(
            _ranked_position_block(p, sort_metric=valadj, rows_per_position=_LEADERS_ROWS_PER_POSITION)
            for p in _POSITIONS
        )
        + "}"
    )
    highest_proj = (
        "={"
        + ";".join(
            _ranked_position_block(p, sort_metric=projpts, rows_per_position=_LEADERS_ROWS_PER_POSITION)
            for p in _POSITIONS
        )
        + "}"
    )

    # ---- Section 4: punt finder ------------------------------------------
    punt_finder = (
        "={"
        + ";".join(
            _ranked_position_block(
                p,
                sort_metric=valadj,
                rows_per_position=_PUNT_ROWS_PER_POSITION,
                extra=f"{salary}<{PUNT_SALARY_CEILING}",
            )
            for p in _POSITIONS
        )
        + "}"
    )

    # ---- Section 5: stack candidates (replaces the old leverage panel, 7.6) --
    # Two teams sharing a game share the identical OverUnder value, so
    # sorting individual teams by it is enough to keep them adjacent --
    # no need to join back through GamesRaw for a game grouping.
    team_list = (
        f"=IFERROR(ARRAY_CONSTRAIN(SORT(UNIQUE(FILTER({{{team},{overunder}}},{live})),2,FALSE),"
        f'{BOARD_STACK_ROWS},1),"")'
    )

    def _stack_row_formulas(row: int) -> list[str]:
        team_cell = f"$A{row}"
        qb_filter = f'({team}={team_cell})*({pos}="QB")'
        wr1_filter = f'({team}={team_cell})*({pos}="WR")*({tmrank}=1)'
        te1_filter = f'({team}={team_cell})*({pos}="TE")*({tmrank}=1)'
        return [
            f'=IFERROR(INDEX(FILTER({name},{qb_filter}),1),"")',
            f'=IFERROR(INDEX(FILTER({salary},{qb_filter}),1),"")',
            f'=IFERROR(INDEX(FILTER({name},{wr1_filter}),1),"")',
            f'=IFERROR(INDEX(FILTER({salary},{wr1_filter}),1),"")',
            f'=IFERROR(INDEX(FILTER({name},{te1_filter}),1),"")',
            f'=IFERROR(INDEX(FILTER({salary},{te1_filter}),1),"")',
        ]

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
        return [
            position,
            f'=IFERROR(MIN(FILTER({pp_salary},{pooled})),"")',
            f'=IFERROR(MAX(FILTER({pp_salary},{pooled})),"")',
            f'=IFERROR(AVERAGE(FILTER({pp_salary},{pooled})),"")',
            f'=IFERROR(INDEX({cheapest},1,1),"")',
            f'=IFERROR(INDEX({cheapest},1,2),"")',
            f'=COUNTIF({pp_flags},"*CHALK*")',
            f'=COUNTIF({pp_flags},"*LEVERAGE*")',
            f'=IF(COUNTIF(FILTER({pp_salary},{pooled}),"<{PUNT_SALARY_CEILING}")=0,'
            f'"No {position} under ${PUNT_SALARY_CEILING:,}","")',
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
    _set(BOARD_SLATE_COLHEADER_ROW, ["Matchup", "Total", "Wind", "Shootout?"])
    wind_end_col = column_letter(WEATHER_COLUMNS.index("Wind"))
    wind_idx = WEATHER_COLUMNS.index("Wind") + 1
    # One spilling SORT, not a per-row passthrough of GamesRaw's own
    # (unsorted) row order -- "games ranked by total" is the actual ask.
    # A second, independent SORT on the exact same key (Total) fills a
    # parallel GameId column at BOARD_SLATE_GAMEID_COL, well past every
    # other section's rightmost visible column so hiding it (style_board)
    # can't hide real content elsewhere -- needed as a join key for the
    # per-row Wind lookup below, since WeatherRaw is keyed on GameId, not
    # the sorted Matchup text. Verified live (2026-09-22, template) that
    # two SORTs on the same key preserve identical relative order for
    # tied values, so the two columns stay row-aligned.
    slate_sorted = (
        f"=IFERROR(ARRAY_CONSTRAIN(SORT(FILTER("
        f'{{{g}!$B$2:$B$40&" @ "&{g}!$C$2:$C$40,{g}!$M$2:$M$40}},'
        f'{g}!$A$2:$A$40<>""),2,FALSE),{BOARD_SLATE_ROWS},2),"")'
    )
    slate_gameid = (
        f"=IFERROR(ARRAY_CONSTRAIN(SORT(FILTER("
        f"{{{g}!$A$2:$A$40,{g}!$M$2:$M$40}},"
        f'{g}!$A$2:$A$40<>""),2,FALSE),{BOARD_SLATE_ROWS},1),"")'
    )
    _set(BOARD_SLATE_FIRST_ROW, [slate_sorted])
    _set(BOARD_SLATE_FIRST_ROW, [slate_gameid], start_col=BOARD_SLATE_GAMEID_COL_INDEX)
    for i in range(BOARD_SLATE_ROWS):
        r = BOARD_SLATE_FIRST_ROW + i
        guard = f'IF($A{r}="","",'
        _set(
            r,
            [
                f"={guard}IFERROR(VLOOKUP(${BOARD_SLATE_GAMEID_COL}{r},{w}!$A:${wind_end_col},"
                f'{wind_idx},FALSE),""))',
                f'={guard}IF($B{r}>={SHOOTOUT_TOTAL_THRESHOLD},"Shootout",""))',
            ],
            start_col=2,
        )

    _set(BOARD_LEADERS_HEADER_ROW, ["PER-POSITION LEADERS  —  ranked within position, never across it"])
    _set(
        BOARD_LEADERS_COLHEADER_ROW,
        ["Player", "Pos", "Salary", "ValAdj", "", "Player", "Pos", "Salary", "ProjPts"],
    )
    _set(BOARD_LEADERS_FIRST_ROW, [best_valadj], start_col=0)
    _set(BOARD_LEADERS_FIRST_ROW, [highest_proj], start_col=5)

    _set(
        BOARD_PUNT_HEADER_ROW, [f"PUNT FINDER  —  best play under ${PUNT_SALARY_CEILING:,} at each position"]
    )
    _set(BOARD_PUNT_COLHEADER_ROW, ["Player", "Pos", "Salary", "ValAdj"])
    _set(BOARD_PUNT_FIRST_ROW, [punt_finder])

    _set(BOARD_STACK_HEADER_ROW, ["STACK CANDIDATES  —  QB + top pass-catchers, highest-total games first"])
    _set(BOARD_STACK_COLHEADER_ROW, ["Team", "QB", "Salary", "WR1", "Salary", "TE1", "Salary"])
    _set(BOARD_STACK_FIRST_ROW, [team_list])
    for i in range(BOARD_STACK_ROWS):
        r = BOARD_STACK_FIRST_ROW + i
        _set(r, _stack_row_formulas(r), start_col=1)

    _set(BOARD_POOL_HEADER_ROW, ["POOL DIAGNOSTICS  —  reads your pool, not the slate"])
    _set(BOARD_POOL_NOTICE_ROW, [pool_empty_notice])
    _set(
        BOARD_POOL_COLHEADER_ROW,
        ["Pos", "Min Sal", "Max Sal", "Avg Sal", "Cheapest", "Cheapest Sal", "Chalk#", "Leverage#", "Gap"],
    )
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


def build_slate_grid(client: SheetsClient, *, games_tab: str, weather_tab: str, edge_tab: str) -> str:
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
    """
    g, w, e = _q(games_tab), _q(weather_tab), _q(edge_tab)
    wind_end_col = column_letter(WEATHER_COLUMNS.index("Wind"))
    wind_idx = WEATHER_COLUMNS.index("Wind") + 1
    gust_end_col = column_letter(WEATHER_COLUMNS.index("Gust"))
    gust_idx = WEATHER_COLUMNS.index("Gust") + 1
    team_col = column_letter(EDGE_COLUMNS.index("Team") + EDGE_DATA_OFFSET)
    tot_move_end_col = column_letter(EDGE_COLUMNS.index("TotMove") + EDGE_DATA_OFFSET)
    tot_move_idx = EDGE_COLUMNS.index("TotMove") - EDGE_COLUMNS.index("Team") + 1
    spd_move_end_col = column_letter(EDGE_COLUMNS.index("SpdMove") + EDGE_DATA_OFFSET)
    spd_move_idx = EDGE_COLUMNS.index("SpdMove") - EDGE_COLUMNS.index("Team") + 1
    rows = [
        [
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
        ]
    ]
    for r in range(2, 20):
        guard = f'IF({g}!$A{r}="","",'
        rows.append(
            [
                f'={guard}{g}!$B{r}&" @ "&{g}!$C{r})',
                f"={guard}"
                f'IFERROR(TEXT({g}!$D{r},"ddd")&" "&TEXT({g}!$E{r},"h:mm am/pm"),'
                f'{g}!$D{r}&" "&{g}!$E{r}))',
                f"={guard}{g}!$M{r})",
                f"={guard}{g}!$L{r})",
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
            ]
        )
    client.write_tab(SLATE_TAB, rows)
    return f"{SLATE_TAB}: built (18 game rows off GamesRaw + WeatherRaw + EdgeRaw)"


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
    lu = f"{_q(lineups_tab)}!$A$1:$A"

    lineups_header = client.read_range(lineups_tab, f"A{lineups_header_row}:{lineups_header_row}")
    lineups_header = lineups_header[0] if lineups_header else []
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
        qb_names = f'UNIQUE(FILTER({lu},{lu_pos}="QB"))'
        distinct_qbs = f"=COUNTA({qb_names})"
        # Found live (2026-09-19), same static-label pitfall as Lineups'
        # stale "DEF" bug: `Pos.` is the FIXED slot label, always "QB" for
        # one row per block regardless of whether a name is typed there,
        # so `COUNTIF(lu_pos,"QB")` was always 20 (the block count), never
        # "how many QB slots are actually filled." That made `shared_qb`
        # read "Yes" even with zero real QBs rostered anywhere (20 > 0).
        # `lu,"<>"` requires the Name cell itself (typed by hand, so
        # genuinely blank when empty -- not a formula-blank like GameID)
        # to be non-blank too.
        qb_slots_filled = f'COUNTIFS({lu_pos},"QB",{lu},"<>")'
        shared_qb = f'=IF({qb_slots_filled}>COUNTA({qb_names}),"Yes","No")'
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
            f'=COUNTIF({lu},"?*")-COUNTIF({lu},"Name")',
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
    LINE↑/LINE↓ actually keys off) -- TotMove/SpdMove ride along as extra
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
