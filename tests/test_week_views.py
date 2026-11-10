"""Plan O2's views on the Week page (`week_views`): the view bar and where each block shows, the overview,
the usage data, the injury report, our model against the sources with the calls and their two records, and
the compare spec. No browser: `tests/test_week_ui_browser.py` drives them in Chromium."""

import re

from test_week_plays import inputs, row, slate_rows

from dfs import week_explorer, week_page, week_views

CALLS = {
    "versions": [
        {
            "at": "2026-11-08T17:48:22+00:00",
            "calls": [
                {
                    "name": "Qb Alpha",
                    "position": "QB",
                    "team": "DAL",
                    "salary": 6000,
                    "calpts": 24.0,
                    "cash": "AVOID",
                    "cash_score": -0.4,
                    "cash_reason": "Overpriced for his Cash %.",
                    "gpp": "FADE",
                    "gpp_score": -0.3,
                    "gpp_reason": "Over-owned for his ceiling.",
                    "bump": 0.0,
                    "bump_from": "",
                },
                {
                    "name": "Qb Beta",
                    "position": "QB",
                    "team": "IND",
                    "salary": 5000,
                    "calpts": 19.0,
                    "cash": "CORE",
                    "cash_score": 0.5,
                    "cash_reason": "Best Cash % for the price.",
                    "gpp": "NEUTRAL",
                    "gpp_score": 0.0,
                    "gpp_reason": "",
                    "bump": 0.0,
                    "bump_from": "",
                },
                {
                    "name": "Wr Value",
                    "position": "WR",
                    "team": "DAL",
                    "salary": 4000,
                    "calpts": 14.0,
                    "cash": "NEUTRAL",
                    "cash_score": 0.1,
                    "cash_reason": "",
                    "gpp": "LEVERAGE",
                    "gpp_score": 0.2,
                    "gpp_reason": "",
                    "bump": 2.4,
                    "bump_from": "Wr Out",
                },
            ],
        }
    ]
}
BACKTEST = {
    "weeks": [2, 4, 5, 6, 7, 8],
    "gpp": {"weeks": [4, 5, 6, 7, 8]},
    "cash hit AVOID": [0.6039, 202],
    "gpp hit FADE": [0.7619, 63],
}


def _inp(tmp_path, rows=None, **fields):
    rows = rows if rows is not None else slate_rows()
    for r in rows:
        r.setdefault("NameKey", r["Name"].lower())
        r.setdefault("SleeperPts", r["CalPts"])
    return inputs(tmp_path, rows, **{"calls": CALLS, "calls_record": None, **fields})


def _ids(html):
    return set(re.findall(r'\bid="([^"]+)"', html))


def test_every_block_names_real_views_and_no_element_id_shadows_a_view_hash(tmp_path):
    for place, views in week_views.PLACES.items():
        assert views.split() and set(views.split()) <= set(week_views.VIEW_NAMES), place
        assert "everything" in views.split() or place == "overview", place  # Everything is the full page
    html = week_page.render(_inp(tmp_path))
    assert 'id="views"' in html and html.index('id="brief"') < html.index(
        'id="views"'
    )  # the brief sits above
    assert not _ids(html) & set(week_views.VIEW_NAMES)  # #usage must open the view, not jump to an element
    for name in week_views.VIEW_NAMES:
        assert f'href="#{name}" data-view="{name}"' in html
    for sid in ("overview-card", "usage-card", "injury-card", "model-card"):
        assert f'id="{sid}"' in html
    assert 'id="compare-spec"' in html and "window.dfsCompare" in html


def test_the_overview_has_a_row_per_position_and_dedupes_a_players_fades(tmp_path):
    inp = _inp(tmp_path)
    from dfs import week_board as wb

    cells = week_views.overview_rows(inp, wb.board(inp))
    assert list(cells) == list(week_views.POSITIONS)
    qb_fades = " ".join(cells["QB"]["fades"])
    assert qb_fades.count('data-name="Qb Alpha"') == 1  # AVOID and FADE on one line
    assert "AVOID cash" in qb_fades and "FADE GPP" in qb_fades
    assert "FADE↓" in " ".join(cells["TE"]["fades"])  # the signal, from the board
    assert "+2.4" in " ".join(cells["WR"]["injury"]) and "Wr Out" in " ".join(cells["WR"]["injury"])
    html = week_views.overview_section(inp, wb.board(inp), wb.pool_panel(inp, wb.board(inp), {}))
    assert "Top 3 games" in html and 'data-game="2026_09_DAL_IND"' in html
    assert "Your pool: 0 players" in html


def test_usage_data_joins_each_slate_player_by_name_and_team(tmp_path):
    games = [
        {
            "NameKey": "wr value",
            "Name": "Wr Value",
            "Team": "DAL",
            "Position": "WR",
            "Wk": w,
            "Opp": "NYG",
            "Snap%": 0.8,
            "Tgt": 7,
            "Car": 0,
            "Tgt%": 0.25,
            "Air%": 0.3,
            "WOPR": 0.585,
            "RZ": 1,
            "HVT": 0,
            "DK": 15,
        }
        for w in (2, 1)
    ] + [{"NameKey": "wr value", "Name": "Wr Value", "Team": "KC", "Position": "WR", "Wk": 1, "Tgt": 1}]
    teams = [
        {
            "Team": "DAL",
            "Wk": 1,
            "Opp": "NYG",
            "Plays": 60,
            "Pass%": 0.6,
            "PROE": 1.0,
            "Pace": 30,
            "RZTrips": 3,
        },
        {"Team": "SEA", "Wk": 1, "Plays": 50},
    ]
    data = week_views.usage_data(_inp(tmp_path, usage_weeks=games, team_weeks=teams))
    got = data["players"]["id-Wr Value"]
    assert [g[0] for g in got] == [1, 2]  # oldest first, only his DAL games
    assert dict(zip(data["fields"], got[0], strict=True))["Tgt%"] == 0.25
    assert list(data["teams"]) == ["DAL"]  # slate teams only
    html = week_views.usage_section(_inp(tmp_path))
    assert "Not run yet" in html and "dfs sync" in html


def test_the_injury_report_lists_each_team_with_who_benefits(tmp_path):
    teams = week_views.injury_teams(_inp(tmp_path))
    assert {x.name for x in teams["DAL"]} == {"Wr Star"}
    out = next(x for x in teams["IND"] if x.name == "Wr Out")
    assert out.status == "OUT" and [b.name for b in out.benefits] == ["Wr Value"]
    html = week_views.injury_section(_inp(tmp_path))
    assert html.index(">DAL <") < html.index(">IND <") or html.index(">IND <") < html.index(">DAL <")
    assert 'data-name="Wr Value"' in html and "+2.4" in html


def test_model_vs_sources_ranks_disagreements_and_leads_with_who_not_to_play(tmp_path):
    rows = slate_rows()
    rows[0].update({"SleeperPts": "18.0", "EspnPts": "19.0"})  # Qb Alpha: CalPts 24 vs a median of 19
    by_pos = week_views.compared(_inp(tmp_path, rows).edge)
    top = by_pos["QB"][0]
    assert top.row["Name"] == "Qb Alpha" and top.diff == 5.0 and top.consensus == 19.0
    record = week_views.record_of(
        [
            {"week": 9, "cash": "AVOID", "cash_hit": "1.0", "gpp": "NEUTRAL", "gpp_hit": ""},
            {"week": 9, "cash": "AVOID", "cash_hit": "0.0", "gpp": "FADE", "gpp_hit": "1.0"},
        ],
        BACKTEST,
    )
    assert record["live"]["cash"]["AVOID"] == [1, 2] and record["live_weeks"] == [9]
    html = week_views.model_section(_inp(tmp_path, rows, calls_record=record))
    assert (
        html.index('data-label="AVOID"') < html.index('data-label="FADE"') < html.index('data-label="CORE"')
    )
    assert "backtest 60% of 202 (Weeks 2, 4-8) · live 1 of 2 (50%)" in html
    assert "backtest 76% of 63 (Weeks 4-8)" in html  # the GPP labels: the weeks with GPP standings
    none_yet = week_views.model_section(_inp(tmp_path, rows, calls_record={"backtest": BACKTEST, "live": {}}))
    assert "live: none yet, it starts at the Week 9 close (0 of 0)" in none_yet  # never the backtest's weeks


def test_the_compare_spec_reads_columns_the_explorer_has():
    have = set(week_explorer.columns()) | {"Implied"}
    rows = [r for s in week_views.compare_spec() for r in s["rows"]]
    assert rows and all(r["col"] in have for r in rows)
    better = {r["col"]: r["better"] for r in rows}
    assert better["Bust%"] == "low" and better["Salary"] is None and better["CalPts"] == "high"


def test_a_traded_player_keeps_his_games_when_only_his_name_matches(tmp_path):
    rows = slate_rows() + [row("Wr Moved", "WR", "DAL", "IND", 4100, 9.0)]
    games = [{"NameKey": "wr moved", "Name": "Wr Moved", "Team": "NYJ", "Position": "WR", "Wk": 1, "Tgt": 5}]
    data = week_views.usage_data(_inp(tmp_path, rows, usage_weeks=games))
    assert "id-Wr Moved" in data["players"]


def test_contradicted_and_chalk_avoid_labels_are_held_back_on_the_overview_and_the_model_view(tmp_path):
    """M1.6, until the new labels clear the bar: no cash AVOID on a 30%+ CashOwn player and no label its own
    reason contradicts; the page says "no call: label under review" instead, and the news chips stay."""
    import copy

    calls = copy.deepcopy(CALLS)
    held = [
        {  # Week 9's JSN: AVOID although his reason says he is ahead of Puka
            "name": "Qb Alpha",
            "position": "QB",
            "team": "DAL",
            "salary": 6000,
            "calpts": 24.0,
            "cash_own": 0.15,
            "cash": "AVOID",
            "cash_score": -0.5,
            "cash_reason": "Costs -5.4 points of Cash % against Qb Beta ($5,000) at the same price.",
            "gpp": "FADE",
            "gpp_score": -0.3,
            "gpp_reason": "Over-owned for his ceiling: 8% owned, in 12% of simulated top-1% lineups.",
            "bump": 0.0,
            "bump_from": "",
        },
        {  # heavy chalk AVOID, reason consistent: held back anyway
            "name": "Qb Beta",
            "position": "QB",
            "team": "IND",
            "salary": 5000,
            "calpts": 19.0,
            "cash_own": 0.49,
            "cash": "AVOID",
            "cash_score": -0.9,
            "cash_reason": "Fading chalk: 49% of the double-up plays him, but X ($5,200) adds 5.4 more "
            "points of Cash % at the price.",
            "gpp": "NEUTRAL",
            "gpp_score": 0.0,
            "gpp_reason": "",
            "bump": 0.0,
            "bump_from": "",
        },
    ]
    calls["versions"][0]["calls"] = held + calls["versions"][0]["calls"][2:]
    inp = _inp(tmp_path, calls=calls)
    from dfs import plays
    from dfs import week_board as wb

    qb = " ".join(week_views.overview_rows(inp, wb.board(inp))["QB"]["fades"])
    assert ">AVOID cash<" not in qb and ">FADE GPP<" not in qb
    assert qb.count(plays.HOLD_TEXT) == 3  # Alpha's two labels, Beta's AVOID
    html = week_views.model_section(inp)
    avoid = html[html.index('data-label="AVOID"') : html.index('data-label="FADE"')]
    assert "Costs -5.4" not in avoid and "Fading chalk" not in avoid
    assert avoid.count(plays.HOLD_TEXT) == 2 and "2 under review" in avoid
    page = week_page.render(inp)
    assert 'id="news-data"' in page  # the news chips' data is still on the page
