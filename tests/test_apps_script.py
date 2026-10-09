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
from dfs import sheet_pool_cells as pc
from dfs.sheet_names import ALIAS_TAB
from dfs.sheet_pool_control import DEFAULT_TYPE, LABEL_TEXT
from dfs.sheet_views import BOARD_ID_HEADER, BOARD_TAB
from dfs.weekly_reset import PLAYER_POOL_CONTROL_DEFAULT_TYPE, PLAYER_POOL_CONTROL_LABEL

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


def _list_const(name: str) -> list[str]:
    options = re.search(rf"^var {name} = \[(.*?)\];", SOURCE, re.MULTILINE).group(1)
    return [o.strip().strip("'") for o in options.split(",")]


def test_the_pool_headers_options_and_tabs_match_what_python_writes():
    assert _const("POOL_HEADER") == pc.POOL_HEADER == eft.POOL_HEADER == "Pool"
    assert _const("ID_HEADER") == "Id" and "Id" in eft.TRAILING_HEADERS and BOARD_ID_HEADER == "Id"
    assert _list_const("POOL_VALUES") == pc.POOL_OPTIONS == ["Cash", "GPP", "Both"]
    assert _list_const("POOL_TABS") == [eft.EDGE_FINDER_TAB, BOARD_TAB, "Player Pool"]
    assert _const("LINEUPS_TAB") == "Lineups" and _const("PLAYER_POOL_TAB") == "Player Pool"
    assert _const("EDGE_RAW_TAB") == "EdgeRaw" and _const("VERSION_TAB") == ALIAS_TAB
    assert _const("VERSION_RANGE_NAME") == apps_script.VERSION_RANGE_NAME
    assert _const("ADD_LABEL") == LABEL_TEXT == PLAYER_POOL_CONTROL_LABEL
    assert _const("ADD_DEFAULT_TYPE") == DEFAULT_TYPE == PLAYER_POOL_CONTROL_DEFAULT_TYPE


def test_the_old_set_dropdown_and_added_list_are_gone_from_the_script():
    assert "SET_VALUES" not in SOURCE and "Remove" not in re.findall(r"var POOL_VALUES = .*", SOURCE)[0]
    assert "removeFromAddedList_" not in SOURCE and "ADDED_HEADER" not in SOURCE


def test_every_edit_path_puts_the_formula_back_or_leaves_the_cell_alone():
    """No branch of the Pool edit may leave a typed value behind: each early exit that reaches the cell
    writes the formula back first (the one exception, a cell with no header above, is not a Pool control)."""
    body = SOURCE.split("function handlePoolEdit_")[1].split("/** EdgeRaw's sheet")[0]
    # row without an Id, an invalid value, a player EdgeRaw lacks, and the set / remove that succeeded
    assert body.count("cell.setFormula(formula)") == 4
    assert "if (headerRow === -1) continue;" in body
    assert "clearContent()" in body  # only EdgeRaw's own Pool cell on a remove


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
def test_pool_values_blank_is_a_remove_and_anything_else_is_invalid(node_module):
    cases = "[' cash ', 'GPP', 'both', '', '   ', null, 'Remove', 'nope']"
    assert _run_node(node_module, f"{cases}.map(m.normalizePoolValue)") == [
        {"kind": "set", "value": "Cash"},
        {"kind": "set", "value": "GPP"},
        {"kind": "set", "value": "Both"},
        {"kind": "remove"},
        {"kind": "remove"},
        {"kind": "remove"},
        {"kind": "invalid"},
        {"kind": "invalid"},
    ]


@needs_node
def test_add_player_type_defaults_to_both_and_the_resolved_name_wins(node_module):
    assert _run_node(node_module, "['GPP', 'cash', '', 'nope', null].map(m.normalizeAddType)") == [
        "GPP",
        "Cash",
        "Both",
        "Both",
        "Both",
    ]
    assert (
        _run_node(node_module, "m.chooseAddName(' kenneth walker ', 'Kenneth Walker III')")
        == "Kenneth Walker III"
    )
    assert _run_node(node_module, "m.chooseAddName(' kenneth walker ', '')") == "kenneth walker"


@needs_node
def test_the_scripts_pool_formula_is_the_one_python_writes(node_module):
    """The script restores a Pool cell with this text; `doctor` and the rest of the sheet expect Python's."""
    for row, id_col in ((7, "P"), (120, "AB")):
        from_script = _run_node(
            node_module,
            f"m.poolFormula({row}, {json.dumps(id_col)}, 'EdgeRaw', "
            f"{json.dumps(pc.POOL_COLUMN)}, {json.dumps(pc.edge_letter('Id'))})",
        )
        assert from_script == pc.pool_formula(row, "EdgeRaw", id_col=id_col)


@needs_node
def test_header_row_and_column_helpers(node_module):
    column = [
        "Name",
        "x",
        "Pool",
        "",
        "",
        "Pool",
        "",
        "",
    ]  # rows 1..8; edited row 8; the nearest Pool is row 6
    assert _run_node(node_module, f"m.headerRowAbove({json.dumps(column)}, 8, 'Pool')") == 6
    assert _run_node(node_module, f"m.headerRowAbove({json.dumps(column)}, 3, 'Pool')") == -1  # not itself
    assert _run_node(node_module, f"m.headerRowAbove({json.dumps(column)}, 2, 'Pool')") == -1
    assert _run_node(node_module, "m.findColumn(['Pool','Name','Why','Do','Id'], 'Id')") == 5
    assert _run_node(node_module, "m.findColumn(['Name','Idx'], 'Id')") == -1
    assert _run_node(node_module, "[1, 26, 27, 52, 53, 78].map(m.columnLetter)") == [
        "A",
        "Z",
        "AA",
        "AZ",
        "BA",
        "BZ",
    ]
    assert _run_node(
        node_module, "[m.sameList(['Cash','GPP','Both'], m.POOL_VALUES), m.sameList(['a'], ['a','b'])]"
    ) == [
        True,
        False,
    ]


# ---- onEdit end to end, against an in-memory spreadsheet (tests/js/apps_script_harness.cjs) ----------------


HARNESS = Path(__file__).parent / "js" / "apps_script_harness.cjs"
POOL_LIST = ["Cash", "GPP", "Both"]


def _scenario(edits, *, edge_ids=(1001, 1002), player_pool_box=None):
    """EdgeRaw (Pool in A, Id in D), an Edge Finder with player rows and a title, a Player Pool."""
    edge_rows = [["Pool", "Name", "Position", "Id"]] + [["", f"Player {i}", "WR", i] for i in edge_ids]
    edge_rows[1][1] = "Kenneth Walker III"
    finder_rows = [
        ["EDGE FINDER"],
        [],
        ["Pool", "Name", "Pos", "Id"],
        ["", "Player 1001", "WR", 1001],
        ["", "Player 1002", "WR", 1002],
        ["", "Player 9999", "WR", 9999],
        ["", "No id", "WR", ""],
        ["SECTION TITLE"],
    ]
    formulas = [{"row": r, "col": 1, "value": "", "formula": "=orig"} for r in (4, 5, 6, 7)]
    validations = [{"row": r, "col": 1, "values": POOL_LIST} for r in (4, 5, 6, 7)]
    pool_rows = [
        ["Both", "Add a player", player_pool_box or "", "", "Kenneth Walker III"],
        ["Pool", "Name", "Pos.", "Team", "Id"],
    ]
    return {
        "sheets": {
            "EdgeRaw": {"rows": edge_rows, "lastRow": len(edge_rows), "lastCol": 4},
            "Edge Finder": {
                "rows": finder_rows,
                "formulas": formulas,
                "validations": validations,
                "lastRow": 8,
                "lastCol": 4,
            },
            "Player Pool": {
                "rows": pool_rows,
                "lastRow": 2,
                "lastCol": 5,
                "validations": [{"row": 1, "col": 1, "values": POOL_LIST}],
            },
        },
        "edits": edits,
    }


def _run_flow(tmp_path, scenario):
    path = tmp_path / "scenario.json"
    path.write_text(json.dumps(scenario))
    out = subprocess.run(
        [NODE, str(HARNESS), str(CODE), str(path)], capture_output=True, text=True, check=True
    )
    return json.loads(out.stdout)


def _cell(state, sheet, row, col):
    return state["sheets"][sheet].get(f"{row},{col}", {"value": None, "formula": None})


@needs_node
def test_picking_a_value_writes_edgeraw_by_id_and_puts_the_pool_formula_back(tmp_path):
    state = _run_flow(tmp_path, _scenario([{"sheet": "Edge Finder", "row": 5, "col": 1, "values": ["Cash"]}]))
    assert _cell(state, "EdgeRaw", 3, 1)["value"] == "Cash"  # Id 1002 is EdgeRaw row 3, never matched by name
    assert _cell(state, "EdgeRaw", 2, 1)["value"] is None  # the other player is untouched
    restored = _cell(state, "Edge Finder", 5, 1)["formula"]
    assert restored == '=IF($D5="","",IFERROR(INDEX(EdgeRaw!$A:$A,MATCH($D5,EdgeRaw!$D:$D,0)),""))'
    assert any("Cash" in t for t in state["toasts"])


@needs_node
def test_clearing_the_cell_removes_him_from_the_pool_and_still_restores_the_formula(tmp_path):
    scenario = _scenario(
        [
            {"sheet": "Edge Finder", "row": 4, "col": 1, "values": ["Both"]},
            {"sheet": "Edge Finder", "row": 4, "col": 1, "values": [None]},  # the Delete key
        ]
    )
    state = _run_flow(tmp_path, scenario)
    assert _cell(state, "EdgeRaw", 2, 1)["value"] is None  # set, then blanked
    assert _cell(state, "Edge Finder", 4, 1)["formula"].startswith("=IF($D4")
    assert any(t.startswith("Removed") for t in state["toasts"])


@needs_node
def test_a_bad_id_or_value_changes_nothing_and_never_leaves_a_typed_value(tmp_path):
    edits = [
        {"sheet": "Edge Finder", "row": 6, "col": 1, "values": ["Cash"]},  # Id 9999: not on EdgeRaw
        {"sheet": "Edge Finder", "row": 7, "col": 1, "values": ["Cash"]},  # no Id on the row
        {"sheet": "Edge Finder", "row": 4, "col": 1, "values": ["Remove"]},  # not a pool value
    ]
    state = _run_flow(tmp_path, _scenario(edits))
    assert [_cell(state, "EdgeRaw", r, 1)["value"] for r in (2, 3)] == [None, None]
    for row in (4, 6, 7):
        cell = _cell(state, "Edge Finder", row, 1)
        assert cell["formula"] and cell["formula"].startswith("=IF($D") and cell["value"] == "(formula)"
    assert len(state["toasts"]) == 3 and all("nothing changed" in t for t in state["toasts"])


@needs_node
def test_cells_without_the_pool_dropdown_are_left_alone(tmp_path):
    edits = [
        {"sheet": "Edge Finder", "row": 8, "col": 1, "values": ["a note I typed"]},  # a section title cell
        {"sheet": "Edge Finder", "row": 4, "col": 2, "values": ["typed in the Name column"]},
    ]
    state = _run_flow(tmp_path, _scenario(edits))
    assert _cell(state, "Edge Finder", 8, 1)["value"] == "a note I typed"
    assert _cell(state, "Edge Finder", 8, 1)["formula"] is None and state["toasts"] == []
    assert _cell(state, "EdgeRaw", 2, 1)["value"] is None


@needs_node
def test_the_add_a_player_box_sets_edgeraw_pool_to_the_chosen_type_and_clears_the_box(tmp_path):
    scenario = _scenario([{"sheet": "Player Pool", "row": 1, "col": 3, "values": ["kenneth walker"]}])
    state = _run_flow(tmp_path, scenario)
    assert _cell(state, "EdgeRaw", 2, 1)["value"] == "Both"  # the resolved name, the default type
    assert _cell(state, "Player Pool", 1, 3)["value"] is None  # the box clears
    assert any("Added Kenneth Walker III (Both)" in t for t in state["toasts"])

    gpp = _scenario([{"sheet": "Player Pool", "row": 1, "col": 3, "values": ["kenneth walker"]}])
    gpp["sheets"]["Player Pool"]["rows"][0][0] = "GPP"
    assert _cell(_run_flow(tmp_path, gpp), "EdgeRaw", 2, 1)["value"] == "GPP"

    unknown = _scenario([{"sheet": "Player Pool", "row": 1, "col": 3, "values": ["nobody"]}])
    unknown["sheets"]["Player Pool"]["rows"][0][4] = ""  # the resolver found nothing
    state = _run_flow(tmp_path, unknown)
    assert _cell(state, "EdgeRaw", 2, 1)["value"] is None and any(
        "nothing added" in t for t in state["toasts"]
    )


@needs_node
def test_editing_the_type_dropdown_above_the_header_never_clears_it(tmp_path):
    state = _run_flow(tmp_path, _scenario([{"sheet": "Player Pool", "row": 1, "col": 1, "values": ["Cash"]}]))
    assert _cell(state, "Player Pool", 1, 1)["value"] == "Cash"  # no Pool header above it: left as typed
    assert _cell(state, "EdgeRaw", 2, 1)["value"] is None
