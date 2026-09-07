from dfs.derived import EDGE_COLUMNS, EDGE_DATA_OFFSET
from dfs.sheet_filters import (
    BASIC_FILTER_PLAIN_TABS,
    FULL_RANGE_FILTER_TABS,
    add_basic_filters,
    add_edge_filter_views,
    add_plain_filter_views,
)
from dfs.sheets import column_letter


class FakeFilterClient:
    def __init__(self, *, present: bool = True):
        self._present = present
        self.clear_calls: list[tuple[str, str]] = []
        self.add_calls: list[tuple[str, str, str, dict | None]] = []
        self.basic_filter_calls: list[tuple[str, str]] = []

    def tab_exists(self, tab_name: str) -> bool:
        return self._present

    def clear_filter_view(self, tab_name: str, title: str) -> None:
        self.clear_calls.append((tab_name, title))

    def add_filter_view(
        self, tab_name: str, *, title: str, a1_range: str, criteria: dict | None = None
    ) -> None:
        self.add_calls.append((tab_name, title, a1_range, criteria))

    def set_basic_filter(self, tab_name: str, a1_range: str) -> None:
        self.basic_filter_calls.append((tab_name, a1_range))


def test_add_edge_filter_views_skips_missing_tab():
    client = FakeFilterClient(present=False)
    result = add_edge_filter_views(client, "EdgeRaw")
    assert result == ["EdgeRaw: not present -- skipped"]
    assert client.add_calls == []


def test_add_edge_filter_views_clears_before_adding_each_title():
    client = FakeFilterClient()
    add_edge_filter_views(client, "EdgeRaw")

    titles = [t for _tab, t, _rng, _crit in client.add_calls]
    assert titles == ["Pool picking", "Leverage plays", "Available only", "In my pool"]
    assert client.clear_calls == [("EdgeRaw", t) for t in titles]
    for i, (_tab, title) in enumerate(client.clear_calls):
        add_title = client.add_calls[i][1]
        assert title == add_title  # clear always precedes its own matching add


def test_add_edge_filter_views_pool_picking_has_no_criteria():
    client = FakeFilterClient()
    add_edge_filter_views(client, "EdgeRaw")
    _tab, _title, _rng, criteria = client.add_calls[0]
    assert criteria is None


def test_add_edge_filter_views_leverage_plays_targets_flag_column():
    client = FakeFilterClient()
    add_edge_filter_views(client, "EdgeRaw")
    _tab, title, _rng, criteria = client.add_calls[1]
    assert title == "Leverage plays"
    flag_idx = EDGE_COLUMNS.index("Flag") + EDGE_DATA_OFFSET
    assert list(criteria) == [flag_idx]
    assert criteria[flag_idx]["condition"]["type"] == "TEXT_EQ"
    assert criteria[flag_idx]["condition"]["values"][0]["userEnteredValue"] == "LEVERAGE"


def test_add_edge_filter_views_available_only_is_blank_avail():
    client = FakeFilterClient()
    add_edge_filter_views(client, "EdgeRaw")
    _tab, title, _rng, criteria = client.add_calls[2]
    assert title == "Available only"
    avail_idx = EDGE_COLUMNS.index("Avail") + EDGE_DATA_OFFSET
    assert criteria[avail_idx]["condition"]["type"] == "BLANK"


def test_add_edge_filter_views_in_my_pool_targets_column_a():
    client = FakeFilterClient()
    add_edge_filter_views(client, "EdgeRaw")
    _tab, title, _rng, criteria = client.add_calls[3]
    assert title == "In my pool"
    assert list(criteria) == [0]  # Pool is always column A
    assert criteria[0]["condition"]["values"][0]["userEnteredValue"] == "TRUE"


def test_add_edge_filter_views_range_spans_every_edge_column():
    client = FakeFilterClient()
    add_edge_filter_views(client, "EdgeRaw")
    _tab, _title, a1_range, _crit = client.add_calls[0]
    last_col = column_letter(len(EDGE_COLUMNS) - 1 + EDGE_DATA_OFFSET)
    assert a1_range.startswith(f"A1:{last_col}")


def test_add_plain_filter_views_covers_every_registered_tab():
    client = FakeFilterClient()
    results = add_plain_filter_views(client)
    assert len(results) == len(FULL_RANGE_FILTER_TABS)
    added_tabs = [tab for tab, _title, _rng, _crit in client.add_calls]
    assert added_tabs == [tab for tab, _rng in FULL_RANGE_FILTER_TABS]


def test_add_plain_filter_views_skips_a_missing_tab_without_erroring():
    class SelectivelyMissing(FakeFilterClient):
        def tab_exists(self, tab_name: str) -> bool:
            return tab_name != "SoSQB"

    client = SelectivelyMissing()
    results = add_plain_filter_views(client)
    assert any("SoSQB: not present -- skipped" == r for r in results)
    assert "SoSQB" not in [tab for tab, _t, _r, _c in client.add_calls]


def test_full_range_filter_tabs_excludes_the_formula_driven_tabs():
    # Player Pool, Lineups, PlayerPoolRaw, Board are deliberately absent --
    # a filter view sorting their rows would visually separate a lineup's
    # picks from its own totals row even though nothing underneath moves.
    tabs = {tab for tab, _rng in FULL_RANGE_FILTER_TABS}
    for excluded in ("Player Pool", "Lineups", "PlayerPoolRaw", "Board", "EdgeRaw"):
        assert excluded not in tabs


def test_add_basic_filters_covers_edgeraw_pool_picks_and_the_plain_tabs():
    client = FakeFilterClient()
    add_basic_filters(client, edge_tab="EdgeRaw", pool_picks_range="A2:J102")

    tabs = [tab for tab, _rng in client.basic_filter_calls]
    assert tabs == ["EdgeRaw", "Pool Picks", *[t for t, _r in BASIC_FILTER_PLAIN_TABS]]


def test_add_basic_filters_edgeraw_spans_the_whole_real_range():
    client = FakeFilterClient()
    add_basic_filters(client, edge_tab="EdgeRaw", pool_picks_range="A2:J102")
    _tab, a1_range = client.basic_filter_calls[0]
    last_col = column_letter(len(EDGE_COLUMNS) - 1 + EDGE_DATA_OFFSET)
    assert a1_range.startswith(f"A1:{last_col}")


def test_add_basic_filters_pool_picks_uses_the_given_range():
    client = FakeFilterClient()
    add_basic_filters(client, edge_tab="EdgeRaw", pool_picks_range="A2:J102")
    assert ("Pool Picks", "A2:J102") in client.basic_filter_calls


def test_add_basic_filters_skips_missing_tabs_without_erroring():
    class SelectivelyMissing(FakeFilterClient):
        def tab_exists(self, tab_name: str) -> bool:
            return tab_name not in ("Pool Picks", "SoSQB")

    client = SelectivelyMissing()
    results = add_basic_filters(client, edge_tab="EdgeRaw", pool_picks_range="A2:J102")

    assert any("Pool Picks: not present -- skipped" == r for r in results)
    assert any("SoSQB: not present -- skipped" == r for r in results)
    touched = [tab for tab, _rng in client.basic_filter_calls]
    assert "Pool Picks" not in touched
    assert "SoSQB" not in touched


def test_basic_filter_plain_tabs_excludes_the_view_only_tabs():
    # Slate Grid/Movement/Exposure keep their filter view as the only
    # sort/search mechanism (Fix 1.3) -- a basic filter is not added there.
    tabs = {tab for tab, _rng in BASIC_FILTER_PLAIN_TABS}
    excluded_tabs = ("Player Pool", "Lineups", "PlayerPoolRaw", "Board", "Slate Grid", "Movement", "Exposure")
    for excluded in excluded_tabs:
        assert excluded not in tabs
