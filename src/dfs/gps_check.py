"""Round 5 item 5c: GPS's `Implied Total` is Vegas, not a model.

Checked against Kyle Borgognoni's Week 3 worksheet: `Implied Total`
matched the Vegas total/spread exactly for 12 of 16 games, and the other
four were two PAIRS OF ROWS SWAPPED in the source (NE@JAX <-> KC@MIA,
TEN@NYG <-> SEA@WAS) -- our own odds snapshots had those lines set since
Monday, before the article went up. So `ImpliedTotal` carries no model
signal; `ModelImplied` (EdgeRaw) and `Model Tot`/`Tot Δ`/`Model Spd`/`Spd Δ`
(Slate Grid/Board) were removed. It is kept in the `tffb_gps` snapshot as a
SANITY CHECK only: a row swap in the source shows up here as a big miss
against Vegas, and `GPS` (the 1-5 score for that game) would then be
describing the wrong game.

Pure and offline-testable. Vegas implied points come from the same
`nflverse_games` rows Slate Grid reads: `Spread` is positive when the HOME
team is favoured (see `sources/nflverse_games.py`), so

    home implied = (Total + Spread) / 2      away implied = (Total - Spread) / 2
"""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from dfs.player_join import normalize_team

# Either team's GPS `ImpliedTotal` differing from its Vegas implied total by
# more than this many points flags the game. Sam's number (Round 5 5c).
GPS_IMPLIED_MISMATCH_PTS = 1.5


@dataclass(frozen=True)
class GpsMismatch:
    away: str
    home: str
    away_gps: float
    home_gps: float
    away_vegas: float
    home_vegas: float

    @property
    def game(self) -> str:
        return f"{self.away} @ {self.home}"

    @property
    def worst_miss(self) -> float:
        return max(abs(self.away_gps - self.away_vegas), abs(self.home_gps - self.home_vegas))


def find_gps_mismatches(
    gps: pd.DataFrame, games: pd.DataFrame, *, threshold: float = GPS_IMPLIED_MISMATCH_PTS
) -> list[GpsMismatch]:
    """Games where either team's GPS `ImpliedTotal` is off Vegas by more than
    `threshold`. A game missing a Vegas line or a GPS row for either team is
    skipped (nothing to compare), never flagged."""
    implied = {
        normalize_team(t): float(v)
        for t, v in zip(gps["Team"], pd.to_numeric(gps["ImpliedTotal"], errors="coerce"), strict=True)
        if pd.notna(v)
    }
    out: list[GpsMismatch] = []
    for _, g in games.iterrows():
        total = pd.to_numeric(g.get("Total"), errors="coerce")
        spread = pd.to_numeric(g.get("Spread"), errors="coerce")
        away, home = normalize_team(g["Away"]), normalize_team(g["Home"])
        if pd.isna(total) or pd.isna(spread) or away not in implied or home not in implied:
            continue
        mismatch = GpsMismatch(
            away=away,
            home=home,
            away_gps=implied[away],
            home_gps=implied[home],
            away_vegas=(total - spread) / 2,
            home_vegas=(total + spread) / 2,
        )
        if mismatch.worst_miss > threshold:
            out.append(mismatch)
    return sorted(out, key=lambda m: m.worst_miss, reverse=True)
