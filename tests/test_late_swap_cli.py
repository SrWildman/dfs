"""`dfs lineups late-swap` end to end, on a stub sheet and a hand-made EdgeRaw (no network, no real sheet)."""

from datetime import UTC, datetime
from types import SimpleNamespace

import pandas as pd
import pytest

from dfs import cli
from dfs.models import ROSTER_SLOTS
from dfs.weekly_reset import LINEUPS_NAME_BLOCKS, PLAYER_POOL_HEADER_ROW, PLAYER_POOL_NAME_BLOCKS

# 1:30 pm ET on a Sunday: the 1 pm games are on, the 4:25 pm game is not.
NOW = datetime(2026, 10, 11, 17, 30, tzinfo=UTC)
EARLY, LATE = "2026-10-11T13:00:00Z", "2026-10-11T16:25:00Z"


def _row(name, pos, team, opp, salary, proj, game_start, game):
    return {
        "Name": name, "Position": pos, "Team": team, "Opp": opp, "Salary": salary, "ProjPts": proj,
        "AggPts": proj - 1, "GameID": game, "GameStart": game_start, "Avail": None, "Flags": None,
    }  # fmt: skip


def _edge():
    rows = [
        _row("Locked QB", "QB", "AAA", "BBB", 6000, 20.0, EARLY, "g1"),
        _row("Locked RB", "RB", "CCC", "DDD", 5000, 12.0, EARLY, "g2"),
        _row("Open RB", "RB", "EEE", "FFF", 5000, 11.0, LATE, "g3"),
        _row("Better RB", "RB", "GGG", "HHH", 5400, 15.0, LATE, "g4"),
        _row("Unpooled RB", "RB", "III", "JJJ", 5000, 18.0, LATE, "g5"),
        _row("Locked WR1", "WR", "KKK", "LLL", 5000, 12.0, EARLY, "g6"),
        _row("Locked WR2", "WR", "MMM", "NNN", 5000, 12.0, EARLY, "g7"),
        _row("Locked WR3", "WR", "OOO", "PPP", 5000, 12.0, EARLY, "g8"),
        _row("Locked TE", "TE", "QQQ", "RRR", 4000, 9.0, EARLY, "g9"),
        _row("Locked Flex", "WR", "SSS", "TTT", 4000, 9.0, EARLY, "g10"),
        _row("Locked DST", "DST", "UUU", "VVV", 3000, 8.0, EARLY, "g11"),
    ]
    return pd.DataFrame(rows)


LINEUP = [
    "Locked QB",
    "Locked RB",
    "Open RB",
    "Locked WR1",
    "Locked WR2",
    "Locked WR3",
    "Locked TE",
    "Locked Flex",
    "Locked DST",
]


class StubClient:
    def __init__(self, lineup, pool):
        self.lineup, self.pool = lineup, pool

    def describe(self):
        return ("Week X", "https://example.test/sheet")

    def batch_read_ranges(self, specs):  # the Cash/GPP markers on the Total rows: all blank (both)
        return [[] for _ in specs]

    def read_range(self, tab, a1):
        if tab == "Lineups":
            rows = [[""] for _ in range(LINEUPS_NAME_BLOCKS[-1][1] - 1)]
            for i, name in enumerate(self.lineup):
                rows[LINEUPS_NAME_BLOCKS[0][0] - 2 + i] = [name]
            return rows
        if a1.startswith(f"A{PLAYER_POOL_HEADER_ROW}:"):
            return [["Name", "Pos."]]
        first = PLAYER_POOL_NAME_BLOCKS[0][0]
        rows = [[""] for _ in range(PLAYER_POOL_NAME_BLOCKS[-1][1] - first + 1)]
        for i, name in enumerate(self.pool):
            rows[PLAYER_POOL_NAME_BLOCKS[0][0] - first + i] = [name]
        return rows


@pytest.fixture
def cfg():
    return SimpleNamespace(
        google_sheets=SimpleNamespace(),
        lineups=SimpleNamespace(builder_tab="Lineups", player_pool_tab="Player Pool", salary_cap=50000),
    )


@pytest.fixture(autouse=True)
def edge(monkeypatch):
    monkeypatch.setattr(cli.store, "load_current", lambda name: _edge())


def _run(cfg, capsys, *, pool, lineup=LINEUP, all_players=False, metric="ProjPts"):
    cli._run_late_swap(
        cfg, top=3, metric=metric, all_players=all_players, now=NOW, client=StubClient(lineup, pool)
    )
    return capsys.readouterr().out


def test_it_suggests_from_the_player_pool_only(cfg, capsys):
    out = _run(cfg, capsys, pool=["Better RB"])
    assert "Open slots: RB." in out and "Ranked by ProjPts" in out
    assert (
        "Better RB" in out and "Unpooled RB" not in out
    )  # the better-scoring unpooled RB is not in the pool
    assert "a. Best full re-fill: +4.0 pts" in out
    assert "LOCKED" in out and "OPEN" in out


def test_all_players_widens_the_search_to_the_rosterable_pool(cfg, capsys):
    out = _run(cfg, capsys, pool=["Better RB"], all_players=True)
    assert "Unpooled RB" in out  # +7 beats Better RB's +4


def test_an_empty_player_pool_says_so_instead_of_staying_silent(cfg, capsys):
    out = _run(cfg, capsys, pool=[])
    assert "Your Player Pool is empty" in out and "--all-players" in out
    assert "Nothing beats this lineup" in out


def test_the_metric_option_changes_what_it_ranks_by(cfg, capsys):
    out = _run(cfg, capsys, pool=["Better RB"], metric="AggPts")
    assert "Ranked by AggPts" in out


def test_a_lineup_with_every_slot_locked_is_skipped(cfg, capsys, monkeypatch):
    monkeypatch.setattr(cli.store, "load_current", lambda name: _edge().assign(GameStart=EARLY))
    out = _run(cfg, capsys, pool=["Better RB"])
    assert "No lineup currently has an open (not-yet-locked) slot" in out
    assert len(LINEUP) == len(ROSTER_SLOTS)
