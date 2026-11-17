"""`data/week/decisions.jsonl`: append-only, ids per week, undos folded out by `read`."""

from __future__ import annotations

import pytest

from dfs import decisions_log as dl


def rec(kind="swap", week=11, **kw) -> dict:
    return {
        "season": 2026,
        "week": week,
        "kind": kind,
        "state": "lock",
        "chosen": {"id": "1", "name": "A"},
        **kw,
    }


def test_the_first_lines_bytes_never_change(tmp_path):
    path = tmp_path / "decisions.jsonl"
    first = dl.append(rec(), path)
    before = path.read_bytes()
    dl.append(rec("keep"), path)
    dl.undo(first, season=2026, week=11, path=path)
    after = path.read_bytes()
    assert after.startswith(before) and len(after.splitlines()) == 3


def test_ids_run_per_week_and_carry_the_schema(tmp_path):
    path = tmp_path / "decisions.jsonl"
    assert dl.append(rec(), path) == "2026-11-0001"
    assert dl.append(rec(), path) == "2026-11-0002"
    assert dl.append(rec(week=12), path) == "2026-12-0001"
    [one, _] = dl.read(2026, 11, path)
    for key in (
        "id",
        "at",
        "season",
        "week",
        "state",
        "kind",
        "lineup",
        "slot",
        "chosen",
        "alternatives",
        "default_at_time",
        "reason",
        "note",
        "source",
        "undo_of",
    ):
        assert key in one, key
    assert one["source"] == "page" and one["undo_of"] is None


def test_an_undone_decision_is_folded_out_and_extra_context_is_kept(tmp_path):
    path = tmp_path / "decisions.jsonl"
    a = dl.append(rec(row="7:status"), path)
    dl.append(rec("keep"), path)
    dl.undo(a, season=2026, week=11, path=path)
    assert [d["kind"] for d in dl.read(2026, 11, path)] == ["keep"]
    assert dl.lines(path)[0]["row"] == "7:status"


def test_an_unknown_kind_is_refused_and_a_missing_file_reads_empty(tmp_path):
    with pytest.raises(ValueError):
        dl.append(rec("delete"), tmp_path / "d.jsonl")
    assert dl.read(path=tmp_path / "none.jsonl") == []
