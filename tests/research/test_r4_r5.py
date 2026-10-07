"""R4 weather helpers and R5 context flags."""

import numpy as np
import pandas as pd
import pytest

from dfs.research import r4_weather as r4
from dfs.research import r5_checks as r5
from dfs.research.stats import cluster_diff, cluster_mean, ols_cluster


def test_weather_text_parsing():
    r = r4.parse_weather_text("Cloudy, rain Temp: 69° F, Humidity: 87%, Wind: SW 16 mph")
    assert r == {"wind": 16.0, "temp": 69.0, "precip": "rain"}
    assert r4.parse_weather_text("Sunny Temp: 54° F, Humidity: 26%, Wind: NE 6 mph")["precip"] == "none"
    assert r4.parse_weather_text("Light snow Temp: 28° F, Wind: N 12 mph")["precip"] == "snow"
    assert r4.parse_weather_text("Chance of rain Temp: 60° F, Wind: S 3 mph")["precip"] == "none"
    assert r4.parse_weather_text("Temp: 81° F, Humidity: 40%, Wind: South 13 mph")["wind"] == 13.0
    assert np.isnan(r4.parse_weather_text(None)["wind"])
    assert np.isnan(r4.parse_weather_text("Controlled climate")["wind"])


def test_wind_bin_edges():
    wind = pd.Series([0, 9, 9.9, 10, 14, 15, 19, 20, 24, 25, 40])
    assert r4.bin_label(wind, r4.WIND_BINS).tolist() == [
        "0-9",
        "0-9",
        "0-9",
        "10-14",
        "10-14",
        "15-19",
        "15-19",
        "20-24",
        "20-24",
        "25+",
        "25+",
    ]


def test_game_weather_falls_back_to_the_text_when_the_schedule_has_no_wind():
    games = pd.DataFrame(
        {
            "game_id": ["g1", "g2", "g3"],
            "season": 2022,
            "week": 1,
            "roof": ["outdoors", "outdoors", "dome"],
            "wind": [np.nan, 7.0, np.nan],
            "temp": [np.nan, 50.0, np.nan],
            "total_line": [45.0, 44.0, 50.0],
            "home_score": [20, 24, 30],
            "away_score": [17, 10, 20],
        }
    )
    pbp = pd.DataFrame(
        {
            "game_id": ["g1", "g1", "g2", "g3"],
            "weather": ["Rain Temp: 61° F, Humidity: 80%, Wind: W 18 mph"] * 2
            + ["Sunny Temp: 70° F, Wind: N 2 mph", None],
            "pass": [1, 0, 1, 1],
            "rush": [0, 1, 0, 0],
            "pass_oe": [1.0, -1.0, 2.0, 0.0],
        }
    )
    gw = r4.game_weather(games, pbp).set_index("game_id")
    assert gw.loc["g1", "wind_mph"] == 18.0 and gw.loc["g1", "wind_source"] == "text"
    assert gw.loc["g2", "wind_mph"] == 7.0 and gw.loc["g2", "wind_source"] == "schedule"  # schedule wins
    assert gw.loc["g1", "precip"] == "rain" and gw.loc["g1", "temp_f"] == 61.0
    assert bool(gw.loc["g1", "outdoor"]) and not bool(gw.loc["g3", "outdoor"])
    assert gw.loc["g1", "total_pts"] == 37


def _qb_bins(means_by_bin):
    rows = []
    for _, _, label in r4.WIND_BINS:
        m = means_by_bin.get(label, 0.0)
        cell = {"n": 100, "mean_resid": m, "lo90": m - 0.5, "hi90": m + 0.5}
        rows.append({"position": "QB", "bin": label, "all": cell, "fit": cell, "test": cell})
    return rows


def _sweep(fit_test_by_threshold):
    rows = []
    for t in r4.THRESHOLDS:
        d = fit_test_by_threshold.get(t, 0.0)
        cell = {"n_flagged": 100, "diff": d, "lo90": d - 0.4, "hi90": d + 0.4}
        rows.append({"threshold_mph": t, "fit": cell, "test": cell, "all": cell})
    return {"positions": {"QB": rows}}


def test_threshold_recommendation_needs_both_periods():
    bins = _qb_bins({"15-19": -1.2})
    out = r4.recommend_threshold(bins, _sweep({15: -1.3, 17: -1.4, 20: -1.5}))
    assert out["first_bin_meeting_rule_all"] == "15-19"
    assert out["qualifying_thresholds"] == [15, 17, 20] and out["recommended_mph"] == 15
    # an effect that is not significant (interval includes 0) does not qualify
    sweep = _sweep({15: -1.3})
    sweep["positions"]["QB"][2]["fit"]["hi90"] = 0.2
    assert r4.recommend_threshold(bins, sweep)["recommended_mph"] is None
    # no effect anywhere
    out = r4.recommend_threshold(_qb_bins({}), _sweep({}))
    assert out["recommended_mph"] is None and out["first_bin_meeting_rule_all"] is None


def test_ols_slope_and_clustered_stats():
    rng = np.random.default_rng(0)
    x = rng.uniform(0, 30, 3000)
    y = -0.2 * x + rng.normal(0, 2, 3000)
    s = r4.ols_slope(x, y)
    assert s["slope"] == pytest.approx(-0.2, abs=0.02) and s["lo90"] < -0.2 < s["hi90"]
    m = cluster_mean([1.0, 3.0], ["a", "b"])
    assert m["mean"] == 2.0 and m["n"] == 2
    d = cluster_diff([5.0, 5.0, 1.0, 1.0], [True, True, False, False], ["a", "b", "c", "d"])
    assert d["diff"] == 4.0 and d["n_flag"] == 2
    assert np.isnan(cluster_diff([1.0, 2.0], [True, True], ["a", "b"])["diff"])  # nothing to compare to
    fit = ols_cluster(
        np.array([[0.0], [1.0], [2.0], [3.0]]), np.array([1.0, 3.0, 5.0, 7.0]), np.array([1, 2, 3, 4])
    )
    assert fit["coef"] == pytest.approx([1.0, 2.0])


def _schedule():
    rows = [
        # AAA: away, away (back to back), home, away (not back to back), neutral, away (chain broken)
        ("2023_01_AAA_X", 1, "X", "AAA", "Home", 7, 7, 0),
        ("2023_02_AAA_Y", 2, "Y", "AAA", "Home", 7, 7, 1),
        ("2023_03_Z_AAA", 3, "AAA", "Z", "Home", 7, 4, 0),
        ("2023_04_AAA_W", 4, "W", "AAA", "Home", 7, 7, 0),
        ("2023_05_AAA_N", 5, "N", "AAA", "Neutral", 7, 7, 0),
        ("2023_06_AAA_V", 6, "V", "AAA", "Home", 7, 7, 0),
    ]
    return pd.DataFrame(
        [
            {
                "game_id": gid,
                "season": 2023,
                "week": week,
                "home_team": home,
                "away_team": away,
                "location": loc,
                "home_rest": hr,
                "away_rest": ar,
                "div_game": div,
            }
            for gid, week, home, away, loc, hr, ar, div in rows
        ]
    )


def test_context_flags():
    ctx = r5.team_game_context(_schedule())
    a = ctx[ctx["team"] == "AAA"].set_index("game_id")
    # away, away, home, away, neutral (NaN), away
    assert a["home"].fillna(-1.0).tolist() == [0.0, 0.0, 1.0, 0.0, -1.0, 0.0]
    # week 2 follows a road game; week 4 follows a home game; week 6 follows a neutral-site game
    assert a["back_to_back_road"].tolist() == [0.0, 1.0, 0.0, 0.0, 0.0, 0.0]
    assert a["divisional"].tolist() == [0, 1, 0, 0, 0, 0]
    # week 3: AAA hosts on 7 days, the visiting Z has 4
    assert a.loc["2023_03_Z_AAA", "short_week"] == 0.0
    assert ctx[ctx["team"] == "Z"].iloc[0]["short_week"] == 1.0
    assert (
        ctx[ctx["game_id"] == "2023_05_AAA_N"]["home"].isna().all()
    )  # a neutral site is neither side's home
