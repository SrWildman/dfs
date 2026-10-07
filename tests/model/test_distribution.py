"""The distribution engine: monotone quantiles, probabilities that fall as the bar rises, sane floor/ceiling,
whole-number DST scoring, and the committed tables."""

import numpy as np
import pandas as pd
import pytest

from dfs.model import distribution as D
from dfs.model.history import POSITIONS


def _synthetic_oof(n: int = 8000, seed: int = 0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    pred = rng.uniform(2.0, 26.0, n)
    # outcomes spread around the projection, wider (and sometimes 0) at the low end
    ratio = rng.lognormal(mean=-0.05, sigma=0.55 - 0.012 * pred, size=n)
    dk = np.round(pred * ratio, 1)
    dk[(pred < 6) & (rng.random(n) < 0.12)] = 0.0
    return pd.DataFrame({"pred": pred, "dk": dk})


@pytest.fixture(scope="module")
def tables() -> pd.DataFrame:
    return pd.concat([D.build_tables(_synthetic_oof(), pos) for pos in POSITIONS], ignore_index=True)


def test_every_bucket_table_has_monotone_quantiles_and_valid_zero_share(tables):
    ratios = tables[D.LEVEL_COLUMNS].to_numpy()
    assert (np.diff(ratios, axis=1) >= 0).all()
    assert tables["p_zero"].between(0, 1).all()
    for position in POSITIONS:
        sub = tables[tables["position"] == position]
        assert len(sub) == len(D.BUCKET_EDGES[position]) + 1
        assert sub["center"].is_monotonic_increasing
        assert sub["n"].sum() == len(_synthetic_oof())


def test_the_required_quantile_grid_is_present(tables):
    required = [f"q{round(0.05 * i, 2):g}" for i in range(1, 20)]
    assert set(required) <= set(tables.columns)


@pytest.mark.parametrize("position", POSITIONS)
@pytest.mark.parametrize("projection", [1.0, 4.5, 8.0, 12.3, 17.0, 24.0, 60.0])
def test_outcome_quantiles_are_monotone_for_any_projection(tables, position, projection):
    q = D.outcome_distribution(position, projection, tables=tables)
    assert list(q.levels) == sorted(q.levels)
    assert list(q.values) == sorted(q.values)
    assert 0 <= q.p_zero <= 1


@pytest.mark.parametrize("position", POSITIONS)
def test_prob_at_least_falls_as_the_threshold_rises(tables, position):
    for projection in (5.0, 9.0, 14.0, 20.0):
        thresholds = np.linspace(0.1, 70, 120)
        p = D.prob_at_least_many(position, projection, thresholds, tables=tables)
        assert (np.diff(p) <= 1e-12).all()
        assert ((p >= 0) & (p <= 1)).all()
        assert p[0] > 0.9 and p[-1] < 0.01


@pytest.mark.parametrize("position", ["QB", "RB", "WR", "TE"])
def test_floor_is_below_the_projection_and_ceiling_above_it(tables, position):
    for projection in (5.0, 8.0, 12.0, 17.0, 22.0):
        floor, ceil = D.floor_ceiling(position, projection, tables=tables)
        assert 0 <= floor < projection < ceil


def test_prob_at_least_agrees_with_the_quantiles_it_was_built_from(tables):
    q = D.outcome_distribution("RB", 12.0, tables=tables)
    for level in (0.1, 0.25, 0.5, 0.75, 0.9):
        assert D.prob_at_least("RB", 12.0, q.at(level), tables=tables) == pytest.approx(1 - level, abs=0.01)


def test_at_a_bucket_centre_the_outcomes_are_that_buckets_table_scaled_by_the_projection(tables):
    sub = tables[tables["position"] == "WR"].sort_values("center")
    row = sub.iloc[2]
    q = D.outcome_distribution("WR", row["center"], tables=tables)
    assert np.allclose(q.values, row[D.LEVEL_COLUMNS].to_numpy(dtype=float) * row["center"])


def test_between_two_centres_the_table_is_the_blend_of_both(tables):
    sub = tables[tables["position"] == "RB"].sort_values("center")
    a, b = sub.iloc[1], sub.iloc[2]
    mid = (a["center"] + b["center"]) / 2
    ratios = D.ratio_matrix("RB", [mid], tables=tables)[0]
    assert np.allclose(
        ratios, (a[D.LEVEL_COLUMNS].to_numpy(dtype=float) + b[D.LEVEL_COLUMNS].to_numpy(dtype=float)) / 2
    )


def test_beyond_the_outermost_centres_the_end_table_is_used(tables):
    sub = tables[tables["position"] == "TE"].sort_values("center")
    assert np.allclose(
        D.ratio_matrix("TE", [0.5], tables=tables)[0], sub.iloc[0][D.LEVEL_COLUMNS].to_numpy(dtype=float)
    )
    assert np.allclose(
        D.ratio_matrix("TE", [400.0], tables=tables)[0], sub.iloc[-1][D.LEVEL_COLUMNS].to_numpy(dtype=float)
    )


@pytest.mark.parametrize("position", POSITIONS)
def test_a_projection_of_zero_or_less_returns_zeros(tables, position):
    for projection in (0.0, -3.0):
        q = D.outcome_distribution(position, projection, tables=tables)
        assert set(q.values) == {0.0} and q.p_zero == 1.0
        assert D.floor_ceiling(position, projection, tables=tables) == (0.0, 0.0)
        assert D.prob_at_least(position, projection, 5.0, tables=tables) == 0.0


def test_a_missing_projection_is_blank_not_zero(tables):
    q = D.outcome_distribution("QB", float("nan"), tables=tables)
    assert np.isnan(q.values).all()
    assert all(np.isnan(x) for x in D.floor_ceiling("QB", float("nan"), tables=tables))
    assert np.isnan(D.prob_at_least("QB", float("nan"), 10.0, tables=tables))
    assert np.isnan(D.prob_at_least("QB", 15.0, float("nan"), tables=tables))


def test_a_threshold_of_zero_or_less_is_always_met(tables):
    assert D.prob_at_least("WR", 9.0, 0.0, tables=tables) == 1.0
    assert D.prob_at_least("WR", 9.0, -1.0, tables=tables) == 1.0


def test_dst_scores_whole_numbers_so_a_whole_threshold_is_read_at_half_a_point_below(tables):
    # P(X >= 3) for an integer X is P(X >= 2.5) on the continuous CDF; P(X >= 3.2) = P(X >= 4) = P(>= 3.5)
    p = lambda t: D.prob_at_least("DST", 7.0, t, tables=tables)  # noqa: E731
    assert p(3.0) == pytest.approx(p(2.5)) and p(3.0) > p(3.2)
    assert p(3.2) == pytest.approx(p(3.5))
    # offense is not integer-valued: no correction
    r = lambda t: D.prob_at_least("RB", 7.0, t, tables=tables)  # noqa: E731
    assert r(3.0) != pytest.approx(r(2.5), abs=1e-9)


def test_vectorised_and_scalar_forms_agree(tables):
    proj = np.array([4.0, 9.0, 15.0, 22.0, 0.0, np.nan])
    thr = np.array([6.0, 12.0, 20.0, 30.0, 5.0, 5.0])
    many = D.prob_at_least_many("WR", proj, thr, tables=tables)
    assert np.allclose(
        many[:5], [D.prob_at_least("WR", p, t, tables=tables) for p, t in zip(proj[:5], thr[:5], strict=True)]
    )
    assert np.isnan(many[5])
    floors, ceils = D.floor_ceiling_many("WR", proj, tables=tables)
    for i in range(5):
        assert (floors[i], ceils[i]) == pytest.approx(D.floor_ceiling("WR", proj[i], tables=tables))
    assert np.isnan(floors[5])


def test_a_bucket_with_no_rows_is_an_error_not_a_silent_gap():
    oof = pd.DataFrame({"pred": [3.0, 4.0, 5.0], "dk": [2.0, 4.0, 6.0]})
    with pytest.raises(D.DistributionError, match="no rows"):
        D.build_tables(oof, "RB")


def test_tables_round_trip_and_a_missing_file_says_to_train(tables, tmp_path):
    path = D.save_tables(tables, tmp_path)
    assert path.name == "distribution.csv"
    loaded = D.load_tables(tmp_path)
    assert list(loaded.columns) == list(tables.columns)
    assert np.allclose(loaded[D.LEVEL_COLUMNS].to_numpy(), tables[D.LEVEL_COLUMNS].to_numpy())
    with pytest.raises(D.DistributionError, match="dfs model train"):
        D.load_tables(tmp_path / "nowhere")


def test_the_committed_tables_are_complete_and_well_formed():
    shipped = D.load_tables()
    for position in POSITIONS:
        sub = shipped[shipped["position"] == position]
        assert len(sub) == len(D.BUCKET_EDGES[position]) + 1
        assert (np.diff(sub[D.LEVEL_COLUMNS].to_numpy(), axis=1) >= 0).all()
        q = D.outcome_distribution(position, 10.0)
        assert list(q.values) == sorted(q.values) and q.values[-1] > 10.0
