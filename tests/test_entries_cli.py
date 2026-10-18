"""`dfs entries fill` / `print` and `dfs setup dk-upload` end to end on a stateful fake sheet (no network, no
real sheet, no real DraftKings file): the entries file is the synthetic one in `test_entries`."""

import csv
import sys

import pytest
from typer.testing import CliRunner

sys.path.insert(0, "tests")
import test_entries as te  # noqa: E402

from dfs import cli  # noqa: E402
from dfs import sheet_dk_upload as dku  # noqa: E402
from dfs.config import Config  # noqa: E402
from dfs.lineups import DK_UPLOAD_HEADER, DK_UPLOAD_SHEET_HEADER  # noqa: E402
from dfs.sheets import column_letter  # noqa: E402
from dfs.weekly_reset import LINEUPS_NAME_BLOCKS  # noqa: E402

LINEUPS_HEADER = ["Name", "Pts", "CalPts", "Own%", "Val", "ValAdj", "Hit3x%", "Boom%"]


class FakeSheet:
    """DK Upload and Lineups as grids; records every write."""

    def __init__(self, upload_header, lineups):
        self.upload = {1: list(upload_header)}
        self.lineups = lineups
        self.writes: list[tuple[str, str, list]] = []

    def describe(self):
        return ("Week 7", "https://example.test/sheet")

    def read_range(self, tab, a1):
        if tab == "Lineups":
            return [LINEUPS_HEADER]
        if a1 == "A1:1":
            return [self.upload[1]]
        width = len(DK_UPLOAD_SHEET_HEADER)
        rows = sorted(r for r in self.upload if r > 1)
        return [self.upload[r] + [""] * (width - len(self.upload[r])) for r in rows]

    def read_range_unformatted(self, tab, a1):
        grid = [LINEUPS_HEADER]
        for number, (start, _end) in enumerate(LINEUPS_NAME_BLOCKS, start=1):
            lineup = self.lineups[number - 1]
            while len(grid) < start - 1:
                grid.append([""])
            for name in lineup.names:
                grid.append([name])
            grid.append(
                [
                    lineup.marker,
                    lineup.proj_pts,
                    lineup.cal_pts,
                    lineup.own,
                    "",
                    "",
                    lineup.cash_pct,
                    lineup.top1,
                ]
            )
        return grid

    def update_range(self, tab, a1, rows):
        self.writes.append(("update_range", a1, rows))
        first = int("".join(c for c in a1.split(":")[0] if c.isdigit()))
        for i, row in enumerate(rows):
            self.upload[first + i] = list(row)

    def update_ranges(self, tab, updates):
        self.writes.append(("update_ranges", "", updates))
        for a1, rows in updates.items():
            row = int("".join(c for c in a1.split(":")[0] if c.isdigit()))
            current = self.upload[row]
            col = DK_UPLOAD_SHEET_HEADER.index("QB")
            current[col : col + len(rows[0])] = rows[0]


@pytest.fixture
def env(tmp_path, monkeypatch):
    te.write_entries_file(tmp_path / "DraftKings Entries (4).csv")
    cfg = Config.model_validate(
        {
            "google_sheets": {
                "sheet_id": "abc",
                "credentials_file": "c.json",
                "tab_mappings": {"edge": "EdgeRaw"},
            },
            "lineups": {
                "upload_tab": "DK Upload",
                "builder_tab": "Lineups",
                "player_pool_tab": "Player Pool",
            },
            "bankroll": {"tab": "Bankroll"},
            "contests": {"downloads_dir": str(tmp_path)},
        }
    )
    sheet = FakeSheet(DK_UPLOAD_SHEET_HEADER, te.sheet_lineups(**te.STANDARD_LINEUPS))
    monkeypatch.setattr(cli, "_load_config_or_exit", lambda: cfg)
    monkeypatch.setattr(cli, "SheetsClient", lambda _cfg: sheet)
    monkeypatch.setattr(cli, "_entries_now", lambda: te.BEFORE)  # before any kickoff, whatever today is
    monkeypatch.chdir(tmp_path)
    return sheet, tmp_path


def run(*args):
    result = CliRunner(env={"COLUMNS": "300"}).invoke(cli.app, ["entries", *args])
    return result.exit_code, result.output


def choose(sheet, **picks):
    """Sam sets dropdowns on the tab (the rows a first `fill` laid out)."""
    col = DK_UPLOAD_SHEET_HEADER.index("Lineup")
    for row in sheet.upload.values():
        if row[0] in {k[1:] for k in picks}:
            row[col] = picks["e" + row[0]]


def test_fill_lays_out_every_classic_entry_with_a_blank_dropdown_and_a_suggestion(env):
    sheet, folder = env
    code, out = run("fill")
    assert code == 0
    assert "DraftKings Entries (4).csv" in out and "newest entries file" in out  # says which file it used
    assert "Skipped 1 entry: Showdown" in out
    assert "8 entries: 0 set, 8 blank" in out
    assert sheet.upload[1] == list(DK_UPLOAD_SHEET_HEADER)
    col = {h: DK_UPLOAD_SHEET_HEADER.index(h) for h in ("Format", "Lineup", "Suggested", "Why")}
    row = next(r for r in sheet.upload.values() if r[0] == "9001")
    assert (row[col["Format"]], row[col["Lineup"]], row[col["Suggested"]]) == ("Double Up", "", "L1")
    assert not any(row[4:13])  # slots stay blank until Sam picks a lineup
    assert not list(folder.glob("upload_*.csv"))  # no --write


def test_a_second_run_fills_the_slots_for_what_sam_set_and_write_makes_the_upload_file(env):
    sheet, folder = env
    run("fill")
    choose(sheet, e9001="L1", e9003="L1", e9007="L3")
    code, out = run("fill", "--write")
    assert code == 0
    assert "8 entries: 3 set (2 DU" not in out  # (9001 is the Double Up, 9003 an H2H)
    assert "3 set (1 DU + 1 H2H <- L1; 1 GPP <- L3), 5 blank" in out
    assert "3 to upload" in out
    row = next(r for r in sheet.upload.values() if r[0] == "9003")
    assert row[4:13] == te.LINEUP_1_AT_DK
    [upload] = folder.glob("upload_*.csv")
    rows = list(csv.reader(open(upload, encoding="utf-8")))
    assert rows[0] == list(DK_UPLOAD_HEADER)
    assert sorted(r[0] for r in rows[1:]) == ["9001", "9003", "9007"]
    # the dropdowns Sam set survived the rewrite
    lineup_col = DK_UPLOAD_SHEET_HEADER.index("Lineup")
    assert {r[0]: r[lineup_col] for r in sheet.upload.values() if r[0] != "Entry ID"}["9007"] == "L3"


def test_dry_run_writes_nothing(env):
    sheet, folder = env
    code, out = run("fill", "--dry-run", "--write")
    assert code == 0 and "Dry run: nothing was written" in out
    assert sheet.writes == [] and not list(folder.glob("upload_*.csv"))


def test_again_rewrites_only_the_slots_of_entries_whose_lineup_changed(env):
    sheet, _ = env
    run("fill")
    choose(sheet, e9001="L1", e9003="L1", e9007="L3")
    run("fill")
    # a late swap on lineup 1 (Sam edits the Lineups tab): Rob Early out, Rod Night in
    swapped = list(te.LINEUP_1)
    swapped[1] = "Rod Night"
    sheet.lineups[0] = te.ent.SheetLineup(1, tuple(swapped), "Cash", 0.58, 0.004, 141.0, 131.0, 1.26)
    sheet.writes.clear()
    code, out = run("fill", "--again", "--write")
    assert code == 0
    [(kind, _, updates)] = sheet.writes
    assert kind == "update_ranges" and len(updates) == 2  # entries 9001 and 9003; not 9007 (lineup 3)
    assert "2 to upload" in out


def test_print_gives_every_lineup_in_dk_format_without_any_pairing_and_writes_the_file(env):
    sheet, folder = env
    code, out = run("print")
    assert code == 0
    assert "DraftKings Entries (4).csv" in out
    assert "Lineups from: Week 7" in out
    line = next(ln for ln in out.splitlines() if ln.startswith("L1 |"))
    assert line.startswith("L1 | 141.0 | 131.0 | 126.0% | Quinn Early (1001) | Rob Early (2001)")
    [written] = folder.glob("dk_lineups_*.csv")
    rows = list(csv.reader(open(written, encoding="utf-8")))
    assert [r[0] for r in rows] == ["Lineup", "L1", "L2", "L3", "L4"]
    assert sheet.writes == []  # never writes a sheet


def test_fill_stops_when_the_tab_has_no_entries_layout_yet(env):
    sheet, _ = env
    sheet.upload[1] = list(DK_UPLOAD_HEADER)
    code, out = run("fill")
    assert code == 1 and "dfs setup dk-upload" in out and sheet.writes == []


def test_a_showdown_file_is_skipped_with_a_report(env):
    sheet, folder = env
    for old in folder.glob("*.csv"):
        old.unlink()
    te.write_entries_file(folder / "sd.csv", slots=("CPT", "FLEX", "FLEX", "FLEX", "FLEX", "FLEX"))
    code, out = run("fill")
    assert code == 0 and "Skipped 9 entries: Showdown file" in out and sheet.writes == []


def test_the_column_letters_are_derived_not_typed():
    assert column_letter(DK_UPLOAD_SHEET_HEADER.index("Lineup")) == "O"
    assert dku.lineup_range() == "O2:O500"
