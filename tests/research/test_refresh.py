"""`dfs research refresh`: fail-soft studies, the report with its embedded metrics, "changed since last week",
the ten-line summary and the command. The studies themselves are replaced by fakes; their own modules have
tests."""

import sys
import types

import pytest
from typer.testing import CliRunner

from dfs.cli import app
from dfs.research import refresh

runner = CliRunner()


def fake_study(key, metrics, status="ok", verdict="fine"):
    def run(ctx):
        return refresh.Study(
            key,
            f"Study {key}",
            "a bar",
            status=status,
            verdict=verdict,
            headline=[f"headline {key}"],
            metrics=metrics,
        )

    return run


@pytest.fixture
def two_studies(monkeypatch):
    monkeypatch.setattr(
        refresh,
        "STUDIES",
        {"a": fake_study("a", {"gain": 1.0, "ships": False}), "b": fake_study("b", {"mae": 5.0})},
    )
    monkeypatch.setattr(refresh, "TITLES", {"a": "Study a", "b": "Study b"})


def test_a_broken_study_is_reported_and_the_rest_still_run(monkeypatch):
    def boom(ctx):
        raise RuntimeError("cache missing")

    monkeypatch.setattr(refresh, "STUDIES", {"bad": boom, "good": fake_study("good", {"x": 1.0})})
    monkeypatch.setattr(refresh, "TITLES", {"bad": "Bad", "good": "Good"})
    out = refresh.run_studies(refresh.Context(2026, 6, 1))
    assert [s.status for s in out] == ["failed", "ok"]
    assert "RuntimeError: cache missing" in out[0].verdict and out[0].note
    assert all(s.seconds >= 0 for s in out)


def test_unknown_study_names_are_refused(two_studies):
    with pytest.raises(ValueError, match="unknown studies"):
        refresh.run_studies(refresh.Context(2026, 6, 1), ["a", "zzz"])


def test_changes_lists_flipped_flags_first_then_the_numbers_that_really_moved():
    old = {"gain": 1.00, "z": 2.0, "tiny": 0.0001, "ships": False, "same": 5.0}
    new = {"gain": 1.50, "z": 2.05, "tiny": 0.0002, "ships": True, "same": 5.0, "fresh": 3.0}
    got = refresh.changes(old, new)
    assert got[0] == "**ships: False -> True**"
    assert "gain: 1.000 -> 1.500" in got
    assert not any(g.startswith(("z:", "tiny:", "same:")) for g in got)  # under 5%, under 0.001, unchanged
    assert any(g.startswith("fresh: new") for g in got)
    assert refresh.changes({"n": 45}, {"n": 65}) == ["n: 45 -> 65"]  # counts stay whole numbers


def test_the_report_embeds_its_metrics_and_the_next_week_compares_against_them(two_studies, tmp_path):
    path5, studies, _ = refresh.refresh(2026, 5, directory=tmp_path)
    assert path5.name == "2026-wk05.md"
    assert refresh.read_metrics(path5) == {"a": {"gain": 1.0, "ships": False}, "b": {"mae": 5.0}}
    assert "no earlier report" in path5.read_text()
    refresh.STUDIES["a"] = fake_study("a", {"gain": 2.0, "ships": True})
    path6, _, _ = refresh.refresh(2026, 6, directory=tmp_path)
    text = path6.read_text()
    assert "Compared against `2026-wk05.md`" in text
    assert "**ships: False -> True**" in text and "gain: 1.000 -> 2.000" in text
    assert "nothing moved by more than 5%" in text  # study b is unchanged
    assert "Ship bar:** a bar" in text and "headline a" in text


def test_the_previous_report_is_the_newest_earlier_week_of_the_same_season(two_studies, tmp_path):
    for season, week in ((2025, 17), (2026, 3), (2026, 5), (2026, 8)):
        refresh.refresh(season, week, directory=tmp_path)
    prev = refresh.previous_report(2026, 6, tmp_path)
    assert prev is not None and prev[0].name == "2026-wk05.md"
    assert refresh.previous_report(2026, 3, tmp_path) is None


def test_the_summary_is_ten_lines_for_eight_studies(tmp_path):
    studies = [refresh.Study(k, k, "bar", verdict="v", seconds=1.0) for k in refresh.STUDIES]
    lines = refresh.summary_lines(studies, 12.0, tmp_path / "r.md", 2026, 6)
    assert len(refresh.STUDIES) == 8 and len(lines) == 10
    assert lines[0].startswith("research refresh 2026 through Week 6") and "shared history" in lines[0]
    assert lines[-1].strip().startswith("report:")


def test_the_contest_retro_is_skipped_until_the_module_exists_and_used_when_it_does(monkeypatch):
    ctx = refresh.Context(2026, 6, 1)
    skipped = refresh.study_contest_retro(ctx)
    assert skipped.status == "skipped" and "not on this branch" in skipped.verdict
    fake = types.ModuleType("dfs.sim.contest_retro")
    fake.refresh_summary = lambda season, through: {
        "headline": [f"retro {season} wk{through}"],
        "metrics": {"edge": 0.5},
        "ship_bar": "its own",
    }
    monkeypatch.setitem(sys.modules, "dfs.sim.contest_retro", fake)
    used = refresh.study_contest_retro(ctx)
    assert used.status == "ok" and used.metrics == {"edge": 0.5} and used.headline == ["retro 2026 wk6"]
    bare = types.ModuleType("dfs.sim.contest_retro")
    monkeypatch.setitem(sys.modules, "dfs.sim.contest_retro", bare)
    assert refresh.study_contest_retro(ctx).status == "skipped"


def test_the_command_is_registered_and_fails_cleanly_with_nothing_scored(monkeypatch):
    assert "refresh" in runner.invoke(app, ["research", "--help"]).output
    monkeypatch.setattr(refresh, "latest_scored_week", lambda season: None)
    result = runner.invoke(app, ["research", "refresh"])
    assert result.exit_code == 1 and "no scored weeks" in result.output


def test_the_command_prints_the_summary_and_writes_where_told(two_studies, monkeypatch, tmp_path):
    monkeypatch.setattr(refresh, "REPORT_DIR", tmp_path)
    monkeypatch.setattr(refresh, "latest_scored_week", lambda season: 6)
    result = runner.invoke(app, ["research", "refresh"])
    assert result.exit_code == 0, result.output
    assert (tmp_path / f"{refresh_season()}-wk06.md").exists()
    assert "research refresh" in result.output and "report:" in result.output


def test_the_command_refuses_an_unknown_study(two_studies, monkeypatch):
    monkeypatch.setattr(refresh, "latest_scored_week", lambda season: 6)
    result = runner.invoke(app, ["research", "refresh", "--only", "nope"])
    assert result.exit_code == 1 and "unknown studies" in result.output


def refresh_season():
    from dfs.nfl_calendar import current_season

    return current_season()


def test_the_latest_scored_week_is_read_from_the_results_files(monkeypatch, tmp_path):
    from dfs import results_loop

    monkeypatch.setattr(results_loop, "RESULTS_DIR", tmp_path)
    assert refresh.latest_scored_week(2026) is None
    for name in (
        "scored_2026_w01.csv",
        "scored_2026_w06.csv",
        "scored_2025_w17.csv",
        "unmatched_2026_w09.csv",
    ):
        (tmp_path / name).write_text("x")
    assert refresh.latest_scored_week(2026) == 6


def test_tail_metrics_read_the_real_column_names():
    import pandas as pd

    cov = pd.DataFrame({"position": ["QB", "RB"], "n": [10, 20], ">90": [10.234, 9.02], "z90": [0.3, -1.9]})
    judged = pd.DataFrame(
        {"group": ["RB2", "All"], "pinball_better": [0.0567, 0.0201], "clears_2se": [True, False]}
    )
    got = refresh.tail_metrics(cov, judged)
    assert got == {
        "QB >90": 10.23,
        "RB >90": 9.02,
        "RB2 pinball_better": 0.0567,
        "RB2 clears_2se": True,
        "All pinball_better": 0.0201,
        "All clears_2se": False,
    }


def test_ownership_metrics_compare_ours_with_tffb_per_format():
    import pandas as pd

    summary = pd.DataFrame(
        {
            "Format": ["gpp", "gpp", "cash", "cash"],
            "Model": ["TFFB Own%", "Ours", "TFFB Own%", "Ours"],
            "Weeks": [3, 3, 3, 3],
            "MAE": [0.030, 0.035, 0.050, 0.040],
            "Chalk bias": [-0.05, -0.06, float("nan"), -0.01],
        }
    )
    gaps = pd.DataFrame({"Week": ["5", "All"], "n": [50, 90], "Gap share": [0.4, 0.55]})
    got = refresh.ownership_metrics(summary, gaps)
    assert got["gpp ours beats TFFB"] is False and got["cash ours beats TFFB"] is True
    assert got["gpp Ours MAE"] == 0.035 and "cash TFFB Own% chalk bias" not in got
    assert got["gap share"] == 0.55
    assert refresh.ownership_metrics(summary, gaps.iloc[0:0]).get("gap share") is None


def test_props_metrics_read_the_all_markets_rows_by_band():
    import pandas as pd

    table = pd.DataFrame(
        {
            "Market": ["Pass yards", "All markets", "All markets", "All markets"],
            "Band": ["all gaps", "all gaps", "10+ pts", "15+ pts"],
            "n": [90, 400, 120, 0],
            "Won": [0.5, 0.56, 0.61, float("nan")],
            "Needed": [0.5, 0.51, 0.52, float("nan")],
            "Return": [0.0, 0.05, 0.12, float("nan")],
            "Verdict": ["n < 100", "beat the price", "no edge shown", "n < 100"],
        }
    )
    got = refresh.props_metrics(table)
    assert got["all gaps n"] == 400 and got["all gaps won"] == 0.56 and got["all gaps beat the price"] is True
    assert got["10+ pts beat the price"] is False and got["15+ pts n"] == 0
    assert "15+ pts won" not in got and not any(k.startswith("Pass") for k in got)
    assert refresh.props_metrics(pd.DataFrame()) == {}


def test_the_props_study_uses_the_shadow_and_cuts_it_at_the_closed_week(monkeypatch):
    import pandas as pd

    from dfs import props_edge

    rows = pd.DataFrame(
        {
            "Week": [4, 5, 6],
            "Stat": ["rec_yd"] * 3,
            "Edge": [0.12, 0.2, 0.3],
            "Price": [1.9, 1.9, 1.9],
            "Won": [1.0, 0.0, 1.0],
            "Profit": [0.9, -1.0, 0.9],
        }
    )
    seen = {}

    def fake_build(stats_player, *, season, pulls=None):
        seen["season"] = season
        return props_edge.Shadow(rows, props_edge.band_table(rows), pd.DataFrame(), {}, [4, 5, 6], [4, 5, 6])

    monkeypatch.setattr(props_edge, "build_shadow", fake_build)
    monkeypatch.setattr(props_edge, "latest_stats_player", lambda: None)
    study = refresh.study_props(refresh.Context(2026, 5, 1))
    assert seen["season"] == 2026 and study.metrics["all gaps n"] == 2  # week 6 is not closed yet
    assert study.status == "ok" and "Too early for a verdict" in study.verdict


def test_a_partial_run_never_replaces_the_full_report_or_becomes_last_weeks(two_studies, tmp_path):
    full, _, _ = refresh.refresh(2026, 5, directory=tmp_path)
    before = full.read_text()
    partial, studies, _ = refresh.refresh(2026, 5, only=["a"], directory=tmp_path)
    assert partial.name == "2026-wk05-partial.md" and [s.key for s in studies] == ["a"]
    assert full.read_text() == before
    prev = refresh.previous_report(2026, 6, tmp_path)
    assert prev is not None and prev[0].name == "2026-wk05.md"
