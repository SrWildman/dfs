from contextlib import contextmanager

from dfs.season_long import finder as fd
from dfs.season_long import season_tabs as st
from dfs.season_long import sheet_season as ss
from dfs.season_long import trade as tr
from dfs.sheets import TabInfo, column_index

from . import synthetic as syn


class FakeClient:
    """Tabs as grids of values (formulas kept as typed), validations and protections recorded."""

    def __init__(self):
        self.tabs = {"Sheet1": {}}
        self.validations, self.protected, self.filter_views, self.calls = set(), {}, {}, []
        self.widths: dict[str, int] = {}

    def __getattr__(self, name):  # formatting and other presentation calls: recorded only
        return lambda *a, **k: self.calls.append(name)

    @contextmanager
    def batched(self):
        yield self

    def list_tabs(self):
        return [TabInfo(t, 1000, 26, [], 0) for t in self.tabs]

    def tab_exists(self, tab):
        return tab in self.tabs

    def rename_tab(self, old, new):
        self.tabs[new] = self.tabs.pop(old)

    def write_tab(self, tab, rows, **_):
        self.tabs[tab] = {(r, c): v for r, row in enumerate(rows) for c, v in enumerate(row)}

    def _cells(self, a1):
        a, _, b = a1.partition(":")
        b = b or a

        def rc(ref):
            letters = "".join(ch for ch in ref if ch.isalpha())
            return int(ref[len(letters) :]) - 1, column_index(letters + "1")

        return rc(a), rc(b)

    def update_range(self, tab, a1, rows):
        (r0, c0), _ = self._cells(a1)
        grid = self.tabs.setdefault(tab, {})
        for i, row in enumerate(rows):
            for j, v in enumerate(row):
                grid[(r0 + i, c0 + j)] = v

    def clear_ranges(self, tab, ranges):
        for a1 in ranges:
            (r0, c0), (r1, c1) = self._cells(a1)
            grid = self.tabs[tab]
            for key in [k for k in grid if r0 <= k[0] <= r1 and c0 <= k[1] <= c1]:
                del grid[key]

    def read_range(self, tab, a1):
        (r0, c0), (r1, c1) = self._cells(a1)
        grid = self.tabs.get(tab, {})
        rows = [[str(grid.get((r, c), "")) for c in range(c0, c1 + 1)] for r in range(r0, r1 + 1)]
        while rows and not any(rows[-1]):
            rows.pop()
        return rows

    read_range_unformatted = read_range
    read_formula = read_range

    def set_range_dropdown_validation(self, tab, a1, **_):
        self.validations.add((tab, a1))

    def ensure_column_capacity(self, tab, min_cols):
        self.widths[tab] = max(self.widths.get(tab, 26), min_cols)

    def has_data_validation(self, tab, a1):
        return (tab, a1) in self.validations

    def protect_sheet(self, tab, unprotected_ranges=None, **_):
        self.protected[tab] = unprotected_ranges or []

    def add_filter_view(self, tab, *, title, **_):
        self.filter_views[title] = tab


def _run(monkeypatch):
    v = syn.view()
    ev = syn.evaluator(v, monkeypatch)
    m = syn.market(v)
    return st.LeagueRun(v, ev, m, fd.find_trades(v, ev, m, fd.Filters(), top=5), tr.free_move(v, ev, 1, []))


def test_setup_sync_then_doctor_is_clean(monkeypatch):
    client = FakeClient()
    ss.setup(client, ["Test"])
    assert "Sheet1" not in client.tabs and set(st.VISIBLE_TABS) | set(st.RAW_TABS) <= set(client.tabs)
    assert client.protected[st.LEAGUE_TAB] == [st.PICKER_CELL]
    assert client.protected[st.OVERVIEW_TAB] == []
    run = _run(monkeypatch)
    ss.write_sync(client, [run])
    assert client.filter_views == {"League: Test": st.TRADE_FINDER_TAB}
    results = ss.doctor(client)
    assert all(ok for _, ok, _ in results), [r for r in results if not r[1]]


def test_rebuild_keeps_the_typed_box_and_pickers(monkeypatch):
    client = FakeClient()
    ss.setup(client, ["Test", "Other"])
    client.update_range(st.TRADE_FINDER_TAB, st.box_cell("Get"), [["RB"]])
    client.update_range(st.PLAYERS_TAB, st.PICKER_CELL, [["Other"]])
    ss.setup(client, ["Test", "Other"])
    assert ss.read_box(client) == {"Get": "RB"}
    assert client.read_range(st.PLAYERS_TAB, st.PICKER_CELL) == [["Other"]]


def test_write_ideas_replaces_only_its_league(monkeypatch):
    client = FakeClient()
    ss.setup(client, ["Test"])
    first = st.IDEAS_HEADER_ROW
    client.update_range(st.TRADE_FINDER_TAB, f"A{first + 1}", [["Elsewhere", 1, "x", "y"]])
    run = _run(monkeypatch)
    ss.write_ideas(client, [run])
    leagues = [r[0] for r in client.read_range(st.TRADE_FINDER_TAB, f"A{first + 1}:A{first + 50}")]
    assert leagues[0] == "Elsewhere" and leagues.count("Test") == len(run.ideas)


def test_doctor_flags_a_missing_tab_and_a_stale_formula():
    client = FakeClient()
    ss.setup(client, ["Test"])
    client.update_range(st.LEAGUE_TAB, f"A{st.VIEW_HEADER_ROW + 1}", [["=old"]])
    del client.tabs[st.ANALYZER_TAB]
    bad = {name for name, ok, _ in ss.doctor(client) if not ok}
    assert bad == {f"tab {st.ANALYZER_TAB}", f"{st.LEAGUE_TAB} formula"}


def test_setup_grows_the_raw_tabs_before_writing_formulas_over_them():
    """A raw tab narrower than its columns makes Sheets shift formula references when a sync grows it."""
    client = FakeClient()
    ss.setup(client, ["Test"])
    assert client.widths[st.TEAMS_RAW] >= len(st.TEAM_COLUMNS) > 26
    assert client.widths[st.PLAYERS_RAW] >= len(st.PLAYER_COLUMNS)
