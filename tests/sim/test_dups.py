"""Duplicates: the expected-copies maths, the Top 1% share objective, the fit, and the Week 4 order of "
"magnitude."""

import math

import numpy as np
import pytest

from dfs.sim import dups


def test_the_independence_estimate_is_entries_times_the_product_of_the_shares():
    own = [0.2, 0.1, 0.3, 0.05, 0.1, 0.15, 0.25, 0.4, 0.08]
    product = math.prod(own)
    assert dups.ownership_product(own) == pytest.approx(product)
    assert dups.expected_dups(own, 1000, exponent=1.0) == pytest.approx(1000 * product)
    assert dups.ownership_sum(own) == pytest.approx(sum(own))


def test_the_stated_correction_is_a_power_of_the_product():
    own = [0.3] * 9
    assert dups.expected_dups(own, 2000) == pytest.approx(2000 * (0.3**9) ** dups.DUP_EXPONENT)
    assert dups.expected_dups(own, 2000, exponent=0.5) == pytest.approx(2000 * 0.3**4.5)
    # a chalkier lineup is duplicated more; a bigger field has more copies; no field has none
    assert dups.expected_dups([0.4] * 9, 1000) > dups.expected_dups([0.2] * 9, 1000)
    assert dups.expected_dups(own, 4000) == pytest.approx(2 * dups.expected_dups(own, 2000))
    assert dups.expected_dups(own, 0) == 0.0


def test_a_missing_or_tiny_share_counts_at_the_floor_never_zero():
    own = [0.2] * 8 + [float("nan")]
    assert dups.ownership_product(own) == pytest.approx(0.2**8 * dups.OWN_FLOOR)
    assert dups.ownership_product([0.2] * 8 + [0.0]) == pytest.approx(0.2**8 * dups.OWN_FLOOR)
    assert dups.expected_dups([0.0] * 9, 1000) > 0.0
    assert dups.ownership_sum(own) == pytest.approx(1.6)  # a missing number adds nothing to the sum
    assert dups.ownership_product([1.7] + [0.5] * 8) == pytest.approx(0.5**8)  # shares are capped at 1


def test_with_equal_top_1_percent_the_lineup_with_fewer_duplicates_ranks_first():
    p = 0.02
    unique, chalk = dups.expected_dups([0.08] * 9, 1000), dups.expected_dups([0.35] * 9, 1000)
    assert unique < chalk
    assert dups.top1_share(p, unique) > dups.top1_share(p, chalk)
    assert dups.top1_share(p, 0.0) == pytest.approx(p)  # nobody else holds it: the whole chance
    assert dups.top1_share(p, 3.0) == pytest.approx(p / 4)  # three copies split the prize four ways
    # a higher chance can still lose to enough extra copies, which is the point of the objective
    assert dups.top1_share(0.021, chalk) < dups.top1_share(0.020, unique)


def test_the_week_4_lineup_is_the_order_of_magnitude_the_correction_gets_and_independence_misses():
    """Sam's Week 4 double-up lineup was one of about 500 identical entries in a 4,597-entry field. Its
    players' exact ownership is in his standings file, not here, so this is a stand-in: nine players at the
    55-60% average a mega-chalk double-up lineup needs. (Replace with the real numbers locally.)"""
    entries = 4597
    for share in (0.55, 0.60):
        own = [share] * 9
        independent = dups.expected_dups(own, entries, exponent=1.0)
        corrected = dups.expected_dups(own, entries)
        assert independent < 60  # the plain product is an order of magnitude short
        assert 250 <= corrected <= 600  # the stated correction lands within a factor of two of 500


def test_a_typical_gpp_build_is_a_fraction_of_a_copy_and_a_chalky_one_is_several():
    typical = dups.expected_dups([0.12, 0.15, 0.10, 0.20, 0.12, 0.08, 0.10, 0.06, 0.08], 1000)
    chalky = dups.expected_dups([0.30] * 9, 1000)
    assert 0.0 < typical < 0.5 < 2.0 < chalky < 20.0


def test_labels_for_a_title():
    assert dups.dups_label(0.0) == "~0 dupes"
    assert dups.dups_label(0.3) == "~0.3 dupes"
    assert dups.dups_label(12.4) == "~12 dupes"


def test_fit_recovers_the_scale_and_exponent_of_a_known_field():
    rng = np.random.default_rng(3)
    products = 10 ** rng.uniform(-9, -2, 4000)
    entries = 2000
    truth_scale, truth_exponent = 1.5, 0.45
    copies = rng.poisson(truth_scale * entries * products**truth_exponent)
    scale, exponent = dups.fit_dup_model(products, entries, copies)
    assert exponent == pytest.approx(truth_exponent, abs=0.05)
    assert scale == pytest.approx(truth_scale, rel=0.35)
    with pytest.raises(ValueError):
        dups.fit_dup_model(
            [0.1, 0.1, 0.1], 1000, [1, 2, 3]
        )  # no spread in the products to fit an exponent on


def test_the_gpp_model_is_the_fitted_one_and_the_cash_model_is_still_the_placeholder():
    assert (dups.DUP_MODEL_GPP.scale, dups.DUP_MODEL_GPP.exponent) == (10.02, 0.712)
    assert (dups.DUP_MODEL_CASH.scale, dups.DUP_MODEL_CASH.exponent) == (dups.DUP_SCALE, dups.DUP_EXPONENT)
    own = [0.12, 0.15, 0.10, 0.20, 0.12, 0.08, 0.10, 0.06, 0.08]
    assert dups.DUP_MODEL_GPP.expected(own, 1000) == pytest.approx(
        dups.expected_dups(own, 1000, exponent=0.712, scale=10.02)
    )
    # the fit gives a typical build far fewer copies than the placeholder did, and a chalky one about as many
    assert dups.DUP_MODEL_GPP.expected(own, 1000) < dups.DUP_MODEL_CASH.expected(own, 1000)
    chalky = [0.30] * 9
    ratio = dups.DUP_MODEL_GPP.expected(chalky, 1000) / dups.DUP_MODEL_CASH.expected(chalky, 1000)
    assert 0.5 < ratio < 2.0
