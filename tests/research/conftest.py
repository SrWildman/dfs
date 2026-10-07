"""Small deterministic synthetic data for the research tests: no network, no cache, no real history."""

import numpy as np
import pandas as pd
import pytest

SEASON = 2023
# Per-game usage of one synthetic offense: (position, targets, carries)
ROSTER = {
    "WR1": ("WR", 12, 0),
    "WR2": ("WR", 8, 0),
    "WR3": ("WR", 5, 0),
    "TE1": ("TE", 6, 0),
    "RB1": ("RB", 3, 18),
    "RB2": ("RB", 2, 7),
    "QB1": ("QB", 0, 0),
}
TEAM_TARGETS = sum(t for _, t, _ in ROSTER.values())  # 36
TEAM_CARRIES = sum(c for _, _, c in ROSTER.values())  # 25


def make_games(bye_week: int | None = 5, weeks: int = 9) -> pd.DataFrame:
    """AAA hosts BBB every week except `bye_week` (both off that week); CCC / DDD play every week."""
    rows = []
    for week in range(1, weeks + 1):
        pairs = [("CCC", "DDD")] + ([] if week == bye_week else [("AAA", "BBB")])
        for home, away in pairs:
            rows.append(
                {
                    "season": SEASON,
                    "week": week,
                    "game_id": f"{SEASON}_{week:02d}_{away}_{home}",
                    "home_team": home,
                    "away_team": away,
                    "home_score": 24,
                    "away_score": 20,
                    "location": "Home",
                    "home_rest": 7,
                    "away_rest": 7,
                    "div_game": 0,
                }
            )
    return pd.DataFrame(rows)


def make_stats(
    games: pd.DataFrame, missing: set[tuple[str, str, int]] | None = None, scale=None
) -> pd.DataFrame:
    """stats_player-shaped rows: every roster player on every team that played, minus `missing` =
    {(team, slot, week)}. `scale` = {(team, slot, week): multiplier} inflates one game's usage."""
    missing = missing or set()
    scale = scale or {}
    out = []
    for g in games.itertuples():
        for team in (g.home_team, g.away_team):
            for slot, (position, targets, carries) in ROSTER.items():
                if (team, slot, g.week) in missing:
                    continue
                k = scale.get((team, slot, g.week), 1)
                out.append(
                    {
                        "player_id": f"{team}-{slot}",
                        "player_display_name": f"{team} {slot}",
                        "position": position,
                        "team": team,
                        "season": g.season,
                        "week": g.week,
                        "season_type": "REG",
                        "targets": targets * k,
                        "carries": carries * k,
                        "attempts": 35 if position == "QB" else 0,
                    }
                )
    return pd.DataFrame(out)


def make_injuries(rows: list[tuple[str, int, str | None]] | None = None) -> pd.DataFrame:
    """(gsis_id, week, primary injury text) -> an injury-report frame."""
    rows = rows or []
    cols = ["gsis_id", "season", "week", "report_primary_injury", "report_status"]
    return pd.DataFrame(
        [
            {
                "gsis_id": gid,
                "season": SEASON,
                "week": week,
                "report_primary_injury": text,
                "report_status": "Out",
            }
            for gid, week, text in rows
        ],
        columns=cols,
    )


@pytest.fixture
def games() -> pd.DataFrame:
    return make_games()


@pytest.fixture
def rng() -> np.random.Generator:
    return np.random.default_rng(0)


def make_matchup_tables(seasons=(2020, 2021, 2022, 2023), weeks=8, seed=1):
    """Synthetic (base, pos_game, team_game, off_game) for `matchup_features` / R2: four teams, two games a
    week, one player per offensive position per team, random but fixed values."""
    rng = np.random.default_rng(seed)
    base, pos_game, team_game, off_game = [], [], [], []
    for season in seasons:
        for week in range(1, weeks + 1):
            pairs = [("AAA", "BBB"), ("CCC", "DDD")] if week % 2 else [("BBB", "AAA"), ("DDD", "CCC")]
            for home, away in pairs:
                game_id = f"{season}_{week:02d}_{away}_{home}"
                total = float(rng.integers(38, 52))
                for team, opp, is_home in ((home, away, 1), (away, home, 0)):
                    implied = total / 2 + (1.5 if is_home else -1.5)
                    t = season * 100 + week
                    ctx = {"season": season, "week": week, "game_id": game_id, "t": t}
                    for pos in ("QB", "RB", "WR", "TE"):
                        dk = float(rng.normal(12, 5))
                        um = dk + float(rng.normal(0, 4))
                        l8 = 12 + float(rng.normal(0, 1))
                        pos_game.append({"team": team, "opp": opp, "position": pos, "dk": dk, **ctx})
                        base.append(
                            {
                                "gsis_id": f"{team}-{pos}",
                                "position": pos,
                                "team": team,
                                "opp": opp,
                                "dk": dk,
                                "um": um,
                                "l8": l8,
                                "resid": dk - um,
                                "resid_l8": dk - l8,
                                "implied": implied,
                                "total": total,
                                "opp_implied": total - implied,
                                **ctx,
                            }
                        )
                    dst = float(rng.integers(-2, 18))
                    stats = {
                        "dk": dst,
                        "sacks_allowed": float(rng.integers(0, 6)),
                        "giveaways": float(rng.integers(0, 4)),
                        "dst_conceded": float(rng.integers(-2, 18)),
                    }
                    team_game.append(
                        {"team": team, "opp": opp, **stats, "implied": implied, "total": total, **ctx}
                    )
                    base.append(
                        {
                            "gsis_id": team,
                            "position": "DST",
                            "team": team,
                            "opp": opp,
                            "dk": dst,
                            "um": dst + float(rng.normal(0, 3)),
                            "l8": 7.0,
                            "resid": 0.0,
                            "resid_l8": dst - 7.0,
                            "implied": implied,
                            "total": total,
                            "opp_implied": total - implied,
                            **ctx,
                        }
                    )
                    off_game.append(
                        {
                            "team": team,
                            "opp": opp,
                            "plays": float(rng.integers(55, 75)),
                            "epa_pass": float(rng.normal(0, 0.2)),
                            "epa_rush": float(rng.normal(-0.05, 0.2)),
                            "proe": float(rng.normal(0, 4)),
                            **ctx,
                        }
                    )
    base = pd.DataFrame(base)
    base["resid"] = base["dk"] - base["um"]
    return base, pd.DataFrame(pos_game), pd.DataFrame(team_game), pd.DataFrame(off_game)


@pytest.fixture(scope="module")
def matchup_tables():
    return make_matchup_tables()
