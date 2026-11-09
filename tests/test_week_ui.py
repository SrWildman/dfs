"""Plan N's page items (1-14) as the HTML and the scripts carry them, checked without a browser: the chat
toggle remembered, one wide column, sticky headers and names, the game columns on every player grid, the page
state kept across a refresh, one pool store, the bulk bar, decisions that move the pool, the chart viewer's
data, the game view's data, the cash core, the chalk check, the consensus in DK's roster order and a "how this
is built" note on every table and chart. `test_week_ui_browser.py` drives the same page in a real browser."""

from __future__ import annotations

import json
import re

import pytest

from dfs import consensus as cons
from dfs import week_charts, week_explorer, week_inputs, week_live, week_page, week_plays, week_top, week_ui
from tests.test_week_money import OWN, entered_record, rows_with_more_wrs
from tests.test_week_plays import WED, inputs, page_text, render, row, section, slate_rows, write_csv


def live(page: str) -> str:
    return week_live.augment(page, token="tok", nonce="n", read_only=False)


def full_page(tmp_path, **kw):
    inp = inputs(tmp_path / "data", rows=rows_with_more_wrs(**OWN), entered=entered_record(), **kw)
    return inp, render(inp)


def json_block(page: str, block_id: str) -> dict:
    m = re.search(rf'<script type="application/json" id="{block_id}">(.*?)</script>', page, re.S)
    assert m, f"no {block_id}"
    return json.loads(m.group(1))


# -- item 1: the chat folds to a tab, remembered per browser -------------------------------------------------


def test_the_chat_toggle_is_remembered_per_browser_and_wrapped_in_try():
    js = week_live.JS
    assert (
        "localStorage.setItem('dfs-chat-collapsed'" in js
        and "localStorage.getItem('dfs-chat-collapsed')" in js
    )
    for call in re.findall(r"[^\n]*localStorage\.[gs]etItem\('dfs-chat-collapsed'[^\n]*", js):
        assert "try {" in call  # every access wrapped: a private window never breaks the page
    assert "chat-open" in js and "chat-closed" in js  # the layout follows it
    assert "#chat.collapsed #chat-toggle{writing-mode:vertical-rl" in week_live.CSS  # the thin tab


# -- item 2: one column, wide ------------------------------------------------------------------------------


def test_the_board_charts_games_and_explorer_are_full_width_rows(tmp_path):
    _, page = full_page(tmp_path)
    main = page[page.index("<main>") :]
    board, charts, games, explorer = (
        main.index(x) for x in ('id="board"', 'class="charts-row"', 'id="games"', 'id="explorer-card"')
    )
    assert board < charts < games < explorer
    assert (
        'class="grid board-grid"' not in page and '<div class="side">' not in page
    )  # nothing beside the board
    assert "max-width:1240px" not in week_page.CSS  # wide: the board and the Explorer use the screen
    assert (
        "body.live.chat-open .grid,body.live.chat-open .charts-row{grid-template-columns:minmax(0,1fr)}"
        in (week_page.CSS)
    )  # chat open: every section is one column


# -- item 3: sticky headers and names ------------------------------------------------------------------------


def test_the_board_repeats_its_header_at_each_position_group(tmp_path):
    _, page = full_page(tmp_path)
    board = section(page, "board")
    groups = re.findall(r'<tbody class="posgrp" data-pos="(\w+)">(<tr class="ph">.*?</tr>)', board, re.S)
    assert [g for g, _ in groups] == ["QB", "RB", "WR", "TE", "DST"]
    for pos, head in groups:
        text = page_text(head)
        for label in (
            "Opp",
            "Spread",
            "Total",
            "Implied",
            "Salary",
            "Proj",
            "DU own",
            "GPP own",
            "Why",
            "Pool",
        ):
            assert label in text, (pos, label)
    assert "<thead>" not in board  # the group headers are the header
    assert "table.board tr.ph th{position:sticky;top:0" in week_page.CSS


def test_sticky_headers_live_in_each_tables_scroll_box_and_names_stick_sideways(tmp_path):
    css = week_page.CSS
    assert ".scroll{overflow:auto;max-height:80vh}" in css  # the scroll box the header sticks in
    assert ".scroll>table>thead th{position:sticky;top:0" in css
    assert "table.stickyname td:first-child,table.stickyname th:first-child{position:sticky;left:0" in css
    _, page = full_page(tmp_path)
    assert '<table class="board stickyname">' in page
    assert 'class="ex stickyname"' in page
    assert '<table class="stickyname">' in section(page, "consensus")
    assert '<table class="stickyname">' in section(page, "entry-plan")


# -- item 4: Opp, spread, total and implied on every player grid ---------------------------------------------


def test_the_board_shows_opp_spread_total_and_implied(tmp_path):
    _, page = full_page(tmp_path)
    alpha = re.search(r'<tr class="[^"]*" data-id="id-Qb Alpha".*?</tr>', section(page, "board"), re.S).group(
        0
    )
    cells = re.findall(r"<td[^>]*>(.*?)</td>", alpha, re.S)
    assert 'data-team="IND"' in cells[1] and ">IND</a>" in cells[1]  # Opp opens the game
    assert cells[2:5] == ["-6", "52.5", "29.2"]  # spread, total, implied = 52.5 / 2 + 3


def test_every_explorer_lens_and_the_drawer_carry_the_game():
    for lens in week_explorer.LENSES:
        if lens.key == "all":
            continue
        cols = [*week_explorer.BASE, *lens.columns]
        for c in ("Opp", "Spread", "OverUnder", week_explorer.IMPLIED):
            assert c in cols, (lens.key, c)
    js = week_explorer.JS
    assert "'spread ' +" in js and "'total ' +" in js and "'implied ' +" in js  # the drawer's game line


def test_explorer_lens_columns_are_not_repeated(tmp_path):
    inp, _ = full_page(tmp_path)
    data = json.loads(week_explorer.payload_json(week_explorer.payload(inp, week_top.wb.board(inp), {})))
    for lens in data["lenses"]:
        assert len(lens["columns"]) == len(set(lens["columns"])), lens["key"]


# -- item 5: what is open and where the page is survive a refresh --------------------------------------------


def test_the_page_state_is_kept_per_browser_and_restored_after_each_refresh(tmp_path):
    _, page = full_page(tmp_path)
    assert '<meta name="dfs-week" content="2026-09">' in page  # the state is per week
    js = week_ui.JS
    for piece in (
        "document.addEventListener('toggle'",  # every fold Sam opens or closes
        "U.more.push(b.dataset.pos)",  # every Show more
        "U.scroll = scrollPos()",  # where he was
        "capture() {",
        "restore(opts) {",
    ):
        assert piece in js
    assert (
        "try { const v = localStorage.getItem('dfs-' + k)" in js
        and "try { localStorage.setItem('dfs-' + k" in js
    )
    refresh = week_live.JS[week_live.JS.index("async function refreshSections") :]
    refresh = refresh[: refresh.index("\n}\n")]
    assert refresh.index("window.dfsUI.capture()") < refresh.index("old.replaceWith(fresh)")
    assert refresh.index("old.replaceWith(fresh)") < refresh.index("window.dfsUI.restore()")


def test_every_details_block_sits_in_a_section_with_an_id(tmp_path):
    """The state is keyed by section id: a fold outside one would never be remembered."""
    _, page = full_page(tmp_path)
    main = page[page.index("<main>") : page.index("</main>")]
    depth, sections = 0, []
    for m in re.finditer(r"<(/?)(section|details)\b([^>]*)>", main):
        closing, tag, attrs = m.groups()
        if tag == "section":
            if closing:
                sections.pop()
            else:
                sections.append(re.search(r'id="([^"]+)"', attrs))
        elif not closing:
            depth += 1
            assert sections and sections[-1], "a <details> outside a section with an id"
    assert depth


# -- item 6: one pool store ---------------------------------------------------------------------------------


def test_every_place_that_shows_a_pool_value_carries_the_players_id(tmp_path):
    rows = rows_with_more_wrs(**OWN)
    for r in rows:
        if r["Name"] == "Qb Alpha":
            r["CashOwn"] = "0.41"
    inp = inputs(tmp_path / "data", rows=rows, entered=entered_record())
    page = live(week_page.render(inp, live=True, nonce="n"))
    assert '<span class="seg" data-id="id-Qb Alpha"' in section(page, "board")
    assert 'class="poolval" data-id="id-Qb Alpha"' in section(page, "decisions")
    assert 'class="poolval" data-id="' in section(page, "consensus")
    js = week_ui.JS
    assert "$$('.seg[data-id=\"' + CSS.escape(id) + '\"]')" in js  # board and drawer controls
    assert "$$('.poolval[data-id=\"' + CSS.escape(id) + '\"]')" in js  # decisions, consensus, game view
    assert "$$('button.pivot[data-pivot=\"' + CSS.escape(id) + '\"]')" in js  # the pivot's `was`
    assert "window.dfsExplorer.setPool(id, v)" in js  # the Explorer's Pool column
    assert (
        "function showPool(values) { if (window.dfsPool) window.dfsPool.set(values || {}); }" in week_live.JS
    )
    assert "window.dfsPool.get(id)" in week_explorer.JS  # a re-render reads the store


# -- item 7: multi-select, the bulk bar, the queue -----------------------------------------------------------


def test_board_rows_have_a_tick_box_and_the_explorer_draws_one(tmp_path):
    _, page = full_page(tmp_path)
    board = section(page, "board")
    assert board.count('<input type="checkbox" class="sel" data-id="') == board.count(
        '<tr class="'
    ) - board.count('<tr class="ph"') - board.count('<tr class="morerow"')
    assert "window.dfsSel.box(id)" in week_explorer.JS
    js = week_ui.JS
    assert "e.shiftKey && lastTick" in js  # shift-click ticks the range
    assert "[['Cash', 'Cash'], ['GPP', 'GPP'], ['Both', 'Both'], ['', 'none']]" in js


def test_pool_writes_queue_instead_of_being_refused(tmp_path):
    js = week_ui.JS
    assert "One change at a time" not in week_live.JS  # the old refusal is gone
    assert "Q.push(job); pump();" in js
    assert "P.saving[c.id] = c.value" in js and "saving\\2026" in week_ui.CSS  # "saving..." on each cell
    assert "res.status === 'conflict'" in js and "drawIssues()" in js  # a refused cell keeps its own row
    assert "window.dfsQueue.run(async () => {" in week_live.JS  # decisions wait their turn too


# -- item 9: every chart opens full screen -------------------------------------------------------------------


def test_the_viewer_draws_the_same_players_as_the_inline_charts(tmp_path):
    rows = rows_with_more_wrs(**OWN)
    for r in rows:
        r["Tgt%"], r["Snap%"] = "0.2", "0.7"
    inp = inputs(tmp_path / "data", rows=rows)
    page = render(inp)
    data = json_block(page, "chart-data")
    assert set(data) == {"value", "leverage", "usage"}
    inline = set(
        re.findall(
            r'<circle class="dot [^"]*" cx="[^"]*" cy="[^"]*" r="[^"]*" data-id="([^"]+)"',
            week_charts.value_map(inp.edge),
        )
    )
    assert {p["id"] for p in data["value"]["points"]} == inline
    p = data["value"]["points"][0]
    assert {"name", "team", "pos", "sal", "proj", "du", "gpp", "x", "y", "prio"} <= set(
        p
    )  # what the hover shows
    for key in data:
        assert data[key]["how"] == week_charts.HOW[key]
    for sid, key in (("value-map", "value"), ("leverage-map", "leverage")):
        assert f'id="{sid}" data-chart="{key}"' in page
        assert f'<button type="button" class="expand" data-chart="{key}"' in page


def test_the_viewer_has_zoom_pan_box_lasso_hover_filters_and_declutter():
    js = week_ui.JS
    for piece in (
        "svg.addEventListener('wheel'",
        "V.pointers.size === 2",  # pinch
        "['zoom', 'Zoom box'",
        "['pan', 'Pan'",
        "['box', 'Select box'",
        "['lasso', 'Lasso'",
        "function inPoly(",
        "Reset zoom",
        "V.show[p] = cb.checked",  # position filters
        "if (placed.some(",  # labels only where they fit
        "'CalPts ' + ",  # the hover
    ):
        assert piece in js, piece
    assert "<script src" not in render_plain() and "http" not in js  # inline, no library


def render_plain():
    return week_page.CSS + week_ui.JS


# -- item 10: the game view ----------------------------------------------------------------------------------


def test_the_game_view_has_both_teams_by_position_and_the_games_stacks(tmp_path):
    from dfs import week_inputs

    week_inputs.save_input(
        "stacks",
        {"rows": [{"qb": "Qb Alpha", "mates": "Wr Value", "game": "DAL v IND", "top1": 0.021, "lift": 1.4}]},
        season=2026,
        week=9,
        data_dir=tmp_path / "data",
    )
    inp, page = full_page(tmp_path)
    games = json_block(page, "game-data")
    g = games["2026_09_DAL_IND"]
    assert g["order"] == ["DAL", "IND"] and g["title"] == "DAL @ IND"
    assert [grp for grp, _ in g["teams"]["DAL"]] == ["QB", "Pass catchers", "DST"]  # DAL has no RB here
    assert [grp for grp, _ in g["teams"]["IND"]] == ["QB", "Pass catchers"]
    qb = g["teams"]["DAL"][0][1][0]
    assert qb["name"] == "Qb Alpha" and {"sal", "cal", "ceil", "gpp", "lev"} <= set(qb)
    assert "Wr Out" not in json.dumps(g)  # OUT is left out
    assert "total 52.5" in g["line"] and "DAL -6" in g["line"] and "implied DAL 29.2 / IND 23.2" in g["line"]
    assert g["stacks"] and g["stacks"][0].startswith("Qb Alpha + Wr Value: Top 1% 2.1%")
    assert games["2026_09_NE_NYJ"]["stacks"] == []
    assert 'data-game="2026_09_DAL_IND"' in section(page, "games")  # a slate card opens it
    assert 'class="tm" href="#" data-game="2026_09_DAL_IND" data-team="IND"' in section(
        page, "board"
    )  # an Opp too
    assert "a.dataset.team = team" in week_explorer.JS  # the Explorer's Team / Opp cells too


@pytest.mark.parametrize(("nflverse", "dk"), [("LA", "LAR"), ("JAC", "JAX"), ("WSH", "WAS")])
def test_the_game_view_finds_a_team_that_nflverse_spells_differently(tmp_path, nflverse, dk):
    """Plan O item 1: the Rams showed no players. GameID is nflverse's (`2026_09_LA_WAS`), the rows' Team is
    DraftKings' (`LAR`), and the view matched one against the other."""
    rows = [
        row("Qb Ram", "QB", dk, "NYG", 6000, 20.0, Spread="-3", OverUnder="45.5"),
        row("Wr Ram", "WR", dk, "NYG", 5000, 14.0, Spread="-3", OverUnder="45.5"),
        row("Qb Giant", "QB", "NYG", dk, 5500, 17.0, Spread="3", OverUnder="45.5"),
    ]
    for r in rows:
        r["GameID"] = f"2026_09_{nflverse}_NYG"
    write_csv(tmp_path / "data" / "current" / "edge.csv", rows)
    inp = week_inputs.gather(2026, 9, WED, tmp_path / "data")
    g = week_top.game_data(inp)[f"2026_09_{nflverse}_NYG"]
    assert g["order"] == [dk, "NYG"] and g["title"] == f"{dk} @ NYG"
    assert [p["name"] for _, ps in g["teams"][dk] for p in ps] == ["Qb Ram", "Wr Ram"]
    assert g["total_text"] == "total 45.5"
    assert [x.away for x in week_plays.slate(rows)] == [dk]


def test_the_game_view_has_a_pool_control_on_every_row_and_moves_between_games():
    js = week_ui.JS
    assert (
        "pc.append(pool.seg(p.id, p.name))" in js
    )  # the same Cash / GPP / Both / none control (add AND remove)
    assert "dataset.act = 'prev'" in js and "dataset.act = 'next'" in js and "dataset.act = 'list'" in js
    assert "gameStep(e.key === 'ArrowLeft' ? -1 : 1)" in js


# -- item 11 is in test_week_plays (caps, ranking, chalk core) -----------------------------------------------


def test_the_explorer_role_filter_includes_chalk_core(tmp_path):
    inp, _ = full_page(tmp_path)
    data = week_explorer.payload(inp, week_top.wb.board(inp), {})
    assert "Chalk core" in data["roles"] and "opts(F.role, D.roles)" in week_explorer.JS


# -- item 12: the chalk check header and the close ones ------------------------------------------------------


def test_the_chalk_check_says_who_counts_and_shows_the_next_three_as_close(tmp_path):
    rows = slate_rows()
    own = {
        "Qb Alpha": 0.41,
        "Wr Value": 0.33,
        "Wr Star": 0.29,
        "Rb Fine": 0.22,
        "Te Plain": 0.20,
        "Rb Warn": 0.05,
    }
    for r in rows:
        r["CashOwn"] = str(own.get(r["Name"], 0.01))
    builds = {"builds": [{"kind": "cash", "number": 1, "names": ["Qb Alpha"], "metrics": {"p_cash": 0.6}}]}
    page = render(inputs(tmp_path, rows, suggest=builds))
    cash = page_text(section(page, "cash"))
    assert "Cash chalk check: everyone predicted in 30% or more of double-up lineups (CashOwn) (2)" in cash
    rows = page_text(
        re.sub(r'<p class="cap">.*?</p>', "", section(page, "cash"), flags=re.S)
    )  # not the caption
    closes = re.findall(r"(\w+ \w+)\s+close\b", rows)
    assert closes == ["Wr Star", "Rb Fine", "Te Plain"]  # the next 3 under the line


# -- item 13: the consensus in DK's roster order, with its caption -------------------------------------------


def test_dk_order_is_qb_rb_rb_wr_wr_wr_te_flex_dst_with_the_latest_kickoff_in_flex():
    edge = [
        row("D", "DST", "AAA", "BBB", 3000, 7.0),
        row("W1", "WR", "AAA", "BBB", 6000, 15.0),
        row("R1", "RB", "AAA", "BBB", 6000, 15.0),
        row("W2", "WR", "AAA", "BBB", 6000, 15.0, GameStart="2026-11-09T01:20:00Z"),  # the Sunday night game
        row("T", "TE", "AAA", "BBB", 4000, 9.0),
        row("Q", "QB", "AAA", "BBB", 6000, 20.0),
        row("R2", "RB", "AAA", "BBB", 6000, 15.0),
        row("W3", "WR", "AAA", "BBB", 6000, 15.0),
        row("W4", "WR", "AAA", "BBB", 6000, 15.0),
    ]
    names = [r["Name"] for r in edge]
    out = cons.dk_order(names, edge)
    assert [s for s, _ in out] == ["QB", "RB", "RB", "WR", "WR", "WR", "TE", "FLEX", "DST"]
    assert dict((n, s) for s, n in out)["W2"] == "FLEX"  # the latest kickoff holds the FLEX
    assert [n for s, n in out if s == "WR"] == ["W1", "W4", "W3"]
    assert cons.dk_order(names[:5], edge) == [("", n) for n in names[:5]]  # not a lineup: its own order


def test_the_consensus_table_is_in_roster_order_with_sams_caption(tmp_path):
    _, page = full_page(tmp_path)
    sec = section(page, "consensus")
    assert page_text(week_money_caption()) in page_text(sec)
    for table in re.findall(r"<h4>The field's lineup.*?</table>", sec, re.S):
        slots = re.findall(r"<tr><td>(QB|RB|WR|TE|FLEX|DST)</td>", table)
        assert slots == ["QB", "RB", "RB", "WR", "WR", "WR", "TE", "FLEX", "DST"]


def week_money_caption():
    from dfs import week_money

    assert week_money.CONSENSUS_CAPTION == (
        "The legal lineup with the highest total predicted ownership: CashOwn for double-ups, GppOwn for "
        "GPPs; our ownership model; DK's roster rules and salary cap."
    )
    return week_money.CONSENSUS_CAPTION


# -- item 14: a "how this is built" note on every table and chart --------------------------------------------


def test_table_requires_its_how_note():
    with pytest.raises(TypeError):
        week_page.table(["A"], [["1"]])  # type: ignore[call-arg]
    out = week_page.table(["A"], [["1"]], how="Counted.")
    assert 'data-how="Counted."' in out and 'tabindex="0"' in out


@pytest.mark.parametrize("served", [False, True])
def test_every_table_and_chart_on_the_page_has_a_how_note(tmp_path, served):
    calibration = {"fit": {"cash": {"scale": 1.0, "exponent": 0.5}, "gpp": {"scale": 1e5, "exponent": 0.5}},
                   "summary": {}, "held_out": []}  # fmt: skip
    builds = {
        "builds": [
            {"kind": "cash", "number": 1, "names": ["Qb Alpha"], "metrics": {"p_cash": 0.6}},
            {"kind": "gpp", "number": 2, "names": ["Qb Beta"], "metrics": {"p_gpp": 0.01}},
        ]
    }
    inp = inputs(
        tmp_path / "data",
        rows=rows_with_more_wrs(**OWN),
        entered=entered_record(),
        dup_calibration=calibration,
        suggest=builds,
    )
    page = week_page.render(inp, live=served, nonce="n") if served else render(inp)
    main = page[page.index("<main>") : page.index("</main>")]
    tables = [m.start() for m in re.finditer(r"<table\b", main)]
    assert len(tables) >= 6
    for at in tables:
        before = main[max(0, at - 1500) : at]
        card = main[main.rfind("<section", 0, at) : at]
        assert 'class="how"' in before[-700:] or 'class="how"' in card, main[at - 300 : at + 120]
    for chart in re.finditer(r'<section class="card chartcard" id="([^"]+)"', main):
        card = main[chart.start() : main.index("</section>", chart.start())]
        assert 'class="how"' in card, chart.group(1)
    assert "a.how" not in week_ui.JS and "h.classList.toggle('show')" in week_ui.JS  # a tap shows it


def test_the_static_page_still_has_no_live_parts(tmp_path):
    _, page = full_page(tmp_path)
    assert "/api/" not in page and "http://" not in page and "https://" not in page
    assert "dfs-token" not in page
    order = [
        page.index(x)
        for x in ('id="explorer-data"', 'id="chart-data"', 'id="game-data"', "window.dfsPool = pool")
    ]
    assert order == sorted(order) and page.index("window.dfsPool = pool") < page.index("const D = JSON.parse")


def test_week_ui_sends_nothing_anywhere():
    js = week_ui.JS
    for banned in ("fetch(", "XMLHttpRequest", "WebSocket", "import(", "eval("):
        assert banned not in js, banned


# -- Plan O items 3 and 4: colour by position, a caption on every table and chart ---------------------------


def test_the_colour_kinds_follow_the_compare_tab_and_leave_neutral_columns_plain():
    from dfs import week_shade

    k = week_shade.kinds()
    assert k["CalPts"] == k["Proj"] == k["Per $1K"] == k["Leverage"] == "good"
    assert k["Bust%"] == k["OppPosRank"] == k["Opp rank"] == "bad"  # 1 = the softest matchup
    assert k["CashOwn"] == k["DU own"] == k["GPP own"] == k["Own%"] == "own"
    assert k["PropsGap"] == "diff"
    for neutral in ("Salary", "Spread", "Team", "Position", "Kickoff", "Avail"):
        assert neutral not in k
    assert week_shade.OWN_HEX == "#FCD9A8" and week_shade.GOOD_HEX == "#A8DBB0"  # the sheet's palette
    assert "window.dfsShade" in week_shade.JS and '"CalPts": "good"' in week_shade.JS


@pytest.mark.parametrize("served", [False, True])
def test_every_table_and_chart_has_a_caption_with_its_data_date(tmp_path, served):
    synced = {"tffb": {"synced_at": "2026-11-04T15:00:00+00:00"}}
    inp = inputs(tmp_path / "data", rows=rows_with_more_wrs(**OWN), entered=entered_record(), manifest=synced)
    page = week_page.render(inp, live=served, nonce="n") if served else render(inp)
    main = page[page.index("<main>") : page.index("</main>")]
    assert week_page.CAP_ASOF not in page  # every placeholder filled
    for m in re.finditer(r'<div class="tbl">.*?</table></div>(<p class="cap">.*?</p>)', main, re.S):
        assert "Data:" in m.group(1), m.group(1)
    for chart in re.finditer(r'<section class="card chartcard" id="([^"]+)"', main):
        card = main[chart.start() : main.index("</section>", chart.start())]
        cap = re.search(r'<p class="cap">(.*?)</p>', card, re.S)
        assert cap and "Data:" in cap.group(1) and "From:" in cap.group(1), chart.group(1)
    value = main[main.index('id="value-map"') :]
    assert "Value = CalPts ÷ salary × 1,000" in value[: value.index("</section>")]
    board = main[main.index('id="board"') :]
    assert '<p class="cap">' in board[: board.index("</section>")]
