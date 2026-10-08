"""R6 features: windows are prior-only; the pbp definitions (end-zone target, goal-line carry, high-value
touch,
designed QB rush) hold on tiny hand-built plays; the id crosswalk drops what it cannot map one-to-one."""

import numpy as np
import pandas as pd
import pytest

from dfs.research import r6_features as F

NUM_COLS = sorted({c for num, den in F.METRICS.values() for c in (num, den) if c})


def _games(n_games=14, players=("P1", "P2"), seed=0) -> pd.DataFrame:
    """Per-game columns for every metric, two players on one team; weeks run across a season boundary."""
    rng = np.random.default_rng(seed)
    rows = []
    for pid in players:
        for i in range(n_games):
            season, week = (2022, i + 1) if i < 10 else (2023, i - 9)
            row = {"gsis_id": pid, "season": season, "week": week, "team": "AAA", "position": "WR"}
            row |= {c: float(rng.integers(0, 9)) + float(rng.random()) for c in NUM_COLS}
            row["targets"] = float(rng.integers(1, 12))  # a ratio's denominator must be positive
            row["team_gl_carries"] = float(rng.integers(1, 5))
            rows.append(row)
    g = pd.DataFrame(rows)
    g["t"] = g["season"] * 100 + g["week"]
    return g


def _team_games(n_games=14, seed=1) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    rows = [
        {"team": "AAA", "season": 2022 if i < 10 else 2023, "week": i + 1 if i < 10 else i - 9}
        | {"proe": float(rng.normal(0, 5))}
        for i in range(n_games)
    ]
    return pd.DataFrame(rows)


# --------------------------------------------------------------------------------------------------------
# Windows
# --------------------------------------------------------------------------------------------------------


def test_mean_metric_is_last_three_against_the_six_before():
    g = _games()
    out = F.window_features(g, F.METRICS)
    p1 = g[g["gsis_id"] == "P1"].reset_index(drop=True)
    o = out.loc[g["gsis_id"] == "P1"].reset_index(drop=True)
    i = 11
    x = p1["receptions"].to_numpy()
    assert o["rec_pg_l3"].iloc[i] == pytest.approx(x[i - 3 : i].mean())
    assert o["rec_pg_prior"].iloc[i] == pytest.approx(x[i - 9 : i - 3].mean())
    assert o["rec_pg_chg"].iloc[i] == pytest.approx(x[i - 3 : i].mean() - x[i - 9 : i - 3].mean())


def test_ratio_metric_is_a_ratio_of_window_sums_not_a_mean_of_ratios():
    g = _games()
    out = F.window_features(g, F.METRICS)
    p1 = g[g["gsis_id"] == "P1"].reset_index(drop=True)
    o = out.loc[g["gsis_id"] == "P1"].reset_index(drop=True)
    i = 12
    ay, tg = p1["receiving_air_yards"].to_numpy(), p1["targets"].to_numpy()
    assert o["adot_l3"].iloc[i] == pytest.approx(ay[i - 3 : i].sum() / tg[i - 3 : i].sum())
    assert o["adot_prior"].iloc[i] == pytest.approx(ay[i - 9 : i - 3].sum() / tg[i - 9 : i - 3].sum())
    ratios = ay[i - 3 : i] / tg[i - 3 : i]
    assert o["adot_l3"].iloc[i] != pytest.approx(ratios.mean())
    gl, team = p1["gl_carries"].to_numpy(), p1["team_gl_carries"].to_numpy()
    assert o["gl_share_l3"].iloc[i] == pytest.approx(gl[i - 3 : i].sum() / team[i - 3 : i].sum())


def test_a_level_needs_three_earlier_games_and_a_change_needs_nine():
    out = F.window_features(_games(), F.METRICS)
    p1 = out.loc[_games()["gsis_id"] == "P1"].reset_index(drop=True)
    assert F.RECENT == 3 and F.MIN_PRIOR_GAMES == 9
    assert p1["rec_pg_l3"].iloc[:3].isna().all() and p1["rec_pg_l3"].iloc[3:].notna().all()
    assert p1["rec_pg_chg"].iloc[:9].isna().all() and p1["rec_pg_chg"].iloc[9:].notna().all()


def test_a_missing_game_value_keeps_that_game_out_of_every_window_it_would_enter():
    g = _games()
    g.loc[g.index[(g["gsis_id"] == "P1")][4], "snap_pct"] = np.nan  # P1's 5th game has no snap row
    out = F.window_features(g, F.METRICS)
    p1 = out.loc[g["gsis_id"] == "P1"].reset_index(drop=True)
    assert p1["snap_pct_l3"].iloc[5:8].isna().all()  # games 6-8 have game 5 in their last 3
    assert p1["snap_pct_l3"].iloc[8] == p1["snap_pct_l3"].iloc[8]  # game 9 does not
    assert p1["snap_pct_chg"].iloc[9:13].isna().all()  # game 5 is inside the 9 earlier games of games 10-13


def test_players_do_not_share_windows():
    g = _games()
    alone = F.window_features(g[g["gsis_id"] == "P2"], F.METRICS)
    both = F.window_features(g, F.METRICS).loc[g["gsis_id"] == "P2"]
    pd.testing.assert_frame_equal(alone, both)


@pytest.mark.parametrize("i", [3, 9, 11])
def test_a_games_features_ignore_that_game_and_every_later_one(i):
    """The leakage test: absurd values in game i and every later game move no feature of games 0..i."""
    g = _games()
    base = F.window_features(g, F.METRICS)
    changed = g.copy()
    later = changed.groupby("gsis_id").cumcount() >= i
    for c in NUM_COLS:
        changed.loc[later, c] = 1e6
    out = F.window_features(changed, F.METRICS)
    early = (g.groupby("gsis_id").cumcount() <= i).to_numpy()
    pd.testing.assert_frame_equal(base[early], out[early])
    assert not base[~early].equals(out[~early])


def test_appending_the_current_game_changes_no_earlier_feature_and_the_new_row_uses_priors_only():
    g = _games(n_games=13)
    base = F.window_features(g, F.METRICS)
    extra = g[g["gsis_id"] == "P1"].tail(1).copy()
    extra["week"] += 1
    extra["t"] += 1
    for c in NUM_COLS:
        extra[c] = 1e6
    grown = pd.concat([g, extra], ignore_index=True)
    out = F.window_features(grown, F.METRICS)
    pd.testing.assert_frame_equal(base, out.iloc[: len(g)])
    last = out.iloc[-1]
    p1 = g[g["gsis_id"] == "P1"]["receptions"].to_numpy()
    assert last["rec_pg_l3"] == pytest.approx(p1[-3:].mean())  # not contaminated by its own 1e6


def test_full_feature_build_is_prior_only_including_the_team_metric():
    g, tg = _games(), _team_games()
    base = F.usage_features(g, tg)
    bad_g, bad_tg = g.copy(), tg.copy()
    cut = g["t"] >= 202303  # the third game of 2023 and everything after it
    assert cut.any() and (~cut).any()
    for c in NUM_COLS:
        bad_g.loc[cut, c] = 1e6
    bad_tg.loc[bad_tg["season"] * 100 + bad_tg["week"] >= 202303, "proe"] = 1e6
    out = F.usage_features(bad_g, bad_tg)
    early = (g["t"] <= 202303).to_numpy()  # up to and including the first corrupted game itself
    pd.testing.assert_frame_equal(base[early].reset_index(drop=True), out[early].reset_index(drop=True))
    assert not base[~early].reset_index(drop=True).equals(out[~early].reset_index(drop=True))


def test_team_metric_is_windowed_over_the_teams_own_games_and_attached_to_its_players():
    g, tg = _games(), _team_games()
    out = F.usage_features(g, tg)
    x = tg["proe"].to_numpy()
    row = out[(out["gsis_id"] == "P1") & (out["season"] == 2023) & (out["week"] == 2)].iloc[0]  # 12th game
    assert row["proe_l3"] == pytest.approx(x[8:11].mean())
    assert row["proe_chg"] == pytest.approx(x[8:11].mean() - x[2:8].mean())
    p2 = out[(out["gsis_id"] == "P2") & (out["season"] == 2023) & (out["week"] == 2)].iloc[0]
    assert p2["proe_chg"] == pytest.approx(row["proe_chg"])


# --------------------------------------------------------------------------------------------------------
# The pbp definitions on tiny fixtures
# --------------------------------------------------------------------------------------------------------


def play(kind="pass", **kw) -> dict:
    """One play. kind: pass | run | scramble | kneel | no_play. Defaults: team AAA, week 1, own 40."""
    base = {
        "season": 2023,
        "week": 1,
        "game_id": "g1",
        "posteam": "AAA",
        "defteam": "BBB",
        "play_type": {"scramble": "run", "kneel": "qb_kneel"}.get(kind, kind),
        "pass": 1 if kind in ("pass", "scramble") else 0,
        "rush": 1 if kind == "run" else 0,
        "qb_scramble": 1 if kind == "scramble" else 0,
        "two_point_attempt": 0,
        "yardline_100": 60.0,
        "air_yards": np.nan,
        "receiver_player_id": None,
        "rusher_player_id": None,
    }
    return base | kw


def counts(plays: list[dict]) -> pd.DataFrame:
    out = F.pbp_player_counts(pd.DataFrame(plays))
    return out.set_index("gsis_id")


def test_end_zone_target_is_air_yards_at_least_yards_to_the_goal_line():
    plays = [
        play(receiver_player_id="W", yardline_100=20.0, air_yards=20.0),  # exactly to the goal line: yes
        play(receiver_player_id="W", yardline_100=20.0, air_yards=19.0),  # one yard short: no
        play(receiver_player_id="W", yardline_100=8.0, air_yards=-1.0),  # a behind-the-line target: no
        play(receiver_player_id="W", yardline_100=3.0, air_yards=9.0),  # past the end line: yes
    ]
    c = counts(plays).loc["W"]
    assert c["ez_targets"] == 2 and c["targets_pbp"] == 4


def test_a_target_with_no_air_yards_is_not_an_end_zone_target():
    c = counts([play(receiver_player_id="W", yardline_100=5.0, air_yards=np.nan)]).loc["W"]
    assert c["ez_targets"] == 0 and c["targets_pbp"] == 1


def test_deep_target_is_twenty_air_yards_or_more():
    plays = [play(receiver_player_id="W", air_yards=a) for a in (19.0, 20.0, 45.0)]
    assert counts(plays).loc["W", "deep_targets"] == 2


def test_goal_line_carry_is_inside_the_five_and_the_share_uses_every_runner():
    plays = [
        play("run", rusher_player_id="R1", yardline_100=5.0),  # the 5: in
        play("run", rusher_player_id="R1", yardline_100=6.0),  # the 6: out
        play("run", rusher_player_id="R2", yardline_100=1.0),
        play("run", rusher_player_id="R2", yardline_100=2.0),
        play("run", rusher_player_id="Q", yardline_100=1.0),  # a QB sneak is a team carry too
    ]
    c = counts(plays)
    assert c.loc["R1", "gl_carries"] == 1 and c.loc["R2", "gl_carries"] == 2 and c.loc["Q", "gl_carries"] == 1
    team = F.team_goal_line_carries(pd.DataFrame(plays))
    assert team.loc[0, "team_gl_carries"] == 4 and team.loc[0, "team"] == "AAA"


def test_high_value_touches_are_every_target_plus_carries_inside_the_ten():
    plays = [
        play(receiver_player_id="B", yardline_100=70.0, air_yards=5.0),  # a target anywhere counts
        play(receiver_player_id="B", yardline_100=4.0, air_yards=2.0),
        play("run", rusher_player_id="B", yardline_100=10.0),  # the 10: in
        play("run", rusher_player_id="B", yardline_100=11.0),  # the 11: out
        play("run", rusher_player_id="B", yardline_100=30.0),
    ]
    assert counts(plays).loc["B", "hvt"] == 3


def test_red_zone_opportunities_are_targets_and_carries_inside_the_twenty():
    plays = [
        play(receiver_player_id="B", yardline_100=20.0, air_yards=5.0),
        play(receiver_player_id="B", yardline_100=21.0, air_yards=5.0),
        play("run", rusher_player_id="B", yardline_100=19.0),
        play("run", rusher_player_id="B", yardline_100=50.0),
    ]
    assert counts(plays).loc["B", "rz_opps"] == 2


def test_designed_qb_rush_excludes_scrambles_and_kneels():
    plays = [
        play("run", rusher_player_id="Q"),  # designed
        play("run", rusher_player_id="Q", yardline_100=1.0),  # a sneak: designed
        play("scramble", rusher_player_id="Q"),  # a scramble: not designed
        play("scramble", rusher_player_id="Q"),
        play("kneel", rusher_player_id="Q"),  # a kneel: not a carry at all
    ]
    c = counts(plays).loc["Q"]
    assert c["qb_designed_rushes"] == 2 and c["carries_pbp"] == 4


def test_scrambles_are_carries_for_the_goal_line_count_but_two_point_tries_and_no_plays_are_not_plays():
    plays = [
        play("scramble", rusher_player_id="Q", yardline_100=3.0),
        play("run", rusher_player_id="R", yardline_100=2.0, two_point_attempt=1),
        play("no_play", rusher_player_id="R", yardline_100=2.0),
        play(receiver_player_id="W", yardline_100=2.0, air_yards=2.0, two_point_attempt=1),
    ]
    c = counts(plays)
    assert c.loc["Q", "gl_carries"] == 1
    assert "R" not in c.index and "W" not in c.index


def test_counts_are_per_player_and_per_game():
    plays = [
        play(receiver_player_id="W", air_yards=30.0),
        play(receiver_player_id="W", air_yards=30.0, week=2),
        play(receiver_player_id="X", air_yards=1.0),
    ]
    c = F.pbp_player_counts(pd.DataFrame(plays)).set_index(["gsis_id", "week"])
    assert c.loc[("W", 1), "deep_targets"] == 1 and c.loc[("W", 2), "deep_targets"] == 1
    assert c.loc[("X", 1), "deep_targets"] == 0


# --------------------------------------------------------------------------------------------------------
# Snaps, box scores
# --------------------------------------------------------------------------------------------------------


def test_snap_shares_join_through_a_one_to_one_crosswalk_and_drop_ambiguous_ids():
    snaps = pd.DataFrame(
        {
            "season": 2023,
            "week": 1,
            "pfr_player_id": ["AaaA00", "BbbB00", "CccC00", "DddD00"],
            "offense_pct": [0.9, 0.5, 0.4, 0.3],
        }
    )
    players = pd.DataFrame(
        {
            "pfr_id": ["AaaA00", "BbbB00", "BbbB00", "CccC00", "DddD00"],
            "gsis_id": ["g-a", "g-b1", "g-b2", "g-c", "g-c"],  # B maps to two gsis ids; C and D share one
        }
    )
    out = F.snap_shares(snaps, players).set_index("gsis_id")
    assert list(out.index) == ["g-a"] and out.loc["g-a", "snap_pct"] == 0.9


def test_box_scores_prefer_nflverses_own_shares_and_fall_back_to_the_team_totals():
    sp = pd.DataFrame(
        {
            "player_id": ["a", "b"],
            "position": ["WR", "RB"],
            "team": "X",
            "season": 2023,
            "week": 1,
            "targets": [6.0, 2.0],
            "receptions": [4.0, 1.0],
            "carries": [0.0, 10.0],
            "receiving_air_yards": [60.0, 4.0],
        }
    )
    own = F.box_scores(sp).set_index("gsis_id")
    assert own.loc["a", "target_share"] == pytest.approx(0.75) and own.loc["b", "carry_share"] == 1.0
    assert own.loc["a", "wopr"] == pytest.approx(1.5 * 0.75 + 0.7 * (60 / 64))
    given = F.box_scores(sp.assign(target_share=[0.5, 0.2], air_yards_share=[0.4, 0.1], wopr=[0.9, 0.3]))
    given = given.set_index("gsis_id")
    assert given.loc["a", "target_share"] == 0.5 and given.loc["a", "wopr"] == 0.9
