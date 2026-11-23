"""Plan N's page items in a real browser (Playwright's Chromium and Firefox: the `browser` fixture in
`conftest.py`), against the served page on a fake sheet: the page state across a refresh and a reload, one
pool store, the bulk write with a conflicting cell, the queue, the chart viewer (zoom, box, lasso, hover), the
game view's writes and the chat toggle remembered.

Skipped where an engine is not installed (CI runs no browser; `playwright install chromium firefox` enables
these locally). Nothing here touches a real sheet: the server writes a `FakeSheet`."""

from __future__ import annotations

import contextlib
import re
import threading
import time

import pytest

from dfs import sheet_suggested as ss
from dfs import week_inputs, week_jobs, week_server, week_writes
from tests.test_week_money import OWN, rows_with_more_wrs
from tests.test_week_plays import row, write_csv
from tests.test_week_server import stub_python
from tests.test_week_writes import FakeSheet

# Playwright (Chromium and Firefox): also slow; a bare `pytest` leaves the file out, `--full` runs it.
pytestmark = [pytest.mark.browser, pytest.mark.slow]

sync_api = pytest.importorskip("playwright.sync_api")

EDGE = "EdgeRaw"


TEAMS = (
    "ARI ATL BAL BUF CAR CHI CIN CLE DAL DEN DET GB HOU IND JAX KC LV LAC LAR MIA MIN NO NYG PHI PIT SEA SF"
    " TB TEN WAS"
).split()
ROSTER = ["QB", "QB", "RB", "RB", "RB", "RB", "WR", "WR", "WR", "WR", "WR", "WR", "TE", "TE", "TE", "DST"]


def slate_of(n: int) -> list[dict]:
    """`n` more players in a main slate's shape (16 a team, its opponent the next team in the list): the size
    of Sam's real EdgeRaw, for the tests that measure the page under it."""
    out = []
    for i in range(n):
        t = (i // len(ROSTER)) % len(TEAMS)
        team, opp = TEAMS[t], TEAMS[t ^ 1]
        pos = ROSTER[i % len(ROSTER)]
        out.append(
            row(
                f"Big {pos} {team} {i}",
                pos,
                team,
                opp,
                3000 + (i * 137) % 6000,
                4.0 + (i * 7) % 19,
                CashOwn=f"{(i % 30) / 100:.2f}",
                GppOwn=f"{(i % 25) / 100:.2f}",
            )
        )
    return out


class SlowSheet(FakeSheet):
    """A fake sheet that answers as slowly as Google does: every read and every write waits `latency` s."""

    def __init__(self, latency: float):
        super().__init__()
        self.latency = latency

    def read_range(self, tab, a1):
        time.sleep(self.latency)
        return super().read_range(tab, a1)

    def batch_read_ranges(self, pairs):
        time.sleep(self.latency)
        return super().batch_read_ranges(pairs)

    def update_ranges(self, tab, updates):
        time.sleep(self.latency)
        return super().update_ranges(tab, updates)


class Served:
    def __init__(
        self,
        tmp_path,
        *,
        extra_players: int = 0,
        latency: float = 0.0,
        job_script: str = "echo ran $@; echo second line",
    ):
        self.tmp_path = tmp_path
        self.rows = rows_with_more_wrs(**OWN)
        self.rows += [
            row(
                f"Wr Extra{i}",
                "WR",
                "NE",
                "NYJ",
                3000 + i * 300,
                7.0 + i,
                Spread="-1.5",
                OverUnder="38.5",
                CashOwn="0.05",
                GppOwn="0.05",
            )
            for i in range(8)
        ]  # enough WRs for a Show more
        self.rows += slate_of(extra_players)
        for r in self.rows:
            r["Tgt%"], r["Snap%"] = "0.2", "0.7"
        write_csv(tmp_path / "data" / "current" / "edge.csv", self.rows)
        sheet = SlowSheet(latency) if latency else FakeSheet()
        for c, h in enumerate(["Pool", "Name", "Id"]):
            sheet.put(EDGE, 1, c, h)
        for i, r in enumerate(self.rows, start=2):
            sheet.put(EDGE, i, 1, r["Name"])
            sheet.put(EDGE, i, 2, r["Id"])
        for i, label in enumerate(["Always use", "Never use", "Max exposure", "Stack rules"], start=2):
            sheet.put(ss.TAB, i, 0, label)
        sheet.put(ss.TAB, 8, 0, f"{ss.BUILD_LABEL} 1")
        self.sheet = sheet

        @contextlib.contextmanager
        def lock():
            yield

        edits = week_writes.SheetEdits(
            sheet, edge_tab=EDGE, log_path=tmp_path / "edits.log", lock=lock, title="fake"
        )
        self.server = week_server.make_server(
            season=2026,
            week=9,
            token="tok-" + "x" * 20,
            read_only=False,
            data_dir=tmp_path / "data",
            log_path=tmp_path / "serve.log",
            edits=edits,
            claude_available=lambda: False,
            # a stand-in for `python -m dfs.cli`: a run button here never runs a real command
            jobs=week_jobs.JobRunner(
                python=stub_python(tmp_path, job_script),
                history_path=tmp_path / "jobs.jsonl",
                cwd=tmp_path,
                stop_waits=(0.05, 0.05, 0.05),
            ),
        )
        threading.Thread(target=lambda: self.server.serve_forever(poll_interval=0.02), daemon=True).start()
        self.url = f"http://127.0.0.1:{self.server.ctx.port}/?t=tok-{'x' * 20}"

    def row_of(self, name: str) -> int:
        return next(i for i, r in enumerate(self.rows, start=2) if r["Name"] == name)

    def pool(self, name: str) -> str:
        return self.sheet.get(EDGE, self.row_of(name), 0)

    def close(self):
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def served(tmp_path):
    s = Served(tmp_path)
    yield s
    s.close()


@pytest.fixture
def page(browser, served):
    ctx = browser.new_context(viewport={"width": 1440, "height": 1000})
    pg = ctx.new_page()
    errors: list[str] = []
    pg.on("pageerror", lambda e: errors.append(str(e)))
    pg.goto(served.url + "#everything")  # the full page (Plan O2: the page opens on the Overview)
    until(pg, "!!(window.dfsPool && window.dfsLive)")
    yield pg
    ctx.close()
    assert errors == []


def until(pg, expression: str, timeout_ms: int = 8000):
    """Poll from outside the page: the served page's Content Security Policy (rightly) refuses the string
    `eval` Playwright's `wait_for_function` would run inside it."""
    for _ in range(timeout_ms // 50):
        if pg.evaluate(expression):
            return
        pg.wait_for_timeout(50)
    raise AssertionError(f"timed out waiting for {expression}")


def settle(pg):
    until(pg, "!window.dfsQueue.busy()")
    pg.wait_for_timeout(150)


def test_open_folds_show_more_and_scroll_survive_a_refresh_and_a_reload(page):
    page.evaluate("document.querySelector('#consensus details').open = true")
    page.locator("#board button.showmore").first.click()
    page.evaluate("document.querySelector('#consensus').scrollIntoView()")
    page.wait_for_timeout(300)
    y = page.evaluate("document.querySelector('#pagecol').scrollTop")
    assert y > 0
    page.evaluate("window.dfsLive.refresh()")
    page.wait_for_timeout(800)
    assert page.evaluate("document.querySelector('#consensus details').open")
    assert (
        page.evaluate(
            "document.querySelectorAll('#board tbody.posgrp')[0].querySelectorAll('tr.more[hidden]').length"
        )
        == 0
    )
    assert abs(page.evaluate("document.querySelector('#pagecol').scrollTop") - y) < 5
    page.reload()
    page.wait_for_timeout(800)
    assert page.evaluate("document.querySelector('#consensus details').open")
    assert abs(page.evaluate("document.querySelector('#pagecol').scrollTop") - y) < 5


def test_one_write_updates_the_board_the_explorer_the_drawer_and_the_decisions(page, served):
    seg = page.locator('#board .seg[data-id="id-Qb Alpha"]')
    seg.locator("button[data-v=Cash]").click()
    settle(page)
    assert served.pool("Qb Alpha") == "Cash"
    assert (
        page.evaluate("document.querySelector('#board .seg[data-id=\"id-Qb Alpha\"] button.on').dataset.v")
        == "Cash"
    )
    assert page.evaluate("window.dfsPool.get('id-Qb Alpha')") == "Cash"
    page.evaluate("window.dfsDrawer('id-Qb Alpha')")
    assert page.evaluate("document.querySelector('#drawer .seg button.on').dataset.v") == "Cash"
    page.keyboard.press("Escape")
    page.locator("#ex-name").fill("Qb Alpha")
    assert page.evaluate("document.querySelector('#ex-table .seg button.on').dataset.v") == "Cash"
    for v in page.evaluate(
        "[...document.querySelectorAll('.poolval[data-id=\"id-Qb Alpha\"]')].map(s => s.textContent)"
    ):
        assert v == "Cash"


def test_a_bulk_write_reports_a_conflicting_cell_on_its_own_row_and_try_again_writes_it(page, served):
    boxes = page.locator("#board tbody.posgrp[data-pos=WR] input.sel")
    boxes.nth(0).click()
    boxes.nth(1).click(modifiers=["Shift"])
    ids = page.evaluate("window.dfsSel.ids()")
    assert len(ids) == 2
    names = [served.rows[[r["Id"] for r in served.rows].index(i)]["Name"] for i in ids]
    served.sheet.put(EDGE, served.row_of(names[0]), 0, "Both")  # changed in the sheet behind the page's back
    page.locator("#bulkbar button[data-bulk=GPP]").click()
    settle(page)
    assert served.pool(names[0]) == "Both" and served.pool(names[1]) == "GPP"  # one written, one refused
    issues = page.locator("#pool-issues li")
    assert (
        issues.count() == 1
        and names[0] in issues.inner_text()
        and "the sheet has Both" in issues.inner_text()
    )
    assert page.evaluate("window.dfsSel.ids().length") == 0
    issues.locator("button", has_text="Try again").click()
    settle(page)
    assert served.pool(names[0]) == "GPP" and page.locator("#pool-issues li").count() == 0


def test_changes_queue_while_a_write_runs_and_none_is_refused(page, served):
    page.locator('#board .seg[data-id="id-Qb Alpha"] button[data-v=GPP]').click()
    page.locator('#board .seg[data-id="id-Qb Beta"] button[data-v=Both]').click()
    page.locator('#board .seg[data-id="id-Qb Alpha"] button[data-v=Both]').click()
    settle(page)
    assert served.pool("Qb Alpha") == "Both" and served.pool("Qb Beta") == "Both"
    assert page.locator(".toast.bad").count() == 0


def test_the_chart_viewer_zooms_selects_by_box_and_lasso_and_hovers(page):
    page.locator("#value-map button.expand").click()
    page.wait_for_selector("#viewer circle.dot")
    before = page.evaluate("window.dfsViewer.V.view.slice()")
    box = page.locator("#viewer .vplot").bounding_box()
    cx, cy = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
    page.mouse.move(cx, cy)
    page.mouse.wheel(0, -400)
    page.wait_for_timeout(200)
    after = page.evaluate("window.dfsViewer.V.view.slice()")
    assert after[1] - after[0] < before[1] - before[0]  # zoomed in
    page.locator("#viewer button[data-act=reset]").click()
    page.locator("#viewer button[data-mode=box]").click()
    page.mouse.move(box["x"] + 5, box["y"] + 5)
    page.mouse.down()
    page.mouse.move(box["x"] + box["width"] - 5, box["y"] + box["height"] - 5, steps=6)
    page.mouse.up()
    page.wait_for_timeout(200)
    every = page.evaluate("window.dfsViewer.V.inView.length")
    assert page.evaluate("window.dfsSel.ids().length") == every > 0
    assert page.locator("#viewer .vside li").count() == every
    page.locator("#viewer .vside button", has_text="Clear").click()
    page.locator("#viewer button[data-mode=lasso]").click()
    x0, y0, w, h = box["x"], box["y"], box["width"], box["height"]
    page.mouse.move(x0 + 3, y0 + 3)
    page.mouse.down()
    for fx, fy in ((0.99, 0.01), (0.99, 0.99), (0.01, 0.99), (0.01, 0.01)):
        page.mouse.move(x0 + w * fx, y0 + h * fy, steps=5)
    page.mouse.up()
    page.wait_for_timeout(200)
    assert page.evaluate("window.dfsSel.ids().length") == every
    dot = page.locator("#viewer circle.dot").first
    dot.hover()
    assert (
        page.locator("#viewer .vtip").is_visible() and "CalPts" in page.locator("#viewer .vtip").inner_text()
    )
    page.keyboard.press("Escape")
    assert page.locator("#viewer").is_hidden()


def test_the_usage_chart_opens_from_the_explorer(page):
    page.locator("#ex-lenses button[data-lens=usage]").click()
    page.locator("#ex-chart svg.chart").click(position={"x": 30, "y": 20})
    page.wait_for_selector("#viewer circle.dot")
    assert "Usage" in page.locator("#viewer h2").inner_text()
    assert page.evaluate("window.dfsViewer.V.mode") == "pan"  # Plan O: pan is the default


def test_the_explorer_chart_follows_the_lens_and_the_filters(page):
    """Plan O item 1: the chart changes with the lens, and draws only the rows the filters pass."""
    titles = {}
    for key in ("value", "cash", "gpp", "usage", "matchup", "projections", "ownership"):
        page.locator(f"#ex-lenses button[data-lens={key}]").click()
        assert page.evaluate("document.querySelector('#ex-chart').dataset.lens") == key
        titles[key] = page.locator("#ex-chart h4").inner_text()
    assert len(set(titles.values())) == len(titles)  # a different chart per lens
    page.locator("#ex-lenses button[data-lens=value]").click()
    every = page.locator("#ex-chart circle.dot").count()
    page.select_option("#ex-pos", "WR")
    wrs = page.locator("#ex-chart circle.dot").count()
    assert 0 < wrs < every
    assert page.evaluate("window.dfsCharts.explorer.points.every(p => p.pos === 'WR')")
    assert f"{wrs} players" in page.locator("#ex-chart-cap").inner_text()
    page.locator("#ex-chart button.expand").click()
    page.wait_for_selector("#viewer circle.dot")
    assert page.evaluate("window.dfsViewer.V.spec.points.length") == wrs  # full screen: the same points


def test_the_game_view_adds_the_ticked_as_gpp(page, served):
    page.locator('#games .game[data-game="2026_09_DAL_IND"]').click()
    page.wait_for_selector("#gameview input.sel")
    assert page.locator("#gameview h3").all_inner_texts()[0].startswith("DAL")
    for name in ("Qb Alpha", "Wr Value", "Te Fade"):  # the QB, a mate, a bring-back
        page.locator(f'#gameview input.sel[data-name="{name}"]').check()
    page.locator("#gameview button[data-act=gpp]").click()
    settle(page)
    assert [served.pool(n) for n in ("Qb Alpha", "Wr Value", "Te Fade")] == ["GPP", "GPP", "GPP"]
    assert page.locator("#gameview button[data-act=stack]").count() == 0  # Removal F7
    page.keyboard.press("Escape")
    page.locator('#board a.tm[data-team="IND"]').first.click()  # an Opp cell opens the same game
    assert page.locator("#gameview").is_visible()


def test_the_chat_starts_folded_and_ask_claude_opens_it(page):
    """Sam, 2026-11-17: the chat is collapsed by default everywhere and never opens by itself."""
    folded = "document.querySelector('#chat').classList.contains('collapsed')"
    assert page.evaluate(folded) and page.evaluate("document.body.classList.contains('chat-closed')")
    assert not page.locator("#chat").is_visible()  # no rail: the header's Ask Claude is the way in
    page.locator("#ask-open").click()
    assert not page.evaluate(folded) and page.locator("#chat").is_visible()
    page.reload()
    until(page, "!!window.dfsLive")
    assert page.evaluate(folded)  # opened once is not opened for good


def test_a_pool_click_keeps_the_board_box_and_page_scroll_and_the_focus(browser, served):
    """Plan O item 1: a Pool click scrolled the board back up. The board sits in its own scroll box, and the
    refresh after a write replaced its section, which put the box back at the top; the focus went too."""
    ctx = browser.new_context(
        viewport={"width": 1440, "height": 420}
    )  # a short window: the board box scrolls
    pg = ctx.new_page()
    pg.goto(served.url + "#players")
    until(pg, "!!(window.dfsPool && window.dfsLive)")
    box = "document.querySelector('#board .scroll.tall')"
    pg.evaluate(f"({box}).scrollIntoView({{block: 'start'}})")
    pg.evaluate(f"({box}).scrollTop = ({box}).scrollHeight")
    pg.wait_for_timeout(300)
    inner, outer = (
        pg.evaluate(f"({box}).scrollTop"),
        pg.evaluate("document.querySelector('#pagecol').scrollTop"),
    )
    assert inner > 50 and outer > 0
    target = pg.evaluate(
        f"""(() => {{ const b = {box}, r = b.getBoundingClientRect();
        const btn = [...b.querySelectorAll('.seg button[data-v=GPP]')].find((x) => {{
          const q = x.getBoundingClientRect(); return q.top > r.top + 30 && q.bottom < r.bottom; }});
        return btn.closest('.seg').dataset.id; }})()"""
    )
    pg.locator(f'#board .seg[data-id="{target}"] button[data-v=GPP]').click()
    settle(pg)
    pg.wait_for_timeout(300)
    assert abs(pg.evaluate(f"({box}).scrollTop") - inner) < 5
    assert abs(pg.evaluate("document.querySelector('#pagecol').scrollTop") - outer) < 5
    assert pg.evaluate("document.activeElement.dataset.v") == "GPP"
    assert pg.evaluate("document.activeElement.closest('.seg').dataset.id") == target
    pg.reload()
    until(pg, "!!window.dfsLive")
    pg.wait_for_timeout(500)
    assert abs(pg.evaluate(f"({box}).scrollTop") - inner) < 5  # and after a reload
    ctx.close()


def test_every_explorer_lens_draws_its_rows(page):
    """Plan O item 1: the Matchup lens was empty (it named a column the page's data leaves out, and the script
    stopped). Every lens draws rows, and nothing on the page throws (the fixture checks page errors)."""
    for key in page.evaluate("[...document.querySelectorAll('#ex-lenses button')].map(b => b.dataset.lens)"):
        page.locator(f"#ex-lenses button[data-lens={key}]").click()
        if key == "signals":
            continue  # only players with a signal chip: none in this fixture is guaranteed
        assert page.locator("#ex-table tbody tr[data-id]").count() > 0, key


def test_the_run_panel_is_the_weekly_buttons_curated_forms_and_recent_runs(page):
    """Plan O item 1b: the weekly commands in the day's order, setup and fix commands behind a collapsed
    Advanced toggle, forms with the used options up front (short labels, the help as a tooltip, defaults
    filled, toggles in one column) and a preview of the command; recent runs say ok or failed, how long and
    when, and open their output."""
    until(page, "document.querySelectorAll('#run-menu button').length > 0")
    labels = page.locator("#run-menu button b").all_inner_texts()
    assert labels == [w.label for w in week_jobs.WEEKLY]
    assert not page.evaluate("document.querySelector('#run-adv').open")
    adv = page.evaluate("[...document.querySelectorAll('#run-adv-list button')].map(b => b.textContent)")
    assert "setup repair-formula-ranges" in adv and "setup polish" in adv
    others = page.evaluate("[...document.querySelectorAll('#run-list button')].map(b => b.textContent)")
    assert others and not any(t.startswith("setup ") or "fix-" in t for t in others)
    page.locator("#run-menu button[data-command='dfs lineups late-swap']").click()
    assert page.locator("#opt-refresh_injuries").is_checked()  # its default, filled in
    assert page.locator("#run-preview").inner_text() == "dfs lineups late-swap --refresh-injuries"
    assert page.locator("label[for=opt-refresh_injuries]").get_attribute("title").startswith("Lay ESPN")
    assert page.locator("#run-form details.more").count() == 1  # the rest, folded away
    assert not page.evaluate("document.querySelector('#run-form details.more').open")
    page.locator("#run-menu button[data-command='dfs sync']").click()
    assert page.locator("label[for=opt-live]").inner_text() == "Final sync: fast-moving sources only"
    page.locator("#opt-live").check()
    assert page.locator("#run-preview").inner_text() == "dfs sync --live"
    toggles = page.evaluate(
        "[...document.querySelectorAll('#run-form .opts.toggles input')]"
        ".map(i => Math.round(i.getBoundingClientRect().left))"
    )
    assert len(set(toggles)) == 1  # one aligned column
    page.locator("#run-form button[type=submit]").click()
    until(page, "document.querySelector('#run-out').textContent.includes('[exit 0]')")
    until(page, "document.querySelectorAll('#run-history li.ok').length > 0")
    first = page.locator("#run-history li").first
    assert first.locator(".st").inner_text() == "ok" and "dfs sync --live" in first.inner_text()
    assert re.search(r"\d+s", first.locator(".t").inner_text())
    assert first.locator("a", has_text="output").count() == 0  # Removal F7: no job-output viewer


def test_every_name_carries_the_pool_chip_and_a_missing_player_is_one_click_away(page, served):
    """Plan O item 2: every resolved `a.pl` outside a row that already has the control gets a chip with his
    pool value; the chip opens Cash / GPP / Both / none, which writes the same cell; "+ Cash" / "+ GPP" adds a
    player listed as missing from the pool in one click."""
    until(page, "document.querySelectorAll('.pchip').length > 0")
    bare = page.evaluate(
        """[...document.querySelectorAll('a.pl[data-id]')].filter(a => !a.closest('#drawer, .seg, svg'))
          .filter(a => {
            const id = a.dataset.id, row = a.closest('tr, li, .gp-row');
            const hasSeg = row && row.querySelector('.seg[data-id="' + CSS.escape(id) + '"]');
            // the square sits before the name (and its position label)
            const p = a.previousElementSibling;
            const q = p && p.classList.contains('posl') ? p.previousElementSibling : p;
            return !hasSeg && !(q && q.classList.contains('pchip'));
          }).map(a => a.textContent)"""
    )
    assert bare == []
    assert page.locator("#board .pchip").count() == 0  # the board rows have the control already
    chip = page.locator(".pchip").first
    pid = chip.get_attribute("data-id")
    name = page.evaluate(f"window.dfsPool.name({pid!r})")
    chip.click()
    assert page.locator("#pool-pop .seg button").count() == 4
    page.locator("#pool-pop .seg button[data-v=GPP]").click()
    read_back(page, pid, "GPP")
    assert served.pool(name) == "GPP"
    until(page, f"document.querySelector('.pchip[data-id=\"{pid}\"]').dataset.pool === 'GPP'")
    page.keyboard.press("Escape")
    assert page.locator("#pool-pop").count() == 0
    add = page.locator("button.addpool:visible").first
    assert add.count(), "the fixture lists a player the data likes who is not in the pool"
    aid, kind = add.get_attribute("data-id"), add.get_attribute("data-v")
    aname = page.evaluate(f"window.dfsPool.name({aid!r})")
    # what the add gives, by the page's own rule (GPP + Cash = Both), from the last read-back
    want = page.evaluate(f"window.dfsPool.addKind(window.dfsPool.get({aid!r}), {kind!r})")
    page.locator(f'button.addpool[data-id="{aid}"]').first.click()
    read_back(page, aid, want)
    assert served.pool(aname) == want
    # hidden at once, then gone: the refreshed section no longer lists him as missing
    gone = f"(b => !b || b.hidden)(document.querySelector('button.addpool[data-id=\"{aid}\"]'))"
    until(page, gone)


def read_back(pg, pid: str, value: str):
    """Wait on the write's read-back, not on an idle queue: the page holds `value` for `pid` from the
    server's answer (nothing saving for him, no write queued) and the refresh after it has landed."""
    until(
        pg,
        f"!window.dfsPool.saving({pid!r}) && window.dfsPool.get({pid!r}) === {value!r}"
        " && !window.dfsQueue.busy() && !window.dfsLive.refreshing()",
    )


def test_a_slow_refresh_never_puts_an_old_pool_value_back_and_an_add_merges_with_the_last_read_back(
    page, served, monkeypatch
):
    """The flake behind the chip test, made to happen every time: two writes on one player back to back (GPP,
    then "+ Cash"). The render the GPP write's refresh asked for reads the sheet (GPP) and is held on a gate
    until "+ Cash" has been read back, so it always lands after a newer read-back. The add is resolved
    against the last read-back (Both, never a stale Cash or GPP), and no render puts an older value back."""
    real = week_server.render_page
    gate = threading.Event()

    def held(ctx, nonce):
        html = real(ctx, nonce)  # the sheet as it is now
        gate.wait(15)  # ...delivered only once the test lets it go
        return html

    monkeypatch.setattr(week_server, "render_page", held)
    pid, name = "id-Qb Alpha", "Qb Alpha"
    assert served.pool(name) == ""
    page.evaluate(f"window.dfsPool.request([{{id: {pid!r}, value: 'GPP'}}])")
    # GPP is read back; the refresh that write asked for is held (its render read the sheet as GPP)
    until(page, f"!window.dfsPool.saving({pid!r}) && window.dfsPool.get({pid!r}) === 'GPP'")
    until(page, "window.dfsLive.refreshing()")
    shown = f"document.querySelector('#board .seg[data-id=\"{pid}\"] button.on').dataset.v"
    # The click and what it shows at once are read in ONE evaluate: the write is a local request that can
    # land between two round trips to the browser, so "saving" read in a second call was the flake (it failed
    # every time with a 300 ms pause between the two calls; the page was right, the test was not).
    saving, at_click = page.evaluate(
        f"(() => {{ window.dfsPool.request([{{id: {pid!r}, add: 'Cash'}}]);"
        f" return [window.dfsPool.saving({pid!r}), {shown}]; }})()"
    )
    assert saving  # shown as saving until it lands
    assert at_click == "Both"  # resolved against the read-back: GPP + Cash
    until(page, f"!window.dfsPool.saving({pid!r}) && window.dfsPool.get({pid!r}) === 'Both'")
    assert served.pool(name) == "Both"
    assert page.evaluate("window.dfsLive.refreshing()")  # the GPP render is still held
    gate.set()  # the stale render (GPP) lands now, after the Both read-back
    read_back(page, pid, "Both")
    assert page.evaluate(shown) == "Both"
    page.evaluate("window.dfsLive.refresh()")
    until(page, "!window.dfsLive.refreshing()")
    assert page.evaluate(f"window.dfsPool.get({pid!r})") == "Both" and page.evaluate(shown) == "Both"


SHADE_PROBE = """(() => {
  const t = document.querySelector('#board table');
  const heads = [...t.querySelectorAll('tbody.posgrp')[0].rows[0].cells].map(c => c.textContent.trim());
  const col = (h) => heads.findIndex(x => x.endsWith(h));
  const has = (td, c) => td.classList.contains(c);
  const out = {};
  for (const g of t.querySelectorAll('tbody.posgrp')) {
    const rows = [...g.rows].filter(r => r.cells.length === heads.length && r.dataset.id);
    const per = rows.map(r => [parseFloat(r.cells[col('Per $1K')].textContent), r.cells[col('Per $1K')]]);
    if (per.length < 3) continue;
    per.sort((a, b) => b[0] - a[0]);
    const own = rows.flatMap(r => [...r.cells[col('DU own')].classList]);
    out[g.dataset.pos] = {
      topGood: has(per[0][1], 'sh-good'),
      bottomBad: has(per[per.length - 1][1], 'sh-bad'),
      salary: rows.some(r => has(r.cells[col('Salary')], 'sh')),
      ownSides: [...new Set(own.filter(c => c.startsWith('sh-') && c !== 'sh-tail'))],
    };
  }
  return out;
})()"""


def test_numbers_are_coloured_within_their_position_with_a_legend(page):
    """Plan O item 3: a z-score within the position in the sheet's palette; ownership orange; the top and
    bottom 10% bold; Salary never coloured; a legend above the table."""
    until(page, "document.querySelectorAll('#board td.sh').length > 0")
    got = page.evaluate(SHADE_PROBE)
    assert got
    for pos, g in got.items():
        assert g["topGood"] and g["bottomBad"], pos
        assert not g["salary"], pos
        assert set(g["ownSides"]) <= {"sh-own"}, pos
    assert page.locator("#board .shade-key").count() == 1
    assert page.locator("#board td.sh-tail").count() > 0


# -- Plan O2: the view bar, the new views, search and compare ----------------------------------------------


def _usage_files(served, tmp_path):
    """Two games for every skill player on the fake slate, so the usage view has rows."""
    import csv

    from dfs import usage_weeks

    data = tmp_path / "data" / "current"
    with (data / "usage_weeks.csv").open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=usage_weeks.PLAYER_COLUMNS)
        w.writeheader()
        for i, r in enumerate(x for x in served.rows if x["Position"] in ("QB", "RB", "WR", "TE")):
            for wk in (1, 2):
                w.writerow(
                    {
                        "NameKey": r["Name"].lower(),
                        "Name": r["Name"],
                        "Team": r["Team"],
                        "Position": r["Position"],
                        "Wk": wk,
                        "Opp": r["Opp"],
                        "Snap%": 0.5 + i / 100,
                        "Tgt": 3 + i % 5,
                        "Car": i % 3,
                        "Tgt%": 0.1 + i / 200,
                        "Air%": 0.1,
                        "WOPR": 0.3,
                        "RZ": 1,
                        "HVT": 0,
                        "DK": 10 + i,
                    }
                )
    with (data / "team_weeks.csv").open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=usage_weeks.TEAM_COLUMNS)
        w.writeheader()
        for t in sorted({r["Team"] for r in served.rows}):
            for wk in (1, 2):
                w.writerow(
                    {
                        "Team": t,
                        "Wk": wk,
                        "Opp": "XXX",
                        "Plays": 60,
                        "Pass%": 0.6,
                        "PROE": 1.5,
                        "Pace": 30,
                        "RZTrips": 3,
                        "Tgt": 30,
                        "Car": 25,
                    }
                )
    for r in served.rows:
        r.setdefault("NameKey", r["Name"].lower())
        cal = float(r["CalPts"])
        r["SleeperPts"], r["EspnPts"] = str(round(cal * 0.85, 1)), str(round(cal * 1.1, 1))
    write_csv(tmp_path / "data" / "current" / "edge.csv", served.rows)
    import json

    calls = [
        {
            "name": r["Name"],
            "position": r["Position"],
            "team": r["Team"],
            "salary": float(r["Salary"]),
            "calpts": float(r["CalPts"]),
            "cash": "AVOID" if i % 2 else "CORE",
            "cash_score": 0.1,
            "cash_reason": "test",
            "gpp": "FADE" if i % 3 == 0 else "NEUTRAL",
            "gpp_score": -0.1,
            "gpp_reason": "test",
            "bump": 0.0,
            "bump_from": "",
        }
        for i, r in enumerate(served.rows)
    ]
    inputs = tmp_path / "data" / "week" / "inputs"
    inputs.mkdir(parents=True, exist_ok=True)
    (inputs / "2026-wk09-calls.json").write_text(
        json.dumps({"versions": [{"at": "2026-11-08", "calls": calls}]})
    )


def visible_views(pg):
    return pg.evaluate("[...document.querySelectorAll('.vw')].filter(w => !w.hidden).map(w => w.dataset.v)")


def test_the_page_opens_on_the_clocks_day_every_view_has_its_hash_and_the_last_one_is_kept(browser, served):
    """The design pass (Sam, 2026-11-17): three day tabs in the header, every other view on the More bar, the
    clock only picks which day opens first; each view its own #name, the last one kept per week."""
    ctx = browser.new_context(viewport={"width": 1440, "height": 1000})
    pg = ctx.new_page()
    pg.goto(served.url)
    until(pg, "!!(window.dfsViews && window.dfsPool)")
    # blueprint C2 / D: the page opens on the clock's state; this fixture's slate is over, so the Close view
    assert pg.evaluate("document.body.dataset.state") == "close"
    assert pg.evaluate("window.dfsViews.current()") == "close"
    assert pg.locator("#close-card").is_visible() and pg.locator("#status-close").is_visible()
    assert not pg.locator("#overview-card").is_visible() and not pg.locator("#status-build").is_visible()
    # every day is reachable any day
    pg.click('#views a[data-view="build"]')
    until(pg, "window.dfsViews.current() === 'build'")
    assert pg.locator("#status-build").is_visible() and pg.locator("#progress").is_visible()
    assert pg.locator("#digest").is_visible() and not pg.locator("#board").is_visible()
    pg.click('#views a[data-view="lock"]')
    until(pg, "window.dfsViews.current() === 'lock'")
    assert pg.locator("#lock-card").is_visible() and pg.locator("#status-lock").is_visible()
    # More keeps every view the page had
    pg.click('#more a[data-view="usage"]')
    until(pg, "location.hash === '#usage' && window.dfsViews.current() === 'usage'")
    assert pg.locator("#usage-card").is_visible() and not pg.locator("#digest").is_visible()
    pg.click('#more a[data-view="overview"]')
    until(pg, "window.dfsViews.current() === 'overview'")
    assert pg.locator("#overview-card").is_visible() and pg.locator("#brief").is_visible()
    pg.click('#more a[data-view="model"]')
    until(pg, "window.dfsViews.current() === 'model'")
    pg.go_back()
    until(pg, "window.dfsViews.current() === 'overview'")
    pg.goto(served.url)  # no hash: the last view this week
    until(pg, "!!window.dfsViews")
    assert pg.evaluate("window.dfsViews.current()") == "overview"
    pg.goto(served.url + "#everything")
    until(pg, "window.dfsViews.current() === 'everything'")
    for sid in (
        "#board",
        "#usage-card",
        "#injury-card",
        "#model-card",
        "#explorer-card",
        "#digest",
        "#chalk-off",
    ):
        assert pg.locator(sid).count() <= 1, sid
    for sid in ("#board", "#usage-card", "#injury-card", "#model-card", "#explorer-card", "#digest"):
        assert pg.locator(sid).is_visible(), sid
    # a link to a section on another view opens a view that has it
    pg.goto(served.url + "#explore")
    until(pg, "window.dfsViews.current() === 'explore'")
    pg.evaluate("location.hash = 'numbers'")
    until(pg, "window.dfsViews.current() === 'lastweek'")
    ctx.close()


def test_every_name_on_the_new_views_carries_the_pool_chip_and_one_store(browser, served, tmp_path):
    """Plan O2: every name on the overview, usage, injury and model views has the pool control, and a pool
    click on one view shows on every view (one store)."""
    _usage_files(served, tmp_path)
    ctx = browser.new_context(viewport={"width": 1440, "height": 1000})
    pg = ctx.new_page()
    errors = []
    pg.on("pageerror", lambda e: errors.append(str(e)))
    pg.goto(served.url + "#usage")
    until(pg, "document.querySelectorAll('#uv-table tbody tr').length > 0")
    until(pg, "document.querySelectorAll('#uv-teams .bars span.pl').length > 0")
    # a name the page resolves (every priced player but OUT / IR, the Explorer's set) carries the chip
    bare = """sel => [...document.querySelectorAll(sel + ' a.pl[data-id]')].filter(a => {
        const p = a.previousElementSibling;
        const q = p && p.classList.contains('posl') ? p.previousElementSibling : p;
        return !(q && q.classList.contains('pchip'));
      }).map(a => a.textContent)"""
    plain = "sel => [...document.querySelectorAll(sel + ' a.pl:not([data-id])')].map(a => a.textContent)"
    out = {r["Name"] for r in served.rows if r.get("Avail") in ("OUT", "IR")}
    for view, sel in (
        ("usage", "#usage-card"),
        ("overview", "#overview-card"),
        ("injuries", "#injury-card"),
        ("model", "#model-card"),
    ):
        pg.evaluate(f"location.hash = '{view}'")
        until(pg, f"window.dfsViews.current() === '{view}'")
        assert pg.evaluate(f"document.querySelectorAll('{sel} a.pl').length") > 0, view
        assert pg.evaluate(bare, sel) == [], view
        assert set(pg.evaluate(plain, sel)) <= out, view
    # one store: a pool write on the overview shows on the usage view's chip
    pg.evaluate("location.hash = 'overview'")
    chip = pg.locator("#overview-card .pchip").first
    pid = chip.get_attribute("data-id")
    chip.click()
    pg.locator("#pool-pop .seg button[data-v=Cash]").click()
    read_back(pg, pid, "Cash")
    assert served.pool(pg.evaluate(f"window.dfsPool.name({pid!r})")) == "Cash"
    pg.evaluate("location.hash = 'everything'")
    until(
        pg,
        f"[...document.querySelectorAll('.pchip[data-id=\"{pid}\"]')].every(c => c.dataset.pool === 'Cash')",
    )
    assert errors == []
    ctx.close()


def test_search_finds_a_player_on_any_view_and_the_compare_tray_holds_two_to_four(page, served):
    """Plan O2 item 8: the search box on every view; + Compare from the search, the drawer or a pool chip;
    2-4 players side by side with the Compare tab's rows, the best value green."""
    page.evaluate("location.hash = 'injuries'")
    page.fill("#psearch", "qb")
    until(page, "document.querySelectorAll('#psearch-list .sr').length >= 2")
    page.keyboard.press("Enter")  # the first hit opens in the drawer
    until(page, "!document.getElementById('drawer').hidden")
    page.locator("#drawer button.cmp-add").click()
    page.keyboard.press("Escape")
    until(
        page,
        "document.getElementById('cmp-tray') && document.querySelectorAll('#cmp-tray .tchip2').length === 1",
    )
    assert page.locator("#cmp-open").is_disabled()  # one is not a comparison
    page.fill("#psearch", "qb")
    until(page, "document.querySelectorAll('#psearch-list .sr').length >= 2")
    page.locator("#psearch-list .sr").nth(1).locator("button.cmp-add").click()
    page.click("#cmp-open")
    until(page, "!!document.querySelector('#cmp-view table.cmp')")
    labels = page.evaluate("[...document.querySelectorAll('#cmp-view tr.sect td')].map(t => t.textContent)")
    assert labels[:3] == ["PLAYER", "PROJECTIONS", "OWNERSHIP"]
    best = page.evaluate(
        """[...document.querySelectorAll('#cmp-view table.cmp tr')]
           .find(r => r.cells[0].textContent === 'CalPts').querySelectorAll('td.best').length"""
    )
    assert best == 1  # the two QBs differ: one is green
    # a fifth pick pushes the oldest out: at most four
    ids = page.evaluate("window.dfsData.D.ids.slice(0, 5)")
    for pid in ids:
        page.evaluate(f"window.dfsCompare.picks().includes({pid!r}) || window.dfsCompare.add({pid!r})")
    assert len(page.evaluate("window.dfsCompare.picks()")) == 4
    page.reload()  # kept per week
    until(page, "!!window.dfsCompare")
    assert len(page.evaluate("window.dfsCompare.picks()")) == 4
    # from any name: the pool chip's popover has + Compare
    page.locator(".pchip:visible").first.click()
    assert page.locator("#pool-pop button.cmp-add").count() == 1


def test_the_ledger_fits_a_1440_wide_window_with_its_bars_and_no_scolding(browser, served, tmp_path):
    """Blueprint Phase B: the ledger fold at 1440 px: every column in view (no sideways scroll), a ±2 SE bar
    drawn on each row with two weeks or more, and nothing red."""
    from tests.test_ledger import ledger_payload, record

    week_inputs.save_input(
        "ledger", ledger_payload(entered=record()), season=2026, week=9, data_dir=tmp_path / "data"
    )
    ctx = browser.new_context(viewport={"width": 1440, "height": 1000})
    pg = ctx.new_page()
    errors: list[str] = []
    pg.on("pageerror", lambda e: errors.append(str(e)))
    pg.goto(served.url + "#everything")
    until(pg, "!!document.querySelector('#ledger details')")
    pg.evaluate("document.querySelector('#ledger details').open = true")
    pg.wait_for_timeout(100)
    box = pg.evaluate(
        """(() => { const s = document.querySelector('#ledger .scroll');
        return {sw: s.scrollWidth, cw: s.clientWidth, page: document.documentElement.scrollWidth,
                win: window.innerWidth, bars: document.querySelectorAll('#ledger svg.iv rect').length,
                red: [...document.querySelectorAll('#ledger *')].filter(n =>
                    getComputedStyle(n).color === 'rgb(255, 0, 0)'
                    || n.classList.contains('bad')).length}; })()"""
    )
    assert box["sw"] <= box["cw"] + 1, box
    assert box["page"] <= box["win"], box
    assert box["bars"] >= 2 and box["red"] == 0, box
    ctx.close()
    assert errors == []


def test_the_sunday_view_opens_by_itself_and_swap_records_without_a_sheet_write(browser, tmp_path):
    """Blueprint C2: on Sunday morning the page opens on the Sunday view; Swap posts the answer, the row
    shows it, and nothing is written to the sheet."""
    from datetime import UTC, datetime

    from tests.test_week_server import lock_files

    s = Served(tmp_path)
    try:
        lock_files(tmp_path, 2026, 9)  # Wr Star OUT at 10 am ET, in L1 on the Lineups tab
        s.server.ctx.now = lambda: datetime(2026, 11, 8, 16, 0, tzinfo=UTC)
        ctx = browser.new_context(viewport={"width": 1440, "height": 1000})
        pg = ctx.new_page()
        errors: list[str] = []
        pg.on("pageerror", lambda e: errors.append(str(e)))
        pg.goto(s.url)
        until(pg, "!!(window.dfsPool && window.dfsLive)")
        assert pg.evaluate("document.body.dataset.state") == "lock"
        assert pg.evaluate("window.dfsViews.current()") == "lock"
        assert pg.locator("#lock-card").is_visible() and pg.locator("#status-lock").is_visible()
        swap = pg.locator('#lock-card button.lockbtn[data-action="swap"]').first
        assert swap.is_enabled()
        swap.click()
        until(pg, "!!document.querySelector('#lock-card span.answered')")
        assert s.sheet.writes == []
        log = tmp_path / "data" / "week" / "decisions.jsonl"
        assert log.exists()
        # blueprint Phase D: the Tuesday view's decision log lists it; one tap gives it a reason (a new line)
        first = log.read_bytes()
        pg.goto(s.url + "#close")
        pg.reload()  # a hash-only goto keeps the old render; the log is read at render
        until(pg, "window.dfsViews && window.dfsViews.current() === 'close'")
        pg.click("#decision-log summary")
        pill = pg.locator('#decision-log button[data-reason="news"]').first
        assert pill.is_enabled()
        pill.click()
        until(pg, "!!document.querySelector('#decision-log button.on[data-reason=news]')")
        assert log.read_bytes().startswith(first) and b'"reason":"news"' in log.read_bytes()[len(first) :]
        assert s.sheet.writes == []
        ctx.close()
        assert errors == []
    finally:
        s.close()


def test_the_stale_notice_is_one_line_at_both_widths_with_the_rest_behind_a_click(browser, served):
    """Sam, 2026-11-18: the yellow notice was three lines on a desktop and seven on a phone. Now: what is
    stale and the one command that fixes it, on one line; every sentence behind the click."""
    import json
    from datetime import UTC, datetime, timedelta

    old = (datetime.now(UTC) - timedelta(days=3)).isoformat()
    manifest = {
        "edge": {"synced_at": old, "error": None},
        "tffb": {"synced_at": old, "error": "login expired"},
        "tffb_gps": {"synced_at": old, "error": None, "pending": "GPS not published"},
    }
    (served.tmp_path / "data" / "manifest.json").write_text(json.dumps(manifest))
    for width, height in ((1440, 900), (390, 844)):
        ctx = browser.new_context(viewport={"width": width, "height": height})
        pg = ctx.new_page()
        pg.goto(served.url)
        until(pg, "!!window.dfsViews")
        summary = pg.locator("#stale-note summary")
        assert "dfs sync" in summary.inner_text() and "+2 more" in summary.inner_text()
        box = summary.bounding_box()
        line = pg.evaluate(
            "parseFloat(getComputedStyle(document.querySelector('#stale-note summary')).lineHeight)"
        )
        assert box["height"] <= line + 2, (width, box)  # one line
        assert pg.evaluate("document.documentElement.scrollWidth") <= width  # and no sideways scroll
        assert not pg.locator("#stale-note li").first.is_visible()
        summary.click()
        items = pg.locator("#stale-note li")
        assert items.count() == 3 and items.first.is_visible()
        assert "login expired" not in items.all_inner_texts()[0] and "tffb" in items.all_inner_texts()[0]
        ctx.close()
