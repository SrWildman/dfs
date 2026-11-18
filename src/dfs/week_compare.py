"""The Week page's compare rows: which EdgeRaw measures the compare tray shows, and which way is better.

Pure: no I/O. Two readers, so the page cannot disagree with itself:

- the **compare tray** (`week_views.compare_spec` / `compare_json`): 2-4 players side by side, the best value
  per row shaded green when the row has a stated direction;
- the **colour by position** of every page table (`week_shade.kinds`): a column with a direction is green
  when good and red when bad.

`HIGH` / `LOW` say which end of a row is better; `None` = no clear direction, never shaded. LOW rows are the
ones where "better" is not "bigger": Bust% and OppPosRank (1 = softest matchup). Salary and the ownership rows
are NOT shaded (Sam, 2026-10-22): which way is better depends on cash vs GPP. Spread, wind and text have no
clear direction either.

Until removal F9 (2026-11-17) these rows also drove the sheet's Compare tab (`compare_card.py`), built on the
Player tab's field specs (`player_card.py`); both tabs are gone and the rows live here.
"""

from __future__ import annotations

from dataclasses import dataclass

HIGH, LOW = "high", "low"  # which end of a row is better; None = no clear direction


@dataclass(frozen=True)
class Row:
    """One compared measure: its label, the EdgeRaw column it reads (or `Implied`, which the page derives:
    `(total - spread) / 2`) and which way is better."""

    label: str
    column: str
    better: str | None = HIGH

    @property
    def edge(self) -> bool:
        """An EdgeRaw column (not one the page derives)."""
        return self.column not in DERIVED


DERIVED = frozenset({"Implied"})

OWNERSHIP_NOTE = (
    "Own% is TFFB's projection once it publishes. GppOwn / CashOwn are estimates of what your contests own, "
    "OurOwn what your GPPs would own on CalPts: models, not published numbers."
)
OPPORTUNITY_NOTE = (
    "This season only, games he played before this slate. TPRR~ is a PROXY for targets per route run: "
    "routes are estimated as his offensive snaps x his team's dropback rate that game (no free in-season "
    "route counts exist). PriceGap = how many SDs more expected scoring (xFP) his role has than a typical "
    "player at his salary and position: positive = the opportunity is bigger than the price. Context, not "
    "a projection; scored in Model Check."
)
MATCHUP_NOTE = (
    "OppPosRank: 1 = softest. OppEPA: higher = softer for every position. OppPosRank is that defense against "
    "his position, OppEPA the defense overall, so they can disagree."
)

SECTIONS: list[tuple[str, list[Row]]] = [
    (
        "PLAYER",
        [
            Row("Team", "Team", None),
            Row("Pos", "Position", None),
            Row("Salary", "Salary", None),
            Row("Opp", "Opp", None),
            Row("Kickoff (ET)", "Kickoff", None),
            Row("Status", "Avail", None),
            Row("Flags", "Flags", None),
        ],
    ),
    (
        "PROJECTIONS",
        [
            Row("TFFB", "ProjPts"),
            Row("Sleeper", "SleeperPts"),
            Row("FantasyPros", "FantasyProsPts"),
            Row("ESPN", "EspnPts"),
            Row("Props", "PropPts"),
            Row("AggPts", "AggPts"),
            Row("CalPts", "CalPts"),
            Row("Hit3x%", "Hit3x%"),
            Row("Boom%", "Boom%"),
            Row("Bust%", "Bust%", LOW),
            Row("Ceil (model)", "CeilM"),
            Row("Val", "Val"),
            Row("ValAdj", "ValAdj"),
        ],
    ),
    (
        "OWNERSHIP",
        [
            Row("Own% (TFFB)", "Own%", None),
            Row("GppOwn (shadow)", "GppOwn", None),
            Row("CashOwn (shadow)", "CashOwn", None),
            Row("Leverage", "Leverage"),
        ],
    ),
    (
        "USAGE",
        [
            Row("Snap% L5", "Snap% L5"),
            Row("Targets L5", "Tgt L5"),
            Row("Carries L5", "Car L5"),
            Row("DK pts L5", "DK L5"),
            Row("Target share", "Tgt%"),
            Row("xFP / game", "xFP Szn"),
            Row("xFP last 3", "xFP/G"),
            Row("xFP trend", "xFP trend"),
            Row("Receiving share of xFP", "xFP Rec%"),
        ],
    ),
    (
        "OPPORTUNITY PER DOLLAR",
        [
            Row("TPRR~ (proxy)", "TPRR~"),
            Row("xFP per $1k", "xFP/$"),
            Row("Targets per $1k", "Tgt/$"),
            Row("HVT per $1k (RB)", "HVT/$"),
            Row("PriceGap (SD)", "PriceGap"),
        ],
    ),
    (
        "MATCHUP AND GAME",
        [
            Row("Total", "OverUnder"),
            Row("Spread", "Spread", None),
            Row("Implied (team)", "Implied"),
            Row("OppPosRank", "OppPosRank", LOW),
            Row("OppEPA (whole defense)", "OppEPA"),
            Row("Wind (mph)", "Wind", None),
        ],
    ),
]

SAME_POSITION_NOTE = (
    "Different positions picked: compare within a position (a QB has no targets, and a RB's price is not a "
    "TE's)."
)
HIGHLIGHT_NOTE = (
    "Green = the best of the picked players, when two or more have a number and they differ. Lowest is best "
    "for Bust% and OppPosRank (1 = softest); highest for the projections, Val, Leverage, usage and "
    "opportunity rows. Salary, ownership (the better side depends on cash vs GPP), spread, wind, team, "
    "status and flags are never shaded."
)
NOTES = {
    "PROJECTIONS": (
        "Sources side by side. TFFB, Sleeper and FantasyPros feed AggPts; CalPts is the calibrated blend; "
        "ESPN and Props are scored in Model Check and join the blend once they clear its gate."
    ),
    "OWNERSHIP": OWNERSHIP_NOTE,
    "USAGE": (
        "Last five games he played (Snap%, targets, carries, real DK points) and this season's expected "
        "fantasy points (xFP). The game by game rows are in each player's drawer (click his name)."
    ),
    "OPPORTUNITY PER DOLLAR": OPPORTUNITY_NOTE,
    "MATCHUP AND GAME": MATCHUP_NOTE,
}
