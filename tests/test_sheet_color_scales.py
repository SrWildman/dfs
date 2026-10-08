import re

import pytest

from dfs.derived import ALL_PCT_COLUMNS
from dfs.sheet_color_scales import (
    EDGE_HIGH_PERCENTILE,
    EDGE_LOW_PERCENTILE,
    FIELD_COLOR_SCALES,
    GRAD_MAX,
    GRAD_MIN,
    PCT_HELPER_FOR_FIELD,
    STEP_LEVELS,
    WHITE,
    ZERO_EXCLUDED_COLUMNS,
    ZERO_GREY_BG,
    _zero_exclude_formula,
    column_rule_specs,
    diverging_anchor_kwargs,
    grouped_column_rule_specs,
    step_rule_specs,
)

# ---------------------------------------------------------------------------
# Sam, 2026-10-01: no fixed cut-offs. Player metrics are compared WITHIN POSITION as a percentile,
# in many small shades (steps); everything with no position is one smooth gradient. Zeros grey.
# ---------------------------------------------------------------------------


def _formulas(specs):
    return [s["values"][0] for s in specs if s["condition_type"] == "CUSTOM_FORMULA"]


def test_every_percentile_helper_the_rules_read_exists_in_edge_columns_config():
    assert set(PCT_HELPER_FOR_FIELD.values()) == set(ALL_PCT_COLUMNS.values())


def test_a_pct_column_gets_two_ladders_of_small_shades_and_a_plain_middle():
    specs = step_rule_specs("pct", "F", 2, 619, pct_letter="AS")
    assert len(specs) == 2 * STEP_LEVELS == 26
    assert {s["a1_range"] for s in specs} == {"F2:F619"}
    f = _formulas(specs)
    # relative row reference to the hidden helper, never a blank percentile
    assert all("ISNUMBER($AS2)" in x and "$AS2" in x for x in f)
    # the middle 20% (40..60) matches no rule -- plain
    assert not any(">=50" in x or "<=50" in x for x in f)
    assert "$AS2>=60,$AS2<65" in f[0] and "$AS2<=40,$AS2>35" in f[1]
    # steps are 5 points wide out to p 75 / 25, then HALF-size (2.5) where the top players live
    assert "$AS2>=70,$AS2<75" in f[4] and "$AS2>=75,$AS2<77.5" in f[6] and "$AS2>=95,$AS2<97.5" in f[-4]
    # the last shade is open-ended on each side (top and bottom 2.5%)
    assert f[-2] == "=AND(ISNUMBER($AS2),$AS2>=97.5)"
    assert f[-1] == "=AND(ISNUMBER($AS2),$AS2<=2.5)"


def test_neighbouring_steps_differ_by_a_barely_visible_shade_and_the_ends_are_the_gradient_palette():
    specs = step_rule_specs("pct", "F", 2, 100, pct_letter="AS")
    greens = [s["fmt"]["backgroundColor"] for s in specs[0::2]]
    reds = [s["fmt"]["backgroundColor"] for s in specs[1::2]]
    assert greens[-1] == GRAD_MAX and reds[-1] == GRAD_MIN  # no new hues
    for ladder, strong in ((greens, GRAD_MAX), (reds, GRAD_MIN)):
        for lighter, darker in zip(ladder, ladder[1:], strict=False):
            gap = max(abs(lighter[k] - darker[k]) for k in strong)
            assert 0 < gap <= 0.06  # one step is a soft tint, never a jump
        assert all(ladder[0][k] > strong[k] for k in strong if strong[k] < 1)  # first is pale


def test_the_steps_partition_the_percentile_with_no_gap_or_overlap_outside_the_plain_middle():
    """Model of the rule set: every percentile in (0, 100] lands in at most one step, and only
    the middle 40..60 lands in none."""
    f = _formulas(step_rule_specs("pct", "F", 2, 100, pct_letter="AS"))

    def matches(p):
        hits = 0
        for formula in f:
            conds = re.findall(r"\$AS2([<>]=?)(\d+)", formula)
            ok = all(
                {">=": p >= float(n), ">": p > float(n), "<=": p <= float(n), "<": p < float(n)}[op]
                for op, n in conds
            )
            hits += ok
        return hits

    for tenth in range(1, 1001):
        p = tenth / 10
        assert matches(p) == (0 if 40 < p < 60 else 1), p


def test_pct_without_a_helper_column_colours_nothing_rather_than_guessing():
    assert step_rule_specs("pct", "F", 2, 10, pct_letter=None) == []
    _, bools = column_rule_specs("Pts", "F", 2, 100, header=["Name", "Pts"])
    assert [b["condition_type"] for b in bools] == ["NUMBER_EQ"]  # zero stays grey, nothing else


def test_a_score_column_steps_on_its_own_value_and_never_matches_a_zero_or_non_number():
    f = _formulas(step_rule_specs("score", "J", 2, 619))
    assert len(f) == 26
    assert all("ISNUMBER($J2),$J2<>0" in x for x in f)


def test_player_metric_columns_resolve_their_helper_by_name_from_the_header():
    header = ["Name", "Pts", "ProjPts%ile"]
    grads, bools = column_rule_specs("Pts", "B", 2, 100, header=header)
    assert grads == []
    *steps, chip = bools
    assert len(steps) == 26 and all("$C2" in x["values"][0] for x in steps)
    assert chip["condition_type"] == "NUMBER_EQ" and chip["fmt"] == {"backgroundColor": ZERO_GREY_BG}


def test_the_player_metrics_are_pct_and_the_zero_to_100_scores_are_score():
    for name in ("ProjPts", "AggPts", "Pts", "Ceiling", "Ceil", "Val", "CeilVal"):
        assert FIELD_COLOR_SCALES[name] == "pct", name
    for name in ("ValAdj", "CeilPct", "GameEnv"):
        assert FIELD_COLOR_SCALES[name] == "score", name


# ---- the columns with no position: one smooth gradient ----------------------------------------


def test_a_game_or_team_column_is_red_white_green_with_white_at_the_median():
    (grad,), _ = column_rule_specs("Total", "C", 2, 19)
    assert grad["min_color"] == GRAD_MIN and grad["mid_color"] == WHITE and grad["max_color"] == GRAD_MAX
    assert grad["mid_value"] == "=MEDIAN(FILTER($C$2:$C$19,$C$2:$C$19<>0))"


def test_the_gradient_ends_are_the_5th_and_95th_percentile_of_the_non_zero_values_not_min_max():
    (grad,), _ = column_rule_specs("PROE", "J", 2, 619)
    assert (EDGE_LOW_PERCENTILE, EDGE_HIGH_PERCENTILE) == (0.05, 0.95)
    assert grad["min_value"] == "=PERCENTILE(FILTER($J$2:$J$619,$J$2:$J$619<>0),0.05)"
    assert grad["max_value"] == "=PERCENTILE(FILTER($J$2:$J$619,$J$2:$J$619<>0),0.95)"
    assert grad["min_type"] == grad["max_type"] == "NUMBER"


def test_a_lower_is_better_column_is_green_white_red():
    for field in ("Pace", "Spread", "OppPosRank", "Exposure"):
        (grad,), _ = column_rule_specs(field, "D", 2, 30)
        assert grad["min_color"] == GRAD_MAX and grad["max_color"] == GRAD_MIN, field
        assert grad["mid_color"] == WHITE, field


def test_no_gradient_anchor_is_a_fixed_number():
    for name, kind in FIELD_COLOR_SCALES.items():
        if kind in ("pct", "score", "diverging", "warm"):
            continue  # steps are percentile ranks; diverging is zero-centred; Own% is below
        (grad,), _ = column_rule_specs(name, "K", 2, 50)
        for key in ("min_value", "max_value"):
            assert grad[key].startswith("=") and "$K$2:$K$50" in grad[key], (name, key)


def test_exact_zeros_get_the_grey_chip_except_spread_where_zero_is_a_real_value():
    for name in ("ProjPts", "ValAdj", "Own%", "Used", "Exposure", "Total"):
        _, bools = column_rule_specs(name, "F", 2, 100, header=["Name", "ProjPts%ile"])
        chip = bools[-1]
        assert chip["condition_type"] == "NUMBER_EQ" and chip["values"] == ["0"], name
        assert chip["fmt"] == {"backgroundColor": ZERO_GREY_BG}
    assert "Spread" not in ZERO_EXCLUDED_COLUMNS
    (grad,), bools = column_rule_specs("Spread", "D", 2, 30)
    assert bools == [] and "FILTER" not in grad["min_value"]  # a zero spread (pick'em) counts


def test_own_pct_keeps_the_chalk_anchored_midpoint_so_its_colours_are_not_flat():
    # Ownership is right-skewed (most players low, a few chalk plays high): a median midpoint
    # (~5%) crushed nearly every value into one pale amber (Week 3 A2; again 2026-10-02, "really
    # flat in terms of colour spread"). The midpoint is the 20% CHALK line, the low end the lowest
    # NON-ZERO value, the high end the real maximum.
    from dfs.derived import CHALK_OWNERSHIP_THRESHOLD

    (grad,), bools = column_rule_specs("Own%", "H", 2, 100)
    assert grad["mid_type"] == "NUMBER" and grad["mid_value"] == str(CHALK_OWNERSHIP_THRESHOLD)
    assert grad["min_value"] == '=MINIFS($H$2:$H$100,$H$2:$H$100,"<>0")'
    assert "max_value" not in grad  # the real column maximum
    assert [b["condition_type"] for b in bools] == ["NUMBER_EQ"]  # zeros stay grey


def test_leverage_is_a_zero_centred_diverging_scale():
    (grad,), bools = column_rule_specs("Leverage", "M", 2, 100)
    assert grad["mid_value"] == "0" and grad["mid_color"] == WHITE
    assert bools == []  # zero is a real, meaningful white, not missing data


def test_a_gradient_column_split_into_blocks_gets_one_rule_over_all_blocks():
    groups = [(3, 19), (21, 42), (44, 70)]
    grad, bools = grouped_column_rule_specs("Total", "F", groups)
    assert grad["a1_ranges"] == ["F3:F19", "F21:F42", "F44:F70"] and "a1_range" not in grad
    assert grad["mid_value"].count("FILTER(") == 3  # every block feeds the one median
    assert "{FILTER($F$3:$F$19,$F$3:$F$19<>0);FILTER(" in grad["min_value"]
    (chip,) = bools
    assert chip["a1_range"] == "F3:F70" and chip["condition_type"] == "NUMBER_EQ"


def test_a_step_column_split_into_blocks_is_one_set_of_rules_over_the_span_and_no_gradient():
    groups = [(3, 19), (21, 42)]
    grad, bools = grouped_column_rule_specs("Pts", "F", groups, header=["Name", "ProjPts%ile"])
    assert grad is None
    *steps, chip = bools
    assert len(steps) == 26 and {b["a1_range"] for b in steps} == {"F3:F42"}
    assert chip["a1_range"] == "F3:F42"


def test_every_field_has_a_known_kind():
    known = {"pct", "score", "gradient", "diverging", "reversed", "warm"}
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
