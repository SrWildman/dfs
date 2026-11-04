"""The Week page: every section renders from fixture outputs (or says "not run yet"), the day logic, and the
tool list covers every registered command and every phrase."""

import csv
import html
import json
import re
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from dfs import commands_doc, runlog, week_inputs, week_page, week_today, week_tools
from dfs.cli import app

SAT = datetime(2026, 11, 7, 10, 0).astimezone()  # a Saturday, local time; Week 9 of 2026
SECTION_IDS = ["contest-plan", "cash", "gpp", "unknowns", "sunday", "season", "last-week"]


def write_json(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload))


def write_csv(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def build(names, kind, number, p_cash=0.7, p_gpp=0.02, title=""):
    return {
        "number": number,
        "kind": kind,
        "title": title or f"{kind} build {number}",
        "names": names,
        "metrics": {"p_cash": p_cash, "p_gpp": p_gpp, "salary": 49900},
    }


def fixtures(d, *, now=SAT):
    """A full set of the outputs the page reads, under data dir `d` (Week 9 of 2026, a Saturday)."""
    sync = (now - timedelta(hours=2)).astimezone(UTC).isoformat()
    write_json(
        d / "manifest.json",
        {
            "edge": {"error": None, "rows": 5, "synced_at": sync},
            "tffb": {"error": "login expired", "rows": 0, "synced_at": sync},
        },
    )
    edge = [
        {
            "Name": "Dak Prescott",
            "Position": "QB",
            "Team": "DAL",
            "Salary": "6000",
            "ProjPts": "20",
            "CalPts": "20",
            "Avail": "",
            "Kickoff": "Sun 1:00 pm ET",
            "GameStart": "2026-11-08T13:00:00Z",
            "CashOwn": "0.12",
        },
        {
            "Name": "Ja'Marr Chase",
            "Position": "WR",
            "Team": "CIN",
            "Salary": "9000",
            "ProjPts": "21",
            "CalPts": "21",
            "Avail": "",
            "Kickoff": "Sun 1:00 pm ET",
            "GameStart": "2026-11-08T13:00:00Z",
            "CashOwn": "0.41",
        },
        {
            "Name": "Wan'Dale Robinson",
            "Position": "WR",
            "Team": "NYG",
            "Salary": "5000",
            "ProjPts": "15",
            "CalPts": "15",
            "Avail": "",
            "Kickoff": "Sun 1:00 pm ET",
            "GameStart": "2026-11-08T13:00:00Z",
            "CashOwn": "0.33",
        },
        {
            "Name": "Puka Nacua",
            "Position": "WR",
            "Team": "LAR",
            "Salary": "8800",
            "ProjPts": "19",
            "CalPts": "19",
            "Avail": "Q",
            "Kickoff": "Sun 4:25 pm ET",
            "GameStart": "2026-11-08T16:25:00Z",
            "CashOwn": "0.2",
        },
        {
            "Name": "Hurt Guy",
            "Position": "RB",
            "Team": "SEA",
            "Salary": "4000",
            "ProjPts": "9",
            "CalPts": "9",
            "Avail": "OUT",
            "Kickoff": "Sun 4:05 pm ET",
            "GameStart": "2026-11-08T16:05:00Z",
            "CashOwn": "0.01",
        },
        {
            "Name": "Random Q",
            "Position": "TE",
            "Team": "NE",
            "Salary": "3000",
            "ProjPts": "7",
            "CalPts": "7",
            "Avail": "D",
            "Kickoff": "Sun 1:00 pm ET",
            "GameStart": "2026-11-08T13:00:00Z",
            "CashOwn": "0.02",
        },
    ]
    write_csv(d / "current" / "edge.csv", edge)
    cash1 = ["Dak Prescott", "Ja'Marr Chase", "Puka Nacua", "Hurt Guy"]
    cash2 = ["Dak Prescott", "Puka Nacua"]
    gpp = ["Wan'Dale Robinson", "Ja'Marr Chase"]
    write_json(
        d / "suggest_state" / "9.json",
        {
            "week": 9,
            "rounds": [
                {"round": 1, "at": "2026-01-01T00:00:00+00:00", "builds": []},
                {
                    "round": 2,
                    "at": (now - timedelta(hours=1)).astimezone(UTC).isoformat(),
                    "builds": [
                        build(cash1, "cash", 1, p_cash=0.73),
                        build(cash2, "cash", 2, p_cash=0.60),
                        build(
                            gpp, "gpp", 3, p_gpp=0.031, title="GPP build 3  ·  PIT stack QB+WR  ·  ~0 dupes"
                        ),
                    ],
                },
            ],
        },
    )
    write_json(
        d / "pools" / "2026-wk09.json",
        [{"at": sync, "source": "sync", "pool": {"Random Q": "GPP"}, "rules": {}}],
    )
    write_json(
        d / "cockpit" / "late_swap.json",
        {
            "saved_at": (now - timedelta(hours=3)).astimezone(UTC).isoformat(),
            "field_source": "field: projected + live scores (ESPN)",
            "lineups": [
                {
                    "number": 1,
                    "goal": "cash",
                    "open_slots": ["RB"],
                    "position": "Cash L1: 31% to cash",
                    "best": {
                        "kind": "single",
                        "out": ["Tyler Higbee"],
                        "into": ["Hunter Henry"],
                        "delta_pts": 3.1,
                        "delta_p_cash": 0.0355,
                        "delta_p_gpp": 0.0,
                        "overlap": "HOLD",
                    },
                },
                {"number": 2, "goal": "gpp", "open_slots": [], "position": "", "best": None},
            ],
        },
    )
    write_json(d / "model_cache" / "stacks_retro.json", {"verdict": "weak", "combined_z": 1.73})
    write_json(
        d / "model_cache" / "du_line.json",
        {"offset": 9.0, "weeks": [4, 5, 6, 7, 8], "passes": False, "z": -1.1},
    )
    week_inputs.save_input(
        "stacks",
        {
            "rows": [
                {
                    "cue": "BUILD",
                    "qb": "Dak Prescott",
                    "mates": "CeeDee Lamb",
                    "game": "DAL v PHI",
                    "salary": 15000,
                    "top1": 0.031,
                    "lift": 3.1,
                    "lev": 0.02,
                    "rule": "Always use: Dak Prescott, CeeDee Lamb",
                }
            ],
            "sorted_by": "leverage",
        },
        season=2026,
        week=9,
        data_dir=d,
    )
    week_inputs.save_input(
        "contest_plan",
        {
            "line": "spent $179 vs plan $150; over in: Showdown",
            "caps": {"Showdown": 5, "N-max GPP (2,000+ entrants)": 10},
            "spent": {"Showdown": 7, "N-max GPP (2,000+ entrants)": 4},
            "roi": [{"format": "Double Up", "entries": 152, "roi": 0.091, "rake": 0.13}],
            "h2h_avoid": [{"opponent": "Jpbielke", "record": "0-6"}],
        },
        season=2026,
        week=9,
        data_dir=d,
    )
    week_inputs.save_input(
        "postmortem",
        {"headline": "5 of 10 cash builds would have cashed", "lines": ["Chase: in 7 of 10 builds"]},
        season=2026,
        week=8,
        data_dir=d,
    )
    week_inputs.save_season_league(
        {
            "league": "Choppa",
            "headline": ["Choppa (Chopped): survive 71%", "  free move first: add X"],
            "alerts": ["SWAP QB Dak: OUT -> Brissett"],
            "pickups": ["add Y drop Z, bid $12"],
            "ideas": ["give A | get B"],
        },
        data_dir=d,
        now=now,
    )
    log = [
        {
            "Sport": "NFL",
            "Season": "2026",
            "Week": "8",
            "Format": "Double Up",
            "Entry_Fee": "$5.00",
            "Winnings_Non_Ticket": "$0.00",
        },
        {
            "Sport": "NFL",
            "Season": "2026",
            "Week": "8",
            "Format": "Showdown",
            "Entry_Fee": "$3.00",
            "Winnings_Non_Ticket": "$10.00",
        },
        {
            "Sport": "NFL",
            "Season": "2026",
            "Week": "7",
            "Format": "Showdown",
            "Entry_Fee": "$3.00",
            "Winnings_Non_Ticket": "$0.00",
        },
        {
            "Sport": "MLB",
            "Season": "2026",
            "Week": "8",
            "Format": "H2H",
            "Entry_Fee": "$50.00",
            "Winnings_Non_Ticket": "$99.00",
        },
        {
            "Sport": "NFL",
            "Season": "2026",
            "Week": "8",
            "Format": "Freeroll",
            "Entry_Fee": "$0.00",
            "Winnings_Non_Ticket": "$1.00",
        },
    ]
    write_csv(d / "contests" / "contest_log.csv", log)
    write_csv(
        d / "contests" / "decisions.csv",
        [
            {
                "season": "2026",
                "week": "8",
                "cls": "cash",
                "series": "NFL $5 Double Up",
                "points": "114.6",
                "cash_line": "123.3",
                "margin": "-8.7",
                "cashed": "False",
                "copies": "0",
            }
        ],
    )


def load(d, *, now=SAT, config=None):
    return replace(week_inputs.gather(2026, 9, now, d), config=config if config is not None else {})


@pytest.fixture
def full(tmp_path):
    fixtures(tmp_path)
    return load(tmp_path)


# -- every section renders ---------------------------------------------------------------------------


def test_every_section_says_not_run_yet_on_an_empty_data_dir(tmp_path):
    inp = load(tmp_path)
    page = week_page.render(inp, registered=set(commands_doc.all_commands()))
    for section_id in SECTION_IDS:
        assert f'id="{section_id}"' in page
    assert 'id="today"' in page and 'id="tools"' in page
    assert page.count("Not run yet") >= len(SECTION_IDS)
    assert "Nothing is synced" in page  # and Today says so too


def test_every_section_renders_from_fixture_outputs(full):
    page = week_page.render(full, registered=set(commands_doc.all_commands()))
    text = html.unescape(re.sub(r"<[^>]+>", " ", page))
    for section_id in SECTION_IDS:
        assert f'id="{section_id}"' in page
    # contest plan (Plan D's sidecar)
    assert "spent $179 vs plan $150" in text and "Jpbielke" in text and 'class="bad">over' in page
    # cash: top builds by Cash %, and the chalk check (Chase 41% is in 1 of 2 cash builds, Wan'Dale 33% in 0)
    assert "73%" in text and "60%" in text
    assert re.search(r"Ja'Marr Chase\s+WR\s+41%\s+1 of 2", text)
    assert re.search(r"Wan'Dale Robinson\s+WR\s+33%\s+0 of 2", text)
    assert "today's line" in text  # the saved run is not the calibrated one, and the page says so
    # gpp: the build's shape and the stacks
    assert (
        "PIT stack QB+WR" in text
        and "CeeDee Lamb" in text
        and "Always use: Dak Prescott, CeeDee Lamb" in text
    )
    # unknowns: Q in the pool/builds with his kickoff, and the OUT player sitting in a build
    assert re.search(r"Puka Nacua\s+WR LAR\s+Questionable\s+Sun 4:25 pm", text)
    assert "OUT or on IR but in a build" in text and "Hurt Guy (OUT, in 1 builds)" in text
    # sunday: the run, its field source, the swap with its label
    assert "Tyler Higbee" in text and "HOLD" in text and "nothing beats it" in text
    assert "Safe game-day sync" in text and "never blanks a started player" in text
    # season
    assert "Choppa" in text and "SWAP QB Dak: OUT" in text and "give A | get B" in text
    # last week: the newest week in the log, NFL paid entries only (no MLB, no freeroll)
    assert "Week 8" in text and "$8" in text and "5 of 10 cash builds would have cashed" in text
    assert "114.6 against a line of 123.3" in text and "-8.7" in text


def test_the_page_is_self_contained(full):
    page = week_page.render(full)
    assert "http://" not in page and "https://" not in page
    assert "<script" not in page and "<link" not in page and " src=" not in page


def test_calibrated_cash_is_labelled_as_such(full):
    builds = full.suggest["builds"]
    builds[0]["metrics"]["p_cash_shown"] = 0.41
    page = html.unescape(week_page.render(full))
    assert "41%" in page and "calibrated one" in page


def test_the_calibration_switch_on_but_not_in_the_saved_run_is_explained(full):
    cfg = {"sim": {"lines": {"calibrated_cash_offset": True}}}
    page = week_page.render(replace(full, config=cfg))
    assert (
        "switched on, but the saved run keeps" in page and "field average +9.0, Weeks 4, 5, 6, 7, 8" in page
    )


def test_chalk_check_waits_for_ownership(full):
    for row in full.edge:
        row["CashOwn"] = ""
    assert "CashOwn is not out yet" in week_page.render(full)


def test_a_late_swap_run_from_last_week_is_not_this_weeks(full):
    full.late_swap["saved_at"] = (SAT - timedelta(days=6)).astimezone(UTC).isoformat()
    section = html.unescape(week_page.sunday_section(full).html())
    assert "Late swap has not run this week" in section and "Tyler Higbee" not in section


def test_the_game_day_sync_guard_is_detected_not_hand_set(full, monkeypatch):
    import importlib.util

    assert week_page.sync_guard_merged() is True  # this checkout has dfs.sync_guard
    assert "never blanks a started player" in week_page.sunday_section(full).html()

    real = importlib.util.find_spec
    monkeypatch.setattr(
        importlib.util,
        "find_spec",
        lambda name, *a, **k: None if name == "dfs.sync_guard" else real(name, *a, **k),
    )
    assert week_page.sync_guard_merged() is False
    html_off = week_page.sunday_section(full).html()
    assert "Not confirmed" in html_off and "never blanks" not in html_off


def test_a_run_without_live_scores_says_so(full):
    full.late_swap["field_source"] = "field: projected"
    assert "no live scores" in week_page.sunday_section(full).html()


def test_trust_is_shown_for_each_decision(full):
    page = week_page.render(full)
    for chip in ("chip proven", "chip shadow", "chip untested"):
        assert chip in page
    assert (
        week_page.stacks_trust(full)[0] == week_page.SHADOW
    )  # the retro says "weak": information, not a rule
    assert week_page.stacks_trust(replace(full, stacks_retro=None))[0] == week_page.UNTESTED


def test_the_contest_plan_says_not_built_until_plan_d_registers_the_command(tmp_path):
    inp = load(tmp_path)
    without = week_page.contest_plan_section(inp, registered={"dfs sync"}).html()
    assert "is not built yet" in without and "Run <code" not in without
    with_it = week_page.contest_plan_section(inp, registered={"dfs contests plan"}).html()
    assert "Not run yet" in with_it and "dfs contests plan" in with_it


# -- the day logic ------------------------------------------------------------------------------------


@pytest.mark.parametrize("day", range(7))
@pytest.mark.parametrize("registered", [set(), {"dfs contests plan", "dfs entries record"}, None])
def test_every_day_has_three_to_five_steps(day, registered):
    steps = week_today.day_plan(day, registered)
    assert 3 <= len(steps) <= week_today.MAX_STEPS


def test_planned_steps_wait_for_their_command():
    names = lambda registered: [s.command for s in week_today.day_plan(week_today.SAT, registered)]  # noqa: E731
    assert "dfs contests plan" not in names(set())
    assert "dfs contests plan" in names({"dfs contests plan"})
    assert "dfs entries record" in names({"dfs contests plan", "dfs entries record"})
    assert len(names({"dfs contests plan", "dfs entries record"})) == 5


def test_the_week_in_order():
    first = {d: week_today.day_plan(d)[0] for d in range(7)}
    assert "close the week" in [s.phrase for s in week_today.day_plan(week_today.MON)]
    assert first[week_today.TUE].phrase == "start week N"
    assert [s.phrase for s in week_today.day_plan(week_today.WED)][0] == "sync"
    assert "build me lineups" in [s.phrase for s in week_today.day_plan(week_today.SAT)]
    sunday = [s.phrase for s in week_today.day_plan(week_today.SUN)]
    assert "final sync" in sunday and "late swap" in sunday


def test_every_phrase_on_the_page_is_one_claude_code_knows_and_every_command_exists():
    says = {s for p in week_tools.parse_phrases() for s in p.says}
    registered = set(commands_doc.all_commands())
    for day, specs in week_today.PLANS.items():
        for spec in specs:
            if spec.phrase:
                assert spec.phrase in says, f"{spec.phrase!r} (day {day}) is not in CLAUDE.md's tables"
            if spec.command and not spec.planned:
                assert week_tools.command_path(spec.command) in registered, spec.command
            if spec.planned:
                assert spec.phrase is None  # D's commands get their phrases in CLAUDE.md when they land


def test_today_shows_the_day_and_each_steps_last_run(full):
    runs = [runlog.Run(SAT - timedelta(hours=2), "dfs sync", True)]
    page = html.unescape(week_page.today_html(replace(full, runs=runs), set(commands_doc.all_commands())))
    assert "Today: Saturday, build and enter" in page
    assert 'Say <code class="say">"build me lineups"</code>' in page
    assert "last run" in page and "no run logged" in page


def test_stale_items():
    now = SAT
    manifest = {
        "edge": {"synced_at": (now - timedelta(hours=20)).astimezone(UTC).isoformat(), "error": None},
        "tffb": {
            "synced_at": (now - timedelta(hours=30)).astimezone(UTC).isoformat(),
            "error": "login expired",
        },
    }
    items = week_today.stale_items(
        now=now,
        manifest=manifest,
        suggest_at=now - timedelta(hours=25),
        late_swap_at=now - timedelta(hours=30),
        season_at=now - timedelta(days=6),
        day=week_today.SUN,
    )
    text = " ".join(items)
    assert "20 h old" in text and "tffb" in text and "builds on Suggested" in text
    assert "Late swap last ran" in text and "season-long view" in text
    fresh = {"edge": {"synced_at": (now - timedelta(hours=1)).astimezone(UTC).isoformat(), "error": None}}
    assert (
        week_today.stale_items(
            now=now, manifest=fresh, suggest_at=now, late_swap_at=None, season_at=None, day=0
        )
        == []
    )
    assert (
        "Nothing is synced"
        in week_today.stale_items(
            now=now, manifest={}, suggest_at=None, late_swap_at=None, season_at=None, day=0
        )[0]
    )


def test_the_page_header_counts_a_pending_source_apart_from_synced_and_failed(full):
    stamp = (SAT - timedelta(hours=1)).astimezone(UTC).isoformat()
    manifest = {
        "edge": {"synced_at": stamp, "error": None},
        "tffb_gps": {"synced_at": stamp, "error": None, "pending": "GPS not published"},
        "tffb": {"synced_at": stamp, "error": "login expired"},
    }
    page = week_page.render(replace(full, manifest=manifest), registered=set(commands_doc.all_commands()))
    assert "1 of 3 sources synced, 1 pending (tffb_gps), 1 failed (tffb)" in page


def test_a_pending_source_is_shown_as_pending_not_as_failed_or_synced():
    now = SAT
    stamp = (now - timedelta(hours=1)).astimezone(UTC).isoformat()
    manifest = {
        "edge": {"synced_at": stamp, "error": None},
        "tffb_gps": {"synced_at": stamp, "error": None, "pending": "GPS not published for Week 9 yet"},
        "tffb": {"synced_at": stamp, "error": "login expired"},
    }
    items = week_today.stale_items(
        now=now, manifest=manifest, suggest_at=None, late_swap_at=None, season_at=None, day=0
    )
    text = " ".join(items)
    assert "Failed their last sync: tffb." in text  # the real error is named alone
    assert "Pending (not published yet, not a failure): tffb_gps." in text
    state = week_today.launcher_state(
        week=9, now=now, manifest=manifest, game_starts=[], pool_count=0, finished_after_hours=3.5
    )
    assert state.synced_sources == 1  # only edge: a pending source is not "synced"


def test_the_launcher_state_the_page_builds_comes_from_local_files():
    now = datetime(2026, 11, 8, 18, 0, tzinfo=UTC)  # 1 pm ET Sunday: the 1 pm games have started
    manifest = {"edge": {"synced_at": (now - timedelta(hours=3)).isoformat(), "error": None}}
    state = week_today.launcher_state(
        week=9,
        now=now,
        manifest=manifest,
        game_starts=["2026-11-08T13:00:00Z", "2026-11-08T16:25:00Z"],
        pool_count=12,
        finished_after_hours=3.5,
    )
    assert state.game_started and not state.games_finished and state.pool_count == 12
    assert state.freshest_sync_age_hours == pytest.approx(3.0)
    assert [a.command for a in week_page.launcher.suggest_actions(state)][0] == "dfs sync --live"


# -- the tool list --------------------------------------------------------------------------------------


def test_the_tool_list_covers_every_registered_command_and_every_phrase(full):
    tools = week_tools.build([])
    assert set(commands_doc.all_commands()) <= {t.command for t in tools}
    page = week_page.render(full, registered=set(commands_doc.all_commands()), tools=tools)
    for path in commands_doc.all_commands():
        assert f"<code >{path}</code>" in page or f"<code >{html.escape(path)}</code>" in page or path in page
    for phrase in week_tools.parse_phrases():
        for say in phrase.says:
            assert html.escape('"' + say + '"') in page, say


def test_every_phrase_and_standard_week_command_has_a_day():
    assert week_tools.unplaced_phrases() == []


def test_a_command_with_no_day_falls_to_its_commands_doc_section():
    assert week_tools.group_of("dfs setup polish") == week_tools.REFERENCE
    assert week_tools.group_of("dfs status") == week_tools.ANY
    assert week_tools.group_of("dfs season doctor") == week_tools.SEASON


def test_phrases_are_parsed_from_claude_mds_tables():
    text = (
        "| Sam says | Run | Report |\n|---|---|---|\n"
        '| "sync" | `dfs sync`, then `dfs doctor` | each source\'s status |\n'
        '| "find me an RB" (in <league>) | `dfs season trades --get RB [--league <league> \\| --all]` |'
        " the same |\n"
        '| "fix flex" | nothing to run: tell him to use **Lineup Tools** | which of the two did it |\n'
        '| "add X, Y to my buy list" / "take X off my buy list" | edit `buy_list` in season.toml |'
        " the list |\n"
    )
    phrases = week_tools.parse_phrases(text)
    assert [p.says for p in phrases] == [
        ["sync"],
        ["find me an RB"],
        ["fix flex"],
        ["add X, Y to my buy list", "take X off my buy list"],
    ]
    assert phrases[0].commands == ["dfs sync", "dfs doctor"] and phrases[0].report == "each source's status"
    assert phrases[1].command == "dfs season trades --get"
    assert phrases[2].command is None and phrases[3].command is None


def test_each_tool_shows_when_it_was_last_run():
    runs = [runlog.Run(SAT - timedelta(days=1), "dfs lineups suggest", True)]
    tools = {t.command: t for t in week_tools.build(runs)}
    assert tools["dfs lineups suggest"].last.time == SAT - timedelta(days=1)
    assert tools["dfs sync"].last is None


def test_not_used_in_three_weeks_waits_for_a_log_that_is_old_enough_then_flags():
    tools = week_tools.build([])
    young = [runlog.Run(SAT - timedelta(days=5), "dfs sync", True)]
    stale, line = week_tools.not_used_lately(tools, young, SAT)
    assert stale == [] and "starts" in line
    assert week_tools.not_used_lately(tools, [], SAT)[0] == []

    old = [
        runlog.Run(SAT - timedelta(days=40), "dfs week close", True),
        runlog.Run(SAT - timedelta(days=25), "dfs lineups suggest", True),
        runlog.Run(SAT - timedelta(days=1), "dfs sync", True),
    ]
    tools = week_tools.build(old)
    stale, _ = week_tools.not_used_lately(tools, old, SAT)
    names = {t.command for t in stale}
    assert "dfs lineups suggest" in names and "dfs week close" in names
    assert "dfs sync" not in names and "dfs setup polish" not in names  # reference tools are never flagged
    page = week_page.tools_html(replace(load_empty(), runs=old, now=SAT), tools)
    assert "Not used in 3+ weeks" in page and "dfs lineups suggest" in page


def load_empty():
    return week_inputs.WeekInputs(season=2026, week=9, now=SAT, data_dir=week_inputs.data_root())


# -- inputs -----------------------------------------------------------------------------------------------


def test_last_week_is_nfl_paid_entries_of_the_newest_week(tmp_path):
    fixtures(tmp_path)
    lw = week_inputs.last_week(tmp_path, 2026)
    assert lw.week == 8
    assert {f["format"] for f in lw.formats} == {"Double Up", "Showdown"}
    assert lw.fees == 8 and lw.net == pytest.approx(2.0)  # -5 + (10 - 3)
    assert lw.cash_entries[0]["margin"] == pytest.approx(-8.7) and lw.cash_entries[0]["cashed"] is False
    assert week_inputs.last_week(tmp_path, 2025) is None


def test_stacks_summary_takes_the_top_rows_of_the_tab_layout():
    rows = [["", "title"], ["", "note"], ["", "QB", "Teammates"]]
    for i in range(5):
        rows.append(
            [
                "BUILD",
                f"QB{i}",
                "WR",
                "DAL v PHI",
                15000 + i,
                20.0,
                0.03,
                3.0,
                0.1,
                0.1,
                0.02,
                0.0,
                f"Always use: QB{i}, WR",
            ]
        )
    rows.append(["", "Top 1% = the chance ..."])  # the note under the table
    layout = SimpleNamespace(rows=rows, header_rows=[3], sort_label="leverage")
    summary = week_inputs.stacks_summary(layout)
    assert [r["qb"] for r in summary["rows"]] == ["QB0", "QB1", "QB2"] and summary["sorted_by"] == "leverage"
    assert week_inputs.stacks_summary(SimpleNamespace(rows=[], header_rows=[])) is None
    assert week_inputs.stacks_summary(None) is None


def test_season_summaries_merge_per_league(tmp_path):
    week_inputs.save_season_league({"league": "A", "headline": ["a"], "alerts": []}, data_dir=tmp_path)
    week_inputs.save_season_league({"league": "B", "headline": ["b"], "alerts": []}, data_dir=tmp_path)
    week_inputs.save_season_alerts("A", ["SWAP QB x: OUT"], data_dir=tmp_path)
    leagues = week_inputs.load_input("season_long", None, None, tmp_path)["leagues"]
    assert set(leagues) == {"A", "B"}
    assert leagues["A"]["alerts"] == ["SWAP QB x: OUT"] and leagues["A"]["headline"] == ["a"]
    assert "checked_at" in leagues["A"] and "checked_at" not in leagues["B"]


def test_saving_an_input_never_raises(tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("x")
    assert week_inputs.save_input("stacks", {"a": 1}, season=2026, week=9, data_dir=blocker) is False


# -- writing and opening -----------------------------------------------------------------------------------


def test_generate_writes_the_page_and_only_opens_it_when_asked(tmp_path, monkeypatch):
    opened = []
    monkeypatch.setattr(week_page.webbrowser, "open", lambda url: opened.append(url))
    path = week_page.generate(now=SAT, data_dir=tmp_path, open_browser=False)
    assert path == tmp_path / "week" / "2026-wk09.html" and "Week 9, 2026" in path.read_text()
    assert opened == []
    week_page.generate(now=SAT, data_dir=tmp_path, open_browser=True)
    assert opened == [path.resolve().as_uri()]


def test_the_command_builds_the_page_and_opens_it_by_default(monkeypatch):
    opened = []
    monkeypatch.setattr(week_page.webbrowser, "open", lambda url: opened.append(url))
    runner = CliRunner()
    result = runner.invoke(app, ["week", "page"])
    assert result.exit_code == 0, result.output
    assert len(opened) == 1 and opened[0].startswith("file://")
    opened.clear()
    assert runner.invoke(app, ["week", "page", "--no-open"]).exit_code == 0 and opened == []
    assert runner.invoke(app, ["week", "page", "--week", "3", "--no-open"]).exit_code == 0
    assert (week_inputs.data_root() / "week" / "2026-wk03.html").exists() or any(
        (week_inputs.data_root() / "week").glob("*-wk03.html")
    )


def test_a_phrase_is_listed_under_every_command_it_runs():
    tools = {t.command: t for t in week_tools.build([])}
    assert "start week N" in tools["dfs week new"].says and "start week N" in tools["dfs odds snapshot"].says
    assert "final sync" in tools["dfs sync"].says and "dfs sync --live" in tools["dfs sync"].variants
