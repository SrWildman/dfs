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

from dfs.derived import EDGE_COLUMNS, EDGE_DATA_OFFSET
from dfs.sheets import SheetsClient, column_letter

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
# Board (Direction A)
# ---------------------------------------------------------------------------


def build_board(client: SheetsClient, *, edge_tab: str, games_tab: str, weather_tab: str) -> str:
    """A landing tab that answers "who am I looking at this week" without
    scrolling anything.

    The leverage panel takes EdgeRaw's own row order rather than re-sorting
    by Leverage itself: `derived.build_edge_frame` already writes EdgeRaw
    pre-sorted by Leverage descending once ownership is real, or by CeilPct
    descending while it's still unpublished (Leverage reads blank in that
    window, not a stand-in number -- see derived.py). Trusting that order
    here means this panel never needs its own basis-aware sort key. The
    banner in row 3 still says out loud which case is in effect, since a
    ceiling ranking and a leverage ranking answer different questions even
    though they can share a column.

    Phase 6, Part 1.3 (2026-09-17): `BEST CEILING VALUE` used to be one
    flat `SORT` by `CeilVal` descending across the whole slate --
    `CeilVal` (points per $1,000) isn't comparable across positions, so
    it read 11 QBs out of 12 rows on a real slate, not "best value at
    each position." Rebuilt as five independent per-position blocks
    (`_best_value_block`), each ranking `CeilVal` within its own position
    only, vertically stacked -- the same fix class `sheet_style.
    apply_grouped_color_scales` already applies to colour scales for the
    identical reason. Every panel here (this one included) is regenerated
    fresh every call against the CURRENT `EDGE_COLUMNS` layout via `_rng`/
    `_col` -- this function must be re-run after any EdgeRaw column
    reorder (Part 2 does one), since a formula string, once written, does
    NOT follow a later column move the way a live formula reference would
    -- found live 2026-09-17: the Sept 16 CeilPct/OwnPct reorder shifted
    every EdgeRaw column after CeilVal/ProjOwn two slots right, and
    because this tab was never regenerated afterward, `TOP LEVERAGE` was
    silently reading `ProjOwn` (coincidentally also 0.0 pre-midweek, which
    is what made it look like a formatting bug) and `LANDMINES` was
    silently reading `OwnPct`/`Leverage` where it expected `Avail`/`Flag`
    -- explaining both "renders 0.0" and "is empty" without either being
    what the original bug report assumed.
    """
    name = _rng(edge_tab, "Name")
    pos = _rng(edge_tab, "Position")
    team = _rng(edge_tab, "Team")
    lev = _rng(edge_tab, "Leverage")
    ceilval = _rng(edge_tab, "CeilVal")
    avail = _rng(edge_tab, "Avail")
    # Part 7.9: "Flags" is every matching condition -- deliberately not the
    # hidden, top-priority-only "Flag" -- since LANDMINES below needs to
    # catch a player who is OUT and something else too.
    flag = _rng(edge_tab, "Flags")
    basis = _rng(edge_tab, "OwnStatus")
    salary = _rng(edge_tab, "Salary")

    g = _q(games_tab)
    w = _q(weather_tab)

    live = f'{name}<>""'
    # Flags can hold more than one token space-separated (Fix 2.1 -- e.g.
    # "WIND LEVERAGE"), so an exact `="OUT"` no longer catches a player who
    # is OUT and something else too. SEARCH-based substring matching does;
    # none of the flag vocabulary (OUT/WIND/LINE↑/LINE↓/LEVERAGE/CHALK) is
    # a substring of another, so this can't misfire.
    not_out = f'NOT(ISNUMBER(SEARCH("OUT",{flag})))'
    is_wind = f'ISNUMBER(SEARCH("WIND",{flag}))'
    is_out = f'ISNUMBER(SEARCH("OUT",{flag}))'

    top_leverage = (
        f"=IFERROR(ARRAY_CONSTRAIN(FILTER("
        f'{{{name},{pos}&" "&{team},{lev},{ceilval}}},{live},{not_out}),12,4),"")'
    )
    # Ranked WITHIN position, not across the whole slate -- see this
    # function's docstring. 2 rows per position (5 positions = 10 rows) is
    # a plain vertical count, not a total cut: unlike ARRAY_CONSTRAIN-ing
    # ONE combined 12-row result (which would still show whichever
    # position happens to sort first, cutting off the rest), stacking
    # fixed-size per-position blocks guarantees every position is
    # represented every time.
    _BEST_VALUE_ROWS_PER_POSITION = 2

    def _best_value_block(position: str) -> str:
        is_position = f'{pos}="{position}"'
        return (
            f"IFERROR(ARRAY_CONSTRAIN(SORT(FILTER("
            f'{{{name},{pos}&" "&{team},{salary},{ceilval}}},{live},{not_out},{is_position}),4,FALSE),'
            f'{_BEST_VALUE_ROWS_PER_POSITION},4),{{"","","",""}})'
        )

    best_value = "={" + ";".join(_best_value_block(p) for p in ("QB", "RB", "WR", "TE", "DST")) + "}"
    landmines = (
        f"=IFERROR(ARRAY_CONSTRAIN(SORT(FILTER("
        f'{{{name},{pos}&" "&{team},{avail},{flag}}},{live},'
        f'({avail}<>"")+({is_wind})+({is_out})),'
        f'FILTER({salary},{live},({avail}<>"")+({is_wind})+({is_out})),'
        f'FALSE),14,4),"")'
    )

    games = f'=IFERROR(COUNTA(FILTER({g}!$A$2:$A$40,{g}!$A$2:$A$40<>"")),0)'
    top_total = (
        f'=IFERROR(INDEX(SORT(FILTER({{{g}!$B$2:$B$40&" / "&{g}!$C$2:$C$40,{g}!$M$2:$M$40}},'
        f'{g}!$A$2:$A$40<>""),2,FALSE),1,1)&"  "&'
        f'TEXT(MAX(FILTER({g}!$M$2:$M$40,{g}!$A$2:$A$40<>"")),"0.0"),"--")'
    )
    max_wind = f'=IFERROR(MAX(FILTER({w}!$F$2:$F$40,{w}!$A$2:$A$40<>""))&" mph","--")'
    injuries = (
        f'=COUNTIF({avail},"OUT")&" out  /  "&COUNTIF({avail},"IR")&" IR  /  "&COUNTIF({avail},"Q")&" Q"'
    )
    freshness_banner = (
        f'=IF(COUNTIF({basis},"unpublished")>0,'
        f'"UNPUBLISHED  —  ownership not out yet, so Leverage is blank. Ranked by ceiling '
        f'percentile instead; treat it as a ceiling ranking, not a leverage ranking.",'
        f'"Leverage is running on real ownership.")'
    )

    rows = [
        ["THIS WEEK'S BOARD"],
        ["Games", games, "Highest total", top_total, "Max wind", max_wind, "Injuries", injuries],
        [freshness_banner],
        [],
        ["TOP LEVERAGE", "", "", "", "", "BEST CEILING VALUE", "", "", "", "", "LANDMINES"],
        # fmt: off
        [
            "Player",
            "Pos",
            "Lev",
            "CeilVal",
            "",
            "Player",
            "Pos",
            "Salary",
            "CeilVal",
            "",
            "Player",
            "Pos",
            "Avail",
            "Flags",
        ],
        [top_leverage, "", "", "", "", best_value, "", "", "", "", landmines],
        # fmt: on
    ]
    client.write_tab(BOARD_TAB, rows)
    return f"{BOARD_TAB}: built (3 ranked panels + slate summary, all read-only)"


# ---------------------------------------------------------------------------
# Slate Grid (Direction F)
# ---------------------------------------------------------------------------


def build_slate_grid(client: SheetsClient, *, games_tab: str, weather_tab: str) -> str:
    """One row per game instead of one row per player.

    Surfaces AwayRest/HomeRest and DivGame, which `nflverse_games` already
    syncs into GamesRaw and which nothing in the sheet currently displays
    anywhere.
    """
    g, w = _q(games_tab), _q(weather_tab)
    rows = [["Matchup", "Kickoff", "Total", "Spread", "Roof", "Wind", "Gust", "Rest (A/H)", "Div", "Stadium"]]
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
                f'={guard}IFERROR(VLOOKUP({g}!$A{r},{w}!$A:$F,6,FALSE),""))',
                f'={guard}IFERROR(VLOOKUP({g}!$A{r},{w}!$A:$G,7,FALSE),""))',
                f'={guard}{g}!$I{r}&" / "&{g}!$J{r})',
                f'={guard}IF({g}!$K{r}=1,"DIV",""))',
                f"={guard}{g}!$F{r})",
            ]
        )
    client.write_tab(SLATE_TAB, rows)
    return f"{SLATE_TAB}: built (18 game rows off GamesRaw + WeatherRaw)"


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
