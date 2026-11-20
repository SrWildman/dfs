"""Every piece of text on the Week page is readable (Sam, 2026-11-19): in every state
(`test_week_style.states`) and every day view plus Everything, in dark and light, at 1440 px and phone
width, in Chromium and Firefox (the `browser` fixture in `conftest.py`), each element's text has a contrast
of at least 4.5:1 against what is behind it (WCAG AA; the colours read from the browser's computed styles,
alpha and opacity composited).

Every fold is opened first, so folded text is checked too, and then a player's drawer. Exempt, as WCAG has it:
a disabled control and text drawn transparent on purpose (the pool square's "+" before hover)."""

from __future__ import annotations

import pytest
from test_week_style import states

pytestmark = [pytest.mark.browser, pytest.mark.slow]

MIN_RATIO = 4.5
VIEWS = ("build", "lock", "close", "everything")
WIDTHS = {"desktop": (1440, 900), "phone": (390, 844)}

# Every element with its own text: its colour composited over the backgrounds under it (the first opaque one
# and every translucent one above it), with the opacity of the elements in between. A canvas reads any CSS
# colour syntax (rgb, color(srgb ...), color-mix's result) as RGBA.
CHECK = r"""
(minRatio) => {
  const cv = document.createElement('canvas'); cv.width = cv.height = 1;
  const cx = cv.getContext('2d', {willReadFrequently: true}), cache = new Map();
  const rgba = (s) => {
    if (cache.has(s)) return cache.get(s);
    cx.clearRect(0, 0, 1, 1); cx.fillStyle = 'rgba(0,0,0,0)'; cx.fillStyle = s; cx.fillRect(0, 0, 1, 1);
    const d = cx.getImageData(0, 0, 1, 1).data, v = [d[0], d[1], d[2], d[3] / 255];
    cache.set(s, v); return v;
  };
  const over = (top, under) => {
    const a = top[3];
    return [0, 1, 2].map((i) => top[i] * a + under[i] * (1 - a)).concat(1);
  };
  const lum = (c) => {
    const f = (v) => { v /= 255; return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); };
    return 0.2126 * f(c[0]) + 0.7152 * f(c[1]) + 0.0722 * f(c[2]);
  };
  const path = (el) => {
    const out = [];
    for (let e = el; e && e.nodeType === 1 && out.length < 4; e = e.parentElement) {
      out.unshift(e.tagName.toLowerCase() + (e.id ? '#' + e.id : '') +
        (e.classList && e.classList.length ? '.' + [...e.classList].join('.') : ''));
    }
    return out.join(' > ');
  };
  const backdrop = (el) => {
    const layers = []; let opacity = 1, found = null;
    for (let e = el; e; e = e.parentElement) {
      const cs = getComputedStyle(e);
      const bg = rgba(cs.backgroundColor);
      if (bg[3] > 0) { layers.push(bg); if (bg[3] >= 1) { found = e; break; } }
      opacity *= parseFloat(cs.opacity);
    }
    let base = found ? layers.pop() : rgba(getComputedStyle(document.documentElement).backgroundColor);
    if (base[3] < 1) base = [255, 255, 255, 1];
    while (layers.length) base = over(layers.pop(), base);
    return [base, opacity];
  };
  const bad = []; let checked = 0;
  for (const el of document.body.querySelectorAll('*')) {
    if (['SCRIPT', 'STYLE', 'TEMPLATE', 'NOSCRIPT', 'TITLE'].includes(el.tagName)) continue;
    const own = [...el.childNodes].some((n) => n.nodeType === 3 && n.textContent.trim());
    if (!own) continue;
    if (!el.getClientRects().length) continue;
    const cs = getComputedStyle(el);
    if (cs.visibility !== 'visible' || cs.display === 'none') continue;
    if (el.closest(':disabled, [hidden]')) continue;
    const svg = el instanceof SVGElement;
    let fg = rgba(svg ? cs.fill : cs.color);
    if (fg[3] === 0) continue;
    const [bg, opacity] = backdrop(svg ? el.closest('svg').parentElement : el);
    fg = over([fg[0], fg[1], fg[2], fg[3] * opacity], bg);
    const a = lum(fg), b = lum(bg);
    const ratio = (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05);
    checked++;
    if (ratio < minRatio) {
      const text = [...el.childNodes].filter((n) => n.nodeType === 3).map((n) => n.textContent).join('');
      const what = `"${text.trim().slice(0, 40)}"  fg ${fg.slice(0, 3)} on ${bg.slice(0, 3)}`;
      bad.push(`${ratio.toFixed(2)}  ${path(el)}  ${what}`);
    }
  }
  return {checked, bad};
}
"""


@pytest.fixture(scope="module")
def pages(tmp_path_factory):
    out = tmp_path_factory.mktemp("states")
    files = {}
    for name, page in states(out).items():
        (out / f"{name}.html").write_text(page)
        files[name] = (out / f"{name}.html").as_uri()
    return files


def check(pg) -> tuple[int, list[str]]:
    r = pg.evaluate(CHECK, MIN_RATIO)
    return r["checked"], r["bad"]


@pytest.mark.parametrize("width", WIDTHS)
@pytest.mark.parametrize("theme", ["dark", "light"])
def test_every_text_on_the_page_reads_at_4_5_to_1_in_every_state(browser, pages, theme, width):
    w, h = WIDTHS[width]
    ctx = browser.new_context(viewport={"width": w, "height": h}, color_scheme=theme)
    failures: list[str] = []
    try:
        for name, url in pages.items():
            pg = ctx.new_page()
            for view in VIEWS:
                pg.goto(f"{url}#{view}")
                pg.wait_for_timeout(150)
                pg.evaluate("() => document.querySelectorAll('details').forEach((d) => { d.open = true; })")
                pg.wait_for_timeout(100)
                checked, bad = check(pg)
                # the browser's own controls (the file input, search fields) follow the theme too
                assert pg.evaluate("getComputedStyle(document.documentElement).colorScheme") == theme, name
                assert checked > 50, (name, view)
                failures += [f"{name} #{view}: {b}" for b in bad]
            # a player's drawer, over the open-spots table when there is one
            link = pg.locator("#open-spots a.pl, a.pl").first
            if link.count():
                link.click()
                pg.wait_for_timeout(150)
                failures += [f"{name} drawer: {b}" for b in check(pg)[1]]
            pg.close()
    finally:
        ctx.close()
    assert failures == [], (
        f"{len(failures)} below {MIN_RATIO}:1 ({browser.browser_type.name}, {theme}, {width}):\n"
        + "\n".join(sorted(set(failures))[:80])
    )
