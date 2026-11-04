"""`dfs week serve` (Plan F+G): the server's locks, the chat against a fake Claude, the run buttons, the drops
and the contest check. Nothing here starts real Claude, a real `dfs` command or leaves 127.0.0.1."""

from __future__ import annotations

import http.client
import json
import os
import queue
import signal
import stat
import sys
import threading
import time
import zipfile
from datetime import datetime
from io import BytesIO
from pathlib import Path

import pytest
from test_entries import (
    LINEUP_1_AT_DK,
    LINEUP_2,
    LINEUP_3,
    LINEUP_4,
    PLAYERS,
    cells,
    write_entries_file,
)

from dfs import run_lock, week_chat, week_drops, week_jobs, week_live, week_page, week_server
from dfs.week_server import make_server

SEASON, WEEK = 2026, 7
EOF = object()


# -- a fake Claude Code --------------------------------------------------------------------------------------


class FakeProcess:
    """A scripted `claude -p` stream. `script(proc)` runs in a thread once the user message arrives; it calls
    `proc.emit(event)` and may `proc.wait_for(predicate)` on what the page wrote back."""

    pid = 424242

    def __init__(self, script):
        self.script = script
        self.out: queue.Queue = queue.Queue()
        self.written: list[dict] = []
        self.signals: list[int] = []
        self.stdin_closed = False
        self.cond = threading.Condition()
        self.exit_code = 0

    def write(self, message):
        with self.cond:
            self.written.append(message)
            self.cond.notify_all()
        if message.get("type") == "user":
            threading.Thread(target=self._run, daemon=True).start()

    def _run(self):
        try:
            self.script(self)
        finally:
            self.out.put(EOF)

    def emit(self, event):
        self.out.put(event)

    def wait_for(self, predicate, timeout=5.0):
        end = time.monotonic() + timeout
        with self.cond:
            while not (hit := next((m for m in self.written if predicate(m)), None)):
                left = end - time.monotonic()
                if left <= 0:
                    raise AssertionError("the page never wrote the expected message")
                self.cond.wait(left)
            return hit

    def events(self):
        while True:
            item = self.out.get()
            if item is EOF:
                return
            yield item

    def close_stdin(self):
        self.stdin_closed = True

    def send_signal(self, sig):
        self.signals.append(sig)
        self.out.put(EOF)  # the process dies

    def wait(self, timeout):
        return self.exit_code

    def stderr_tail(self):
        return ""


class FakeRunner:
    def __init__(self, script):
        self.script = script
        self.started: list[week_chat.StartArgs] = []
        self.processes: list[FakeProcess] = []

    def start(self, args):
        self.started.append(args)
        proc = FakeProcess(self.script)
        self.processes.append(proc)
        return proc


def init(session="sess-1"):
    return {"type": "system", "subtype": "init", "session_id": session}


def delta(text, mid="m1"):
    return [
        {"type": "stream_event", "event": {"type": "message_start", "message": {"id": mid}}},
        {
            "type": "stream_event",
            "event": {"type": "content_block_delta", "delta": {"type": "text_delta", "text": text}},
        },
    ]


def result(session="sess-1", error=False):
    return {
        "type": "result",
        "subtype": "success",
        "is_error": error,
        "session_id": session,
        "total_cost_usd": 0.01,
    }


def say_hello(proc):
    proc.emit(init())
    for ev in delta("Hel"):
        proc.emit(ev)
    proc.emit(delta("lo")[1])
    proc.emit({"type": "assistant", "message": {"id": "m1", "content": [{"type": "text", "text": "Hello"}]}})
    proc.emit(result())


# -- the server under test -----------------------------------------------------------------------------------


class Client:
    def __init__(self, server):
        self.server = server
        self.port = server.ctx.port
        self.token = server.ctx.token

    def request(self, method, path, *, body=None, headers=None, token=True, host=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        sep = "&" if "?" in path else "?"
        full = path + (f"{sep}t={self.token}" if token is True else (f"{sep}t={token}" if token else ""))
        hdrs = {"Host": f"127.0.0.1:{self.port}" if host is None else host, **(headers or {})}
        data = body
        if isinstance(body, dict):
            data = json.dumps(body).encode()
            hdrs.setdefault("Content-Type", "application/json")
        conn.request(method, full, body=data, headers=hdrs)
        resp = conn.getresponse()
        raw = resp.read()
        conn.close()
        return resp.status, raw, dict(resp.getheaders())

    def get(self, path, **kw):
        status, raw, _ = self.request("GET", path, **kw)
        return status, (json.loads(raw) if raw[:1] in (b"{", b"[") else raw.decode())

    def post(self, path, body=None, **kw):
        status, raw, _ = self.request("POST", path, body=body if body is not None else {}, **kw)
        return status, json.loads(raw)

    def sse(self, path, until=lambda ev: False, limit=200):
        """Read an event stream until `until(event)` or it ends."""
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        conn.request("GET", f"{path}&t={self.token}", headers={"Host": f"127.0.0.1:{self.port}"})
        resp = conn.getresponse()
        assert resp.status == 200
        events = []
        for line in resp:
            line = line.decode().strip()
            if line.startswith("data: "):
                events.append(json.loads(line[6:]))
                if until(events[-1]) or len(events) >= limit:
                    break
        conn.close()
        return events


def stub_python(tmp_path, body="echo ran $@"):
    """A stand-in for `python -m dfs.cli`: a script that ignores its arguments."""
    path = tmp_path / "fakepy"
    path.write_text(f"#!/bin/sh\n{body}\n")
    path.chmod(path.stat().st_mode | stat.S_IEXEC)
    return str(path)


@pytest.fixture
def make(tmp_path):
    servers = []

    def build(script=say_hello, *, week=WEEK, read_only=False, python_body="echo ran $@", **kw):
        runner = FakeRunner(script)
        data = tmp_path / "data"
        chat = week_chat.ChatManager(
            runner,
            log=lambda line: server.ctx.log(line),
            cwd=tmp_path,
            store_dir=tmp_path / "chats",
            season=SEASON,
            week=week,
            stop_waits=(0.05, 0.05, 0.05),
        )
        jobs = week_jobs.JobRunner(
            week_is_closed=lambda: server.ctx.read_only,
            python=stub_python(tmp_path, python_body),
            history_path=tmp_path / "jobs.jsonl",
            stop_waits=(0.05, 0.05, 0.05),
            cwd=tmp_path,
        )
        server = make_server(
            season=SEASON,
            week=week,
            runner=runner,
            token="tok-" + "x" * 20,
            read_only=read_only,
            data_dir=data,
            log_path=tmp_path / "serve.log",
            jobs=jobs,
            chat=chat,
            **{"claude_available": lambda: True, **kw},
        )
        threading.Thread(target=lambda: server.serve_forever(poll_interval=0.02), daemon=True).start()
        servers.append(server)
        client = Client(server)
        client.runner, client.chat, client.jobs, client.tmp = runner, chat, jobs, tmp_path
        return client

    yield build
    for s in servers:
        s.shutdown()
        s.server_close()


# -- security ------------------------------------------------------------------------------------------------


def test_the_server_binds_to_loopback_only_and_refuses_any_other_host():
    with pytest.raises(ValueError, match="127.0.0.1 only"):
        make_server(season=SEASON, week=WEEK, host="0.0.0.0")  # noqa: S104 - the point of the test
    server = make_server(season=SEASON, week=WEEK, runner=FakeRunner(say_hello), read_only=True)
    try:
        assert server.server_address[0] == "127.0.0.1"
    finally:
        server.server_close()


def test_a_request_without_the_token_is_refused(make):
    c = make()
    for path in ("/", "/api/status", "/api/commands"):
        assert c.get(path, token=False)[0] == 403
        assert c.get(path, token="wrong-token")[0] == 403
    assert c.post("/api/chat/stop", token=False)[0] == 403
    assert c.get("/api/status")[0] == 200


def test_a_request_with_a_foreign_host_header_is_refused(make):
    """DNS rebinding: the browser sends the attacker's host name."""
    c = make()
    for host in ("evil.example.com", f"evil.example.com:{c.port}", "127.0.0.1:1", ""):
        assert c.get("/api/status", host=host)[0] == 403
    assert c.get("/api/status", host=f"localhost:{c.port}")[0] == 200


def test_a_post_from_another_origin_or_without_json_is_refused(make):
    c = make()
    assert c.post("/api/chat/stop", headers={"Origin": "https://evil.example.com"})[0] == 403
    assert c.post("/api/chat/stop", headers={"Sec-Fetch-Site": "cross-site"})[0] == 403
    status, _, _ = c.request("POST", "/api/check", body=b"format=GPP", headers={"Content-Type": "text/plain"})
    assert status == 415
    # the page's own origin is fine (it gets past the checks to the route's own answer)
    own = {"Origin": f"http://127.0.0.1:{c.port}"}
    assert c.post("/api/chat/stop", headers=own)[0] == 409  # "nothing is running"


def test_a_preflight_is_never_answered(make):
    c = make()
    assert c.request("OPTIONS", "/api/check")[0] == 403


def test_unknown_paths_404_after_the_token_check(make):
    c = make()
    assert c.get("/etc/passwd")[0] == 404 and c.get("/nope", token=False)[0] == 403


def test_the_page_is_never_cached_and_runs_its_script_by_nonce(make):
    c = make()
    status, raw, headers = c.request("GET", "/")
    page = raw.decode()
    assert status == 200
    assert headers["Cache-Control"] == "no-store" and headers["Referrer-Policy"] == "no-referrer"
    csp = headers["Content-Security-Policy"]
    nonce = csp.split("'nonce-")[1].split("'")[0]
    assert f'<script nonce="{nonce}">' in page and "default-src 'none'" in csp
    assert f'name="dfs-token" content="{c.token}"' in page


def test_the_log_has_paths_but_no_token_prompt_or_command_arguments(make, tmp_path):
    c = make()
    c.get("/api/status")
    c.post("/api/chat/send", {"text": "SECRET-PROMPT-TEXT please"})
    time.sleep(0.3)
    log = (tmp_path / "serve.log").read_text()
    assert "GET /api/status 200" in log and "chat turn" in log
    assert c.token not in log and "SECRET-PROMPT-TEXT" not in log


# -- the page ------------------------------------------------------------------------------------------------


def test_the_live_page_adds_ask_buttons_the_chat_and_the_run_card(make):
    c = make()
    _, page = c.get("/")
    assert 'id="chat"' in page and "Claude Code</b> on your Mac, in <code>dfs/</code>" in page
    assert 'id="live-run"' in page and 'id="live-contest"' in page and 'id="check-form"' in page
    assert "<!--live:" not in page  # both markers were replaced
    assert page.count('class="ask"') >= 3  # Today's steps and the tools list
    assert 'data-say="close the week"' in page or 'data-say="sync"' in page


def test_the_static_page_has_no_live_parts():
    """`dfs week page` is unchanged: no chat, no buttons, only inert comments."""
    html = week_page.render(
        week_page.gather(SEASON, WEEK, datetime(2026, 11, 7, 10).astimezone()), registered=set(), tools=[]
    )
    assert "<aside" not in html and 'class="ask"' not in html and "<script" not in html


def test_augment_only_touches_say_phrases():
    page = (
        '<html><head></head><body><code class="say">&quot;late swap&quot;</code>'
        "<code>dfs sync</code></body></html>"
    )
    out = week_live.augment(page, token="t", nonce="n", read_only=False)
    assert out.count('class="ask"') == 1 and 'data-say="late swap"' in out


# -- the chat ------------------------------------------------------------------------------------------------


def test_a_chat_streams_text_and_remembers_its_session(make):
    c = make()
    assert c.post("/api/chat/send", {"text": "hi", "model": "sonnet", "effort": "low"})[0] == 200
    turn = c.chat.turn
    events = c.sse(f"/api/chat/stream?turn={turn.id}&after=-1", until=lambda e: e["k"] == "done")
    assert [e["k"] for e in events] == ["session", "text", "text", "done"]
    assert (
        "".join(e["delta"] for e in events if e["k"] == "text") == "Hello"
    )  # the final message is not repeated
    assert events[0]["id"] == "sess-1" and events[-1]["ok"] is True
    args = c.runner.started[0]
    assert (args.settings.model, args.settings.effort, args.resume) == ("sonnet", "low", None)
    assert "ANTHROPIC_API_KEY" not in args.env and args.env[run_lock.ORIGIN_ENV] == "chat"
    # the chat is listed for this week and can be reopened
    assert c.get("/api/chat/list")[1]["chats"][0]["id"] == "sess-1"
    ts = c.get("/api/chat/transcript?id=sess-1")[1]["events"]
    assert ts[0] == {"k": "user", "text": "hi"} and ts[1] == {"k": "text", "delta": "Hello"}


def test_the_next_turn_of_a_chat_resumes_its_session(make):
    c = make()
    c.post("/api/chat/send", {"text": "one"})
    c.sse(f"/api/chat/stream?turn={c.chat.turn.id}&after=-1", until=lambda e: e["k"] == "done")
    c.post("/api/chat/send", {"text": "two", "chat_id": "sess-1"})
    c.sse(f"/api/chat/stream?turn={c.chat.turn.id}&after=-1", until=lambda e: e["k"] == "done")
    assert [a.resume for a in c.runner.started] == [None, "sess-1"]
    assert len(c.get("/api/chat/list")[1]["chats"]) == 1  # one chat, two turns
    assert [e["text"] for e in c.get("/api/chat/transcript?id=sess-1")[1]["events"] if e["k"] == "user"] == [
        "one",
        "two",
    ]


def test_a_second_send_while_a_turn_runs_is_refused(make):
    gate = threading.Event()

    def slow(proc):
        proc.emit(init())
        gate.wait(5)
        proc.emit(result())

    c = make(slow)
    assert c.post("/api/chat/send", {"text": "one"})[0] == 200
    status, body = c.post("/api/chat/send", {"text": "two"})
    assert status == 409 and "already running" in body["error"]
    assert c.get("/api/status")[1]["chat_busy"] is True
    gate.set()


def test_bad_settings_are_refused_and_bypass_permissions_cannot_be_asked_for(make):
    c = make()
    assert c.post("/api/chat/send", {"text": "x", "model": "gpt-9"})[0] == 400
    assert c.post("/api/chat/send", {"text": "x", "effort": "ludicrous"})[0] == 400
    assert c.post("/api/chat/send", {"text": "   "})[0] == 400
    assert set(week_chat.PERMISSION_MODES) == {"default", "auto"}
    # a mode outside the list never reaches the command line
    bad = week_chat.Settings("opus", "high", False)
    assert "bypassPermissions" not in week_chat.claude_command(week_chat.StartArgs(bad, None, Path("."), {}))


def test_the_claude_command_line_uses_the_login_streams_json_and_the_page_for_approvals():
    args = week_chat.StartArgs(week_chat.Settings("opus", "high", False), "sess-9", Path("."), {})
    cmd = week_chat.claude_command(args)
    assert cmd[:2] == ["claude", "-p"] and "--bare" not in cmd
    assert cmd[cmd.index("--permission-prompt-tool") + 1] == "stdio"
    assert cmd[cmd.index("--input-format") + 1] == cmd[cmd.index("--output-format") + 1] == "stream-json"
    assert "--include-partial-messages" in cmd and cmd[cmd.index("--resume") + 1] == "sess-9"
    assert "--permission-mode" not in cmd
    auto = week_chat.claude_command(
        week_chat.StartArgs(week_chat.Settings("opus", "high", True), None, Path("."), {})
    )
    assert auto[auto.index("--permission-mode") + 1] == "auto"


def test_the_chat_environment_drops_api_keys(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-secret")
    monkeypatch.setenv(run_lock.INHERIT_ENV, "99")
    env = week_chat.chat_env()
    assert "ANTHROPIC_API_KEY" not in env and run_lock.INHERIT_ENV not in env


def _ask_to_run(tool_use_id="tu1"):
    def script(proc):
        proc.emit(init())
        proc.emit(
            {
                "type": "assistant",
                "message": {
                    "id": "m1",
                    "content": [
                        {
                            "type": "tool_use",
                            "id": tool_use_id,
                            "name": "Bash",
                            "input": {"command": "touch x"},
                        }
                    ],
                },
            }
        )
        proc.emit(
            {
                "type": "control_request",
                "request_id": "req-1",
                "request": {"subtype": "can_use_tool", "tool_name": "Bash", "input": {"command": "touch x"}},
            }
        )
        answer = proc.wait_for(lambda m: m.get("type") == "control_response")["response"]["response"]
        proc.emit(
            {
                "type": "user",
                "message": {
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": tool_use_id,
                            "content": "done" if answer["behavior"] == "allow" else "denied",
                            "is_error": answer["behavior"] != "allow",
                        }
                    ]
                },
            }
        )
        proc.emit(result())

    return script


def test_a_tool_that_needs_approval_waits_for_the_page_and_runs_when_approved(make):
    c = make(_ask_to_run())
    c.post("/api/chat/send", {"text": "touch a file"})
    turn = c.chat.turn
    events = c.sse(f"/api/chat/stream?turn={turn.id}&after=-1", until=lambda e: e["k"] == "approval")
    ask = events[-1]
    assert ask["tool"] == "Bash" and "touch x" in ask["input"] and ask["id"] == "req-1"
    assert any(e["k"] == "tool" and e["name"] == "Bash" and e["summary"] == "touch x" for e in events)
    # nothing is answered until Sam clicks: Claude is still waiting
    assert not [m for m in c.runner.processes[0].written if m.get("type") == "control_response"]
    assert c.post("/api/chat/approve", {"turn": turn.id, "id": "req-1", "allow": True})[0] == 200
    rest = c.sse(f"/api/chat/stream?turn={turn.id}&after={ask['seq']}", until=lambda e: e["k"] == "done")
    answer = c.runner.processes[0].written[-1]["response"]
    assert answer["request_id"] == "req-1" and answer["response"] == {
        "behavior": "allow",
        "updatedInput": {"command": "touch x"},
    }
    assert [e["k"] for e in rest] == ["approval_closed", "result", "done"] and rest[0]["allowed"] is True
    assert rest[1]["text"] == "done"
    # answering twice is refused
    assert c.post("/api/chat/approve", {"turn": turn.id, "id": "req-1", "allow": True})[0] == 409


def test_a_denied_tool_is_told_no_and_does_not_run(make):
    c = make(_ask_to_run())
    c.post("/api/chat/send", {"text": "touch a file"})
    turn = c.chat.turn
    c.sse(f"/api/chat/stream?turn={turn.id}&after=-1", until=lambda e: e["k"] == "approval")
    c.post("/api/chat/approve", {"turn": turn.id, "id": "req-1", "allow": False})
    events = c.sse(f"/api/chat/stream?turn={turn.id}&after=-1", until=lambda e: e["k"] == "done")
    answer = c.runner.processes[0].written[-1]["response"]["response"]
    assert answer["behavior"] == "deny" and answer["message"]
    assert [e for e in events if e["k"] == "result"][0]["error"] is True


def test_nothing_is_approved_unless_the_page_says_so(make):
    """No approval arrives -> the turn just waits; it is never auto-approved or auto-denied."""
    c = make(_ask_to_run())
    c.post("/api/chat/send", {"text": "touch a file"})
    time.sleep(0.4)
    proc = c.runner.processes[0]
    assert not [m for m in proc.written if m.get("type") == "control_response"] and not c.chat.turn.done
    assert list(c.chat.turn.pending) == ["req-1"]
    c.chat.stop()  # tidy up: Stop is what finally answers it (with a denial)
    wait_turn_done(c.chat.turn)


def test_other_host_requests_are_refused_not_guessed_at(make):
    def script(proc):
        proc.emit(init())
        proc.emit({"type": "control_request", "request_id": "r9", "request": {"subtype": "set_model"}})
        proc.wait_for(lambda m: m.get("type") == "control_response")
        proc.emit(result())

    c = make(script)
    c.post("/api/chat/send", {"text": "x"})
    c.sse(f"/api/chat/stream?turn={c.chat.turn.id}&after=-1", until=lambda e: e["k"] == "done")
    reply = c.runner.processes[0].written[-1]["response"]
    assert reply["subtype"] == "error" and reply["request_id"] == "r9"


def test_stop_asks_claude_to_interrupt_and_ends_the_turn_cleanly(make):
    def script(proc):
        proc.emit(init())
        for ev in delta("working"):
            proc.emit(ev)
        proc.wait_for(lambda m: m.get("request", {}).get("subtype") == "interrupt")
        proc.emit(result(error=True))

    c = make(script)
    c.post("/api/chat/send", {"text": "long task"})
    turn = c.chat.turn
    c.sse(f"/api/chat/stream?turn={turn.id}&after=-1", until=lambda e: e["k"] == "text")
    assert c.post("/api/chat/stop")[0] == 200
    events = c.sse(f"/api/chat/stream?turn={turn.id}&after=-1", until=lambda e: e["k"] == "done")
    assert events[-1]["stopped"] is True
    wait_turn_done(turn)
    assert c.runner.processes[0].signals == []  # the interrupt was enough: no signal was sent
    assert c.post("/api/chat/stop")[0] == 409  # nothing left to stop


def test_stop_escalates_to_sigint_when_the_interrupt_is_ignored(make):
    def script(proc):
        proc.emit(init())
        time.sleep(30)  # ignores the interrupt; the fake dies when it gets a signal

    c = make(script)
    c.post("/api/chat/send", {"text": "stuck"})
    time.sleep(0.2)
    c.post("/api/chat/stop")
    deadline = time.time() + 5
    while not c.chat.turn.done and time.time() < deadline:
        time.sleep(0.05)
    assert c.chat.turn.done and c.runner.processes[0].signals[0] == signal.SIGINT


def test_stop_denies_what_is_waiting_for_approval(make):
    c = make(_ask_to_run())
    c.post("/api/chat/send", {"text": "touch a file"})
    turn = c.chat.turn
    c.sse(f"/api/chat/stream?turn={turn.id}&after=-1", until=lambda e: e["k"] == "approval")
    c.post("/api/chat/stop")
    deadline = time.time() + 5
    while not turn.done and time.time() < deadline:
        time.sleep(0.05)
    sent = [m for m in c.runner.processes[0].written if m.get("type") == "control_response"]
    assert sent and sent[0]["response"]["response"]["behavior"] == "deny"


def test_a_process_that_dies_without_a_result_is_reported(make):
    c = make(lambda proc: proc.emit(init()))
    c.post("/api/chat/send", {"text": "x"})
    events = c.sse(f"/api/chat/stream?turn={c.chat.turn.id}&after=-1", until=lambda e: e["k"] == "done")
    assert any(e["k"] == "error" and "stopped before it finished" in e["text"] for e in events)


def test_a_claude_that_is_not_installed_is_said_plainly(make):
    c = make(claude_available=lambda: False)
    status, body = c.post("/api/chat/send", {"text": "x"})
    assert status == 503 and "not installed" in body["error"]


def test_the_stream_resumes_from_the_last_event_seen(make):
    c = make()
    c.post("/api/chat/send", {"text": "hi"})
    turn = c.chat.turn
    first = c.sse(f"/api/chat/stream?turn={turn.id}&after=-1", until=lambda e: e["k"] == "text")
    rest = c.sse(
        f"/api/chat/stream?turn={turn.id}&after={first[-1]['seq']}", until=lambda e: e["k"] == "done"
    )
    assert first[-1]["seq"] + 1 == rest[0]["seq"] and rest[-1]["k"] == "done"


def test_main_checkout_of_a_linked_worktree_is_the_shared_repo(tmp_path):
    main = tmp_path / "dfs"
    (main / ".git" / "worktrees" / "wt").mkdir(parents=True)
    wt = tmp_path / "dfs-wt"
    wt.mkdir()
    (wt / ".git").write_text(f"gitdir: {main / '.git' / 'worktrees' / 'wt'}\n")
    assert week_chat.main_checkout(wt) == main
    assert week_chat.main_checkout(main) == main


# -- the run buttons -----------------------------------------------------------------------------------------


def wait_turn_done(turn, timeout=5):
    end = time.time() + timeout
    while not turn.done and time.time() < end:
        time.sleep(0.02)
    assert turn.done


def wait_job(c, job_id, timeout=5):
    job = c.jobs.jobs[job_id]
    end = time.time() + timeout
    while not job.done and time.time() < end:
        time.sleep(0.02)
    assert job.done
    return job


def test_a_button_runs_a_command_and_streams_its_output(make):
    c = make()
    status, started = c.post("/api/jobs/start", {"command": "dfs sync --live", "values": {}})
    assert status == 200 and started["command"] == "dfs sync --live"
    job = wait_job(c, started["job"])
    events = c.sse(f"/api/jobs/stream?job={job.id}&after=0", until=lambda e: "end" in e)
    assert events[0] == {"line": "ran -m dfs.cli sync --live"} and events[-1] == {"end": 0}
    history = c.get("/api/jobs/history")[1]["history"]
    assert history[0]["command"] == "dfs sync --live" and history[0]["rc"] == 0


def test_history_keeps_option_names_not_values(make):
    c = make()
    status, body = c.post(
        "/api/jobs/start", {"command": "dfs entries record", "values": {"csv": "/Users/sam/secret path.csv"}}
    )
    assert status == 200 and body["command"] == "dfs entries record --csv"
    wait_job(c, body["job"])
    assert "secret" not in (c.tmp / "jobs.jsonl").read_text()


def test_a_second_job_is_refused_while_one_runs(make):
    c = make(python_body="exec sleep 30")
    _, first = c.post("/api/jobs/start", {"command": "dfs status"})
    status, body = c.post("/api/jobs/start", {"command": "dfs status"})
    assert status == 409 and "one job at a time" in body["error"]
    assert c.get("/api/status")[1]["job"] == "dfs status"
    assert c.post("/api/jobs/stop", {"job": first["job"]})[0] == 200
    job = wait_job(c, first["job"])
    assert job.returncode == -signal.SIGINT and job.stopping


def test_a_writer_is_refused_while_the_chat_or_a_terminal_holds_the_run_lock(make):
    c = make()
    import fcntl

    path = run_lock.lock_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_RDWR | os.O_CREAT)
    fcntl.flock(fd, fcntl.LOCK_EX)
    os.write(
        fd,
        json.dumps(
            {
                "pid": os.getppid(),
                "command": "dfs lineups suggest",
                "since": "2026-11-07T15:00:00+00:00",
                "origin": "chat",
            }
        ).encode(),
    )
    try:
        status, body = c.post("/api/jobs/start", {"command": "dfs sync"})
        assert (
            status == 409 and "`dfs lineups suggest` is running" in body["error"] and "chat" in body["error"]
        )
        assert c.post("/api/jobs/start", {"command": "dfs status"})[0] == 200  # a reader passes
        lock = c.get("/api/status")[1]["lock"]
        assert lock["command"] == "dfs lineups suggest" and lock["origin"] == "chat"
    finally:
        os.close(fd)
    assert c.get("/api/status")[1]["lock"] is None


@pytest.mark.parametrize(
    "command",
    [
        "dfs week new",
        "dfs week close",
        "dfs lineups clear",
        "dfs pool clear",
        "dfs setup cockpit",
        "dfs sheets cockpit",
    ],
)
def test_commands_that_rewrite_tabs_need_the_command_typed_out(make, command):
    c = make()
    values = {"sheet_url": "https://docs.google.com/spreadsheets/d/abc"} if command == "dfs week new" else {}
    status, body = c.post("/api/jobs/start", {"command": command, "values": values})
    assert status == 400 and "to confirm" in body["error"]
    phrase = week_jobs.confirm_phrase(command)
    assert c.post("/api/jobs/start", {"command": command, "values": values, "confirm": "yes"})[0] == 400
    ok_status, ok = c.post("/api/jobs/start", {"command": command, "values": values, "confirm": phrase})
    assert ok_status == 200
    wait_job(c, ok["job"])


def test_the_typed_confirmation_stands_in_for_the_yes_flag(make):
    c = make()
    _, body = c.post(
        "/api/jobs/start",
        {"command": "dfs week new", "values": {"sheet_url": "https://x/y"}, "confirm": "week new"},
    )
    job = wait_job(c, body["job"])
    assert job.lines == ["ran -m dfs.cli week new https://x/y --yes"]


def test_a_closed_week_runs_nothing_that_writes(make):
    c = make(read_only=True)
    status, body = c.post("/api/jobs/start", {"command": "dfs sync"})
    assert status == 403 and "closed" in body["error"]
    assert c.post("/api/jobs/start", {"command": "dfs status"})[0] == 200  # reading is fine
    assert c.get("/api/commands")[1]["read_only"] is True


def test_a_form_is_built_from_the_commands_own_parameters(make):
    c = make()
    forms = c.get("/api/commands")[1]["forms"]
    new = forms["dfs week new"]
    assert new["writes"] is True and new["confirm"] == "week new"
    sheet = next(f for f in new["fields"] if f["name"] == "sheet_url")
    assert sheet["required"] and "this week's sheet" in sheet["question"]
    assert forms["dfs status"]["writes"] is False and forms["dfs status"]["confirm"] == ""
    menu = c.get("/api/commands")[1]["menu"]
    assert [m["command"] for m in menu][:3] == ["dfs week new", "dfs sync", ""]  # the standard week, in order


def test_arguments_are_checked_against_the_command(make):
    c = make()
    assert c.post("/api/jobs/start", {"command": "dfs nonsense"})[0] == 404
    assert c.post("/api/jobs/start", {"command": "rm -rf /"})[0] == 400
    assert c.post("/api/jobs/start", {"command": "dfs sync", "values": {"bogus": "1"}})[0] == 400
    assert c.post("/api/jobs/start", {"command": "dfs entries record", "values": {"csv": "--evil"}})[0] == 400
    status, body = c.post("/api/jobs/start", {"command": "dfs week new", "confirm": "week new"})
    assert status == 400 and "required" in body["error"]
    assert c.post("/api/jobs/start", {"command": "dfs sync", "values": {"only": "a\nb"}})[0] == 400


# -- the contest check ---------------------------------------------------------------------------------------


def test_the_check_form_returns_a_tier_a_rake_and_a_reason(make):
    c = make()
    status, body = c.post(
        "/api/check",
        {"format": "GPP", "fee": "15", "entrants": "400 / 784", "entry_max": "3", "prize": "10000 2x"},
    )
    assert status == 200 and body["tier"] == "B" and body["rake"] == pytest.approx(1 - 10000 / (784 * 15))
    assert body["reason"].startswith("3-max GPP") and any("overlay possible" in f for f in body["flags"])
    avoid = c.post("/api/check", {"format": "GPP", "fee": "8", "entrants": "2941", "entry_max": "5"})[1]
    assert avoid["tier"] == "Avoid" and "worst bucket" in avoid["reason"]
    assert c.post("/api/check", {"format": "GPP"})[0] == 400


def test_the_check_form_looks_up_an_h2h_opponent(make, tmp_path):
    import pandas as pd

    from dfs import paths

    paths.CONTESTS_DIR.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(
        {
            "opponent": ["coletrain3193", "azabern2"],
            "meetings": [7, 7],
            "wins": [1, 4],
            "losses": [6, 3],
            "ties": [0, 0],
            "net": [-7.4, 0.6],
        }
    ).to_csv(paths.CONTESTS_DIR / "h2h_opponents.csv", index=False)
    c = make()
    regular = c.post("/api/check", {"format": "H2H", "fee": "2", "opponent": "ColeTrain3193"})[1]
    assert regular["tier"] == "Avoid" and regular["opponent_record"] == "1-6 over 7 games"
    fine = c.post("/api/check", {"format": "H2H", "fee": "2", "opponent": "azabern2"})[1]
    assert fine["tier"] == "A" and fine["opponent_record"] == "4-3 over 7 games"
    unknown = c.post("/api/check", {"format": "H2H", "fee": "2", "opponent": "new guy"})[1]
    assert unknown["tier"] == "A" and unknown["opponent_record"] == ""


def test_the_budget_is_saved_for_the_week_and_the_plan_uses_it(make):
    c = make()
    assert "No Weekly Budget yet" in c.get("/")[1]
    assert c.post("/api/budget", {"budget": "$100"})[1] == {"budget": 100.0}
    page = c.get("/")[1]
    assert "Weekly Budget $100" in page and "$60" in page and "$30" in page and "$10" in page
    assert c.post("/api/budget", {"budget": "lots"})[0] == 400
    assert c.post("/api/budget", {"budget": "0"})[0] == 400


# -- file drops ----------------------------------------------------------------------------------------------


def entries_bytes(tmp_path):
    entries = [
        ("9001", "NFL GIANT $25 Double Up [Single Entry]", "700", "$25", LINEUP_1_AT_DK),
        ("9002", "NFL $1 srwildman vs. TBD", "701", "$1", cells(*LINEUP_2)),
        ("9003", "NFL $4K Singleback [3 Entry Max] (1/3)", "703", "$5", cells(*LINEUP_3)),
        ("9004", "NFL $4K Singleback [3 Entry Max] (2/3)", "703", "$5", cells(*LINEUP_4)),
        ("9005", "NFL $5K Screen Pass [Single Entry]", "704", "$8", LINEUP_1_AT_DK),
        ("9006", "NFL Showdown Captain's Challenge (DAL @ NYG)", "705", "$5", []),
    ]
    path = write_entries_file(tmp_path / "DKEntries.csv", entries=entries, players=PLAYERS)
    return Path(path).read_bytes()


def drop(c, name, data, content_type="application/octet-stream"):
    status, raw, _ = c.request(
        "POST", f"/api/drop?name={name}", body=data, headers={"Content-Type": content_type}
    )
    return status, json.loads(raw)


@pytest.fixture
def downloads(tmp_path, monkeypatch):
    folder = tmp_path / "Downloads"
    monkeypatch.setattr(
        week_server,
        "_load_config",
        lambda: (__import__("dfs.config", fromlist=["x"]).ContestPlanConfig(), str(folder)),
    )
    return folder


def test_dropping_the_entries_file_recognises_its_lineups_and_tiers_the_spend(make, tmp_path, downloads):
    c = make()
    c.post("/api/budget", {"budget": "50"})
    status, got = drop(c, "DKEntries.csv", entries_bytes(tmp_path))
    assert status == 200 and got["kind"] == "entries"
    assert got["summary"][0].startswith("entries for 4 lineups, 5 contests")
    assert (downloads / "DKEntries.csv").exists()  # filed where `entries record` looks
    assert got["runs"] == [
        {
            "label": "Record these entries",
            "command": "dfs entries record",
            "values": {"csv": str(downloads / "DKEntries.csv")},
        }
    ]
    tiers = got["tiers"]
    assert tiers["plan"] == {"A": 30.0, "B": 15.0, "C": 5.0}
    assert tiers["spent"]["A"] == 26.0  # the $25 single-entry double up and the $1 H2H
    assert tiers["spent"]["C"] == 5.0  # Showdown
    assert tiers["spent"]["B"] + tiers["spent"]["Avoid"] == 18.0  # the GPPs: the $5 pair and the $8
    assert any("Showdown" in o or "Tier" in o for o in tiers["over"]) or tiers["over"] == []
    assert {c["tier"] for c in tiers["contests"]} <= {"A", "B", "C", "Avoid"}


def test_dropping_a_file_twice_never_overwrites(make, tmp_path, downloads):
    c = make()
    data = entries_bytes(tmp_path)
    drop(c, "DKEntries.csv", data)
    _, second = drop(c, "DKEntries.csv", data)
    assert second["name"] == "DKEntries (2).csv" and len(list(downloads.glob("*.csv"))) == 2


def test_an_entries_file_for_another_week_warns(make, tmp_path, downloads):
    c = make(week=WEEK + 1)
    _, got = drop(c, "DKEntries.csv", entries_bytes(tmp_path))
    assert any("`entries record` will refuse it" in w for w in got["warnings"])


def test_a_history_export_and_a_standings_zip_are_filed_with_the_matching_commands(make, downloads):
    c = make()
    header = (
        "Sport,Game_Type,Entry_Key,Entry,Contest_Key,Contest_Date_EST,Place,Points,Winnings_Non_Ticket,"
        "Winnings_Ticket,Contest_Entries,Entry_Fee,Prize_Pool,Places_Paid\n"
    )
    row = "NFL,Classic,1,NFL $3 srwildman vs. x,9,2026-10-18 13:00:00,1,100.5,$5.40,$0.00,2,$3.00,$5.40,1\n"
    status, got = drop(c, "contest-history.csv", (header + row).encode())
    assert (
        status == 200 and got["kind"] == "history" and got["summary"] == ["contest-history export: 1 entries"]
    )
    assert [r["command"] for r in got["runs"]] == ["dfs week close", "dfs contests update"]
    assert got["runs"][1]["values"]["no_sheet"] is True

    standings = (
        b"Rank,EntryId,EntryName,TimeRemaining,Points,Lineup,,Player,Roster Position,%Drafted,FPTS\n"
        b"1,5,srwildman (1/1),0,150.5,,,,,,\n"
    )
    buf = BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("contest-standings-123456.csv", standings)
    status, got = drop(c, "standings.zip", buf.getvalue())
    assert status == 200 and got["kind"] == "standings" and got["summary"][0] == "standings for 1 contest"
    assert (downloads / "standings.zip").exists() and got["runs"][0]["command"] == "dfs week close"


def test_a_wrong_type_an_oversize_file_and_a_zip_bomb_are_refused(make, tmp_path, downloads, monkeypatch):
    c = make()
    assert drop(c, "notes.txt", b"hello")[0] == 415
    assert drop(c, "run.sh", b"#!/bin/sh\nrm -rf /\n")[0] == 415
    assert drop(c, "..%2F..%2Fevil.csv.exe", b"x")[0] == 415
    assert drop(c, "empty.csv", b"")[0] in (400, 415)
    status, body = drop(c, "mystery.csv", b"a,b,c\n1,2,3\n")
    assert status == 422 and "not one of the three files" in body["error"]
    assert not list(downloads.glob("*")) if downloads.exists() else True  # nothing refused is kept

    monkeypatch.setattr(week_drops, "MAX_CSV_BYTES", 100)
    status, body = drop(c, "big.csv", b"x" * 101)
    assert status == 413 and "too large" in body["error"]

    bomb = BytesIO()
    with zipfile.ZipFile(bomb, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("contest-standings-1.csv", b"0" * 5_000_000)  # compresses ~1000:1
    status, body = drop(c, "bomb.zip", bomb.getvalue())
    assert status == 400 and "compressed" in body["error"]

    assert drop(c, "broken.zip", b"PK\x03\x04 not really a zip")[0] == 400
    assert drop(c, "a.csv", b"x", content_type="text/csv")[0] == 415


def test_the_size_limit_is_checked_before_the_body_is_read(make):
    c = make()
    conn = http.client.HTTPConnection("127.0.0.1", c.port, timeout=5)
    conn.putrequest("POST", f"/api/drop?name=big.csv&t={c.token}", skip_host=True)
    conn.putheader("Host", f"127.0.0.1:{c.port}")
    conn.putheader("Content-Type", "application/octet-stream")
    conn.putheader("Content-Length", str(week_drops.MAX_CSV_BYTES + 1))
    conn.endheaders()  # no body sent at all
    resp = conn.getresponse()
    assert resp.status == 413
    conn.close()


def test_a_name_is_reduced_to_a_file_name_alone():
    assert week_drops.safe_name("../../etc/passwd.csv") == "passwd.csv"
    assert week_drops.safe_name("C:\\Users\\sam\\entries.csv") == "entries.csv"
    assert week_drops.safe_name(".hidden.csv") == "hidden.csv"
    assert week_drops.safe_name("DraftKings Entries (6).csv") == "DraftKings Entries (6).csv"
    assert week_drops.safe_name("a;b|c$d.csv") == "a_b_c_d.csv"


def test_a_python_file_named_csv_is_just_data(make, downloads):
    """Nothing in a file is executed: a script with a .csv name is not an entries file and is refused."""
    c = make()
    status, _ = drop(c, "payload.csv", b"import os; os.system('echo pwned')\n")
    assert status == 422 and "pwned" not in sys.modules
