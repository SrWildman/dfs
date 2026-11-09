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
import json
import re
import sys
import webbrowser
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from dfs import kickoff, nfl_calendar, runlog, week_plays, week_today, week_tools
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


NUMBERS_ANCHOR = (
    "numbers"  # "How good are our numbers" (`week_learn.numbers_section`): every chip links there
)


def chip_html(label: str, why: str) -> str:
    """A trust chip (Proven / Shadow / Untested / Data): a link to the numbers section, its reason on
    hover."""
    return (
        f'<a class="chip {html.escape(label.lower(), quote=True)}" href="#{NUMBERS_ANCHOR}" '
        f'title="{html.escape(why, quote=True)} (click: how good are our numbers)">{html.escape(label)}</a>'
    )


def e(text: object) -> str:
    return html.escape(str(text), quote=True)


def rich(text: object) -> str:
    """Escaped text with `backticks` shown as code."""
    return re.sub(r"`([^`]+)`", r"<code>\1</code>", e(text))


# -- small HTML helpers -----------------------------------------------------------------------------


def pn(name: object, team: object = "", pid: object = "") -> str:
    """A player's name in a section that holds only his name (Plan O item 2): a link the page resolves to
    his id from the Explorer's data (by name, and team where two share one), which opens the drawer and
    carries the pool chip (`week_ui`). An id, when the section has it, is used as is."""
    name = str(name or "")
    if not name:
        return ""
    attrs = (f' data-id="{e(pid)}"' if pid else "") + f' data-name="{e(name)}"'
    attrs += f' data-team="{e(team)}"' if team else ""
    return f'<a class="pl" href="#"{attrs}>{e(name)}</a>'


def pns(names) -> str:
    """A comma-separated list of names, each a `pn` link."""
    return ", ".join(pn(n) for n in names or [] if n)


def add_button(pid: object, kind: str) -> str:
    """One click adds a player the page lists as missing from the pool: `kind` (Cash / GPP / Both), merged
    with what he already has (GPP + Cash = Both). The page hides it once he is in at that kind."""
    return (
        f'<button type="button" class="addpool" data-id="{e(pid)}" data-v="{e(kind)}" '
        f'title="Add him to your pool as {e(kind)} (one write to EdgeRaw\'s Pool cell)">+ {e(kind)}</button>'
    )


def how_html(text: str) -> str:
    """The one-line "how this is built" note every table and chart carries (Plan N item 14): shown on hover,
    and on a tap (the shared script toggles it), never in the way."""
    return (
        f'<span class="how" tabindex="0" role="note" data-how="{e(text)}" '
        f'aria-label="How this is built: {e(text)}">'
        "ⓘ how this is built</span>"
    )


CAP_ASOF = '<span class="cap-asof"></span>'  # filled with the section's data date (`fill_asof`)


def caption(text: str, src: tuple[str, ...] | list[str] = ()) -> str:
    """The visible caption under a table or chart (Plan O item 4): how it is built (the formula), the
    source columns, and the date of the data (the section's own, else the last sync's; `fill_asof`)."""
    cols = f" From: {', '.join(src)}." if src else ""
    return f'<p class="cap">{rich(text)}{e(cols)} {CAP_ASOF}</p>'


def fill_asof(body: str, asof: str) -> str:
    """Every caption's data date in `body`: `asof`, else the page's last sync."""
    when = asof or _ASOF.get()
    return body.replace(CAP_ASOF, f'<span class="cap-asof">Data: {e(when)}.</span>' if when else "")


def table(
    headers: list[str],
    rows: list[list[str]],
    *,
    how: str,
    cls: str = "",
    src: tuple[str, ...] | list[str] = (),
) -> str:
    """Cells are already-escaped HTML. `how` is the table's one-line "how this is built" note (required: every
    table says how it is built); it shows as the caption under the table (Plan O item 4) with `src`, the
    columns it reads, and the data's date. The "how this is built" tip above it carries the same note. The
    header is sticky inside its scroll box; `cls="stickyname"` keeps the first column in view when the
    table scrolls sideways."""
    head = "".join(f"<th>{e(h)}</th>" for h in headers)
    body = "".join("<tr>" + "".join(f"<td>{c}</td>" for c in row) + "</tr>" for row in rows)
    return (
        f'<div class="tbl">{how_html(how)}<div class="scroll"><table class="{cls}">'
        f"<thead><tr>{head}</tr></thead>"
        f"<tbody>{body}</tbody></table></div>{caption(how, src)}</div>"
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
            chip = chip_html(label, why)
        asof = f'<span class="asof">{e(self.asof)}</span>' if self.asof else ""
        trust_line = (
            f'<p class="trust"><b>{e(self.trust[0])}.</b> {rich(self.trust[1])}</p>' if self.trust else ""
        )
        cls = "card" if self.ran else "card notrun-card"
        return (
            f'<section class="{cls}" id="{e(self.id)}"><div class="card-head"><h3>{e(self.title)}</h3>'
            f"{chip}{asof}</div>{fill_asof(self.body, self.asof)}{trust_line}</section>"
        )


LIVE_BUTTON = "runbtn-slot"  # `week_live.augment` turns this span into a button that runs its command
STATIC_ONLY = "static-only"  # a paragraph the live page drops (it has the buttons instead)


def run_button(command: str, label: str) -> str:
    """Empty on the static page (which shows the phrase to say); the served page makes it a button."""
    return f'<span class="{LIVE_BUTTON}" data-command="{e(command)}" data-label="{e(label)}"></span>'


_ASOF: ContextVar[str] = ContextVar("week_page_asof", default="")  # the last sync, for captions
_LIVE: ContextVar[bool] = ContextVar("week_page_live", default=False)  # the served page: no command text


def not_run(
    what: str, say: str | None = None, command: str | None = None, button: tuple[str, str] | None = None
) -> str:
    """ "Not run yet" and what produces it: the phrase to say and (static page) the command. The served page
    shows the phrase (it gets an Ask button) and the run button, never the command text."""
    hint = ""
    if say:
        hint += f" Say {code(chr(34) + say + chr(34), 'say')}"
    if command and not _LIVE.get():
        hint += f" ({code(command)})" if say else f" Run {code(command)}"
    btn = f" {run_button(*button)}" if button else ""
    return f'<p class="notrun"><b>Not run yet.</b> {rich(what)}.{hint}{btn}</p>'


# -- Today ------------------------------------------------------------------------------------------


def last_tag(inp: WeekInputs, run: runlog.Run | None) -> str:
    if run is None:
        return '<span class="last never">no run logged</span>'
    ok = "" if run.ok else " (failed)"
    return f'<span class="last">last run {e(week_today.when(inp.now, run.time))}{ok}</span>'


def suggest_time(inp: WeekInputs) -> datetime | None:
    return parse_time((inp.suggest or {}).get("at"))


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
        + table(
            ["Tier", "This week", "What to look for in the lobby"],
            rows,
            how="Your Bankroll tab's Weekly Budget split about 60 / 30 / at most 10% across Tiers A / B / C "
            "(contest_tiers); the look-for text is the tier's rule in plain words.",
        )
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
        parts.append(
            "<h4>Caps by format, this week</h4>"
            + table(
                ["Format", "Cap a week"],
                rows,
                how="What dfs contests plan saved: the [contest_plan] caps by format against this week's "
                "Weekly Budget.",
            )
        )
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
            "<h4>Your ROI and the rake, by format</h4>"
            + table(
                ["Format", "Entries", "ROI", "Rake"],
                rows,
                how="Your contest log: ROI = net / fees by format; rake = 1 - prize pool / (entries x fee).",
            )
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
            chalk.append(
                {
                    "name": r["Name"],
                    "pos": r.get("Position", ""),
                    "team": r.get("Team", ""),
                    "own": own,
                    "used": used,
                }
            )
    if not published:
        return None, len(cash)
    return sorted(chalk, key=lambda c: -c["own"]), len(cash)


CHALK_CHECK_HEADER = "everyone predicted in 30% or more of double-up lineups (CashOwn)"  # Plan N item 12
CLOSE_COUNT = 3  # the next ones under the line, shown as "close" (Plan N item 12)


def cash_close(inp: WeekInputs) -> list[dict]:
    """The `CLOSE_COUNT` highest CashOwn players under the 30% line (shown as "close")."""
    cash = builds_of(inp, "cash")
    under = []
    for r in inp.edge or []:
        own = num(r.get("CashOwn"))
        if own is not None and own < CHALK_THRESHOLD:
            used = sum(1 for b in cash if r["Name"] in b.get("names", []))
            under.append(
                {
                    "name": r["Name"],
                    "pos": r.get("Position", ""),
                    "team": r.get("Team", ""),
                    "own": own,
                    "used": used,
                }
            )
    return sorted(under, key=lambda c: -c["own"])[:CLOSE_COUNT]


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
                pns(b.get("names", [])),
                e(f"${int(m['salary']):,}") if m.get("salary") else "-",
                f"<b>{e(pct(shown))}</b>",
            ]
        )
    body = [
        table(
            ["Build", "Players", "Salary", "Cash %"],
            rows,
            how="The last dfs lineups suggest round's cash builds, the top 5 by the Cash % the simulator "
            "gave each (its chance to clear the double-up line).",
        ),
        note(cash_note(inp, calibrated)),
    ]
    chalk, n_builds = cash_chalk(inp)
    close = cash_close(inp)
    count = f" ({len(chalk)})" if chalk is not None else ""
    body.append(f"<h4>Cash chalk check: {e(CHALK_CHECK_HEADER)}{count} {_chip('cash_chalk')}</h4>")
    if chalk is None:
        body.append(
            not_run(
                "CashOwn is not out yet (it needs TFFB's ownership), so there is no chalk to check",
                command="dfs sync",
            )
        )
    else:
        if not chalk:
            body.append('<p class="ok">No player is expected at 30%+ in double-ups.</p>')
        rows = [
            [
                pn(c["name"], c.get("team", "")),
                e(c["pos"]),
                e(pct(c["own"])),
                f'<span class="{"bad" if c["used"] == 0 else ""}">{c["used"]} of {n_builds}</span>',
            ]
            for c in chalk
        ]
        rows += [
            [
                pn(c["name"], c.get("team", "")) + ' <span class="chip close">close</span>',
                e(c["pos"]),
                e(pct(c["own"])),
                f"{c['used']} of {n_builds}",
            ]
            for c in close
        ]
        if rows:
            body.append(
                table(
                    ["Player", "Pos", "Double-up own", "Cash builds using him"],
                    rows,
                    cls="stickyname",
                    how="Every player at 30%+ CashOwn (our double-up ownership model), then the next 3 under "
                    "the line as close; how many of the last round's cash builds hold each.",
                )
            )
        body.append(
            note("Fading a 30%+ player should be a choice, not an accident. The builder does not use this.")
        )
    return Section("cash", "Cash", "".join(body), trust, asof=round_label(inp))


def _chip(key: str) -> str:
    label, why = TRUST[key]
    return chip_html(label, why)


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
                    pns(b.get("names", [])),
                    e(pct(num(m.get("p_gpp")), 1)),
                ]
            )
        body.append(
            table(
                ["Build", "Shape", "Players", "Top 1%"],
                rows,
                how="The last dfs lineups suggest round's GPP builds, the top 3 by simulated Top 1% (the "
                "chance to finish in the top 1% of the generated GPP field).",
            )
        )
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
    body.append(f"<h4>Top 3 stacks {chip_html(label, why)}</h4>")
    if stacks and stacks.get("rows"):
        rows = [
            [
                pn(r["qb"]),
                pns(str(r.get("mates") or "").split(", ")),
                e(r["game"]),
                e(pct(num(r.get("top1")), 1)),
                e(f"{r['lift']:.1f}x") if num(r.get("lift")) is not None else "-",
                e(pct(num(r.get("lev")), 1)) if r.get("lev") is not None else "-",
                code(r.get("rule", "")),
            ]
            for r in stacks["rows"]
        ]
        body.append(
            table(
                ["QB", "Teammates", "Game", "Top 1%", "Lift", "Lev", "Type in Suggested's rules box"],
                rows,
                how="The Stacks tab's top rows as dfs stacks saved them: each QB stack's simulated Top 1% "
                "rate against how much of the generated GPP field plays it.",
            )
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
            + ", ".join(
                pn(r["Name"], r.get("Team", ""), r.get("Id", ""))
                + e(f" ({r['Avail']}, in {in_builds[r['Name']]} builds)")
                for r in outs
            )
            + ".</p>"
        )
    if shown:
        shown.sort(key=lambda r: r.get("GameStart") or "9")
        rows = [
            [
                pn(r["Name"], r.get("Team", ""), r.get("Id", "")),
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
            + table(
                ["Player", "Team", "Status", "Kickoff (ET)", "Yours"],
                rows,
                how="The last sync's Questionable and Doubtful players in your pool or builds (else the top "
                "by projection), by kickoff.",
            )
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
        table(
            ["Lineup", "Open slots", "Position", "Best swap"],
            rows,
            how="The last dfs lineups late-swap run (data/cockpit/late_swap.json): each lineup's unlocked "
            "slots and the best change it found.",
        ),
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
    return out + (
        table(
            ["Format", "Cap a week", "Spent", ""],
            rows,
            how="What dfs contests plan saved: last week's spend by format (contest log) against the caps.",
        )
        if rows
        else ""
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
        table(
            ["Format", "Entries", "Fees", "Net", "ROI"],
            rows,
            how="Your contest log (DK's history export, read at week close): paid NFL entries of the newest "
            "week, net = winnings minus fees.",
        ),
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
    from dfs import week_learn

    body.append(week_learn.scorecard_html(inp))
    return Section("last-week", "Last week", "".join(body), trust)


# -- plays, slate, what changed -----------------------------------------------------------------------------


def sync_stamp(inp: WeekInputs) -> str:
    at = max(
        (parse_time(v.get("synced_at")) for v in inp.manifest.values() if v.get("synced_at")), default=None
    )
    return f"as of the sync {week_today.when(inp.now, at)}" if at else ""


def change_list(items: list[week_plays.Change], empty: str, limit: int = 12) -> str:
    if not items:
        return f'<p class="ok">{e(empty)}</p>'

    def text(c: week_plays.Change) -> str:
        if c.name and c.text.startswith(c.name):
            return pn(c.name, c.team, c.pid) + e(c.text[len(c.name) :])
        return e(c.text)

    lines = [("<b>yours</b> " if c.mine else "") + text(c) for c in items[:limit]]
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
        body = table(
            ["Say to Claude Code", "Command", "What you get back", "Last run"],
            rows,
            cls="tools",
            how="Every command, grouped by the day you use it (week_tools); last run from data/runlog.csv.",
        )
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
:root{--bg:#f5f6f3;--card:#fff;--ink:#1c2420;--dim:#5f6b65;--line:#dde2de;--accent:#2d5f8a;
--accent-soft:#e1ecf5;
--proven:#2f6b4f;--proven-soft:#e2efe7;--shadow:#8a6d1f;--shadow-soft:#f4ecd4;--untested:#62706a;
--bad:#b8462e;
--bad-soft:#f7e4de;--warn:#8a5a00;--ok:#2f6b4f;--flag:#fff3d6;--wr:#2d5f8a;--rb:#2f6b4f;--te:#a8781a;--s1:#2a78d6;--s2:#eb6834;--s3:#1baf7a}
@media (prefers-color-scheme:dark){:root:not([data-theme=light]){--bg:#121513;--card:#1a1f1c;--ink:#e6ebe8;
--dim:#9aa69f;--line:#2d3531;--accent:#7fb2df;--accent-soft:#18283a;--proven:#73c197;--proven-soft:#1d3328;
--shadow:#d8bf73;--shadow-soft:#332b16;--untested:#9aa69f;--bad:#ec8a72;--bad-soft:#3a221c;--warn:#e0b04a;
--ok:#73c197;--flag:#3a3015;--wr:#7fb2df;--rb:#73c197;--te:#e0b04a;--s1:#3987e5;--s2:#d95926;--s3:#199e70}}
:root[data-theme=dark]{--bg:#121513;--card:#1a1f1c;--ink:#e6ebe8;--dim:#9aa69f;--line:#2d3531;
--accent:#7fb2df;
--accent-soft:#18283a;--proven:#73c197;--proven-soft:#1d3328;--shadow:#d8bf73;--shadow-soft:#332b16;
--untested:#9aa69f;--bad:#ec8a72;--bad-soft:#3a221c;--warn:#e0b04a;--ok:#73c197;--flag:#3a3015;--wr:#7fb2df;
--rb:#73c197;--te:#e0b04a;--s1:#3987e5;--s2:#d95926;--s3:#199e70}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.5 -apple-system,
BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif}
header,main{max-width:2400px;margin:0 auto;padding:0 16px}header{padding-top:18px}
h1{font-size:26px;margin:0}h2{font-size:17px;margin:0}h3{font-size:15.5px;margin:0}h4{font-size:14px;
margin:14px 0 6px}
h5{font-size:13px;margin:10px 0 4px;color:var(--dim)}
.sub{color:var(--dim);margin:2px 0 14px}
a{color:var(--accent)}a.pl{color:inherit;text-decoration:none;border-bottom:1px dotted var(--dim);
cursor:pointer}
a.pl:hover{color:var(--accent);border-color:var(--accent)}
abbr[title]{text-decoration:underline dotted;cursor:help}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px 16px;
margin:0 0 14px;
min-width:0}
.card-head{display:flex;flex-wrap:wrap;align-items:baseline;gap:6px 10px;margin-bottom:6px}
.asof{color:var(--dim);font-size:12.5px;margin-left:auto}
.prog{display:grid;grid-template-columns:repeat(6,1fr);gap:6px;margin:0 0 14px}
.step{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:8px 10px;display:flex;
flex-direction:column;min-width:0}
.step span{color:var(--dim);font-size:13px}.step.done{border-color:var(--proven);
background:var(--proven-soft)}
.step.next{border:2px solid var(--accent);background:var(--accent-soft)}
.step.done b::before{content:"✓ ";color:var(--proven)}
.grid{display:grid;grid-template-columns:minmax(0,2fr) minmax(0,1fr);gap:14px;align-items:start}
.brief ul{margin:4px 0 8px;padding-left:20px}.brief li{margin:5px 0}
.next-btn{background:var(--accent);color:#fff;border:0;border-radius:7px;padding:7px 16px;font:inherit;
font-weight:600;cursor:pointer}
.nextline{margin:8px 0 0}
button{font:inherit;font-size:13px;border:1px solid var(--line);background:var(--card);color:var(--ink);
border-radius:6px;padding:3px 9px;cursor:pointer}
button:disabled{cursor:default;opacity:.55}
.pool table.counts{width:auto}.pool table.counts td,.pool table.counts th{padding:3px 10px 3px 0;
text-align:center}
.pool table.counts td:first-child{text-align:left}
.dec ul{list-style:none;padding:0;margin:0}.dec li{padding:9px 0;border-top:1px solid var(--line);
display:flex;
flex-wrap:wrap;gap:6px 14px;align-items:center;justify-content:space-between}
.dec li:first-child{border-top:0}.dec li>div:first-child{flex:1 1 360px}
.dec .opts{display:flex;gap:6px;flex-wrap:wrap;align-items:center}
.dec .opts button.opt{border-color:var(--accent);color:var(--accent)}
.dec li.answered>div:first-child{opacity:.75}.answer{color:var(--proven);font-weight:600;font-size:13.5px}
.dec ul.pivots{flex-basis:100%;margin:2px 0 0 14px;padding:0;list-style:disc}
.dec ul.pivots li{border:0;padding:2px 0;display:list-item}
.scroll{overflow:auto;max-height:80vh}.scroll.tall{max-height:88vh}table{border-collapse:separate;border-spacing:0;width:100%;font-size:13.5px}
th,td{text-align:left;padding:6px 8px 6px 0;border-bottom:1px solid var(--line);vertical-align:top}
th{color:var(--dim);font-weight:600;font-size:12.5px}td.n,th.n{text-align:right;
font-variant-numeric:tabular-nums}
table.board td.n{padding-right:12px}
table.board td.nm{min-width:130px}table.board td.nm .tags{display:block}
table.board tr.ph th{position:sticky;top:0;z-index:2;background:var(--bg);padding:5px 8px 5px 0;
white-space:nowrap}
table.board tr.ph th.nm{z-index:3}table.board .posname{font-weight:700;color:var(--ink);font-size:13px}
table.board .why{color:var(--dim);font-size:13px;min-width:190px}
table.board tr.fade-row td{background:var(--bad-soft)}td.hot{color:var(--bad);font-weight:600}
.tags{margin-top:2px}
.chip{display:inline-block;font-size:11px;font-weight:600;border-radius:999px;padding:0 7px;
margin:2px 3px 0 0;
white-space:nowrap}
.chip.value{background:var(--proven-soft);color:var(--proven)}.chip.cash{background:var(--accent-soft);
color:var(--accent)}.chip.upside{background:var(--shadow-soft);color:var(--shadow)}
.chip.fade,.chip.q{background:var(--bad-soft);color:var(--bad)}
.chip.proven,.chip.shadow,.chip.untested,.chip.data{background:transparent;border:1px solid currentColor;
font-size:11.5px;padding:0 8px}
.chip.proven{color:var(--proven)}.chip.shadow{color:var(--shadow)}
.chip.untested,.chip.data{color:var(--untested)}
a.chip{text-decoration:none;cursor:pointer}a.chip:hover{text-decoration:underline}
.tchip{display:inline-block;font-size:10px;font-weight:700;border-radius:4px;padding:0 4px;border:1px solid
currentColor;line-height:1.3}
.tchip.proven{color:var(--proven)}.tchip.shadow{color:var(--shadow)}
.tchip.untested,.tchip.data{color:var(--untested)}
.record{font-size:12px;color:var(--proven)}
.seg{display:inline-flex;white-space:nowrap}.seg button{margin:0;border-radius:0;padding:2px 5px;
font-size:12px;
margin-left:-1px}.seg button:first-child{border-radius:6px 0 0 6px;margin-left:0}
.seg button:last-child{border-radius:0 6px 6px 0}
.seg button.on{background:var(--accent);color:#fff;border-color:var(--accent);opacity:1}
.seg.busy{opacity:.5;pointer-events:none}
.showmore{border:0;color:var(--accent);background:transparent;padding:2px 0}
.chart{display:block;width:100%;height:auto;max-width:100%}
.chart .dot{fill:var(--dim);opacity:.55}.chart .dot.WR{fill:var(--wr)}.chart .dot.RB{fill:var(--rb)}
.chart .dot.TE{fill:var(--te)}.chart .dot.strong{opacity:.95;stroke:var(--card);stroke-width:1}
.chart .dot,.chart .lbl{cursor:pointer}
.chart .ref{stroke:var(--bad);stroke-dasharray:5 4;stroke-width:1.5}
.chart .grid{stroke:var(--line);stroke-width:1}.chart .tick{font-size:12px;fill:var(--dim)}
.chart .axis{font-size:12.5px;fill:var(--dim)}.chart .lbl{font-size:13px;fill:var(--ink);font-weight:600}
.chart .ref-lbl{font-size:12px;fill:var(--bad)}.chart .shade{fill:var(--proven-soft)}
.chart .shade-lbl{font-size:12px;fill:var(--proven)}
.legend{font-size:12.5px;color:var(--dim);margin:2px 0 4px}.legend .key{display:inline-block;width:10px;
height:10px;border-radius:50%;margin:0 4px 0 8px;vertical-align:-1px}.legend .key.RB{background:var(--rb)}
.legend .key.WR{background:var(--wr)}.legend .key.TE{background:var(--te)}
.legend .key.QB{background:var(--s2)}.legend .key.DST{background:var(--dim)}.chart .dot.QB{fill:var(--s2)}
.cap{font-size:11.5px;color:var(--dim);margin:4px 0 8px;line-height:1.45}.cap .cap-asof{white-space:nowrap}
.games{display:grid;grid-template-columns:1fr 1fr;gap:8px}
.game{border:1px solid var(--line);border-radius:8px;padding:8px 9px;min-width:0}
.game.top{border-color:var(--accent)}
.gh{display:flex;justify-content:space-between;font-size:13.5px}.gk{font-size:12px;color:var(--dim);
margin:0 0 4px}
.bar{display:grid;grid-template-columns:38px 1fr 34px;align-items:center;gap:6px;font-size:12.5px}
.bar i{height:8px;background:var(--accent);border-radius:4px;display:block}.bar b{text-align:right}
.gflags{margin-top:4px}
.tag{display:inline-block;font-size:11px;border-radius:4px;padding:0 6px;margin:2px 4px 0 0;
white-space:nowrap}
.tag.stack{background:var(--shadow-soft);color:var(--shadow);font-weight:600}
.tag.wx{background:var(--accent-soft);
color:var(--accent)}.tag.moved{background:var(--bad-soft);color:var(--bad)}
.lenses{display:flex;flex-wrap:wrap;gap:6px;margin:4px 0}
.lenses button.on,.ex-bar button.on{background:var(--accent);
color:#fff;border-color:var(--accent)}
.ex-filters{display:flex;flex-wrap:wrap;gap:6px 12px;align-items:flex-end;margin:8px 0}
.ex-filters label{display:flex;flex-direction:column;font-size:12px;color:var(--dim);gap:2px}
.ex-filters select,.ex-filters input{font:inherit;font-size:13px;background:var(--bg);color:var(--ink);
border:1px solid var(--line);border-radius:5px;padding:2px 5px}
.ex-filters .range{display:flex;gap:3px}
.ex-bar{display:flex;flex-wrap:wrap;gap:6px 14px;align-items:center;margin:6px 0;font-size:13px}
.ex-bar span{display:inline-flex;gap:3px;align-items:center}
#ex-picker{border:1px solid var(--line);border-radius:8px;padding:6px 10px;margin:6px 0;max-height:300px;
overflow:auto;columns:3 220px;font-size:13px}
#ex-picker details{break-inside:avoid}.pick{display:block}
.ex-chart{max-width:560px}
.ex-scroll{max-height:72vh;overflow:auto}
table.ex th{position:sticky;top:0;background:var(--card);cursor:pointer;white-space:nowrap;z-index:1}
table.ex tr.groups th{top:0;cursor:default;color:var(--ink);border-left:1px solid var(--line)}
table.ex td{white-space:nowrap;padding:4px 10px 4px 0}table.ex tr.pos td{background:var(--bg);font-weight:700;
font-size:12px}
.drawer{position:fixed;top:0;right:0;bottom:0;width:min(440px,100%);background:var(--card);
border-left:1px solid var(--line);box-shadow:-6px 0 18px rgba(0,0,0,.12);overflow-y:auto;
padding:12px 16px 30px;
z-index:20}
.dhead{display:flex;justify-content:space-between;align-items:center}.dclose{font-size:20px;line-height:1;
padding:0 8px}
.dsec h4{margin:12px 0 4px}table.kv th{font-weight:500;color:var(--dim);width:48%}table.kv td,table.kv th{
padding:3px 6px 3px 0}
table.log td,table.log th{padding:3px 6px 3px 0}
.fold{padding:0}.fold>details>summary{list-style:none;cursor:pointer;padding:12px 16px;display:flex;
flex-wrap:wrap;gap:4px 12px;align-items:baseline}
.fold>details>summary::-webkit-details-marker{display:none}
.fold>details>summary::before{content:"▸";color:var(--dim)}.fold>details[open]>summary::before{content:"▾"}
.fold .line{color:var(--dim);font-size:13.5px}.fold .line.bad{color:var(--bad)}
.fold>details>.body{padding:0 16px 14px}
h2.sec{font-size:13px;text-transform:uppercase;letter-spacing:.06em;color:var(--dim);margin:22px 0 8px}
.notrun{color:var(--dim)}.trust{font-size:13px;color:var(--dim);margin:10px 0 0;
border-top:1px dashed var(--line);
padding-top:8px}
code{font:12.5px/1.4 ui-monospace,SFMono-Regular,Menlo,monospace;background:rgba(127,127,127,.14);
padding:1px 5px;
border-radius:4px}
code.say{background:var(--accent-soft);color:var(--accent)}
.why{color:var(--dim);font-size:14px}.last{font-size:12.5px;color:var(--dim)}.last.never{color:var(--warn)}
.note{color:var(--dim);font-size:13px;margin:6px 0}.ok{color:var(--ok)}.bad{color:var(--bad)}
.warn{color:var(--warn)}
.dim{color:var(--dim)}.flag{background:var(--flag);border-radius:8px;padding:8px 12px;margin:8px 0}
.leagues{display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));gap:12px}
.league{border:1px solid var(--line);border-radius:8px;padding:2px 12px 8px}
ul{margin:4px 0;padding-left:20px}details summary{cursor:pointer}details summary h4{display:inline}
table.tools td:nth-child(1){min-width:150px}table.tools td:nth-child(2){min-width:190px}
table.tools td:nth-child(3){min-width:260px}table.tools code{white-space:normal;word-break:break-word}
.urgent{border:2px solid var(--bad);border-radius:8px;padding:6px 12px;margin:6px 0 10px;color:var(--bad)}
.toast{position:fixed;left:50%;bottom:18px;transform:translateX(-50%);background:var(--ink);color:var(--card);
padding:8px 14px;border-radius:8px;font-size:14px;z-index:30;max-width:90vw}
.toast.bad{background:var(--bad);color:#fff}
footer{max-width:2400px;margin:0 auto;padding:6px 16px 30px;color:var(--dim);font-size:12.5px}
.chart.bank{max-width:760px}.chart .line{fill:none;stroke-width:2}
.chart .zero{stroke:var(--dim);stroke-width:1}
.chart .s1{stroke:var(--s1)}.chart .s2{stroke:var(--s2)}.chart .s3{stroke:var(--s3)}
.chart .st{stroke:var(--ink)}
.chart .pt{stroke:var(--card);stroke-width:2}.chart .pt.s1{fill:var(--s1)}.chart .pt.s2{fill:var(--s2)}
.chart .pt.s3{fill:var(--s3)}.chart .pt.st{fill:var(--ink)}
.legend .lkey{display:inline-block;width:14px;height:3px;border-radius:2px;margin:0 4px 0 10px;
vertical-align:3px}
.lkey.s1{background:var(--s1)}.lkey.s2{background:var(--s2)}.lkey.s3{background:var(--s3)}
.lkey.st{background:var(--ink)}
.prac{font-size:12px;color:var(--dim);white-space:nowrap}
main{container-type:inline-size}
.charts-row{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,460px),1fr));gap:0 14px;
align-items:start}
.charts-row .chart{max-width:760px}
#games .games{grid-template-columns:repeat(auto-fill,minmax(190px,1fr))}
.scroll>table>thead th{position:sticky;top:0;z-index:2;background:var(--card)}
table.stickyname td:first-child,table.stickyname th:first-child{position:sticky;left:0;z-index:1;
background:var(--card)}
table.stickyname thead th:first-child,table.stickyname tr.ph th:first-child{z-index:3}
table.board tr.ph th:first-child{background:var(--bg)}
table.board tr.fade-row td:first-child{background:var(--bad-soft)}
table.ex tr.pos td:first-child{background:var(--bg)}
body.live.chat-open .grid,body.live.chat-open .charts-row{grid-template-columns:minmax(0,1fr)}
.drawer{z-index:28}
@container (max-width:640px){.grid{grid-template-columns:minmax(0,1fr)}
.prog{grid-template-columns:repeat(3,1fr)}}
@media(max-width:900px){.grid{grid-template-columns:minmax(0,1fr)}.prog{grid-template-columns:repeat(3,1fr)}}
@media(max-width:520px){.prog{grid-template-columns:repeat(2,1fr)}.games{grid-template-columns:1fr}
table.board .why{min-width:160px}h1{font-size:22px}}
"""


def week_dir(data_dir: Path | None = None) -> Path:
    return (data_dir or data_root()) / "week"


def page_path(season: int, week: int, data_dir: Path | None = None) -> Path:
    return week_dir(data_dir) / f"{season}-wk{week:02d}.html"


# Below the fold, collapsed, ordered by day: what Sam needs first today comes first. Tuesday to Friday is
# research, Saturday is build and enter, Sunday is game day, Monday the close.
FOLD_IDS = [
    "field-review",
    "changes",
    "salaries",
    "contest-plan",
    "entry-plan",
    "bankroll",
    "season",
    "last-week",
    "exposure",
    "cash",
    "gpp",
    "consensus",
    "inactives",
    "sunday",
    "numbers",
]
DAY_ORDER: dict[int, list[str]] = {
    0: [  # Monday: the close
        "field-review",
        "last-week",
        "numbers",
        "exposure",
        "bankroll",
        "season",
        "changes",
        "contest-plan",
        "entry-plan",
        "cash",
        "gpp",
        "consensus",
        "salaries",
        "inactives",
        "sunday",
    ],
    1: [  # Tuesday: research, last week's lessons still on top
        "field-review",
        "changes",
        "salaries",
        "contest-plan",
        "bankroll",
        "season",
        "last-week",
        "numbers",
        "cash",
        "gpp",
        "consensus",
        "entry-plan",
        "exposure",
        "inactives",
        "sunday",
    ],
    2: [  # Wednesday to Friday: research
        "changes",
        "salaries",
        "contest-plan",
        "bankroll",
        "season",
        "last-week",
        "field-review",
        "cash",
        "gpp",
        "consensus",
        "entry-plan",
        "exposure",
        "numbers",
        "inactives",
        "sunday",
    ],
    5: [  # Saturday: build and enter
        "contest-plan",
        "entry-plan",
        "exposure",
        "cash",
        "gpp",
        "consensus",
        "salaries",
        "bankroll",
        "changes",
        "inactives",
        "sunday",
        "season",
        "last-week",
        "field-review",
        "numbers",
    ],
    6: [  # Sunday: game day
        "inactives",
        "sunday",
        "entry-plan",
        "exposure",
        "changes",
        "consensus",
        "cash",
        "gpp",
        "contest-plan",
        "salaries",
        "bankroll",
        "season",
        "last-week",
        "field-review",
        "numbers",
    ],
}
OPEN_ON_SUNDAY = "inactives"  # Sunday: the inactives check is open when anything is flagged
OPEN_ON_DAY = {0: "field-review", 1: "field-review"}  # Monday and Tuesday: what won is open, at the top
DAY_ORDER[3] = DAY_ORDER[4] = DAY_ORDER[2]
SECTION_IDS = FOLD_IDS


def section_order(day: int) -> list[str]:
    """The collapsed sections' ids in the order the page shows them on weekday `day` (0 = Monday)."""
    order = DAY_ORDER.get(day, DAY_ORDER[1])
    return [i for i in order if i in FOLD_IDS] + [i for i in FOLD_IDS if i not in order]


# -- one-line summaries of the collapsed sections -------------------------------------------------------


def fold_line(sid: str, inp: WeekInputs, built: dict[str, Section]) -> tuple[str, bool]:
    """(the line a collapsed section shows, whether it is urgent)."""
    s = built[sid]
    if sid == "season":
        leagues = ((inp.season_long or {}).get("leagues")) or {}
        if not leagues:
            return "not run yet", False
        urgent = season_urgent(leagues)
        if urgent:
            first = "; ".join(f"{n}: {t}" for n, t in urgent[:2])
            return f"{len(urgent)} urgent ({first})", True
        return f"nothing urgent in {len(leagues)} leagues", False
    if not s.ran:
        return "not run yet", False
    if sid == "changes" and inp.edge and inp.prev_edge:
        d = week_plays.changes(inp.edge, inp.prev_edge, mine_names(inp))
        return (
            f"{len(d.status)} status changes for yours, {len(d.new_outs)} new OUTs, {len(d.calpts)} CalPts "
            f"moves, {len(d.lines)} line moves"
        ), bool(d.status or d.new_outs)
    if sid == "contest-plan":
        budget = weekly_budget(inp)  # this week's plan only: last week's spend is under Last week
        if not budget:
            return "no Weekly Budget yet", False
        return f"Weekly Budget {dollars(budget)}: about 60% Tier A, 30% Tier B, at most 10% Tier C", False
    if sid == "last-week" and inp.last_week:
        lw = inp.last_week
        roi = pct(lw.net / lw.fees if lw.fees else None)
        return f"Week {lw.week}: {dollars(lw.fees)} in, net {dollars(lw.net)} ({roi})", lw.net < 0
    if sid in ("cash", "gpp"):
        bs = builds_of(inp, sid)
        if sid == "cash":
            best = max((cash_shown(b)[0] or 0 for b in bs), default=0)
            return f"{len(bs)} cash builds, best Cash % {pct(best)}", False
        best = max((num((b.get("metrics") or {}).get("p_gpp")) or 0 for b in bs), default=0)
        st = ((inp.stacks or {}).get("rows") or [])[:1]
        top = f"; top stack {st[0].get('qb', '')} + {st[0].get('mates', '')}" if st else ""
        return (
            f"{len(bs)} GPP builds, best Top 1% {pct(best, 1)}" if bs else "no GPP builds yet"
        ) + top, False
    if sid in ("field-review", "exposure", "numbers"):
        from dfs import week_learn as wl

        return {
            "field-review": wl.field_review_line,
            "exposure": wl.exposure_line,
            "numbers": wl.numbers_line,
        }[sid](inp)
    if sid == "salaries":
        from dfs import week_learn as wl

        return wl.salary_line(inp, player_log(inp))
    if sid in ("bankroll", "inactives", "consensus", "entry-plan"):
        from dfs import week_money as wm

        return {
            "bankroll": wm.bankroll_line,
            "inactives": wm.inactives_line,
            "consensus": wm.consensus_line,
            "entry-plan": wm.entry_plan_line,
        }[sid](inp)
    if sid == "sunday":
        at = parse_time((inp.late_swap or {}).get("saved_at"))
        return (f"late swap ran {week_today.when(inp.now, at)}" if at else "late swap not run"), False
    return s.asof or "", False


def fold_html(s: Section, line: str, urgent: bool, open_: bool = False) -> str:
    chip = ""
    if s.trust:
        label, why = s.trust
        chip = chip_html(label, why)
    trust_line = f'<p class="trust"><b>{e(s.trust[0])}.</b> {rich(s.trust[1])}</p>' if s.trust else ""
    return (
        f'<section class="card fold" id="{e(s.id)}"><details{" open" if open_ else ""}>'
        f"<summary><h3>{e(s.title)}</h3>"
        f'<span class="line{" bad" if urgent else ""}">{e(line)}</span>{chip}</summary>'
        f'<div class="body">{fill_asof(s.body, s.asof)}{trust_line}</div></details></section>'
    )


def tools_fold(inp: WeekInputs, tools: list[week_tools.Tool]) -> str:
    stale, _ = week_tools.not_used_lately(tools, inp.runs, inp.now)
    inner = tools_html(inp, tools)
    inner = inner.replace(
        '<section class="card" id="tools"><div class="card-head"><h2>Your tools</h2></div>', "", 1
    )
    inner = inner.removesuffix("</section>")
    line = f"{len(tools)} commands and phrases" + (f"; {len(stale)} not used in 3+ weeks" if stale else "")
    return (
        '<section class="card fold" id="tools"><details><summary><h3>Your tools</h3>'
        f'<span class="line">{e(line)}</span></summary><div class="body">{inner}</div></details></section>'
    )


def unknowns_into_sunday(sunday: Section, unknowns: Section) -> Section:
    """Sunday's collapsed section also carries the Questionable / Doubtful list (and an OUT sitting in a
    build), which used to be a section of its own."""
    return Section(
        sunday.id,
        "Sunday and the unknowns",
        sunday.body + "<h4>Questionable, Doubtful and OUT</h4>" + unknowns.body,
        sunday.trust,
        ran=sunday.ran or unknowns.ran,
        asof=sunday.asof,
    )


def player_log(inp: WeekInputs) -> dict[str, list[dict]]:
    """Each player's last five games (`data/current/player_log.csv`, the Player card's), by DK Id."""
    from dfs.week_inputs import _read_csv

    rows = _read_csv(inp.data_dir / "current" / "player_log.csv")
    by_key: dict[str, list[dict]] = {}
    for r in rows:
        by_key.setdefault(r.get("NameKey", ""), []).append(r)
    out: dict[str, list[dict]] = {}
    for r in inp.edge or []:
        games = by_key.get(str(r.get("NameKey") or ""))
        if not games or not r.get("Id"):
            continue
        games = sorted(games, key=lambda g: int(num(g.get("Back")) or 99))[:5]
        out[str(r["Id"])] = [
            {
                "Wk": g.get("Wk", ""),
                "Opp": g.get("Opp", ""),
                "Snap": num(g.get("Snap%")),
                "Tgt": None if num(g.get("Tgt")) is None else round(num(g.get("Tgt")) or 0),
                "Car": None if num(g.get("Car")) is None else round(num(g.get("Car")) or 0),
                "DK": num(g.get("DK")),
            }
            for g in games
        ]
    return out


def record_lines(inp: WeekInputs) -> dict[str, str]:
    return {
        t: f"{week_plays.record_line(t, inp.signal_table, inp.scored_weeks)}  "
        f"[{TRUST[SIGNAL_TRUST.get(t, 'plays_shadow')][0]}]"
        for t in ("FADE↓", "USAGE↑", "USAGE↓", "INJ+", "Proj ▲", "Proj ▼")
    }


def render(
    inp: WeekInputs,
    *,
    registered: set[str] | None = None,
    tools: list[week_tools.Tool] | None = None,
    live: bool = False,
    nonce: str = "",
    sheet=None,
    answers: dict | None = None,
) -> str:
    """The whole page as one HTML string. `live` (the served page) adds the controls that write the sheet;
    `sheet` is what the server read from it (`week_writes.LiveSheet`), `answers` the decisions file."""
    from dfs import week_board as wb
    from dfs import (
        week_charts,
        week_explorer,
        week_learn,
        week_money,
        week_shade,
        week_top,
        week_ui,
        week_views,
    )

    token = _LIVE.set(live)
    asof_token = _ASOF.set(sync_stamp(inp))
    try:
        if tools is None:
            tools = week_tools.build(inp.runs)
        day = inp.now.astimezone().weekday()
        nonce_attr = f' nonce="{e(nonce)}"' if nonce else ""
        live_pool = sheet.pool if (sheet is not None and sheet.ok) else None
        rules = sheet.rules if (sheet is not None and sheet.ok and sheet.rules_found) else None
        pool = wb.pool_by_name(inp, live_pool)
        by_id = wb.pool_by_id(inp, live_pool)
        board = wb.board(inp)
        panel = wb.pool_panel(inp, board, pool)
        steps = wb.progress(inp, pool)
        sentences = wb.brief(inp, board, panel)
        ds = wb.decisions(inp, rules, answers)
        if sheet is not None and sheet.ok:
            source = f"from the sheet, {kickoff.format_et(inp.now)}"
        elif sheet is not None:
            source = "the sheet could not be read: as of the last sync"
        else:
            source = "as of the last sync or build"
        built = {
            s.id: s
            for s in (
                changes_section(inp),
                contest_plan_section(inp, registered),
                cash_section(inp),
                gpp_section(inp),
                season_section(inp),
                last_week_section(inp),
                unknowns_into_sunday(sunday_section(inp), unknowns_section(inp)),
                week_money.bankroll_section(inp),
                week_money.inactives_section(inp),
                week_money.consensus_section(inp),
                week_money.entry_plan_section(inp),
                week_learn.field_review_section(inp),
                week_learn.exposure_section(inp),
                week_learn.numbers_section(inp),
                week_learn.salary_section(inp, player_log(inp)),
            )
        }
        folds = []
        for sid in section_order(day):
            line, urgent = fold_line(sid, inp, built)
            open_ = (day == 6 and sid == OPEN_ON_SUNDAY and urgent) or (
                OPEN_ON_DAY.get(day) == sid and built[sid].ran
            )
            folds.append(
                fold_html(built[sid], line, urgent, open_) + (LIVE_CONTEST if sid == "contest-plan" else "")
            )
        sources = sorted(inp.manifest)
        failed = [n for n in sources if inp.manifest[n].get("error")]
        pending = [n for n in sources if inp.manifest[n].get("pending") and n not in failed]
        stale = week_today.stale_items(
            now=inp.now,
            manifest=inp.manifest,
            suggest_at=suggest_time(inp),
            late_swap_at=parse_time((inp.late_swap or {}).get("saved_at")),
            season_at=parse_time((inp.season_long or {}).get("saved_at")),
            day=day,
        )
        theme = week_today.DAY_THEME[day].lower()
        sub = (
            f"{e(week_today.DAY_NAMES[day])} &middot; {e(theme)} &middot; "
            f"{len(sources) - len(failed) - len(pending)} of {len(sources)} sources synced"
            + (f", {len(pending)} pending ({e(', '.join(pending))})" if pending else "")
            + (f", {len(failed)} failed ({e(', '.join(failed))})" if failed else "")
            + (f" &middot; sheet: {e(sheet.title)}" if sheet is not None and sheet.title else "")
            + f" &middot; built {e(kickoff.format_et(inp.now))}"
        )
        stale_html = '<p class="note warn">Stale: ' + e("; ".join(stale)) + "</p>" if stale else ""
        data = week_explorer.payload(
            inp,
            board,
            by_id,
            record_lines=record_lines(inp),
            log=player_log(inp),
            practice=week_money.practice_by_id(inp),
            asof=("Data " + sync_stamp(inp) + ".") if sync_stamp(inp) else "",
        )
        own_out = not board.gpp_pending
        wrap = week_views.wrap
        rules_json = json.dumps(rules or {}).replace("</", "<\\/")
        live_data = f'<script type="application/json" id="rules-was">{rules_json}</script>' if live else ""
        page = (
            "<!doctype html><html lang=en><head><meta charset=utf-8>"
            '<meta name=viewport content="width=device-width,initial-scale=1">'
            f'<meta name="dfs-week" content="{inp.season}-{inp.week:02d}">'
            f"<title>Week {inp.week}</title><style>{CSS}{week_ui.CSS}{week_shade.CSS}{week_views.CSS}</style>"
            "</head><body>"
            f'<header><h1>Week {inp.week}, {inp.season}</h1><p class="sub">{sub}</p></header><main>'
            + week_top.progress_html(steps)
            + stale_html
            + '<div class="grid">'
            + week_top.brief_html(sentences, wb.next_step(steps), live, week_today.DAY_NAMES[day])
            + wrap(week_top.pool_panel_html(panel, live, source), "pool")
            + "</div>"
            # Plan O2: the view bar; each block below names the views it shows on (`week_views.PLACES`)
            + week_views.view_bar()
            + wrap(week_views.overview_section(inp, board, panel), "overview")
            + wrap(week_top.decisions_html(ds, live, inp.now, rules is not None, by_id), "decisions")
            + wrap(week_top.board_html(board, by_id, live, week_money.practice_by_id(inp)), "board")
            + wrap('<div class="charts-row">' + week_top.charts_html(inp.edge, own_out) + "</div>", "charts")
            + wrap(week_top.games_html(inp, live), "games")
            + wrap(week_top.explorer_html(inp, data), "explorer")
            + wrap(week_views.usage_section(inp), "usage")
            + wrap(week_views.injury_section(inp), "injuries")
            + wrap(week_views.model_section(inp), "model")
            + wrap(
                '<h2 class="sec">The rest of the week</h2>'
                + "".join(folds)
                + LIVE_RUN
                + tools_fold(inp, tools),
                "rest",
            )
            + "</main>"
            + week_top.DRAWER
            + live_data
            + "<footer>"
            + (
                "Served page: pool picks and decisions write the live sheet (the cells the sheet's own "
                "controls use), re-read first and read back after."
                if live
                else "Read-only: built from files the tool already saved; nothing here writes a sheet. "
                "Regenerated after sync, suggest, late-swap, contests plan, season sync/check and week "
                "close; "
                "<code>dfs week page</code> rebuilds it now."
            )
            + "</footer>"
            + week_top.explorer_data(data)
            + '<script type="application/json" id="chart-data">'
            + json.dumps(week_charts.viewer_data(inp.edge, own_out), separators=(",", ":")).replace(
                "</", "<\\/"
            )
            + "</script>"
            + f'<script type="application/json" id="game-data">{week_top.game_data_json(inp)}</script>'
            + f"<script{nonce_attr}>{week_shade.JS}</script>"
            + f"<script{nonce_attr}>{week_ui.JS}</script>"
            + week_top.explorer_js(data, nonce_attr)
            + f'<script type="application/json" id="compare-spec">{week_views.compare_json()}</script>'
            + f'<script type="application/json" id="news-data">{week_views.news_json(inp)}</script>'
            + f"<script{nonce_attr}>{week_views.JS}</script>"
            + f"<script{nonce_attr}>{BOARD_JS}</script>"
            + "</body></html>"
        )
        return fill_asof(page, "")
    finally:
        _ASOF.reset(asof_token)
        _LIVE.reset(token)


# The board's Show more (both pages) and the fold state kept across a refresh of the served page.
BOARD_JS = r"""
document.addEventListener('click', (e) => {
  const b = e.target.closest('button.showmore'); if (!b) return;
  const grp = b.closest('tbody'); for (const tr of grp.querySelectorAll('tr.more')) tr.hidden = false;
  b.closest('tr').remove();
});
"""


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
