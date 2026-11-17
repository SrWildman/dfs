"""The Week page's Plan K2 sections, the learning loop: what won last week and how the field builds, exposure
against the field, how good are our numbers (every trust chip links here) and the salary watch; plus the
process scorecard shown under Last week.

The inputs are what `dfs week review` (run by `dfs week close`) saved for the closed week (`week_review`), and
for the salary watch last week's DraftKings salary file. Read-only, like the rest of the page: a section whose
input is missing says "not run yet" and what produces it.
"""

from __future__ import annotations

import dataclasses

from dfs import exposure as expo
from dfs import field_review as fr
from dfs import numbers as nums
from dfs import salary_watch as sw
from dfs import scorecard as sc
from dfs.week_inputs import WeekInputs
from dfs.week_page import (
    DATA,
    PROVEN,
    SHADOW,
    UNTESTED,
    Section,
    add_button,
    bullets,
    chip_html,
    e,
    not_run,
    note,
    num,
    pct,
    pn,
    table,
)

NUMBERS_ID = "numbers"  # every trust chip on the page links to this section

TRUST: dict[str, tuple[str, str]] = {
    "field_review": (DATA, "The closed week's standings: what the field played and what scored."),
    "scorecard": (
        DATA,
        "Arithmetic on what you entered, your saved builds and the actual points: decisions, not luck.",
    ),
    "exposure": (
        DATA,
        "Your entries against ownership: the actual %Drafted after the close, our GppOwn estimate (Proven) "
        "before it.",
    ),
    "numbers": (
        DATA,
        "Model Check's own scores and the closed weeks' results: the record behind every chip.",
    ),
    "salaries": (
        DATA,
        "DraftKings' salaries this week and last, and the snap / target log: a price change, not a "
        "prediction.",
    ),
}


def chip(key: str) -> str:
    label, why = TRUST[key]
    return chip_html(label, why)


def _money(x: float | None) -> str:
    return "-" if x is None else f"${x:,.0f}"


# -- 6. what won last week
# ------------------------------------------------------------------------------------


def _lineup_cell(v: dict | None) -> str:
    if not v:
        return "-"
    own = f"own sum {v['own_sum'] * 100:.0f}%" if v.get("own_sum") is not None else "own sum -"
    return (
        f"<b>{v['points']:.1f}</b> &middot; {e(v['shape'])} &middot; {e(own)} &middot; "
        f'{e(_money(v.get("salary_left")))} left<br><span class="dim">{e(", ".join(v["names"]))}</span>'
    )


def _tend_row(label: str, t: dict | None) -> list[str]:
    if not t or not t.get("n"):
        return [e(label), "-", "-", "-", "-", "-", "-", "-"]
    stacks = ", ".join(f"{team} {share * 100:.0f}%" for team, share in t.get("top_stacks") or [])
    return [
        e(label),
        f"{t['n']:,}",
        e(pct(t.get("stack"))),
        e(pct(t.get("stack2"))),
        e(pct(t.get("bringback"))),
        e(_money(t.get("salary_left"))),
        e(pct(t.get("flex_rb"))),
        e(stacks or "-")
        + (f" &middot; own sum {t['own_sum'] * 100:.0f}%" if t.get("own_sum") is not None else ""),
    ]


def field_review_section(inp: WeekInputs) -> Section:
    sid, title = "field-review", "What won last week"
    p = inp.field_review
    if not p or not p.get("formats"):
        return Section(
            sid,
            title,
            not_run("No closed week has been reviewed", say="close the week", command="dfs week review"),
            TRUST["field_review"],
            ran=False,
        )
    body = [
        f"<p>Week {p['week']}, from the standings you downloaded at the close: the winner and the last paid "
        "place of each contest type's largest contest, then how the whole field of that type built, and your "
        "lineups beside it.</p>"
    ]
    for b in p["formats"]:
        big = b["largest"]
        body.append(
            f"<h4>{e(b['format'])} &middot; {b['contests']} contest{'s' if b['contests'] != 1 else ''}</h4>"
            + note(f"Largest: {big['series']} ({big['entries']:,} entries, {big['places_paid']:,} paid).")
        )
        rows = [["Won", _lineup_cell(b.get("winner"))]]
        if b.get("cash_line"):
            rows.append(["Last paid place", _lineup_cell(b["cash_line"])])
        body.append(
            table(
                ["", "Lineup"],
                rows,
                how="The standings export of the format's largest contest: the winning lineup and the last "
                "paid place, with their own sum, salary left and stack shape.",
            )
        )
        body.append(
            table(
                [
                    "",
                    "Lineups",
                    "QB stack",
                    "QB + 2",
                    "Bring-back",
                    "Salary left",
                    "RB in FLEX",
                    "Top stacks",
                ],
                [_tend_row("The field", b.get("field")), _tend_row("You", b.get("mine"))],
                how="Every lineup in the format's standings exports against yours: the share with a QB "
                "stack, a bring-back, three RBs, and the average salary left.",
            )
        )
    body.append(
        note(
            "QB stack = the QB and one or more of his WR / TE (QB + 2: two or more). Bring-back = any player "
            "from the QB's opponent. RB in FLEX = three running backs. Own sum = the nine players' ownership "
            "in that contest, added up."
        )
    )
    return Section(sid, title, "".join(body), TRUST["field_review"])


def field_review_line(inp: WeekInputs) -> tuple[str, bool]:
    return fr.headline(inp.field_review) or "not run yet", False


# -- 7. the process scorecard (under Last week)
# -----------------------------------------------------------------


def scorecard_html(inp: WeekInputs) -> str:
    p = inp.scorecard
    head = f"<h4>Process scorecard {chip('scorecard')}</h4>"
    if not p:
        return head + not_run(
            "The scorecard is part of `week close`", say="close the week", command="dfs week review"
        )
    out = [head, f"<p>Week {p['week']}: what was decided, not what it paid.</p>"]
    lines = sc.lines(p)[1:]
    out.append(bullets([e(x.strip()) for x in lines if not x.startswith("    ")]))
    changed = p.get("overrides") or []
    if changed:
        rows = [
            [
                e(o["kind"]),
                e(o["build"]),
                f"{o['shared']} of 9",
                f"<b>{o['delta']:+.1f}</b>" if o.get("delta") is not None else "-",
                swap_html(o),
            ]
            for o in changed
        ]
        out.append(
            "<details><summary>Every override</summary>"
            + table(
                ["Kind", "Nearest build", "Shared", "Points vs the build", "Swaps (actual points)"],
                rows,
                how="Each entered lineup against its nearest saved build of its kind (most players shared), "
                "scored with the actual points.",
            )
            + "</details>"
        )
    return "".join(out)


# -- 8. exposure against the field
# ------------------------------------------------------------------------------


def _expo_rows(rows: list[dict]) -> list[list[str]]:
    return [
        [
            pn(r["name"]),
            e(pct(r["mine"])),
            e(pct(r["field"])),
            f"<b>{(r['diff'] or 0) * 100:+.0f}</b>",
            str(r["count"]),
        ]
        for r in rows
    ]


EXPO_HOW = (
    "Your GPP entries per contest entry holding each player, against the field's share (GppOwn before lock, "
    "DK's actual %Drafted after the close); 15+ points over or under."
)
SALARY_HOW = (
    "DK's salary file against last week's main-slate file; the role is steady when snaps or targets held "
    "within 10% over his last three games."
)


def _expo_block(title: str, v: dict, field_label: str) -> str:
    out = [f"<h4>{e(title)}</h4>"]
    heads = ["Player", "You", field_label, "Gap (pts)", "Entries"]
    if v.get("over"):
        out.append(
            '<p class="flag"><b>Overweight</b> (15+ points above the field)</p>'
            + table(heads, _expo_rows(v["over"]), cls="stickyname", how=EXPO_HOW)
        )
    if v.get("under"):
        out.append(
            '<p class="flag"><b>Underweight</b> (15+ points below)</p>'
            + table(heads, _expo_rows(v["under"]), cls="stickyname", how=EXPO_HOW)
        )
    if not v.get("over") and not v.get("under"):
        out.append(note("No player 15 points or more away from the field."))
    return "".join(out)


def entered_gpp(inp: WeekInputs) -> list[tuple[str, ...]]:
    """This week's recorded GPP entries, one per contest entry (a lineup in three GPPs counts three times)."""
    from dfs import inactives as ina

    out = []
    for lu in (inp.entered or {}).get("lineups") or []:
        names = tuple(lu.get("names") or [])
        gpps = [c for c in lu.get("contests") or [] if ina.kind_from("", [str(c.get("format", ""))]) == "gpp"]
        out.extend([names] * len(gpps))
    return out


def exposure_section(inp: WeekInputs) -> Section:
    sid, title = "exposure", "Exposure against the field"
    body = []
    entries = entered_gpp(inp)
    if entries:
        own = {}
        for r in inp.edge or []:
            v = num(r.get("GppOwn"))
            if v is not None:
                own[str(r.get("Name", ""))] = v
        if own:
            body.append(
                _expo_block(
                    f"This week: {len(entries)} GPP entries against GppOwn", expo.view(entries, own), "GppOwn"
                )
            )
        else:
            body.append(note("GppOwn is not out yet: the comparison waits for it."))
    closed = inp.exposure_close
    if closed and (closed.get("actual") or {}).get("entries"):
        a = closed["actual"]
        body.append(
            _expo_block(
                f"Week {closed['week']}: {a['entries']} GPP entries against actual ownership", a, "Actual"
            )
        )
        pred = closed.get("predicted") or {}
        if pred.get("over") or pred.get("under"):
            body.append(
                "<details><summary>The same entries against what GppOwn predicted</summary>"
                + _expo_block("Against GppOwn (predicted before lock)", pred, "GppOwn")
                + "</details>"
            )
    if not body:
        return Section(
            sid,
            title,
            not_run(
                "No GPP entries recorded this week and no closed week reviewed",
                say="record my entries",
                command="dfs entries record",
            ),
            TRUST["exposure"],
            ran=False,
        )
    body.append(note("GPP only: cash is one lineup. Exposure counts every contest entry."))
    return Section(sid, title, "".join(body), TRUST["exposure"])


def exposure_line(inp: WeekInputs) -> tuple[str, bool]:
    entries = entered_gpp(inp)
    if entries:
        own = {
            str(r.get("Name", "")): num(r.get("GppOwn"))
            for r in inp.edge or []
            if num(r.get("GppOwn")) is not None
        }
        v = expo.view(entries, own)
        return (
            f"this week: {len(v['over'])} overweights, {len(v['under'])} underweights over {len(entries)} "
            "GPP entries",
            False,
        )
    closed = (inp.exposure_close or {}).get("actual") or {}
    if closed.get("entries"):
        return (
            f"Week {inp.exposure_close['week']} vs actual: {len(closed['over'])} overweights, "
            f"{len(closed['under'])} underweights"
        ), False
    return "not run yet", False


# -- 9. how good are our numbers
# --------------------------------------------------------------------------------


def numbers_section(inp: WeekInputs) -> Section:
    sid, title = NUMBERS_ID, "How good are our numbers"
    p = inp.numbers
    if not p:
        return Section(
            sid,
            title,
            not_run("No closed week has been reviewed", say="close the week", command="dfs week review"),
            TRUST["numbers"],
            ran=False,
        )
    body = [
        '<p class="note">Every trust chip on this page links here. <b>Proven</b>: beat its baseline in Model '
        "Check. <b>Shadow</b>: scored, no verdict yet. <b>Untested</b>: not scored against results. "
        "<b>Data</b>: a feed or arithmetic, not a prediction.</p>"
    ]
    proj = p.get("projection") or []
    body.append(
        "<h4>CalPts against TFFB, average error by week "
        f"{chip_html(PROVEN, 'Model Check: CalPts refit on earlier weeks only.')}</h4>"
    )
    if proj:
        rows = [
            [
                str(r["week"]),
                f"{r['n']:,}",
                f"<b>{r['calpts']:.2f}</b>" if r["calpts"] < r["tffb"] else f"{r['calpts']:.2f}",
                f"<b>{r['tffb']:.2f}</b>" if r["tffb"] < r["calpts"] else f"{r['tffb']:.2f}",
                f"{r['tffb'] - r['calpts']:+.2f}",
            ]
            for r in proj
        ]
        body.append(
            table(
                ["Week", "Players", "CalPts", "TFFB", "CalPts better by"],
                rows,
                how="calibration.backtest_by_week: each week's average absolute error against the actual DK "
                "points, CalPts refit on the earlier weeks only.",
            )
        )
        body.append(
            note(
                "Average absolute error in DK points (lower is better); each week CalPts was refit on the "
                "weeks before it only."
            )
        )
    else:
        body.append(note("No scored week yet."))
    cash = p.get("cash") or {}
    body.append(
        "<h4>Cash % shown against the cash rate "
        f"{chip_html(SHADOW, 'The simulator behind Cash %: shown against what happened.')}</h4>"
    )
    if cash.get("all"):
        a = cash["all"]
        rows = [[str(w["week"]), str(w["n"]), e(pct(w["shown"])), e(pct(w["cashed"]))] for w in cash["weeks"]]
        rows.append(
            ["<b>All</b>", f"<b>{a['n']}</b>", f"<b>{pct(a['shown'])}</b>", f"<b>{pct(a['cashed'])}</b>"]
        )
        body.append(
            table(
                ["Week", "Cash builds", "Cash % shown (avg)", "Cashed"],
                rows,
                how="data/contests/cash_shown.csv: the Cash % the builds showed before lock against the "
                "share of them that cleared the real double-up line.",
            )
        )
        gap = a["shown"] - a["cashed"]
        if abs(gap) >= 0.1:
            body.append(
                f'<p class="flag bad"><b>Cash % ran {abs(gap) * 100:.0f} points '
                f"{'high' if gap > 0 else 'low'}: "
                f"shown {pct(a['shown'])}, {pct(a['cashed'])} cashed over {a['n']} builds.</b> Few weeks: "
                "read n.</p>"
            )
        body.append(
            note(
                "Every distinct cash build of the week (any round), its nine actual scores against that "
                "week's double-up cash line."
            )
        )
    else:
        body.append(note("No closed week has both saved cash builds and double-up standings yet."))
    sig = p.get("signals") or []
    body.append("<h4>Each signal's record (Model Check)</h4>")
    if sig:
        rows = [
            [
                e(r["signal"]),
                str(r["n"]) + (" (thin)" if r["thin"] else ""),
                e(pct(r["hit"])),
                "-" if r["vs_cal"] is None else f"{r['vs_cal']:+.1f}",
                chip_html(
                    r["trust"], "FADE↓ is Proven in Model Check; the others are scored, no verdict yet."
                ),
            ]
            for r in sig
        ]
        body.append(
            table(
                ["Signal", "Fired (n)", "Hit vs CalPts", "Actual - CalPts", "Trust"],
                rows,
                how="Model Check's signal record: every time a chip fired, whether the player beat or missed "
                "CalPts the way it pointed.",
            )
        )
        body.append(
            note(
                "Hit = the player went the way the signal points, against CalPts. A thin sample is under 30."
            )
        )
    else:
        body.append(note("No signal table yet (`dfs results update`)."))
    return Section(sid, title, "".join(body), TRUST["numbers"])


def numbers_line(inp: WeekInputs) -> tuple[str, bool]:
    p = inp.numbers
    if not p:
        return "not run yet", False
    a = (p.get("cash") or {}).get("all")
    return nums.headline(p) or "no scores yet", bool(a and a["shown"] - a["cashed"] >= 0.1)


# -- 10. salary watch
# -----------------------------------------------------------------------------------------


def salary_view(inp: WeekInputs, log: dict[str, list[dict]]) -> dict:
    return sw.watch(inp.edge or [], inp.salary_prev, log)


def swap_html(o: dict) -> str:
    """`sc.swap_text` with every name linked (drawer, pool chip)."""
    into = ", ".join(f"{pn(n)} {pts:.1f}" for n, pts in o["into"])
    out = ", ".join(f"{pn(n)} {pts:.1f}" for n, pts in o["out"])
    return f"in {into}; out {out}"


def salary_section(inp: WeekInputs, log: dict[str, list[dict]]) -> Section:
    sid, title = "salaries", "Salary watch"
    if not inp.edge:
        return Section(
            sid,
            title,
            not_run("Nothing is synced", say="sync", command="dfs sync"),
            TRUST["salaries"],
            ran=False,
        )
    v = salary_view(inp, log)
    if not v["found"]:
        return Section(
            sid,
            title,
            not_run(f"Last week's DraftKings salaries are not saved ({inp.salary_prev_note or 'no file'})"),
            TRUST["salaries"],
            ran=False,
        )

    by_key = {(str(r.get("Name", "")), str(r.get("Team", ""))): r for r in inp.edge}

    def salary_add(m: dict) -> str:
        """Cheaper with the same job and not in the pool: one click adds him (Cash: a salary-saver)."""
        r = by_key.get((str(m["name"]), str(m["team"])))
        if r is None or not r.get("Id") or inp.pool.get(str(m["name"])):
            return ""
        return " " + add_button(r["Id"], "Cash")

    def rows(moves: list[dict]) -> list[list[str]]:
        return [
            [
                pn(m["name"], m["team"]) + salary_add(m),
                e(m["position"]),
                e(m["team"]),
                e(_money(m["salary"])),
                e(_money(m["before"])),
                f"<b>{m['change']:+,.0f}</b>",
                e(m["role"] or ("moved" if m.get("logged", True) else "no game log")),
            ]
            for m in moves
        ]

    heads = ["Player", "Pos", "Team", "Now", "Last week", "Change", "Last 3 games"]
    body = [
        f"<p>DraftKings' salary against last week's main slate ({v['matched']} players on both): a drop of "
        f"${sw.DROP}+ is flagged when his role held steady (snaps, or targets, within 10% over his last "
        "three "
        "games).</p>"
    ]
    if v["flagged"]:
        body.append(
            "<h4>Cheaper, same job</h4>" + table(heads, rows(v["flagged"]), cls="stickyname", how=SALARY_HOW)
        )
    else:
        body.append(note("No $300+ drop with a steady role this week."))
    if v["other_drops"]:
        body.append(
            "<details><summary>Other $300+ drops (the role moved, or no three-game log)</summary>"
            + table(heads, rows(v["other_drops"]), cls="stickyname", how=SALARY_HOW)
            + "</details>"
        )
    return Section(sid, title, "".join(body), TRUST["salaries"])


def salary_line(inp: WeekInputs, log: dict[str, list[dict]]) -> tuple[str, bool]:
    if not inp.edge or not inp.salary_prev:
        return "not run yet", False
    v = salary_view(inp, log)
    first = ", ".join(f"{m['name']} {m['change']:+,.0f}" for m in v["flagged"][:2])
    return (f"{len(v['flagged'])} cheaper with the same job" + (f" ({first})" if first else "")), False


__all__ = [
    "UNTESTED",
    "exposure_section",
    "field_review_section",
    "numbers_section",
    "salary_section",
    "scorecard_html",
]


# -- the Close view (blueprint Phase D) ---------------------------------------------------------------------

CLOSE_ID = "close-card"
TOO_FEW_WEEKS = 12  # under this, "you against the build" says it is too few weeks to call
STATUS_CLASS = {sc.PROVEN: "ok", sc.NOT_PROVEN: "", sc.OVERSTATED: "warn", sc.NEW: "new"}
TAG_CLASS = {"you had him": "had", "a choice": "choice", "variance": "variance"}
REASON_LABELS = {
    "projection": "Projection",
    "news": "News",
    "source": "A source",
    "gut": "Gut",
    "price": "Price",
    "correlation": "Correlation",
    "ownership": "Ownership",
}
CLOSE_CSS = """
#close-card .line1{font-size:16px;line-height:1.5;padding:10px 14px;border:1px solid var(--line);
border-radius:8px;background:var(--card);margin:0 0 14px}
#close-card .bar{display:inline-block;vertical-align:middle;width:90px;height:6px;border-radius:3px;
background:var(--line);margin-left:6px;position:relative;overflow:hidden}
#close-card .bar i{position:absolute;left:0;top:0;bottom:0;background:var(--accent)}
#close-card .bar.non i{background:var(--dim)}
.tagchip{display:inline-block;font-size:11.5px;padding:1px 7px;border-radius:9px;border:1px solid var(--line)}
.tagchip.had{color:var(--ok)}.tagchip.choice{color:var(--warn)}.tagchip.variance{color:var(--dim)}
.vsdef{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:10px;margin:6px 0 14px}
.vsdef div{border:1px solid var(--line);border-radius:8px;padding:8px 12px;background:var(--card)}
.vsdef b{display:block;font-size:20px}.vsdef small{text-transform:uppercase;font-size:11px;color:var(--dim)}
td.status.ok{color:var(--ok);font-weight:600}td.status.warn{color:var(--warn);font-weight:600}
td.status.new{color:var(--accent);font-weight:600}
.onething{border-left:4px solid var(--accent);padding:10px 14px;background:var(--card);font-size:15.5px;
margin:6px 0 10px}
.reasons button{font-size:11.5px;padding:1px 7px;border-radius:9px;border:1px solid var(--line);
background:none;cursor:pointer;margin:1px}.reasons button.on{background:var(--accent);color:#fff}
@media(max-width:760px){.vsdef{grid-template-columns:1fr}}
"""


def _signed(x: float | None, nd: int = 1, unit: str = "") -> str:
    return "-" if x is None else f"{x:+.{nd}f}{unit}"


def _bar(share: float, cls: str = "") -> str:
    w = max(0.0, min(1.0, share)) * 100
    return f'{share * 100:.0f}%<span class="bar {cls}"><i style="width:{w:.0f}%"></i></span>'


def close_tiles(c: dict) -> list[tuple[str, str, str, str]]:
    """(label, value, note, tone) for the four Close tiles."""
    out = []
    edge = c.get("edge") or {}
    this = edge.get("this_week")
    if this and this.get("edge") is not None:
        note_ = (
            f"Week {c['week']} vs the four-source consensus, out-projected {this['pct'] * 100:.0f}% of the "
            f"double-up field; {edge.get('n_weeks', 0)}-week mean {_signed(edge.get('season_edge'))} pts"
            + (
                f" ({edge['season_pct'] * 100:.0f}th percentile)"
                if edge.get("season_pct") is not None
                else ""
            )
            + ". A tripwire, not proof it pays."
        )
        out.append(("Edge at lock", f"{this['edge']:+.1f} pts", note_, "warn" if this["edge"] <= 0 else ""))
    else:
        out.append(
            (
                "Edge at lock",
                "-",
                f"no lock-time snapshot or double-up standings for Week {c['week']}; season mean "
                f"{_signed(edge.get('season_edge'))} pts over {edge.get('n_weeks', 0)} weeks",
                "",
            )
        )
    av = c.get("avoidable") or {}
    this_av = av.get("week") if av.get("week") is not None else "no record"
    n_av = int(av.get("weeks") or 0)
    out.append(
        (
            "Avoidable errors, season",
            str(av.get("season", 0)),
            f"{av.get('what', 'blank entries')}: {this_av} in Week {c['week']}; "
            f"{n_av} week{'s' if n_av != 1 else ''} with entries recorded",
            "warn" if av.get("week") else "",
        )
    )
    gap = c.get("calibration_gap")
    cal = next((r for r in c.get("scorecards") or [] if r.get("model") == "Cash % calibration"), {})
    out.append(
        (
            "Calibration gap",
            _signed(None if gap is None else gap * 100, 1, " pts"),
            f"cash chance shown minus cashed; {cal.get('season', '')}",
            "warn" if gap is not None and gap > 0.10 else "",
        )
    )
    pctl = c.get("percentile") or {}
    this_p = pctl.get(str(c["week"]))
    out.append(
        (
            "Cash percentile in field",
            "-" if this_p is None else f"{this_p * 100:.0f}th",
            "share of the double-up field beaten, weeks "
            + " · ".join(f"{w}: {v * 100:.0f}" for w, v in pctl.items())
            + " (50 = the median)",
            "",
        )
    )
    return out


def _decision_log(inp: WeekInputs, live: bool) -> str:
    from dfs import decisions_log

    items = inp.decisions_log or []
    if not items:
        body = note(
            "No decisions recorded this week (the page records Swap / Keep / Later, open spots, Play / Fade, "
            "stacks and pivots)."
        )
    else:
        rows = []
        for d in items:
            chosen = (d.get("chosen") or {}).get("name") or "nobody named"
            was = (d.get("default_at_time") or {}).get("name") or ""
            dis = "" if live else " disabled"
            pills = "".join(
                f'<button type="button" class="{"on" if d.get("reason") == r else ""}" '
                f'data-id="{e(d["id"])}" data-reason="{e(r)}"{dis}>{e(lab)}</button>'
                for r, lab in REASON_LABELS.items()
            )
            rows.append(
                [
                    e(str(d.get("at", ""))[11:16] + " " + str(d.get("state", ""))),
                    e(str(d.get("kind", "")).replace("_", " ")),
                    e(str(d.get("lineup") or "")) + (f" {e(d['slot'])}" if d.get("slot") else ""),
                    e(chosen)
                    + (f' <span class="dim">(was {e(was)})</span>' if was and was != chosen else ""),
                    f'<span class="reasons">{pills}</span>',
                ]
            )
        body = table(
            ["When", "Kind", "Lineup", "Chosen", "Why (one tap)"],
            rows,
            how="every decision the page recorded this week, undone ones left out; a reason is a new line, "
            "the latest wins",
            src=("data/week/decisions.jsonl",),
        )
    path = decisions_log.PATH
    return (
        f'<details class="declog" id="decision-log"><summary>Decision log ({len(items)})</summary>{body}'
        f'<p class="note">Export: the file itself, <code>{e(str(path))}</code> (one JSON object per line, '
        "append-only).</p></details>"
    )


def close_section(inp: WeekInputs, live: bool = False) -> Section:
    """The Close view's one screen (`close` input, `close_view.payload`)."""
    from dfs import close_view, week_money

    c = inp.close
    if not c:
        return Section(
            CLOSE_ID,
            "The week, closed",
            not_run("the close's review", "dfs week review", "close the week") + _decision_log(inp, live),
            ran=False,
        )
    parts = [f'<p class="line1">{e(close_view.week_line(c))}</p>']
    rows = [
        [
            pn(d["name"]) + f' <span class="dim">{e(d.get("position", ""))}</span>',
            _bar(float(d["in_cashers"])),
            _bar(float(d["in_non_cashers"]), "non"),
            ("-" if d.get("cash_own") is None else pct(d["cash_own"])) + " / " + pct(d.get("field")),
            f'<span class="tagchip {TAG_CLASS.get(d["tag"], "")}">{e(d["tag"])}</span> '
            f'<span class="dim">{e(d.get("detail", ""))}</span>',
        ]
        for d in c.get("decided") or []
    ]
    parts.append('<h3 class="sub">What decided it</h3>')
    parts.append(
        table(
            ["Player", "In cashers", "In non-cashers", "Cash own: predicted / actual", "Tag"],
            rows,
            how="the cash post-mortem's five biggest casher / non-casher gaps in the week's double-ups; you "
            "had him = in a cash lineup you entered, a choice = in a cash build or your pool, variance = "
            "neither",
            src=("data/contests/cash_postmortem.csv", "data/entered/"),
        )
        if rows
        else note("No double-up standings for this week, so nothing to separate cashers from the rest.")
    )
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
    parts.append(f'<h3 class="sub">You against {e(vs.get("against", "the nearest build"))}</h3>')
    parts.append(
        '<div class="vsdef">'
        + fig("This week", wk)
        + fig(f"Season to date ({weeks} week{'s' if weeks != 1 else ''})", ss)
        + "</div>"
        + (
            f'<p class="note"><span class="tagchip choice">{weeks} weeks: too few to call</span> under '
            f"{TOO_FEW_WEEKS} weeks a total like this is mostly the players' variance.</p>"
            if weeks < TOO_FEW_WEEKS
            else ""
        )
    )
    cards = c.get("scorecards") or []
    parts.append('<h3 class="sub">Scorecards</h3>')
    parts.append(
        table(
            ["Model", "This week", "Season", "Against its simple baseline", "Weeks", "Status"],
            [
                [
                    e(r["model"]),
                    e(r["week"]),
                    e(r["season"]),
                    e(r["baseline"]),
                    str(r["weeks"]),
                    f'<span class="status {STATUS_CLASS.get(r["status"], "")}">{e(r["status"])}</span>',
                ]
                for r in cards
            ],
            how="each model against its simple baseline by week, weeks as the unit: Proven = ahead by 2 SE, "
            "New = under 3 weeks, Overstated = the cash chance shown above the cash rate by 2 SE",
            src=("numbers", "field_own", "data/contests/cash_shown.csv", "data/contests/edge_at_lock.csv"),
        )
    )
    # the ledger (Phase B's section) under its own id: the fold below keeps `#ledger`
    parts.append(dataclasses.replace(week_money.ledger_section(inp), id="close-ledger").html())
    tiles = "".join(
        f'<div class="tile {e(tone)}"><small>{e(label)}</small><b>{e(value)}</b><span>{e(n)}</span></div>'
        for label, value, n, tone in close_tiles(c)
    )
    parts.append(
        f'<section class="strip close-tiles" aria-label="The close in four numbers">{tiles}</section>'
    )
    parts.append('<h3 class="sub">The one thing worth changing</h3>')
    parts.append(f'<p class="onething">{e(c.get("one_thing") or "Nothing.")}</p>')
    parts.append(_decision_log(inp, live))
    parts.append(note("Information only: nothing here blocks, warns or writes a sheet."))
    return Section(CLOSE_ID, f"Week {c['week']}, closed", "".join(parts), trust=TRUST["numbers"])
