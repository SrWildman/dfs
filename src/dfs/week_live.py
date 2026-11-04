"""What `dfs week serve` adds to the Week page: the chat panel, Ask buttons, the run buttons, the contest check
form and the file drop zone.

The static page (`week_page.render`) stays exactly what `dfs week page` writes. `augment` takes that HTML and
returns the live version:

- every `"phrase"` Sam can say gets an **Ask** button that puts it in the chat box (it never sends);
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
#chat.collapsed .chat-head{justify-content:center;padding:8px 0}
.chat-controls{padding:6px 10px;display:flex;flex-wrap:wrap;gap:6px 10px;align-items:center;font-size:12.5px;
border-bottom:1px solid var(--line)}
.chat-controls select,.chat-controls button{font:inherit;padding:2px 6px}
#lockbar{font-size:12.5px;padding:4px 10px;color:var(--dim);border-bottom:1px solid var(--line)}
#lockbar.busy{background:var(--flag);color:var(--ink)}
#chat-log{flex:1;overflow-y:auto;padding:10px;font-size:14px}
.msg{margin:0 0 10px;white-space:pre-wrap;word-break:break-word}
.msg.user{background:rgba(31,94,255,.1);border-radius:8px;padding:6px 10px}
.msg.error{color:var(--bad)}.msg.note{color:var(--dim);font-size:12.5px}
.chip{margin:0 0 8px;border:1px solid var(--line);border-radius:6px;font-size:12.5px;padding:2px 8px}
.chip summary{cursor:pointer;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.chip pre{white-space:pre-wrap;word-break:break-word;font:11.5px/1.4 ui-monospace,Menlo,monospace;margin:4px 0;
max-height:220px;overflow:auto}
.chip.err summary{color:var(--bad)}
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
.formgrid{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:8px 12px;margin:8px 0}
.formgrid label{display:flex;flex-direction:column;font-size:12.5px;color:var(--dim);gap:2px}
.verdict{border:1px solid var(--line);border-radius:8px;padding:8px 12px;margin:8px 0}
.tier{display:inline-block;font-weight:700;border-radius:6px;padding:1px 10px;color:#fff;margin-right:6px}
.tier.A{background:var(--proven)}.tier.B{background:var(--accent)}.tier.C{background:var(--shadow)}
.tier.Avoid{background:var(--bad)}
.dropzone{border:2px dashed var(--line);border-radius:10px;padding:16px;text-align:center;color:var(--dim);
margin:8px 0}.dropzone.over{border-color:var(--accent);color:var(--accent)}
.menu-buttons{display:flex;flex-wrap:wrap;gap:6px;margin:8px 0}
.menu-buttons button,.cmdlist button{border:1px solid var(--line);background:var(--bg);color:var(--ink);
border-radius:6px;padding:4px 10px;cursor:pointer}
.cmdlist{max-height:180px;overflow:auto;margin:6px 0;display:flex;flex-wrap:wrap;gap:4px}
.cmdlist button{font-size:12px}
#run-out{background:#0f1115;color:#d7dae0;border-radius:8px;padding:10px;font:12px/1.4 ui-monospace,Menlo,
monospace;max-height:320px;overflow:auto;white-space:pre-wrap;word-break:break-word;margin:8px 0}
@media(max-width:900px){body.live{display:block}#pagecol{height:auto;overflow:visible}
#chat{position:fixed;right:0;top:0;width:100%;max-width:430px;z-index:5}#chat.collapsed{width:44px}}
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
<p class="note">Buttons run the same <code>dfs</code> commands as the terminal, one at a time, and a command that
writes takes the run lock, so it is refused while the chat or a terminal is running one.</p>
<div class="menu-buttons" id="run-menu"></div>
<input type="text" id="run-search" placeholder="Search all commands (sync, odds, cockpit...)" size="40">
<div class="cmdlist" id="run-list"></div>
<form id="run-form" hidden></form>
<pre id="run-out" hidden></pre>
<button id="run-stop" hidden>Stop</button>
<h4>Recent runs</h4><ul id="run-history"></ul>
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
  if (!r.ok) { const err = new Error(body.error || r.statusText); err.status = r.status; throw err; }
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
function render(ev) {
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
      log.appendChild(d); chat.tools[ev.id] = d; scroll(); break; }
    case 'result': {
      const d = chat.tools[ev.id];
      if (d) { d.querySelector('.res').textContent = ev.text; if (ev.error) d.classList.add('err'); }
      break; }
    case 'approval': chat.bubble = null; approvalCard(ev); break;
    case 'approval_closed': {
      const c = document.getElementById('appr-' + ev.id);
      if (c) { c.querySelector('.btns').replaceChildren(el('span', 'note', ev.allowed === false ? 'Denied.' : (ev.allowed ? 'Approved.' : 'Closed.'))); }
      break; }
    case 'error': chat.bubble = null; add('error', ev.text); break;
    case 'note': add('note', ev.text); break;
    case 'done': chat.bubble = null;
      add('note', ev.stopped ? 'Stopped.' : (ev.ok === false ? 'Finished with an error.' : 'Done' + (ev.cost != null ? ' ($' + Number(ev.cost).toFixed(3) + ')' : '') + '.')); break;
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
    const ev = JSON.parse(m.data); chat.last = ev.seq; render(ev);
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
$('#chat-toggle').onclick = () => $('#chat').classList.toggle('collapsed');
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
  $('#chat').classList.remove('collapsed'); box.value = b.dataset.say; box.focus();
});

/* ---------- status ---------- */
async function status() {
  try {
    const s = await get('/api/status'); const bar = $('#lockbar');
    if (s.lock) { bar.textContent = s.lock.text; bar.className = 'busy'; }
    else if (s.job) { bar.textContent = s.job + ' is running from a button.'; bar.className = 'busy'; }
    else { bar.textContent = 'Nothing is running.'; bar.className = ''; }
    if (!chat.running && s.chat_busy && !chat.finished) setRunning(true);
  } catch (e) {}
}
setInterval(status, 4000);

/* ---------- reload the page's sections (the chat and the live cards stay where they are) ---------- */
async function refreshSections() {
  try {
    const r = await fetch(url('/?fragment=1')); if (!r.ok) return;
    const doc = new DOMParser().parseFromString(await r.text(), 'text/html');
    for (const fresh of doc.querySelectorAll('#pagecol section.card[id]:not([data-keep])')) {
      const old = document.getElementById(fresh.id); if (old) old.replaceWith(fresh);
    }
    const sub = doc.querySelector('.sub'), cur = $('.sub'); if (sub && cur) cur.replaceWith(sub);
  } catch (e) {}
}

/* ---------- run buttons ---------- */
let COMMANDS = null;
async function loadCommands() {
  COMMANDS = await get('/api/commands');
  const menu = $('#run-menu');
  for (const m of COMMANDS.menu) {
    const b = el('button', null, m.label + (m.command ? '' : ' (in the sheet)')); b.disabled = !m.command;
    if (m.command) b.onclick = () => openForm(m.command);
    menu.append(b);
  }
  $('#run-week').textContent = COMMANDS.read_only ? 'closed week: read only' : '';
  filterList('');
}
function filterList(q) {
  const list = $('#run-list'); list.replaceChildren();
  const words = q.toLowerCase().split(/\s+/).filter(Boolean);
  for (const f of Object.values(COMMANDS.forms)) {
    const hay = (f.command + ' ' + f.label + ' ' + f.help).toLowerCase();
    if (words.every((w) => hay.includes(w))) {
      const b = el('button', null, f.command.replace(/^dfs /, '')); b.title = f.label || f.help;
      b.onclick = () => openForm(f.command); list.append(b);
    }
  }
}
$('#run-search').oninput = (e) => filterList(e.target.value);
let current = null;
function openForm(command, values, note) {
  const path = command.split(/\s+/).filter((w) => !w.startsWith('-')).join(' ');
  const spec = COMMANDS.forms[path]; if (!spec) return;
  const form = $('#run-form'); form.replaceChildren(); form.hidden = false;
  form.append(el('h4', null, command), el('p', 'note', spec.label || spec.help));
  if (note) form.append(el('p', 'note', note));
  const grid = el('div', 'formgrid');
  for (const f of spec.fields) {
    const lab = el('label'); lab.append(document.createTextNode(f.question + (f.required ? ' *' : '')));
    let inp;
    if (f.kind === 'flag') { inp = el('input'); inp.type = 'checkbox'; inp.checked = !!(values && values[f.name]); }
    else if (f.kind === 'choice') { inp = el('select'); inp.append(new Option('', '')); for (const c of f.choices) inp.append(new Option(c, c)); inp.value = (values && values[f.name]) || ''; }
    else { inp = el('input'); inp.type = 'text'; inp.value = (values && values[f.name]) || ''; if (f.default) inp.placeholder = f.default; }
    inp.name = f.name; lab.append(inp); grid.append(lab);
  }
  let confirmInput = null;
  if (spec.confirm) {
    const lab = el('label'); lab.append(document.createTextNode('This rewrites tabs. Type "' + spec.confirm + '" to confirm'));
    confirmInput = el('input'); confirmInput.type = 'text'; lab.append(confirmInput); grid.append(lab);
  }
  form.append(grid);
  const go = el('button', 'primary', 'Run'); go.type = 'submit';
  if (spec.writes && COMMANDS.read_only) { go.disabled = true; form.append(el('p', 'bad', 'This week is closed: nothing that writes can run on it.')); }
  form.append(go);
  form.onsubmit = async (ev) => {
    ev.preventDefault(); const vals = {};
    for (const inp of grid.querySelectorAll('input[name],select[name]')) vals[inp.name] = inp.type === 'checkbox' ? inp.checked : inp.value;
    try {
      const r = await post('/api/jobs/start', {command, values: vals, confirm: confirmInput ? confirmInput.value : ''});
      followJob(r.job, command);
    } catch (e) { showRunError(e.message); }
  };
  form.scrollIntoView({block: 'nearest'});
}
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
async function loadHistory() {
  try {
    const r = await get('/api/jobs/history'); const ul = $('#run-history'); ul.replaceChildren();
    if (!r.history.length) ul.append(el('li', 'dim', 'Nothing run from this page yet.'));
    for (const h of r.history) ul.append(el('li', null, h.time.slice(5, 16).replace('T', ' ') + ' UTC  ' + h.command + (h.rc === 0 ? '' : '  (exit ' + h.rc + (h.stopped ? ', stopped' : '') + ')')));
  } catch (e) {}
}

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
