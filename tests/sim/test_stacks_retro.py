"""The stack retro's arithmetic: the top 1% of a contest, the lift and its baseline, the verdict and the
sentence the tab shows. No standings are read."""

from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from dfs import stacks
from dfs.sim import field_gen
from dfs.sim import stacks_retro as sr


def week(n, lineups, top, lev, base, *, pctile=0.8):
    """A WeekRetro whose leverage set holds `lev` top lineups out of a field share and the baseline `base`."""
    w = sr.WeekRetro(week=n, contests=3, lineups=lineups, top=top, pctile=pctile)
    for r in (*sr.RANKINGS, sr.BASELINE):
        w.x_top[r], w.x_field[r], w.predicted[r], w.field_pred[r] = 0, 0, 1.0, 0.1
    w.x_top["lev"], w.x_field["lev"] = lev
    w.x_top[sr.BASELINE], w.x_field[sr.BASELINE] = base
    return w


def test_the_top_one_percent_keeps_ties_at_the_cut():
    pts = np.arange(1000, dtype=float)
    assert sr.top_mask(pts).sum() == 10
    tied = np.array([5.0] * 3 + [4.0] * 997)  # the 10th place is inside the 997 tied at 4: all are kept
    assert sr.top_mask(tied).sum() == 1000
    assert sr.top_mask(np.array([5.0] * 10 + [4.0] * 990)).sum() == 10
    tied_at_cut = np.array([9.0] + [8.0] * 20 + [1.0] * 979)
    assert sr.top_mask(tied_at_cut).sum() == 21  # the 10th place is one of 20 tied: all are kept
    assert sr.top_mask(np.array([3.0, 2.0])).sum() == 1  # a tiny contest still has a winner


def test_lift_is_the_top_share_over_the_field_share_and_z_tests_the_count():
    w = week(4, 10_000, 100, (40, 2_000), (60, 8_000))
    assert w.field_rate("lev") == pytest.approx(0.2)
    assert w.lift("lev") == pytest.approx(0.40 / 0.20)
    assert w.lift(sr.BASELINE) == pytest.approx(0.60 / 0.80)
    assert w.over_baseline() == pytest.approx(2.0 / 0.75)
    assert w.z("lev") == pytest.approx((40 - 100 * 0.2) / math.sqrt(100 * 0.2 * 0.8))
    empty = week(2, 0, 0, (0, 0), (0, 0))
    assert math.isnan(empty.lift("lev")) and math.isnan(empty.z("lev"))


def test_pooled_adds_counts_and_counts_the_weeks_above_one():
    a = week(4, 10_000, 100, (40, 2_000), (60, 8_000))
    b = week(5, 5_000, 50, (5, 1_000), (30, 4_000))
    p = sr.pooled([a, b], "lev")
    assert p["x_top"] == 45 and p["top"] == 150
    assert p["lift"] == pytest.approx((45 / 150) / (3_000 / 15_000))
    assert p["weeks"] == 2 and p["weeks_above_1"] == 1


def test_a_flagged_set_is_compared_with_random_sets_of_the_same_size():
    rng = np.random.default_rng(0)
    cands = {(i, (100 + i,)) for i in range(60)}
    seen = {k: int(rng.integers(50, 150)) for k in cands}
    in_top = {k: int(rng.binomial(seen[k], 0.01)) for k in cands}
    in_top.update({k: 12 for k in sorted(cands)[:5]})  # the first five are the winners
    w = sr.WeekRetro(week=4, contests=1, lineups=sum(seen.values()), top=sum(in_top.values()))
    best = set(sorted(cands)[:5])
    assert sr.random_percentile(w, best, cands, seen, in_top) > 0.9
    worst = set(sorted(cands, key=lambda k: in_top[k])[:5])
    assert sr.random_percentile(w, worst, cands, seen, in_top) < 0.1
    assert math.isnan(sr.random_percentile(w, cands, cands, seen, in_top))  # nothing to draw from
    assert math.isnan(sr.random_percentile(w, set(), cands, seen, in_top))


def test_the_verdict_needs_the_baseline_beaten_and_chance_beaten_to_be_tested():
    good = [week(w, 10_000, 100, (40, 2_000), (60, 8_000), pctile=0.97) for w in (4, 5, 6)]
    assert sr.verdict(good) == sr.VERDICT_TESTED and sr.tested(good)
    assert sr.combined_z(good) > sr.TESTED_Z
    mixed = [week(w, 10_000, 100, (40, 2_000), (60, 8_000), pctile=0.8) for w in (4, 5, 6)]
    assert sr.combined_z(mixed) < sr.TESTED_Z and sr.verdict(mixed) == sr.VERDICT_WEAK
    below = [week(w, 10_000, 100, (10, 2_000), (60, 8_000), pctile=0.3) for w in (4, 5, 6)]
    assert sr.verdict(below) == sr.VERDICT_UNTESTED and not sr.tested(below)
    split = [good[0], below[0], below[1]]  # above the baseline in one week of three
    assert sr.verdict(split) == sr.VERDICT_UNTESTED
    assert sr.verdict([week(2, 0, 0, (0, 0), (0, 0))]) == sr.VERDICT_UNTESTED
    assert sr.verdict([]) == sr.VERDICT_UNTESTED


def test_a_week_with_no_big_gpp_is_left_out_of_the_verdict_and_the_sentence():
    good = [week(w, 10_000, 100, (40, 2_000), (60, 8_000), pctile=0.97) for w in (4, 5)]
    thin = week(2, 0, 0, (0, 0), (0, 0), pctile=float("nan"))
    assert sr.verdict([thin, *good]) == sr.verdict(good)
    line = sr.track_record_line([thin, *good])
    assert "weeks 4, 5" in line and "W2" not in line
    assert "2.0x" in line and "0.7x" in line  # the flagged set and the baseline
    assert sr.VERDICT_WORDS[sr.VERDICT_TESTED] in line
    assert "no big GPP standings" in sr.track_record_line([thin])


def test_the_saved_payload_carries_the_sentence_the_verdict_and_every_ranking():
    weeks = [week(w, 10_000, 100, (40, 2_000), (60, 8_000), pctile=0.8) for w in (4, 5, 6)]
    payload = sr.retro_payload(weeks)
    assert payload["verdict"] == sr.VERDICT_WEAK and payload["tested"] is False
    assert payload["line"] == sr.track_record_line(weeks)
    assert {"lev", "top1", "field", "all"} <= set(payload["weeks"][0])
    assert payload["weeks"][0]["lev"]["lift"] == pytest.approx(2.0)
    assert payload["pooled"]["lev"]["weeks_above_1"] == 3


def test_flagged_takes_the_top_rows_of_a_ranking_and_all_for_the_baseline():
    t = pd.DataFrame(
        {
            "qb_i": [1, 2, 3],
            "mates_i": [(10,), (20, 21), (30,)],
            "lev": [0.1, 0.3, 0.2],
            "top1": [0.3, 0.2, 0.1],
        }
    )
    assert sr.flagged(t, "lev", k=2) == {(2, (20, 21)), (3, (30,))}
    assert sr.flagged(t, "top1", k=1) == {(1, (10,))}
    assert sr.flagged(t, sr.BASELINE) == {(1, (10,)), (2, (20, 21)), (3, (30,))}
    assert sr.flagged(t.iloc[0:0], "lev") == set()


def test_the_field_model_survives_a_round_trip_through_json():
    m = sr.FieldModel(
        rates=field_gen.FieldRates(0.6, 0.2, 0.5, 0.3, (0.0, 200.0, 900.0)),
        herd=field_gen.HerdModel(share=0.02, decay=0.46, k=20, lead_decay=0.6),
        weeks=(2, 4, 5),
    )
    back = sr.FieldModel.from_dict(m.to_dict())
    assert back == m
    assert stacks.CHALK_K <= m.herd.k
