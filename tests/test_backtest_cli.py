"""`dfs backtest` on the command line: the argument errors, and one run with the backtest itself stubbed."""

from typer.testing import CliRunner

from dfs import backtest as bt
from dfs import backtest_settings as bs
from dfs.cli import app
from tests.test_backtest import result

runner = CliRunner()


def write(tmp_path, text, name="s.toml"):
    p = tmp_path / name
    p.write_text(text)
    return p


def test_the_command_is_listed_and_documented():
    out = runner.invoke(app, ["backtest", "--help"]).output
    for needle in ("--weeks", "--settings", "--compare", "--fresh", "--no-report"):
        assert needle in out


def test_a_settings_file_that_does_not_exist_is_a_usage_error(tmp_path):
    r = runner.invoke(app, ["backtest", "--settings", str(tmp_path / "nope.toml")])
    assert r.exit_code != 0


def test_a_typo_in_the_settings_stops_before_any_work(tmp_path, monkeypatch):
    monkeypatch.setattr(bt, "backtest", lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not run")))
    r = runner.invoke(app, ["backtest", "--settings", str(write(tmp_path, "[builds]\nrnak = 'top1'\n"))])
    assert r.exit_code == 1 and "unknown [builds] key" in r.output


def test_an_unknown_flag_names_what_is_registered(tmp_path, monkeypatch):
    monkeypatch.setattr(bs, "FLAGS", {})
    monkeypatch.setattr(bs, "PLUGIN_MODULES", ())
    r = runner.invoke(app, ["backtest", "--settings", str(write(tmp_path, "[flags]\nportfolio = true\n"))])
    assert r.exit_code == 1 and "unknown flag" in r.output and "portfolio" in r.output


def test_weeks_must_be_numbers():
    r = runner.invoke(app, ["backtest", "--weeks", "four"])
    assert r.exit_code == 1


def test_a_run_prints_the_summary_and_writes_the_report(tmp_path, monkeypatch):
    seen = {}

    def fake(weeks, settings, **kwargs):
        seen.update(weeks=weeks, names=[s.name for s in settings], fresh=kwargs["fresh"])
        return {s.name: result(s.name) for s in settings}

    monkeypatch.setattr(bt, "backtest", fake)
    monkeypatch.setattr(bt, "REPORT_DIR", tmp_path / "reports")
    mine = write(tmp_path, '[builds]\ncash = 2\n[rules]\nbuild = "min unique 2"\n', "mine.toml")
    base = write(tmp_path, "", "base.toml")
    r = runner.invoke(
        app, ["backtest", "--weeks", "6,4", "--settings", str(mine), "--compare", str(base), "--fresh"]
    )
    assert r.exit_code == 0, r.output
    assert seen == {"weeks": [4, 6], "names": ["mine", "base"], "fresh": True}
    assert "Flashback 'mine'" in r.output and "Flashback 'base'" in r.output
    assert "Build rules" in r.output  # the line the backtest cannot apply is named
    assert "mine (A) against base (B)" in r.output
    (report,) = (tmp_path / "reports").glob("*.md")
    assert "## Comparison" in report.read_text()


def test_two_settings_with_the_same_name_are_told_apart(tmp_path, monkeypatch):
    seen = {}

    def fake(weeks, settings, **kwargs):
        seen["names"] = [s.name for s in settings]
        return {s.name: result(s.name) for s in settings}

    monkeypatch.setattr(bt, "backtest", fake)
    a = write(tmp_path, 'name = "x"\n', "a.toml")
    b = write(tmp_path, 'name = "x"\n', "b.toml")
    r = runner.invoke(app, ["backtest", "--settings", str(a), "--compare", str(b), "--no-report"])
    assert r.exit_code == 0 and seen["names"] == ["x", "x (baseline)"]
