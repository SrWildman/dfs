"""Which DraftKings Classic slate a command works on: `main` (the Sunday main slate, today's behaviour) or a
named secondary slate of the same NFL week (`thu` for Thanksgiving, `sat` for a Saturday, `fri` for
Christmas).

**Choosing it.** `--slate <key>` on the commands that take one (`SLATE_COMMANDS`), or the environment variable
`DFS_SLATE` (which `dfs week serve --slate thu` sets for the page's chat and run buttons, so a `dfs sync`
typed
there works on the same slate). The CLI entry point (`prompt_missing.PromptingGroup.main`) reads it before the
command runs and holds it for the whole command (`active`). Left out, it is `main`, and nothing changes.

**Every command takes it** (Sam, Nov 23: "It's a normal slate like anything else"). `PromptingGroup` gives
every command a `--slate` option; only the season-long and login commands (`MAIN_ONLY`) refuse a secondary
slate, since a slate means nothing to them. Each slate keeps its own records (below), and every record it
writes carries its key (`SLATE_COLUMN`). The fitted models (the cash line offset, the ownership fits, the
calls weights, the copies estimate, the calibration) are main's: they fit on main slates only unless config
says `[learning] fit_secondary_slates = true` (`fit_keys`). The money is every slate's own (`slate_chain`).

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

# The commands a secondary slate means nothing to: the season-long leagues (their own sheet, `season.toml`)
# and the logins. Every other command takes `--slate`.
MAIN_ONLY: tuple[str, ...] = ("dfs season", "dfs auth")

# Every record a slate writes carries its key, as this column (a CSV) or field (JSON).
SLATE_COLUMN = "SlateKey"

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


def main_only(command: str | None) -> bool:
    """True for a command under `MAIN_ONLY` (`dfs season trades`, `dfs auth tffb`)."""
    path = command_path(command) or ""
    return any(path == p or path.startswith(p + " ") for p in MAIN_ONLY)


def refusal(command: str | None, key: str) -> str | None:
    """Why `command` will not run on slate `key`, or None when it may. `main` always may; every command but
    the `MAIN_ONLY` ones takes a secondary slate."""
    if key == MAIN or command is None or not main_only(command):
        return None
    return (
        f"`{command_path(command)}` has nothing to do with a DraftKings slate, so it does not take one (the "
        f"{label(key)} slate was asked for). Run it without --slate (and with {ENV_VAR} unset)."
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


def data_path(main_path: Path, key: str | None = None) -> Path:
    """A record's path for slate `key` (the active one by default): `main_path` itself on main; on a
    secondary slate the same place under its folder (`data/contests/edge_at_lock.csv` ->
    `data/slates/thu/contests/edge_at_lock.csv`). `main_path` is under `data/` (absolute, or the relative
    `data/...` some modules use); one that is not raises SlateError rather than let a slate write a main
    record."""
    from dfs import paths

    k = validate(key or _current)
    p = Path(main_path)
    if k == MAIN:
        return p
    for base in (paths.DATA_DIR, Path("data")):
        try:
            rel = p.relative_to(base)
        except ValueError:
            continue
        if rel.parts[:1] == (SLATES_SUBDIR,):
            return p  # already a slate's
        return root(k) / rel
    raise SlateError(f"{p} is not under data/: no place for the {label(k)} slate's copy of it.")


def tag(frame, key: str | None = None):  # noqa: ANN001, ANN201 - a DataFrame
    """`frame` with its `SlateKey` column (`SLATE_COLUMN`) set to `key` (the active slate by default). A main
    record is left as it is: no column means main (`of`), so main's files keep today's shape."""
    k = validate(key or _current)
    return frame if k == MAIN else frame.assign(**{SLATE_COLUMN: k})


def columns(names: list[str]) -> list[str]:
    """A CSV's columns as a record writes them: `names`, plus `SlateKey` on a secondary slate (a main file
    keeps today's columns)."""
    return list(names) if is_main() else [*names, SLATE_COLUMN]


def stamp(row: dict) -> dict:
    """A record (a CSV row or a JSON line) with its slate key on a secondary slate; main's as it was."""
    return dict(row) if is_main() else {**row, SLATE_COLUMN: _current}


def configured_keys() -> list[str]:
    """The secondary slates config.toml names (`[slates.<key>]`), sorted; [] when it cannot be read."""
    try:
        from dfs.config import load_base_config

        return sorted((load_base_config().slates or {}).keys())
    except Exception:  # noqa: BLE001 - no config (tests, a fresh checkout): no secondary slate
        return []


def fit_keys() -> list[str]:
    """The slates the fitted models train on: `["main"]`, plus every configured secondary slate when
    config.toml says `[learning] fit_secondary_slates = true` (Sam, Nov 23: "fit on main slates by
    default"). Read whatever slate is active: a fit is one model, the main slate's."""
    try:
        from dfs.config import load_base_config

        include = bool(load_base_config().learning.fit_secondary_slates)
    except Exception:  # noqa: BLE001 - no config: main only
        include = False
    return [MAIN, *(configured_keys() if include else [])]


def read_keys() -> list[str]:
    """The slates whose records a reader sees: main's, the fitted slates' (`fit_keys`) and the active
    slate's own. Main with no switch: `["main"]`, today's records exactly. A player plays one game a week, so
    a secondary slate's rows of a week never repeat a main row of it."""
    out = list(fit_keys())
    if _current not in out:
        out.append(_current)
    return out


def may_save_fit() -> bool:
    """True when a refit run now may replace the saved model: always on main; on a secondary slate only when
    its records are part of the fits (`fit_keys`), since otherwise the slate's own rows would move main's
    model."""
    return _current in fit_keys()


def of(frame):  # noqa: ANN001, ANN201 - a DataFrame -> a Series
    """Each row's slate: its `SlateKey` column, main where it is blank or missing (a main record has none)."""
    import pandas as pd

    if SLATE_COLUMN not in frame.columns:
        return pd.Series(MAIN, index=frame.index, dtype=object)
    col = frame[SLATE_COLUMN].astype(object)
    return col.where(col.notna() & (col.astype(str).str.strip() != ""), MAIN).astype(str)


def own_rows(frame, key: str | None = None):  # noqa: ANN001, ANN201 - DataFrames
    """The rows of slate `key` (the active one by default)."""
    return frame[of(frame) == validate(key or _current)]


def fit_paths(main_path: Path) -> list[Path]:
    """Every slate's copy of a record a fit reads (`fit_keys`), main's first, those that exist."""
    out = [Path(main_path)]
    for k in fit_keys():
        if k != MAIN:
            p = data_path(main_path, k)
            if p.exists():
                out.append(p)
    return out


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
