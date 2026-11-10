"""R8, the QB downgrade (`research.r8_qb_downgrade`): events and controls on a fixture season, the pooled
groups, what ships and the loader."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from dfs.research import r1_redistribution as r1
from dfs.research import r8_qb_downgrade as r8


def season(
    missed: dict[str, list[int]] | None = None, weeks: int = 8, teams: int = 4
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """`(stats, games)`: each team a QB (and a backup who throws only when the QB misses), two WRs, a TE and a
    RB, every week; `missed` = team -> weeks its starter sits."""
    missed = missed or {}
    rows, games = [], []
    names = [f"T{t}" for t in range(teams)]
    for wk in range(1, weeks + 1):
        for a in range(0, teams, 2):
            games.append(
                {
                    "season": 2020,
                    "week": wk,
                    "game_id": f"{wk}-{a}",
                    "home_team": names[a],
                    "away_team": names[a + 1],
                }
            )
        for team in names:
            out = wk in missed.get(team, [])

            def add(pid, pos, team=team, wk=wk, **kw):
                rows.append(
                    {
                        "player_id": f"{team}-{pid}",
                        "player_display_name": f"{team} {pid}",
                        "position": pos,
                        "team": team,
                        "season": 2020,
                        "week": wk,
                        "targets": 0.0,
                        "carries": 0.0,
                        "attempts": 0.0,
                        **kw,
                    }
                )

            if not out:
                add("QB1", "QB", attempts=35.0)
            else:
                add("QB2", "QB", attempts=30.0)
            add("WR1", "WR", targets=9.0)
            add("WR2", "WR", targets=6.0)
            add("TE1", "TE", targets=4.0)
            add("RB1", "RB", carries=15.0, targets=3.0)
    return pd.DataFrame(rows), pd.DataFrame(games)


def test_a_missed_game_by_the_established_starter_is_an_event_and_his_starts_are_controls():
    sp, games = season({"T0": [6]})
    tg = r1.team_game_index(games)
    qg = r8.qb_games(r1.offense_rows(sp, tg), tg)
    t0 = qg[qg["team"] == "T0"].set_index("week")
    assert bool(t0.at[6, "event"]) and t0.at[6, "regular"] == "T0-QB1"
    assert not t0.loc[[4, 5], "event"].any()
    assert 7 not in t0.index  # the week after: the starter of week 6 was the backup, not established
    assert 8 in t0.index and not t0.at[8, "event"]
    assert qg[qg["team"] == "T1"]["event"].sum() == 0


def test_event_rows_label_the_rotation_and_carry_um_residuals():
    sp, games = season({"T0": [6]})
    resid = sp[["player_id", "season", "week", "position"]].rename(columns={"player_id": "gsis_id"})
    resid = resid.assign(
        resid=np.where(resid["gsis_id"].str.startswith("T0-W") & (resid["week"] == 6), -3.0, 0.0)
    )
    rows = r8.event_rows(sp, games, resid)
    ev = rows[rows["event"]]
    assert set(ev["label"]) == {"WR1", "WR2", "TE1", "RB1"} and set(ev["abs_label"]) == {"QB1"}
    assert ev.loc[ev["label"] == "WR1", "resid"].iloc[0] == -3.0
    assert rows.loc[~rows["event"], "abs_label"].isna().all()


def test_only_a_group_whose_halves_agree_ships_and_wr_te_read_the_pooled_number():
    table = pd.DataFrame(
        [
            {
                "group": "pass catchers",
                "n_events": 600,
                "n_control": 9000,
                "effect": -0.64,
                "se": 0.28,
                "lo90": -1.1,
                "hi90": -0.2,
                "t": -2.3,
                "fit_n": 300,
                "fit_effect": -0.44,
                "test_n": 300,
                "test_effect": -0.83,
                "agree": True,
            },
            {
                "group": "RB",
                "n_events": 300,
                "n_control": 4000,
                "effect": -0.40,
                "se": 0.41,
                "lo90": -1.1,
                "hi90": 0.3,
                "t": -1.0,
                "fit_n": 140,
                "fit_effect": -0.96,
                "test_n": 160,
                "test_effect": 0.08,
                "agree": False,
            },
        ]
    )
    out = r8.shipped(table).set_index("position")["points"]
    assert out["WR"] == pytest.approx(-0.64) and out["TE"] == pytest.approx(-0.64) and out["RB"] == 0.0


def test_the_loader_reads_the_shipped_points(tmp_path):
    r8._load.cache_clear()
    path = tmp_path / "qb.csv"
    pd.DataFrame({"position": ["WR", "TE", "RB"], "points": [-0.64, -0.64, 0.0]}).to_csv(path, index=False)
    assert r8.load_table(path) == {"WR": -0.64, "TE": -0.64}
    assert r8.load_table(tmp_path / "missing.csv") == {}
    assert r8.load_table() == {"WR": pytest.approx(-0.642143), "TE": pytest.approx(-0.642143)}  # the repo's
