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

The menu itself (`MENU_SECTIONS`) is a FIXED list -- the standard week is always on screen, in order, so Sam
never has to remember a command; the state only decides which entry gets the "suggested" marker. A command
that needs an argument (a URL, a CSV path, a player name) is prompted for, not refused: `missing_required`
reads the command's own required parameters, so a new required option is asked for automatically.
"""

from __future__ import annotations

import re
import shlex
from dataclasses import dataclass
from typing import Any

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
        return [Suggestion("Start this week's sheet", "dfs week new")]

    if state.sheet_error:
        return [Suggestion("Can't reach the sheet -- check config/credentials", "dfs status")]

    if state.synced_sources == 0:
        return [Suggestion("Sync data", "dfs sync")]

    if state.games_finished:
        return [
            Suggestion("Reconcile results into your bankroll", "dfs bankroll sync"),
            Suggestion("Close out the week", "dfs week close"),
        ]

    if state.game_started:
        return [
            Suggestion("Re-sync live data (odds, statuses, weather)", "dfs sync --live"),
            Suggestion("Check for late swaps", "dfs lineups late-swap"),
        ]

    suggestions: list[Suggestion] = []
    if state.lineups_total and state.lineups_filled >= state.lineups_total:
        suggestions.append(Suggestion("Export DK's upload CSV", "dfs export"))
    elif state.pool_count:
        suggestions.append(Suggestion("Build lineups in the sheet", None))
        suggestions.append(Suggestion("Add more players to your pool", "dfs pool add"))
    else:
        suggestions.append(Suggestion("Add players to your pool", "dfs pool add"))
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


@dataclass
class MenuItem:
    """One numbered line on the menu. `command` is the bare `dfs ...` text with no arguments (they are
    prompted for); None means "this happens in the sheet"."""

    label: str
    command: str | None


# The standard week, in order -- the same six steps as docs/COMMANDS.md's "Your standard week".
STANDARD_WEEK_ITEMS = [
    MenuItem("Start a new week", "dfs week new"),
    MenuItem("Sync all data", "dfs sync"),
    MenuItem("Build lineups (research + pool, in the sheet)", None),
    MenuItem("Export the DraftKings upload file", "dfs export"),
    MenuItem("Gameday: refresh odds/statuses/weather", "dfs sync --live"),
    MenuItem("Gameday: who can still be swapped?", "dfs lineups late-swap"),
    MenuItem("Close out the week (after the games)", "dfs week close"),
]

# Used most weeks but not part of the six steps.
HANDY_ITEMS = [
    MenuItem("Add a player to your pool", "dfs pool add"),
    MenuItem("Remove a player from your pool", "dfs pool remove"),
    MenuItem("Show your pool", "dfs pool list"),
    MenuItem("Score projections (Model Check)", "dfs results update"),
    MenuItem("Status: config, sheet, data freshness", "dfs status"),
    MenuItem("Check the sheet's structure", "dfs doctor"),
]

MENU_SECTIONS = [("Your standard week", STANDARD_WEEK_ITEMS), ("Also handy", HANDY_ITEMS)]

# What to offer when the prompt can reasonably propose an answer: (command, parameter name) -> default.
ANSWER_DEFAULTS = {("dfs export", "output"): "lineups.csv"}

# Plain-English questions where the CLI's own help text reads too much like a flag description.
QUESTIONS = {
    ("dfs week new", "sheet_url"): "Link (or ID) of this week's sheet -- the copy of the template",
    ("dfs export", "output"): "File to write",
    ("dfs week close", "csv"): "DraftKings contest-history CSV (My Contests > export) -- drag the file in",
    ("dfs bankroll sync", "csv"): "DraftKings contest-history CSV (My Contests > export) -- drag the file in",
    ("dfs pool add", "names"): "Player name(s), separated by commas",
    ("dfs pool remove", "names"): "Player name(s), separated by commas",
}


def command_path(command: str) -> str:
    """`dfs sync --live` -> `dfs sync`: the command's name without the flags a menu entry carries."""
    words = []
    for token in shlex.split(command):
        if token.startswith("-"):
            break
        words.append(token)
    return " ".join(words)


def suggested_commands(state: LauncherState) -> set[str | None]:
    """The commands `suggest_actions` recommends right now, for the menu's marker (None = "in the sheet")."""
    return {a.command for a in suggest_actions(state)}


def menu_sections(state: LauncherState) -> list[tuple[str, list[MenuItem]]]:
    """`MENU_SECTIONS`, plus any suggested command that is not on it (e.g. reconciling results) appended
    to the "Also handy" section so a suggestion is never invisible."""
    shown = {item.command for _t, items in MENU_SECTIONS for item in items}
    extras = [
        MenuItem(a.label, a.command) for a in suggest_actions(state) if a.command and a.command not in shown
    ]
    return [(title, items + (extras if title == "Also handy" else [])) for title, items in MENU_SECTIONS]


@dataclass
class MissingParam:
    """A required parameter not yet supplied: how to ask for it, and how to turn the answer into argv."""

    name: str
    question: str
    default: str | None
    flag: str | None  # the long option (`--csv`), or None for a positional argument
    many: bool  # a positional that takes several values (player names)


def prompt_spec(command: str, param: Any) -> MissingParam:
    """How to ask for one click parameter. Duck-typed (Typer bundles its own click): reads `.name`, `.opts`,
    `.nargs`, `.help` and `.type.choices`."""
    opts = list(getattr(param, "opts", []))
    is_option = bool(opts) and opts[0].startswith("-")
    flag = next((o for o in opts if o.startswith("--")), opts[0]) if is_option else None
    path = command_path(command)
    question = (QUESTIONS.get((path, param.name)) or getattr(param, "help", None) or param.name).rstrip(".")
    choices = getattr(getattr(param, "type", None), "choices", None)
    if choices:
        question += f" ({' / '.join(choices)})"
    return MissingParam(
        name=param.name,
        question=question,
        default=ANSWER_DEFAULTS.get((path, param.name)),
        flag=flag,
        many=(not is_option and getattr(param, "nargs", 1) == -1),
    )


def missing_required(command: str, params: list[Any]) -> list[MissingParam]:
    """Every required parameter among `params` (a click command's), as something to prompt for."""
    return [prompt_spec(command, p) for p in params if getattr(p, "required", False)]


def clean_path_or_text(answer: str) -> str:
    """What the terminal hands over when a file is dragged in: quotes or backslash-escaped spaces. Decoded
    with shell rules; falls back to the stripped text if it is not valid shell quoting (an apostrophe)."""
    text = answer.strip()
    try:
        parts = shlex.split(text)
    except ValueError:
        return text
    return parts[0] if len(parts) == 1 else text


def answer_to_args(param: MissingParam, answer: str) -> list[str]:
    """The argv fragment for one answered prompt: `--csv path`, or the bare value(s) of an argument."""
    if param.many:
        values = [v.strip() for v in re.split(r"[,;]", answer) if v.strip()]
    else:
        values = [clean_path_or_text(answer)]
    return [param.flag, values[0]] if param.flag else values


# One plain-English line per command for the "More commands" screen (the docstrings carry project history).
# tests/test_launcher.py fails if a command in docs/COMMANDS.md has no label here (a new command needs one).
MORE_LABELS = {
    "dfs go": "Sync, check the sheet and report what changed, in one go",
    "dfs edge": "Show the top leverage plays from the last sync",
    "dfs pool clear": "Clear your whole pool",
    "dfs odds movement": "Which betting lines moved since the last sync",
    "dfs bankroll sync": "Reconcile a DK contest-history CSV into Bankroll",
    "dfs ownership log": "Log a contest's actual ownership",
    "dfs lineups clear": "Clear last week's typed-in lineups",
    "dfs auth tffb": "Log in to The Fantasy Footballers (one time)",
    "dfs auth dk": "Log in to DraftKings (one time)",
    "dfs auth fantasypros": "Log in to FantasyPros (one time)",
    "dfs bankroll backfill-keys": "Repair: fill in Bankroll's dedupe keys",
    "dfs bankroll build-betting-ledger": "Build the hand-entered Betting ledger",
    "dfs setup sheet": "Build a whole sheet from scratch (the full setup)",
    "dfs setup polish": "Re-apply widths, formats and header styling",
    "dfs setup build-views": "Rebuild Board, Slate Grid and the other derived tabs",
    "dfs setup instructions": "Rewrite the Instructions tab",
    "dfs setup link-edge": "Link EdgeRaw's columns into Player Pool and Lineups",
    "dfs setup reorder-columns": "Reorder the Pool/Lineups columns",
    "dfs setup add-pool-control": "Add Player Pool's add-a-player row",
    "dfs setup add-filters": "Add the sort/search filters",
    "dfs setup protect": "Add warning-only protection to formula ranges",
    "dfs setup audit-style": "Check every tab's styling (read-only)",
    "dfs setup build-season": "Build the Season tab",
    "dfs setup inspect": "List every tab with its size and headers",
    "dfs setup repair-formula-ranges": "Repair the formula gaps `dfs doctor` reports",
    "dfs setup guard-empty-states": "Restore the empty-state guards on division cells",
    "dfs setup resize-player-pool": "Resize the Player Pool position blocks",
    "dfs setup fix-opp-pos-rank": "One-time fix: opponent position rank",
    "dfs setup fix-pct-of-cap": "One-time fix: the % of Own column",
    "dfs setup fix-flag-split": "One-time fix: split the Flag column",
    "dfs setup fix-lineups-dst-label": "One-time fix: Lineups' DST label",
    "dfs setup remove-pool-deck": "Remove the retired pool deck",
    "dfs setup remove-retired-tabs": "Remove retired tabs",
    "dfs setup remove-sos-placeholders": "Remove the SoS placeholder tabs",
    "dfs setup remove-lineup-metrics": "Remove the retired Lineups metric columns",
    "dfs setup remove-model-implied": "Remove the retired model-implied column",
}
