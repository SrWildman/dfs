"""The macOS `launchd` job that saves the betting lines every Monday and Tuesday at 10:00 ET.

Why: the week's movement columns (`ImpliedMove`, `TotMove`, `SpdMove`, the Movement tab) are measured against
the week's opening lines, and the odds source only ever shows the current slate, so the opening is only
on record if something saved it before the sheet was copied (`store.opening_snapshot`). `dfs odds snapshot`
fetches the lines and saves them locally (no sheet needed); this job runs it on Monday and Tuesday morning.

`install` writes a per-user LaunchAgent and loads it; `remove` unloads and deletes it. Nothing here runs
unless the person types the command: a normal week never needs it installed more than once.
"""

from __future__ import annotations

import plistlib
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from dfs.kickoff import KICKOFF_TIMEZONE
from dfs.paths import DATA_DIR

LABEL = "com.dfs.odds-snapshot"
PLIST_PATH = Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"
LOG_PATH = DATA_DIR / "odds_snapshot.log"
WEEKDAYS = (1, 2)  # launchd: 0 or 7 is Sunday, so 1 is Monday and 2 is Tuesday
RUN_HOUR_ET = 10


def local_run_time(now: datetime | None = None) -> tuple[int, int]:
    """10:00 Eastern as (hour, minute) on this machine's own clock: launchd fires in local time, and this
    Mac is not necessarily on Eastern. Both zones change on the same dates, so the offset holds all year."""
    eastern = (now or datetime.now(ZoneInfo(KICKOFF_TIMEZONE))).astimezone(ZoneInfo(KICKOFF_TIMEZONE))
    at_ten = eastern.replace(hour=RUN_HOUR_ET, minute=0, second=0, microsecond=0)
    local = at_ten.astimezone()
    return local.hour, local.minute


def plist_contents(program: Path, workdir: Path, log_path: Path, hour: int, minute: int) -> bytes:
    """The LaunchAgent: run `<program> odds snapshot` from `workdir` on `WEEKDAYS` at `hour:minute`."""
    job = {
        "Label": LABEL,
        "ProgramArguments": [str(program), "odds", "snapshot"],
        "WorkingDirectory": str(workdir),
        "StartCalendarInterval": [{"Weekday": day, "Hour": hour, "Minute": minute} for day in WEEKDAYS],
        "StandardOutPath": str(log_path),
        "StandardErrorPath": str(log_path),
        "RunAtLoad": False,
    }
    return plistlib.dumps(job)


def dfs_program() -> Path:
    """The `dfs` command next to the running interpreter (the virtualenv's), not whatever is on PATH."""
    return Path(sys.executable).parent / "dfs"


def install(*, run=subprocess.run) -> str:
    hour, minute = local_run_time()
    PLIST_PATH.parent.mkdir(parents=True, exist_ok=True)
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    PLIST_PATH.write_bytes(plist_contents(dfs_program(), Path.cwd(), LOG_PATH, hour, minute))
    domain = f"gui/{_uid()}"
    run(["launchctl", "bootout", domain, str(PLIST_PATH)], capture_output=True)  # replace a loaded copy
    result = run(["launchctl", "bootstrap", domain, str(PLIST_PATH)], capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"launchctl bootstrap failed: {result.stderr.strip()}")
    return f"installed: Mondays and Tuesdays at {hour:02d}:{minute:02d} local (10:00 ET); log {LOG_PATH}"


def remove(*, run=subprocess.run) -> str:
    if not PLIST_PATH.exists():
        return "not installed"
    run(["launchctl", "bootout", f"gui/{_uid()}", str(PLIST_PATH)], capture_output=True)
    PLIST_PATH.unlink()
    return "removed"


def status() -> str:
    return f"installed ({PLIST_PATH})" if PLIST_PATH.exists() else "not installed"


def _uid() -> int:
    import os

    return os.getuid()
