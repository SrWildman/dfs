"""The Week page's design system (design pass, `docs/planning/audit/DESIGN_PASS_2026-11.md` section 4).

One set of tokens (colour, type, spacing) on `:root` with a dark mode, and the components every block is
drawn with: the header, the status line, the section card and fold, the table, the player row with its pool
square, the tag, the trust chip, the candidate card, the lineup card, the checklist and the More bar.

The older blocks (the board, the Explorer, the folds of the rest of the week) keep their own class names: the
old variable names (`--card`, `--dim`, `--proven`, ...) are aliases of the tokens here, so they draw in the
same palette and type scale without being rewritten. New blocks use the components below.

Type: 11 chip, 12 meta, 13 dense secondary, 14 body, 16 section title, 22 headline number. Weights 400 and
600 only. Spacing: 4 / 8 / 12 / 16 / 24 / 32. Colour: grey by default; the accent is for actions only (the one
primary button, the selected candidate, the day's "now" dot); good / warn / bad only for outliers.
"""

from __future__ import annotations

import html
from dataclasses import dataclass

# Every colour on the page is one of these tokens (a colour literal anywhere else in the page fails
# `tests/test_week_style.py`). `on-fill` is text on a filled accent / good / bad / bar: white on the light
# theme's dark fills, near-black on the dark theme's bright ones. `bar1`-`bar5` are the share bars' series,
# each readable under `on-fill` text in its theme. `color-scheme` makes the browser's own controls (the file
# input, search fields, scroll bars) follow the theme too: without it both engines drew them light on dark.
LIGHT = {
    "bg": "#f5f6f8", "surface": "#fff", "sunk": "#f0f2f5", "line": "#e3e6ea", "line-2": "#d3d8de",
    "ink": "#16191d", "ink-2": "#4d5560", "ink-3": "#636a75", "accent": "#1f56d6", "accent-soft": "#e9effc",
    "good": "#17784a", "good-soft": "#e7f4ec", "warn": "#9a5b00", "warn-soft": "#fdf3e2", "bad": "#b4312a",
    "bad-soft": "#fbeceb", "cash": "#1f56d6", "gpp": "#7a3fc4", "gpp-soft": "#f1eafa",
    "wr": "#1f56d6", "rb": "#17784a", "te": "#9a5b00", "s1": "#2a78d6", "s2": "#eb6834", "s3": "#1baf7a",
    "on-fill": "#fff", "hover": "rgba(20,25,30,.06)", "rest": "#c8ccd3",
    "bar1": "#2563eb", "bar2": "#15803d", "bar3": "#b45309", "bar4": "#7e22ce", "bar5": "#0e7490",
    "elev": "0 1px 2px rgba(20,25,30,.05)", "pop": "0 6px 20px rgba(0,0,0,.18)",
}  # fmt: skip
DARK = {
    "bg": "#111316", "surface": "#191c20", "sunk": "#202429", "line": "#2a2f35", "line-2": "#363c44",
    "ink": "#e8eaed", "ink-2": "#aab1ba", "ink-3": "#8a929c", "accent": "#6d97ff", "accent-soft": "#1d2840",
    "good": "#4cc38a", "good-soft": "#15291f", "warn": "#e2a64a", "warn-soft": "#2c2414", "bad": "#ef7b72",
    "bad-soft": "#2e1a19", "cash": "#6d97ff", "gpp": "#b38cf0", "gpp-soft": "#261d36",
    "wr": "#6d97ff", "rb": "#4cc38a", "te": "#e2a64a", "s1": "#3987e5", "s2": "#d95926", "s3": "#199e70",
    "on-fill": "#111316", "hover": "rgba(255,255,255,.06)", "rest": "#3a4048",
    "bar1": "#6d97ff", "bar2": "#4cc38a", "bar3": "#e2a64a", "bar4": "#b38cf0", "bar5": "#38bdf8",
    "elev": "0 1px 2px rgba(0,0,0,.3)", "pop": "0 6px 20px rgba(0,0,0,.5)",
}  # fmt: skip
FONT = '-apple-system,BlinkMacSystemFont,"Segoe UI",Inter,Roboto,sans-serif'


def _decl(tokens: dict[str, str]) -> str:
    return ";".join(f"--{k}:{v}" for k, v in tokens.items())


TOKENS = (
    f"\n:root{{{_decl(LIGHT)};--font:{FONT};color-scheme:light}}\n"
    f"@media (prefers-color-scheme:dark){{:root:not([data-theme=light]){{{_decl(DARK)};color-scheme:dark}}}}\n"
    f":root[data-theme=dark]{{{_decl(DARK)};color-scheme:dark}}\n"
    """:root{--card:var(--surface);--dim:var(--ink-2);--proven:var(--good);--proven-soft:var(--good-soft);
--shadow:var(--warn);--shadow-soft:var(--warn-soft);--untested:var(--ink-3);--ok:var(--good);--flag:var(--warn-soft)}
"""
)

# The base elements, in the type scale: every older block inherits these.
BASE = """
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font:14px/1.45 var(--font);-webkit-font-smoothing:antialiased}
h1{font-size:22px;font-weight:600;margin:0}h2{font-size:16px;font-weight:600;margin:0}
h3{font-size:14px;font-weight:600;margin:0}h4{font-size:14px;font-weight:600;margin:14px 0 6px}
h5{font-size:12px;font-weight:600;margin:10px 0 4px;color:var(--ink-2)}
b,strong,th{font-weight:600}
.card{background:var(--surface);border:1px solid var(--line);border-radius:10px;padding:12px 16px;margin:0 0 16px;
box-shadow:var(--elev)}
table{font-size:13px;font-variant-numeric:tabular-nums}
th{font-size:12px;font-weight:500;color:var(--ink-3)}
th,td{padding:6px 8px 6px 0;border-bottom:1px solid var(--line)}
.chip{font-size:11px}.note,.cap,.legend,.asof,.last{font-size:12px}.fold .line{font-size:13px}
.fold>details>summary h3{font-size:16px}
button{font:inherit;font-size:13px;border:1px solid var(--line-2);background:var(--surface);color:var(--ink);
border-radius:6px;padding:3px 10px;cursor:pointer}
code{font-size:12px}
"""

# The shell (header, day tabs, status line, two columns, More) and the new components.
SHELL = """
.wk-top{position:sticky;top:0;z-index:20;background:var(--bg);display:flex;flex-wrap:wrap;align-items:center;
gap:8px 16px;padding:12px 0 8px;border-bottom:1px solid var(--line)}
.wk-top .wk{font-size:16px;font-weight:600;white-space:nowrap}
.wk-top .wk span{font-weight:400;color:var(--ink-3);font-size:14px;margin-left:6px}
.daytabs{display:flex;gap:2px;background:var(--sunk);border-radius:8px;padding:3px}
.daytabs a{padding:5px 12px;border-radius:6px;color:var(--ink-2);text-decoration:none;white-space:nowrap}
.daytabs a.on{background:var(--surface);color:var(--ink);font-weight:600;box-shadow:var(--elev)}
.daytabs a .now{display:inline-block;width:6px;height:6px;border-radius:50%;background:var(--accent);
margin-left:6px;vertical-align:middle}
.wk-tools{margin-left:auto;display:flex;gap:8px;align-items:center}
.wk-tools .vsearch{position:relative}
.wk-tools #psearch{width:220px;padding:5px 10px;border:1px solid var(--line-2);border-radius:8px;
background:var(--surface);color:var(--ink);font:inherit}
.wk-tools .askbtn{border-color:var(--line-2);border-radius:999px;padding:4px 12px}
.wk-meta{font-size:12px;color:var(--ink-3);margin:6px 0 0}
.wk-meta.warn{color:var(--warn)}
details.stale summary{list-style:none;cursor:pointer;display:flex;align-items:baseline;gap:6px;min-width:0;
white-space:nowrap}details.stale summary::-webkit-details-marker{display:none}
details.stale .what{overflow:hidden;text-overflow:ellipsis;flex:0 1 auto;min-width:0}
details.stale code.fix{font-size:11.5px;color:inherit;flex:0 1 auto;overflow:hidden;text-overflow:ellipsis}
details.stale .more{color:var(--ink-3);text-decoration:underline;flex:none}
details.stale ul{margin:4px 0 0;padding-left:18px;white-space:normal}details.stale li{margin:2px 0}
.sline{display:flex;margin:12px 0 16px;background:var(--surface);border:1px solid var(--line);border-radius:10px;
overflow-x:auto;scrollbar-width:none;padding:0}
.sline::-webkit-scrollbar{display:none}
.sfact{flex:1 0 auto;padding:8px 16px;border-right:1px solid var(--line);min-width:140px}
.sfact:last-child{border-right:0}
.sfact .l{font-size:12px;color:var(--ink-3)}.sfact .v{font-weight:600;white-space:nowrap}
.sfact .n{font-size:12px;color:var(--ink-3);white-space:nowrap}
.sfact.warn .v{color:var(--warn)}.sfact.ok .v{color:var(--good)}
.layout{display:grid;grid-template-columns:minmax(0,1fr) 340px;gap:16px;align-items:start}
.side{position:sticky;top:64px;display:flex;flex-direction:column;gap:0}
.side>.vw>section,.side>section{margin-bottom:16px}
@media(max-width:1100px){.layout{grid-template-columns:minmax(0,1fr)}.side{position:static}}
@media(max-width:700px){.sline{display:grid;grid-template-columns:1fr 1fr}.sfact{min-width:0;border-right:0;border-bottom:1px solid var(--line)}
.sfact:nth-child(odd){border-right:1px solid var(--line)}.sfact .v,.sfact .n{white-space:normal;overflow-wrap:anywhere}}
.sec{padding:0;overflow:hidden}
.sec-h{display:flex;align-items:baseline;gap:4px 12px;flex-wrap:wrap;padding:12px 16px 8px}
.sec-h h2{font-size:16px}.sec-h .sub{color:var(--ink-3);font-size:12px;margin:0}
.sec-h .end{margin-left:auto;display:flex;gap:8px;align-items:center}
.sec-b{padding:0 16px 12px}.sec-b.flush{padding:0 0 4px;overflow-x:auto}
.t td.acts .opts{flex-wrap:nowrap;justify-content:flex-end}
.sec-f{padding:8px 16px;border-top:1px solid var(--line);font-size:12px;color:var(--ink-3)}
details.dfold>summary{list-style:none;cursor:pointer;display:flex;align-items:baseline;gap:4px 12px;
flex-wrap:wrap;padding:12px 16px}
details.dfold>summary::-webkit-details-marker{display:none}
details.dfold>summary::before{content:"\\25B8";color:var(--ink-3);font-size:12px}
details.dfold[open]>summary::before{content:"\\25BE"}
details.dfold>summary h2{font-size:16px}details.dfold>summary .line{color:var(--ink-2);font-size:13px}
.tag{display:inline-block;font-size:11px;line-height:18px;padding:0 6px;border-radius:4px;background:var(--sunk);
color:var(--ink-2);white-space:nowrap;margin:0 2px}
.tag.q{background:var(--warn-soft);color:var(--warn)}.tag.out{background:var(--bad-soft);color:var(--bad)}
.posl{display:inline-block;min-width:28px;font-size:11px;font-weight:600;color:var(--ink-3)}
table.t{width:100%;border-collapse:collapse;font-size:14px}
.t th{font-weight:500;font-size:12px;color:var(--ink-3);text-align:left;padding:6px 8px;border-bottom:1px solid var(--line);
white-space:nowrap;background:var(--surface)}
.t td{padding:7px 8px;border-bottom:1px solid var(--line);vertical-align:middle}
.t tr:last-child td{border-bottom:0}
.t th.n,.t td.n{text-align:right;width:1%;white-space:nowrap;font-variant-numeric:tabular-nums}
.t td.who{min-width:180px}.t td.acts{width:1%;white-space:nowrap;text-align:right}
.t .meta,.meta{font-size:12px;color:var(--ink-3)}
.t tr.grp td{background:var(--sunk);font-size:12px;font-weight:600;color:var(--ink-2);padding:4px 8px}
.t td.hot{color:var(--bad);font-weight:600}
@media(max-width:700px){.t .hm{display:none}.t td.who{min-width:0}.t th .chip{display:none}
.t td.acts .opts{flex-direction:column;gap:4px}.sec-h .sub{flex-basis:100%}}
.pw{display:inline-flex;align-items:center;gap:6px;min-width:0}
.digest{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,300px),1fr));gap:0}
.digest>div{padding:0 16px 12px;min-width:0}
.digest h3{font-size:12px;font-weight:600;color:var(--ink-3);margin:4px 0 6px}
.dl{list-style:none;margin:0;padding:0}
.dl li{display:flex;gap:8px;align-items:baseline;padding:5px 0;border-top:1px solid var(--line);font-size:13px}
.dl li:first-child{border-top:0}.dl li .r{margin-left:auto;white-space:nowrap;font-variant-numeric:tabular-nums}
.dl li .r b{font-weight:600}
.best{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,210px),1fr));gap:4px 16px;padding:0 16px 12px}
.best .grp{font-size:12px;font-weight:600;color:var(--ink-3);margin:6px 0 2px}
.prow{display:flex;align-items:center;gap:6px;padding:4px 0;border-top:1px solid var(--line);min-width:0}
.prow .nm{min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.prow .r{margin-left:auto;white-space:nowrap;font-variant-numeric:tabular-nums;font-weight:600}
.prow .meta{display:block}
.steps{list-style:none;margin:0;padding:4px 0}
.steps li{display:grid;grid-template-columns:18px minmax(0,1fr) auto;gap:8px;align-items:center;padding:5px 16px}
.steps .i{width:14px;height:14px;border-radius:50%;border:1.5px solid var(--line-2)}
.steps .done .i{background:var(--good);border-color:var(--good)}
.steps .next .i{border-color:var(--accent);box-shadow:0 0 0 3px var(--accent-soft)}
.steps .next .w{font-weight:600}.steps .w small{display:block;color:var(--ink-3);font-size:12px;font-weight:400}
.steps .runbtn,.steps button{font-size:12px;padding:2px 9px}
.morebar{margin:24px 0 12px;display:flex;flex-wrap:wrap;gap:6px 12px;align-items:center}
.morebar h2{font-size:16px;margin-right:4px}
.morebar .grp{display:flex;gap:4px;flex-wrap:wrap}
.morebar a{border:1px solid var(--line);background:var(--surface);border-radius:999px;padding:3px 11px;
color:var(--ink-2);font-size:13px;text-decoration:none;white-space:nowrap}
.morebar a.on{background:var(--ink);border-color:var(--ink);color:var(--surface)}
.morebar .sep{width:1px;align-self:stretch;background:var(--line-2);margin:2px 2px}
.empty{color:var(--ink-2);margin:0;padding:0 16px 12px}
.btn-primary{background:var(--accent);border-color:var(--accent);color:var(--on-fill);font-weight:600}
.crow.chead{text-transform:none;font-size:12px;color:var(--ink-3)}
.pairflag{margin:6px 0 10px;padding:6px 10px;border-radius:8px;background:var(--warn-soft);color:var(--ink)}
.pairflag summary{cursor:pointer}
.pairswaps{list-style:none;margin:6px 0 0;padding:0}
.pairswaps li{display:grid;grid-template-columns:3em 1fr auto auto;gap:10px;align-items:baseline;
padding:2px 0}
.slotrow{grid-template-columns:40px minmax(0,1fr) auto 18px}
.sec-h .howtip{font-size:12px}
.dfold>.howtip{display:block;padding:0 16px 4px;font-size:12px}
/* the pool square: left of the name, empty until hover, C / G / B when pooled */
.pchip.psq{width:22px;height:22px;flex:none;border-radius:5px;border:1px dashed transparent;padding:0;margin:0 6px 0 0;
font-size:11px;font-weight:600;line-height:20px;text-align:center;color:var(--ink-3);background:none;vertical-align:middle}
.pchip.psq:not(.in){color:transparent}
:is(p,.prose,h1,h2,h3,h4,.meta,small) .pchip.psq{width:16px;height:16px;font-size:9px;margin-right:3px;vertical-align:-3px}
.pchip.psq:not(.in)::after{content:"+";color:var(--ink-3);font-size:14px;font-weight:400;opacity:0}
:is(tr,li,.prow,.crow,.who):hover>.pchip.psq:not(.in),:is(tr,li,.prow,.crow,.who):hover .pchip.psq:not(.in),
.pchip.psq:focus-visible{border-color:var(--line-2)}
:is(tr,li,.prow,.crow,.who):hover .pchip.psq:not(.in)::after,.pchip.psq:focus-visible::after{opacity:1}
@media (hover:none){.pchip.psq:not(.in)::after{opacity:.5}}
.pchip.psq.in{border-style:solid;border-color:transparent}
.pchip.psq.C{background:var(--accent-soft);color:var(--cash)}.pchip.psq.G{background:var(--gpp-soft);color:var(--gpp)}
.pchip.psq.B{background:var(--ink);color:var(--surface)}
/* someone else: a search over the pool for an open spot */
.other{margin-top:8px}.other>summary{color:var(--accent);font-size:13px;cursor:pointer;list-style:none}
.other>summary::-webkit-details-marker{display:none}
.other input{width:min(100%,280px);margin:8px 0 4px;padding:4px 8px;border:1px solid var(--line-2);border-radius:6px;
background:var(--surface);color:var(--ink);font:inherit}
.other .olist{max-height:260px;overflow:auto}
.other .orow{display:grid;grid-template-columns:minmax(0,1fr) auto auto auto auto;gap:4px 12px;align-items:center;
padding:5px 0;border-top:1px solid var(--line);font-size:13px}
.other .orow .d{text-align:right;white-space:nowrap;font-variant-numeric:tabular-nums}
.other .orow .d i{font-style:normal;color:var(--ink-3);font-size:11px;margin-right:3px}
.other .orow .g{color:var(--good)}.other .orow .rd{color:var(--bad)}
@media(max-width:700px){.other .orow{grid-template-columns:minmax(0,1fr) auto auto}.other .orow .own{display:none}}
"""

CSS = TOKENS + BASE + SHELL


def e(text: object) -> str:
    return html.escape(str(text), quote=True)


@dataclass(frozen=True)
class Fact:
    """One item of the status line: a label, a value and a short note; `tone` is "" (grey), "warn" or "ok"."""

    label: str
    value: str
    note: str = ""
    tone: str = ""


def status_line(sid: str, facts: list[Fact]) -> str:
    items = "".join(
        f'<div class="sfact{" " + f.tone if f.tone else ""}"><div class="l">{e(f.label)}</div>'
        f'<div class="v">{e(f.value)}</div>'
        + (f'<div class="n">{e(f.note)}</div>' if f.note else "")
        + "</div>"
        for f in facts
    )
    return f'<section class="sline" id="{e(sid)}" aria-label="Status">{items}</section>'


def _how(text: str) -> str:
    from dfs.week_page import how_html

    return f'<span class="howtip">{how_html(text)}</span>' if text else ""


def card(
    sid: str,
    title: str,
    body: str,
    *,
    sub: str = "",
    end: str = "",
    foot: str = "",
    cls: str = "",
    how: str = "",
) -> str:
    """A section: the header line (16 px title, a 12 px phrase saying what it is, a chip or a button on the
    right), the body, and an optional 12 px footer. `how` is the "how this is built" tip every table carries."""
    return (
        f'<section class="card sec{" " + cls if cls else ""}" id="{e(sid)}"><div class="sec-h"><h2>{e(title)}</h2>'
        + (f'<p class="sub">{sub}</p>' if sub else "")
        + _how(how)
        + (f'<span class="end">{end}</span>' if end else "")
        + f"</div>{body}"
        + (f'<div class="sec-f">{foot}</div>' if foot else "")
        + "</section>"
    )


def fold(
    sid: str, title: str, line: str, body: str, *, open_: bool = False, end: str = "", how: str = ""
) -> str:
    """A section closed to one line: the same header as a card, with a one-line summary of what is inside."""
    return (
        f'<section class="card sec" id="{e(sid)}"><details class="dfold"{" open" if open_ else ""}>'
        f'<summary><h2>{e(title)}</h2><span class="line">{e(line)}</span>'
        + (f'<span class="end">{end}</span>' if end else "")
        + f"</summary>{_how(how)}{body}</details></section>"
    )
