"""The field generator on a synthetic slate: every lineup is legal, the construction rates and ownership come
out as asked, the chalk herd lands on the highest-product lineups, and it is deterministic in the seed."""

from collections import Counter

import numpy as np
import pandas as pd
import pytest

from dfs.sim import field_gen as fg

_CODES = ["AAA", "BBB", "CCC", "DDD", "EEE", "FFF", "GGG", "HHH", "III", "JJJ", "KKK", "LLL"]
TEAMS = [(t, _CODES[i + 1 - 2 * (i % 2)]) for i, t in enumerate(_CODES)]  # six games
DEPTH = {
    "QB": [7000, 5600],
    "RB": [8000, 6200, 5000, 4000],
    "WR": [8000, 6800, 5600, 4500, 3500, 3000],
    "TE": [5500, 4000, 2800],
    "DST": [3000],
}


def _frame() -> pd.DataFrame:
    rows, rng = [], np.random.default_rng(7)
    for team, opp in TEAMS:
        for pos, salaries in DEPTH.items():
            for k, salary in enumerate(salaries):
                own = rng.uniform(0.02, 0.30) / (k + 1)
                rows.append((f"{team}-{pos}{k}", pos, team, opp, salary, own))
    return pd.DataFrame(rows, columns=["name", "position", "team", "opp", "salary", "own"])


@pytest.fixture(scope="module")
def slate() -> fg.Slate:
    return fg.Slate.from_frame(_frame())


RATES = fg.FieldRates(
    stack1=0.8,
    stack2=0.35,
    bringback_stacked=0.6,
    bringback_unstacked=0.3,
    unspent=tuple(float(x) for x in np.linspace(0, 400, 41)),
)


def _legal(slate: fg.Slate, lineups: np.ndarray) -> None:
    pos = slate.position[lineups]
    for row in pos:
        c = Counter(row)
        assert c["QB"] == 1 and c["DST"] == 1
        assert 2 <= c["RB"] <= 3 and 3 <= c["WR"] <= 4 and 1 <= c["TE"] <= 2
        assert c["RB"] + c["WR"] + c["TE"] == 7
    assert (slate.salary[lineups].sum(axis=1) <= fg.SALARY_CAP).all()
    assert all(len(set(r)) == 9 for r in lineups)


def test_targets_fill_one_lineup_per_slot_group(slate):
    t = slate.targets()
    assert t[slate.position == "QB"].sum() == pytest.approx(1.0)
    assert t[slate.position == "DST"].sum() == pytest.approx(1.0)
    assert t[np.isin(slate.position, fg.FLEX_POSITIONS)].sum() == pytest.approx(7.0)
    curved = slate.targets(lambda x: x**2)  # any transform: the group totals still hold
    assert curved[slate.position == "QB"].sum() == pytest.approx(1.0)


def test_every_sampled_lineup_is_legal_and_leaves_little_salary(slate):
    rng = np.random.default_rng(0)
    lineups = fg.sample_lineups(slate, RATES, 1000, slate.targets(), rng)
    assert len(lineups) == 1000
    _legal(slate, lineups)
    left = fg.SALARY_CAP - slate.salary[lineups].sum(axis=1)
    assert left.max() <= max(RATES.unspent) + fg.UNSPENT_SLACK


def test_the_construction_rates_come_out_as_asked(slate):
    field = fg.generate_field(slate, RATES, 3000, seed=1)
    f = field.features()
    assert f["stack1"].mean() == pytest.approx(RATES.stack1, abs=0.04)
    assert f["stack2"].mean() == pytest.approx(RATES.stack2, abs=0.04)
    never = fg.FieldRates(0.0, 0.0, 0.0, 0.0, (0.0, 200.0))
    g = fg.generate_field(slate, never, 1000, seed=1, rake_rounds=1).features()
    assert g["stack1"].sum() == 0 and g["bringback"].sum() == 0


def test_raking_brings_the_field_to_its_targets(slate):
    field = fg.generate_field(slate, RATES, 3000, seed=2)
    err = np.abs(field.ownership() - slate.targets())
    assert err.mean() < 0.02
    assert err.max() < 0.08


def test_the_chalk_lineups_are_legal_distinct_and_in_product_order(slate):
    chalk = fg.chalk_lineups(slate, 5)
    assert chalk.shape == (5, 9)
    _legal(slate, chalk)
    assert len({tuple(r) for r in chalk}) == 5
    logp = np.log(slate.own)[chalk].sum(axis=1)
    assert np.all(np.diff(logp) <= 1e-9)


def test_the_herd_puts_its_share_on_the_chalk(slate):
    herd = fg.HerdModel(share=0.3, decay=0.0, k=3)
    field = fg.generate_field(slate, RATES, 2000, herd=herd, seed=3, rake_rounds=2)
    _legal(slate, field.lineups)
    top = tuple(fg.chalk_lineups(slate, 1)[0])
    count = Counter(field.keys())
    assert count[top] == pytest.approx(600, abs=2)
    assert field.dup_share() >= 0.3
    weights = fg.HerdModel(share=0.1, decay=0.5, k=3).weights(3)
    assert weights == pytest.approx([4 / 7, 2 / 7, 1 / 7])


def test_the_field_is_deterministic_in_the_seed(slate):
    a = fg.generate_field(slate, RATES, 500, seed=9, rake_rounds=2)
    b = fg.generate_field(slate, RATES, 500, seed=9, rake_rounds=2)
    c = fg.generate_field(slate, RATES, 500, seed=10, rake_rounds=2)
    assert np.array_equal(a.lineups, b.lineups)
    assert not np.array_equal(a.lineups, c.lineups)


def test_scores_and_lines(slate):
    field = fg.Field(slate, np.array([np.arange(9), np.arange(9) + 9]), np.ones(len(slate)))
    pts = np.arange(len(slate), dtype=float)
    assert list(field.scores(pts)) == [36.0, 117.0]
    assert field.scores(np.vstack([pts, 2 * pts])).shape == (2, 2)
    lines = fg.lines(np.arange(100, dtype=float), entries=1000, places_paid=220)
    assert lines["mean"] == pytest.approx(49.5)
    assert lines["cash_line"] == pytest.approx(78.0)  # place 220 of 1,000 is the 22nd of 100 from the top
    assert lines["top1"] == pytest.approx(99.0)


def test_rates_from_features_split_the_bring_back_by_stack():
    feats = pd.DataFrame(
        {
            "resolved": [True, True, True, True, False],
            "stack1": [1.0, 1.0, 0.0, 0.0, np.nan],
            "stack2": [1.0, 0.0, 0.0, 0.0, np.nan],
            "bringback": [1.0, 0.0, 1.0, 0.0, np.nan],
            "unspent": [0.0, 100.0, 300.0, np.nan, 50.0],
        }
    )
    r = fg.rates_from_features(feats)
    assert (r.stack1, r.stack2, r.bringback_stacked, r.bringback_unstacked) == (0.5, 0.25, 0.5, 0.5)
    assert r.unspent == (0.0, 50.0, 100.0, 300.0)
    assert r.stack2_given_stack1 == 0.5


def _reference_fits(b, left_at_most=None):
    """The lineup-by-player 'fits' test as it was first written (one float array per bound); `_Batch.fits` is
    a faster layout of the same rule and must agree with it everywhere."""
    pos = b.s.position
    fits_slot = np.zeros((b.n, len(b.s)), dtype=bool)
    slot_min = np.zeros((b.n, len(b.s)))
    slot_max = np.zeros((b.n, len(b.s)))
    for p in fg.POSITIONS:
        col = pos == p
        own_slot = b.open[p] > 0
        via_flex = (b.flex > 0) & ~own_slot if p in fg.FLEX_POSITIONS else np.zeros(b.n, dtype=bool)
        fits_slot[:, col] = (own_slot | via_flex)[:, None]
        slot_min[:, col] = np.where(own_slot, b.min_salary[p], b.min_flex)[:, None]
        slot_max[:, col] = np.where(own_slot, b.max_salary[p], b.max_flex)[:, None]
    salary = b.s.salary[None, :]
    ok = salary <= (b.budget - b.min_cost())[:, None] + slot_min
    if left_at_most is not None:
        ok &= salary >= (b.budget - left_at_most - b.max_cost())[:, None] + slot_max
    return fits_slot & ok & ~b.chosen & ~b.banned


def test_fits_agrees_with_the_reference_rule_through_a_whole_lineup(slate):
    rng = np.random.default_rng(11)
    n = 300
    b = fg._Batch(slate, n)
    left = rng.choice(np.asarray(RATES.unspent, dtype=float), size=n)
    b.banned[:, rng.integers(0, len(slate), size=20)] = True
    weights = np.maximum(slate.targets(), 1e-6)
    for _ in range(fg.LINEUP_SIZE):
        for bound in (None, left):
            np.testing.assert_array_equal(b.fits(bound), _reference_fits(b, bound))
        pick = fg._pick(weights, b.fits(left) | b.fits(), rng)
        b.take(b.rows, pick)
