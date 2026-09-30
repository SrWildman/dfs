import pytest

from dfs.derived import PLAYER_METRIC_PCT_COLUMNS
from dfs.sheet_color_scales import (
    BAND_LIGHT_GREEN,
    BAND_LIGHT_RED,
    BAND_STRONG_GREEN,
    BAND_STRONG_RED,
    FIELD_COLOR_SCALES,
    GRAD_MAX,
    GRAD_MIN,
    LEVERAGE_BANDS,
    PCT_HELPER_FOR_FIELD,
    RANK_BANDS,
    ZERO_EXCLUDED_COLUMNS,
    ZERO_GREY_BG,
    _zero_exclude_formula,
    band_rule_specs,
    column_rule_specs,
    diverging_anchor_kwargs,
)


def _formulas(specs):
    return [s["values"][0] for s in specs if s["condition_type"] == "CUSTOM_FORMULA"]


def test_every_percentile_helper_the_rules_read_exists_in_edge_columns_config():
    assert set(PCT_HELPER_FOR_FIELD.values()) == set(PLAYER_METRIC_PCT_COLUMNS.values())


def test_no_new_hues_bands_reuse_the_gradient_palette():
    assert BAND_STRONG_GREEN == GRAD_MAX and BAND_STRONG_RED == GRAD_MIN
    # the light bands are a 50% tint toward white of the strong ones, so they sit between
    for light, strong in ((BAND_LIGHT_GREEN, GRAD_MAX), (BAND_LIGHT_RED, GRAD_MIN)):
        assert all(strong[k] < light[k] <= 1 for k in strong if strong[k] < 1)


def test_pct_bands_read_the_helper_column_and_never_match_a_blank_percentile():
    specs = band_rule_specs("pct", "F", 2, 619, pct_letter="AS")
    assert [s["a1_range"] for s in specs] == ["F2:F619"] * 4  # ONE range per band, whole column
    assert _formulas(specs) == [
        "=AND(ISNUMBER($AS2),$AS2>=90)",
        "=AND(ISNUMBER($AS2),$AS2>=70,$AS2<90)",
        "=AND(ISNUMBER($AS2),$AS2>=10,$AS2<30)",
        "=AND(ISNUMBER($AS2),$AS2<10)",
    ]
    assert [s["fmt"]["backgroundColor"] for s in specs] == [
        BAND_STRONG_GREEN,
        BAND_LIGHT_GREEN,
        BAND_LIGHT_RED,
        BAND_STRONG_RED,
    ]


def test_pct_without_a_helper_column_colours_nothing_rather_than_guessing():
    assert band_rule_specs("pct", "F", 2, 10, pct_letter=None) == []


@pytest.mark.parametrize("kind", ["score", "leverage", "rank", "game", "game_reversed"])
def test_own_value_bands_never_match_zero_or_a_non_number(kind):
    for formula in _formulas(band_rule_specs(kind, "K", 2, 50)):
        assert "ISNUMBER($K2)" in formula and "$K2<>0" in formula


def test_spread_allows_zero_because_a_pickem_is_a_real_value():
    for formula in _formulas(band_rule_specs("game_reversed", "D", 2, 50, allow_zero=True)):
        assert "$D2<>0" not in formula and "ISNUMBER($D2)" in formula


def test_game_bands_use_percentrank_over_the_columns_own_range_without_filter():
    high, _, _, low = _formulas(band_rule_specs("game", "L", 2, 30))
    assert "PERCENTRANK($L$2:$L$30,$L2)*100>=90" in high
    assert "PERCENTRANK($L$2:$L$30,$L2)*100<10" in low
    assert not any("FILTER" in f for f in _formulas(band_rule_specs("game", "L", 2, 30)))


def test_reversed_game_bands_treat_low_as_good():
    strong_green = _formulas(band_rule_specs("game_reversed", "E", 2, 30))[0]
    assert "(1-PERCENTRANK($E$2:$E$30,$E2))*100>=90" in strong_green


def test_leverage_and_rank_bands_use_their_own_cutoffs():
    a, b, c, d = LEVERAGE_BANDS
    lev = _formulas(band_rule_specs("leverage", "M", 2, 9))
    assert f"$M2>={a}" in lev[0] and f"$M2<={d}" in lev[3]
    assert f"$M2>={b}" in lev[1] and f"$M2<={c}" in lev[2]
    ra, rb, rc, rd = RANK_BANDS
    rank = _formulas(band_rule_specs("rank", "N", 2, 9))
    assert f"$N2<={ra}" in rank[0] and f"$N2>={rd}" in rank[3]  # low rank = tough matchup?
    assert f"$N2<={rb}" in rank[1] and f"$N2>={rc}" in rank[2]


def test_the_five_bands_partition_a_percentile_with_no_gap_or_overlap():
    """Model of the pct/score rule set: every value in (0, 100] lands in exactly one of
    strong green / light green / none / light red / red."""

    def band(p):
        if p >= 90:
            return "sg"
        if p >= 70:
            return "lg"
        if p >= 30:
            return "none"
        if p >= 10:
            return "lr"
        return "sr"

    counts = {}
    for i in range(1, 1001):
        counts[band(i / 10)] = counts.get(band(i / 10), 0) + 1
    assert counts == {"sg": 101, "lg": 200, "none": 400, "lr": 200, "sr": 99}  # 1,000 values, none twice


def test_column_rule_specs_adds_the_grey_zero_chip_last_for_banded_columns():
    grads, bools = column_rule_specs("Pts", "F", 2, 100, header=["A", "B", "C", "D", "E", "F", "ProjPts%ile"])
    assert grads == []
    *bands, chip = bools
    assert len(bands) == 4 and chip["condition_type"] == "NUMBER_EQ"
    assert chip["values"] == ["0"] and chip["fmt"] == {"backgroundColor": ZERO_GREY_BG}


def test_a_banded_column_keeps_its_zero_chip_even_when_the_helper_is_missing():
    _, bools = column_rule_specs("Pts", "F", 2, 100, header=["Name", "Pts"])
    assert [b["condition_type"] for b in bools] == ["NUMBER_EQ"]  # zero stays grey, nothing else colours


def test_spread_has_no_zero_chip_because_zero_is_a_real_spread():
    assert "Spread" not in ZERO_EXCLUDED_COLUMNS
    _, bools = column_rule_specs("Spread", "D", 2, 30)
    assert all(b["condition_type"] == "CUSTOM_FORMULA" for b in bools)


def test_non_band_kinds_still_return_a_gradient_and_chip():
    grads, bools = column_rule_specs("Own%", "H", 2, 100)
    assert len(grads) == 1 and "mid_value" in grads[0]  # the warm CHALK midpoint
    assert [b["condition_type"] for b in bools] == ["NUMBER_EQ"]
    grads, bools = column_rule_specs("Exposure", "E", 2, 180)  # item 1d
    assert len(grads) == 1 and "MINIFS" in grads[0]["min_value"]  # min over NON-ZERO values only
    assert [b["condition_type"] for b in bools] == ["NUMBER_EQ"]


def test_every_field_has_a_known_kind():
    known = {
        "pct",
        "score",
        "leverage",
        "rank",
        "game",
        "game_reversed",
        "gradient",
        "diverging",
        "reversed",
        "warm",
    }
    assert set(FIELD_COLOR_SCALES.values()) <= known


def test_diverging_anchors_are_symmetric_so_a_zero_is_always_the_white_midpoint():
    """A column with no negatives has its own MIN at the zero midpoint, and Sheets then
    paints every zero the end colour (Slate Grid's Total move read solid red)."""
    a = diverging_anchor_kwargs("K2:K19")
    # ABSOLUTE: a relative anchor formula is shifted per row by Sheets, so every cell below the
    # column's only non-zero value would see a window with none in it (zeros rendered green).
    assert a["min_value"] == "=-MAX(MAX($K$2:$K$19),-MIN($K$2:$K$19))"
    assert a["max_value"] == "=MAX(MAX($K$2:$K$19),-MIN($K$2:$K$19))"
    assert a["min_type"] == a["max_type"] == "NUMBER"
    multi = diverging_anchor_kwargs(["A2:A5", "A9:A12"])
    assert (
        "MAX($A$2:$A$5,$A$9:$A$12)" in multi["max_value"]
        and "MIN($A$2:$A$5,$A$9:$A$12)" in multi["max_value"]
    )


def test_every_diverging_field_gets_the_symmetric_anchors_and_keeps_its_zero_midpoint():
    for field in ("ImpliedMove", "TotMove", "SpdMove"):
        (grad,), _ = column_rule_specs(field, "K", 2, 50)
        assert grad["mid_type"] == "NUMBER" and grad["mid_value"] == "0"
        assert grad["min_value"].startswith("=-MAX(MAX($K$2:$K$50)")
        assert grad["max_value"].startswith("=MAX(MAX($K$2:$K$50)")


def _every_reference_is_absolute(formula: str) -> bool:
    """No bare `K2`/`K2:K19`/`$K2` left: every cell reference has `$` on column AND row."""
    import re

    refs = re.findall(r"(?<![A-Za-z0-9_$])\$?[A-Z]{1,3}\$?\d+", formula)
    return bool(refs) and all(re.fullmatch(r"\$[A-Z]{1,3}\$\d+", r) for r in refs)


def test_zero_exclude_anchors_use_absolute_references_or_sheets_shifts_them_per_row():
    assert _zero_exclude_formula("MIN", "K2:K19") == 'MINIFS($K$2:$K$19,$K$2:$K$19,"<>0")'
    assert _zero_exclude_formula("MEDIAN", "K2:K19") == "MEDIAN(FILTER($K$2:$K$19,$K$2:$K$19<>0))"
    multi = ["B3:B12", "B14:B33"]  # one position's rows across several runs
    assert _every_reference_is_absolute(_zero_exclude_formula("MIN", multi))
    assert _every_reference_is_absolute(_zero_exclude_formula("MEDIAN", multi))


@pytest.mark.parametrize("name", sorted(FIELD_COLOR_SCALES))
def test_every_gradient_anchor_formula_the_dispatch_emits_is_absolute(name):
    grads, _ = column_rule_specs(name, "K", 2, 19)
    for grad in grads:
        for key in ("min_value", "mid_value", "max_value"):
            value = grad.get(key)
            if isinstance(value, str) and value.startswith("="):
                assert _every_reference_is_absolute(value), (name, key, value)
