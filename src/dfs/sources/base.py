"""Shared shape for every data source."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

import pandas as pd

from dfs import nfl_calendar


@dataclass
class SyncContext:
    week: int
    season: int

    @classmethod
    def current(cls, *, week: int | None = None, season: int | None = None) -> "SyncContext":
        return cls(
            week=week if week is not None else nfl_calendar.current_week(),
            season=season if season is not None else nfl_calendar.current_season(),
        )


class Source(ABC):
    name: str
    needs_auth: bool = False

    @abstractmethod
    def fetch(self, ctx: SyncContext) -> pd.DataFrame:
        """Return this source's data as a tidy DataFrame. Raise on failure --
        never return an empty/partial frame to signal an error."""
        raise NotImplementedError

    def to_sheet_rows(self, df: pd.DataFrame) -> list[list]:
        """Shape the DataFrame into rows to write to this source's sheet tab
        (header row included). Override for tabs with unusual layouts (e.g.
        nfl_odds' legacy 2-row header)."""
        return [list(df.columns)] + df.astype(object).where(df.notna(), "").values.tolist()
