"""ESPN's public box scores as DraftKings points so far (`dfs.live_scores`), on hand-made payloads."""

import json
from datetime import UTC, datetime

import httpx
import pytest

from dfs import live_scores as ls
from dfs.live_scores import Game, ScoresError


def cat(name, keys, *athletes):
    return {
        "name": name,
        "keys": keys,
        "athletes": [{"athlete": {"displayName": n}, "stats": s} for n, s in athletes],
    }


def team(abbr, nickname, tid):
    return {"team": {"abbreviation": abbr, "name": nickname, "id": tid}}


TWO_POINT_PLAY = (
    "Wally Catch 5 Yd pass from Pat Passer (Pat Passer Pass to Wally Catch for Two-Point Conversion)"
)


def summary(*, home_score=24, away_score=17, scoring=()):
    """KC (home) vs DEN (away): a 300-yard QB, a 100-yard RB with a fumble lost, a catcher with a 2-point try,
    a KC defense with a sack, an interception, a safety and a return touchdown."""
    players = [
        {
            "team": {"abbreviation": "KC"},
            "statistics": [
                cat(
                    "passing",
                    [
                        "completions/passingAttempts",
                        "passingYards",
                        "yardsPerPassAttempt",
                        "passingTouchdowns",
                        "interceptions",
                    ],
                    ("Pat Passer", ["25/30", "312", "10.4", "2", "1"]),
                ),
                cat(
                    "rushing",
                    [
                        "rushingAttempts",
                        "rushingYards",
                        "yardsPerRushAttempt",
                        "rushingTouchdowns",
                        "longRushing",
                    ],
                    ("Ron Runner", ["20", "104", "5.2", "1", "30"]),
                ),
                cat(
                    "receiving",
                    [
                        "receptions",
                        "receivingYards",
                        "yardsPerReception",
                        "receivingTouchdowns",
                        "longReception",
                        "receivingTargets",
                    ],
                    ("Wally Catch", ["6", "70", "11.7", "1", "25", "9"]),
                ),
                cat(
                    "fumbles", ["fumbles", "fumblesLost", "fumblesRecovered"], ("Ron Runner", ["1", "1", "0"])
                ),
                cat(
                    "defensive",
                    [
                        "totalTackles",
                        "soloTackles",
                        "sacks",
                        "tacklesForLoss",
                        "passesDefended",
                        "QBHits",
                        "defensiveTouchdowns",
                    ],
                    ("Dee Fender", ["5", "4", "1.5", "0", "0", "0", "1"]),
                ),
                cat(
                    "interceptions",
                    ["interceptions", "interceptionYards", "interceptionTouchdowns"],
                    ("Dee Fender", ["1", "40", "1"]),
                ),
            ],
        },
        {
            "team": {"abbreviation": "DEN"},
            "statistics": [
                cat(
                    "passing",
                    [
                        "completions/passingAttempts",
                        "passingYards",
                        "yardsPerPassAttempt",
                        "passingTouchdowns",
                        "interceptions",
                    ],
                    ("Bo Quarter", ["20/35", "210", "6.0", "1", "1"]),
                ),
                cat(
                    "fumbles", ["fumbles", "fumblesLost", "fumblesRecovered"], ("Bo Quarter", ["2", "1", "0"])
                ),
            ],
        },
    ]
    plays = [
        {"type": {"text": "Safety"}, "team": {"id": "1"}, "text": "Safety"},
        {
            "type": {"text": "Passing Touchdown"},
            "team": {"id": "1"},
            "text": TWO_POINT_PLAY,
        },
        {
            "type": {"text": "Rushing Touchdown"},
            "team": {"id": "1"},
            "text": "Ron Runner 3 Yd Rush (Two-Point Pass Conversion Failed)",
        },
        *scoring,
    ]
    return {
        "boxscore": {"teams": [team("KC", "Chiefs", "1"), team("DEN", "Broncos", "2")], "players": players},
        "header": {"competitions": [{"competitors": [
            {"team": {"abbreviation": "KC"}, "score": str(home_score)},
            {"team": {"abbreviation": "DEN"}, "score": str(away_score)},
        ]}]},
        "scoringPlays": plays,
    }  # fmt: skip


GAME = Game("401", "post", 4, 0.0, ("KC", "DEN"))


def test_progress_is_the_share_of_regulation_played():
    assert ls.game_progress("pre", 0, 0) == 0.0
    assert ls.game_progress("post", 4, 0) == 1.0
    assert ls.game_progress("in", 1, 900) == 0.0  # the whole first quarter ahead
    assert ls.game_progress("in", 2, 0) == pytest.approx(0.5)  # halftime
    assert ls.game_progress("in", 3, 450) == pytest.approx(0.625)
    assert ls.game_progress("in", 5, 600) == 1.0  # overtime counts as played


def test_offense_points_follow_draftkings_rules_with_bonuses():
    qb = {"pass_yd": 312, "pass_td": 2, "pass_int": 1, "two_pt": 1}
    assert ls.offense_points(qb) == pytest.approx(0.04 * 312 + 8 - 1 + 3 + 2)
    rb = {"rush_yd": 104, "rush_td": 1, "fum_lost": 1}
    assert ls.offense_points(rb) == pytest.approx(10.4 + 6 - 1 + 3)
    wr = {"rec": 6, "rec_yd": 99, "rec_td": 0}
    assert ls.offense_points(wr) == pytest.approx(6 + 9.9)  # 99 yards: no bonus
    assert ls.offense_points({"ret_td": 1}) == 6.0


def test_points_allowed_tiers_and_a_defense_only_scores_them_when_final():
    tiers = {0: 10, 6: 7, 7: 4, 13: 4, 14: 1, 20: 1, 21: 0, 27: 0, 28: -1, 34: -1, 35: -4, 50: -4}
    assert {pa: ls.points_allowed_score(pa) for pa in tiers} == {pa: float(v) for pa, v in tiers.items()}
    stats = {"sack": 3, "int": 1, "fum_rec": 1, "def_td": 1, "safety": 1, "points_allowed": 10}
    assert ls.dst_points(stats, final=False) == 3 + 2 + 2 + 6 + 2
    assert ls.dst_points(stats, final=True) == 15 + 4


def test_a_box_score_becomes_player_and_defense_stats():
    box = ls.parse_box(summary(), GAME)
    assert box.players["pat passer"]["pass_yd"] == 312 and box.players["pat passer"]["two_pt"] == 1
    assert box.players["wally catch"]["two_pt"] == 1  # the catcher of the good try scores too
    assert (
        box.players["ron runner"]["fum_lost"] == 1 and "two_pt" not in box.players["ron runner"]
    )  # failed try
    kc = box.teams["KC"]
    assert kc["sack"] == 1.5 and kc["int"] == 1
    assert kc["def_td"] == 1  # the interception return TD is already in defensiveTouchdowns: counted once
    assert kc["fum_rec"] == 1  # DEN's Bo Quarter lost a fumble
    assert kc["safety"] == 1
    assert kc["points_allowed"] == 17 - 0 - 2 * 1  # DEN's 17, less the safety KC scored on them
    assert box.nicknames["KC"] == "chiefs"
    # DEN's fumble recovery comes from KC's lost fumble
    assert box.teams["DEN"]["fum_rec"] == 1


def test_score_box_scores_defenses_under_their_nickname_and_the_tier_only_when_final():
    box = ls.parse_box(summary(), GAME)
    players, defenses = ls.score_box(box, GAME)
    assert players["ron runner"] == pytest.approx(10.4 + 6 - 1 + 3)
    final = defenses["chiefs"]
    live = ls.score_box(box, Game("401", "in", 3, 300.0, ("KC", "DEN")))[1]["chiefs"]
    assert final - live == ls.points_allowed_score(15)  # the tier is earned at the final whistle


def transport(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def scoreboard(*games):
    return {
        "events": [
            {
                "id": gid,
                "status": {"period": period, "clock": clock, "type": {"state": state}},
                "competitions": [{"competitors": [
                    {"homeAway": "home", "team": {"abbreviation": home}},
                    {"homeAway": "away", "team": {"abbreviation": away}},
                ]}],
            }
            for gid, state, period, clock, home, away in games
        ]
    }  # fmt: skip


def test_fetch_scores_reads_started_games_only_and_caches_final_ones(tmp_path):
    calls = []

    def handler(request):
        calls.append(str(request.url))
        if "scoreboard" in str(request.url):
            return httpx.Response(
                200, json=scoreboard(("401", "post", 4, 0, "KC", "DEN"), ("402", "pre", 0, 900, "WSH", "LA"))
            )
        return httpx.Response(200, json=summary())

    when = datetime(2026, 10, 25, 18, 0, tzinfo=UTC)
    scores = ls.fetch_scores(["20261025"], client=transport(handler), cache_dir=tmp_path, now=lambda: when)
    assert scores.progress == {"KC": 1.0, "DEN": 1.0, "WAS": 0.0, "LAR": 0.0}  # team codes as DK spells them
    assert scores.fpts["pat passer"] > 20 and "chiefs" in scores.fpts
    assert sum("summary" in c for c in calls) == 1  # the unstarted game has no box score to read
    assert (tmp_path / "401.json").exists() and scores.fetched_at == when
    calls.clear()
    again = ls.fetch_scores(["20261025"], client=transport(handler), cache_dir=tmp_path)
    assert sum("summary" in c for c in calls) == 0 and again.fpts == scores.fpts  # final box: from the cache


def test_a_game_in_progress_is_always_re_read(tmp_path):
    calls = []

    def handler(request):
        calls.append(str(request.url))
        if "scoreboard" in str(request.url):
            return httpx.Response(200, json=scoreboard(("401", "in", 2, 300, "KC", "DEN")))
        return httpx.Response(200, json=summary())

    for _ in range(2):
        scores = ls.fetch_scores(["20261025"], client=transport(handler), cache_dir=tmp_path)
    assert sum("summary" in c for c in calls) == 2 and not list(tmp_path.iterdir())
    assert scores.progress["KC"] == pytest.approx((900 + 600) / 3600)


def test_an_unreadable_scoreboard_is_a_scores_error_and_an_unreadable_box_is_a_note():
    with pytest.raises(ScoresError):
        ls.fetch_scores(["20261025"], client=transport(lambda r: httpx.Response(500)))

    def handler(request):
        if "scoreboard" in str(request.url):
            return httpx.Response(200, json=scoreboard(("401", "post", 4, 0, "KC", "DEN")))
        return httpx.Response(503)

    scores = ls.fetch_scores(["20261025"], client=transport(handler))
    assert scores.fpts == {} and "box score of KC-DEN not read" in scores.notes[0]


class Spec:
    def __init__(self, id, team, position="WR"):
        self.id, self.team, self.position = id, team, position


def test_specs_take_espns_points_by_name_and_then_by_a_near_name_on_the_same_team():
    scores = ls.LiveScores(
        {"mitchell tinsley": 7.9, "pat passer": 30.0},
        {"CIN": 1.0, "KC": 1.0, "BUF": 0.0},
        [],
        datetime(2026, 10, 25, tzinfo=UTC),
        by_team={"CIN": {"mitchell tinsley": 7.9}, "KC": {"pat passer": 30.0}},
    )
    specs = [
        Spec("Mitch Tinsley", "CIN"),  # DK says Mitch, ESPN Mitchell
        Spec("Pat Passer", "KC", "QB"),
        Spec("Mitch Tinsley", "BUF"),  # his game has not started: nothing to take
        Spec("Nobody Known", "KC"),  # no stats in the box score: left out, counts as 0
    ]
    assert ls.points_for_specs(scores, specs) == {"mitch tinsley": 7.9, "pat passer": 30.0}


def test_kickoff_dates_are_the_eastern_calendar_days():
    late = datetime(2026, 10, 26, 0, 20, tzinfo=UTC)  # 8:20 pm ET on the 25th
    early = datetime(2026, 10, 25, 17, 0, tzinfo=UTC)
    assert ls.kickoff_dates([late, early, None]) == ["20261025"]


def test_the_cache_file_is_the_payload(tmp_path):
    ls._write_cache(tmp_path, GAME, {"a": 1})
    assert json.loads((tmp_path / "401.json").read_text()) == {"a": 1}
    assert ls._read_cache(tmp_path, GAME) == {"a": 1}
    assert ls._read_cache(tmp_path / "none", GAME) is None
