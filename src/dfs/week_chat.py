"""The Week page's chat: Claude Code, on Sam's Mac, in the main checkout, driven from the browser.

One turn is one `claude -p` process in Claude Code's stream-JSON mode (`--input-format stream-json
--output-format stream-json --verbose --include-partial-messages --permission-prompt-tool stdio`). The first
turn of a chat starts a session; each later turn is `--resume <session id>`, so a chat keeps its context.

- **Sam's own login.** No API key is passed (an `ANTHROPIC_API_KEY` in the environment is dropped so the
  login is what runs), `--bare` is never used (it skips CLAUDE.md), and nothing is stored in the repo.
- **Permissions are never bypassed.** The default mode applies (the repo's allow rules work as in the
  terminal); a call that needs approval arrives as a `can_use_tool` control request and is shown on the page
  as **Approve / Deny** with exactly what will run. Nothing answers it but Sam, except the panel's "auto"
  switch, off by default, which starts the turn in Claude Code's own `auto` mode. `bypassPermissions` is
  refused here.
- **One turn at a time.** A second send while a turn runs is refused. A command the chat runs takes the run
  lock like any terminal command (`run_lock.py`; the process carries `DFS_RUN_ORIGIN=chat`).
- **Stop** asks Claude Code to interrupt (a control request), then SIGINT, then SIGTERM, then SIGKILL, each
  after a wait and always by pid.
- **Chats** are listed per week and can be reopened: `data/week/chats/index.json` plus one transcript file per
  chat (the text, one-line tool chips and results, truncated). The server log never holds prompt text.

`Runner` is the seam the tests fake: nothing in the suite starts real Claude.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import threading
import time
import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from dfs import paths, run_lock

MODELS = ("opus", "sonnet", "haiku")
EFFORTS = ("low", "medium", "high", "xhigh", "max")
DEFAULT_MODEL, DEFAULT_EFFORT = "opus", "high"
PERMISSION_MODES = ("default", "auto")  # never bypassPermissions
RESULT_CHARS = 1500  # a tool result is kept this long in a transcript
KEEP_CHATS = 200

# Waits (seconds) between the steps of Stop.
STOP_WAITS = (4.0, 5.0, 3.0)


class Busy(RuntimeError):
    """A turn is already running."""


class BadRequest(ValueError):
    pass


@dataclass(frozen=True)
class Settings:
    model: str = DEFAULT_MODEL
    effort: str = DEFAULT_EFFORT
    auto: bool = False  # Claude Code's own auto permission mode; Sam turns it on in the panel

    @classmethod
    def parse(cls, raw: dict[str, Any]) -> Settings:
        model = str(raw.get("model") or DEFAULT_MODEL)
        effort = str(raw.get("effort") or DEFAULT_EFFORT)
        if model not in MODELS:
            raise BadRequest(f"model must be one of {', '.join(MODELS)}")
        if effort not in EFFORTS:
            raise BadRequest(f"effort must be one of {', '.join(EFFORTS)}")
        return cls(model, effort, bool(raw.get("auto")))

    @property
    def permission_mode(self) -> str:
        return "auto" if self.auto else "default"


@dataclass(frozen=True)
class StartArgs:
    settings: Settings
    resume: str | None
    cwd: Path
    env: dict[str, str]


class Process(Protocol):
    pid: int

    def write(self, message: dict) -> None: ...
    def events(self) -> Iterator[dict]: ...
    def close_stdin(self) -> None: ...
    def send_signal(self, sig: int) -> None: ...
    def wait(self, timeout: float) -> int | None: ...
    def stderr_tail(self) -> str: ...


class Runner(Protocol):
    def start(self, args: StartArgs) -> Process: ...


# -- the real runner ----------------------------------------------------------------------------------------


def main_checkout(root: Path | None = None) -> Path:
    """The main working tree of this repo: where Claude Code runs. In a linked worktree `.git` is a file that
    names the shared git dir, whose parent is the main checkout."""
    root = root or paths.REPO_ROOT
    git = root / ".git"
    if git.is_file():
        try:
            text = git.read_text().strip()
            gitdir = Path(text.split("gitdir:", 1)[1].strip())
            common = gitdir.parent.parent if gitdir.parent.name == "worktrees" else gitdir
            if (common.parent / ".git").exists():
                return common.parent
        except (OSError, IndexError):
            pass
    return root


def claude_command(args: StartArgs, claude: str = "claude") -> list[str]:
    """The `claude` argv for one turn (see the module docstring)."""
    if args.settings.permission_mode not in PERMISSION_MODES:
        raise BadRequest("permission mode not allowed")
    cmd = [
        claude,
        "-p",
        "--input-format",
        "stream-json",
        "--output-format",
        "stream-json",
        "--verbose",
        "--include-partial-messages",
        "--permission-prompt-tool",
        "stdio",
        "--model",
        args.settings.model,
        "--effort",
        args.settings.effort,
    ]
    if args.settings.auto:
        cmd += ["--permission-mode", "auto"]
    if args.resume:
        cmd += ["--resume", args.resume]
    return cmd


def chat_env(base: dict[str, str] | None = None) -> dict[str, str]:
    """The environment for Claude Code: this process's, marked as the chat for the run lock, with no API key
    (so Sam's login is what runs)."""
    env = run_lock.child_env("chat", base)
    for key in ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"):
        env.pop(key, None)
    return env


class ClaudeProcess:
    def __init__(self, popen: subprocess.Popen):
        self.popen = popen
        self.pid = popen.pid
        self._lock = threading.Lock()
        self._err: list[str] = []
        threading.Thread(target=self._drain_stderr, daemon=True).start()

    def _drain_stderr(self) -> None:
        for line in self.popen.stderr or []:
            self._err.append(line)
            del self._err[:-40]

    def write(self, message: dict) -> None:
        with self._lock:
            if self.popen.stdin and not self.popen.stdin.closed:
                self.popen.stdin.write(json.dumps(message) + "\n")
                self.popen.stdin.flush()

    def events(self) -> Iterator[dict]:
        for line in self.popen.stdout or []:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except ValueError:
                continue

    def close_stdin(self) -> None:
        with self._lock:
            try:
                if self.popen.stdin and not self.popen.stdin.closed:
                    self.popen.stdin.close()
            except OSError:
                pass

    def send_signal(self, sig: int) -> None:
        if self.popen.poll() is None:
            try:
                os.kill(self.pid, sig)  # by pid only
            except ProcessLookupError:
                pass

    def wait(self, timeout: float) -> int | None:
        try:
            return self.popen.wait(timeout)
        except subprocess.TimeoutExpired:
            return None

    def stderr_tail(self) -> str:
        return "".join(self._err)[-600:]


class SubprocessRunner:
    def __init__(self, claude: str = "claude"):
        self.claude = claude

    def start(self, args: StartArgs) -> Process:
        popen = subprocess.Popen(  # noqa: S603 - a fixed argv, no shell
            claude_command(args, self.claude),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            cwd=args.cwd,
            env=args.env,
        )
        return ClaudeProcess(popen)


# -- one turn ------------------------------------------------------------------------------------------------


def tool_summary(name: str, tool_input: dict[str, Any]) -> str:
    """One line for a tool chip: what it will do."""
    if not isinstance(tool_input, dict):
        return ""
    for key in ("command", "file_path", "path", "pattern", "url", "query", "description", "skill"):
        value = tool_input.get(key)
        if isinstance(value, str) and value:
            return " ".join(value.split())[:200]
    return ""


def result_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(str(b.get("text", "")) for b in content if isinstance(b, dict))
    return ""


@dataclass
class Turn:
    id: str
    settings: Settings
    chat_id: str | None = None  # Claude Code's session id, known from the first event
    events: list[dict] = field(default_factory=list)
    done: bool = False
    pending: dict[str, dict] = field(default_factory=dict)  # request id -> the approval asked for
    cond: threading.Condition = field(default_factory=threading.Condition)
    process: Process | None = None
    stopping: bool = False

    def emit(self, event: dict) -> None:
        with self.cond:
            event = {**event, "seq": len(self.events)}
            self.events.append(event)
            self.cond.notify_all()

    def finish(self, last: dict | None = None) -> None:
        """End the turn, adding its last event in the same step so a client that sees it can send at once."""
        with self.cond:
            if last is not None:
                self.events.append({**last, "seq": len(self.events)})
            self.done = True
            self.cond.notify_all()


class ChatManager:
    """Runs at most one turn at a time and keeps the week's chat list."""

    def __init__(
        self,
        runner: Runner,
        *,
        cwd: Path | None = None,
        store_dir: Path | None = None,
        season: int = 0,
        week: int = 0,
        log: Callable[[str], None] = lambda line: None,
        stop_waits: tuple[float, float, float] = STOP_WAITS,
    ):
        self.runner = runner
        self.cwd = cwd or main_checkout()
        self._store_dir = store_dir
        self.season, self.week = season, week
        self.log = log
        self.stop_waits = stop_waits
        self._lock = threading.Lock()
        self.turn: Turn | None = None

    # -- storage
    @property
    def store_dir(self) -> Path:
        return self._store_dir or paths.DATA_DIR / "week" / "chats"

    def _index(self) -> list[dict]:
        try:
            return json.loads((self.store_dir / "index.json").read_text())
        except (OSError, ValueError):
            return []

    def _save_index(self, index: list[dict]) -> None:
        self.store_dir.mkdir(parents=True, exist_ok=True)
        (self.store_dir / "index.json").write_text(json.dumps(index[:KEEP_CHATS], indent=1))

    def list_chats(self) -> list[dict]:
        """This week's chats, newest first."""
        mine = [c for c in self._index() if c.get("season") == self.season and c.get("week") == self.week]
        return sorted(mine, key=lambda c: c.get("updated", ""), reverse=True)

    def transcript(self, chat_id: str) -> list[dict] | None:
        if not chat_id.replace("-", "").isalnum():
            return None
        try:
            return json.loads((self.store_dir / f"{chat_id}.json").read_text())
        except (OSError, ValueError):
            return None

    def _remember(self, turn: Turn, user_text: str, first: bool, last: dict | None = None) -> None:
        """Append this turn to its chat's transcript and index entry. Never raises."""
        try:
            chat_id = turn.chat_id
            if not chat_id:
                return
            lines = self.transcript(chat_id) or []
            lines.append({"k": "user", "text": user_text})
            lines.extend(_compact([*turn.events, *([{**last, "seq": 0}] if last else [])]))
            self.store_dir.mkdir(parents=True, exist_ok=True)
            (self.store_dir / f"{chat_id}.json").write_text(json.dumps(lines))
            now = datetime.now(UTC).isoformat(timespec="seconds")
            index = [c for c in self._index() if c.get("id") != chat_id]
            old = next((c for c in self._index() if c.get("id") == chat_id), {})
            index.insert(
                0,
                {
                    "id": chat_id,
                    "title": old.get("title") or " ".join(user_text.split())[:60],
                    "season": self.season,
                    "week": self.week,
                    "model": turn.settings.model,
                    "effort": turn.settings.effort,
                    "started": old.get("started") or now,
                    "updated": now,
                },
            )
            self._save_index(index)
        except Exception as exc:  # noqa: BLE001 - history is a convenience
            self.log(f"chat history not saved: {type(exc).__name__}")

    # -- running a turn
    @property
    def busy(self) -> bool:
        turn = self.turn
        return turn is not None and not turn.done

    def send(self, text: str, settings: Settings, chat_id: str | None = None) -> Turn:
        text = text.strip()
        if not text:
            raise BadRequest("nothing to send")
        with self._lock:
            if self.busy:
                raise Busy("a chat turn is already running; wait for it or press Stop")
            turn = Turn(id=uuid.uuid4().hex[:12], settings=settings, chat_id=chat_id)
            self.turn = turn
        args = StartArgs(settings, chat_id, self.cwd, chat_env())
        try:
            turn.process = self.runner.start(args)
        except Exception as exc:  # noqa: BLE001
            turn.emit({"k": "error", "text": f"Could not start Claude Code: {exc}"})
            turn.finish()
            raise
        self.log(
            f"chat turn {turn.id} started: model={settings.model} effort={settings.effort} "
            f"mode={settings.permission_mode} resume={'yes' if chat_id else 'no'} chars={len(text)}"
        )
        threading.Thread(target=self._drive, args=(turn, text, chat_id is None), daemon=True).start()
        return turn

    def _drive(self, turn: Turn, text: str, first: bool) -> None:
        proc = turn.process
        assert proc is not None
        translator = Translator(turn)
        try:
            proc.write({"type": "user", "message": {"role": "user", "content": text}})
            for event in proc.events():
                translator.handle(event, proc)
                if translator.finished:
                    break
        except Exception as exc:  # noqa: BLE001
            turn.emit({"k": "error", "text": f"The chat stream failed: {type(exc).__name__}"})
        finally:
            proc.close_stdin()
            code = proc.wait(5.0)
            if not translator.finished and not turn.stopping:
                tail = proc.stderr_tail().strip()
                turn.emit(
                    {
                        "k": "error",
                        "text": "Claude Code stopped before it finished"
                        + (f" (exit {code})" if code is not None else "")
                        + (f": {tail}" if tail else "."),
                    }
                )
            for request_id in list(turn.pending):
                turn.pending.pop(request_id, None)
                turn.emit({"k": "approval_closed", "id": request_id})
            last = {"k": "done", "stopped": turn.stopping, **translator.summary}
            self._remember(turn, text, first, last)
            turn.finish(last)
            self.log(f"chat turn {turn.id} ended: stopped={turn.stopping} ok={translator.ok}")

    def stream(self, turn: Turn, after: int = -1, wait: float = 15.0) -> Iterator[dict | None]:
        """Events with seq > `after`, as they arrive; None is a keep-alive tick after `wait` quiet seconds.
        Ends when the turn is done and everything has been sent."""
        index = after + 1
        while True:
            with turn.cond:
                if index >= len(turn.events) and not turn.done:
                    turn.cond.wait(wait)
                batch = turn.events[index:]
                finished = turn.done and index + len(batch) >= len(turn.events)
            if batch:
                index += len(batch)
                yield from batch
            elif not finished:
                yield None
            if finished:
                return

    def approve(self, turn: Turn, request_id: str, allow: bool, message: str = "") -> bool:
        """Answer a pending approval. False when there is none by that id (already answered or closed)."""
        info = turn.pending.pop(request_id, None)
        proc = turn.process
        if info is None or proc is None:
            return False
        if allow:
            body: dict[str, Any] = {"behavior": "allow", "updatedInput": info["input"]}
        else:
            body = {"behavior": "deny", "message": message or "Sam denied this on the Week page."}
        proc.write(
            {
                "type": "control_response",
                "response": {"subtype": "success", "request_id": request_id, "response": body},
            }
        )
        turn.emit({"k": "approval_closed", "id": request_id, "allowed": allow})
        self.log(f"chat approval {'allowed' if allow else 'denied'}: tool={info.get('tool')}")
        return True

    def stop(self) -> bool:
        """End the running turn cleanly: ask Claude Code to interrupt, then SIGINT, SIGTERM, SIGKILL (a step
        only if the process is still alive after the wait before it). Returns False when nothing runs."""
        turn = self.turn
        if turn is None or turn.done or turn.process is None:
            return False
        turn.stopping = True
        proc = turn.process
        for request_id in list(turn.pending):
            self.approve(turn, request_id, False, "Stopped.")
        proc.write(
            {"type": "control_request", "request_id": uuid.uuid4().hex, "request": {"subtype": "interrupt"}}
        )
        threading.Thread(target=self._escalate, args=(turn, proc), daemon=True).start()
        self.log(f"chat turn {turn.id}: stop requested")
        return True

    def _escalate(self, turn: Turn, proc: Process) -> None:
        for wait, sig in zip(self.stop_waits, (signal.SIGINT, signal.SIGTERM, signal.SIGKILL), strict=True):
            deadline = time.monotonic() + wait
            while time.monotonic() < deadline:
                if turn.done:
                    return
                time.sleep(0.05)
            if turn.done:
                return
            proc.send_signal(sig)
            self.log(f"chat turn {turn.id}: sent signal {int(sig)}")


class Translator:
    """Claude Code's stream-json events -> the page's events (`k`: session, text, tool, result, approval)."""

    def __init__(self, turn: Turn):
        self.turn = turn
        self.finished = False
        self.ok = True
        self.summary: dict[str, Any] = {}
        self._streamed: set[str] = set()
        self._message_id = ""

    def handle(self, event: dict, proc: Process) -> None:
        kind = event.get("type")
        if kind == "system" and event.get("subtype") == "init":
            self._session(event.get("session_id"))
        elif kind == "stream_event":
            self._stream(event.get("event") or {})
        elif kind == "assistant":
            self._assistant(event.get("message") or {})
        elif kind == "user":
            self._tool_results((event.get("message") or {}).get("content"))
        elif kind == "control_request":
            self._control(event, proc)
        elif kind == "control_cancel_request":
            request_id = str(event.get("request_id") or "")
            if self.turn.pending.pop(request_id, None) is not None:
                self.turn.emit({"k": "approval_closed", "id": request_id})
        elif kind == "result":
            self._session(event.get("session_id"))
            self.ok = not event.get("is_error")
            self.summary = {
                "ok": self.ok,
                "cost": event.get("total_cost_usd"),
                "turns": event.get("num_turns"),
                "reason": event.get("subtype"),
            }
            if event.get("is_error") and event.get("result"):
                self.turn.emit({"k": "error", "text": str(event["result"])[:600]})
            self.finished = True

    def _session(self, session_id: Any) -> None:
        if session_id and self.turn.chat_id != session_id:
            self.turn.chat_id = str(session_id)
            self.turn.emit({"k": "session", "id": self.turn.chat_id})

    def _stream(self, event: dict) -> None:
        etype = event.get("type")
        if etype == "message_start":
            self._message_id = str((event.get("message") or {}).get("id") or "")
        elif etype == "content_block_delta":
            delta = event.get("delta") or {}
            if delta.get("type") == "text_delta" and delta.get("text"):
                self._streamed.add(self._message_id)
                self.turn.emit({"k": "text", "delta": delta["text"]})
            elif delta.get("type") == "thinking_delta":
                self.turn.emit({"k": "thinking"})

    def _assistant(self, message: dict) -> None:
        message_id = str(message.get("id") or "")
        for block in message.get("content") or []:
            if block.get("type") == "text" and message_id not in self._streamed and block.get("text"):
                self.turn.emit({"k": "text", "delta": block["text"]})
            elif block.get("type") == "tool_use":
                tool_input = block.get("input") or {}
                self.turn.emit(
                    {
                        "k": "tool",
                        "id": block.get("id"),
                        "name": block.get("name"),
                        "summary": tool_summary(str(block.get("name")), tool_input),
                        "input": _clip_json(tool_input),
                    }
                )

    def _tool_results(self, content: Any) -> None:
        if not isinstance(content, list):
            return
        for block in content:
            if isinstance(block, dict) and block.get("type") == "tool_result":
                text = result_text(block.get("content"))
                self.turn.emit(
                    {
                        "k": "result",
                        "id": block.get("tool_use_id"),
                        "error": bool(block.get("is_error")),
                        "text": text[:RESULT_CHARS] + ("..." if len(text) > RESULT_CHARS else ""),
                    }
                )

    def _control(self, event: dict, proc: Process) -> None:
        request = event.get("request") or {}
        request_id = str(event.get("request_id") or "")
        if request.get("subtype") != "can_use_tool":
            # Anything else Claude Code asks of its host is refused rather than guessed at.
            proc.write(
                {
                    "type": "control_response",
                    "response": {
                        "subtype": "error",
                        "request_id": request_id,
                        "error": "the Week page does not handle this request",
                    },
                }
            )
            return
        tool_input = request.get("input") or {}
        self.turn.pending[request_id] = {"tool": request.get("tool_name"), "input": tool_input}
        self.turn.emit(
            {
                "k": "approval",
                "id": request_id,
                "tool": request.get("tool_name"),
                "title": request.get("description") or request.get("title") or "",
                "summary": tool_summary(str(request.get("tool_name")), tool_input),
                "input": _clip_json(tool_input, 4000),
            }
        )


def _clip_json(value: Any, limit: int = 1500) -> str:
    try:
        text = json.dumps(value, indent=1, default=str)
    except (TypeError, ValueError):
        text = str(value)
    return text if len(text) <= limit else text[:limit] + "\n..."


def _compact(events: list[dict]) -> list[dict]:
    """A turn's events as a transcript: consecutive text deltas joined, only what a reopened chat shows."""
    out: list[dict] = []
    for event in events:
        kind = event.get("k")
        if kind == "text":
            if out and out[-1].get("k") == "text":
                out[-1]["delta"] += event["delta"]
            else:
                out.append({"k": "text", "delta": event["delta"]})
        elif kind in ("tool", "result", "error"):
            out.append({k: v for k, v in event.items() if k != "seq"})
        elif kind == "approval_closed" and "allowed" in event:
            out.append({"k": "note", "text": "approved" if event["allowed"] else "denied"})
        elif kind == "done":
            out.append({"k": "done", **{k: v for k, v in event.items() if k not in ("seq", "k")}})
    return out
