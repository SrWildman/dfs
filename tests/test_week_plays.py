"""Plan I: the Week page's Plays, Slate and What-changed sections, the contest plan split between this week
and last, the season-long urgent block, the Build / Late swap / pool-tick buttons, and the day ordering.

Every test builds its own data under `tmp_path` (conftest keeps the suite off the real `data/`)."""

import csv
import html
import re
from dataclasses import replace
from datetime import datetime

import pytest

from dfs import commands_doc, week_inputs, week_jobs, week_live, week_page, week_plays

SAT = datetime(2026, 11, 7, 10, 0).astimezone()
WED = datetime(2026, 11, 4, 10, 0).astimezone()
SUN = datetime(2026, 11, 8, 10, 0).astimezone()
MON = datetime(2026, 11, 9, 10, 0).astimezone()


def row(name, pos, team, opp, sal, cal, **kw):
    base = {
        "Id": f"id-{name}",
        "Name": name,
        "Position": pos,
        "Team": team,
        "Opp": opp,
        "Salary": str(sal),
        "ProjPts": str(cal),
        "CalPts": str(cal),
        "Floor": str(round(cal * 0.6, 1)),
        "Ceiling": str(round(cal * 1.6, 1)),
        "CeilM": str(round(cal * 1.5, 1)),
        "Hit3x%": "40",
        "Boom%": "10",
        "Bust%": "30",
        "Avail": "",
        "Edge": "",
        "Kickoff": "Sun 1:00 pm ET",
        "GameStart": "2026-11-08T18:00:00Z",
        "GameID": "2026_09_" + "_".join(sorted([team, opp])),
        "OverUnder": "47.5",
        "Spread": "-3.0",
        "TotMove": "0.0",
        "SpdMove": "0.0",
        "Roof": "outdoors",
        "Wind": "6.0",
        "WX": "",
        "OppPosRank": "16",
        "Snap% L5": "0.8",
        "Tgt L5": "6.0",
        "Car L5": "",
        "DK L5": "15.0",
        "xFP/G": "14.0",
        "CashOwn": "",
        "GppOwn": "",
    }
    base.update({k: str(v) for k, v in kw.items()})
    return base


def slate_rows(**own):
    """Two games. DAL (-6) @ ... is the high total; the second is low. Names are fictional enough to grep."""
    hi = {"OverUnder": "52.5", "TotMove": "2.5"}
    lo = {"OverUnder": "38.5"}
    return [
        row("Qb Alpha", "QB", "DAL", "IND", 6000, 24.0, Spread="-6.0", **hi, **own.get("alpha", {})),
        row("Qb Beta", "QB", "IND", "DAL", 5000, 19.0, Spread="6.0", **hi, **own.get("beta", {})),
        row("Wr Value", "WR", "DAL", "IND", 4000, 14.0, Spread="-6.0", **hi, **own.get("value", {})),
        row(
            "Wr Star",
            "WR",
            "DAL",
            "IND",
            9000,
            21.0,
            Spread="-6.0",
            Avail="Q",
            **hi,
            **own.get("star", {}),
        ),
        row("Wr Out", "WR", "IND", "DAL", 3000, 18.0, Spread="6.0", Avail="OUT", **hi),
        row("Te Fade", "TE", "IND", "DAL", 4500, 11.0, Spread="6.0", Edge="FADE↓", **hi),
        row("Te Plain", "TE", "DAL", "IND", 3500, 8.0, Spread="-6.0", **hi),
        row("Rb Warn", "RB", "NE", "NYJ", 5500, 13.0, Spread="-1.5", Edge="USAGE↓ Proj ▼", **lo),
        row("Rb Fine", "RB", "NYJ", "NE", 5000, 12.0, Spread="1.5", **lo),
        row("Cowboys", "DST", "DAL", "IND", 3000, 7.0, Spread="-6.0", **hi),
    ]


def write_csv(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def inputs(d, rows=None, now=WED, **fields):
    if rows is not None:
        write_csv(d / "current" / "edge.csv", rows)
    return replace(week_inputs.gather(2026, 9, now, d), config={}, **fields)


def page_text(page):
    return html.unescape(re.sub(r"<[^>]+>", " ", page))


def section(page, section_id):
    m = re.search(rf'<section class="card[^"]*" id="{section_id}">.*?</section>', page, re.S)
    assert m, f"no section {section_id}"
    return m.group(0)


def render(inp):
    return week_page.render(inp, registered=set(commands_doc.all_commands()))


# -- Plays ------------------------------------------------------------------------------------------------


def test_plays_value_is_calpts_per_thousand_with_calpts_tiebreak_and_skips_the_out(tmp_path):
    view = week_plays.plays(slate_rows())
    wr = [p.name for p in view.value["WR"]]
    assert wr == ["Wr Value", "Wr Star"]  # 3.5 and 2.33 per $1K; Wr Out is OUT and absent
    assert "Wr Out" not in wr
    tie = [
        row("A", "RB", "X", "Y", 5000, 10.0),
        row("B", "RB", "X", "Y", 5000, 10.0),
        row("C", "RB", "X", "Y", 4000, 8.0),
    ]
    tie[1]["CalPts"] = "10.0"
    assert [p.name for p in week_plays.plays(tie).value["RB"]] == [
        "A",
        "B",
        "C",
    ]  # all 2.0 per $1K: CalPts breaks the tie


def test_questionable_stays_on_the_list_tagged(tmp_path):
    page = render(inputs(tmp_path, slate_rows()))
    star = re.search(r"Wr Star.{0,200}", section(page, "plays"), re.S).group(0)
    assert "Questionable" in star


def test_ownership_not_published_says_so_instead_of_blank(tmp_path):
    page = render(inputs(tmp_path, slate_rows()))
    plays = section(page, "plays")
    assert "Ownership publishes ~Fri/Sat" in plays
    assert plays.count("publishes ~Fri/Sat") >= 4  # the banner, the headers and every ownership cell
    assert "chalk: play or fade on purpose" not in plays


def test_published_cash_ownership_tags_30_percent_chalk(tmp_path):
    rows = slate_rows(alpha={"CashOwn": "0.34", "GppOwn": "0.30"}, beta={"CashOwn": "0.05", "GppOwn": "0.04"})
    for r in rows:
        if r["Name"] == "Wr Value":
            r["CashOwn"], r["GppOwn"] = "0.31", "0.10"
        if r["Name"] == "Wr Star":
            r["CashOwn"], r["GppOwn"] = "0.12", "0.05"
    page = render(inputs(tmp_path, rows))
    plays = section(page, "plays")
    assert "publishes ~Fri/Sat" not in plays
    assert plays.count("chalk: play or fade on purpose") >= 1  # Qb Alpha, a cash core row
    view = week_plays.plays(rows)
    assert next(p for p in view.cash["QB"] if p.name == "Qb Alpha").chalk is True
    assert next(p for p in view.value["QB"] if p.name == "Qb Beta").chalk is False


def test_gpp_leverage_once_gpp_ownership_is_out(tmp_path):
    rows = [
        row("Cheap Own", "WR", "A", "B", 5000, 10.0, **{"Boom%": "20", "GppOwn": "0.30"}),
        row("Low Own", "WR", "A", "B", 5000, 10.0, **{"Boom%": "19", "GppOwn": "0.02"}),
        row("Mid Own", "WR", "A", "B", 5000, 10.0, **{"Boom%": "18", "GppOwn": "0.10"}),
        row("Bad", "WR", "A", "B", 5000, 10.0, **{"Boom%": "2", "GppOwn": "0.01"}),
    ]
    gpp = week_plays.plays(rows).gpp["WR"]
    assert gpp[0].name == "Low Own" and gpp[0].lev is not None and gpp[0].lev > gpp[-1].lev  # leverage first
    assert "Bad" not in [p.name for p in gpp]  # bottom half of Boom% never listed
    before = week_plays.plays([{**r, "GppOwn": ""} for r in rows]).gpp["WR"]
    assert before[0].name == "Cheap Own" and before[0].lev is None  # plain Boom% order until it publishes


def test_fades_are_the_fade_chip_and_other_chips_are_shadow(tmp_path):
    page = render(inputs(tmp_path, slate_rows()))
    plays = section(page, "plays")
    fades = plays[plays.index("Fades") :]
    assert "Te Fade" in fades and "FADE↓ Proven" in fades
    assert "Rb Warn" in plays[plays.index("Other warning chips") :]
    assert "USAGE↓ Shadow" in plays
    view = week_plays.plays(slate_rows())
    assert [p.name for p in view.fades] == ["Te Fade"]
    assert [p.name for p in view.shadow] == ["Rb Warn"]


def test_each_play_has_a_football_why_game_and_kickoff(tmp_path):
    rows = slate_rows()
    rows[0].update({"OppPosRank": "3", "Tgt L5": ""})
    view = week_plays.plays(rows)
    alpha = next(p for p in view.cash["QB"] if p.name == "Qb Alpha")
    assert "soft matchup" in alpha.why and "high-scoring game" in alpha.why and "40% to reach 3x" in alpha.why
    assert alpha.game == "DAL -6 vs IND, total 52.5" and alpha.kickoff


def test_plays_not_run_without_a_sync(tmp_path):
    plays = section(render(inputs(tmp_path)), "plays")
    assert "Not run yet" in plays and "dfs sync" in plays


def test_pool_players_are_marked_not_offered_a_tick(tmp_path):
    inp = inputs(tmp_path, slate_rows(), pool={"Qb Alpha": "Both"})
    plays = section(render(inp), "plays")
    assert "in pool (Both)" in plays


# -- Slate ------------------------------------------------------------------------------------------------


def test_slate_sorts_by_total_marks_top_games_and_big_moves(tmp_path):
    games = week_plays.slate(slate_rows())
    assert [g.key for g in games] == ["2026_09_DAL_IND", "2026_09_NE_NYJ"]
    top = games[0]
    assert top.top and top.moved and top.tot_move == 2.5
    assert top.implied == {"DAL": 29.25, "IND": 23.25}  # (52.5 -/+ 6) / 2
    assert top.favorite == "DAL" and top.spread == -6.0
    assert not games[1].moved
    assert ("Wr Star", "WR", "DAL", "Q") in top.injuries  # a likely starter, Questionable
    assert all(i[0] != "Wr Out" or i[3] == "OUT" for i in top.injuries)
    page = render(inputs(tmp_path, slate_rows()))
    slate = section(page, "slate")
    assert "DAL @ IND" in slate and "top 3 total" in slate and "Wr Star (WR Questionable)" in slate
    assert "Wr Out (WR OUT)" in slate and 'class="bad"' in slate  # the +2.5 total move in red


def test_slate_only_lists_likely_starters_not_ir_or_backups(tmp_path):
    rows = [
        row("Starter WR", "WR", "A", "B", 8000, 15.0, Avail="Q"),
        row("Backup WR1", "WR", "A", "B", 4000, 5.0),
        row("Backup WR2", "WR", "A", "B", 3500, 4.0),
        row("Backup WR3", "WR", "A", "B", 3000, 3.0),
        row("Deep Backup WR", "WR", "A", "B", 2500, 2.0, Avail="Q"),
        row("IR Star", "WR", "A", "B", 9500, 18.0, Avail="IR"),
    ]
    (game,) = week_plays.slate(rows)
    assert [i[0] for i in game.injuries] == ["Starter WR"]


def test_slate_without_an_opening_snapshot_says_how_to_get_one(tmp_path):
    rows = slate_rows()
    for r in rows:
        r["TotMove"] = r["SpdMove"] = ""
    slate = section(render(inputs(tmp_path, rows)), "slate")
    assert "no opening yet" in slate and "dfs odds snapshot" in slate


# -- What changed -----------------------------------------------------------------------------------------


def write_prev(d, rows, stamp="20261103T145049Z", newer=None):
    write_csv(d / "raw" / "edge" / f"{stamp}.csv", rows)
    write_csv(d / "raw" / "edge" / "20261104T042651Z.csv", newer or rows)


def test_changes_lists_status_calpts_new_outs_and_line_moves(tmp_path):
    before = slate_rows()
    now = slate_rows()
    for r in now:
        if r["Name"] == "Qb Alpha":
            r["Avail"] = "Q"  # his pool player got a tag
        if r["Name"] == "Wr Value":
            r["CalPts"] = "9.5"  # -4.5
            r["Avail"] = "OUT"  # a new OUT
        if r["Name"] in ("Rb Fine", "Rb Warn"):
            r["OverUnder"] = "36.5"  # -2 on the second game's total
        if r["Name"] == "Qb Beta":
            r["CalPts"] = "20.0"  # +1.0: not a move
    write_prev(tmp_path, before, newer=now)
    write_csv(tmp_path / "current" / "edge.csv", now)
    inp = inputs(tmp_path, pool={"Qb Alpha": "Both"})
    diff = week_plays.changes(inp.edge, inp.prev_edge, {"Qb Alpha"})
    assert [c.text for c in diff.status] == ["Qb Alpha (QB DAL): healthy to Questionable"]
    assert any("Wr Value" in c.text and "-4.5" in c.text for c in diff.calpts)
    assert not any("Qb Beta" in c.text for c in diff.calpts)
    assert any("Wr Value" in c.text and "healthy to OUT" in c.text for c in diff.new_outs)
    assert any("NYJ" in c.text or "NE" in c.text for c in diff.lines)
    text = page_text(section(render(inp), "changes"))
    assert "Qb Alpha" in text and "healthy to Questionable" in text and "CalPts 14.0 to 9.5" in text


def test_changes_status_only_for_players_in_the_pool_or_builds(tmp_path):
    before, now = slate_rows(), slate_rows()
    for r in now:
        if r["Name"] == "Qb Alpha":
            r["Avail"] = "Q"
    write_prev(tmp_path, before, newer=now)
    write_csv(tmp_path / "current" / "edge.csv", now)
    inp = inputs(tmp_path)
    assert week_plays.changes(inp.edge, inp.prev_edge, set()).status == []  # not his player
    assert len(week_plays.changes(inp.edge, inp.prev_edge, {"Qb Alpha"}).status) == 1


def test_changes_includes_builds_names_as_his(tmp_path):
    inp = inputs(
        tmp_path,
        slate_rows(),
        suggest={"builds": [{"kind": "cash", "names": ["Wr Star"]}]},
        pool={"Qb Alpha": "Both"},
    )
    assert week_page.mine_names(inp) == {"Wr Star", "Qb Alpha"}


def test_changes_not_run_without_an_earlier_sync_or_with_another_weeks_slate(tmp_path):
    write_csv(tmp_path / "current" / "edge.csv", slate_rows())
    assert "Not run yet" in section(render(inputs(tmp_path)), "changes")  # one snapshot at most
    last_week = [{**r, "GameID": "2026_08_" + r["GameID"][8:]} for r in slate_rows()]
    write_prev(tmp_path, last_week, newer=slate_rows())
    inp = inputs(tmp_path)
    assert inp.prev_edge is None  # never diff against last week's games
    assert "no earlier sync of this week" in page_text(section(render(inp), "changes")).lower()


# -- the contest plan: this week here, last week under Last week --------------------------------------------


def contest_plan(d):
    week_inputs.save_input(
        "contest_plan",
        {
            "line": "spent $179 vs plan $150; over in: Showdown",
            "caps": {"Showdown": 5},
            "spent": {"Showdown": 7},
            "budget": 150,
        },
        season=2026,
        week=9,
        data_dir=d,
    )
    write_csv(
        d / "contests" / "contest_log.csv",
        [
            {
                "Sport": "NFL",
                "Season": "2026",
                "Week": "8",
                "Format": "Showdown",
                "Entry_Fee": "$7.00",
                "Winnings_Non_Ticket": "$0.00",
            }
        ],
    )


def test_this_weeks_plan_has_no_last_week_spend_and_last_week_has_it(tmp_path):
    contest_plan(tmp_path)
    page = render(inputs(tmp_path))
    plan, last = section(page, "contest-plan"), section(page, "last-week")
    assert "Tier A" in plan and "Weekly Budget $150" in plan  # this week's plan, in dollars
    assert "Spent last week" not in plan and "spent $179" not in plan and ">over<" not in plan
    assert "spent $179 vs plan $150" in last and "Spent against the plan" in last and ">over<" in last


# -- season-long ------------------------------------------------------------------------------------------


def season(d, alerts):
    week_inputs.save_season_league(
        {
            "league": "Choppa",
            "headline": ["Choppa (Get To The Choppa): survive 78%", "  free move first: add Caleb Williams"],
            "alerts": alerts,
            "pickups": ["add J.J. McCarthy (QB)"],
            "ideas": [],
        },
        data_dir=d,
        now=WED,
    )


def test_choppa_with_no_qb_is_urgent_and_red(tmp_path):
    season(tmp_path, ["SWAP QB (empty): nobody in this slot -> start Caleb Williams (free agent, 18.3)"])
    sec = section(render(inputs(tmp_path)), "season")
    assert 'class="urgent"' in sec
    text = page_text(sec)
    assert "Choppa" in text and "no QB in your lineup" in text and "Caleb Williams (free agent, 18.3)" in text
    assert "Top move:" in text and "add Caleb Williams" in text
    assert 'class="bad"' in sec  # the alert line itself is red too


def test_season_with_only_look_alerts_is_not_urgent(tmp_path):
    season(tmp_path, ["LOOK RB Someone: Questionable: check before kickoff"])
    sec = section(render(inputs(tmp_path)), "season")
    assert 'class="urgent"' not in sec and "Nothing urgent" in sec


def test_urgent_text_for_other_swaps():
    assert week_page.urgent_text("SWAP K Tyler Loop: Injured Reserve (ESPN) -> start X") == (
        "K Tyler Loop: Injured Reserve (ESPN) -> start X"
    )


# -- day ordering -----------------------------------------------------------------------------------------


def ids_in_order(page):
    ids = re.findall(r'<section class="card[^"]*" id="([a-z-]+)">', page.split('<h2 class="sec">')[1])
    return [i for i in ids if i != "tools"]


@pytest.mark.parametrize(
    "day,lead",
    [
        (1, ["plays", "slate", "changes", "contest-plan", "season", "last-week"]),
        (2, ["plays", "slate", "changes", "contest-plan", "season", "last-week"]),
        (4, ["plays", "slate", "changes", "contest-plan", "season", "last-week"]),
        (5, ["contest-plan", "plays", "cash", "gpp"]),
        (6, ["sunday", "changes", "cash", "gpp"]),
    ],
)
def test_page_is_ordered_by_day(tmp_path, day, lead):
    now = datetime(2026, 11, 2 + day, 10, 0).astimezone()
    assert now.weekday() == day
    order = ids_in_order(render(inputs(tmp_path, now=now)))
    assert order[: len(lead)] == lead
    assert sorted(order) == sorted(week_page.SECTION_IDS)  # nothing dropped on any day


def test_monday_leads_with_last_week():
    assert week_page.section_order(0)[0] == "last-week"


def test_nav_follows_the_page_order(tmp_path):
    page = render(inputs(tmp_path, now=SUN))
    nav = re.findall(r'<a href="#([a-z-]+)">', page.split("<nav>")[1].split("</nav>")[0])
    assert nav[1:4] == ["sunday", "changes", "cash"]


# -- buttons ----------------------------------------------------------------------------------------------


def live(page, **kw):
    return week_live.augment(page, token="tok", nonce="n", read_only=kw.get("read_only", False))


def test_static_page_has_no_tick_boxes_but_says_the_phrase(tmp_path):
    page = render(inputs(tmp_path, slate_rows()))
    assert 'class="pool-tick"' not in page and "<input" not in page
    assert "add Name, Name to my pool" in page and "dfs pool add" in page
    assert "<button" not in page


def test_served_page_has_a_tick_box_per_play_and_the_add_bar(tmp_path):
    page = live(render(inputs(tmp_path, slate_rows())))
    assert 'class="pool-tick" data-name="Qb Alpha"' in page
    assert 'id="pool-add"' in page and "Add ticked to pool" in page
    assert "add Name, Name to my pool" not in page  # the static hint is gone
    assert "pool-slot" not in page


def test_tick_box_runs_the_pool_command_through_the_job_runner(tmp_path):
    js = week_live.JS
    assert "startJob('dfs pool add', {names: names.join(', ')})" in js
    assert "/api/jobs/start" in js
    spec = week_jobs.forms()["dfs pool add"]
    assert spec["writes"] is True  # takes the run lock: refused while another writer runs
    argv = week_jobs.build_argv("dfs pool add", {"names": "Qb Alpha, Ja'Marr Chase"})
    assert argv == ["pool", "add", "Qb Alpha", "Ja'Marr Chase"]
    with pytest.raises(week_jobs.Refused):
        week_jobs.build_argv("dfs pool add", {"names": "--all"})


def test_empty_cash_gpp_and_sunday_get_buttons_only_when_served(tmp_path):
    inp = inputs(tmp_path, slate_rows())
    static = render(inp)
    for sid in ("cash", "gpp", "sunday"):
        assert "<button" not in section(static, sid)
    served = live(static)
    cash, gpp, sunday = (section(served, s) for s in ("cash", "gpp", "sunday"))
    assert 'data-command="dfs lineups suggest">Build</button>' in cash
    assert 'data-command="dfs lineups suggest">Build</button>' in gpp
    assert 'data-command="dfs lineups late-swap --refresh-injuries">Late swap</button>' in sunday
    assert "button.runbtn" in week_live.JS


def test_build_button_gone_once_builds_exist(tmp_path):
    inp = inputs(
        tmp_path,
        slate_rows(),
        suggest={
            "round": 1,
            "builds": [{"kind": "cash", "number": 1, "names": ["Qb Alpha"], "metrics": {"p_cash": 0.7}}],
        },
    )
    assert "runbtn-slot" not in section(render(inp), "cash")


def test_ticks_survive_a_section_refresh_in_the_script():
    assert "restoreTicks();" in week_live.JS and "const ticked = new Set()" in week_live.JS


def test_page_reads_only_the_given_data_dir(tmp_path):
    inp = inputs(tmp_path, slate_rows())
    assert inp.data_dir == tmp_path
    assert week_plays.plays(inp.edge, inp.pool).players == 9  # everyone healthy and priced
