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
from dfs.sheet_pool_control import DEFAULT_TYPE, HINT_TEXT, LEGACY_LABEL
from dfs.sheet_views import BOARD_ID_HEADER, BOARD_TAB
from dfs.weekly_reset import PLAYER_POOL_CONTROL_DEFAULT_TYPE, PLAYER_POOL_CONTROL_HINT

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
    assert _const("ADD_HINT") == HINT_TEXT == PLAYER_POOL_CONTROL_HINT
    assert _const("ADD_LEGACY_LABEL") == LEGACY_LABEL
    assert _const("ADD_DEFAULT_TYPE") == DEFAULT_TYPE == PLAYER_POOL_CONTROL_DEFAULT_TYPE


def test_the_old_set_dropdown_and_added_list_are_gone_from_the_script():
    assert "SET_VALUES" not in SOURCE and "Remove" not in re.findall(r"var POOL_VALUES = .*", SOURCE)[0]
    assert "removeFromAddedList_" not in SOURCE and "ADDED_HEADER" not in SOURCE


def test_every_edit_path_puts_the_formula_back_or_leaves_the_cell_alone():
    """No branch of the Pool edit may leave a typed value behind: each early exit that reaches the cell
    writes the formula back first (the one exception, a cell with no header above, is not a Pool control)."""
    body = SOURCE.split("function handlePoolEdit_")[1].split("function restoreFormula_")[0]
    # row without an Id, an invalid value, a player EdgeRaw lacks, and the set / remove that succeeded
    assert body.count("restoreFormula_(cell, formula)") == 4 and "cell.setFormula" not in body
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


def _scenario(edits, *, edge_ids=(1001, 1002), player_pool_box=None, strict_rejects=False):
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
        ["Both", player_pool_box if player_pool_box is not None else HINT_TEXT, "", "", "Kenneth Walker III"],
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
        "strictRejects": strict_rejects,
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


BOX = {"sheet": "Player Pool", "row": 1, "col": 2}  # the box is the Name column's cell in row 1


@needs_node
def test_the_add_a_player_box_sets_edgeraw_pool_to_the_chosen_type_and_puts_the_hint_back(tmp_path):
    scenario = _scenario([{**BOX, "values": ["kenneth walker"]}])
    state = _run_flow(tmp_path, scenario)
    assert _cell(state, "EdgeRaw", 2, 1)["value"] == "Both"  # the resolved name, the default type
    assert _cell(state, "Player Pool", 1, 2)["value"] == HINT_TEXT  # the hint is back, not blank
    assert any("Added Kenneth Walker III (Both)" in t for t in state["toasts"])

    gpp = _scenario([{**BOX, "values": ["kenneth walker"]}])
    gpp["sheets"]["Player Pool"]["rows"][0][0] = "GPP"
    assert _cell(_run_flow(tmp_path, gpp), "EdgeRaw", 2, 1)["value"] == "GPP"

    unknown = _scenario([{**BOX, "values": ["nobody"]}])
    unknown["sheets"]["Player Pool"]["rows"][0][4] = ""  # the resolver found nothing
    state = _run_flow(tmp_path, unknown)
    assert _cell(state, "EdgeRaw", 2, 1)["value"] is None and any(
        "nothing added" in t for t in state["toasts"]
    )


@needs_node
def test_the_box_is_found_from_the_header_so_a_blank_row_one_cannot_kill_it(tmp_path):
    # Found live on Weeks 5 and 6: the old script found the box from a label cell, and a name typed over
    # the label left it nowhere to look. Here nothing else in row 1 says "add a player" at all.
    scenario = _scenario([{**BOX, "values": ["kenneth walker"]}], player_pool_box="")
    state = _run_flow(tmp_path, scenario)
    assert _cell(state, "EdgeRaw", 2, 1)["value"] == "Both"


@needs_node
def test_clearing_the_box_puts_the_hint_back_and_adds_nobody(tmp_path):
    state = _run_flow(tmp_path, _scenario([{**BOX, "values": [None]}]))
    assert _cell(state, "Player Pool", 1, 2)["value"] == HINT_TEXT
    assert _cell(state, "EdgeRaw", 2, 1)["value"] is None


@needs_node
def test_putting_the_hint_or_the_old_label_in_the_box_adds_nobody(tmp_path):
    for text in (HINT_TEXT, LEGACY_LABEL):
        state = _run_flow(tmp_path, _scenario([{**BOX, "values": [text]}]))
        assert _cell(state, "EdgeRaw", 2, 1)["value"] is None and state["toasts"] == []


@needs_node
def test_editing_the_type_dropdown_above_the_header_never_clears_it(tmp_path):
    state = _run_flow(tmp_path, _scenario([{"sheet": "Player Pool", "row": 1, "col": 1, "values": ["Cash"]}]))
    assert _cell(state, "Player Pool", 1, 1)["value"] == "Cash"  # no Pool header above it: left as typed
    assert _cell(state, "EdgeRaw", 2, 1)["value"] is None


@needs_node
def test_a_strict_dropdown_that_refuses_the_formula_still_gets_it_back_and_keeps_its_rule(tmp_path):
    """Sam's "Pool change failed: The data you entered in cell A741 violates the data validation rules": a
    strict dropdown refused the restoring setFormula and the edit left the cell empty."""
    edits = [
        {"sheet": "Edge Finder", "row": 4, "col": 1, "values": ["Both"]},
        {"sheet": "Edge Finder", "row": 4, "col": 1, "values": [None]},  # remove: the result is blank
    ]
    state = _run_flow(tmp_path, _scenario(edits, strict_rejects=True))
    cell = _cell(state, "Edge Finder", 4, 1)
    assert cell["formula"] and cell["formula"].startswith("=IF($D4")  # not left empty
    assert cell["validated"] is True  # the dropdown is back
    assert not any("failed" in t for t in state["toasts"])
    assert _cell(state, "EdgeRaw", 2, 1)["value"] is None  # and the removal landed


@needs_node
def test_a_non_strict_dropdown_never_needs_the_workaround(tmp_path):
    scenario = _scenario(
        [{"sheet": "Edge Finder", "row": 5, "col": 1, "values": ["GPP"]}], strict_rejects=True
    )
    for v in scenario["sheets"]["Edge Finder"]["validations"]:
        v["strict"] = False
    state = _run_flow(tmp_path, scenario)
    assert _cell(state, "Edge Finder", 5, 1)["formula"].startswith("=IF($D5")
    assert _cell(state, "EdgeRaw", 3, 1)["value"] == "GPP"


# --- Fix FLEX order (Week 6): the same rule as flex_order.py, pinned to the same cases ----------------------


def test_the_flex_constants_match_the_python_rule():
    from dfs import flex_order
    from dfs.kickoff import KICKOFF_TIMEZONE
    from dfs.models import ROSTER_SLOTS
    from dfs.sheet_lineup_keys import LINEUP_KEY_HEADER

    assert _list_const("SLOT_ORDER") == list(ROSTER_SLOTS)
    assert _const("FLEX_SLOT") == flex_order.FLEX_SLOT
    base = re.search(r"^var FLEX_BASE = \{(.*?)\};", SOURCE, re.MULTILINE).group(1)
    assert {k.strip(): int(v) for k, v in (p.split(":") for p in base.split(","))} == flex_order.BASE_SLOTS
    assert _const("KICKOFF_TIMEZONE") == KICKOFF_TIMEZONE
    assert _const("KEY_HEADER") == LINEUP_KEY_HEADER and _const("POSITION_HEADER") == "Position"
    assert _const("GAMESTART_HEADER") == "GameStart"


def test_the_menu_offers_fix_flex_order_and_the_version_moved_on():
    assert ".addItem('Fix FLEX order', 'fixFlexOrder')" in SOURCE
    assert apps_script.repo_script_version() >= 4
    body = SOURCE.split("function fixFlexOrder")[1].split("function edgeKickoffs_")[0]
    assert "getSheetByName(LINEUPS_TAB)" in body and "getActiveSheet" not in SOURCE
    assert "ButtonSet.YES_NO" in body  # asks before it changes anything


_SLOTS = ["QB", "RB", "RB", "WR", "WR", "WR", "TE", "FLEX", "DST"]
_WR_FLEX = ["QB", "RB", "RB", "WR", "WR", "WR", "TE", "WR", "DST"]
_EARLY, _SNF, _MON = "2030-10-20T13:00:00", "2030-10-20T20:20:00", "2030-10-21T20:15:00"
_BEFORE, _DURING = "2030-10-20T11:00:00", "2030-10-20T14:00:00"


def _seats(positions, starts):
    return [
        {"slot": s, "name": f"P{i}", "position": pos, "start": st}
        for i, (s, pos, st) in enumerate(zip(_SLOTS, positions, starts, strict=True))
    ]


@needs_node
@pytest.mark.parametrize(
    ("positions", "starts", "now", "expected"),
    [
        (
            _WR_FLEX,
            [_EARLY] * 5 + [_SNF] + [_EARLY] * 3,
            _BEFORE,
            {"status": "move", "toFlex": 5, "fromFlex": 7},
        ),
        (_WR_FLEX, [_EARLY] * 7 + [_SNF, _EARLY], _BEFORE, {"status": "ok"}),
        (_WR_FLEX, [_EARLY] * 5 + [_SNF, _EARLY, _SNF, _EARLY], _BEFORE, {"status": "tie"}),
        (
            _WR_FLEX,
            [_EARLY] * 5 + [_SNF] + [_EARLY] * 3,
            _DURING,
            {"status": "locked", "toFlex": 5, "fromFlex": 7},
        ),
        (_WR_FLEX, [_EARLY] * 5 + [""] + [_EARLY] * 3, _BEFORE, {"status": "unknown"}),
        (
            ["QB", "RB", "RB", "WR", "WR", "WR", "TE", "RB", "DST"],
            [_EARLY, _EARLY, _MON] + [_EARLY] * 6,
            _BEFORE,
            {"status": "move", "toFlex": 2, "fromFlex": 7},
        ),
        (
            ["QB", "RB", "RB", "WR", "WR", "WR", "TE", "QB", "DST"],
            [_EARLY] * 9,
            _BEFORE,
            {"status": "incomplete"},
        ),
    ],
)
def test_plan_flex_swap_agrees_with_the_python_rule(node_module, positions, starts, now, expected):
    got = _run_node(
        node_module, f"m.planFlexSwap({json.dumps(_seats(positions, starts))}, {json.dumps(now)})"
    )
    assert got == expected


@needs_node
def test_a_lineup_with_a_blank_name_is_incomplete_and_a_kickoff_parses_as_eastern_text(node_module):
    seats = _seats(_WR_FLEX, [_EARLY] * 9)
    seats[3]["name"] = ""
    assert _run_node(node_module, f"m.planFlexSwap({json.dumps(seats)}, {json.dumps(_BEFORE)})") == {
        "status": "incomplete"
    }
    assert _run_node(node_module, "m.parseKickoff('2030-10-20T20:20:00Z')") == "2030-10-20T20:20:00"
    assert _run_node(node_module, "['', null, 'soon'].map(m.parseKickoff)") == ["", "", ""]


@needs_node
def test_plays_label_matches_the_python_wording(node_module):
    from dfs.flex_order import plays_label

    cases = [_SNF, "2030-10-21T20:15:00", "2030-10-17T20:15:00", "2030-10-20T16:25:00"]
    got = _run_node(node_module, f"{json.dumps(cases)}.map(m.playsLabel)")
    # Python reads the same Eastern wall-clock text through kickoff.py
    from dfs.kickoff import parse_kickoff

    assert got == [plays_label(parse_kickoff(c + "Z")) for c in cases]


@needs_node
def test_find_lineup_blocks_numbers_header_first_player_and_total_rows(node_module):
    grid = _lineups_grid(blocks=2)
    got = _run_node(node_module, f"m.findLineupBlocks({json.dumps(grid)})")
    assert got["blocks"] == [{"header": 1, "first": 2, "total": 11}, {"header": 14, "first": 15, "total": 24}]
    assert got["skipped"] == []


# --- Copy builds to Lineups (plural since version 6): Python and the script read the same Suggested grid ---


def _suggested_grid(builds=((1, "Cash"), (2, "GPP"), (3, "GPP")), names_per_build=9):
    """A Suggested grid in `sheet_suggested`'s layout: three lines, a blank, then the blocks (a bar with the
    kind over the lock column, the decision over Name and the title after it; a lock tick before each name)."""
    grid = [["SUGGESTED LINEUPS"], ["status"], ["how to"], []]
    for number, kind in builds:
        grid.append([f"Build {number}", kind, "Keep", f"{kind} build {number}"])
        grid.append(["Slot", "🔒", "Name", "Team", "Opp"])
        for i in range(names_per_build):
            slot = _SLOTS[i] if i < len(_SLOTS) else ""
            grid.append([slot, i == 0, f"B{number} Player {i}", "DET", "ARI"])
        grid.append(["Total", "", "", "", "", 49900])
        grid.append([])
    return grid


def _int_const(name: str) -> int:
    match = re.search(rf"^var {name} = (\d+);", SOURCE, re.MULTILINE)
    assert match, f"Code.gs has no `var {name} = <number>;`"
    return int(match.group(1))


def test_the_suggested_columns_the_script_reads_are_the_ones_python_derives_from_its_column_list():
    """The lock column moved Name one column right (version 7): the script keeps its own copies of where the
    kind, the Name and the title sit, and they must be the positions `sheet_suggested` derives from COLUMNS."""
    from dfs import sheet_suggested as ss

    assert ss.COLUMNS[ss.NAME_INDEX] == "Name" and ss.COLUMNS[ss.LOCK_INDEX] == ss.LOCK_HEADER
    assert _int_const("SUGGESTED_NAME_COL") == ss.NAME_INDEX
    assert _int_const("SUGGESTED_KIND_COL") == ss.KIND_INDEX
    assert _int_const("SUGGESTED_TITLE_COL") == ss.TITLE_INDEX
    assert _int_const("SUGGESTED_READ_COLUMNS") == ss.TITLE_INDEX + 1
    assert apps_script.repo_script_version() >= 7


def test_the_script_names_match_the_suggested_tab_python_writes():
    from dfs import sheet_suggested as ss
    from dfs.sheet_lineup_sim import MARKER_OPTIONS

    assert _const("SUGGESTED_TAB") == ss.TAB == "Suggested" and _const("BUILD_LABEL") == ss.BUILD_LABEL
    assert _list_const("MARKER_VALUES") == MARKER_OPTIONS == ["Cash", "GPP"]
    assert ".addItem('Copy builds to Lineups…', 'copyBuildsToLineups')" in SOURCE


@needs_node
def test_parse_builds_agrees_with_the_python_parser_on_the_same_grid(node_module):
    from dfs import sheet_suggested as ss

    grid = _suggested_grid()
    short = _suggested_grid(builds=((7, "Cash"),), names_per_build=8)  # only eight names: not a build
    for g in (grid, grid + short[4:]):
        from_js = _run_node(node_module, f"m.parseBuilds({json.dumps(g)})")
        assert from_js == ss.parse_builds(g)
    assert [b["number"] for b in ss.parse_builds(grid + short[4:])] == [1, 2, 3]


@needs_node
def test_the_script_reads_the_grid_the_writer_really_produces_on_any_round(node_module):
    """Not just the fixture: the real `build_layout` (a decision on the bar, ticks, a note row, the rules box
    above and a History block below) read by the script and by Python alike."""
    import pandas as pd

    from dfs import sheet_suggested as ss
    from dfs.late_swap_search import SwapPlayer
    from dfs.lineup_suggest import Build, SuggestReport
    from dfs.models import ROSTER_SLOTS

    def build(number, kind):
        positions = ["QB", "RB", "RB", "WR", "WR", "WR", "TE", "WR", "DST"]
        players = tuple(
            SwapPlayer(f"B{number} {slot}{i}", pos, "DET", "ARI", "g", 5000, 10.0)
            for i, (slot, pos) in enumerate(zip(ROSTER_SLOTS, positions, strict=True))
        )
        return Build(
            number, kind, f"{kind} build {number}", players, 45000, 90.0, 0.5, 0.02, 100.0, 130.0,
            decision="Keep", locked=(players[0].name,), yours=(players[1].name,), note="kept as it was",
        )  # fmt: skip

    report = SuggestReport(
        builds=[build(1, "cash"), build(2, "gpp")], seconds=1.0, round=3,
        rules={"Always use": "Bijan"}, history=["Round 1  ·  fresh run", "Round 3  ·  rerun"],
    )  # fmt: skip
    names = [p.name for b in report.builds for p in b.players]
    edge = pd.DataFrame({"Name": names, "GameStart": [""] * len(names)})
    grid = ss.build_layout(report, edge, metric="CalPts", cash_line=1.0, gpp_target=2.0).rows
    from_js = _run_node(node_module, f"m.parseBuilds({json.dumps(grid)})")
    assert from_js == ss.parse_builds(grid) and [b["number"] for b in from_js] == [1, 2]
    assert [b["kind"] for b in from_js] == ["Cash", "GPP"] and from_js[1]["title"] == "gpp build 2"
    assert from_js[0]["names"] == [p.name for p in report.builds[0].players]


def _empty_lineups(blocks=3, filled=()):
    """`_lineups_grid` with every name cleared (a name back in the third row of each block in `filled`)."""
    grid = _lineups_grid(blocks=blocks)
    stride = 13  # a header, nine player rows, the Total and Remaining rows, and a blank row
    for block in range(blocks):
        top = block * stride
        for p in range(9):
            grid[top + 1 + p][0] = f"Old {block} {p}" if block in filled and p == 2 else ""
        grid[top + 10][0] = ""  # the Total row's marker
    return grid


def _plan(node_module, suggested, text, lineups):
    return _run_node(
        node_module, f"m.planCopyBuilds({json.dumps(suggested)}, {json.dumps(text)}, {json.dumps(lineups)})"
    )


@needs_node
def test_copy_builds_fill_the_empty_blocks_in_order_and_set_each_marker(node_module):
    suggested, lineups = _suggested_grid(), _empty_lineups(blocks=3, filled=(0,))
    plan = _plan(node_module, suggested, "3, 1", lineups)
    assert plan["status"] == "ok" and plan["unplaced"] == [] and plan["unknown"] == []
    # block 0 holds a name (skipped); builds go in the order typed, into blocks 1 and 2 in order
    assert [(p["build"]["number"], p["marker"], p["block"]) for p in plan["placements"]] == [
        (3, "GPP", {"header": 14, "first": 15, "total": 24}),
        (1, "Cash", {"header": 27, "first": 28, "total": 37}),
    ]
    assert plan["placements"][0]["build"]["names"][0] == "B3 Player 0"


@needs_node
def test_copy_all_takes_every_build_in_order_and_reports_what_did_not_fit(node_module):
    suggested = _suggested_grid()
    plan = _plan(node_module, suggested, "ALL", _empty_lineups(blocks=2))
    assert [p["build"]["number"] for p in plan["placements"]] == [1, 2]  # two empty blocks, three builds
    assert [b["number"] for b in plan["unplaced"]] == [3]
    summary = _run_node(node_module, f"m.planSummary({json.dumps(plan)}, true)")
    assert (
        "Build 1 (Cash) into the lineup at row 1" in summary and "No empty lineup left for build 3" in summary
    )
    after = _run_node(node_module, f"m.planSummary({json.dumps(plan)}, false)")
    assert after.startswith("Copied:") and "went into" in after and "not copied" in after


@needs_node
def test_copy_reports_numbers_not_on_suggested_and_text_it_cannot_read(node_module):
    plan = _plan(node_module, _suggested_grid(), "2; 9 x 2 12", _empty_lineups(blocks=3))
    assert [p["build"]["number"] for p in plan["placements"]] == [2]  # a number typed twice is used once
    assert plan["unknown"] == [9, 12] and plan["unreadable"] == ["x"]
    summary = _run_node(node_module, f"m.planSummary({json.dumps(plan)}, true)")
    assert "Not on Suggested: 9, 12." in summary and "Not understood: x." in summary


@needs_node
def test_copy_builds_never_overwrite_a_block_with_a_name_and_say_when_nothing_is_free(node_module):
    suggested = _suggested_grid()
    full = _empty_lineups(blocks=2, filled=(0, 1))  # a name in every block
    got = _plan(node_module, suggested, "1", full)
    assert got["status"] == "noBlock" and [b["number"] for b in got["builds"]] == [1]
    free = _empty_lineups(blocks=2)
    assert _plan(node_module, suggested, "9", free) == {"status": "noBuild", "unknown": [9], "unreadable": []}
    assert _plan(node_module, [], "1", free)["status"] == "noBuild"
    assert _plan(node_module, suggested, "  ", free) == {"status": "noChoice", "unreadable": []}
    assert _plan(node_module, suggested, "what", free) == {"status": "noChoice", "unreadable": ["what"]}


@needs_node
def test_the_prompt_lists_the_builds_as_runs_by_kind(node_module):
    builds = _run_node(node_module, f"m.parseBuilds({json.dumps(_suggested_grid())})")
    assert _run_node(node_module, f"m.describeBuilds({json.dumps(builds)})") == "Cash 1, GPP 2-3"


def test_the_copy_action_asks_before_it_writes_and_only_writes_names_and_the_marker():
    body = SOURCE.split("function copyBuildsToLineups")[1].split("// ----")[0]
    assert "ButtonSet.YES_NO" in body and "getActiveSheet" not in SOURCE
    assert body.count(".setValues(") == 1 and body.count(".setValue(") == 1  # per build: nine names, marker
    assert body.index("ButtonSet.YES_NO") < body.index(".setValues(")  # it asks before it writes
    assert "clearContent" not in body
