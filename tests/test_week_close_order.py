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
    monkeypatch.setattr(cli, "_contest_plan_after_close", lambda *a, **k: calls.append("plan"))
    monkeypatch.setattr(cli, "_entered_record_warning", lambda *a, **k: calls.append("record?"))
    monkeypatch.setattr(cli, "_cash_postmortem_after_close", lambda *a, **k: calls.append("postmortem"))
    monkeypatch.setattr(cli, "_du_line_after_close", lambda *a, **k: calls.append("du-line"))
    monkeypatch.setattr(cli, "_plan_k_after_close", lambda *a, **k: calls.append("plan-k"))
    monkeypatch.setattr(cli, "_closed_week", lambda cfg, week: 8)
    cli.week_close(csv=None, week=None)
    # the plan line reads the log the contests step just updated; the bankroll curve and the copies
    # calibration read what the close wrote; the cash band's line is refitted before the review (Phase A)
    assert calls == [
        "bankroll", "keep", "score", "contests", "plan", "record?", "postmortem", "du-line", "plan-k"
    ]  # fmt: skip


def test_the_double_up_line_refit_never_fails_the_close(monkeypatch):
    from dfs.sim import du_line_retro

    def boom(*a, **k):
        raise FileNotFoundError("no standings")

    monkeypatch.setattr(du_line_retro, "refresh_summary", boom)
    cli._du_line_after_close(9)  # prints a warning, does not raise
    cli._du_line_after_close(None)  # no closed week: nothing to refit


def test_the_plan_check_never_fails_the_close():
    cli._contest_plan_after_close(object(), 8)  # no config at all: prints a warning, does not raise


def test_the_plan_k_step_never_fails_the_close(monkeypatch):
    from dfs.sim import dup_calibration

    def boom(*a, **k):
        raise ValueError("no standings")

    monkeypatch.setattr(cli, "SheetsClient", lambda *a, **k: object())
    monkeypatch.setattr(cli, "_save_bankroll_health", lambda *a, **k: None)
    monkeypatch.setattr(dup_calibration, "run", boom)
    cli._plan_k_after_close(object(), 8)  # prints a warning, does not raise


def test_a_failure_while_keeping_the_files_does_not_stop_the_close(monkeypatch, tmp_path):
    class Cfg:
        class contests:  # noqa: N801
            downloads_dir = str(tmp_path)

    def boom(csv):
        raise OSError("disk")

    monkeypatch.setattr(cli, "_keep_history_export", boom)
    cli._keep_dk_files_before_scoring(Cfg(), Path("history.csv"))  # prints a warning, does not raise
