"""The design pass's Tuesday (Sam, 2026-11-17): the Week page's Close view, the closed week in the order he
reads it after the close.

1. **The week, closed** (`#close-card`): the sentence (his cash lineup against the double-up lines, the
   double-ups cashed, the share of the field beaten, the week's net) with **the one thing worth changing**
   directly under it.
2. **What decided it** (`#close-decided`): the cash post-mortem's five biggest casher / non-casher gaps, each with
   the cash ownership predicted against actual and its tag.
3. **You against the nearest build** (`#close-vs`): this week and the season, "too few to call" under 12 weeks.
4. **Scorecards** (`#close-scores`): the four tiles, then the model table (each model against its baseline).
5. **The ledger** (`#close-ledger`), folded to its one line.
6. **The decision log** (`#decision-log`), folded; one tap gives a decision its reason.

The side adds **Next** (start the next week) below This week and the drop zone. Everything is blueprint Phase D's
(`close_view`, the `close` input `dfs week review` saves); nothing here blocks, warns or writes a sheet. Pure."""

from __future__ import annotations

from dfs import close_view, week_money
from dfs import week_learn as wl
from dfs.week_inputs import WeekInputs
from dfs.week_page import chip_html, not_run, note, pct, pn, table
from dfs.week_style import card, e, fold

CLOSE_ID = wl.CLOSE_ID  # "close-card": the id tests and links already use
DECIDED_ID = "close-decided"
VS_ID = "close-vs"
SCORES_ID = "close-scores"
LEDGER_ID = "close-ledger"  # the Ledger and plan view keeps `#ledger`
LOG_ID = "decision-log"
NEXT_ID = "next-week"

SENTENCE_HOW = (
    "Your double-up lineup's points against the week's double-up cash lines, how many double-ups cashed, the "
    "share of the double-up field you beat (the median of your entries' finish), and the week's net over every "
    "contest. The one thing is the first of the monthly triggers that fires (CalPts behind TFFB 4 weeks, cash "
    "ownership behind the curve, the edge at lock at or under zero, the cash chance above the cash rate, the Fun "
    "line over plan), else Nothing. From: the close input (dfs week review), the contest log, the standings."
)
DECIDED_HOW = (
    "The cash post-mortem's five biggest casher / non-casher gaps in the week's double-ups; you had him = in a "
    "cash lineup you entered, a choice = in a cash build or your pool, variance = neither."
)
SCORES_HOW = (
    "Each model against its simple baseline by week, weeks as the unit: Proven = ahead by 2 SE, New = under 3 "
    "weeks, Overstated = the cash chance shown above the cash rate by 2 SE."
)


def _chip() -> str:
    return chip_html(*wl.TRUST["numbers"])


def sentence_html(inp: WeekInputs) -> str:
    c = inp.close
    if not c:
        body = (
            '<div class="sec-b">'
            + not_run("the close's review", "close the week", "dfs week close")
            + "</div>"
        )
        return card(CLOSE_ID, "The week, closed", body, sub="after the games, from the close", cls="close1")
    body = (
        f'<div class="sec-b"><p class="line1">{e(close_view.week_line(c))}</p>'
        '<div class="onething"><small>The one thing worth changing</small>'
        f"<p>{e(c.get('one_thing') or 'Nothing.')}</p></div></div>"
    )
    return card(
        CLOSE_ID,
        f"Week {c['week']}, closed",
        body,
        end=_chip(),
        how=SENTENCE_HOW,
        foot="Information only: nothing here blocks, warns or writes a sheet.",
        cls="close1",
    )


def decided_html(c: dict) -> str:
    rows = [
        [
            pn(d["name"]) + f' <span class="dim">{e(d.get("position", ""))}</span>',
            wl._bar(float(d["in_cashers"])),
            wl._bar(float(d["in_non_cashers"]), "non"),
            ("-" if d.get("cash_own") is None else pct(d["cash_own"])) + " → " + pct(d.get("field")),
            f'<span class="tagchip {wl.TAG_CLASS.get(d["tag"], "")}">{e(d["tag"])}</span> '
            f'<span class="dim">{e(d.get("detail", ""))}</span>',
        ]
        for d in c.get("decided") or []
    ]
    body = (
        '<div class="sec-b">'
        + table(
            ["Player", "In cashers", "In non-cashers", "Cash own, predicted → actual", "Tag"],
            rows,
            how=DECIDED_HOW,
            src=("data/contests/cash_postmortem.csv", "data/entered/"),
        )
        + "</div>"
        if rows
        else '<div class="sec-b">'
        + note("No double-up standings for this week, so nothing to separate cashers from the rest.")
        + "</div>"
    )
    return card(DECIDED_ID, "What decided it", body, sub="the players who split cashers from the rest")


def vs_html(c: dict) -> str:
    vs = c.get("vs_default") or {}
    wk, ss = vs.get("week"), vs.get("season") or {}

    def fig(label: str, v: dict | None) -> str:
        if not v or not v.get("lineups"):
            return f"<div><small>{e(label)}</small><b>-</b><span class=dim>no lineups matched</span></div>"
        net = float(v["gain"]) + float(v["loss"])
        return (
            f"<div><small>{e(label)}</small><b>{net:+.1f} pts</b>"
            f'<span class="dim">{v["overrides"]} of {v["lineups"]} lineups overrode the build · '
            f"{float(v['gain']):+.1f} / {float(v['loss']):+.1f}</span></div>"
        )

    weeks = int(ss.get("weeks") or 0)
    body = (
        '<div class="sec-b"><div class="vsdef">'
        + fig("This week", wk)
        + fig(f"Season to date ({weeks} week{'s' if weeks != 1 else ''})", ss)
        + "</div>"
        + (
            f'<p class="note"><span class="tagchip choice">{weeks} weeks: too few to call</span> under '
            f"{wl.TOO_FEW_WEEKS} weeks a total like this is mostly the players' variance.</p>"
            if weeks < wl.TOO_FEW_WEEKS
            else ""
        )
        + "</div>"
    )
    return card(
        VS_ID,
        f"You against {vs.get('against', 'the nearest build')}",
        body,
        sub="points your edits won or lost against the build each lineup came from",
        how="Each lineup you entered against its nearest saved build of its kind (8+ shared players: "
        "edited); gained / lost is the points of the players you swapped in minus the ones you swapped out. "
        "From: data/contests/scorecard.csv.",
    )


def scores_html(c: dict) -> str:
    tiles = "".join(
        f'<div class="tile {e(tone)}"><small>{e(label)}</small><b>{e(value)}</b><span>{e(n)}</span></div>'
        for label, value, n, tone in wl.close_tiles(c)
    )
    cards = c.get("scorecards") or []
    model = table(
        ["Model", "This week", "Season", "Against its simple baseline", "Weeks", "Status"],
        [
            [
                e(r["model"]),
                e(r["week"]),
                e(r["season"]),
                e(r["baseline"]),
                str(r["weeks"]),
                f'<span class="status {wl.STATUS_CLASS.get(r["status"], "")}">{e(r["status"])}</span>',
            ]
            for r in cards
        ],
        how=SCORES_HOW,
        src=("numbers", "field_own", "data/contests/cash_shown.csv", "data/contests/edge_at_lock.csv"),
    )
    return card(
        SCORES_ID,
        "Scorecards",
        f'<div class="sec-b"><div class="strip close-tiles" aria-label="The close in four numbers">{tiles}</div>'
        f'</div><div class="sec-b">{model}</div>',
        sub="each model against its simple baseline",
        end=_chip(),
    )


def ledger_html(inp: WeekInputs) -> str:
    sec = week_money.ledger_section(inp)
    line, _ = week_money.ledger_line(inp)
    return fold(
        LEDGER_ID,
        "The ledger",
        line,
        f'<div class="sec-b">{sec.body}</div>',
        end=chip_html(*sec.trust) if sec.trust else "",
    )


def log_html(inp: WeekInputs, live: bool) -> str:
    n, body = wl.decision_log_body(inp, live)
    return fold(
        LOG_ID,
        "Decision log",
        f"{n} decision{'s' if n != 1 else ''} this week",
        f'<div class="sec-b">{body}</div>',
    )


def next_html(inp: WeekInputs) -> str:
    """The side's Next card: start the next week (it needs the new sheet's URL, so it is said, not a button)."""
    nxt = inp.week + 1
    body = (
        f'<p class="note">Say <b>“start week {nxt}”</b> with the new sheet’s URL: it runs '
        f"<code>dfs week new</code> and <code>dfs doctor</code>.</p>"
        if inp.close
        else '<p class="note">After the close: say <b>“close the week”</b> first.</p>'
    )
    return card(NEXT_ID, f"Next: Week {nxt}", f'<div class="sec-b">{body}</div>')


def tuesday_html(inp: WeekInputs, live: bool = False) -> str:
    """The Close view's main column."""
    c = inp.close
    parts = [sentence_html(inp)]
    if c:
        parts += [decided_html(c), vs_html(c), scores_html(c), ledger_html(inp)]
    parts.append(log_html(inp, live))
    return "".join(parts)


CSS = """
#close-card .line1{font-size:16px;line-height:1.5;margin:0 0 12px}
#close-card .onething{border-left:4px solid var(--accent);padding:8px 14px;background:var(--bg);margin:0}
#close-card .onething small{display:block;font-size:11px;letter-spacing:.04em;text-transform:uppercase;
color:var(--ink-3)}#close-card .onething p{margin:2px 0 0;font-size:15px}
#close-decided .bar{display:inline-block;vertical-align:middle;width:90px;height:6px;border-radius:3px;
background:var(--line);margin-left:6px;position:relative;overflow:hidden}
#close-decided .bar i{position:absolute;left:0;top:0;bottom:0;background:var(--accent)}
#close-decided .bar.non i{background:var(--ink-3)}
#close-scores .strip.close-tiles{margin:4px 0 14px}
@media(max-width:760px){#close-decided .bar{width:48px}}
"""
