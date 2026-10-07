"""Features: one code path for training and inference, strictly prior games only."""

import numpy as np
import pandas as pd
import pytest

from dfs.model.features import ROW_COLUMNS, build_features, feature_columns
from dfs.model.history import POSITIONS


def _all_rows(history) -> pd.DataFrame:
    return pd.concat([history.player_games[ROW_COLUMNS], history.team_games[ROW_COLUMNS]], ignore_index=True)


def _frame(history, t: int) -> pd.DataFrame:
    rows = _all_rows(history)
    return rows[(rows["season"] * 100 + rows["week"]) == t].reset_index(drop=True)


def test_inference_features_equal_training_features_exactly(history):
    """The parity test: the slate for week N, built from only the games before N (what inference sees),
    equals the training row for the same players built from the full history."""
    training = build_features(history, _all_rows(history))
    for t in (202304, 202306, 202401, 202403, 202406):
        slate = _frame(history, t)
        inference = build_features(history.before(t), slate)
        expected = training[training["t"] == t].reset_index(drop=True)
        assert len(inference) == len(slate) > 0
        pd.testing.assert_frame_equal(inference, expected, check_exact=True)


def test_features_for_week_n_do_not_change_when_week_n_and_later_are_added(history):
    t = 202405
    slate = _frame(history, t)
    before_only = build_features(history.before(t), slate)

    # a history that contains week N and later, with those games' outcomes made absurd
    poisoned = history.__class__(
        history.player_games.copy(), history.team_games.copy(), history.def_vs_pos.copy()
    )
    for table, cols in (
        (poisoned.player_games, ["dk", "xfp", "targets", "carries", "target_share"]),
        (poisoned.team_games, ["dk", "sacks", "takeaways", "giveaways", "sacks_allowed", "dst_conceded"]),
        (poisoned.def_vs_pos, ["allowed", "rel_allowed"]),
    ):
        table.loc[table["t"] >= t, cols] = 1e6
    with_future = build_features(poisoned, slate)
    pd.testing.assert_frame_equal(before_only, with_future, check_exact=True)


def test_every_feature_matches_a_naive_per_player_computation(history):
    feats = build_features(history, history.player_games[ROW_COLUMNS])
    pg = history.player_games
    for i in range(0, len(pg), 7):
        row = pg.iloc[i]
        prior = pg[(pg["gsis_id"] == row["gsis_id"]) & (pg["t"] < row["t"])].sort_values("t")
        for col in ("dk", "xfp", "targets", "target_share"):
            for n in (1, 3, 8):
                expected = prior[col].tail(n).mean() if len(prior) else np.nan
                got = feats.loc[i, f"{col}_l{n}"]
                assert (np.isnan(expected) and np.isnan(got)) or got == pytest.approx(expected)
        assert feats.loc[i, "games_l8"] == min(len(prior), 8)
        assert feats.loc[i, "weeks_into_season"] == row["week"] - 1


def test_rolling_windows_cross_the_season_boundary(history):
    pg = history.player_games
    first_2024 = pg[(pg["gsis_id"] == "AAA-QB0") & (pg["t"] == 202401)]
    feats = build_features(history, first_2024[ROW_COLUMNS])
    prior = pg[(pg["gsis_id"] == "AAA-QB0") & (pg["t"] < 202401)].sort_values("t")
    assert feats["games_l8"].iloc[0] == 6
    assert feats["dk_l3"].iloc[0] == pytest.approx(prior["dk"].tail(3).mean())
    assert feats["dk_l1"].iloc[0] == prior["dk"].iloc[-1]


def test_games_played_not_calendar_weeks(history):
    """AAA-WR1 sat out 2023 week 3: his last-3 window skips it and reaches one game further back."""
    pg = history.player_games
    row = pg[(pg["gsis_id"] == "AAA-WR1") & (pg["t"] == 202305)]
    got = build_features(history, row[ROW_COLUMNS])
    played = pg[(pg["gsis_id"] == "AAA-WR1") & (pg["t"] < 202305)].sort_values("t")
    assert played["week"].tolist() == [1, 2, 4]
    assert got["dk_l3"].iloc[0] == pytest.approx(played["dk"].mean())
    assert got["games_l8"].iloc[0] == 3


def test_a_player_with_no_history_keeps_nans_and_zero_games(history):
    rookie = history.player_games[history.player_games["gsis_id"] == "ROOKIE-WR"]
    feats = build_features(history, rookie[ROW_COLUMNS])
    assert feats["games_l8"].iloc[0] == 0
    assert feats[["dk_l1", "dk_l8", "xfp_l3", "target_share_l8"]].isna().all(axis=None)
    assert feats["implied"].notna().all()  # this game's own context is still there


def test_opponent_defense_feature_is_prior_weeks_net_of_league_average(history):
    pg = history.player_games
    row = pg[(pg["position"] == "WR") & (pg["t"] == 202405)].iloc[[0]]
    feats = build_features(history, row[ROW_COLUMNS])
    dvp = history.def_vs_pos
    prior = dvp[(dvp["team"] == row["opp"].iloc[0]) & (dvp["position"] == "WR") & (dvp["t"] < 202405)]
    assert feats["def_vs_pos_l8"].iloc[0] == pytest.approx(
        prior.sort_values("t")["rel_allowed"].tail(8).mean()
    )


def test_dst_features_look_up_own_defense_and_the_opposing_offense(history):
    tg = history.team_games
    row = tg[tg["t"] == 202404].iloc[[0]]
    feats = build_features(history, row[ROW_COLUMNS])
    own = tg[(tg["team"] == row["team"].iloc[0]) & (tg["t"] < 202404)].sort_values("t").tail(8)
    opp = tg[(tg["team"] == row["opp"].iloc[0]) & (tg["t"] < 202404)].sort_values("t").tail(8)
    assert feats["def_sacks"].iloc[0] == pytest.approx(own["sacks"].mean())
    assert feats["def_dk"].iloc[0] == pytest.approx(own["dk"].mean())
    assert feats["opp_giveaways"].iloc[0] == pytest.approx(opp["giveaways"].mean())
    assert feats["opp_dst_conceded"].iloc[0] == pytest.approx(opp["dst_conceded"].mean())
    assert feats["opp_implied"].iloc[0] == pytest.approx(row["total"].iloc[0] - row["implied"].iloc[0])


def test_output_keeps_input_order_and_has_every_feature_column(history):
    rows = _all_rows(history).sample(frac=1.0, random_state=1).reset_index(drop=True)
    feats = build_features(history, rows)
    assert feats["gsis_id"].tolist() == rows["gsis_id"].tolist()
    for position in POSITIONS:
        sub = feats[feats["position"] == position]
        assert set(feature_columns(position)) <= set(sub.columns)


def test_rows_missing_columns_or_with_an_unknown_position_are_rejected(history):
    rows = _frame(history, 202405)
    with pytest.raises(ValueError, match="missing columns"):
        build_features(history, rows.drop(columns=["implied"]))
    bad = rows.copy()
    bad.loc[0, "position"] = "K"
    with pytest.raises(ValueError, match="unknown position"):
        build_features(history, bad)


def test_qb_features_exist_only_for_qbs():
    assert "passing_epa_l3" in feature_columns("QB") and "attempts_l8" in feature_columns("QB")
    for position in ("RB", "WR", "TE"):
        assert not any(c.startswith(("passing_epa", "attempts")) for c in feature_columns(position))
