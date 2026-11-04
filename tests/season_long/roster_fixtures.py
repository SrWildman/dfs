"""Sam's real rosters (2026-11-04), shrunk to a few weeks, for the roster-rule tests: Bourbon (a QB, a K and a
DEF, one IR slot full), Agile (a kicker on IR, one IR slot full), Mega (two IR slots full, no K slot) and
Choppa (no QB at all). No network, no sheet: points per week are typed here.

A player is `(name, position, injury status, points for each of WEEKS)`; a 0 is a bye (or a week out).
"""

from __future__ import annotations

import pandas as pd

from dfs.season_long import sleeper_league as sl
from dfs.season_long import team_value as tvm
from dfs.season_long.config import LeagueConfig, RosterConfig, SeasonLongConfig
from dfs.season_long.league_view import LeagueView, positions_of
from dfs.season_long.ros import RosResult

WEEKS = [8, 9, 10, 11]
Player = tuple[str, str, str, list[float]]


def flat(pts: float) -> list[float]:
    return [pts] * len(WEEKS)


def bye(pts: float, week: int = 8) -> list[float]:
    return [0.0 if w == week else pts for w in WEEKS]


def pad(n: int, prefix: str = "bn") -> dict[str, Player]:
    """`n` bench bodies who never start, so a roster is full and an add needs a drop."""
    return {f"{prefix}{i}": (f"Bench {prefix}{i}", "WR", "", flat(2.0)) for i in range(n)}


def _filler(prefix: str) -> dict[str, Player]:
    """A plain team for the other managers: one of each starter, so every lineup is full."""
    base = {"QB": 17, "RB": 11, "WR": 12, "TE": 8, "K": 7, "DEF": 7}
    out: dict[str, Player] = {}
    for pos, n in {"QB": 1, "RB": 3, "WR": 3, "TE": 1, "K": 1, "DEF": 1}.items():
        for i in range(n):
            out[f"{prefix}_{pos}{i}"] = (f"{prefix}{pos}{i}", pos, "", flat(base[pos] - i))
    return out


def build(
    mine: dict[str, Player],
    free: dict[str, Player],
    *,
    slots: list[str],
    bench: int,
    reserve_slots: int,
    reserve: list[str],
    chopped: bool = False,
    raw_settings: dict | None = None,
    roster: RosterConfig | None = None,
    others: int = 2,
) -> LeagueView:
    """Sam (roster 1) holds `mine` (`reserve` of them in IR slots); `free` are the free agents."""
    players: dict[str, Player] = {**mine, **free}
    teams = [
        sl.Team(
            1,
            "u1",
            "sam",
            "Sam",
            list(mine),
            [],
            3,
            4,
            0,
            700.0,
            100,
            reserve=[p for p in reserve if p in mine],
        )
    ]
    for rid in range(2, 2 + others):
        roster_players = _filler(f"t{rid}")
        players.update(roster_players)
        teams.append(
            sl.Team(rid, f"u{rid}", f"m{rid}", f"Team {rid}", list(roster_players), [], 3, 4, 0, 650.0, 100)
        )
    lg = sl.League(
        name="Test", league_id="1", title="Test League", season=2026, scoring={}, slots=list(slots),
        bench_slots=bench, reserve_slots=reserve_slots, playoff_week_start=0 if chopped else 11,
        playoff_teams=2, playoff_round_type=0, trade_deadline=11, waiver_budget=100, chopped=chopped,
        league_type=3 if chopped else 0, current_week=8, last_scored_week=7, teams=teams, my_roster_id=1,
        raw_settings={"reserve_allow_out": 1, **(raw_settings or {})},
    )  # fmt: skip
    rows = [
        {"player_id": pid, "name": name, "position": pos, "fantasy_positions": (pos,), "team": "NFL",
         "injury_status": status, "search_rank": 1, "depth": 1}
        for pid, (name, pos, status, _) in players.items()
    ]  # fmt: skip
    table = pd.DataFrame(rows).set_index("player_id")
    weekly = pd.DataFrame(
        [
            (pid, w, float(p[w_i]), 0.3 * p[w_i])
            for pid, (_, _, _, p) in players.items()
            for w_i, w in enumerate(WEEKS)
        ],
        columns=["player_id", "week", "pts", "sd"],
    )
    summary = table[["name", "position", "team", "injury_status", "search_rank"]].copy()
    summary["ros_pts"] = weekly.groupby("player_id")["pts"].sum()
    summary["ros_weighted"] = summary["ros_pts"]
    summary["playoff_pts"] = 0.0
    summary["next_week"] = weekly[weekly["week"] == WEEKS[0]].set_index("player_id")["pts"]
    summary["games_left"] = (weekly["pts"] > 0).groupby(weekly["player_id"]).sum()
    summary["games"], summary["season_rate"] = 7, summary["next_week"]
    cfg = SeasonLongConfig(
        leagues=[LeagueConfig(name="Test", league_id="1", username="u1")],
        starter_miss_rate=0.0,
        survival_draws=400,
        roster=roster or RosterConfig(),
    )
    v = LeagueView(
        lg, table, RosResult(weekly, summary, 4.0, {}, []), {}, {}, {}, None, positions_of(table), cfg
    )
    for team in lg.alive_teams():
        v.values[team.roster_id] = v.team_value(team.roster_id, team.players)
    v.strengths = {rid: tvm.slot_strength(x, weekly, lg.slots) for rid, x in v.values.items()}
    v.needs = tvm.needs(v.strengths)
    return v


# -- Bourbon: Daniels, Little, the Broncos; DeVonta Smith in the one IR slot ----------------------------
BOURBON_SLOTS = ["QB", "RB", "RB", "WR", "WR", "TE", "FLEX", "FLEX", "K", "DEF"]


def bourbon_mine(daniels: list[float] | None = None) -> dict[str, Player]:
    return {
        "daniels": ("Jayden Daniels", "QB", "", daniels or flat(22)),
        "rb1": ("RB One", "RB", "", flat(15)),
        "rb2": ("RB Two", "RB", "", flat(14)),
        "corum": ("Blake Corum", "RB", "", flat(1)),
        "wr1": ("WR One", "WR", "", flat(16)),
        "wr2": ("WR Two", "WR", "", flat(15)),
        "wr3": ("WR Three", "WR", "", flat(12)),
        "wr4": ("WR Four", "WR", "", flat(11)),
        "te1": ("TE One", "TE", "", flat(9)),
        "little": ("Cam Little", "K", "", flat(8)),
        "broncos": ("Denver Broncos", "DEF", "", flat(9)),
        "smith": ("DeVonta Smith", "WR", "Out", flat(0)),
        **pad(4),
    }


def bourbon_free() -> dict[str, Player]:
    return {
        "young": ("Bryce Young", "QB", "", flat(19)),
        "stroud": ("C.J. Stroud", "QB", "", flat(20)),
        "jaguars": ("Jacksonville Jaguars", "DEF", "", flat(8.5)),
        "texans": ("Houston Texans", "DEF", "", flat(8)),
        "wr9": ("WR Nine", "WR", "", flat(13)),
    }


def bourbon(daniels: list[float] | None = None, **kw) -> LeagueView:
    return build(
        bourbon_mine(daniels), bourbon_free(), slots=BOURBON_SLOTS, bench=5, reserve_slots=1,
        reserve=["smith"], **kw,
    )  # fmt: skip


# -- Agile: Tyler Loop (K, on IR) and Jordyn Tyson in the one IR slot ----------------------------
AGILE_SLOTS = ["QB", "RB", "RB", "WR", "WR", "TE", "FLEX", "K", "DEF"]


def agile(reserve_slots: int = 1, loop_status: str = "IR", **kw) -> LeagueView:
    mine = {
        "qb": ("Agile QB", "QB", "", flat(20)),
        "rb1": ("RB One", "RB", "", flat(15)),
        "rb2": ("RB Two", "RB", "", flat(14)),
        "wr1": ("WR One", "WR", "", flat(16)),
        "wr2": ("WR Two", "WR", "", flat(15)),
        "wr3": ("WR Three", "WR", "", flat(12)),
        "bernard": ("Germie Bernard", "WR", "", flat(1)),
        "te1": ("TE One", "TE", "", flat(9)),
        "loop": ("Tyler Loop", "K", loop_status, [0.0, 0.0, 0.0, 8.0]),
        "def1": ("Agile DEF", "DEF", "", flat(8)),
        "tyson": ("Jordyn Tyson", "WR", "IR", flat(0)),
        **pad(1),
    }
    free = {
        "fairbairn": ("Ka'imi Fairbairn", "K", "", flat(9)),
        "bass": ("Tyler Bass", "K", "", flat(8.5)),
        "wr9": ("WR Nine", "WR", "", flat(10)),
    }
    return build(
        mine, free, slots=AGILE_SLOTS, bench=2, reserve_slots=reserve_slots, reserve=["tyson"], **kw
    )  # fmt: skip


# -- Mega: Caleb Williams, the Commanders; Henderson (Out) and Tate in the two IR slots; no K slot ----------
MEGA_SLOTS = ["QB", "RB", "RB", "WR", "WR", "TE", "FLEX", "FLEX", "DEF"]


def mega(texans: list[float] | None = None, **kw) -> LeagueView:
    mine = {
        "williams": ("Caleb Williams", "QB", "", flat(20)),
        "rb1": ("RB One", "RB", "", flat(15)),
        "rb2": ("RB Two", "RB", "", flat(14)),
        "wr1": ("WR One", "WR", "", flat(16)),
        "wr2": ("WR Two", "WR", "", flat(15)),
        "wr3": ("WR Three", "WR", "", flat(12)),
        "wr4": ("WR Four", "WR", "", flat(11)),
        "te1": ("TE One", "TE", "", flat(9)),
        "bench1": ("Bench One", "RB", "", flat(1)),
        "commanders": ("Washington Commanders", "DEF", "", flat(9)),
        "henderson": ("TreVeyon Henderson", "RB", "Out", flat(0)),
        "tate": ("Carnell Tate", "WR", "Out", flat(0)),
        **pad(4),
    }
    free = {
        "mccarthy": ("J.J. McCarthy", "QB", "", flat(17)),
        "murray": ("Kyler Murray", "QB", "", flat(18)),
        "texans": ("Houston Texans", "DEF", "", texans or flat(8)),
        "wr9": ("WR Nine", "WR", "", flat(10)),
    }
    return build(
        mine, free, slots=MEGA_SLOTS, bench=5, reserve_slots=2, reserve=["henderson", "tate"],
        raw_settings={"reserve_allow_doubtful": 1}, **kw,
    )  # fmt: skip


# -- Choppa: no QB on the roster; Montgomery in the IR slot ----------------------------
CHOPPA_SLOTS = ["QB", "RB", "RB", "WR", "WR", "TE", "FLEX", "FLEX"]


def choppa(**kw) -> LeagueView:
    mine = {
        "rb1": ("RB One", "RB", "", flat(15)),
        "rb2": ("RB Two", "RB", "", flat(14)),
        "wr1": ("WR One", "WR", "", flat(16)),
        "wr2": ("WR Two", "WR", "", flat(15)),
        "wr3": ("WR Three", "WR", "", flat(12)),
        "te1": ("TE One", "TE", "", flat(9)),
        "bench1": ("Bench One", "WR", "", flat(5)),
        "bench2": ("Bench Two", "RB", "", flat(2)),
        "montgomery": ("David Montgomery", "RB", "Out", flat(0)),
    }
    free = {
        "mccarthy": ("J.J. McCarthy", "QB", "", flat(19)),
        "stafford": ("Matthew Stafford", "QB", "", flat(21)),
    }
    return build(
        mine, free, slots=CHOPPA_SLOTS, bench=0, reserve_slots=1, reserve=["montgomery"], chopped=True,
        raw_settings={"reserve_allow_cov": 1}, others=3, **kw,
    )  # fmt: skip
