import gspread
import pytest
import requests
from gspread.exceptions import APIError

from dfs import perf


class _FakeResponse:
    def __init__(self, code, headers=None):
        self.status_code = code
        self.headers = headers or {}
        self.text = "x"
        self._code = code

    def json(self):
        return {"error": {"code": self._code, "message": "boom", "status": "X"}}


def _client():
    # A plain Session skips credential setup; `request` on the base class is
    # patched out, so nothing here touches the network.
    return perf.InstrumentedHTTPClient(auth=None, session=requests.Session())


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    perf.reset()
    monkeypatch.setattr(perf, "_sleep", lambda s: None)


def _script(monkeypatch, outcomes):
    calls = []
    it = iter(outcomes)

    def fake_request(self, method, endpoint, *a, **k):
        calls.append((method, endpoint))
        out = next(it)
        if isinstance(out, Exception):
            raise out
        return out

    monkeypatch.setattr(gspread.http_client.HTTPClient, "request", fake_request)
    return calls


def test_retries_429_then_succeeds_and_counts_every_attempt(monkeypatch):
    calls = _script(monkeypatch, [APIError(_FakeResponse(429)), APIError(_FakeResponse(503)), "ok"])
    assert _client().request("get", "https://sheets/x") == "ok"
    assert len(calls) == 3
    assert perf.STATS.requests == 3
    assert perf.STATS.retries == 2


def test_does_not_retry_a_permanent_error(monkeypatch):
    calls = _script(monkeypatch, [APIError(_FakeResponse(400))])
    with pytest.raises(APIError):
        _client().request("get", "https://sheets/x")
    assert len(calls) == 1
    assert perf.STATS.retries == 0


def test_gives_up_after_max_retries(monkeypatch):
    _script(monkeypatch, [APIError(_FakeResponse(500)) for _ in range(perf.MAX_RETRIES + 1)])
    with pytest.raises(APIError):
        _client().request("get", "https://sheets/x")
    assert perf.STATS.requests == perf.MAX_RETRIES + 1


def test_retries_a_network_error(monkeypatch):
    _script(monkeypatch, [requests.ConnectionError("reset"), "ok"])
    assert _client().request("get", "https://sheets/x") == "ok"
    assert perf.STATS.retries == 1


def test_honors_retry_after(monkeypatch):
    waits = []
    monkeypatch.setattr(perf, "_sleep", waits.append)
    _script(monkeypatch, [APIError(_FakeResponse(429, {"Retry-After": "7"})), "ok"])
    _client().request("get", "https://sheets/x")
    assert waits == [7.0]


def test_backoff_grows_and_is_capped():
    assert perf._backoff_seconds(0, None) < perf._backoff_seconds(4, None)
    assert perf._backoff_seconds(30, None) <= perf.MAX_BACKOFF_SECONDS


def test_requests_are_bucketed_by_kind():
    assert perf._kind("post", "https://x/spreadsheets/ID:batchUpdate") == "batchUpdate"
    assert perf._kind("get", "https://x/spreadsheets/ID/values:batchGet") == "batchGet"
    assert perf._kind("get", "https://x/spreadsheets/ID/values/A1") == "read"
    assert perf._kind("put", "https://x/spreadsheets/ID/values/A1") == "write"


def test_phase_records_time_requests_and_nesting():
    with perf.phase("outer"):
        perf._record_request("get", "https://x/a", 0.1)
        with perf.phase("inner"):
            perf._record_request("post", "https://x/b:batchUpdate", 0.1)
    by_name = {p.name: p for p in perf.STATS.phases}
    assert by_name["outer"].requests == 2 and by_name["outer"].depth == 0
    assert by_name["inner"].requests == 1 and by_name["inner"].depth == 1
    assert perf.top_phases(1, depth=0)[0].name == "outer"
    assert "outer" in perf.format_report("test")


# --- request batching (item 2.2) -------------------------------------------


class _Recorder:
    """Stands in for the network: records every request that would be sent."""

    def __init__(self, monkeypatch):
        self.sent: list[tuple[str, object]] = []
        sent = self.sent

        def fake_request(http, method, endpoint, *a, **k):
            sent.append((endpoint, k.get("json")))

            class R:
                def json(self):
                    return {"replies": []}

            return R()

        monkeypatch.setattr(gspread.http_client.HTTPClient, "request", fake_request)

    def batches(self):
        return [body["requests"] for ep, body in self.sent if ep.endswith(":batchUpdate")]


def _req(kind, n=0):
    return {kind: {"n": n}}


def test_queueable_requests_are_held_then_sent_together_in_order(monkeypatch):
    net = _Recorder(monkeypatch)
    http = _client()
    http.batching = True
    for i in range(5):
        reply = http.batch_update("SID", {"requests": [_req("repeatCell", i)]})
        assert reply["replies"] == [{}]  # fabricated, one per request
    assert net.sent == []  # nothing on the wire yet
    assert http.flush() == 5
    assert len(net.batches()) == 1
    assert [r["repeatCell"]["n"] for r in net.batches()[0]] == [0, 1, 2, 3, 4]  # FIFO: rule order kept


def test_a_read_flushes_the_queue_first(monkeypatch):
    net = _Recorder(monkeypatch)
    http = _client()
    http.batching = True
    http.batch_update("SID", {"requests": [_req("addConditionalFormatRule", 1)]})
    http.request("get", "https://sheets/spreadsheets/SID")  # e.g. fetch_sheet_metadata
    endpoints = [ep for ep, _ in net.sent]
    assert endpoints[0].endswith(":batchUpdate")  # the write landed BEFORE the read
    assert endpoints[1] == "https://sheets/spreadsheets/SID"


def test_shape_changing_requests_are_never_queued(monkeypatch):
    net = _Recorder(monkeypatch)
    http = _client()
    http.batching = True
    http.batch_update("SID", {"requests": [_req("repeatCell", 1)]})
    http.batch_update("SID", {"requests": [_req("insertDimension", 2)]})
    kinds = [list(b[0])[0] for b in net.batches()]
    assert kinds == ["repeatCell", "insertDimension"]  # queue flushed, then the insert sent at once


def test_not_batching_sends_immediately(monkeypatch):
    net = _Recorder(monkeypatch)
    http = _client()
    http.batch_update("SID", {"requests": [_req("repeatCell", 1)]})
    assert len(net.batches()) == 1


def test_a_mixed_request_list_is_sent_immediately_not_split(monkeypatch):
    net = _Recorder(monkeypatch)
    http = _client()
    http.batching = True
    http.batch_update("SID", {"requests": [_req("repeatCell"), _req("deleteDimension")]})
    assert len(net.batches()) == 1 and len(net.batches()[0]) == 2


def test_flush_chunks_by_request_count(monkeypatch):
    net = _Recorder(monkeypatch)
    http = _client()
    http.batching = True
    for i in range(perf.MAX_REQUESTS_PER_BATCH + 10):
        http.batch_update("SID", {"requests": [_req("repeatCell", i)]})
    http.flush()
    assert [len(b) for b in net.batches()] == [perf.MAX_REQUESTS_PER_BATCH, 10]


def test_a_failed_flush_raises_and_drops_the_rest(monkeypatch):
    calls = []

    def boom(http, method, endpoint, *a, **k):
        calls.append(endpoint)
        raise APIError(_FakeResponse(400))

    monkeypatch.setattr(gspread.http_client.HTTPClient, "request", boom)
    http = _client()
    http.batching = True
    http.batch_update("SID", {"requests": [_req("repeatCell", 1)]})
    with pytest.raises(APIError):
        http.flush()
    assert http._pending_list() == []  # never re-sent by a later flush


# --- cell-value writes in the queue ----------------------------------------


def _values_recorder(monkeypatch):
    """Like `_Recorder` but also captures the `values_update` PUT/POST bodies."""
    net = _Recorder(monkeypatch)
    return net


def test_value_writes_coalesce_into_one_values_batch_update(monkeypatch):
    net = _Recorder(monkeypatch)
    http = _client()
    http.batching = True
    for i in range(3):
        http.values_update(
            "SID",
            f"T!A{i + 1}",
            params={"valueInputOption": "USER_ENTERED", "includeValuesInResponse": None},
            body={"values": [[i]], "majorDimension": "ROWS"},
        )
    assert net.sent == []
    assert http.flush() == 3
    (endpoint, body) = net.sent[0]
    assert endpoint.endswith("/values:batchUpdate")
    assert body["valueInputOption"] == "USER_ENTERED"
    assert [d["range"] for d in body["data"]] == ["T!A1", "T!A2", "T!A3"]  # order kept
    assert len(net.sent) == 1


def test_value_writes_are_copied_so_later_mutation_cannot_change_them(monkeypatch):
    net = _Recorder(monkeypatch)
    http = _client()
    http.batching = True
    rows = [["a"]]
    http.values_update("SID", "T!A1", params={"valueInputOption": "USER_ENTERED"}, body={"values": rows})
    rows[0][0] = "MUTATED"
    http.flush()
    assert net.sent[0][1]["data"][0]["values"] == [["a"]]


def test_values_and_formatting_keep_their_relative_order(monkeypatch):
    net = _Recorder(monkeypatch)
    http = _client()
    http.batching = True
    http.batch_update("SID", {"requests": [_req("repeatCell", 1)]})
    http.values_update("SID", "T!A1", params={"valueInputOption": "USER_ENTERED"}, body={"values": [[1]]})
    http.batch_update("SID", {"requests": [_req("repeatCell", 2)]})
    http.flush()
    kinds = ["values" if ep.endswith("values:batchUpdate") else "format" for ep, _ in net.sent]
    assert kinds == ["format", "values", "format"]  # never reordered across kinds


def test_different_value_input_options_are_not_merged(monkeypatch):
    net = _Recorder(monkeypatch)
    http = _client()
    http.batching = True
    http.values_update("SID", "T!A1", params={"valueInputOption": "USER_ENTERED"}, body={"values": [[1]]})
    http.values_update("SID", "T!A2", params={"valueInputOption": "RAW"}, body={"values": [[2]]})
    http.flush()
    assert [b["valueInputOption"] for _ep, b in net.sent] == ["USER_ENTERED", "RAW"]


def test_a_read_flushes_queued_value_writes_first(monkeypatch):
    net = _Recorder(monkeypatch)
    http = _client()
    http.batching = True
    http.values_update("SID", "T!A1", params={"valueInputOption": "USER_ENTERED"}, body={"values": [[1]]})
    http.request("get", "https://sheets/spreadsheets/SID/values/T!A1")
    assert net.sent[0][0].endswith("values:batchUpdate")
    assert net.sent[1][0].endswith("/values/T!A1")


def test_unusual_value_updates_are_sent_immediately(monkeypatch):
    net = _Recorder(monkeypatch)
    http = _client()
    http.batching = True
    http.values_update(
        "SID",
        "T!A1",
        params={"valueInputOption": "USER_ENTERED", "includeValuesInResponse": True},
        body={"values": [[1]]},
    )
    assert len(net.sent) == 1  # asked for the response values, so it can't be faked
