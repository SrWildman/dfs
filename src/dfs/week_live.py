"""What `dfs week serve` adds to the Week page: the chat panel, Ask buttons, the run buttons, the contest check
form and the file drop zone.

The static page (`week_page.render`) stays exactly what `dfs week page` writes. `augment` takes that HTML and
returns the live version:

- every `"phrase"` Sam can say gets an **Ask** button that puts it in the chat box (it never sends);
- the top of the page (`week_top`) already holds the served controls: every board / Explorer row's Pool
  segment, the decisions' buttons and "Add the suggested starting pool". The script here sends them to the
  server (`/api/pool`, `/api/pool/start`, `/api/decide`; `week_actions`), which re-reads the sheet, refuses on a
  change, writes one batch under the run lock and reads it back; the page then shows what the sheet holds. An
  empty Cash / GPP section gets a **Build** button and an empty Sunday section **Late swap**;
- the `<!--live:contest-->` and `<!--live:run-->` markers become the contest widgets and the Run card (cards
  marked `data-keep`, so a refresh of the page's sections never wipes a half-typed form or a running job's
  output);
- the page is wrapped in a two-column layout with the chat docked on the right (it collapses);
- one inline script (vendored nothing, loads nothing) talks to the server's `/api/...` with the run's token.

Text from the server is only ever put on the page with `textContent` (never as HTML), so a contest name, a
command's output or Claude's reply cannot inject markup.
"""

from __future__ import annotations

import html
import re

from dfs import week_chat, week_page

SAY = re.compile(r'(<code class="say">&quot;(.*?)&quot;</code>)')
BUTTON_SLOT = re.compile(
    rf'<span class="{week_page.LIVE_BUTTON}" data-command="([^"]*)" data-label="([^"]*)"></span>'
)
STATIC_ONLY = re.compile(rf'<p class="note {week_page.STATIC_ONLY}">.*?</p>', re.S)

CSS = """
body.live{display:flex;align-items:flex-start}
#pagecol{flex:1;min-width:0;height:100vh;overflow-y:auto}
#chat{width:430px;flex:none;height:100vh;position:sticky;top:0;display:flex;flex-direction:column;
border-left:1px solid var(--line);background:var(--card)}
#chat.collapsed{width:44px}
#chat.collapsed>*:not(.chat-head){display:none}
#chat.collapsed .chat-head>*:not(#chat-toggle){display:none}
.chat-head{padding:8px 10px;border-bottom:1px solid var(--line);display:flex;gap:8px;align-items:center;
flex-wrap:wrap}
.chat-head .who{font-size:12px;color:var(--dim);flex:1;min-width:160px}
#chat.collapsed .chat-head{justify-content:center;padding:8px 0;border-bottom:0}
#chat.collapsed{cursor:pointer}
#chat.collapsed #chat-toggle{writing-mode:vertical-rl;padding:10px 3px;border-color:var(--accent);color:var(--accent);
font-weight:600}
#chat.collapsed #chat-toggle::after{content:" ◂"}
.chat-controls{padding:6px 10px;display:flex;flex-wrap:wrap;gap:6px 10px;align-items:center;font-size:12.5px;
border-bottom:1px solid var(--line)}
.chat-controls select,.chat-controls button{font:inherit;padding:2px 6px}
#chat-list{max-width:100%;min-width:0;flex:1 1 140px}
#lockbar{font-size:12.5px;padding:4px 10px;color:var(--dim);border-bottom:1px solid var(--line)}
#lockbar.busy{background:var(--flag);color:var(--ink)}
#chat-log{flex:1;overflow-y:auto;padding:10px;font-size:14px}
.msg{margin:0 0 10px;white-space:pre-wrap;word-break:break-word}
.msg.user{background:rgba(31,94,255,.1);border-radius:8px;padding:6px 10px}
.msg.error{color:var(--bad)}.msg.note{color:var(--dim);font-size:12.5px}
#chat-log .chip{display:block;margin:0 0 8px;border:1px solid var(--line);border-radius:6px;font-size:12.5px;
padding:2px 8px;white-space:normal;font-weight:400;background:transparent;color:inherit}
#chat-log .chip summary{cursor:pointer;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
#chat-log .chip pre{white-space:pre-wrap;word-break:break-word;font:11.5px/1.4 ui-monospace,Menlo,monospace;margin:4px 0;
max-height:220px;overflow:auto}
#chat-log .chip.err summary{color:var(--bad)}
.approval{border:2px solid var(--shadow);border-radius:8px;padding:8px 10px;margin:0 0 10px;background:var(--flag)}
.approval pre{white-space:pre-wrap;word-break:break-word;font:12px/1.4 ui-monospace,Menlo,monospace;
max-height:200px;overflow:auto;margin:6px 0}
.approval button{margin-right:8px;font:inherit;padding:3px 12px}
.approval .allow{background:var(--proven);color:#fff;border:0;border-radius:5px}
.approval .deny{background:var(--bad);color:#fff;border:0;border-radius:5px}
#chat-input{border-top:1px solid var(--line);padding:8px 10px;display:flex;gap:6px;flex-direction:column}
#chat-input textarea{width:100%;min-height:62px;font:inherit;resize:vertical;background:var(--bg);
color:var(--ink);border:1px solid var(--line);border-radius:6px;padding:6px}
#chat-input .row{display:flex;gap:8px;align-items:center}
button.ask{font:11.5px/1 inherit;margin-left:6px;padding:2px 7px;border:1px solid var(--accent);
color:var(--accent);background:transparent;border-radius:999px;cursor:pointer}
.live-card button,.live-card select,.live-card input{font:inherit}
.live-card input[type=text],.live-card select{background:var(--bg);color:var(--ink);border:1px solid var(--line);
border-radius:5px;padding:3px 6px}
.live-card button.primary{background:var(--accent);color:#fff;border:0;border-radius:6px;padding:4px 14px;
cursor:pointer}
.live-card button:disabled{opacity:.5;cursor:not-allowed}
.runbtn{background:var(--accent);color:#fff;border:0;border-radius:6px;padding:3px 12px;cursor:pointer;
font:inherit;margin-left:6px}
.confirm{margin:8px 0}.confirm button{margin-top:6px}
.formgrid{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:8px 12px;margin:8px 0}
.formgrid label{display:flex;flex-direction:column;font-size:12.5px;color:var(--dim);gap:2px}
.verdict{border:1px solid var(--line);border-radius:8px;padding:8px 12px;margin:8px 0}
.tier{display:inline-block;font-weight:700;border-radius:6px;padding:1px 10px;color:#fff;margin-right:6px}
.tier.A{background:var(--proven)}.tier.B{background:var(--accent)}.tier.C{background:var(--shadow)}
.tier.Avoid{background:var(--bad)}
.dropzone{border:2px dashed var(--line);border-radius:10px;padding:16px;text-align:center;color:var(--dim);
margin:8px 0}.dropzone.over{border-color:var(--accent);color:var(--accent)}
.weekly{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:8px;margin:8px 0}
.weekly button{border:1px solid var(--line);background:var(--bg);color:var(--ink);border-radius:8px;
padding:7px 10px;cursor:pointer;text-align:left;font:inherit;display:flex;flex-direction:column;gap:1px}
.weekly button b{font-size:14px}.weekly button span{font-size:11.5px;color:var(--dim)}
.weekly button:hover,.weekly button.on{border-color:var(--accent)}.weekly button.on{background:var(--accent-soft)}
.weekly button i{font-style:normal;font-size:11px;color:var(--dim)}
.cmdlist button{border:1px solid var(--line);background:var(--bg);color:var(--ink);border-radius:6px;
padding:3px 9px;cursor:pointer;font-size:12px}
.cmdlist{max-height:200px;overflow:auto;margin:6px 0;display:flex;flex-wrap:wrap;gap:4px}
#live-run details>summary{cursor:pointer;margin:8px 0 4px;font-weight:600}
#run-form{border:1px solid var(--line);border-radius:8px;padding:10px 12px;margin:8px 0;background:var(--bg)}
#run-form h4{margin:0 0 2px}#run-form .when{font-size:12px;color:var(--dim);margin:0 0 8px}
.opts{display:grid;grid-template-columns:minmax(160px,240px) minmax(0,1fr);gap:6px 12px;align-items:center;
margin:6px 0}
.opts .lab{font-size:13px;cursor:help}.opts .lab.req::after{content:" *";color:var(--bad)}
.opts input[type=text],.opts select{width:100%;max-width:340px}
.opts.toggles{grid-template-columns:22px minmax(0,1fr);gap:4px 6px}
.opts.toggles input{margin:0;justify-self:center}
#run-form details.more{margin:6px 0}#run-form details.more>summary{font-weight:400;color:var(--accent)}
.preview{font:12px/1.4 ui-monospace,Menlo,monospace;background:var(--card);border:1px dashed var(--line);
border-radius:6px;padding:4px 8px;margin:8px 0;word-break:break-word}
.preview::before{content:"Runs: ";color:var(--dim);font-family:inherit}
.runs{list-style:none;padding:0;margin:4px 0}.runs li{display:flex;flex-wrap:wrap;gap:4px 10px;align-items:baseline;
font-size:13px;padding:3px 0;border-bottom:1px solid var(--line)}
.runs .st{font-weight:700;min-width:52px}.runs .ok .st{color:var(--ok)}.runs .bad .st{color:var(--bad)}
.runs code{flex:1 1 220px}.runs .t{color:var(--dim);font-size:12px}
#run-out{background:#0f1115;color:#d7dae0;border-radius:8px;padding:10px;font:12px/1.4 ui-monospace,Menlo,
monospace;max-height:320px;overflow:auto;white-space:pre-wrap;word-break:break-word;margin:8px 0}
@media(max-width:900px){body.live{display:block}#pagecol{height:auto;overflow:visible}
#chat{position:fixed;right:0;top:0;width:100%;max-width:430px;z-index:5}#chat.collapsed{width:44px}
body.live.chat-closed #pagecol{padding-right:44px}}
"""

CONTEST_HTML = """
<section class="card live-card" id="live-contest" data-keep>
<div class="card-head"><h3>Check a contest, and drop your entries</h3></div>
<p class="note">Type five fields while you look at a contest in the lobby; nothing is read from DraftKings.</p>
<form id="check-form" autocomplete="off">
<div class="formgrid">
<label>Format<select name="format"><option>GPP</option><option>H2H</option><option>50/50</option>
<option>Double Up</option><option>Showdown</option><option>Other</option></select></label>
<label>Fee ($)<input type="text" name="fee" inputmode="decimal" placeholder="5"></label>
<label>Entrants (so far / max)<input type="text" name="entrants" placeholder="1,234 / 2,450"></label>
<label>Entries per user<input type="text" name="entry_max" inputmode="numeric" placeholder="1, 3, 20"></label>
<label>Prize pool or min-cash<input type="text" name="prize" placeholder="30000  or  2x"></label>
<label id="opp-field" hidden>H2H opponent<input type="text" name="opponent" placeholder="username"></label>
</div>
<button class="primary" type="submit">Check</button>
</form>
<div id="check-out"></div>
<h4>Weekly Budget</h4>
<form id="budget-form" class="row" autocomplete="off">
<input type="text" name="budget" inputmode="decimal" placeholder="%BUDGET%" size="8">
<button type="submit">Save for this week</button>
<span class="note">The plan above splits it by tier. <code>dfs contests plan</code> reads it from the
Bankroll tab; this is for when you have not run that.</span></form>
<h4>Drop a file</h4>
<div class="dropzone" id="dropzone">Drop DraftKings' <b>entries file</b> at lock, or your <b>contest-history
export</b> and the <b>standings</b> (.csv or .zip) at close.<br><input type="file" id="drop-file"
accept=".csv,.zip"></div>
<div id="drop-out"></div>
</section>
"""

RUN_HTML = """
<section class="card live-card" id="live-run" data-keep>
<div class="card-head"><h2>Run</h2><span class="asof" id="run-week"></span></div>
<p class="note">The week's commands, in the day's order. Each runs the same <code>dfs</code> command as the
terminal, one at a time; a command that writes takes the run lock, so it waits while the chat or a terminal
runs one.</p>
<div class="weekly" id="run-menu"></div>
<form id="run-form" hidden></form>
<pre id="run-out" hidden></pre>
<button id="run-stop" hidden>Stop</button>
<h4>Recent runs</h4><ul id="run-history" class="runs"></ul>
<details id="run-more"><summary>Other commands</summary>
<input type="text" id="run-search" placeholder="Search (odds, stacks, backtest...)" size="36">
<div class="cmdlist" id="run-list"></div></details>
<details id="run-adv"><summary>Advanced: sheet setup and one-off fixes <span id="run-adv-n"></span></summary>
<p class="note">These rewrite tabs: template first, then the live week, and <code>dfs doctor</code> after.</p>
<div class="cmdlist" id="run-adv-list"></div></details>
</section>
"""

CHAT_HTML = """
<aside id="chat" aria-label="Claude Code chat">
<div class="chat-head"><button id="chat-toggle" title="Show or hide the chat">Claude</button>
<span class="who"><b>Claude Code</b> on your Mac, in <code>dfs/</code>. It can run commands and edit files
here, and asks you first.</span></div>
<div class="chat-controls">
<label>Model <select id="chat-model">%MODELS%</select></label>
<label>Effort <select id="chat-effort">%EFFORTS%</select></label>
<label title="Claude Code's own auto permission mode: it approves what it judges safe. Off unless you turn it on.">
<input type="checkbox" id="chat-auto"> auto</label>
<button id="chat-new">New chat</button>
<select id="chat-list" title="This week's chats"><option value="">This week's chats</option></select>
<button id="chat-stop" disabled>Stop</button></div>
<div id="lockbar">Nothing is running.</div>
<div id="chat-log"></div>
<div id="chat-input"><textarea id="chat-text" placeholder="Ask, or click Ask beside a step (Enter sends, Shift+Enter is a new line)"></textarea>
<div class="row"><button class="primary live-card" id="chat-send" style="background:var(--accent);color:#fff;border:0;border-radius:6px;padding:4px 14px">Send</button>
<span class="note" id="chat-note"></span></div></div>
</aside>
"""

JS = r"""
(() => {
const T = document.querySelector('meta[name=dfs-token]').content;
const READONLY = document.querySelector('meta[name=dfs-readonly]').content === '1';
const $ = (s, r = document) => r.querySelector(s);
const el = (tag, cls, text) => { const e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; };
const url = (p) => p + (p.includes('?') ? '&' : '?') + 't=' + encodeURIComponent(T);
async function call(path, opts) {
  const r = await fetch(url(path), opts);
  let body = {};
  try { body = await r.json(); } catch (e) {}
  if (!r.ok) { const err = new Error(body.error || r.statusText); err.status = r.status; err.body = body; throw err; }
  return body;
}
const post = (path, data) => call(path, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(data || {})});
const get = (path) => call(path);

/* ---------- chat ---------- */
const log = $('#chat-log'), box = $('#chat-text');
const chat = {id: null, turn: null, last: -1, bubble: null, tools: {}, running: false, finished: true};
function scroll() { log.scrollTop = log.scrollHeight; }
function add(cls, text) { const m = el('div', 'msg ' + cls, text); log.appendChild(m); scroll(); return m; }
function setRunning(on) {
  chat.running = on; $('#chat-stop').disabled = !on; $('#chat-send').disabled = on;
  $('#chat-note').textContent = on ? 'Claude is working...' : '';
}
/* "still running: <command>, 4m12s" under a tool that has not returned yet (live turns only). */
const running = {};
function since(ms) { const s = Math.max(0, Math.floor(ms / 1000)), m = Math.floor(s / 60); return m ? m + 'm' + String(s % 60).padStart(2, '0') + 's' : s + 's'; }
function tick() {
  const waiting = log.querySelector('.approval .btns button');
  for (const r of Object.values(running)) {
    const ms = Date.now() - r.t0;
    r.line.hidden = !!waiting || ms < 3000;
    r.line.textContent = 'still running: ' + (r.summary || r.name) + ', ' + since(ms);
  }
}
setInterval(tick, 1000);
function settle(id) { const r = running[id]; if (r) { r.line.remove(); delete running[id]; } }
function settleAll() { for (const id of Object.keys(running)) settle(id); }
function render(ev, live) {
  switch (ev.k) {
    case 'user': chat.bubble = null; add('user', ev.text); break;
    case 'session': chat.id = ev.id; break;
    case 'thinking': $('#chat-note').textContent = 'Claude is thinking...'; break;
    case 'text':
      if (!chat.bubble) chat.bubble = add('assistant', '');
      chat.bubble.textContent += ev.delta; scroll(); break;
    case 'tool': {
      chat.bubble = null;
      const d = el('details', 'chip'); const s = el('summary'); s.append(el('b', null, ev.name + ' '), document.createTextNode(ev.summary || ''));
      d.append(s, el('pre', null, ev.input || ''), el('pre', 'res'));
      log.appendChild(d); chat.tools[ev.id] = d;
      if (live) { const line = el('div', 'msg note ticker'); line.hidden = true; log.appendChild(line);
        running[ev.id] = {name: ev.name, summary: ev.summary, t0: Date.now(), line}; }
      scroll(); break; }
    case 'result': {
      settle(ev.id);
      const d = chat.tools[ev.id];
      if (d) { d.querySelector('.res').textContent = ev.text; if (ev.error) d.classList.add('err'); }
      break; }
    case 'approval': chat.bubble = null; approvalCard(ev); break;
    case 'approval_closed': {
      const c = document.getElementById('appr-' + ev.id);
      if (c) { c.querySelector('.btns').replaceChildren(el('span', 'note', ev.allowed === false ? 'Denied.' : (ev.allowed ? 'Approved.' : 'Closed.'))); }
      if (ev.allowed) for (const r of Object.values(running)) r.t0 = Date.now();  // the clock starts when it runs
      break; }
    case 'error': chat.bubble = null; add('error', ev.text); break;
    case 'note': chat.bubble = null; add('note', ev.text); break;
    case 'done': chat.bubble = null; settleAll();
      /* a turn that gave no answer says why, in red: never a silent Done */
      if (ev.stopped) add('note', 'Stopped.');
      else if (ev.problem) add('error', ev.problem);
      else if (ev.ok === false) add('error', 'Finished with an error' + (ev.reason ? ' (' + ev.reason + ')' : '') + '.');
      else add('note', 'Done' + (ev.cost != null ? ' ($' + Number(ev.cost).toFixed(3) + ')' : '') + '.');
      break;
  }
}
function approvalCard(ev) {
  const c = el('div', 'approval'); c.id = 'appr-' + ev.id;
  c.append(el('div', null, 'Claude wants to use ' + ev.tool + (ev.title ? ': ' + ev.title : '')), el('pre', null, ev.input || ev.summary || ''));
  const b = el('div', 'btns'); const yes = el('button', 'allow', 'Approve'), no = el('button', 'deny', 'Deny');
  yes.onclick = () => decide(ev.id, true); no.onclick = () => decide(ev.id, false);
  b.append(yes, no); c.append(b); log.appendChild(c); scroll();
}
async function decide(id, allow) {
  try { await post('/api/chat/approve', {turn: chat.turn, id, allow}); } catch (e) { add('error', e.message); }
}
function follow(turn, after) {
  chat.turn = turn; chat.last = after; chat.finished = false; setRunning(true);
  const es = new EventSource(url('/api/chat/stream?turn=' + turn + '&after=' + after));
  es.onmessage = (m) => {
    const ev = JSON.parse(m.data); chat.last = ev.seq; render(ev, true);
    if (ev.k === 'done') { es.close(); chat.finished = true; setRunning(false); loadChats(); refreshSections(); }
  };
  es.onerror = () => { es.close(); if (!chat.finished) setTimeout(() => follow(turn, chat.last), 1000); };
}
async function send() {
  const text = box.value.trim(); if (!text || chat.running) return;
  const body = {text, chat_id: chat.id, model: $('#chat-model').value, effort: $('#chat-effort').value, auto: $('#chat-auto').checked};
  render({k: 'user', text}); box.value = '';
  try { const r = await post('/api/chat/send', body); follow(r.turn, -1); } catch (e) { add('error', e.message); }
}
$('#chat-send').onclick = send;
box.addEventListener('keydown', (e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send(); } });
$('#chat-stop').onclick = async () => { try { await post('/api/chat/stop'); } catch (e) { add('error', e.message); } };
$('#chat-new').onclick = () => { if (chat.running) return; chat.id = null; log.replaceChildren(); $('#chat-list').value = ''; };
/* the chat folds to a thin tab and back, remembered per browser (Plan N item 1) */
function chatFold(collapsed, save) {
  $('#chat').classList.toggle('collapsed', collapsed);
  document.body.classList.toggle('chat-open', !collapsed); document.body.classList.toggle('chat-closed', collapsed);
  $('#chat-toggle').setAttribute('aria-expanded', String(!collapsed));
  $('#chat-toggle').title = collapsed ? 'Show the chat' : 'Hide the chat';
  if (save) { try { localStorage.setItem('dfs-chat-collapsed', collapsed ? '1' : '0'); } catch (e) {} }
}
(() => { let c = false; try { c = localStorage.getItem('dfs-chat-collapsed') === '1'; } catch (e) {} chatFold(c, false); })();
$('#chat-toggle').onclick = (e) => { e.stopPropagation(); chatFold(!$('#chat').classList.contains('collapsed'), true); };
$('#chat').addEventListener('click', () => { if ($('#chat').classList.contains('collapsed')) chatFold(false, true); });
$('#chat-auto').onchange = (e) => { if (e.target.checked && !confirm("Auto mode lets Claude Code approve what it judges safe without asking you. Turn it on?")) e.target.checked = false; };
async function loadChats() {
  try {
    const r = await get('/api/chat/list'); const sel = $('#chat-list'); const keep = sel.value;
    sel.replaceChildren(new Option("This week's chats", ''));
    for (const c of r.chats) sel.append(new Option(c.title + ' (' + c.model + ')', c.id));
    sel.value = keep;
  } catch (e) {}
}
$('#chat-list').onchange = async (e) => {
  if (!e.target.value || chat.running) return;
  try {
    const r = await get('/api/chat/transcript?id=' + encodeURIComponent(e.target.value));
    log.replaceChildren(); chat.tools = {}; chat.bubble = null; chat.id = e.target.value;
    for (const ev of r.events) render(ev);
  } catch (err) { add('error', err.message); }
};
document.addEventListener('click', (e) => {
  const b = e.target.closest('button.ask'); if (!b) return;
  chatFold(false, true); box.value = b.dataset.say; box.focus();
});

/* ---------- status ---------- */
let stamp = null;   // when the page file was last rebuilt by any command (a terminal's too)
async function status() {
  try {
    const s = await get('/api/status'); const bar = $('#lockbar');
    if (s.lock) { bar.textContent = s.lock.text; bar.className = 'busy'; }
    else if (s.job) { bar.textContent = s.job + ' is running from a button.'; bar.className = 'busy'; }
    else { bar.textContent = 'Nothing is running.'; bar.className = ''; }
    if (!chat.running && s.chat_busy && !chat.finished) setRunning(true);
    if (stamp !== null && s.page_stamp !== stamp) refreshSections();
    stamp = s.page_stamp;
  } catch (e) {}
}
setInterval(status, 4000);

/* ---------- reload the page's sections (the chat and the live cards stay where they are) ---------- */
/* What is open, what is expanded and where the page is scrolled survive the refresh (Plan N item 5:
   week_ui's dfsUI keeps them per browser); the pool store adopts the sheet's values from the fresh HTML,
   except a cell still being written. */
/* A refresh asked for while one is in flight runs once more after it (`again`): the one in flight may have
   left before the write that asked for it landed. `since` is the pool store's last read-back when the render
   was requested; a cell read back after that keeps its read-back (`week_ui` `stale`). */
let refreshing = null, again = false;
async function refreshSections() {
  if (refreshing) { again = true; return refreshing; }
  refreshing = (async () => {
    try {
      if (window.dfsUI) window.dfsUI.capture();
      const since = window.dfsPool ? window.dfsPool.mark() : null;
      const r = await fetch(url('/?fragment=1')); if (!r.ok) return;
      const doc = new DOMParser().parseFromString(await r.text(), 'text/html');
      const ed = doc.querySelector('#explorer-data');
      if (ed && window.dfsPool) { try { window.dfsPool.adoptExplorer(JSON.parse(ed.textContent), since); } catch (e) {} }
      for (const fresh of doc.querySelectorAll('#pagecol section[id]:not([data-keep])')) {
        const old = document.getElementById(fresh.id); if (!old) continue;
        const was = old.querySelector(':scope > details'), now = fresh.querySelector(':scope > details');
        if (was && now) now.open = was.open;   // a section Sam opened stays open
        old.replaceWith(fresh);
        if (window.dfsPool) window.dfsPool.adopt(fresh, since);
      }
      const sub = doc.querySelector('.sub'), cur = $('.sub'); if (sub && cur) cur.replaceWith(sub);
      const rw = doc.querySelector('#rules-was'), rc = $('#rules-was'); if (rw && rc) rc.textContent = rw.textContent;
      for (const id of ['chart-data', 'game-data']) { const f = doc.getElementById(id), c = document.getElementById(id); if (f && c) c.textContent = f.textContent; }
      if (window.dfsExplorer) window.dfsExplorer.render();
      if (window.dfsUI) window.dfsUI.restore();
      document.dispatchEvent(new CustomEvent('dfs-sections'));   // week_views redraws what it draws itself
    } catch (e) {} finally { refreshing = null; }
    if (again) { again = false; await refreshSections(); }
  })();
  return refreshing;
}

/* ---------- run buttons (Plan O item 1b: the weekly ones, curated forms, recent runs) ---------- */
let COMMANDS = null;
async function loadCommands() {
  COMMANDS = await get('/api/commands');
  const P = COMMANDS.panel, menu = $('#run-menu');
  menu.replaceChildren();
  for (const w of P.weekly) {
    const b = el('button'); b.type = 'button'; b.dataset.command = w.command; b.title = w.tip;
    b.append(el('b', null, w.label), el('span', null, w.when), el('i', null, w.command));
    b.onclick = () => openForm(w.command); menu.append(b);
  }
  $('#run-week').textContent = COMMANDS.read_only ? 'closed week: read only' : '';
  const adv = $('#run-adv-list'); adv.replaceChildren();
  for (const p of P.advanced) adv.append(cmdButton(p));
  $('#run-adv-n').textContent = '(' + P.advanced.length + ')';
  filterList('');
}
function cmdButton(path) {
  const f = COMMANDS.forms[path]; const b = el('button', null, path.replace(/^dfs /, ''));
  b.type = 'button'; b.title = f.label || f.help; b.onclick = () => openForm(path); return b;
}
function filterList(q) {
  const list = $('#run-list'); list.replaceChildren();
  const words = q.toLowerCase().split(/\s+/).filter(Boolean);
  for (const p of COMMANDS.panel.other) {
    const f = COMMANDS.forms[p]; const hay = (f.command + ' ' + f.label + ' ' + f.help).toLowerCase();
    if (words.every((w) => hay.includes(w))) list.append(cmdButton(p));
  }
}
$('#run-search').oninput = (e) => filterList(e.target.value);
let current = null;
const shq = (v) => /^[\w@%+=:,./-]+$/.test(v) ? v : "'" + v.replace(/'/g, "'\\''") + "'";
/* a command's flags as form values: `dfs sync --live` opens the sync form with Final sync ticked */
function splitCommand(command, spec) {
  const words = command.split(/\s+/).filter(Boolean), path = words.filter((w) => !w.startsWith('-')).join(' ');
  const vals = {}, byFlag = Object.fromEntries(spec.fields.filter((f) => f.flag).map((f) => [f.flag, f]));
  for (let i = 0; i < words.length; i++) {
    const f = byFlag[words[i]]; if (!f) continue;
    if (f.kind === 'flag') vals[f.name] = true; else if (i + 1 < words.length) { vals[f.name] = words[i + 1]; i++; }
  }
  return [path, vals];
}
function openForm(command, values, note) {
  const path0 = command.split(/\s+/).filter((w) => !w.startsWith('-')).join(' ');
  const spec = COMMANDS.forms[path0]; if (!spec) return;
  const [path, fromCmd] = splitCommand(command, spec);
  const weekly = COMMANDS.panel.weekly.find((w) => w.command === path);
  const labels = COMMANDS.panel.labels[path] || {};
  const vals = Object.assign({}, weekly ? weekly.defaults : {}, fromCmd, values || {});
  for (const b of $$('#run-menu button')) b.classList.toggle('on', b.dataset.command === path);
  const form = $('#run-form'); form.replaceChildren(); form.hidden = false;
  const h = el('h4', null, weekly ? weekly.label : path); h.title = spec.help; form.append(h);
  const first = (t) => { const m = String(t || '').match(/^.*?(?:[.:;](?=\s)|\s--\s|$)/); return (m ? m[0] : t).replace(/[\s:;-]+$/, '').slice(0, 160); };
  form.append(el('p', 'when', (weekly ? weekly.when + ' · ' : '') + first(spec.label || spec.help)));
  if (note) form.append(el('p', 'note', note));
  const up = new Set([...(weekly ? weekly.common : spec.fields.slice(0, 4).map((f) => f.name)), ...spec.fields.filter((f) => f.required).map((f) => f.name)]);
  const inputs = {};
  function row(f, box) {
    const lab = el('label', 'lab' + (f.required ? ' req' : ''), labels[f.name] || f.question);
    lab.title = f.help || f.question;
    let inp;
    if (f.kind === 'flag') { inp = el('input'); inp.type = 'checkbox'; inp.checked = !!vals[f.name]; }
    else if (f.kind === 'choice') { inp = el('select'); inp.append(new Option(f.default ? 'default (' + f.default + ')' : 'default', '')); for (const c of f.choices) inp.append(new Option(c, c)); inp.value = vals[f.name] || ''; }
    else { inp = el('input'); inp.type = 'text'; inp.value = vals[f.name] != null ? vals[f.name] : (f.default || ''); if (!f.default) inp.placeholder = f.many ? 'comma-separated' : ''; }
    inp.name = f.name; inp.id = 'opt-' + f.name; lab.htmlFor = inp.id; inp.title = f.help || '';
    inp.addEventListener('input', preview); inp.addEventListener('change', preview);
    inputs[f.name] = inp;
    if (f.kind === 'flag') box.toggles.append(inp, lab); else box.texts.append(lab, inp);
  }
  function group(parent) {
    const texts = el('div', 'opts'), toggles = el('div', 'opts toggles'); parent.append(texts, toggles); return {texts, toggles};
  }
  const main = group(form);
  for (const f of spec.fields) if (up.has(f.name)) row(f, main);
  const rest = spec.fields.filter((f) => !up.has(f.name));
  if (rest.length) {
    const more = el('details', 'more'); more.append(el('summary', null, 'More options (' + rest.length + ')'));
    const box = group(more); for (const f of rest) row(f, box); form.append(more);
  }
  let confirmInput = null;
  if (spec.confirm) {
    const box = el('div', 'opts'); const lab = el('label', 'lab', 'Type "' + spec.confirm + '" to confirm');
    lab.title = 'This one rewrites tabs.'; confirmInput = el('input'); confirmInput.type = 'text'; confirmInput.id = 'opt-confirm'; lab.htmlFor = 'opt-confirm';
    box.append(lab, confirmInput); form.append(box);
  }
  const pv = el('p', 'preview'); pv.id = 'run-preview'; form.append(pv);
  /* a box still holding the command's own default is left out: the command applies it anyway, and the
     preview shows only what Sam changed */
  const DEF = Object.fromEntries(spec.fields.map((f) => [f.name, f.default]));
  function current_values() {
    const out = {};
    for (const [n, inp] of Object.entries(inputs)) {
      if (inp.type === 'checkbox') out[n] = inp.checked;
      else out[n] = DEF[n] != null && inp.value.trim() === String(DEF[n]) ? '' : inp.value;
    }
    return out;
  }
  function preview() {
    const v = current_values(), words = [path], pos = [];
    for (const f of spec.fields) {
      const x = v[f.name];
      if (f.kind === 'flag') { if (x && f.flag) words.push(f.flag); continue; }
      const t = String(x == null ? '' : x).trim(); if (!t) continue;
      if (f.flag) words.push(f.flag, shq(t)); else pos.push(...(f.many ? t.split(/[,;]/).map((q) => q.trim()).filter(Boolean) : [t]).map(shq));
    }
    pv.textContent = [...words, ...pos].join(' ');
  }
  preview();
  const go = el('button', 'primary', 'Run'); go.type = 'submit';
  if (spec.writes && COMMANDS.read_only) { go.disabled = true; form.append(el('p', 'bad', 'This week is closed: nothing that writes can run on it.')); }
  const cancel = el('button', null, 'Close'); cancel.type = 'button'; cancel.onclick = () => { form.hidden = true; for (const b of $$('#run-menu button')) b.classList.remove('on'); };
  form.append(go, document.createTextNode(' '), cancel);
  form.onsubmit = async (ev) => {
    ev.preventDefault();
    try {
      const r = await post('/api/jobs/start', {command: path, values: current_values(), confirm: confirmInput ? confirmInput.value : ''});
      followJob(r.job, r.command);
    } catch (e) { showRunError(e.message); }
  };
  form.scrollIntoView({block: 'nearest'});
}
const $$ = (s2, r = document) => [...r.querySelectorAll(s2)];
function showRunError(text) {
  const out = $('#run-out'); out.hidden = false; out.textContent = text;
}
function followJob(job, command) {
  current = job; const out = $('#run-out'); out.hidden = false; out.textContent = '$ ' + command + '\n';
  $('#run-stop').hidden = false; $('#run-form').hidden = true;
  const es = new EventSource(url('/api/jobs/stream?job=' + job + '&after=0'));
  es.onmessage = (m) => {
    const ev = JSON.parse(m.data);
    if (ev.line != null) { out.textContent += ev.line + '\n'; out.scrollTop = out.scrollHeight; }
    if (ev.end != null) {
      es.close(); current = null; $('#run-stop').hidden = true;
      out.textContent += '\n[exit ' + ev.end + ']'; loadHistory(); refreshSections();
    }
  };
  es.onerror = () => { es.close(); if (current) setTimeout(() => followJob(job, command), 1000); };
}
$('#run-stop').onclick = async () => { if (current) { try { await post('/api/jobs/stop', {job: current}); } catch (e) { showRunError(e.message); } } };
function took(sec) { if (sec == null) return ''; const s2 = Math.round(sec); return s2 < 60 ? s2 + 's' : Math.floor(s2 / 60) + 'm ' + String(s2 % 60).padStart(2, '0') + 's'; }
function at(iso) { try { return new Date(iso).toLocaleString([], {weekday: 'short', hour: 'numeric', minute: '2-digit'}); } catch (e) { return iso; } }
async function loadHistory() {
  try {
    const r = await get('/api/jobs/history'); const ul = $('#run-history'); ul.replaceChildren();
    if (!r.history.length) ul.append(el('li', 'dim', 'Nothing run from this page yet.'));
    for (const h of r.history) {   // newest first (the server's order)
      const ok = h.rc === 0 && !h.stopped;
      const li = el('li', ok ? 'ok' : 'bad');
      li.append(el('span', 'st', ok ? 'ok' : (h.stopped ? 'stopped' : 'failed')), el('code', null, h.command),
        el('span', 't', [took(h.seconds), at(h.time), ok || h.stopped ? '' : 'exit ' + h.rc].filter(Boolean).join(' · ')));
      if (h.id) { const a = el('a', null, 'output'); a.href = '#'; a.dataset.job = h.id; a.onclick = (e) => { e.preventDefault(); showOutput(h.id, h.command); }; li.append(a); }
      ul.append(li);
    }
  } catch (e) {}
}
async function showOutput(id, command) {
  const out = $('#run-out'); out.hidden = false;
  try { const r = await get('/api/jobs/output?job=' + encodeURIComponent(id)); out.textContent = r.lines.join('\n'); }
  catch (e) { out.textContent = command + ': ' + e.message; }
  out.scrollIntoView({block: 'nearest'});
}


/* ---------- buttons inside the sections: the next step, Build, Late swap ---------- */
async function startJob(command, values) {
  try {
    const r = await post('/api/jobs/start', {command, values: values || {}, confirm: ''});
    $('#live-run').scrollIntoView({block: 'start'});
    followJob(r.job, command);
  } catch (e) { showRunError(e.message); $('#live-run').scrollIntoView({block: 'start'}); }
}

/* ---------- the page's own writes: Pool cells, decisions, the starting pool (week_actions.py) ---------- */
function rulesWas() { try { return JSON.parse($('#rules-was').textContent || '{}'); } catch (e) { return {}; } }
function toast(text, bad) {
  const t = el('div', 'toast' + (bad ? ' bad' : ''), text); document.body.append(t);
  setTimeout(() => t.remove(), bad ? 9000 : 4000);
}
/* the read-back goes to the shared pool store (week_ui), which paints every place that shows him */
function showPool(values) { if (window.dfsPool) window.dfsPool.set(values || {}); }
/* the page's other writes (decisions, the starting pool) wait their turn in the same queue as the pool
   cells (week_ui's dfsQueue): never refused for being second; the queue refreshes the sections at its end */
function write(path, data) {
  return new Promise((resolve) => window.dfsQueue.run(async () => {
    try { const r = await post(path, data); showPool(r.pool); if (r.message) toast(r.message); resolve(r); }
    catch (e) {
      if (e.body && e.body.current && path.startsWith('/api/pool')) showPool(e.body.current);
      toast(e.message, true); resolve(null);
    }
  }));
}
window.dfsLive = {post, pool: (body) => post('/api/pool', body), stack: (body) => post('/api/stack', body), refresh: refreshSections, refreshing: () => !!refreshing || again, toast};
function askFirst(anchor, text, yes, onYes) {
  const old = anchor.parentElement.querySelector('.confirm'); if (old) { old.remove(); return; }
  const box = el('div', 'confirm flag'); box.append(el('div', null, text));
  const ok = el('button', 'primary', yes), no = el('button', null, 'Cancel');
  ok.onclick = () => { box.remove(); onYes(); }; no.onclick = () => box.remove();
  box.append(ok, document.createTextNode(' '), no); anchor.after(box);
}
document.addEventListener('click', async (e) => {
  const b = e.target.closest('button.runbtn');
  if (b) { if (b.dataset.form) openForm(b.dataset.command, {}, ''); else startJob(b.dataset.command, {}); return; }
  if (READONLY && e.target.closest('.seg button, .dec button, #start-pool')) { toast('This week is closed: the page writes nothing.', true); return; }
  if (e.target.closest('button[data-action=start-pool]')) { const sp = $('#start-pool'); if (sp) { sp.scrollIntoView({block: 'center'}); sp.click(); } return; }
  const sp = e.target.closest('#start-pool');
  if (sp) {
    askFirst(sp, 'Add these to your pool on the sheet: ' + sp.dataset.names + '?', 'Yes, add them', () => write('/api/pool/start', {}));
    return;
  }
  const opt = e.target.closest('.dec button.opt');
  if (opt) {
    if (opt.dataset.opt === 'pivot') {
      const ul = document.querySelector('ul.pivots[data-key="' + CSS.escape(opt.dataset.key) + '"]'); if (ul) ul.hidden = !ul.hidden; return;
    }
    await write('/api/decide', {key: opt.dataset.key, option: opt.dataset.opt, rules_was: rulesWas()}); return;
  }
  const pv = e.target.closest('.dec button.pivot');
  if (pv) { await write('/api/decide', {key: pv.dataset.key, option: 'pivot', pivot: pv.dataset.pivot, pool_was: pv.dataset.was || '', rules_was: rulesWas()}); return; }
  const un = e.target.closest('.dec button.undo');
  if (un) { await write('/api/decide', {key: un.dataset.key, option: 'undo', rules_was: rulesWas()}); }
});

/* ---------- the contest check form, the budget, the drop zone ---------- */
const checkForm = $('#check-form');
checkForm.format.onchange = () => { $('#opp-field').hidden = checkForm.format.value !== 'H2H'; };
checkForm.onsubmit = async (ev) => {
  ev.preventDefault(); const out = $('#check-out'); out.replaceChildren();
  const data = {}; for (const [k, v] of new FormData(checkForm)) data[k] = v;
  try {
    const r = await post('/api/check', data); const box = el('div', 'verdict');
    box.append(el('span', 'tier ' + r.tier, r.tier === 'Avoid' ? 'Avoid' : 'Tier ' + r.tier), document.createTextNode(r.reason));
    const bits = []; if (r.rake != null) bits.push('Effective rake ' + (r.rake * 100).toFixed(1) + '%');
    if (r.opponent_record) bits.push('Your record against him: ' + r.opponent_record);
    if (bits.length) box.append(el('div', 'note', bits.join('  |  ')));
    for (const f of r.flags) box.append(el('div', 'note', 'Flag: ' + f));
    out.append(box);
  } catch (e) { out.append(el('p', 'bad', e.message)); }
};
$('#budget-form').onsubmit = async (ev) => {
  ev.preventDefault();
  try { await post('/api/budget', {budget: ev.target.budget.value}); refreshSections(); ev.target.budget.value = ''; } catch (e) { alert(e.message); }
};
const zone = $('#dropzone');
['dragenter', 'dragover'].forEach((n) => zone.addEventListener(n, (e) => { e.preventDefault(); zone.classList.add('over'); }));
['dragleave', 'drop'].forEach((n) => zone.addEventListener(n, (e) => { e.preventDefault(); zone.classList.remove('over'); }));
zone.addEventListener('drop', (e) => { for (const f of e.dataTransfer.files) upload(f); });
$('#drop-file').onchange = (e) => { for (const f of e.target.files) upload(f); e.target.value = ''; };
async function upload(file) {
  const out = $('#drop-out'); const card = el('div', 'verdict'); out.prepend(card);
  card.append(el('div', null, 'Checking ' + file.name + '...'));
  try {
    const r = await fetch(url('/api/drop?name=' + encodeURIComponent(file.name)), {method: 'POST', headers: {'Content-Type': 'application/octet-stream'}, body: file});
    const j = await r.json(); if (!r.ok) throw new Error(j.error || r.statusText);
    card.replaceChildren(el('div', null, 'Saved as ' + j.name + ' (' + j.kind + ')'));
    for (const s of j.summary) card.append(el('div', null, s));
    for (const w of j.warnings) card.append(el('div', 'bad', w));
    if (j.tiers) card.append(tierTable(j.tiers));
    for (const run of j.runs) {
      const b = el('button', 'primary', run.label); b.style.marginRight = '8px';
      b.onclick = () => openForm(run.command, run.values, 'From your drop: ' + j.name);
      card.append(b);
    }
  } catch (e) { card.replaceChildren(el('div', 'bad', file.name + ': ' + e.message)); }
}
function tierTable(t) {
  const wrap = el('div'); const tbl = el('table'); const head = el('tr');
  for (const h of ['Tier', 'Entries', 'Spent so far', 'Plan']) head.append(el('th', null, h)); tbl.append(head);
  for (const k of ['A', 'B', 'C', 'Avoid']) {
    const tr = el('tr'); const plan = t.plan && t.plan[k] != null ? '$' + t.plan[k].toFixed(2) : (k === 'Avoid' ? '$0' : '-');
    tr.append(el('td', null, k), el('td', null, String((t.entries || {})[k] || 0)), el('td', null, '$' + (t.spent[k] || 0).toFixed(2)), el('td', null, plan)); tbl.append(tr);
  }
  wrap.append(tbl);
  if (t.over && t.over.length) wrap.append(el('div', 'bad', 'Over the plan: ' + t.over.join('; ') + '. You can still withdraw before lock.'));
  else if (t.plan) wrap.append(el('div', 'ok', 'Within the plan.'));
  else wrap.append(el('div', 'note', 'No Weekly Budget yet, so no plan to compare with.'));
  const det = el('details'); det.append(el('summary', null, 'Each contest and its tier'));
  for (const c of t.contests || []) det.append(el('div', 'note', c.tier + '  $' + c.fee.toFixed(2) + '  ' + c.name + ': ' + c.reason + (c.sized ? '' : ' (field unknown)')));
  wrap.append(det); return wrap;
}

loadCommands().catch((e) => showRunError(e.message)); loadHistory(); loadChats(); status();
})();
"""


def augment(page: str, *, token: str, nonce: str, read_only: bool, budget: float | None = None) -> str:
    """The live version of a rendered Week page (see the module docstring)."""
    page = SAY.sub(
        lambda m: f'{m.group(1)}<button type="button" class="ask" data-say="{m.group(2)}">Ask</button>', page
    )
    page = STATIC_ONLY.sub("", page)
    page = BUTTON_SLOT.sub(
        lambda m: f'<button type="button" class="runbtn" data-command="{m.group(1)}">{m.group(2)}</button>',
        page,
    )
    contest = CONTEST_HTML.replace("%BUDGET%", f"{budget:,.0f}" if budget else "150")
    page = page.replace(week_page.LIVE_CONTEST, contest).replace(week_page.LIVE_RUN, RUN_HTML)
    chat = CHAT_HTML.replace(
        "%MODELS%",
        "".join(
            f'<option value="{m}"{" selected" if m == week_chat.DEFAULT_MODEL else ""}>{m}</option>'
            for m in week_chat.MODELS
        ),
    ).replace(
        "%EFFORTS%",
        "".join(
            f'<option value="{e}"{" selected" if e == week_chat.DEFAULT_EFFORT else ""}>{e}</option>'
            for e in week_chat.EFFORTS
        ),
    )
    metas = (
        f'<meta name="dfs-token" content="{html.escape(token)}">'
        f'<meta name="dfs-readonly" content="{1 if read_only else 0}">'
        '<meta name="referrer" content="no-referrer">'
    )
    page = page.replace("</head>", f"{metas}<style>{CSS}</style></head>", 1)
    page = page.replace("<body>", '<body class="live"><div id="pagecol">', 1)
    page = page.replace("</body>", f'</div>{chat}<script nonce="{nonce}">{JS}</script></body>', 1)
    return page
