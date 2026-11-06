"""`dfs week serve`: the Week page as a small local web app, with a Claude Code chat, run buttons, file drops
and the contest check beside the decisions.

Deliberately minimal and locked down:

- **Local only.** It binds to `127.0.0.1` (never `0.0.0.0`; `make_server` refuses any other host) on a free
  port.
- **A random token per run.** `dfs week serve` opens the browser at `/?t=<token>`; every request, GET or POST,
  must carry that token (`t=`), and a `Host` that is `127.0.0.1:<port>` or `localhost:<port>`. A POST must
  also come from this page (an `Origin`, when sent, that is this server) and say what it carries
  (`application/json` or `application/octet-stream`, which a web page on another site cannot send without a
  preflight this server never answers). Together these stop another site in the browser from driving it
  (CSRF, DNS rebinding). Responses are never cached, send a strict Content-Security-Policy (the page's one
  script runs by nonce, nothing loads from outside), and the page sends no referrer.
- **Standard library only**, no build step, no vendored scripts: the page's JavaScript is in `week_live.py`.
- **No DraftKings access; the sheet writes are the page's own three.** The server reads the files the CLI
  already saved, plus the live sheet's Pool column and Suggested's rules box on every render. The page writes
  only a player's Pool cell, the rules box's Always use / Never use / Max exposure lines for a decision, and
  the
  suggested starting pool (`week_actions`, through `week_writes.SheetEdits`: re-read first and refused on a
  change, one batch under the run lock, read back, logged to `data/week/edits.log`; never on a closed week).
  Anything else that writes is a `dfs` command Sam starts (a button, or the chat with his approval), and those
  take the run lock too.
- **The log** (`data/week/serve.log`) holds the time, method, path without its query (so no token) and status
  of each request, and chat turns' settings and counts: never a prompt, a reply, a file's contents or a
  command's arguments.

- **One server at a time** (`data/week/serve.pid`, Plan K item 10): a running server leaves its pid, port,
  week and link there (owner-only file mode). `dfs week serve` with a live one opens that one's link instead
  of starting a second; `dfs week serve --stop` sends SIGTERM to that pid only (never a broad kill), and the
  server removes the file as it stops. A file whose pid is gone, or is not a `dfs` process, is stale and
  replaced.

`dfs week page` is unchanged and keeps working as the static file.
"""

from __future__ import annotations

import hmac
import json
import os
import secrets
import shutil
import signal
import subprocess
import threading
import webbrowser
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

from dfs import (
    commands_doc,
    contest_tiers,
    nfl_calendar,
    paths,
    run_lock,
    week_actions,
    week_chat,
    week_drops,
    week_inputs,
    week_jobs,
    week_live,
    week_page,
    week_tools,
    week_writes,
)

LOOPBACK = "127.0.0.1"
MAX_JSON_BYTES = 1_000_000
KEEPALIVE_SECONDS = 15.0


class HttpError(Exception):
    def __init__(self, status: int, message: str, extra: dict[str, Any] | None = None):
        super().__init__(message)
        self.status = status
        self.message = message
        self.extra = extra or {}


@dataclass
class Context:
    """Everything a request needs; `make_server` builds one, tests build their own with fakes."""

    season: int
    week: int
    token: str
    chat: week_chat.ChatManager
    jobs: week_jobs.JobRunner
    read_only: bool = False
    edits: Any = None  # week_writes.SheetEdits for the week's sheet; None: no sheet (the page cannot write)
    port: int = 0
    data_dir: Path | None = None
    log_path: Path | None = None
    now: Callable[[], datetime] = lambda: datetime.now().astimezone()
    claude_available: Callable[[], bool] = lambda: shutil.which("claude") is not None
    _log_lock: threading.Lock = field(default_factory=threading.Lock)

    def log(self, line: str) -> None:
        try:
            target = self.log_path or paths.DATA_DIR / "week" / "serve.log"
            target.parent.mkdir(parents=True, exist_ok=True)
            with self._log_lock, target.open("a") as fh:
                fh.write(f"{datetime.now().astimezone().isoformat(timespec='seconds')} {line}\n")
        except OSError:
            pass

    @property
    def allowed_hosts(self) -> set[str]:
        return {f"{LOOPBACK}:{self.port}", f"localhost:{self.port}"}

    @property
    def allowed_origins(self) -> set[str]:
        return {f"http://{h}" for h in self.allowed_hosts}


# -- the app's own work (no HTTP in here) --------------------------------------------------------------------


def render_page(ctx: Context, nonce: str) -> str:
    inp = week_inputs.gather(ctx.season, ctx.week, ctx.now(), ctx.data_dir)
    registered = set(commands_doc.all_commands())
    sheet = ctx.edits.read() if ctx.edits is not None else None
    page = week_page.render(
        inp,
        registered=registered,
        tools=week_tools.build(inp.runs),
        live=not ctx.read_only,
        nonce=nonce,
        sheet=sheet,
        answers=week_actions.answers(inp),
    )
    return week_live.augment(
        page, token=ctx.token, nonce=nonce, read_only=ctx.read_only, budget=week_page.weekly_budget(inp)
    )


def page_stamp(ctx: Context) -> float:
    """When the static page file was last rebuilt: every command that changes what the page shows rebuilds it
    (`week_page.after_command`), so a change here tells the open page to reload its sections."""
    try:
        return week_page.page_path(ctx.season, ctx.week, ctx.data_dir).stat().st_mtime
    except OSError:
        return 0.0


def status(ctx: Context) -> dict[str, Any]:
    holder = run_lock.current_holder()
    job = ctx.jobs.current
    return {
        "page_stamp": page_stamp(ctx),
        "lock": {"text": holder.describe() + ".", "command": holder.command, "origin": holder.origin}
        if holder
        else None,
        "job": job.command if job is not None and not job.done else None,
        "chat_busy": ctx.chat.busy,
        "claude": ctx.claude_available(),
        "week": ctx.week,
        "read_only": ctx.read_only,
    }


def commands(ctx: Context) -> dict[str, Any]:
    return {"menu": week_jobs.menu_buttons(), "forms": week_jobs.forms(), "read_only": ctx.read_only}


def _load_config():
    from dfs.config import ContestPlanConfig, load_config

    try:
        cfg = load_config()
        return cfg.contest_plan, cfg.contests.downloads_dir
    except Exception:  # noqa: BLE001 - the page works on a machine without credentials
        return ContestPlanConfig(), None


def check_contest(ctx: Context, form: dict[str, Any]) -> dict[str, Any]:
    """The check form: a verdict for one contest from its five fields, and the H2H opponent's record."""
    try:
        contest = contest_tiers.contest_from_form(form)
    except contest_tiers.FormError as exc:
        raise HttpError(400, str(exc)) from exc
    opponents = week_drops.load_opponents()
    record = ""
    avoid: list[str] = []
    if opponents is not None:
        from dfs import contest_plan

        avoid = contest_plan.avoid_names(opponents)
        if contest.opponent:
            row = opponents[opponents["opponent"].astype(str).str.casefold() == contest.opponent.casefold()]
            if len(row):
                r = row.iloc[0]
                record = f"{int(r['wins'])}-{int(r['losses'])} over {int(r['meetings'])} games"
    verdict = contest_tiers.classify(contest, avoid_opponents=avoid)
    return contest_tiers.verdict_payload(contest, verdict, opponent_record=record)


def save_budget(ctx: Context, raw: Any) -> dict[str, Any]:
    try:
        value = contest_tiers.parse_amount(str(raw), "budget")
    except contest_tiers.FormError as exc:
        raise HttpError(400, str(exc)) from exc
    if not 0 < value < 100_000:
        raise HttpError(400, "budget: type the week's budget in dollars")
    if not week_inputs.save_input(
        "budget", {"budget": value}, season=ctx.season, week=ctx.week, data_dir=ctx.data_dir
    ):
        raise HttpError(500, "could not save the budget")
    return {"budget": value}


def receive_drop(ctx: Context, name: str, data: bytes) -> dict[str, Any]:
    cfg_plan, downloads = _load_config()
    inp = week_inputs.gather(ctx.season, ctx.week, ctx.now(), ctx.data_dir)
    try:
        dropped = week_drops.receive(
            name,
            data,
            downloads=week_drops.default_downloads(downloads),
            season=ctx.season,
            week=ctx.week,
            budget=week_page.weekly_budget(inp),
            cfg_plan=cfg_plan,
            typed=week_drops.load_typed(),
            opponents=week_drops.load_opponents(),
        )
    except week_drops.Rejected as exc:
        ctx.log(f"drop refused: {exc.status}")
        raise HttpError(exc.status, str(exc)) from exc
    ctx.log(f"drop filed: kind={dropped.kind}")
    return dropped.as_json()


def sheet_write(ctx: Context, action: Callable[[week_writes.SheetEdits, week_inputs.WeekInputs], Any]) -> Any:
    """Run one of the page's writes, its refusals as HTTP errors (a conflict carries what the sheet holds)."""
    if ctx.read_only:
        raise HttpError(403, "This week is closed: the page writes nothing to its sheet.")
    if ctx.edits is None:
        raise HttpError(503, "No sheet is connected (config.toml), so the page cannot write.")
    inp = week_inputs.gather(ctx.season, ctx.week, ctx.now(), ctx.data_dir)
    try:
        return action(ctx.edits, inp)
    except week_writes.Conflict as exc:
        raise HttpError(409, str(exc), {"current": exc.current}) from exc
    except week_writes.WriteError as exc:
        raise HttpError(exc.status, str(exc)) from exc
    except run_lock.LockHeld as exc:
        raise HttpError(409, f"{exc}") from exc


def pool_message(values: dict[str, str], inp: week_inputs.WeekInputs) -> str:
    names = {str(r.get("Id", "")): str(r.get("Name", "")) for r in inp.edge or []}
    return "; ".join(f"{names.get(i, i)}: {v or 'out of the pool'}" for i, v in values.items())


# -- HTTP ----------------------------------------------------------------------------------------------------


class Handler(BaseHTTPRequestHandler):
    server_version = "dfs-week"
    sys_version = ""
    protocol_version = "HTTP/1.0"  # one request per connection; streams end when the connection closes

    @property
    def ctx(self) -> Context:
        return self.server.ctx  # type: ignore[attr-defined]

    # -- plumbing
    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 - the base class's name
        pass  # requests are logged in `_finish` (no query string, so no token)

    def _finish(self, status: int) -> None:
        path = urlsplit(self.path).path
        self.ctx.log(f"{self.command} {path} {status}")

    def _send(self, status: int, body: bytes, content_type: str, extra: dict[str, str] | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self._security_headers()
        for key, value in (extra or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)
        self._finish(status)

    def _security_headers(self) -> None:
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Frame-Options", "DENY")

    def _json(self, status: int, payload: Any) -> None:
        self._send(status, json.dumps(payload).encode(), "application/json")

    def _error(self, status: int, message: str) -> None:
        self._json(status, {"error": message})

    def _query(self) -> dict[str, str]:
        return {k: v[0] for k, v in parse_qs(urlsplit(self.path).query).items()}

    def _authorize(self, post: bool) -> None:
        ctx = self.ctx
        host = (self.headers.get("Host") or "").strip().lower()
        if host not in ctx.allowed_hosts:
            raise HttpError(403, "forbidden")
        token = self._query().get("t", "")
        if not hmac.compare_digest(token.encode(), ctx.token.encode()):
            raise HttpError(403, "forbidden")
        if post:
            origin = self.headers.get("Origin")
            if origin is not None and origin not in ctx.allowed_origins:
                raise HttpError(403, "forbidden")
            site = self.headers.get("Sec-Fetch-Site")
            if site is not None and site not in ("same-origin", "none"):
                raise HttpError(403, "forbidden")

    def _body(self, limit: int) -> bytes:
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError as exc:
            raise HttpError(400, "bad Content-Length") from exc
        if length < 0:
            raise HttpError(400, "bad Content-Length")
        if length > limit:
            raise HttpError(413, f"too large (the limit is {limit // 1_000_000} MB)")
        return self.rfile.read(length)

    def _json_body(self) -> dict[str, Any]:
        if (self.headers.get("Content-Type") or "").split(";")[0].strip() != "application/json":
            raise HttpError(415, "send application/json")
        try:
            data = json.loads(self._body(MAX_JSON_BYTES) or b"{}")
        except ValueError as exc:
            raise HttpError(400, "bad JSON") from exc
        if not isinstance(data, dict):
            raise HttpError(400, "send a JSON object")
        return data

    def _dispatch(self, post: bool) -> None:
        try:
            self._authorize(post)
            path = urlsplit(self.path).path
            routes = POST_ROUTES if post else GET_ROUTES
            route = routes.get(path)
            if route is None:
                raise HttpError(404, "not found")
            route(self)
        except HttpError as exc:
            self._json(exc.status, {"error": exc.message, **exc.extra})
        except (BrokenPipeError, ConnectionResetError):
            return
        except Exception as exc:  # noqa: BLE001 - never a stack trace to the browser
            self.ctx.log(f"error {type(exc).__name__} on {urlsplit(self.path).path}")
            try:
                self._error(500, "something went wrong; the terminal has the details")
            except OSError:
                return

    def do_GET(self) -> None:  # noqa: N802
        self._dispatch(post=False)

    def do_POST(self) -> None:  # noqa: N802
        self._dispatch(post=True)

    def do_OPTIONS(self) -> None:  # noqa: N802 - never answer a preflight
        self._error(403, "forbidden")

    # -- GET routes
    def page(self) -> None:
        nonce = secrets.token_urlsafe(16)
        body = render_page(self.ctx, nonce).encode()
        csp = (
            f"default-src 'none'; script-src 'nonce-{nonce}'; style-src 'unsafe-inline'; connect-src 'self'; "
            "img-src data:; base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
        )
        self._send(200, body, "text/html; charset=utf-8", {"Content-Security-Policy": csp})

    def get_status(self) -> None:
        self._json(200, status(self.ctx))

    def get_commands(self) -> None:
        self._json(200, commands(self.ctx))

    def _stream(self, events) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self._security_headers()
        self.end_headers()
        self._finish(200)
        try:
            for event in events:
                if event is None:
                    self.wfile.write(b": ping\n\n")
                else:
                    self.wfile.write(b"data: " + json.dumps(event).encode() + b"\n\n")
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            return

    def get_chat_stream(self) -> None:
        query = self._query()
        turn = self.ctx.chat.turn
        if turn is None or turn.id != query.get("turn"):
            raise HttpError(404, "no such turn")
        after = int(query.get("after", "-1") or -1)
        self._stream(self.ctx.chat.stream(turn, after, KEEPALIVE_SECONDS))

    def get_job_stream(self) -> None:
        query = self._query()
        job = self.ctx.jobs.jobs.get(query.get("job", ""))
        if job is None:
            raise HttpError(404, "no such job")

        def events():
            for item in self.ctx.jobs.stream(job, int(query.get("after", "0") or 0), KEEPALIVE_SECONDS):
                if item is None:
                    yield None
                elif item[0] == "line":
                    yield {"line": item[1]}
                else:
                    yield {"end": item[1]}

        self._stream(events())

    def get_job_history(self) -> None:
        self._json(200, {"history": self.ctx.jobs.history()})

    def get_chat_list(self) -> None:
        self._json(200, {"chats": self.ctx.chat.list_chats()})

    def get_chat_transcript(self) -> None:
        events = self.ctx.chat.transcript(self._query().get("id", ""))
        if events is None:
            raise HttpError(404, "no such chat")
        self._json(200, {"events": events})

    # -- POST routes
    def post_job_start(self) -> None:
        data = self._json_body()
        try:
            job = self.ctx.jobs.start(
                str(data.get("command", "")), dict(data.get("values") or {}), str(data.get("confirm") or "")
            )
        except week_jobs.Refused as exc:
            raise HttpError(exc.status, str(exc)) from exc
        self._json(200, {"job": job.id, "command": job.command})

    def post_job_stop(self) -> None:
        job = self.ctx.jobs.jobs.get(str(self._json_body().get("job", "")))
        if job is None or not self.ctx.jobs.stop(job):
            raise HttpError(409, "that job is not running")
        self._json(200, {"stopping": True})

    def post_chat_send(self) -> None:
        data = self._json_body()
        if not self.ctx.claude_available():
            raise HttpError(503, "Claude Code is not installed on this Mac (no `claude` on the PATH)")
        try:
            settings = week_chat.Settings.parse(data)
            chat_id = str(data["chat_id"]) if data.get("chat_id") else None
            turn = self.ctx.chat.send(str(data.get("text", "")), settings, chat_id)
        except week_chat.Busy as exc:
            raise HttpError(409, str(exc)) from exc
        except week_chat.BadRequest as exc:
            raise HttpError(400, str(exc)) from exc
        except OSError as exc:
            raise HttpError(500, f"could not start Claude Code: {exc}") from exc
        self._json(200, {"turn": turn.id})

    def post_chat_approve(self) -> None:
        data = self._json_body()
        turn = self.ctx.chat.turn
        if turn is None or turn.id != data.get("turn"):
            raise HttpError(404, "no such turn")
        if not self.ctx.chat.approve(turn, str(data.get("id", "")), bool(data.get("allow"))):
            raise HttpError(409, "that request is already answered")
        self._json(200, {"ok": True})

    def post_chat_stop(self) -> None:
        self._json_body()
        if not self.ctx.chat.stop():
            raise HttpError(409, "nothing is running")
        self._json(200, {"stopping": True})

    def post_check(self) -> None:
        self._json(200, check_contest(self.ctx, self._json_body()))

    def post_budget(self) -> None:
        self._json(200, save_budget(self.ctx, self._json_body().get("budget")))

    def post_drop(self) -> None:
        if (self.headers.get("Content-Type") or "").split(";")[0].strip() != "application/octet-stream":
            raise HttpError(415, "send the file as application/octet-stream")
        name = self._query().get("name", "")
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError as exc:
            raise HttpError(400, "bad Content-Length") from exc
        try:
            week_drops.check_name_and_size(name, length)  # before a byte of the body is read
        except week_drops.Rejected as exc:
            raise HttpError(exc.status, str(exc)) from exc
        self._json(200, receive_drop(self.ctx, name, self._body(week_drops.max_upload_bytes(name))))


def _post_pool(self: Handler) -> None:
    data = self._json_body()
    changes = data.get("changes")

    def act(edits, inp):
        values = week_actions.pool_change(edits, changes)
        return {"pool": values, "message": "Pool: " + pool_message(values, inp)}

    def bulk(edits, inp):
        results = week_actions.pool_bulk(edits, changes)
        ok = {i: r["value"] for i, r in results.items() if r["status"] == "ok"}
        bad = {i: r for i, r in results.items() if r["status"] != "ok"}
        text = f"Pool: {len(ok)} written" + (f"; {len(bad)} not written (see each row)" if bad else "")
        return {"pool": ok, "results": results, "message": text}

    if data.get("each"):  # the multi-select: per-cell conflicts, always 200 with each cell's result
        self._json(200, sheet_write(self.ctx, bulk))
        return

    self._json(200, sheet_write(self.ctx, act))


def _post_pool_start(self: Handler) -> None:
    self._json_body()

    def act(edits, inp):
        values = week_actions.start_pool(edits, inp)
        return {"pool": values, "message": f"Added {len(values)} to your pool: " + pool_message(values, inp)}

    self._json(200, sheet_write(self.ctx, act))


def _post_decide(self: Handler) -> None:
    data = self._json_body()
    rules_was = data.get("rules_was") if isinstance(data.get("rules_was"), dict) else {}

    def act(edits, inp):
        text, pool = week_actions.decide_pool(
            edits,
            inp,
            key=str(data.get("key", "")),
            option=str(data.get("option", "")),
            rules_was=rules_was,
            pivot=str(data.get("pivot") or ""),
            pool_was=str(data.get("pool_was") or ""),
        )
        return {"message": text, "pool": pool}

    self._json(200, sheet_write(self.ctx, act))


def _post_stack(self: Handler) -> None:
    data = self._json_body()
    rules_was = data.get("rules_was") if isinstance(data.get("rules_was"), dict) else {}
    names = data.get("names") if isinstance(data.get("names"), list) else []

    def act(edits, inp):
        return {"message": week_actions.more_stack(edits, inp, names, rules_was)}

    self._json(200, sheet_write(self.ctx, act))


Handler.post_pool = _post_pool  # type: ignore[attr-defined]
Handler.post_stack = _post_stack  # type: ignore[attr-defined]
Handler.post_pool_start = _post_pool_start  # type: ignore[attr-defined]
Handler.post_decide = _post_decide  # type: ignore[attr-defined]

GET_ROUTES: dict[str, Callable[[Handler], None]] = {
    "/": Handler.page,
    "/api/status": Handler.get_status,
    "/api/commands": Handler.get_commands,
    "/api/chat/stream": Handler.get_chat_stream,
    "/api/chat/list": Handler.get_chat_list,
    "/api/chat/transcript": Handler.get_chat_transcript,
    "/api/jobs/stream": Handler.get_job_stream,
    "/api/jobs/history": Handler.get_job_history,
}
POST_ROUTES: dict[str, Callable[[Handler], None]] = {
    "/api/jobs/start": Handler.post_job_start,
    "/api/jobs/stop": Handler.post_job_stop,
    "/api/chat/send": Handler.post_chat_send,
    "/api/chat/approve": Handler.post_chat_approve,
    "/api/chat/stop": Handler.post_chat_stop,
    "/api/check": Handler.post_check,
    "/api/budget": Handler.post_budget,
    "/api/drop": Handler.post_drop,
    "/api/pool": _post_pool,
    "/api/pool/start": _post_pool_start,
    "/api/decide": _post_decide,
    "/api/stack": _post_stack,
}


class Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address: tuple[str, int], ctx: Context):
        super().__init__(address, Handler)
        self.ctx = ctx


def make_server(
    *,
    season: int,
    week: int,
    port: int = 0,
    host: str = LOOPBACK,
    runner: week_chat.Runner | None = None,
    token: str | None = None,
    read_only: bool | None = None,
    data_dir: Path | None = None,
    log_path: Path | None = None,
    jobs: week_jobs.JobRunner | None = None,
    chat: week_chat.ChatManager | None = None,
    claude_available: Callable[[], bool] | None = None,
    edits: Any = None,
) -> Server:
    """A server bound to the loopback address only. The caller starts it (`serve_forever`) and stops it."""
    if host != LOOPBACK:
        raise ValueError("the Week server binds to 127.0.0.1 only")
    if read_only is None:
        read_only = (season, week) < (nfl_calendar.current_season(), nfl_calendar.current_week())
    ctx = Context(
        season=season,
        week=week,
        token=token or secrets.token_urlsafe(24),
        chat=None,  # type: ignore[arg-type] - set below, after the logger exists
        jobs=None,  # type: ignore[arg-type]
        read_only=read_only,
        edits=edits,
        data_dir=data_dir,
        log_path=log_path,
    )
    if claude_available is not None:
        ctx.claude_available = claude_available
    ctx.chat = chat or week_chat.ChatManager(
        runner or week_chat.SubprocessRunner(), season=season, week=week, log=ctx.log
    )
    ctx.jobs = jobs or week_jobs.JobRunner(week_is_closed=lambda: ctx.read_only, log=ctx.log)
    server = Server((host, port), ctx)
    ctx.port = server.server_address[1]
    return server


def sheet_edits(sheet_id: str | None, read_only: bool) -> tuple[Any, str]:
    """The page's reads and writes against the configured sheet (or `sheet_id`): (SheetEdits or None, a line
    to print). No config or no credentials: None, and the page shows the last sync's pool, read only."""
    try:
        from dfs.config import load_config
        from dfs.sheets import SheetsClient

        cfg = load_config()
        gs = cfg.google_sheets.model_copy(update={"sheet_id": sheet_id}) if sheet_id else cfg.google_sheets
        edge_tab = gs.tab_mappings.get("edge")
        if not edge_tab:
            return None, "No 'edge' tab mapped in config.toml: the page cannot write the pool."
        client = SheetsClient(gs)
        title, url = client.describe()
    except Exception as exc:  # noqa: BLE001 - the page still works from the saved files
        return (
            None,
            f"No sheet ({type(exc).__name__}: {exc}): the page shows the last sync and writes nothing.",
        )
    edits = week_writes.SheetEdits(client, edge_tab=edge_tab, read_only=read_only, title=title)
    return edits, f"Sheet: {title} ({url}){' (read only)' if read_only else ''}"


def pid_path() -> Path:
    return paths.DATA_DIR / "week" / "serve.pid"


def _is_dfs_process(pid: int) -> bool:
    """True when `pid` is alive and its command line is a `dfs` one (so a reused pid is never signalled)."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return False
    try:
        out = subprocess.run(
            ["ps", "-p", str(pid), "-o", "command="], capture_output=True, text=True, timeout=5, check=False
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return False
    return "dfs" in out


def running(path: Path | None = None, alive: Callable[[int], bool] = _is_dfs_process) -> dict | None:
    """The live server's pidfile record (`pid`, `port`, `week`, `url`), or None (no file, unreadable, or its
    process is gone: a stale file)."""
    path = path or pid_path()
    try:
        record = json.loads(path.read_text())
        pid = int(record["pid"])
    except (OSError, ValueError, KeyError, TypeError):
        return None
    return record if alive(pid) else None


def write_pidfile(record: dict, path: Path | None = None) -> None:
    path = path or pid_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(record, f)


def remove_pidfile(pid: int, path: Path | None = None) -> None:
    """Remove the pidfile only when it is still this process's."""
    path = path or pid_path()
    try:
        if int(json.loads(path.read_text()).get("pid", -1)) == pid:
            path.unlink()
    except (OSError, ValueError, TypeError):
        pass


def stop(path: Path | None = None, alive: Callable[[int], bool] = _is_dfs_process, kill=os.kill) -> str:
    """`dfs week serve --stop`: SIGTERM to the pidfile's pid, and nothing else."""
    path = path or pid_path()
    record = running(path, alive)
    if record is None:
        if path.exists():
            path.unlink(missing_ok=True)
            return "No Week server is running (removed a stale pidfile)."
        return "No Week server is running."
    kill(int(record["pid"]), signal.SIGTERM)
    where = f"pid {record['pid']}, Week {record.get('week')}, port {record.get('port')}"
    return f"Stopped the Week server ({where})."


def _sigterm(signum, frame) -> None:  # noqa: ANN001, ARG001
    raise KeyboardInterrupt


def serve(
    week: int | None = None,
    port: int = 0,
    open_browser: bool = True,
    echo=print,
    sheet_id: str | None = None,
) -> None:
    """Run the server until Ctrl-C."""
    existing = running()
    if existing is not None:
        echo(
            f"A Week server is already running (pid {existing['pid']}, Week {existing.get('week')}): "
            f"{existing.get('url')}"
        )
        echo("Opening it instead of starting a second. `dfs week serve --stop` stops it.")
        if open_browser and existing.get("url"):
            webbrowser.open(str(existing["url"]))
        return
    season = nfl_calendar.current_season()
    week = week if week is not None else nfl_calendar.current_week()
    read_only = (season, week) < (nfl_calendar.current_season(), nfl_calendar.current_week())
    edits, line = sheet_edits(sheet_id, read_only)
    server = make_server(season=season, week=week, port=port, edits=edits, read_only=read_only)
    ctx = server.ctx
    url = f"http://{LOOPBACK}:{ctx.port}/?t={ctx.token}"
    echo(line)
    echo(f"Week {week} page: {url}")
    echo(
        f"Closed week, read only: {ctx.read_only}. Ctrl-C stops the server."
        if ctx.read_only
        else "Ctrl-C stops the server."
    )
    ctx.log(f"server started on port {ctx.port} for week {week}")
    pid = os.getpid()
    write_pidfile(
        {"pid": pid, "port": ctx.port, "week": week, "url": url, "started": datetime.now().isoformat()}
    )
    previous = signal.signal(signal.SIGTERM, _sigterm)  # `--stop` ends it the way Ctrl-C does
    if open_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        signal.signal(signal.SIGTERM, previous)
        if ctx.chat.busy:
            ctx.chat.stop()
        server.server_close()
        remove_pidfile(pid)
        ctx.log("server stopped")
