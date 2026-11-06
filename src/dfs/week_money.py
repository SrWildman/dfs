"""The Week page's Plan K1 sections: bankroll health, the Sunday inactives check with its pivots, the
consensus lineup and copies, and the entry plan; plus the practice-report text shown beside every Q / D tag.

Each builder takes a `WeekInputs` and returns a `week_page.Section` (or text); the math lives in
`bankroll_health`, `inactives`, `consensus`, `entry_plan` and `practice`. Read-only, like the rest of the
page: a section whose input is missing says "not run yet" and what produces it.
"""

from __future__ import annotations

from dfs import consensus as cons
from dfs import entry_plan as ep
from dfs import inactives as ina
from dfs import practice as prac
from dfs.week_inputs import WeekInputs, parse_time
from dfs.week_page import (
    DATA,
    PROVEN,
    SHADOW,
    UNTESTED,
    Section,
    bullets,
    chip_html,
    dollars,
    e,
    not_run,
    note,
    pct,
    table,
)

TRUST: dict[str, tuple[str, str]] = {
    "bankroll": (DATA, "Your own Season and Bankroll tabs: what happened, not a prediction."),
    "stake_guide": (
        DATA,
        "Your own Weekly Budget (or your [stakes] rules when set), split by tier the way the contest plan "
        "splits it: arithmetic on your settings, not a model.",
    ),
    "inactives": (DATA, "Injury statuses from the last sync and kickoff times: a feed, not a model."),
    "pivots_cash": (PROVEN, "Cash pivots are ranked by CalPts, which beat TFFB's projection in Model Check."),
    "pivots_gpp": (
        UNTESTED,
        "GPP pivots are ranked by Leverage (ceiling rank minus ownership rank), which has no measured edge "
        "yet.",
    ),
    "consensus": (
        SHADOW,
        "The duplicate estimate is fitted on your own standings and scored week by week out of sample; the "
        "error is shown below. Double-up copies are a rough guide.",
    ),
    "entry_plan": (DATA, "Checks on what you entered against your own plan: facts, not predictions."),
    "entry_hint": (
        UNTESTED,
        "The same rule `dfs entries fill` uses for its grey Suggested hint (best Cash % / best Top 1%). No "
        "contest-by-contest ranking has cleared the contest retro's bar.",
    ),
    "practice": (DATA, "nflverse's practice reports as our syncs saw them: a feed."),
}


def chip(key: str) -> str:
    label, why = TRUST[key]
    return chip_html(label, why)


# -- practice -----------------------------------------------------------------------------------------------


def practice_by_id(inp: WeekInputs) -> dict[str, str]:
    """EdgeRaw Id -> `DNP Wed · Limited Thu` for every player the week's snapshots report."""
    return {pid: t.text() for pid, t in prac.by_edge_id(inp.practice, inp.edge or []).items()}


def practice_text(inp: WeekInputs, row: dict) -> str:
    if inp.practice is None:
        return ""
    trend = inp.practice.for_player(row.get("Name"), row.get("Team"))
    return trend.text() if trend is not None else ""


def practice_cell(inp: WeekInputs, row: dict) -> str:
    text = practice_text(inp, row)
    if not text:
        return (
            '<span class="dim">no report seen</span>'
            if inp.practice is not None
            else '<span class="dim">-</span>'
        )
    trend = inp.practice.for_player(row.get("Name"), row.get("Team"))
    arrow = {"up": " ↑", "down": " ↓"}.get(trend.direction, "")
    return f'<span class="prac">{e(text)}{e(arrow)}</span>'


# -- 1. bankroll health
# ---------------------------------------------------------------------------------------


def cents(value: float | None) -> str:
    """Money with cents (the bankroll's small numbers: a $7.24 guide must not read $7)."""
    from dfs.bankroll_health import money

    return money(value)


SERIES_CLASS = {"dfs": "s1", "betting": "s2", "other": "s3", "total": "st"}


def bankroll_chart(curve: list[dict], *, width: int = 640, height: int = 220) -> str:
    """Cumulative net by week, one line per series (NFL DFS, Betting, Other sports, Total), a zero line, the
    week ticks, direct labels at the line ends and a native tooltip on every point."""
    from dfs.bankroll_health import SERIES

    if len(curve) < 1:
        return ""
    left, right, top, bottom = 52, 96, 12, 26
    weeks = [c["week"] for c in curve]
    values = [c["cum"][k] for c in curve for k, _ in SERIES] + [0.0]
    lo, hi = min(values), max(values)
    pad = max((hi - lo) * 0.08, 5.0)
    lo, hi = lo - pad, hi + pad
    w0, w1 = min(weeks), max(weeks)

    def x(week: float) -> float:
        return left + (week - w0) / max(w1 - w0, 1) * (width - left - right)

    def y(v: float) -> float:
        return top + (hi - v) / (hi - lo) * (height - top - bottom)

    from dfs.week_charts import _nice_ticks

    parts = [
        f'<svg class="chart bank" viewBox="0 0 {width} {height}" role="img" aria-label="Cumulative net by '
        'week">'
    ]
    for t in _nice_ticks(lo, hi, 4):
        parts.append(f'<line class="grid" x1="{left}" x2="{width - right}" y1="{y(t):.1f}" y2="{y(t):.1f}"/>')
        parts.append(
            f'<text class="tick" x="{left - 6}" y="{y(t) + 4:.1f}" text-anchor="end">{e(dollars(t))}</text>'
        )
    parts.append(f'<line class="zero" x1="{left}" x2="{width - right}" y1="{y(0):.1f}" y2="{y(0):.1f}"/>')
    for wk in weeks:
        parts.append(
            f'<text class="tick" x="{x(wk):.1f}" y="{height - 8}" text-anchor="middle">Wk {wk}</text>'
        )
    ends = []
    for key, label in SERIES:
        pts = [(x(c["week"]), y(c["cum"][key]), c) for c in curve]
        path = " ".join(f"{'M' if i == 0 else 'L'}{px:.1f},{py:.1f}" for i, (px, py, _) in enumerate(pts))
        cls = SERIES_CLASS[key]
        parts.append(f'<path class="line {cls}" d="{path}"/>')
        for px, py, c in pts:
            parts.append(
                f'<circle class="pt {cls}" cx="{px:.1f}" cy="{py:.1f}" r="4"><title>{e(label)}, Week '
                f"{c['week']}: "
                f"{e(dollars(c['cum'][key]))} to date ({e(dollars(c['net'][key]))} that "
                "week)</title></circle>"
            )
        ends.append([pts[-1][1], label, cls])
    ends.sort()
    for i in range(1, len(ends)):  # keep the end labels 14px apart
        ends[i][0] = max(ends[i][0], ends[i - 1][0] + 14)
    for py, label, _cls in ends:
        parts.append(f'<text class="lbl end" x="{width - right + 6}" y="{py + 4:.1f}">{e(label)}</text>')
    parts.append("</svg>")
    legend = (
        '<p class="legend">'
        + "".join(f'<span class="lkey {SERIES_CLASS[k]}"></span>{e(label)}' for k, label in SERIES)
        + " (cumulative net, from the Season tab)</p>"
    )
    return legend + "".join(parts)


def guide_of(b: dict) -> dict:
    """The stake guide to show: the saved one when it is Sam's `[stakes]` rules, else his Weekly Budget worked
    out here from the saved bankroll and budget (so an input saved before the guide became the Weekly Budget,
    with the old proposal in it, never shows)."""
    g = b.get("guide") or {}
    if g.get("source") == "your rules":
        return g
    from dfs import bankroll_health as bh
    from dfs.config import StakesConfig

    guide = bh.stake_guide(StakesConfig(), b.get("bankroll"), b.get("budget"))
    return {
        "source": guide.source,
        "pct_week": guide.pct_week,
        "pct_entry": None,
        "week": guide.week,
        "entry": None,
        "tiers": guide.tiers,
        "lines": guide.lines,
    }


def bankroll_section(inp: WeekInputs) -> Section:
    trust = TRUST["bankroll"]
    b = inp.bankroll
    if not b:
        return Section(
            "bankroll",
            "Bankroll health",
            not_run(
                "The Season and Bankroll tabs have not been read this week (`dfs sync`, `dfs contests plan` "
                "and "
                "`dfs bankroll stakes` save them)",
                say="sync",
                command="dfs sync",
            ),
            trust,
            ran=False,
        )
    g = guide_of(b)
    body = []
    head = [
        f"<b>Bankroll {e(cents(b['bankroll']))}</b> at the start of the week"
        if b.get("bankroll") is not None
        else "Bankroll not read"
    ]
    if b.get("budget") is not None:
        head.append(f"Weekly Budget {e(cents(b['budget']))}")
        head.append(
            f"spent so far {e(cents(b.get('cost') or 0))}, {e(cents(b.get('budget_left') or 0))} left"
        )
    if b.get("season_left") is not None:
        head.append(f"season deposit budget {e(pct(b['season_left']))} left")
    body.append("<p>" + " &middot; ".join(head) + ".</p>")
    if b.get("over") and g.get("source") == "your rules":
        body.append(f'<p class="flag bad"><b>{e(b["over"])}</b></p>')
    source = g.get("source")
    label = {"your rules": "your [stakes] rules", "Weekly Budget": "your Weekly Budget"}.get(source, "")
    body.append(f"<h4>Stake guide ({e(label)}) {chip('stake_guide')}</h4>")
    if g.get("week") is None:
        body.append(
            note(" ".join(g.get("lines") or ["No guide: the Weekly Budget is not on the Bankroll tab."]))
        )
    else:
        from dfs import contest_tiers as ct

        budget = b.get("budget")
        share = f" ({g['pct_week']:g}% of the bankroll)" if g.get("pct_week") is not None else ""
        if source == "your rules":
            plan = ct.plan_dollars(budget) if budget else None
            rows = []
            for tier in (ct.A, ct.B, ct.C):
                guide = (g.get("tiers") or {}).get(tier)
                mine = plan.dollars(tier) if plan else None
                over = mine is not None and guide is not None and mine > guide + 1e-9
                rows.append(
                    [
                        f"<b>Tier {e(tier)}</b>",
                        e(cents(guide)) if guide is not None else "-",
                        (
                            f'<span class="{"bad" if over else ""}">{e(cents(mine))}</span>'
                            if mine is not None
                            else "-"
                        ),
                    ]
                )
            rows.append(
                [
                    "<b>The week</b>",
                    f"<b>{e(cents(g['week']))}</b>{e(share)}",
                    f"<b>{e(cents(budget))}</b>" if budget is not None else "-",
                ]
            )
            heads = ["", "Your rules allow", "Your Weekly Budget's plan"]
        else:
            rows = [[f"<b>Tier {e(t)}</b>", e(cents(v))] for t, v in (g.get("tiers") or {}).items()]
            rows.append(["<b>The week</b>", f"<b>{e(cents(g['week']))}</b>{e(share)}"])
            heads = ["", "Your Weekly Budget, by tier"]
        if g.get("entry") is not None:
            rows.append(["Largest entry", f"{e(cents(g['entry']))} ({g['pct_entry']:g}% of the bankroll)"])
        body.append(table(heads, rows))
        body.append(note(" ".join(g.get("lines") or [])))
    curve = b.get("curve") or []
    if curve:
        body.append("<h4>The bankroll by week</h4>")
        body.append(bankroll_chart(curve))
        rows = []
        for c in curve:
            share = c.get("spend_share")
            rows.append(
                [
                    e(c["week"]),
                    *(e(cents(c["net"][k])) for k in ("dfs", "betting", "other", "total")),
                    e(cents(c["ending"])) if c.get("ending") is not None else "-",
                    e(cents(c["risked"])),
                    f'<span class="{"bad" if share and share > 1 else ""}">{e(pct(share))}</span>'
                    if share is not None
                    else "-",
                ]
            )
        body.append(
            "<details><summary>The weeks as a table</summary>"
            + table(
                [
                    "Week",
                    "NFL DFS net",
                    "Betting net",
                    "Other net",
                    "Total net",
                    "Ending bankroll",
                    "Spent",
                    "Spent / bankroll",
                ],
                rows,
            )
            + "</details>"
        )
        last = curve[-1]
        if last.get("spend_share") is not None:
            body.append(
                note(
                    f"Week {last['week']}: spent {cents(last['risked'])}, {pct(last['spend_share'])} of the "
                    f"{cents(last['start'])} bankroll it started with."
                )
            )
    at = parse_time(b.get("saved_at"))
    from dfs import week_today

    return Section(
        "bankroll",
        "Bankroll health",
        "".join(body),
        trust,
        asof=(f"from {b.get('sheet') or 'the sheet'}, {week_today.when(inp.now, at)}" if at else ""),
    )


def bankroll_line(inp: WeekInputs) -> tuple[str, bool]:
    b = inp.bankroll or {}
    g = guide_of(b)
    if not b:
        return "not run yet", False
    text = f"Bankroll {cents(b['bankroll'])}" if b.get("bankroll") is not None else "Bankroll not read"
    if b.get("budget") is not None and g.get("source") == "your rules" and g.get("week") is not None:
        text += f"; Weekly Budget {cents(b['budget'])} vs your rules {cents(g['week'])}"
    elif b.get("budget") is not None:
        share = f" ({g['pct_week']:g}% of it)" if g.get("pct_week") is not None else ""
        text += f"; Weekly Budget {cents(b['budget'])}{share}"
    return text, bool(b.get("over")) and g.get("source") == "your rules"


# -- 2. Sunday inactives
# ---------------------------------------------------------------------------------------


def inactive_sources(inp: WeekInputs) -> tuple[list[ina.Source], str]:
    if inp.entered and inp.entered.get("lineups"):
        return ina.sources_from_entered(inp.entered), "the lineups you entered (`dfs entries record`)"
    lineups = (inp.sheet_lineups or {}).get("lineups") or []
    if lineups:
        return ina.sources_from_sheet(lineups), "the Lineups tab, as the last Cockpit refresh read it"
    return [], ""


def checks(inp: WeekInputs) -> list[ina.LineupCheck] | None:
    sources, _ = inactive_sources(inp)
    if not inp.edge or not sources:
        return None
    return ina.check(inp.edge, sources, inp.now)


def pivot_text(p: ina.Pivot, by: str) -> str:
    value = (
        f"Lev {p.leverage:+.0f}"
        if by == "Leverage" and p.leverage is not None
        else f"{p.calpts:.1f} pts"
        if p.calpts
        else ""
    )
    return f"{p.name} ({p.position} {p.team}, ${p.salary:,}, {value}, {p.kickoff_text})"


def inactives_section(inp: WeekInputs) -> Section:
    trust = TRUST["inactives"]
    sources, where = inactive_sources(inp)
    if not inp.edge:
        return Section(
            "inactives",
            "Sunday inactives and pivots",
            not_run("Nothing is synced", say="sync", command="dfs sync"),
            trust,
            ran=False,
        )
    if not sources:
        return Section(
            "inactives",
            "Sunday inactives and pivots",
            not_run(
                "No entered lineups yet: record them at lock (or the Cockpit reads the Lineups tab on the "
                "next "
                "sync)",
                say="record my entries",
                command="dfs entries record",
            ),
            trust,
            ran=False,
        )
    found = ina.check(inp.edge, sources, inp.now)
    body = [note(f"Checked {len(sources)} lineups: {where}. Only players whose game has not started.")]
    if not found:
        body.append('<p class="ok">No OUT, Doubtful or Questionable player in any of them.</p>')
    for lc in found:
        left = (
            ""
            if lc.left is None
            else f", ${lc.left:,} left under the cap"
            if lc.left >= 0
            else f", ${-lc.left:,} over the cap"
        )
        kind_chip = chip("pivots_gpp") if lc.source.kind == "gpp" else chip("pivots_cash")
        body.append(f"<h4>{e(lc.source.label)} ({e(lc.source.kind)}{e(left)}) {kind_chip}</h4>")
        rows = []
        for f in lc.flags:
            row = next((r for r in inp.edge if r.get("Name") == f.name), {"Name": f.name, "Team": f.team})
            tone = "bad" if f.status in ("OUT", "IR", "D") else "warn"
            pivots = (
                "<br>".join(e(pivot_text(p, f.ranked_by)) for p in f.pivots)
                or '<span class="dim">none fits</span>'
            )
            rows.append(
                [
                    f"<b>{e(f.name)}</b> {e(f.position)} {e(f.team)}",
                    f'<span class="{tone}">{e(ina.STATUS_WORD[f.status])}</span>',
                    e(f.slot),
                    e(f.kickoff_text),
                    practice_cell(inp, row),
                    pivots + f'<br><span class="dim">up to ${f.budget:,}; by {e(f.ranked_by)}</span>'
                    if f.budget is not None
                    else pivots,
                ]
            )
        body.append(
            table(["Player", "Status", "Slot", "Kickoff (ET)", "Practice", "Pivots (best first)"], rows)
        )
    body.append(
        note(
            "A pivot fits his slot (FLEX takes RB / WR / TE), costs at most his salary plus what the lineup "
            "leaves, "
            "is healthy and has not kicked off. Cash lineups rank by CalPts, GPP lineups by Leverage. Making "
            "the "
            "swap is yours, in DraftKings."
        )
    )
    if inp.practice is not None:
        body.append(note(inp.practice.note()))
    stamp = max(
        (parse_time(v.get("synced_at")) for v in inp.manifest.values() if v.get("synced_at")), default=None
    )
    from dfs import week_today

    return Section(
        "inactives",
        "Sunday inactives and pivots",
        "".join(body),
        trust,
        asof=f"as of the sync {week_today.when(inp.now, stamp)}" if stamp else "",
    )


def inactives_line(inp: WeekInputs) -> tuple[str, bool]:
    found = checks(inp)
    if found is None:
        return "not run yet", False
    n = sum(len(lc.flags) for lc in found)
    if not n:
        return "no Q / D / OUT in your lineups", False
    outs = sum(1 for lc in found for f in lc.flags if f.status in ("OUT", "IR", "D"))
    lineups = f"{len(found)} lineup{'' if len(found) == 1 else 's'}"
    return f"{n} flagged in {lineups} ({outs} OUT or Doubtful), pivots ready", True


# -- 3. consensus and copies
# ---------------------------------------------------------------------------------------


def _typed():  # noqa: ANN202
    try:
        from dfs import paths
        from dfs.contests import history

        return history.typed(history.read_log(paths.DATA_DIR / "contests" / "contest_log.csv"))
    except Exception:  # noqa: BLE001
        return None


def consensus_view(inp: WeekInputs) -> dict:
    from dfs import contest_tiers
    from dfs.sim import dup_calibration as dc

    if inp.entered and inp.entered.get("lineups"):
        lineups = cons.lineups_from_entered(inp.entered)
    else:
        lineups = cons.lineups_from_builds((inp.suggest or {}).get("builds") or [])
    typed = _typed()
    sizes = contest_tiers.history_by_series(typed) if typed is not None and len(typed) else {}
    typical = cons.typical_sizes(typed, inp.season) if typed is not None else {}
    models = {k: dc.model_from(inp.dup_calibration, k) for k in (cons.CASH, cons.GPP)}
    return cons.view(inp.edge, lineups, models=models, sizes=sizes, typical=typical)


def copies_text(value: float | None) -> str:
    """`10.2`, `0.3`, and `under 0.1` rather than a misleading `0.0`."""
    if value is None:
        return "-"
    return "under 0.1" if value < 0.05 else f"{value:,.1f}"


def calibration_lines(cal: dict | None) -> list[str]:
    if not cal:
        return []
    out = []
    names = {"cash": "Double-ups", "gpp": "GPPs"}
    for fmt in ("cash", "gpp"):
        s = (cal.get("summary") or {}).get(fmt)
        if not s:
            continue
        out.append(
            f"{names[fmt]} (Weeks {', '.join(map(str, s['weeks']))}, each scored on a fit without it): "
            "typical "
            f"error a factor of {2.718281828 ** s['log_rmse']:.1f} on an entry; the most-duplicated lineups "
            f"{s['top_observed']:,} copies seen against {s['top_predicted']:,.0f} predicted; your own "
            "entries "
            f"{s['mine_observed']:,} seen against {s['mine_predicted']:,.0f} predicted."
        )
    worst = [
        (h["week"], m["observed"], m["predicted"])
        for h in cal.get("held_out") or []
        if h["fmt"] == "cash"
        for m in h.get("mine") or []
    ]
    if worst:
        w = max(worst, key=lambda t: abs(t[1] - t[2]))
        out.append(
            f"Worst miss on your cash lineups: Week {w[0]}, {w[1]:,} copies seen, {w[2]:,.0f} predicted."
        )
    return out


def consensus_section(inp: WeekInputs) -> Section:
    trust = TRUST["consensus"]
    title = "Consensus lineup and copies"
    if not inp.edge:
        return Section(
            "consensus", title, not_run("Nothing is synced", say="sync", command="dfs sync"), trust, ran=False
        )
    v = consensus_view(inp)
    if v.get("pending"):
        return Section(
            "consensus",
            title,
            not_run(v.get("reason", "Not available"), say="sync", command="dfs sync"),
            trust,
            ran=False,
        )
    body = []
    for kind, label, own in (("cash", "Double-ups (CashOwn)", "CashOwn"), ("gpp", "GPPs (GppOwn)", "GppOwn")):
        c = (v.get("consensus") or {}).get(kind)
        if not c:
            body.append(f"<h4>{e(label)}</h4>" + note(f"{own} is not out yet."))
            continue
        copies = (
            f"; about {copies_text(c['copies'])} copies of it in a {c['entrants']:,}-entry field"
            if c.get("copies") is not None
            else ""
        )
        body.append(
            f"<h4>The field's lineup: {e(label)}</h4><p>{e(', '.join(c['names']))}</p>"
            + note(f"Total {own} {pct(c['own_sum'])}, salary ${c['salary']:,}{copies}.")
        )
    rows = []
    for lu in v.get("lineups") or []:
        for i, c in enumerate(lu["contests"]):
            shared = lu["shared"].get(c["kind"])
            copies = copies_text(c["copies"])
            flag = ' <span class="bad">5+ copies split a prize</span>' if c["flag"] else ""
            near = (
                ' <span class="ok">fine for cash</span>'
                if c["kind"] == "cash" and shared and shared >= 7
                else ""
            )
            rows.append(
                [
                    f"<b>{e(lu['label'])}</b>" if i == 0 else "",
                    e("double-up" if c["kind"] == "cash" else "GPP"),
                    e(f"{shared} of 9") + near if shared is not None else "-",
                    e(c["name"]),
                    e(f"{c['entrants']:,}") + f'<br><span class="dim">{e(c["size_from"])}</span>',
                    f"<b>{e(copies)}</b>{flag}",
                ]
            )
    if rows:
        body.append("<h4>Your lineups</h4>")
        body.append(
            table(
                [
                    "Lineup",
                    "Contest kind",
                    "Shares with the field's lineup",
                    "Contest",
                    "Field",
                    "Expected copies",
                ],
                rows,
            )
        )
    else:
        body.append(
            not_run(
                "No builds and no entered lineups yet", say="build me lineups", command="dfs lineups suggest"
            )
        )
    body.append(
        note(
            "Cash: sharing most of the field's lineup is fine, the cash line is the field's middle. GPP: "
            "copies "
            "split a prize, so a lineup expected at 5+ copies is flagged."
        )
    )
    cal = calibration_lines(inp.dup_calibration)
    if cal:
        body.append("<h4>How good is the copies estimate</h4>" + bullets([e(x) for x in cal]))
    else:
        body.append(
            not_run(
                "The copies estimate is not calibrated on your standings yet", command="dfs contests dupes"
            )
        )
    return Section("consensus", title, "".join(body), trust)


def consensus_line(inp: WeekInputs) -> tuple[str, bool]:
    if not inp.edge:
        return "not run yet", False
    try:
        v = consensus_view(inp)
    except Exception:  # noqa: BLE001
        return "not available", False
    if v.get("pending"):
        return "ownership not out yet", False
    flagged = sorted({lu["label"] for lu in v.get("lineups") or [] for c in lu["contests"] if c["flag"]})
    cash = (v.get("consensus") or {}).get("cash")
    text = f"double-up field's lineup at {pct(cash['own_sum'])} total CashOwn" if cash else "consensus built"
    if flagged:
        text += f"; {len(flagged)} GPP lineups at 5+ copies ({', '.join(flagged[:3])})"
    return text, bool(flagged)


# -- 4. entry plan
# ---------------------------------------------------------------------------------------------------


def plan_of(inp: WeekInputs) -> ep.Plan:
    from dfs import contest_tiers
    from dfs.week_page import weekly_budget

    typed = _typed() if inp.entered else None
    sizes = contest_tiers.history_by_series(typed) if typed is not None and len(typed) else {}
    settings = inp.config.get("contest_plan") or {}
    return ep.make(
        budget=weekly_budget(inp),
        builds=(inp.suggest or {}).get("builds") or [],
        record=inp.entered,
        history=sizes,
        showdown_max=float(settings.get("showdown_max", contest_tiers.DEFAULT_SHOWDOWN_MAX)),
        big_multi_max=float(settings.get("big_nmax_max", contest_tiers.DEFAULT_BIG_MULTI_MAX)),
    )


def entry_plan_section(inp: WeekInputs) -> Section:
    trust = TRUST["entry_plan"]
    title = "Entry plan"
    p = plan_of(inp)
    body = []
    if p.tiers:
        body.append(
            "<p>This week's money by tier: "
            + ", ".join(f"<b>Tier {t}</b> {e(dollars(v))}" for t, v in p.tiers.items())
            + f" (Weekly Budget {e(dollars(p.budget))}).</p>"
        )
    else:
        body.append(
            '<p class="notrun">No Weekly Budget yet, so there is no plan by tier (say <code '
            'class="say">"contest plan"</code>).</p>'
        )
    cash = [b["number"] for b in p.builds if b["kind"] == "cash"]
    gpp = [b["number"] for b in p.builds if b["kind"] == "gpp"]
    if p.builds:
        body.append(
            bullets(
                [
                    x
                    for x in (
                        f"Cash builds {', '.join(map(str, cash))} go in cash contests (Tier A)."
                        if cash
                        else "",
                        f"GPP builds {', '.join(map(str, gpp))} go in GPPs (Tier B, plus the Tier C lottery)."
                        if gpp
                        else "",
                    )
                    if x
                ]
            )
        )
    else:
        body.append(
            not_run("No builds saved for this week", say="build me lineups", command="dfs lineups suggest")
        )
    if not p.entered:
        body.append(
            not_run(
                "Nothing entered yet. After you enter, download DraftKings' entries file again and record "
                "it: the "
                "checks then run on every contest",
                say="record my entries",
                command="dfs entries record",
            )
        )
        return Section("entry-plan", title, "".join(body), trust, ran=bool(p.tiers or p.builds))
    body.append(
        "<h4>Checks</h4>"
        + bullets(
            [
                (f'<span class="ok">ok</span> {e(line)}' if ok else f'<span class="bad">{e(line)}</span>')
                for line, ok in p.checks
            ]
        )
    )
    rows = [
        [
            e(c.name),
            e(c.format),
            f"<b>{e(c.tier)}</b>"
            + (f'<br><span class="dim">{e(c.tier_reason)}</span>' if c.tier_reason else ""),
            e(dollars(c.fee)),
            e(f"{c.entries + c.blank}" + (f" of {c.entry_max}" if c.entry_max else "")),
            e(", ".join(sorted({label for label, _ in c.lineups})) or "-"),
            e(c.hint) or '<span class="dim">-</span>',
        ]
        for c in sorted(p.contests, key=lambda c: (c.tier, -c.fee, c.name))
    ]
    heads = ["Contest", "Format", "Tier", "Fee", "Entries", "Your lineups", "Suggested (hint)"]
    body.append(f"<h4>Contests</h4>{table(heads, rows)}")
    body.append(
        f"<p>{chip('entry_hint')} <b>Suggested (hint)</b> is the same rule <code>dfs entries fill</code> "
        "greys in: the cash "
        "build with the best Cash % for a cash contest, the GPP build with the best Top 1% for a GPP. No "
        "contest-by-contest ranking has cleared the contest retro's bar, so it is a hint only. You upload to "
        "DraftKings yourself.</p>"
    )
    if p.spent:
        body.append(
            note(
                "Spent by tier: "
                + ", ".join(f"{t} {dollars(v)}" for t, v in p.spent.items() if v)
                + (" (over: " + "; ".join(p.over) + ")" if p.over else "")
            )
        )
    at = parse_time((inp.entered or {}).get("recorded_at"))
    from dfs import week_today

    return Section(
        "entry-plan",
        title,
        "".join(body),
        trust,
        asof=f"entries recorded {week_today.when(inp.now, at)}" if at else "",
    )


def entry_plan_line(inp: WeekInputs) -> tuple[str, bool]:
    try:
        p = plan_of(inp)
    except Exception:  # noqa: BLE001
        return "not available", False
    if not p.entered:
        n = len(p.builds)
        return (f"{n} builds; nothing entered yet" if n else "no builds, nothing entered yet"), False
    bad = p.problems
    word = "problem" if len(bad) == 1 else "problems"
    return (
        f"{len(p.contests)} contests: " + (f"{len(bad)} {word} ({bad[0]})" if bad else "every check ok")
    ), bool(bad)
