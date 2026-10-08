from dfs.derived import EDGE_COLUMNS, ZONE_LABELS
from dfs.sheet_color_scales import (
    FIELD_COLOR_SCALES,
    GRAD_MAX,
    GRAD_MIN,
    WARM_MID,
    WHITE,
    ZERO_EXCLUDED_COLUMNS,
    ZERO_GREY_BG,
)
from dfs.sheet_style import (
    AVAIL_CHIPS,
    BAND_BG,
    BANKROLL_CURRENCY_CELLS,
    BANKROLL_KPI_LAST_ROW,
    BANKROLL_PERCENT_CELLS,
    BANKROLL_VALUE_CELLS,
    CENTERED_COLUMNS,
    CRIT_BG,
    CRIT_FG,
    EDGE_COLUMN_GROUPS,
    EDGE_ROWS,
    EDGE_WIDTHS,
    FAMILY_COLORS,
    FIELD_FORMATS,
    FLAG_CHIPS,
    FLAT_BG,
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
    _edge_letter,
    apply_field_color_scales,
    apply_field_formats,
    apply_grouped_color_scales,
    apply_tab_chrome,
    column_alignment,
    polish_bankroll,
    polish_builder_tab,
    polish_edge,
    polish_guardrails,
    polish_lineups_identity_cells,
    polish_lineups_pct_of_cap,
    polish_lineups_totals_rows,
    style_board,
    style_flat_tab,
    style_movement,
    style_results,
    style_slate_grid,
    style_sos_tab,
    style_tier23_tabs,
)
from dfs.sheet_views import (
    BOARD_CHALK_HEADER_ROW,
    BOARD_LAST_ROW,
    BOARD_LEADERS_FIRST_ROW,
    BOARD_LEADERS_LAST_ROW,
    BOARD_LEADERS_PCT_COL_INDEX,
    BOARD_LEADERS_SUBLABEL_ROW,
    BOARD_QUEUE_COLHEADER_ROW,
    BOARD_QUEUE_LAST_ROW,
    BOARD_ROWS_PER_POSITION,
    BOARD_SLATE_COLHEADER_ROW,
    BOARD_SLATE_GAMEID_COL_INDEX,
    BOARD_SLATE_LAST_ROW,
)
from dfs.sheets import column_letter
from dfs.sources.edge import POOL_HEADER


def test_edge_widths_and_groups_only_name_real_edge_columns():
    # Each dict is keyed by EdgeRaw column NAME so a reordered EDGE_COLUMNS
    # still styles the right column -- but that only works if every key
    # actually is a current EDGE_COLUMNS entry. A typo'd or removed name
    # here doesn't error, it just silently styles nothing (_edge_letter
    # returns None), so this pins the dicts against drifting from the
    # column list without anyone noticing. "Pool" is the one deliberate
    # exception (Part B widths-audit extension): it's EdgeRaw's own fixed
    # column A, never an EDGE_COLUMNS entry (that's the whole reason
    # EDGE_DATA_OFFSET exists) -- _edge_letter special-cases it directly.
    for name in EDGE_WIDTHS:
        if name == "Pool":
            continue
        assert name in EDGE_COLUMNS, f"{name!r} in EDGE_WIDTHS is not an EDGE_COLUMNS entry"
    for first, last in EDGE_COLUMN_GROUPS:
        assert first in EDGE_COLUMNS
        assert last in EDGE_COLUMNS


def test_field_color_scales_covers_every_edgeraw_decision_column_not_salary():
    # FIELD_COLOR_SCALES is shared across every tab (Fix 2.1), so it also
    # carries builder-only header text ("Team Implied"...) that isn't a
    # literal EDGE_COLUMNS name -- pin the EdgeRaw-side subset that matters.
    edge_header = [POOL_HEADER, *EDGE_COLUMNS]
    matched = {name for name in edge_header if name in FIELD_COLOR_SCALES}
    assert matched == {
        # Edge Finder (2026-10-07)
        "CalPts",
        "xFP/G",
        "Hit3x%",
        "Boom%",
        "Bust%",
        "ProjPts",
        "AggPts",
        "Own%",
        "Ceiling",
        "Val",
        "ValAdj",
        "CeilVal",
        "Leverage",
        "GameEnv",
        "Pace",
        "PROE",
        "Expl%",
        "OppPosRank",
        "OppEPA",
        "ImpliedMove",
        "TotMove",
        "SpdMove",
        "OverUnder",
        "Spread",
        "CeilPct",
        # Usage volume (2026-10-02), compared within position like ProjPts.
        "Tgt%",
        "WOPR",
        "Rush%",
        "RZ/G",
        "HVT/G",
    }
    assert "Salary" not in FIELD_COLOR_SCALES  # a constraint, not a quality -- left neutral
    # Movement diverges around zero, Own% is the warm chalk scale, Leverage is zero-centred.
    for name in ("ImpliedMove", "TotMove", "SpdMove", "Leverage"):
        assert FIELD_COLOR_SCALES[name] == "diverging"
    assert FIELD_COLOR_SCALES["Own%"] == "warm"
    # Player metrics are compared within position (percentile steps); the 0-100 scores step on
    # their own value; the columns with no position are smooth gradients.
    for name in ("ProjPts", "AggPts", "Ceiling", "Val", "CeilVal", "Pts", "Ceil"):
        assert FIELD_COLOR_SCALES[name] == "pct"
    for name in ("ValAdj", "CeilPct", "GameEnv"):
        assert FIELD_COLOR_SCALES[name] == "score"
    for name in ("Spread", "Pace", "OppPosRank"):
        assert FIELD_COLOR_SCALES[name] == "reversed"
    for name in ("Total", "PROE", "OppEPA"):
        assert FIELD_COLOR_SCALES[name] == "gradient"


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

        def clear_conditional_formats_for(self, tab_name, targets):
            for column, row_range in targets:
                self.clear_conditional_formats(tab_name, column=column, row_range=row_range)

        def add_color_scale(self, tab_name, a1_range, **kwargs):
            calls.append(("scale", a1_range, kwargs))

        def add_color_scales(self, tab_name, specs):
            for spec in specs:
                spec = dict(spec)
                self.add_color_scale(tab_name, spec.pop("a1_range"), **spec)

        def add_boolean_rule(self, tab_name, a1_range, *, condition_type, values, fmt):
            calls.append(("bool", a1_range, condition_type, values, fmt))

        def add_boolean_rules(self, tab_name, specs):
            for spec in specs:
                spec = dict(spec)
                self.add_boolean_rule(tab_name, spec.pop("a1_range"), **spec)

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
    # Own%: low end = the lowest NON-ZERO value (a zero must not anchor it), midpoint = the 20%
    # CHALK line, high end = the real maximum
    assert scale_call[2]["min_value"] == '=MINIFS($B$2:$B$100,$B$2:$B$100,"<>0")'
    assert scale_call[2]["mid_value"] == "0.2"
    assert "max_value" not in scale_call[2]

    bool_call = calls[2]
    assert bool_call[1] == "B2:B100"
    assert bool_call[2] == "NUMBER_EQ"
    assert bool_call[3] == ["0"]
    assert bool_call[4] == {"backgroundColor": ZERO_GREY_BG}


def test_apply_field_color_scales_excludes_zero_for_every_gradient_column():
    # PROMPT_BOARD_FIXES.md item 7 (2026-09-25): a common real zero (363/658 ProjPts zeros, mostly
    # OUT/deep-backup players) must not anchor a scale. A step column never matches a zero (it
    # has to be a number and non-zero), and a gradient column's ends and median skip zeros.
    scales = []
    booleans = []

    class _Client:
        def clear_conditional_formats(self, tab_name, column=None, row_range=None):
            pass

        def clear_conditional_formats_for(self, tab_name, targets):
            pass

        def add_color_scales(self, tab_name, specs):
            scales.extend(specs)

        def add_boolean_rules(self, tab_name, specs):
            booleans.extend(specs)

    apply_field_color_scales(
        _Client(), "EdgeRaw", ["Name", "ValAdj", "OverUnder"], header_row=1, last_row=100
    )

    # ValAdj (a 0-100 score): twenty-six steps that need ISNUMBER and a non-zero, then the grey chip.
    valadj = [b for b in booleans if b["a1_range"] == "B2:B100"]
    *steps, chip = valadj
    assert len(steps) == 26 and all("ISNUMBER($B2),$B2<>0" in b["values"][0] for b in steps)
    assert chip["condition_type"] == "NUMBER_EQ" and chip["values"] == ["0"]
    # OverUnder: one gradient whose ends/median come from NON-ZERO values only, plus the chip.
    (grad,) = scales
    assert grad["a1_range"] == "C2:C100"
    assert "FILTER(" in grad["min_value"] and "FILTER(" in grad["mid_value"]


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

    def clear_conditional_formats_for(self, tab_name, targets) -> None:
        # One recorded call per batched clear (the whole point of item 2), not one per target.
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

    def add_color_scales(self, tab_name: str, specs: list[dict]) -> None:
        for spec in specs:
            spec = dict(spec)
            self.add_color_scale(tab_name, spec.pop("a1_range"), **spec)

    def add_boolean_rule(self, tab_name: str, a1_range: str, *, condition_type, values, fmt) -> None:
        self.calls.append("add_boolean_rule")
        rule = {"condition_type": condition_type, "values": values, "fmt": fmt}
        self.boolean_rule_calls.append((a1_range, rule))

    def add_boolean_rules(self, tab_name: str, specs: list[dict]) -> None:
        self.calls.append("add_boolean_rules")
        for spec in specs:
            self.add_boolean_rule(
                tab_name,
                spec["a1_range"],
                condition_type=spec["condition_type"],
                values=spec["values"],
                fmt=spec["fmt"],
            )

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
    # adjacent and Sheets keeps them independently collapsible. GAME's own
    # range ends at TmRank, not OppPosRank -- Part 7.4 added GameID/TmRank
    # right after OppPosRank in EDGE_COLUMNS, and EDGE_COLUMN_GROUPS'
    # first tuple missed updating at the time, leaving both outside the
    # collapsed range on EdgeRaw specifically until this fix (2026-09-18).
    client = FakeEdgeClient()
    polish_edge(client, "EdgeRaw")

    assert client.group_calls == [
        ("EdgeRaw", _edge_letter("OverUnder"), _edge_letter("TmRank"), True),
        ("EdgeRaw", _edge_letter("CeilPct"), _edge_letter("OwnStatus"), True),
        ("EdgeRaw", _edge_letter("ImpliedMove"), _edge_letter("GameStart"), True),
        ("EdgeRaw", _edge_letter("Stadium"), _edge_letter("Wind"), True),
        ("EdgeRaw", _edge_letter("Snap%"), _edge_letter("HVT/G"), True),  # USAGE: Snap% + usage metrics
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


def test_polish_edge_colours_every_scaled_column_with_whole_column_rules_not_per_position_runs():
    edge_header = [POOL_HEADER, *EDGE_COLUMNS]
    matched = [name for name in edge_header if name in FIELD_COLOR_SCALES]
    assert len(matched) == 30  # nothing is skipped on EdgeRaw (20 + five usage metrics + five Edge Finder)

    client = FakeEdgeClient()
    polish_edge(client, "EdgeRaw")

    # The 15 within-position/0-100 columns (8 + five usage metrics + CalPts and xFP/G) are 26 steps each;
    # the other 15 (12 + Hit3x%, Boom%, Bust%) are one gradient each.
    # Either way: whole-column rules (they follow any sort or filter), no per-position row runs.
    assert len(client.color_scale_calls) == 15
    assert client.multi_range_color_scale_calls == []
    steps = [
        a1
        for a1, k in client.boolean_rule_calls
        if k["condition_type"] == "CUSTOM_FORMULA"
        and not a1.startswith("B2:")  # B = Name tints (columns BH.. are real data columns now)
        and "DST" not in k["values"][0]  # the low-confidence DST rules are not colour steps
    ]
    assert len(steps) == 15 * 26
    # A grey zero chip on every column where zero means "missing" (not the four zero-centred
    # diverging columns, and not Spread, where 0 is a real pick'em).
    zero_chips = [a1 for a1, k in client.boolean_rule_calls if k["condition_type"] == "NUMBER_EQ"]
    assert len(zero_chips) == 30 - 5
    assert len(client.boolean_rule_calls) < 520  # a handful per column, not ~1,400 in total


def test_polish_edge_steps_player_metrics_off_their_hidden_percentile_column():
    # ProjPts is coloured by `ProjPts%ile` -- a hidden helper holding the player's standing within
    # his position -- via custom formulas with a RELATIVE row reference, so the colour follows the
    # row through any sort or filter.
    client = FakeEdgeClient()
    polish_edge(client, "EdgeRaw")

    header = [POOL_HEADER, *EDGE_COLUMNS]
    proj_col = column_letter(header.index("ProjPts"))
    pct_col = column_letter(header.index("ProjPts%ile"))
    mine = [(a1, k) for a1, k in client.boolean_rule_calls if a1.startswith(f"{proj_col}2:")]
    formulas = [k["values"][0] for _a1, k in mine if k["condition_type"] == "CUSTOM_FORMULA"]
    assert len(formulas) == 26
    assert formulas[0] == f"=AND(ISNUMBER(${pct_col}2),${pct_col}2>=60,${pct_col}2<65)"
    assert formulas[-2] == f"=AND(ISNUMBER(${pct_col}2),${pct_col}2>=97.5)"
    assert formulas[-1] == f"=AND(ISNUMBER(${pct_col}2),${pct_col}2<=2.5)"
    # Every step spans the WHOLE column (not one rule per position run).
    assert {a1 for a1, k in mine if k["condition_type"] == "CUSTOM_FORMULA"} == {
        f"{proj_col}2:{proj_col}{EDGE_ROWS}"
    }
    # The grey zero chip survives, added last so it wins an exact zero.
    assert mine[-1][1]["condition_type"] == "NUMBER_EQ" and mine[-1][1]["values"] == ["0"]


def test_polish_edge_move_scales_are_diverging_at_zero():
    client = FakeEdgeClient()
    polish_edge(client, "EdgeRaw")

    diverging = [
        kwargs
        for _rng, kwargs in client.color_scale_calls
        if kwargs.get("mid_type") == "NUMBER" and kwargs.get("mid_value") == "0"
    ]
    # ImpliedMove, TotMove, SpdMove and Leverage (CeilPct - OwnPct, centred on 0). Spread is
    # a monotonic "lower is better" reading, so it is reversed, not diverging-at-zero.
    assert len(diverging) == 4


def test_polish_edge_own_pct_midpoint_is_the_chalk_threshold_not_the_median():
    # Week 3 feedback (A2), restored 2026-10-02 after Sam found ownership "really flat in terms of
    # colour spread": ownership is right-skewed, so a median midpoint (~5%) crushes the real spread
    # into one pale amber. Anchoring it at CHALK_OWNERSHIP_THRESHOLD (the same line `Flag`'s CHALK
    # token uses) gives the white-to-amber half real meaning.
    from dfs.derived import CHALK_OWNERSHIP_THRESHOLD

    client = FakeEdgeClient()
    polish_edge(client, "EdgeRaw")

    own_pct = next(kwargs for _rng, kwargs in client.color_scale_calls if kwargs["mid_color"] == WARM_MID)
    assert own_pct["mid_type"] == "NUMBER"
    assert own_pct["mid_value"] == str(CHALK_OWNERSHIP_THRESHOLD)


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
        if kwargs["condition_type"] == "CUSTOM_FORMULA"
        and kwargs["values"][0].startswith(f"=AND(${POOL_COLUMN}2")
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

    def clear_conditional_formats_for(self, tab_name, targets) -> None:
        for column, _row_range in targets:
            self.clear_conditional_formats(tab_name, column=column)

    def add_boolean_rule(
        self, tab_name: str, a1_range: str, *, condition_type: str, values, fmt: dict
    ) -> None:
        self.boolean_rule_calls.append((a1_range, condition_type, values, fmt))

    def format_range(self, tab_name: str, a1_range: str, fmt: dict) -> None:
        self.format_calls.append((a1_range, fmt))

    def clear_data_validation(self, tab_name: str, a1_range: str) -> None:
        self.clear_validation_calls.append(a1_range)


class FakeBoardClient:
    def __init__(self, *, present: bool = True):
        self._present = present
        self.notes: list[tuple[str, str, str]] = []
        self.row_group_calls: list[tuple[int, int, bool]] = []
        self.cleared_row_groups = False
        self.unhidden_rows: list[tuple[int, int]] = []
        self.format_calls: list[tuple[str, dict]] = []
        self.freeze_calls: list[int] = []
        self.width_calls: list[dict] = []
        self.color_scale_calls: list[str] = []
        self.boolean_rule_calls: list[str] = []
        self.hide_columns_calls: list[tuple[str, str]] = []

    def tab_exists(self, tab_name: str) -> bool:
        return self._present

    def read_range(self, tab_name: str, a1_range: str):
        """Header rows only: Slate Grid's game header (row 1) or its TEAMS header (any later row)."""
        from dfs.sheet_views import SLATE_HEADER, SLATE_TEAMS_COLHEADER

        row = int(a1_range.split(":")[0].lstrip("A"))
        return [list(SLATE_HEADER if row == 1 else SLATE_TEAMS_COLHEADER)]

    def set_note(self, tab_name: str, cell_a1: str, note: str) -> None:
        self.notes.append((tab_name, cell_a1, note))

    def hide_columns(self, tab_name: str, first_col: str, last_col: str, *, hidden: bool = True) -> None:
        self.hide_columns_calls.append((first_col, last_col))

    def clear_conditional_formats(self, tab_name: str) -> None:
        pass

    def clear_row_groups(self, tab_name: str) -> None:
        self.cleared_row_groups = True

    def unhide_rows(self, tab_name: str, first_row: int, last_row: int) -> None:
        self.unhidden_rows.append((first_row, last_row))

    def group_rows(self, tab_name: str, first_row: int, last_row: int, *, collapsed: bool = False) -> None:
        self.row_group_calls.append((first_row, last_row, collapsed))

    def set_column_widths(self, tab_name: str, widths: dict[str, int]) -> None:
        self.width_calls.append(widths)

    def set_row_heights(self, tab_name: str, *, start_row: int, end_row: int, pixel_size: int) -> None:
        self.row_height_calls = getattr(self, "row_height_calls", [])
        self.row_height_calls.append((start_row, end_row, pixel_size))

    def format_range(self, tab_name: str, a1_range: str, fmt: dict) -> None:
        self.format_calls.append((a1_range, fmt))

    def add_color_scale(self, tab_name: str, a1_range: str, **kwargs) -> None:
        self.color_scale_calls.append(a1_range)

    def add_boolean_rule(self, tab_name: str, a1_range: str, **kwargs) -> None:
        self.boolean_rule_calls.append(a1_range)

    def freeze(self, tab_name: str, *, rows: int) -> None:
        self.freeze_calls.append(rows)


def test_style_board_skips_cleanly_when_tab_absent():
    client = FakeBoardClient(present=False)
    result = style_board(client)
    assert "not present" in result
    assert client.row_group_calls == []


def test_style_board_clears_existing_row_groups_before_regrouping():
    # Same reason clear_column_groups exists elsewhere -- re-running
    # group_rows over an already-grouped range nests a deeper group
    # instead of replacing it.
    client = FakeBoardClient()
    style_board(client)
    assert client.cleared_row_groups is True


def test_style_board_leaves_every_section_expanded():
    # Sam, 2026-09-30: all Board categories open by default.
    client = FakeBoardClient()
    style_board(client)
    grouped_ranges = {(first, last): collapsed for first, last, collapsed in client.row_group_calls}
    assert grouped_ranges[(BOARD_QUEUE_COLHEADER_ROW, BOARD_QUEUE_LAST_ROW)] is False
    assert grouped_ranges[(BOARD_SLATE_COLHEADER_ROW, BOARD_SLATE_LAST_ROW)] is False
    assert client.row_group_calls, "sections must still be grouped so they can be collapsed"
    assert not any(collapsed for _, _, collapsed in client.row_group_calls)


def test_style_board_groups_leaders_from_the_sublabel_row_and_the_chalk_placeholder():
    client = FakeBoardClient()
    style_board(client)
    grouped_ranges = {(first, last): collapsed for first, last, collapsed in client.row_group_calls}
    # PROMPT_BOARD_FIXES.md item 2: the group starts at the sub-label row (right after the
    # section header), not the column header row, so collapsing hides the sub-label too.
    assert (BOARD_LEADERS_SUBLABEL_ROW, BOARD_LEADERS_LAST_ROW) in grouped_ranges
    # Chalk map's one placeholder row is grouped too, though it has no column-header row.
    assert any(first == BOARD_CHALK_HEADER_ROW + 1 for first, _, _ in client.row_group_calls)


def test_style_board_unhides_rows_a_collapsed_group_left_hidden():
    # Deleting a collapsed row group leaves its rows hidden, so "expanded by default" needs an
    # explicit unhide over the whole Board, not just collapsed=False on the new groups.
    client = FakeBoardClient()
    style_board(client)
    assert client.unhidden_rows and client.unhidden_rows[0][0] == 1
    assert client.unhidden_rows[0][1] >= BOARD_LAST_ROW


def test_style_board_resets_stale_alignment_and_number_format_first():
    # Week 4, 2026-09-30: a few Leaders rows kept an explicit LEFT alignment (and spacer columns
    # a "$" format) from an earlier layout. The whole-tab reset must unset both (None), not just
    # repaint fill and font.
    client = FakeBoardClient()
    style_board(client)
    reset = client.format_calls[0][1]
    assert reset["horizontalAlignment"] is None
    assert reset["numberFormat"] is None


def test_style_board_freezes_only_the_title_and_summary_banner():
    # Rebuilt design: the old subheader-row freeze (rows=6) no longer
    # applies -- Queue's own header/column-header are part of the
    # collapsible content now, not something that needs to stay pinned.
    client = FakeBoardClient()
    style_board(client)
    assert client.freeze_calls == [3]


def test_style_board_hides_the_slate_shape_gameid_join_key():
    # PROMPT_BOARD_FIXES.md item 5: GameId/Away/Home sit past Stack candidates' own
    # 14-column width (the widest section), derived from BOARD_MAX_VISIBLE_COL_INDEX so
    # this can't collide with a real column belonging to a different section.
    client = FakeBoardClient()
    style_board(client)
    # Three join keys (GameId/Away/Home), item 5c's GPS-check helper, and item 3's
    # ProjPts-percentile helper -- one contiguous hidden run.
    assert (column_letter(BOARD_SLATE_GAMEID_COL_INDEX), column_letter(BOARD_LEADERS_PCT_COL_INDEX)) in (
        client.hide_columns_calls
    )


def test_style_board_resets_background_before_applying_new_formatting():
    # Found live (2026-09-23, visually opening the actual sheet): the
    # pre-rebuild 3-panel Board painted a plain (non-conditional) grey
    # fill on spacer columns E/J, which clear_conditional_formats can't
    # touch (it only clears conditional-format RULES) -- so the old
    # bands were still visible after this rebuild shipped. The very
    # first format_range call must reset to white, before any
    # section-specific formatting is applied.
    client = FakeBoardClient()
    style_board(client)
    first_range, first_fmt = client.format_calls[0]
    assert first_fmt["backgroundColor"] == WHITE
    assert first_range.startswith("A1:N")


def test_style_board_reset_also_clears_stale_white_text_so_player_rows_never_vanish():
    # Round 5 item 5a, found live: an old layout's dark header rows left white bold text
    # behind, and real player rows that later landed on them rendered white-on-white.
    client = FakeBoardClient()
    style_board(client)
    _, first_fmt = client.format_calls[0]
    text = first_fmt["textFormat"]
    assert text["foregroundColor"] != WHITE and text["bold"] is False


def test_style_board_steps_leaders_and_punt_with_one_rule_set_per_column():
    # ValAdj steps on its own 0-100 value and ProjPts on a hidden percentile lookup: one set of
    # formula rules over the whole block, so a leader looks like the same player on EdgeRaw.
    client = FakeBoardClient()
    style_board(client)

    leaders_last = BOARD_LEADERS_FIRST_ROW + sum(BOARD_ROWS_PER_POSITION.values()) - 1
    d_ranges = [a1 for a1 in client.boolean_rule_calls if a1.startswith("D") and ":" in a1]
    assert f"D{BOARD_LEADERS_FIRST_ROW}:D{leaders_last}" in d_ranges
    i_ranges = [a1 for a1 in client.boolean_rule_calls if a1.startswith("I")]
    assert set(i_ranges) == {f"I{BOARD_LEADERS_FIRST_ROW}:I{leaders_last}"}
    # the leaders/punt player metrics are steps, never a gradient (the slate-shape and stack
    # sections' game metrics are the gradients)
    assert not [a1 for a1 in client.color_scale_calls if a1.startswith(("D4", "D5", "I4", "I5"))]


def test_style_board_puts_a_top_border_between_positions_not_before_the_first():
    # PROMPT_BOARD_FIXES.md item 3: "a thin visual break between
    # positions... a top border on each position's first row" -- QB (the
    # first position, right under the column header) doesn't need one.
    client = FakeBoardClient()
    style_board(client)

    border_ranges = [a1 for a1, fmt in client.format_calls if "borders" in fmt]
    # 4 borders per block (RB/WR/TE/DST, not QB) x 2 Leaders blocks + 1
    # Punt block = 12.
    assert len(border_ranges) == 12
    # The very first Leaders row (QB's own first row) must never get one.
    assert not any(a1.startswith(f"A{BOARD_LEADERS_FIRST_ROW}:") for a1 in border_ranges)


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
    # Fix 2.4 / Phase 3, reworked A8 (2026-09-22). Uses the same fixture/
    # fake as polish_guardrails below -- Team=C, DK Sal=D, O/U=E,
    # Spread=F, Team Implied=G, Opp.=H, Venue=I, OppPosRank=J, Pts=K,
    # Ceil=L, Val=M, Own%=N, and the linked columns scattered at
    # Q,R,S,T,U,V,W,X,Y,Z.
    client = FakeGuardrailsClient(_HEADER_WITH_AVAIL_AT_Y)

    result = polish_lineups_totals_rows(
        client, "Lineups", header_row=8, name_blocks=[(9, 17)], salary_cap=50000
    )

    calls = {a1: rows for a1, rows in client.update_calls}
    totals_row = 18  # end + 1
    remaining_row = 19  # totals_row + 1 -- A8's new "Remaining" row

    # Dead VLOOKUP columns cleared on the totals row -- native lookups
    # (O/U, Spread, Team Implied, OppPosRank) alongside the linked block,
    # PLUS Team/Venue/Val now that Remaining moved off Venue/Val (A8) --
    # all three are genuinely dead on the totals row now.
    for letter in ("C", "E", "F", "G", "I", "J", "M", "Q", "R", "S", "T", "U", "V", "W", "X", "Y", "Z"):
        assert calls[f"{letter}{totals_row}"] == [[""]]

    # Ceil, Pts and Rstr% all (re)summed unconditionally -- Phase 6, Part 1:
    # Pts/Rstr% turned out NOT to already hold real sums on most live
    # blocks (found live: a stale VLOOKUP-against-blank sitting there
    # instead), so both are now self-healed the same way Ceil already was.
    assert calls[f"L{totals_row}"] == [["=SUM(L9:L17)"]]
    assert calls[f"K{totals_row}"] == [["=SUM(K9:K17)"]]
    assert calls[f"N{totals_row}"] == [["=SUM(N9:N17)"]]

    # "Total" at Opp.'s column (H), same row as the Salary sum (D18).
    assert calls[f"H{totals_row}"] == [["Total"]]

    # A8: "Remaining" -- value AND label -- sits one row below, in the
    # SAME two columns as Total's own value/label (D/H), not off to the
    # side. Average remaining per unfilled slot rides the same new row,
    # in Pts' column (otherwise dead there), guarded against a full
    # lineup's divide-by-zero.
    assert calls[f"D{remaining_row}"] == [["=50000-D18"]]
    assert calls[f"H{remaining_row}"] == [["Remaining"]]
    assert calls[f"K{remaining_row}"] == [['=IF(COUNTBLANK($A$9:$A$17)=0,"",D19/COUNTBLANK($A$9:$A$17))']]

    assert "1 totals row(s)" in result
    assert "3 sum(s) written" in result


def test_polish_lineups_totals_rows_clears_the_totals_row_name_cells_typo_guard():
    # The totals row's Name cell (column A) still carried the same input
    # background and typo-guard player dropdown as a real roster slot --
    # a leftover from Fix 2.4 shrinking each block's own range to exclude
    # the totals row, never retroactively cleaned up off the row it
    # stopped covering. Found live, from a screenshot.
    client = FakeGuardrailsClient(_HEADER_WITH_AVAIL_AT_Y)

    result = polish_lineups_totals_rows(
        client, "Lineups", header_row=8, name_blocks=[(9, 17)], salary_cap=50000
    )

    assert client.clear_validation_calls == ["A18"]
    # Fix 6.3 also formats the average-remaining cell (K19) as currency in
    # this same call -- assert containment, not exact equality, since
    # that's a separate concern from this test's own typo-guard check.
    assert ("A18", {"backgroundColor": WHITE}) in client.format_calls
    assert "1 Name cell(s) un-typo-guarded" in result


def test_polish_lineups_totals_rows_formats_average_remaining_as_currency():
    # Fix 6.3 (Week 3 fixes, 2026-09-23): the average-remaining-per-slot
    # cell lives in Pts' column (K here), whose own column-wide format is
    # "0.0" (points) -- rendering a real dollar figure as a raw "5555.6"
    # instead of matching Remaining's own "$5,556" one row up. Formatted
    # directly, per-cell, to match DK Sal's own currency format.
    client = FakeGuardrailsClient(_HEADER_WITH_AVAIL_AT_Y)

    polish_lineups_totals_rows(client, "Lineups", header_row=8, name_blocks=[(9, 17)], salary_cap=50000)

    assert ("K19", FIELD_FORMATS["DK Sal"]) in client.format_calls


def test_polish_lineups_totals_rows_never_touches_salary_or_issues():
    # D (Salary) already holds a real SUM formula this function has never
    # needed to touch; O (Issues) holds the real guardrail formula --
    # neither is a dead VLOOKUP and neither should be cleared or
    # overwritten here. Pts (K) and Rstr% (N) are NOT in this list any
    # more -- Phase 6, Part 1 found live that they don't reliably already
    # hold a real sum, so both are now unconditionally rewritten (see
    # test_polish_lineups_totals_rows_clears_dead_vlookups_sums_ceil_and_labels).
    client = FakeGuardrailsClient(_HEADER_WITH_AVAIL_AT_Y)

    polish_lineups_totals_rows(client, "Lineups", header_row=8, name_blocks=[(9, 17)], salary_cap=50000)

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

    result = polish_lineups_totals_rows(
        client, "Lineups", header_row=8, name_blocks=[(9, 17)], salary_cap=50000
    )

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
    result = polish_lineups_totals_rows(
        client, "Lineups", header_row=8, name_blocks=[(9, 17)], salary_cap=50000
    )
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


# Part 7.4: adds GameID to _HEADER_FOR_GUARDRAILS so the stack checks have
# everything they need (Pos./Team/Opp. were already present).
_HEADER_FOR_GUARDRAILS_WITH_GAMEID = [*_HEADER_FOR_GUARDRAILS, "GameID"]


class FakeDstLabelClient:
    """Per-cell read_range (unlike FakeGuardrailsClient's fixed-header-
    regardless-of-range fake) -- fix_lineups_dst_slot_label reads each
    block's own last row individually, so the fake needs to answer
    differently per cell."""

    def __init__(self, cell_values: dict[str, str], *, present: bool = True):
        self._cell_values = cell_values
        self._present = present
        self.update_calls: list[tuple[str, list[list]]] = []

    def tab_exists(self, tab_name: str) -> bool:
        return self._present

    def read_range(self, tab_name: str, a1_range: str):
        value = self._cell_values.get(a1_range, "")
        return [[value]] if value else [[]]

    def update_range(self, tab_name: str, a1_range: str, rows: list[list]) -> None:
        self.update_calls.append((a1_range, rows))
        self._cell_values[a1_range] = rows[0][0]


def test_fix_lineups_dst_slot_label_corrects_every_stale_def_and_leaves_dst_alone():
    from dfs.sheet_style import fix_lineups_dst_slot_label

    # Block 1 (ends row 10) still says the stale "DEF"; block 2 (ends row
    # 23) was already fixed (or never wrong) and already says "DST".
    client = FakeDstLabelClient({"B10": "DEF", "B23": "DST"})

    result = fix_lineups_dst_slot_label(client, "Lineups", position_col="B", name_blocks=[(2, 10), (13, 23)])

    assert client.update_calls == [("B10", [["DST"]])]
    assert client._cell_values["B23"] == "DST"  # untouched, no update_range call for it
    assert "1 'DEF' -> 'DST' fix(es), 1 already correct" in result


def test_fix_lineups_dst_slot_label_never_overwrites_an_unexpected_value():
    from dfs.sheet_style import fix_lineups_dst_slot_label

    # A block whose last row says neither DEF nor DST (a malformed/
    # unexpected sheet state) must be reported, not silently clobbered.
    client = FakeDstLabelClient({"B10": "FLEX"})

    result = fix_lineups_dst_slot_label(client, "Lineups", position_col="B", name_blocks=[(2, 10)])

    assert client.update_calls == []
    assert "unexpected value(s)" in result
    assert "(10, 'FLEX')" in result


def test_fix_lineups_dst_slot_label_skips_when_tab_absent():
    from dfs.sheet_style import fix_lineups_dst_slot_label

    client = FakeDstLabelClient({}, present=False)
    result = fix_lineups_dst_slot_label(client, "Lineups", position_col="B", name_blocks=[(2, 10)])
    assert result == "Lineups: not present -- skipped"
    assert client.update_calls == []


def test_stack_check_formula_flags_dst_against_the_lineups_own_qb():
    from dfs.sheet_style import _stack_check_formula

    formula = _stack_check_formula(9, 17, position_col="B", team_col="C", opp_col="H", gameid_col="AA")
    qb_team = 'IFERROR(INDEX($C$9:$C$17,MATCH("QB",$B$9:$B$17,0)),"")'
    dst_opp = 'IFERROR(INDEX($H$9:$H$17,MATCH("DST",$B$9:$B$17,0)),"")'
    assert f'IF(AND({qb_team}<>"",{dst_opp}<>"",{qb_team}={dst_opp}),"DST/QB","")' in formula


def test_stack_check_formula_flags_more_than_one_rb_sharing_a_gameid():
    # Part B (2026-09-22): rebuilt as a TRANSPOSE pairwise-matrix
    # comparison, not COUNTIFS(range,range) -- COUNTIFS can't take a
    # computed array (resolved_pos, below), only a real cell range. See
    # _stack_check_formula's own docstring for the full derivation,
    # confirmed empirically against Scratch.
    from dfs.sheet_style import _stack_check_formula

    formula = _stack_check_formula(9, 17, position_col="B", team_col="C", opp_col="H", gameid_col="AA")
    resolved_pos = (
        'IF($B$9:$B$17="FLEX",IFERROR(VLOOKUP($A$9:$A$17,PlayerPoolRaw!$A:$B,2,FALSE),""),$B$9:$B$17)'
    )
    is_rb = f'({resolved_pos}="RB")'
    pair_matches = (
        f'SUMPRODUCT(($AA$9:$AA$17=TRANSPOSE($AA$9:$AA$17))*($AA$9:$AA$17<>"")*{is_rb}*TRANSPOSE({is_rb}*1))'
    )
    self_matches = f'SUMPRODUCT({is_rb}*($AA$9:$AA$17<>"")*1)'
    assert f'IF({pair_matches}-{self_matches}>0,"RB/GAME","")' in formula


def test_stack_check_formula_resolves_flex_to_the_players_real_position():
    # Part B (2026-09-22), found live: "Pos." is a FIXED per-slot label
    # ("FLEX", never "RB"), so an RB rostered in the FLEX slot was
    # invisible to the old Position="RB" check -- the exact same
    # static-label-vs-real-data confusion this codebase has hit before
    # (RB/GAME's formula-blank GameID, the DST/QB slot-label bug). QB/DST
    # never need this treatment -- neither can legally sit in FLEX.
    from dfs.sheet_style import _stack_check_formula

    formula = _stack_check_formula(9, 17, position_col="B", team_col="C", opp_col="H", gameid_col="AA")
    assert 'IF($B$9:$B$17="FLEX"' in formula
    assert "VLOOKUP($A$9:$A$17,PlayerPoolRaw!$A:$B,2,FALSE)" in formula


def test_stack_check_formula_raw_tab_is_overridable():
    from dfs.sheet_style import _stack_check_formula

    formula = _stack_check_formula(
        9, 17, position_col="B", team_col="C", opp_col="H", gameid_col="AA", raw_tab="TemplateRaw"
    )
    assert "TemplateRaw!$A:$B" in formula


def test_stack_check_formula_rb_per_game_excludes_formula_blank_gameid():
    """Found live (2026-09-18): Lineups' GameID is always a FORMULA cell
    (blank when its slot has no name), and a formula-produced "" DOES
    self-match another formula-produced "" inside COUNTIFS -- unlike two
    genuinely-blank (never-typed) cells, which don't. Without the
    `<>""` term, two unfilled RB slots (the normal state of an
    incomplete lineup) would always false-positive as RB/GAME."""
    from dfs.sheet_style import _stack_check_formula

    formula = _stack_check_formula(9, 17, position_col="B", team_col="C", opp_col="H", gameid_col="AA")
    assert '($AA$9:$AA$17<>"")' in formula


def test_totals_check_formula_replaces_ok_with_the_real_stack_violation():
    from dfs.sheet_style import _totals_check_formula

    base = _totals_check_formula(9, 17, 18, "K")[1:]  # strip leading "="
    formula = _totals_check_formula(9, 17, 18, "K", stack_check='"DST/QB"')
    assert formula == (
        f'=IF(({base})="","",IF("DST/QB"="",({base}),IF(({base})="OK","DST/QB",({base})&" "&"DST/QB")))'
    )


def test_totals_check_formula_appends_stack_check_without_masking_an_over_or_incomplete():
    from dfs.sheet_style import _totals_check_formula

    base = _totals_check_formula(9, 17, 18, "K")[1:]  # strip leading "="
    formula = _totals_check_formula(9, 17, 18, "K", stack_check='"RB/GAME"')
    # The exact prior cap/completeness formula is embedded verbatim, not
    # rewritten -- Part 7.4 is additive on top of it, and the "&" concat
    # branch (an OVER/INCOMPLETE result, not "OK") is what appends rather
    # than replaces.
    assert base in formula
    assert f'({base})&" "&"RB/GAME"' in formula


def test_totals_check_formula_none_stack_check_reproduces_prior_behaviour_exactly():
    from dfs.sheet_style import _totals_check_formula

    assert _totals_check_formula(9, 17, 18, "K", stack_check=None) == _totals_check_formula(9, 17, 18, "K")


def test_polish_guardrails_adds_stack_checks_when_position_team_opp_gameid_all_linked():
    client = FakeGuardrailsClient(_HEADER_FOR_GUARDRAILS_WITH_GAMEID)
    guardrails_col = column_letter(_HEADER_FOR_GUARDRAILS_WITH_GAMEID.index("Issues"))

    polish_guardrails(client, "Lineups", header_row=8, name_blocks=[(9, 17)])

    block_call = next(c for c in client.update_calls if c[0] == f"{guardrails_col}9:{guardrails_col}18")
    totals_formula = block_call[1][-1][0]
    assert "DST/QB" in totals_formula
    assert "RB/GAME" in totals_formula


def test_polish_guardrails_omits_stack_checks_when_gameid_not_yet_linked():
    # _HEADER_FOR_GUARDRAILS (no GameID) -- exact prior behaviour, no
    # stack tokens anywhere in the totals formula.
    client = FakeGuardrailsClient(_HEADER_FOR_GUARDRAILS)
    guardrails_col = column_letter(_HEADER_FOR_GUARDRAILS.index("Issues"))

    polish_guardrails(client, "Lineups", header_row=8, name_blocks=[(9, 17)])

    block_call = next(c for c in client.update_calls if c[0] == f"{guardrails_col}9:{guardrails_col}18")
    totals_formula = block_call[1][-1][0]
    assert "DST/QB" not in totals_formula
    assert "RB/GAME" not in totals_formula


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
        self.multi_range_calls: list[dict] = []
        self.hide_calls: list[tuple[str, str, bool]] = []
        self._grouped_column_indices = grouped_column_indices or set()
        self.notes: list[tuple[str, str]] = []

    def set_note(self, tab_name: str, cell: str, text: str) -> None:
        self.notes.append((cell, text))

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

    def clear_conditional_formats_for(self, tab_name, targets) -> None:
        for column, row_range in targets:
            self.clear_conditional_formats(tab_name, column=column, row_range=row_range)

    def clear_banding(self, tab_name: str) -> None:
        pass

    def add_row_banding(self, tab_name: str, a1_range: str, **kwargs) -> None:
        self.banding_calls.append((a1_range, kwargs))

    def add_color_scale(self, tab_name: str, a1_range: str, **kwargs) -> None:
        self.color_scale_calls.append((a1_range, kwargs))

    def add_color_scales_multi_range(self, tab_name: str, specs: list[dict]) -> None:
        self.multi_range_calls.extend(specs)

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


def test_apply_grouped_color_scales_writes_one_rule_set_per_column_over_all_the_blocks():
    # One set of rules per column across every block (not one per (column, block): that made the
    # same number a different colour on each tab and put hundreds of rules on Lineups). Player
    # metrics are steps off the hidden percentile column over the blocks' whole span; a column
    # with no position is ONE multi-range gradient. Header/totals rows between blocks are outside
    # the gradient and match no step.
    client = FakeBuilderTabClient(["Name", "Pts", "Val", "Total", "ProjPts%ile", "Val%ile"])
    applied = apply_grouped_color_scales(client, "Player Pool", client._header, [(3, 12), (14, 33)])

    assert applied == 3  # Pts, Val, Total
    assert client.color_scale_calls == []
    # Total (no position) is the only gradient: one rule over both blocks
    assert [spec["a1_ranges"] for spec in client.multi_range_calls] == [["D3:D12", "D14:D33"]]
    steps = [(a1, k) for a1, k in client.boolean_rule_calls if k["condition_type"] == "CUSTOM_FORMULA"]
    assert {a1 for a1, _ in steps} == {"B3:B33", "C3:C33"}  # first block start .. last block end
    assert len([1 for a1, _ in steps if a1 == "B3:B33"]) == 26
    # each metric reads ITS OWN helper column, by name
    assert all("$E3" in k["values"][0] for a1, k in steps if a1 == "B3:B33")  # ProjPts%ile
    assert all("$F3" in k["values"][0] for a1, k in steps if a1 == "C3:C33")  # Val%ile
    chips = {a1 for a1, k in client.boolean_rule_calls if k["condition_type"] == "NUMBER_EQ"}
    assert chips == {"B3:B33", "C3:C33", "D3:D33"}


def test_apply_grouped_color_scales_no_longer_skips_ceilpct_or_valadj():
    client = FakeBuilderTabClient(["Name", "Pts", "CeilPct", "ProjPts%ile"])
    applied = apply_grouped_color_scales(
        client, "Player Pool", client._header, [(3, 12)], skip=GROUPED_TAB_UNSCALED_COLUMNS
    )
    assert applied == 2
    assert GROUPED_TAB_UNSCALED_COLUMNS == frozenset()


def test_apply_grouped_color_scales_keeps_the_grey_zero_chip_and_the_warm_scale_for_ownership():
    # Own% keeps its warm colours, anchored over every block's non-zero values; exact zeros
    # keep the grey chip.
    assert "Own%" in ZERO_EXCLUDED_COLUMNS
    client = FakeBuilderTabClient(["Name", "Own%"])
    apply_grouped_color_scales(client, "Player Pool", client._header, [(3, 12), (14, 33)])

    (spec,) = client.multi_range_calls
    assert spec["a1_ranges"] == ["B3:B12", "B14:B33"]
    assert spec["mid_color"] == WARM_MID
    assert spec["mid_value"] == "0.2"  # the CHALK line, not a median
    assert spec["min_value"].count("MINIFS(") == 2  # both blocks feed the one non-zero minimum
    assert {a1 for a1, _ in client.boolean_rule_calls} == {"B3:B33"}


def test_apply_grouped_color_scales_honors_skip_argument():
    client = FakeBuilderTabClient(["Name", "Pts", "Leverage", "ProjPts%ile"])
    applied = apply_grouped_color_scales(
        client, "Player Pool", client._header, [(3, 12)], skip=frozenset({"Leverage"})
    )
    assert applied == 1
    assert {a1 for a1, _ in client.boolean_rule_calls} == {"B3:B12"}
    assert client.multi_range_calls == []  # Leverage (the only gradient column) was skipped


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

    # The header row and the data rows (alignment) -- and nothing else: no repeat-header rows.
    assert {a1 for a1, _fmt in client.format_calls} == {"A1:C1", "A2:C100"}


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


def test_polish_builder_tab_can_leave_the_pos_column_untinted():
    # Week 5: Lineups' Pos. is plain white so the correlation tints read; every other tab keeps its tint.
    client = FakeBuilderTabClient(["Name", "Pos.", "Team"])
    polish_builder_tab(client, "Lineups", last_row=100, header_row=1, position_tint=False)
    assert client.boolean_rule_calls == []

    client = FakeBuilderTabClient(["Name", "Pos.", "Team"])
    polish_builder_tab(client, "Player Pool", last_row=100, header_row=1)
    assert len(client.boolean_rule_calls) == len(POSITION_TINTS)


def test_lineups_identity_cells_are_plain_white_in_every_block_and_nothing_else():
    client = FakeBuilderTabClient(["Name", "Pos.", "Team", "Opp.", "DK Sal"])
    result = polish_lineups_identity_cells(client, "Lineups", header_row=1, name_blocks=[(2, 10), (13, 21)])
    assert {a1 for a1, _ in client.format_calls} == {
        "A2:A10", "B2:B10", "C2:C10", "A13:A21", "B13:B21", "C13:C21",
    }  # fmt: skip
    assert all(fmt == {"backgroundColor": WHITE} for _, fmt in client.format_calls)  # no yellow, no tint
    assert "2 lineup block(s)" in result


def test_lineups_identity_cells_found_by_header_text_not_position():
    client = FakeBuilderTabClient(["Pos.", "Name", "Team"])
    polish_lineups_identity_cells(client, "Lineups", header_row=1, name_blocks=[(2, 10)])
    assert {a1 for a1, _ in client.format_calls} == {"A2:A10", "B2:B10", "C2:C10"}
    client = FakeBuilderTabClient(["Name", "Pts"])  # Pos./Team missing: only what exists
    polish_lineups_identity_cells(client, "Lineups", header_row=1, name_blocks=[(2, 10)])
    assert {a1 for a1, _ in client.format_calls} == {"A2:A10"}


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


def test_polish_builder_tab_shrinks_and_mutes_the_edge_link_when_present():
    # Week 3 feedback (A5): a quiet affordance, not a call to action --
    # smaller, muted text; the column WIDTH is BUILDER_WIDTHS' job, this
    # is only about the cell's own text formatting.
    from dfs.sheet_style import INK_MUTED

    client = FakeBuilderTabClient(["Name", "Edge ↗"])
    polish_builder_tab(client, "Player Pool", last_row=100, header_row=1)

    edge_link_fmt = next(fmt for rng, fmt in client.format_calls if rng == "B2:B100" and "textFormat" in fmt)
    assert edge_link_fmt["textFormat"]["foregroundColor"] == INK_MUTED
    assert edge_link_fmt["textFormat"]["fontSize"] < 10


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


def test_polish_builder_tab_highlights_every_matching_column():
    client = FakeBuilderTabClient(["Name", "Pts", "Leverage", "OppPosRank", "ProjPts%ile"])
    result = polish_builder_tab(client, "Player Pool", last_row=100, header_row=1)

    # Pts: steps off the helper; Leverage/OppPosRank: one gradient each
    steps = {a1 for a1, k in client.boolean_rule_calls if k["condition_type"] == "CUSTOM_FORMULA"}
    assert steps == {"B2:B100"}
    assert {a1 for a1, _k in client.color_scale_calls} == {"C2:C100", "D2:D100"}
    assert "3 highlighted column(s)" in result or "3 colour scale(s)" in result


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


def test_builder_widths_covers_every_base_column_order_name():
    # Part 7.4 (2026-09-18): "O/U" had no entry at all -- `polish_builder_
    # tab` only sets a width for a name it finds in this dict (see its own
    # width loop), so an absent entry means "whatever a past reorder
    # happened to leave the physical column at," never actively managed.
    # Caught live: Part 7.4's own GameID/TmRank insert shifted "O/U" onto
    # a too-narrow physical column on Player Pool. "Pts" had the identical
    # gap, fixed alongside it pre-emptively. `Flag` is the one deliberate
    # exception -- hidden outright (`sheet_columns.INTERNAL`) on every tab
    # this dict serves, so its width can never be visible.
    from dfs.sheet_columns import BASE_COLUMN_ORDER
    from dfs.sheet_style import BUILDER_WIDTHS

    missing = {name for name in BASE_COLUMN_ORDER if name not in BUILDER_WIDTHS} - {"Flag"}
    assert missing == set()


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


def test_style_exposure_sets_widths_for_the_portfolio_headline_columns():
    # Part 7.5: K1:P1 (Distinct QBs/Shared QB?/Distinct games) had no
    # width entry at all when first added -- same "no entry means
    # Sheets' own 100px default" gap Part 7.4 already hit for O/U/Pts --
    # caught live: "Distinct games" (14 characters) truncated at 100px.
    from dfs.sheet_style import style_exposure

    client = FakeBuilderTabClient(["Name"])
    style_exposure(client, "Exposure")

    widths = client.width_calls[0]
    for col in ("K", "L", "M", "N", "O", "P"):
        assert col in widths
        assert widths[col] != 100


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
        dk_upload_last_row=200,
        results_last_row=30,
        sos_comb_last_row=40,
    )
    assert len(results) == 8  # DK Upload, Results, 5xSoS, SoSComb


def test_highlight_rules_follow_header_order_so_two_runs_are_identical():
    """Rule order must not depend on the process hash seed (a frozenset used to be
    iterated here), or two identical polish runs would add rules in a different order."""
    client = FakeBuilderTabClient(
        ["Name", "CeilVal", "Ceil", "Pts", "Val", "CeilVal%ile", "Ceiling%ile", "ProjPts%ile", "Val%ile"]
    )  # deliberately not alphabetical
    apply_field_color_scales(client, "Player Pool", client._header, header_row=1, last_row=10)
    columns = list(
        dict.fromkeys(
            a1.split(":")[0][0]
            for a1, k in client.boolean_rule_calls
            if k["condition_type"] == "CUSTOM_FORMULA"
        )
    )
    assert columns == [column_letter(i) for i in range(1, 5)]  # CeilVal, Ceil, Pts, Val -- header order


def test_style_board_unhides_the_visible_range_before_hiding_the_helper_block():
    """Found live: columns hidden by an OLDER Board layout (the old join keys at J:L) stayed
    hidden after the slate shape grew, swallowing Shootout?/GPS."""

    class Recording(FakeBoardClient):
        def __init__(self):
            super().__init__()
            self.hide_events: list[tuple[str, str, bool]] = []

        def hide_columns(self, tab_name, first_col, last_col, *, hidden=True):
            self.hide_events.append((first_col, last_col, hidden))

    client = Recording()
    style_board(client)
    unhide_last = column_letter(BOARD_SLATE_GAMEID_COL_INDEX - 1)
    assert ("A", unhide_last, False) in client.hide_events
    assert client.hide_events.index(("A", unhide_last, False)) < next(
        i for i, e in enumerate(client.hide_events) if e[2] is True
    )


def test_style_slate_grid_dims_games_and_teams_with_no_players_and_hides_both_helpers():
    """Round 5 follow-up item 1: the game stays listed but in muted text, driven by the
    hidden "On DK slate" helper; the TEAMS rows dim off the same helper. Both hidden helpers
    (GPS check, On DK slate) are hidden."""
    from dfs.sheet_views import (
        SLATE_GAME_LAST_ROW,
        SLATE_GPS_CHECK_COL_INDEX,
        SLATE_ON_SLATE_COL,
        SLATE_TEAMS_FIRST_ROW,
        SLATE_TEAMS_LAST_ROW,
    )

    rules = []
    hidden_calls = []

    class _Client(FakeBoardClient):
        def add_boolean_rule(self, tab, a1_range, *, condition_type, values, fmt):
            rules.append((a1_range, condition_type, values, fmt))

        def hide_columns(self, tab, first, last, *, hidden=True):
            hidden_calls.append((first, last))

        def freeze(self, tab, *, rows, cols=None):
            pass

    style_slate_grid(_Client())
    games_dim = [r for r in rules if r[2] == [f"=${SLATE_ON_SLATE_COL}2=FALSE"]]
    teams_dim = [r for r in rules if r[2] == [f"=${SLATE_ON_SLATE_COL}{SLATE_TEAMS_FIRST_ROW}=FALSE"]]
    assert len(games_dim) == 1 and len(teams_dim) == 1
    assert rules[-1] == teams_dim[0]  # added last, so it wins over every other rule's text colour
    for dim in (games_dim[0], teams_dim[0]):
        assert dim[3]["textFormat"]["foregroundColor"] != WHITE
    assert games_dim[0][0] == f"A2:{column_letter(SLATE_GPS_CHECK_COL_INDEX - 1)}{SLATE_GAME_LAST_ROW}"
    assert teams_dim[0][0].endswith(f"{SLATE_TEAMS_LAST_ROW}")
    assert hidden_calls == [(column_letter(SLATE_GPS_CHECK_COL_INDEX), SLATE_ON_SLATE_COL)]


def test_style_slate_grid_colours_game_metrics_and_every_teams_column():
    from dfs.sheet_views import SLATE_COL, SLATE_TEAMS_COLHEADER, SLATE_TEAMS_FIRST_ROW, SLATE_TEAMS_LAST_ROW

    gradients, booleans = [], []

    class _Client(FakeBoardClient):
        def add_color_scale(self, tab, a1_range, **kwargs):
            gradients.append(a1_range)

        def add_boolean_rule(self, tab, a1_range, *, condition_type, values, fmt):
            booleans.append(a1_range)

        def freeze(self, tab, *, rows, cols=None):
            pass

    style_slate_grid(_Client())
    # The four game columns (GameEnv is a stepped score, the other three gradients)...
    assert f"{SLATE_COL['Pace']}2:{SLATE_COL['Pace']}19" in gradients
    assert f"{SLATE_COL['PROE']}2:{SLATE_COL['PROE']}19" in gradients
    assert any(a1.startswith(f"{SLATE_COL['GameEnv']}2:") for a1 in booleans)
    # ...and every metric column of TEAMS, over the team rows only (the games rows above are a
    # different population and get their own rules).
    for i, name in enumerate(SLATE_TEAMS_COLHEADER[2:], start=2):
        letter = column_letter(i)
        rng = f"{letter}{SLATE_TEAMS_FIRST_ROW}:{letter}{SLATE_TEAMS_LAST_ROW}"
        assert rng in gradients, name


def test_style_slate_grid_freezes_no_row_so_the_game_header_cannot_mislabel_the_teams_columns():
    from dfs.sheet_audit import FREEZE_OVERRIDES

    freezes = []

    class _Client(FakeBoardClient):
        def freeze(self, tab, *, rows=None, cols=None):
            freezes.append((rows, cols))

    style_slate_grid(_Client())
    assert freezes == [(0, 1)]  # column A pinned, no row
    assert FREEZE_OVERRIDES["Slate Grid"] == 0  # and audit-style agrees that is intended


def test_style_slate_grid_teams_header_wraps_and_number_formats_do_not_bleed_from_the_game_rows():
    from dfs.sheet_views import SLATE_TEAMS_COLHEADER_ROW, SLATE_TEAMS_FIRST_ROW, SLATE_TEAMS_LAST_ROW

    class _Client(FakeBoardClient):
        def freeze(self, tab, *, rows, cols=None):
            pass

    client = _Client()
    style_slate_grid(client)
    wraps = [a1 for a1, fmt in client.format_calls if fmt.get("wrapStrategy") == "WRAP"]
    assert wraps and wraps[0].startswith(f"A{SLATE_TEAMS_COLHEADER_ROW}:")
    reset = [a1 for a1, fmt in client.format_calls if fmt == {"numberFormat": None}]
    assert reset == [f"A{SLATE_TEAMS_FIRST_ROW}:K{SLATE_TEAMS_LAST_ROW}"]
    assert client.row_height_calls == [(SLATE_TEAMS_COLHEADER_ROW, SLATE_TEAMS_COLHEADER_ROW, 34)]


def test_slate_grid_movement_scales_are_zero_centred_with_symmetric_anchors():
    """Both movement columns must paint a zero white even when no value in the column is
    negative (or positive) -- found live on Week 4: zeros rendered solid red/green."""
    scales = []

    class _Client(FakeBoardClient):
        def add_color_scale(self, tab, a1_range, **kwargs):
            scales.append((a1_range, kwargs))

        def freeze(self, tab, *, rows, cols=None):
            pass

    style_slate_grid(_Client())
    movement = [(rng, kw) for rng, kw in scales if rng[0] in "KL" and kw.get("mid_value") == "0"]
    assert [rng for rng, _ in movement] == ["K2:K19", "L2:L19"]
    for rng, kw in movement:
        col = rng[0]
        assert kw["min_value"] == f"=-MAX(MAX(${col}$2:${col}$19),-MIN(${col}$2:${col}$19))"
        assert kw["max_value"] == f"=MAX(MAX(${col}$2:${col}$19),-MIN(${col}$2:${col}$19))"


def test_week_order_puts_slate_grid_before_board():
    names = [tab for tab, _family in WEEK_ORDER]
    assert names.index("Slate Grid") < names.index("Board")


def test_split_flag_chips_use_the_tffb_tokens():
    from dfs.derived import SPLIT_TFFB_HIGH, SPLIT_TFFB_LOW
    from dfs.sheet_style import FLAG_CHIPS

    assert {SPLIT_TFFB_HIGH, SPLIT_TFFB_LOW} <= set(FLAG_CHIPS)
    assert not any(k.startswith("SPLIT") for k in FLAG_CHIPS)


# ---------------------------------------------------------------------------
# One alignment rule: text left, numbers right, header matches its column
# ---------------------------------------------------------------------------


def test_column_alignment_follows_the_declared_format_not_the_data():
    assert column_alignment("Name") == "LEFT"
    assert column_alignment("Stadium") == "LEFT"
    assert column_alignment("In") == "LEFT"  # text ("L1, L2"), not a number
    assert column_alignment("Issues") == "LEFT"  # text with chips, not in the agreed centred list
    assert column_alignment("DK Sal") == "RIGHT"
    assert column_alignment("Own%") == "RIGHT"
    assert column_alignment("Used") == "RIGHT"  # a count with no FIELD_FORMATS entry
    assert column_alignment("ProjPts%ile") == "RIGHT"  # hidden helper, still a number


def test_every_numeric_field_format_aligns_right_and_every_pill_or_label_column_centres():
    for name, fmt in FIELD_FORMATS.items():
        if name in CENTERED_COLUMNS:
            continue
        assert column_alignment(name) == "RIGHT", name  # every FIELD_FORMATS entry is a number
        assert fmt["numberFormat"]["type"] in {"NUMBER", "CURRENCY", "PERCENT"}
    for name in (
        "Avail",
        "Flags",
        "Venue",
        "Pool",
        "OwnStatus",
        "Edge ↗",
        "GAME",
        "CEIL",
        "MOVE",
        "WX",
        "USAGE",
    ):
        assert column_alignment(name) == "CENTER", name


def _alignment_by_cell(client: FakeBuilderTabClient) -> dict[str, str]:
    """a1 range -> alignment, for the alignment-only calls."""
    return {
        a1: fmt["horizontalAlignment"]
        for a1, fmt in client.format_calls
        if set(fmt) == {"horizontalAlignment"}
    }


def test_polish_builder_tab_aligns_data_and_every_header_row_per_column():
    # Name text, DK Sal/Pts numbers, Flags a centred pill: header repeats included.
    client = FakeBuilderTabClient(["Name", "DK Sal", "Pts", "Flags"])

    polish_builder_tab(client, "Lineups", last_row=100, header_row=1, header_repeats_at=[14, 27])

    got = _alignment_by_cell(client)
    assert got["A2:A100"] == "LEFT"
    assert got["B2:C100"] == "RIGHT"  # neighbouring numeric columns share one request
    assert got["D2:D100"] == "CENTER"
    for row in (1, 14, 27):
        assert got[f"A{row}:A{row}"] == "LEFT"
        assert got[f"B{row}:C{row}"] == "RIGHT"  # header sits over its numbers
        assert got[f"D{row}:D{row}"] == "CENTER"


def test_alignment_touches_only_horizontal_alignment():
    client = FakeBuilderTabClient(["Name", "Pts"])
    polish_builder_tab(client, "Player Pool", last_row=100, header_row=1)
    for a1, fmt in client.format_calls:
        if "horizontalAlignment" in fmt and a1 in {"A2:A100", "B2:B100"}:
            assert set(fmt) == {"horizontalAlignment"}  # never resets a fill/number format with it


class RecordingBankrollClient(FakeBankrollClient):
    def __init__(self):
        super().__init__()
        self.calls: list[tuple[str, dict]] = []

    def format_range(self, tab_name, a1_range, fmt) -> None:
        self.calls.append((a1_range, fmt))


def _bankroll_alignment(client: RecordingBankrollClient) -> list[tuple[str, str]]:
    return [(a1, f["horizontalAlignment"]) for a1, f in client.calls if set(f) == {"horizontalAlignment"}]


def test_polish_bankroll_aligns_each_ledger_header_with_its_columns():
    client = RecordingBankrollClient()

    polish_bankroll(client, "Bankroll", cash=(38, 39, 83), gpp=(85, 86, 149), betting=(16, 17, 36))

    got = _bankroll_alignment(client)
    # Header row included in each range, so the header takes its column's alignment.
    for a1, side in (
        ("A38:A83", "LEFT"),
        ("B38:J83", "RIGHT"),
        ("A85:A149", "LEFT"),
        ("B85:J149", "RIGHT"),
        ("A16:A36", "LEFT"),
        ("B16:F36", "RIGHT"),
    ):
        assert (a1, side) in got, a1


def test_polish_bankroll_kpi_block_is_left_with_numeric_values_right():
    client = RecordingBankrollClient()
    polish_bankroll(client, "Bankroll", cash=(38, 39, 83), gpp=(85, 86, 149))

    got = _bankroll_alignment(client)
    baseline = got.index((f"A1:J{BANKROLL_KPI_LAST_ROW}", "LEFT"))
    for rng in BANKROLL_VALUE_CELLS:
        assert got.index((rng, "RIGHT")) > baseline  # right applied over the left baseline


def test_bankroll_value_cells_cover_every_currency_and_percent_cell_and_the_kpi_row_is_pinned():
    from dfs.sheet_bankroll_view import SUMMARY_ROW

    def cells(rng):  # "B1:B2" / "F1" -> [(col, row)...]
        import re

        m = re.fullmatch(r"([A-Z])(\d+)(?::([A-Z])(\d+))?", rng)
        c1, r1, c2, r2 = m.group(1), int(m.group(2)), m.group(3) or m.group(1), int(m.group(4) or m.group(2))
        return {(c, r) for c in map(chr, range(ord(c1), ord(c2) + 1)) for r in range(r1, r2 + 1)}

    values = set().union(*(cells(r) for r in BANKROLL_VALUE_CELLS))
    formatted = set().union(*(cells(r) for r in BANKROLL_CURRENCY_CELLS + BANKROLL_PERCENT_CELLS))
    assert formatted <= values
    assert BANKROLL_KPI_LAST_ROW == SUMMARY_ROW  # the Betting summary row closes the KPI block
    assert max(r for _c, r in values) <= BANKROLL_KPI_LAST_ROW


def test_slate_grid_header_notes_land_on_the_game_row_and_the_teams_row():
    from dfs.sheet_views import SLATE_COL, SLATE_TEAMS_COLHEADER, SLATE_TEAMS_COLHEADER_ROW

    class _Client(FakeBoardClient):
        def freeze(self, tab, *, rows=None, cols=None):
            pass

    client = _Client()
    style_slate_grid(client)
    by_cell = {cell: text for _tab, cell, text in client.notes}
    assert "both teams" in by_cell[f"{SLATE_COL['GameEnv']}1"].lower()
    opp_def_pass = f"{chr(65 + SLATE_TEAMS_COLHEADER.index('Opp Def EPA/pass'))}{SLATE_TEAMS_COLHEADER_ROW}"
    assert "ALLOWS" in by_cell[opp_def_pass]  # what the opponent's defense allows, higher = softer


def test_builder_tab_usage_headers_get_the_note_on_the_header_row_only():
    class _Client(FakeBuilderTabClient):
        def __init__(self):
            super().__init__(["Name", "Pos.", "Tgt%", "WOPR"])
            self.notes = []

        def set_note(self, tab, cell, note):
            self.notes.append((cell, note))

    client = _Client()
    polish_builder_tab(client, "Player Pool", last_row=100, header_row=2)
    cells = dict(client.notes)
    assert set(cells) == {"C2", "D2"}  # exactly the two usage headers present, on the header row
    assert "last 3 games played" in cells["C2"]


def test_model_check_sits_after_season_and_results_in_the_money_band():
    names = [tab for tab, _family in WEEK_ORDER]
    assert names.index("Season") < names.index("Results") < names.index("Model Check")
    assert dict(WEEK_ORDER)["Model Check"] == "money"


def test_every_tab_with_bold_names_says_what_bold_means():
    # Player Pool, EdgeRaw and Lineups bold a Name that has at least one flag (`_apply_name_flag_style`); each
    # tab's A1 note says so, and so does the Instructions tab.
    from dfs.sheet_instructions import render_instructions_grid
    from dfs.sheet_style import BOLD_NAME_HINT, TAB_NOTES

    for tab in ("EdgeRaw", "Player Pool", "Lineups"):
        assert BOLD_NAME_HINT in TAB_NOTES[tab], tab
    grid = render_instructions_grid()
    text = " ".join(cell for pair in grid.values() for cell in pair).lower()
    assert "bold name = at least one flag" in text
