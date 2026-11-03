"""Test-wide safety nets.

**No test may touch the real `data/`.** In a worktree `data/` is a symlink to the main checkout's, which
holds the live week's state (`suggest_state/`, the pools, the contest log, the DK downloads); a test that
wrote there once overwrote Week 6's real Suggested round history with synthetic builds (audit, Nov 2026).
Two layers:

- `_hermetic_data_dir` (autouse) points every `dfs` path under `data/` at the test's own `tmp_path /
  "data"`: module constants (`paths.DATA_DIR`, `suggest_state.STATE_DIR`, `pool_store.POOL_DIR`, ...), the
  same paths imported by name into other modules, and functions' default arguments. Both kinds of data
  path count: the absolute ones under `paths.DATA_DIR` and the relative `Path("data/...")` ones (which
  resolve against the working directory, the repo root under pytest). Every `dfs` module is imported once
  at session start, so the list is complete; `tests/test_hermetic_data.py` fails if a module keeps a data
  path somewhere this misses.
- The guard: an audit hook (`sys.addaudithook`) fails any in-process write, create, rename or delete under
  the real `data/` while the tests run, naming the path. At the end of the session a snapshot of `data/`
  (or its absence) is compared too, which also sees child processes; a difference is printed loudly and
  fails the run.
- **No test may open the network** (Plan D item 9): the same audit hook fails any `socket.connect` or
  `socket.getaddrinfo` to anything but this machine (localhost, a Unix socket), naming the host. A test that
  needs data from a service fakes the call; in the cloud a real call got a 403 and failed.

The guard stays off outside a test's own body (`pytest_runtest_call`), so collection and fixtures that only
import are unaffected; a fixture that reaches the network is caught by its test."""

from __future__ import annotations

import importlib
import inspect
import os
import pkgutil
import sys
from pathlib import Path

import pytest

import dfs
from dfs import paths

REAL_DATA_DIR = paths.DATA_DIR
_REAL_ROOTS = tuple({os.path.abspath(REAL_DATA_DIR), os.path.realpath(REAL_DATA_DIR)})


def data_relative(value: object) -> Path | None:
    """`value`'s path inside the data directory (`Path(".")` for the directory itself), or None when `value`
    is not a data path."""
    if not isinstance(value, Path):
        return None
    if value.is_absolute():
        if value == REAL_DATA_DIR or REAL_DATA_DIR in value.parents:
            return value.relative_to(REAL_DATA_DIR)
        return None
    parts = value.parts
    return Path(*parts[1:]) if parts and parts[0] == "data" else None


def _defaults_with_data_paths(func) -> bool:
    values = list(func.__defaults__ or ()) + list((func.__kwdefaults__ or {}).values())
    return any(data_relative(v) is not None for v in values)


def import_all_dfs_modules() -> None:
    for info in pkgutil.walk_packages(dfs.__path__, "dfs."):
        importlib.import_module(info.name)


def data_path_references() -> list[tuple[object, str, str]]:
    """Every (owner, attribute, kind) in a `dfs` module that holds a data path: kind is "attr" (a module or
    class attribute) or "defaults" (a function's default arguments)."""
    found: list[tuple[object, str, str]] = []
    for name, mod in list(sys.modules.items()):
        if not (name == "dfs" or name.startswith("dfs.")) or mod is None:
            continue
        for attr, value in list(vars(mod).items()):
            if data_relative(value) is not None:
                found.append((mod, attr, "attr"))
            elif inspect.isfunction(value) and value.__module__ == name and _defaults_with_data_paths(value):
                found.append((value, attr, "defaults"))
            elif inspect.isclass(value) and value.__module__ == name:
                for cattr, cvalue in list(vars(value).items()):
                    if data_relative(cvalue) is not None:
                        found.append((value, cattr, "attr"))
                    func = getattr(cvalue, "__func__", cvalue)
                    if inspect.isfunction(func) and _defaults_with_data_paths(func):
                        found.append((func, cattr, "defaults"))
    return found


def _redirect(value: object, fake_root: Path) -> object:
    rel = data_relative(value)
    return value if rel is None else fake_root / rel


# --- the guard ---------------------------------------------------------------------------------------------

_GUARD = {"on": False}
# audit event -> the positions of its path arguments that get written
_WRITE_EVENTS = {
    "os.rename": (0, 1),
    "os.replace": (0, 1),
    "os.remove": (0,),
    "os.unlink": (0,),
    "os.rmdir": (0,),
    "os.mkdir": (0,),
    "os.truncate": (0,),
    "os.chmod": (0,),
    "os.utime": (0,),
    "os.symlink": (1,),
    "os.link": (1,),
    "shutil.copyfile": (1,),
    "shutil.copytree": (1,),
    "shutil.move": (0, 1),
    "shutil.rmtree": (0,),
}


def under_real_data(raw: object) -> bool:
    if raw is None or isinstance(raw, int):
        return False
    try:
        text = os.fsdecode(raw)
    except TypeError:
        return False
    candidates = {os.path.abspath(text), os.path.realpath(text)}
    return any(c == root or c.startswith(root + os.sep) for c in candidates for root in _REAL_ROOTS)


def _opens_for_write(args: tuple) -> bool:
    mode = args[1] if len(args) > 1 else None
    flags = args[2] if len(args) > 2 else 0
    if isinstance(mode, str) and any(ch in mode for ch in "wax+"):
        return True
    return bool((flags or 0) & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND))


_LOCAL_HOSTS = frozenset({"localhost", "127.0.0.1", "::1", "0.0.0.0", ""})


def _is_local(address: object) -> bool:
    if isinstance(address, (str, bytes)):  # a Unix socket path
        return True
    host = address[0] if isinstance(address, tuple) and address else address
    if isinstance(host, bytes):
        host = host.decode(errors="replace")
    return host is None or str(host).lower() in _LOCAL_HOSTS or str(host).startswith("127.")


def _audit(event: str, args: tuple) -> None:
    if not _GUARD["on"]:
        return
    if event in ("socket.connect", "socket.getaddrinfo"):
        address = args[1] if event == "socket.connect" else (args[0],)  # getaddrinfo: a host name
        if not _is_local(address):
            raise PermissionError(
                f"a test tried to reach the network ({event} {address!r}); fake the call instead "
                "(tests/conftest.py blocks every non-local connection)"
            )
        return
    if event == "open":
        targets = [args[0]] if args and _opens_for_write(args) else []
    elif event in _WRITE_EVENTS:
        targets = [args[i] for i in _WRITE_EVENTS[event] if i < len(args)]
    else:
        return
    for target in targets:
        if under_real_data(target):
            raise PermissionError(
                f"a test tried to write the real data directory ({event} {os.fsdecode(target)}); "
                "use tmp_path (tests/conftest.py points every dfs data path there)"
            )


def _snapshot(root: Path) -> dict[str, tuple[int, int]] | None:
    """{relative path: (size, mtime_ns)} for every file under `root`, or None when it doesn't exist."""
    if not root.exists():
        return None
    snap: dict[str, tuple[int, int]] = {}
    for dirpath, _dirnames, filenames in os.walk(root, followlinks=True):
        for fn in filenames:
            full = os.path.join(dirpath, fn)
            try:
                st = os.stat(full)
            except OSError:
                continue
            snap[os.path.relpath(full, root)] = (st.st_size, st.st_mtime_ns)
    return snap


def snapshot_changes(before, after) -> list[str]:
    if before is None and after is None:
        return []
    if before is None:
        return [f"created {REAL_DATA_DIR} ({len(after)} file(s) in it)"]
    if after is None:
        return [f"removed {REAL_DATA_DIR}"]
    changes = [f"new: {p}" for p in sorted(after.keys() - before.keys())]
    changes += [f"deleted: {p}" for p in sorted(before.keys() - after.keys())]
    changes += [f"changed: {p}" for p in sorted(before.keys() & after.keys()) if before[p] != after[p]]
    return changes


_STATE: dict = {}


def pytest_sessionstart(session):
    import_all_dfs_modules()
    _STATE["refs"] = data_path_references()
    _STATE["before"] = _snapshot(REAL_DATA_DIR)
    sys.addaudithook(_audit)
    _GUARD["on"] = True


def pytest_sessionfinish(session, exitstatus):
    _GUARD["on"] = False
    if "before" not in _STATE:
        return
    changes = snapshot_changes(_STATE["before"], _snapshot(REAL_DATA_DIR))
    _STATE["changes"] = changes
    if changes:
        session.exitstatus = pytest.ExitCode.TESTS_FAILED


def pytest_terminal_summary(terminalreporter):
    changes = _STATE.get("changes")
    if not changes:
        return
    terminalreporter.section("THE REAL data/ CHANGED DURING THE TEST RUN", sep="!", red=True, bold=True)
    terminalreporter.write_line(
        "A test (or a child process one started) wrote under the real data directory -- or something else on "
        "this machine did while the tests ran (a `dfs` command in another session). Changes:"
    )
    for line in changes[:40]:
        terminalreporter.write_line(f"  {line}")
    if len(changes) > 40:
        terminalreporter.write_line(f"  ... and {len(changes) - 40} more")


@pytest.fixture(autouse=True)
def _hermetic_data_dir(tmp_path, monkeypatch):
    """Every dfs data path -> `tmp_path / "data"` (see the module docstring). Returns that directory."""
    fake_root = tmp_path / "data"
    for owner, attr, kind in _STATE.get("refs", ()):
        if kind == "attr":
            monkeypatch.setattr(owner, attr, _redirect(getattr(owner, attr), fake_root))
            continue
        if owner.__defaults__:
            monkeypatch.setattr(
                owner, "__defaults__", tuple(_redirect(v, fake_root) for v in owner.__defaults__)
            )
        if owner.__kwdefaults__:
            monkeypatch.setattr(
                owner, "__kwdefaults__", {k: _redirect(v, fake_root) for k, v in owner.__kwdefaults__.items()}
            )
    return fake_root


@pytest.fixture(autouse=True)
def _no_week_page_regeneration(monkeypatch):
    """A CLI command run in a test never regenerates the Week page. The run log (`data/runlog.csv`, written by
    the CLI entry point after EVERY command) and the page's files (`data/week/`) need no override of their
    own: they resolve `paths.DATA_DIR` at call time, which `_hermetic_data_dir` points at the test's tmp dir."""
    from dfs import week_page

    monkeypatch.setattr(week_page, "AUTO_REGENERATE", False)
