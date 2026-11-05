"""One writer at a time: `data/.run.lock`, shared by the terminal, the Week server's chat and its run buttons.

Two things that write a sheet (or `data/`) at once corrupt it: a `dfs sync` in the terminal while the chat
runs `dfs lineups suggest`, a button press during `dfs week close`. So every command that writes takes this
lock for as long as it runs, and a second writer is **refused** -- it says what is running, since when and
from where, and exits. There is no `--force`: wait, or stop the other one.

- **The lock is an OS file lock** (`flock`) on `data/.run.lock`; the file's text says who holds it (pid, the
  command, when it started, and the origin: `terminal`, `chat` or `button`). The OS drops the lock when the
  holder dies, so a crashed run never leaves it stuck; a holder whose pid is dead but whose lock somehow
  survives (an orphaned child that inherited the descriptor) is cleared and replaced. A run that ends --
  normally, with an error or with Ctrl-C -- removes the file as it lets go (a `finally`), so no stale
  `.run.lock` is left naming a finished command; only a killed process leaves one, which the next run ignores.
- **Which commands take it** is `WRITERS`; the ones that only read are `READ_ONLY` (status, doctor, week page,
  ...). `tests/test_run_lock.py` fails if a registered command is in neither, so a new command has to say.
- **Children inherit it.** A process that holds the lock exports `DFS_RUN_LOCK_PID`; a `dfs` it starts (`week
  close --research` runs four of them, the server's run buttons run one) sees that its parent holds the lock
  and carries on instead of refusing itself.
- **Origin** comes from `DFS_RUN_ORIGIN` (the server's chat and buttons set it; a terminal sets nothing).

The path follows `paths.DATA_DIR` at call time, so the suite's hermetic data dir keeps tests off the real
file.
Nothing here touches a sheet.
"""

from __future__ import annotations

import fcntl
import json
import os
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from dfs import paths

ORIGIN_ENV = "DFS_RUN_ORIGIN"
INHERIT_ENV = "DFS_RUN_LOCK_PID"
TERMINAL = "terminal"

# Commands that write a sheet or data/: they take the lock. (A `dfs sheets ...` is `dfs setup ...`.)
WRITERS = frozenset(
    {
        "dfs backtest",
        "dfs bankroll backfill-keys",
        "dfs bankroll build-betting-ledger",
        "dfs bankroll stakes",
        "dfs bankroll sync",
        "dfs contests analyze",
        "dfs contests plan",
        "dfs contests update",
        "dfs entries fill",
        "dfs entries record",
        "dfs export",
        "dfs go",
        "dfs lineups clear",
        "dfs lineups late-swap",
        "dfs lineups suggest",
        "dfs model fetch",
        "dfs model train",
        "dfs odds snapshot",
        "dfs ownership log",
        "dfs pool add",
        "dfs pool clear",
        "dfs pool remove",
        "dfs research fetch",
        "dfs research refresh",
        "dfs research run",
        "dfs results update",
        "dfs season lineups",
        "dfs season outlook",
        "dfs season ros",
        "dfs season setup",
        "dfs season sync",
        "dfs season trade",
        "dfs season trades",
        "dfs season waivers",
        "dfs setup add-filters",
        "dfs setup add-pool-control",
        "dfs setup audit-style",
        "dfs setup build-season",
        "dfs setup build-views",
        "dfs setup cockpit",
        "dfs setup compare",
        "dfs setup dk-upload",
        "dfs setup fix-flag-split",
        "dfs setup fix-lineups-dst-label",
        "dfs setup fix-opp-pos-rank",
        "dfs setup fix-pct-of-cap",
        "dfs setup guard-empty-states",
        "dfs setup instructions",
        "dfs setup lineup-chalk",
        "dfs setup link-edge",
        "dfs setup other-sports",
        "dfs setup player-card",
        "dfs setup polish",
        "dfs setup protect",
        "dfs setup remove-lineup-metrics",
        "dfs setup remove-model-implied",
        "dfs setup remove-pool-deck",
        "dfs setup remove-retired-tabs",
        "dfs setup remove-sos-placeholders",
        "dfs setup reorder-columns",
        "dfs setup repair-formula-ranges",
        "dfs setup resize-player-pool",
        "dfs setup sheet",
        "dfs setup stacks",
        "dfs setup suggested-controls",
        "dfs sim backtest",
        "dfs sim fit",
        "dfs sim script",
        "dfs stacks",
        "dfs sync",
        "dfs week close",
        "dfs week new",
    }
)

# Commands that only read (or only print, or only log in), so they run alongside anything.
READ_ONLY = frozenset(
    {
        "dfs auth dk",
        "dfs auth fantasypros",
        "dfs auth tffb",
        "dfs contests history",
        "dfs doctor",
        "dfs edge",
        "dfs entries print",
        "dfs model backtest",
        "dfs model info",
        "dfs odds movement",
        "dfs pool list",
        "dfs research close-report",
        "dfs season check",
        "dfs season doctor",
        "dfs setup inspect",
        "dfs sim demo",
        "dfs status",
        "dfs week page",
        "dfs week serve",
    }
)


def lock_path() -> Path:
    return paths.DATA_DIR / ".run.lock"


def normalize(command: str) -> str:
    """`dfs sync --live` -> `dfs sync`; the deprecated `dfs sheets X` -> `dfs setup X`."""
    words = [w for w in command.split() if not w.startswith("-")]
    if len(words) > 1 and words[1] == "sheets":
        words[1] = "setup"
    return " ".join(words)


def takes_lock(command: str) -> bool:
    """True for a command that writes (a name in neither table is not locked: it is no `dfs` command)."""
    return normalize(command) in WRITERS


def origin() -> str:
    return os.environ.get(ORIGIN_ENV) or TERMINAL


@dataclass(frozen=True)
class Holder:
    pid: int
    command: str
    since: datetime
    origin: str

    def describe(self) -> str:
        """`dfs sync is running (started 14:02:11, from the terminal, pid 4121)`."""
        where = {
            "terminal": "the terminal",
            "chat": "the Week page's chat",
            "button": "a Week page button",
        }.get(self.origin, self.origin)
        return (
            f"`{self.command}` is running (started {self.since.astimezone():%H:%M:%S}, from {where}, "
            f"pid {self.pid})"
        )


class LockHeld(RuntimeError):
    """Another writer holds the lock."""

    def __init__(self, holder: Holder | None):
        self.holder = holder
        what = holder.describe() if holder else "another dfs command is running"
        super().__init__(
            f"Not started: {what}. Only one command that writes runs at a time; wait for it or stop it."
        )


def pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _read_holder(path: Path) -> Holder | None:
    try:
        data = json.loads(path.read_text())
        return Holder(
            pid=int(data["pid"]),
            command=str(data["command"]),
            since=datetime.fromisoformat(data["since"]),
            origin=str(data.get("origin") or TERMINAL),
        )
    except (OSError, ValueError, KeyError, TypeError):
        return None


def _try_flock(path: Path) -> int | None:
    """Open the lock file and take the lock without waiting; the descriptor, or None when it is held.

    A holder removes the file when it lets go (`hold`), so a lock taken on a file that was removed (or
    replaced) between our open and our flock is a lock on nothing: it is dropped and the open is tried again
    on whatever the path names now."""
    path.parent.mkdir(parents=True, exist_ok=True)
    for _ in range(5):
        fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o644)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            os.close(fd)
            return None
        try:
            same = os.fstat(fd).st_ino == os.stat(path).st_ino
        except OSError:
            same = False
        if same:
            return fd
        os.close(fd)
    return None


def current_holder() -> Holder | None:
    """Who holds the lock right now, or None when it is free (a stale file with a dead owner is free)."""
    path = lock_path()
    if not path.exists():
        return None
    fd = _try_flock(path)
    if fd is not None:  # nobody holds it: whatever the file says is history
        os.close(fd)
        return None
    holder = _read_holder(path)
    if holder is not None and not pid_alive(holder.pid):
        return None
    return holder or Holder(0, "another dfs command", datetime.now(UTC), TERMINAL)


def _inherited(path: Path) -> bool:
    """This process was started by the one that holds the lock (it exported its pid and the lock names it)."""
    try:
        parent = int(os.environ.get(INHERIT_ENV, ""))
    except ValueError:
        return False
    holder = _read_holder(path)
    return holder is not None and holder.pid == parent and pid_alive(parent) and current_holder() is not None


@contextmanager
def hold(command: str, *, origin_name: str | None = None):
    """Hold the run lock for `command` while the block runs; raises `LockHeld` when another writer has it.
    A `dfs` started by the holder (see the module docstring) passes through without taking it again."""
    path = lock_path()
    if _inherited(path):
        yield None
        return
    fd = _try_flock(path)
    if fd is None:
        holder = _read_holder(path)
        if holder is not None and not pid_alive(holder.pid):
            # The owner is dead but something still holds the old file: replace it with a fresh one.
            with suppress(OSError):
                path.unlink()
            fd = _try_flock(path)
        if fd is None:
            raise LockHeld(holder)
    previous = os.environ.get(INHERIT_ENV)
    mine = os.fstat(fd).st_ino
    try:
        record = {
            "pid": os.getpid(),
            "command": normalize(command) or command,
            "since": datetime.now(UTC).isoformat(timespec="seconds"),
            "origin": origin_name or origin(),
        }
        os.ftruncate(fd, 0)
        os.write(fd, json.dumps(record).encode())
        os.environ[INHERIT_ENV] = str(os.getpid())
        yield record
    finally:
        if previous is None:
            os.environ.pop(INHERIT_ENV, None)
        else:
            os.environ[INHERIT_ENV] = previous
        # Remove the file while still holding the lock (only if it is still ours), so a finished run, a
        # failed one or one stopped by Ctrl-C leaves no `.run.lock` claiming a pid that is gone.
        with suppress(OSError):
            if os.stat(path).st_ino == mine:
                path.unlink()
        with suppress(OSError):
            fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def child_env(origin_name: str, base: dict[str, str] | None = None) -> dict[str, str]:
    """The environment for a process the server starts: its origin, and no inherited lock (the server never
    holds the lock itself, so the child takes it)."""
    env = dict(os.environ if base is None else base)
    env[ORIGIN_ENV] = origin_name
    env.pop(INHERIT_ENV, None)
    return env
