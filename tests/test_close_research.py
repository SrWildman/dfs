"""`dfs week close --research`: the stages, the deadline, the one report and its 15-line summary."""

import json
import sys
import time
from pathlib import Path

import pytest

from dfs import backtest_settings, close_research
from dfs.close_research import StageResult


@pytest.fixture
def dirs(tmp_path, monkeypatch):
    monkeypatch.setattr(close_research, "RUN_DIR", tmp_path / "run")
    monkeypatch.setattr(close_research, "REPORT_DIR", tmp_path / "reports")
    return tmp_path


def _result(key, verdict="ok", **kw):
    return StageResult(key=key, title=f"Title {key}", verdict=verdict, seconds=60.0, **kw)


def test_the_settings_files_live_in_the_repo_and_load_with_their_flags():
    names = {}
    for stem in (close_research.BASELINE, *close_research.CANDIDATES):
        path = close_research.SETTINGS_DIR / f"{stem}.toml"
        assert path.exists(), path
        settings = backtest_settings.load(path)
        names[stem] = settings.name
        assert [f.name for f in settings.active_flags()] == ([] if stem == "today" else [stem])
    assert len(set(names.values())) == len(names)  # a comparison needs distinct names


def test_a_stage_that_raises_still_leaves_a_failed_result(dirs, monkeypatch):
    def boom(season, week):
        raise RuntimeError("no standings")

    monkeypatch.setitem(close_research.STAGE_FUNCTIONS, "stacks", boom)
    out = dirs / "stacks.json"
    assert close_research.run_stage("stacks", 2026, 7, out) == 1
    (result,) = [StageResult(**r) for r in json.loads(out.read_text())]
    assert result.status == "failed" and "no standings" in result.verdict


def test_stages_run_side_by_side_and_a_late_one_is_stopped_at_the_deadline(dirs, monkeypatch):
    def command(name, season, week, out):
        if name == "backtest":  # never finishes on its own
            return [sys.executable, "-c", "import time; time.sleep(60)"]
        body = (
            "import json, sys; from dfs.close_research import StageResult; from dataclasses import asdict; "
            f"result = StageResult(key={name!r}, title='t', verdict='fine'); "
            f"open({str(out)!r}, 'w').write(json.dumps([asdict(result)]))"
        )
        return [sys.executable, "-c", body]

    monkeypatch.setattr(close_research, "_stage_command", command)
    lines = []
    start = time.monotonic()
    results = close_research.run_stages(
        2026, 7, ("stacks", "backtest", "own_error"), deadline_minutes=0.05, log=lines.append
    )
    assert time.monotonic() - start < 30
    by_key = {r.key: r for r in results}
    assert by_key["stacks"].status == "ok" and by_key["stacks"].verdict == "fine"
    assert by_key["own_error"].status == "ok"
    assert by_key["backtest"].status == "timeout" and "deadline" in by_key["backtest"].verdict
    assert [r.key for r in results] == ["stacks", "backtest", "own_error"]  # stage order, not finish order


def test_a_stage_that_dies_without_a_result_is_reported_not_raised(dirs, monkeypatch):
    monkeypatch.setattr(
        close_research,
        "_stage_command",
        lambda *a: [sys.executable, "-c", "print('boom'); raise SystemExit(3)"],
    )
    (result,) = close_research.run_stages(2026, 7, ("stacks",), deadline_minutes=1, log=lambda m: None)
    assert result.status == "failed" and "boom" in result.headline[0]


def test_the_report_quotes_the_close_compares_with_last_week_and_embeds_its_metrics(dirs):
    first = [_result("stacks", metrics={"combined_z": 1.0, "tested": False})]
    close_research.fill_changes(first, None)
    assert first[0].changed == ["no earlier close report."]
    path1 = close_research.report_path(2026, 7)
    path1.parent.mkdir(parents=True)
    path1.write_text(
        close_research.render(
            first, 2026, 7, close_text="bankroll ok", decisions=None, previous=None, wall_seconds=600
        )
    )
    prev = close_research.previous_report(2026, 8)
    assert prev is not None and prev[0] == path1 and prev[1]["stacks"]["combined_z"] == 1.0
    assert close_research.previous_report(2026, 7) is None  # only weeks before

    second = [_result("stacks", metrics={"combined_z": 2.0, "tested": True}), _result("gone")]
    close_research.fill_changes(second, prev)
    assert any("combined_z" in c for c in second[0].changed)
    assert any("tested" in c for c in second[0].changed)  # a flipped flag leads
    text = close_research.render(
        second,
        2026,
        8,
        close_text="Closing the week\nbankroll ok",
        decisions=["Finish 0.31", "Swing +4"],
        previous=prev,
        wall_seconds=1800,
    )
    for needle in (
        "bankroll ok",
        "Finish 0.31",
        "Changed since last week",
        "Ship bar" if False else "Title stacks",
    ):
        assert needle in text
    assert close_research.read_metrics(_write(dirs, text))["stacks"]["combined_z"] == 2.0


def _write(dirs, text) -> Path:
    path = dirs / "r.md"
    path.write_text(text)
    return path


def test_without_a_close_the_report_says_so():
    text = close_research.render(
        [_result("x")], 2026, 7, close_text=None, decisions=None, previous=None, wall_seconds=60
    )
    assert "was not run in this session" in text and "not read" in text


def test_the_summary_is_at_most_fifteen_lines_even_with_many_studies():
    results = [_result(f"refresh.s{i}", verdict="a verdict " * 30) for i in range(20)]
    lines = close_research.summary_lines(
        results, 2026, 7, Path("r-close.md"), 1200, decisions=[f"d{i}" for i in range(8)]
    )
    assert len(lines) <= close_research.SUMMARY_LINES
    assert lines[-1].startswith("report:") and lines[0].startswith("Week 7")
    assert sum(1 for line in lines if line.startswith("decision:")) == 5
    assert all(len(line) <= 200 for line in lines)


def test_the_decisions_block_is_the_five_lines_after_its_heading():
    class Client:
        def read_tab(self, tab):
            return [
                ["WHAT WINS"],
                [""],
                ["THIS WEEK, YOUR DECISIONS  -  Week 7"],
                ["Line one", "x"],
                ["Line two"],
                [""],
                ["after the gap"],
            ]

    assert close_research.decisions_from_tab(Client()) == ["Line one x", "Line two"]

    class Empty:
        def read_tab(self, tab):
            raise RuntimeError("no tab")

    assert close_research.decisions_from_tab(Empty()) is None
