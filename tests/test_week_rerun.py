"""Sam's Week 11 note (PR 1 item 4): a command that already ran had no easy rerun. Every step in This week
that has run has Run again and when it last ran; Sunday's status line reruns the final sync and late swap."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

from dfs import runlog, week_board, week_inputs, week_live, week_shell
from dfs import week_board as wb

NOW = datetime(2026, 11, 8, 20, 30, tzinfo=UTC)  # Week 9's Sunday, 3:30 pm ET


def inp(runs=(), **kw):
    return week_inputs.WeekInputs(season=2026, week=9, now=NOW, data_dir=Path("."), runs=list(runs), **kw)


def run(command, hour, ok=True, day=8):
    return runlog.Run(datetime(2026, 11, day, hour, 0, tzinfo=UTC), command, ok)


def steps_by_name(i, pool=None):
    return {s.name: s for s in week_board.progress(i, pool or {"id-a": "Cash"})}


def test_a_step_that_ran_has_run_again_with_when_it_last_ran():
    i = inp(
        [run("dfs lineups late-swap --refresh-injuries", 17)],
        late_swap={"saved_at": "2026-11-08T17:00:00+00:00"},
        suggest={"at": "2026-11-07T23:00:00+00:00", "builds": [{"label": "C1"}], "round": 2},
    )
    steps = steps_by_name(i)
    assert steps["Late swap"].state == wb.DONE and steps["Late swap"].at == datetime(
        2026, 11, 8, 17, tzinfo=UTC
    )
    assert steps["Builds"].at == datetime(2026, 11, 7, 23, tzinfo=UTC)  # no run logged: when it was saved
    html = week_shell.this_week_html(list(steps.values()), live=True)
    li = html[html.index("Late swap") :].split("</li>")[0]
    assert 'class="runbtn again" data-command="dfs lineups late-swap --refresh-injuries">Run again<' in li
    assert "last ran Sun 12:00 pm" in li
    # a step not done keeps Run; the static page has the run slot, never a live button
    assert 'class="runbtn" data-command="dfs entries record">Run<' in html
    static = week_shell.this_week_html(list(steps.values()), live=False)
    assert "runbtn again" not in static and 'data-label="Run again"' in static
    # the served page turns the static slot into a button too
    assert 'class="runbtn" data-command="dfs lineups late-swap --refresh-injuries">Run again<' in (
        week_live.augment(static, token="t", nonce="n", read_only=False)
    )


def test_a_newer_failed_run_is_the_one_shown():
    i = inp(
        [run("dfs lineups late-swap", 17), run("dfs lineups late-swap --refresh-injuries", 19, ok=False)],
        late_swap={"saved_at": "2026-11-08T17:00:00+00:00"},
    )
    s = steps_by_name(i)["Late swap"]
    assert s.at == datetime(2026, 11, 8, 19, tzinfo=UTC) and s.failed
    html = week_shell.this_week_html([s], live=True)
    assert '<span class="ran bad">last ran Sun 2:00 pm (failed)</span>' in html


def test_a_run_from_last_week_is_not_this_weeks():
    i = inp([run("dfs lineups late-swap", 17, day=1)], late_swap={"saved_at": "2026-11-08T17:00:00+00:00"})
    assert steps_by_name(i)["Late swap"].at == datetime(2026, 11, 8, 17, tzinfo=UTC)


def test_sundays_status_line_reruns_the_sync_and_the_late_swap():
    facts = {f.label: f for f in week_shell.sunday_facts(inp(), None)}
    assert facts["News"].run == ("dfs sync --live", "Sync") and facts["News"].ran == ""
    assert facts["Swaps open"].run == ("dfs lineups late-swap --refresh-injuries", "Late swap")
    i = inp([run("dfs sync --live", 16), run("dfs lineups late-swap --refresh-injuries", 18)])
    facts = {f.label: f for f in week_shell.sunday_facts(i, None)}
    assert (
        facts["News"].run == ("dfs sync --live", "Sync again")
        and facts["News"].ran == "last ran Sun 11:00 am"
    )
    assert facts["Swaps open"].run[1] == "Late swap again" and "Sun 1:00 pm" in facts["Swaps open"].ran
    line = week_shell.status_lines(replace(i, changes=None), None, {})["lock"]
    assert 'data-command="dfs sync --live" data-label="Sync again"' in line
    assert '<span class="ran">last ran Sun 11:00 am</span>' in line
    served = week_live.augment(line, token="t", nonce="n", read_only=False)
    assert 'class="runbtn" data-command="dfs lineups late-swap --refresh-injuries">Late swap again<' in served
