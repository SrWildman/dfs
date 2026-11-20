"""Which DraftKings Classic slate a command works on: `main` (the Sunday main slate, today's behaviour) or a
named secondary slate of the same NFL week (`thu` for Thanksgiving, `sat` for a Saturday, `fri` for
Christmas).

**Choosing it.** `--slate <key>` on the commands that take one (`SLATE_COMMANDS`), or the environment variable
`DFS_SLATE` (which `dfs week serve --slate thu` sets for the page's chat and run buttons, so a `dfs sync`
typed
there works on the same slate). The CLI entry point (`prompt_missing.PromptingGroup.main`) reads it before the
command runs and holds it for the whole command (`active`). Left out, it is `main`, and nothing changes.

**Default deny.** Any command not in `SLATE_COMMANDS` refuses a secondary slate before it runs
(`refusal`): the close, `results update`, the research and model commands, the contests engine and the
bankroll stay the main slate's alone, so nothing from a secondary slate can enter the main slate's records or
model fits.

**Where a secondary slate's files go.** Under its own folder, `data/slates/<key>/` (`root`): its synced
`current/` copies and `raw/` snapshots, its pools, builds (`suggest_state`), entries, late swap, Week page,
news log and signals archive, each with the same names `main` uses under `data/`. `main` keeps every path it
has today, so nothing existing moves, and no reader of a main folder (which globs only its own folder) can see
a secondary slate's file. The run lock (`data/.run.lock`) and the run log stay shared on purpose: one writing
command at a time across both slates.

**Its sheet.** `config.toml`'s `[slates.<key>] sheet_id` (written by `dfs week new --slate <key> <url>`);
`config.load_config` hands it out as `google_sheets.sheet_id` while the slate is active, so the main slate's
`sheet_id` is never read or written by a secondary slate's command.

**Its salaries.** The largest Classic draft group whose games all kick off on the slate's day of the NFL week
(`sources.dk_salaries.find_slate_draft_group`).
"""

from __future__ import annotations

import os
import re
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from pathlib import Path

MAIN = "main"
ENV_VAR = "DFS_SLATE"
SLATES_SUBDIR = "slates"

# A secondary slate is named by its day (Python weekday numbers, 0 = Monday). Sunday is the main slate's day.
DAYS: dict[str, int] = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5}
DAY_NAMES = {0: "Monday", 1: "Tuesday", 2: "Wednesday", 3: "Thursday", 4: "Friday", 5: "Saturday"}
KEYS = (MAIN, *DAYS)

# The commands that work on a secondary slate (their `--slate` option); every other command refuses one.
SLATE_COMMANDS = frozenset(
    {
        "dfs status",
        "dfs doctor",
        "dfs sync",
        "dfs export",
        "dfs pool add",
        "dfs pool remove",
        "dfs pool list",
        "dfs pool clear",
        "dfs lineups suggest",
        "dfs lineups late-swap",
        "dfs lineups clear",
        "dfs entries record",
        "dfs entries fill",
        "dfs entries print",
        "dfs week new",
        "dfs week page",
        "dfs week serve",
    }
)

SLATE_HELP = (
    "Which slate: main (the Sunday main slate, the default) or a secondary slate of the same week named by "
    "its "
    "day (thu, fri, sat, ...): its own sheet ([slates.<key>] in config.toml) and its own files under "
    "data/slates/<key>/."
)

_current: str = MAIN


class SlateError(ValueError):
    """An unknown slate key, or two different slates asked for at once."""


def validate(key: str | None) -> str:
    """The slate key, lower-cased; `main` for None or blank. Raises SlateError for an unknown key."""
    k = (key or "").strip().lower() or MAIN
    if k not in KEYS:
        raise SlateError(f"Unknown slate {key!r}: use one of {', '.join(KEYS)}.")
    return k


def current() -> str:
    """The slate the running command works on (`main` unless one was activated)."""
    return _current


def is_main() -> bool:
    return _current == MAIN


def label(key: str | None = None) -> str:
    """`main`, or `thu (Thursday)`: for messages."""
    k = key or _current
    return k if k == MAIN else f"{k} ({DAY_NAMES[DAYS[k]]})"


@contextmanager
def active(key: str | None) -> Iterator[str]:
    """Hold `key` as the current slate (and in `DFS_SLATE`, so a child `dfs` process inherits it) for the
    block, then restore what was there. `main` clears `DFS_SLATE` for the block."""
    global _current
    k = validate(key)
    before, env_before = _current, os.environ.get(ENV_VAR)
    _current = k
    if k == MAIN:
        os.environ.pop(ENV_VAR, None)
    else:
        os.environ[ENV_VAR] = k
    try:
        yield k
    finally:
        _current = before
        if env_before is None:
            os.environ.pop(ENV_VAR, None)
        else:
            os.environ[ENV_VAR] = env_before


def from_args(argv: Sequence[str], env: dict[str, str] | None = None) -> str:
    """The slate a command line asks for: `--slate X` / `--slate=X`, else `DFS_SLATE`, else `main`. Raises
    SlateError on an unknown key, or when the option and the environment name two different slates."""
    given: str | None = None
    args = list(argv)
    for i, token in enumerate(args):
        if token == "--slate" and i + 1 < len(args):
            given = args[i + 1]
        elif token.startswith("--slate="):
            given = token.split("=", 1)[1]
    environ = os.environ if env is None else env
    from_env = environ.get(ENV_VAR)
    opt = validate(given) if given is not None else None
    envk = validate(from_env) if from_env else None
    if opt is not None and envk is not None and opt != envk:
        raise SlateError(
            f"--slate {opt} but {ENV_VAR}={envk} (this terminal or page is on the {envk} slate). "
            f"Use one: drop --slate, or unset {ENV_VAR}."
        )
    return opt or envk or MAIN


def command_path(command: str | None) -> str | None:
    """`dfs lineups suggest --iterate --slate` -> `dfs lineups suggest` (the words before the first flag)."""
    if not command:
        return None
    return " ".join(w for w in command.split() if not w.startswith("-"))


def refusal(command: str | None, key: str) -> str | None:
    """Why `command` will not run on slate `key`, or None when it may. `main` always may."""
    if key == MAIN or command is None:
        return None
    path = command_path(command)
    if path in SLATE_COMMANDS:
        return None
    return (
        f"`{path}` runs for the main slate only, so it does not run on the {label(key)} slate: nothing from "
        "a "
        f"secondary slate enters the main slate's records. Run it without --slate (and with {ENV_VAR} unset)."
    )


# -- paths ---------------------------------------------------------------------------------------------------


def root(key: str | None = None) -> Path:
    """A secondary slate's own data folder, `data/slates/<key>/`; for `main`, `data/` itself."""
    from dfs import paths

    k = validate(key or _current)
    return paths.DATA_DIR if k == MAIN else paths.DATA_DIR / SLATES_SUBDIR / k


def path(main_path: Path, *parts: str) -> Path:
    """`main_path` on the main slate; on a secondary slate, the same place under its own folder
    (`root() / parts`). The main path is passed in (not rebuilt) so main's paths stay exactly what they
    were."""
    return Path(main_path) if is_main() else root().joinpath(*parts)


# -- the slate's day -----------------------------------------------------------------------------------------


def slate_date(key: str, season: int, week: int) -> date:
    """The calendar date of a secondary slate: its weekday inside the NFL week (Tuesday to Monday)."""
    from dfs import nfl_calendar

    k = validate(key)
    if k == MAIN:
        raise SlateError("The main slate is not named by a day.")
    start = nfl_calendar.week_start_date(week, season)  # a Tuesday
    return start + timedelta(days=(DAYS[k] - start.weekday()) % 7)


def in_slate_day(kickoff_et: datetime, key: str, season: int, week: int) -> bool:
    """True when an Eastern-time kickoff falls on the slate's date."""
    return kickoff_et.date() == slate_date(key, season, week)


# -- the sheet title -----------------------------------------------------------------------------------------

_SLATE_TITLE_RE = re.compile(r"^Week (\d+) (\S.*)$")


def title_week(title: str) -> int | None:
    """`Week 12 Thanksgiving` -> 12; None for any other shape (a plain `Week 12` is a main sheet's title)."""
    match = _SLATE_TITLE_RE.match(title.strip())
    return int(match.group(1)) if match else None


def default_title(key: str, week: int) -> str:
    """The title `dfs week new --slate` gives a copy that has none of its own: `Week 12 Thursday`."""
    return f"Week {week} {DAY_NAMES[DAYS[validate(key)]]}"


# -- a contest's slate (the close) ---------------------------------------------------------------------------


def contest_slate(moment: datetime | None, game_type: str, keys: Sequence[str]) -> str | None:
    """The secondary slate (one of `keys`, the slates config.toml has a sheet for) a contest belongs to, or
    None
    for the main slate's week as today: a Classic contest whose start (Eastern, as DraftKings' history gives
    it)
    falls on a secondary slate's day. A Sunday contest is never a secondary slate's; with no `keys` nothing
    is,
    so a week with no secondary slate closes exactly as before."""
    if moment is None or not keys or str(game_type).strip().casefold() != "classic":
        return None
    for key in keys:
        if key in DAYS and moment.weekday() == DAYS[key]:
            return key
    return None
