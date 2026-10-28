"""The double-up line model (`dfs.sim.du_line`): the herd's three cases, ties, and the fit by week."""

import numpy as np
import pytest
from scipy.stats import norm

from dfs.sim import du_line

MODEL = du_line.DuLineModel(herd_share=0.2, cv=0.15, shift=0.0, shift_sd=0.0, paid=0.435)


def test_without_a_herd_the_line_is_the_paid_quantile_of_a_normal_field():
    mu = np.array([100.0, 140.0])
    got = du_line.lines(mu, None, MODEL)
    assert got == pytest.approx(mu + 0.15 * mu * norm.ppf(1 - 0.435))


def test_a_busted_herd_drops_out_and_the_line_sits_in_the_rest():
    mu, h = np.array([120.0]), np.array([80.0])
    rest = (120.0 - 0.2 * 80.0) / 0.8
    expected = rest + 0.15 * 120.0 * norm.ppf(1 - 0.435 / 0.8)
    got = du_line.lines(mu, h, MODEL)
    assert got == pytest.approx([expected]) and got[0] > h[0]


def test_a_booming_herd_takes_its_share_of_the_paid_places():
    mu, h = np.array([120.0]), np.array([180.0])
    rest = (120.0 - 0.2 * 180.0) / 0.8
    expected = rest + 0.15 * 120.0 * norm.ppf(1 - (0.435 - 0.2) / 0.8)
    got = du_line.lines(mu, h, MODEL)
    assert got == pytest.approx([expected]) and got[0] < h[0]


def test_a_herd_near_the_middle_is_the_line_and_its_copies_cash():
    mu, h = np.array([120.0]), np.array([121.0])
    line = du_line.lines(mu, h, MODEL)
    assert line == pytest.approx(h)
    scores = np.array([[121.0, 120.99]])  # the herd itself ties at the line; a hair below does not
    assert list(du_line.cash_probability(scores, line)) == [1.0, 0.0]


def test_the_shift_moves_the_line_and_its_spread_is_seeded():
    mu = np.full(4000, 120.0)
    shifted = du_line.lines(mu, None, du_line.DuLineModel(herd_share=0.0, shift=5.0))
    # the shift moves the field's mean, and its spread with it (cv x the shifted mean)
    assert shifted == pytest.approx(du_line.lines(mu + 5.0, None, du_line.DuLineModel(herd_share=0.0)))
    noisy = du_line.DuLineModel(herd_share=0.0, shift_sd=8.0)
    a, b = du_line.lines(mu, None, noisy), du_line.lines(mu, None, noisy)
    assert np.array_equal(a, b)
    assert 7.0 < np.std(a) < 9.5


def _stats(week, share, cv, real, forecast, paid=0.435):
    return du_line.ContestStats(week, share, cv, real, forecast, paid)


def test_fit_counts_each_week_once_and_leaves_one_out():
    stats = [
        _stats(4, 0.20, 0.18, 110.0, 124.0),
        _stats(4, 0.10, 0.16, 110.0, 124.0),  # same week: averaged first
        _stats(5, 0.12, 0.15, 143.0, 136.0),
        _stats(6, 0.06, 0.13, 126.0, 122.0),
    ]
    m = du_line.fit(stats)
    assert m.weeks == (4, 5, 6)
    assert m.herd_share == pytest.approx((0.15 + 0.12 + 0.06) / 3)
    assert m.shift == pytest.approx((-14.0 + 7.0 + 4.0) / 3)
    assert m.shift_sd == pytest.approx(np.std([-14.0, 7.0, 4.0], ddof=1))
    held = du_line.fit(stats, exclude_week=4)
    assert held.weeks == (5, 6) and held.shift == pytest.approx(5.5)
    assert du_line.fit(stats[:1]).shift_sd == 0.0
    assert du_line.fit([]) == du_line.DuLineModel()


def test_the_calibrated_offset_makes_the_other_weeks_double_up_entries_add_up():
    import pandas as pd

    from dfs.sim import du_line_retro as retro

    rng = np.random.default_rng(1)
    weeks = {}
    for week in (4, 5, 6):
        mu = 120.0 + rng.normal(0, 5, 4000)
        scores = mu[:, None] + rng.normal(0, 20, (4000, 200))
        frame = pd.DataFrame(
            {
                "set": [retro.CALIBRATION_SET] * 150 + ["random"] * 50,
                "col": range(200),
                # 40% of the entries cashed, whatever the week: 60 lineups of one entry and one of 30 copies
                # cashed (120 of 300 entries); the random lineups do not count
                "cashed": [1.0] * 60 + [0.0] * 90 + [1.0] * 50,
                "weight": [1.0] * 59 + [61.0] + [1.0] * 89 + [91.0] + [1.0] * 50,
            }
        )
        weeks[week] = (frame, retro.WeekDraws([], scores, mu, None, 0.6))
    grid = np.arange(-10.0, 20.01, 0.5)
    offset = retro.calibrated_value(weeks, 6, lambda d, v: d.field_mean + v, grid)
    misses = {w: retro.offset_miss(f, d, grid) for w, (f, d) in weeks.items()}
    assert retro.calibrated_offset(misses, 6, grid) == offset  # the one-sort path agrees with the grid search
    one = retro.offset_miss(*weeks[4], grid)
    for k in (0, 20, 40):
        frame, draws = weeks[4]
        p = du_line.cash_probability(
            draws.scores[:, frame["col"].to_numpy()[:150]], draws.field_mean + grid[k]
        )
        brute = float(frame["weight"][:150] @ p - frame["weight"][:150] @ frame["cashed"][:150])
        assert one[k] == pytest.approx(brute, abs=1e-6)
    # P(N(0, 20) >= offset) = 0.40 at offset ~ 5.1: the grid's nearest point
    assert offset == pytest.approx(20 * norm.ppf(0.6), abs=0.6)
    no_set = {w: (f.assign(set="random"), d) for w, (f, d) in weeks.items()}
    assert retro.calibrated_value(no_set, 6, lambda d, v: d.field_mean + v, grid) == grid[0]


def test_a_cached_week_reads_back_without_this_modules_classes(tmp_path):
    import pickle

    import pandas as pd

    from dfs.sim import du_line_retro as retro

    frame = pd.DataFrame({"set": ["random"], "col": [0], "cashed": [1.0]})
    draws = retro.WeekDraws([("A", "B")], np.ones((3, 1)), np.arange(3.0), None, 0.6)
    path = tmp_path / "w.pkl"
    retro.save_week(path, frame, draws)
    raw = pickle.loads(path.read_bytes())
    assert isinstance(raw, dict)  # plain data: no `__main__.WeekDraws` to fail under another entry point
    got_frame, got = retro.load_week(path)
    pd.testing.assert_frame_equal(got_frame, frame)
    assert got.kept == [("A", "B")] and got.herd is None and got.offset == 0.6
    assert np.array_equal(got.scores, draws.scores) and np.array_equal(got.field_mean, draws.field_mean)
