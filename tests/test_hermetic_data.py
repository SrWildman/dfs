"""The guard on the real `data/` (see `tests/conftest.py`): every dfs data path points into the test's
tmp_path, and a write under the real data directory fails the test that makes it."""

from __future__ import annotations

import inspect
import os
import sys
from pathlib import Path

import pytest
from conftest import (
    REAL_DATA_DIR,
    data_path_references,
    data_relative,
    snapshot_changes,
    under_real_data,
)

from dfs import paths, pool_store, suggest_state


def test_every_dfs_data_path_points_into_tmp_path(tmp_path):
    fake = tmp_path / "data"
    assert paths.DATA_DIR == fake
    assert suggest_state.STATE_DIR == fake / "suggest_state"  # the one that overwrote Week 6's real history
    assert pool_store.POOL_DIR == fake / "pools"  # a relative Path("data/pools") counts too
    leftovers = [f"{getattr(o, '__name__', o)}.{a} ({k})" for o, a, k in data_path_references()]
    assert leftovers == []


def test_no_module_keeps_a_data_path_inside_a_container():
    """The fixture redirects attributes and default arguments, not paths tucked inside a tuple, list or dict:
    keep data paths as plain module constants so it can reach them."""
    found = []
    for name, mod in list(sys.modules.items()):
        if not name.startswith("dfs.") or mod is None:
            continue
        for attr, value in vars(mod).items():
            if inspect.ismodule(value) or not isinstance(value, (tuple, list, dict, set, frozenset)):
                continue
            items = list(value.values()) + list(value.keys()) if isinstance(value, dict) else list(value)
            if any(data_relative(v) is not None for v in items):
                found.append(f"{name}.{attr}")
    assert found == []


def test_writing_the_real_data_directory_fails_the_test():
    probe = (
        REAL_DATA_DIR / "_hermetic_probe_does_not_exist" / "x.json"
    )  # never created even if the guard broke
    with pytest.raises(PermissionError, match="real data directory"):
        probe.write_text("{}")
    with pytest.raises(PermissionError, match="real data directory"):
        os.rename(probe, probe.with_suffix(".bak"))
    with pytest.raises(PermissionError, match="real data directory"):
        open(Path("data") / "_hermetic_probe_does_not_exist" / "y.csv", "a")  # noqa: SIM115 -- relative to the repo root


def test_reading_and_tmp_writes_are_untouched(tmp_path):
    (tmp_path / "ok.txt").write_text("fine")
    assert (tmp_path / "ok.txt").read_text() == "fine"
    assert not under_real_data(tmp_path / "ok.txt")
    assert under_real_data(REAL_DATA_DIR / "suggest_state" / "9.json")


def test_snapshot_changes_names_what_moved():
    before = {"a.json": (1, 10), "b.csv": (2, 20)}
    after = {"a.json": (1, 11), "c.csv": (3, 30)}
    assert snapshot_changes(before, after) == ["new: c.csv", "deleted: b.csv", "changed: a.json"]
    assert snapshot_changes(None, None) == []
    assert snapshot_changes(None, {"x": (1, 1)})[0].startswith("created ")
    assert snapshot_changes(before, before) == []
