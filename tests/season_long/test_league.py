import pandas as pd
import pytest

from dfs.season_long import ros
from dfs.season_long import sleeper_league as sl
from dfs.season_long.config import (
    LeagueConfig,
    SeasonConfigError,
    SeasonLongConfig,
    load_season_config,
    select_leagues,
)


def _league(settings=None, rosters=None):
    league = {
        "league_id": "9",
        "name": "Test",
        "season": "2026",
        "settings": {
            "type": 0,
            "waiver_budget": 100,
            "playoff_week_start": 15,
            "playoff_teams": 6,
            "trade_deadline": 11,
            "last_scored_leg": 7,
            **(settings or {}),
        },
        "scoring_settings": {"rec": 1},
        "roster_positions": ["QB", "RB", "FLEX", "SUPER_FLEX", "BN", "BN", "IR"],
    }
    rosters = rosters or [
        {
            "roster_id": 1,
            "owner_id": "u1",
            "players": ["a", "b"],
            "settings": {"wins": 5, "waiver_budget_used": 30},
        },
        {"roster_id": 2, "owner_id": "u2", "players": ["c"], "settings": {"fpts": 700, "fpts_decimal": 25}},
    ]
    users = [
        {"user_id": "u1", "display_name": "sam", "metadata": {"team_name": "Rye Not?"}},
        {"user_id": "u2", "display_name": "bob"},
    ]
    return sl.parse_league("T", league, rosters, users, "u1", {"week": 8, "season": "2026"})


def test_parse_league_slots_faab_and_playoffs():
    lg = _league()
    assert lg.slots == ["QB", "RB", "FLEX", "SUPER_FLEX"] and lg.bench_slots == 2 and lg.reserve_slots == 1
    assert lg.me.label == "Rye Not?" and lg.me.faab_left == 70 and lg.team(2).points_for == 700.25
    assert lg.playoff_weeks == [15, 16, 17] and lg.remaining_weeks == list(range(8, 18))
    assert not lg.chopped and lg.find_team("bob").roster_id == 2 and lg.find_team("Team 1").roster_id == 1


def test_two_week_final_and_no_playoffs():
    assert _league({"playoff_round_type": 1, "playoff_week_start": 14}).playoff_weeks == [14, 15, 16, 17]
    assert _league({"playoff_week_start": 0}).playoff_weeks == []


def test_chopped_league_detected_and_chopped_rosters_dead():
    lg = _league(
        {"type": 3, "playoff_week_start": 0},
        rosters=[
            {"roster_id": 1, "owner_id": "u1", "players": ["a"], "settings": {}},
            {"roster_id": 2, "owner_id": "u2", "players": [], "settings": {}},
        ],
    )
    assert lg.chopped and [t.roster_id for t in lg.alive_teams()] == [1] and lg.last_week == 17


def test_my_roster_must_exist():
    with pytest.raises(sl.SleeperError):
        sl.parse_league("T", {"league_id": "9", "settings": {}}, [], [], "u1", {"week": 8, "season": "2026"})


def test_availability_rules():
    assert ros.availability("Out", 8, 8, 4) == 0.0 and ros.availability("Out", 9, 8, 4) == 1.0
    assert ros.availability("IR", 11, 8, 4) == 0.0 and ros.availability("IR", 12, 8, 4) == 1.0
    assert ros.availability("Doubtful", 8, 8, 4) == ros.DOUBTFUL_FACTOR
    assert ros.availability("Questionable", 8, 8, 4) == 1.0


def test_teams_playing_marks_byes():
    proj = pd.DataFrame(
        {
            "player_id": ["a", "b", "c"],
            "week": [9, 9, 9],
            "proj": [1, 2, 3],
            "team": ["DAL", "BUF", "KC"],
            "opponent": ["NYG", "", None],
        }
    )
    assert ros.teams_playing(proj) == {9: {"DAL"}}


def test_blend_weight_moves_from_projection_to_season_rate():
    rows = pd.DataFrame({"n": [0, 6], "season": [10.0, 10.0], "proj": [20.0, 20.0]})
    assert ros._blend(rows, 6).tolist() == [20.0, 15.0]


def test_rates_at_cutoff_use_only_known_weeks():
    act = pd.DataFrame({"player_id": ["a"] * 5, "week": [1, 2, 3, 4, 5], "pts": [1, 2, 3, 4, 50]})
    r = ros._rates_at(act, 4).loc["a"]
    assert r["n"] == 4 and r["season"] == 2.5 and r["last3"] == 3.0


def test_season_config(tmp_path):
    p = tmp_path / "season.toml"
    p.write_text(
        'sheet_id = "x"\n[[leagues]]\nname = "Work"\nleague_id = "1"\nusername = "sam"\n'
        '[[leagues]]\nname = "Home"\nleague_id = "2"\nusername = "sam"\n'
    )
    cfg = load_season_config(p)
    assert [lg.name for lg in select_leagues(cfg, "work", False)] == ["Work"]
    assert len(select_leagues(cfg, None, True)) == 2
    with pytest.raises(SeasonConfigError):
        select_leagues(cfg, None, False)
    with pytest.raises(SeasonConfigError):
        select_leagues(cfg, "nope", False)
    one = SeasonLongConfig(leagues=[LeagueConfig(name="A", league_id="1", username="s")])
    assert select_leagues(one, None, False)[0].name == "A"
