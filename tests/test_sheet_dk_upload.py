"""The DK Upload tab's entries layout: the build step, protection, the grid it writes, the Lineups read."""

import sys

import pytest

sys.path.insert(0, "tests")
import test_entries as te  # noqa: E402

from dfs import entries as ent  # noqa: E402
from dfs import sheet_dk_upload as dku  # noqa: E402
from dfs.lineups import DK_UPLOAD_HEADER, DK_UPLOAD_SHEET_HEADER  # noqa: E402
from dfs.sheet_protection import protect_dk_upload, protect_workbook  # noqa: E402
from dfs.weekly_reset import DK_UPLOAD_RANGE, LINEUPS_NAME_BLOCKS  # noqa: E402


class RecordingClient:
    def __init__(self, header=None, present=True):
        self.header = header if header is not None else list(DK_UPLOAD_HEADER)
        self.present = present
        self.calls: list[tuple] = []

    def tab_exists(self, tab):
        return self.present

    def read_range(self, tab, a1):
        return [self.header] if a1 == "A1:1" else []

    def __getattr__(self, name):  # every other write is recorded by name
        def record(*args, **kwargs):
            self.calls.append((name, args, kwargs))

        return record

    def named(self, name):
        return [c for c in self.calls if c[0] == name]


def test_the_build_writes_dks_columns_then_format_lineup_suggested_why():
    client = RecordingClient()
    results = dku.build_dk_upload(client, "DK Upload")
    [(_, (_, a1, rows), _)] = client.named("update_range")
    assert a1 == "A1:Q1" and rows == [list(DK_UPLOAD_SHEET_HEADER)]
    assert DK_UPLOAD_SHEET_HEADER[13:] == ("Format", "Lineup", "Suggested", "Why")
    assert any("Lineup dropdown" in r for r in results)


def test_the_lineup_dropdown_is_l1_to_l20_over_the_derived_range_with_the_typed_cell_cue():
    client = RecordingClient()
    dku.build_dk_upload(client, "DK Upload")
    [(_, (_, rng, options), _)] = client.named("set_dropdown_validation")
    assert rng == "O2:O500" == dku.lineup_range()
    assert options == [f"L{i}" for i in range(1, len(LINEUPS_NAME_BLOCKS) + 1)] and options[-1] == "L20"
    from dfs.sheet_style import BAND_BG, INPUT_BG

    fills = {c[1][1]: c[1][2]["backgroundColor"] for c in client.named("format_range")}
    assert fills["O2:O500"] == INPUT_BG  # pale yellow: the workbook's one "type here" cue
    assert fills["P2:Q500"] == BAND_BG  # Suggested / Why: grey, read-only
    [(_, (_, notes), _)] = client.named("set_cell_notes")
    assert set(notes) == {"O1", "P1"} and "never fills this column" in notes["O1"]


def test_the_build_skips_a_missing_tab_and_never_writes_an_entry_row():
    assert "not present" in dku.build_dk_upload(RecordingClient(present=False), "DK Upload")[0]
    client = RecordingClient()
    dku.build_dk_upload(client, "DK Upload")
    assert all(c[1][1].startswith("A1") for c in client.named("update_range"))  # row 1 only


def test_protection_exempts_only_the_lineup_dropdown():
    client = RecordingClient(header=list(DK_UPLOAD_SHEET_HEADER))
    line = protect_dk_upload(client, "DK Upload")
    [(_, (tab,), kw)] = client.named("protect_sheet")
    assert tab == "DK Upload" and kw["unprotected_ranges"] == ["O2:O500"] == dku.unprotected_ranges()
    assert "except the Lineup dropdown" in line and client.named("clear_protected_ranges")


def test_protection_waits_for_the_layout():
    client = RecordingClient(header=list(DK_UPLOAD_HEADER))
    assert "not built yet" in protect_dk_upload(client, "DK Upload")
    assert not client.named("protect_sheet")


def test_protect_workbook_includes_dk_upload_only_when_asked():
    class Client(RecordingClient):
        def read_range(self, tab, a1):
            return [list(DK_UPLOAD_SHEET_HEADER)] if tab == "DK Upload" else [["Pool", "Name"]]

    asked = Client()
    protect_workbook(asked, lineups_tab="Lineups", dk_upload_tab="DK Upload")
    assert "DK Upload" in [c[1][0] for c in asked.named("protect_sheet")]
    unasked = Client()
    protect_workbook(unasked, lineups_tab="Lineups")
    assert "DK Upload" not in [c[1][0] for c in unasked.named("protect_sheet")]


def test_week_new_clears_every_column_of_the_layout_but_not_the_header():
    assert DK_UPLOAD_RANGE == "A2:Q1000"  # derived from the header: it reaches Why


def test_the_grid_pads_so_a_shorter_list_leaves_no_stale_rows_and_fits_the_tab(tmp_path):
    client = RecordingClient()
    parsed = ent.read_entries_file(te.write_entries_file(tmp_path / "e.csv"))
    plan = ent.plan_fill(parsed, {}, te.sheet_lineups(), now=te.BEFORE)
    grid = dku.tab_grid(plan.rows, kept=[])
    dku.write_tab_rows(client, "DK Upload", grid, previous_rows=12)
    [(_, (_, a1, rows), _)] = client.named("update_range")
    assert a1 == "A2:Q13" and len(rows) == 12 and not any(rows[-1])  # 8 entries + 4 blank stale rows
    assert rows[0][:4] == ["9001", "NFL GIANT $25 Double Up [Single Entry]", "700", "$25"]
    with pytest.raises(ValueError):
        dku.write_tab_rows(client, "DK Upload", [[""] * 17] * 600, previous_rows=0)


def test_changed_slots_rewrite_only_the_nine_slot_cells_of_the_rows_given():
    client = RecordingClient()
    tab_rows = {
        "9001": ent.TabRow("9001", "L1", ("",) * 9, row=2),
        "9003": ent.TabRow("9003", "L1", ("",) * 9, row=4),
    }
    changed = [
        ent.RowPlan(
            ent.Entry(ent.FileEntry("9003", "c", "9", "$1", ("",) * 9), "H2H"), 1, cells=te.LINEUP_1_AT_DK
        )
    ]
    dku.write_changed_slots(client, "DK Upload", changed, tab_rows)
    [(_, (_, updates), _)] = client.named("update_ranges")
    assert updates == {"E4:M4": [te.LINEUP_1_AT_DK]}  # one row, QB..DST only: not Lineup, not Suggested


def test_the_lineups_tab_is_read_by_header_with_marker_and_total_row_numbers():
    header = ["Name", "Pts", "AggPts", "CalPts", "Own%", "Val", "ValAdj", "Hit3x%", "Boom%"]

    class Client:
        def read_range(self, tab, a1):
            return [header]

        def read_range_unformatted(self, tab, a1):
            grid = [header]
            for number, (start, _end) in enumerate(LINEUPS_NAME_BLOCKS, start=1):
                while len(grid) < start - 1:
                    grid.append([""])
                names = te.LINEUP_1 if number == 1 else ("",) * 9
                grid.extend([n] for n in names)
                total = ["GPP" if number == 1 else "", 120.5, "", 141.2, 1.234, "", "", 0.55, 0.031]
                grid.append(total if number == 1 else [""])
            return grid

    lineups = dku.read_lineups(Client(), "Lineups")
    assert len(lineups) == len(LINEUPS_NAME_BLOCKS)
    one = lineups[0]
    assert one.names == te.LINEUP_1 and one.marker == "GPP" and one.number == 1 and one.label == "L1"
    assert (one.proj_pts, one.cal_pts, one.own, one.cash_pct, one.top1) == (120.5, 141.2, 1.234, 0.55, 0.031)
    assert not lineups[1].built and lineups[1].cal_pts is None
