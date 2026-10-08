"""The bound Apps Script: Python and Code.gs agree on every name, the version stamp warns correctly, and the
script's pure functions (run under node when it is installed) behave."""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from dfs import apps_script
from dfs import edge_finder_tab as eft
from dfs.sheet_names import ALIAS_TAB
from dfs.sheet_views import BOARD_TAB
from dfs.sources.edge import POOL_HEADER
from dfs.weekly_reset import PLAYER_POOL_ADDED_NAMES_HEADER, PLAYER_POOL_ADDED_NAMES_ROWS

CODE = Path(apps_script.SCRIPT_PATH)
SOURCE = CODE.read_text()


def _const(name: str) -> str:
    match = re.search(rf"^var {name} = '([^']*)';", SOURCE, re.MULTILINE)
    assert match, f"Code.gs has no `var {name} = '...';`"
    return match.group(1)


def test_the_script_and_the_legacy_copy_are_both_in_the_repo():
    assert CODE.exists() and (CODE.parent / "legacy_Code.gs").exists()
    legacy = (CODE.parent / "legacy_Code.gs").read_text()
    assert "getActiveSheet()" in legacy  # the old behaviour is kept verbatim, for the record


def test_the_set_header_options_and_tabs_match_what_python_writes():
    assert _const("SET_HEADER") == "Set" and "Set" in eft.TRAILING_HEADERS
    assert _const("ID_HEADER") == "Id" and "Id" in eft.TRAILING_HEADERS
    assert _const("POOL_HEADER") == POOL_HEADER
    assert _const("ADDED_HEADER") == PLAYER_POOL_ADDED_NAMES_HEADER
    options = re.search(r"^var SET_VALUES = \[(.*?)\];", SOURCE, re.MULTILINE).group(1)
    assert [o.strip().strip("'") for o in options.split(",")] == eft.SET_OPTIONS
    tabs = re.search(r"^var SET_TABS = \[(.*?)\];", SOURCE, re.MULTILINE).group(1)
    assert [o.strip().strip("'") for o in tabs.split(",")] == [eft.EDGE_FINDER_TAB, BOARD_TAB]
    assert _const("LINEUPS_TAB") == "Lineups" and _const("PLAYER_POOL_TAB") == "Player Pool"
    assert _const("EDGE_RAW_TAB") == "EdgeRaw" and _const("VERSION_TAB") == ALIAS_TAB
    assert _const("VERSION_RANGE_NAME") == apps_script.VERSION_RANGE_NAME
    assert PLAYER_POOL_ADDED_NAMES_ROWS <= 50  # the script blanks entries wherever they sit below the header


def test_the_script_never_uses_the_active_sheet_to_clear_names_or_a_fixed_row():
    body = SOURCE.split("function clearLineupNames")[1].split("// ----")[0]
    assert "getActiveSheet" not in SOURCE
    assert "getSheetByName(LINEUPS_TAB)" in body and "ui.alert" in body and "ButtonSet.YES_NO" in body


def test_the_script_declares_a_version_python_can_read():
    assert apps_script.repo_script_version() >= 1
    assert apps_script.repo_script_version("var DFS_SCRIPT_VERSION = 7;\n") == 7
    with pytest.raises(ValueError):
        apps_script.repo_script_version("// nothing")


def test_doctor_warns_when_the_stamp_is_missing_unreadable_or_older_and_is_quiet_when_current():
    assert "not pasted" in apps_script.stamp_warning(
        None, 3
    ) and "Extensions > Apps Script" in apps_script.stamp_warning("", 3)
    assert "unreadable" in apps_script.stamp_warning("abc", 3)
    assert "version 2, the repo has 3" in apps_script.stamp_warning(2, 3)
    assert apps_script.stamp_warning(3, 3) is None and apps_script.stamp_warning("3", 3) is None
    assert apps_script.stamp_warning(4.0, 3) is None


def test_script_warnings_reads_the_named_range_and_survives_a_sheets_error():
    class Client:
        def __init__(self, value):
            self.value = value

        def read_named_range_value(self, name):
            assert name == "DFS_SCRIPT_VERSION"
            if isinstance(self.value, Exception):
                raise self.value
            return self.value

    current = apps_script.repo_script_version()
    assert apps_script.script_warnings(Client(current)) == []
    assert len(apps_script.script_warnings(Client(None))) == 1
    from dfs.sheets import SheetsError

    assert len(apps_script.script_warnings(Client(SheetsError("x")))) == 1  # unreadable counts as missing


NODE = shutil.which("node")


def _run_node(module: Path, expression: str):
    script = f"const m = require({json.dumps(str(module))}); console.log(JSON.stringify({expression}));"
    out = subprocess.run([NODE, "-e", script], capture_output=True, text=True, check=True)
    return json.loads(out.stdout)


@pytest.fixture
def node_module(tmp_path):
    """Code.gs copied to a .cjs file, so node can `require` it."""
    target = tmp_path / "Code.cjs"
    target.write_text(SOURCE)
    return target


needs_node = pytest.mark.skipif(
    NODE is None, reason="node is not installed (the manual checklist in docs/APPS_SCRIPT.md covers it)"
)


def _lineups_grid(blocks=2, players=9):
    grid = []
    for _ in range(blocks):
        grid.append(["Name", "Pos.", "Team", "Opp."])
        for p in range(players):
            grid.append([f"Player {p}" if p % 2 == 0 else "", "QB", "", ""])
        grid.append(["Cash", "", "", "Total"])  # column A of the Total row holds the Cash/GPP marker
        grid.append(["", "", "", "Remaining"])
        grid.append([])
    return grid


@needs_node
def test_lineup_clear_plans_only_the_nine_player_rows_of_each_block(node_module):
    grid = _lineups_grid(blocks=2)
    plan = _run_node(node_module, f"m.planLineupClear({json.dumps(grid)})")
    assert plan["blocks"] == 2 and plan["skipped"] == []
    assert plan["rows"] == [*range(2, 11), *range(15, 24)]  # never the Total (11, 24) or Remaining rows
    for r in plan["rows"]:
        assert grid[r - 1][3] not in ("Total", "Remaining")


@needs_node
def test_lineup_clear_skips_a_block_with_no_total_or_the_wrong_number_of_player_rows(node_module):
    short = _lineups_grid(blocks=1, players=7)
    assert _run_node(node_module, f"m.planLineupClear({json.dumps(short)})") == {
        "rows": [],
        "blocks": 0,
        "skipped": [1],
    }
    no_total = [["Name"], ["x"], ["y"], ["Name"], ["z"]]
    assert _run_node(node_module, f"m.planLineupClear({json.dumps(no_total)})")["rows"] == []
    assert _run_node(node_module, "m.planLineupClear([])") == {"rows": [], "blocks": 0, "skipped": []}


@needs_node
def test_set_value_header_and_column_helpers(node_module):
    values = "[' cash ', 'GPP', 'Remove', 'nope', '']"
    assert _run_node(node_module, f"{values}.map(m.normalizeSetValue)") == [
        "Cash",
        "GPP",
        "Remove",
        None,
        None,
    ]
    assert _run_node(
        node_module, "[m.poolValueFor('Cash'), m.poolValueFor('Both'), m.poolValueFor('Remove')]"
    ) == [
        "Cash",
        "Both",
        "",
    ]
    column = [
        "Name",
        "x",
        "Set",
        "",
        "",
        "Set",
        "",
        "",
    ]  # rows 1..8; edited row 8; the nearest Set above is row 6
    assert _run_node(node_module, f"m.headerRowAbove({json.dumps(column)}, 8, 'Set')") == 6
    assert (
        _run_node(node_module, f"m.headerRowAbove({json.dumps(column)}, 3, 'Set')") == -1
    )  # the Set cell itself is not above itself
    assert _run_node(node_module, f"m.headerRowAbove({json.dumps(column)}, 2, 'Set')") == -1
    assert _run_node(node_module, "m.findColumn(['Name','Why','Do','Pool','Set','↗','Id'], 'Id')") == 7
    assert _run_node(node_module, "m.findColumn(['Name','Idx'], 'Id')") == -1
    assert _run_node(node_module, "m.indicesOfName(['', 'A Player', 'Other', ' A Player '], 'A Player')") == [
        1,
        3,
    ]
    assert (
        _run_node(node_module, "m.indicesOfName(['', ''], '')") == []
    )  # a blank name never matches a blank cell
