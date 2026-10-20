"""The Week 8 re-test of the QB1-receiver pairs: the bar (a held-out gain over 2 SE), the shuffled-feature
control and the report, on synthetic frames with a planted effect."""

import numpy as np
import pandas as pd

from dfs.sim import shape_retest


def make_frame(planted: bool, n_games: int = 3600, seed: int = 0) -> pd.DataFrame:
    """QB1 and WR1 per team across 2014-2025; when `planted` the WR's target share sets his correlation with
    the QB (0.05 at the lowest share up to 0.6 at the highest)."""
    rng = np.random.default_rng(seed)
    rows = []
    for g in range(n_games):
        season = 2014 + g % 12
        for team, opp in (("AAA", "BBB"), ("BBB", "AAA")):
            share = float(rng.uniform(0.05, 0.30))
            rho = 0.05 + 2.2 * (share - 0.05) if planted else 0.2
            qb, noise = rng.standard_normal(2)
            wr = rho * qb + np.sqrt(1 - rho**2) * noise
            for role, position, z, ts in (("QB1", "QB", qb, np.nan), ("WR1", "WR", wr, share)):
                rows.append(
                    {
                        "game_id": f"G{g}",
                        "season": season,
                        "week": 1 + g % 17,
                        "team": team,
                        "opp": opp,
                        "home": 1,
                        "role": role,
                        "position": position,
                        "gsis_id": f"{team}{g}{role}",
                        "pred": 10.0,
                        "dk": 10.0,
                        "z": z,
                        "spread": 0.0,
                        "implied": 22.0,
                        "total": 44.0,
                        "tshare": ts,
                    }
                )
    return pd.DataFrame(rows)


def test_a_planted_receiver_effect_clears_the_bar_and_beats_its_shuffles():
    result = shape_retest.run(make_frame(True), n_shuffle=8)
    row = result["table"].set_index("pair").loc["QB1-WR1"]
    assert row["ships"]
    assert row["gain_per_1000"] > shape_retest.BAR_SE * row["se"]
    assert row["shuffled_as_good"] == 0.0
    assert result["test_seasons"] == [2024, 2025]


def test_without_an_effect_nothing_ships():
    result = shape_retest.run(make_frame(False), n_shuffle=0)
    assert not result["table"]["ships"].any()


def test_pairs_with_no_rows_are_left_out_and_the_report_renders():
    result = shape_retest.run(make_frame(True), n_shuffle=0)
    assert list(result["table"]["pair"]) == ["QB1-WR1"]
    text = shape_retest.render(result)
    assert "QB1-WR1" in text and "ships" in text
    assert "2024" in text


def test_the_shuffle_keeps_each_roles_distribution_but_breaks_the_link():
    f = make_frame(True, n_games=600)
    s = shape_retest.shuffled_tshare(f, np.random.default_rng(1))
    assert s["tshare"].isna().sum() == f["tshare"].isna().sum()
    a = f.groupby(["role", "season"])["tshare"].sum().round(8)
    b = s.groupby(["role", "season"])["tshare"].sum().round(8)
    pd.testing.assert_series_equal(a, b)
    assert not np.allclose(f["tshare"].fillna(0), s["tshare"].fillna(0))
