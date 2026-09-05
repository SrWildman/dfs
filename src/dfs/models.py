"""Core data shapes shared across sources, sheets, lineups, and bankroll.

NFL DraftKings Classic contests only (see rebuild plan: NFL-only scope).
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import Enum

from pydantic import BaseModel, ConfigDict


class Position(str, Enum):
    QB = "QB"
    RB = "RB"
    WR = "WR"
    TE = "TE"
    DST = "DST"


# DK Classic NFL roster slots, in export-column order.
ROSTER_SLOTS: tuple[str, ...] = (
    "QB",
    "RB",
    "RB",
    "WR",
    "WR",
    "WR",
    "TE",
    "FLEX",
    "DST",
)

FLEX_ELIGIBLE: frozenset[Position] = frozenset(
    {Position.RB, Position.WR, Position.TE}
)


class Player(BaseModel):
    model_config = ConfigDict(frozen=True)

    dk_id: str
    name: str
    position: Position
    team: str
    salary: int
    proj_pts: float | None = None
    proj_own: float | None = None


class Slate(BaseModel):
    draft_group_id: str
    week: int
    season: int
    start_time: datetime | None = None
    players: list[Player]

    def player(self, dk_id: str) -> Player | None:
        return next((p for p in self.players if p.dk_id == dk_id), None)


class Lineup(BaseModel):
    """A single built lineup, in DK slot order (see ROSTER_SLOTS)."""

    label: str
    players: list[Player]

    def salary_total(self) -> int:
        return sum(p.salary for p in self.players)

    def proj_total(self) -> float:
        return sum(p.proj_pts or 0.0 for p in self.players)


class ContestEntry(BaseModel):
    """One row of DK contest-entry history (used for both results and bankroll)."""

    model_config = ConfigDict(extra="ignore")

    sport: str
    game_type: str
    entry_key: str
    entry: str
    contest_key: str
    contest_date: datetime
    place: int | None = None
    points: float | None = None
    winnings_non_ticket: Decimal = Decimal("0")
    winnings_ticket: Decimal = Decimal("0")
    contest_entries: int | None = None
    entry_fee: Decimal = Decimal("0")
    prize_pool: Decimal = Decimal("0")
    places_paid: int | None = None

    @property
    def total_winnings(self) -> Decimal:
        return self.winnings_non_ticket + self.winnings_ticket

    @property
    def net(self) -> Decimal:
        return self.total_winnings - self.entry_fee
