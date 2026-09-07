"""Pure state -> suggestion logic for `dfs` run with no subcommand.

Split out from cli.py deliberately: this is the one place in the project
that guesses what to do next, and Task 2.4 of the CLI-reorg brief was
explicit that a launcher whose suggestions drift from reality is worse
than no launcher at all -- "build your pool" still showing after 60
players are ticked is the failure mode that loses trust in a week. Every
branch below reads a fact in `LauncherState`, never a step counter or a
record of "what ran last", so re-deriving suggestions after any real
change (one more tick, a game kicking off) always reflects it immediately.

Kept network-free and filesystem-free on purpose so `suggest_actions` is
directly unit-testable (see tests/test_launcher.py) without faking Sheets
or the local data store. `cli.gather_state` is the impure half: it collects
the actual observed facts (config, sheet, manifest, pool/lineup counts,
kickoff times) and hands them here as a `LauncherState`.
"""

from __future__ import annotations

from dataclasses import dataclass

# "Offer, don't force" (Task 2.4) -- past this age a re-sync is suggested
# alongside whatever else is going on, never substituted for it.
STALE_HOURS = 12.0


@dataclass
class Suggestion:
    label: str
    command: str | None  # None => "do this in the sheet", not a runnable command


@dataclass
class LauncherState:
    config_exists: bool = True
    config_error: str | None = None

    sheet_title: str | None = None
    sheet_error: str | None = None

    week: int | None = None
    synced_sources: int = 0
    freshest_sync_age_hours: float | None = None

    pool_count: int | None = None
    pool_error: str | None = None

    lineups_filled: int = 0
    lineups_total: int = 0
    lineups_error: str | None = None

    # Derived from EdgeRaw's own GameStart column (already synced locally --
    # see `late_swap.py`), never a network call of its own.
    game_started: bool = False
    games_finished: bool = False


def suggest_actions(state: LauncherState) -> list[Suggestion]:
    """The ordered list of things worth doing right now. Short-circuits at
    the first condition that's actually true and blocking (no config, no
    sheet, no data at all) -- everything past that point can combine (e.g.
    a stale-data nudge alongside whatever the pool/lineup state suggests)."""
    if not state.config_exists:
        return [Suggestion("Start this week's sheet", "dfs week new <url-of-the-copy>")]

    if state.sheet_error:
        return [Suggestion("Can't reach the sheet -- check config/credentials", "dfs status")]

    if state.synced_sources == 0:
        return [Suggestion("Sync data", "dfs sync")]

    if state.games_finished:
        return [
            Suggestion("Reconcile results into your bankroll", "dfs bankroll sync --csv <export.csv>"),
            Suggestion("Close out the week", "dfs week close --csv <export.csv>"),
        ]

    if state.game_started:
        return [
            Suggestion("Re-sync live data (odds, statuses, weather)", "dfs sync --live"),
            Suggestion("Check for late swaps", "dfs lineups late-swap"),
        ]

    suggestions: list[Suggestion] = []
    if state.lineups_total and state.lineups_filled >= state.lineups_total:
        suggestions.append(Suggestion("Export DK's upload CSV", "dfs export -o lineups.csv"))
    elif state.pool_count:
        suggestions.append(Suggestion("Build lineups in the sheet", None))
        suggestions.append(Suggestion("Add more players to your pool", "dfs pool add <name>"))
    else:
        suggestions.append(Suggestion("Add players to your pool", "dfs pool add <name>"))
        suggestions.append(Suggestion("...or tick them in EdgeRaw directly", None))

    if state.freshest_sync_age_hours is not None and state.freshest_sync_age_hours > STALE_HOURS:
        suggestions.append(
            Suggestion(f"Data is {state.freshest_sync_age_hours:.0f}h old -- re-sync?", "dfs sync")
        )

    return suggestions


def header_lines(state: LauncherState) -> list[str]:
    """The compact status header printed above the menu. Degrades field by
    field -- a value this session couldn't observe (no sheet reached, pool
    read failed) prints as `?` rather than dropping the whole line, so a
    half-working sheet still renders something instead of nothing."""
    if not state.config_exists:
        return ["No config.toml yet."]

    week = f"Week {state.week}" if state.week is not None else "Week ?"
    sheet = state.sheet_title or ("? (sheet unreachable)" if state.sheet_error else "?")
    if state.freshest_sync_age_hours is None:
        synced = "never synced" if state.synced_sources == 0 else "synced ?"
    else:
        synced = f"synced {state.freshest_sync_age_hours:.0f}h ago"
    line1 = f"{week} · {sheet!r} · {synced}"

    pool = str(state.pool_count) if state.pool_count is not None else ("?" if state.pool_error else "0")
    if state.lineups_error:
        lineups = "?"
    else:
        lineups = f"{state.lineups_filled}/{state.lineups_total}"
    line2 = f"Pool {pool} players · Lineups {lineups} filled"

    return [line1, line2]
