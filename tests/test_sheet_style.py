from dfs.derived import EDGE_COLUMNS
from dfs.sheet_style import (
    EDGE_COLOR_SCALES,
    EDGE_COLUMN_GROUPS,
    EDGE_NUMBER_FORMATS,
    EDGE_WIDTHS,
    FAMILY_COLORS,
    HIDE_TABS,
    WEEK_ORDER,
    apply_tab_chrome,
    polish_edge,
)


def test_edge_widths_and_formats_and_scales_only_name_real_edge_columns():
    # Each dict is keyed by EdgeRaw column NAME so a reordered EDGE_COLUMNS
    # still styles the right column -- but that only works if every key
    # actually is a current EDGE_COLUMNS entry. A typo'd or removed name
    # here doesn't error, it just silently styles nothing (_edge_letter
    # returns None), so this pins the dicts against drifting from the
    # column list without anyone noticing.
    for name in EDGE_WIDTHS:
        assert name in EDGE_COLUMNS, f"{name!r} in EDGE_WIDTHS is not an EDGE_COLUMNS entry"
    for name in EDGE_NUMBER_FORMATS:
        assert name in EDGE_COLUMNS, f"{name!r} in EDGE_NUMBER_FORMATS is not an EDGE_COLUMNS entry"
    for name in EDGE_COLOR_SCALES:
        assert name in EDGE_COLUMNS, f"{name!r} in EDGE_COLOR_SCALES is not an EDGE_COLUMNS entry"
    for first, last in EDGE_COLUMN_GROUPS:
        assert first in EDGE_COLUMNS
        assert last in EDGE_COLUMNS


class _ExplodingClient:
    """Any call other than tab_exists means polish_edge didn't actually
    skip -- it would be a real, unwanted write against a tab that isn't
    there."""

    def tab_exists(self, tab_name: str) -> bool:
        return False

    def __getattr__(self, name):
        def _boom(*_args, **_kwargs):
            raise AssertionError(f"polish_edge should have skipped before calling {name!r}")

        return _boom


def test_polish_edge_skips_a_missing_tab_without_touching_anything():
    result = polish_edge(_ExplodingClient(), "EdgeRaw")
    assert result == "EdgeRaw: not present -- skipped"


class FakeChromeClient:
    def __init__(self, present_tabs: set[str]):
        self.present_tabs = present_tabs
        self.calls: list[tuple[str, dict | None, int | None, bool | None]] = []

    def tab_exists(self, tab_name: str) -> bool:
        return tab_name in self.present_tabs

    def set_tab_properties(self, tab_name, *, color=None, index=None, hidden=None):
        self.calls.append((tab_name, color, index, hidden))


def test_apply_tab_chrome_orders_present_tabs_then_hides_staging_tabs():
    present = {"EdgeRaw", "Player Pool", "Lineups", "Bankroll", "DKSalRaw", "oddsraw"}
    client = FakeChromeClient(present)

    apply_tab_chrome(client)

    ordered_calls = [c for c in client.calls if not c[3]]
    hidden_calls = [c for c in client.calls if c[3]]

    # Only tabs actually present get touched, in WEEK_ORDER's relative
    # order, indexed contiguously from 0 -- absent tabs must not leave a
    # gap in the index sequence.
    expected_order = [tab for tab, _family in WEEK_ORDER if tab in present]
    assert [c[0] for c in ordered_calls] == expected_order
    assert [c[2] for c in ordered_calls] == list(range(len(expected_order)))
    for tab, color, _index, hidden in ordered_calls:
        family = dict(WEEK_ORDER)[tab]
        assert color == FAMILY_COLORS[family]
        assert hidden is False

    expected_hidden = [tab for tab in HIDE_TABS if tab in present]
    assert [c[0] for c in hidden_calls] == expected_hidden
    for _tab, color, index, hidden in hidden_calls:
        assert color == FAMILY_COLORS["feed"]
        assert hidden is True
        assert index >= len(expected_order)
