"""The Explorer: every priced player on the slate (OUT and IR left out), every EdgeRaw column, any lens.

The board is the short answer; the Explorer is where the depth lives. The data is embedded in the page as JSON
(one array per player, `payload`) and a small vanilla script (`JS`, no library, nothing loaded, nothing sent
out) draws the table:

- **Lenses** (`LENSES`): one-click presets, each a column set, a default sort and a note on what it answers.
  `All columns` shows EdgeRaw's own groups (`groups`, from `derived.EDGE_SHEET_ORDER`) as collapsible column
  groups.
- **Show 3 / 10 / 25 / all**, per position or across positions; **filters** on position, salary, team, game,
  kickoff window, Questionable, the pool, role chip, signal chip and a name search; **sort** by any column; a
  **column picker** inside any lens. The last lens, columns and filters are remembered per browser
  (localStorage, every access wrapped in try/catch: a convenience, never state).
- **Trust labels on every column header** (`COLUMN_TRUST`: Proven, Shadow, Untested or Data), so the depth
  never looks more trusted than it is.
- **The Pool column** on every row (the same Cash / GPP / Both / none control the board has; the served page
  writes the sheet's Pool cell, `week_writes`).
- **The player drawer**: a click on any name anywhere on the page opens everything about him, grouped like the
  sheet's Player tab (projections by source, ownership, his last five games, the matchup, the props, the
  signals with their record, his rank in each lens, the Suggested builds that hold him).
- **Charts follow the lens**: Value shows the value map, GPP the leverage map, Usage target share against
  snaps (`week_charts`).
- **Performance**: 100 rows a page; only the visible page is in the DOM.

Pure: `payload` takes what `week_inputs.gather` read; `tests/test_week_explorer.py` checks every lens column
exists in edge.csv's columns (`derived.EDGE_COLUMNS`) or is one of the derived ones here (`DERIVED`).
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from dfs import derived, week_plays
from dfs.player_join import TEAM_ALIASES
from dfs.week_board import ROLES as ROLE_NAMES
from dfs.week_board import Board, implied_total, pid
from dfs.week_plays import num

PROVEN, SHADOW, UNTESTED, DATA = "Proven", "Shadow", "Untested", "Data"

# Derived per row here (not EdgeRaw columns): the names the lenses may use beside EdgeRaw's own.
PER_K, IMPLIED, CHALK, PROPS_GAP, SRC_SPREAD, POOL, ROLES = (
    "Per $1K",
    "Implied",
    "Chalk",
    "PropsGap",
    "SrcSpread",
    "Pool",
    "Roles",
)
DERIVED = (PER_K, IMPLIED, CHALK, PROPS_GAP, SRC_SPREAD, POOL, ROLES)
SOURCES = ("CalPts", "ProjPts", "SleeperPts", "FantasyProsPts", "EspnPts", "PropPts")
LABELS = {*derived.ZONE_LABELS, *derived.EDGE_ONLY_LABELS}
HIDDEN = {"NameKey", "Id"}

# Values saved as a fraction, shown as a percent.
FRACTION = {
    "Own%",
    "Snap%",
    "Tgt%",
    "Rush%",
    "GppOwn",
    "CashOwn",
    "OurOwn",
    "xFP Rec%",
    "Snap% L5",
    "TPRR~",
    "TPRR~ L3",
}
TEXT = {
    "Name",
    "Position",
    "Team",
    "Opp",
    "Avail",
    "Flags",
    "Edge",
    "GameID",
    "OwnStatus",
    "GameStart",
    "WX",
    "Stadium",
    "Roof",
    "Flag",
    "Kickoff",
    CHALK,
    POOL,
    ROLES,
}
MONEY = {"Salary"}
HEADER = {  # a shorter header where EdgeRaw's own is long or cryptic
    "ProjPts": "TFFB",
    "SleeperPts": "Sleeper",
    "FantasyProsPts": "FPros",
    "EspnPts": "ESPN",
    "PropPts": "Props",
    "Own%": "TFFB Own%",
    "CashOwn": "DU own",
    "GppOwn": "GPP own",
    "OurOwn": "Our own",
    "OverUnder": "Total",
    "OppPosRank": "Opp rank",
    "SrcSpread": "Src spread",
    "PropsGap": "Props gap",
}
TIPS = {  # plain words, the header's tooltip
    "CalPts": "CalPts: our calibrated projection: TFFB, Sleeper and FantasyPros, bias-corrected and weighted by "
    "accuracy.",
    "Hit3x%": "Hit 3x: the chance he scores 3x his salary in thousands (the cash bar).",
    "Boom%": "Boom: the chance he scores 4x his salary in thousands (the GPP bar).",
    "Bust%": "Bust: the chance he scores under 2x his salary in thousands.",
    "CashOwn": "DU own: predicted ownership in double-ups, where the chalk is far heavier than in GPPs.",
    "GppOwn": "GPP own: TFFB's projected ownership, sharpened for your GPPs.",
    "OurOwn": "What GPPs would own if the field used CalPts.",
    "Leverage": "Leverage: ceiling rank against ownership rank (the sheet's Leverage): high = upside the field "
    "is not on.",
    "Per $1K": "CalPts per $1,000 of salary.",
    "Implied": "His team's implied total from the line: half the total minus half the spread.",
    "Spread": "His team's spread from the line (minus = favoured).",
    "OverUnder": "The game's total (over / under).",
    "PropsGap": "Props projection minus CalPts.",
    "SrcSpread": "The widest gap between the sources' projections: high = the sources disagree.",
    "Floor": "Floor: the 20th percentile of his outcomes.",
    "Ceiling": "TFFB's ceiling.",
    "CeilM": "Our ceiling: the 85th percentile of his outcomes.",
    "OppPosRank": "How the opponent ranks against his position, 1 = easiest (the schedule-strength rank).",
    "Chalk": "30%+ in double-ups.",
}


def trust_of(column: str) -> tuple[str, str]:
    """A column's trust chip and why. Predictions get Proven / Shadow / Untested (the audit's verdicts, the
    Week page's `TRUST`); feeds and arithmetic on them are Data."""
    if column in ("CalPts", PER_K, "CalPts%ile"):
        return PROVEN, "CalPts beat TFFB on average error in Model Check; per $1K is arithmetic on it."
    if column in ("CashOwn", "GppOwn", "OurOwn", CHALK):
        return (
            PROVEN,
            "Our ownership estimate beat TFFB's on average error in Model Check; still an estimate.",
        )
    if column in ("EspnPts", "PropPts", PROPS_GAP):
        return SHADOW, "A shadow source: scored in Model Check, not in CalPts until it earns its way in."
    if column in ("Hit3x%", "Bust%", "Floor"):
        return SHADOW, "Outcome odds from the distribution engine; not scored against results yet."
    if column in (
        "Boom%",
        "CeilM",
        "Leverage",
        "CeilPct",
        "Ceiling",
        "CeilVal",
        "Ceiling%ile",
        "CeilVal%ile",
    ):
        return UNTESTED, "Ceilings and leverage are not scored against results; a way to look, not a rule."
    if column in ("Edge",):
        return SHADOW, "Signal chips: FADE↓ is proven (Model Check); the others are context, not verdicts."
    if column in (
        "xFP/G",
        "xFP Szn",
        "xFP trend",
        "xFP Rec%",
        "xFP/$",
        "Tgt/$",
        "HVT/$",
        "PriceGap",
        "TPRR~",
        "TPRR~ L3",
        "xFP/G%ile",
    ):
        return SHADOW, "Opportunity per dollar: shadow signals in Model Check, no verdict yet."
    if column in ("ValAdj", "Val", "Val%ile", "AggPts", "AggPts%ile", "GameEnv", "OppEPA", SRC_SPREAD):
        return UNTESTED, "A derived score the tool computes; not scored against results on its own."
    return (
        DATA,
        "A feed or arithmetic on one (a source's number, the line, usage, weather): not a prediction.",
    )


def fmt_of(column: str) -> str:
    if column in TEXT:
        return "text"
    if column in MONEY:
        return "money"
    if column in FRACTION:
        return "frac"
    if column.endswith("%") or column.endswith("%ile") or column == "CeilPct":
        return "pct"
    return "num"


def columns() -> list[str]:
    """Every EdgeRaw column the Explorer shows, in the tab's order, then the derived ones."""
    own = [c for c in derived.EDGE_SHEET_ORDER if c not in LABELS and c not in HIDDEN]
    return [*own, *DERIVED]


def groups() -> list[tuple[str, list[str]]]:
    """EdgeRaw's own column groups (the label columns start each one), in the tab's order."""
    out: list[tuple[str, list[str]]] = [("Spine", [])]
    names = {
        "SRC": "Sources",
        "OWN": "Field ownership",
        "GAME": "Game",
        "CEIL": "Ceiling detail",
        "MOVE": "Movement",
        "WX": "Weather",
        "USAGE": "Usage",
        "OPP$": "Opportunity per $",
    }
    for c in derived.EDGE_SHEET_ORDER:
        if c in LABELS:
            out.append((names.get(c, c), []))
        elif c == "Id":
            out.append(("Helpers", []))
        elif c not in HIDDEN:
            out[-1][1].append(c)
    out.append(("Derived here", list(DERIVED)))
    return [(g, cols) for g, cols in out if cols]


@dataclass(frozen=True)
class Lens:
    key: str
    label: str
    note: str
    columns: tuple[str, ...]
    sort: str
    desc: bool = True
    chart: str = ""  # value | leverage | usage
    signal_only: bool = False


BASE = (
    "Name",
    "Position",
    "Team",
    "Opp",
    "Spread",
    "OverUnder",
    IMPLIED,
    "Salary",
)  # Plan N item 4: the game on every lens
LENSES: tuple[Lens, ...] = (
    Lens(
        "value",
        "Value",
        "Who is cheap for what he projects, and does his usage back it up (xFP/$ and PriceGap)?",
        ("CalPts", PER_K, "xFP/$", "PriceGap", "Avail"),
        PER_K,
        chart="value",
    ),
    Lens(
        "cash",
        "Cash",
        "Who is safe: a high floor and hit rate, a low bust rate, and how chalky in double-ups.",
        ("CalPts", "Floor", "Hit3x%", "Bust%", "CashOwn", CHALK),
        "Floor",
    ),
    Lens(
        "gpp",
        "GPP / Leverage",
        "Who can win a tournament: ceiling and boom odds against how owned he will be.",
        ("CalPts", "Ceiling", "CeilM", "Boom%", "GppOwn", "Leverage", "CeilPct"),
        "Leverage",
        chart="leverage",
    ),
    Lens(
        "usage",
        "Usage",
        "Who gets the ball: snaps, targets, WOPR, route rate, red-zone and high-value touches, the last five.",
        (
            "Snap%",
            "Tgt%",
            "WOPR",
            "TPRR~",
            "xFP/G",
            "RZ/G",
            "HVT/G",
            "Snap% L5",
            "Tgt L5",
            "Car L5",
            "DK L5",
            "xFP trend",
        ),
        "xFP/G",
        chart="usage",
    ),
    Lens(
        "matchup",
        "Matchup",
        "Who has the game: the opponent's rank against his position (the schedule-strength rank), its EPA, "
        "the totals, pace, pass rate over expected, the wind and the roof.",
        # Not `WX`: on EdgeRaw that is the weather group's label column (always empty), which the Explorer
        # leaves out, so naming it here crashed the table and the lens showed nothing (Plan O item 1).
        ("OppPosRank", "OppEPA", "OverUnder", IMPLIED, "Pace", "PROE", "Wind", "Roof", "Kickoff"),
        IMPLIED,
    ),
    Lens(
        "projections",
        "Projections",
        "Where the sources disagree: CalPts against TFFB, Sleeper, FantasyPros, ESPN and the props.",
        (*SOURCES, SRC_SPREAD),
        SRC_SPREAD,
    ),
    Lens(
        "ownership",
        "Ownership",
        "Who the field is on: GPP, double-up, ours and TFFB's ownership, with the chalk and leverage flags.",
        ("GppOwn", "CashOwn", "OurOwn", "Own%", CHALK, "Flags"),
        "GppOwn",
    ),
    Lens(
        "signals",
        "Signals",
        "Every player with a signal chip (FADE↓, USAGE↑/↓, the teammate bump INJ+, Proj ▲▼) and the props gap. "
        "FADE↓ is proven; the others are context.",
        ("CalPts", "Edge", "Flags", PROPS_GAP, "Avail"),
        "CalPts",
        signal_only=True,
    ),
    Lens("all", "All columns", "Everything EdgeRaw holds, in its own groups.", (), "CalPts"),
)
SIGNAL_TOKENS = ("FADE↓", "USAGE↑", "USAGE↓", "INJ+", "Proj ▲", "Proj ▼")


@dataclass(frozen=True)
class LensChart:
    """The chart a lens draws (Plan O item 1: every lens has one, and it follows the Explorer's filters). `x` /
    `y` are payload columns (`alt_x`: the column used where `x` is blank); `prio` names the players labelled
    (the top 8 by it); `guide` is a reference: `per_k` (3 points per $1K), `diag` (x = y) or `zero` (y = 0);
    `shade` the leverage quadrant. `formula` is the caption's first line (Plan O item 4)."""

    title: str
    x: str
    y: str
    prio: str
    formula: str
    alt_x: str = ""
    guide: str = ""
    shade: bool = False


LENS_CHARTS: dict[str, LensChart] = {
    "value": LensChart(
        "Value: salary against CalPts",
        "Salary",
        "CalPts",
        PER_K,
        "Value = CalPts ÷ salary × 1,000; the dashed line is 3 points per $1K.",
        guide="per_k",
    ),
    "cash": LensChart(
        "Cash: salary against floor",
        "Salary",
        "Floor",
        "Hit3x%",
        "Floor = the 20th percentile of his outcomes; the named are the best Hit 3x (3x his salary in thousands).",
    ),
    "gpp": LensChart(
        "Leverage: GPP ownership against ceiling",
        "GppOwn",
        "CeilM",
        "Leverage",
        "Leverage = ceiling against GPP ownership (ceiling rank minus ownership rank); shaded = under 10% owned "
        "with a top-quarter ceiling.",
        shade=True,
    ),
    "usage": LensChart(
        "Usage: snap share against target share",
        "Snap% L5",
        "Tgt%",
        "Tgt%",
        "Target share = his share of the team's targets; snap share = his share of the snaps, last five games "
        "(else the season).",
        alt_x="Snap%",
    ),
    "matchup": LensChart(
        "Matchup: opponent rank against implied total",
        "OppPosRank",
        "Implied",
        IMPLIED,
        "Implied = half the total minus half his team's spread; Opp rank 1 = the easiest matchup for his "
        "position.",
    ),
    "projections": LensChart(
        "Projections: TFFB against CalPts",
        "ProjPts",
        "CalPts",
        SRC_SPREAD,
        "CalPts = TFFB, Sleeper and FantasyPros, bias-corrected and weighted by accuracy; above the line = we "
        "project him higher than TFFB.",
        guide="diag",
    ),
    "ownership": LensChart(
        "Ownership: double-ups against GPPs",
        "CashOwn",
        "GppOwn",
        "GppOwn",
        "DU own = predicted double-up ownership; GPP own = TFFB's ownership sharpened for GPPs; above the line = "
        "chalkier in GPPs than in double-ups.",
        guide="diag",
    ),
    "signals": LensChart(
        "Signals: CalPts against the props gap",
        "CalPts",
        PROPS_GAP,
        PROPS_GAP,
        "Props gap = the props projection minus CalPts: above 0, the betting market is higher than we are.",
        guide="zero",
    ),
}
LENS_CHARTS["all"] = LENS_CHARTS["value"]


def _src_spread(row: dict) -> float | None:
    values = [v for c in SOURCES if (v := num(row.get(c))) is not None and v > 0]
    return round(max(values) - min(values), 2) if len(values) >= 2 else None


def derived_values(row: dict, pool: dict[str, str], roles: dict[str, list[str]]) -> dict[str, object]:
    cal, props = num(row.get("CalPts")), num(row.get("PropPts"))
    implied = implied_total(row)
    value = week_plays.value_per_k(row)
    own = num(row.get("CashOwn"))
    return {
        PER_K: round(value, 2) if value is not None else None,
        IMPLIED: round(implied, 1) if implied is not None else None,
        CHALK: "chalk" if own is not None and own >= week_plays.CHALK else "",
        PROPS_GAP: round(props - cal, 2) if props is not None and cal is not None else None,
        SRC_SPREAD: _src_spread(row),
        POOL: pool.get(pid(row), ""),
        ROLES: ", ".join(roles.get(str(row.get("Name", "")), [])),
    }


def players(edge: list[dict] | None) -> list[dict]:
    """Every priced player on the slate who is not OUT or on IR."""
    return [r for r in edge or [] if week_plays.salary(r) > 0 and week_plays.avail(r) not in ("OUT", "IR")]


def _cell(column: str, value: object) -> object:
    if column in TEXT:
        return "" if value is None else str(value)
    if isinstance(value, int | float):
        return value
    v = num(value)
    return None if v is None else round(v, 4)


# The served page's drawer pre-fills the docked chat with this (Plan K item 10); `{name}` is the player.
ASK_PLAYER = "Tell me about {name} this week: role, matchup, ownership, and whether he fits cash or GPP"


def payload(
    inp,
    b: Board,
    pool_by_id: dict[str, str],
    *,
    record_lines: dict[str, str] | None = None,
    log: dict[str, list[dict]] | None = None,
    practice: dict[str, str] | None = None,
    asof: str = "",
) -> dict:
    """The Explorer's data: columns (key, header, group, trust, format, tip), one array per player, the
    lenses, the Suggested builds per player, the signal records and each player's last five games."""
    cols = columns()
    group_of = {c: g for g, cs in groups() for c in cs}
    roles = b.role_of()
    rows_in = players(inp.edge)
    data = []
    ids = []
    for r in rows_in:
        extra = derived_values(r, pool_by_id, roles)
        data.append([_cell(c, extra[c] if c in extra else r.get(c)) for c in cols])
        ids.append(pid(r))
    builds: dict[str, list[str]] = {}
    for bd in (inp.suggest or {}).get("builds") or []:
        for name in bd.get("names") or []:
            builds.setdefault(name, []).append(f"{str(bd.get('kind', '')).upper()} {bd.get('number', '')}")
    return {
        "columns": [
            {
                "key": c,
                "label": HEADER.get(c, c),
                "group": group_of.get(c, ""),
                "trust": trust_of(c)[0],
                "why": trust_of(c)[1],
                "fmt": fmt_of(c),
                "tip": TIPS.get(c, ""),
            }
            for c in cols
        ],
        "rows": data,
        "ids": ids,
        "ask_player": ASK_PLAYER,
        "lenses": [
            {
                "key": lens.key,
                "label": lens.label,
                "note": lens.note,
                "columns": list(dict.fromkeys([*BASE, *lens.columns, POOL])) if lens.key != "all" else [],
                "sort": lens.sort,
                "desc": lens.desc,
                "chart": lens.chart,
                "lens_chart": LENS_CHARTS[lens.key].__dict__,
                "signal_only": lens.signal_only,
            }
            for lens in LENSES
        ],
        "groups": [{"name": g, "columns": cs} for g, cs in groups()],
        "positions": list(week_plays.POSITIONS),
        "signals": list(SIGNAL_TOKENS),
        "roles": list(ROLE_NAMES),
        "records": record_lines or {},
        "builds": builds,
        "log": log or {},
        "practice": practice or {},
        "team_alias": dict(TEAM_ALIASES),
        "chart_source": "Every priced player in the last sync (OUT and IR left out) that passes the Explorer's "
        "filters.",
        "asof": asof,
    }


def payload_json(data: dict) -> str:
    """JSON safe to put inside `<script type="application/json">`."""
    return json.dumps(data, separators=(",", ":"), default=str).replace("</", "<\\/")


JS = r"""
(() => {
const D = JSON.parse(document.getElementById('explorer-data').textContent);
const $ = (s, r = document) => r.querySelector(s);
const el = (tag, cls, text) => { const e = document.createElement(tag); if (cls) e.className = cls; if (text != null) e.textContent = text; return e; };
const COL = {}; D.columns.forEach((c, i) => { c.i = i; COL[c.key] = c; });
const IDX = {}; D.ids.forEach((id, i) => { IDX[id] = i; }); D.rows.forEach((r, i) => { r.__i = i; });
const LIVE = document.body.classList.contains('live');
const ASK_PLAYER = D.ask_player;
const PAGE = 100;
const store = { get(k, d) { try { const v = localStorage.getItem('dfs-explorer-' + k); return v == null ? d : JSON.parse(v); } catch (e) { return d; } },
                set(k, v) { try { localStorage.setItem('dfs-explorer-' + k, JSON.stringify(v)); } catch (e) {} } };
const S = Object.assign({lens: 'value', show: 10, per: 'pos', page: 0, sortKey: null, desc: true, cols: {}, f: {}}, store.get('state', {}));
S.page = 0;
const v = (row, key) => row[COL[key].i];
function fmt(c, x) {
  if (x == null || x === '') return '';
  switch (c.fmt) {
    case 'money': return '$' + Number(x).toLocaleString();
    case 'frac': return (x * 100).toFixed(x < 0.1 ? 1 : 0) + '%';
    case 'pct': return Number(x).toFixed(0) + '%';
    case 'num': return Math.abs(x) >= 100 ? Number(x).toFixed(0) : Math.abs(x) >= 10 ? Number(x).toFixed(1) : Number(x).toFixed(2).replace(/0$/, '');
    default: return String(x);
  }
}
function lens() { return D.lenses.find((l) => l.key === S.lens) || D.lenses[0]; }
function lensCols() {
  const l = lens();
  if (S.cols[l.key]) return S.cols[l.key].filter((k) => COL[k]);
  if (l.key === 'all') return D.columns.map((c) => c.key);
  return l.columns.filter((k) => COL[k]);  // a column the data does not carry is left out, never a crash
}

/* ---------- controls ---------- */
const root = $('#explorer');
const lensBar = $('#ex-lenses', root), note = $('#ex-note', root);
for (const l of D.lenses) {
  const b = el('button', 'lens', l.label); b.dataset.lens = l.key; b.type = 'button';
  b.addEventListener('click', () => { S.lens = l.key; S.sortKey = null; S.page = 0; render(); });
  lensBar.append(b);
}
const F = {pos: $('#ex-pos'), team: $('#ex-team'), game: $('#ex-game'), kick: $('#ex-kick'), q: $('#ex-q'), pool: $('#ex-pool'), role: $('#ex-role'), sig: $('#ex-sig'), smin: $('#ex-smin'), smax: $('#ex-smax'), name: $('#ex-name')};
function opts(sel, values) { for (const x of values) sel.append(new Option(x, x)); }
const uniq = (key) => [...new Set(D.rows.map((r) => v(r, key)).filter(Boolean))].sort();
/* a game's label in DraftKings' spelling: GameID is nflverse's (the Rams are LA there, LAR on every row) */
const gameLabel = (g) => String(g || '').split('_').slice(2).map((t) => (D.team_alias || {})[t] || t).join(' @ ');
opts(F.pos, D.positions); opts(F.team, uniq('Team')); opts(F.game, [...new Set(uniq('GameID').map(gameLabel))]);
opts(F.kick, uniq('Kickoff')); opts(F.role, D.roles); opts(F.sig, D.signals);
for (const [k, inp] of Object.entries(F)) {
  if (S.f[k] != null) inp.value = S.f[k];
  inp.addEventListener(inp.tagName === 'INPUT' ? 'input' : 'change', () => { S.f[k] = inp.value; S.page = 0; render(); });
}
$('#ex-reset').addEventListener('click', () => { for (const [k, inp] of Object.entries(F)) inp.value = ''; S.f = {}; S.cols[S.lens] = null; S.sortKey = null; render(); });
for (const b of root.querySelectorAll('[data-show]')) b.addEventListener('click', () => { S.show = b.dataset.show === 'all' ? 'all' : Number(b.dataset.show); S.page = 0; render(); });
for (const b of root.querySelectorAll('[data-per]')) b.addEventListener('click', () => { S.per = b.dataset.per; S.page = 0; render(); });
$('#ex-prev').addEventListener('click', () => { if (S.page > 0) { S.page--; render(); } });
$('#ex-next').addEventListener('click', () => { S.page++; render(); });

/* ---------- the column picker ---------- */
const picker = $('#ex-picker');
$('#ex-cols').addEventListener('click', () => { picker.hidden = !picker.hidden; if (!picker.hidden) drawPicker(); });
function drawPicker() {
  picker.replaceChildren();
  const cur = new Set(lensCols());
  for (const g of D.groups) {
    const d = el('details'); d.open = g.columns.some((k) => cur.has(k));
    d.append(el('summary', null, g.name + ' (' + g.columns.length + ')'));
    for (const k of g.columns) {
      const lab = el('label', 'pick'); const cb = el('input'); cb.type = 'checkbox'; cb.checked = cur.has(k);
      cb.addEventListener('change', () => {
        const now = lensCols().slice(); const at = now.indexOf(k);
        if (cb.checked && at < 0) now.push(k); if (!cb.checked && at >= 0) now.splice(at, 1);
        S.cols[S.lens] = now; render(false);
      });
      lab.append(cb, document.createTextNode(' ' + COL[k].label)); d.append(lab);
    }
    picker.append(d);
  }
}

/* ---------- filtering and sorting ---------- */
function passes(r) {
  const f = S.f;
  if (f.pos && v(r, 'Position') !== f.pos) return false;
  if (f.team && v(r, 'Team') !== f.team) return false;
  if (f.game && gameLabel(v(r, 'GameID')) !== f.game) return false;
  if (f.kick && v(r, 'Kickoff') !== f.kick) return false;
  const st = v(r, 'Avail');
  if (f.q === 'healthy' && st) return false;
  if (f.q === 'q' && !(st === 'Q' || st === 'D')) return false;
  const pool = v(r, 'Pool');
  if (f.pool === 'none' && pool) return false;
  if (f.pool === 'any' && !pool) return false;
  if (['Cash', 'GPP', 'Both'].includes(f.pool) && pool !== f.pool) return false;
  if (f.role && !v(r, 'Roles').split(', ').includes(f.role)) return false;
  if (f.sig && !v(r, 'Edge').includes(f.sig)) return false;
  const sal = v(r, 'Salary');
  if (f.smin && sal < Number(f.smin)) return false;
  if (f.smax && sal > Number(f.smax)) return false;
  if (f.name && !v(r, 'Name').toLowerCase().includes(f.name.toLowerCase())) return false;
  if (lens().signal_only && !v(r, 'Edge') && v(r, 'PropsGap') == null) return false;
  return true;
}
function sorted(rows) {
  const key = S.sortKey || lens().sort, desc = S.sortKey ? S.desc : lens().desc, c = COL[key];
  const sign = desc ? -1 : 1;
  return rows.slice().sort((a, b) => {
    const x = v(a, key), y = v(b, key);
    if (x == null || x === '') return 1; if (y == null || y === '') return -1;
    if (c.fmt === 'text') return sign * String(x).localeCompare(String(y));
    return sign * (x - y);
  });
}
function visible() {
  let rows = sorted(D.rows.filter(passes));
  if (S.show === 'all') return rows;
  if (S.per === 'pos') {
    const out = [];
    for (const p of D.positions) out.push(...rows.filter((r) => v(r, 'Position') === p).slice(0, S.show));
    return out;
  }
  return rows.slice(0, S.show);
}

/* ---------- the table ---------- */
function chip(t) { const s = el('a', 'tchip ' + t.toLowerCase(), t[0]); s.href = '#numbers'; s.addEventListener('click', (e) => e.stopPropagation()); return s; }
function poolSeg(id, name, value) {
  if (window.dfsPool && !window.dfsPool.saving(id)) value = window.dfsPool.get(id) || value || '';
  const s = el('span', 'seg'); s.dataset.id = id; s.dataset.name = name; s.dataset.was = value || '';
  for (const [val, lab] of [['Cash', 'Cash'], ['GPP', 'GPP'], ['Both', 'Both'], ['', 'none']]) {
    const b = el('button', (value || '') === val ? 'on' : '', lab); b.type = 'button'; b.dataset.v = val;
    if (!LIVE) { b.disabled = true; b.title = 'Read only here: the served page (dfs week serve) writes the sheet\'s Pool cell'; }
    s.append(b);
  }
  return s;
}
window.dfsPoolSeg = poolSeg;
function render(redrawPicker = true) {
  store.set('state', {lens: S.lens, show: S.show, per: S.per, sortKey: S.sortKey, desc: S.desc, cols: S.cols, f: S.f});
  for (const b of lensBar.children) b.classList.toggle('on', b.dataset.lens === S.lens);
  for (const b of root.querySelectorAll('[data-show]')) b.classList.toggle('on', String(S.show) === b.dataset.show);
  for (const b of root.querySelectorAll('[data-per]')) b.classList.toggle('on', S.per === b.dataset.per);
  note.textContent = lens().note;
  const cols = lensCols(); const all = visible();
  drawChart();
  const pages = Math.max(1, Math.ceil(all.length / PAGE)); if (S.page >= pages) S.page = pages - 1;
  const rows = all.slice(S.page * PAGE, S.page * PAGE + PAGE);
  $('#ex-count').textContent = all.length + ' players' + (pages > 1 ? ' · page ' + (S.page + 1) + ' of ' + pages : '');
  $('#ex-prev').disabled = S.page === 0; $('#ex-next').disabled = S.page >= pages - 1;
  const table = $('#ex-table'); table.replaceChildren();
  const thead = el('thead'); const hr = el('tr');
  if (lens().key === 'all') {
    const gr = el('tr', 'groups');
    let last = null, th = null;
    for (const k of cols) { const g = COL[k].group; if (g !== last) { th = el('th', 'grp', g); th.colSpan = 0; gr.append(th); last = g; } th.colSpan++; }
    thead.append(gr);
  }
  const key = S.sortKey || lens().sort, desc = S.sortKey ? S.desc : lens().desc;
  for (const k of cols) {
    const c = COL[k]; const th = el('th'); th.dataset.key = k; th.title = (c.tip ? c.tip + '\n' : '') + c.trust + ': ' + c.why;
    th.append(document.createTextNode(c.label + ' '), chip(c.trust));
    if (k === key) th.append(document.createTextNode(desc ? ' ▼' : ' ▲'));
    th.addEventListener('click', () => { if (S.sortKey === k) S.desc = !S.desc; else { S.sortKey = k; S.desc = c.fmt !== 'text'; } render(false); });
    hr.append(th);
  }
  thead.append(hr); table.append(thead);
  const tb = el('tbody'); let pos = null;
  for (const r of rows) {
    const id = D.ids[r.__i];
    if (S.per === 'pos' && S.show !== 'all' && v(r, 'Position') !== pos) { pos = v(r, 'Position'); const tr = el('tr', 'pos'); const td = el('td', null, pos); td.colSpan = cols.length; tr.append(td); tb.append(tr); }
    const tr = el('tr'); tr.dataset.id = id; tr.dataset.pos = v(r, 'Position') || '';
    for (const k of cols) {
      const c = COL[k]; const td = el('td'); const x = v(r, k);
      if (k === 'Name') { if (window.dfsSel) td.append(window.dfsSel.box(id)); const a = el('a', 'pl', x); a.dataset.id = id; a.href = '#'; td.append(a); const st = v(r, 'Avail'); if (st) { const q = el('span', 'chip q', st); const pr = (D.practice || {})[id]; if (pr && (st === 'Q' || st === 'D')) { q.title = 'Practice: ' + pr; td.append(q, el('span', 'prac', pr)); } else td.append(q); } for (const role of v(r, 'Roles').split(', ').filter(Boolean)) td.append(el('span', 'chip ' + role.split(' ')[0].toLowerCase(), role)); }
      else if (k === 'Pool') td.append(poolSeg(id, v(r, 'Name'), x));
      else if ((k === 'Team' || k === 'Opp') && x) td.append(gameLink(r, x));
      else { td.textContent = fmt(c, x); if (c.fmt !== 'text') td.className = 'n'; }
      tr.append(td);
    }
    tb.append(tr);
  }
  table.append(tb);
  if (window.dfsPool) for (const r of rows) window.dfsPool.paint(D.ids[r.__i]);
  if (window.dfsShade) window.dfsShade.table(table);   // within each position (week_shade)
  if (redrawPicker && !picker.hidden) drawPicker();
}
/* ---------- the lens chart (Plan O item 1): every lens has one, drawn from the rows the filters pass ---------- */
const chartBox = $('#ex-chart'), chartCap = $('#ex-chart-cap');
function num(r, key) { const c = COL[key]; if (!c) return null; const x = v(r, key); if (x == null || x === '' || isNaN(x)) return null; return c.fmt === 'frac' ? Number(x) * 100 : Number(x); }
const axisFmt = (key) => { const c = COL[key]; return !c ? 'num' : c.fmt === 'money' ? 'money' : (c.fmt === 'frac' || c.fmt === 'pct') ? 'pct' : 'num'; };
function lensSpec() {
  const lc = lens().lens_chart; if (!lc) return null;
  const rows = D.rows.filter(passes);
  const pts = [];
  for (const r of rows) {
    let x = num(r, lc.x); if (x == null && lc.alt_x) x = num(r, lc.alt_x);
    const y = num(r, lc.y); if (x == null || y == null) continue;
    const pr = num(r, lc.prio);
    const name = v(r, 'Name'), parts = String(name).split(' ');
    pts.push({id: D.ids[r.__i], name, short: v(r, 'Position') === 'DST' ? name : (parts.length > 1 ? parts.slice(1).filter((w) => !/^(Jr\.?|Sr\.?|II|III|IV|V)$/.test(w)).join(' ') || name : name),
      pos: v(r, 'Position'), team: v(r, 'Team'), x, y, sal: v(r, 'Salary'), proj: num(r, 'CalPts'), du: v(r, 'CashOwn'), gpp: v(r, 'GppOwn'),
      prio: pr == null ? -1e9 : (lc.guide === 'zero' ? Math.abs(pr) : pr)});
  }
  if (pts.length < 2) return {title: lc.title, empty: true, lc};
  const xs = pts.map((p) => p.x), ys = pts.map((p) => p.y);
  let [x0, x1, y0, y1] = [Math.min(...xs), Math.max(...xs), Math.min(...ys), Math.max(...ys)];
  const px = (x1 - x0) * 0.06 || 1, py = (y1 - y0) * 0.08 || 1;
  x0 -= px; x1 += px; y0 -= py; y1 += py;
  if (lc.guide === 'diag') { x0 = y0 = Math.min(x0, y0); x1 = y1 = Math.max(x1, y1); }
  const spec = {title: lc.title, x: (COL[lc.x] || {}).label || lc.x, y: (COL[lc.y] || {}).label || lc.y, xfmt: axisFmt(lc.x), yfmt: axisFmt(lc.y),
    range: [x0, x1, y0, y1], points: pts, how: lc.formula + ' ' + D.chart_source, lc};
  if (lc.guide === 'per_k') { const end = Math.min(x1, y1 / 3 * 1000); spec.ref = [[x0, x0 * 3 / 1000], [end, end * 3 / 1000], '3 pts per $1K']; }
  if (lc.guide === 'diag') spec.ref = [[x0, x0], [x1, x1], 'same'];
  if (lc.guide === 'zero') spec.ref = [[x0, 0], [x1, 0], 'CalPts = props'];
  if (lc.shade) { const c = ys.slice().sort((a, b) => a - b); spec.shade = [Math.max(x0, 0), 10, c[Math.floor(c.length * 0.75)], y1, 'leverage: <10% owned, top-quarter ceiling']; }
  return spec;
}
function drawChart() {
  if (!chartBox) return;
  const spec = lensSpec(); if (!spec) { chartBox.hidden = true; return; }
  chartBox.hidden = false; chartBox.dataset.lens = S.lens;
  const n = spec.empty ? 0 : spec.points.length;
  const src = [spec.lc.x, spec.lc.alt_x, spec.lc.y].filter(Boolean).map((k) => (COL[k] || {}).label || k);
  chartCap.textContent = spec.lc.formula + ' ' + n + ' players (the filters above apply). From: ' + src.join(', ') + '. ' + D.asof;
  if (spec.empty) { window.dfsCharts && delete window.dfsCharts.explorer; chartBox.querySelector('.ex-plot').innerHTML = '<p class="dim">No player passes the filters with both numbers.</p>'; return; }
  if (window.dfsCharts) window.dfsCharts.explorer = spec;
  const w = Math.max(320, Math.min(640, chartBox.clientWidth || 560));
  chartBox.querySelector('.ex-plot').innerHTML = window.dfsScatter ? window.dfsScatter(spec, w, 340, 8) : '';
  chartBox.querySelector('h4').textContent = spec.title;
}
window.dfsExplorerChart = lensSpec;
function gameLink(r, team) { const a = el('a', 'tm', team); a.href = '#'; a.dataset.team = team; a.dataset.game = v(r, 'GameID') || ''; a.title = 'Open the game'; return a; }
window.dfsExplorer = { setPool(id, value) { const i = IDX[id]; if (i != null) D.rows[i][COL.Pool.i] = value; }, render: () => render(false) };

/* ---------- the drawer: click any name, anywhere ---------- */
const drawer = $('#drawer');
function kv(list, keys) {
  const t = el('table', 'kv');
  for (const k of keys) {
    const c = COL[k]; if (!c) continue; const x = v(list, k); if (x == null || x === '') continue;
    const tr = el('tr'); const th = el('th', null, c.label + ' '); th.append(chip(c.trust)); th.title = c.trust + ': ' + c.why;
    tr.append(th, el('td', null, fmt(c, x))); t.append(tr);
  }
  return t;
}
function rankIn(l, r) {
  if (!l.sort || l.key === 'all') return null;
  const pos = v(r, 'Position'); const c = COL[l.sort];
  const peers = D.rows.filter((x) => v(x, 'Position') === pos && v(x, l.sort) != null && v(x, l.sort) !== '');
  if (v(r, l.sort) == null || v(r, l.sort) === '') return null;
  const better = peers.filter((x) => (l.desc ? v(x, l.sort) > v(r, l.sort) : v(x, l.sort) < v(r, l.sort))).length;
  return [better + 1, peers.length, c.label];
}
function openDrawer(id) {
  const i = IDX[id]; if (i == null) return;
  const r = D.rows[i]; drawer.replaceChildren(); drawer.hidden = false;
  const head = el('div', 'dhead');
  const close = el('button', 'dclose', '×'); close.type = 'button'; close.title = 'Close'; close.addEventListener('click', () => { drawer.hidden = true; });
  head.append(el('h3', null, v(r, 'Name')));
  if (LIVE) {  // served page: pre-fill the docked chat
    const ask = el('button', 'ask', 'Ask Claude'); ask.type = 'button';
    ask.dataset.say = ASK_PLAYER.replace('{name}', v(r, 'Name')); head.append(ask);
  }
  if (window.dfsCompare) head.append(window.dfsCompare.button(id));
  head.append(close);
  drawer.append(head);
  const sub = el('p', 'dim');
  sub.append(document.createTextNode(v(r, 'Position') + ' · '), gameLink(r, v(r, 'Team')), document.createTextNode(' vs '), gameLink(r, v(r, 'Opp')));
  const game = [COL.Spread && v(r, 'Spread') != null && v(r, 'Spread') !== '' ? 'spread ' + (v(r, 'Spread') > 0 ? '+' : '') + fmt(COL.Spread, v(r, 'Spread')) : '',
    COL.OverUnder && v(r, 'OverUnder') ? 'total ' + fmt(COL.OverUnder, v(r, 'OverUnder')) : '',
    v(r, 'Implied') != null && v(r, 'Implied') !== '' ? 'implied ' + fmt(COL.Implied, v(r, 'Implied')) : ''];
  const rest = [...game, fmt(COL.Salary, v(r, 'Salary')), v(r, 'Kickoff'), v(r, 'Avail')].filter(Boolean).join(' · ');
  sub.append(document.createTextNode(' · ' + rest)); sub.className = 'dim dgame'; drawer.append(sub);
  const prac = (D.practice || {})[id]; if (prac) drawer.append(el('p', 'prac', 'Practice this week: ' + prac));
  if (window.dfsNews && window.dfsNews.get(id).length) { const p = el('p', 'newsline'); p.title = window.dfsNews.note; p.append(window.dfsNews.chips(id, true)); drawer.append(p); }
  const tags = el('p'); for (const role of v(r, 'Roles').split(', ').filter(Boolean)) tags.append(el('span', 'chip ' + role.split(' ')[0].toLowerCase(), role));
  tags.append(document.createTextNode(' Pool: ')); tags.append(poolSeg(id, v(r, 'Name'), v(r, 'Pool'))); drawer.append(tags);
  const sec = (title, node) => { const d = el('section', 'dsec'); d.append(el('h4', null, title), node); drawer.append(d); };
  sec('Projections', kv(r, ['CalPts', 'ProjPts', 'SleeperPts', 'FantasyProsPts', 'EspnPts', 'PropPts', 'SrcSpread', 'Per $1K', 'Floor', 'CeilM', 'Hit3x%', 'Boom%', 'Bust%']));
  sec('Ownership', kv(r, ['CashOwn', 'GppOwn', 'OurOwn', 'Own%', 'Leverage', 'Flags']));
  const log = D.log[id] || [];
  if (log.length) {
    const t = el('table', 'log'); const h = el('tr'); for (const x of ['Wk', 'Opp', 'Snaps', 'Tgt', 'Car', 'DK']) h.append(el('th', null, x)); t.append(h);
    for (const g of log) { const tr = el('tr'); for (const x of [g.Wk, g.Opp, g.Snap == null ? '' : Math.round(g.Snap * 100) + '%', g.Tgt, g.Car, g.DK]) tr.append(el('td', null, x == null ? '' : String(x))); t.append(tr); }
    sec('Last five games', t);
  }
  sec('Usage (last five)', kv(r, ['Snap% L5', 'Tgt L5', 'Car L5', 'DK L5', 'Snap%', 'Tgt%', 'WOPR', 'TPRR~', 'xFP/G', 'RZ/G', 'HVT/G']));
  sec('Matchup', kv(r, ['OppPosRank', 'OppEPA', 'OverUnder', 'Spread', 'Implied', 'Pace', 'PROE', 'Wind', 'Roof']));
  sec('Props', kv(r, ['PropPts', 'PropsGap']));
  const sig = el('div'); const edge = v(r, 'Edge');
  const tokens = D.signals.filter((t) => edge.includes(t));
  if (!tokens.length) sig.append(el('p', 'dim', 'No signal chip.'));
  for (const t of tokens) sig.append(el('p', null, D.records[t] || t));
  sec('Signals', sig);
  const ranks = el('ul');
  for (const l of D.lenses) { const x = rankIn(l, r); if (x) ranks.append(el('li', null, l.label + ': #' + x[0] + ' of ' + x[1] + ' ' + v(r, 'Position') + 's by ' + x[2])); }
  sec('His rank in each lens', ranks);
  const bl = D.builds[v(r, 'Name')] || [];
  sec('Suggested builds that hold him', el('p', bl.length ? null : 'dim', bl.length ? bl.join(', ') : 'None this round.'));
}
document.addEventListener('click', (e) => {
  const a = e.target.closest('.pl[data-id], circle[data-id], text.lbl[data-id]');
  if (!a) return; e.preventDefault(); openDrawer(a.dataset.id);
});
document.addEventListener('keydown', (e) => { if (e.key === 'Escape') drawer.hidden = true; });
window.dfsDrawer = openDrawer;
window.dfsData = {D, COL, IDX, fmt, v};   // week_views: search, compare and the usage view read every player here
// a trust chip links to #numbers: open the collapsed section it points at
function openHash() { const t = location.hash.length > 1 && document.getElementById(location.hash.slice(1)); const d = t && t.querySelector('details'); if (d) d.open = true; }
window.addEventListener('hashchange', openHash); openHash();
render();
})();
"""
