"""The Week page's "Your tools" list: every command and every phrase Sam can say, grouped by day, each with
what you get back and when it was last run. Generated, not written by hand:

- the commands come from the CLI's own registry (`commands_doc.all_commands()`), so a new command shows up on
  the page the moment it exists, and a test fails if one is missing from it;
- the phrases and what they return come from CLAUDE.md's two shortcut tables (`parse_phrases`), the same text
  Claude Code reads, so the page and the session cannot disagree about what "late swap" means;
- "last run" comes from the run log (`runlog.py`).

The only hand-kept data is `TOOL_DAY` (which weekday a command belongs to; everything not listed falls to its
`commands_doc` section) and `PHRASE_DAY` (phrases that run no command). `tests/test_week_page.py` fails if a
phrase or a standard-week command has no day, so the table cannot quietly rot.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from dfs import commands_doc, launcher, paths, runlog

MON = "Monday: close the week"
TUE = "Tuesday: start the new week"
SAT = "Saturday: build and enter"
SUN = "Sunday: game day"
SEASON = "Season-long leagues"
ANY = "Any day"
REFERENCE = "Reference and one-time setup"
DAY_GROUPS = [MON, TUE, SAT, SUN, SEASON, ANY, REFERENCE]

# Command -> the day it belongs to. Anything not here goes by its commands_doc section: "Also used most weeks"
# is ANY, the season-long section is SEASON, everything else is REFERENCE.
TOOL_DAY: dict[str, str] = {
    "dfs week close": MON,
    "dfs results update": MON,
    "dfs contests update": MON,
    "dfs bankroll sync": MON,
    "dfs research close-report": MON,
    "dfs research refresh": MON,
    "dfs week new": TUE,
    "dfs odds snapshot": TUE,
    "dfs lineups suggest": SAT,
    "dfs entries fill": SAT,
    "dfs entries print": SAT,
    "dfs entries record": SAT,
    "dfs contests plan": SAT,
    "dfs export": SAT,
    "dfs lineups late-swap": SUN,
    "dfs season sync": SEASON,
    "dfs season check": SEASON,
    "dfs season lineups": SEASON,
    "dfs season waivers": SEASON,
    "dfs season trades": SEASON,
    "dfs season trade": SEASON,
    "dfs season ros": SEASON,
}

# A phrase that runs no command is placed by hand (a sheet script, or reading a tab).
PHRASE_DAY: dict[str, str] = {"fix flex": SAT, "how did we do": MON, "add X, Y to my buy list": SEASON}

SECTION_DAY = {
    "Also used most weeks": ANY,
    "Reference: season-long leagues and the Season sheet (only when needed)": SEASON,
}

NOT_USED_AFTER = timedelta(days=21)


@dataclass
class Phrase:
    says: list[str]
    runs: str  # the "Run, in order" cell as written
    commands: list[str]  # every `dfs ...` in it, in order, as `dfs sync --live`
    report: str

    @property
    def command(self) -> str | None:
        return self.commands[0] if self.commands else None


@dataclass
class Tool:
    command: str  # `dfs lineups late-swap`, or "" for a phrase that runs no command
    description: str
    group: str
    phrases: list[Phrase] = field(default_factory=list)
    standard: bool = False  # part of the weekly routine: the 3-week "not used" check applies
    last: runlog.Run | None = None
    in_sheet: bool = False  # a phrase answered in the sheet, not by a command

    @property
    def says(self) -> list[str]:
        return [s for p in self.phrases for s in p.says]

    @property
    def report(self) -> str:
        return one_line(next((p.report for p in self.phrases if p.report), self.description), 230)

    @property
    def variants(self) -> list[str]:
        """The commands as the phrases type them (`dfs sync --live`), the plain one first."""
        seen = dict.fromkeys(c for p in self.phrases for c in p.commands if command_path(c) == self.command)
        return sorted(seen, key=lambda c: (len(c), c)) or [self.command]


# -- CLAUDE.md --------------------------------------------------------------------------------------

_CELLS = re.compile(r"(?<!\\)\|")


def clean_cell(text: str) -> str:
    return re.sub(r"\s+", " ", text.replace("\\|", "|")).strip()


def commands_with_flags(cell: str) -> list[str]:
    """Every `dfs ...` in a table cell, each reduced to its name and the NAMES of its required flags
    (`dfs sync --live`). Anything in [brackets] is optional in CLAUDE.md's notation and is dropped."""
    out = []
    for m in re.finditer(r"`(dfs [^`]+)`", cell):
        text = re.sub(r"\[[^\]]*\]", " ", m.group(1))
        flags = re.findall(r"(?<![\w-])--[a-z][a-z0-9-]*", text)
        command = " ".join([command_path(text), *dict.fromkeys(flags)])
        if command not in out:
            out.append(command)
    return out


def parse_phrases(text: str | None = None) -> list[Phrase]:
    """Every row of CLAUDE.md's shortcut tables (`| "start week N" | run | report |`)."""
    if text is None:
        try:
            text = (paths.REPO_ROOT / "CLAUDE.md").read_text()
        except OSError:
            return []
    out = []
    for line in text.splitlines():
        if not line.startswith('| "'):
            continue
        cells = [clean_cell(c) for c in _CELLS.split(line.strip().strip("|"))]
        if len(cells) < 3:
            continue
        says = re.findall(r'"([^"]+)"', cells[0])
        if says:
            out.append(Phrase(says, cells[1], commands_with_flags(cells[1]), cells[2]))
    return out


# -- the list ---------------------------------------------------------------------------------------


def command_path(command: str) -> str:
    """`dfs lineups late-swap --state live` -> `dfs lineups late-swap`: the leading words that are names
    (a flag, a placeholder such as <league> or a quoted value ends it)."""
    words = []
    for token in command.split():
        if not re.fullmatch(r"[a-z][a-z-]*", token):
            break
        words.append(token)
    return " ".join(words)


def section_of(path: str) -> str | None:
    for title, _blurb, group in commands_doc.SECTIONS:
        if path in group:
            return title
    return None


def group_of(path: str) -> str:
    if path in TOOL_DAY:
        return TOOL_DAY[path]
    return SECTION_DAY.get(section_of(path) or "", REFERENCE)


def one_line(text: str, limit: int = 170) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= limit:
        return text
    cut = text[:limit].rsplit(" ", 1)[0]
    return cut.rstrip(",;:") + "..."


def build(
    runs: list[runlog.Run], phrases: list[Phrase] | None = None, commands: dict | None = None
) -> list[Tool]:
    """Every registered command as a Tool (with the phrases that run it), plus one row for each phrase that
    runs no command, in `DAY_GROUPS` order."""
    commands = commands if commands is not None else commands_doc.all_commands()
    phrases = parse_phrases() if phrases is None else phrases
    standard = set(commands_doc.standard_week_commands())
    by_path: dict[str, list[Phrase]] = {}
    sheet_only: list[Phrase] = []
    for p in phrases:
        for command in p.commands:
            by_path.setdefault(command_path(command), []).append(p)
        if not p.commands:
            sheet_only.append(p)
    tools = []
    for path, command in commands.items():
        description = commands_doc._first_paragraph(command.help) or commands_doc._first_paragraph(
            command.short_help
        )
        label = launcher.MORE_LABELS.get(path)
        tools.append(
            Tool(
                command=path,
                description=one_line(label or description),
                group=group_of(path),
                phrases=by_path.get(path, []),
                standard=path in standard or path in by_path,
                last=runlog.last_run(runs, path),
            )
        )
    for p in sheet_only:
        tools.append(
            Tool(
                command="",
                description=one_line(p.report),
                group=PHRASE_DAY.get(p.says[0], ANY),
                phrases=[p],
                standard=True,
                in_sheet=True,
            )
        )
    order = {g: i for i, g in enumerate(DAY_GROUPS)}
    return sorted(tools, key=lambda t: (order[t.group], not t.phrases, t.command))


def unplaced_phrases(phrases: list[Phrase] | None = None, commands: dict | None = None) -> list[str]:
    """Phrases and standard-week commands whose day nobody chose (the test's list of things to place)."""
    commands = commands if commands is not None else commands_doc.all_commands()
    phrases = parse_phrases() if phrases is None else phrases
    missing = []
    for p in phrases:
        for command in p.commands:
            path = command_path(command)
            if path in commands and path not in TOOL_DAY and group_of(path) not in (ANY, SEASON):
                missing.append(path)
        if not p.commands and p.says[0] not in PHRASE_DAY:
            missing.append(p.says[0])
    for path in commands_doc.standard_week_commands():
        if path not in TOOL_DAY and group_of(path) != ANY:
            missing.append(path)
    return sorted(set(missing))


def not_used_lately(tools: list[Tool], runs: list[runlog.Run], now: datetime) -> tuple[list[Tool], str]:
    """The weekly tools not run in 3+ weeks, and a sentence about the log. Until the log itself is 3 weeks
    old there is nothing honest to flag, so the list is empty and the sentence says when it starts."""
    if not runs:
        return [], "The run log has no entries yet: it starts counting the first time you run a command."
    began = runs[0].time
    ready = began + NOT_USED_AFTER
    if now < ready:
        return [], (
            f"The run log began {began.astimezone().strftime('%b %-d')}: the 'not used in 3+ weeks' check "
            f"starts {ready.astimezone().strftime('%b %-d')}."
        )
    stale = [
        t
        for t in tools
        if t.standard and not t.in_sheet and (t.last is None or now - t.last.time > NOT_USED_AFTER)
    ]
    return stale, f"The run log began {began.astimezone().strftime('%b %-d')}."
