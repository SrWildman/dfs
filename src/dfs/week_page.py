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

from dfs import kickoff, launcher, nfl_calendar, runlog, week_plays, week_today, week_tools
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


def sync_guard_merged() -> bool:
    """Plan C's guard (a game-day sync never wipes data) is in this checkout: `dfs.sync_guard` is importable.
    Detected, not hand-set, so the Sunday section stays true on any branch."""
    import importlib.util

    try:
        return importlib.util.find_spec("dfs.sync_guard") is not None
    except (ImportError, ValueError):
        return False


BUILD_BUTTON = ("dfs lineups suggest", "Build")
CHALK_THRESHOLD = 0.30  # a player this owned in double-ups is "chalk" (the cash chalk check)
PROVEN, SHADOW, UNTESTED = "Proven", "Shadow", "Untested"
DATA = "Data"  # a feed, not a prediction: trust chips (Proven / Shadow / Untested) are for predictions only

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
    "plays_value": (
        PROVEN,
        "CalPts is the calibrated blend and beat TFFB's own projection on average error in Model Check; "
        "points per $1K is arithmetic on it, not a second model.",
    ),
    "plays_cash": (
        SHADOW,
        "CalPts is Proven, but the Floor and the Hit3x% / Bust% odds behind this list are not scored against "
        "results yet. The 30% chalk tag is a model estimate of double-up ownership.",
    ),
    "plays_gpp": (
        UNTESTED,
        "Boom% and the ceiling are not scored against results, and leverage (upside against ownership) has "
        "no measured edge yet: a way to look, not a rule.",
    ),
    "plays_own": (
        PROVEN,
        "Our ownership estimate beat TFFB's on average error in Model Check. It is an estimate, not the "
        "field's published ownership.",
    ),
    "plays_fade": (
        PROVEN,
        "FADE↓ (a tight end scoring above what his usage supports) hit 67% in Week 8's Model Check.",
    ),
    "plays_shadow": (
        SHADOW,
        "Proj ▼ and USAGE↓ are context chips that have not earned a verdict: shown as a second opinion only.",
    ),
    "slate": (DATA, "Odds, kickoff times, weather and injury statuses are data feeds, not predictions."),
    "changes": (
        DATA,
        "A diff of two syncs: what the feeds said then against now. A CalPts move is the model's number "
        "moving.",
    ),
}

SIGNAL_TRUST = {
    "FADE↓": "plays_fade",
    "USAGE↑": "plays_shadow",
    "USAGE↓": "plays_shadow",
    "INJ+": "plays_shadow",
    "Proj ▼": "plays_shadow",
    "Proj ▲": "plays_shadow",
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


LIVE_BUTTON = "runbtn-slot"  # `week_live.augment` turns this span into a button that runs its command
LIVE_POOL = "<!--live:pool-->"  # where the server's "Add ticked to pool" bar goes
STATIC_ONLY = "static-only"  # a paragraph the live page drops (it has the buttons instead)


def run_button(command: str, label: str) -> str:
    """Empty on the static page (which shows the phrase to say); the served page makes it a button."""
    return f'<span class="{LIVE_BUTTON}" data-command="{e(command)}" data-label="{e(label)}"></span>'


def not_run(
    what: str, say: str | None = None, command: str | None = None, button: tuple[str, str] | None = None
) -> str:
    hint = ""
    if say:
        hint += f" Say {code(chr(34) + say + chr(34), 'say')}"
    if command:
        hint += f" ({code(command)})" if say else f" Run {code(command)}"
    btn = f" {run_button(*button)}" if button else ""
    return f'<p class="notrun"><b>Not run yet.</b> {rich(what)}.{hint}{btn}</p>'


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


LIVE_CONTEST = "<!--live:contest-->"  # where the server's check form, drop zone and spend table go
LIVE_RUN = "<!--live:run-->"  # where the server's run buttons go


def weekly_budget(inp: WeekInputs) -> float | None:
    """The Weekly Budget: what `dfs contests plan` read from the Bankroll tab, else the one typed on the live
    page."""
    for source in (inp.contest_plan, inp.budget_input):
        value = num((source or {}).get("budget"))
        if value:
            return value
    return None


def tier_plan_html(inp: WeekInputs) -> str:
    """This week's plan by tier (`contest_tiers`): dollars from the Weekly Budget and, in plain words, what to
    look for in the lobby, plus the day's reminder. Rules, not a list of contests."""
    from dfs import contest_tiers as ct

    settings = inp.config.get("contest_plan") or {}
    budget = weekly_budget(inp)
    plan = (
        ct.plan_dollars(
            budget,
            showdown_max=float(settings.get("showdown_max", ct.DEFAULT_SHOWDOWN_MAX)),
            big_multi_max=float(settings.get("big_nmax_max", ct.DEFAULT_BIG_MULTI_MAX)),
        )
        if budget
        else None
    )
    rows = []
    for tier, name in ((ct.A, "Tier A"), (ct.B, "Tier B"), (ct.C, "Tier C")):
        money = e(dollars(plan.dollars(tier))) if plan else "-"
        rows.append([f"<b>{e(name)}</b>", money, e(ct.LOOK_FOR[tier])])
    rows.append(["<b>Avoid</b>", "$0", e(ct.AVOID_LOOK_FOR)])
    head = (
        f"<p><b>Weekly Budget {e(dollars(budget))}</b>: about 60% to Tier A, 30% to Tier B and at most 10% "
        f"(and ${ct.C_MAX_DOLLARS:,.0f}) to Tier C"
        + (
            f", of which Showdown ${plan.c_showdown:,.0f} and big-field multi-entry ${plan.c_big_multi:,.0f}"
            if plan
            else ""
        )
        + ".</p>"
        if budget
        else '<p class="notrun"><b>No Weekly Budget yet.</b> Say <code class="say">"contest plan"</code> '
        "(<code>dfs contests plan</code> reads it from the Bankroll tab) or type it on the live page.</p>"
    )
    day = inp.now.astimezone().weekday()
    reminder = ""
    if day == 5:
        reminder = "Saturday: set the plan before you build, and enter against it."
    elif day == 6:
        reminder = (
            "Sunday morning: check the lobby for late-released small fields and for overlay (a guaranteed "
            "contest under ~80% full within 2 hours of lock)."
        )
    return (
        "<h4>This week's plan, by tier</h4>"
        + head
        + table(["Tier", "This week", "What to look for in the lobby"], rows)
        + (f'<p class="flag">{e(reminder)}</p>' if reminder else "")
        + note(
            "Tiers are rules on a contest's format, fee, field size, entries a user and rake, so they hold "
            "when the lobby changes. The evidence is in docs/CONTESTS.md."
        )
    )


def contest_plan_section(inp: WeekInputs, registered: set[str] | None) -> Section:
    trust = TRUST["contest_plan"]
    plan = inp.contest_plan
    tiers = tier_plan_html(inp)
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
            tiers + not_run(why, command="dfs contests plan" if built else None),
            trust,
            ran=False,
        )
    parts = []
    caps = plan.get("caps") or {}
    if caps:
        rows = [[e(fmt), e(dollars(cap)) if cap is not None else "-"] for fmt, cap in caps.items()]
        parts.append("<h4>Caps by format, this week</h4>" + table(["Format", "Cap a week"], rows))
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
    parts.append(note("Last week's spend against this plan is under Last week."))
    return Section("contest-plan", "Contest plan", tiers + "".join(parts), trust, asof=_saved(inp, plan))


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
                button=BUILD_BUTTON,
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
                "No GPP builds are saved for this week",
                say="build me lineups",
                command="dfs lineups suggest",
                button=BUILD_BUTTON,
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
        if sync_guard_merged()
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
            not_run(
                what,
                say="late swap",
                command="dfs lineups late-swap --refresh-injuries",
                button=("dfs lineups late-swap --refresh-injuries", "Late swap"),
            )
            + guard_html,
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


_EMPTY_SLOT = re.compile(r"^SWAP (\S+) \(empty\): nobody in this slot(?: -> (.*))?$")


def urgent_text(alert: str) -> str:
    """A SWAP alert as one plain sentence: an empty slot says which one (Choppa with no QB set)."""
    m = _EMPTY_SLOT.match(alert)
    if m:
        slot, swap = m.group(1), m.group(2)
        return f"no {slot} in your lineup (the {slot} slot is empty)" + (f": {swap}" if swap else "")
    return alert.removeprefix("SWAP ").strip()


def season_urgent(leagues: dict) -> list[tuple[str, str]]:
    """(league, sentence) for every SWAP alert (an empty slot, a bye, an OUT starter): shown first, in red."""
    return [
        (name, urgent_text(a))
        for name, lg in sorted(leagues.items())
        for a in (lg.get("alerts") or [])
        if str(a).startswith("SWAP")
    ]


def top_move(lg: dict) -> str:
    """The league's best move: the free move the sync printed (headline line 2), else its top pickup."""
    for line in (lg.get("headline") or [])[1:]:
        text = str(line).strip()
        if text.startswith("free move first:"):
            return text.removeprefix("free move first:").strip()
    pickups = lg.get("pickups") or []
    return str(pickups[0]) if pickups else ""


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
    urgent = season_urgent(leagues)
    banner = (
        '<div class="urgent"><b>Urgent:</b>'
        + bullets([f"<b>{e(name)}</b>: {e(text)}" for name, text in urgent])
        + "</div>"
        if urgent
        else '<p class="ok">Nothing urgent: every starter is set in every league.</p>'
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
        move = top_move(lg)
        if move:
            parts.append(f"<p><b>Top move:</b> {e(move)}</p>")
        alert_note = f" (checked {e(week_today.when(inp.now, checked))})" if checked else ""
        parts.append(f"<h5>Lineup problems {_chip('season_alerts')}{alert_note}</h5>")
        parts.append(
            bullets(
                [
                    f'<span class="{"bad" if str(a).startswith("SWAP") else "warn"}">{e(a)}</span>'
                    for a in alerts
                ]
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
        banner + f'<div class="leagues">{"".join(cards)}</div>',
        None,
        asof=_saved(inp, inp.season_long),
    )


def spent_vs_plan_html(plan: dict | None) -> str:
    """Last week's spend against the format caps (what `dfs contests plan` saved as `spent`)."""
    plan = plan or {}
    caps, spent = plan.get("caps") or {}, plan.get("spent") or {}
    if not caps and not plan.get("line"):
        return ""
    out = "<h4>Spent against the plan</h4>"
    if plan.get("line"):
        out += f"<p><b>{e(plan['line'])}</b></p>"
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
    return out + (table(["Format", "Cap a week", "Spent", ""], rows) if rows else "")


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
    body.append(spent_vs_plan_html(inp.contest_plan))
    tw = inp.tier_week
    if tw and tw.get("line"):
        over = f' <span class="bad">Over: {e("; ".join(tw["over"]))}</span>' if tw.get("over") else ""
        body.append(f"<h4>The week by tier (planned against spent)</h4><p><b>{e(tw['line'])}</b>{over}</p>")
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


# -- plays, slate, what changed -----------------------------------------------------------------------------


def sync_stamp(inp: WeekInputs) -> str:
    at = max(
        (parse_time(v.get("synced_at")) for v in inp.manifest.values() if v.get("synced_at")), default=None
    )
    return f"as of the sync {week_today.when(inp.now, at)}" if at else ""


def signal_chip(token: str) -> str:
    label, why = TRUST[SIGNAL_TRUST.get(token, "plays_shadow")]
    return f'<span class="sig {label.lower()}" title="{e(token)}: {e(why)}">{e(token)} {e(label)}</span>'


def pool_slot(name: str) -> str:
    """Empty on the static page; the served page makes it a tick box that adds him with `dfs pool add`."""
    return f'<span class="pool-slot" data-name="{e(name)}"></span>'


def player_cell(p: week_plays.Play) -> str:
    bits = [f"<b>{e(p.name)}</b>"]
    if p.avail in week_plays.STATUS_WORD:
        bits.append(f'<span class="sig bad">{e(week_plays.STATUS_WORD[p.avail])}</span>')
    bits += [signal_chip(c) for c in p.chips]
    if p.in_pool:
        bits.append(f'<span class="sig proven">in pool ({e(p.in_pool)})</span>')
    return " ".join(bits)


def game_cell(p: week_plays.Play) -> str:
    return e(p.game) + (f'<br><span class="dim">{e(p.kickoff)}</span>' if p.kickoff else "")


def own_cell(value: float | None, pending: bool, *, chalk: bool = False) -> str:
    if pending:
        return '<span class="dim">publishes ~Fri/Sat</span>'
    if value is None:
        return "-"
    tag = '<br><span class="bad">chalk: play or fade on purpose</span>' if chalk else ""
    return f"{e(pct(value))}{tag}"


def f1(value: float | None) -> str:
    return "-" if value is None else f"{value:.1f}"


def f0(value: float | None) -> str:
    return "-" if value is None else f"{value:.0f}"


def plays_table(headers: list[str], by_pos: dict[str, list], row_cells) -> str:
    """One table, a band per position; `row_cells(play)` returns the cells after the pool tick."""
    head = '<th class="poolcol">Pool</th>' + "".join(f"<th>{h}</th>" for h in headers)
    body = []
    width = len(headers) + 1
    for pos in week_plays.POSITIONS:
        rows = by_pos.get(pos) or []
        if not rows:
            continue
        body.append(f'<tr class="pos"><td colspan="{width}">{e(pos)}</td></tr>')
        for p in rows:
            body.append(
                f'<tr><td class="poolcol">{pool_slot(p.name)}</td>'
                + "".join(f"<td>{c}</td>" for c in row_cells(p))
                + "</tr>"
            )
    if not body:
        return '<p class="dim">Nobody qualifies yet.</p>'
    rows = "".join(body)
    return (
        f'<div class="scroll"><table class="playtbl"><thead><tr>{head}</tr></thead>'
        f"<tbody>{rows}</tbody></table></div>"
    )


def trust_chip(key: str) -> str:
    return _chip(key)


def plays_section(inp: WeekInputs) -> Section:
    if not inp.edge:
        return Section(
            "plays",
            "Plays",
            not_run("Nothing is synced, so there are no plays", say="sync", command="dfs sync"),
            TRUST["plays_value"],
            ran=False,
        )
    view = week_plays.plays(inp.edge, inp.pool)
    if view.empty:
        return Section(
            "plays",
            "Plays",
            not_run("The synced slate has no priced, projected players", say="sync", command="dfs sync"),
            TRUST["plays_value"],
            ran=False,
        )
    cash_pending, gpp_pending = view.cash_own_pending, view.gpp_own_pending
    body = [
        note(
            f"Five a position from {view.players} healthy, priced players on the synced slate. CalPts is the "
            "calibrated projection; a player who is Questionable stays on the list, tagged."
        )
    ]
    if cash_pending or gpp_pending:
        pending_banner = week_plays.OWN_NOT_OUT[0].upper() + week_plays.OWN_NOT_OUT[1:]
        body.append(
            f'<p class="flag">{e(pending_banner)}. The chalk tags and the leverage list appear then.</p>'
        )

    def game_why(p: week_plays.Play, token: str = "") -> tuple[str, str]:
        record = ""
        if token:
            line = week_plays.record_line(token, inp.signal_table, inp.scored_weeks)
            record = f'<br><span class="record">{e(line)}</span>'
        return game_cell(p), f'<span class="why-line">{e(p.why)}</span>{record}'

    body.append(f"<h4>Best projected value {trust_chip('plays_value')}</h4>")
    body.append(note("CalPts per $1K of salary, CalPts breaking ties."))
    body.append(
        plays_table(
            ["Player", "Salary", "CalPts", "Per $1K", "Game", "Why"],
            view.value,
            lambda p: [player_cell(p), e(f"${p.salary:,.0f}"), f1(p.calpts), f1(p.value), *game_why(p)],
        )
    )
    own_head = "Double-up own" + (f" ({e(week_plays.OWN_NOT_OUT)})" if cash_pending else "")
    body.append(f"<h4>Cash core candidates {trust_chip('plays_cash')} {trust_chip('plays_own')}</h4>")
    body.append(
        note(
            "CalPts and value both at least the position's median, best Floor first. Floor is the 20th "
            "percentile of his outcomes."
        )
    )
    body.append(
        plays_table(
            ["Player", "Salary", "CalPts", "Floor", "Hit3x / Bust", own_head, "Game", "Why"],
            view.cash,
            lambda p: [
                player_cell(p),
                e(f"${p.salary:,.0f}"),
                f1(p.calpts),
                f1(p.floor),
                f"{f0(p.hit3x)}% / {f0(p.bust)}%",
                own_cell(p.cash_own, cash_pending, chalk=p.chalk),
                *game_why(p),
            ],
        )
    )
    gpp_note = (
        "Best Boom% (the chance of 4x salary), ceiling breaking ties. Leverage shows once ownership is out."
        if gpp_pending
        else "Top half of each position's Boom%, ordered by leverage: Boom% rank minus ownership rank within "
        "the position (the sheet's Lev), so low-owned upside comes first."
    )
    gpp_head = "GPP own" + (f" ({e(week_plays.OWN_NOT_OUT)})" if gpp_pending else "")
    body.append(f"<h4>GPP upside {trust_chip('plays_gpp')} {trust_chip('plays_own')}</h4>")
    body.append(note(gpp_note))
    body.append(
        plays_table(
            ["Player", "Salary", "CalPts", "Ceiling", "Boom%", gpp_head, "Leverage", "Game", "Why"],
            view.gpp,
            lambda p: [
                player_cell(p),
                e(f"${p.salary:,.0f}"),
                f1(p.calpts),
                f1(p.ceiling),
                f"{f0(p.boom)}%",
                own_cell(p.gpp_own, gpp_pending),
                ('<span class="dim">-</span>' if p.lev is None else e(f"{p.lev:+.0f}")),
                *game_why(p),
            ],
        )
    )
    body.append(f"<h4>Fades {trust_chip('plays_fade')}</h4>")
    if view.fades:
        body.append(
            plays_table(
                ["Player", "Salary", "CalPts", "Game", "Why"],
                {"TE": view.fades},
                lambda p: [
                    player_cell(p),
                    e(f"${p.salary:,.0f}"),
                    f1(p.calpts),
                    *game_why(p, week_plays.FADE),
                ],
            )
        )
        body.append(note("FADE↓ is a tight-end signal: fade him or pay for it on purpose."))
    else:
        body.append('<p class="ok">No FADE↓ on the slate this week.</p>')
    if view.shadow:
        body.append(f"<h4>Other warning chips {trust_chip('plays_shadow')}</h4>")
        body.append(
            plays_table(
                ["Player", "Salary", "CalPts", "Game", "Why"],
                {p.pos: [x for x in view.shadow if x.pos == p.pos] for p in view.shadow},
                lambda p: [
                    player_cell(p),
                    e(f"${p.salary:,.0f}"),
                    f1(p.calpts),
                    *game_why(p, next((c for c in p.chips if c in week_plays.SHADOW_TOKENS), "")),
                ],
            )
        )
        body.append(
            note("Shadow: unproven chips on players near the top of the lists. A second opinion only.")
        )
    body.append(LIVE_POOL)
    body.append(
        f'<p class="note {STATIC_ONLY}">To add players to your pool, say '
        f"{code(chr(34) + 'add X, Y to my pool' + chr(34), 'say')} ("
        f"{code('dfs pool add ' + chr(34) + 'Name' + chr(34))}). The served page "
        "(<code>dfs week serve</code>) "
        "has a tick box on every row.</p>"
    )
    return Section("plays", "Plays", "".join(body), TRUST["plays_value"], asof=sync_stamp(inp))


def signed(value: float | None, digits: int = 1) -> str:
    if value is None:
        return "-"
    return "0" if abs(value) < 0.05 else f"{value:+.{digits}f}"


def slate_section(inp: WeekInputs) -> Section:
    trust = TRUST["slate"]
    games = week_plays.slate(inp.edge)
    if not games:
        return Section(
            "slate",
            "Slate",
            not_run("Nothing is synced", say="sync", command="dfs sync"),
            trust,
            ran=False,
        )
    rows = []
    for g in games:
        title = f"{g.away} @ {g.home}" if g.away and g.home else g.key
        cls = []
        if g.top:
            cls.append("top")
        if g.moved:
            cls.append("moved")
        implied = " / ".join(f"{t} {v:.1f}" for t, v in g.implied.items()) if g.implied else "-"
        if g.favorite and g.spread is not None:
            spread = f"{g.favorite} {g.spread:+g}"
        else:
            spread = "-"
        move = (
            f"total {signed(g.tot_move)}, spread {signed(g.spread_move)}"
            if g.tot_move is not None or g.spread_move is not None
            else '<span class="dim">no opening yet</span>'
        )
        if g.moved:
            move = f'<span class="bad">{move}</span>'
        inj = ", ".join(
            f"{e(n)} ({e(pos)} {e(week_plays.STATUS_WORD.get(st, st))})" for n, pos, _t, st in g.injuries
        )
        rows.append(
            '<tr class="'
            + " ".join(cls)
            + '">'
            + "".join(
                f"<td>{c}</td>"
                for c in [
                    f"<b>{e(title)}</b>" + (' <span class="tag">top 3 total</span>' if g.top else ""),
                    e(g.kickoff) or "-",
                    e(f"{g.total:g}") if g.total is not None else "-",
                    e(spread),
                    e(implied),
                    move,
                    e(g.weather) or "-",
                    inj or '<span class="dim">none</span>',
                ]
            )
            + "</tr>"
        )
    head = "".join(
        f"<th>{h}</th>"
        for h in [
            "Game",
            "Kickoff (ET)",
            "Total",
            "Spread",
            "Implied totals",
            "Moved since the opening",
            "Weather and roof",
            "Key injuries (OUT, Doubtful, Questionable)",
        ]
    )
    body = [
        f'<div class="scroll"><table class="slate"><thead><tr>{head}</tr></thead>'
        f"<tbody>{''.join(rows)}</tbody></table></div>",
        note(
            "Highest total first. The shaded rows are the three best scoring environments; red marks a total "
            f"that moved {week_plays.TOTAL_MOVED:g}+ points since the opening snapshot. Key injuries are a "
            "team's likely starters (top salaries: QB1, RB1-2, WR1-3, TE1) who are not healthy."
        ),
    ]
    if not week_plays.opening_known(games):
        body.append(
            not_run(
                "No opening odds snapshot has been diffed yet, so movement is blank. Snapshot the lines "
                "(Tuesday morning is the opening) and the next sync fills it",
                command="dfs odds snapshot",
            )
        )
    return Section("slate", "Slate", "".join(body), trust, asof=sync_stamp(inp))


def change_list(items: list[week_plays.Change], empty: str, limit: int = 12) -> str:
    if not items:
        return f'<p class="ok">{e(empty)}</p>'
    lines = [("<b>yours</b> " if c.mine else "") + e(c.text) for c in items[:limit]]
    more = f'<p class="note">and {len(items) - limit} more.</p>' if len(items) > limit else ""
    return bullets(lines) + more


def mine_names(inp: WeekInputs) -> set[str]:
    names = set(inp.pool)
    for b in (inp.suggest or {}).get("builds", []):
        names.update(b.get("names", []))
    return names


def changes_section(inp: WeekInputs) -> Section:
    trust = TRUST["changes"]
    if not inp.edge:
        return Section(
            "changes",
            "What changed",
            not_run("Nothing is synced", say="sync", command="dfs sync"),
            trust,
            ran=False,
        )
    if not inp.prev_edge:
        return Section(
            "changes",
            "What changed",
            not_run(
                "There is no earlier sync of this week to compare with. After the next sync this shows what"
                " moved",
                say="sync",
                command="dfs sync",
            ),
            trust,
            ran=False,
        )
    diff = week_plays.changes(inp.edge, inp.prev_edge, mine_names(inp))
    since = week_today.when(inp.now, inp.prev_edge_at) if inp.prev_edge_at else "the previous sync"
    body = [
        note(f"Since the sync {since}: your pool and every Suggested build, new OUTs, and the biggest moves.")
    ]
    body.append("<h4>Your players: status changes</h4>")
    body.append(change_list(diff.status, "No status change for anyone in your pool or builds."))
    body.append("<h4>New OUTs</h4>")
    body.append(change_list(diff.new_outs, "Nobody new is OUT or on IR."))
    body.append(f"<h4>CalPts moves of {week_plays.CALPTS_MOVED:g}+ points</h4>")
    body.append(change_list(diff.calpts, "No CalPts moved that far."))
    body.append(f"<h4>Line moves of {week_plays.LINE_MOVED:g}+ point</h4>")
    body.append(change_list(diff.lines, "No total or spread moved that far."))
    return Section("changes", "What changed", "".join(body), trust, asof=sync_stamp(inp))


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
.chip.data{color:var(--dim)}
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
.playtbl td,.slate td{font-size:13px}
.playtbl .pos td{background:rgba(127,127,127,.12);font-weight:700;font-size:12px;padding:3px 8px}
.playtbl td:last-child{min-width:230px}
.playtbl td:nth-last-child(2){min-width:150px}
.poolcol{display:none}body.live .poolcol{display:table-cell}
.sig{display:inline-block;font-size:11px;font-weight:600;border-radius:999px;padding:0 7px;margin-left:2px;
border:1px solid currentColor;white-space:nowrap}
.sig.proven{color:var(--proven)}.sig.shadow{color:var(--shadow)}.sig.untested{color:var(--untested)}
.sig.bad{color:var(--bad)}
.tag{display:inline-block;font-size:11px;border-radius:4px;padding:0 6px;background:rgba(31,94,255,.15);
color:var(--accent);white-space:nowrap}
.why-line{color:var(--dim)}
.record{font-size:12px;color:var(--proven)}
.slate tr.top td{background:rgba(31,94,255,.07)}
.slate td{min-width:80px}
.urgent{border:2px solid var(--bad);border-radius:8px;padding:6px 12px;margin:6px 0 10px;color:var(--bad)}
.urgent ul{margin:2px 0}
.pool-tick{width:16px;height:16px}
.poolbar{margin:10px 0;display:flex;gap:10px;align-items:center}
footer{max-width:1040px;margin:0 auto;padding:6px 16px 30px;color:var(--dim);font-size:12.5px}
"""


def week_dir(data_dir: Path | None = None) -> Path:
    return (data_dir or data_root()) / "week"


def page_path(season: int, week: int, data_dir: Path | None = None) -> Path:
    return week_dir(data_dir) / f"{season}-wk{week:02d}.html"


# The page is ordered by day: what Sam needs first today comes first, and everything else follows. Tuesday to
# Friday is research (who to play, the games, what moved); Saturday is build and enter; Sunday is game day.
_REST = ["unknowns", "cash", "gpp", "sunday"]
DAY_ORDER: dict[int, list[str]] = {
    0: ["last-week", "season", "changes", "plays", "slate", "contest-plan", *_REST],
    1: ["plays", "slate", "changes", "contest-plan", "season", "last-week", *_REST],
    5: [
        "contest-plan",
        "plays",
        "cash",
        "gpp",
        "slate",
        "changes",
        "unknowns",
        "season",
        "last-week",
        "sunday",
    ],
    6: [
        "sunday",
        "changes",
        "cash",
        "gpp",
        "unknowns",
        "slate",
        "plays",
        "contest-plan",
        "season",
        "last-week",
    ],
}
DAY_ORDER[2] = DAY_ORDER[3] = DAY_ORDER[4] = DAY_ORDER[1]
SECTION_IDS = [
    "plays",
    "slate",
    "changes",
    "contest-plan",
    "cash",
    "gpp",
    "unknowns",
    "sunday",
    "season",
    "last-week",
]


def section_order(day: int) -> list[str]:
    """The section ids in the order the page shows them on weekday `day` (0 = Monday)."""
    order = DAY_ORDER.get(day, DAY_ORDER[1])
    return [i for i in order if i in SECTION_IDS] + [i for i in SECTION_IDS if i not in order]


def render(
    inp: WeekInputs, *, registered: set[str] | None = None, tools: list[week_tools.Tool] | None = None
) -> str:
    """The whole page as one HTML string."""
    if tools is None:
        tools = week_tools.build(inp.runs)
    day = inp.now.astimezone().weekday()
    built = {
        s.id: s
        for s in (
            plays_section(inp),
            slate_section(inp),
            changes_section(inp),
            contest_plan_section(inp, registered),
            cash_section(inp),
            gpp_section(inp),
            unknowns_section(inp),
            sunday_section(inp),
            season_section(inp),
            last_week_section(inp),
        )
    }
    sections = [built[i] for i in section_order(day)]
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
        + '<h2 class="sec">This week</h2>'
        + "".join(s.html() + (LIVE_CONTEST if s.id == "contest-plan" else "") for s in sections)
        + LIVE_RUN
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
