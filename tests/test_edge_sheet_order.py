"""EdgeRaw's sheet order (`derived.EDGE_SHEET_ORDER`) and the rule that goes with it: every EdgeRaw column
letter and VLOOKUP index comes from the name-to-letter functions, never from `EDGE_COLUMNS.index`."""

import re
from pathlib import Path

import dfs
from dfs import derived
from dfs.derived import (
    EDGE_COLUMNS,
    EDGE_DATA_OFFSET,
    EDGE_SHEET_ORDER,
    ZONE_LABELS,
    edge_last_letter,
    edge_sheet_index,
    edge_sheet_letter,
    edge_vlookup_index,
)
from dfs.sheet_links import LINKED_EDGE_COLUMNS
from dfs.sheet_style import EDGE_COLUMN_GROUPS

SRC = Path(dfs.__file__).parent


def test_the_sheet_order_is_a_permutation_of_the_append_only_frame_order():
    assert sorted(EDGE_SHEET_ORDER) == sorted(EDGE_COLUMNS) and len(set(EDGE_SHEET_ORDER)) == len(
        EDGE_SHEET_ORDER
    )
    assert EDGE_SHEET_ORDER != EDGE_COLUMNS  # the tab has its own designed order
    assert (
        EDGE_COLUMNS[-len(derived.EDGE_FINDER_COLUMNS) :] == derived.EDGE_FINDER_COLUMNS
    )  # still append-only


def test_no_code_outside_derived_computes_an_edgeraw_letter_from_edge_columns():
    pattern = re.compile(r"EDGE_COLUMNS\s*\.\s*index\(|len\(\s*EDGE_COLUMNS\s*\)|EDGE_COLUMNS\)\s*-\s*1")
    offenders = []
    for path in sorted(SRC.rglob("*.py")):
        if path.name == "derived.py":
            continue
        for number, line in enumerate(path.read_text().splitlines(), start=1):
            if pattern.search(line) and not line.lstrip().startswith("#"):
                offenders.append(f"{path.relative_to(SRC)}:{number}: {line.strip()}")
    assert not offenders, (
        "use derived.edge_sheet_letter / edge_sheet_index / edge_vlookup_index:\n" + "\n".join(offenders)
    )


def test_the_designed_order_pool_name_spine_then_zones_then_hidden_helpers():
    spine = EDGE_SHEET_ORDER[: EDGE_SHEET_ORDER.index("Edge") + 1]
    assert spine[:6] == ["Name", "Position", "Team", "Opp", "Salary", "ProjPts"]
    assert (
        spine.index("CalPts") == spine.index("AggPts") + 1
        and spine.index("Hit3x%") == spine.index("ValAdj") + 1
    )
    assert spine[-4:] == ["Own%", "Avail", "Flags", "Edge"][-4:] or spine[-1] == "Edge"
    assert edge_sheet_letter("Name") == "B" and EDGE_DATA_OFFSET == 1  # Pool is column A
    zones = [EDGE_SHEET_ORDER.index(label) for label in ZONE_LABELS]
    assert zones == sorted(zones) and zones[0] > EDGE_SHEET_ORDER.index("Edge")
    hidden = ["Id", "Flag", *derived.ALL_PCT_COLUMNS.values(), "NameKey"]
    assert EDGE_SHEET_ORDER[-len(hidden) :] == hidden  # helpers last
    for name in ("Floor", "CeilM", "Bust%"):  # the CEIL group
        assert (
            EDGE_SHEET_ORDER.index("CeilPct")
            < EDGE_SHEET_ORDER.index(name)
            < EDGE_SHEET_ORDER.index("ImpliedMove")
        )
    assert EDGE_SHEET_ORDER.index("xFP/G") > EDGE_SHEET_ORDER.index("Snap%")  # xFP/G in USAGE


def test_the_letter_functions_agree_with_each_other_and_the_vlookup_range():
    assert edge_sheet_index("Name") == 1 and edge_sheet_index("Id") == EDGE_SHEET_ORDER.index("Id") + 1
    assert edge_last_letter() == edge_sheet_letter(EDGE_SHEET_ORDER[-1])
    assert edge_vlookup_index("Name") == 1 and edge_vlookup_index("Position") == 2
    # every linked column sits to the right of Name, so one `EdgeRaw!$B:$<last>` VLOOKUP range reaches it
    assert all(edge_vlookup_index(name) >= 1 for name in LINKED_EDGE_COLUMNS)


def test_each_column_group_is_a_contiguous_run_of_the_sheet_order_with_its_label_just_before():
    for first, last in EDGE_COLUMN_GROUPS:
        i, j = EDGE_SHEET_ORDER.index(first), EDGE_SHEET_ORDER.index(last)
        assert i < j
        assert EDGE_SHEET_ORDER[i - 1] in ZONE_LABELS, (first, last)  # a visible label sits outside the fold
        assert not set(ZONE_LABELS) & set(EDGE_SHEET_ORDER[i : j + 1])


def test_the_edgeraw_rows_are_written_in_sheet_order_with_pool_first():
    import pandas as pd

    from dfs.sources.edge import EdgeSource

    frame = pd.DataFrame({c: [str(i)] for i, c in enumerate(EDGE_COLUMNS)})
    rows = EdgeSource().to_sheet_rows(frame)
    assert rows[0] == ["Pool", *EDGE_SHEET_ORDER]
    assert rows[1][0] == "" and rows[1][1] == frame["Name"].iloc[0]
    assert rows[1][1 + EDGE_SHEET_ORDER.index("Id")] == frame["Id"].iloc[0]  # values follow their headers


def test_no_hide_unhide_or_group_call_types_a_column_letter():
    """Every `hide_columns` / `group_columns` call gets its letters from a name, a header or a constant.
    A typed letter ("AL", "Q") is the bug slice 5 hit: the columns it
    named moved and the old hide stayed behind.
    ("A" alone is allowed: it means the first column of a tab that has no data columns before it.)"""
    import re
    from pathlib import Path

    call = re.compile(r"(?:hide_columns|group_columns)\((?P<args>[^)]*(?:\([^)]*\)[^)]*)*)\)")
    typed = re.compile(r'"(?!A")[A-Z]{1,2}"')
    offenders = []
    for path in sorted(Path(__file__).resolve().parents[1].glob("src/dfs/*.py")):
        for match in call.finditer(path.read_text()):
            if typed.search(match.group("args")):
                offenders.append(f"{path.name}: {match.group(0)[:80]}")
    assert not offenders, offenders
