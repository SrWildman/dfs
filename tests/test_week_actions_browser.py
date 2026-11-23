"""Sam's Week 11 note (PR 1 item 3): clicking an action gave no sign it was running. Every Run, Play / Fade,
Pick, Swap and Keep button shows a spinner and "running…" and is disabled while in flight, then a line with
done or failed, the time and one line of result (and the output's link for a command); a long job shows its
elapsed time. In a real browser (Chromium and Firefox) on a fake sheet; nothing here touches a real one."""

from __future__ import annotations

import re
from datetime import UTC, datetime

import pytest

from tests.test_week_ui_browser import Served, until

pytestmark = [pytest.mark.browser, pytest.mark.slow]

pytest.importorskip("playwright.sync_api")

# the stand-in for `python -m dfs.cli`: slow enough to watch, its last line the result; a file named `fail`
# in its working directory makes it fail
JOB = 'echo starting $@; sleep 1.6; if [ -f fail ]; then echo "no slate synced"; exit 1; fi;'
JOB += ' echo "Built 3 lineups"'


def open_page(browser, served, view="#everything"):
    ctx = browser.new_context(viewport={"width": 1440, "height": 1000})
    pg = ctx.new_page()
    errors: list[str] = []
    pg.on("pageerror", lambda e: errors.append(str(e)))
    pg.goto(served.url + view)
    until(pg, "!!(window.dfsPool && window.dfsLive && window.dfsAct)")
    return ctx, pg, errors


STATE = """(sel) => { const b = document.querySelector(sel); return b && {disabled: b.disabled,
  working: b.classList.contains('working'), text: b.textContent, spin: !!b.querySelector('.spin')}; }"""
LINE = """(k) => { const l = document.querySelector('.actst[data-act="' + k + '"]');
  return l && l.textContent; }"""


def test_a_run_button_shows_running_with_elapsed_time_then_done_with_its_last_line(browser, tmp_path):
    s = Served(tmp_path, job_script=JOB)
    try:
        ctx, pg, errors = open_page(browser, s)
        sel = "#progress button.runbtn:not([data-form])"
        command = pg.evaluate(f"document.querySelector({sel!r}).dataset.command")
        pg.locator(sel).first.click()
        st = pg.evaluate(STATE, sel)
        assert st["disabled"] and st["working"] and st["spin"] and "running…" in st["text"]
        until(pg, f"/running… 0:0[1-9]/.test(document.querySelector({sel!r}).textContent)")  # elapsed
        key = "run:" + command
        until(pg, f"(document.querySelector('.actst[data-act=\"{key}\"]') || {{}}).className === 'actst ok'")
        line = pg.evaluate(LINE, key)
        assert line.startswith("done ") and "Built 3 lineups" in line and re.search(r" · 0:0[1-9] · ", line)
        assert (
            pg.evaluate(f"document.querySelector('.actst[data-act=\"{key}\"] a.actout').getAttribute('href')")
            == "#run"
        )
        assert not pg.evaluate(STATE, sel)["disabled"]  # the button is back, with its label
        (tmp_path / "fail").write_text("")
        pg.locator(sel).first.click()
        until(pg, f"(document.querySelector('.actst[data-act=\"{key}\"]') || {{}}).className === 'actst bad'")
        assert pg.evaluate(LINE, key).startswith("failed ") and "no slate synced" in pg.evaluate(LINE, key)
        ctx.close()
        assert errors == []
    finally:
        s.close()


def test_a_decision_button_shows_saving_then_its_result_and_the_others_wait(browser, tmp_path):
    s = Served(tmp_path, latency=0.4, job_script=JOB)  # a write takes about a second, as Google's do
    try:
        ctx, pg, errors = open_page(browser, s)
        opt = pg.evaluate(
            """(() => { const b = [...document.querySelectorAll('.dec button.opt')]
              .find((x) => x.dataset.opt !== 'pivot' && x.offsetParent);
              return b && [b.dataset.key, b.dataset.opt]; })()"""
        )
        assert opt, "the fixture has a decision to answer"
        key, value = opt
        sel = f'.dec button.opt[data-key="{key}"][data-opt="{value}"]'
        pg.locator(sel).first.click()
        st = pg.evaluate(STATE, sel)
        assert st["disabled"] and st["working"] and "running…" in st["text"]
        others = pg.evaluate(
            f"[...document.querySelectorAll('.dec button.opt[data-key=\"{key}\"]')].map((b) => b.disabled)"
        )
        assert all(others)  # Play and Fade wait while one is in flight
        until(pg, f"/^(done|failed) /.test(({LINE})('dec:{key}') || '')", timeout_ms=20000)
        assert pg.evaluate(LINE, f"dec:{key}").startswith("done ")
        ctx.close()
        assert errors == []
    finally:
        s.close()


def test_swap_shows_recording_then_done_beside_the_answer(browser, tmp_path):
    from tests.test_week_server import lock_files

    s = Served(tmp_path)
    try:
        lock_files(tmp_path, 2026, 9)
        s.server.ctx.now = lambda: datetime(2026, 11, 8, 16, 0, tzinfo=UTC)
        ctx, pg, errors = open_page(browser, s, view="")
        swap = pg.locator('#lock-card button.lockbtn[data-action="swap"]').first
        row = swap.get_attribute("data-row")
        swap.click()
        until(pg, f"/^done /.test(({LINE})('lock:{row}') || '')")
        assert (
            pg.evaluate(f"document.querySelector('#lock-card span.answered[data-row=\"{row}\"]').textContent")
            == "Swap"
        )
        ctx.close()
        assert errors == []
    finally:
        s.close()
