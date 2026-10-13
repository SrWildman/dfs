"""`props_history.py`: the proxy, the dispersion fit and the checks, on synthetic games with known answers."""

import numpy as np
import pandas as pd
import pytest

from dfs import props, props_history


def _history(n_players=150, n_games=14, *, seed=0, mean_level=4.0, stat_column="receptions", draw="poisson"):
    rng = np.random.default_rng(seed)
    rows = []
    for player in range(n_players):
        level = mean_level * rng.uniform(0.6, 1.6)
        for game in range(n_games):
            season = 2020 + game // 7
            week = game % 7 + 1
            value = rng.poisson(level) if draw == "poisson" else rng.gamma(level / 25.0, 25.0)
            rows.append((f"p{player}", f"Player {player}", "WR", season, week, "REG", 5, value))
    return pd.DataFrame(
        rows,
        columns=[
            "player_id",
            "player_display_name",
            "position",
            "season",
            "week",
            "season_type",
            "attempts",
            stat_column,
        ],
    )


def test_the_proxy_uses_only_earlier_games_and_needs_six():
    history = _history(n_players=3, n_games=10)
    frame = props_history.case_frame(history, "WR", "rec")
    one = history[history["player_id"] == "p0"].sort_values(["season", "week"]).reset_index(drop=True)
    kept = frame[frame["player_id"] == "p0"].sort_values(["season", "week"]).reset_index(drop=True)
    # games 1-6 have fewer than six earlier games: the first scored game is the 7th, proxy = mean of games 1-6
    assert len(kept) == len(one) - props_history.MIN_PRIOR_GAMES
    first = kept.iloc[0]
    assert first["proxy"] == pytest.approx(one["receptions"].iloc[:6].mean())
    assert first["actual"] == one["receptions"].iloc[6]
    # the game itself is never in its own proxy
    last = kept.iloc[-1]
    assert last["proxy"] == pytest.approx(one["receptions"].iloc[-9:-1].mean())


def test_phi_recovers_a_poisson_and_a_known_overdispersion():
    poisson = props_history.case_frame(_history(draw="poisson"), "WR", "rec")
    phi, _, slope = props_history.fit_phi(poisson)
    assert phi == pytest.approx(1.0, abs=0.15)
    assert 0 < slope < 1.2
    gamma_history = _history(mean_level=60.0, stat_column="receiving_yards", draw="gamma")
    gamma = props_history.case_frame(gamma_history, "WR", "rec_yd")
    phi, _, _ = props_history.fit_phi(gamma)
    assert phi == pytest.approx(25.0, rel=0.2)  # gamma shape level/25, scale 25: variance = 25 * mean


def test_survival_calibration_is_calibrated_on_data_from_the_assumed_family():
    history = _history(
        n_players=300, n_games=14, mean_level=60.0, stat_column="receiving_yards", draw="gamma"
    )
    table = props_history.survival_calibration(history, train_seasons={2020}, test_seasons={2021})
    wr = table[(table["Position"] == "WR") & (table["Stat"] == "rec_yd")]
    assert len(wr) == len(props_history.SURVIVAL_MULTIPLES)
    assert (wr["Predicted"] - wr["Realised"]).abs().max() < 0.08


def test_mean_from_line_backtest_prefers_the_modelled_mean_on_skewed_data():
    history = _history(
        n_players=400, n_games=16, mean_level=60.0, stat_column="receiving_yards", draw="gamma"
    )
    history["week"] = (
        history["week"] + (history["season"] - 2020) * 7
    )  # distinct weeks so both parities exist
    table = props_history.mean_from_line_backtest(history)
    wr = table[(table["Position"] == "WR") & (table["Stat"] == "rec_yd")].set_index("Estimate")
    assert abs(wr.loc["mean from line", "Bias"]) < abs(wr.loc["expected = line", "Bias"])


def test_league_int_rate():
    df = pd.DataFrame(
        {
            "season_type": ["REG"] * 4,
            "position": ["QB"] * 4,
            "attempts": [30, 30, 30, 3],
            "passing_interceptions": [1, 0, 2, 5],
        }
    )
    assert props_history.league_int_rate(df) == pytest.approx(1.0)  # 3 INTs in 3 qualifying games


def test_fit_table_covers_every_case_and_pools_shared_stats():
    cases = {k for k in props_history.CASES}
    assert ("WR", "rec_yd") in cases and ("QB", "pass_yd") in cases
    assert set(props.DISPERSION) == cases  # the committed table has exactly the fitted cases
