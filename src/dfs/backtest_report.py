"""The text of a backtest: the ten-line summary the command prints, the full markdown report written to
`docs/research/backtest/`, and the `refresh_summary` dictionary `dfs research refresh` embeds.

Everything here reads a `backtest.Result` (or two, for `--compare`); nothing is computed that the Result's
`rows` do not hold, so a report can be rebuilt from a saved Result.
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

import pandas as pd

from dfs import backtest as bt

SHIP_BAR = (
    "A setting ships only if it beats today's by more than 2 standard errors of the paired difference in "
    "realized ROI, held out by week, AND wins in most weeks, with at least 4 weeks scored; fewer than that, "
    "or a win carried by one ticket, is 'not proven yet'"
)
MIN_WEEKS = 4
SE_BAR = 2.0

SIDES = {
    "backtest": "Builds, as entered",
    "any": "Builds, any build",
    "sam_actual": "Sam, as DraftKings paid",
    "sam": "Sam, on the same table",
}


def pct(x: float, places: int = 0, sign: bool = False) -> str:
    if x != x:
        return "-"
    return f"{x:+.{places}%}" if sign else f"{x:.{places}%}"


def money(x: float) -> str:
    return f"${x:,.0f}" if x == x else "-"


def side_rows(rows: pd.DataFrame, name: str, side: str, fmt: str | None = None) -> pd.DataFrame:
    """The rows behind one line of the table: `side` is a key of `SIDES` ("backtest" is `name`'s entered
    builds, "any" its every-build scope)."""
    if side == "any":
        d = rows[(rows["side"] == "backtest") & (rows["scope"] == "any")]
    elif side == "backtest":
        d = rows[(rows["side"] == "backtest") & (rows["scope"] == "entry")]
    else:
        d = rows[rows["side"] == side]
    return d if fmt is None else d[d["fmt"] == fmt]


def stats(d: pd.DataFrame) -> dict[str, float]:
    """Entries, dollars, ROI, cash rate, top-1% rate, the ROI without the single best ticket, and the ROI by
    week."""
    if d.empty:
        return {
            "entries": 0,
            "cost": 0.0,
            "paid": 0.0,
            "roi": float("nan"),
            "cash": float("nan"),
            "top1": float("nan"),
            "ex_top": float("nan"),
            "week_se": float("nan"),
            "weeks": 0,
        }
    cost = float(d["fee"].sum())
    paid = float(d["paid"].sum())
    by_week = d.groupby("week").apply(lambda g: g["paid"].sum() / g["fee"].sum() - 1.0, include_groups=False)
    _, se = bt.mean_se(by_week.to_list())
    best = d["paid"].idxmax()
    ex = d.drop(index=best)
    return {
        "entries": len(d),
        "cost": cost,
        "paid": paid,
        "roi": paid / cost - 1.0,
        "cash": float(d["cashed"].mean()),
        "top1": float(d["top1"].mean()),
        "ex_top": float(ex["paid"].sum() / ex["fee"].sum() - 1.0) if len(ex) else float("nan"),
        "week_se": se,
        "weeks": int(d["week"].nunique()),
    }


def table_lines(result: bt.Result, fmt: str | None = None) -> list[str]:
    """The comparison table for one format (None: all), one row per side."""
    head = f"{'':26}{'entries':>8}{'in':>8}{'out':>8}{'ROI':>8}{'cash':>7}{'top 1%':>8}"
    lines = [head + f"{'ROI w/o best ticket':>21}"]
    for side, label in SIDES.items():
        s = stats(side_rows(result.rows, result.name, side, fmt))
        if not s["entries"]:
            continue
        label = label if side not in ("backtest", "any") else f"{label} ({result.name})"
        cells = [
            f"{label[:26]:26}",
            f"{s['entries']:>8}",
            f"{money(s['cost']):>8}",
            f"{money(s['paid']):>8}",
            f"{pct(s['roi'], 0, True):>8}",
            f"{pct(s['cash']):>7}",
            f"{pct(s['top1'], 1):>8}",
            f"{pct(s['ex_top'], 0, True):>21}",
        ]
        lines.append("".join(cells))
    return lines


def week_roi(result: bt.Result, side: str) -> dict[int, float]:
    d = side_rows(result.rows, result.name, side)
    return {int(w): g["paid"].sum() / g["fee"].sum() - 1.0 for w, g in d.groupby("week")}


def week_line(result: bt.Result) -> str:
    """ROI by week, the builds as entered beside Sam as DraftKings paid him."""
    builds, sam = week_roi(result, "backtest"), week_roi(result, "sam_actual")
    parts = [
        f"W{w} {pct(builds[w], 0, True)} / {pct(sam.get(w, float('nan')), 0, True)}" for w in sorted(builds)
    ]
    return "By week, builds / Sam as paid: " + ", ".join(parts)


def sam_gap_line(result: bt.Result) -> str:
    """The builds as entered minus Sam as paid, over the same contests: by contest and by week."""
    t = result.contests
    p = bt.paired_vs(t, "backtest", "sam_actual")
    if not p["contests"]:
        return ""
    return (
        f"Builds minus Sam, same contests: {pct(p['diff_week'], 0, True)} by week "
        f"(SE {pct(p['se_week'], 0)}), builds ahead in {p['weeks_won']} of {p['weeks']} weeks; "
        f"{pct(p['diff_contest'], 0, True)} by contest (SE {pct(p['se_contest'], 0)}, {p['contests']} "
        "contests: far from independent)"
    )


def weeks_text(weeks) -> str:  # noqa: ANN001
    return ", ".join(str(w) for w in sorted(weeks))


def pool_summary(result: bt.Result) -> str:
    sources = {w: r.get("pool", "") for w, r in result.runs.items()}
    constructed = [w for w, p in sources.items() if p.startswith("constructed")]
    saved = [w for w in sources if w not in constructed]
    if not saved:
        return "Pool: constructed in every week (Sam's real pool is saved from Week 7 on)"
    other = weeks_text(constructed) or "none"
    return f"Pool: Sam's saved pool in week(s) {weeks_text(saved)}; constructed in {other}"


def timebox_weeks(result: bt.Result) -> list[int]:
    return sorted(
        w
        for w, r in result.runs.items()
        if any("time box" in n or "time limit" in n for n in r.get("notes", []))
    )


def curve_check(result: bt.Result) -> str:
    """Sam's real winnings against the same lineups paid on the fitted table, by format."""
    parts = []
    for fmt in ("cash", "gpp"):
        a = side_rows(result.rows, result.name, "sam_actual", fmt)
        c = side_rows(result.rows, result.name, "sam", fmt)
        if a.empty:
            continue
        parts.append(f"{fmt} {money(c['paid'].sum())} on the table vs {money(a['paid'].sum())} paid")
    return "Payout table check (Sam's own lineups): " + "; ".join(parts)


def summary_lines(result: bt.Result) -> list[str]:
    """The ten lines the command prints."""
    n_contests = result.rows[result.rows["side"] == "sam"]["contest"].nunique()
    sam = stats(side_rows(result.rows, result.name, "sam_actual"))
    lines = [
        f"Flashback '{result.name}': weeks {weeks_text(result.weeks)}, {n_contests} of Sam's contests "
        f"({sam['entries']} entries, {pct(result.coverage['share'])} of his main-slate Classic fees)",
        pool_summary(result),
        *table_lines(result),
        week_line(result),
        sam_gap_line(result),
    ]
    lines = [x for x in lines if x]
    tb = timebox_weeks(result)
    if tb:
        lines.append(
            f"The builder's time box cut the search short in week(s) {weeks_text(tb)}, as it does live."
        )
    return lines


# --- compare ------------------------------------------------------------------------------------------------


def verdict(p: dict, weeks: int, *, ex_top: float | None = None) -> str:
    if weeks < MIN_WEEKS:
        return f"not proven yet: {weeks} week(s) scored, the bar needs {MIN_WEEKS}"
    se = p["se_week"]
    clear = se == se and se > 0 and p["diff_week"] > SE_BAR * se
    most = p["weeks_won"] > p["weeks"] / 2
    if clear and most and (ex_top is None or ex_top > 0):
        return "clears the bar"
    why = []
    if not clear:
        why.append(
            f"{p['diff_week']:+.1%} is within {SE_BAR:g} SE ({se:.1%})" if se == se else "no standard error"
        )
    if not most:
        why.append(f"wins {p['weeks_won']} of {p['weeks']} weeks")
    if ex_top is not None and ex_top <= 0:
        why.append("the win disappears without its best ticket")
    return "not proven yet: " + "; ".join(why)


def compare_lines(a: bt.Result, b: bt.Result) -> list[str]:
    """`a` against `b` (b is the one to beat) on the same contests: paired by contest and by week."""
    rows = pd.concat(
        [
            bt.rename_side(a.rows[a.rows["side"] == "backtest"], "A"),
            bt.rename_side(b.rows[b.rows["side"] == "backtest"], "B"),
        ]
    )
    t = bt.contest_table(rows)
    out = [f"{a.name} (A) against {b.name} (B), the same contests; ROI points, A minus B"]
    for scope, label in (("entry", "as entered"), ("any", "any build")):
        for fmt, flabel in ((None, "all"), ("cash", "cash"), ("gpp", "GPP")):
            p = bt.paired_vs(t, "A", "B", scope, fmt)
            if not p["contests"]:
                continue
            per_contest = (
                f"{p['diff_contest']:+.1%} per contest (SE {p['se_contest']:.1%}, {p['contests']} contests)"
            )
            by_week = f"by week {p['diff_week']:+.1%} (SE {p['se_week']:.1%})"
            by_week += f", A wins {p['weeks_won']} of {p['weeks']} weeks"
            out.append(f"- {label}, {flabel}: {per_contest}; {by_week}")
    p = bt.paired_vs(t, "A", "B", "any", None)
    ex = _ex_top_diff(rows)
    out.append(f"Verdict on the ship bar (any build, all formats): {verdict(p, p['weeks'], ex_top=ex)}")
    cut = [(r.name, timebox_weeks(r)) for r in (a, b) if timebox_weeks(r)]
    if cut:
        who = "; ".join(f"{name} in week(s) {weeks_text(w)}" for name, w in cut)
        out.append(
            f"Caution: the builder's time box cut the search short ({who}), so part of any difference is "
            "which lineups the clock let it find. Raise `seconds` under [builds] on both sides until the "
            "search finishes: it then gives the same builds every time."
        )
    return out


def _ex_top_diff(rows: pd.DataFrame) -> float | None:
    """A's ROI minus B's with each side's single best ticket removed (any-build scope)."""
    vals = []
    for side in ("A", "B"):
        d = rows[(rows["side"] == side) & (rows["scope"] == "any")]
        if d.empty:
            return None
        ex = d.drop(index=d["paid"].idxmax())
        vals.append(ex["paid"].sum() / ex["fee"].sum() - 1.0)
    return vals[0] - vals[1]


# --- the full report ----------------------------------------------------------------------------------------


def contest_lines(result: bt.Result) -> list[str]:
    t = result.contests
    out = [
        "| week | contest | fmt | k | builds ROI | any build ROI | Sam ROI (paid) | Sam ROI (table) |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for (w, cid), g in t.groupby(["week", "contest"]):
        series = g["series"].iloc[0]
        entries = int(g[(g["side"] == "sam") & (g["scope"] == "entry")]["entries"].sum())
        rois = [
            pct(_roi_of(g, side, scope), 0, True)
            for side, scope in (
                ("backtest", "entry"),
                ("backtest", "any"),
                ("sam_actual", "entry"),
                ("sam", "entry"),
            )
        ]
        out.append(f"| {w} | {series} #{cid} | {g['fmt'].iloc[0]} | {entries} | " + " | ".join(rois) + " |")
    return out


def _roi_of(g: pd.DataFrame, side: str, scope: str) -> float:
    r = g[(g["side"] == side) & (g["scope"] == scope)]
    return float(r["roi"].iloc[0]) if len(r) else float("nan")


def build_lines(result: bt.Result) -> list[str]:
    out = []
    for w, run in sorted(result.runs.items()):
        kinds = {k: sum(1 for b in run["builds"] if b["kind"] == k) for k in ("cash", "gpp")}
        made = f"{kinds['cash']} cash, {kinds['gpp']} GPP builds in {run['seconds']:.0f} s"
        out.append(f"- Week {w}: {made}; pool {run['pool']}")
        for n in run.get("notes", []):
            if n.startswith("pool: ") or "Heavy chalk" in n:
                continue
            out.append(f"  - {n}")
    return out


def render(results: list[bt.Result], compare: list[str] | None = None, when: datetime | None = None) -> str:
    """The markdown report for one or two Results."""
    when = when or datetime.now()
    first = results[0]
    out = [
        f"# Flashback backtest: {' vs '.join(r.name for r in results)}",
        "",
        f"Run {when:%Y-%m-%d %H:%M}, weeks {', '.join(map(str, first.weeks))}.",
        "",
    ]
    out += ["**Ship bar.** " + SHIP_BAR + ".", ""]
    for r in results:
        out += [f"## {r.name}", ""]
        out += ["Settings: " + "; ".join(r.settings.describe()), ""]
        if r.settings.unsupported_rules():
            lines = ", ".join(r.settings.unsupported_rules())
            out += [f"Rules lines not applied by the backtest (they need a built tab): {lines}.", ""]
        out += ["```", *summary_lines(r), "```", ""]
        for fmt, label in (("cash", "Cash (double-ups)"), ("gpp", "GPP")):
            out += [f"### {label}", "", "```", *table_lines(r, fmt), "```", ""]
        out += ["### Contest by contest", "", *contest_lines(r), ""]
        out += ["### The builds", "", *build_lines(r), ""]
        out += [curve_check(r), ""]
        if r.dropped:
            out += [f"Lineups left out for a player with no known score: {len(r.dropped)}.", ""]
    if compare:
        out += ["## Comparison", "", *compare, ""]
    out += [
        "## How to read this",
        "",
        "Contests of one week share most of their players and one slate's luck, so they are not independent: "
        "believe the by-week numbers, not the by-contest ones, and treat anything resting on one ticket as "
        "luck. 'As entered' puts the best builds by the tool's own ranking into the contests Sam entered; "
        "'any build' scores every build in every one of them (the same question with less luck in it). "
        "Builds ran with each setting's search budget (`seconds`; the live tool stops at 120) on the pool "
        "the report names.",
        "",
    ]
    return "\n".join(out)


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "run"


def write_report(
    results: list[bt.Result], compare: list[str] | None = None, directory: Path | None = None
) -> Path:
    directory = directory or bt.REPORT_DIR
    directory.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M")
    path = directory / f"{stamp}-{'-vs-'.join(slug(r.name) for r in results)}.md"
    path.write_text(render(results, compare))
    return path


# --- the refresh hook ---------------------------------------------------------------------------------------


def refresh_from(result: bt.Result) -> dict:
    """`research.refresh`'s contract for today's defaults: the headline numbers and the ship bar."""
    built = stats(side_rows(result.rows, result.name, "backtest"))
    anyb = stats(side_rows(result.rows, result.name, "any"))
    sam = stats(side_rows(result.rows, result.name, "sam_actual"))
    head = (
        f"Flashback, weeks {weeks_text(result.weeks)}: today's builds as entered "
        f"{pct(built['roi'], 0, True)} ROI ({built['entries']} entries), any build "
        f"{pct(anyb['roi'], 0, True)}, Sam as paid {pct(sam['roi'], 0, True)}."
    )
    metrics = {
        "weeks": len(result.weeks),
        "builds entered roi": round(built["roi"], 4),
        "builds any roi": round(anyb["roi"], 4),
        "builds any roi ex best": round(anyb["ex_top"], 4),
        "sam roi": round(sam["roi"], 4),
        "builds cash rate": round(built["cash"], 4),
    }
    return {"headline": [head, "", *summary_lines(result)[2:]], "metrics": metrics, "ship_bar": SHIP_BAR}
