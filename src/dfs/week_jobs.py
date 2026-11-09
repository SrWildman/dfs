"""The Week page's run buttons: start a `dfs` command, stream its output, stop it, keep a history.

- **A button is a command.** `forms()` describes every registered command (from the CLI's own registry, so a
  new command gets a button by existing): its options as a form, built from the command's parameters, with the
  required ones asked the way the terminal asks them (`launcher.prompt_spec` / `QUESTIONS`). The menu's
  standard-week entries (`launcher.MENU_SECTIONS`) are the first buttons.
- **A job is one subprocess** (`python -m dfs.cli <args>` with the server's interpreter, so it is the code the
  server runs) with its output streamed line by line. Only one job runs at a time; a second is refused. The
  command itself takes the run lock (`run_lock.py`) as it would in a terminal, so a writer is also refused
  while the chat or a terminal session is running one, and the refusal says what is running.
- **Stop** sends SIGINT to the job's pid, then SIGTERM, then SIGKILL, each after a wait (never a name-based
  kill).
- **Guards.** `week new`, `week close`, `lineups clear`, `pool clear` and every `setup ...` (and the old
  `sheets ...`) rewrite tabs, so the page asks for the command typed out (`confirm_phrase`) and the server
  checks it. Nothing writes in a closed week (`read_only`). Nothing here runs on a timer.
- **History** is `data/week/jobs.jsonl`: time, command (option NAMES only, never values), exit code, how
  long it ran and the job's id; each job's output is kept beside it (`data/week/jobs/<id>.log`, the newest
  `OUTPUT_KEPT`), so a past run in "Recent runs" opens its output.
- **The panel** (`panel()`, Plan O item 1b): the weekly commands as a short set of buttons in the day's order
  (`WEEKLY`, each with the options people use, short labels and its defaults filled in), every other command
  behind a search, and every `setup ...` / `fix-...` one behind a collapsed Advanced toggle (`is_advanced`).
"""

from __future__ import annotations

import functools
import json
import os
import re
import shlex
import signal
import subprocess
import sys
import threading
import time
import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from dfs import commands_doc, launcher, paths, run_lock

OUTPUT_LINES = 3000
STOP_WAITS = (4.0, 5.0, 3.0)
HISTORY_SHOWN = 15
OUTPUT_KEPT = 30  # past runs whose output is kept on disk for "Recent runs"

# Commands that rewrite tabs or reset a week: the page makes Sam type them out.
CONFIRM_COMMANDS = frozenset({"dfs week new", "dfs week close", "dfs lineups clear", "dfs pool clear"})


class Refused(RuntimeError):
    """The job was not started; the message says why."""

    def __init__(self, message: str, status: int = 409):
        super().__init__(message)
        self.status = status


def command_path(command: str) -> str:
    return run_lock.normalize(command)


def needs_confirmation(command: str) -> bool:
    path = command_path(command)
    return path in CONFIRM_COMMANDS or path.startswith("dfs setup ")


def confirm_phrase(command: str) -> str:
    """What Sam types to confirm: the command without `dfs ` (`week close`, `setup cockpit`)."""
    return command_path(command).removeprefix("dfs ")


# -- the forms -----------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Field:
    name: str
    flag: str | None  # the long option, None for a positional argument
    kind: str  # text | flag | choice
    required: bool
    help: str
    default: str | None
    choices: tuple[str, ...]
    many: bool
    question: str


def _fields(path: str, command: Any) -> list[Field]:
    out = []
    for param in command.params:
        if param.name == "help":
            continue
        spec = launcher.prompt_spec(path, param)
        choices = tuple(getattr(getattr(param, "type", None), "choices", None) or ())
        is_flag = bool(getattr(param, "is_flag", False))
        default = getattr(param, "default", None)
        show = getattr(param, "show_default", None)
        if isinstance(show, str):
            default = show
        out.append(
            Field(
                name=param.name,
                flag=spec.flag,
                kind="flag" if is_flag else ("choice" if choices else "text"),
                required=bool(getattr(param, "required", False)),
                help=" ".join(str(getattr(param, "help", "") or "").split())[:400],
                default=None if is_flag or default in (None, False, (), "") else str(default),
                choices=choices,
                many=spec.many,
                question=spec.question,
            )
        )
    return out


@functools.cache
def forms() -> dict[str, dict[str, Any]]:
    """Every registered command: its one-line help, the fields of its form and whether it writes (takes the
    run lock) or needs the command typed out."""
    out = {}
    for path, command in sorted(commands_doc.all_commands().items()):
        out[path] = {
            "command": path,
            "help": commands_doc._first_paragraph(command.help)[:300],
            "label": launcher.MORE_LABELS.get(path, ""),
            "writes": run_lock.takes_lock(path),
            "confirm": confirm_phrase(path) if needs_confirmation(path) else "",
            "fields": [f.__dict__ for f in _fields(path, command)],
        }
    return out


# -- the panel (Plan O item 1b) -----------------------------------------------------------------------------


@dataclass(frozen=True)
class Weekly:
    """One of the weekly buttons: what it is called, the command, the options shown up front (the rest sit
    under "More options") and the values its form starts with."""

    label: str
    command: str
    when: str  # the day, in plain words
    common: tuple[str, ...]
    defaults: tuple[tuple[str, Any], ...] = ()


# Sam's standard week, in the day's order (Plan O item 1b). The options up front are the ones his weekly
# shortcuts use (CLAUDE.md): `sync --live` on Sunday, the late swap's ESPN injury refresh and `--field auto`,
# `week close --research`, an entries file by hand.
WEEKLY: tuple[Weekly, ...] = (
    Weekly("Sync", "dfs sync", "Saturday, and after any news", ("live", "props")),
    Weekly("Contest plan", "dfs contests plan", "before entering", ("budget",)),
    Weekly("Build me lineups", "dfs lineups suggest", "after the sync", ("iterate", "round_", "cash", "gpp")),
    Weekly(
        "Record entries", "dfs entries record", "at lock, after entering", ("csv", "from_sheet", "dry_run")
    ),
    Weekly(
        "Late swap",
        "dfs lineups late-swap",
        "Sunday, before the late games",
        ("refresh_injuries", "field", "state"),
        (("refresh_injuries", True),),
    ),
    Weekly(
        "Inactives check",
        "dfs plays",
        "Sunday, after inactives",
        ("inactives", "position"),
        (("inactives", True),),
    ),
    Weekly("Season sync", "dfs season sync", "any day", ("league", "no_sheet")),
    Weekly("Close the week", "dfs week close", "Monday", ("csv", "research"), (("research", True),)),
)

# A short label per option name, where the command's own help is a sentence (the full help is the tooltip).
SHORT_LABELS = {
    "live": "Final sync: fast-moving sources only",
    "props": "Pull player props (Odds API credits)",
    "only": "Only these sources",
    "no_upload": "Store locally, skip the sheet",
    "week": "Week",
    "season": "Season",
    "sheet_id": "Another sheet (its ID)",
    "fixed_lines": "Score against the old fixed lines",
    "budget": "Weekly budget ($)",
    "no_sheet": "Read or write no sheet",
    "cash": "Cash builds",
    "gpp": "GPP builds",
    "no_write": "Print only, leave Suggested alone",
    "seconds": "Search seconds",
    "gpp_entries": "GPP size (entries)",
    "double_up_entries": "Double-up size (entries)",
    "rank": "Rank GPP builds by",
    "iterate": "Rerun from my Suggested edits",
    "round_": "Bring back round",
    "portfolio_": "Also plan the GPP entries (shadow)",
    "portfolio_objective": "Portfolio objective",
    "portfolio_wide": "Portfolio: noisy reruns",
    "entries_csv": "Entries file",
    "csv": "File (left empty: the newest in Downloads)",
    "from_sheet": "Use the Lineups tab",
    "dry_run": "Dry run: write nothing",
    "top": "How many to show",
    "metric": "Rank by projection",
    "all_players": "Whole pool, not only mine",
    "goal": "Rank by",
    "field": "Live standings export (auto = Downloads)",
    "state": "Game state",
    "refresh_injuries": "Refresh ESPN's game-day injuries",
    "position": "Positions (QB,RB,...)",
    "per": "Calls per label",
    "no_save": "Print only, save nothing",
    "inactives": "Run the final sync first",
    "league": "League (blank: all)",
    "all_leagues": "Every league",
    "open_search": "Leave out the buy-list ideas",
    "research": "Then run Monday's studies",
    "yes": "Skip the confirmation",
    "output": "File to write",
    "write": "Also write the upload file",
    "again": "Only lineups changed by a late swap",
    "refit": "Refit the field model first",
    "retro": "Rerun the retro",
    "sheet_url": "This week's sheet (link or ID)",
}


def is_advanced(path: str) -> bool:
    """Sheet setup and one-off fixes: behind the panel's Advanced toggle (Plan O item 1b)."""
    words = path.split()
    return path.startswith(("dfs setup ", "dfs sheets ")) or any(w.startswith("fix-") for w in words[1:])


def short_label(field_spec: dict) -> str:
    """The option's short label: `SHORT_LABELS`, else the first clause of its question."""
    if field_spec["name"] in SHORT_LABELS:
        return SHORT_LABELS[field_spec["name"]]
    text = str(field_spec.get("question") or field_spec["name"])
    for stop in (" (", ": ", ". ", "; ", " -- "):
        text = text.split(stop)[0]
    return text[:48]


def panel(known: dict[str, dict[str, Any]] | None = None) -> dict[str, Any]:
    """The run panel: the weekly buttons (each with its options up front and its defaults), then every other
    command, then the advanced ones. A weekly button whose command is not registered is left out (a renamed
    command fails `tests/test_week_jobs`'s check, not the page)."""
    known = known or forms()
    weekly = []
    for w in WEEKLY:
        spec = known.get(w.command)
        if spec is None:
            continue
        names = {f["name"] for f in spec["fields"]}
        weekly.append(
            {
                "label": w.label,
                "command": w.command,
                "when": w.when,
                "common": [n for n in w.common if n in names],
                "defaults": {k: v for k, v in w.defaults if k in names},
                "tip": spec["help"],
            }
        )
    mine = {w.command for w in WEEKLY}
    rest = [p for p in known if p not in mine]
    return {
        "weekly": weekly,
        "other": [p for p in rest if not is_advanced(p)],
        "advanced": [p for p in rest if is_advanced(p)],
        "labels": {p: {f["name"]: short_label(f) for f in spec["fields"]} for p, spec in known.items()},
    }


def menu_buttons() -> list[dict[str, str]]:
    """The standard week's steps, in order (`launcher.MENU_SECTIONS`); a step with no command happens in the
    sheet."""
    out = []
    for section, items in launcher.MENU_SECTIONS:
        for item in items:
            out.append({"section": section, "label": item.label, "command": item.command or ""})
    return out


def build_argv(
    command: str, values: dict[str, Any], known: dict[str, dict[str, Any]] | None = None
) -> list[str]:
    """The argv for `command` (`dfs sync --live`) plus the form's `values`, checked against the command's own
    parameters: an unknown name, a value that looks like an option or a missing required one is refused."""
    known = known or forms()
    words = shlex.split(command)
    if not words or words[0] != "dfs":
        raise Refused("not a dfs command", 400)
    path = command_path(command)
    spec = known.get(path)
    if spec is None:
        raise Refused(f"`{path}` is not a dfs command", 404)
    argv = words[1:]  # the command's words and the flags a menu entry carries (`--live`)
    fields = {f["name"]: f for f in spec["fields"]}
    for name in values:
        if name not in fields:
            raise Refused(f"`{path}` has no option {name!r}", 400)
    positional: list[str] = []
    for name, f in fields.items():
        raw = values.get(name)
        if f["kind"] == "flag":
            if raw in (True, "true", "on", "1", 1) and f["flag"] and f["flag"] not in argv:
                argv.append(f["flag"])
            continue
        text = "" if raw is None else str(raw).strip()
        if not text:
            if f["required"] and not (f["flag"] and f["flag"] in argv):
                raise Refused(f"{f['question']}: this is required", 400)
            continue
        pieces = [p.strip() for p in text.replace(";", ",").split(",") if p.strip()] if f["many"] else [text]
        for piece in pieces:
            if piece.startswith("-"):
                raise Refused(f"{name}: a value cannot start with '-'", 400)
            if "\x00" in piece or "\n" in piece:
                raise Refused(f"{name}: not a valid value", 400)
        if f["kind"] == "choice" and text not in f["choices"]:
            raise Refused(f"{name} must be one of {', '.join(f['choices'])}", 400)
        if f["flag"]:
            argv += [f["flag"], pieces[0]]
        else:
            positional += pieces
    return [*argv, *positional]


# -- jobs ----------------------------------------------------------------------------------------------------


@dataclass
class Job:
    id: str
    command: str  # the display text: `dfs sync --live`
    argv: list[str]
    started: datetime
    lines: list[str] = field(default_factory=list)
    returncode: int | None = None
    done: bool = False
    stopping: bool = False
    pid: int = 0
    cond: threading.Condition = field(default_factory=threading.Condition)

    def add(self, line: str) -> None:
        with self.cond:
            self.lines.append(line)
            del self.lines[:-OUTPUT_LINES]
            self.cond.notify_all()


class JobRunner:
    def __init__(
        self,
        *,
        week_is_closed: Callable[[], bool] = lambda: False,
        log: Callable[[str], None] = lambda line: None,
        on_done: Callable[[Job], None] = lambda job: None,
        cwd: Path | None = None,
        python: str | None = None,
        history_path: Path | None = None,
        stop_waits: tuple[float, float, float] = STOP_WAITS,
    ):
        self.week_is_closed = week_is_closed
        self.log = log
        self.on_done = on_done
        self.cwd = cwd or paths.REPO_ROOT
        self.python = python or sys.executable
        self._history_path = history_path
        self.stop_waits = stop_waits
        self._lock = threading.Lock()
        self.jobs: dict[str, Job] = {}
        self.current: Job | None = None

    @property
    def history_path(self) -> Path:
        return self._history_path or paths.DATA_DIR / "week" / "jobs.jsonl"

    # -- starting
    def start(self, command: str, values: dict[str, Any] | None = None, confirm: str = "") -> Job:
        values = values or {}
        known = forms()
        argv = build_argv(command, values, known)
        path = command_path(command)
        if run_lock.takes_lock(path) and self.week_is_closed():
            raise Refused("This week is closed: nothing that writes can run on it.", 403)
        if needs_confirmation(path):
            if confirm.strip() != confirm_phrase(path):
                raise Refused(f"Type `{confirm_phrase(path)}` to confirm: this one rewrites tabs.", 400)
            if any(f["name"] == "yes" for f in known[path]["fields"]) and "--yes" not in argv:
                argv.append("--yes")  # the typed phrase is the confirmation; there is no terminal to ask
        if run_lock.takes_lock(path):
            holder = run_lock.current_holder()
            if holder is not None:
                raise Refused(f"Not started: {holder.describe()}. Wait for it, or stop it.")
        with self._lock:
            if self.current is not None and not self.current.done:
                raise Refused(f"`{self.current.command}` is already running here; one job at a time.")
            job = Job(
                id=uuid.uuid4().hex[:10],
                command=" ".join(["dfs", *_names_only(argv, len(path.split()) - 1)]),
                argv=argv,
                started=datetime.now(UTC),
            )
            self.jobs[job.id] = job
            self.current = job
        try:
            popen = subprocess.Popen(  # noqa: S603 - the server's interpreter and a checked argv, no shell
                [self.python, "-m", "dfs.cli", *argv],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                cwd=self.cwd,
                env=run_lock.child_env("button"),
            )
        except OSError as exc:
            job.add(f"Could not start: {exc}")
            self._finish(job, 127)
            raise Refused(f"Could not start: {exc}", 500) from exc
        job.pid = popen.pid
        self.log(f"job {job.id} started: {job.command}")
        threading.Thread(target=self._pump, args=(job, popen), daemon=True).start()
        return job

    def _pump(self, job: Job, popen: subprocess.Popen) -> None:
        try:
            for line in popen.stdout or []:
                job.add(line.rstrip("\n"))
            code = popen.wait()
        except Exception as exc:  # noqa: BLE001
            job.add(f"(output stopped: {type(exc).__name__})")
            code = popen.poll() if popen.poll() is not None else 1
        self._finish(job, code)

    def _finish(self, job: Job, code: int) -> None:
        job.returncode = code
        # History first, then `done`: whoever sees the job end (the page reloading "Recent runs", a test) must
        # find its line already written. The other order left a window where a finished job was not listed.
        self._remember(job)
        with job.cond:
            job.done = True
            job.cond.notify_all()
        self.log(f"job {job.id} ended: rc={code} stopped={job.stopping}")
        try:
            self.on_done(job)
        except Exception as exc:  # noqa: BLE001 - a callback never fails a job
            self.log(f"job callback failed: {type(exc).__name__}")

    @property
    def output_dir(self) -> Path:
        return self.history_path.parent / "jobs"

    def _remember(self, job: Job) -> None:
        seconds = round((datetime.now(UTC) - job.started).total_seconds(), 1)
        try:
            self.history_path.parent.mkdir(parents=True, exist_ok=True)
            with self.history_path.open("a") as fh:
                fh.write(
                    json.dumps(
                        {
                            "time": job.started.isoformat(timespec="seconds"),
                            "command": job.command,
                            "rc": job.returncode,
                            "stopped": job.stopping,
                            "seconds": seconds,
                            "id": job.id,
                        }
                    )
                    + "\n"
                )
        except OSError:
            pass
        try:  # the output, for "Recent runs" (the newest OUTPUT_KEPT only)
            self.output_dir.mkdir(parents=True, exist_ok=True)
            (self.output_dir / f"{job.id}.log").write_text("\n".join([f"$ {job.command}", *job.lines]) + "\n")
            logs = sorted(self.output_dir.glob("*.log"), key=lambda q: q.stat().st_mtime)
            for old in logs[:-OUTPUT_KEPT]:
                old.unlink(missing_ok=True)
        except OSError:
            pass

    def output(self, job_id: str) -> list[str] | None:
        """A run's output: the running or recent job's lines, else its saved log; None when there is none."""
        if not re.fullmatch(r"[0-9a-f]{10}", job_id or ""):
            return None
        job = self.jobs.get(job_id)
        if job is not None:
            with job.cond:
                return [f"$ {job.command}", *job.lines]
        try:
            return (self.output_dir / f"{job_id}.log").read_text().splitlines()
        except OSError:
            return None

    def history(self, limit: int = HISTORY_SHOWN) -> list[dict]:
        try:
            rows = [json.loads(line) for line in self.history_path.read_text().splitlines() if line.strip()]
        except (OSError, ValueError):
            rows = []
        return rows[-limit:][::-1]

    # -- watching and stopping
    def stream(self, job: Job, after: int = 0, wait: float = 15.0) -> Iterator[tuple[str, Any] | None]:
        """`("line", text)` events from line `after` on, then `("end", returncode)`; None is a quiet tick."""
        index = after
        while True:
            with job.cond:
                if index >= len(job.lines) and not job.done:
                    job.cond.wait(wait)
                batch = job.lines[index:]
                finished = job.done and index + len(batch) >= len(job.lines)
            for line in batch:
                yield ("line", line)
            index += len(batch)
            if finished:
                yield ("end", job.returncode)
                return
            if not batch:
                yield None

    def stop(self, job: Job) -> bool:
        if job.done or not job.pid:
            return False
        job.stopping = True
        threading.Thread(target=self._escalate, args=(job,), daemon=True).start()
        return True

    def _escalate(self, job: Job) -> None:
        for wait, sig in zip(self.stop_waits, (signal.SIGINT, signal.SIGTERM, signal.SIGKILL), strict=True):
            if job.done:
                return
            try:
                os.kill(job.pid, sig)  # by pid only
            except ProcessLookupError:
                return
            deadline = time.monotonic() + wait
            while time.monotonic() < deadline:
                if job.done:
                    return
                time.sleep(0.05)


def _names_only(argv: list[str], path_words: int) -> list[str]:
    """The command's words and the NAMES of its options, never their values (a sheet id, a path, a URL)."""
    return argv[:path_words] + [t.split("=", 1)[0] for t in argv[path_words:] if t.startswith("--")]
