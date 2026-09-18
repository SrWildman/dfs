from dfs.derived import EDGE_COLUMNS, ZONE_LABELS
from dfs.sheet_style import (
    AVAIL_CHIPS,
    BAND_BG,
    CRIT_BG,
    CRIT_FG,
    EDGE_COLUMN_GROUPS,
    EDGE_UNSCALED_PLAYER_METRICS,
    EDGE_WIDTHS,
    FAMILY_COLORS,
    FIELD_COLOR_SCALES,
    FIELD_FORMATS,
    FLAG_CHIPS,
    FLAT_BG,
    GRAD_MAX,
    GRAD_MIN,
    GROUPED_TAB_UNSCALED_COLUMNS,
    HEADER_FMT,
    HIDE_TABS,
    OK_BG,
    OK_FG,
    POOL_TAG_TINTS,
    POSITION_TINTS,
    VENUE_CHIPS,
    WARN_BG,
    WARN_FG,
    WEEK_ORDER,
    WHITE,
    ZERO_EXCLUDED_COLUMNS,
    ZERO_GREY_BG,
    _edge_letter,
    apply_field_color_scales,
    apply_field_formats,
    apply_grouped_color_scales,
    apply_tab_chrome,
    polish_bankroll,
    polish_builder_tab,
    polish_edge,
    polish_guardrails,
    polish_lineups_pct_of_cap,
    polish_lineups_remaining_per_slot_helper,
    polish_lineups_totals_rows,
    style_flat_tab,
    style_movement,
    style_results,
    style_sos_tab,
    style_tier23_tabs,
)
from dfs.sheets import column_letter
from dfs.sources.edge import POOL_HEADER


def test_edge_widths_and_groups_only_name_real_edge_columns():
    # Each dict is keyed by EdgeRaw column NAME so a reordered EDGE_COLUMNS
    # still styles the right column -- but that only works if every key
    # actually is a current EDGE_COLUMNS entry. A typo'd or removed name
    # here doesn't error, it just silently styles nothing (_edge_letter
    # returns None), so this pins the dicts against drifting from the
    # column list without anyone noticing.
    for name in EDGE_WIDTHS:
        assert name in EDGE_COLUMNS, f"{name!r} in EDGE_WIDTHS is not an EDGE_COLUMNS entry"
    for first, last in EDGE_COLUMN_GROUPS:
        assert first in EDGE_COLUMNS
        assert last in EDGE_COLUMNS


def test_field_color_scales_covers_every_edgeraw_decision_column_not_salary():
    # FIELD_COLOR_SCALES is shared across every tab (Fix 2.1), so it also
    # carries builder-only header text ("Team Implied"...) that isn't a
    # literal EDGE_COLUMNS name -- pin the EdgeRaw-side subset that
    # matters instead of the whole dict. `OppPosRank` WAS builder-only
    # too, until Phase 5 (2026-09-16, "all data should be in edge raw")
    # added it natively to EDGE_COLUMNS as well.
    edge_header = [POOL_HEADER, *EDGE_COLUMNS]
    matched = {name for name in edge_header if name in FIELD_COLOR_SCALES}
    assert matched == {
        "ProjPts",
        "Own%",
        "Ceiling",
        "Val",
        "ValAdj",
        "CeilVal",
        "Leverage",
        "GameEnv",
        "OppPosRank",
        "ImpliedMove",
        "TotMove",
        "SpdMove",
        "OverUnder",
        "Spread",
        "CeilPct",
    }
    assert "Salary" not in FIELD_COLOR_SCALES  # a constraint, not a quality -- left neutral
    assert FIELD_COLOR_SCALES["ImpliedMove"] == "diverging"  # scored separately, zero as the midpoint
    assert FIELD_COLOR_SCALES["TotMove"] == "diverging"
    assert FIELD_COLOR_SCALES["SpdMove"] == "diverging"
    assert FIELD_COLOR_SCALES["Spread"] == "diverging"  # signed, zero (pick'em) is the midpoint
    assert FIELD_COLOR_SCALES["Own%"] == "warm"  # high ownership is chalk, not "good" (Fix 2.8)
    assert FIELD_COLOR_SCALES["OppPosRank"] == "reversed"  # 1 (toughest matchup) is best, not worst


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
        "Own%",
        "Ceiling",
        "Val",
        "CeilVal",
        "CeilPct",
        "Leverage",
        "GameEnv",
        "Wind",
        "ImpliedMove",
        "TotMove",
        "SpdMove",
        "OverUnder",
        "Spread",
    ]
    for name in edge_numeric_columns:
        assert name in FIELD_FORMATS, f"{name!r} (an EdgeRaw column) has no FIELD_FORMATS entry"


def test_field_formats_gives_id_a_plain_integer_pattern():
    # Found live: with no explicit entry, Id inherited a stray "0.0"
    # number format from wherever it sat before Phase 3 moved it,
    # rendering every value as e.g. "44133074.0" -- which broke Pool-tick
    # preservation across a sync, since `sources/edge.py` matches ticks
    # by this exact string. Must be a plain integer, no decimal point.
    fmt = FIELD_FORMATS["Id"]
    assert "." not in fmt["numberFormat"]["pattern"]


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


def test_apply_field_color_scales_excludes_zero_for_ownership_columns():
    # Fix 2.7: a real, common zero (unpublished ownership) would otherwise
    # anchor the gradient's low end. The minpoint must be computed over
    # non-zero values only, and a flat grey rule added AFTER the gradient
    # (so it wins -- see the insert-at-front note on FLAG_CHIPS) must cover
    # exact zeros.
    calls = []

    class _Client:
        def clear_conditional_formats(self, tab_name, column=None, row_range=None):
            calls.append(("clear", column, row_range))

        def add_color_scale(self, tab_name, a1_range, **kwargs):
            calls.append(("scale", a1_range, kwargs))

        def add_boolean_rule(self, tab_name, a1_range, *, condition_type, values, fmt):
            calls.append(("bool", a1_range, condition_type, values, fmt))

    apply_field_color_scales(_Client(), "EdgeRaw", ["Name", "Own%"], header_row=1, last_row=100)

    kinds = [c[0] for c in calls]
    assert kinds == ["clear", "scale", "bool"]  # scale added, THEN the zero rule, so it wins
    # Fix A2: the clear is scoped to BOTH this column AND this exact data
    # range -- a column-only clear would also delete a different caller's
    # rule on the same column covering different rows (e.g. the pool
    # deck's own narrower window vs. the real blocks below it).
    assert calls[0] == ("clear", "B", (2, 100))

    scale_call = calls[1]
    assert scale_call[1] == "B2:B100"
    assert scale_call[2]["min_type"] == "NUMBER"
    assert scale_call[2]["min_value"] == '=MINIFS(B2:B100,B2:B100,"<>0")'

    bool_call = calls[2]
    assert bool_call[1] == "B2:B100"
    assert bool_call[2] == "NUMBER_EQ"
    assert bool_call[3] == ["0"]
    assert bool_call[4] == {"backgroundColor": ZERO_GREY_BG}


def test_apply_field_color_scales_no_zero_exclusion_for_ordinary_gradient_columns():
    calls = []

    class _Client:
        def clear_conditional_formats(self, tab_name, column=None, row_range=None):
            pass

        def add_color_scale(self, tab_name, a1_range, **kwargs):
            calls.append(kwargs)

        def add_boolean_rule(self, tab_name, a1_range, **kwargs):
            calls.append(kwargs)

    apply_field_color_scales(_Client(), "EdgeRaw", ["Name", "Pts"], header_row=1, last_row=100)

    assert len(calls) == 1  # just the gradient, no extra zero rule
    assert "min_type" not in calls[0]


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
    guards against -- 8 nested groups from repeated `dfs setup polish`
    runs, found live)."""

    def __init__(
        self, grouped_column_indices: set[int] | None = None, position_rows: list[list[str]] | None = None
    ):
        self.calls: list[str] = []
        self.clear_group_calls: list[str] = []
        self.group_calls: list[tuple[str, str, str, bool]] = []
        self.group_control_before_calls: list[str] = []
        self.banding_calls: list[tuple] = []
        self.color_scale_calls: list[tuple[str, dict]] = []
        self.multi_range_color_scale_calls: list[dict] = []
        self.boolean_rule_calls: list[tuple[str, dict]] = []
        self.format_range_calls: list[tuple[str, dict]] = []
        self.hide_calls: list[tuple[str, str, bool]] = []
        self._grouped_column_indices = grouped_column_indices or set()
        self._position_rows = position_rows if position_rows is not None else []

    def tab_exists(self, tab_name: str) -> bool:
        return True

    def get_grouped_column_indices(self, tab_name: str) -> set[int]:
        return self._grouped_column_indices

    def read_range(self, tab_name: str, a1_range: str):
        return self._position_rows

    def add_color_scales_multi_range(self, tab_name: str, specs: list[dict]) -> None:
        self.calls.append("add_color_scales_multi_range")
        self.multi_range_color_scale_calls.extend(specs)

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
        self.format_range_calls.append((a1_range, fmt))

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

    def set_column_group_control_before(self, tab_name: str) -> None:
        self.calls.append("set_column_group_control_before")
        self.group_control_before_calls.append(tab_name)

    def hide_columns(
        self, tab_name: str, first_col_a1: str, last_col_a1: str, *, hidden: bool = True
    ) -> None:
        self.calls.append("hide_columns")
        self.hide_calls.append((first_col_a1, last_col_a1, hidden))

    def group_columns(
        self, tab_name: str, first_col_a1: str, last_col_a1: str, *, collapsed: bool = False
    ) -> None:
        self.calls.append("group_columns")
        self.group_calls.append((tab_name, first_col_a1, last_col_a1, collapsed))


def test_polish_edge_clears_column_groups_before_re_adding_them():
    client = FakeEdgeClient()
    polish_edge(client, "EdgeRaw")

    assert client.clear_group_calls == ["EdgeRaw"]
    assert len(client.group_calls) == len(EDGE_COLUMN_GROUPS)
    # clear must run before any group is (re-)added.
    clear_index = client.calls.index("clear_column_groups")
    first_group_index = client.calls.index("group_columns")
    assert clear_index < first_group_index


def test_polish_edge_sets_column_group_control_before_the_group():
    # 2026-09-18: Sheets' default toggle placement (after the group) reads
    # as belonging to the *next* zone's label under this tab's
    # label-before-zone design -- see set_column_group_control_before's
    # own docstring. Must run before any group is added, same ordering
    # requirement as clear_column_groups.
    client = FakeEdgeClient()
    polish_edge(client, "EdgeRaw")

    assert client.group_control_before_calls == ["EdgeRaw"]
    control_index = client.calls.index("set_column_group_control_before")
    first_group_index = client.calls.index("group_columns")
    assert control_index < first_group_index


def test_polish_edge_groups_game_through_weather_collapsed_by_default():
    # Phase 6, Part 2 overrides Fix 2.9: EdgeRaw's Game/Ceiling detail/
    # Movement/Weather zones all collapse by default now too, matching
    # Player Pool/Lineups -- previously only GameStart alone was grouped,
    # and not collapsed. Originally landed as one merged group (Sheets
    # merges adjacent same-depth groups regardless), then split into four
    # independent ranges the same day once each zone got its own real
    # label column ahead of it (the zone-label usability fix) -- a label
    # sits outside its own zone's range, so the four ranges are no longer
    # adjacent and Sheets keeps them independently collapsible.
    client = FakeEdgeClient()
    polish_edge(client, "EdgeRaw")

    assert client.group_calls == [
        ("EdgeRaw", _edge_letter("OverUnder"), _edge_letter("OppPosRank"), True),
        ("EdgeRaw", _edge_letter("CeilPct"), _edge_letter("OwnStatus"), True),
        ("EdgeRaw", _edge_letter("ImpliedMove"), _edge_letter("GameStart"), True),
        ("EdgeRaw", _edge_letter("Stadium"), _edge_letter("Wind"), True),
    ]


def test_polish_edge_styles_each_zone_label_column():
    # Each zone label (GAME/CEIL/MOVE/WX) sits OUTSIDE the collapsed range
    # it names (see EDGE_COLUMN_GROUPS' own comment) and gets a distinct,
    # always-visible tint so it reads as a divider, not a data column.
    client = FakeEdgeClient()
    polish_edge(client, "EdgeRaw")

    # Data rows only, not the header (see `_apply_zone_label_style`'s own
    # docstring for why: tinting the header cell too silently overwrote
    # the shared dark header fill, which `dfs setup audit-style` correctly
    # flags as a real defect).
    tinted_ranges = [a1 for a1, fmt in client.format_range_calls if fmt.get("backgroundColor") == FLAT_BG]
    for label in ZONE_LABELS:
        letter = _edge_letter(label)
        assert any(a1.startswith(f"{letter}2:") for a1 in tinted_ranges), label


def _letter_to_index(letter: str) -> int:
    n = 0
    for ch in letter:
        n = n * 26 + (ord(ch) - ord("A") + 1)
    return n - 1


def test_polish_edge_reset_before_hide_skips_columns_already_inside_a_group():
    # Real live incident, three times in one session (Name; Avail/Flags;
    # GAME/CEIL/MOVE, all on EdgeRaw): a killed/retried polish run left a
    # stray column hidden that should never have been, because the old
    # hide-Id/Flag loop only ever ADDED a hide, never reset a stale one.
    # The fix resets every non-grouped column visible first -- but must
    # skip columns already inside an EXISTING collapsed group, or it
    # desyncs the group (verified live: explicitly unhiding a grouped
    # range's columns makes them visible while the group's own metadata
    # still says collapsed=true). Columns 14-16 here (0-indexed) simulate
    # OverUnder/Spread/GameEnv already sitting inside a real group.
    client = FakeEdgeClient(grouped_column_indices={14, 15, 16})
    polish_edge(client, "EdgeRaw")

    unhide_calls = [(a, b) for a, b, hidden in client.hide_calls if hidden is False]
    touched = set()
    for start, end in unhide_calls:
        touched.update(range(_letter_to_index(start), _letter_to_index(end) + 1))
    assert not touched & {14, 15, 16}


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


def test_polish_edge_scales_eleven_columns_skipping_raw_player_metrics():
    # Phase 4 (4.1): EdgeRaw isn't position-grouped, so ProjPts/Ceiling/
    # Val/CeilVal (EDGE_UNSCALED_PLAYER_METRICS) are skipped there --
    # CeilPct/Leverage (already percentile) stand in for them, same as
    # OppPosRank (Phase 5, 2026-09-16 -- already comparable across
    # positions, needs no position-grouping either). `OwnPct` used to be
    # one of these too; dropped entirely from EDGE_COLUMNS in Part 7.9.
    # `ValAdj` (Part 7.2) joins this scaled set too -- already a
    # per-position residual, not a raw player metric, so it's excluded
    # from EDGE_UNSCALED_PLAYER_METRICS on purpose (see that constant's
    # own comment).
    edge_header = [POOL_HEADER, *EDGE_COLUMNS]
    matched = [
        name
        for name in edge_header
        if name in FIELD_COLOR_SCALES and name not in EDGE_UNSCALED_PLAYER_METRICS
    ]
    assert len(matched) == 11

    client = FakeEdgeClient()
    polish_edge(client, "EdgeRaw")

    assert len(client.color_scale_calls) == 11


def test_polish_edge_scales_raw_metrics_per_position_via_multi_range_rules():
    # Sam, 2026-09-18: filtering EdgeRaw to a position and sorting by a raw
    # stat should let him spot outliers -- "everything being white numbers
    # makes that very hard." EdgeRaw isn't grouped into position blocks
    # (it's one flat list sorted by Leverage/CeilPct), so a position's rows
    # are scattered non-contiguously -- verified live that one gradient
    # rule's `ranges` can hold multiple non-contiguous GridRanges with a
    # shared min/max computed only over their union.
    position_rows = [["QB"], ["RB"], ["QB"], ["WR"]]  # rows 2, 3, 4, 5
    client = FakeEdgeClient(position_rows=position_rows)
    polish_edge(client, "EdgeRaw")

    # 4 EDGE_UNSCALED_PLAYER_METRICS x 3 distinct positions (QB, RB, WR).
    assert len(client.multi_range_color_scale_calls) == 12

    proj_pts_col = _edge_letter("ProjPts")
    qb_spec = next(
        spec
        for spec in client.multi_range_color_scale_calls
        if spec["a1_ranges"][0].startswith(f"{proj_pts_col}2")
    )
    # QB occupies rows 2 and 4 -- non-contiguous, so two separate 1-row
    # ranges, not one run spanning 2-4 (which would wrongly include RB's
    # own row 3).
    assert qb_spec["a1_ranges"] == [f"{proj_pts_col}2:{proj_pts_col}2", f"{proj_pts_col}4:{proj_pts_col}4"]


def test_polish_edge_move_and_spread_scales_are_diverging_at_zero():
    client = FakeEdgeClient()
    polish_edge(client, "EdgeRaw")

    diverging = [kwargs for _rng, kwargs in client.color_scale_calls if kwargs.get("mid_type") == "NUMBER"]
    assert len(diverging) == 4  # ImpliedMove, TotMove, SpdMove, Spread
    assert all(kwargs["mid_value"] == "0" for kwargs in diverging)


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


class FakeNotesClient:
    def __init__(self, present_tabs: set[str]):
        self.present_tabs = present_tabs
        self.note_calls: list[tuple[str, str, str]] = []

    def tab_exists(self, tab_name: str) -> bool:
        return tab_name in self.present_tabs

    def set_note(self, tab_name: str, cell_a1: str, note: str) -> None:
        self.note_calls.append((tab_name, cell_a1, note))


def test_apply_tab_notes_sets_a1_on_every_present_tab():
    from dfs.sheet_style import TAB_NOTES, apply_tab_notes

    client = FakeNotesClient(present_tabs=set(TAB_NOTES))
    results = apply_tab_notes(client)

    assert len(client.note_calls) == len(TAB_NOTES)
    assert all(cell == "A1" for _tab, cell, _note in client.note_calls)
    assert all("A1 note set" in r for r in results)


def test_apply_tab_notes_skips_a_missing_tab_without_erroring():
    from dfs.sheet_style import TAB_NOTES, apply_tab_notes

    missing = next(iter(TAB_NOTES))
    client = FakeNotesClient(present_tabs=set(TAB_NOTES) - {missing})
    results = apply_tab_notes(client)

    assert f"{missing}: not present -- skipped" in results
    assert missing not in [tab for tab, _cell, _note in client.note_calls]


class FakeGuardrailsClient:
    def __init__(
        self, header: list[str], *, present: bool = True, formulas: dict[str, list[list]] | None = None
    ):
        self._header = header
        self._present = present
        self._formulas = formulas or {}
        self.width_calls: list[dict] = []
        self.update_calls: list[tuple[str, list[list]]] = []
        self.clear_calls: list[str | None] = []
        self.boolean_rule_calls: list[tuple[str, str, list[str], dict]] = []
        self.format_calls: list[tuple[str, dict]] = []
        self.clear_validation_calls: list[str] = []

    def tab_exists(self, tab_name: str) -> bool:
        return self._present

    def read_range(self, tab_name: str, a1_range: str):
        return [self._header] if self._header else []

    def read_formula(self, tab_name: str, a1_range: str):
        return self._formulas.get(a1_range, [[]])

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

    def format_range(self, tab_name: str, a1_range: str, fmt: dict) -> None:
        self.format_calls.append((a1_range, fmt))

    def clear_data_validation(self, tab_name: str, a1_range: str) -> None:
        self.clear_validation_calls.append(a1_range)


_HEADER_WITH_AVAIL_AT_Y = (
    ["Name", "Pos.", "Team", "DK Sal", "O/U", "Spread", "Team Implied", "Opp.", "Venue", "OppPosRank", "Pts"]
    + ["Ceil", "Val", "Own%", "", "% of Cap", "CeilVal", "CeilPct", "Leverage", "OwnStatus", "GameEnv"]
    + ["Stadium", "Roof", "Wind", "Avail", "Flags"]
)

# For polish_guardrails specifically: DK Sal and Issues are deliberately
# NOT at their old pre-Phase-3 letters (D and O) -- proves the by-name
# derivation the Phase 3 fix added, rather than coincidentally passing
# because a fixture still matches the old layout.
_HEADER_FOR_GUARDRAILS = (
    ["Name", "Team", "Pos.", "O/U", "Spread", "Team Implied", "Opp.", "Venue", "OppPosRank", "Pts", "DK Sal"]
    + ["Ceil", "Val", "Own%", "% of Cap", "CeilVal", "CeilPct", "Leverage", "OwnStatus", "GameEnv"]
    + ["Stadium", "Roof", "Wind", "Avail", "Flags", "Issues"]
)


class FakeBankrollClient:
    def __init__(self):
        self._present = True
        self.hide_calls: list[tuple[str, str]] = []

    def tab_exists(self, tab_name: str) -> bool:
        return self._present

    def clear_conditional_formats(self, tab_name: str, **_kwargs) -> None:
        pass

    def hide_columns(self, tab_name: str, first_col: str, last_col: str) -> None:
        self.hide_calls.append((first_col, last_col))

    def format_range(self, *_args, **_kwargs) -> None:
        pass

    def add_boolean_rule(self, *_args, **_kwargs) -> None:
        pass


def test_polish_bankroll_hides_the_dedupe_key_column():
    # Fix 2.17: "a stray column appears at L after sync" -- that's
    # sync_bucket's dedupe key, hidden (not deleted) so it stops reading
    # as an unexplained value in the middle of the ledger.
    client = FakeBankrollClient()

    polish_bankroll(client, "Bankroll", cash=(16, 17, 59), gpp=(63, 64, 149), entry_key_columns=("L", "L"))

    assert client.hide_calls == [("L", "L")]  # deduped -- cash and gpp share the same column


def test_polish_bankroll_hides_nothing_when_no_entry_key_columns_given():
    client = FakeBankrollClient()
    polish_bankroll(client, "Bankroll", cash=(16, 17, 59), gpp=(63, 64, 149))
    assert client.hide_calls == []


def test_polish_lineups_totals_rows_clears_dead_vlookups_sums_ceil_and_labels():
    # Fix 2.4 / Phase 3. Uses the same fixture/fake as polish_guardrails
    # below -- Team=C, DK Sal=D, O/U=E, Spread=F, Team Implied=G, Opp.=H,
    # Venue=I, OppPosRank=J, Ceil=L, Val=M, and the linked columns
    # scattered at Q,R,S,T,U,V,W,X,Y,Z.
    client = FakeGuardrailsClient(_HEADER_WITH_AVAIL_AT_Y)

    result = polish_lineups_totals_rows(client, "Lineups", header_row=8, name_blocks=[(9, 17)])

    calls = {a1: rows for a1, rows in client.update_calls}
    totals_row = 18  # end + 1

    # Dead VLOOKUP columns cleared on the totals row only -- native
    # lookups (O/U, Spread, Team Implied, OppPosRank) alongside the
    # linked block. Venue is NOT cleared/repurposed as a label any more --
    # it holds the real remaining-cap number a separate hand-authored row
    # depends on (see this function's own docstring).
    for letter in ("E", "F", "G", "J", "Q", "R", "S", "T", "U", "V", "W", "X", "Y", "Z"):
        assert calls[f"{letter}{totals_row}"] == [[""]]

    # Ceil, Pts and Rstr% all (re)summed unconditionally -- Phase 6, Part 1:
    # Pts/Rstr% turned out NOT to already hold real sums on most live
    # blocks (found live: a stale VLOOKUP-against-blank sitting there
    # instead), so both are now self-healed the same way Ceil already was.
    assert calls[f"L{totals_row}"] == [["=SUM(L9:L17)"]]
    assert calls[f"K{totals_row}"] == [["=SUM(K9:K17)"]]
    assert calls[f"N{totals_row}"] == [["=SUM(N9:N17)"]]

    # "Total" goes at Opp.'s column (H); the remaining-cap NUMBER (no
    # text -- a hand-authored row below reads it via INDIRECT) goes at
    # Venue's column (I); "Remaining" is a plain text label at Val's
    # column (M). All found by header name, so they land correctly
    # regardless of this fixture's scrambled layout, not stranded next to
    # Spread/O-U the way a hardcoded version once would have been.
    assert calls[f"H{totals_row}"] == [["Total"]]
    assert calls[f"I{totals_row}"] == [['=IF(COUNTA($A$9:$A$17)=0,"",50000-D18)']]
    assert calls[f"M{totals_row}"] == [["Remaining"]]

    assert "1 totals row(s)" in result
    assert "3 sum(s) written" in result


def test_polish_lineups_totals_rows_puts_a_bare_number_at_venue_never_text():
    # The exact regression this guards against: a separate, genuinely
    # hand-authored row directly below the totals row (documented in
    # docs/SHEET_REFERENCE.md, never written by any `dfs` command) reads
    # Venue's totals-row cell via `INDIRECT("E"&(ROW()-1))` and divides it
    # by a count -- a string-built reference `moveDimension` can't see or
    # retarget. Any text there (a label, or a self-labeled "Remaining
    # $X" string) produces #VALUE! on that row instead of a real number.
    # Found live, twice: once when Venue held "Total", and again when the
    # very first fix for that put self-labeled text there instead.
    client = FakeGuardrailsClient(_HEADER_WITH_AVAIL_AT_Y)

    polish_lineups_totals_rows(client, "Lineups", header_row=8, name_blocks=[(9, 17)])

    calls = {a1: rows for a1, rows in client.update_calls}
    venue_value = calls["I18"][0][0]
    assert venue_value.startswith("=IF(")
    assert "Remaining" not in venue_value
    assert "Total" not in venue_value


def test_polish_lineups_totals_rows_clears_the_totals_row_name_cells_typo_guard():
    # The totals row's Name cell (column A) still carried the same input
    # background and typo-guard player dropdown as a real roster slot --
    # a leftover from Fix 2.4 shrinking each block's own range to exclude
    # the totals row, never retroactively cleaned up off the row it
    # stopped covering. Found live, from a screenshot.
    client = FakeGuardrailsClient(_HEADER_WITH_AVAIL_AT_Y)

    result = polish_lineups_totals_rows(client, "Lineups", header_row=8, name_blocks=[(9, 17)])

    assert client.clear_validation_calls == ["A18"]
    assert client.format_calls == [("A18", {"backgroundColor": WHITE})]
    assert "1 Name cell(s) un-typo-guarded" in result


def test_polish_lineups_totals_rows_never_touches_salary_or_issues():
    # D (Salary) already holds a real SUM formula this function has never
    # needed to touch; O (Issues) holds the real guardrail formula --
    # neither is a dead VLOOKUP and neither should be cleared or
    # overwritten here. Pts (K) and Rstr% (N) are NOT in this list any
    # more -- Phase 6, Part 1 found live that they don't reliably already
    # hold a real sum, so both are now unconditionally rewritten (see
    # test_polish_lineups_totals_rows_clears_dead_vlookups_sums_ceil_and_labels).
    client = FakeGuardrailsClient(_HEADER_WITH_AVAIL_AT_Y)

    polish_lineups_totals_rows(client, "Lineups", header_row=8, name_blocks=[(9, 17)])

    touched = {a1 for a1, _ in client.update_calls}
    assert "D18" not in touched
    assert "O18" not in touched


def test_polish_lineups_totals_rows_self_heals_a_corrupted_pts_or_rstr_total():
    # The exact live bug found 2026-09-17: 15 of 20 real Lineups blocks had
    # `=VLOOKUP($A<totals_row>,PlayerPoolRaw!$A:S,11,false)` (or `,14,false`
    # for Rstr%) sitting in the totals row's Pts/Rstr% cells instead of a
    # SUM -- a lookup against the totals row's own always-blank Name cell,
    # which resolves to #N/A the instant a lineup in that block is built.
    # This function doesn't need to detect that specific formula: it
    # always overwrites both cells with a real SUM, so whatever was there
    # before (correct or corrupted) self-heals the same way on every call.
    client = FakeGuardrailsClient(_HEADER_WITH_AVAIL_AT_Y)

    result = polish_lineups_totals_rows(client, "Lineups", header_row=8, name_blocks=[(9, 17)])

    calls = {a1: rows for a1, rows in client.update_calls}
    assert calls["K18"] == [["=SUM(K9:K17)"]]
    assert calls["N18"] == [["=SUM(N9:N17)"]]
    assert "3 sum(s) written (Ceil/Pts/Own%)" in result


def test_polish_lineups_pct_of_cap_divides_by_the_salary_cap():
    # Part 7.9: `% of Cap` (renamed from `% of Own`, Part 2's own `% of
    # Rstr` before that) is this player's DK Sal as a share of the
    # SALARY CAP, not the lineup's own running total -- Sam confirmed the
    # intended meaning is cap allocation. A constant denominator can't
    # divide by zero, so unlike Part 1.2's original fix, only the
    # blank-slot guard remains.
    client = FakeGuardrailsClient(_HEADER_WITH_AVAIL_AT_Y)

    result = polish_lineups_pct_of_cap(
        client, "Lineups", header_row=8, name_blocks=[(9, 17)], salary_cap=50000
    )

    calls = {a1: rows for a1, rows in client.update_calls}
    # DK Sal = D, % of Cap = P.
    assert calls["P9"] == [['=IF(A9="","",D9/50000)']]
    assert calls["P17"] == [['=IF(A17="","",D17/50000)']]
    assert "9 row(s)" in result


def test_polish_lineups_pct_of_cap_skips_when_column_missing():
    header = [h for h in _HEADER_WITH_AVAIL_AT_Y if h != "% of Cap"]
    client = FakeGuardrailsClient(header)

    result = polish_lineups_pct_of_cap(
        client, "Lineups", header_row=8, name_blocks=[(9, 17)], salary_cap=50000
    )

    assert client.update_calls == []
    assert "not all present" in result


def test_polish_lineups_totals_rows_skips_when_lineups_missing():
    client = FakeGuardrailsClient(_HEADER_WITH_AVAIL_AT_Y, present=False)
    result = polish_lineups_totals_rows(client, "Lineups", header_row=8, name_blocks=[(9, 17)])
    assert result == "Lineups: not present -- skipped"
    assert client.update_calls == []


def test_polish_lineups_remaining_per_slot_helper_regenerates_with_derived_letters():
    # Phase 6, Part 2: moving Venue out of IDENTITY breaks the hand-typed
    # "average remaining per slot" helper row's `INDIRECT("E"&...)`
    # reference (E was Venue's old column). Simulate the existing
    # formula sitting at some arbitrary column (R here, not E -- proving
    # this is found by content, not assumed position) and confirm it's
    # rewritten in place with BOTH letters re-derived from the header.
    header = _HEADER_WITH_AVAIL_AT_Y  # Name=A, Venue=I
    helper_row = 19  # end (17) + 2
    old_formula = (
        '=IF(COUNTBLANK(INDIRECT("A"&(ROW()-10)&":A"&(ROW()-2)))=0,"",'
        'INDIRECT("E"&(ROW()-1))/COUNTBLANK(INDIRECT("A"&(ROW()-10)&":A"&(ROW()-2))))'
    )
    formulas = {f"A{helper_row}:Z{helper_row}": [[""] * 17 + [old_formula] + [""] * (len(header) - 18)]}
    client = FakeGuardrailsClient(header, formulas=formulas)

    result = polish_lineups_remaining_per_slot_helper(client, "Lineups", name_blocks=[(9, 17)], last_col="Z")

    calls = {a1: rows for a1, rows in client.update_calls}
    assert calls["R19"] == [
        [
            '=IF(COUNTBLANK(INDIRECT("A"&(ROW()-10)&":A"&(ROW()-2)))=0,"",'
            'INDIRECT("I"&(ROW()-1))/COUNTBLANK(INDIRECT("A"&(ROW()-10)&":A"&(ROW()-2))))'
        ]
    ]
    assert "1 block(s)" in result


def test_polish_lineups_remaining_per_slot_helper_skips_a_block_with_no_existing_formula():
    header = _HEADER_WITH_AVAIL_AT_Y
    client = FakeGuardrailsClient(header, formulas={})

    result = polish_lineups_remaining_per_slot_helper(client, "Lineups", name_blocks=[(9, 17)], last_col="Z")

    assert client.update_calls == []
    assert "0 block(s)" in result
    assert "1 block(s) had no existing formula" in result


def test_polish_lineups_remaining_per_slot_helper_skips_when_lineups_missing():
    client = FakeGuardrailsClient(_HEADER_WITH_AVAIL_AT_Y, present=False)
    result = polish_lineups_remaining_per_slot_helper(client, "Lineups", name_blocks=[(9, 17)], last_col="Z")
    assert result == "Lineups: not present -- skipped"
    assert client.update_calls == []


def test_polish_guardrails_skips_when_lineups_missing():
    client = FakeGuardrailsClient(_HEADER_FOR_GUARDRAILS, present=False)
    result = polish_guardrails(client, "Lineups", header_row=8, name_blocks=[(9, 18)])
    assert result == "Lineups: not present -- skipped"
    assert client.update_calls == []


def test_polish_guardrails_skips_when_avail_not_yet_linked():
    client = FakeGuardrailsClient(["Name", "Pos.", "Team"])  # link-edge never run
    result = polish_guardrails(client, "Lineups", header_row=8, name_blocks=[(9, 18)])
    assert "not linked yet" in result
    assert client.update_calls == []
    assert client.clear_calls == []


def test_polish_guardrails_skips_when_issues_column_not_found():
    # The exact regression this guards against: Phase 3 moved "Issues" to
    # a new column, and this function used to write to a hardcoded "O"
    # regardless -- clobbering whatever real column now sits at O (Flag,
    # after Phase 3) with a duplicate copy of the guardrails header and
    # formulas. Now it must refuse instead of guessing.
    header = [name for name in _HEADER_FOR_GUARDRAILS if name != "Issues"]
    client = FakeGuardrailsClient(header)
    result = polish_guardrails(client, "Lineups", header_row=8, name_blocks=[(9, 18)])
    assert "'Issues' column not found" in result
    assert client.update_calls == []


def test_polish_guardrails_skips_when_dk_sal_column_not_found():
    header = [name for name in _HEADER_FOR_GUARDRAILS if name != "DK Sal"]
    client = FakeGuardrailsClient(header)
    result = polish_guardrails(client, "Lineups", header_row=8, name_blocks=[(9, 18)])
    assert "'DK Sal' column not found" in result
    assert client.update_calls == []


def test_polish_guardrails_never_touches_a_column_other_than_issues():
    # Direct regression test for the real incident: Flag sits at a
    # DIFFERENT column than Issues in this fixture -- polish_guardrails
    # must never write there.
    client = FakeGuardrailsClient(_HEADER_FOR_GUARDRAILS)
    flag_col = column_letter(_HEADER_FOR_GUARDRAILS.index("Flags"))

    polish_guardrails(client, "Lineups", header_row=8, name_blocks=[(9, 17)])

    assert not any(a1.startswith(flag_col) for a1, _rows in client.update_calls)


def test_polish_guardrails_writes_slot_and_totals_formulas_against_the_real_avail_column():
    # Fix 2.4: `end` (17) is the block's own last REAL roster row now,
    # not the totals row -- the totals row is `end + 1` (18), a separate
    # row entirely. Avail/DK Sal/Issues are all found by header name here
    # (columns X/K/Z in this fixture -- deliberately not their old D/O
    # letters, see `_HEADER_FOR_GUARDRAILS`).
    client = FakeGuardrailsClient(_HEADER_FOR_GUARDRAILS)
    avail_col = column_letter(_HEADER_FOR_GUARDRAILS.index("Avail"))
    salary_col = column_letter(_HEADER_FOR_GUARDRAILS.index("DK Sal"))
    guardrails_col = column_letter(_HEADER_FOR_GUARDRAILS.index("Issues"))
    assert (avail_col, salary_col, guardrails_col) == ("X", "K", "Z")

    polish_guardrails(client, "Lineups", header_row=8, name_blocks=[(9, 17)])

    header_call = next(c for c in client.update_calls if c[0] == f"{guardrails_col}8")
    assert header_call[1] == [["Issues"]]

    block_call = next(c for c in client.update_calls if c[0] == f"{guardrails_col}9:{guardrails_col}18")
    rows = block_call[1]
    assert len(rows) == 10  # 9 slots + 1 totals row

    # Slot row 9: duplicate check over the block's own name range, then
    # falls back to that row's own Avail cell.
    assert rows[0] == [
        f'=IF($A9="","",IF(COUNTIF($A$9:$A$17,$A9)>1,"DUPLICATE",IF(${avail_col}9<>"",${avail_col}9,"")))'
    ]
    # Totals row (18): cap / completeness / OK, against DK Sal's column.
    assert rows[-1] == [
        '=IF(COUNTA($A$9:$A$17)=0,"",'
        f'IF({salary_col}18>50000,"OVER "&TEXT({salary_col}18-50000,"$#,##0"),'
        'IF(COUNTA($A$9:$A$17)<9,"INCOMPLETE "&COUNTA($A$9:$A$17)&"/9","OK")))'
    ]


def test_polish_guardrails_repeats_header_at_every_block():
    # Fix 2.6: the header was only ever written at `header_row`, so 19 of
    # 20 lineup blocks were missing it entirely.
    client = FakeGuardrailsClient(_HEADER_FOR_GUARDRAILS)
    guardrails_col = column_letter(_HEADER_FOR_GUARDRAILS.index("Issues"))

    polish_guardrails(
        client, "Lineups", header_row=8, name_blocks=[(9, 18), (22, 31)], header_repeats_at=[21]
    )

    assert next(c for c in client.update_calls if c[0] == f"{guardrails_col}8")[1] == [["Issues"]]
    assert next(c for c in client.update_calls if c[0] == f"{guardrails_col}21")[1] == [["Issues"]]


def test_polish_guardrails_widens_its_own_column_and_clears_only_its_own_rules():
    client = FakeGuardrailsClient(_HEADER_FOR_GUARDRAILS)
    guardrails_col = column_letter(_HEADER_FOR_GUARDRAILS.index("Issues"))

    polish_guardrails(client, "Lineups", header_row=8, name_blocks=[(9, 18)])

    assert client.width_calls == [{guardrails_col: 110}]
    assert client.clear_calls == [guardrails_col]  # never a blanket clear of Lineups' other rules


class FakeBuilderTabClient:
    def __init__(self, header: list[str], grouped_column_indices: set[int] | None = None):
        self._header = header
        self.format_calls: list[tuple[str, dict]] = []
        self.freeze_calls: list[tuple] = []
        self.width_calls: list[dict] = []
        self.clear_cf_calls: list[str] = []
        self.boolean_rule_calls: list[tuple[str, dict]] = []
        self.banding_calls: list[tuple] = []
        self.color_scale_calls: list[tuple[str, dict]] = []
        self.hide_calls: list[tuple[str, str, bool]] = []
        self._grouped_column_indices = grouped_column_indices or set()

    def tab_exists(self, tab_name: str) -> bool:
        return True

    def get_grouped_column_indices(self, tab_name: str) -> set[int]:
        return self._grouped_column_indices

    def read_range(self, tab_name: str, a1_range: str):
        return [self._header]

    def hide_columns(
        self, tab_name: str, first_col_a1: str, last_col_a1: str, *, hidden: bool = True
    ) -> None:
        self.hide_calls.append((first_col_a1, last_col_a1, hidden))

    def format_range(self, tab_name: str, a1_range: str, fmt: dict) -> None:
        self.format_calls.append((a1_range, fmt))

    def freeze(self, tab_name: str, *, rows=None, cols=None) -> None:
        self.freeze_calls.append((tab_name, rows, cols))

    def set_column_widths(self, tab_name: str, widths: dict[str, int]) -> None:
        self.width_calls.append(widths)

    def clear_conditional_formats(self, tab_name: str, *, column=None, row_range=None) -> None:
        self.clear_cf_calls.append(column)

    def clear_banding(self, tab_name: str) -> None:
        pass

    def add_row_banding(self, tab_name: str, a1_range: str, **kwargs) -> None:
        self.banding_calls.append((a1_range, kwargs))

    def add_color_scale(self, tab_name: str, a1_range: str, **kwargs) -> None:
        self.color_scale_calls.append((a1_range, kwargs))

    def add_color_scales(self, tab_name: str, specs: list[dict]) -> None:
        for spec in specs:
            spec = dict(spec)
            self.color_scale_calls.append((spec.pop("a1_range"), spec))

    def add_boolean_rule(self, tab_name: str, a1_range: str, *, condition_type, values, fmt) -> None:
        self.boolean_rule_calls.append(
            (a1_range, {"condition_type": condition_type, "values": values, "fmt": fmt})
        )

    def add_boolean_rules(self, tab_name: str, specs: list[dict]) -> None:
        for spec in specs:
            spec = dict(spec)
            a1_range = spec.pop("a1_range")
            self.boolean_rule_calls.append((a1_range, spec))


def test_apply_grouped_color_scales_writes_one_rule_per_column_per_group():
    # Phase 4 (4.1/4.2): 3 scaled columns x 2 groups = 6 gradient rules --
    # verified live that one rule can't independently scale multiple
    # groups (see CONTRIBUTING.md's Phase 4 changelog), so this is
    # genuinely len(groups) * matched_columns, not a smaller number.
    client = FakeBuilderTabClient(["Name", "Pts", "Ceil", "Val"])
    applied = apply_grouped_color_scales(client, "Player Pool", client._header, [(3, 12), (14, 33)])

    assert applied == 6
    ranges = {a1 for a1, _kwargs in client.color_scale_calls}
    assert ranges == {"B3:B12", "C3:C12", "D3:D12", "B14:B33", "C14:C33", "D14:D33"}


def test_apply_grouped_color_scales_skips_the_grouped_tab_unscaled_columns():
    # `OwnPct` used to sit in GROUPED_TAB_UNSCALED_COLUMNS too; dropped
    # entirely from the sheet in Part 7.9. `ValAdj` (Part 7.2) joins
    # `CeilPct` here now -- also already a per-position value computed
    # once on EdgeRaw, so re-grouping it per position block here would be
    # redundant.
    client = FakeBuilderTabClient(["Name", "Pts", "CeilPct"])
    applied = apply_grouped_color_scales(
        client, "Player Pool", client._header, [(3, 12)], skip=GROUPED_TAB_UNSCALED_COLUMNS
    )

    assert applied == 1
    assert {a1 for a1, _ in client.color_scale_calls} == {"B3:B12"}
    assert GROUPED_TAB_UNSCALED_COLUMNS == {"CeilPct", "ValAdj"}


def test_apply_grouped_color_scales_scopes_zero_exclusion_to_each_groups_own_range():
    # Own% (renamed from Rstr% in Part 2) is zero-excluded (Fix 2.7) --
    # per-block, the MINIFS formula must read that BLOCK's own range, not
    # the whole tab, or one position's unpublished-ownership zeros would
    # pollute another's.
    assert "Own%" in ZERO_EXCLUDED_COLUMNS
    client = FakeBuilderTabClient(["Name", "Own%"])
    apply_grouped_color_scales(client, "Player Pool", client._header, [(3, 12), (14, 33)])

    scales_by_range = dict(client.color_scale_calls)
    assert scales_by_range["B3:B12"]["min_value"] == '=MINIFS(B3:B12,B3:B12,"<>0")'
    assert scales_by_range["B14:B33"]["min_value"] == '=MINIFS(B14:B33,B14:B33,"<>0")'
    # A zero-exclusion grey chip lands per group too, same range each.
    zero_ranges = {a1 for a1, _ in client.boolean_rule_calls}
    assert zero_ranges == {"B3:B12", "B14:B33"}


def test_apply_grouped_color_scales_honors_skip_argument():
    client = FakeBuilderTabClient(["Name", "Pts", "Leverage"])
    applied = apply_grouped_color_scales(
        client, "Player Pool", client._header, [(3, 12)], skip=frozenset({"Leverage"})
    )
    assert applied == 1
    assert {a1 for a1, _ in client.color_scale_calls} == {"B3:B12"}


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


def test_polish_builder_tab_chips_flag_and_avail_columns_when_present():
    # PlayerPoolRaw/Player Pool/Lineups all carry the same linked Flags/
    # Avail columns EdgeRaw has, but nothing applied their chips there --
    # found by `dfs setup audit-style`. Column-scoped clear, since
    # link_edge_columns' own colour scales and polish_guardrails' column
    # O live on this same tab and must not be touched.
    client = FakeBuilderTabClient(["Name", "Flags", "Avail"])

    result = polish_builder_tab(client, "Player Pool", last_row=100, header_row=1)

    # A leading `None` is the whole-tab clear every polish_builder_tab run
    # opens with (Fix 2) -- before that, only the per-chip-column clears.
    assert client.clear_cf_calls == [None, "B", "C"]
    flag_rules = [r for a1, r in client.boolean_rule_calls if a1 == "B2:B100"]
    avail_rules = [r for a1, r in client.boolean_rule_calls if a1 == "C2:C100"]
    assert len(flag_rules) == len(FLAG_CHIPS)
    assert len(avail_rules) == len(AVAIL_CHIPS)
    assert "2 chip column(s)" in result


def test_polish_builder_tab_chips_source_column_when_present():
    from dfs.sheet_style import SOURCE_CHIPS

    client = FakeBuilderTabClient(["Name", "Source"])
    result = polish_builder_tab(client, "Player Pool", last_row=100, header_row=1)

    assert client.clear_cf_calls == [None, "B"]
    source_rules = [r for a1, r in client.boolean_rule_calls if a1 == "B2:B100"]
    assert len(source_rules) == len(SOURCE_CHIPS)
    assert "1 chip column(s)" in result


def test_polish_builder_tab_chips_venue_column_when_present():
    client = FakeBuilderTabClient(["Name", "Venue"])
    result = polish_builder_tab(client, "Player Pool", last_row=100, header_row=1)

    assert client.clear_cf_calls == [None, "B"]
    venue_rules = [r for a1, r in client.boolean_rule_calls if a1 == "B2:B100"]
    assert len(venue_rules) == len(VENUE_CHIPS)
    assert "1 chip column(s)" in result


def test_polish_builder_tab_skips_chips_when_flag_and_avail_absent():
    client = FakeBuilderTabClient(["Name", "Pos.", "Team"])

    result = polish_builder_tab(client, "Player Pool", last_row=100, header_row=1)

    assert client.clear_cf_calls == [None]
    # Position tint still fires (Pos. is present) -- only the FLAG_CHIPS/
    # AVAIL_CHIPS/etc chip loop is skipped when its own columns are absent.
    assert all(a1.startswith("B") for a1, _ in client.boolean_rule_calls)
    assert len(client.boolean_rule_calls) == 5  # one per POSITION_TINTS entry
    assert "0 chip column(s)" in result


def test_polish_builder_tab_tints_every_pool_tag_defined_in_pool_tag_tints():
    # Part 7.10: bands Player Pool's own `Pool` column by tag
    # (Both/Cash/GPP) so the new sort groups read visually.
    client = FakeBuilderTabClient(["Name", "Pool"])
    polish_builder_tab(client, "Player Pool", last_row=100, header_row=1)

    tinted_tags = {
        kwargs["values"][0]
        for a1, kwargs in client.boolean_rule_calls
        if a1 == "B2:B100" and kwargs["condition_type"] == "TEXT_EQ" and kwargs["values"][0] in POOL_TAG_TINTS
    }
    assert tinted_tags == set(POOL_TAG_TINTS)


def test_polish_builder_tab_skips_pool_tag_tint_when_pool_column_absent():
    # PlayerPoolRaw/Lineups have no `Pool` column -- only Player Pool
    # surfaces the tag (see `_apply_pool_tag_tint`'s own docstring).
    client = FakeBuilderTabClient(["Name", "Pos."])
    polish_builder_tab(client, "PlayerPoolRaw", last_row=100, header_row=1)

    assert not any(
        kwargs["values"][0] in POOL_TAG_TINTS
        for _a1, kwargs in client.boolean_rule_calls
        if kwargs["condition_type"] == "TEXT_EQ"
    )


def test_polish_builder_tab_applies_wind_chip_when_present():
    client = FakeBuilderTabClient(["Name", "Wind"])
    polish_builder_tab(client, "Player Pool", last_row=100, header_row=1)

    wind_rules = [r for a1, r in client.boolean_rule_calls if a1 == "B2:B100"]
    assert len(wind_rules) == 1
    assert wind_rules[0]["condition_type"] == "NUMBER_GREATER"


def test_polish_builder_tab_greys_own_status_when_present():
    client = FakeBuilderTabClient(["Name", "OwnStatus"])
    polish_builder_tab(client, "Player Pool", last_row=100, header_row=1)

    formatted = [rng for rng, _ in client.format_calls if rng == "B2:B100"]
    assert formatted, "OwnStatus column should be formatted"


def test_polish_builder_tab_styles_zone_label_columns():
    client = FakeBuilderTabClient(["Name", "GAME", "CEIL", "MOVE", "WX"])
    polish_builder_tab(client, "Player Pool", last_row=100, header_row=1)

    # Data rows only (2:100), not the header row -- see
    # `_apply_zone_label_style`'s own docstring for why.
    tinted = {rng for rng, fmt in client.format_calls if fmt.get("backgroundColor") == FLAT_BG}
    for letter in ("B", "C", "D", "E"):
        assert f"{letter}2:{letter}100" in tinted


def test_polish_builder_tab_reset_before_hide_skips_columns_already_inside_a_group():
    # Same fix, same reasoning as polish_edge's own version -- see that
    # test's docstring. Column B (index 1) simulates a column already
    # inside a real collapsed group; the reset pass must not touch it.
    client = FakeBuilderTabClient(["Name", "O/U", "Id"], grouped_column_indices={1})
    polish_builder_tab(client, "Player Pool", last_row=100, header_row=1)

    unhide_calls = [(a, b) for a, b, hidden in client.hide_calls if hidden is False]
    touched = set()
    for start, end in unhide_calls:
        touched.update(range(_letter_to_index(start), _letter_to_index(end) + 1))
    assert 1 not in touched


def test_polish_builder_tab_bolds_name_on_flag_without_pool_tint():
    # Player Pool/Lineups have no unpooled rows to tint against (every row
    # is already a pool pick or roster slot) -- unlike EdgeRaw, this must
    # be a plain bold-on-Flag rule, not the three-way pooled/flagged split.
    client = FakeBuilderTabClient(["Name", "Flag"])
    polish_builder_tab(client, "Player Pool", last_row=100, header_row=1)

    name_rules = [r for a1, r in client.boolean_rule_calls if a1 == "A2:A100"]
    assert len(name_rules) == 1
    assert name_rules[0]["fmt"] == {"textFormat": {"bold": True}}


def test_polish_builder_tab_clears_conditional_formats_whole_tab_first():
    # Player Pool/Lineups had accumulated hand-applied rules (including a
    # multi-column one) a column-scoped clear alone can't reliably catch
    # -- this is what actually removes them (Fix 2).
    client = FakeBuilderTabClient(["Name", "Pos.", "Team"])
    polish_builder_tab(client, "Player Pool", last_row=100, header_row=1)
    assert client.clear_cf_calls[0] is None


def test_polish_builder_tab_bands_the_whole_span_when_no_band_blocks_given():
    client = FakeBuilderTabClient(["Name", "Pos.", "Team"])
    polish_builder_tab(client, "PlayerPoolRaw", last_row=987, header_row=1)
    assert client.banding_calls == [("A2:C987", {"first_band_color": WHITE, "second_band_color": BAND_BG})]


def test_polish_builder_tab_bands_each_block_independently():
    client = FakeBuilderTabClient(["Name", "Pos.", "Team"])
    polish_builder_tab(client, "Player Pool", last_row=80, header_row=1, band_blocks=[(2, 11), (13, 32)])
    ranges = [a1 for a1, _kwargs in client.banding_calls]
    assert ranges == ["A2:C11", "A13:C32"]


def test_polish_builder_tab_color_scales_every_matching_column():
    client = FakeBuilderTabClient(["Name", "Pts", "Leverage", "OppPosRank"])
    result = polish_builder_tab(client, "Player Pool", last_row=100, header_row=1)

    scaled_ranges = {a1 for a1, _kwargs in client.color_scale_calls}
    assert scaled_ranges == {"B2:B100", "C2:C100", "D2:D100"}
    # OppPosRank is reversed -- max colour at the MIN end.
    opp_kwargs = next(kwargs for a1, kwargs in client.color_scale_calls if a1 == "D2:D100")
    assert opp_kwargs["min_color"] == GRAD_MAX
    assert "3 colour scale(s)" in result


def _chip(bg: dict, fg: dict) -> dict:
    return {"backgroundColor": bg, "textFormat": {"bold": True, "foregroundColor": fg}}


def test_polish_guardrails_chips_cover_every_documented_state():
    client = FakeGuardrailsClient(_HEADER_FOR_GUARDRAILS)
    guardrails_col = column_letter(_HEADER_FOR_GUARDRAILS.index("Issues"))

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
    # Every rule targets the Issues column only, across the full block
    # range given PLUS its totals row (19 = end + 1, Fix 2.4).
    for a1_range, *_ in client.boolean_rule_calls:
        assert a1_range == f"{guardrails_col}2:{guardrails_col}19"


class FakeTier23Client:
    def __init__(self, header: list[str] | None, *, present: bool = True):
        self._header = header
        self._present = present
        self.format_calls: list[tuple[str, dict]] = []
        self.freeze_calls: list[tuple] = []
        self.width_calls: list[dict] = []
        self.clear_cf_calls: int = 0
        self.boolean_rule_calls: list[tuple[str, dict]] = []
        self.color_scale_calls: list[tuple[str, dict]] = []

    def tab_exists(self, tab_name: str) -> bool:
        return self._present

    def read_range(self, tab_name: str, a1_range: str):
        return [self._header] if self._header else []

    def format_range(self, tab_name: str, a1_range: str, fmt: dict) -> None:
        self.format_calls.append((a1_range, fmt))

    def freeze(self, tab_name: str, *, rows=None, cols=None) -> None:
        self.freeze_calls.append((tab_name, rows, cols))

    def set_column_widths(self, tab_name: str, widths: dict[str, int]) -> None:
        self.width_calls.append(widths)

    def clear_conditional_formats(self, tab_name: str, *, column=None, row_range=None) -> None:
        self.clear_cf_calls += 1

    def add_boolean_rule(self, tab_name: str, a1_range: str, *, condition_type, values, fmt) -> None:
        rule = {"condition_type": condition_type, "values": values, "fmt": fmt}
        self.boolean_rule_calls.append((a1_range, rule))

    def add_color_scale(self, tab_name: str, a1_range: str, **kwargs) -> None:
        self.color_scale_calls.append((a1_range, kwargs))


def test_style_flat_tab_skips_missing_tab():
    result = style_flat_tab(FakeTier23Client(None, present=False), "Scratch", last_row=20)
    assert result == "Scratch: not present -- skipped"


def test_style_flat_tab_skips_empty_header():
    result = style_flat_tab(FakeTier23Client([]), "SoSComb", last_row=40)
    assert "empty header row" in result


def test_style_flat_tab_styles_header_freezes_and_widths_every_column():
    client = FakeTier23Client(["QB", "RB", "RB", "WR", "WR", "WR", "TE", "FLEX", "DST"])

    style_flat_tab(client, "Scratch", last_row=20)

    assert client.format_calls[0] == ("A1:I1", HEADER_FMT)
    assert client.freeze_calls == [("Scratch", 1, None)]
    widths = client.width_calls[0]
    assert set(widths) == {column_letter_for(i) for i in range(9)}
    # None of these header names are in BUILDER_WIDTHS -- every one falls
    # back to the generic width, not Sheets' own 100px default.
    assert all(px != 100 for px in widths.values())


def column_letter_for(i: int) -> str:
    from dfs.sheets import column_letter

    return column_letter(i)


def test_style_results_chips_cash_results_and_scales_h2h_pct():
    header = ["Week", "Cash Pts", "Cash Line", "Cash Results", "H2H Entered", "H2H Win", "H2H %"]
    client = FakeTier23Client(header)

    result = style_results(client, last_row=30)

    assert client.clear_cf_calls == 1
    true_rule = next(r for a1, r in client.boolean_rule_calls if r["values"] == ["TRUE"])
    false_rule = next(r for a1, r in client.boolean_rule_calls if r["values"] == ["FALSE"])
    assert true_rule["fmt"] == _chip(OK_BG, OK_FG)
    assert false_rule["fmt"] == _chip(CRIT_BG, CRIT_FG)
    h2h_range, _ = client.color_scale_calls[0]
    assert h2h_range == "G2:G30"  # H2H % is the 7th column
    assert "Cash Results/H2H % coloured" in result


def test_style_sos_tab_skips_when_nothing_pasted_yet():
    result = style_sos_tab(FakeTier23Client([]), "SoSQB")
    assert "nothing pasted yet" in result


def test_style_sos_tab_scales_rank_reversed():
    header = ["Team", "Team.1", "Rank", "Opp Avg", "PAE", "Week 1"]
    client = FakeTier23Client(header)

    result = style_sos_tab(client, "SoSQB")

    rank_range, colors = client.color_scale_calls[0]
    assert rank_range == "C2:C33"
    # Reversed from the standard scale: max colour at the MIN end.
    assert colors["min_color"] == GRAD_MAX
    assert colors["max_color"] == GRAD_MIN
    assert "Rank scaled (reversed)" in result


def test_style_movement_finds_columns_by_name_not_position():
    # Section F: build_movement's header is now 4-7 columns wide depending
    # on which optional columns EdgeRaw/GameStart provide, so this must be
    # header-name-driven, not a hardcoded A:E range.
    client = FakeBuilderTabClient(["Player", "Pos", "Implied move", "Total move", "Spread move", "Flags"])

    result = style_movement(client, "Movement")

    scale_ranges = {a1 for a1, _ in client.color_scale_calls}
    assert scale_ranges == {"C4:C60", "D4:D60", "E4:E60"}
    flag_rules = [r for a1, r in client.boolean_rule_calls if a1 == "F4:F60"]
    assert len(flag_rules) == len(FLAG_CHIPS)
    assert "3 movement column(s) colour-scaled" in result


def test_style_movement_handles_the_narrower_no_kickoff_no_extras_shape():
    client = FakeBuilderTabClient(["Player", "Pos", "Implied move", "Flags"])

    result = style_movement(client, "Movement")

    scale_ranges = {a1 for a1, _ in client.color_scale_calls}
    assert scale_ranges == {"C4:C60"}
    flag_rules = [r for a1, r in client.boolean_rule_calls if a1 == "D4:D60"]
    assert len(flag_rules) == len(FLAG_CHIPS)
    assert "1 movement column(s) colour-scaled" in result


def test_style_movement_skips_when_tab_absent():
    class AbsentClient(FakeBuilderTabClient):
        def tab_exists(self, tab_name: str) -> bool:
            return False

    client = AbsentClient(["Player"])
    result = style_movement(client, "Movement")
    assert "not present" in result


def test_style_tier23_tabs_covers_every_expected_tab():
    client = FakeTier23Client(["A", "B"])
    results = style_tier23_tabs(
        client,
        scratch_last_row=20,
        dk_upload_last_row=200,
        dk_lineups_final_last_row=500,
        results_last_row=30,
        sos_comb_last_row=40,
    )
    assert len(results) == 10  # Scratch, DK Upload, DKLineupsFinal, Results, 5xSoS, SoSComb
