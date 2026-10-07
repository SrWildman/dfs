"""`dfs model ...`: registered but hidden, real exit codes, no network or sheet."""

import re

import typer.main
from typer.testing import CliRunner

from dfs.cli import app
from dfs.model import data

runner = CliRunner()


def test_model_is_registered_and_hidden_from_the_main_help():
    command = typer.main.get_command(app)
    assert command.commands["model"].hidden is True
    assert set(command.commands["model"].commands) == {"fetch", "train", "backtest", "info"}
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    # no command row named `model` (the word appears only inside `results`' wrapped "(Model Check)")
    assert not re.search(r"^[│\s]*model\s{2,}", result.output, re.MULTILINE | re.IGNORECASE)


def test_model_help_still_works_when_typed():
    result = runner.invoke(app, ["model", "--help"])
    assert result.exit_code == 0
    for command in ("fetch", "train", "backtest", "info"):
        assert command in result.output


def test_info_describes_the_committed_artifacts():
    result = runner.invoke(app, ["model", "info"])
    assert result.exit_code == 0, result.output
    for word in ("training seasons", "scikit-learn", "QB", "DST", "compatible"):
        assert word in result.output


def test_info_fails_with_the_fix_when_the_artifacts_are_stale(monkeypatch):
    import dfs.model.artifacts as A

    real = A.read_metadata()
    monkeypatch.setattr(A, "read_metadata", lambda *a, **k: {**real, "sklearn_version": "0.0.1"})
    result = runner.invoke(app, ["model", "info"])
    assert result.exit_code == 1
    assert "FAIL" in result.output and "dfs model train" in result.output


def test_saved_backtest_prints_the_shipped_report():
    result = runner.invoke(app, ["model", "backtest", "--saved"])
    assert result.exit_code == 0 and "Holdout" in result.output and "Reliability" in result.output


def test_fetch_failure_is_a_failed_exit_code(monkeypatch):
    def boom(*args, **kwargs):
        raise data.ModelDataError("Request for games.parquet failed: no route")

    monkeypatch.setattr(data, "fetch_history", boom)
    result = runner.invoke(app, ["model", "fetch"])
    assert result.exit_code == 1 and "FAIL" in result.output and "no route" in result.output


def test_train_and_backtest_without_a_cache_say_to_fetch(monkeypatch):
    import dfs.model.predict as predict

    monkeypatch.setattr(predict, "available_seasons", lambda through: [])
    for command in ("train", "backtest"):
        result = runner.invoke(app, ["model", command])
        assert result.exit_code == 1, command
        assert "dfs model fetch" in result.output
