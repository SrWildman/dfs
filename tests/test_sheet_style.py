from dfs.derived import EDGE_COLUMNS
from dfs.sheet_style import (
    CRIT_BG,
    CRIT_FG,
    EDGE_COLOR_SCALES,
    EDGE_COLUMN_GROUPS,
    EDGE_WIDTHS,
    FAMILY_COLORS,
    FIELD_FORMATS,
    HIDE_TABS,
    OK_BG,
    OK_FG,
    POSITION_TINTS,
    WARN_BG,
    WARN_FG,
    WEEK_ORDER,
    apply_field_formats,
    apply_tab_chrome,
    polish_builder_tab,
    polish_edge,
    polish_guardrails,
)


def test_edge_widths_and_scales_and_groups_only_name_real_edge_columns():
    # Each dict is keyed by EdgeRaw column NAME so a reordered EDGE_COLUMNS
    # still styles the right column -- but that only works if every key
    # actually is a current EDGE_COLUMNS entry. A typo'd or removed name
    # here doesn't error, it just silently styles nothing (_edge_letter
    # returns None), so this pins the dicts against drifting from the
    # column list without anyone noticing.
    for name in EDGE_WIDTHS:
        assert name in EDGE_COLUMNS, f"{name!r} in EDGE_WIDTHS is not an EDGE_COLUMNS entry"
    for name in EDGE_COLOR_SCALES:
        assert name in EDGE_COLUMNS, f"{name!r} in EDGE_COLOR_SCALES is not an EDGE_COLUMNS entry"
    for first, last in EDGE_COLUMN_GROUPS:
        assert first in EDGE_COLUMNS
        assert last in EDGE_COLUMNS


def test_field_formats_covers_every_edgeraw_numeric_column():
    # FIELD_FORMATS is shared across every tab now (the whole point of
    # Task 2.1), so it also carries builder-only header text ("DK Sal",
    # "Pts"...) that isn't a literal EDGE_COLUMNS name -- unlike the old,
    # now-removed EDGE_NUMBER_FORMATS, this dict can't be pinned against
    # EDGE_COLUMNS wholesale. Instead pin the EdgeRaw-side aliases that
    # matter: every numeric EdgeRaw column must resolve to *some* format.
    edge_numeric_columns = [
        "Salary",
        "ProjPts",
        "ProjOwn",
        "Ceiling",
        "Val",
        "CeilVal",
        "CeilPct",
        "Leverage",
        "GameEnv",
        "Wind",
        "LineMove",
    ]
    for name in edge_numeric_columns:
        assert name in FIELD_FORMATS, f"{name!r} (an EdgeRaw column) has no FIELD_FORMATS entry"


def test_apply_field_formats_matches_by_header_text_not_position():
    calls = []

    class _Client:
        def format_range(self, tab_name, a1_range, fmt):
            calls.append((a1_range, fmt))

    header = ["Name", "DK Sal", "Junk", "Pts"]
    applied = apply_field_formats(_Client(), "Player Pool", header, header_row=1, last_row=50)

    assert applied == 2
    ranges = dict(calls)
    assert ranges["B2:B50"] == FIELD_FORMATS["DK Sal"]
    assert ranges["D2:D50"] == FIELD_FORMATS["Pts"]


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


class FakeEdgeClient:
    """Records calls; used to pin polish_edge's re-run behaviour, in
    particular that it clears existing column groups before re-adding
    them (see clear_column_groups's own docstring for the bug this
    guards against -- 8 nested groups from repeated `dfs sheets polish`
    runs, found live)."""

    def __init__(self):
        self.calls: list[str] = []
        self.clear_group_calls: list[str] = []
        self.group_calls: list[tuple[str, str, str]] = []
        self.banding_calls: list[tuple] = []
        self.color_scale_calls: list[tuple[str, dict]] = []
        self.boolean_rule_calls: list[tuple[str, dict]] = []

    def tab_exists(self, tab_name: str) -> bool:
        return True

    def clear_conditional_formats(
        self, tab_name: str, *, column: str | None = None, row_range: tuple[int, int] | None = None
    ) -> None:
        self.calls.append("clear_conditional_formats")

    def clear_banding(self, tab_name: str) -> None:
        self.calls.append("clear_banding")

    def add_row_banding(self, tab_name: str, a1_range: str, **_kwargs) -> None:
        self.calls.append("add_row_banding")
        self.banding_calls.append((a1_range, _kwargs))

    def set_column_widths(self, tab_name: str, widths: dict) -> None:
        self.calls.append("set_column_widths")

    def format_range(self, tab_name: str, a1_range: str, fmt: dict) -> None:
        self.calls.append("format_range")

    def freeze(self, tab_name: str, *, rows=None, cols=None) -> None:
        self.calls.append("freeze")

    def add_color_scale(self, tab_name: str, a1_range: str, **kwargs) -> None:
        self.calls.append("add_color_scale")
        self.color_scale_calls.append((a1_range, kwargs))

    def add_boolean_rule(self, tab_name: str, a1_range: str, *, condition_type, values, fmt) -> None:
        self.calls.append("add_boolean_rule")
        rule = {"condition_type": condition_type, "values": values, "fmt": fmt}
        self.boolean_rule_calls.append((a1_range, rule))

    def clear_column_groups(self, tab_name: str) -> None:
        self.calls.append("clear_column_groups")
        self.clear_group_calls.append(tab_name)

    def hide_columns(
        self, tab_name: str, first_col_a1: str, last_col_a1: str, *, hidden: bool = True
    ) -> None:
        self.calls.append("hide_columns")

    def group_columns(self, tab_name: str, first_col_a1: str, last_col_a1: str) -> None:
        self.calls.append("group_columns")
        self.group_calls.append((tab_name, first_col_a1, last_col_a1))


def test_polish_edge_clears_column_groups_before_re_adding_them():
    client = FakeEdgeClient()
    polish_edge(client, "EdgeRaw")

    assert client.clear_group_calls == ["EdgeRaw"]
    assert len(client.group_calls) == len(EDGE_COLUMN_GROUPS)
    # clear must run before any group is (re-)added.
    clear_index = client.calls.index("clear_column_groups")
    first_group_index = client.calls.index("group_columns")
    assert clear_index < first_group_index


def test_polish_edge_clears_banding_before_re_adding_it():
    # Same idempotency hazard as column groups, different Sheets API: a
    # re-run without clearing first would hit "range overlaps an existing
    # banded range" rather than silently stacking, but the fix is the
    # same clear-then-add shape.
    client = FakeEdgeClient()
    polish_edge(client, "EdgeRaw")

    assert client.calls.count("clear_banding") == 1
    assert client.calls.count("add_row_banding") == 1
    assert client.calls.index("clear_banding") < client.calls.index("add_row_banding")


def test_polish_edge_scales_six_decision_columns_not_salary():
    assert len(EDGE_COLOR_SCALES) == 6
    assert "Salary" not in EDGE_COLOR_SCALES  # a constraint, not a quality -- left neutral
    assert "LineMove" not in EDGE_COLOR_SCALES  # scored separately, as a diverging scale

    client = FakeEdgeClient()
    polish_edge(client, "EdgeRaw")

    # 6 standard scales + 1 diverging LineMove scale = 7 add_color_scale calls.
    assert len(client.color_scale_calls) == 7


def test_polish_edge_linemove_scale_is_diverging_at_zero():
    client = FakeEdgeClient()
    polish_edge(client, "EdgeRaw")

    diverging = [kwargs for _rng, kwargs in client.color_scale_calls if kwargs.get("mid_type") == "NUMBER"]
    assert len(diverging) == 1
    assert diverging[0]["mid_value"] == "0"


def test_polish_edge_wind_chip_matches_slate_grid_threshold():
    from dfs.sheet_style import WARN_BG, WARN_FG, WIND_CHIP_THRESHOLD

    client = FakeEdgeClient()
    polish_edge(client, "EdgeRaw")

    wind_rules = [
        kwargs
        for _rng, kwargs in client.boolean_rule_calls
        if kwargs["condition_type"] == "NUMBER_GREATER" and kwargs["values"] == [WIND_CHIP_THRESHOLD]
    ]
    assert wind_rules
    expected_fmt = {"backgroundColor": WARN_BG, "textFormat": {"bold": True, "foregroundColor": WARN_FG}}
    assert wind_rules[0]["fmt"] == expected_fmt


def test_polish_edge_tints_every_position_defined_in_position_tints():
    client = FakeEdgeClient()
    polish_edge(client, "EdgeRaw")

    tinted_positions = {
        kwargs["values"][0]
        for _rng, kwargs in client.boolean_rule_calls
        if kwargs["condition_type"] == "TEXT_EQ" and kwargs["values"][0] in POSITION_TINTS
    }
    assert tinted_positions == set(POSITION_TINTS)


def test_polish_edge_name_column_pool_and_flag_rules_are_mutually_exclusive():
    from dfs.sources.edge import POOL_COLUMN

    client = FakeEdgeClient()
    polish_edge(client, "EdgeRaw")

    custom_formulas = [
        kwargs["values"][0]
        for _rng, kwargs in client.boolean_rule_calls
        if kwargs["condition_type"] == "CUSTOM_FORMULA" and POOL_COLUMN in kwargs["values"][0]
    ]
    # 3 rules: pooled+flagged, pooled-only, flagged-only -- never a bare
    # pooled rule and a bare flagged rule that could both match one cell.
    assert len(custom_formulas) == 3
    assert any("AND(" in f for f in custom_formulas)


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


class FakeGuardrailsClient:
    def __init__(self, header: list[str], *, present: bool = True):
        self._header = header
        self._present = present
        self.width_calls: list[dict] = []
        self.update_calls: list[tuple[str, list[list]]] = []
        self.clear_calls: list[str | None] = []
        self.boolean_rule_calls: list[tuple[str, str, list[str], dict]] = []

    def tab_exists(self, tab_name: str) -> bool:
        return self._present

    def read_range(self, tab_name: str, a1_range: str):
        return [self._header] if self._header else []

    def set_column_widths(self, tab_name: str, widths: dict[str, int]) -> None:
        self.width_calls.append(widths)

    def update_range(self, tab_name: str, a1_range: str, rows: list[list]) -> None:
        self.update_calls.append((a1_range, rows))

    def clear_conditional_formats(self, tab_name: str, *, column: str | None = None) -> None:
        self.clear_calls.append(column)

    def add_boolean_rule(
        self, tab_name: str, a1_range: str, *, condition_type: str, values, fmt: dict
    ) -> None:
        self.boolean_rule_calls.append((a1_range, condition_type, values, fmt))


_HEADER_WITH_AVAIL_AT_Y = (
    ["Name", "Pos.", "Team", "DK Sal", "O/U", "Spread", "Team Implied", "Opp.", "Venue", "OppPosRank", "Pts"]
    + ["Ceil", "Val", "Rstr%", "", "% of Rstr", "CeilVal", "CeilPct", "Leverage", "LevBasis", "GameEnv"]
    + ["Stadium", "Roof", "Wind", "Avail", "Flag"]
)


def test_polish_guardrails_skips_when_lineups_missing():
    client = FakeGuardrailsClient(_HEADER_WITH_AVAIL_AT_Y, present=False)
    result = polish_guardrails(client, "Lineups", header_row=8, name_blocks=[(9, 18)])
    assert result == "Lineups: not present -- skipped"
    assert client.update_calls == []


def test_polish_guardrails_skips_when_avail_not_yet_linked():
    client = FakeGuardrailsClient(["Name", "Pos.", "Team"])  # link-edge never run
    result = polish_guardrails(client, "Lineups", header_row=8, name_blocks=[(9, 18)])
    assert "not linked yet" in result
    assert client.update_calls == []
    assert client.clear_calls == []


def test_polish_guardrails_writes_slot_and_totals_formulas_against_the_real_avail_column():
    client = FakeGuardrailsClient(_HEADER_WITH_AVAIL_AT_Y)
    avail_index = _HEADER_WITH_AVAIL_AT_Y.index("Avail")
    assert avail_index == 24  # column Y, 0-indexed -- confirms the fixture matches spec section 1.2

    polish_guardrails(client, "Lineups", header_row=8, name_blocks=[(9, 18)])

    header_call = next(c for c in client.update_calls if c[0] == "O8")
    assert header_call[1] == [["Check"]]

    block_call = next(c for c in client.update_calls if c[0] == "O9:O18")
    rows = block_call[1]
    assert len(rows) == 10  # 9 slots + 1 totals row

    # Slot row 9: duplicate check over the block's own name range, then
    # falls back to that row's own Avail cell.
    assert rows[0] == ['=IF($A9="","",IF(COUNTIF($A$9:$A$17,$A9)>1,"DUPLICATE",IF($Y9<>"",$Y9,"")))']
    # Totals row (18): cap / completeness / OK.
    assert rows[-1] == [
        '=IF(COUNTA($A$9:$A$17)=0,"",'
        'IF(D18>50000,"OVER "&TEXT(D18-50000,"$#,##0"),'
        'IF(COUNTA($A$9:$A$17)<9,"INCOMPLETE "&COUNTA($A$9:$A$17)&"/9","OK")))'
    ]


def test_polish_guardrails_widens_column_o_and_clears_only_its_own_rules():
    client = FakeGuardrailsClient(_HEADER_WITH_AVAIL_AT_Y)

    polish_guardrails(client, "Lineups", header_row=8, name_blocks=[(9, 18)])

    assert client.width_calls == [{"O": 110}]
    assert client.clear_calls == ["O"]  # never a blanket clear of Lineups' other rules


class FakeBuilderTabClient:
    def __init__(self, header: list[str]):
        self._header = header
        self.format_calls: list[tuple[str, dict]] = []
        self.freeze_calls: list[tuple] = []
        self.width_calls: list[dict] = []

    def tab_exists(self, tab_name: str) -> bool:
        return True

    def read_range(self, tab_name: str, a1_range: str):
        return [self._header]

    def format_range(self, tab_name: str, a1_range: str, fmt: dict) -> None:
        self.format_calls.append((a1_range, fmt))

    def freeze(self, tab_name: str, *, rows=None, cols=None) -> None:
        self.freeze_calls.append((tab_name, rows, cols))

    def set_column_widths(self, tab_name: str, widths: dict[str, int]) -> None:
        self.width_calls.append(widths)


def test_polish_builder_tab_styles_header_repeats_the_same_as_the_real_header():
    # Lineups' repeated sub-headers (one per lineup block after the
    # first) looked plain while only the real header was dark -- every
    # block should read consistently, not just the first one.
    client = FakeBuilderTabClient(["Name", "Pos.", "Team"])

    polish_builder_tab(
        client,
        "Lineups",
        last_row=100,
        header_row=11,
        header_repeats_at=[24, 37],
    )

    by_range = dict(client.format_calls)
    assert "A11:C11" in by_range
    assert "A24:C24" in by_range
    assert "A37:C37" in by_range
    # All three get the identical dark header treatment, not a lesser one.
    assert by_range["A11:C11"] == by_range["A24:C24"] == by_range["A37:C37"]


def test_polish_builder_tab_skips_repeat_styling_when_none_given():
    client = FakeBuilderTabClient(["Name", "Pos.", "Team"])

    polish_builder_tab(client, "Player Pool", last_row=100, header_row=1)

    formatted_ranges = [a1 for a1, _fmt in client.format_calls]
    assert formatted_ranges == ["A1:C1"]


def _chip(bg: dict, fg: dict) -> dict:
    return {"backgroundColor": bg, "textFormat": {"bold": True, "foregroundColor": fg}}


def test_polish_guardrails_chips_cover_every_documented_state():
    client = FakeGuardrailsClient(_HEADER_WITH_AVAIL_AT_Y)

    polish_guardrails(client, "Lineups", header_row=8, name_blocks=[(9, 18)])

    by_value = {
        values[0]: (condition_type, fmt) for _rng, condition_type, values, fmt in client.boolean_rule_calls
    }
    assert by_value["DUPLICATE"] == ("TEXT_CONTAINS", _chip(CRIT_BG, CRIT_FG))
    assert by_value["OVER"] == ("TEXT_CONTAINS", _chip(CRIT_BG, CRIT_FG))
    assert by_value["OUT"] == ("TEXT_EQ", _chip(CRIT_BG, CRIT_FG))
    assert by_value["IR"] == ("TEXT_EQ", _chip(CRIT_BG, CRIT_FG))
    assert by_value["Q"] == ("TEXT_EQ", _chip(WARN_BG, WARN_FG))
    assert by_value["INCOMPLETE"] == ("TEXT_CONTAINS", _chip(WARN_BG, WARN_FG))
    assert by_value["OK"] == ("TEXT_EQ", _chip(OK_BG, OK_FG))
    # Every rule targets column O only, across the full block range given.
    for a1_range, *_ in client.boolean_rule_calls:
        assert a1_range == "O2:O18"
