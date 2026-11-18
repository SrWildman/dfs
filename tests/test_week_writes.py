"""The served Week page's writes (`week_writes.SheetEdits`, `week_actions`) against a fake sheet: cells found
by header, a re-read before every write, a conflict refused with nothing written, one batch per action, the
read back, the run lock held during the write, the closed week refused, and every write in edits.log."""

import contextlib
import re
from dataclasses import replace
from datetime import datetime

import pytest
from test_week_plays import WED, row, slate_rows, write_csv

from dfs import sheet_suggested as ss
from dfs import week_actions, week_board, week_inputs, week_writes
from dfs.sheets import column_letter


def col_index(letters: str) -> int:
    n = 0
    for ch in letters:
        n = n * 26 + ord(ch) - 64
    return n - 1


class FakeSheet:
    """A grid per tab; reads and single-cell batch writes in A1 notation, each call counted."""

    def __init__(self):
        self.tabs: dict[str, dict[tuple[int, int], str]] = {}
        self.reads = 0
        self.writes: list[tuple[str, dict]] = []
        self.on_write = None

    def put(self, tab, row, col, value):
        self.tabs.setdefault(tab, {})[(row, col)] = value

    def get(self, tab, row, col):
        return self.tabs.get(tab, {}).get((row, col), "")

    def tab_exists(self, tab):
        return tab in self.tabs

    def _range(self, tab, a1):
        grid = self.tabs.get(tab, {})
        if re.fullmatch(r"\d+:\d+", a1):
            r = int(a1.split(":")[0])
            width = max((c for (rr, c) in grid if rr == r), default=-1) + 1
            return [[self.get(tab, r, c) for c in range(width)]]
        m = re.fullmatch(r"([A-Z]+)(\d+):([A-Z]+)(\d+)", a1)
        c0, r0, c1, r1 = col_index(m[1]), int(m[2]), col_index(m[3]), int(m[4])
        last = max((r for (r, c) in grid if c0 <= c <= c1), default=r0 - 1)
        return [[self.get(tab, r, c) for c in range(c0, c1 + 1)] for r in range(r0, min(r1, last) + 1)]

    def read_range(self, tab, a1):
        self.reads += 1
        return self._range(tab, a1)

    def batch_read_ranges(self, pairs):
        self.reads += 1
        return [self._range(t, a) for t, a in pairs]

    def update_ranges(self, tab, updates):
        if self.on_write:
            self.on_write()
        self.writes.append((tab, updates))
        for a1, rows in updates.items():
            m = re.fullmatch(r"([A-Z]+)(\d+)", a1)
            self.put(tab, int(m[2]), col_index(m[1]), rows[0][0])


EDGE = "EdgeRaw"


@pytest.fixture
def sheet():
    s = FakeSheet()
    for c, h in enumerate(["Pool", "Name", "Team", "Id"]):  # found by header: not where pool.py assumes
        s.put(EDGE, 1, c, h)
    for i, r in enumerate(slate_rows(), start=2):
        s.put(EDGE, i, 1, r["Name"])
        s.put(EDGE, i, 2, r["Team"])
        s.put(EDGE, i, 3, r["Id"])
    s.put(EDGE, 2, 0, "Both")  # Qb Alpha is pooled
    for i, label in enumerate(["Always use", "Never use", "Max exposure", "Stack rules"], start=2):
        s.put(ss.TAB, i, 0, label)
    s.put(ss.TAB, 3, ss.NAME_INDEX, "Rb Fine")  # Never use, typed on the sheet
    s.put(ss.TAB, 8, 0, f"{ss.BUILD_LABEL} 1")
    s.put(ss.TAB, 9, 0, "Max exposure")  # below the first build: not the rules box
    return s


class Lock:
    def __init__(self):
        self.held = False
        self.taken = 0

    @contextlib.contextmanager
    def __call__(self):
        self.held, self.taken = True, self.taken + 1
        try:
            yield
        finally:
            self.held = False


@pytest.fixture
def lock():
    return Lock()


@pytest.fixture
def edits(sheet, lock, tmp_path):
    e = week_writes.SheetEdits(sheet, edge_tab=EDGE, log_path=tmp_path / "edits.log", lock=lock)
    sheet.on_write = lambda: (
        (_ for _ in ()).throw(AssertionError("wrote without the run lock")) if not lock.held else None
    )
    return e


def inp_for(d, rows=None):
    write_csv(d / "current" / "edge.csv", rows or slate_rows())
    return replace(week_inputs.gather(2026, 9, WED, d), config={})


# -- reading -------------------------------------------------------------------------------------------------


def test_read_finds_columns_by_header_and_the_rules_box_above_the_builds(edits):
    live = edits.read()
    assert live.ok and live.pool == {"id-Qb Alpha": "Both"}
    assert live.rules_found and live.rules["Never use"] == "Rb Fine" and live.rules["Max exposure"] == ""
    assert edits.edge_columns() == {"Pool": "A", "Name": "B", "Id": "D"}


def test_read_never_raises(tmp_path):
    class Broken:
        def read_range(self, *a):
            raise OSError("quota")

    live = week_writes.SheetEdits(Broken(), edge_tab=EDGE, log_path=tmp_path / "l").read()
    assert not live.ok and "quota" in live.error


# -- the pool ------------------------------------------------------------------------------------------------


def test_a_pool_write_is_one_batch_under_the_lock_read_back_and_logged(edits, sheet, lock, tmp_path):
    out = edits.set_pool(
        [{"id": "id-Wr Value", "value": "Cash", "was": ""}, {"id": "id-Qb Alpha", "value": "", "was": "Both"}]
    )
    assert out == {"id-Wr Value": "Cash", "id-Qb Alpha": ""}  # what the sheet holds after
    assert len(sheet.writes) == 1 and set(sheet.writes[0][1]) == {"A4", "A2"}  # one batch, column by header
    assert sheet.get(EDGE, 4, 0) == "Cash" and lock.taken == 1 and not lock.held
    log = (tmp_path / "edits.log").read_text()
    assert "EdgeRaw!A4 Wr Value: - -> Cash" in log and "EdgeRaw!A2 Qb Alpha: Both -> -" in log


def test_a_pool_cell_changed_in_the_sheet_is_refused_and_nothing_is_written(edits, sheet, tmp_path):
    sheet.put(EDGE, 4, 0, "GPP")  # Sam picked GPP in the sheet after the page loaded
    with pytest.raises(week_writes.Conflict) as exc:
        edits.set_pool(
            [{"id": "id-Wr Value", "value": "Cash", "was": ""}, {"id": "id-Qb Beta", "value": "Both"}]
        )
    assert exc.value.current == {"id-Wr Value": "GPP"} and exc.value.status == 409
    assert sheet.writes == [] and sheet.get(EDGE, 3, 0) == ""  # all or nothing
    assert "refused (changed in the sheet): Wr Value is GPP" in (tmp_path / "edits.log").read_text()


def test_bad_values_unknown_players_and_a_closed_week_are_refused(edits, sheet, lock, tmp_path):
    with pytest.raises(week_writes.WriteError):
        edits.set_pool([{"id": "id-Wr Value", "value": "Maybe", "was": ""}])
    with pytest.raises(week_writes.NotFound):
        edits.set_pool([{"id": "id-Nobody", "value": "Cash", "was": ""}])
    closed = week_writes.SheetEdits(sheet, edge_tab=EDGE, read_only=True, log_path=tmp_path / "l", lock=lock)
    with pytest.raises(week_writes.ReadOnly):
        closed.set_pool([{"id": "id-Wr Value", "value": "Cash", "was": ""}])
    assert sheet.writes == [] and lock.taken == 1  # only the NotFound attempt re-read under the lock


def test_the_run_lock_held_elsewhere_refuses_the_write(sheet, tmp_path):
    from dfs import run_lock

    def busy():
        raise run_lock.LockHeld(
            run_lock.Holder(pid=1, command="dfs sync", origin="terminal", since=datetime.now().astimezone())
        )

    e = week_writes.SheetEdits(sheet, edge_tab=EDGE, log_path=tmp_path / "l", lock=busy)
    with pytest.raises(run_lock.LockHeld):
        e.set_pool([{"id": "id-Wr Value", "value": "Cash", "was": ""}])
    assert sheet.writes == []


def test_the_suggested_starting_pool_is_one_batch_and_skips_the_pooled(edits, sheet, tmp_path):
    inp = inp_for(tmp_path)
    out = week_actions.start_pool(edits, inp)
    assert len(sheet.writes) == 1 and "id-Qb Alpha" not in out and "id-Wr Star" not in out
    expected = {
        week_board.pid(r): v for r, v in week_board.starting_pool(week_board.board(inp), {"Qb Alpha": "Both"})
    }
    assert out == expected


# -- the bulk write (Plan N item 7) --------------------------------------------------------------------------


def test_a_bulk_write_is_one_batch_with_the_conflict_check_per_cell(edits, sheet, lock, tmp_path):
    sheet.put(EDGE, 4, 0, "GPP")  # Wr Value changed in the sheet after the page loaded
    out = week_actions.pool_bulk(
        edits,
        [
            {"id": "id-Qb Beta", "value": "Cash", "was": ""},
            {"id": "id-Wr Value", "value": "Cash", "was": ""},
            {"id": "id-Te Plain", "value": "Cash", "was": ""},
            {"id": "id-Nobody", "value": "Cash", "was": ""},
        ],
    )
    assert out["id-Qb Beta"] == {"status": "ok", "value": "Cash"}
    assert out["id-Te Plain"] == {"status": "ok", "value": "Cash"}
    assert out["id-Wr Value"] == {"status": "conflict", "value": "GPP"}  # reported, never dropped
    assert out["id-Nobody"]["status"] == "missing"
    assert len(sheet.writes) == 1 and len(sheet.writes[0][1]) == 2 and lock.taken == 1  # one batch, the lock
    assert sheet.get(EDGE, 4, 0) == "GPP"  # the changed cell is left as the sheet has it
    log = (tmp_path / "edits.log").read_text()
    assert "bulk pool refused (changed in the sheet): Wr Value is GPP" in log and "Qb Beta: - -> Cash" in log


def test_a_bulk_write_where_every_cell_changed_writes_nothing(edits, sheet):
    sheet.put(EDGE, 4, 0, "GPP")
    out = week_actions.pool_bulk(edits, [{"id": "id-Wr Value", "value": "Cash", "was": ""}])
    assert out == {"id-Wr Value": {"status": "conflict", "value": "GPP"}} and sheet.writes == []


def test_a_bulk_write_refuses_bad_values_duplicates_and_a_closed_week(edits, sheet, lock, tmp_path):
    with pytest.raises(week_writes.WriteError):
        week_actions.pool_bulk(edits, [{"id": "id-Qb Beta", "value": "Maybe", "was": ""}])
    with pytest.raises(week_writes.WriteError):
        week_actions.pool_bulk(
            edits, [{"id": "id-Qb Beta", "value": "Cash"}, {"id": "id-Qb Beta", "value": ""}]
        )
    with pytest.raises(week_writes.WriteError):
        week_actions.pool_bulk(edits, [])
    closed = week_writes.SheetEdits(sheet, edge_tab=EDGE, read_only=True, log_path=tmp_path / "l", lock=lock)
    with pytest.raises(week_writes.ReadOnly):
        week_actions.pool_bulk(closed, [{"id": "id-Qb Beta", "value": "Cash", "was": ""}])
    assert sheet.writes == []


def test_add_kind_and_drop_cash_only():
    assert [week_writes.add_kind(v, "Cash") for v in ("", "Cash", "GPP", "Both")] == [
        "Cash",
        "Cash",
        "Both",
        "Both",
    ]
    assert [week_writes.add_kind(v, "GPP") for v in ("", "Cash", "GPP", "Both")] == [
        "GPP",
        "Both",
        "GPP",
        "Both",
    ]
    assert [week_writes.drop_cash_only(v) for v in ("", "Cash", "GPP", "Both")] == ["", "", "GPP", "Both"]


# -- the rules box -------------------------------------------------------------------------------------------


def chalk_rows():
    rows = slate_rows(alpha={"CashOwn": "0.41"}, value={"CashOwn": "0.33"})
    for r in rows:
        r["CashOwn"] = r["CashOwn"] or "0.05"
    return rows


def test_play_writes_cash_scope_on_always_use_and_undo_takes_it_back(edits, sheet, tmp_path):
    inp = inp_for(tmp_path, chalk_rows())
    cell = (2, ss.NAME_INDEX)
    sheet.put(ss.TAB, *cell, "Te Plain")
    week_actions.decide(edits, inp, key="chalk:id-Qb Alpha", option="play", rules_was=edits.read().rules)
    assert sheet.get(ss.TAB, *cell) == "Te Plain, cash: Qb Alpha"
    assert sheet.writes[-1] == (ss.TAB, {f"{column_letter(ss.NAME_INDEX)}2": [["Te Plain, cash: Qb Alpha"]]})
    d = next(d for d in week_board.decisions(inp, edits.read().rules, {}) if d.key == "chalk:id-Qb Alpha")
    assert d.answer == "play" and d.undo
    with pytest.raises(week_writes.Conflict):  # answered: undo first
        week_actions.decide(edits, inp, key="chalk:id-Qb Alpha", option="fade", rules_was=edits.read().rules)
    week_actions.decide(edits, inp, key="chalk:id-Qb Alpha", option="undo", rules_was=edits.read().rules)
    assert sheet.get(ss.TAB, *cell) == "Te Plain"  # only what the page wrote is removed


def test_fade_on_a_line_that_changed_since_the_page_read_it_is_refused(edits, sheet, tmp_path):
    inp = inp_for(tmp_path, chalk_rows())
    shown = edits.read().rules
    sheet.put(ss.TAB, 3, ss.NAME_INDEX, "Rb Fine, Te Plain")  # typed in the sheet meanwhile
    with pytest.raises(week_writes.Conflict) as exc:
        week_actions.decide(edits, inp, key="chalk:id-Wr Value", option="fade", rules_was=shown)
    assert exc.value.current == {"Never use": "Rb Fine, Te Plain"}
    assert sheet.get(ss.TAB, 3, ss.NAME_INDEX) == "Rb Fine, Te Plain" and sheet.writes == []


def test_the_stack_pick_writes_more_items_on_max_exposure_only(edits, sheet, tmp_path):
    week_inputs.save_input(
        "stacks",
        {
            "rows": [
                {"qb": "Qb Alpha", "mates": "Wr Value", "game": "x"},
                {"qb": "Qb Beta", "mates": "Te Fade"},
            ]
        },
        season=2026,
        week=9,
        data_dir=tmp_path,
    )
    inp = inp_for(tmp_path)
    week_actions.decide(edits, inp, key="stack", option="both", rules_was=edits.read().rules)
    assert sheet.get(ss.TAB, 4, ss.NAME_INDEX) == "more Qb Alpha, more Wr Value, more Qb Beta, more Te Fade"
    assert sheet.get(ss.TAB, 2, ss.NAME_INDEX) == "" and sheet.get(ss.TAB, 9, ss.NAME_INDEX) == ""
    week_actions.decide(edits, inp, key="stack", option="undo", rules_was=edits.read().rules)
    assert sheet.get(ss.TAB, 4, ss.NAME_INDEX) == ""


def test_a_pivot_pools_him_as_both_and_wait_writes_nothing(edits, sheet, tmp_path):
    rows = [*slate_rows(), row("Wr Near", "WR", "NE", "NYJ", 8500, 16.0)]
    sheet.put(EDGE, len(rows) + 1, 1, "Wr Near")
    sheet.put(EDGE, len(rows) + 1, 3, "id-Wr Near")
    inp = inp_for(tmp_path, rows)
    star = next(d for d in week_board.decisions(inp, {}, {}) if d.kind == "q")
    assert week_actions.decide(edits, inp, key=star.key, option="wait", rules_was={}).startswith("Waiting")
    assert sheet.writes == []
    with pytest.raises(week_writes.WriteError):  # only a pivot the page offers
        week_actions.decide(edits, inp, key=star.key, option="pivot", pivot="id-Te Fade", rules_was={})
    pick = week_board.pid(star.pivots[0])
    week_actions.decide(edits, inp, key=star.key, option="pivot", pivot=pick, rules_was={})
    at = next(r for r in range(2, 20) if sheet.get(EDGE, r, 3) == pick)
    assert sheet.get(EDGE, at, 0) == "Both"
    assert week_actions.answers(inp)[star.key]["pivot_id"] == pick
    week_actions.decide(edits, inp, key=star.key, option="undo", rules_was={})
    assert sheet.get(EDGE, at, 0) == "" and star.key not in week_actions.answers(inp)


# -- decisions also move the pool (Plan N item 8) ------------------------------------------------------------


def test_play_adds_him_as_cash_and_undo_takes_back_both_writes(edits, sheet, tmp_path):
    inp = inp_for(tmp_path, chalk_rows())
    text, pool = week_actions.decide_pool(
        edits, inp, key="chalk:id-Wr Value", option="play", rules_was=edits.read().rules
    )
    assert (
        pool == {"id-Wr Value": "Cash"} and sheet.get(EDGE, 4, 0) == "Cash" and "In your pool as Cash" in text
    )
    assert "cash: Wr Value" in sheet.get(ss.TAB, 2, ss.NAME_INDEX)
    text, pool = week_actions.decide_pool(
        edits, inp, key="chalk:id-Wr Value", option="undo", rules_was=edits.read().rules
    )
    assert pool == {"id-Wr Value": ""} and sheet.get(EDGE, 4, 0) == "" and "Pool put back" in text
    assert "Wr Value" not in sheet.get(ss.TAB, 2, ss.NAME_INDEX)


def test_play_on_a_gpp_player_makes_him_both_and_on_a_pooled_one_changes_nothing(edits, sheet, tmp_path):
    inp = inp_for(tmp_path, chalk_rows())
    sheet.put(EDGE, 4, 0, "GPP")
    _, pool = week_actions.decide_pool(
        edits, inp, key="chalk:id-Wr Value", option="play", rules_was=edits.read().rules
    )
    assert pool == {"id-Wr Value": "Both"}
    before = len(sheet.writes)
    _, pool = week_actions.decide_pool(
        edits, inp, key="chalk:id-Qb Alpha", option="play", rules_was=edits.read().rules
    )
    assert pool == {"id-Qb Alpha": "Both"} and len(sheet.writes) == before + 1  # the rules line only


def test_fade_takes_out_a_cash_only_player_and_leaves_the_rest_alone(edits, sheet, tmp_path):
    inp = inp_for(tmp_path, chalk_rows())
    sheet.put(EDGE, 4, 0, "Cash")
    text, pool = week_actions.decide_pool(
        edits, inp, key="chalk:id-Wr Value", option="fade", rules_was=edits.read().rules
    )
    assert pool == {"id-Wr Value": ""} and sheet.get(EDGE, 4, 0) == "" and "Cash only" in text
    week_actions.decide_pool(edits, inp, key="chalk:id-Wr Value", option="undo", rules_was=edits.read().rules)
    assert sheet.get(EDGE, 4, 0) == "Cash"  # undo put him back
    text, pool = week_actions.decide_pool(
        edits, inp, key="chalk:id-Qb Alpha", option="fade", rules_was=edits.read().rules
    )
    assert pool == {"id-Qb Alpha": "Both"} and "left alone (he is Both there)" in text


def test_undo_leaves_a_pool_cell_changed_since_and_says_so(edits, sheet, tmp_path):
    inp = inp_for(tmp_path, chalk_rows())
    week_actions.decide_pool(edits, inp, key="chalk:id-Wr Value", option="play", rules_was=edits.read().rules)
    sheet.put(EDGE, 4, 0, "GPP")  # Sam changed it in the sheet
    text, _ = week_actions.decide_pool(
        edits, inp, key="chalk:id-Wr Value", option="undo", rules_was=edits.read().rules
    )
    assert sheet.get(EDGE, 4, 0) == "GPP" and "left alone: Wr Value (now GPP)" in text


def test_a_stack_pick_adds_the_qb_and_mates_as_gpp_and_undo_takes_them_out(edits, sheet, tmp_path):
    week_inputs.save_input(
        "stacks",
        {"rows": [{"qb": "Qb Beta", "mates": "Te Fade"}, {"qb": "Qb Alpha", "mates": "Wr Value"}]},
        season=2026,
        week=9,
        data_dir=tmp_path,
    )
    inp = inp_for(tmp_path)
    sheet.put(EDGE, 3, 0, "Cash")  # Qb Beta, Cash already
    _, pool = week_actions.decide_pool(edits, inp, key="stack", option="a", rules_was=edits.read().rules)
    assert pool == {"id-Qb Beta": "Both", "id-Te Fade": "GPP"}
    week_actions.decide_pool(edits, inp, key="stack", option="undo", rules_was=edits.read().rules)
    assert sheet.get(EDGE, 3, 0) == "Cash" and sheet.get(EDGE, 7, 0) == ""


def test_a_decision_the_data_no_longer_has_is_not_found(edits, tmp_path):
    with pytest.raises(week_writes.NotFound):
        week_actions.decide(edits, inp_for(tmp_path), key="chalk:id-Nobody", option="play", rules_was={})


# -- the text helpers ---------------------------------------------------------------------------------------


def test_items_are_added_once_and_removed_without_reformatting_the_rest():
    slate = ["Qb Alpha", "Wr Value"]
    match = week_writes.scoped_matcher("cash", "Qb Alpha", slate)
    assert week_writes.add_item("", "cash: Qb Alpha", match) == "cash: Qb Alpha"
    assert week_writes.add_item("cash: Qb Alpha", "cash: Qb Alpha", match) == "cash: Qb Alpha"
    assert (
        week_writes.add_item("Qb Alpha", "cash: Qb Alpha", match) == "Qb Alpha, cash: Qb Alpha"
    )  # unscoped differs
    assert week_writes.remove_items("Wr Value,cash: Qb Alpha", match) == "Wr Value"
    assert week_writes.remove_items("Wr Value;  odd", match) == "Wr Value;  odd"  # nothing matched: untouched


# -- blueprint Phase A: the Default cash lineup's core and open spots ----------------------------------------


def cash_core_input():
    def p(name):
        return {"name": name, "position": "", "team": "", "salary": 0, "projection": 0.0}

    return {
        "core": [{**p("Qb Alpha"), "in": 9, "of": 9, "faded": False}],
        "open": [
            {
                "slot": "FLEX",
                "default": {**p("Wr Value"), "in": 6},
                "alternatives": [{**p("Te Plain"), "d_points": -0.4, "d_salary": 500, "in": 3}],
            }
        ],
        "default": {"names": ["Qb Alpha", "Wr Value"]},
    }


def test_an_open_spot_pick_writes_cash_scope_on_always_use_and_adds_him_as_cash(edits, sheet, tmp_path):
    inp = replace(inp_for(tmp_path, chalk_rows()), cash_core=cash_core_input())
    cell = (2, ss.NAME_INDEX)
    text, pool = week_actions.decide_pool(
        edits, inp, key="spot:FLEX", option="id-Te Plain", rules_was=edits.read().rules
    )
    assert sheet.get(ss.TAB, *cell) == "cash: Te Plain" and "FLEX: Te Plain" in text
    assert pool == {"id-Te Plain": "Cash"}
    d = next(
        d
        for d in week_board.decisions(inp, edits.read().rules, week_actions.answers(inp))
        if d.key == "spot:FLEX"
    )
    assert d.answer == "id-Te Plain" and d.undo
    with pytest.raises(week_writes.WriteError):  # only a candidate the spot offers
        week_actions.decide(edits, inp, key="spot:FLEX", option="id-Qb Beta", rules_was=edits.read().rules)
    week_actions.decide(edits, inp, key="spot:FLEX", option="undo", rules_was=edits.read().rules)
    assert sheet.get(ss.TAB, *cell) == "" and edits.read().pool.get("id-Te Plain", "") == ""


def test_someone_else_for_an_open_spot_writes_the_same_cash_rule_and_undo_takes_it_back(
    edits, sheet, tmp_path
):
    """The design pass (Sam, 2026-11-17): "Someone else..." picks a pool player who fits the slot and the
    salary left; it writes the same `cash: Name` on Always use as a card pick."""
    core = cash_core_input()
    core["default"]["left"] = 1500
    inp = replace(inp_for(tmp_path, chalk_rows()), cash_core=core)
    cell = (2, ss.NAME_INDEX)
    with pytest.raises(week_writes.WriteError):  # $9,000: more than the spot can pay
        week_actions.decide(edits, inp, key="spot:FLEX", option="id-Wr Star", rules_was=edits.read().rules)
    assert week_actions.answers(inp) == {}  # a refused pick leaves nothing behind
    text, pool = week_actions.decide_pool(
        edits, inp, key="spot:FLEX", option="id-Te Fade", rules_was=edits.read().rules
    )
    assert sheet.get(ss.TAB, *cell) == "cash: Te Fade" and "FLEX: Te Fade" in text
    assert pool == {"id-Te Fade": "Cash"}
    d = next(
        d
        for d in week_board.decisions(inp, edits.read().rules, week_actions.answers(inp))
        if d.key == "spot:FLEX"
    )
    assert d.answer == "id-Te Fade" and d.undo and "Te Fade" in [c["Name"] for c in d.candidates]
    week_actions.decide(edits, inp, key="spot:FLEX", option="undo", rules_was=edits.read().rules)
    assert sheet.get(ss.TAB, *cell) == "" and week_board.OTHER + "spot:FLEX" not in week_actions.answers(inp)


def test_a_core_fade_writes_cash_scope_on_never_use_and_the_chalk_decision_does_not_ask_again(
    edits, sheet, tmp_path
):
    inp = replace(inp_for(tmp_path, chalk_rows()), cash_core=cash_core_input())
    keys = [d.key for d in week_board.decisions(inp, edits.read().rules, {})]
    assert "core:id-Qb Alpha" in keys and "chalk:id-Qb Alpha" not in keys
    assert "chalk:id-Wr Value" not in keys  # an open-spot player is asked there, not as chalk
    week_actions.decide(edits, inp, key="core:id-Qb Alpha", option="fade", rules_was=edits.read().rules)
    assert sheet.get(ss.TAB, 3, ss.NAME_INDEX) == "Rb Fine, cash: Qb Alpha"


# -- blueprint Phase D: every page decision is logged --------------------------------------------------------


def test_each_page_decision_becomes_a_decisions_log_record(edits, sheet, tmp_path):
    from types import SimpleNamespace

    from dfs import decisions_log, week_server

    inp = inp_for(tmp_path, chalk_rows())
    shown = week_board.decisions(inp, edits.read().rules, {})
    chalk = next(d for d in shown if d.key == "chalk:id-Wr Value")
    play = week_actions.decision_record(chalk, inp, option="play")
    assert play["kind"] == "core_fade" and play["chosen"] == {"id": "id-Wr Value", "name": "Wr Value"}
    assert play["default_at_time"] == play["chosen"] and play["state"] == "build" and play["row"] == chalk.key
    fade = week_actions.decision_record(chalk, inp, option="fade")
    assert fade["chosen"] is None  # a Fade names no one in his place: it settles to nothing
    assert week_actions.decision_record(chalk, inp, option="undo") is None

    ctx = SimpleNamespace(season=2026, week=9, data_dir=tmp_path)
    path = tmp_path / "week" / "decisions.jsonl"
    week_actions.decide(edits, inp, key=chalk.key, option="play", rules_was=edits.read().rules)
    first, warning = week_server.log_decision(ctx, inp, chalk, {"option": "play"})
    assert first == "2026-09-0001" and warning == ""
    undone, _ = week_server.log_decision(ctx, inp, chalk, {"option": "undo"})
    assert decisions_log.read(2026, 9, path) == [] and undone == "2026-09-0002"


def test_a_pivot_and_a_wait_are_logged_against_the_star(edits, sheet, tmp_path):
    rows = [*slate_rows(), row("Wr Near", "WR", "NE", "NYJ", 8500, 16.0)]
    inp = inp_for(tmp_path, rows)
    star = next(d for d in week_board.decisions(inp, {}, {}) if d.kind == "q")
    pick = week_board.pid(star.pivots[0])
    rec = week_actions.decision_record(star, inp, option="pivot", pivot=pick)
    assert rec["kind"] == "pivot" and rec["chosen"]["id"] == pick
    assert rec["default_at_time"]["id"] == week_board.pid(star.row)
    assert all(a["id"] != pick for a in rec["alternatives"])
    wait = week_actions.decision_record(star, inp, option="wait")
    assert wait["kind"] == "later" and wait["chosen"] == wait["default_at_time"]


def test_a_log_that_cannot_be_written_never_fails_the_sheet_write(edits, sheet, tmp_path):
    from types import SimpleNamespace

    from dfs import week_server

    inp = inp_for(tmp_path, chalk_rows())
    shown = week_board.decisions(inp, edits.read().rules, {})
    chalk = next(d for d in shown if d.key == "chalk:id-Wr Value")
    (tmp_path / "week").mkdir(parents=True, exist_ok=True)
    (tmp_path / "week" / "decisions.jsonl").mkdir()  # a folder where the file should be
    ctx = SimpleNamespace(season=2026, week=9, data_dir=tmp_path)
    decision_id, warning = week_server.log_decision(ctx, inp, chalk, {"option": "play"})
    assert decision_id == "" and "not recorded in the decisions log" in warning
