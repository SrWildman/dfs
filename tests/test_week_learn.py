"""Plan K2 on the Week page, from fixtures and with missing inputs: what won, exposure, how good are our
numbers (every trust chip links there), the salary watch, the scorecard under Last week, the drawer's Ask
Claude, and `dfs week serve`'s one-server-at-a-time pidfile."""

from __future__ import annotations

import json
import re
import signal
from datetime import datetime

from dfs import salary_watch, week_explorer, week_learn, week_money, week_server
from tests.test_week_money import entered_record, rows_with_more_wrs
from tests.test_week_plays import WED, inputs, page_text, render, section, slate_rows

MON = datetime(2026, 11, 2, 10, 0).astimezone()
THU = datetime(2026, 11, 5, 10, 0).astimezone()

FIELD_REVIEW = {
    "week": 8,
    "formats": [
        {
            "format": "Double Up",
            "cash": True,
            "contests": 2,
            "largest": {"series": "NFL $10 Double Up", "entries": 3448, "places_paid": 1500},
            "winner": {
                "names": ["A", "B"],
                "points": 218.5,
                "own_sum": 1.42,
                "own_known": 9,
                "salary_left": 100.0,
                "shape": "CIN QB + 2+ WR/TE, bring-back",
            },
            "cash_line": {
                "names": ["C"],
                "points": 123.3,
                "own_sum": 4.76,
                "own_known": 9,
                "salary_left": 0.0,
                "shape": "no QB stack",
            },
            "field": {
                "n": 10801,
                "stack": 0.37,
                "stack2": 0.05,
                "bringback": 0.24,
                "salary_left": 76.0,
                "flex_rb": 0.75,
                "top_stacks": [["DAL", 0.17]],
                "own_sum": 3.42,
            },
            "mine": None,
        }
    ],
}
NUMBERS = {
    "projection": [{"week": 7, "n": 207, "calpts": 4.83, "tffb": 5.43}],
    "cash": {
        "weeks": [{"week": 8, "n": 10, "shown": 0.7, "cashed": 0.5}],
        "all": {"n": 10, "shown": 0.7, "cashed": 0.46},
    },
    "signals": [
        {"signal": "FADE↓", "n": 52, "hit": 0.673, "vs_cal": -2.51, "thin": False, "trust": "Proven"}
    ],
}


def test_every_k2_section_says_not_run_yet_without_its_input(tmp_path):
    page = render(inputs(tmp_path, slate_rows()))
    for sid in ("field-review", "exposure", "numbers", "salaries"):
        assert "not run yet" in page_text(section(page, sid)).lower(), sid
    assert "Process scorecard" in page_text(section(page, "last-week")) or "last-week" in page


def test_what_won_shows_the_winner_the_line_and_the_field(tmp_path):
    page = render(inputs(tmp_path, slate_rows(), field_review=FIELD_REVIEW))
    text = re.sub(r"\s+", " ", page_text(section(page, "field-review")))
    assert "218.5 · CIN QB + 2+ WR/TE, bring-back · own sum 142% · $100 left" in text
    assert "Last paid place" in text and "123.3" in text
    assert "10,801" in text and "DAL 17%" in text
    assert "Week 8; Double Up line 123.3" in text  # the fold line


def test_monday_and_tuesday_open_what_won_at_the_top_and_thursday_folds_it(tmp_path):
    def opened(now):
        page = render(inputs(tmp_path, slate_rows(), now=now, field_review=FIELD_REVIEW))
        return '<section class="card fold" id="field-review"><details open>' in page

    assert opened(MON) and not opened(THU)


def test_every_trust_chip_links_to_the_numbers_section(tmp_path):
    page = render(inputs(tmp_path, slate_rows(), numbers=NUMBERS, field_review=FIELD_REVIEW))
    trust = re.findall(r'<(a|span) class="chip (proven|shadow|untested|data)"[^>]*>', page)
    assert trust and all(tag == "a" for tag, _ in trust)
    assert all('href="#numbers"' in m for m in re.findall(r'<a class="chip [a-z]+"[^>]*>', page))
    assert 'id="numbers"' in page and "openHash" in page  # the jump opens the collapsed section


def test_the_numbers_flag_cash_percent_running_high(tmp_path):
    page = render(inputs(tmp_path, slate_rows(), numbers=NUMBERS))
    body = section(page, "numbers")
    text = re.sub(r"\s+", " ", page_text(body))
    assert "Cash % ran 24 points high: shown 70%, 46% cashed over 10 builds." in text
    assert "FADE↓" in text and "67%" in text and "**" not in text
    assert 'class="line bad"' in body  # the fold line is urgent while it runs high


def test_exposure_this_week_from_recorded_gpp_entries(tmp_path):
    rows = rows_with_more_wrs(GppOwn="0.05")
    gpp = [{"contest_id": str(i), "name": "NFL $3 SE", "format": "SE GPP", "fee": 3.0} for i in range(2)]
    inp = inputs(tmp_path, rows, entered=entered_record(contests=gpp))
    v = week_learn.entered_gpp(inp)
    assert len(v) == 2  # one per contest entry
    text = page_text(section(render(inp), "exposure"))
    assert "This week: 2 GPP entries against GppOwn" in text and "Overweight" in text


def test_exposure_after_the_close_against_actual_ownership(tmp_path):
    closed = {
        "week": 8,
        "actual": {
            "entries": 10,
            "over": [{"name": "Jonah Coleman", "mine": 0.5, "field": 0.21, "count": 5, "diff": 0.29}],
            "under": [],
            "rows": [],
            "unknown": [],
        },
        "predicted": {"entries": 10, "over": [], "under": [], "rows": [], "unknown": []},
    }
    text = page_text(section(render(inputs(tmp_path, slate_rows(), exposure_close=closed)), "exposure"))
    assert "Week 8: 10 GPP entries against actual ownership" in text and "Jonah Coleman" in text


def test_the_salary_watch_section_flags_the_steady_drop(tmp_path):
    rows = slate_rows()
    star = next(r for r in rows if r["Name"] == "Wr Star")
    before = {salary_watch.key("Wr Star", star["Team"]): float(star["Salary"]) + 400}
    inp = inputs(tmp_path, rows, salary_prev=before)
    log = {str(star["Id"]): [{"Snap": 0.8, "Tgt": 7}, {"Snap": 0.82, "Tgt": 6}, {"Snap": 0.78, "Tgt": 8}]}
    text = page_text(week_learn.salary_section(inp, log).body)
    assert (
        "Cheaper, same job" in text and "Wr Star" in text and "-400" in text and "snaps 80 / 82 / 78%" in text
    )
    assert week_learn.salary_line(inp, log)[0].startswith("1 cheaper with the same job")


def test_the_scorecard_sits_under_last_week_and_says_when_it_has_not_run(tmp_path):
    card = {
        "week": 8,
        "tier_line": "",
        "tier_over": [],
        "decisions": [],
        "lineups": 2,
        "no_build": 0,
        "overrides": [
            {
                "kind": "gpp",
                "entries": 1,
                "build": "gpp build 3 (round 1)",
                "shared": 8,
                "into": [["Wr Star", 20.0]],
                "out": [["Wr Value", 5.0]],
                "points": 150.0,
                "build_points": 135.0,
                "delta": 15.0,
            }
        ],
        "gain": 15.0,
        "loss": 0.0,
        "season_line": "your overrides against the model: +15.0 / +0.0 points over 1 week",
    }
    text = re.sub(r"\s+", " ", page_text(week_learn.scorecard_html(inputs(tmp_path, scorecard=card))))
    assert (
        "1 of 2 lineups differ from their nearest build (1 edited builds, 0 your own): +15.0 / +0.0" in text
    )
    assert "in Wr Star 20.0; out Wr Value 5.0" in text and "over 1 week" in text
    assert "not run yet" in page_text(week_learn.scorecard_html(inputs(tmp_path))).lower()


def test_an_old_bankroll_input_with_the_proposal_shows_the_weekly_budget(tmp_path):
    old = {
        "bankroll": 131.69,
        "budget": 250.0,
        "cost": 0.0,
        "budget_left": 250.0,
        "curve": [],
        "guide": {"source": "proposed", "pct_week": 5.5, "week": 7.24, "tiers": {}, "lines": []},
        "over": "Your Weekly Budget $250.00 is above what a $131.69 bankroll supports under the proposed",
    }
    inp = inputs(tmp_path, slate_rows(), bankroll=old)
    assert week_money.bankroll_line(inp) == ("Bankroll $131.69; Weekly Budget $250.00 (189.8% of it)", False)
    text = page_text(section(render(inp), "bankroll"))
    assert "proposed" not in text and "is above what" not in text


# -- the drawer's Ask Claude ---------------------------------------------------------------------------------


def test_the_drawer_asks_claude_about_the_player_only_on_the_served_page(tmp_path):
    inp = inputs(tmp_path, slate_rows())
    from dfs import week_board as wb

    data = week_explorer.payload(inp, wb.board(inp), wb.pool_by_id(inp, None))
    assert data["ask_player"] == (
        "Tell me about {name} this week: role, matchup, ownership, and whether he fits cash or GPP"
    )
    assert "if (LIVE) {" in week_explorer.JS and "ASK_PLAYER.replace('{name}'" in week_explorer.JS


# -- one Week server at a time -------------------------------------------------------------------------------


def test_a_live_pidfile_is_the_running_server_and_a_dead_one_is_stale(tmp_path):
    path = tmp_path / "serve.pid"
    week_server.write_pidfile({"pid": 4242, "port": 9, "week": 9, "url": "http://127.0.0.1:9/?t=x"}, path)
    assert oct(path.stat().st_mode & 0o777) == "0o600"
    assert week_server.running(path, alive=lambda pid: pid == 4242)["port"] == 9
    assert week_server.running(path, alive=lambda pid: False) is None
    path.write_text("not json")
    assert week_server.running(path, alive=lambda pid: True) is None


def test_stop_sends_sigterm_to_that_pid_only(tmp_path):
    path = tmp_path / "serve.pid"
    week_server.write_pidfile({"pid": 4242, "port": 9, "week": 9, "url": "u"}, path)
    sent = []
    msg = week_server.stop(path, alive=lambda pid: True, kill=lambda pid, sig: sent.append((pid, sig)))
    assert sent == [(4242, signal.SIGTERM)] and "Stopped the Week server (pid 4242" in msg
    stale = week_server.stop(path, alive=lambda pid: False, kill=lambda *a: sent.append(a))
    assert "stale" in stale and not path.exists() and len(sent) == 1
    assert week_server.stop(path) == "No Week server is running."


def test_the_pidfile_is_removed_only_by_its_owner(tmp_path):
    path = tmp_path / "serve.pid"
    week_server.write_pidfile({"pid": 1}, path)
    week_server.remove_pidfile(2, path)
    assert path.exists()
    week_server.remove_pidfile(1, path)
    assert not path.exists()


def test_serve_opens_the_running_server_instead_of_starting_a_second(monkeypatch):
    opened, said = [], []
    monkeypatch.setattr(
        week_server, "running", lambda: {"pid": 7, "week": 9, "url": "http://127.0.0.1:1/?t=z"}
    )
    monkeypatch.setattr(week_server.webbrowser, "open", opened.append)
    monkeypatch.setattr(
        week_server, "make_server", lambda **kw: (_ for _ in ()).throw(AssertionError("second"))
    )
    week_server.serve(echo=said.append)
    assert opened == ["http://127.0.0.1:1/?t=z"]
    assert "already running (pid 7, Week 9)" in said[0]


def test_json_payloads_round_trip(tmp_path):
    # the saved inputs are plain JSON (tuples become lists) and the page reads them back
    assert json.loads(json.dumps(FIELD_REVIEW))["formats"][0]["field"]["top_stacks"] == [["DAL", 0.17]]
    assert WED.weekday() == 2
