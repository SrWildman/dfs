import math

import pandas as pd

from dfs.team_metrics import (
    PBP_PRIOR_WEIGHT_GAMES,
    blend_with_prior,
    combined_by_game,
    team_explosive_pct,
    team_games_played,
    team_pace,
    team_proe,
    weighted_mean_skipna,
)


def _play(
    game_id,
    posteam,
    defteam,
    home_team,
    away_team,
    drive,
    play_id,
    play_type,
    game_seconds_remaining,
    *,
    wp=0.5,
    qtr=1,
    half_seconds_remaining=900.0,
    yards_gained=0.0,
    pass_=0,
    rush=0,
    pass_oe=0.0,
):
    return {
        "game_id": game_id,
        "posteam": posteam,
        "defteam": defteam,
        "home_team": home_team,
        "away_team": away_team,
        "drive": drive,
        "play_id": play_id,
        "play_type": play_type,
        "game_seconds_remaining": game_seconds_remaining,
        "wp": wp,
        "qtr": qtr,
        "half_seconds_remaining": half_seconds_remaining,
        "yards_gained": yards_gained,
        "pass": pass_,
        "rush": rush,
        "pass_oe": pass_oe,
    }


def test_team_pace_averages_gaps_within_a_drive_neutral_script_only():
    rows = [
        _play("g1", "KC", "DEN", "DEN", "KC", 1, 1, "run", 3600),
        _play("g1", "KC", "DEN", "DEN", "KC", 1, 2, "pass", 3570),
        _play("g1", "KC", "DEN", "DEN", "KC", 1, 3, "pass", 3530),
        # A different drive -- the gap into it must not be counted.
        _play("g1", "KC", "DEN", "DEN", "KC", 2, 4, "run", 2000),
    ]
    pbp = pd.DataFrame(rows)
    pace = team_pace(pbp)
    # Drive 1 gaps: 30, 40 -> mean 35. Drive 2's lone play has no prior
    # play in ITS OWN drive, so it contributes nothing.
    assert pace["KC"] == 35.0


def test_team_pace_excludes_plays_outside_neutral_script():
    rows = [
        _play("g1", "KC", "DEN", "DEN", "KC", 1, 1, "run", 3600, wp=0.5),
        # Garbage time: wp far from neutral -- must not count toward the gap.
        _play("g1", "KC", "DEN", "DEN", "KC", 1, 2, "pass", 3550, wp=0.95),
        _play("g1", "KC", "DEN", "DEN", "KC", 1, 3, "pass", 3500, wp=0.5),
    ]
    pbp = pd.DataFrame(rows)
    pace = team_pace(pbp)
    # Only play 1 and play 3 are neutral-script; their own gap (100s) is
    # what should be measured, skipping over the excluded middle play.
    assert pace["KC"] == 100.0


def test_team_pace_excludes_final_two_minutes_of_a_half():
    rows = [
        _play("g1", "KC", "DEN", "DEN", "KC", 1, 1, "run", 3600, half_seconds_remaining=200.0),
        _play("g1", "KC", "DEN", "DEN", "KC", 1, 2, "pass", 3550, half_seconds_remaining=110.0),
    ]
    pbp = pd.DataFrame(rows)
    pace = team_pace(pbp)
    assert "KC" not in pace.index or math.isnan(pace.get("KC", float("nan")))


def test_team_pace_excludes_fourth_quarter():
    rows = [
        _play("g1", "KC", "DEN", "DEN", "KC", 1, 1, "run", 3600, qtr=3),
        _play("g1", "KC", "DEN", "DEN", "KC", 1, 2, "pass", 3550, qtr=4),
    ]
    pbp = pd.DataFrame(rows)
    pace = team_pace(pbp)
    assert "KC" not in pace.index or math.isnan(pace.get("KC", float("nan")))


def test_team_proe_means_pass_oe_over_neutral_script_only():
    rows = [
        _play("g1", "KC", "DEN", "DEN", "KC", 1, 1, "pass", 3600, pass_oe=10.0),
        _play("g1", "KC", "DEN", "DEN", "KC", 1, 2, "run", 3550, pass_oe=-2.0),
        _play("g1", "KC", "DEN", "DEN", "KC", 1, 3, "pass", 3500, wp=0.99, pass_oe=99.0),
    ]
    pbp = pd.DataFrame(rows)
    proe = team_proe(pbp)
    assert proe["KC"] == 4.0


def test_team_explosive_pct_pass_and_rush_thresholds_differ():
    rows = [
        _play("g1", "KC", "DEN", "DEN", "KC", 1, 1, "pass", 3600, yards_gained=20.0, pass_=1, rush=0),
        _play("g1", "KC", "DEN", "DEN", "KC", 1, 2, "pass", 3550, yards_gained=19.0, pass_=1, rush=0),
        _play("g1", "KC", "DEN", "DEN", "KC", 1, 3, "run", 3500, yards_gained=10.0, pass_=0, rush=1),
        _play("g1", "KC", "DEN", "DEN", "KC", 1, 4, "run", 3450, yards_gained=9.0, pass_=0, rush=1),
    ]
    pbp = pd.DataFrame(rows)
    expl = team_explosive_pct(pbp)
    # 2 of 4 plays clear their own threshold -> 50%.
    assert expl["KC"] == 50.0


def test_team_explosive_pct_excludes_kneels_spikes_and_no_plays():
    rows = [
        _play("g1", "KC", "DEN", "DEN", "KC", 1, 1, "qb_kneel", 3600, yards_gained=-1.0),
        _play("g1", "KC", "DEN", "DEN", "KC", 1, 2, "qb_spike", 3550, yards_gained=0.0),
        _play("g1", "KC", "DEN", "DEN", "KC", 1, 3, "no_play", 3500, yards_gained=30.0, pass_=1),
        _play("g1", "KC", "DEN", "DEN", "KC", 1, 4, "run", 3450, yards_gained=1.0, rush=1),
    ]
    pbp = pd.DataFrame(rows)
    expl = team_explosive_pct(pbp)
    # Only the one real "run" play counts as the denominator; it isn't
    # explosive, so this must be 0%, not skip the team entirely and not
    # count the penalty/kneel/spike rows.
    assert expl["KC"] == 0.0


def test_team_games_played_counts_distinct_games_by_home_or_away():
    rows = [
        _play("g1", "KC", "DEN", "DEN", "KC", 1, 1, "run", 3600),
        _play("g1", "KC", "DEN", "DEN", "KC", 1, 2, "run", 3500),
        _play("g2", "KC", "BUF", "KC", "BUF", 1, 1, "run", 3600),
    ]
    pbp = pd.DataFrame(rows)
    played = team_games_played(pbp)
    assert played["KC"] == 2
    assert played["DEN"] == 1
    assert played["BUF"] == 1


def test_blend_with_prior_weights_by_games_played():
    current = pd.Series({"KC": 30.0})
    prior = pd.Series({"KC": 26.0})
    games_played = pd.Series({"KC": PBP_PRIOR_WEIGHT_GAMES})  # weight_current = 0.5
    blended = blend_with_prior(current, prior, games_played, PBP_PRIOR_WEIGHT_GAMES)
    assert blended["KC"] == 28.0


def test_blend_with_prior_falls_back_to_whichever_side_exists():
    current = pd.Series({"KC": 30.0, "DEN": float("nan")}, dtype=float)
    prior = pd.Series({"KC": float("nan"), "DEN": 26.0}, dtype=float)
    games_played = pd.Series({"KC": 2.0, "DEN": 2.0})
    blended = blend_with_prior(current, prior, games_played, PBP_PRIOR_WEIGHT_GAMES)
    assert blended["KC"] == 30.0
    assert blended["DEN"] == 26.0


def test_blend_with_prior_both_missing_is_nan_not_zero():
    current = pd.Series({"KC": float("nan")}, dtype=float)
    prior = pd.Series({"KC": float("nan")}, dtype=float)
    games_played = pd.Series({"KC": 2.0})
    blended = blend_with_prior(current, prior, games_played, PBP_PRIOR_WEIGHT_GAMES)
    assert math.isnan(blended["KC"])


def test_combined_by_game_averages_both_teams_not_player_weighted():
    # Team A has 3 player rows, Team B has 1 -- the combined value must
    # still be a plain average of the two TEAMS' own metric, not skewed
    # toward the team with more rostered players.
    game = pd.Series(["G1", "G1", "G1", "G1"])
    team = pd.Series(["A", "A", "A", "B"])
    metric = pd.Series([10.0, 10.0, 10.0, 20.0])  # A's own per-row value repeated, B's once
    combined = combined_by_game(game, team, metric)
    assert combined["G1"] == 15.0


def test_weighted_mean_skipna_full_row_is_plain_weighted_mean():
    components = pd.DataFrame({"total": [80.0], "spread_tightness": [60.0], "pace": [40.0], "proe": [20.0]})
    weights = {"total": 0.25, "spread_tightness": 0.25, "pace": 0.25, "proe": 0.25}
    result = weighted_mean_skipna(components, weights)
    assert result.iloc[0] == 50.0


def test_weighted_mean_skipna_renormalizes_when_some_inputs_missing():
    # Pace/PROE both blank (e.g. pbp didn't sync) -- must reduce to the
    # OLD GameEnv formula exactly: a plain 50/50 of total/spread_tightness.
    components = pd.DataFrame(
        {"total": [80.0], "spread_tightness": [60.0], "pace": [float("nan")], "proe": [float("nan")]}
    )
    weights = {"total": 0.25, "spread_tightness": 0.25, "pace": 0.25, "proe": 0.25}
    result = weighted_mean_skipna(components, weights)
    assert result.iloc[0] == 70.0


def test_weighted_mean_skipna_all_missing_is_nan():
    components = pd.DataFrame(
        {
            "total": [float("nan")],
            "spread_tightness": [float("nan")],
            "pace": [float("nan")],
            "proe": [float("nan")],
        }
    )
    weights = {"total": 0.25, "spread_tightness": 0.25, "pace": 0.25, "proe": 0.25}
    result = weighted_mean_skipna(components, weights)
    assert math.isnan(result.iloc[0])
