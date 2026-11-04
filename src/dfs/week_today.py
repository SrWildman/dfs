"""The Week page's "Today": what day it is in Sam's week, the 3-5 steps for it, and what is stale. Pure
(no files, no network) so it is directly testable; `week_inputs.gather` collects the facts it reads.

The week, as Sam runs it (HANDOFF section 1, CLAUDE.md's shortcut table): Monday close, Tuesday new week,
Wednesday-Friday research, Saturday build, Sunday game day. The state-driven part is not reinvented: the
launcher's own `suggest_actions` (the one place that guesses what to do next from the data) is shown beside
the day's plan, built from the same local facts it reads for the guided menu (no sheet).

Each step carries the exact phrase to say to Claude Code (verbatim from CLAUDE.md's table; a test checks it)
and the command it runs. A step whose command does not exist yet (Plan D's `dfs contests plan` and `dfs
entries record`) is left out until the command is registered, so the page never names a command that fails.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from dfs import kickoff, launcher, runlog

MON, TUE, WED, THU, FRI, SAT, SUN = range(7)
DAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
DAY_THEME = {
    MON: "Close the week",
    TUE: "Start the new week",
    WED: "Research",
    THU: "Research",
    FRI: "Research",
    SAT: "Build and enter",
    SUN: "Game day",
}

MAX_STEPS = 5


@dataclass(frozen=True)
class StepSpec:
    phrase: str | None  # what to say to Claude Code (verbatim from CLAUDE.md), None for a manual step
    command: str | None  # the command it runs, as typed (`dfs sync --live`); None: by hand or in the sheet
    why: str
    planned: bool = False  # the command is Plan D's: shown only once it is registered


@dataclass
class Step:
    number: int
    phrase: str | None
    command: str | None
    why: str
    last: runlog.Run | None = None


_DOWNLOADS = StepSpec(
    None,
    None,
    "Download, by hand: DraftKings' contest-history export and 'export full standings' for every paid "
    "Classic contest (DK keeps the standings about two weeks). The close needs both.",
)
_SYNC_WED = StepSpec(
    "sync", "dfs sync", "Lines and injuries move through the week; one sync refreshes every source."
)
_SYNC_SAT = StepSpec(
    "sync", "dfs sync", "The last full sync before you lock: pulls props once, and Own% if TFFB has it out."
)
_PLAN = StepSpec(
    None,
    "dfs contests plan",
    "Decide where the money goes before you build: spend by format against the caps, and who to avoid "
    "in H2Hs.",
    planned=True,
)
_PICKUPS = StepSpec(
    "best pickups in <league>",
    "dfs season waivers --league <league>",
    "Your season-long leagues: the best adds, who to drop and what to bid.",
)
_TRADES = StepSpec(
    "find me trades in <league>",
    "dfs season trades --league <league>",
    "The trade finder's top ideas for one league (or say 'find me trades for A, B, C and D').",
)

PLANS: dict[int, list[StepSpec]] = {
    MON: [
        _DOWNLOADS,
        StepSpec(
            "close the week",
            "dfs week close --research",
            "Reconciles the bankroll, scores the model into Model Check, rebuilds the Contests tab, then "
            "runs the offline studies (under 45 minutes; run detached).",
        ),
        StepSpec(
            "how did we do", None, "Read-only: Model Check's verdict and what the model learned this week."
        ),
        StepSpec(
            "season sync",
            "dfs season sync",
            "Your season-long leagues: odds, this week's lineup alerts, the free move and the top ideas.",
        ),
    ],
    TUE: [
        StepSpec(
            "start week N",
            "dfs week new",
            "Takes the opening odds snapshot first if there is none. Copy the template in Drive and share "
            "it with the service account; Claude asks you to confirm and for the new sheet's URL.",
        ),
        StepSpec(
            "season sync",
            "dfs season sync",
            "Your season-long leagues: odds, lineup alerts, the free move and the top ideas.",
        ),
        _PICKUPS,
    ],
    WED: [_SYNC_WED, _PICKUPS, _TRADES, _PLAN],
    THU: [_SYNC_WED, _PICKUPS, _TRADES, _PLAN],
    FRI: [_SYNC_WED, _PICKUPS, _TRADES, _PLAN],
    SAT: [
        _SYNC_SAT,
        _PLAN,
        StepSpec(
            "build me lineups",
            "dfs lineups suggest",
            "Needs a pool. Prints each build; then Lineup Tools > Copy builds to Lineups puts them in the "
            "sheet.",
        ),
        StepSpec(
            "fix flex",
            "dfs export",
            "In the sheet, Lineup Tools > Fix FLEX order; then `dfs export` writes DK's upload CSV (and "
            "fixes the FLEX in the file too). With DK's entries file, run `dfs entries fill` first.",
        ),
        StepSpec(
            None,
            "dfs entries record",
            "At lock: save exactly what you entered, so next week can compare it with the builds.",
            planned=True,
        ),
    ],
    SUN: [
        StepSpec(
            "check my lineups",
            "dfs season check",
            "First thing: empty or OUT starters in your season-long leagues (read-only).",
        ),
        StepSpec(
            "final sync",
            "dfs sync --live",
            "After the inactives, about 90 minutes before the 1 pm games; then read the Cockpit's SWAP and "
            "FILL rows.",
        ),
        StepSpec(
            "late swap",
            "dfs lineups late-swap --refresh-injuries",
            "Who is still swappable in each lineup, and the best change for each. Once a game has started it "
            "scores swaps on your players' points so far (ESPN's box scores).",
        ),
        StepSpec(
            "set my lineups",
            "dfs season lineups",
            "Season-long: this week's best lineup in each league (you set it in Sleeper by hand).",
        ),
    ],
}


def day_plan(day: int, registered: set[str] | None = None) -> list[StepSpec]:
    """The day's steps in order, minus any whose command is not registered yet, capped at MAX_STEPS.
    `registered` is the set of `dfs ...` command paths that exist (None: do not filter)."""
    out = []
    for spec in PLANS[day]:
        if spec.planned and registered is not None and not _registered(spec.command, registered):
            continue
        out.append(spec)
    return out[:MAX_STEPS]


def _registered(command: str | None, registered: set[str]) -> bool:
    return command is None or " ".join(w for w in command.split() if not w.startswith("-")) in registered


def steps_for(day: int, runs: list[runlog.Run], registered: set[str] | None = None) -> list[Step]:
    return [
        Step(
            i,
            s.phrase,
            s.command,
            s.why,
            runlog.last_run(runs, s.command) if s.command else None,
        )
        for i, s in enumerate(day_plan(day, registered), 1)
    ]


# -- times ------------------------------------------------------------------------------------------


def ago(now: datetime, then: datetime) -> str:
    """`12 min ago`, `3 h ago`, `2 d ago`."""
    seconds = max(0.0, (now - then).total_seconds())
    if seconds < 90:
        return "just now"
    if seconds < 3600 * 1.5:
        return f"{round(seconds / 60)} min ago"
    if seconds < 86400 * 2:
        return f"{round(seconds / 3600)} h ago"
    return f"{round(seconds / 86400)} d ago"


def when(now: datetime, then: datetime) -> str:
    """`Sat 3:29 pm ET (2 h ago)`."""
    return f"{kickoff.format_et(then)} ({ago(now, then)})"


# -- the launcher's view, and what is stale ---------------------------------------------------------


def launcher_state(
    *,
    week: int,
    now: datetime,
    manifest: dict,
    game_starts: list[str],
    pool_count: int,
    finished_after_hours: float,
) -> launcher.LauncherState:
    """The facts `launcher.suggest_actions` reads, from local files only (the guided menu reads the sheet for
    the pool and lineup counts; the page never opens the sheet, so `lineups_total` stays 0: unknown)."""
    import pandas as pd

    state = launcher.LauncherState(week=week, pool_count=pool_count)
    state.synced_sources = sum(1 for v in manifest.values() if not v.get("error"))
    stamps = []
    for v in manifest.values():
        try:
            stamps.append(datetime.fromisoformat(v["synced_at"]))
        except (KeyError, TypeError, ValueError):
            continue
    if stamps:
        state.freshest_sync_age_hours = (now - max(stamps)).total_seconds() / 3600
    if game_starts:
        state.game_started, state.games_finished = kickoff.games_state(
            pd.Series(game_starts), now, finished_after_hours
        )
    return state


def stale_items(
    *,
    now: datetime,
    manifest: dict,
    suggest_at: datetime | None,
    late_swap_at: datetime | None,
    season_at: datetime | None,
    day: int,
) -> list[str]:
    """What is out of date, each one sentence. Empty when everything the page reads is fresh."""
    items = []
    stamps = {}
    for name, v in manifest.items():
        try:
            stamps[name] = datetime.fromisoformat(v["synced_at"])
        except (KeyError, TypeError, ValueError):
            continue
    failed = sorted(n for n, v in manifest.items() if v.get("error"))
    if not manifest:
        items.append("Nothing is synced: run `dfs sync` (the page has no data to read).")
    else:
        newest = max(stamps.values()) if stamps else None
        if newest and now - newest > timedelta(hours=launcher.STALE_HOURS):
            items.append(
                f"Data is {ago(now, newest).replace(' ago', '')} old (last sync {when(now, newest)})."
            )
        if failed:
            items.append(f"Failed their last sync: {', '.join(failed)}.")
        if suggest_at and newest and suggest_at < newest - timedelta(minutes=5):
            items.append(
                f"The builds on Suggested are from {when(now, suggest_at)}, before the last sync "
                f"({when(now, newest)}): rebuild if the sync changed anything you care about."
            )
        if late_swap_at and newest and day == SUN and late_swap_at < newest - timedelta(minutes=5):
            items.append(f"Late swap last ran {when(now, late_swap_at)}, before the last sync: run it again.")
    if season_at and now - season_at > timedelta(days=4):
        items.append(f"The season-long view is from {when(now, season_at)}: `season sync` refreshes it.")
    return items
