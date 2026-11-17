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
    for candidate, baseline in close_research.CALLS_PAIRS:
        loaded = {}
        for stem in (candidate, baseline):
            path = close_research.SETTINGS_DIR / f"{stem}.toml"
            assert path.exists(), path
            loaded[stem] = backtest_settings.load(path)
        assert loaded[baseline].active_flags() == [] and loaded[candidate].active_flags()
        assert loaded[candidate].name != loaded[baseline].name  # a comparison needs distinct names


def test_a_stage_that_raises_still_leaves_a_failed_result(dirs, monkeypatch):
    def boom(season, week):
        raise RuntimeError("no standings")

    monkeypatch.setitem(close_research.STAGE_FUNCTIONS, "stacks", boom)
    out = dirs / "stacks.json"
    assert close_research.run_stage("stacks", 2026, 7, out) == 1
    (result,) = [StageResult(**r) for r in json.loads(out.read_text())]
    assert result.status == "failed" and "no standings" in result.verdict


@pytest.mark.slow
def test_stages_run_side_by_side_and_a_late_one_is_stopped_at_the_deadline(dirs, monkeypatch):
    def command(name, season, week, out):
        if name == "calls_flashback":  # never finishes on its own
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
        2026, 7, ("stacks", "calls_flashback", "calls"), deadline_minutes=0.05, log=lines.append
    )
    assert time.monotonic() - start < 30
    by_key = {r.key: r for r in results}
    assert by_key["stacks"].status == "ok" and by_key["stacks"].verdict == "fine"
    assert by_key["calls"].status == "ok"
    assert by_key["calls_flashback"].status == "timeout" and "deadline" in by_key["calls_flashback"].verdict
    assert [r.key for r in results] == ["stacks", "calls_flashback", "calls"]  # stage order, not finish order


@pytest.mark.slow
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


def test_the_calls_flashback_pairs_load_and_compare_like_with_like():
    for candidate, baseline in close_research.CALLS_PAIRS:
        a = backtest_settings.load(close_research.SETTINGS_DIR / f"{candidate}.toml")
        b = backtest_settings.load(close_research.SETTINGS_DIR / f"{baseline}.toml")
        assert a.name != b.name and a.flags.get("calls") in ("avoid", "fade") and not b.flags
        assert (a.cash, a.gpp) == (b.cash, b.gpp)  # same kind of builds on both sides
    assert "calls_flashback" in close_research.STAGE_NAMES
    assert close_research.STAGE_FUNCTIONS["calls_flashback"] is close_research.stage_calls_flashback


# -- blueprint Phase D: the scorecards weekly, everything else monthly ---


def test_the_monthly_set_runs_on_the_first_close_of_a_month_only():
    # the Monday after each Sunday: Week 4 (Oct 5), Week 8 (Nov 2), Week 13 (Dec 7), Week 17 (Jan 4)
    firsts = [w for w in range(2, 19) if close_research.first_close_of_month(2026, w)]
    assert firsts == [4, 8, 13, 17]
    assert close_research.stages_for(2026, 9) == (("scorecards",), ())
    assert close_research.stages_for(2026, 9, monthly=True) == (("scorecards",), close_research.STAGE_NAMES)
    assert close_research.stages_for(2026, 8)[1] == close_research.MONTHLY
    assert (
        close_research.ALL_STAGES[0] == "scorecards" and close_research.MONTHLY == close_research.STAGE_NAMES
    )


def test_a_plain_run_is_the_weekly_set_and_monthly_adds_the_rest_after_it(dirs, monkeypatch):
    calls = []

    def fake(season, week, stages, *, deadline_minutes, log):
        calls.append((tuple(stages), deadline_minutes))
        return [_result(s) for s in stages]

    monkeypatch.setattr(close_research, "run_stages", fake)
    _path, results, _wall = close_research.run(2026, 9, log=lambda m: None)
    assert [c[0] for c in calls] == [("scorecards",)] and [r.key for r in results] == ["scorecards"]
    calls.clear()
    close_research.run(2026, 9, monthly=True, log=lambda m: None)
    assert [c[0] for c in calls] == [("scorecards",), close_research.STAGE_NAMES]
    assert calls[1][1] <= close_research.DEADLINE_MINUTES  # the same deadline, what is left of it
    calls.clear()
    close_research.run(2026, 9, stages=("stacks",), log=lambda m: None)
    assert [c[0] for c in calls] == [("stacks",)]


def test_the_scorecards_stage_is_four_results_and_the_report_lists_them(dirs, monkeypatch):
    from dfs import week_review

    cards = [
        {"model": m, "week": "w", "season": "s", "baseline": "b", "weeks": 6, "status": st}
        for m, st in (
            ("Projection (CalPts vs TFFB)", "Proven"),
            ("Ownership (GPP, cash)", "Not proven"),
            ("Cash % calibration", "Not proven"),
            ("Edge at lock", "Proven"),
        )
    ]
    cards[2]["gap"] = 0.17
    monkeypatch.setattr(week_review, "scorecard_inputs", lambda *a, **k: (cards, [], [], {}))
    results = close_research.stage_scorecards(2026, 9)
    assert [r.key for r in results] == [
        "scorecards.projection",
        "scorecards.ownership",
        "scorecards.calibration",
        "scorecards.edge_at_lock",
    ]
    assert results[0].verdict == "Proven: s" and results[2].metrics["gap"] == 0.17
    text = close_research.render(
        results, 2026, 9, close_text=None, decisions=None, previous=None, wall_seconds=60.0
    )
    for card in cards:
        assert f"Scorecard: {card['model']}" in text
