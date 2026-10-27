"""`dfs week close` keeps the DK files before it scores Model Check, and scores before the contests step."""

from pathlib import Path

from dfs import cli


def test_the_dk_files_are_kept_before_model_check_is_scored_and_the_contests_step_runs_last(monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(cli, "_load_config_or_exit", lambda: object())
    monkeypatch.setattr(cli, "_resolve_history_csv", lambda cfg, csv: Path("history.csv"))
    monkeypatch.setattr(cli, "_sync_bankroll_from_csv", lambda *a, **k: calls.append("bankroll"))
    monkeypatch.setattr(cli, "_keep_dk_files_before_scoring", lambda *a, **k: calls.append("keep"))
    monkeypatch.setattr(cli, "_score_results_after_close", lambda *a, **k: calls.append("score"))
    monkeypatch.setattr(cli, "_contests_after_close", lambda *a, **k: calls.append("contests"))
    cli.week_close(csv=None, week=None)
    assert calls == ["bankroll", "keep", "score", "contests"]


def test_a_failure_while_keeping_the_files_does_not_stop_the_close(monkeypatch, tmp_path):
    class Cfg:
        class contests:  # noqa: N801
            downloads_dir = str(tmp_path)

    def boom(csv):
        raise OSError("disk")

    monkeypatch.setattr(cli, "_keep_history_export", boom)
    cli._keep_dk_files_before_scoring(Cfg(), Path("history.csv"))  # prints a warning, does not raise
