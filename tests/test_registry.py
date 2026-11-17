"""`research/registry.toml` (blueprint Phase D): every experiment carries its pre-registration; at most two
are open; an open one has no verdict yet."""

from __future__ import annotations

import datetime as dt
import tomllib

import pytest

from dfs.paths import REPO_ROOT

PATH = REPO_ROOT / "research" / "registry.toml"
KEYS = {
    "id",
    "question",
    "hypothesis",
    "metric",
    "baseline",
    "bar",
    "weeks",
    "variants_tried",
    "registered",
    "testable",
    "status",
    "report",
}
STATUSES = {"open", "read", "dropped"}
MAX_OPEN = 2


def check(data: dict) -> list[str]:
    """Every problem with a loaded registry (empty: fine)."""
    problems = []
    entries = data.get("experiment", [])
    ids = [e.get("id") for e in entries]
    if len(ids) != len(set(ids)):
        problems.append("a repeated id")
    for e in entries:
        missing = KEYS - set(e)
        if missing:
            problems.append(f"{e.get('id')}: missing {sorted(missing)}")
        if e.get("status") not in STATUSES:
            problems.append(f"{e.get('id')}: status {e.get('status')!r}")
        if e.get("status") == "open" and e.get("verdict"):
            problems.append(f"{e.get('id')}: a verdict on an open entry")
        if e.get("status") in ("read", "dropped") and not e.get("verdict"):
            problems.append(f"{e.get('id')}: {e.get('status')} with no verdict")
        if not isinstance(e.get("registered"), dt.date):
            problems.append(f"{e.get('id')}: registered is not a date")
        if not isinstance(e.get("variants_tried"), int) or e.get("variants_tried", 0) < 1:
            problems.append(f"{e.get('id')}: variants_tried")
        if not str(e.get("report", "")).startswith("docs/research/"):
            problems.append(f"{e.get('id')}: the report is not under docs/research/")
    if sum(e.get("status") == "open" for e in entries) > MAX_OPEN:
        problems.append(f"more than {MAX_OPEN} open")
    return problems


def test_the_registry_loads_with_r2_read():
    data = tomllib.loads(PATH.read_text())
    assert check(data) == []
    r2 = next(e for e in data["experiment"] if e["id"] == "R2")
    assert r2["status"] == "read" and r2["registered"] == dt.date(2026, 11, 10)
    assert r2["report"] == "docs/research/news/R2_LATE_NEWS_2026.md"


def entry(id_, **over):
    base = tomllib.loads(PATH.read_text())["experiment"][0]
    return {**base, "id": id_, **over}


@pytest.mark.parametrize(
    ("entries", "problem"),
    [
        ([{k: v for k, v in entry("R9").items() if k != "metric"}], "missing ['metric']"),
        ([entry(f"R{i}", status="open", verdict="") for i in range(3)], "more than 2 open"),
        ([entry("R9", status="open")], "a verdict on an open entry"),
        ([entry("R9", status="read", verdict="")], "read with no verdict"),
    ],
)
def test_the_check_fails_on_a_missing_key_a_third_open_entry_or_an_open_verdict(entries, problem):
    assert any(problem in p for p in check({"experiment": entries}))
