"""The Compare tab's sheet side against a recording fake: what it writes, keeps, exempts and rebuilds."""

from __future__ import annotations

from types import SimpleNamespace

from dfs import compare_card as cc
from dfs import sheet_compare
from dfs.sheet_protection import protect_compare, protect_workbook
from dfs.sheets import SheetsError

EXPOSURE_HEADER = ["Name", "Pos", "Salary", "# Lineups", "Exposure", "Target", "vs Target"]
TABS = ("Edge Finder", "Player", "EdgeRaw", "Exposure", "Compare")


class FakeClient:
    def __init__(self, tabs=TABS, typed=None):
        self.tabs = list(tabs)
        self.typed = typed
        self.calls: list[tuple] = []

    def describe(self):
        return "Week 7", "https://example.test/sheet"

    def tab_exists(self, tab):
        return tab in self.tabs

    def list_tabs(self):
        return [SimpleNamespace(title=t) for t in self.tabs]

    def read_range(self, tab, rng):
        return [EXPOSURE_HEADER] if tab == "Exposure" else []

    def read_range_unformatted(self, tab, rng):
        return [self.typed] if self.typed else []

    def write_tab(self, tab, rows, **kw):
        self.calls.append(("write_tab", tab, rows))
        if tab not in self.tabs:
            self.tabs.append(tab)
        return len(rows)

    def __getattr__(self, name):  # every formatting / protection call is just recorded
        def record(*args, **kwargs):
            self.calls.append((name, *args, kwargs))

        return record

    def named(self, name):
        return [c for c in self.calls if c[0] == name]


def cfg():
    return SimpleNamespace(
        google_sheets=SimpleNamespace(tab_mappings={"edge": "EdgeRaw"}),
        lineups=SimpleNamespace(builder_tab="Lineups"),
    )


def test_build_writes_formulas_keeps_the_four_picks_and_exempts_only_the_pickers():
    client = FakeClient(typed=["Puka Nacua", "", "Trey McBride", "x"])
    lines = sheet_compare.build_card(client, cfg())
    written = client.named("write_tab")[0]
    assert written[1] == "Compare" and written[2][0][1:] == ["Puka Nacua", "", "Trey McBride", "x"]
    assert written[2][cc.HELPER_ROW - 1][1].startswith("=IFERROR(MATCH(B$1,EdgeRaw!$B$2:$B,0)")
    (validation,) = client.named("set_range_dropdown_validation")
    assert validation[1:3] == ("Compare", "B1:E1")
    assert validation[3]["source"] == "EdgeRaw!$B$2:$B" and validation[3]["strict"] is False
    (protect,) = client.named("protect_sheet")
    assert protect[1] == "Compare" and protect[2]["unprotected_ranges"] == ["B1:E1"]
    assert client.named("clear_protected_ranges")
    placed = client.named("set_tab_properties")[-1]
    assert placed[1] == "Compare" and placed[2]["index"] == 2  # right after Player (index 1)
    assert any("placed right after Player" in line for line in lines)


def test_rules_are_cleared_before_they_are_added_so_a_rebuild_never_stacks_them():
    client = FakeClient()
    sheet_compare.build_card(client, cfg())
    order = [c[0] for c in client.calls]
    assert order.index("clear_conditional_formats") < order.index("add_boolean_rules")
    (rules,) = client.named("add_boolean_rules")
    layout = cc.build_layout(
        edge_tab="EdgeRaw",
        lineups_tab="Lineups",
        exposure_tab="Exposure",
        exposure=None,
        lineup_count_cell="H1",
    )
    assert len(rules[2]) == len(layout.highlights) > 20


def test_a_new_tab_starts_with_the_pickers_empty_and_a_missing_exposure_tab_is_said():
    client = FakeClient(tabs=("Edge Finder", "Player", "EdgeRaw"))
    sheet_compare.build_card(client, cfg())
    rows = client.named("write_tab")[0][2]
    assert rows[0][1:] == [""] * 4
    assert any("(no Exposure tab)" in str(row[0]) for row in rows)


def test_without_a_player_tab_it_is_left_where_it_is():
    client = FakeClient(tabs=("EdgeRaw",))
    line = sheet_compare.place_after_player(client)
    assert "Player not found" in line and not client.named("set_tab_properties")


def test_the_tab_is_rebuilt_only_when_edgeraws_width_changed_and_the_sheet_has_it():
    client = FakeClient()
    ok, line = sheet_compare.rebuild_if_widened(client, cfg(), 74, 90)
    assert ok and "74 to 90" in line and client.named("write_tab")[0][1] == "Compare"

    for before in (90, None):
        quiet = FakeClient()
        assert sheet_compare.rebuild_if_widened(quiet, cfg(), before, 90) == (True, "")
        assert quiet.calls == []

    bare = FakeClient(tabs=("Edge Finder", "Player", "EdgeRaw"))  # a sync never creates the tab
    assert sheet_compare.rebuild_if_widened(bare, cfg(), 74, 90) == (True, "")
    assert bare.calls == []


def test_a_failed_rebuild_is_a_warning_line_and_never_an_exception():
    client = FakeClient()

    def boom(tab, rows, **kw):
        raise SheetsError("quota")

    client.write_tab = boom
    ok, line = sheet_compare.rebuild_if_widened(client, cfg(), 74, 90)
    assert not ok and "not rebuilt" in line and "dfs setup compare" in line


def test_protect_workbook_carves_the_pickers_out_of_the_compare_tab():
    client = FakeClient(tabs=("Compare", "Lineups"))
    protect_workbook(client, lineups_tab="Lineups")
    protected = {c[1]: c[2] for c in client.named("protect_sheet")}
    assert protected["Compare"]["unprotected_ranges"] == ["B1:E1"]
    assert "not present" in protect_compare(FakeClient(tabs=()))


def test_the_tab_sits_right_after_player_in_the_week_order():
    from dfs.sheet_style import WEEK_ORDER

    tabs = [tab for tab, _family in WEEK_ORDER]
    assert tabs.index("Compare") == tabs.index("Player") + 1
    assert dict(WEEK_ORDER)["Compare"] == "decide"
