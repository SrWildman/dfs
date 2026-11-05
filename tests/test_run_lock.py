"""One writer at a time (`run_lock.py`): a second writer is refused, readers pass, a dead owner is cleared."""

from __future__ import annotations

import fcntl
import json
import os
import subprocess
import sys
from datetime import UTC, datetime

import pytest
import typer
from typer.testing import CliRunner

from dfs import commands_doc, run_lock
from dfs.prompt_missing import PromptingGroup


def _other_holder(pid: int, command: str = "dfs sync", origin: str = "terminal") -> int:
    """Hold the lock the way another process would (a second open file description conflicts even inside
    this one) with `pid` in the file; the descriptor, to close when done."""
    path = run_lock.lock_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o644)
    fcntl.flock(fd, fcntl.LOCK_EX)
    record = {"pid": pid, "command": command, "since": datetime.now(UTC).isoformat(), "origin": origin}
    os.write(fd, json.dumps(record).encode())
    return fd


def _dead_pid() -> int:
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait()
    return proc.pid


def test_every_registered_command_is_classified_once():
    commands = set(commands_doc.all_commands()) | {"dfs week serve"}
    assert not run_lock.WRITERS & run_lock.READ_ONLY
    unclassified = commands - run_lock.WRITERS - run_lock.READ_ONLY
    assert not unclassified, (
        f"say whether these write (run_lock.WRITERS) or only read: {sorted(unclassified)}"
    )
    assert not (run_lock.WRITERS | run_lock.READ_ONLY) - commands - {"dfs contests history"}


def test_the_commands_named_for_the_lock_take_it():
    for command in ("dfs sync", "dfs week new", "dfs week close", "dfs setup sheet", "dfs lineups suggest"):
        assert run_lock.takes_lock(command)
    for command in ("dfs entries record", "dfs entries fill", "dfs bankroll sync", "dfs season sync"):
        assert run_lock.takes_lock(command)
    assert run_lock.takes_lock("dfs contests plan")
    for command in ("dfs status", "dfs doctor", "dfs week page", "dfs contests history"):
        assert not run_lock.takes_lock(command)


def test_the_deprecated_sheets_spelling_is_the_setup_command():
    assert run_lock.takes_lock("dfs sheets cockpit")
    assert not run_lock.takes_lock("dfs sheets inspect")
    assert run_lock.takes_lock("dfs sync --live")  # options are not part of the name


def test_a_free_lock_is_taken_and_released():
    assert run_lock.current_holder() is None
    with run_lock.hold("dfs sync --live", origin_name="button") as record:
        assert record["command"] == "dfs sync" and record["origin"] == "button"
        assert os.environ[run_lock.INHERIT_ENV] == str(os.getpid())
    assert run_lock.INHERIT_ENV not in os.environ
    assert run_lock.current_holder() is None
    with run_lock.hold("dfs sync"):  # and it can be taken again
        pass


def test_the_lock_file_is_removed_when_a_run_ends_normally_with_an_error_or_with_ctrl_c():
    """Week 9: `data/.run.lock` still named `dfs pool add` (pid 67700) long after it finished."""
    path = run_lock.lock_path()
    with run_lock.hold("dfs pool add"):
        assert json.loads(path.read_text())["command"] == "dfs pool add"
    assert not path.exists()
    for exc in (RuntimeError("boom"), KeyboardInterrupt(), SystemExit(2)):
        with pytest.raises(type(exc)), run_lock.hold("dfs sync"):
            assert path.exists()
            raise exc
        assert not path.exists(), f"left behind after {type(exc).__name__}"
    assert run_lock.current_holder() is None


def test_a_run_that_lets_go_never_removes_a_newer_holders_file():
    """If the file was replaced while we held ours (a dead-owner replacement elsewhere), it is not ours to
    remove."""
    path = run_lock.lock_path()
    with run_lock.hold("dfs sync"):
        path.unlink()
        fd = _other_holder(os.getppid(), "dfs week close")
    try:
        assert path.exists() and run_lock.current_holder().command == "dfs week close"
    finally:
        os.close(fd)


def test_a_lock_taken_on_a_file_that_was_just_removed_is_retried_on_the_new_one(monkeypatch):
    """Removing the file on release opens a race: a run that opened the old file, then flocks it after the
    holder let go, holds a lock on nothing. `_try_flock` sees the path names another file and tries again."""
    path = run_lock.lock_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    real_flock, swapped = fcntl.flock, []

    def flock(fd, op):
        if not swapped:  # between our open and our flock, the holder removed it and a new run made another
            swapped.append(True)
            path.unlink()
            path.write_text("")
        return real_flock(fd, op)

    monkeypatch.setattr(run_lock.fcntl, "flock", flock)
    fd = run_lock._try_flock(path)
    assert fd is not None and os.fstat(fd).st_ino == os.stat(path).st_ino
    os.close(fd)


def test_a_second_writer_is_refused_and_told_what_is_running():
    fd = _other_holder(os.getppid(), "dfs week close", origin="chat")
    try:
        holder = run_lock.current_holder()
        assert holder and holder.command == "dfs week close" and holder.origin == "chat"
        with pytest.raises(run_lock.LockHeld) as refused, run_lock.hold("dfs sync"):
            pass
        message = str(refused.value)
        assert "`dfs week close` is running" in message and "chat" in message and "started" in message
        assert str(os.getppid()) in message
    finally:
        os.close(fd)
    assert run_lock.current_holder() is None


def test_a_dead_owners_lock_is_cleared_and_replaced():
    dead = _dead_pid()
    fd = _other_holder(dead)
    try:
        assert run_lock.current_holder() is None  # a dead pid is no holder
        with run_lock.hold("dfs sync") as record:
            assert record["pid"] == os.getpid()
    finally:
        os.close(fd)


def test_a_lock_file_left_behind_by_a_finished_run_is_free():
    fd = _other_holder(os.getpid())
    os.close(fd)  # the OS dropped the lock; the text stays
    assert run_lock.current_holder() is None
    with run_lock.hold("dfs sync"):
        pass


def test_a_child_of_the_holder_passes_through(monkeypatch):
    with run_lock.hold("dfs week close"):
        # what a `dfs` started by the holder sees: its parent's pid in the environment, the lock held
        monkeypatch.setenv(run_lock.INHERIT_ENV, str(os.getpid()))
        with run_lock.hold("dfs research refresh") as record:
            assert record is None


def test_a_stale_inherit_variable_does_not_bypass_a_real_holder(monkeypatch):
    fd = _other_holder(os.getppid())
    try:
        monkeypatch.setenv(run_lock.INHERIT_ENV, str(_dead_pid()))
        with pytest.raises(run_lock.LockHeld), run_lock.hold("dfs sync"):
            pass
    finally:
        os.close(fd)


def test_child_env_names_the_origin_and_drops_the_inherited_lock(monkeypatch):
    monkeypatch.setenv(run_lock.INHERIT_ENV, "123")
    env = run_lock.child_env("button")
    assert env[run_lock.ORIGIN_ENV] == "button" and run_lock.INHERIT_ENV not in env


# --- the CLI entry point ------------------------------------------------------------------------------


@pytest.fixture
def mini_cli():
    app = typer.Typer(cls=PromptingGroup)
    ran: list[str] = []

    @app.command()
    def sync():
        ran.append("sync")

    @app.command()
    def status():
        ran.append("status")

    return app, ran


def test_the_cli_refuses_a_writer_while_another_runs_but_lets_a_reader_pass(mini_cli):
    app, ran = mini_cli
    fd = _other_holder(os.getppid(), "dfs week close")
    try:
        runner = CliRunner()
        refused = runner.invoke(app, ["sync"])
        assert refused.exit_code == 1 and ran == []
        assert "`dfs week close` is running" in refused.output
        assert runner.invoke(app, ["status"]).exit_code == 0 and ran == ["status"]
    finally:
        os.close(fd)
    assert CliRunner().invoke(app, ["sync"]).exit_code == 0 and ran == ["status", "sync"]


def test_the_cli_holds_the_lock_while_a_writer_runs():
    app = typer.Typer(cls=PromptingGroup)
    seen: list = []

    @app.command()
    def sync():
        seen.append(run_lock.current_holder())

    @app.command()
    def status():
        seen.append(run_lock.current_holder())

    runner = CliRunner()
    runner.invoke(app, ["status"])
    assert seen == [None]  # a reader takes nothing
    runner.invoke(app, ["sync"])
    assert seen[-1] is not None and seen[-1].pid == os.getpid() and seen[-1].command == "dfs sync"
    assert run_lock.current_holder() is None  # released afterwards
    assert not run_lock.lock_path().exists()  # and the file is gone


def test_the_cli_removes_the_lock_file_when_a_writer_fails():
    app = typer.Typer(cls=PromptingGroup)

    @app.command()
    def sync():
        raise RuntimeError("the sheet said no")

    @app.command()
    def status():
        pass

    assert CliRunner().invoke(app, ["sync"]).exit_code != 0
    assert not run_lock.lock_path().exists()
