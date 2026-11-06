"""Plan N's page items in a real browser (Playwright's Chromium), against the served page on a fake sheet: the
page state across a refresh and a reload, one pool store, the bulk write with a conflicting cell, the queue,
the chart viewer (zoom, box, lasso, hover), the game view's writes and the chat toggle remembered.

Skipped where Chromium is not installed (CI runs no browser; `playwright install chromium` enables these
locally). Nothing here touches a real sheet: the server writes a `FakeSheet`."""

from __future__ import annotations

import contextlib
import threading

import pytest

from dfs import sheet_suggested as ss
from dfs import week_server, week_writes
from tests.test_week_money import OWN, rows_with_more_wrs
from tests.test_week_plays import row, write_csv
from tests.test_week_writes import FakeSheet

sync_api = pytest.importorskip("playwright.sync_api")

EDGE = "EdgeRaw"


@pytest.fixture(scope="module")
def browser():
    try:
        pw = sync_api.sync_playwright().start()
    except Exception as exc:  # noqa: BLE001
        pytest.skip(f"no Playwright driver: {exc}")
    try:
        b = pw.chromium.launch()
    except Exception as exc:  # noqa: BLE001 - no browser installed (CI)
        pw.stop()
        pytest.skip(f"Chromium is not installed: {exc}")
    yield b
    b.close()
    pw.stop()


class Served:
    def __init__(self, tmp_path):
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
        for r in self.rows:
            r["Tgt%"], r["Snap%"] = "0.2", "0.7"
        write_csv(tmp_path / "data" / "current" / "edge.csv", self.rows)
        sheet = FakeSheet()
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
    pg.goto(served.url)
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
    page.locator(".ex-chart[data-chart=usage] svg.chart").click(position={"x": 30, "y": 20})
    page.wait_for_selector("#viewer circle.dot")
    assert "Usage" in page.locator("#viewer h2").inner_text()


def test_the_game_view_adds_the_ticked_as_gpp_and_writes_a_more_stack(page, served):
    page.locator('#games .game[data-game="2026_09_DAL_IND"]').click()
    page.wait_for_selector("#gameview input.sel")
    assert page.locator("#gameview h3").all_inner_texts()[0].startswith("DAL")
    for name in ("Qb Alpha", "Wr Value", "Te Fade"):  # the QB, a mate, a bring-back
        page.locator(f'#gameview input.sel[data-name="{name}"]').check()
    page.locator("#gameview button[data-act=gpp]").click()
    settle(page)
    assert [served.pool(n) for n in ("Qb Alpha", "Wr Value", "Te Fade")] == ["GPP", "GPP", "GPP"]
    page.locator("#gameview button[data-act=stack]").click()
    settle(page)
    assert served.sheet.get(ss.TAB, 4, ss.NAME_INDEX) == "more Qb Alpha, more Wr Value, more Te Fade"
    page.keyboard.press("Escape")
    page.locator('#board a.tm[data-team="IND"]').first.click()  # an Opp cell opens the same game
    assert page.locator("#gameview").is_visible()


def test_the_chat_toggle_is_remembered_across_a_reload(page):
    assert not page.evaluate("document.querySelector('#chat').classList.contains('collapsed')")
    page.locator("#chat-toggle").click()
    assert page.evaluate("document.body.classList.contains('chat-closed')")
    page.reload()
    until(page, "!!window.dfsLive")
    assert page.evaluate("document.querySelector('#chat').classList.contains('collapsed')")
    page.locator("#chat").click()
    assert not page.evaluate("document.querySelector('#chat').classList.contains('collapsed')")
