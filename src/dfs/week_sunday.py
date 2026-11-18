"""The design pass's Sunday (Sam, 2026-11-17 and 2026-11-18): the Week page's Lock view, in the order he reads it
on Sunday morning.

1. **What changed since Saturday night** (`#lock-card`): the high-priority inactives first (an OUT / D / Q player
   in a lineup, the Default or the pool, listed while he is there, whatever the time of the change), then the
   week's movement that has a better swap, by points at stake. The rest of what moved sits in one fold, "No
   action needed". Every rule is blueprint C2's (`lock_state`): before lock the lineups are the Lineups tab
   (read fresh on the served page, no recording needed), swaps are the inactives check's pivots, and Swap /
   Keep / Later record to the decision log (`/api/lock`) and never write a sheet.
2. **Chalk you're not on** (`#chalk-sun`): every 30%+ CashOwn player, from the latest sync, who is in none of
   his cash lineups, with the move since Saturday night. Week 9's CeeDee Lamb (20% Saturday night, 36% at
   11:57 am and 41% at 12:40 pm Sunday) is the case it must catch. Information: no button writes the rules box.
3. **Before lock** (`#prelock`): the four checks, one line each.
4. **Late window** (`#late`): folded to one line.

Pure: it renders a `lock_state.LockView` and the week's inputs. Layout and type come from `week_style`."""

from __future__ import annotations

from dfs import lock_state as ls
from dfs import week_saturday
from dfs.week_inputs import WeekInputs
from dfs.week_page import num
from dfs.week_style import card, e, fold

LOCK_ID = "lock-card"  # the id the Lock state's buttons, tests and links already use
CHALK_ID = "chalk-sun"
CHECKS_ID = "prelock"
LATE_ID = "late"

LOCK_HOW = (
    "The news change log (data/news/changes.csv) netted from the baseline (the build your lineups came from, "
    "else Saturday 9 pm ET). First: every OUT / D / Q player in a lineup, the Default or your pool, listed while "
    "he is there whatever the time of the change. Then what moved (a status, CalPts by 1+ pt, ownership by 5+ "
    "points) and has a better swap. Sorted by points at stake. Best swap = the inactives check's pivot (same "
    "slot, the salary he frees plus what the lineup leaves, game not started, clean status; CalPts for cash, "
    f"Leverage for GPP), offered when it gains {ls.MIN_SWAP_GAIN:g}+ pts. Before lock the lineups are the "
    "Lineups tab; after it, the entries you recorded. From: changes.csv, EdgeRaw Avail / CalPts / CashOwn / "
    "GppOwn / GameStart / Salary."
)
CHALK_SUN_HOW = (
    "Every player with CashOwn 30% or more in the latest sync (the double-up field's projected ownership) who is "
    "in none of your cash lineups (the Lineups tab's cash lineups before lock, your recorded entries after; "
    "the Default when there are none) and is not OUT or IR. The last column is his move since Saturday night "
    "(the news change log's cash ownership at the baseline). From: EdgeRaw CashOwn / CalPts / Salary / Avail, "
    "changes.csv."
)
CHECKS_HOW = (
    "Four checks on the lineups (the Lineups tab before lock): no OUT / IR / Doubtful player, the latest player "
    "of each lineup in a FLEX-eligible slot, every contest has an entry (once entries are recorded), salary "
    "sanity. Grey when fine, amber only when wrong."
)


# -- what changed ------------------------------------------------------------------------------------------


def holders(lv: ls.LockView | None) -> set[str]:
    """Every name in a lineup the Lock state checks (normalised): a row's `touches` also counts lineups a
    teammate's news reaches, so "in a lineup" is asked here."""
    from dfs.player_join import normalize_name

    return {
        normalize_name(n) for s in (lv.sources if lv is not None else ()) for n in s.names if str(n).strip()
    }


def at_risk(lv: ls.LockView | None) -> list[ls.Row]:
    """The OUT / D / Q players in a lineup (or the Default, when no lineup is on file), game not started."""
    from dfs.player_join import normalize_name

    if lv is None:
        return []
    mine = holders(lv)
    return [
        r
        for r in lv.changes.all
        if r.status in ls.LIVE
        and not r.started
        and (normalize_name(r.name) in mine or (not mine and ls.DEFAULT_LABEL in r.touches))
    ]


def lineups_holding(lv: ls.LockView, rows: list[ls.Row]) -> list[str]:
    """The labels of the lineups that hold any of `rows`' players (the Default's label when none is on file)."""
    from dfs.player_join import normalize_name

    keys = {normalize_name(r.name) for r in rows}
    labels = {s.label for s in lv.sources if keys & {normalize_name(n) for n in s.names}}
    if not labels and any(ls.DEFAULT_LABEL in r.touches for r in rows):
        labels = {ls.DEFAULT_LABEL}
    return sorted(labels, key=lambda x: (len(x), x))


def is_inactive(r: ls.Row) -> bool:
    """A live problem: his status now is OUT / IR / D / Q (`lock_state.rows` only lists one that touches a
    lineup, the Default or the pool)."""
    return r.status in ls.LIVE


def lead_rows(lv: ls.LockView) -> tuple[list[ls.Row], list[ls.Row]]:
    """(the rows that lead, the rest). Lead: every live problem, and every change with a better swap whose game
    has not started; inactives first, rows in a lineup before pool-only ones, then by points at stake."""
    lead, rest = [], []
    for r in lv.changes.all:
        (lead if is_inactive(r) or (r.swap is not None and not r.started) else rest).append(r)
    lead.sort(
        key=lambda r: (
            not r.in_lineup,
            not is_inactive(r),
            -r.stake,
            -ls.WORSE.get(r.status, 0),
            r.name,
        )
    )
    return lead, rest


def _status_tag(r: ls.Row) -> str:
    if not is_inactive(r):
        return ""
    cls = "out" if r.status in ("OUT", "IR", "D") else "q"
    return f'<span class="tag {cls}">{e(r.status)}</span> '


def _buttons(r: ls.Row, lv: ls.LockView, live: bool) -> str:
    """Swap / Keep / Later (`week_views._lock_buttons`); Swap only when there is a swap to make."""
    from dfs import week_views as wv

    html = wv._lock_buttons(r, lv.answered, live)
    if r.swap is None and 'data-action="swap"' in html:
        start = html.index('<button type="button" class="lockbtn primary"')
        html = html[:start] + html[html.index("</button>", start) + len("</button>") :]
    return html


def _row(r: ls.Row, lv: ls.LockView, live: bool, mine: set[str]) -> str:
    from dfs import week_views as wv

    when = e(ls.clock(r.seen)) if r.seen else '<span title="before this week\'s log">earlier</span>'
    sub = f"{r.position} · {r.team}" + (f" · proj {wv._signed(r.d_points)}" if r.d_points is not None else "")
    started = " · <b>game started</b>" if r.started else ""
    from dfs.player_join import normalize_name

    via = (
        '<span class="meta">through his teammates: </span>'
        if r.touches and ls.DEFAULT_LABEL not in r.touches and normalize_name(r.name) not in mine
        else ""
    )
    who = wv._who(r, "; ")
    if who == '<span class="dim">-</span>':
        who = ""
    return (
        f'<div class="chg{" inact" if is_inactive(r) else ""}" data-row="{e(r.key)}">'
        f'<div class="when">{when}</div>'
        f'<div class="what">{_status_tag(r)}<b>{wv._what_line(r)}</b> <span class="meta">{e(sub)}{started}</span>'
        + (f'<div class="meta">{who}</div>' if who else "")
        + f'<div class="tchs">{via}{wv._touch(r)}</div></div>'
        f'<div class="swap">{wv._swap_cell(r)}</div>'
        f'<div class="btns">{_buttons(r, lv, live)}</div></div>'
    )


def changed_html(inp: WeekInputs, lv: ls.LockView | None, live: bool) -> str:
    title = "What changed since Saturday night"
    if lv is None:
        return card(LOCK_ID, title, '<p class="empty">Nothing to show: no slate synced.</p>')
    lead, rest = lead_rows(lv)
    since = lv.base.label
    where = f"lineups: {lv.where}" if lv.where else "lineups: your recorded entries" if lv.sources else ""
    sub = e(" · ".join(x for x in (since, "by points at stake", where) if x))
    parts = []
    if not lv.has_log:
        last = f"last sync {ls.when(lv.synced)}" if lv.synced else "no sync on file"
        parts.append(f'<p class="empty">No change logged this week yet ({e(last)}).</p>')
    if lead:
        mine = holders(lv)
        parts.append('<div class="chgs">' + "".join(_row(r, lv, live, mine) for r in lead) + "</div>")
    elif lv.has_log:
        parts.append('<p class="empty">No inactive player in your lineups, the Default or your pool, and nothing '
                     "that moved has a better swap.</p>")  # fmt: skip
    if rest or lv.changes.other:
        from dfs import week_views as wv

        items = "".join(wv._none_row(r) for r in rest)
        other = (
            f'<li class="dim">{lv.changes.other} other change{"s" if lv.changes.other != 1 else ""} touch no '
            "lineup and no pooled player</li>"
            if lv.changes.other
            else ""
        )
        parts.append(
            f'<details class="noact"><summary>No action needed ({len(rest)}) '
            '<span class="meta">moved, touches your lineups or pool, nothing better fits</span></summary>'
            f'<ul class="noact">{items}{other}</ul></details>'
        )
    body = f'<div class="sec-b">{"".join(parts)}</div>'
    head = card(
        LOCK_ID,
        title,
        body,
        sub=sub,
        foot="Swap, Keep and Later record your choice in the decision log and name the swap; you make it on "
        "DraftKings. Nothing here writes a sheet.",
        how=LOCK_HOW,
    )
    return head.replace(f'id="{LOCK_ID}"', f'id="{LOCK_ID}" data-state="{e(lv.state)}"', 1)


# -- chalk you're not on, Sunday ------------------------------------------------------------------------------


def cash_lineup(inp: WeekInputs, lv: ls.LockView | None) -> tuple[list[str], str]:
    """His cash lineup(s) as the Lock state reads them: the cash lineups among `lv.sources` (the Lineups tab
    before lock, the recorded entries after), every name in any of them; else Saturday's choice (the Default,
    the newest cash build, the entered cash lineup)."""
    cash = [s for s in (lv.sources if lv is not None else ()) if s.kind == "cash"]
    if cash:
        names = list(dict.fromkeys(str(n) for s in cash for n in s.names if str(n).strip()))
        labels = ", ".join(s.label for s in cash)
        many = "s" if len(cash) > 1 else ""
        return names, f"your cash lineup{many} ({labels})"
    return week_saturday.your_cash_lineup(inp)


def own_at_baseline(inp: WeekInputs, lv: ls.LockView | None) -> dict[str, float]:
    """Name -> CashOwn at the baseline, for every player whose cash ownership moved since (the rest did not)."""
    if lv is None:
        return {}
    out = {}
    for n in ls.net_changes(inp.changes, lv.base.at, inp.now):
        if n.field == "cash_own" and (b := num(n.before)) is not None:
            out[n.name] = b
    return out


def chalk_html(inp: WeekInputs, lv: ls.LockView | None) -> str:
    return week_saturday.chalk_html(
        inp,
        sid=CHALK_ID,
        prev=own_at_baseline(inp, lv),
        lineup=cash_lineup(inp, lv),
        controls=False,
        foot="Information: the double-up field is expected to play them and your cash lineup does not. A swap "
        "is yours to make on DraftKings; nothing here writes a sheet.",
        how=CHALK_SUN_HOW,
    )


# -- before lock ------------------------------------------------------------------------------------------------


def checks_html(lv: ls.LockView | None) -> str:
    if lv is None or not lv.checks:
        return ""
    items = []
    for c in lv.checks:
        cls = "bad" if c.ok is False else ("unk" if c.ok is None else "fine")
        mark = "!" if c.ok is False else ("?" if c.ok is None else "✓")
        items.append(
            f'<li class="chk1 {cls}" data-key="{e(c.key)}" title="{e(c.detail)}"><span class="mk">{mark}</span>'
            f'<b>{e(c.text)}</b><span class="meta">{e(c.detail)}</span></li>'
        )
    bad = sum(1 for c in lv.checks if c.ok is False)
    sub = f"{bad} to look at" if bad else "grey when fine, amber only when wrong"
    return card(
        CHECKS_ID, "Before lock", f'<ul class="chk1s">{"".join(items)}</ul>', sub=e(sub), how=CHECKS_HOW
    )


# -- the late window --------------------------------------------------------------------------------------------


def late_line(lv: ls.LockView | None) -> str:
    if lv is None or not lv.late:
        return "no player in your lineups plays after the first kickoff, or every game has started"
    players = {i.name for lu in lv.late for i in lu.items}
    swaps = sum(1 for lu in lv.late for i in lu.items if i.alt is not None)
    return (
        f"{len(players)} player{'s' if len(players) != 1 else ''} in later games across {len(lv.late)} "
        f"lineup{'s' if len(lv.late) != 1 else ''}; {swaps} could still swap for more"
    )


def late_html(lv: ls.LockView | None) -> str:
    from dfs import week_views as wv

    body = wv._late_rows(lv.late if lv is not None else [])
    return fold(
        LATE_ID,
        "Late window",
        late_line(lv),
        f'<div class="sec-b">{body}</div>',
        how="Your players whose games start after the first kickoff, by lineup, with the best pivot still "
        "open for each (same slot, salary he frees plus what the lineup leaves; CalPts for cash, Leverage for "
        "GPP). From: EdgeRaw GameStart / Salary / CalPts.",
    )


def sunday_html(inp: WeekInputs, lv: ls.LockView | None, live: bool) -> str:
    """The Sunday view's main column, in order."""
    return changed_html(inp, lv, live) + chalk_html(inp, lv) + checks_html(lv) + late_html(lv)


CSS = """
.chgs{display:flex;flex-direction:column}
.chg{display:grid;grid-template-columns:64px minmax(0,1.4fr) minmax(0,1fr) auto;gap:4px 12px;align-items:start;
padding:10px 0;border-top:1px solid var(--line)}
.chg:first-child{border-top:0}
.chg .when{font-size:12px;color:var(--ink-3);padding-top:2px;font-variant-numeric:tabular-nums}
.chg .what b{font-weight:600}.chg .tchs{margin-top:4px}
.chg .swap{font-size:13px}.chg .swap .up{color:var(--good)}
.chg .btns .lockbtns{display:flex;gap:4px;flex-wrap:wrap;justify-content:flex-end}
.chg.inact .when{color:var(--bad)}
.tch{display:inline-block;font-size:11px;line-height:18px;padding:0 6px;border-radius:4px;background:var(--sunk);
color:var(--ink-2);margin-right:2px}.tch.pool{background:transparent;border:1px solid var(--accent);color:var(--accent)}
details.noact{margin-top:8px}details.noact summary{cursor:pointer;font-size:13px}
ul.noact{list-style:none;padding:0;margin:6px 0 0;font-size:13px}ul.noact li{padding:4px 0;border-top:1px solid var(--line)}
ul.chk1s{list-style:none;margin:0;padding:0 16px 8px}
.chk1{display:grid;grid-template-columns:18px auto minmax(0,1fr);gap:8px;align-items:baseline;padding:5px 0;
border-top:1px solid var(--line);color:var(--ink-3)}
.chk1:first-child{border-top:0}
.chk1 .meta{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.chk1 b{font-weight:500;color:var(--ink-2);white-space:nowrap}
.chk1.bad{color:var(--warn)}.chk1.bad b{color:var(--warn)}.chk1.bad .meta,.chk1.unk .meta{white-space:normal}
@media(max-width:700px){
.chg{grid-template-columns:52px minmax(0,1fr)}.chg .swap,.chg .btns{grid-column:2}
.chg .btns .lockbtns{justify-content:flex-start}
.chk1{grid-template-columns:18px minmax(0,1fr)}.chk1 .meta{grid-column:2;white-space:normal}.chk1 b{white-space:normal}}
"""
