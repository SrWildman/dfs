"""The Explorer (`week_explorer`): every lens's columns exist on EdgeRaw (a rename fails here, loudly, not as
an empty column on Sunday), every priced player is in the data, the trust chips, the controls the script
drives are all on the page, and the page carries one inline data block and one script, nothing loaded."""

import json
import re
from dataclasses import replace

import pytest
from test_week_plays import WED, row, slate_rows, write_csv

from dfs import derived, week_board, week_explorer, week_inputs, week_top


def inputs(d, rows):
    write_csv(d / "current" / "edge.csv", rows)
    return replace(week_inputs.gather(2026, 9, WED, d), config={})


@pytest.mark.parametrize("lens", week_explorer.LENSES, ids=lambda lens: lens.key)
def test_every_lens_column_is_an_edgeraw_column_or_one_derived_here(lens):
    known = set(derived.EDGE_SHEET_ORDER) | set(week_explorer.DERIVED)
    missing = [c for c in (*week_explorer.BASE, *lens.columns, lens.sort) if c not in known]
    assert not missing, f"the {lens.label} lens names columns EdgeRaw no longer has: {missing}"


def test_all_columns_is_every_edgeraw_column_but_the_labels_and_helpers():
    cols = week_explorer.columns()
    for c in derived.EDGE_SHEET_ORDER:
        if c in week_explorer.LABELS or c in week_explorer.HIDDEN:
            assert c not in cols
        else:
            assert c in cols, f"{c} is on EdgeRaw but not in the Explorer"
    grouped = [c for _, cs in week_explorer.groups() for c in cs]
    assert sorted(grouped) == sorted(cols)  # every column in exactly one group


def test_every_column_has_a_trust_chip():
    for c in week_explorer.columns():
        label, why = week_explorer.trust_of(c)
        assert label in ("Proven", "Shadow", "Untested", "Data") and why
    assert week_explorer.trust_of("Salary")[0] == "Data"
    assert week_explorer.trust_of("CalPts")[0] == "Proven"


def test_the_data_has_every_priced_player_not_out_with_the_pool_and_roles(tmp_path):
    rows = [*slate_rows(), row("No Price", "WR", "A", "B", 0, 3.0)]
    inp = inputs(tmp_path, rows)
    b = week_board.board(inp)
    data = week_explorer.payload(inp, b, {"id-Qb Alpha": "Both"})
    names = {
        r[data["columns"].index(next(c for c in data["columns"] if c["key"] == "Name"))] for r in data["rows"]
    }
    assert "Wr Out" not in names and "No Price" not in names and "Wr Star" in names  # Q stays, OUT goes
    assert len(data["rows"]) == len(data["ids"]) == 9
    keys = [c["key"] for c in data["columns"]]
    alpha = data["rows"][data["ids"].index("id-Qb Alpha")]
    assert alpha[keys.index("Pool")] == "Both"
    assert alpha[keys.index(week_explorer.PER_K)] == pytest.approx(4.0)  # 24 / 6
    lens = {lens["key"]: lens for lens in data["lenses"]}
    assert lens["value"]["columns"][-1] == "Pool" and lens["value"]["chart"] == "value"
    assert lens["gpp"]["chart"] == "leverage" and lens["signals"]["signal_only"]


def test_the_data_block_cannot_close_its_script_tag():
    text = week_explorer.payload_json({"rows": [["</script><b>x"]]})
    assert "</script" not in text and json.loads(text)["rows"][0][0] == "</script><b>x"


def test_every_control_the_script_drives_is_on_the_page(tmp_path):
    inp = inputs(tmp_path, slate_rows())
    data = week_explorer.payload(inp, week_board.board(inp), {})
    page = week_top.explorer_html(inp, data) + week_top.DRAWER
    ids = set(re.findall(r"#((?:ex-[a-z]+)|drawer|explorer)\b", week_explorer.JS))
    assert ids >= {
        "ex-pos",
        "ex-team",
        "ex-game",
        "ex-kick",
        "ex-q",
        "ex-pool",
        "ex-role",
        "ex-sig",
        "ex-name",
    }
    for i in ids:
        assert f'id="{i}"' in page, f"the script looks for #{i}, which the page does not have"
    for n in ("3", "10", "25", "all"):
        assert f'data-show="{n}"' in page  # show 3 / 10 / 25 / all
    assert 'data-per="pos"' in page and 'data-per="all"' in page
    for chart in ("value", "leverage", "usage"):
        assert f'data-chart="{chart}"' in page  # charts that follow the lens
    assert "localStorage" in week_explorer.JS and "try" in week_explorer.JS  # remembered, never required


def test_the_explorer_script_is_inline_with_its_nonce(tmp_path):
    inp = inputs(tmp_path, slate_rows())
    data = week_explorer.payload(inp, week_board.board(inp), {})
    out = week_top.explorer_script(data, ' nonce="abc"')
    assert out.count("<script") == 2 and '<script nonce="abc">' in out and " src=" not in out
    assert week_top.explorer_script({"rows": []}, "") == ""  # nothing synced: no script at all
