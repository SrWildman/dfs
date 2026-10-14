"""The Odds API's credit meter, kept on disk so the guards and `dfs doctor` can read it without a call.

The plan allows 500 credits a month. A call costs (markets that return data) x (regions); `/events` and an
empty response are free. Every response says what is left in `x-requests-remaining` (and `x-requests-used`,
`x-requests-last`), so every pull records those headers here: `data/current/odds_credits.json` is the latest
reading and `data/raw/odds_props/credits.csv` the append-only log (one line per pull, never a key).

`dfs doctor` warns under `LOW_CREDITS`. Nothing here talks to the network and nothing here ever sees the key.
"""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from dfs.paths import CURRENT_DIR, RAW_DIR

LOW_CREDITS = 100
STATUS_FILE = CURRENT_DIR / "odds_credits.json"
LOG_FILE = RAW_DIR / "odds_props" / "credits.csv"
LOG_COLUMNS = ["pulled_at", "remaining", "used", "spent", "events", "note"]


@dataclass(frozen=True)
class Credits:
    remaining: int | None
    used: int | None
    pulled_at: str  # UTC ISO stamp of the reading

    @property
    def low(self) -> bool:
        return self.remaining is not None and self.remaining < LOW_CREDITS


def _int(value: object) -> int | None:
    try:
        return int(float(str(value)))
    except (TypeError, ValueError):
        return None


def from_headers(headers) -> tuple[int | None, int | None, int | None]:
    """`(remaining, used, last call's cost)` from a response's headers (any may be None)."""
    return (
        _int(headers.get("x-requests-remaining")),
        _int(headers.get("x-requests-used")),
        _int(headers.get("x-requests-last")),
    )


def record(
    remaining: int | None,
    used: int | None,
    *,
    spent: int | None,
    events: int,
    note: str = "",
    now: datetime | None = None,
    status_file: Path | None = None,
    log_file: Path | None = None,
) -> Credits:
    """Writes the latest reading and appends one log line. Returns the reading."""
    status_file = status_file or STATUS_FILE
    log_file = log_file or LOG_FILE
    stamp = (now or datetime.now(UTC)).strftime("%Y-%m-%dT%H:%M:%SZ")
    reading = Credits(remaining, used, stamp)
    status_file.parent.mkdir(parents=True, exist_ok=True)
    status_file.write_text(json.dumps({"remaining": remaining, "used": used, "pulled_at": stamp}))
    log_file.parent.mkdir(parents=True, exist_ok=True)
    fresh = not log_file.exists()
    with log_file.open("a", newline="") as handle:
        writer = csv.writer(handle)
        if fresh:
            writer.writerow(LOG_COLUMNS)
        writer.writerow([stamp, remaining, used, spent, events, note])
    return reading


def read(status_file: Path | None = None) -> Credits | None:
    """The last reading, or None when there has been no pull yet (or the file is unreadable)."""
    path = status_file or STATUS_FILE
    try:
        data = json.loads(path.read_text())
        return Credits(_int(data.get("remaining")), _int(data.get("used")), str(data.get("pulled_at", "")))
    except (OSError, ValueError, AttributeError):
        return None


def low_warning(status_file: Path | None = None) -> str | None:
    """A sentence for `dfs doctor` when fewer than `LOW_CREDITS` are left, else None."""
    reading = read(status_file)
    if reading is None or not reading.low:
        return None
    return (
        f"The Odds API has {reading.remaining} credit(s) left (last pull {reading.pulled_at}); under "
        f"{LOW_CREDITS}. A full pull costs about 78 and the plan resets monthly: skip `--props` until then."
    )
