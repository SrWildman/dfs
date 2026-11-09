"""Plan O item 3: every number column in the Week page's player tables is coloured within its position,
the way the sheet's Edge Finder and Board colour a position block.

- **What is compared:** a cell against the same position's other rows in that table, as a z-score (the
  distance from the position's mean in standard deviations). A table with no position (a column headed
  Pos / Position, a `data-pos` on the row or on its group) is not coloured: there is nothing to compare by.
- **The palette is the sheet's** (`sheet_color_scales`): more is better = green (`GRAD_MAX`) above the
  position's average and the workbook's soft red (`GRAD_MIN`) below; more is worse = the reverse;
  ownership = the chalk map's orange (`BLOCK_ORANGE`), only above the average (ownership is context, not
  good or bad); a difference (Props gap) = red below 0, green above. Colour strength grows with the
  distance, full at 2 standard deviations.
- **The top and bottom 10%** of his position in the table are bold with a full-strength colour (for
  ownership only the top 10%).
- **What a column means** comes from `compare_card.SECTIONS` (which way is better, per EdgeRaw column: the
  Compare tab's own rule, so the page and the sheet cannot disagree), the ownership columns, and the page's
  own short headers (`PAGE_HEADERS`). Salary, spread, kickoff and text are never coloured (`compare_card`
  gives them no direction).
- A legend (`LEGEND`) sits right above every coloured table.

`kinds()` is the one map; `JS` / `CSS` are the client half (`window.dfsShade`), loaded with `week_ui`.
"""

from __future__ import annotations

import json

from dfs import compare_card as cc
from dfs import sheet_color_scales as scs

GOOD, BAD, OWN, DIFF = "good", "bad", "own", "diff"
OWN_COLUMNS = ("Own%", "CashOwn", "GppOwn", "OurOwn")
# Headers the page writes itself (the board, the Cash / GPP views, Stacks) and the Explorer's short ones.
PAGE_HEADERS = {
    "Proj": GOOD,  # the board's CalPts
    "Ceiling": GOOD,  # TFFB's ceiling (the game view)
    "Per $1K": GOOD,
    "Cash %": GOOD,
    "Top 1%": GOOD,
    "Lift": GOOD,
    "Lev": GOOD,
    "DU own": OWN,
    "GPP own": OWN,
    "Our own": OWN,
    "TFFB Own%": OWN,
    "Double-up own": OWN,
    "Props gap": DIFF,
    "PropsGap": DIFF,
    # the usage view and the model view (`week_views`)
    "Snap %": GOOD,
    "Tgt/G": GOOD,
    "Tgt %": GOOD,
    "Air %": GOOD,
    "WOPR": GOOD,
    "Car/G": GOOD,
    "RZ/G": GOOD,
    "HVT/G": GOOD,
    "DK/G": GOOD,
    "vs sources": DIFF,
}
FULL_AT_Z = 2.0  # colour reaches full strength this many standard deviations out
TAIL = 0.10  # the top and bottom 10% stand out
MIN_ROWS = 3  # a position with fewer rows in the table is left plain

LEGEND = (
    "Colours compare each player with his own position in this table: green = better than the position's "
    "average, red = worse (stronger = further out), orange = more owned; bold = the position's top or bottom "
    "10%. Salary, spread and text are not coloured."
)


def kinds() -> dict[str, str]:
    """Column key or header -> good / bad / own / diff. A column missing here is never coloured."""
    from dfs import week_explorer

    out: dict[str, str] = {}
    for _title, rows in cc.SECTIONS:
        for r in rows:
            if r.better is None or r.spec.source != "edge":
                continue
            kind = GOOD if r.better == cc.HIGH else BAD
            out[r.spec.column] = kind
            out.setdefault(week_explorer.HEADER.get(r.spec.column, r.spec.column), kind)
    for c in OWN_COLUMNS:
        out[c] = OWN
        out[week_explorer.HEADER.get(c, c)] = OWN
    out.update(PAGE_HEADERS)
    return out


def _hex(c: dict) -> str:
    return "#" + "".join(f"{round(c[k] * 255):02X}" for k in ("red", "green", "blue"))


GOOD_HEX, BAD_HEX, OWN_HEX = _hex(scs.GRAD_MAX), _hex(scs.GRAD_MIN), _hex(scs.BLOCK_ORANGE)

CSS = (
    f":root{{--sh-good:{GOOD_HEX};--sh-bad:{BAD_HEX};--sh-own:{OWN_HEX}}}"
    "@media (prefers-color-scheme:dark){:root:not([data-theme=light]){--sh-good:#2f7a4c;--sh-bad:#8e3b34;"
    "--sh-own:#9a6420}}"
    ":root[data-theme=dark]{--sh-good:#2f7a4c;--sh-bad:#8e3b34;--sh-own:#9a6420}"
    "td.sh{background:color-mix(in srgb,var(--sh-c) calc(var(--sh-p)*1%),transparent)}"
    "td.sh.sh-good{--sh-c:var(--sh-good)}td.sh.sh-bad{--sh-c:var(--sh-bad)}td.sh.sh-own{--sh-c:var(--sh-own)}"
    "td.sh.sh-tail{font-weight:700}"
    ".shade-key{font-size:11.5px;color:var(--dim);margin:4px 0;display:flex;flex-wrap:wrap;gap:4px 10px;"
    "align-items:center}"
    ".shade-key i{display:inline-block;width:12px;height:12px;border-radius:2px;vertical-align:-2px;"
    "margin-right:3px}"
    ".shade-key i.good{background:var(--sh-good)}.shade-key i.bad{background:var(--sh-bad)}"
    ".shade-key i.own{background:var(--sh-own)}"
)

JS = (
    r"""
(function () {
const KINDS = __KINDS__, FULL = __FULL__, TAIL = __TAIL__, MIN = __MIN__, LEGEND = __LEGEND__;
const POSH = /^(pos|pos\.|position)$/i;
function headName(th) {
  if (th.dataset.key) return th.dataset.key;
  const c = th.cloneNode(true);
  for (const x of c.querySelectorAll('.tchip, .how, .chip, abbr + sup')) x.remove();
  return c.textContent.replace(/[▲▼]/g, '').trim();
}
function num(t) {
  t = String(t || '').trim().replace(/−/g, '-');
  if (!t || t === '-' || t === '–') return null;
  t = t.replace(/[$,%x\s]/g, '').replace(/^\+/, '');
  return /^-?\d*\.?\d+$/.test(t) ? parseFloat(t) : null;
}
function legend(table) {
  const anchor = table.closest('.scroll') || table;
  const prev = anchor.previousElementSibling;
  if (prev && prev.classList.contains('shade-key')) return;
  const p = document.createElement('p'); p.className = 'shade-key'; p.title = LEGEND;
  p.innerHTML = '<span><i class="good"></i>better than his position</span><span><i class="bad"></i>worse</span>' +
    '<span><i class="own"></i>more owned</span><span><b>bold</b> = top or bottom 10%</span>';
  anchor.before(p);
}
function shade(table) {
  if (!table || table.dataset.shade === 'off' || table.closest('#drawer')) return false;
  for (const td of table.querySelectorAll('td.sh')) { td.classList.remove('sh', 'sh-good', 'sh-bad', 'sh-own', 'sh-tail'); td.style.removeProperty('--sh-p'); }
  let cols = null, posCol = -1;
  const groups = {};   // "col|pos" -> [[td, value]]
  for (const tr of table.rows) {
    const cells = [...tr.cells];
    if (cells.length && cells.every((c) => c.tagName === 'TH')) {
      cols = cells.map((th) => KINDS[headName(th)] || '');
      posCol = cells.findIndex((th) => POSH.test(headName(th)));
      continue;
    }
    if (!cols || cells.length !== cols.length) continue;
    const grp = tr.closest('[data-pos]');
    const pos = tr.dataset.pos || (grp && table.contains(grp) ? grp.dataset.pos : '') || (posCol >= 0 ? cells[posCol].textContent.trim() : '');
    if (!pos) continue;
    cells.forEach((td, i) => {
      const kind = cols[i]; if (!kind) return;
      const x = num(td.textContent); if (x == null) return;
      (groups[i + '|' + pos] = groups[i + '|' + pos] || []).push([td, x, kind]);
    });
  }
  let any = false;
  for (const list of Object.values(groups)) {
    if (list.length < MIN) continue;
    const xs = list.map((c) => c[1]), n = xs.length;
    const mean = xs.reduce((a, b) => a + b, 0) / n;
    const sd = Math.sqrt(xs.reduce((a, b) => a + (b - mean) * (b - mean), 0) / n);
    const order = xs.map((x, i) => [x, i]).sort((a, b) => a[0] - b[0]);
    const rank = new Array(n); order.forEach(([, i], r) => { rank[i] = n > 1 ? r / (n - 1) : 0.5; });
    const maxAbs = Math.max(...xs.map(Math.abs)) || 1;
    list.forEach(([td, x, kind], i) => {
      let side, strength;
      if (kind === 'diff') { side = x > 0 ? 'good' : x < 0 ? 'bad' : ''; strength = Math.abs(x) / maxAbs; }
      else {
        if (!sd) return;
        const z = (x - mean) / sd;
        if (kind === 'own') { side = z > 0 ? 'own' : ''; }
        else { const up = kind === 'good' ? z > 0 : z < 0; side = z === 0 ? '' : (up ? 'good' : 'bad'); }
        strength = Math.min(Math.abs(z) / FULL, 1);
      }
      const top = rank[i] >= 1 - TAIL, bottom = rank[i] <= TAIL;
      const tail = kind === 'own' ? top : (top || bottom);
      if (!side) return;
      td.classList.add('sh', 'sh-' + side); if (tail && n >= 5) { td.classList.add('sh-tail'); strength = 1; }
      td.style.setProperty('--sh-p', String(Math.round(15 + 70 * strength)));
      any = true;
    });
  }
  if (any) legend(table);
  return any;
}
function all(root) {
  for (const t of (root || document).querySelectorAll('table')) { try { shade(t); } catch (e) {} }
}
window.dfsShade = {table: shade, all, num};
document.addEventListener('DOMContentLoaded', () => all());
})();
""".replace("__KINDS__", json.dumps(kinds()))
    .replace("__FULL__", repr(FULL_AT_Z))
    .replace("__TAIL__", repr(TAIL))
    .replace("__MIN__", str(MIN_ROWS))
    .replace("__LEGEND__", json.dumps(LEGEND))
)
