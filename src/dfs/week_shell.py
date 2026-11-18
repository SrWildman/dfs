"""The Week page's shell (design pass, PR 1): the sticky header with the three day views, the status line of
each day, the side column's "This week" checklist, and the More bar.

- **The day views** (Saturday · Build, Sunday · Lock, Tuesday · Close) are reachable on any day; the clock
  (`lock_state.state`) only picks which one opens first (it carries the dot).
- **The status line** changes by day (Sam, 2026-11-17): Saturday: Lock · Pool (by position) · Builds · News ·
  Entered. Sunday: Lock · News (changes since Saturday night) · Lineups (at risk) · Swaps open · Entered.
  Tuesday: Week net · Cash (x of y) · Scorecards · Next. Grey; amber or green only for an outlier. An item that
  waits on a step says what it waits for: "not run yet" never takes a row.
- **This week** (the side): the six steps of the week as a checklist, each with its Run button; this is where a
  step that has not run is listed.
- **More**: one row of pills, each a view of its own (`#name`), so every section the page has is one click away
  while the day view leads.
"""

from __future__ import annotations

from datetime import timedelta

from dfs import kickoff
from dfs import week_board as wb
from dfs.week_inputs import WeekInputs, parse_time
from dfs.week_page import builds_of, e, num, run_button, suggest_time
from dfs.week_plays import POSITIONS
from dfs.week_style import Fact, status_line
from dfs.week_top import say as say_html

DAYS = (("build", "Saturday"), ("lock", "Sunday"), ("close", "Tuesday"))
# More: (view, label) groups; each is a view of its own (`week_views.VIEWS`)
MORE = (
    (
        ("players", "Board"),
        ("explore", "Explorer"),
        ("slate", "Games"),
        ("usage", "Usage"),
        ("injuries", "Injuries"),
        ("model", "Model vs sources"),
        ("overview", "Overview"),
    ),
    (
        ("builds", "Builds"),
        ("gameday", "Game day"),
        ("money", "Ledger and plan"),
        ("lastweek", "Last week"),
        ("seasonlong", "Season-long"),
    ),
    (("run", "Run a command"), ("everything", "Everything")),
)
NEWS_WINDOW = timedelta(hours=24)  # Saturday's News item before a build: the last day of the change log


# -- the header ------------------------------------------------------------------------------------------------


def header_html(inp: WeekInputs, clock_state: str, live: bool, meta: str, stale: str) -> str:
    tabs = "".join(
        f'<a href="#{k}" data-view="{k}"{" class=on" if k == clock_state else ""}>{e(label)}'
        + ('<span class="now" title="Today, by the clock"></span>' if k == clock_state else "")
        + "</a>"
        for k, label in DAYS
    )
    ask = (
        '<button type="button" class="askbtn" id="ask-open" title="Open the chat">Ask Claude</button>'
        if live
        else ""
    )
    return (
        f'<header class="wk-top"><div class="wk">Week {inp.week}<span>{e(kickoff.format_et(inp.now))}</span></div>'
        f'<nav id="views" class="daytabs" aria-label="Day views">{tabs}</nav>'
        '<div class="wk-tools"><span class="vsearch"><input id="psearch" type="search" placeholder="Find a player  /" '
        'autocomplete="off" aria-label="Find a player"><span id="psearch-list" class="srlist" hidden></span></span>'
        f"{ask}</div></header>"
        f'<p class="sub wk-meta">{meta}</p>' + (f'<p class="wk-meta warn">{stale}</p>' if stale else "")
    )


def more_html() -> str:
    groups = '<span class="sep"></span>'.join(
        '<span class="grp">'
        + "".join(f'<a href="#{k}" data-view="{k}">{e(label)}</a>' for k, label in g)
        + "</span>"
        for g in MORE
    )
    return f'<nav id="more" class="morebar" aria-label="More"><h2>More</h2>{groups}</nav>'


# -- the status lines ------------------------------------------------------------------------------------------


def _tile(lv, key: str):  # noqa: ANN001, ANN202 - lock_state.LockView, Tile
    return next((t for t in (lv.tiles if lv is not None else []) if t.key == key), None)


def _lock_fact(lv) -> Fact:  # noqa: ANN001
    t = _tile(lv, "time")
    if t is None:
        return Fact("Lock", "not known", "no kickoffs synced yet")
    return Fact("Lock", t.value, t.note, t.tone)


def _entered_fact(lv) -> Fact:  # noqa: ANN001
    t = _tile(lv, "entered")
    if t is None:
        return Fact("Entered", "-", "")
    value = t.value if t.value != "not run yet" else "no plan yet"
    note = t.note if t.value != "not run yet" else "contest plan not run"
    return Fact("Entered", value, note, t.tone)


def pool_counts(inp: WeekInputs, by_id: dict[str, str]) -> dict[str, int]:
    out = dict.fromkeys(POSITIONS, 0)
    for r in inp.edge or []:
        if by_id.get(wb.pid(r)) and r.get("Position") in out:
            out[str(r["Position"])] += 1
    return out


def saturday_facts(inp: WeekInputs, lv, by_id: dict[str, str]) -> list[Fact]:  # noqa: ANN001
    counts = pool_counts(inp, by_id)
    total = sum(counts.values())
    pool = Fact(
        "Pool",
        f"{total} players" if total else "empty",
        " · ".join(f"{p} {n}" for p, n in counts.items()) if total else "add from the slate below",
    )
    cash, gpp = builds_of(inp, "cash"), builds_of(inp, "gpp")
    at = suggest_time(inp)
    if cash or gpp:
        builds = Fact(
            "Builds",
            f"round {(inp.suggest or {}).get('round', '?')}",
            f"{len(cash)} cash · {len(gpp)} GPP"
            + (f" · {kickoff.format_et(at, suffix=False)}" if at else ""),
        )
    else:
        builds = Fact("Builds", "none yet", "say “build me lineups”")
    return [_lock_fact(lv), pool, builds, news_fact(inp, at), _entered_fact(lv)]


def news_fact(inp: WeekInputs, since=None) -> Fact:  # noqa: ANN001 - datetime | None
    from dfs import lock_state

    if not inp.changes:
        return Fact("News", "no log yet", "the news log starts with a sync")
    start = since or inp.now - NEWS_WINDOW
    nets = lock_state.net_changes(inp.changes, start, inp.now)
    status = [n for n in nets if n.field == "status"]
    when = "since the build" if since else "in the last day"
    return Fact(
        "News",
        f"{len(status)} status change{'s' if len(status) != 1 else ''}",
        f"{len(nets) - len(status)} projection / ownership moves {when}",
    )


def sunday_facts(inp: WeekInputs, lv) -> list[Fact]:  # noqa: ANN001
    if lv is None:
        return [
            _lock_fact(lv),
            Fact("News", "-"),
            Fact("Lineups", "-"),
            Fact("Swaps open", "-"),
            _entered_fact(lv),
        ]
    ch = lv.changes
    n = len(ch.act) + len(ch.none) + ch.other
    since = lv.base.at
    news = Fact(
        "News", f"{n} change{'s' if n != 1 else ''}", f"since {kickoff.format_et(since, suffix=False)}"
    )
    risky = {
        t
        for r in [*ch.act, *ch.none]
        if r.status in ("OUT", "IR", "D", "Q") and not r.started
        for t in r.touches
    }
    health = _tile(lv, "health")
    if risky:
        lineups = Fact("Lineups", f"{len(risky)} at risk", "hold an OUT / D / Q player", "warn")
    elif health is not None and health.value != "-":
        lineups = Fact("Lineups", "none at risk", "no OUT / D / Q player in them")
    else:
        lineups = Fact("Lineups", "none yet", "the Lineups tab or your entries")
    swaps = sum(1 for r in ch.act if r.key not in lv.answered)
    return [
        _lock_fact(lv),
        news,
        lineups,
        Fact("Swaps open", str(swaps), "changes with a better swap" if swaps else "nothing to take or leave"),
        _entered_fact(lv),
    ]


def tuesday_facts(inp: WeekInputs) -> list[Fact]:
    c = inp.close or {}
    if not c:
        return [
            Fact("Week net", "not closed", "say “close the week” after the games"),
            Fact("Cash", "-"),
            Fact("Scorecards", "-"),
            Fact("Next", f"Week {inp.week + 1}", "after the close"),
        ]
    net = num(c.get("net"))
    cash = c.get("cash") or {}
    cashed, entered = cash.get("cashed"), cash.get("entered")
    cards: dict[str, int] = {}
    for r in c.get("scorecards") or []:
        st = str(r.get("status") or "")
        if st:
            cards[st] = cards.get(st, 0) + 1
    return [
        Fact(
            "Week net", "-" if net is None else f"{'-' if net < 0 else '+'}${abs(net):,.0f}", "every contest"
        ),
        Fact(
            "Cash",
            f"{cashed} of {entered}" if entered else "-",
            "double-ups cashed" if entered else "no double-ups entered",
        ),
        Fact(
            "Scorecards",
            f"{sum(cards.values())} models" if cards else "-",
            " · ".join(f"{n} {k.lower()}" for k, n in cards.items()),
        ),
        Fact("Next", f"Week {inp.week + 1}", "say “start week " + str(inp.week + 1) + "”"),
    ]


def status_lines(inp: WeekInputs, lv, by_id: dict[str, str]) -> dict[str, str]:  # noqa: ANN001
    return {
        "build": status_line("status-build", saturday_facts(inp, lv, by_id)),
        "lock": status_line("status-lock", sunday_facts(inp, lv)),
        "close": status_line("status-close", tuesday_facts(inp)),
    }


# -- this week (the side) ----------------------------------------------------------------------------------------


def this_week_html(steps: list[wb.Step], live: bool) -> str:
    rows = []
    for s in steps:
        act = ""
        if s.state != wb.DONE and s.command:
            if live:
                form = ' data-form="1"' if s.name == "Close" else ""
                act = f'<button type="button" class="runbtn" data-command="{e(s.command)}"{form}>Run</button>'
            else:
                act = run_button(s.command, "Run")
        rows.append(
            f'<li class="{e(s.state)}"><span class="i"></span><span class="w">{e(s.name)}'
            f"<small>{e(s.status)}"
            + (f" · say {say_html(s.say)}" if s.state == wb.NEXT and not live else "")
            + f"</small></span>{act}</li>"
        )
    return (
        '<section class="card sec" id="progress"><div class="sec-h"><h2>This week</h2></div>'
        f'<ul class="steps">{"".join(rows)}</ul></section>'
    )


def stale_text(items: list[str]) -> str:
    return ("Stale: " + e("; ".join(items))) if items else ""


def since_build(inp: WeekInputs):  # noqa: ANN201 - datetime | None
    return parse_time((inp.suggest or {}).get("at"))
