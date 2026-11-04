"""A tiny run log: one line per `dfs` command Sam ran, so the Week page can say when each tool was last used.

`data/runlog.csv` has three columns: `time` (ISO, UTC), `command` (the command's name plus the NAMES of the
`--options` typed, never their values: `dfs sync --live`, `dfs week close --research`; a sheet id, a URL or a
file path is never written down) and `ok` (1 / 0). It is written by the CLI entry point
(`prompt_missing.PromptingGroup.main`, which every Typer app's top group is built from), so a command nobody
remembered to instrument is still logged, and nothing in a command's own code changes.

Never fails the command it logs. The path follows `paths.DATA_DIR` at call time, so the suite's hermetic data
dir (`tests/conftest.py`'s `_hermetic_data_dir`) keeps every test off the real file.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from dfs import paths

HEADER = ["time", "command", "ok"]


def runlog_path() -> Path:
    return paths.DATA_DIR / "runlog.csv"


@dataclass(frozen=True)
class Run:
    time: datetime
    command: str  # `dfs lineups late-swap --state`
    ok: bool

    @property
    def path(self) -> str:
        """The command's name without its options: `dfs sync --live` -> `dfs sync`."""
        return " ".join(w for w in self.command.split() if not w.startswith("-"))

    @property
    def flags(self) -> frozenset[str]:
        return frozenset(w for w in self.command.split() if w.startswith("--"))


def command_from_args(root: Any, args: list[str]) -> str | None:
    """`dfs <group> <command> [--option ...]` for the argv a run was given, or None when it should not be
    logged (no command: the launcher menu; `--help`; a name that is no command). Duck-typed on click's
    `.commands`."""
    if any(a in ("--help", "-h") for a in args):
        return None
    node = root
    words: list[str] = []
    flags: list[str] = []
    for token in args:
        if token.startswith("-"):
            if token.startswith("--"):
                flags.append(token.split("=", 1)[0])
            continue
        children = getattr(node, "commands", None)
        if children is None:
            continue  # past the command: a positional value, never logged
        sub = children.get(token)
        if sub is None:
            continue  # the value of an option given before the command
        words.append(token)
        node = sub
    if not words or hasattr(node, "commands"):
        return None  # no command, or only a group name (its usage page)
    return " ".join(["dfs", *words, *sorted(dict.fromkeys(flags))])


def record(command: str, ok: bool, *, now: datetime | None = None, path: Path | None = None) -> None:
    """Append one line. Never raises."""
    try:
        target = path or runlog_path()
        target.parent.mkdir(parents=True, exist_ok=True)
        fresh = not target.exists() or target.stat().st_size == 0
        stamp = (now or datetime.now(UTC)).astimezone(UTC).isoformat(timespec="seconds")
        with target.open("a", newline="") as fh:
            writer = csv.writer(fh)
            if fresh:
                writer.writerow(HEADER)
            writer.writerow([stamp, command, 1 if ok else 0])
    except Exception:  # noqa: BLE001 - a log must never fail the command it logs
        return


def read(path: Path | None = None) -> list[Run]:
    """Every logged run, oldest first (empty when there is no log, or it cannot be read)."""
    target = path or runlog_path()
    try:
        with target.open(newline="") as fh:
            rows = list(csv.DictReader(fh))
    except OSError:
        return []
    runs = []
    for row in rows:
        try:
            runs.append(
                Run(datetime.fromisoformat(row["time"]), str(row["command"]), str(row["ok"]).strip() == "1")
            )
        except (KeyError, ValueError, TypeError):
            continue
    return sorted(runs, key=lambda r: r.time)


def last_run(runs: list[Run], command: str, *, only_ok: bool = False) -> Run | None:
    """The latest run of `command` (a path such as `dfs sync`, or with options: `dfs sync --live`, which only
    counts a run that typed all of those options)."""
    words = command.split()
    path = " ".join(w for w in words if not w.startswith("-"))
    need = frozenset(w for w in words if w.startswith("--"))
    for run in reversed(runs):
        if run.path == path and need <= run.flags and (run.ok or not only_ok):
            return run
    return None
