from decimal import Decimal

from dfs.models import ContestEntry, Lineup, Player, Position


def make_player(dk_id: str, position: Position, salary: int) -> Player:
    return Player(
        dk_id=dk_id,
        name=f"Player {dk_id}",
        position=position,
        team="BUF",
        salary=salary,
        proj_pts=15.5,
    )


def test_lineup_salary_and_proj_totals():
    players = [
        make_player("1", Position.QB, 8000),
        make_player("2", Position.RB, 7000),
    ]
    lineup = Lineup(label="test", players=players)
    assert lineup.salary_total() == 15000
    assert lineup.proj_total() == 31.0


def test_contest_entry_net_and_total_winnings():
    entry = ContestEntry(
        sport="NFL",
        game_type="Classic",
        entry_key="E1",
        entry="My Entry",
        contest_key="C1",
        contest_date="2026-01-05T20:20:00",
        entry_fee=Decimal("5.00"),
        winnings_non_ticket=Decimal("12.50"),
        winnings_ticket=Decimal("0"),
    )
    assert entry.total_winnings == Decimal("12.50")
    assert entry.net == Decimal("7.50")


def test_contest_entry_ignores_unknown_csv_columns():
    entry = ContestEntry(
        sport="NFL",
        game_type="Classic",
        entry_key="E1",
        entry="My Entry",
        contest_key="C1",
        contest_date="2026-01-05T20:20:00",
        some_future_dk_column="whatever",
    )
    assert entry.entry_key == "E1"
