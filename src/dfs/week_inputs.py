"""STAND-IN for Plan E's `week_inputs.py` (branch `week-page`): only `save_input`, with the same signature and
file layout, so `dfs contests plan` and `week close`'s cash post-mortem can leave their results for the Week
page before that branch merges. Plan E's rebase replaces this file with the full module (a superset).

Files: `data/week/inputs/<season>-wk<NN>-<name>.json` (`saved_at` added), never raising.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from dfs import paths

INPUTS_SUBDIR = Path("week") / "inputs"

# Set by tests (and nothing else) to redirect what the page reads and writes; None = `paths.DATA_DIR`.
DATA_ROOT: Path | None = None


def data_root() -> Path:
    return DATA_ROOT if DATA_ROOT is not None else paths.DATA_DIR


def inputs_dir(data_dir: Path | None = None) -> Path:
    return (data_dir or data_root()) / INPUTS_SUBDIR


def input_path(name: str, season: int | None, week: int | None, data_dir: Path | None = None) -> Path:
    stem = name if season is None or week is None else f"{season}-wk{week:02d}-{name}"
    return inputs_dir(data_dir) / f"{stem}.json"


def save_input(
    name: str,
    payload: dict,
    *,
    season: int | None = None,
    week: int | None = None,
    data_dir: Path | None = None,
    now: datetime | None = None,
) -> bool:
    """Write one input for the page (adds `saved_at`). Never raises; False when it could not be written."""
    try:
        path = input_path(name, season, week, data_dir)
        path.parent.mkdir(parents=True, exist_ok=True)
        body = {
            **payload,
            "saved_at": (now or datetime.now(UTC)).astimezone(UTC).isoformat(timespec="seconds"),
        }
        path.write_text(json.dumps(body, indent=1, default=str))
        return True
    except Exception:  # noqa: BLE001 - a command never fails over the page
        return False


def load_input(name: str, season: int | None, week: int | None, data_dir: Path | None = None) -> dict | None:
    try:
        return json.loads(input_path(name, season, week, data_dir).read_text())
    except (OSError, ValueError):
        return None
