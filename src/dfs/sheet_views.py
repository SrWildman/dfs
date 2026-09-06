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

Re-runnable: each builder overwrites its own tab. The one piece of typed
user input across all four -- Exposure's Target column -- is read back and
restored before the rewrite, so re-running never costs you your targets.
"""

from __future__ import annotations

from dfs.derived import EDGE_COLUMNS
from dfs.sheets import SheetsClient, column_letter

BOARD_TAB = "Board"
SLATE_TAB = "Slate Grid"
EXPOSURE_TAB = "Exposure"
MOVEMENT_TAB = "Movement"

# How far down the source tabs the formulas look. EdgeRaw runs ~743 rows.
_EXPOSURE_ROWS = 180


def _q(tab: str) -> str:
    """Quote a tab name for use in a formula if it needs it."""
    return f"'{tab}'" if (" " in tab or "-" in tab) else tab


def _col(name: str) -> str | None:
    if name not in EDGE_COLUMNS:
        return None
    return column_letter(EDGE_COLUMNS.index(name))


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

    The leverage panel is capped at 12 and tiebroken by CeilVal, which
    matters more than it sounds: while ProjOwn is all zeros every top
    player's Leverage is pinned at exactly 100.0, so an untiebroken sort
    returns whatever order the rows happened to arrive in. The proxy banner
    in row 3 says so out loud rather than letting a proxy number read as
    the real metric.
    """
    name = _rng(edge_tab, "Name")
    pos = _rng(edge_tab, "Position")
    team = _rng(edge_tab, "Team")
    lev = _rng(edge_tab, "Leverage")
    ceilval = _rng(edge_tab, "CeilVal")
    avail = _rng(edge_tab, "Avail")
    flag = _rng(edge_tab, "Flag")
    basis = _rng(edge_tab, "LevBasis")
    salary = _rng(edge_tab, "Salary")

    g = _q(games_tab)
    w = _q(weather_tab)

    live = f'{name}<>""'
    not_out = f'{flag}<>"OUT"'

    top_leverage = (
        f"=IFERROR(ARRAY_CONSTRAIN(SORT(FILTER("
        f'{{{name},{pos}&" "&{team},{lev},{ceilval}}},{live},{not_out}),3,FALSE,4,FALSE),12,4),"")'
    )
    best_value = (
        f"=IFERROR(ARRAY_CONSTRAIN(SORT(FILTER("
        f'{{{name},{pos}&" "&{team},{salary},{ceilval}}},{live},{not_out}),4,FALSE),12,4),"")'
    )
    landmines = (
        f"=IFERROR(ARRAY_CONSTRAIN(SORT(FILTER("
        f'{{{name},{pos}&" "&{team},{avail},{flag}}},{live},'
        f'({avail}<>"")+({flag}="WIND")+({flag}="OUT")),3,TRUE),14,4),"")'
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
    proxy_banner = (
        f'=IF(COUNTIF({basis},"proxy")>0,'
        f'"PROXY  —  ownership not published yet, so Leverage is showing CeilPct alone. '
        f'Treat the ranking as a ceiling ranking, not a leverage ranking.",'
        f'"Leverage is running on real ownership.")'
    )

    rows = [
        ["THIS WEEK'S BOARD"],
        ["Games", games, "Highest total", top_total, "Max wind", max_wind, "Injuries", injuries],
        [proxy_banner],
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
            "Flag",
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


def build_exposure(client: SheetsClient, *, edge_tab: str, lineups_tab: str, lineup_count: int) -> str:
    """Fills the Exposure tab, which has been a documented feature and a
    single empty cell since the sheet was built.

    Counts each player across the whole of Lineups column A, which holds
    only typed names plus the repeated "Name" header -- so it works
    regardless of how the twenty blocks are laid out, and keeps working if
    those blocks ever move.

    Target is the one typed column in the tab. It's read back and restored
    before the rewrite so re-running this never costs you the targets you
    set.
    """
    name = _rng(edge_tab, "Name")
    pos = _rng(edge_tab, "Position")
    salary = _rng(edge_tab, "Salary")
    lu = f"{_q(lineups_tab)}!$A:$A"

    # Preserve any targets already typed, keyed by player name.
    existing: dict[str, str] = {}
    if client.tab_exists(EXPOSURE_TAB):
        try:
            current = client.read_range(EXPOSURE_TAB, f"A2:F{_EXPOSURE_ROWS}")
            for row in current:
                if len(row) >= 6 and row[0] and row[5]:
                    existing[row[0].strip()] = row[5]
        except Exception:  # noqa: BLE001 - a malformed old tab must not block a rebuild
            existing = {}

    roster = f'=IFERROR(SORT(FILTER({{{name},{pos},{salary}}},{name}<>"",COUNTIF({lu},{name})>0),3,FALSE),"")'

    rows = [
        [
            "Name",
            "Pos",
            "Salary",
            "# Lineups",
            "Exposure",
            "Target",
            "vs Target",
            "",
            "Slots filled",
            f'=COUNTIF({lu},"?*")-COUNTIF({lu},"Name")',
        ],
        [roster, "", "", f'=IF($A2="","",COUNTIF({lu},$A2))', f'=IF($A2="","",D2/{lineup_count})', "", ""],
    ]
    for r in range(3, _EXPOSURE_ROWS + 1):
        rows.append(
            [
                "",
                "",
                "",
                f'=IF($A{r}="","",COUNTIF({lu},$A{r}))',
                f'=IF($A{r}="","",D{r}/{lineup_count})',
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

    note = f", {len(existing)} target(s) preserved" if existing else ""
    return f"{EXPOSURE_TAB}: built off {lineups_tab} (out of {lineup_count} lineups){note}"


# ---------------------------------------------------------------------------
# Movement (Direction H)
# ---------------------------------------------------------------------------


def build_movement(client: SheetsClient, *, edge_tab: str) -> str:
    """The hour before lock: players ranked by how far their team's implied
    total has moved since the start of the NFL week, with kickoff alongside.

    LineMove is blank until at least one `nfl_odds` sync has happened this
    week, so an unsynced sheet says so rather than showing a page of
    convincing-looking zeros.
    """
    name = _rng(edge_tab, "Name")
    pos = _rng(edge_tab, "Position")
    team = _rng(edge_tab, "Team")

    if "LineMove" not in EDGE_COLUMNS:
        return f"{MOVEMENT_TAB}: skipped -- this version of EDGE_COLUMNS has no LineMove column"

    move = _rng(edge_tab, "LineMove")
    start = _rng(edge_tab, "GameStart") if "GameStart" in EDGE_COLUMNS else None
    flag = _rng(edge_tab, "Flag")

    cond = f'{name}<>"",{move}<>""'
    cols = f'{{{name},{pos}&" "&{team},{move},{flag}}}'
    if start:
        cols = f'{{{name},{pos}&" "&{team},{move},{start},{flag}}}'

    body = (
        f"=IFERROR(ARRAY_CONSTRAIN(SORT(FILTER({cols},{cond}),"
        f"ABS(FILTER({move},{cond})),FALSE),40,{5 if start else 4}),"
        f'"No line movement recorded yet — run dfs sync at least once this week.")'
    )

    header = (
        ["Player", "Pos", "Line Move", "Kickoff (UTC)", "Flag"]
        if start
        else ["Player", "Pos", "Line Move", "Flag"]
    )
    rows = [
        ["MOVEMENT DESK — biggest line moves since the start of the NFL week"],
        [],
        header,
        [body],
    ]
    client.write_tab(MOVEMENT_TAB, rows)
    return f"{MOVEMENT_TAB}: built (top 40 by absolute line movement)"
