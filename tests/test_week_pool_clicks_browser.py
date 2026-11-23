"""Sam's Week 11 note (PR 1 item 1): pool clicks in quick succession froze the page and some never landed.

A slate of a real size (about 400 players, as a main slate's EdgeRaw holds) on a fake sheet that answers as
slowly as Google does, and Sam's pattern: ten clicks across six players inside two seconds, a player clicked
again before his first write is back. Every click must land on the sheet (the last value per player), the page
must show each one at once, nothing may put an old value back, and no frame may freeze for more than 200 ms
while it happens (measured from `requestAnimationFrame` gaps, which both engines have; Chromium's long-task
entries too, where the engine reports them).

Skipped where an engine is not installed, as on CI. Nothing here touches a real sheet."""

from __future__ import annotations

import time

import pytest

from tests.test_week_ui_browser import Served, until

pytestmark = [pytest.mark.browser, pytest.mark.slow]

pytest.importorskip("playwright.sync_api")

FRAME_LIMIT_MS = 200
SHEET_LATENCY_S = 0.15  # each sheet read or write: a write is read, write, read back (about 0.45 s)

# Installed before the clicks: the longest gap between two animation frames (and Chromium's longest task).
PROBE = """(() => {
  const m = {gap: 0, long: 0, frames: 0}; let last = performance.now();
  const tick = (t) => {
    m.gap = Math.max(m.gap, t - last); last = t; m.frames++; if (!m.stop) requestAnimationFrame(tick);
  };
  requestAnimationFrame(tick);
  try {
    new PerformanceObserver((l) => { for (const e of l.getEntries()) m.long = Math.max(m.long, e.duration); })
      .observe({type: 'longtask', buffered: false});
  } catch (e) {}
  window.__frames = m;
})()"""


BOARD_IDS = "[...new Set([...document.querySelectorAll('#board .seg[data-id]')].map(s => s.dataset.id))]"


@pytest.fixture
def big(tmp_path):
    s = Served(tmp_path, extra_players=400, latency=SHEET_LATENCY_S)
    yield s
    s.close()


def test_quick_pool_clicks_all_land_without_freezing_the_page(browser, big):
    ctx = browser.new_context(viewport={"width": 1440, "height": 1000})
    pg = ctx.new_page()
    errors: list[str] = []
    pg.on("pageerror", lambda e: errors.append(str(e)))
    pg.goto(big.url + "#players")
    until(pg, "!!(window.dfsPool && window.dfsLive)", timeout_ms=20000)
    pg.wait_for_timeout(500)
    ids = pg.evaluate(BOARD_IDS)[:6]
    assert len(ids) == 6
    # ten clicks on six players; the first two and the fourth are clicked twice, the last click wins
    clicks = [
        (ids[0], "GPP"),
        (ids[1], "Cash"),
        (ids[2], "Both"),
        (ids[0], "Both"),
        (ids[3], "GPP"),
        (ids[4], "Cash"),
        (ids[1], "GPP"),
        (ids[5], "Both"),
        (ids[3], "Cash"),
        (ids[0], "Cash"),
    ]
    want = dict(clicks)
    pg.evaluate(PROBE)
    started = time.monotonic()
    for pid, v in clicks:
        pg.locator(f'#board .seg[data-id="{pid}"] button[data-v={v}]').first.click()
        # optimistic: the click shows at once, before any write is back
        shown = pg.evaluate(f"document.querySelector('#board .seg[data-id=\"{pid}\"] button.on')?.dataset.v")
        assert shown == v, f"{pid} shows {shown!r} right after a click on {v}"
        pg.wait_for_timeout(40)
    assert time.monotonic() - started < 2.0, "the clicks themselves took over two seconds"
    # every click lands: the sheet holds the last value per player, and nothing is left saving
    until(
        pg,
        "!window.dfsQueue.busy() && !window.dfsLive.refreshing() && !document.querySelector('.seg.saving')",
        timeout_ms=20000,
    )
    names = {r["Id"]: r["Name"] for r in big.rows}
    assert {pid: big.pool(names[pid]) for pid in want} == want
    # ...and the page agrees after the refresh that followed, everywhere it shows them
    for pid, v in want.items():
        assert pg.evaluate(f"window.dfsPool.get({pid!r})") == v
        assert (
            pg.evaluate(f"document.querySelector('#board .seg[data-id=\"{pid}\"] button.on')?.dataset.v") == v
        )
    pg.evaluate("window.dfsLive.refresh()")
    until(pg, "!window.dfsLive.refreshing()", timeout_ms=20000)
    for pid, v in want.items():
        assert pg.evaluate(f"window.dfsPool.get({pid!r})") == v
    m = pg.evaluate("(() => { window.__frames.stop = true; return window.__frames; })()")
    assert m["frames"] > 10
    # The frame gap is the frozen frame Sam sees (2.4 s in Chromium, 11 s in Firefox before the fix). The
    # longest long task is reported, not asserted: in headless Chromium, which rasterises in software, the
    # first paint of the board after the pointer reaches it is a ~120 ms browser frame with no page work in it
    # (traced), and it passes 200 ms when the machine is loaded by a parallel test run.
    longest = f"longest task {m['long']:.0f} ms"
    assert m["gap"] <= FRAME_LIMIT_MS, f"a frame froze for {m['gap']:.0f} ms ({longest})"
    assert pg.locator(".toast.bad").count() == 0
    ctx.close()
    assert errors == []


def test_a_click_while_the_refresh_after_a_write_runs_still_lands(browser, big):
    """A guard for the queue, not a reproduction (it passes on the code before the fix too): a click that
    arrives while the refresh after a write is still on its way is written at once, never held behind it."""
    ctx = browser.new_context(viewport={"width": 1440, "height": 1000})
    pg = ctx.new_page()
    pg.goto(big.url + "#players")
    until(pg, "!!(window.dfsPool && window.dfsLive)", timeout_ms=20000)
    a, b = pg.evaluate(BOARD_IDS)[:2]
    pg.locator(f'#board .seg[data-id="{a}"] button[data-v=GPP]').first.click()
    until(pg, f"window.dfsPool.get({a!r}) === 'GPP' && window.dfsLive.refreshing()", timeout_ms=20000)
    pg.locator(f'#board .seg[data-id="{b}"] button[data-v=Cash]').first.click()
    until(
        pg,
        f"window.dfsPool.get({b!r}) === 'Cash' && !window.dfsPool.saving({b!r}) && !window.dfsQueue.busy()",
        timeout_ms=20000,
    )
    names = {r["Id"]: r["Name"] for r in big.rows}
    assert big.pool(names[a]) == "GPP" and big.pool(names[b]) == "Cash"
    ctx.close()
