"""Write and check the Season sheet (`season_tabs` is the pure layout).

- `setup` (`dfs season setup`): creates the nine tabs in order (the blank sheet's `Sheet1` becomes Overview),
  the League / Players pickers (a dropdown over `SeasonLeaguesRaw`, like Player!B1), their FILTER formulas,
  the Trade Finder's targeting box, styling, and warning-only protection on every visible tab except the
  pickers and the box. Template-free: there is one Season sheet. Keeps what is typed in the pickers and box.
- `write_sync` (`dfs season sync`): the raw tabs whole, Overview, My Teams, the Trade Finder's ideas (never
  its box) and one filter view per league.
- `write_analyzer` (`dfs season trade`): the Analyzer tab.
- `doctor` (`dfs season doctor`): read-only structure check.
"""

from __future__ import annotations

from dfs.config import GoogleSheetsConfig, load_config
from dfs.season_long import season_tabs as st
from dfs.sheets import SheetsClient, SheetsError, column_letter

PROTECT_NOTE = "Written by dfs season sync; edits here are overwritten."


def client_for(sheet_id: str) -> SheetsClient:
    gs = load_config().google_sheets
    return SheetsClient(
        GoogleSheetsConfig(sheet_id=sheet_id, credentials_file=gs.credentials_file, tab_mappings={})
    )


def _style():
    from dfs.sheet_style import _HEADER_FMT, _PANEL_FMT, _TITLE_FMT, FAMILY_COLORS, INK_MUTED

    return _HEADER_FMT, _PANEL_FMT, _TITLE_FMT, FAMILY_COLORS, INK_MUTED


TYPED_FMT = {
    "backgroundColor": {"red": 1.0, "green": 0.957, "blue": 0.8},
    "textFormat": {"bold": True},
    "borders": {s: {"style": "SOLID"} for s in ("top", "bottom", "left", "right")},
}


def _read_cell(client: SheetsClient, tab: str, cell: str) -> str:
    if not client.tab_exists(tab):
        return ""
    try:
        grid = client.read_range_unformatted(tab, cell)
    except SheetsError:
        return ""
    return str(grid[0][0]).strip() if grid and grid[0] else ""


def read_box(client: SheetsClient) -> dict[str, str]:
    if not client.tab_exists(st.TRADE_FINDER_TAB):
        return {}
    first, last = st.box_cell(st.BOX_FIELDS[0]), st.box_cell(st.BOX_FIELDS[-1])
    grid = client.read_range_unformatted(st.TRADE_FINDER_TAB, f"{first}:{last}")
    values = [str(r[0]).strip() if r else "" for r in grid] + [""] * len(st.BOX_FIELDS)
    return {name: values[i] for i, name in enumerate(st.BOX_FIELDS) if values[i]}


def _ensure_tabs(client: SheetsClient) -> None:
    tabs = [t.title for t in client.list_tabs()]
    if st.OVERVIEW_TAB not in tabs and "Sheet1" in tabs:
        client.rename_tab("Sheet1", st.OVERVIEW_TAB)
    for tab in (*st.VISIBLE_TABS, *st.RAW_TABS):
        if not client.tab_exists(tab):
            client.write_tab(tab, [[""]])
    _, _, _, family, _ = _style()
    for i, tab in enumerate(st.VISIBLE_TABS):
        client.set_tab_properties(tab, index=i, color=family["decide"])
    for i, tab in enumerate(st.RAW_TABS):
        client.set_tab_properties(tab, index=len(st.VISIBLE_TABS) + i, color=family["feed"], hidden=True)


def _picker(client: SheetsClient, tab: str, rows: list[list], keep: str) -> None:
    header, panel, title, _, muted = _style()
    rows = [list(r) for r in rows]
    rows[0][1] = keep
    client.write_tab(tab, rows)
    client.format_range(tab, "A1", title)
    client.format_range(tab, st.PICKER_CELL, TYPED_FMT)
    client.format_range(tab, "A2", {"textFormat": {"italic": True, "fontSize": 9, "foregroundColor": muted}})
    last = column_letter(len(rows[st.VIEW_HEADER_ROW - 1]) - 1)
    client.format_range(tab, f"A{st.VIEW_HEADER_ROW}:{last}{st.VIEW_HEADER_ROW}", header)
    client.set_range_dropdown_validation(
        tab, st.PICKER_CELL, source=f"{st.LEAGUES_RAW}!$A$2:$A$50", strict=True
    )
    client.freeze(tab, rows=st.VIEW_HEADER_ROW, cols=0)


def _protect(client: SheetsClient, tab: str, exempt: list[str]) -> None:
    client.clear_protected_ranges(tab)
    client.protect_sheet(tab, unprotected_ranges=exempt or None, description=PROTECT_NOTE)


def setup(client: SheetsClient, league_names: list[str]) -> list[str]:
    _ensure_tabs(client)
    client.write_tab(st.LEAGUES_RAW, [["League"], *[[n] for n in league_names]])
    out = []
    for tab, rows in ((st.LEAGUE_TAB, st.league_tab_rows()), (st.PLAYERS_TAB, st.players_tab_rows())):
        keep = _read_cell(client, tab, st.PICKER_CELL) or (league_names[0] if league_names else "")
        _picker(client, tab, rows, keep)
        out.append(f"{tab}: picker in {st.PICKER_CELL} ({keep})")
    box = read_box(client)
    write_box(client, box)
    for tab in st.VISIBLE_TABS:
        exempt = {st.LEAGUE_TAB: [st.PICKER_CELL], st.PLAYERS_TAB: [st.PICKER_CELL]}.get(tab, [])
        if tab == st.TRADE_FINDER_TAB:
            exempt = [f"{st.box_cell(st.BOX_FIELDS[0])}:{st.box_cell(st.BOX_FIELDS[-1])}"]
        _protect(client, tab, exempt)
    out.append(
        f"{st.TRADE_FINDER_TAB}: targeting box "
        f"{st.box_cell(st.BOX_FIELDS[0])}:{st.box_cell(st.BOX_FIELDS[-1])}"
    )
    out.append("protection: every visible tab warns on an edit, except the pickers and the targeting box")
    return out


def write_box(client: SheetsClient, values: dict[str, str]) -> None:
    header, _, title, _, muted = _style()
    rows = st.box_rows(values)
    client.update_range(
        st.TRADE_FINDER_TAB,
        f"A{st.BOX_TITLE_ROW}:B{st.BOX_TITLE_ROW + len(rows) - 1}",
        [r + [""] * (2 - len(r)) for r in rows],
    )
    client.format_range(st.TRADE_FINDER_TAB, f"A{st.BOX_TITLE_ROW}", title)
    client.format_range(
        st.TRADE_FINDER_TAB, f"{st.box_cell(st.BOX_FIELDS[0])}:{st.box_cell(st.BOX_FIELDS[-1])}", TYPED_FMT
    )
    league_cell = st.box_cell("League")
    client.set_range_dropdown_validation(
        st.TRADE_FINDER_TAB, league_cell, source=f"{st.LEAGUES_RAW}!$A$2:$A$50", strict=True
    )


def _write_block(
    client: SheetsClient, tab: str, rows: list[list], titles: list[int], headers: list[int]
) -> None:
    header, panel, title, _, _ = _style()
    width = max(len(r) for r in rows) if rows else 1
    client.clear_ranges(tab, [f"A1:{column_letter(max(width, 12) - 1)}{max(len(rows) + 50, 200)}"])
    client.update_range(
        tab,
        f"A1:{column_letter(width - 1)}{len(rows)}",
        st.literal_rows([r + [""] * (width - len(r)) for r in rows]),
    )
    with client.batched():
        client.format_range(
            tab,
            f"A1:{column_letter(max(width, 12) - 1)}{max(len(rows) + 50, 200)}",
            {
                "textFormat": {"bold": False, "fontSize": 10},
                "backgroundColor": {"red": 1, "green": 1, "blue": 1},
            },
        )
        for r in titles:
            client.format_range(tab, f"A{r}:{column_letter(width - 1)}{r}", panel)
        for r in headers:
            client.format_range(tab, f"A{r}:{column_letter(width - 1)}{r}", header)


def write_sync(client: SheetsClient, runs: list[st.LeagueRun]) -> list[str]:
    if not client.tab_exists(st.OVERVIEW_TAB) or not client.tab_exists(st.TEAMS_RAW):
        raise SheetsError("The Season sheet is not set up yet: run `dfs season setup` first.")
    names = [r.view.league.name for r in runs]
    client.write_tab(st.LEAGUES_RAW, [["League"], *[[n] for n in names]])
    teams, players = st.teams_frame(runs), st.players_frame(runs)
    client.write_tab(st.TEAMS_RAW, st.cells(teams))
    client.write_tab(st.PLAYERS_RAW, st.cells(players))
    overview = st.overview_rows(runs)
    exposure_header = next(i for i, r in enumerate(overview) if r[:1] == ["Player"]) + 1
    _write_block(client, st.OVERVIEW_TAB, overview, [1, exposure_header - 1], [2, exposure_header])
    block = st.my_teams_block(runs)
    _write_block(client, st.MY_TEAMS_TAB, block.rows, block.title_rows, block.header_rows)
    write_waivers(client, runs)
    lines = [
        f"{st.TEAMS_RAW}: {len(teams)} teams, {st.PLAYERS_RAW}: {len(players)} players",
        f"{st.OVERVIEW_TAB}: {len(runs)} leagues; {st.MY_TEAMS_TAB}: {len(runs)} blocks",
    ]
    return [*lines, _write_ideas_table(client, st.ideas_rows(runs), names)]


def write_ideas(client: SheetsClient, runs: list[st.LeagueRun]) -> str:
    """`dfs season trades`: replace these leagues' rows in the ideas list, keep every other league's."""
    names = [r.view.league.name for r in runs]
    tf, first = st.TRADE_FINDER_TAB, st.IDEAS_HEADER_ROW
    last_col = column_letter(len(st.IDEAS_HEADER) - 1)
    kept = []
    if client.tab_exists(tf):
        grid = client.read_range(tf, f"A{first + 1}:{last_col}{first + 500}")
        kept = [r + [""] * (len(st.IDEAS_HEADER) - len(r)) for r in grid if r and r[0] and r[0] not in names]
    new = st.ideas_rows(runs)
    rows = [new[0], *kept, *new[1:]]
    all_names = list(dict.fromkeys([r[0] for r in rows[1:]] + names))
    return _write_ideas_table(client, rows, all_names)


def _write_ideas_table(client: SheetsClient, ideas: list[list], names: list[str]) -> str:
    first = st.IDEAS_HEADER_ROW
    tf = st.TRADE_FINDER_TAB
    client.clear_ranges(tf, [f"A{first}:{column_letter(len(st.IDEAS_HEADER) - 1)}{first + 500}"])
    client.update_range(
        tf,
        f"A{first}:{column_letter(len(st.IDEAS_HEADER) - 1)}{first + len(ideas) - 1}",
        st.literal_rows(ideas),
    )
    header, *_ = _style()
    client.format_range(tf, f"A{first}:{column_letter(len(st.IDEAS_HEADER) - 1)}{first}", header)
    for name in names:
        title = f"League: {name}"
        client.clear_filter_view(tf, title)
        client.add_filter_view(
            tf,
            title=title,
            a1_range=f"A{first}:{column_letter(len(st.IDEAS_HEADER) - 1)}{first + 500}",
            criteria={0: {"condition": {"type": "TEXT_EQ", "values": [{"userEnteredValue": name}]}}},
        )
    return f"{tf}: {len(ideas) - 1} ideas, a filter view per league"


def write_my_teams(client: SheetsClient, runs: list[st.LeagueRun]) -> str:
    """`dfs season lineups`: My Teams alone (the other tabs keep the last sync)."""
    if not client.tab_exists(st.MY_TEAMS_TAB):
        raise SheetsError("The Season sheet is not set up yet: run `dfs season setup` first.")
    block = st.my_teams_block(runs)
    _write_block(client, st.MY_TEAMS_TAB, block.rows, block.title_rows, block.header_rows)
    return f"{st.MY_TEAMS_TAB}: {len(runs)} leagues"


def write_waivers(client: SheetsClient, runs: list[st.LeagueRun]) -> str:
    if not client.tab_exists(st.WAIVERS_TAB):
        raise SheetsError("The Season sheet has no Waivers tab yet: run `dfs season setup` first.")
    block = st.waivers_block(runs)
    _write_block(client, st.WAIVERS_TAB, block.rows, block.title_rows, block.header_rows)
    return f"{st.WAIVERS_TAB}: {sum(len(r.pickups) for r in runs)} pickups"


def write_analyzer(client: SheetsClient, rows: list[list]) -> str:
    if not client.tab_exists(st.ANALYZER_TAB):
        raise SheetsError("The Season sheet is not set up yet: run `dfs season setup` first.")
    week_header = next((i + 1 for i, r in enumerate(rows) if r[:1] == ["Week"]), 0)
    _write_block(client, st.ANALYZER_TAB, rows, [1], [week_header] if week_header else [])
    return f"{st.ANALYZER_TAB}: written"


def doctor(client: SheetsClient) -> list[tuple[str, bool, str]]:
    """(check, ok, detail) per check. Never writes."""
    out: list[tuple[str, bool, str]] = []
    tabs = {t.title: t for t in client.list_tabs()}
    for tab in (*st.VISIBLE_TABS, *st.RAW_TABS):
        out.append((f"tab {tab}", tab in tabs, "present" if tab in tabs else "missing: run dfs season setup"))
    for raw, cols in ((st.TEAMS_RAW, st.TEAM_COLUMNS), (st.PLAYERS_RAW, st.PLAYER_COLUMNS)):
        if raw in tabs:
            header = client.read_range(raw, f"A1:{column_letter(len(cols) - 1)}1")
            got = header[0] if header else []
            ok = got == cols or got == []
            out.append(
                (f"{raw} header", ok, "matches" if ok else f"differs: {got[:6]}... (run dfs season sync)")
            )
    for tab, want in (
        (st.LEAGUE_TAB, st.league_tab_rows()[-1][0]),
        (st.PLAYERS_TAB, st.players_tab_rows()[-1][0]),
    ):
        if tab in tabs:
            got = client.read_formula(tab, f"A{st.VIEW_HEADER_ROW + 1}")
            text = got[0][0] if got and got[0] else ""
            out.append(
                (
                    f"{tab} formula",
                    text == want,
                    "matches the raw columns" if text == want else "stale: run dfs season setup",
                )
            )
            ok = client.has_data_validation(tab, st.PICKER_CELL)
            out.append((f"{tab} picker", ok, st.PICKER_CELL if ok else "no dropdown: run dfs season setup"))
    if st.TRADE_FINDER_TAB in tabs:
        grid = client.read_range(
            st.TRADE_FINDER_TAB, f"A{st.BOX_FIRST_ROW}:A{st.BOX_FIRST_ROW + len(st.BOX_FIELDS) - 1}"
        )
        labels = [r[0] if r else "" for r in grid]
        ok = labels == list(st.BOX_FIELDS)
        out.append(("Trade Finder box", ok, "labels in place" if ok else f"labels differ: {labels}"))
    return out
