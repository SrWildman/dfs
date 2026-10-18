"""The shipped team-shape table (QB1-TE1 by the tight end's target share): the live feature (no leakage), the
fit, the layer on the pooled table, the simulator default, and the committed file."""

from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from dfs.sim import correlation, shape_table
from dfs.sim.script import lookup_from_rhos
from dfs.sim.script_shape import ShapeLookup
from dfs.sim.simulate import PlayerSpec, correlation_matrix, simulate_lineups

TEST = shape_table.SHIPPED_TESTS[0]


def _frame(n_games=1500, seed=0):
    """QB1 and TE1 per team; the TE's `tshare` sets how much he moves with the QB (0.05 low .. 0.55 high)."""
    rng = np.random.default_rng(seed)
    rows = []
    for g in range(n_games):
        for team, opp in (("AAA", "BBB"), ("BBB", "AAA")):
            share = float(rng.uniform(0.05, 0.25))
            rho = 0.05 + 2.0 * (share - 0.05)
            qb, noise = rng.standard_normal(2)
            te = rho * qb + np.sqrt(1 - rho**2) * noise
            for role, z, ts in (("QB1", qb, np.nan), ("TE1", te, share)):
                rows.append(
                    {
                        "game_id": f"G{g}",
                        "season": 2014 + g % 12,
                        "team": team,
                        "opp": opp,
                        "role": role,
                        "z": z,
                        "tshare": ts,
                    }
                )
    return pd.DataFrame(rows)


def test_the_shipped_list_is_the_one_pair_and_nothing_else():
    assert shape_table.SHIPPED_TESTS == (("same_team", "QB1", "TE1", "b", "tshare"),)


def test_the_live_feature_is_the_ratio_of_window_sums_over_the_previous_games_of_the_season():
    def row(t, season, week, targets, team_targets):
        # two teammates so the team's targets are the sum of both
        return [
            {"gsis_id": "te", "t": t, "season": season, "week": week, "game_id": f"G{t}", "team": "AAA",
             "targets": targets},
            {"gsis_id": "wr", "t": t, "season": season, "week": week, "game_id": f"G{t}", "team": "AAA",
             "targets": team_targets - targets},
        ]  # fmt: skip

    games = [(1, 5, 20), (2, 2, 10), (3, 3, 10), (4, 10, 20), (5, 99, 99)]
    pg = pd.DataFrame([r for t, a, b in games for r in row(t, 2024, t, a, b)])
    pg = pd.concat([pg, pd.DataFrame(row(6, 2025, 1, 4, 8))], ignore_index=True)
    out = shape_table.live_tshare(SimpleNamespace(player_games=pg))
    te = out[out["gsis_id"] == "te"].set_index(["season", "week"])["tshare"]
    assert np.isnan(te[(2024, 1)])  # no earlier game this season: blank, as `Tgt%` is in week 1
    assert te[(2024, 2)] == pytest.approx(5 / 20)
    assert te[(2024, 3)] == pytest.approx(7 / 30)  # a ratio of sums, not a mean of 0.25 and 0.2
    assert te[(2024, 4)] == pytest.approx(10 / 40)
    assert te[(2024, 5)] == pytest.approx(15 / 40)  # games 2-4: a three-game window, not the game itself
    assert np.isnan(te[(2025, 1)])  # the new season starts blank; last year's share is not carried over


def test_the_feature_never_sees_the_game_it_is_attached_to():
    base = pd.DataFrame(
        [
            {
                "gsis_id": "te",
                "t": t,
                "season": 2024,
                "week": t,
                "game_id": f"G{t}",
                "team": "AAA",
                "targets": n,
            }
            for t, n in enumerate((4, 6, 5, 7), start=1)
        ]
    )
    bumped = base.assign(targets=base["targets"].where(base["t"] != 4, 40))
    a = shape_table.live_tshare(SimpleNamespace(player_games=base))
    b = shape_table.live_tshare(SimpleNamespace(player_games=bumped))
    assert a["tshare"].iloc[3] == pytest.approx(b["tshare"].iloc[3])  # week 4's own targets are not in it


def test_the_fit_has_one_row_per_third_and_the_correlation_rises_with_the_share():
    table = shape_table.fit_shape(_frame())
    assert (
        list(table["bucket"]) == ["low", "mid", "high"] and list(table.columns) == shape_table.SHAPE_COLUMNS
    )
    assert table["r"].is_monotonic_increasing and table["r"].iloc[2] - table["r"].iloc[0] > 0.25
    assert (
        table["n"].sum() == 3000
        and table["cut_lo"].nunique() == 1
        and table["cut_lo"].iloc[0] < table["cut_hi"].iloc[0]
    )


def test_the_layer_moves_the_pair_with_the_tight_ends_share_and_nothing_else():
    pooled = lookup_from_rhos({("same_team", "QB1", "TE1"): 0.25, ("same_team", "QB1", "WR1"): 0.30})
    lk = shape_table.shape_lookup(pooled, shape_table.fit_shape(_frame()))
    assert lk.rho("same_team", "QB1", "TE1") == pytest.approx(0.25)  # share unknown: pooled
    low = lk.rho("same_team", "QB1", "TE1", {}, {"tshare": 0.06})
    high = lk.rho("same_team", "QB1", "TE1", {}, {"tshare": 0.24})
    assert low < 0.25 < high
    # the five other receiver pairs, and the QB's own `tshare`, are the pooled value
    assert lk.rho("same_team", "QB1", "WR1", {}, {"tshare": 0.24}) == pytest.approx(0.30)
    assert lk.rho("same_team", "QB1", "TE1", {"tshare": 0.24}, {}) == pytest.approx(0.25)
    assert lk.rho("opp", "QB1", "TE1", {}, {"tshare": 0.24}) == pytest.approx(0.0)


def test_a_table_that_names_a_pair_that_is_not_shipped_is_refused():
    table = shape_table.fit_shape(_frame()).assign(role_b="WR1")
    with pytest.raises(shape_table.ShapeTableError, match="not shipped"):
        shape_table.shape_lookup(lookup_from_rhos({}), table)
    with pytest.raises(shape_table.ShapeTableError, match="missing columns"):
        shape_table.shape_lookup(lookup_from_rhos({}), table.drop(columns=["cut_hi"]))


def _directory(tmp_path, with_shape=True):
    pooled = pd.DataFrame(
        [("same_team", "QB1", "TE1", "all", 0.25, 5000, 0.2, 0.3)], columns=correlation.CSV_COLUMNS
    )
    pooled.to_csv(tmp_path / correlation.CORRELATIONS_FILE, index=False)
    if with_shape:
        shape_table.fit_shape(_frame()).to_csv(tmp_path / shape_table.SHAPE_FILE, index=False)
    shape_table.default_lookup.cache_clear()
    return tmp_path


def test_the_default_lookup_is_the_pooled_table_with_the_layer_on_top_when_the_file_exists(tmp_path):
    (tmp_path / "with").mkdir()
    (tmp_path / "without").mkdir()
    layered = shape_table.default_lookup(_directory(tmp_path / "with"))
    plain = shape_table.default_lookup(_directory(tmp_path / "without", with_shape=False))
    assert isinstance(layered, ShapeLookup) and layered.rho("same_team", "QB1", "TE1") == pytest.approx(0.25)
    assert isinstance(plain, correlation.CorrelationLookup) and plain.rho("same_team", "QB1", "TE1") == 0.25
    shape_table.default_lookup.cache_clear()


def test_saving_the_shape_table_clears_the_cached_default(tmp_path):
    _directory(tmp_path, with_shape=False)
    assert isinstance(shape_table.default_lookup(tmp_path), correlation.CorrelationLookup)
    shape_table.save_shape(shape_table.fit_shape(_frame()), tmp_path)
    assert isinstance(shape_table.default_lookup(tmp_path), ShapeLookup)
    shape_table.default_lookup.cache_clear()


def _spec(id_, position, role, tshare=None, team="AAA"):
    return PlayerSpec(
        id_, position, team, "BBB", "G1", role, 12.0 if position != "QB" else 20.0, 5000, tshare=tshare
    )


def test_the_simulator_reads_a_tight_ends_share_off_his_spec_by_default():
    qb = _spec("qb", "QB", "QB1")
    lookup = shape_table.default_lookup()
    low, high, unknown = (_spec("te", "TE", "TE1", s) for s in (0.05, 0.30, None))
    r = {
        k: correlation_matrix([qb, te], lookup)[0][0, 1]
        for k, te in (("low", low), ("high", high), ("none", unknown))
    }
    assert r["low"] < r["none"] < r["high"]
    assert r["none"] == pytest.approx(correlation.load_correlations().rho("same_team", "QB1", "TE1"))
    # a lineup holding both sees it in the spread of its score: a bigger share, a wider range
    spread = {}
    for k, te in (("low", low), ("high", high)):
        spread[k] = simulate_lineups([[qb, te]], n_sims=20000, seed=1).lineups[0].sd
    assert spread["high"] > spread["low"]


def test_the_committed_table_has_exactly_the_shipped_pair_and_the_effect_the_write_up_reports():
    table = pd.read_csv(correlation.SIM_DIR / shape_table.SHAPE_FILE)
    assert list(table.columns) == shape_table.SHAPE_COLUMNS
    assert {
        tuple(t) for t in table[["relation", "role_a", "role_b", "side", "feature"]].itertuples(index=False)
    } == set(shape_table.SHIPPED_TESTS)
    assert list(table["bucket"]) == ["low", "mid", "high"] and table["r"].is_monotonic_increasing
    assert 0.1 < table["r"].iloc[0] < 0.25 and 0.28 < table["r"].iloc[2] < 0.4
    assert table["cut_lo"].iloc[0] == pytest.approx(0.1287, abs=0.002)
