"""OurDST (`dfs.model.dst`): the DK sum, strictly-earlier features, the league levels, fit and predict."""

import math

import numpy as np
import pandas as pd
import pytest

from dfs.dk_scoring import score_dst_actual_row
from dfs.model import dst
from dfs.model.dst_study import Paired, paired, week_metrics

TEAMS = ["AAA", "BBB", "CCC", "DDD", "EEE", "FFF"]


def _synthetic(seasons=(2014, 2015, 2016, 2017), weeks=8, seed=0) -> tuple[pd.DataFrame, pd.DataFrame]:
    """A small league: every week the six teams play three games; outcomes loosely follow the line."""
    rng = np.random.default_rng(seed)
    rows, qbs = [], []
    for season in seasons:
        for week in range(1, weeks + 1):
            order = rng.permutation(TEAMS)
            for g in range(3):
                home, away = order[2 * g], order[2 * g + 1]
                total = rng.uniform(38, 52)
                spread = rng.uniform(-7, 7)
                for team, opp, side in ((home, away, 1), (away, home, 0)):
                    implied = total / 2 + (spread if side else -spread) / 2
                    opp_implied = total - implied
                    dropbacks = int(rng.integers(28, 45))
                    sacks = int(rng.poisson(0.065 * dropbacks))
                    ints = int(rng.poisson(0.8))
                    fum = int(rng.poisson(0.5))
                    pa = int(max(0, rng.normal(opp_implied, 9)))
                    rows.append(
                        {
                            "team": team,
                            "opp": opp,
                            "season": season,
                            "week": week,
                            "t": season * 100 + week,
                            "game_id": f"{season}_{week}_{g}",
                            "sacks": sacks,
                            "qb_hits": sacks + int(rng.poisson(3)),
                            "ints": ints,
                            "fum_rec": fum,
                            "takeaways": ints + fum,
                            "def_td": int(rng.random() < 0.12),
                            "misc": int(rng.random() < 0.05),
                            "points_allowed": pa,
                            "opp_dropbacks": dropbacks,
                            "opp_attempts": dropbacks - sacks,
                            "opp_plays": dropbacks + int(rng.integers(20, 32)),
                            "opp_giveaways": ints + fum,
                            "opp_qb_id": f"QB-{opp}",
                            "implied": implied,
                            "spread": spread if side else -spread,
                            "total": total,
                            "home": side,
                            "dome": 0,
                        }
                    )
                    qbs.append(
                        {
                            "qb_id": f"QB-{opp}",
                            "season": season,
                            "week": week,
                            "t": season * 100 + week,
                            "attempts": dropbacks - sacks,
                            "dropbacks": dropbacks,
                            "sacks": sacks,
                            "ints": ints,
                            "fumbles": fum,
                        }
                    )
    games = pd.DataFrame(rows)
    pa_score = dst.points_allowed_score(games["points_allowed"])
    games["dk"] = games["sacks"] + 2 * games["takeaways"] + 6 * games["def_td"] + 2 * games["misc"] + pa_score
    return games[dst.DST_GAME_COLUMNS], pd.DataFrame(qbs)


@pytest.fixture(scope="module")
def league():
    return _synthetic()


def test_vectorised_dk_points_equal_the_reference_scorer():
    rng = np.random.default_rng(3)
    n = 300
    fields = pd.DataFrame(
        {
            "sack": rng.integers(0, 8, n),
            "def_int": rng.integers(0, 4, n),
            "fum_rec": rng.integers(0, 3, n),
            "def_td": rng.integers(0, 2, n),
            "safety": rng.integers(0, 2, n),
            "blocked_kick": rng.integers(0, 2, n),
            "two_pt_return": rng.integers(0, 2, n),
            "points_allowed": rng.integers(0, 50, n),
        }
    ).astype(float)
    expected = fields.apply(lambda r: score_dst_actual_row(r.to_dict()), axis=1)
    pd.testing.assert_series_equal(dst._dk_points(fields), expected, check_names=False)


@pytest.mark.parametrize(
    ("pa", "points"), [(0, 10.0), (1, 7.0), (6, 7.0), (7, 4.0), (20, 1.0), (21, 0.0), (34, -1.0), (35, -4.0)]
)
def test_points_allowed_tiers(pa, points):
    assert dst.points_allowed_score(pd.Series([pa])).iloc[0] == points


def test_ewm_state_decays_by_game_and_by_season():
    table = pd.DataFrame(
        {"team": ["A"] * 3, "season": [2020, 2020, 2021], "t": [202001, 202002, 202101], "x": [1.0, 1.0, 1.0]}
    )
    rec = dst.Recency(half_life=1.0, carry=0.5)
    state = ewm_state = dst.ewm_state(table, "team", ["x"], rec)
    assert list(state["x"]) == pytest.approx([1.0, 1.5, 1.5 * 0.5 * 0.5 + 1.0])
    assert list(ewm_state["w_games"]) == pytest.approx([1.0, 1.5, 1.375])


def test_state_before_never_sees_the_rows_own_game_and_carries_across_a_season():
    table = pd.DataFrame({"team": ["A"] * 2, "season": [2020, 2020], "t": [202001, 202002], "x": [2.0, 4.0]})
    rec = dst.Recency(half_life=1e9, carry=0.5)
    state = dst.ewm_state(table, "team", ["x"], rec)
    rows = pd.DataFrame(
        {
            "team": ["A", "A", "A", "B"],
            "season": [2020, 2020, 2021, 2020],
            "t": [202001, 202002, 202101, 202002],
        }
    )
    got = dst.state_before(rows, state, row_key="team", state_key="team", recency=rec)
    # week 1: nothing earlier; week 2: week 1 only; next season: both, times the carry; B: no games.
    assert list(got["x"]) == pytest.approx([0.0, 2.0, 3.0, 0.0])


def test_features_ignore_the_week_itself_and_everything_after(league):
    games, qbs = league
    priors = dst.Priors.measure(games)
    week = games[games["t"] == 201603].reset_index(drop=True)
    clean = dst.dst_features(week, games, qbs, priors)
    poisoned = games.copy()
    later = poisoned["t"] >= 201603
    for col in ("sacks", "qb_hits", "points_allowed", "takeaways", "def_td", "misc", "dk"):
        poisoned.loc[later, col] = 999
    poisoned_qbs = qbs.copy()
    poisoned_qbs.loc[poisoned_qbs["t"] >= 201603, "sacks"] = 999
    dirty = dst.dst_features(week, poisoned, poisoned_qbs, priors)
    pd.testing.assert_frame_equal(clean, dirty)


def test_league_levels_are_strictly_earlier_and_cover_a_future_week(league):
    games, _ = league
    levels = dst.league_levels(games, dst.DstParams(league_half_life=1e12))
    first = levels.iloc[0]
    assert math.isnan(first["lg_takeaways"])
    at = levels[levels["t"] == 201501].iloc[0]
    assert at["lg_takeaways"] == pytest.approx(games[games["t"] < 201501]["takeaways"].mean())
    assert levels["t"].iloc[-1] > games["t"].max()
    assert levels["lg_def_td"].iloc[-1] == pytest.approx(games["def_td"].mean())


def test_fit_and_predict_give_sane_means_and_probabilities(league):
    games, qbs = league
    model = dst.fit(games, qbs, (2014, 2016))
    assert model.fit_seasons == (2015, 2016)
    test = games[games["season"] == 2017].reset_index(drop=True)
    pred, draws = dst.predict(model, test, games, qbs)
    assert pred["dst_mean"].notna().all()
    assert pred["dst_mean"].between(0, 20).all()
    assert draws.shape == (len(test), len(model.residuals))
    # the draws are whole DK points and their mean sits on the projection
    assert np.allclose(draws, np.round(draws))
    assert abs(np.mean(draws.mean(axis=1) - pred["dst_mean"].to_numpy())) < 0.5
    p10 = dst.prob_at_least(draws, 10.0)
    p14 = dst.prob_at_least(draws, 14.0)
    assert ((p10 >= p14) & (p10 <= 1) & (p14 >= 0)).all()


def test_predict_leaves_a_row_without_a_line_blank(league):
    games, qbs = league
    model = dst.fit(games, qbs, (2014, 2016))
    rows = games[games["t"] == 201701].reset_index(drop=True).copy()
    rows.loc[0, ["implied", "total"]] = np.nan
    pred, draws = dst.predict(model, rows, games, qbs)
    assert math.isnan(pred.loc[0, "dst_mean"])
    assert np.isnan(draws[0]).all()
    assert math.isnan(dst.prob_at_least(draws, 10.0)[0])
    assert pred.loc[1:, "dst_mean"].notna().all()


def test_paired_ship_bar_needs_two_se_and_most_weeks():
    weeks = range(1, 9)
    rows = []
    for w in weeks:
        rows.append({"season": 2026, "week": w, "Source": "OurDST", "MAE": 4.0, "Rho": 0.30})
        rows.append({"season": 2026, "week": w, "Source": "TFFB", "MAE": 4.2 + 0.01 * w, "Rho": 0.20})
    weekly = pd.DataFrame(rows)
    mae = paired(weekly, "MAE", "OurDST", "TFFB")
    assert mae.better == 8 and mae.passes
    noisy = weekly.copy()
    noisy.loc[(noisy["Source"] == "TFFB") & (noisy["week"] % 2 == 0), "MAE"] = 3.0
    assert not paired(noisy, "MAE", "OurDST", "TFFB").passes
    assert Paired("Rho", "a", "b", 0.05, 0.01, 6, 2).passes is False


def test_week_metrics_scores_only_rows_every_column_has():
    frame = pd.DataFrame(
        {
            "season": 2026,
            "week": 1,
            "dk": [1.0, 5.0, 9.0, 3.0] * 2,
            "a": [1.0, 5.0, 9.0, 3.0] * 2,
            "b": [2.0, 4.0, 8.0, np.nan] * 2,
        }
    )
    got = week_metrics(frame, ["a", "b"]).set_index("Source")
    assert got.loc["a", "n"] == 6
    assert got.loc["a", "MAE"] == 0.0
    assert math.isnan(got.loc["a", "Rho"])  # 6 rows is under the rank-correlation minimum
