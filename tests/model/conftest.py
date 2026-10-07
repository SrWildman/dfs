"""A small deterministic synthetic history (4 teams, 2 seasons x 6 weeks) for the feature/predict tests."""

import numpy as np
import pandas as pd
import pytest

from dfs.model.history import History, build_def_vs_pos, time_key

TEAMS = ["AAA", "BBB", "CCC", "DDD"]
# (season, week) in play order
WEEKS = [(s, w) for s in (2023, 2024) for w in range(1, 7)]
ROSTER = [("QB", 1), ("RB", 1), ("WR", 2), ("TE", 1)]


def _games_for(week_index: int):
    """Two games a week; home/away swap every other week so both sides get a turn."""
    pairs = [("AAA", "BBB"), ("CCC", "DDD")]
    return [(h, a) if week_index % 2 == 0 else (a, h) for h, a in pairs]


def make_history(seed: int = 0) -> History:
    rng = np.random.default_rng(seed)
    player_rows, team_rows = [], []
    for i, (season, week) in enumerate(WEEKS):
        for home, away in _games_for(i):
            total = float(rng.integers(38, 52))
            spread = float(rng.integers(-7, 8))  # home perspective, positive = home favoured
            for team, opp, is_home in ((home, away, 1), (away, home, 0)):
                implied = total / 2 + (spread / 2 if is_home else -spread / 2)
                ctx = {
                    "implied": implied,
                    "spread": spread if is_home else -spread,
                    "total": total,
                    "home": is_home,
                }
                for position, n in ROSTER:
                    for k in range(n):
                        pid = f"{team}-{position}{k}"
                        # one WR, in one game, sits out: games played != games on the calendar
                        if pid == "AAA-WR1" and (season, week) == (2023, 3):
                            continue
                        player_rows.append(
                            {
                                "gsis_id": pid,
                                "name": pid,
                                "position": position,
                                "team": team,
                                "opp": opp,
                                "season": season,
                                "week": week,
                                "dk": float(rng.integers(0, 35)),
                                "xfp": float(rng.uniform(4, 22)),
                                "targets": float(rng.integers(0, 12)),
                                "carries": float(rng.integers(0, 22)),
                                "attempts": float(rng.integers(0, 45)),
                                "target_share": float(rng.uniform(0, 0.35)),
                                "air_yards_share": float(rng.uniform(0, 0.4)),
                                "receiving_air_yards": float(rng.integers(0, 150)),
                                "passing_epa": float(rng.normal(0, 6)),
                                **ctx,
                            }
                        )
                team_rows.append(
                    {
                        "gsis_id": team,
                        "position": "DST",
                        "team": team,
                        "opp": opp,
                        "season": season,
                        "week": week,
                        "dk": float(rng.integers(-2, 20)),
                        "sacks": float(rng.integers(0, 6)),
                        "takeaways": float(rng.integers(0, 4)),
                        "giveaways": float(rng.integers(0, 4)),
                        "sacks_allowed": float(rng.integers(0, 6)),
                        "dst_conceded": float(rng.integers(-2, 20)),
                        **ctx,
                    }
                )
    # a rookie who debuts in the last week of the history
    player_rows.append({**player_rows[-1], "gsis_id": "ROOKIE-WR", "name": "ROOKIE-WR", "position": "WR"})
    players = pd.DataFrame(player_rows)
    players["t"] = time_key(players["season"], players["week"])
    teams = pd.DataFrame(team_rows)
    teams["t"] = time_key(teams["season"], teams["week"])
    players = players.sort_values(["gsis_id", "t"]).reset_index(drop=True)
    return History(
        players, teams.sort_values(["team", "t"]).reset_index(drop=True), build_def_vs_pos(players)
    )


@pytest.fixture(scope="module")
def history() -> History:
    return make_history()
