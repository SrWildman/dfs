"""The bound Apps Script (`apps_script/Code.gs`): its version, and whether a sheet has it pasted.

The script handles the `Pool` dropdown on the Edge Finder, Board and Player Pool tabs (it writes EdgeRaw's
`Pool` cell for the player whose hidden `Id` sits on the edited row, then puts the cell's formula back),
Player Pool's add-a-player box, and the "Lineup Tools" menu. It lives in the repo so nothing Sam has is lost,
and is pasted into each sheet by hand (Extensions > Apps Script; the steps are in `docs/APPS_SCRIPT.md`).
Weekly copies of the template inherit it.

Every time `onOpen` runs, the script writes its `DFS_SCRIPT_VERSION` into a hidden named range of the same
name (a cell on the hidden `NameAlias` tab). `dfs doctor` reads that stamp and WARNS (it never fails) when it
is missing or older than the repo's `Code.gs`, with the one-line fix. The names the script and this package
must agree on (tab names, the `Id` / `Pool` headers, the dropdown options) are checked against
`Code.gs` by `tests/test_apps_script.py`.
"""

from __future__ import annotations

import re
from pathlib import Path

from dfs.sheets import SheetsClient, SheetsError

SCRIPT_PATH = Path(__file__).resolve().parents[2] / "apps_script" / "Code.gs"
VERSION_RANGE_NAME = "DFS_SCRIPT_VERSION"
FIX = "paste apps_script/Code.gs into Extensions > Apps Script (docs/APPS_SCRIPT.md)"
_VERSION_RE = re.compile(r"^var DFS_SCRIPT_VERSION = (\d+);", re.MULTILINE)


def repo_script_version(source: str | None = None) -> int:
    """The `DFS_SCRIPT_VERSION` declared in `apps_script/Code.gs` (or in `source`, for tests)."""
    text = source if source is not None else SCRIPT_PATH.read_text()
    match = _VERSION_RE.search(text)
    if match is None:
        raise ValueError("apps_script/Code.gs declares no `var DFS_SCRIPT_VERSION = <n>;`")
    return int(match.group(1))


def stamp_warning(stamp: object, repo_version: int) -> str | None:
    """The doctor warning for a sheet's stamp (`None` when current): missing, unreadable, or older."""
    text = "" if stamp is None else str(stamp).strip()
    if text == "":
        return (
            "Apps Script not pasted (or never opened since): the Pool dropdowns will not change the pool "
            f"yet (a picked value would sit there stale). Fix: {FIX}"
        )
    try:
        have = int(float(text))
    except ValueError:
        return f"Apps Script version stamp {text!r} is unreadable. Fix: {FIX}"
    if have < repo_version:
        return f"Apps Script is version {have}, the repo has {repo_version}. Fix: {FIX}"
    return None


def script_warnings(client: SheetsClient) -> list[str]:
    """Doctor warnings about the bound script on the connected sheet (read only)."""
    try:
        stamp = client.read_named_range_value(VERSION_RANGE_NAME)
    except SheetsError:
        stamp = None
    warning = stamp_warning(stamp, repo_script_version())
    return [warning] if warning else []
