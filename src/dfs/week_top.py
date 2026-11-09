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
from dfs.week_page import add_button, caption, how_html
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
ROLE_CLASS = {
    wb.VALUE: "value",
    wb.CASH: "cash",
    wb.CHALK_CORE: "chalk",
    wb.UPSIDE: "upside",
    wb.FADE_ROLE: "fade",
}
ROLE_TIP = {
    wb.VALUE: "Value: top CalPts per $1K at his position.",
    wb.CASH: week_plays.CASH_CORE_RULE,
    wb.CHALK_CORE: "Chalk core: predicted in 30% or more of double-up lineups (CashOwn), so in the cash core "
    "whatever the caps; play him or fade him on purpose.",
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
        how_html(
            "Your pool as the sheet's EdgeRaw Pool column holds it (read live on the served page, else the "
            "last sync): Both counts in Cash and in GPP."
        )
        + f'<table class="counts"><tr><th></th>{head}</tr>{body}</table>',
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
            + ", ".join(
                f"{pl(r.row)} <span class=dim>({e(', '.join(r.roles))})</span> {add_button(r.id, wb.kind_of(r.roles))}"
                for r in panel.likes
            )
            + "</p>"
        )
    if live and panel.start:
        names = ", ".join(f"{r.get('Name', '')} ({v})" for r, v in panel.start)
        changes = [{"id": str(r.get("Id", "")), "value": v, "was": ""} for r, v in panel.start]
        parts.append(
            f'<p><button type="button" id="start-pool" data-names="{e(names)}" '
            f"data-changes='{e(json.dumps(changes))}'>Add the suggested starting pool ({len(panel.start)})"
            "</button></p>"
            '<p class="note">Value, Cash core and Chalk core players as Cash, Upside players as GPP, both as Both; '
            "nobody already in your pool is changed. It asks first.</p>"
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
        pooled = [r for r in ([d.row] if d.kind in ("chalk", "q") and d.row else [])]
        badge = "".join(
            f' <span class="poolval{" in" if (pool_by_id or {}).get(wb.pid(r)) else ""}" data-id="{e(wb.pid(r))}">'
            f"{e((pool_by_id or {}).get(wb.pid(r)) or 'not in pool')}</span>"
            for r in pooled
        )
        items.append(
            f'<li class="{"answered" if d.answer else ""}" data-key="{e(d.key)}"><div>{text}{badge}</div>'
            f'<div class="opts">{controls}</div>{pivots}</li>'
        )
    foot = (
        '<p class="note">Play writes <code>cash: Name</code> on Suggested\'s Always use line and adds him to your '
        "pool as Cash; Fade writes Never use (cash builds only) and takes him out of the pool only if he was Cash "
        "only; a stack writes <code>more Name</code> on Max exposure (GPP builds only) and adds the QB and mates "
        "as GPP; a pivot adds him as Both. Undo takes back both. The next <code>--iterate</code> reads them.</p>"
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


def role_chips(r: wb.BoardRow, practice: str = "") -> str:
    """The row's role chips, then a Q / D tag with the week's practice trend beside it (`practice.py`)."""
    chips = "".join(
        f'<span class="chip {ROLE_CLASS[x]}" title="{e(ROLE_TIP[x])}">{e(x)}</span>' for x in r.roles
    )
    status = wb.avail(r.row)
    if status in ("Q", "D"):
        word = week_plays.STATUS_WORD[status]
        tip = f"{word}. Practice: {practice}" if practice else word
        chips += f'<span class="chip q" title="{e(tip)}">{e(status)}</span>'
        if practice:
            chips += f'<span class="prac">{e(practice)}</span>'
    return chips


def own_td(value: float | None, pending: bool) -> str:
    if pending:
        return '<td class="n dim" title="ownership publishes ~Fri/Sat">-</td>'
    if value is None:
        return '<td class="n">-</td>'
    hot = " hot" if value >= wb.CHALK else ""
    return f'<td class="n{hot}">{pct(value)}</td>'


BOARD_COLUMNS = (
    ("Player", ""),
    ("Opp", ""),
    ("Spread", "n"),
    ("Total", "n"),
    ("Implied", "n"),
    ("Salary", "n"),
    ("Proj", "n"),
    ("Per $1K", "n"),
    ("DU own", "n"),
    ("GPP own", "n"),
    ("Why", ""),
    ("Pool", ""),
)
GAME_TIP = {
    "Spread": "His team's spread from the line (minus = favoured).",
    "Total": "The game's total (over / under).",
    "Implied": week_explorer.TIPS["Implied"],
}
BOARD_HOW = (
    "Plan I's plays from the last sync (week_plays): Value = CalPts per $1K, Cash core = "
    + week_plays.CASH_CORE_RULE.removeprefix("Cash core: ")
    + " Upside = Boom% (by leverage once GppOwn is out); FADE↓ = the proven signal."
)


def game_link(row: dict, team: str) -> str:
    """A team (or Opp) cell that opens the game view (Plan N item 10)."""
    game = str(row.get("GameID") or "")
    if not team:
        return ""
    return f'<a class="tm" href="#" data-game="{e(game)}" data-team="{e(team)}" title="Open the game">{e(team)}</a>'


def sel_box(row_id: str) -> str:
    """The multi-select tick (Plan N item 7): shift-click ticks the range."""
    return f'<input type="checkbox" class="sel" data-id="{e(row_id)}" aria-label="Select">'


def _signed(value: float | None) -> str:
    return "-" if value is None else f"{value:+g}"


def board_head(pos: str) -> str:
    """The header row each position group repeats (sticky while its group scrolls by)."""
    cells = []
    for label, cls in BOARD_COLUMNS:
        if label == "Player":
            text = f'<span class="posname">{e(pos)}</span>'
        elif label == "Proj":
            text = tip("CalPts", "Proj")
        elif label in ("Per $1K", "DU own", "GPP own"):
            text = tip(label)
        elif label in GAME_TIP:
            text = f'<abbr title="{e(GAME_TIP[label])}">{e(label)}</abbr>'
        else:
            text = e(label)
        cells.append(
            f'<th class="{" ".join(c for c in (cls, "nm" if label == "Player" else "") if c)}">{text}</th>'
        )
    return f'<tr class="ph">{"".join(cells)}</tr>'


def board_html(
    b: wb.Board, pool_by_id: dict[str, str], live: bool, practice: dict[str, str] | None = None
) -> str:
    if b.empty:
        return (
            '<section class="card" id="board"><div class="card-head"><h2>The board</h2></div>'
            '<p class="notrun"><b>Not run yet.</b> Nothing is synced, so there is nothing on the board. Say '
            + say("sync")
            + ".</p></section>"
        )
    width = len(BOARD_COLUMNS)
    body = []
    for pos in POSITIONS:
        rows = b.by_pos.get(pos) or []
        if not rows:
            continue
        more = sum(1 for r in rows if not r.shown)
        body.append(f'<tbody class="posgrp" data-pos="{pos}">{board_head(pos)}')
        for r in rows:
            why = e(r.why) + (f'<br><span class="record">{e(r.record)}</span>' if r.record else "")
            cls = " ".join(c for c in ("fade-row" if r.fade else "", "" if r.shown else "more") if c)
            implied = wb.implied_total(r.row)
            total = num(r.row.get("OverUnder"))
            body.append(
                f'<tr class="{cls}" data-id="{e(r.id)}"{" hidden" if not r.shown else ""}><td class="nm">'
                f"{sel_box(r.id)}{pl(r.row)}"
                f'<div class="tags">{role_chips(r, (practice or {}).get(wb.pid(r.row), ""))}</div></td>'
                f"<td>{game_link(r.row, str(r.row.get('Opp') or ''))}</td>"
                f"<td class=n>{_signed(num(r.row.get('Spread')))}</td>"
                f"<td class=n>{'-' if total is None else f'{total:g}'}</td>"
                f"<td class=n>{'-' if implied is None else f'{implied:.1f}'}</td>"
                f"<td class=n>${week_plays.salary(r.row):,.0f}</td><td class=n>{r.calpts:.1f}</td>"
                f"<td class=n>{(wb.per_k(r.row) or 0):.2f}</td>"
                + own_td(num(r.row.get("CashOwn")), b.cash_pending)
                + own_td(num(r.row.get("GppOwn")), b.gpp_pending)
                + f'<td class="why">{why}</td><td>{pool_seg(r.id, r.name, pool_by_id.get(r.id, ""), live)}</td></tr>'
            )
        if more:
            body.append(
                f'<tr class="morerow"><td colspan="{width}"><button type="button" class="showmore" data-pos="{pos}">'
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
        '<section class="card" id="board"><div class="card-head"><h2>The board</h2>'
        + how_html(BOARD_HOW)
        + "</div>"
        '<p class="note">Each player once, with his roles: the top 3 per role and position (<i>Show more</i> for the '
        "next ones; a chalk core player is always shown). A FADE↓ player is only a Fade, last in his position, with "
        "the signal's record. The Why line is what the columns do not say. Click a name for everything about him, "
        "an Opp for the game; tick players (shift-click for a range) to set their pool in one go.</p>"
        + pending
        + f'<div class="scroll tall"><table class="board stickyname">{"".join(body)}</table></div>'
        + caption(
            "Proj = CalPts; Per $1K = CalPts ÷ salary × 1,000; Implied = half the total minus half the spread. "
            "The roles come from week_plays.",
            ("CalPts", "Salary", "Spread", "OverUnder", "CashOwn", "GppOwn"),
        )
        + "</section>"
    )


# -- the charts and the slate --------------------------------------------------------------------------------


def chart_card(sid: str, key: str, title: str, note_html: str, svg: str) -> str:
    """A chart in its card: the note, the legend, the chart, a Full screen button and the "how this is built"
    note (Plan N items 9 and 14). A click on the chart (not on a dot) opens the viewer too."""
    return (
        f'<section class="card chartcard" id="{sid}" data-chart="{key}"><div class="card-head"><h2>{e(title)}</h2>'
        f'<button type="button" class="expand" data-chart="{key}" title="Zoom, pan and select">⤢ Full screen</button>'
        + how_html(week_charts.HOW[key])
        + f"</div>{note_html}{week_charts.LEGEND}{svg}"
        + caption(week_charts.FORMULA[key] + " " + week_charts.HOW[key], week_charts.SOURCES[key])
        + "</section>"
    )


def charts_html(edge: list[dict] | None, ownership_out: bool) -> str:
    value = week_charts.value_map(edge, width=520, height=340)
    lev = week_charts.leverage_map(edge, width=520, height=340) if ownership_out else ""
    parts = []
    if value:
        parts.append(
            chart_card(
                "value-map",
                "value",
                "Value map",
                '<p class="note">RB, WR, TE. Dots above the dashed line beat 3 points per $1K; the best are named. '
                "Hover a dot for the numbers, click it for the player, click the chart to zoom and select.</p>",
                value,
            )
        )
    if lev:
        parts.append(
            chart_card(
                "leverage-map",
                "leverage",
                "Leverage map",
                f'<p class="note">GPP ownership against the ceiling. The shaded corner is {tip("leverage")}: '
                "low owned with a top-quarter ceiling.</p>",
                lev,
            )
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
            f'<div class="game{" top" if g.top else ""}" data-game="{e(g.key)}" role="button" tabindex="0" '
            f'title="Open the game"><div class="gh"><b>{e(title)}</b><span>{e(total)}</span></div>'
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
        '<section class="card" id="games"><div class="card-head"><h2>The slate</h2>'
        + how_html(
            "Every game in the last sync, highest total first: team implied total = half the total minus half "
            "the team's spread; stack = a game of the Stacks input's top rows."
        )
        + "</div>"
        '<p class="note">Team implied totals, highest games first; <span class="tag stack">stack</span> marks a '
        "game of the sim's top stacks. Click a game for both teams side by side.</p>"
        f'{opening}<div class="games">{"".join(cards)}</div></section>'
    )


GAME_GROUPS = (("QB", ("QB",)), ("Pass catchers", ("WR", "TE")), ("RB", ("RB",)), ("DST", ("DST",)))
GAME_HOW = (
    "Both teams' priced players from the last sync (OUT and IR left out), by position, best CalPts first; the "
    "line from the odds feed; the stacks are the Stacks input's saved top rows in this game (dfs stacks)."
)


def _pct1(value: float | None) -> str:
    return "-" if value is None else f"{value * 100:.1f}%"


def game_data(inp: WeekInputs) -> dict:
    """The game view's data (Plan N item 10), per game key: the title and line, each team's players by position
    group (salary, CalPts, ceiling, GppOwn, leverage) and the saved stacks in the game."""
    games = week_plays.slate(inp.edge)
    if not games:
        return {}
    players = week_explorer.players(inp.edge)
    stacks = (inp.stacks or {}).get("rows") or []
    out = {}
    for g in games:
        teams = [t for t in (g.away, g.home) if t]
        title = f"{g.away} @ {g.home}" if g.away and g.home else g.key
        bits = [f"total {g.total:g}" if g.total is not None else "no total yet"]
        if g.favorite and g.spread is not None:
            bits.append(f"{g.favorite} {g.spread:+g}")
        imp = [f"{t} {g.implied[t]:.1f}" for t in teams if t in g.implied]
        if imp:
            bits.append("implied " + " / ".join(imp))
        bits += [x for x in (g.kickoff, g.weather) if x]
        by_team: dict[str, list] = {}
        for t in teams:
            mine = [r for r in players if r.get("Team") == t]
            groups = []
            for label, positions in GAME_GROUPS:
                rows = sorted(
                    (r for r in mine if r.get("Position") in positions),
                    key=lambda r: -(num(r.get("CalPts")) or 0),
                )
                if rows:
                    groups.append(
                        [
                            label,
                            [
                                {
                                    "id": wb.pid(r),
                                    "name": str(r.get("Name", "")),
                                    "pos": str(r.get("Position", "")),
                                    "sal": week_plays.salary(r),
                                    "cal": num(r.get("CalPts")),
                                    "ceil": num(r.get("CeilM")) or num(r.get("Ceiling")),
                                    "gpp": num(r.get("GppOwn")),
                                    "lev": num(r.get("Leverage")),
                                    "avail": wb.avail(r),
                                }
                                for r in rows
                            ],
                        ]
                    )
            by_team[t] = groups
        in_game = []
        for st in stacks:
            found = set(re.findall(r"[A-Z]{2,3}", str(st.get("game") or "")))
            if set(teams) and set(teams) <= found:
                lift = num(st.get("lift"))
                in_game.append(
                    f"{st.get('qb', '')} + {st.get('mates', '')}: Top 1% {_pct1(num(st.get('top1')))}"
                    + (f", lift {lift:.1f}x" if lift is not None else "")
                    + (f", leverage {_pct1(num(st.get('lev')))}" if num(st.get("lev")) is not None else "")
                    + (f" (type: {st.get('rule')})" if st.get("rule") else "")
                )
        out[g.key] = {
            "title": title,
            "total_text": f"total {g.total:g}" if g.total is not None else "",
            "how": GAME_HOW,
            "line": " · ".join(bits),
            "order": teams,
            "implied": {t: g.implied.get(t) for t in teams},
            "teams": by_team,
            "stacks": in_game,
            "no_stacks": "None of the saved top stacks is in this game"
            if stacks
            else "No stacks saved for this week yet (dfs stacks saves its top rows).",
        }
    return out


def game_data_json(inp: WeekInputs) -> str:
    return json.dumps(game_data(inp), separators=(",", ":"), default=str).replace("</", "<\\/")


# -- the Explorer --------------------------------------------------------------------------------------------


def explorer_html(inp: WeekInputs, data: dict) -> str:
    if not data.get("rows"):
        return (
            '<section class="card" id="explorer-card"><div class="card-head"><h2>Explorer</h2></div>'
            '<p class="notrun"><b>Not run yet.</b> Nothing is synced.</p></section>'
        )
    charts = (
        '<div class="ex-chart chartcard" id="ex-chart" data-chart="explorer"><div class="card-head"><h4>Chart</h4>'
        '<button type="button" class="expand" data-chart="explorer" title="Open full screen: pan, zoom, select">'
        "Full screen</button></div>"
        + week_charts.LEGEND_ALL
        + '<div class="ex-plot"></div><p class="cap" id="ex-chart-cap"></p></div>'
    )
    sel = lambda i, label, first="": (  # noqa: E731
        f'<label>{label}<select id="{i}"><option value="">{first or "any"}</option></select></label>'
    )
    return (
        '<section class="card" id="explorer-card" data-keep><div class="card-head"><h2>Explorer</h2>'
        '<span class="asof">every priced player, every EdgeRaw column</span>'
        + how_html(
            "Every priced player in the last sync (OUT and IR left out) with every EdgeRaw column; a lens is a "
            "column set and a sort; Per $1K, Implied, Chalk, PropsGap and SrcSpread are worked out here."
        )
        + "</div>"
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
        '<div class="scroll ex-scroll"><table id="ex-table" class="ex stickyname"></table></div>'
        + caption("Every priced player and every EdgeRaw column, as the last sync wrote them.", ("EdgeRaw",))
        + "</div></section>"
    )


DRAWER = '<div id="drawer" class="drawer" role="dialog" hidden aria-label="Player"></div>'


def explorer_data(data: dict) -> str:
    """The Explorer's data, before the shared script (`week_ui`, whose pool store starts from it)."""
    if not data.get("rows"):
        return ""
    return f'<script type="application/json" id="explorer-data">{week_explorer.payload_json(data)}</script>'


def explorer_js(data: dict, nonce_attr: str) -> str:
    """The Explorer's script: after the shared one, whose tick boxes and pool store it uses."""
    if not data.get("rows"):
        return ""
    return f"<script{nonce_attr}>{week_explorer.JS}</script>"


def explorer_script(data: dict, nonce_attr: str) -> str:
    """The Explorer's data and script together (the page itself puts the shared script between them)."""
    return explorer_data(data) + explorer_js(data, nonce_attr)
