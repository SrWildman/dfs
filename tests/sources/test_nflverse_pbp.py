import pandas as pd

from dfs.sources.nflverse_pbp import build_team_metrics


def _play(game_id, posteam, defteam, home_team, away_team, drive, play_id, play_type, game_seconds_remaining):
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
        "wp": 0.5,
        "qtr": 1,
        "half_seconds_remaining": 900.0,
        "yards_gained": 5.0,
        "pass": 1 if play_type == "pass" else 0,
        "rush": 1 if play_type == "run" else 0,
        "pass_oe": 2.0,
    }


def _pbp(team: str, opp: str, n_plays: int = 4, gap_seconds: int = 30) -> pd.DataFrame:
    rows = [
        _play("g1", team, opp, opp, team, 1, i, "pass" if i % 2 else "run", 3600 - i * gap_seconds)
        for i in range(1, n_plays + 1)
    ]
    return pd.DataFrame(rows)


def test_build_team_metrics_remaps_la_to_lar():
    current = _pbp("LA", "SF")
    metrics = build_team_metrics(current, None)
    assert "LA" not in metrics["Team"].tolist()
    assert "LAR" in metrics["Team"].tolist()


def test_build_team_metrics_without_prior_uses_current_alone():
    current = _pbp("KC", "DEN")
    metrics = build_team_metrics(current, None)
    row = metrics[metrics["Team"] == "KC"].iloc[0]
    assert row.notna().all()


def test_build_team_metrics_blends_with_prior_early_in_season():
    current = _pbp("KC", "DEN", n_plays=4, gap_seconds=30)  # 1 game played, fast tempo
    prior = _pbp("KC", "DEN", n_plays=20, gap_seconds=90)  # a much slower prior-season tempo
    metrics_with_prior = build_team_metrics(current, prior)
    metrics_without_prior = build_team_metrics(current, None)
    kc_with = metrics_with_prior[metrics_with_prior["Team"] == "KC"].iloc[0]
    kc_without = metrics_without_prior[metrics_without_prior["Team"] == "KC"].iloc[0]
    # A genuinely different prior-season tempo must pull the blended Pace
    # away from the current-season-alone number -- confirms the blend
    # path actually runs rather than silently falling back to current-only.
    assert kc_with["Pace"] != kc_without["Pace"]
    assert kc_without["Pace"] == 30.0
    assert kc_with["Pace"] > kc_without["Pace"]
