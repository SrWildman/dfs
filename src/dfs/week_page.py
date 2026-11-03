"""`dfs week page`: one self-contained HTML page for the whole week, built only from outputs that exist.

Top: **Today**, day-aware (Mon close, Tue new week, Wed-Fri research, Sat build, Sun game day): the 3-5 steps
with the exact phrase to say to Claude Code and the command, and what is stale. Then each decision Sam makes
in a week, answered from what the tool has already saved, each with its **trust** (Proven / Shadow / Untested;
the outside audit's verdicts, `TRUST`, are the starting point): the contest plan, cash, GPP, the unknowns,
Sunday, the season-long leagues, last week. Last: **Your tools**, the anti-forgetting list, generated from the
CLI's own registry and CLAUDE.md's phrase tables (`week_tools.py`), with when each was last run.

Read-only. No sheet, no network, no server, no runner: a section whose input does not exist says "not run
yet" and what to say to produce it. The file is `data/week/<season>-wk<NN>.html` (inline CSS, no external
loads) and is regenerated at the end of the commands in `REGENERATE_AFTER` (`after_command`, called by the CLI
entry point after the run log line; a failure there warns and the command carries on).

Pure builders (`today_html`, `*_section`, `tools_html`) take a `WeekInputs` and return text; `generate` is the
only function that touches the disk or the browser.
"""

from __future__ import annotations

import html
import re
import sys
import webbrowser
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from dfs import kickoff, launcher, nfl_calendar, runlog, week_today, week_tools
from dfs.week_inputs import WeekInputs, data_root, gather, parse_time

# Run after one of these commands succeeds. `dfs week new` is added to the planned list: a new week's
# page should exist the moment its sheet does.
REGENERATE_AFTER = {
    "dfs sync",
    "dfs lineups suggest",
    "dfs lineups late-swap",
    "dfs contests plan",
    "dfs season sync",
    "dfs season check",
    "dfs week close",
    "dfs week new",
}

# Tests turn this off (tests/conftest.py): a test that runs a CLI command must not write a page.
AUTO_REGENERATE = True

# Plan C item 4b (a game-day sync never wipes data). Flip when it is merged and `dfs sync --live` is safe on
# Sunday; the Sunday section says which it is.
LIVE_SYNC_GUARD_MERGED = False

CHALK_THRESHOLD = 0.30  # a player this owned in double-ups is "chalk" (the cash chalk check)
PROVEN, SHADOW, UNTESTED = "Proven", "Shadow", "Untested"

# The audit's verdict on each decision's evidence (docs/planning/audit/AUDIT_2026-11.md), the starting point.
# One line each, kept here so they can be edited in one place when the evidence moves.
TRUST: dict[str, tuple[str, str]] = {
    "contest_plan": (
        PROVEN,
        "Five seasons of your own paid entries: Showdown -45%, N-max GPP -32%, Double Up +9%.",
    ),
    "cash": (
        UNTESTED,
        "One cash lineup a week is 8 observations a season: nothing yet shows these builds beat the field.",
    ),
    "cash_chalk": (
        SHADOW,
        "Display only: CashOwn is a model estimate and the builder does not use this check.",
    ),
    "gpp": (
        UNTESTED,
        "In Weeks 7-8 the Suggested builds scored about what your own lineups did; no edge is measured.",
    ),
    "unknowns": (PROVEN, "A data feed (injury statuses and kickoff times), not a model."),
    "sunday": (
        SHADOW,
        "Live-state late swap is the only mode the retro found helpful: about +0.24 points and +0.6 points "
        "of cash rate per lineup. A swap is worth fractions of a point.",
    ),
    "season_alerts": (PROVEN, "Empty slots, byes and OUT starters are facts from Sleeper and ESPN."),
    "season_pickups": (
        SHADOW,
        "Sleeper's projections rescored in your scoring; held-out accuracy is Sleeper's own.",
    ),
    "season_trades": (
        UNTESTED,
        "Values are ours against FantasyPros' consensus; no trade has been tracked yet.",
    ),
    "last_week": (PROVEN, "What happened: your contest log and the close."),
}


def e(text: object) -> str:
    return html.escape(str(text), quote=True)


def rich(text: object) -> str:
    """Escaped text with `backticks` shown as code."""
    return re.sub(r"`([^`]+)`", r"<code>\1</code>", e(text))


# -- small HTML helpers -----------------------------------------------------------------------------


def table(headers: list[str], rows: list[list[str]], *, cls: str = "") -> str:
    """Cells are already-escaped HTML."""
    head = "".join(f"<th>{e(h)}</th>" for h in headers)
    body = "".join("<tr>" + "".join(f"<td>{c}</td>" for c in row) + "</tr>" for row in rows)
    return (
        f'<div class="scroll"><table class="{cls}"><thead><tr>{head}</tr></thead>'
        f"<tbody>{body}</tbody></table></div>"
    )


def bullets(items: list[str]) -> str:
    return "<ul>" + "".join(f"<li>{i}</li>" for i in items) + "</ul>"


def code(text: str, cls: str = "") -> str:
    return f'<code class="{cls}">{e(text)}</code>'


def note(text: str) -> str:
    return f'<p class="note">{rich(text)}</p>'


def pct(value: float | None, digits: int = 0) -> str:
    return "-" if value is None else f"{value * 100:.{digits}f}%"


def dollars(value: float) -> str:
    return f"-${abs(value):,.0f}" if value < 0 else f"${value:,.0f}"


def num(value: object) -> float | None:
    try:
        out = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return None if out != out else out


@dataclass
class Section:
    id: str
    title: str
    body: str
    trust: tuple[str, str] | None = None
    ran: bool = True  # False: "not run yet" (shown muted)
    asof: str = ""

    def html(self) -> str:
        chip = ""
        if self.trust:
            label, why = self.trust
            chip = f'<span class="chip {label.lower()}" title="{e(why)}">{e(label)}</span>'
        asof = f'<span class="asof">{e(self.asof)}</span>' if self.asof else ""
        trust_line = (
            f'<p class="trust"><b>{e(self.trust[0])}.</b> {rich(self.trust[1])}</p>' if self.trust else ""
        )
        cls = "card" if self.ran else "card notrun-card"
        return (
            f'<section class="{cls}" id="{e(self.id)}"><div class="card-head"><h3>{e(self.title)}</h3>'
            f"{chip}{asof}</div>{self.body}{trust_line}</section>"
        )


def not_run(what: str, say: str | None = None, command: str | None = None) -> str:
    hint = ""
    if say:
        hint += f" Say {code(chr(34) + say + chr(34), 'say')}"
    if command:
        hint += f" ({code(command)})" if say else f" Run {code(command)}"
    return f'<p class="notrun"><b>Not run yet.</b> {rich(what)}.{hint}</p>'


# -- Today ------------------------------------------------------------------------------------------


def last_tag(inp: WeekInputs, run: runlog.Run | None) -> str:
    if run is None:
        return '<span class="last never">no run logged</span>'
    ok = "" if run.ok else " (failed)"
    return f'<span class="last">last run {e(week_today.when(inp.now, run.time))}{ok}</span>'


def launcher_state_for(inp: WeekInputs):
    from dfs.cli import GAMES_FINISHED_AFTER_HOURS

    return week_today.launcher_state(
        week=inp.week,
        now=inp.now,
        manifest=inp.manifest,
        game_starts=[r["GameStart"] for r in (inp.edge or []) if r.get("GameStart")],
        pool_count=len(inp.pool),
        finished_after_hours=GAMES_FINISHED_AFTER_HOURS,
    )


def suggest_time(inp: WeekInputs) -> datetime | None:
    return parse_time((inp.suggest or {}).get("at"))


def today_html(inp: WeekInputs, registered: set[str] | None) -> str:
    day = inp.now.astimezone().weekday()
    steps = week_today.steps_for(day, inp.runs, registered)
    items = []
    for s in steps:
        say = f"Say {code(chr(34) + s.phrase + chr(34), 'say')}" if s.phrase else "By hand"
        run = f"{code(s.command)}" if s.command else ""
        items.append(
            f'<li><div class="step-head">{say} {("&rarr; " + run) if run else ""}</div>'
            f'<div class="why">{rich(s.why)}</div>{last_tag(inp, s.last) if s.command else ""}</li>'
        )
    state = launcher_state_for(inp)
    suggestions = [
        f"{e(a.label)} {('(' + code(a.command) + ')') if a.command else '(in the sheet)'}"
        for a in launcher.suggest_actions(state)
    ]
    stale = week_today.stale_items(
        now=inp.now,
        manifest=inp.manifest,
        suggest_at=suggest_time(inp),
        late_swap_at=parse_time((inp.late_swap or {}).get("saved_at")),
        season_at=parse_time((inp.season_long or {}).get("saved_at")),
        day=day,
    )
    stale_html = (
        bullets([e(s) for s in stale]) if stale else '<p class="ok">Nothing stale: every input is fresh.</p>'
    )
    return (
        f'<section class="card today" id="today"><div class="card-head"><h2>Today: '
        f"{e(week_today.DAY_NAMES[day])}, {e(week_today.DAY_THEME[day].lower())}</h2></div>"
        f'<ol class="steps">{"".join(items)}</ol>'
        f'<div class="two"><div><h4>What the data says to do next</h4>{bullets(suggestions)}'
        '<p class="note">The guided menu\'s own suggestions (<code>dfs</code>), from local files: the page '
        "never opens the sheet, so it cannot see how many lineups are filled.</p></div>"
        f"<div><h4>What is stale</h4>{stale_html}</div></div></section>"
    )


# -- decisions --------------------------------------------------------------------------------------


def builds_of(inp: WeekInputs, kind: str) -> list[dict]:
    return [b for b in (inp.suggest or {}).get("builds", []) if b.get("kind") == kind]


def round_label(inp: WeekInputs) -> str:
    s = inp.suggest or {}
    at = suggest_time(inp)
    return f"round {s.get('round', '?')}, {week_today.when(inp.now, at)}" if at else ""


def contest_plan_section(inp: WeekInputs, registered: set[str] | None) -> Section:
    trust = TRUST["contest_plan"]
    plan = inp.contest_plan
    if not plan:
        built = registered is None or "dfs contests plan" in registered
        why = (
            "Spend by format against the caps, and the H2H opponents to avoid"
            if built
            else "`dfs contests plan` (Plan D) is not built yet, so there is no plan to show"
        )
        return Section(
            "contest-plan",
            "Contest plan",
            not_run(why, command="dfs contests plan" if built else None),
            trust,
            ran=False,
        )
    parts = [f"<p><b>{e(plan.get('line', ''))}</b></p>"] if plan.get("line") else []
    caps, spent = plan.get("caps") or {}, plan.get("spent") or {}
    if caps:
        rows = []
        for fmt, cap in caps.items():
            used = spent.get(fmt)
            over = used is not None and cap is not None and used > cap
            rows.append(
                [
                    e(fmt),
                    e(dollars(cap)) if cap is not None else "-",
                    e(dollars(used)) if used is not None else "-",
                    '<span class="bad">over</span>' if over else "ok",
                ]
            )
        parts.append(table(["Format", "Cap a week", "Spent last week", ""], rows))
    if plan.get("roi"):
        rows = [
            [
                e(r["format"]),
                e(r.get("entries", "")),
                e(pct(num(r.get("roi")), 1)),
                e(pct(num(r.get("rake")), 0)),
            ]
            for r in plan["roi"]
        ]
        parts.append(
            "<h4>Your ROI and the rake, by format</h4>" + table(["Format", "Entries", "ROI", "Rake"], rows)
        )
    if plan.get("h2h_avoid"):
        parts.append(
            "<h4>H2H opponents to avoid</h4>"
            + bullets([f"{e(o['opponent'])} ({e(o.get('record', ''))})" for o in plan["h2h_avoid"]])
        )
    return Section("contest-plan", "Contest plan", "".join(parts), trust, asof=_saved(inp, plan))


def _saved(inp: WeekInputs, payload: dict | None) -> str:
    at = parse_time((payload or {}).get("saved_at"))
    return week_today.when(inp.now, at) if at else ""


def cash_shown(b: dict) -> tuple[float | None, bool]:
    """(the Cash % to show, whether it is the calibrated one). A saved build that carries the shown number
    (`p_cash_shown`) is calibrated; otherwise the saved `p_cash` is the build's own-line number."""
    m = b.get("metrics") or {}
    if m.get("p_cash_shown") is not None:
        return num(m["p_cash_shown"]), True
    return num(m.get("p_cash")), False


def cash_note(inp: WeekInputs, calibrated: bool) -> str:
    on = bool(((inp.config.get("sim") or {}).get("lines") or {}).get("calibrated_cash_offset"))
    du = inp.du_line or {}
    if calibrated:
        return "Cash % is the calibrated one (against the double-up line fitted on your own contests)."
    if on:
        weeks = ", ".join(str(w) for w in du.get("weeks", []))
        return (
            "The calibrated Cash % is switched on, but the saved run keeps the builds' own-line numbers: "
            f"Suggested shows the calibrated ones (field average +{du.get('offset', '?')}, Weeks {weeks})."
        )
    return (
        "Cash % here is today's line, which the audit found runs 16-21 points above what your double-ups "
        "really cash. The calibrated display is off (`[sim.lines] calibrated_cash_offset`)."
    )


def cash_chalk(inp: WeekInputs) -> tuple[list[dict] | None, int]:
    """Players the double-ups are expected to own at 30%+ (CashOwn), each with how many of the cash builds use
    him. None when CashOwn is not out yet."""
    cash = builds_of(inp, "cash")
    chalk = []
    published = False
    for r in inp.edge or []:
        own = num(r.get("CashOwn"))
        if own is None:
            continue
        published = True
        if own >= CHALK_THRESHOLD:
            used = sum(1 for b in cash if r["Name"] in b.get("names", []))
            chalk.append({"name": r["Name"], "pos": r.get("Position", ""), "own": own, "used": used})
    if not published:
        return None, len(cash)
    return sorted(chalk, key=lambda c: -c["own"]), len(cash)


def cash_section(inp: WeekInputs) -> Section:
    trust = TRUST["cash"]
    cash = builds_of(inp, "cash")
    if not cash:
        return Section(
            "cash",
            "Cash",
            not_run(
                "No cash builds are saved for this week",
                say="build me lineups",
                command="dfs lineups suggest",
            ),
            trust,
            ran=False,
        )
    top = sorted(cash, key=lambda b: -(cash_shown(b)[0] or 0))[:5]
    calibrated = any(cash_shown(b)[1] for b in top)
    rows = []
    for b in top:
        shown, _ = cash_shown(b)
        m = b.get("metrics") or {}
        rows.append(
            [
                e(b.get("number", "")),
                e(", ".join(b.get("names", []))),
                e(f"${int(m['salary']):,}") if m.get("salary") else "-",
                f"<b>{e(pct(shown))}</b>",
            ]
        )
    body = [table(["Build", "Players", "Salary", "Cash %"], rows), note(cash_note(inp, calibrated))]
    chalk, n_builds = cash_chalk(inp)
    body.append(f"<h4>Cash chalk check {_chip('cash_chalk')}</h4>")
    if chalk is None:
        body.append(
            not_run(
                "CashOwn is not out yet (it needs TFFB's ownership), so there is no chalk to check",
                command="dfs sync",
            )
        )
    elif not chalk:
        body.append('<p class="ok">No player is expected at 30%+ in double-ups.</p>')
    else:
        rows = [
            [
                e(c["name"]),
                e(c["pos"]),
                e(pct(c["own"])),
                f'<span class="{"bad" if c["used"] == 0 else ""}">{c["used"]} of {n_builds}</span>',
            ]
            for c in chalk
        ]
        body.append(table(["Player", "Pos", "Double-up own", "Cash builds using him"], rows))
        body.append(
            note("Fading a 30%+ player should be a choice, not an accident. The builder does not use this.")
        )
    return Section("cash", "Cash", "".join(body), trust, asof=round_label(inp))


def _chip(key: str) -> str:
    label, why = TRUST[key]
    return f'<span class="chip {label.lower()}" title="{e(why)}">{e(label)}</span>'


def stacks_trust(inp: WeekInputs) -> tuple[str, str]:
    retro = inp.stacks_retro or {}
    verdict = retro.get("verdict")
    z = num(retro.get("combined_z"))
    if verdict in ("tested", "weak"):
        zt = f"z {z:.2f}" if z is not None else ""
        return (
            SHADOW,
            f"The retro rates the leverage signal {verdict} ({zt}); the audit calls it weak but real: "
            "information, not a rule.",
        )
    return UNTESTED, "The stacks retro has not rated the leverage signal yet."


def gpp_section(inp: WeekInputs) -> Section:
    trust = TRUST["gpp"]
    gpp = sorted(builds_of(inp, "gpp"), key=lambda b: -(num((b.get("metrics") or {}).get("p_gpp")) or 0))[:3]
    body = []
    if gpp:
        rows = []
        for b in gpp:
            m = b.get("metrics") or {}
            rows.append(
                [
                    e(b.get("number", "")),
                    e(b.get("title", "").split("  ·  ", 1)[-1]),
                    e(", ".join(b.get("names", []))),
                    e(pct(num(m.get("p_gpp")), 1)),
                ]
            )
        body.append(table(["Build", "Shape", "Players", "Top 1%"], rows))
        body.append(
            note("The audit's use for these: single-entry and small-field GPPs (under about 1,000 entrants).")
        )
    else:
        body.append(
            not_run(
                "No GPP builds are saved for this week", say="build me lineups", command="dfs lineups suggest"
            )
        )
    stacks = inp.stacks
    label, why = stacks_trust(inp)
    body.append(
        f'<h4>Top 3 stacks <span class="chip {label.lower()}" title="{e(why)}">{e(label)}</span></h4>'
    )
    if stacks and stacks.get("rows"):
        rows = [
            [
                e(r["qb"]),
                e(r["mates"]),
                e(r["game"]),
                e(pct(num(r.get("top1")), 1)),
                e(f"{r['lift']:.1f}x") if num(r.get("lift")) is not None else "-",
                e(pct(num(r.get("lev")), 1)) if r.get("lev") is not None else "-",
                code(r.get("rule", "")),
            ]
            for r in stacks["rows"]
        ]
        body.append(
            table(["QB", "Teammates", "Game", "Top 1%", "Lift", "Lev", "Type in Suggested's rules box"], rows)
        )
        body.append(
            note(f"Sorted by {stacks.get('sorted_by') or 'simulated Top 1%'}. {stacks_trust(inp)[1]}")
        )
    else:
        body.append(not_run("The Stacks view has not been saved for this week", command="dfs stacks"))
    ran = bool(gpp) or bool(stacks and stacks.get("rows"))
    return Section("gpp", "GPP", "".join(body), trust, ran=ran, asof=round_label(inp))


def unknowns_section(inp: WeekInputs) -> Section:
    trust = TRUST["unknowns"]
    if not inp.edge:
        return Section(
            "unknowns",
            "Unknowns",
            not_run("Nothing is synced", say="sync", command="dfs sync"),
            trust,
            ran=False,
        )
    in_builds: dict[str, int] = {}
    for b in (inp.suggest or {}).get("builds", []):
        for name in b.get("names", []):
            in_builds[name] = in_builds.get(name, 0) + 1
    outs = [r for r in inp.edge if r.get("Avail") in ("OUT", "IR") and r["Name"] in in_builds]
    mine = {r["Name"] for r in inp.edge if r["Name"] in in_builds or r["Name"] in inp.pool}
    uncertain = [r for r in inp.edge if r.get("Avail") in ("Q", "D")]
    shown = [r for r in uncertain if r["Name"] in mine]
    scope = "in your pool or builds"
    if not shown:
        shown = sorted(uncertain, key=lambda r: -(num(r.get("ProjPts")) or 0))[:8]
        scope = "top by projection, none in your pool or builds" if shown else ""
    body = []
    if outs:
        body.append(
            '<p class="bad"><b>OUT or on IR but in a build:</b> '
            + e(", ".join(f"{r['Name']} ({r['Avail']}, in {in_builds[r['Name']]} builds)" for r in outs))
            + ".</p>"
        )
    if shown:
        shown.sort(key=lambda r: r.get("GameStart") or "9")
        rows = [
            [
                e(r["Name"]),
                e(f"{r.get('Position', '')} {r.get('Team', '')}".strip()),
                f"<b>{'Doubtful' if r['Avail'] == 'D' else 'Questionable'}</b>",
                e(kickoff.kickoff_label(r.get("GameStart")) or r.get("Kickoff", "")),
                e(
                    f"in {in_builds[r['Name']]} builds"
                    if r["Name"] in in_builds
                    else (f"pool: {inp.pool[r['Name']]}" if r["Name"] in inp.pool else "")
                ),
            ]
            for r in shown
        ]
        body.append(
            f'<p class="note">Q and Doubtful players, {e(scope)}, by kickoff.</p>'
            + table(["Player", "Team", "Status", "Kickoff (ET)", "Yours"], rows)
        )
    elif not outs:
        body.append('<p class="ok">No Questionable or Doubtful players on the slate.</p>')
    manifest_at = max(
        (parse_time(v.get("synced_at")) for v in inp.manifest.values() if v.get("synced_at")), default=None
    )
    return Section(
        "unknowns",
        "Unknowns",
        "".join(body),
        trust,
        asof=f"as of the sync {week_today.when(inp.now, manifest_at)}" if manifest_at else "",
    )


def swap_text(best: dict | None) -> str:
    if not best:
        return "nothing beats it"
    out, into = ", ".join(best.get("out", [])), ", ".join(best.get("into", []))
    bits = []
    if num(best.get("delta_pts")) is not None:
        bits.append(f"{best['delta_pts']:+.1f} pts")
    if num(best.get("delta_p_cash")):
        bits.append(f"Cash {best['delta_p_cash'] * 100:+.1f} pts")
    if num(best.get("delta_p_gpp")):
        bits.append(f"Top 1% {best['delta_p_gpp'] * 100:+.1f} pts")
    if best.get("overlap"):
        bits.append(best["overlap"])
    return f"{e(out)} &rarr; {e(into)} ({e(', '.join(bits))})"


def sunday_section(inp: WeekInputs) -> Section:
    trust = TRUST["sunday"]
    guard = (
        "Yes: `dfs sync --live` merges and never blanks a started player's numbers, so it is safe to run "
        "any time on Sunday."
        if LIVE_SYNC_GUARD_MERGED
        else "Not confirmed. Plan C's guard (a game-day sync never wipes data) is not merged, so "
        "`dfs sync --live` can still blank started players' numbers: until then, treat Sunday's "
        "projections as Saturday's."
    )
    guard_html = f"<p><b>Safe game-day sync:</b> {rich(guard)}</p>"
    swap = inp.late_swap
    start = nfl_calendar.week_start_date(inp.week, inp.season)
    at = parse_time((swap or {}).get("saved_at"))
    if not swap or at is None or at.astimezone().date() < start:
        what = "Late swap has not run this week" if swap else "No late-swap run is saved"
        return Section(
            "sunday",
            "Sunday",
            not_run(what, say="late swap", command="dfs lineups late-swap --refresh-injuries") + guard_html,
            trust,
            ran=False,
        )
    rows = []
    for lineup in swap.get("lineups", []):
        rows.append(
            [
                e(f"{lineup.get('goal', '').title()} {lineup.get('number', '')}"),
                e(", ".join(lineup.get("open_slots") or []) or "none open"),
                e(lineup.get("position") or "-"),
                swap_text(lineup.get("best")),
            ]
        )
    field = swap.get("field_source") or "field: projected"
    live = "live" in field or "real" in field
    body = [
        f"<p>Last run {e(week_today.when(inp.now, at))}. <b>{e(field)}</b>"
        + (
            ""
            if live
            else ' <span class="bad">no live scores: positions are blank and a swap rarely beats a projection'
            '</span> (say "late swap with live scores")'
        )
        + "</p>",
        table(["Lineup", "Open slots", "Position", "Best swap"], rows),
        guard_html,
        note("The Cockpit tab holds the SWAP / LOOK / FILL cues for the same run."),
    ]
    return Section("sunday", "Sunday", "".join(body), trust, asof=week_today.when(inp.now, at))


def season_section(inp: WeekInputs) -> Section:
    leagues = ((inp.season_long or {}).get("leagues")) or {}
    if not leagues:
        return Section(
            "season",
            "Season-long leagues",
            not_run("No season-long view is saved", say="season sync", command="dfs season sync"),
            TRUST["season_pickups"],
            ran=False,
        )
    cards = []
    for name, lg in sorted(leagues.items()):
        at = parse_time(lg.get("saved_at"))
        checked = parse_time(lg.get("checked_at"))
        alerts = lg.get("alerts") or []
        parts = [
            f'<h4>{e(name)} <span class="asof">{e(week_today.when(inp.now, at)) if at else ""}</span></h4>'
        ]
        if lg.get("headline"):
            parts.append("<p>" + e(lg["headline"][0]) + "</p>")
            if len(lg["headline"]) > 1:
                parts.append(f'<p class="note">{e(lg["headline"][1].strip())}</p>')
        alert_note = f" (checked {e(week_today.when(inp.now, checked))})" if checked else ""
        parts.append(f"<h5>Lineup problems {_chip('season_alerts')}{alert_note}</h5>")
        parts.append(
            bullets(
                [f'<span class="{"bad" if a.startswith("SWAP") else "warn"}">{e(a)}</span>' for a in alerts]
            )
            if alerts
            else '<p class="ok">None: every starter is set, playing and healthy.</p>'
        )
        if lg.get("pickups"):
            parts.append(
                f"<h5>Top pickups and bids {_chip('season_pickups')}</h5>"
                + bullets([e(p) for p in lg["pickups"]])
            )
        if lg.get("ideas"):
            parts.append(
                f"<h5>Trade finder, top 3 {_chip('season_trades')}</h5>"
                + bullets([e(i) for i in lg["ideas"]])
            )
        cards.append(f'<div class="league">{"".join(parts)}</div>')
    return Section(
        "season",
        "Season-long leagues",
        f'<div class="leagues">{"".join(cards)}</div>',
        None,
        asof=_saved(inp, inp.season_long),
    )


def last_week_section(inp: WeekInputs) -> Section:
    trust = TRUST["last_week"]
    lw = inp.last_week
    if lw is None:
        return Section(
            "last-week",
            "Last week",
            not_run("The contest log has no paid NFL entries for this season", command="dfs week close"),
            trust,
            ran=False,
        )
    rows = [
        [e(f["format"]), e(f["entries"]), e(dollars(f["fees"])), e(dollars(f["net"])), e(pct(f["roi"]))]
        for f in lw.formats
    ]
    rows.append(
        [
            "<b>Total</b>",
            "",
            f"<b>{e(dollars(lw.fees))}</b>",
            f"<b>{e(dollars(lw.net))}</b>",
            e(pct(lw.net / lw.fees if lw.fees else None)),
        ]
    )
    body = [
        f"<p>Week {lw.week}, the newest in your contest log (NFL, paid entries; net = cash winnings minus "
        "fees).</p>",
        table(["Format", "Entries", "Fees", "Net", "ROI"], rows),
    ]
    pm = inp.postmortem
    body.append("<h4>Cash post-mortem</h4>")
    if pm and pm.get("headline"):
        body.append(f"<p><b>{e(pm['headline'])}</b></p>")
        if pm.get("lines"):
            body.append(bullets([e(x) for x in pm["lines"][:5]]))
    else:
        body.append(
            not_run(
                "The cash post-mortem is part of `week close` (Plan D) and has not been saved",
                say="close the week",
            )
        )
    body.append("<h4>Margin to the cash line</h4>")
    if lw.cash_entries:
        lines = []
        for c in lw.cash_entries:
            if c["points"] is None or c["line"] is None:
                continue
            verdict = "cashed" if c["cashed"] else "missed"
            lines.append(
                f"{e(c['series'])}: {c['points']:.1f} against a line of {c['line']:.1f} "
                f"(<b>{c['points'] - c['line']:+.1f}</b>, {verdict})"
                + (f", {c['copies']} identical copies" if c.get("copies") else "")
            )
        body.append(bullets(lines) if lines else not_run("No cash entry has a line"))
    else:
        body.append(not_run("No double-up standings are saved for that week", say="close the week"))
    return Section("last-week", "Last week", "".join(body), trust)


# -- tools ------------------------------------------------------------------------------------------


def tools_html(inp: WeekInputs, tools: list[week_tools.Tool]) -> str:
    stale, log_line = week_tools.not_used_lately(tools, inp.runs, inp.now)
    banner = f'<p class="note">{e(log_line)}</p>'
    if stale:
        banner = (
            '<div class="flag"><b>Not used in 3+ weeks:</b> '
            + ", ".join(code(t.variants[0] if t.command else t.says[0]) for t in stale)
            + f'</div><p class="note">{e(log_line)}</p>'
        )
    groups = []
    for group in week_tools.DAY_GROUPS:
        in_group = [t for t in tools if t.group == group]
        if not in_group:
            continue
        rows = []
        for t in in_group:
            says = "<br>".join(code('"' + s + '"', "say") for s in t.says) or '<span class="dim">-</span>'
            if t.in_sheet:
                run, last = '<span class="dim">in the sheet</span>', ""
            else:
                run = "<br>".join(code(v) for v in t.variants)
                last = last_tag(inp, t.last).replace("last run ", "")
            rows.append([says, run, rich(t.report), last])
        body = table(["Say to Claude Code", "Command", "What you get back", "Last run"], rows, cls="tools")
        if group == week_tools.REFERENCE:
            groups.append(
                f"<details><summary><h4>{e(group)} ({len(in_group)})</h4></summary>{body}</details>"
            )
        else:
            groups.append(f"<h4>{e(group)}</h4>{body}")
    return (
        '<section class="card" id="tools"><div class="card-head"><h2>Your tools</h2></div>'
        '<p class="note">Generated from the CLI and CLAUDE.md, so it lists every command and every phrase, '
        "including the ones you have not used lately.</p>" + banner + "".join(groups) + "</section>"
    )


# -- the page ---------------------------------------------------------------------------------------

CSS = """
:root{--bg:#f6f6f3;--card:#fff;--ink:#1d1f24;--dim:#666b75;--line:#dcdde1;--accent:#1f5eff;
--proven:#17794a;--shadow:#9a6400;--untested:#6b6f78;--bad:#b3261e;--warn:#8a5a00;--ok:#17794a;--flag:#fff3d6}
@media (prefers-color-scheme:dark){:root:not([data-theme=light]){--bg:#14161a;--card:#1c1f25;--ink:#e8e9ec;
--dim:#9aa0ab;--line:#2e323a;--accent:#7da2ff;--proven:#58c58e;--shadow:#e0b04a;--untested:#9aa0ab;--bad:#ff8a80;
--warn:#e0b04a;--ok:#58c58e;--flag:#3a3015}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.5 -apple-system,
BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif}
header,main{max-width:1040px;margin:0 auto;padding:0 16px}header{padding-top:20px}
h1{font-size:22px;margin:0 0 2px}h2{font-size:18px;margin:0}h3{font-size:16px;margin:0}
h4{font-size:14px;margin:16px 0 6px}
h5{font-size:13px;margin:10px 0 4px;color:var(--dim)}
.sub{color:var(--dim);margin:0 0 10px}
nav{display:flex;flex-wrap:wrap;gap:6px 14px;margin:8px 0 16px;font-size:14px}
nav a{color:var(--accent);text-decoration:none}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px 16px;
margin:0 0 14px}
.card-head{display:flex;flex-wrap:wrap;align-items:baseline;gap:8px 10px;margin-bottom:6px}
.notrun-card{opacity:.85}.notrun{color:var(--dim)}
.asof{color:var(--dim);font-size:12.5px;margin-left:auto}
.chip{font-size:11.5px;font-weight:600;border-radius:999px;padding:1px 9px;border:1px solid currentColor}
.chip.proven{color:var(--proven)}.chip.shadow{color:var(--shadow)}.chip.untested{color:var(--untested)}
.trust{font-size:13px;color:var(--dim);margin:10px 0 0;border-top:1px dashed var(--line);padding-top:8px}
code{font:12.5px/1.4 ui-monospace,SFMono-Regular,Menlo,monospace;background:rgba(127,127,127,.14);
padding:1px 5px;border-radius:4px}
code.say{background:rgba(31,94,255,.12);color:var(--accent)}
.steps{margin:6px 0 10px;padding-left:22px}.steps li{margin:0 0 10px}.step-head{font-weight:600}
.why{color:var(--dim);font-size:14px}.last{font-size:12.5px;color:var(--dim)}
.last.never{color:var(--warn)}
.two{display:grid;grid-template-columns:1fr 1fr;gap:6px 24px}
@media(max-width:720px){.two{grid-template-columns:1fr}}
.scroll{overflow-x:auto}table{border-collapse:collapse;width:100%;font-size:13.5px}
th,td{text-align:left;padding:5px 10px 5px 0;border-bottom:1px solid var(--line);vertical-align:top}
th{color:var(--dim);font-weight:600;font-size:12.5px}table.tools td:nth-child(1){min-width:150px}
table.tools td:nth-child(2){min-width:190px}
table.tools td:nth-child(3){min-width:260px}table.tools td:nth-child(4){min-width:110px}
table.tools code{white-space:normal;word-break:break-word}
.note{color:var(--dim);font-size:13px;margin:6px 0}.ok{color:var(--ok)}.bad{color:var(--bad)}
.warn{color:var(--warn)}
.dim{color:var(--dim)}.flag{background:var(--flag);border-radius:8px;padding:8px 12px;margin:8px 0}
.leagues{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:12px}
.league{border:1px solid var(--line);border-radius:8px;padding:2px 12px 8px}
ul{margin:4px 0;padding-left:20px}details summary{cursor:pointer}details summary h4{display:inline}
footer{max-width:1040px;margin:0 auto;padding:6px 16px 30px;color:var(--dim);font-size:12.5px}
"""


def week_dir(data_dir: Path | None = None) -> Path:
    return (data_dir or data_root()) / "week"


def page_path(season: int, week: int, data_dir: Path | None = None) -> Path:
    return week_dir(data_dir) / f"{season}-wk{week:02d}.html"


def render(
    inp: WeekInputs, *, registered: set[str] | None = None, tools: list[week_tools.Tool] | None = None
) -> str:
    """The whole page as one HTML string."""
    if tools is None:
        tools = week_tools.build(inp.runs)
    day = inp.now.astimezone().weekday()
    sections = [
        contest_plan_section(inp, registered),
        cash_section(inp),
        gpp_section(inp),
        unknowns_section(inp),
        sunday_section(inp),
        season_section(inp),
        last_week_section(inp),
    ]
    nav = (
        '<nav><a href="#today">Today</a>'
        + "".join(f'<a href="#{e(s.id)}">{e(s.title)}</a>' for s in sections)
        + '<a href="#tools">Your tools</a></nav>'
    )
    sources = sorted(inp.manifest)
    failed = [n for n in sources if inp.manifest[n].get("error")]
    stamp = kickoff.format_et(inp.now)
    sub = (
        f"{e(week_today.DAY_NAMES[day])} &middot; page built {e(stamp)} &middot; "
        f"{len(sources) - len(failed)} of {len(sources)} sources synced"
    )
    return (
        "<!doctype html><html lang=en><head><meta charset=utf-8>"
        '<meta name=viewport content="width=device-width,initial-scale=1">'
        f"<title>Week {inp.week}</title><style>{CSS}</style></head><body>"
        f'<header><h1>Week {inp.week}, {inp.season}</h1><p class="sub">{sub}</p>{nav}</header><main>'
        + today_html(inp, registered)
        + '<h2 class="sec">Decisions</h2>'
        + "".join(s.html() for s in sections)
        + tools_html(inp, tools)
        + "</main><footer>Read-only: built from files the tool already saved; nothing here writes a sheet. "
        "Regenerated after sync, suggest, late-swap, contests plan, season sync/check and week close; "
        "<code>dfs week page</code> rebuilds it now.</footer></body></html>"
    )


def generate(
    *,
    now: datetime | None = None,
    week: int | None = None,
    season: int | None = None,
    data_dir: Path | None = None,
    open_browser: bool = False,
) -> Path:
    """Build the page from the saved outputs, write it, and (only when asked) open it in the browser."""
    from dfs import commands_doc

    now = now or datetime.now().astimezone()
    season = season if season is not None else nfl_calendar.current_season(now.date())
    week = week if week is not None else nfl_calendar.week_for_date(now.date(), season)
    inp = gather(season, week, now, data_dir)
    registered = set(commands_doc.all_commands())
    path = page_path(season, week, data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render(inp, registered=registered, tools=week_tools.build(inp.runs)))
    if open_browser:
        webbrowser.open(path.resolve().as_uri())
    return path


def after_command(command: str) -> None:
    """Called by the CLI entry point after a command succeeded. Refreshes the page for the commands in
    `REGENERATE_AFTER`; a failure prints one warning and never fails the command."""
    if not AUTO_REGENERATE or week_tools.command_path(command) not in REGENERATE_AFTER:
        return
    try:
        path = generate()
        print(f"Week page refreshed: {path}")
    except Exception as exc:  # noqa: BLE001 - a command never fails over the page
        print(
            f"Warning: the Week page was not refreshed ({exc}); `dfs week page` rebuilds it.", file=sys.stderr
        )
