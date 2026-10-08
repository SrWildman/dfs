"""`dfs sim ...`: registered and visible, help works with no network, cache or sheet."""

import re

import typer.main
from typer.testing import CliRunner

from dfs.cli import app

runner = CliRunner()


def test_sim_is_registered_and_listed_in_the_main_help():
    command = typer.main.get_command(app)
    assert not command.commands["sim"].hidden
    assert set(command.commands["sim"].commands) == {"fit", "backtest", "demo"}
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert re.search(r"^[│\s]*sim\s{2,}", result.output, re.MULTILINE)


def test_sim_help_lists_the_commands():
    result = runner.invoke(app, ["sim", "--help"])
    assert result.exit_code == 0
    for command in ("fit", "backtest", "demo"):
        assert command in result.output


def test_fit_says_what_to_do_when_the_history_is_not_cached(monkeypatch, tmp_path):
    import dfs.model.data as data

    monkeypatch.setattr(data, "CACHE_DIR", tmp_path)
    result = runner.invoke(app, ["sim", "fit"])
    assert result.exit_code == 1
    assert "dfs model fetch" in result.output
