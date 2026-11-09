"""The Week page's shared client layer (Plan N items 1-10, 14): one script and one stylesheet on both the static
page (`dfs week page`) and the served one (`dfs week serve`), where the served page's own script
(`week_live.JS`) plugs in through `window.dfsLive` (its `pool` and `stack` writes, `refresh` and `toast`).

- **The pool store** (`window.dfsPool`, item 6): one client-side copy of every player's Pool value, from the
  Explorer's data and the page's Pool controls. Every place that shows a pool value (the board's and the
  drawer's Cash / GPP / Both / none controls, the Explorer's Pool column, the decisions' pivot buttons, the
  consensus table's Pool cells, the game view) paints from it, and a write's read-back updates it, so nothing
  needs a manual refresh.
- **The write queue** (item 7): a pool change never waits for the last one and is never refused for being
  second. Changes queue while a write runs; each cell shows "saving..." and then the value the sheet read back.
  A cell the sheet refused (it changed since the page read it, or the player is not on the tab) stays on its
  own row in the issues list, with the sheet's value and Try again, until Sam clears it: never dropped. The
  served page's other writes (decisions, the starting pool) go through the same queue (`dfsQueue`).
- **Multi-select** (item 7): a tick box on every board and Explorer row (shift-click ticks the range), one
  selection across the page and the chart viewer, and the bulk bar: Cash / GPP / Both / none for all of them,
  one batched write (`/api/pool` with `each`: the conflict check per cell, `week_writes.set_pool_each`).
- **Page state** (item 5): which sections and folds are open, which positions' Show more is expanded and the
  scroll position, kept per browser (localStorage, every access in try/catch) and put back after every refresh
  of the sections (a command's end, a chat turn, a write) and on reload.
- **The chart viewer** (item 9): any value / leverage / usage chart opens full screen on click: pan by
  dragging (the default, Plan O), zoom on the wheel, a pinch or a box (Shift-drag or Zoom box), Reset, hover for name / team / salary / projection / ownership, position
  filters, labels that declutter as you zoom, and selection by click, box or lasso into the shared selection.
  Inline SVG and plain JS, from `week_charts.viewer_data` (the inline charts' own points).
- **The game view** (item 10): a slate card, or any team or Opp cell, opens the game: both teams side by side
  by position with salary, CalPts, ceiling, GPP own and leverage, the line and implied totals, and the Stacks
  input's rows for the game; tick a QB, mates and a bring-back to add them as GPP or to write them as a Max
  exposure `more` stack (served page only, `/api/stack`).
- **"How this is built"** (item 14): the note every table and chart carries (`week_page.how_html`) shows on
  hover, and on a tap (toggled here).

Nothing here sends anything anywhere: the static page has no server, and on the served page every write goes
through `dfsLive` to this Mac's own server.
"""

from __future__ import annotations

CSS = r"""
.how{font-size:11.5px;color:var(--dim);cursor:help;position:relative;white-space:nowrap;font-weight:400;
border-bottom:1px dotted var(--dim);outline-offset:2px}
.card-head .how{margin-left:auto}.card-head .asof+.how{margin-left:10px}
.tbl{margin:4px 0 8px}.tbl>.how{display:inline-block;margin:0 0 2px}
.how:hover::after,.how:focus::after,.how.show::after{content:attr(data-how);position:absolute;left:0;top:130%;
width:min(380px,80vw);white-space:normal;z-index:40;background:var(--ink);color:var(--card);border-radius:7px;
padding:7px 10px;font-size:12.5px;line-height:1.4;box-shadow:0 4px 14px rgba(0,0,0,.2)}
.card-head .how:hover::after,.card-head .how:focus::after,.card-head .how.show::after{left:auto;right:0}
.chip.chalk{background:var(--accent-soft);color:var(--bad);border:1px solid currentColor}
.chip.close{background:transparent;color:var(--dim);border:1px dashed currentColor}
input.sel{margin:0 6px 0 0;vertical-align:-1px;cursor:pointer}
.seg.saving button{opacity:.6}.seg.saving::after{content:"saving\2026";font-size:11px;color:var(--dim);
margin-left:5px;align-self:center}
.seg.bad button.on{outline:2px solid var(--bad)}
.pchip{font-size:10.5px;line-height:1.3;padding:0 5px;margin-left:4px;border:1px dashed var(--line);
border-radius:9px;background:transparent;color:var(--dim);cursor:pointer;vertical-align:1px;white-space:nowrap}
.pchip.in{border-style:solid;border-color:var(--accent);color:var(--accent);font-weight:600}
.pchip:hover{border-color:var(--accent);color:var(--accent)}
#pool-pop{position:absolute;z-index:60;background:var(--card);border:1px solid var(--line);border-radius:8px;
padding:8px 10px;box-shadow:0 6px 24px rgba(0,0,0,.18);display:flex;flex-direction:column;gap:6px;max-width:300px}
#pool-pop b{font-size:13px}#pool-pop p{margin:0;font-size:11.5px}
button.addpool{font-size:11px;padding:0 6px;margin-left:3px;border:1px solid var(--accent);border-radius:9px;
background:var(--accent-soft);color:var(--accent);cursor:pointer;font-weight:600}
a.pl-unknown{text-decoration:none;color:inherit;cursor:text}
tr.pool-bad td{box-shadow:inset 0 -2px 0 var(--bad)}
.poolval{font-size:12px;color:var(--dim);white-space:nowrap}.poolval.in{color:var(--accent);font-weight:600}
#bulkbar{position:sticky;bottom:10px;z-index:15;margin:10px auto;max-width:980px;background:var(--card);
border:2px solid var(--accent);border-radius:10px;padding:8px 12px;box-shadow:0 6px 18px rgba(0,0,0,.18);
display:flex;flex-wrap:wrap;gap:6px 10px;align-items:center;font-size:13.5px}
#bulkbar[hidden]{display:none}#bulkbar .names{color:var(--dim);flex:1 1 220px;min-width:0;overflow:hidden;
text-overflow:ellipsis;white-space:nowrap}
#bulkbar button.v{border-color:var(--accent);color:var(--accent);font-weight:600}
#pool-issues{position:sticky;bottom:10px;z-index:15;margin:10px auto;max-width:980px}
#pool-issues[hidden]{display:none}
#pool-issues ul{list-style:none;margin:0;padding:0}
#pool-issues li{background:var(--bad-soft);color:var(--ink);border:1px solid var(--bad);border-radius:8px;
padding:6px 10px;margin:4px 0;display:flex;flex-wrap:wrap;gap:4px 10px;align-items:center;font-size:13px}
#pool-issues li b{color:var(--bad)}
#pool-issues li .sp{flex:1 1 200px}
.overlay{position:fixed;inset:0;z-index:25;background:var(--bg);display:flex;flex-direction:column}
.overlay[hidden]{display:none}
.vbar{display:flex;flex-wrap:wrap;gap:6px 12px;align-items:center;padding:8px 14px;border-bottom:1px solid var(--line);
background:var(--card)}
.vbar h2{margin-right:6px}.vbar .grp{display:inline-flex;gap:3px;align-items:center;flex-wrap:wrap}
.vbar button.on{background:var(--accent);color:#fff;border-color:var(--accent)}
.vbar label{font-size:13px;display:inline-flex;gap:3px;align-items:center}
.vbar .x{margin-left:auto;font-size:18px;line-height:1;padding:2px 10px}
.vbody{flex:1;display:flex;min-height:0}
.vplot{flex:1;position:relative;min-width:0;touch-action:none}
.vplot svg{position:absolute;inset:0;width:100%;height:100%;user-select:none;-webkit-user-select:none}
.vplot svg .dot{cursor:pointer}.vplot svg .dot.sel{stroke:var(--ink);stroke-width:2.5;opacity:1}
.vplot svg .dot.dim{opacity:.18}
.vplot svg .lbl{pointer-events:none}
.vplot svg .rubber{fill:var(--accent-soft);fill-opacity:.45;stroke:var(--accent);stroke-dasharray:4 3}
.vtip{position:absolute;pointer-events:none;background:var(--ink);color:var(--card);border-radius:7px;
padding:6px 9px;font-size:12.5px;line-height:1.35;z-index:2;max-width:260px}
.vtip[hidden]{display:none}
.vside{width:300px;flex:none;border-left:1px solid var(--line);background:var(--card);padding:10px 12px;
overflow-y:auto;display:flex;flex-direction:column;gap:8px}
.vside ul{list-style:none;margin:0;padding:0;font-size:13px}.vside li{display:flex;gap:6px;align-items:center;
padding:3px 0;border-bottom:1px solid var(--line)}.vside li a{flex:1}
.vside .bulk{display:flex;flex-wrap:wrap;gap:4px}
.vhelp{font-size:12px;color:var(--dim)}
.gv{max-width:1200px;margin:0 auto;width:100%;padding:12px 16px 0;overflow-y:auto;flex:1}
.gv .teams{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,420px),1fr));gap:14px}
.gv h3{margin:6px 0}.gv table td,.gv table th{padding:4px 8px 4px 0}
.gv .line{font-size:14px;margin:4px 0 10px}
.vbar .gnav select{font:inherit;max-width:260px;background:var(--bg);color:var(--ink);border:1px solid var(--line);
border-radius:5px;padding:2px 6px}
.gv .actions{position:sticky;bottom:0;z-index:5;background:var(--bg);padding:8px 0;display:flex;flex-wrap:wrap;gap:6px 10px;
align-items:center;border-top:1px solid var(--line)}
a.tm{color:inherit;text-decoration:none;border-bottom:1px dotted var(--dim);cursor:pointer}
a.tm:hover{color:var(--accent);border-color:var(--accent)}
.game{cursor:pointer}.game:hover{border-color:var(--accent)}
.chartcard svg.chart{cursor:zoom-in}.chartcard svg.chart .dot{cursor:pointer}
.expand{font-size:12px;padding:1px 8px}
@media(max-width:700px){.vbody{flex-direction:column}.vside{width:auto;border-left:0;border-top:1px solid var(--line);
max-height:38vh}}
"""

JS = r"""
(() => {
const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => [...r.querySelectorAll(s)];
const el = (tag, cls, text) => { const e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; };
const LIVE = document.body.classList.contains('live');
const RO_META = document.querySelector('meta[name=dfs-readonly]');
const READONLY = RO_META ? RO_META.content === '1' : !LIVE;
const WEEK = (document.querySelector('meta[name=dfs-week]') || {}).content || 'week';
const store = {
  get(k, d) { try { const v = localStorage.getItem('dfs-' + k); return v == null ? d : JSON.parse(v); } catch (e) { return d; } },
  set(k, v) { try { localStorage.setItem('dfs-' + k, JSON.stringify(v)); } catch (e) {} },
};
window.dfsStore = store;
const jsonOf = (id) => { const n = document.getElementById(id); if (!n) return null; try { return JSON.parse(n.textContent); } catch (e) { return null; } };
function toast(text, bad) {
  if (window.dfsLive && window.dfsLive.toast) return window.dfsLive.toast(text, bad);
  const t = el('div', 'toast' + (bad ? ' bad' : ''), text); document.body.append(t); setTimeout(() => t.remove(), bad ? 9000 : 4000);
}
const NOT_SERVED = 'Read only here: the served page (dfs week serve) writes the sheet\'s Pool cell';

/* ---------- "how this is built": hover shows it, a tap toggles it ---------- */
document.addEventListener('click', (e) => {
  const h = e.target.closest('.how');
  for (const o of $$('.how.show')) if (o !== h) o.classList.remove('show');
  if (h) { e.preventDefault(); h.classList.toggle('show'); }
});

/* ---------- the pool store (item 6) ---------- */
const P = {val: {}, name: {}, team: {}, byName: {}, saving: {}, bad: {}};
function adoptFromExplorer(d) {
  if (!d || !d.columns) return;
  const at = (k) => d.columns.findIndex((c) => (c.key || c) === k);
  const pi = at('Pool'), ni = at('Name'), ti = at('Team');
  P.byName = {};
  d.ids.forEach((id, i) => {
    if (!(id in P.saving)) P.val[id] = d.rows[i][pi] || '';
    const name = d.rows[i][ni]; P.name[id] = name; P.team[id] = ti >= 0 ? d.rows[i][ti] : '';
    (P.byName[name] = P.byName[name] || []).push(id);
  });
}
function adoptFromDom(root) {
  for (const seg of $$('.seg[data-id]', root || document)) {
    const id = seg.dataset.id; if (seg.dataset.name) P.name[id] = seg.dataset.name;
    if (!(id in P.saving)) P.val[id] = seg.dataset.was || '';
  }
}
function paint(id) {
  const v = P.val[id] || '', saving = id in P.saving, bad = id in P.bad, shown = saving ? P.saving[id] : v;
  for (const seg of $$('.seg[data-id="' + CSS.escape(id) + '"]')) {
    seg.dataset.was = v; seg.classList.toggle('saving', saving); seg.classList.toggle('bad', bad);
    for (const b of seg.querySelectorAll('button')) b.classList.toggle('on', b.dataset.v === shown);
  }
  for (const s of $$('.poolval[data-id="' + CSS.escape(id) + '"]')) {
    s.textContent = saving ? 'saving…' : (v || 'not in pool'); s.classList.toggle('in', !!v);
  }
  for (const b of $$('button.pivot[data-pivot="' + CSS.escape(id) + '"]')) b.dataset.was = v;
  for (const c of $$('.pchip[data-id="' + CSS.escape(id) + '"]')) chipPaint(c, v, saving);
  for (const b of $$('button.addpool[data-id="' + CSS.escape(id) + '"]')) b.hidden = !!v && pool.addKind(v, b.dataset.v) === v;
  for (const tr of $$('tr[data-id="' + CSS.escape(id) + '"]')) tr.classList.toggle('pool-bad', bad);
  if (window.dfsExplorer) window.dfsExplorer.setPool(id, v);
}
function paintAll() { const ids = new Set([...Object.keys(P.val), ...$$('[data-id]').map((n) => n.dataset.id)]); for (const id of ids) paint(id); }
const pool = {
  get: (id) => P.val[id] || '',
  name: (id) => P.name[id] || id,
  set(values) { for (const [id, v] of Object.entries(values || {})) { P.val[id] = v || ''; delete P.bad[id]; paint(id); } if (window.dfsExplorer) window.dfsExplorer.render(); drawIssues(); },
  adopt(root) { adoptFromDom(root); decorate(root); paintAll(); if (window.dfsShade) window.dfsShade.all(root); },
  adoptExplorer(d) { adoptFromExplorer(d); },
  paint, paintAll,
  saving: (id) => id in P.saving,
  issues: () => Object.assign({}, P.bad),
};
window.dfsPool = pool;

/* ---------- the write queue (item 7): one write at a time, the rest wait their turn ---------- */
const Q = [];
let busy = false;
async function pump() {
  if (busy) return; busy = true;
  try {
    while (Q.length) { const job = Q.shift(); try { await job(); } catch (e) { toast(e.message || String(e), true); } }
  } finally {
    busy = false;
    if (window.dfsLive && window.dfsLive.refresh) await window.dfsLive.refresh();
  }
}
window.dfsQueue = { run(job) { Q.push(job); pump(); }, busy: () => busy || Q.length > 0 };

function requestPool(changes, label) {
  if (!LIVE || READONLY) { toast(READONLY && LIVE ? 'This week is closed: the page writes nothing.' : NOT_SERVED, true); return; }
  changes = changes.filter((c) => c.id);
  if (!changes.length) return;
  for (const c of changes) { P.saving[c.id] = c.value; delete P.bad[c.id]; paint(c.id); }
  drawIssues();
  window.dfsQueue.run(async () => {
    // `was` is what the page shows now: the last read-back, never a guess
    const body = {each: true, changes: changes.map((c) => ({id: c.id, value: c.value, was: P.val[c.id] || ''}))};
    let r;
    try { r = await window.dfsLive.pool(body); }
    catch (e) {
      for (const c of changes) { delete P.saving[c.id]; P.bad[c.id] = {want: c.value, why: e.message || 'the write failed'}; paint(c.id); }
      drawIssues(); toast(e.message, true); return;
    }
    for (const c of changes) {
      const res = (r.results || {})[c.id] || {status: 'missing', value: P.val[c.id] || ''};
      delete P.saving[c.id];
      if (res.status === 'ok') { P.val[c.id] = res.value || ''; delete P.bad[c.id]; }
      else if (res.status === 'conflict') { P.val[c.id] = res.value || ''; P.bad[c.id] = {want: c.value, why: 'changed in the sheet since the page read it: the sheet has ' + (res.value || 'nothing')}; }
      else { P.bad[c.id] = {want: c.value, why: 'not on EdgeRaw (sync, then try again)'}; }
      paint(c.id);
    }
    if (window.dfsExplorer) window.dfsExplorer.render();
    drawIssues();
    toast((label ? label + ': ' : '') + (r.message || 'written'), Object.keys(r.results || {}).some((k) => r.results[k].status !== 'ok'));
  });
}
pool.request = requestPool;
/* the Cash / GPP / Both / none control, for any place that draws a player from script (the game view, the
   overview, the compare tray): the same markup the server draws, so the same click handler writes it */
pool.seg = function (id, name) {
  const v = pool.get(id); const s = el('span', 'seg'); s.dataset.id = id; s.dataset.name = name || pool.name(id); s.dataset.was = v;
  for (const [val, lab] of [['Cash', 'Cash'], ['GPP', 'GPP'], ['Both', 'Both'], ['', 'none']]) {
    const b = el('button', v === val ? 'on' : '', lab); b.type = 'button'; b.dataset.v = val;
    if (!LIVE || READONLY) { b.disabled = true; b.title = READONLY && LIVE ? 'This week is closed' : NOT_SERVED; }
    s.append(b);
  }
  return s;
};
/* the value a decision-style "add" gives: GPP + Cash is Both (week_writes.add_kind) */
pool.addKind = (cur, kind) => (cur === 'Both' || cur === kind) ? cur : (cur ? 'Both' : kind);

/* ---------- a pool chip on every name (Plan O item 2) ----------
   Every `a.pl` on the page opens the drawer and carries a small chip with his pool value; the chip opens the
   same Cash / GPP / Both / none control. A name drawn from a section that holds only the name (`pn` in
   week_page) is resolved here to his id from the Explorer's data: by name, then by team where two share one;
   a name that stays ambiguous or unknown is left as plain text, never guessed. A row that already has the
   control (the board, the Explorer, the game view) gets no second one. */
function resolveName(a) {
  if (a.dataset.id) return a.dataset.id;
  let ids = P.byName[a.dataset.name || a.textContent.trim()] || [];
  if (ids.length > 1 && a.dataset.team) ids = ids.filter((id) => P.team[id] === a.dataset.team);
  if (ids.length !== 1) { a.classList.add('pl-unknown'); return ''; }
  a.dataset.id = ids[0]; return ids[0];
}
function chipPaint(c, v, saving) {
  c.textContent = saving ? '\u2026' : (v || '+ pool'); c.classList.toggle('in', !!v);
  c.setAttribute('aria-label', 'Pool: ' + (v || 'not in pool') + '. Change');
}
function decorate(root) {
  for (const a of $$('a.pl', root || document)) {
    if (a.closest('#drawer, .seg, svg, #pool-pop')) continue;
    const id = resolveName(a); if (!id) continue;
    const next = a.nextElementSibling;
    if (next && next.classList.contains('pchip')) continue;
    const ctx = a.closest('tr, li, .gp-row');
    if (ctx && ctx.querySelector('.seg[data-id="' + CSS.escape(id) + '"]')) continue;
    const c = el('button', 'pchip'); c.type = 'button'; c.dataset.id = id; c.title = 'Your pool: Cash / GPP / Both / none';
    chipPaint(c, pool.get(id), id in P.saving); a.after(c);
  }
}
pool.decorate = decorate;
document.addEventListener('click', (e) => { const a = e.target.closest('a.pl:not([data-id])'); if (a) e.preventDefault(); });
function popClose() { const o = $('#pool-pop'); if (o) o.remove(); }
document.addEventListener('click', (e) => {
  const c = e.target.closest('.pchip[data-id]');
  if (!c) { if (!e.target.closest('#pool-pop')) popClose(); return; }
  e.preventDefault(); e.stopPropagation();
  const was = $('#pool-pop'); popClose(); if (was && was.dataset.id === c.dataset.id) return;
  const id = c.dataset.id, o = el('div'); o.id = 'pool-pop'; o.dataset.id = id; o.setAttribute('role', 'dialog');
  o.append(el('b', null, pool.name(id)), pool.seg(id, pool.name(id)));
  if (!LIVE || READONLY) o.append(el('p', 'dim', READONLY && LIVE ? 'This week is closed: the page writes nothing.' : NOT_SERVED));
  document.body.append(o);
  const r = c.getBoundingClientRect();
  o.style.top = (window.scrollY + r.bottom + 4) + 'px';
  o.style.left = Math.max(8, Math.min(window.scrollX + r.left, window.scrollX + document.documentElement.clientWidth - o.offsetWidth - 8)) + 'px';
  const first = o.querySelector('button.on') || o.querySelector('button'); if (first) first.focus({preventScroll: true});
});
document.addEventListener('keydown', (e) => { if (e.key === 'Escape' && $('#pool-pop')) { popClose(); e.stopPropagation(); } }, true);
/* "missing from your pool": one click adds him (merged: GPP + Cash = Both) */
document.addEventListener('click', (e) => {
  const b = e.target.closest('button.addpool[data-id]'); if (!b) return;
  e.preventDefault();
  const id = b.dataset.id, cur = id in P.saving ? P.saving[id] : pool.get(id), want = pool.addKind(cur, b.dataset.v);
  if (want !== cur) requestPool([{id, value: want}], 'Added ' + pool.name(id));
});

function drawIssues() {
  let box = $('#pool-issues');
  const ids = Object.keys(P.bad);
  if (!box) { if (!ids.length) return; box = el('div'); box.id = 'pool-issues'; box.setAttribute('role', 'status'); const main = $('main'); (main || document.body).append(box); }
  box.replaceChildren(); box.hidden = !ids.length;
  if (!ids.length) return;
  const ul = el('ul');
  for (const id of ids) {
    const b = P.bad[id]; const li = el('li');
    li.append(el('b', null, pool.name(id) + ' → ' + (b.want || 'none') + ': not written'), el('span', 'sp', b.why));
    const again = el('button', null, 'Try again'); again.type = 'button'; again.onclick = () => requestPool([{id, value: b.want}], 'Try again');
    const keep = el('button', null, 'Keep the sheet\'s'); keep.type = 'button'; keep.onclick = () => { delete P.bad[id]; paint(id); drawIssues(); };
    li.append(again, keep); ul.append(li);
  }
  box.append(ul);
}

/* single clicks on any Cash / GPP / Both / none control go through the queue too */
document.addEventListener('click', (e) => {
  const sb = e.target.closest('.seg[data-id] button[data-v]'); if (!sb || sb.disabled) return;
  const seg = sb.closest('.seg'); const id = seg.dataset.id;
  const shown = id in P.saving ? P.saving[id] : (P.val[id] || '');
  if (sb.dataset.v === shown) return;
  e.stopPropagation();
  try { sb.focus({preventScroll: true}); } catch (err) {}  // Safari does not focus a clicked button; the refresh finds it again
  requestPool([{id, value: sb.dataset.v}]);
}, true);

/* ---------- multi-select (item 7) ---------- */
const SEL = new Set();
let lastTick = null;
function selBox(id) {
  const cb = el('input', 'sel'); cb.type = 'checkbox'; cb.dataset.id = id; cb.checked = SEL.has(id); cb.setAttribute('aria-label', 'Select'); return cb;
}
function syncTicks() { for (const cb of $$('input.sel[data-id]')) cb.checked = SEL.has(cb.dataset.id); drawBulk(); if (window.dfsViewer) window.dfsViewer.redraw(); }
const selection = {
  has: (id) => SEL.has(id), ids: () => [...SEL], box: selBox,
  add(ids) { for (const id of ids) SEL.add(id); syncTicks(); },
  remove(ids) { for (const id of ids) SEL.delete(id); syncTicks(); },
  toggle(id) { if (SEL.has(id)) SEL.delete(id); else SEL.add(id); syncTicks(); },
  clear() { SEL.clear(); syncTicks(); },
};
window.dfsSel = selection;
document.addEventListener('click', (e) => {
  const cb = e.target.closest('input.sel[data-id]'); if (!cb || cb.closest('#gameview')) return;
  const table = cb.closest('table');
  if (e.shiftKey && lastTick && table && lastTick.table === table) {
    const boxes = $$('input.sel[data-id]', table).filter((b) => !b.closest('tr[hidden]'));
    const a = boxes.findIndex((b) => b.dataset.id === lastTick.id), z = boxes.indexOf(cb);
    if (a >= 0 && z >= 0) { const [lo, hi] = a < z ? [a, z] : [z, a]; for (const b of boxes.slice(lo, hi + 1)) { if (cb.checked) SEL.add(b.dataset.id); else SEL.delete(b.dataset.id); } }
  } else if (cb.checked) SEL.add(cb.dataset.id); else SEL.delete(cb.dataset.id);
  lastTick = {table, id: cb.dataset.id};
  syncTicks();
});
function bulk(value) {
  const ids = selection.ids(); if (!ids.length) return;
  requestPool(ids.map((id) => ({id, value})), ids.length + ' players');
  if (LIVE && !READONLY) selection.clear();
}
selection.apply = bulk;
function drawBulk() {
  let bar = $('#bulkbar');
  if (!bar) {
    bar = el('div'); bar.id = 'bulkbar'; bar.setAttribute('role', 'region'); bar.setAttribute('aria-label', 'Selected players');
    const main = $('main'); (main || document.body).append(bar);
  }
  const ids = selection.ids(); bar.hidden = !ids.length; bar.replaceChildren(); if (!ids.length) return;
  bar.append(el('b', null, ids.length + ' selected'), el('span', 'names', ids.map((i) => pool.name(i)).join(', ')));
  for (const [v, lab] of [['Cash', 'Cash'], ['GPP', 'GPP'], ['Both', 'Both'], ['', 'none']]) {
    const b = el('button', 'v', lab); b.type = 'button'; b.dataset.bulk = v;
    if (!LIVE || READONLY) { b.disabled = true; b.title = READONLY && LIVE ? 'This week is closed' : NOT_SERVED; }
    b.onclick = () => bulk(v); bar.append(b);
  }
  const c = el('button', null, 'Clear'); c.type = 'button'; c.onclick = () => selection.clear(); bar.append(c);
}

/* ---------- page state across refreshes and reloads (item 5) ---------- */
const UKEY = 'ui-' + WEEK;
const U = Object.assign({open: {}, more: [], scroll: 0, boxes: {}}, store.get(UKEY, {}));
if (!U.boxes || typeof U.boxes !== 'object') U.boxes = {};
function scroller() { const pc = $('#pagecol'); return pc && getComputedStyle(pc).overflowY === 'auto' && pc.scrollHeight > pc.clientHeight ? pc : null; }
function scrollPos() { const s = scroller(); return s ? s.scrollTop : window.scrollY; }
function setScroll(y) { const s = scroller(); if (s) s.scrollTop = y; else window.scrollTo(0, y); }
function detailsKey(d) {
  const sec = d.closest('section[id]'); if (!sec) return null;
  return sec.id + ':' + $$('details', sec).indexOf(d);
}
document.addEventListener('toggle', (e) => {
  const d = e.target; if (!(d instanceof HTMLDetailsElement) || d.closest('.overlay') || UI.restoring) return;
  const k = detailsKey(d); if (k) { U.open[k] = d.open; store.set(UKEY, U); }
}, true);
document.addEventListener('click', (e) => {
  const b = e.target.closest('button.showmore'); if (!b) return;
  if (!U.more.includes(b.dataset.pos)) { U.more.push(b.dataset.pos); store.set(UKEY, U); }
}, true);
let scrollT = null;
function onScroll() { if (UI.restoring) return; clearTimeout(scrollT); scrollT = setTimeout(() => { U.scroll = scrollPos(); store.set(UKEY, U); }, 150); }
window.addEventListener('scroll', onScroll, {passive: true});
/* the page's own scroll boxes (the board, every table's .scroll): a refresh replaces their section, which
   put them back at the top (Plan O item 1), so each one's position is kept too, keyed by its section and
   its place in it */
function boxKey(b) { const sec = b.closest('section[id]'); return sec ? sec.id + ':' + $$('.scroll', sec).indexOf(b) : null; }
let boxT = null;
function onBoxScroll(b) {
  if (UI.restoring) return; const k = boxKey(b); if (!k) return;
  U.boxes[k] = [b.scrollTop, b.scrollLeft];
  clearTimeout(boxT); boxT = setTimeout(() => store.set(UKEY, U), 150);
}
document.addEventListener('scroll', (e) => {
  const t = e.target; if (!t || !t.classList) return;
  if (t.id === 'pagecol') onScroll(); else if (t.classList.contains('scroll')) onBoxScroll(t);
}, {capture: true, passive: true});
/* the control that had focus (a Pool button just clicked, a tick box): found again after the refresh by
   what it is (its player, its value), not by the node, which the refresh replaced */
function focusKey() {
  const a = document.activeElement; if (!a || a === document.body) return null;
  const seg = a.closest('.seg[data-id]');
  if (seg && a.dataset.v != null) { const sec = seg.closest('section[id], #drawer'); return {seg: seg.dataset.id, v: a.dataset.v, in: sec ? sec.id : ''}; }
  if (a.matches('input.sel[data-id]')) { const sec = a.closest('section[id]'); return {sel: a.dataset.id, in: sec ? sec.id : ''}; }
  return a.id ? {id: a.id} : null;
}
function refocus(k) {
  if (!k) return;
  const scope = k.in ? document.getElementById(k.in) : document; let n = null;
  if (k.seg != null && scope) n = scope.querySelector('.seg[data-id="' + CSS.escape(k.seg) + '"] button[data-v="' + CSS.escape(k.v) + '"]');
  else if (k.sel != null && scope) n = scope.querySelector('input.sel[data-id="' + CSS.escape(k.sel) + '"]');
  else if (k.id) n = document.getElementById(k.id);
  if (n && document.activeElement !== n) { try { n.focus({preventScroll: true}); } catch (e) {} }
}
function expandMore(pos) {
  const grp = document.querySelector('#board tbody.posgrp[data-pos="' + CSS.escape(pos) + '"]'); if (!grp) return;
  for (const tr of grp.querySelectorAll('tr.more')) tr.hidden = false;
  const m = grp.querySelector('tr.morerow'); if (m) m.remove();
}
const UI = {
  restoring: false,
  state: () => JSON.parse(JSON.stringify(U)),
  focus: null,
  capture() {
    U.scroll = scrollPos();
    for (const b of $$('section[id] .scroll')) { const k = boxKey(b); if (k) U.boxes[k] = [b.scrollTop, b.scrollLeft]; }
    UI.focus = focusKey();
    store.set(UKEY, U);
  },
  restore(opts) {
    UI.restoring = true;
    try {
      for (const d of $$('section[id] details')) { const k = detailsKey(d); if (k && k in U.open) d.open = U.open[k]; }
      for (const pos of U.more) expandMore(pos);
      syncTicks(); pool.paintAll();
      if (!opts || opts.scroll !== false) {
        setScroll(U.scroll || 0);
        for (const b of $$('section[id] .scroll')) { const k = boxKey(b); const at = k && U.boxes[k]; if (at) { b.scrollTop = at[0]; b.scrollLeft = at[1]; } }
      }
      refocus(UI.focus); UI.focus = null;
    } finally { setTimeout(() => { UI.restoring = false; }, 0); }
  },
};
window.dfsUI = UI;

/* ---------- the chart viewer (item 9) ---------- */
/* the page's charts (`chart-data`, re-read on every open: a refresh rewrites it) plus the ones a script draws
   (the Explorer's lens chart, `dfsCharts.explorer`) */
const EXTRA = {};
window.dfsCharts = EXTRA;
const chartSpecs = () => Object.assign({}, jsonOf('chart-data') || {}, EXTRA);
/* a small inline scatter from a viewer spec (the Explorer's lens chart): the same classes as week_charts'
   SVG, so it looks the same; a click opens the viewer (the click handler below) */
function scatterSvg(spec, w, h, labels) {
  const M = {l: 48, r: 14, t: 12, b: 40}, [x0, x1, y0, y1] = spec.range;
  const X = (v) => M.l + (v - x0) / ((x1 - x0) || 1) * (w - M.l - M.r);
  const Y = (v) => h - M.b - (v - y0) / ((y1 - y0) || 1) * (h - M.t - M.b);
  const out = ['<svg class="chart" viewBox="0 0 ' + w + ' ' + h + '" width="' + w + '" height="' + h + '" role="img" aria-label="' + esc(spec.title) + '">'];
  if (spec.shade) { const [a, b, c, d, lab] = spec.shade; out.push('<rect class="shade" x="' + X(a) + '" y="' + Y(d) + '" width="' + (X(b) - X(a)) + '" height="' + (Y(c) - Y(d)) + '"/><text class="shade-lbl" x="' + (X(a) + 6) + '" y="' + (Y(d) + 16) + '">' + esc(lab) + '</text>'); }
  for (const t of niceTicks(x0, x1, 4)) out.push('<line class="grid" x1="' + X(t) + '" y1="' + M.t + '" x2="' + X(t) + '" y2="' + (h - M.b) + '"/><text class="tick" x="' + X(t) + '" y="' + (h - M.b + 16) + '" text-anchor="middle">' + fmtv(spec.xfmt, t) + '</text>');
  for (const t of niceTicks(y0, y1, 4)) out.push('<line class="grid" x1="' + M.l + '" y1="' + Y(t) + '" x2="' + (w - M.r) + '" y2="' + Y(t) + '"/><text class="tick" x="' + (M.l - 6) + '" y="' + (Y(t) + 4) + '" text-anchor="end">' + fmtv(spec.yfmt, t) + '</text>');
  out.push('<text class="axis" x="' + ((M.l + w - M.r) / 2) + '" y="' + (h - 6) + '" text-anchor="middle">' + esc(spec.x) + '</text><text class="axis" transform="translate(13 ' + ((M.t + h - M.b) / 2) + ') rotate(-90)" text-anchor="middle">' + esc(spec.y) + '</text>');
  if (spec.ref) { const [[a, b], [c, d], lab] = spec.ref; out.push('<line class="ref" x1="' + X(a) + '" y1="' + Y(b) + '" x2="' + X(c) + '" y2="' + Y(d) + '"/><text class="ref-lbl" x="' + (X(c) - 4) + '" y="' + (Y(d) + 16) + '" text-anchor="end">' + esc(lab) + '</text>'); }
  const top = new Set(spec.points.slice().sort((a, b) => b.prio - a.prio).slice(0, labels).map((p) => p.id));
  for (const p of spec.points) out.push('<circle class="dot ' + esc(p.pos) + (top.has(p.id) ? ' strong' : '') + '" cx="' + X(p.x).toFixed(1) + '" cy="' + Y(p.y).toFixed(1) + '" r="' + (top.has(p.id) ? 5 : 3.5) + '" data-id="' + esc(p.id) + '"><title>' + esc(p.name + ', $' + Number(p.sal).toLocaleString()) + '</title></circle>');
  const placed = [];
  for (const p of spec.points.filter((q) => top.has(q.id))) {
    const wl = p.short.length * 7.3, cx = X(p.x), cy = Y(p.y);
    for (const [dx, dy, an] of [[7, -6, 'start'], [-7, -6, 'end'], [7, 15, 'start'], [-7, 15, 'end']]) {
      const lx = an === 'start' ? cx + dx : cx + dx - wl, ly = cy + dy;
      if (lx < M.l || lx + wl > w - M.r || ly < M.t + 10 || ly > h - M.b) continue;
      if (placed.some(([px, py, pw]) => Math.abs(ly - py) < 14 && lx < px + pw && px < lx + wl)) continue;
      placed.push([lx, ly, wl]); out.push('<text class="lbl" x="' + (cx + dx).toFixed(1) + '" y="' + ly.toFixed(1) + '" text-anchor="' + an + '" data-id="' + esc(p.id) + '">' + esc(p.short) + '</text>'); break;
    }
  }
  out.push('</svg>'); return out.join('');
}
window.dfsScatter = scatterSvg;
const fmtv = (f, v) => f === 'money' ? '$' + (v / 1000).toFixed(v % 1000 ? 1 : 0) + 'K' : f === 'pct' ? Math.round(v) + '%' : (Math.abs(v) >= 10 ? v.toFixed(0) : v.toFixed(1));
const pctOf = (x) => x == null ? '-' : Math.round(x * 100) + '%';
function niceTicks(lo, hi, n) {
  const span = Math.max(hi - lo, 1e-9), raw = span / n, mag = Math.pow(10, Math.floor(Math.log10(raw)));
  const step = [1, 2, 2.5, 5, 10].map((s) => s * mag).find((s) => s >= raw) || raw;
  const out = []; for (let v = Math.ceil(lo / step) * step; v <= hi + 1e-9; v += step) out.push(+v.toFixed(6)); return out;
}
function viewerOpen(key) {
  const spec = chartSpecs()[key]; if (!spec) return;
  let ov = $('#viewer');
  if (!ov) { ov = el('div', 'overlay'); ov.id = 'viewer'; ov.setAttribute('role', 'dialog'); ov.setAttribute('aria-label', 'Chart'); document.body.append(ov); }
  const positions = [...new Set(spec.points.map((p) => p.pos))];
  const V = {key, spec, view: spec.range.slice(), mode: 'pan', show: Object.fromEntries(positions.map((p) => [p, true])), drag: null, pointers: new Map(), hover: null};
  window.dfsViewer = {V, redraw: () => { draw(); side(); }, close};
  ov.replaceChildren(); ov.hidden = false;
  const bar = el('div', 'vbar'); bar.append(el('h2', null, spec.title));
  const how = el('span', 'how', 'ⓘ how this is built'); how.tabIndex = 0; how.dataset.how = spec.how; bar.append(how);
  const modes = el('span', 'grp');
  for (const [m, lab, t] of [['pan', 'Pan', 'Drag to move (the default); the wheel or a pinch zooms, Shift-drag zooms to a box'], ['zoom', 'Zoom box', 'Drag a box to zoom in'], ['box', 'Select box', 'Drag a box to select'], ['lasso', 'Lasso', 'Draw round the players to select']]) {
    const b = el('button', m === V.mode ? 'on' : '', lab); b.type = 'button'; b.dataset.mode = m; b.title = t;
    b.onclick = () => { V.mode = m; for (const x of modes.children) x.classList.toggle('on', x.dataset.mode === m); }; modes.append(b);
  }
  bar.append(modes);
  const pf = el('span', 'grp');
  for (const p of positions) { const lab = el('label'); const cb = el('input'); cb.type = 'checkbox'; cb.checked = true; cb.dataset.pos = p; cb.onchange = () => { V.show[p] = cb.checked; draw(); }; lab.append(cb, document.createTextNode(p)); pf.append(lab); }
  bar.append(pf);
  const reset = el('button', null, 'Reset zoom'); reset.type = 'button'; reset.dataset.act = 'reset'; reset.onclick = () => { V.view = spec.range.slice(); draw(); }; bar.append(reset);
  const x = el('button', 'x', '×'); x.type = 'button'; x.title = 'Close (Esc)'; x.onclick = close; bar.append(x);
  const body = el('div', 'vbody'); const plot = el('div', 'vplot'); plot.innerHTML = '<svg class="chart" role="img"></svg>'; const svg = plot.firstChild; svg.setAttribute('aria-label', spec.title);
  const tip = el('div', 'vtip'); tip.hidden = true; plot.append(tip);
  const aside = el('aside', 'vside'); body.append(plot, aside); ov.append(bar, body);
  const M = {l: 54, r: 18, t: 14, b: 44};
  let W = 0, H = 0;
  const sx = (v) => M.l + (v - V.view[0]) / (V.view[1] - V.view[0]) * (W - M.l - M.r);
  const sy = (v) => H - M.b - (v - V.view[2]) / (V.view[3] - V.view[2]) * (H - M.t - M.b);
  const dx = (px) => V.view[0] + (px - M.l) / (W - M.l - M.r) * (V.view[1] - V.view[0]);
  const dy = (py) => V.view[2] + (H - M.b - py) / (H - M.t - M.b) * (V.view[3] - V.view[2]);
  const visible = () => spec.points.filter((p) => V.show[p.pos]);
  let raf = 0;
  function draw() { cancelAnimationFrame(raf); raf = requestAnimationFrame(drawNow); }
  function drawNow() {
    const r = plot.getBoundingClientRect(); W = Math.max(200, r.width); H = Math.max(200, r.height);
    svg.setAttribute('viewBox', '0 0 ' + W + ' ' + H);
    const parts = [];
    if (spec.shade) { const [a, b, c, d, lab] = spec.shade; const X0 = Math.max(M.l, sx(a)), X1 = Math.min(W - M.r, sx(b)), Y0 = Math.max(M.t, sy(d)), Y1 = Math.min(H - M.b, sy(c));
      if (X1 > X0 && Y1 > Y0) parts.push('<rect class="shade" x="' + X0 + '" y="' + Y0 + '" width="' + (X1 - X0) + '" height="' + (Y1 - Y0) + '"/><text class="shade-lbl" x="' + (X0 + 6) + '" y="' + (Y0 + 16) + '">' + esc(lab) + '</text>'); }
    for (const t of niceTicks(V.view[0], V.view[1], Math.max(3, Math.round(W / 140)))) parts.push('<line class="grid" x1="' + sx(t) + '" y1="' + M.t + '" x2="' + sx(t) + '" y2="' + (H - M.b) + '"/><text class="tick" x="' + sx(t) + '" y="' + (H - M.b + 16) + '" text-anchor="middle">' + fmtv(spec.xfmt, t) + '</text>');
    for (const t of niceTicks(V.view[2], V.view[3], Math.max(3, Math.round(H / 90)))) parts.push('<line class="grid" x1="' + M.l + '" y1="' + sy(t) + '" x2="' + (W - M.r) + '" y2="' + sy(t) + '"/><text class="tick" x="' + (M.l - 6) + '" y="' + (sy(t) + 4) + '" text-anchor="end">' + fmtv(spec.yfmt, t) + '</text>');
    parts.push('<text class="axis" x="' + ((M.l + W - M.r) / 2) + '" y="' + (H - 8) + '" text-anchor="middle">' + esc(spec.x) + '</text><text class="axis" transform="translate(14 ' + ((M.t + H - M.b) / 2) + ') rotate(-90)" text-anchor="middle">' + esc(spec.y) + '</text>');
    if (spec.ref) { const [[a, b], [c, d], lab] = spec.ref; parts.push('<line class="ref" x1="' + sx(a) + '" y1="' + sy(b) + '" x2="' + sx(c) + '" y2="' + sy(d) + '"/><text class="ref-lbl" x="' + (sx(c) - 4) + '" y="' + (sy(d) + 16) + '" text-anchor="end">' + esc(lab) + '</text>'); }
    parts.push('<g clip-path="url(#vclip)">');
    const pts = visible(); const inView = [];
    for (const p of pts) {
      const X = sx(p.x), Y = sy(p.y); if (X < M.l - 6 || X > W - M.r + 6 || Y < M.t - 6 || Y > H - M.b + 6) continue;
      inView.push([p, X, Y]);
      parts.push('<circle class="dot ' + p.pos + (SEL.has(p.id) ? ' sel' : '') + '" cx="' + X.toFixed(1) + '" cy="' + Y.toFixed(1) + '" r="' + (SEL.has(p.id) ? 6 : 4.5) + '" data-vid="' + esc(p.id) + '"/>');
    }
    // labels: the highest priority first, each only where it fits (more fit as you zoom in)
    const placed = []; let n = 0;
    for (const [p, X, Y] of inView.slice().sort((a, b) => (SEL.has(b[0].id) - SEL.has(a[0].id)) || (b[0].prio - a[0].prio))) {
      if (n >= 80) break;
      const w = p.short.length * 7.3, h = 14;
      for (const [ox, oy, anchor] of [[8, -7, 'start'], [-8, -7, 'end'], [8, 16, 'start'], [-8, 16, 'end']]) {
        const lx = anchor === 'start' ? X + ox : X + ox - w, ly = Y + oy;
        if (lx < M.l || lx + w > W - M.r || ly - h < M.t || ly > H - M.b) continue;
        if (placed.some((q) => lx < q[0] + q[2] && q[0] < lx + w && ly - h < q[1] && q[1] - h < ly)) continue;
        placed.push([lx, ly, w]); n++;
        parts.push('<text class="lbl" x="' + (X + ox).toFixed(1) + '" y="' + ly.toFixed(1) + '" text-anchor="' + anchor + '">' + esc(p.short) + '</text>');
        break;
      }
    }
    parts.push('</g>');
    if (V.drag && V.drag.rect) { const [a, b, c, d] = V.drag.rect; parts.push('<rect class="rubber" x="' + Math.min(a, c) + '" y="' + Math.min(b, d) + '" width="' + Math.abs(c - a) + '" height="' + Math.abs(d - b) + '"/>'); }
    if (V.drag && V.drag.path) parts.push('<path class="rubber" d="M' + V.drag.path.map((q) => q.join(' ')).join('L') + 'Z"/>');
    svg.innerHTML = '<defs><clipPath id="vclip"><rect x="' + M.l + '" y="' + M.t + '" width="' + (W - M.l - M.r) + '" height="' + (H - M.t - M.b) + '"/></clipPath></defs>' + parts.join('');
    V.inView = inView;
  }
  function nearest(px, py, within) {
    let best = null, bd = within * within;
    for (const [p, X, Y] of V.inView || []) { const d = (X - px) ** 2 + (Y - py) ** 2; if (d < bd) { bd = d; best = p; } }
    return best;
  }
  function local(e) { const r = svg.getBoundingClientRect(); return [e.clientX - r.left, e.clientY - r.top]; }
  function zoomAt(px, py, f) {
    const x0 = dx(px), y0 = dy(py);
    V.view = [x0 - (x0 - V.view[0]) * f, x0 + (V.view[1] - x0) * f, y0 - (y0 - V.view[2]) * f, y0 + (V.view[3] - y0) * f];
    draw();
  }
  function showTip(p, px, py) {
    if (!p) { tip.hidden = true; return; }
    tip.replaceChildren(el('b', null, p.name), el('div', null, p.pos + ', ' + p.team + ' · $' + Number(p.sal).toLocaleString()),
      el('div', null, 'CalPts ' + (p.proj == null ? '-' : p.proj.toFixed(1)) + ' · DU own ' + pctOf(p.du) + ' · GPP own ' + pctOf(p.gpp)));
    tip.hidden = false; tip.style.left = Math.min(px + 14, W - 250) + 'px'; tip.style.top = Math.max(4, py - 10) + 'px';
  }
  svg.addEventListener('wheel', (e) => { e.preventDefault(); const [px, py] = local(e); zoomAt(px, py, Math.pow(1.0015, e.deltaY)); }, {passive: false});
  svg.addEventListener('pointerdown', (e) => {
    svg.setPointerCapture(e.pointerId); const q = local(e); V.pointers.set(e.pointerId, q);
    if (V.pointers.size === 2) { const [a, b] = [...V.pointers.values()]; V.drag = {pinch: Math.hypot(a[0] - b[0], a[1] - b[1]), mid: [(a[0] + b[0]) / 2, (a[1] + b[1]) / 2]}; return; }
    V.drag = {start: q, mode: e.shiftKey && V.mode === 'pan' ? 'zoom' : V.mode, view: V.view.slice(), moved: false, add: e.shiftKey || e.metaKey};
    if (V.drag.mode === 'lasso') V.drag.path = [q];
  });
  svg.addEventListener('pointermove', (e) => {
    const q = local(e);
    if (!V.drag) { const p = nearest(q[0], q[1], 12); showTip(p, q[0], q[1]); return; }
    if (V.pointers.has(e.pointerId)) V.pointers.set(e.pointerId, q);
    if (V.drag.pinch) { const [a, b] = [...V.pointers.values()]; const d = Math.hypot(a[0] - b[0], a[1] - b[1]); if (d > 0) { zoomAt(V.drag.mid[0], V.drag.mid[1], V.drag.pinch / d); V.drag.pinch = d; } return; }
    const [x0, y0] = V.drag.start; if (Math.hypot(q[0] - x0, q[1] - y0) > 4) V.drag.moved = true;
    if (!V.drag.moved) return;
    tip.hidden = true;
    if (V.drag.mode === 'pan') {
      const v = V.drag.view, fx = (v[1] - v[0]) / (W - M.l - M.r), fy = (v[3] - v[2]) / (H - M.t - M.b);
      V.view = [v[0] - (q[0] - x0) * fx, v[1] - (q[0] - x0) * fx, v[2] + (q[1] - y0) * fy, v[3] + (q[1] - y0) * fy];
    } else if (V.drag.mode === 'lasso') V.drag.path.push(q);
    else V.drag.rect = [x0, y0, q[0], q[1]];
    draw();
  });
  function inPoly(x, y, poly) { let c = false; for (let i = 0, j = poly.length - 1; i < poly.length; j = i++) { const [xi, yi] = poly[i], [xj, yj] = poly[j]; if ((yi > y) !== (yj > y) && x < (xj - xi) * (y - yi) / (yj - yi) + xi) c = !c; } return c; }
  function endDrag(e) {
    V.pointers.delete(e.pointerId);
    const d = V.drag; if (!d) return; if (d.pinch) { if (V.pointers.size < 2) V.drag = null; return; }
    V.drag = null;
    if (!d.moved) { const [px, py] = d.start; const p = nearest(px, py, 12); if (p) selection.toggle(p.id); draw(); return; }
    if (d.mode === 'zoom' && d.rect) {
      const [a, b, c, f] = d.rect; if (Math.abs(c - a) > 8 && Math.abs(f - b) > 8) V.view = [dx(Math.min(a, c)), dx(Math.max(a, c)), dy(Math.max(b, f)), dy(Math.min(b, f))];
    } else if ((d.mode === 'box' && d.rect) || (d.mode === 'lasso' && d.path && d.path.length > 2)) {
      const hit = [];
      for (const [p, X, Y] of V.inView || []) {
        const inside = d.mode === 'box' ? (X >= Math.min(d.rect[0], d.rect[2]) && X <= Math.max(d.rect[0], d.rect[2]) && Y >= Math.min(d.rect[1], d.rect[3]) && Y <= Math.max(d.rect[1], d.rect[3])) : inPoly(X, Y, d.path);
        if (inside) hit.push(p.id);
      }
      selection.add(hit);
    }
    draw();
  }
  svg.addEventListener('pointerup', endDrag); svg.addEventListener('pointercancel', endDrag);
  svg.addEventListener('pointerleave', () => { if (!V.drag) tip.hidden = true; });
  svg.addEventListener('dblclick', (e) => { const [px, py] = local(e); const p = nearest(px, py, 12); if (p && window.dfsDrawer) window.dfsDrawer(p.id); });
  function side() {
    aside.replaceChildren();
    const ids = selection.ids();
    aside.append(el('h4', null, 'Selected (' + ids.length + ')'));
    aside.append(el('p', 'vhelp', 'Click a dot to select it; Select box or Lasso for many (they add to the selection). Double-click a dot for the player. Drag to move (Pan); the wheel or a pinch zooms, Shift-drag or Zoom box zooms to a box; Reset zoom to start again.'));
    const ul = el('ul');
    for (const id of ids) {
      const li = el('li'); const a = el('a', 'pl', pool.name(id)); a.href = '#'; a.dataset.id = id;
      const pv = el('span', 'poolval', pool.get(id) || 'not in pool'); pv.dataset.id = id; if (pool.get(id)) pv.classList.add('in');
      const rm = el('button', null, '×'); rm.type = 'button'; rm.title = 'Remove'; rm.onclick = () => selection.remove([id]);
      li.append(a, pv, rm); ul.append(li);
    }
    aside.append(ul);
    const bk = el('div', 'bulk');
    for (const [v, lab] of [['Cash', 'Cash'], ['GPP', 'GPP'], ['Both', 'Both'], ['', 'none']]) {
      const b = el('button', 'v', lab); b.type = 'button'; b.dataset.bulk = v; b.disabled = !ids.length || !LIVE || READONLY;
      if (!LIVE) b.title = NOT_SERVED; b.onclick = () => bulk(v); bk.append(b);
    }
    const c = el('button', null, 'Clear'); c.type = 'button'; c.disabled = !ids.length; c.onclick = () => selection.clear(); bk.append(c);
    aside.append(bk);
  }
  function close() { ov.hidden = true; ov.replaceChildren(); window.dfsViewer = null; window.removeEventListener('resize', draw); }
  window.addEventListener('resize', draw);
  draw(); side();
}
function esc(t) { return String(t).replace(/[&<>"]/g, (c) => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;'}[c])); }
window.dfsViewerOpen = viewerOpen;
document.addEventListener('click', (e) => {
  const x = e.target.closest('button.expand[data-chart]');
  if (x) { viewerOpen(x.dataset.chart); return; }
  const svg = e.target.closest('[data-chart] svg.chart');
  if (!svg || e.target.closest('circle[data-id], text.lbl[data-id]') || svg.closest('.overlay')) return;
  viewerOpen(svg.closest('[data-chart]').dataset.chart);
});

/* ---------- the game view (item 10; Plan O: every row's Pool control, previous / next and the game list) ---------- */
const G = jsonOf('game-data') || {};
const GSEL = new Set();
let GCUR = null;
function gameOf(game, team) {
  if (game && G[game]) return game;
  return Object.keys(G).find((k) => G[k].teams && team && team in G[k].teams) || null;
}
function gameStep(d) {
  const keys = Object.keys(G); if (!GCUR || !keys.length) return;
  const i = keys.indexOf(GCUR); gameOpen(keys[(i + d + keys.length) % keys.length]);
}
function gameOpen(game, team) {
  const key = gameOf(game, team); if (!key) return;
  const g = G[key]; GSEL.clear(); GCUR = key;
  let ov = $('#gameview');
  if (!ov) { ov = el('div', 'overlay'); ov.id = 'gameview'; ov.setAttribute('role', 'dialog'); document.body.append(ov); }
  ov.setAttribute('aria-label', g.title); ov.replaceChildren(); ov.hidden = false;
  const bar = el('div', 'vbar'); bar.append(el('h2', null, g.title));
  const how = el('span', 'how', 'ⓘ how this is built'); how.tabIndex = 0; how.dataset.how = g.how; bar.append(how);
  const nav = el('span', 'grp gnav');
  const prev = el('button', null, '‹ Previous'); prev.type = 'button'; prev.dataset.act = 'prev'; prev.title = 'The previous game (←)'; prev.onclick = () => gameStep(-1);
  const list = el('select'); list.setAttribute('aria-label', 'Go to a game'); list.dataset.act = 'list';
  for (const k of Object.keys(G)) list.append(new Option(G[k].title + (G[k].total_text ? ' · ' + G[k].total_text : ''), k));
  list.value = key; list.onchange = () => gameOpen(list.value);
  const next = el('button', null, 'Next ›'); next.type = 'button'; next.dataset.act = 'next'; next.title = 'The next game (→)'; next.onclick = () => gameStep(1);
  nav.append(prev, list, next); bar.append(nav);
  const x = el('button', 'x', '×'); x.type = 'button'; x.title = 'Close (Esc)'; x.onclick = () => { ov.hidden = true; GCUR = null; }; bar.append(x);
  const body = el('div', 'gv'); ov.append(bar, body);
  body.append(el('p', 'line', g.line));
  const st = el('div'); st.append(el('h4', null, 'The sim\'s stacks in this game'));
  if (g.stacks.length) { const ul = el('ul'); for (const s2 of g.stacks) ul.append(el('li', null, s2)); st.append(ul); }
  else st.append(el('p', 'dim', g.no_stacks));
  body.append(st);
  const teams = el('div', 'teams');
  for (const t of g.order) {
    const col = el('div'); col.append(el('h3', null, t + (g.implied[t] != null ? ' · implied ' + g.implied[t].toFixed(1) : '')));
    const tab = el('table', 'stickyname'); const head = el('tr');
    for (const h of ['Player', 'Salary', 'CalPts', 'Ceiling', 'GPP own', 'Leverage', 'Pool']) head.append(el('th', h === 'Player' || h === 'Pool' ? '' : 'n', h));
    const th = el('thead'); th.append(head); tab.append(th);
    if (!(g.teams[t] || []).length) { const tb = el('tbody'); const tr = el('tr'); const td = el('td', 'dim', 'No priced players for ' + t + ' in the last sync.'); td.colSpan = 7; tr.append(td); tb.append(tr); tab.append(tb); }
    for (const [grp, players] of g.teams[t] || []) {
      const tb = el('tbody'); const gr = el('tr', 'pos'); const gd = el('td', null, grp); gd.colSpan = 7; gr.append(gd); tb.append(gr);
      for (const p of players) {
        const tr = el('tr'); tr.dataset.id = p.id; tr.dataset.pos = p.pos || '';
        const nm = el('td'); const cb = el('input', 'sel'); cb.type = 'checkbox'; cb.dataset.id = p.id; cb.dataset.name = p.name; cb.dataset.pos = p.pos;
        cb.onchange = () => { if (cb.checked) GSEL.add(p.id); else GSEL.delete(p.id); actions(); };
        const a = el('a', 'pl', p.name); a.href = '#'; a.dataset.id = p.id; nm.append(cb, a); if (p.avail) nm.append(el('span', 'chip q', p.avail));
        tr.append(nm);
        for (const v of ['$' + Number(p.sal).toLocaleString(), p.cal == null ? '-' : p.cal.toFixed(1), p.ceil == null ? '-' : p.ceil.toFixed(1), pctOf(p.gpp), p.lev == null ? '-' : p.lev.toFixed(1)]) tr.append(el('td', 'n', v));
        if (!(p.id in P.name)) P.name[p.id] = p.name;
        const pc = el('td'); pc.append(pool.seg(p.id, p.name)); tr.append(pc);
        tb.append(tr);
      }
      tab.append(tb);
    }
    const wrap = el('div', 'scroll'); wrap.append(tab); col.append(wrap); teams.append(col);
    if (window.dfsShade) window.dfsShade.table(tab);   // within each position, as on the page (week_shade)
  }
  body.append(teams);
  const act = el('div', 'actions'); body.append(act);
  function picked() { return $$('#gameview input.sel').filter((c) => GSEL.has(c.dataset.id)); }
  function actions() {
    act.replaceChildren();
    const ps = picked();
    act.append(el('b', null, ps.length ? ps.length + ' ticked: ' + ps.map((c) => c.dataset.name).join(', ') : 'Tick a QB, his pass catchers and a bring-back'));
    const add = el('button', 'v', 'Add the ticked as GPP'); add.type = 'button'; add.dataset.act = 'gpp';
    add.disabled = !ps.length || !LIVE || READONLY; if (!LIVE) add.title = NOT_SERVED;
    add.onclick = () => requestPool(ps.map((c) => ({id: c.dataset.id, value: pool.addKind(pool.get(c.dataset.id), 'GPP')})), 'Added as GPP');
    const more = el('button', 'v', 'Write as a Max exposure stack'); more.type = 'button'; more.dataset.act = 'stack';
    more.disabled = !ps.some((c) => c.dataset.pos === 'QB') || !LIVE || READONLY;
    more.title = LIVE ? 'more Name for each on Suggested\'s Max exposure line (GPP builds); needs the QB' : 'The served page writes Suggested\'s Max exposure line';
    more.onclick = () => window.dfsQueue.run(async () => {
      let rules = {}; try { rules = JSON.parse(($('#rules-was') || {}).textContent || '{}'); } catch (e) {}
      try { const r = await window.dfsLive.stack({names: ps.map((c) => c.dataset.name), rules_was: rules}); toast(r.message); }
      catch (e) { toast(e.message, true); }
    });
    act.append(add, more, el('span', 'dim', 'Each row\'s Pool control sets him on its own: Cash, GPP, Both or none.'));
  }
  actions(); pool.paintAll();
}
window.dfsGame = gameOpen;
document.addEventListener('click', (e) => {
  const t = e.target.closest('a.tm[data-team], .game[data-game]');
  if (!t || t.closest('#gameview')) return;
  e.preventDefault(); gameOpen(t.dataset.game, t.dataset.team);
});
document.addEventListener('keydown', (e) => {
  const gv = $('#gameview');
  if ((e.key === 'ArrowLeft' || e.key === 'ArrowRight') && gv && !gv.hidden && !e.target.closest('input, select, textarea')) { e.preventDefault(); gameStep(e.key === 'ArrowLeft' ? -1 : 1); return; }
  if (e.key !== 'Escape') return;
  const d = $('#drawer'); if (d && !d.hidden) return;   // the drawer closes first
  for (const id of ['viewer', 'gameview']) { const o = document.getElementById(id); if (o && !o.hidden) { if (id === 'viewer' && window.dfsViewer) window.dfsViewer.close(); else { o.hidden = true; GCUR = null; } return; } }
});

/* ---------- start ---------- */
adoptFromExplorer(jsonOf('explorer-data'));
adoptFromDom();
document.addEventListener('DOMContentLoaded', () => { decorate(); paintAll(); UI.restore(); drawBulk(); });
})();
"""
