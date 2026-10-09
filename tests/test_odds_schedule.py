import plistlib
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from dfs import odds_schedule as sched


def test_the_job_runs_dfs_odds_snapshot_on_monday_and_tuesday_at_the_given_time():
    job = plistlib.loads(
        sched.plist_contents(Path("/venv/bin/dfs"), Path("/repo"), Path("/repo/data/odds.log"), 9, 0)
    )
    assert job["Label"] == "com.dfs.odds-snapshot"
    assert job["ProgramArguments"] == ["/venv/bin/dfs", "odds", "snapshot"]
    assert job["WorkingDirectory"] == "/repo" and job["RunAtLoad"] is False
    assert job["StartCalendarInterval"] == [
        {"Weekday": 1, "Hour": 9, "Minute": 0},  # Monday
        {"Weekday": 2, "Hour": 9, "Minute": 0},  # Tuesday
    ]


def test_ten_am_eastern_is_converted_to_the_machines_own_clock(monkeypatch):
    # Whatever zone this runs in, 10:00 ET lands on a valid hour/minute and matches the zone's offset.
    hour, minute = sched.local_run_time(datetime(2026, 10, 6, 12, tzinfo=ZoneInfo("America/New_York")))
    local = datetime(2026, 10, 6, 10, tzinfo=ZoneInfo("America/New_York")).astimezone()
    assert (hour, minute) == (local.hour, local.minute) and 0 <= hour < 24


def test_install_writes_the_agent_and_loads_it_and_remove_unloads_and_deletes_it(monkeypatch, tmp_path):
    monkeypatch.setattr(sched, "PLIST_PATH", tmp_path / "LaunchAgents" / "job.plist")
    monkeypatch.setattr(sched, "LOG_PATH", tmp_path / "odds.log")
    calls = []

    class Done:
        returncode, stderr = 0, ""

    def fake_run(command, **kwargs):
        calls.append(command[:2])
        return Done()

    assert "Mondays and Tuesdays" in sched.install(run=fake_run)
    assert sched.PLIST_PATH.exists() and ["launchctl", "bootstrap"] in calls
    assert sched.status().startswith("installed")
    assert sched.remove(run=fake_run) == "removed" and not sched.PLIST_PATH.exists()
    assert ["launchctl", "bootout"] in calls
    assert sched.remove(run=fake_run) == "not installed" and sched.status() == "not installed"
