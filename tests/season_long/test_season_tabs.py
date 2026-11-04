from dfs.season_long import finder as fd
from dfs.season_long import season_tabs as st
from dfs.season_long import trade as tr

from . import synthetic as syn


def test_view_formulas_find_columns_by_name():
    formula = st.league_tab_rows()[-1][0]
    team = st.raw_letter(st.TEAM_COLUMNS, "Team")
    league = st.raw_letter(st.TEAM_COLUMNS, "League")
    assert f"{st.TEAMS_RAW}!{team}2:{team}," in formula
    assert f"{st.TEAMS_RAW}!{league}2:{league}=$B$1" in formula
    assert f", {st.LEAGUE_VIEW.index(st.LEAGUE_SORT) + 1}, TRUE)" in formula
    players = st.players_tab_rows()
    assert players[st.VIEW_HEADER_ROW - 1] == st.PLAYERS_VIEW
    assert f", {st.PLAYERS_VIEW.index(st.PLAYERS_SORT) + 1}, FALSE)" in players[-1][0]


def test_box_cells_follow_the_field_list():
    assert st.box_cell(st.BOX_FIELDS[0]) == f"B{st.BOX_FIRST_ROW}"
    rows = st.box_rows({"Get": "RB"})
    assert rows[1 + st.BOX_FIELDS.index("Get")] == ["Get", "RB"]
    assert st.IDEAS_HEADER_ROW > st.BOX_FIRST_ROW + len(st.BOX_FIELDS)


def test_tabs_from_a_synthetic_run(monkeypatch):
    v = syn.view()
    ev = syn.evaluator(v, monkeypatch)
    m = syn.market(v)
    ideas = fd.find_trades(v, ev, m, fd.Filters(), top=5)
    run = st.LeagueRun(v, ev, m, ideas, tr.free_move(v, ev, 1, tr.free_agents(v)), [st.MEDIAN_NOTE])
    teams = st.teams_frame([run])
    assert list(teams.columns) == st.TEAM_COLUMNS and len(teams) == 3 and (teams["Mine"] == "Y").sum() == 1
    players = st.players_frame([run])
    assert list(players.columns) == st.PLAYER_COLUMNS
    assert set(players.loc[players["Mine"] == "Y", "Name"]) == {"QB1", "RB1", "WR1", "WR2", "WR3"}
    overview = st.overview_rows([run])
    assert (
        overview[1] == st.OVERVIEW_HEADER and overview[2][0] == "Test" and overview[2][-1] == st.MEDIAN_NOTE
    )
    assert ["Player", "Pos", "Leagues", "Which"] in overview
    block = st.my_teams_block([run])
    assert block.rows[block.title_rows[0] - 1][0].startswith("Test: Team 1")
    rows = st.ideas_rows([run])
    assert rows[0] == st.IDEAS_HEADER and len(rows) == len(ideas) + 1


def test_text_that_sheets_would_parse_is_written_as_text():
    assert st.literal("8-6") == "'8-6" and st.literal("+33.4 value") == "'+33.4 value"
    assert st.literal("=SUM(A1)") == "'=SUM(A1)" and st.literal("-1.4/wk") == "'-1.4/wk"
    assert st.literal("Bourbon") == "Bourbon" and st.literal(12.5) == 12.5 and st.literal("124%") == "124%"
    frame = st.pd.DataFrame({"Record": ["8-6"], "Gain": ["+3.1"]})
    assert st.cells(frame) == [["Record", "Gain"], ["'8-6", "'+3.1"]]


def test_the_waivers_tab_puts_ir_moves_first_and_shows_the_spot_each_add_takes(monkeypatch):
    from dfs.season_long import roster_rules as rr
    from dfs.season_long import waivers as wv

    from . import roster_fixtures as fx

    monkeypatch.setattr(wv.sl, "fetch_transactions", lambda lid, w, final: [])
    v = fx.agile(reserve_slots=2)
    ev = syn.evaluator(v, monkeypatch)
    run = st.LeagueRun(v, ev, syn.market(v), [])
    run.ir = rr.ir_lines(v)
    run.pickups = wv.rank_pickups(v, ev, top=5)
    rows = st.waivers_block([run]).rows
    body = [r for r in rows[2:] if r and r[0] == "Test"]
    assert body[0][1] == "IR" and "move Tyler Loop (K, IR) to IR" in body[0][-1]
    kicker = next(r for r in body if len(r) > 2 and r[2] == "Ka'imi Fairbairn")
    assert kicker[5] == "Tyler Loop to IR (nobody dropped)"
    assert st.pickup_text(run).startswith("add ")


def test_the_drop_column_holds_a_bare_name(monkeypatch):
    from dfs.season_long import waivers as wv

    from . import roster_fixtures as fx

    monkeypatch.setattr(wv.sl, "fetch_transactions", lambda lid, w, final: [])
    v = fx.agile()
    ev = syn.evaluator(v, monkeypatch)
    run = st.LeagueRun(v, ev, syn.market(v), [])
    run.pickups = wv.rank_pickups(v, ev, top=5)
    row = next(r for r in st.waivers_block([run]).rows if len(r) > 2 and r[2] == "Ka'imi Fairbairn")
    assert row[5] == "Tyler Loop"
