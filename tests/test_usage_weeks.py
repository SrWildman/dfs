"""The usage view's two files (Plan O item 6): every game per player and per team, `dfs.usage_weeks`, and the
two sheetless sources that save them."""

import pandas as pd
import pytest
from test_player_card import _pbp, _stat

from dfs import usage_weeks
from dfs.sources import SOURCES
from dfs.sources import usage_weeks as src
from dfs.sources.base import SyncContext


def _stats():
    rows = []
    for w in (1, 2):
        rows += [
            {
                **_stat("w1", "Wide One", "WR", "LA", w, "SF", targets=6, team_air=100),
                "receiving_air_yards": 50,
            },
            {
                **_stat("w2", "Wide Two", "WR", "LA", w, "SF", targets=2, team_air=0),
                "receiving_air_yards": 10,
            },
            _stat("q1", "Quarter Back", "QB", "LA", w, "SF", carries=2, team_air=100),
        ]
    return pd.DataFrame(rows)


def _plays():
    base = {
        "posteam": "LA",
        "defteam": "SF",
        "game_id": "g",
        "qtr": 1,
        "wp": 0.5,
        "half_seconds_remaining": 900,
    }
    plays = []
    for w in (1, 2):
        for i, (yard, kind, drive) in enumerate(
            [(40, "pass", 1), (15, "pass", 1), (8, "run", 1), (60, "run", 2)]
        ):
            plays.append(
                {
                    **base,
                    "week": w,
                    "drive": drive,
                    "play_id": i,
                    "game_seconds_remaining": 3000 - i * 30,
                    "yardline_100": yard,
                    "play_type": kind,
                    "pass": int(kind == "pass"),
                    "rush": int(kind == "run"),
                    "pass_oe": 5.0 if kind == "pass" else -5.0,
                    "receiver_player_id": "w1" if kind == "pass" else None,
                    "rusher_player_id": "q1" if kind == "run" else None,
                }
            )
    plays.append(
        {**base, "week": 1, "play_type": "pass", "pass": 1, "two_point_attempt": 1, "drive": 1, "play_id": 9}
    )
    return _pbp(plays)


def test_player_weeks_has_every_game_with_shares_wopr_and_red_zone_and_dk_team_codes():
    out = usage_weeks.player_weeks(_stats(), _plays(), None)
    assert list(out.columns) == usage_weeks.PLAYER_COLUMNS
    w1 = out[out.Name == "Wide One"].iloc[0]
    assert len(out[out.Name == "Wide One"]) == 2  # every game, not the last five
    assert (w1.Team, w1.Opp) == ("LAR", "SF")  # nflverse's LA is DraftKings' LAR
    assert w1["Tgt%"] == pytest.approx(0.75)  # 6 of the team's 8
    assert w1["Air%"] == pytest.approx(0.25)  # 50 of the team's 200 passing air yards
    assert w1.WOPR == pytest.approx(1.5 * 0.75 + 0.7 * 0.25, abs=1e-3)
    assert (w1.RZ, w1.HVT) == (1, 0)  # the target from the 15: inside the 20, not the 10
    qb = out[out.Name == "Quarter Back"].iloc[0]
    assert pd.isna(qb["Tgt%"]) and pd.isna(qb.WOPR)  # a QB's targets are not a role
    assert (qb.RZ, qb.HVT) == (1, 1)  # the carry from the 8
    assert out["Snap%"].isna().all()  # no snap file: blank, never 0


def test_team_weeks_counts_plays_dropbacks_and_red_zone_trips_per_game():
    out = usage_weeks.team_weeks(_plays(), _stats())
    assert list(out.columns) == usage_weeks.TEAM_COLUMNS
    assert set(out.Team) == {"LAR"} and len(out) == 2
    wk = out.iloc[0]
    assert wk.Plays == 4  # the two-point try is not a play
    assert wk["Pass%"] == pytest.approx(0.5)
    assert wk.RZTrips == 1  # two snaps inside the 20, one drive
    assert wk.Opp == "SF" and wk.Tgt == 8 and wk.Car == 2
    assert usage_weeks.team_weeks(None).empty


def test_the_two_sources_are_sheetless_and_fail_soft(monkeypatch):
    assert SOURCES["usage_weeks"].uploads_to_sheet is False
    assert SOURCES["team_weeks"].uploads_to_sheet is False
    names = list(SOURCES)
    assert names.index("usage_weeks") > names.index("usage")  # it reads the stats `usage` saved

    def offline(*a, **k):
        raise src.NflverseUsageFetchError("offline")

    src._pbp.cache_clear()
    monkeypatch.setattr(src, "_stats", offline)
    monkeypatch.setattr(src, "_download", offline)
    ctx = SyncContext(week=9, season=2026)
    assert list(src.UsageWeeksSource().fetch(ctx).columns) == usage_weeks.PLAYER_COLUMNS
    assert src.TeamWeeksSource().fetch(ctx).empty
    src._pbp.cache_clear()
