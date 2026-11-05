"""The HTML of Week page v2's top half (the mockup's layout, in its order): the progress strip, the brief with
the next step, the pool panel, the decisions, the board beside the value / leverage maps and the slate's game
cards, then the Explorer. The answers are `week_board`'s; the charts `week_charts`'; the Explorer's data and
script `week_explorer`'s.

`live` is the served page (`dfs week serve`): its controls are buttons that write the sheet (`week_writes`,
through the server). The static page (`dfs week page`) shows the same answers read-only, with the phrase to say
in place of a button and no command text.
"""

from __future__ import annotations

import html
import json
import re

from dfs import week_board as wb
from dfs import week_charts, week_explorer, week_plays
from dfs.week_inputs import WeekInputs
from dfs.week_plays import POSITIONS, num

TIP = {
    "CalPts": week_explorer.TIPS["CalPts"],
    "Per $1K": week_explorer.TIPS["Per $1K"],
    "DU own": week_explorer.TIPS["CashOwn"],
    "GPP own": week_explorer.TIPS["GppOwn"],
    "Hit 3x": week_explorer.TIPS["Hit3x%"],
    "Boom": week_explorer.TIPS["Boom%"],
    "leverage": "Leverage: upside the field is not on (ceiling or Boom% rank against ownership rank).",
}
ROLE_CLASS = {wb.VALUE: "value", wb.CASH: "cash", wb.UPSIDE: "upside", wb.FADE_ROLE: "fade"}
ROLE_TIP = {
    wb.VALUE: "Value: top CalPts per $1K at his position.",
    wb.CASH: "Cash core: CalPts and value at least the position's median, best Floor first.",
    wb.UPSIDE: "Upside: best Boom% (once GPP ownership is out, the top half of Boom% by leverage).",
    wb.FADE_ROLE: "Fade: the FADE↓ signal (a tight end scoring above his usage), proven in Model Check.",
}


def e(text: object) -> str:
    return html.escape(str(text), quote=True)


def tip(word: str, label: str | None = None) -> str:
    """A word with its plain-words tooltip (dotted underline)."""
    return f'<abbr title="{e(TIP[word])}">{e(label or word)}</abbr>'


def pl(row: dict) -> str:
    """A player's name that opens the drawer."""
    return f'<a class="pl" href="#" data-id="{e(row.get("Id", ""))}">{e(row.get("Name", ""))}</a>'


def say(phrase: str) -> str:
    return f'<code class="say">&quot;{e(phrase)}&quot;</code>'


def pct(value: float | None) -> str:
    return "-" if value is None else f"{value * 100:.0f}%"


# -- the pool control ----------------------------------------------------------------------------------------


def pool_seg(row_id: str, name: str, value: str, live: bool) -> str:
    """Cash / GPP / Both / none, like the sheet's dropdown. Live: it writes the same Pool cell. Static: it shows
    the value and is read-only."""
    value = value or ""
    dis = (
        "" if live else ' disabled title="Read only here: the served page (dfs week serve) writes the sheet"'
    )
    buttons = "".join(
        f'<button type="button" data-v="{v}"{" class=on" if value == v else ""}{dis}>{lab}</button>'
        for v, lab in (("Cash", "Cash"), ("GPP", "GPP"), ("Both", "Both"), ("", "none"))
    )
    return f'<span class="seg" data-id="{e(row_id)}" data-name="{e(name)}" data-was="{e(value)}">{buttons}</span>'


# -- progress and the next step ------------------------------------------------------------------------------


def progress_html(steps: list[wb.Step]) -> str:
    cells = "".join(
        f'<div class="step {s.state}"><b>{e(s.name)}</b><span>{e(s.status)}</span></div>' for s in steps
    )
    return f'<section class="prog" id="progress" aria-label="The week so far">{cells}</section>'


def next_html(step: wb.Step | None, live: bool) -> str:
    if step is None:
        return '<p class="ok">Every step of the week is done.</p>'
    if not live:
        return f'<p class="nextline"><b>Next: {e(step.name.lower())}.</b> Say {say(step.say)}.</p>'
    if step.name == "Pool":
        return (
            '<p><button type="button" class="next-btn" data-action="start-pool">Next: add the suggested starting '
            "pool</button></p>"
        )
    form = ' data-form="1"' if step.name == "Close" else ""
    return (
        f'<p><button type="button" class="runbtn next-btn" data-command="{e(step.command)}"{form}>'
        f"Next: {e(step.say)}</button></p>"
    )


# -- the brief -----------------------------------------------------------------------------------------------


def sentence_html(s: wb.Sentence) -> str:
    rows = {str(r.get("Name", "")): r for r in s.names}

    def link(m: re.Match) -> str:
        name = m.group(1)
        return pl(rows[name]) if name in rows else e(name)

    parts = re.split(r"(\{[^{}]+\})", s.text)
    return "".join(link(re.match(r"\{(.+)\}", p)) if re.fullmatch(r"\{[^{}]+\}", p) else e(p) for p in parts)


def brief_html(sentences: list[wb.Sentence], step: wb.Step | None, live: bool, day_word: str) -> str:
    items = "".join(f"<li>{sentence_html(s)}</li>" for s in sentences)
    return (
        f'<section class="card brief" id="brief"><div class="card-head"><h2>The brief</h2>'
        f'<span class="asof">{e(day_word)}</span></div><ul>{items}</ul>{next_html(step, live)}</section>'
    )


# -- the pool panel ------------------------------------------------------------------------------------------


def pool_panel_html(panel: wb.PoolPanel, live: bool, source: str) -> str:
    head = "".join(f"<th>{p}</th>" for p in POSITIONS)
    body = "".join(
        f"<tr><td>{kind}</td>" + "".join(f"<td>{panel.counts[kind][p]}</td>" for p in POSITIONS) + "</tr>"
        for kind in ("Cash", "GPP")
    )
    parts = [
        f'<section class="card pool" id="pool"><div class="card-head"><h2>Your pool</h2>'
        f'<span class="asof">{e(source)}</span></div>',
        f"<p><b>{panel.total} player{'s' if panel.total != 1 else ''}.</b>"
        + (" Nothing picked yet." if not panel.total else "")
        + "</p>",
        f'<table class="counts"><tr><th></th>{head}</tr>{body}</table>',
    ]
    if panel.gaps:
        parts.append('<p class="warn"><b>Gaps:</b> ' + e("; ".join(panel.gaps)) + "</p>")
    if panel.out:
        parts.append(
            '<p class="bad"><b>OUT in your pool:</b> ' + ", ".join(pl(r) for r in panel.out) + "</p>"
        )
    if panel.questionable:
        parts.append(
            "<p><b>Questionable in your pool:</b> "
            + ", ".join(
                f"{pl(r)} ({e(week_plays.STATUS_WORD.get(wb.avail(r), ''))})" for r in panel.questionable
            )
            + "</p>"
        )
    if panel.likes:
        parts.append(
            "<p><b>The data likes, not in your pool:</b> "
            + ", ".join(f"{pl(r.row)} <span class=dim>({e(', '.join(r.roles))})</span>" for r in panel.likes)
            + "</p>"
        )
    if live and panel.start:
        names = ", ".join(f"{r.get('Name', '')} ({v})" for r, v in panel.start)
        changes = [{"id": str(r.get("Id", "")), "value": v, "was": ""} for r, v in panel.start]
        parts.append(
            f'<p><button type="button" id="start-pool" data-names="{e(names)}" '
            f"data-changes='{e(json.dumps(changes))}'>Add the suggested starting pool ({len(panel.start)})"
            "</button></p>"
            '<p class="note">Value and Cash core players as Cash, Upside players as GPP, both as Both; nobody '
            "already in your pool is changed. It asks first.</p>"
        )
    elif not live:
        parts.append(
            '<p class="note">On the served page (<code>dfs week serve</code>) every board row has Cash / GPP / '
            "Both, and it writes the same cell as the sheet's dropdown. Here, say "
            + say("add X, Y to my pool")
            + ".</p>"
        )
    parts.append("</section>")
    return "".join(parts)


# -- the decisions -------------------------------------------------------------------------------------------


def decision_text(d: wb.Decision, now) -> str:
    r = d.row or {}
    if d.kind == "chalk":
        return (
            f"{pl(r)} will be in about <b>{pct(num(r.get('CashOwn')))}</b> of double-up lineups "
            f"({e(r.get('Position', ''))}, ${week_plays.salary(r):,.0f}, {num(r.get('CalPts')) or 0:.1f} pts). "
            "Play him or fade him on purpose."
        )
    if d.kind == "q":
        return (
            f"{pl(r)} is <b>Questionable</b> ({num(r.get('CalPts')) or 0:.1f} pts, {e(wb.news_due(r, now))})."
        )
    a, b = d.stacks
    return (
        f"GPP stack: <b>{e(a.get('qb', ''))}</b> + {e(a.get('mates', ''))} ({e(a.get('game', ''))}) or "
        f"<b>{e(b.get('qb', ''))}</b> + {e(b.get('mates', ''))} ({e(b.get('game', ''))})?"
    )


def decisions_html(
    ds: list[wb.Decision], live: bool, now, rules_read: bool, pool_by_id: dict[str, str] | None = None
) -> str:
    if not ds:
        return (
            '<section class="card dec" id="decisions"><div class="card-head"><h2>Decisions this week</h2></div>'
            '<p class="dim">Nothing to decide yet: no 30%+ double-up chalk, no Questionable star and no stacks '
            "saved.</p></section>"
        )
    items = []
    for d in ds:
        text = decision_text(d, now)
        if d.answer:
            undo = (
                f' <button type="button" class="undo" data-key="{e(d.key)}">Undo</button>'
                if live and d.undo
                else ""
            )
            controls = f'<span class="answer">{e(d.answer_text)}</span>{undo}'
        elif live:
            controls = "".join(
                f'<button type="button" class="opt" data-key="{e(d.key)}" data-opt="{e(o.key)}">{e(o.label)}</button>'
                for o in d.options
            )
        else:
            controls = (
                "".join(f'<button type="button" disabled>{e(o.label)}</button>' for o in d.options)
                + ' <span class="note">say '
                + " or ".join(say(x) for x in d.say.split(" / "))
                + "</span>"
            )
        pivots = ""
        if d.kind == "q" and d.pivots and not d.answer:
            rows = "".join(
                f"<li>{pl(p)} ${week_plays.salary(p):,.0f}, {num(p.get('CalPts')) or 0:.1f} pts"
                + (
                    f' <button type="button" class="pivot" data-key="{e(d.key)}" data-pivot="{e(p.get("Id", ""))}" '
                    f'data-was="{e((pool_by_id or {}).get(str(p.get("Id", "")), ""))}">'
                    "Add as Both</button>"
                    if live
                    else ""
                )
                + "</li>"
                for p in d.pivots
            )
            pivots = f'<ul class="pivots" data-key="{e(d.key)}"{" hidden" if live else ""}>{rows}</ul>'
        items.append(
            f'<li class="{"answered" if d.answer else ""}" data-key="{e(d.key)}"><div>{text}</div>'
            f'<div class="opts">{controls}</div>{pivots}</li>'
        )
    foot = (
        '<p class="note">Play writes <code>cash: Name</code> on Suggested\'s Always use line, Fade on Never use '
        "(cash builds only); a stack writes <code>more Name</code> on Max exposure (GPP builds only). The next "
        "<code>--iterate</code> reads them.</p>"
        if live
        else '<p class="note">Answers are read from the sheet on the served page.</p>'
    )
    if live and not rules_read:
        foot += '<p class="warn">The rules box could not be read, so answers are not shown.</p>'
    return (
        '<section class="card dec" id="decisions"><div class="card-head"><h2>Decisions this week</h2></div>'
        f"<ul>{''.join(items)}</ul>{foot}</section>"
    )


# -- the board -----------------------------------------------------------------------------------------------


def role_chips(r: wb.BoardRow) -> str:
    chips = "".join(
        f'<span class="chip {ROLE_CLASS[x]}" title="{e(ROLE_TIP[x])}">{e(x)}</span>' for x in r.roles
    )
    status = wb.avail(r.row)
    if status in ("Q", "D"):
        chips += f'<span class="chip q" title="{e(week_plays.STATUS_WORD[status])}">{e(status)}</span>'
    return chips


def own_td(value: float | None, pending: bool) -> str:
    if pending:
        return '<td class="n dim" title="ownership publishes ~Fri/Sat">-</td>'
    if value is None:
        return '<td class="n">-</td>'
    hot = " hot" if value >= wb.CHALK else ""
    return f'<td class="n{hot}">{pct(value)}</td>'


def board_html(b: wb.Board, pool_by_id: dict[str, str], live: bool) -> str:
    if b.empty:
        return (
            '<section class="card" id="board"><div class="card-head"><h2>The board</h2></div>'
            '<p class="notrun"><b>Not run yet.</b> Nothing is synced, so there is nothing on the board. Say '
            + say("sync")
            + ".</p></section>"
        )
    head = (
        "<tr><th>Player</th><th class=n>Salary</th><th class=n>" + tip("CalPts", "Proj") + "</th>"
        "<th class=n>"
        + tip("Per $1K")
        + "</th><th class=n>"
        + tip("DU own")
        + "</th><th class=n>"
        + tip("GPP own")
        + "</th><th>Why</th><th>Pool</th></tr>"
    )
    body = []
    for pos in POSITIONS:
        rows = b.by_pos.get(pos) or []
        if not rows:
            continue
        more = sum(1 for r in rows if not r.shown)
        body.append(f'<tbody class="posgrp" data-pos="{pos}"><tr class="ph"><td colspan="8">{pos}</td></tr>')
        for r in rows:
            why = e(r.why) + (f'<br><span class="record">{e(r.record)}</span>' if r.record else "")
            cls = " ".join(c for c in ("fade-row" if r.fade else "", "" if r.shown else "more") if c)
            body.append(
                f'<tr class="{cls}"{" hidden" if not r.shown else ""}><td class="nm">{pl(r.row)}'
                f'<div class="tags">{role_chips(r)}</div></td>'
                f"<td class=n>${week_plays.salary(r.row):,.0f}</td><td class=n>{r.calpts:.1f}</td>"
                f"<td class=n>{(wb.per_k(r.row) or 0):.2f}</td>"
                + own_td(num(r.row.get("CashOwn")), b.cash_pending)
                + own_td(num(r.row.get("GppOwn")), b.gpp_pending)
                + f'<td class="why">{why}</td><td>{pool_seg(r.id, r.name, pool_by_id.get(r.id, ""), live)}</td></tr>'
            )
        if more:
            body.append(
                f'<tr class="morerow"><td colspan="8"><button type="button" class="showmore" data-pos="{pos}">'
                f"Show {more} more {pos}</button></td></tr>"
            )
        body.append("</tbody>")
    pending = (
        f'<p class="flag">{e(week_plays.OWN_NOT_OUT[0].upper() + week_plays.OWN_NOT_OUT[1:])}: the ownership '
        "columns fill then.</p>"
        if b.cash_pending or b.gpp_pending
        else ""
    )
    return (
        '<section class="card" id="board"><div class="card-head"><h2>The board</h2></div>'
        '<p class="note">Each player once, with his roles: the top 3 per role and position (<i>Show more</i> for the '
        "next ones). A FADE↓ player is only a Fade, last in his position, with the signal's record. The Why line "
        "is what the columns do not say. Click a name for everything about him.</p>"
        + pending
        + f'<div class="scroll"><table class="board"><thead>{head}</thead>{"".join(body)}</table></div></section>'
    )


# -- the charts and the slate --------------------------------------------------------------------------------


def charts_html(edge: list[dict] | None, ownership_out: bool) -> str:
    value = week_charts.value_map(edge)
    lev = week_charts.leverage_map(edge) if ownership_out else ""
    parts = []
    if value:
        parts.append(
            '<section class="card" id="value-map"><div class="card-head"><h2>Value map</h2></div>'
            '<p class="note">RB, WR, TE. Dots above the dashed line beat 3 points per $1K; the best are named. '
            f"Hover a dot for the numbers, click it for the player.</p>{week_charts.LEGEND}{value}</section>"
        )
    if lev:
        parts.append(
            '<section class="card" id="leverage-map"><div class="card-head"><h2>Leverage map</h2></div>'
            f'<p class="note">GPP ownership against the ceiling. The shaded corner is {tip("leverage")}: '
            f"low owned with a top-quarter ceiling.</p>{week_charts.LEGEND}{lev}</section>"
        )
    elif value:
        parts.append(
            '<section class="card" id="leverage-map"><div class="card-head"><h2>Leverage map</h2></div>'
            '<p class="dim">Appears once GPP ownership publishes (~Fri/Sat).</p></section>'
        )
    return "".join(parts)


def stack_games(inp: WeekInputs) -> set[frozenset[str]]:
    """The games of the saved top stacks, as team pairs ("MIA v DET")."""
    out = set()
    for s in (inp.stacks or {}).get("rows") or []:
        teams = re.findall(r"[A-Z]{2,3}", str(s.get("game") or ""))
        if len(teams) >= 2:
            out.add(frozenset(teams[:2]))
    return out


def games_html(inp: WeekInputs, live: bool = False) -> str:
    games = week_plays.slate(inp.edge)
    if not games:
        return ""
    stacks = stack_games(inp)
    top_imp = max((v for g in games for v in g.implied.values()), default=30.0) or 30.0
    cards = []
    for g in games:
        title = f"{g.away} @ {g.home}" if g.away and g.home else g.key
        flags = []
        if frozenset((g.away, g.home)) in stacks:
            flags.append(
                '<span class="tag stack" title="One of the sim\'s top stacks is in this game">stack</span>'
            )
        if g.wind is not None and g.wind >= week_plays.WIND_MPH and g.roof != "dome":
            flags.append(f'<span class="tag wx">wind {g.wind:.0f} mph</span>')
        if g.moved:
            flags.append(f'<span class="tag moved">total {g.tot_move:+g} since open</span>')
        bars = "".join(
            f'<div class="bar"><span>{e(t)}</span><i style="width:{min(100.0, imp / top_imp * 100):.0f}%"></i>'
            f"<b>{imp:.1f}</b></div>"
            for t, imp in sorted(g.implied.items(), key=lambda x: -x[1])
        )
        total = f"{g.total:g}" if g.total is not None else "-"
        cards.append(
            f'<div class="game{" top" if g.top else ""}"><div class="gh"><b>{e(title)}</b><span>{e(total)}</span></div>'
            f'<div class="gk">{e(g.kickoff)}{(" · " + e(g.weather)) if g.weather else ""}</div>{bars}'
            f'<div class="gflags">{"".join(flags)}</div></div>'
        )
    opening = ""
    if all(g.tot_move is None for g in games):
        opening = (
            '<p class="note">No opening line saved yet, so no line movement'
            + ("" if live else ' (<code>dfs odds snapshot</code> saves one; "start week N" does it first)')
            + ".</p>"
        )
    return (
        '<section class="card" id="games"><div class="card-head"><h2>The slate</h2></div>'
        '<p class="note">Team implied totals, highest games first; <span class="tag stack">stack</span> marks a '
        f'game of the sim\'s top stacks.</p>{opening}<div class="games">{"".join(cards)}</div></section>'
    )


# -- the Explorer --------------------------------------------------------------------------------------------


def explorer_html(inp: WeekInputs, data: dict) -> str:
    if not data.get("rows"):
        return (
            '<section class="card" id="explorer-card"><div class="card-head"><h2>Explorer</h2></div>'
            '<p class="notrun"><b>Not run yet.</b> Nothing is synced.</p></section>'
        )
    charts = (
        f'<div class="ex-chart" data-chart="value" hidden>{week_charts.value_map(inp.edge, width=520, height=340)}</div>'
        f'<div class="ex-chart" data-chart="leverage" hidden>'
        f"{week_charts.leverage_map(inp.edge, width=520, height=340) or '<p class=dim>No GPP ownership yet.</p>'}</div>"
        f'<div class="ex-chart" data-chart="usage" hidden>{week_charts.usage_map(inp.edge, width=520, height=340)}</div>'
    )
    sel = lambda i, label, first="": (  # noqa: E731
        f'<label>{label}<select id="{i}"><option value="">{first or "any"}</option></select></label>'
    )
    return (
        '<section class="card" id="explorer-card" data-keep><div class="card-head"><h2>Explorer</h2>'
        '<span class="asof">every priced player, every EdgeRaw column</span></div>'
        '<div id="explorer"><div class="lenses" id="ex-lenses"></div><p class="note" id="ex-note"></p>'
        '<div class="ex-filters">'
        + sel("ex-pos", "Position")
        + '<label>Salary<span class="range"><input id="ex-smin" inputmode="numeric" placeholder="min" size="5">'
        '<input id="ex-smax" inputmode="numeric" placeholder="max" size="5"></span></label>'
        + sel("ex-team", "Team")
        + sel("ex-game", "Game")
        + sel("ex-kick", "Kickoff")
        + '<label>Status<select id="ex-q"><option value="">any</option><option value="healthy">healthy only</option>'
        '<option value="q">Questionable / Doubtful</option></select></label>'
        '<label>My pool<select id="ex-pool"><option value="">any</option><option value="any">in it</option>'
        '<option>Cash</option><option>GPP</option><option>Both</option><option value="none">not in it</option>'
        "</select></label>"
        + sel("ex-role", "Role chip")
        + sel("ex-sig", "Signal chip")
        + '<label>Name<input id="ex-name" placeholder="search" size="12"></label>'
        '<button type="button" id="ex-reset">Reset</button></div>'
        '<div class="ex-bar"><span>Show <button type="button" data-show="3">3</button><button type="button" '
        'data-show="10">10</button><button type="button" data-show="25">25</button><button type="button" '
        'data-show="all">all</button></span><span><button type="button" data-per="pos">per position</button>'
        '<button type="button" data-per="all">across positions</button></span><button type="button" id="ex-cols">'
        'Columns</button><span id="ex-count" class="dim"></span><span><button type="button" id="ex-prev">‹</button>'
        '<button type="button" id="ex-next">›</button></span></div>'
        '<div id="ex-picker" hidden></div>'
        + charts
        + '<p class="note">Each column header carries its trust: <span class="tchip proven">P</span> Proven, '
        '<span class="tchip shadow">S</span> Shadow, <span class="tchip untested">U</span> Untested, '
        '<span class="tchip data">D</span> Data (a feed, not a prediction). Hover for why.</p>'
        '<div class="scroll ex-scroll"><table id="ex-table" class="ex"></table></div></div></section>'
    )


DRAWER = '<div id="drawer" class="drawer" role="dialog" hidden aria-label="Player"></div>'


def explorer_script(data: dict, nonce_attr: str) -> str:
    """The Explorer's data and script: last in the page, after the drawer they fill."""
    if not data.get("rows"):
        return ""
    return (
        f'<script type="application/json" id="explorer-data">{week_explorer.payload_json(data)}</script>'
        f"<script{nonce_attr}>{week_explorer.JS}</script>"
    )
