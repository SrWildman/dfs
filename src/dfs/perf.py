"""Round 5 item 2: request counting, per-phase timing, and retry/backoff.

`dfs` commands are slow because of ROUND TRIPS, not quota: hundreds of
sequential Sheets requests at 0.3-1 s each. Before batching anything, this
module measures it:

- `InstrumentedHTTPClient` (what `SheetsClient` hands to gspread) counts and
  times EVERY API request -- reads included -- and retries 429/408/5xx and
  transient network errors with exponential backoff plus jitter (item 2.4).
- `phase("name")` is a context manager the CLI wraps around each step of a
  command; `report()` prints wall-clock, request count, and the top phases.
  Set `DFS_PROFILE=1` (or pass `dfs --profile ...`) to print it at exit.

Nothing here changes what any command WRITES.
"""

from __future__ import annotations

import atexit
import copy
import functools
import json
import os
import random
import threading
import time
from collections import Counter
from contextlib import contextmanager
from dataclasses import dataclass, field

import gspread
import requests
from gspread.exceptions import APIError

# Retried: rate limit, request timeout, transient server errors.
RETRY_STATUS_CODES = frozenset({408, 429, 500, 502, 503, 504})
MAX_RETRIES = 8
MAX_BACKOFF_SECONDS = 60.0
BASE_BACKOFF_SECONDS = 1.0

# Indirection so tests never really sleep.
_sleep = time.sleep

# Request batching (item 2.2). While a `SheetsClient.batched()` block is open,
# `batchUpdate` requests of these types are QUEUED and sent together. Only
# formatting/metadata-style requests qualify: none of them changes the grid's
# shape, so nothing computed from a later read can depend on them having
# landed *yet* -- and any read or non-queueable write flushes the queue first,
# so order (including conditional-format rule order) and read-after-write are
# identical to sending each one immediately.
QUEUEABLE_REQUEST_TYPES = frozenset(
    {
        "repeatCell",
        "updateCells",
        "addConditionalFormatRule",
        "updateConditionalFormatRule",
        "deleteConditionalFormatRule",
        "updateDimensionProperties",
        "addDimensionGroup",
        "updateDimensionGroup",
        "deleteDimensionGroup",
        "updateSheetProperties",
        "setBasicFilter",
        "clearBasicFilter",
        "addBanding",
        "updateBanding",
        "deleteBanding",
        "updateBorders",
        "setDataValidation",
        "mergeCells",
        "unmergeCells",
        "addProtectedRange",
        "deleteProtectedRange",
        "addChart",
        "deleteEmbeddedObject",
        "addFilterView",
        "deleteFilterView",
    }
)
MAX_REQUESTS_PER_BATCH = 400
MAX_BATCH_BYTES = 2_000_000


@dataclass
class PhaseRecord:
    name: str
    seconds: float
    requests: int
    depth: int


@dataclass
class Stats:
    started: float = field(default_factory=time.perf_counter)
    requests: int = 0
    request_seconds: float = 0.0
    retries: int = 0
    queued: int = 0
    by_kind: Counter = field(default_factory=Counter)
    phases: list[PhaseRecord] = field(default_factory=list)


_lock = threading.Lock()
STATS = Stats()
_phase_depth = 0


def reset() -> None:
    """Fresh counters (tests, and one measurement per command)."""
    global STATS, _phase_depth
    STATS = Stats()
    _phase_depth = 0


def _kind(method: str, endpoint: str) -> str:
    """Coarse bucket for the report: read / batchGet / write / batchUpdate."""
    tail = endpoint.rsplit("/", 1)[-1]
    if ":batchUpdate" in tail:
        return "batchUpdate"
    if "values:batchGet" in endpoint:
        return "batchGet"
    if method.upper() == "GET":
        return "read"
    return "write"


def _record_request(method: str, endpoint: str, seconds: float) -> None:
    with _lock:
        STATS.requests += 1
        STATS.request_seconds += seconds
        STATS.by_kind[_kind(method, endpoint)] += 1


def _backoff_seconds(attempt: int, retry_after: str | None) -> float:
    if retry_after:
        try:
            return min(float(retry_after), MAX_BACKOFF_SECONDS)
        except ValueError:
            pass
    return min(BASE_BACKOFF_SECONDS * (2**attempt) + random.uniform(0, 1), MAX_BACKOFF_SECONDS)  # noqa: S311


def _is_retryable(err: APIError) -> bool:
    if err.code in RETRY_STATUS_CODES:
        return True
    # Drive reports quota exhaustion as a 403 in the `usageLimits` domain.
    errors = err.error.get("errors") if isinstance(err.error, dict) else None
    return bool(err.code == 403 and errors and errors[0].get("domain") == "usageLimits")


class InstrumentedHTTPClient(gspread.http_client.HTTPClient):
    """gspread's HTTP client, counting/timing every request and retrying the
    transient failures `BackOffHTTPClient` only half-covered (it skipped
    502/503/504 jitter and network errors, and its counter was shared across
    unrelated requests)."""

    batching: bool = False
    _flushing: bool = False

    def _pending_list(self) -> list:
        if not hasattr(self, "_pending"):
            # (kind, spreadsheet id, payload): "batch" -> one batchUpdate request dict;
            # "values" -> (valueInputOption, {"range", "values", "majorDimension"}).
            self._pending: list[tuple[str, str, object]] = []
        return self._pending

    def batch_update(self, id, body):  # noqa: A002, ANN001, ANN201
        requests_ = (body or {}).get("requests", [])
        queueable = (
            self.batching
            and requests_
            and set(body) == {"requests"}
            and all(len(r) == 1 and next(iter(r)) in QUEUEABLE_REQUEST_TYPES for r in requests_)
        )
        if not queueable:
            return super().batch_update(id, body)
        pending = self._pending_list()
        pending.extend(("batch", id, r) for r in requests_)
        with _lock:
            STATS.queued += len(requests_)
        return {"spreadsheetId": id, "replies": [{} for _ in requests_]}

    def values_update(self, id, range, params=None, body=None):  # noqa: A002, ANN001, ANN201
        """Cell-value writes (`ws.update`) are queued too and sent as ONE
        `values:batchUpdate` per run of consecutive writes -- the 487 individual
        PUTs a `link-edge` run used to make. Anything unusual (response-value
        options, non-default body keys) is sent immediately."""
        params = {k: v for k, v in (params or {}).items() if v is not None and v is not False}
        body = body or {}
        queueable = (
            self.batching
            and set(params) <= {"valueInputOption"}
            and set(body) <= {"values", "majorDimension"}
            and "values" in body
        )
        if not queueable:
            return super().values_update(id, range, params=params or None, body=body or None)
        payload = {
            "range": range,
            "values": copy.deepcopy(body["values"]),
            "majorDimension": body.get("majorDimension", "ROWS"),
        }
        option = str(params.get("valueInputOption") or "USER_ENTERED")
        self._pending_list().append(("values", id, (option, payload)))
        with _lock:
            STATS.queued += 1
        return {"spreadsheetId": id, "updatedRange": range}

    def flush(self) -> int:
        """Send everything queued, in order: consecutive same-kind items are
        grouped into as few requests as the size limits allow. Returns how many
        queued requests were sent."""
        pending = self._pending_list()
        if not pending or self._flushing:
            return 0
        self._flushing = True
        sent = 0
        try:
            while pending:
                kind, sheet_id, first = pending[0]
                option = first[0] if kind == "values" else None
                chunk: list = []
                size = 0
                while (
                    pending
                    and pending[0][0] == kind
                    and pending[0][1] == sheet_id
                    and (kind == "batch" or pending[0][2][0] == option)
                    and len(chunk) < MAX_REQUESTS_PER_BATCH
                ):
                    item = pending[0][2] if kind == "batch" else pending[0][2][1]
                    est = len(json.dumps(item))
                    if chunk and size + est > MAX_BATCH_BYTES:
                        break
                    chunk.append(item)
                    pending.pop(0)
                    size += est
                try:
                    if kind == "batch":
                        super().batch_update(sheet_id, {"requests": chunk})
                    else:
                        self.values_batch_update(sheet_id, {"valueInputOption": option, "data": chunk})
                except Exception:
                    pending.clear()
                    raise
                sent += len(chunk)
        finally:
            self._flushing = False
        return sent

    def request(self, method, endpoint, *args, **kwargs):  # noqa: ANN001, ANN002, ANN003, ANN201
        if not self._flushing and self._pending_list():
            self.flush()
        attempt = 0
        while True:
            started = time.perf_counter()
            try:
                response = super().request(method, endpoint, *args, **kwargs)
            except APIError as err:
                _record_request(method, endpoint, time.perf_counter() - started)
                if not _is_retryable(err) or attempt >= MAX_RETRIES:
                    raise
                retry_after = None
                headers = getattr(getattr(err, "response", None), "headers", None)
                if headers:
                    retry_after = headers.get("Retry-After")
                wait = _backoff_seconds(attempt, retry_after)
            except (requests.ConnectionError, requests.Timeout):
                _record_request(method, endpoint, time.perf_counter() - started)
                if attempt >= MAX_RETRIES:
                    raise
                wait = _backoff_seconds(attempt, None)
            else:
                _record_request(method, endpoint, time.perf_counter() - started)
                return response
            with _lock:
                STATS.retries += 1
            _sleep(wait)
            attempt += 1


@contextmanager
def phase(name: str):  # noqa: ANN201
    """Time a named step and count the API requests made inside it. Nested
    phases are recorded too (with their depth) so the report can show a
    step and its children."""
    global _phase_depth
    depth = _phase_depth
    _phase_depth += 1
    started = time.perf_counter()
    requests_before = STATS.requests
    try:
        yield
    finally:
        _phase_depth = depth
        STATS.phases.append(
            PhaseRecord(
                name=name,
                seconds=time.perf_counter() - started,
                requests=STATS.requests - requests_before,
                depth=depth,
            )
        )


def timed(name: str | None = None):  # noqa: ANN201
    """Decorator form of `phase` -- wraps a function so every call is a
    recorded phase named after it (or `name`)."""

    def decorate(fn):  # noqa: ANN001, ANN202
        label = name or fn.__name__

        @functools.wraps(fn)
        def wrapper(*args, **kwargs):  # noqa: ANN002, ANN003, ANN202
            with phase(label):
                return fn(*args, **kwargs)

        return wrapper

    return decorate


def top_phases(n: int = 5, *, depth: int | None = None) -> list[PhaseRecord]:
    records = [p for p in STATS.phases if depth is None or p.depth == depth]
    return sorted(records, key=lambda p: p.seconds, reverse=True)[:n]


def format_report(command: str = "") -> str:
    wall = time.perf_counter() - STATS.started
    lines = [
        f"-- profile{': ' + command if command else ''} --",
        f"wall-clock {wall:.1f}s | API requests {STATS.requests} "
        f"({STATS.request_seconds:.1f}s in requests, {STATS.retries} retried, "
        f"{STATS.queued} batched into them) | "
        + ", ".join(f"{k} {v}" for k, v in sorted(STATS.by_kind.items())),
    ]
    top = top_phases(12)
    if top:
        lines.append("top phases by time:")
        for p in top:
            lines.append(f"  {p.seconds:7.1f}s  {p.requests:5d} req  {'  ' * p.depth}{p.name}")
    return "\n".join(lines)


def enable_report_at_exit(command: str = "") -> None:
    """Print the profile when the process exits (used by `--profile` /
    `DFS_PROFILE=1`)."""
    atexit.register(lambda: print(format_report(command)))  # noqa: T201


def batching_disabled() -> bool:
    """`DFS_NO_BATCH=1` restores the pre-item-2 behavior (every call sent
    immediately, one CF clear per target) -- for before/after comparisons
    and as an escape hatch if a batched run ever misbehaves."""
    return os.environ.get("DFS_NO_BATCH", "").strip() not in ("", "0", "false", "False")


def profile_requested() -> bool:
    return os.environ.get("DFS_PROFILE", "").strip() not in ("", "0", "false", "False")
