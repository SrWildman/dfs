"""The Compare tab (`compare_card.py`): the spec, the layout (blank pickers, mixed positions), the highlight
rule as Python and as the conditional format, and the text render."""

import re

import pandas as pd
import pytest

from dfs import compare_card as cc
from dfs import player_card as pc
from dfs.derived import EDGE_SHEET_ORDER, edge_sheet_letter
from dfs.weekly_reset import LINEUPS_NAME_BLOCKS

EXPOSURE_HEADER = ["Name", "Pos", "Salary", "# Lineups", "Exposure", "Target", "vs Target"]
ALL_ROWS = [item for _title, rows in cc.SECTIONS for item in rows]


def _layout(exposure=True, names=None):
    return cc.build_layout(
        edge_tab="EdgeRaw",
        lineups_tab="Lineups",
        exposure_tab="Exposure",
        exposure=pc.exposure_columns(EXPOSURE_HEADER) if exposure else None,
        lineup_count_cell="H1",
        names=names,
    )


def _row_of(lay, label):
    return next(i for i, r in enumerate(lay.rows, start=1) if r[0] == label)


# ---- the spec -------------------------------------------------------------------------------


def test_every_compared_field_is_an_edgeraw_column_or_the_implied_total():
    for item in ALL_ROWS:
        if item.spec is pc.IMPLIED:
            continue
        assert item.spec.source == pc.EDGE and item.spec.column in EDGE_SHEET_ORDER, item


def test_every_measure_the_ask_names_is_on_the_tab():
    wanted = [
        "Team", "Pos", "Salary", "Opp", "Kickoff (ET)", "Status", "Flags",
        "TFFB", "Sleeper", "FantasyPros", "ESPN", "Props", "AggPts", "CalPts",
        "Hit3x%", "Boom%", "Bust%", "Val", "ValAdj",
        "Own% (TFFB)", "GppOwn (shadow)", "CashOwn (shadow)", "Leverage",
        "Snap% L5", "Targets L5", "Carries L5", "DK pts L5", "Target share", "xFP / game", "xFP last 3",
        "xFP trend",
        "TPRR~ (proxy)", "xFP per $1k", "Targets per $1k", "HVT per $1k (RB)", "PriceGap (SD)",
        "Total", "Spread", "Implied (team)", "OppPosRank", "OppEPA", "Wind (mph)",
        "In your lineups", "Exposure",
    ]  # fmt: skip
    labels = [r[0] for r in _layout().rows]
    for label in wanted:
        assert label in labels, label
    assert any(label.startswith("Ceil") for label in labels)


def test_edge_formulas_use_the_derived_letter_and_each_pickers_own_helper_cell():
    lay = _layout()
    for item in ALL_ROWS:
        if item.spec.source != pc.EDGE:
            continue
        row = lay.rows[_row_of(lay, item.spec.label) - 1]
        letter = edge_sheet_letter(item.spec.column)
        for column, cell in zip(cc.PICKER_COLUMNS, row[1:], strict=True):
            assert f"EdgeRaw!${letter}$2:${letter}" in cell, (item.spec.label, column)
            assert f"{column}${cc.HELPER_ROW}" in cell


def test_the_tab_reuses_the_player_cards_formula_not_a_copy():
    spec = pc.Field("Sleeper", pc.EDGE, "SleeperPts", pc.PTS)
    lay = _layout()
    assert lay.rows[_row_of(lay, "Sleeper") - 1][1] == pc.formula(spec, "EdgeRaw", "B$2")
    assert pc.formula(spec, "EdgeRaw") == pc.formula(spec, "EdgeRaw", pc.ROW_REF)  # the card is unchanged


# ---- the layout ------------------------------------------------------------------------------


def test_row_1_is_four_pickers_and_a_rebuild_keeps_what_was_typed():
    lay = _layout(names=["Puka Nacua", "", "Trey McBride"])
    assert lay.rows[0] == ["Compare", "Puka Nacua", "", "Trey McBride", ""]
    assert cc.PICKER_RANGE == "B1:E1" and cc.unprotected_ranges() == ["B1:E1"]
    assert _layout().rows[0] == ["Compare", "", "", "", ""]


def test_every_row_is_exactly_the_tab_width():
    assert {len(r) for r in _layout().rows} == {cc.COLUMN_COUNT}


def test_helper_row_matches_each_picker_against_edgeraws_names_and_is_blank_when_not_found():
    helpers = _layout().rows[cc.HELPER_ROW - 1]
    for column, cell in zip(cc.PICKER_COLUMNS, helpers[1:], strict=True):
        name = edge_sheet_letter("Name")
        assert cell == f'=IFERROR(MATCH({column}$1,EdgeRaw!${name}$2:${name},0),"")'


def test_a_blank_picker_is_a_blank_column_never_an_error():
    lay = _layout()
    for item in ALL_ROWS:
        row = lay.rows[_row_of(lay, item.spec.label) - 1]
        for column, cell in zip(cc.PICKER_COLUMNS, row[1:], strict=True):
            assert cell.startswith(f'=IF({column}$2="","",'), (item.spec.label, column)
            assert cell.count("IFERROR(") >= 1  # a name that is not on EdgeRaw blanks too
    lineups = lay.rows[_row_of(lay, cc.LINEUPS_LABEL) - 1]
    exposure = lay.rows[_row_of(lay, cc.EXPOSURE_LABEL) - 1]
    for column, a, b in zip(cc.PICKER_COLUMNS, lineups[1:], exposure[1:], strict=True):
        assert a.startswith(f'=IF({column}$1="","",') and b.startswith(f'=IF({column}$1="","",')


def test_no_formula_spills_when_a_picker_is_blank():
    # INDEX(range, "") spills a whole column: every INDEX on the tab sits behind its helper guard
    for row in _layout().rows[cc.HELPER_ROW :]:
        for cell in row[1:]:
            if "INDEX(EdgeRaw" in str(cell):
                assert str(cell).startswith("=IF(") and '$2="","",' in str(cell)


def test_the_status_row_asks_for_a_pick_and_flags_mixed_positions():
    lay = _layout()
    status = lay.rows[cc.STATUS_ROW - 1][0]
    assert cc.PICK_NOTE in status and cc.SAME_POSITION_NOTE in status
    assert status.startswith("=IF(COUNTA($B$1:$E$1)=0,")
    pos = _row_of(lay, "Pos")  # the distinct-position count reads the Pos row, wherever it sits
    assert f"$B${pos}:$E${pos}" in status and "SUMPRODUCT" in status
    assert cc.STATUS_ROW in lay.note_rows


def test_lineups_block_counts_one_term_per_name_block_for_each_picker():
    lay = _layout()
    row = lay.rows[_row_of(lay, cc.LINEUPS_LABEL) - 1]
    start, end = LINEUPS_NAME_BLOCKS[0]
    for column, cell in zip(cc.PICKER_COLUMNS, row[1:], strict=True):
        assert cell.count("COUNTIF(") == len(LINEUPS_NAME_BLOCKS)
        assert f"Lineups!$A${start}:$A${end},{column}$1" in cell
    exposure = lay.rows[_row_of(lay, cc.EXPOSURE_LABEL) - 1]
    assert "Exposure!$" in exposure[1] and "MATCH(B$1," in exposure[1]


def test_without_an_exposure_tab_the_row_says_so_and_stays_blank():
    lay = _layout(exposure=False)
    row = next(r for r in lay.rows if str(r[0]).startswith(cc.EXPOSURE_LABEL))
    assert "(no Exposure tab)" in row[0] and row[1:] == [""] * len(cc.PICKER_COLUMNS)


def test_number_patterns_cover_every_measure_row_once():
    lay = _layout()
    ranges = [rng for rng, _ in lay.patterns]
    assert len(ranges) == len(set(ranges)) == len(ALL_ROWS) + 2  # + in-lineups and exposure
    assert all(pattern for _rng, pattern in lay.patterns)


# ---- the highlight rule ---------------------------------------------------------------------


def test_best_columns_shades_the_highest_or_lowest_and_all_ties():
    assert cc.best_columns([10.0, 12.5, 11.0], cc.HIGH) == [1]
    assert cc.best_columns([6000, 5200, 5800], cc.LOW) == [1]
    assert cc.best_columns([5.0, 9.0, 9.0], cc.HIGH) == [1, 2]  # a tie for best shades both


def test_best_columns_shades_nothing_without_a_comparison():
    assert cc.best_columns([10.0, 12.5], None) == []  # no clear direction
    assert cc.best_columns([10.0, None, ""], cc.HIGH) == []  # one number is not a comparison
    assert cc.best_columns([7.0, 7.0, 7.0], cc.HIGH) == []  # all equal: no best
    assert cc.best_columns([None, None], cc.LOW) == []
    assert cc.best_columns(["WR", "TE"], cc.HIGH) == []  # text
    assert cc.best_columns([float("nan"), 3.0, 4.0], cc.HIGH) == [2]  # a blank is skipped, not shaded


def test_the_directions_are_the_stated_ones():
    direction = {item.spec.label: item.better for item in ALL_ROWS}
    lows = {k for k, v in direction.items() if v == cc.LOW}
    assert lows == {
        "Salary", "Bust%", "Own% (TFFB)", "GppOwn (shadow)", "CashOwn (shadow)", "OppPosRank",
    }  # fmt: skip
    nones = {k for k, v in direction.items() if v is None}
    assert {"Team", "Pos", "Opp", "Kickoff (ET)", "Status", "Flags", "Spread", "Wind (mph)"} == nones
    assert direction["CalPts"] == cc.HIGH and direction["Leverage"] == cc.HIGH
    for phrase in ("Salary", "Bust%", "Own%", "OppPosRank"):
        assert phrase in cc.HIGHLIGHT_NOTE  # the lower-is-better rows are stated in one note


def test_highlight_rules_are_one_per_directional_row_and_use_max_or_min():
    lay = _layout()
    specs = cc.highlight_specs(lay)
    assert len(specs) == len(lay.highlights) == sum(1 for i in ALL_ROWS if i.better)
    by_row = {int(re.match(r"B(\d+):", s["a1_range"]).group(1)): s for s in specs}
    salary = by_row[_row_of(lay, "Salary")]
    cal = by_row[_row_of(lay, "CalPts")]
    assert salary["condition_type"] == "CUSTOM_FORMULA"
    r = _row_of(lay, "Salary")
    assert salary["values"][0] == (
        f"=AND(ISNUMBER(B{r}),COUNT($B{r}:$E{r})>=2,MAX($B{r}:$E{r})<>MIN($B{r}:$E{r}),B{r}=MIN($B{r}:$E{r}))"
    )
    assert "B{0}=MAX($B{0}:$E{0})".format(_row_of(lay, "CalPts")) in cal["values"][0]
    for label in ("Spread", "Wind (mph)", "Pos", "Flags", cc.LINEUPS_LABEL, cc.EXPOSURE_LABEL):
        assert _row_of(lay, label) not in by_row, label  # no clear direction: never shaded


def test_the_python_rule_and_the_sheet_rule_agree_on_a_grid():
    """Evaluate the conditional formula the way Sheets does, cell by cell, against `best_columns`."""

    def sheet_says(values, better):
        nums = [v for v in values if isinstance(v, int | float)]
        pick = max if better == cc.HIGH else min
        return [
            i
            for i, v in enumerate(values)
            if isinstance(v, int | float) and len(nums) >= 2 and max(nums) != min(nums) and v == pick(nums)
        ]

    cases = [[1, 2, 3, 4], [4, 4, 4, 4], [None, 3, None, 5], ["", "", 9, ""], [2, 2, 1, ""], [3, "x", 1, 3]]
    for better in (cc.HIGH, cc.LOW):
        for values in cases:
            assert cc.best_columns(values, better) == sheet_says(values, better), (values, better)


# ---- the text render ------------------------------------------------------------------------


def _edge():
    return pd.DataFrame(
        [
            {
                "Name": "Wide One", "Position": "WR", "Team": "AAA", "Opp": "BBB", "Salary": 7800,
                "CalPts": 17.1, "ProjPts": 16.0, "Own%": 0.25, "Bust%": 30.0, "OverUnder": 50.0,
                "Spread": -3.0, "OppPosRank": 4, "Kickoff": "Sun 1:00 PM", "Avail": "", "Flags": "",
            },
            {
                "Name": "Wide Two", "Position": "WR", "Team": "CCC", "Opp": "DDD", "Salary": 6900,
                "CalPts": 14.2, "ProjPts": 15.0, "Own%": 0.10, "Bust%": 38.0, "OverUnder": 44.0,
                "Spread": 2.5, "OppPosRank": 20, "Kickoff": "Sun 4:25 PM", "Avail": "Q", "Flags": "",
            },
            {
                "Name": "Tight End", "Position": "TE", "Team": "EEE", "Opp": "FFF", "Salary": 5400,
                "CalPts": 9.0, "ProjPts": 9.5, "Own%": 0.10, "Bust%": 38.0, "OverUnder": 44.0,
                "Spread": 2.5, "OppPosRank": 9, "Kickoff": "Sun 4:25 PM", "Avail": "", "Flags": "",
            },
        ]
    )  # fmt: skip


def test_render_text_marks_the_best_value_and_blanks_what_is_missing():
    text = cc.render_text(["Wide One", "Wide Two"], _edge())
    line = lambda label: next(ln for ln in text.splitlines() if ln.strip().startswith(label))  # noqa: E731
    assert "17.1 *" in line("CalPts") and "14.2 *" not in line("CalPts")
    assert "$6,900 *" in line("Salary")  # lower is better
    assert "10.0% *" in line("Own% (TFFB)")
    assert "*" not in line("Spread") and "*" not in line("Team")
    assert "-" in line("Sleeper") and "*" not in line("Sleeper")  # nobody has a number: no shade
    assert cc.SAME_POSITION_NOTE not in text


def test_render_text_for_mixed_positions_has_the_note_and_ties_shade_together():
    text = cc.render_text(["Wide One", "Wide Two", "Tight End"], _edge())
    assert cc.SAME_POSITION_NOTE in text
    own = next(ln for ln in text.splitlines() if ln.strip().startswith("Own%"))
    assert own.count("*") == 2  # Wide Two and Tight End tie for the lowest


def test_render_text_with_no_names_or_an_unknown_one():
    assert cc.render_text([], _edge()) == cc.PICK_NOTE
    assert cc.render_text(["", ""], _edge()) == cc.PICK_NOTE
    text = cc.render_text(["Wide One", "Nobody"], _edge())
    assert "Nobody (not on EdgeRaw)" in text and "17.1" in text


@pytest.mark.parametrize("fmt", sorted({i.spec.fmt for i in ALL_ROWS}))
def test_every_format_has_a_sheet_pattern(fmt):
    assert fmt in pc.SHEET_PATTERNS
