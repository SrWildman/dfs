"""The run log (`dfs.runlog`): what it writes, what it never writes, and that no test run touches the real
file."""

from datetime import UTC, datetime, timedelta

from conftest import REAL_DATA_DIR
from typer.main import get_command
from typer.testing import CliRunner

from dfs import paths, prompt_missing, runlog, week_inputs, week_page
from dfs.cli import app

T0 = datetime(2026, 11, 7, 15, 0, tzinfo=UTC)


def test_record_appends_with_a_header_once(tmp_path):
    log = tmp_path / "runlog.csv"
    runlog.record("dfs sync --live", True, now=T0, path=log)
    runlog.record("dfs week close", False, now=T0 + timedelta(hours=1), path=log)
    lines = log.read_text().splitlines()
    assert lines[0] == "time,command,ok"
    assert lines[1] == "2026-11-07T15:00:00+00:00,dfs sync --live,1"
    assert lines[2].endswith(",dfs week close,0")
    runs = runlog.read(log)
    assert [r.ok for r in runs] == [True, False]
    assert runs[0].path == "dfs sync" and runs[0].flags == {"--live"}


def test_record_never_raises(tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("x")
    runlog.record("dfs sync", True, path=blocker / "inside" / "runlog.csv")  # parent is a file


def test_read_of_a_missing_or_damaged_log_is_empty_or_skips_bad_lines(tmp_path):
    assert runlog.read(tmp_path / "nope.csv") == []
    log = tmp_path / "runlog.csv"
    log.write_text("time,command,ok\nnot a time,dfs sync,1\n2026-11-07T15:00:00+00:00,dfs sync,1\n")
    assert [r.command for r in runlog.read(log)] == ["dfs sync"]


def test_last_run_matches_the_flags_it_was_asked_for(tmp_path):
    log = tmp_path / "runlog.csv"
    runlog.record("dfs sync", True, now=T0, path=log)
    runlog.record("dfs sync --live", True, now=T0 + timedelta(days=1), path=log)
    runlog.record("dfs sync", False, now=T0 + timedelta(days=2), path=log)
    runs = runlog.read(log)
    assert runlog.last_run(runs, "dfs sync").time == T0 + timedelta(days=2)
    assert runlog.last_run(runs, "dfs sync", only_ok=True).time == T0 + timedelta(days=1)
    assert runlog.last_run(runs, "dfs sync --live").time == T0 + timedelta(days=1)
    assert runlog.last_run(runs, "dfs lineups suggest") is None


def test_the_command_is_its_name_and_option_names_never_values():
    root = get_command(app)
    cmd = runlog.command_from_args
    assert cmd(root, ["sync", "--live"]) == "dfs sync --live"
    assert cmd(root, ["week", "close", "--csv", "/Users/Sam/Downloads/history.csv"]) == "dfs week close --csv"
    assert cmd(root, ["week", "new", "https://docs.google.com/spreadsheets/d/SECRET/edit"]) == "dfs week new"
    assert cmd(root, ["-v", "lineups", "late-swap", "--state=live"]) == "dfs lineups late-swap --state"
    assert cmd(root, ["lineups", "late-swap", "--state", "live", "--field", "auto"]) == (
        "dfs lineups late-swap --field --state"
    )


def test_nothing_is_logged_for_the_launcher_help_or_a_group_page():
    root = get_command(app)
    assert runlog.command_from_args(root, []) is None
    assert runlog.command_from_args(root, ["--help"]) is None
    assert runlog.command_from_args(root, ["sync", "--help"]) is None
    assert runlog.command_from_args(root, ["lineups"]) is None
    assert runlog.command_from_args(root, ["not-a-command"]) is None


def test_a_cli_run_is_logged_to_the_temp_log_and_a_failed_one_is_logged_as_failed(tmp_path):
    runner = CliRunner()
    runner.invoke(app, ["week", "page", "--no-open"])
    result = runner.invoke(app, ["week", "new"])  # missing its required argument: a usage error
    assert result.exit_code != 0
    runs = runlog.read()
    assert [(r.command, r.ok) for r in runs] == [("dfs week page --no-open", True), ("dfs week new", False)]


def test_the_log_and_the_page_files_sit_under_the_hermetic_data_dir():
    """A CLI run in a test writes the run log and the page under the test's own data dir, never the real one
    (conftest's guard would fail the run on a real write; this pins where they land)."""
    real = REAL_DATA_DIR
    CliRunner().invoke(app, ["week", "page", "--no-open"])
    runlog.record("dfs sync", True)
    week_inputs.save_input("probe", {"x": 1}, season=2026, week=9)
    assert paths.DATA_DIR != real
    for path in (runlog.runlog_path(), week_inputs.inputs_dir(), week_page.week_dir()):
        assert real not in path.parents and paths.DATA_DIR in path.parents, path
    assert runlog.runlog_path().exists()
    assert week_inputs.load_input("probe", 2026, 9) == {
        "x": 1,
        "saved_at": week_inputs.load_input("probe", 2026, 9)["saved_at"],
    }


def test_a_succeeded_command_regenerates_the_page_and_a_failure_of_that_never_fails_it(monkeypatch, capsys):
    root = get_command(app)
    built = []
    monkeypatch.setattr(week_page, "AUTO_REGENERATE", True)
    monkeypatch.setattr(week_page, "generate", lambda **kw: built.append(kw) or paths.REPO_ROOT / "p.html")

    prompt_missing._after_run(root, ["sync"], True)
    prompt_missing._after_run(root, ["lineups", "late-swap"], True)
    prompt_missing._after_run(root, ["sync"], False)  # failed: no page
    prompt_missing._after_run(root, ["pool", "list"], True)  # not one of the commands that regenerates
    assert len(built) == 2
    assert [r.command for r in runlog.read()] == [
        "dfs sync",
        "dfs lineups late-swap",
        "dfs sync",
        "dfs pool list",
    ]

    def boom(**kw):
        raise RuntimeError("disk full")

    monkeypatch.setattr(week_page, "generate", boom)
    prompt_missing._after_run(root, ["sync"], True)  # warns, does not raise
    assert "Week page was not refreshed (disk full)" in capsys.readouterr().err
