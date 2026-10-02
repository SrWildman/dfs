"""Generates `docs/COMMANDS.md`, the complete command reference, from the CLI itself.

A hand-written list of commands drifts the moment a command or option is added (the README, WORKFLOW and
CLAUDE.md each carried a different, partial list). This renders the real thing: every visible command, its
description (the first paragraph of its docstring) and every option with its help text and default, grouped by
WHEN you reach for it, plus the data-source registry that `dfs sync --only` takes.
`tests/test_commands_doc.py` fails if `docs/COMMANDS.md` is out of date, or if a command has no
section assigned below -- so adding a command forces a decision about where it belongs.

Regenerate after changing any command or option:

    python -m dfs.commands_doc            # rewrites docs/COMMANDS.md
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from typer.main import get_command

from dfs.paths import REPO_ROOT

# Typer bundles its own click, so commands are handled by duck typing (`.commands`, `.params`, `.opts`).
Command = Any

COMMANDS_DOC = REPO_ROOT / "docs" / "COMMANDS.md"

# The standard week: what you actually run, in order. This is the primary thing to read; everything else in
# the reference is for when you need it. (step, when, command as typed, what it does). Every command named
# here must exist (checked by the test).
STANDARD_WEEK: list[tuple[str, str, str, str]] = [
    (
        "1",
        "Tuesday/Wednesday: a new week starts",
        'dfs week new "<url-of-the-sheet-copy>"',
        "Points the CLI at a fresh copy of the template, carries your bankroll and Results forward, runs a "
        "full sync. Then check `dfs status`.",
    ),
    (
        "2",
        "Through the week, as lines and injuries move",
        "dfs sync",
        "Re-pulls every source into the sheet. Safe to run as often as you like.",
    ),
    (
        "3",
        "In the sheet (no command)",
        "Board, Slate Grid, EdgeRaw, Player Pool, Lineups",
        "Research, tick your pool, build lineups. `dfs pool add|remove|list` does the pool without opening "
        "the sheet.",
    ),
    (
        "4",
        "Lineups are built",
        "dfs export -o lineups.csv",
        "Validates your lineups and writes DraftKings' bulk-upload CSV.",
    ),
    (
        "5",
        "Sunday, before and between kickoffs",
        "dfs sync --live",
        "Fast refresh of odds, DK statuses and weather only; prints what changed. Then `dfs lineups "
        "late-swap` shows who is still swappable.",
    ),
    (
        "6",
        "After the games (nflverse posts stats a day or two late)",
        "dfs week close --csv history.csv",
        "Reconciles Cash/GPP into Bankroll and Results, then scores the week's projections into the Model "
        "Check tab. `dfs results update` does just the scoring, any time.",
    ),
]

# Section title -> (blurb, ordered commands). Every visible command must appear in exactly one section, so
# adding a command forces a decision about where it belongs. The FIRST section is the least-needed on
# purpose-reading order: the standard week above is what most people use.
SECTIONS: list[tuple[str, str, list[str]]] = [
    (
        "Also used most weeks",
        "Not part of the six steps above, but handy: checking where you are, a quick look at the best "
        "plays, and the commands the standard week calls for you.",
        [
            "dfs status",
            "dfs doctor",
            "dfs go",
            "dfs edge",
            "dfs week new",
            "dfs sync",
            "dfs export",
            "dfs lineups late-swap",
            "dfs week close",
            "dfs results update",
            "dfs pool add",
            "dfs pool remove",
            "dfs pool list",
            "dfs pool clear",
            "dfs odds movement",
        ],
    ),
    (
        "Reference: bankroll, logs and accounts (only when needed)",
        "Reconciling results and keeping records, and one-time logins. `dfs week close` already calls "
        "`bankroll sync` for you.",
        [
            "dfs bankroll sync",
            "dfs ownership log",
            "dfs lineups clear",
            "dfs auth tffb",
            "dfs auth dk",
            "dfs auth fantasypros",
            "dfs bankroll backfill-keys",
            "dfs bankroll build-betting-ledger",
        ],
    ),
    (
        "Reference: building and styling a sheet (only when needed)",
        "You do not run these in a normal week. Run against the TEMPLATE first, then the live sheet "
        "(`--sheet-id`), then `dfs doctor` and `dfs setup audit-style` on both. `dfs setup sheet` runs the "
        "whole build in the one order that works.",
        [
            "dfs setup sheet",
            "dfs setup polish",
            "dfs setup build-views",
            "dfs setup instructions",
            "dfs setup link-edge",
            "dfs setup reorder-columns",
            "dfs setup add-pool-control",
            "dfs setup add-filters",
            "dfs setup protect",
            "dfs setup audit-style",
            "dfs setup build-season",
            "dfs setup inspect",
        ],
    ),
    (
        "Reference: one-time repairs and migrations (only when needed)",
        "Each of these fixed a specific past change to the sheet's structure; they are safe to re-run but "
        "you should not need them. `CONTRIBUTING.md`'s structural changelog says when each ran.",
        [
            "dfs setup repair-formula-ranges",
            "dfs setup guard-empty-states",
            "dfs setup resize-player-pool",
            "dfs setup fix-opp-pos-rank",
            "dfs setup fix-pct-of-cap",
            "dfs setup fix-flag-split",
            "dfs setup fix-lineups-dst-label",
            "dfs setup remove-pool-deck",
            "dfs setup remove-retired-tabs",
            "dfs setup remove-sos-placeholders",
            "dfs setup remove-lineup-metrics",
            "dfs setup remove-model-implied",
        ],
    ),
]


def _walk(command: Command, prefix: str) -> dict[str, Command]:
    """Every visible leaf command under `command`, keyed by its full `dfs ...` path."""
    found: dict[str, Command] = {}
    for name, sub in getattr(command, "commands", {}).items():
        if getattr(sub, "hidden", False):
            continue
        path = f"{prefix} {name}".strip()
        if hasattr(sub, "commands"):
            found.update(_walk(sub, path))
        else:
            found[path] = sub
    return found


def all_commands() -> dict[str, Command]:
    """Every visible command, keyed `dfs <group> <name>`."""
    from dfs.cli import app

    return _walk(get_command(app), "dfs")


def _first_paragraph(help_text: str | None) -> str:
    """The first paragraph of a docstring, joined onto one line."""
    if not help_text:
        return ""
    text = help_text.strip().split("\n\n")[0]
    return re.sub(r"\s+", " ", text).strip()


def _type_hint(param: Any) -> str:
    """` INTEGER`-style suffix for an option that takes a value (flags and plain text take none/TEXT)."""
    name = param.type.name
    return " TEXT" if name in {"text", "string"} else f" {name.upper()}"


def _option_rows(command: Command) -> list[tuple[str, str]]:
    """`(option spelling, help text)` for every option and argument of `command`."""
    rows = []
    for param in command.params:
        if param.name == "help":
            continue
        opts = getattr(param, "opts", None)
        if opts:  # an option
            names = " / ".join(opts) + ("" if getattr(param, "is_flag", False) else _type_hint(param))
        else:  # a positional argument
            names = param.name.upper()
        parts = [_first_paragraph(getattr(param, "help", "") or "")]
        if getattr(param, "required", False):
            parts.append("**Required.**")
        default = getattr(param, "default", None)
        if default not in (None, False, (), "") and not getattr(param, "required", False):
            parts.append(f"Default: `{default}`.")
        rows.append((names, " ".join(p for p in parts if p)))
    return rows


def _render_command(path: str, command: Command) -> list[str]:
    lines = [f"### `{path}`", ""]
    description = _first_paragraph(command.help) or _first_paragraph(command.short_help)
    lines += [description, ""]
    rows = _option_rows(command)
    if rows:
        lines += ["| Option | What it does |", "|---|---|"]
        lines += [f"| `{name}` | {text.replace('|', '/')} |" for name, text in rows]
        lines.append("")
    return lines


def standard_week_commands() -> list[str]:
    """Every command named in `STANDARD_WEEK`, resolved to its real `dfs ...` path (longest match first, so
    `dfs week close --csv x` is `dfs week close` and `dfs sync --live` is `dfs sync`)."""
    known = set(all_commands())
    names = []
    for _step, _when, command, _what in STANDARD_WEEK:
        words = command.split()
        if words[0] != "dfs":
            continue
        for length in (3, 2):
            candidate = " ".join(words[:length])
            if candidate in known:
                names.append(candidate)
                break
        else:
            names.append(" ".join(words[:2]))  # unknown: left as typed so the test names it
    return names


def unassigned(commands: dict[str, Command]) -> list[str]:
    """Visible commands that no section lists (a new command must be placed deliberately)."""
    placed = {c for _, _, group in SECTIONS for c in group}
    return sorted(set(commands) - placed)


def stale_entries(commands: dict[str, Command]) -> list[str]:
    """Names in `SECTIONS` that no longer exist (a renamed or removed command)."""
    placed = {c for _, _, group in SECTIONS for c in group}
    return sorted(placed - set(commands))


def _tab_mappings() -> dict[str, str]:
    """Default source -> sheet tab names from `config.example.toml`'s `[google_sheets.tab_mappings]`."""
    import tomllib

    path = REPO_ROOT / "config.example.toml"
    return tomllib.loads(path.read_text())["google_sheets"]["tab_mappings"] if path.exists() else {}


def _source_summary(source: Any) -> str:
    """One sentence on what a source is: its class docstring, else its module's."""
    import sys

    text = _first_paragraph(type(source).__doc__) or _first_paragraph(
        sys.modules[type(source).__module__].__doc__
    )
    first = re.split(r"(?<=[.])\s", text, maxsplit=1)[0]
    return first[:200]


def _render_sources() -> list[str]:
    from dfs.cli import LIVE_SYNC_SOURCES
    from dfs.sources import SOURCES

    tabs = _tab_mappings()
    out = [
        "## Data sources",
        "",
        "What `dfs sync --only NAME[,NAME]` takes. A plain `dfs sync` runs all of them in this order; "
        "`dfs sync "
        "--live` re-runs only the ones marked. The tab is the default name from `config.example.toml`; "
        "a source "
        "with no tab feeds EdgeRaw directly. A source that fails is reported and skipped, never fatal to the "
        "others.",
        "",
        "| Source | Sheet tab | In `--live` | What it is |",
        "|---|---|---|---|",
    ]
    for name, source in SOURCES.items():
        tab = tabs.get(name, "(none)") if source.uploads_to_sheet else "(none)"
        live = "yes" if name in LIVE_SYNC_SOURCES else "-"
        out.append(f"| `{name}` | `{tab}` | {live} | {_source_summary(source).replace('|', '/')} |")
    out.append("")
    return out


def render() -> str:
    """The whole `docs/COMMANDS.md` as text."""
    from dfs.cli import app

    root = get_command(app)
    commands = all_commands()
    out = [
        "# Command reference",
        "",
        "Every `dfs` command and option, generated from the CLI itself (`python -m dfs.commands_doc`), so it",
        "cannot drift from the code. `dfs <command> --help` shows the same text. For *when* to run what, see",
        "[WORKFLOW.md](WORKFLOW.md); for what a sheet column means, see",
        "[SHEET_REFERENCE.md](SHEET_REFERENCE.md).",
        "",
        "Run `dfs` with no arguments for a compact status and the two or three commands that make "
        "sense right",
        "now.",
        "",
    ]
    out += [
        "## Your standard week",
        "",
        "This is what most weeks look like. Everything below it is reference.",
        "",
    ]
    out += ["| # | When | Run | What it does |", "|---|---|---|---|"]
    for step, when, command, what in STANDARD_WEEK:
        shown = f"`{command}`" if command.startswith("dfs ") else command
        out.append(f"| {step} | {when} | {shown} | {what.replace('|', '/')} |")
    out += [
        "",
        "Not sure what to run next? Run `dfs` on its own: it shows where you are in the week and the two or "
        "three commands that make sense right now.",
        "",
    ]
    global_rows = _option_rows(root)
    if global_rows:
        out += ["## Global options", "", "| Option | What it does |", "|---|---|"]
        out += [f"| `{name}` | {text.replace('|', '/')} |" for name, text in global_rows]
        out.append("")
    for title, blurb, names in SECTIONS:
        out += [f"## {title}", "", blurb, ""]
        for name in names:
            if name in commands:
                out += _render_command(name, commands[name])
    out += _render_sources()
    return "\n".join(out).rstrip("\n") + "\n"


if __name__ == "__main__":
    Path(COMMANDS_DOC).write_text(render())
    print(f"wrote {COMMANDS_DOC}")
