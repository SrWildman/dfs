"""`dfs research ...`: registered hidden, validates its argument, and names `dfs research fetch` when the
cache is empty. `run` never touches the network."""

import httpx
import pytest
from typer.testing import CliRunner

from dfs.cli import app
from dfs.model import data as model_data
from dfs.research import data, pipeline

runner = CliRunner()


def test_research_is_registered_but_hidden_from_the_top_level_help():
    top = runner.invoke(app, ["--help"])
    assert top.exit_code == 0 and "research" not in top.output.lower().replace("researching", "")
    sub = runner.invoke(app, ["research", "--help"])
    assert sub.exit_code == 0
    assert "fetch" in sub.output and "run" in sub.output


def test_study_names():
    assert pipeline.resolve("all") == ["r1", "r2", "r3", "r4", "r5"]
    assert pipeline.resolve("R3") == ["r3"]
    with pytest.raises(ValueError, match="unknown study"):
        pipeline.resolve("r9")


def test_an_unknown_study_fails_cleanly():
    result = runner.invoke(app, ["research", "run", "--study", "r9"])
    assert result.exit_code == 1 and "unknown study" in result.output


@pytest.fixture
def empty_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(data, "CACHE_DIR", tmp_path / "research")
    monkeypatch.setattr(model_data, "CACHE_DIR", tmp_path / "model")

    def no_network(*args, **kwargs):
        raise AssertionError("`dfs research run` must not use the network")

    monkeypatch.setattr(httpx, "get", no_network)


@pytest.mark.parametrize("study", ["r1", "r2", "r3", "r4", "r5"])
def test_run_with_an_empty_cache_names_the_fetch_command(empty_cache, study):
    result = runner.invoke(app, ["research", "run", "--study", study])
    assert result.exit_code == 1
    assert "dfs research fetch" in result.output and "dfs model fetch" not in result.output
