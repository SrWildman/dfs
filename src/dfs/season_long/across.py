"""This week across leagues: every lineup alert, pickup and trade idea in one list, in priority order.

Priority is stated, not tuned:

1. `SWAP` alerts first (an empty slot, a bye, an OUT starter: points lost this week if nothing is done);
2. `LOOK` alerts (Questionable, a better bench option), and in a chopped league a real chance of being
   chopped (25% or more);
3. pickups and trade ideas, biggest first. Leagues count in different units (team-value points; weeks
   survived), so size is the change over the SPREAD of the league's teams (the standard deviation of team
   value
   there, expected weeks survived in a chopped league): "+0.5" means half the distance between a typical
   team and the next.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

CHOP_RISK_ALERT = 0.25
PICKUPS_PER_LEAGUE = 3
TRADES_PER_LEAGUE = 3
TIER_SWAP, TIER_LOOK, TIER_MOVE = 0, 1, 2
TIER_NAME = {TIER_SWAP: "Lineup", TIER_LOOK: "Lineup", TIER_MOVE: ""}


@dataclass
class Item:
    tier: int
    size: float  # in league spreads (nan for alerts)
    league: str
    kind: str  # Lineup, Pickup, Trade
    text: str
    detail: str = ""
    cue: str = ""


def league_spread(run) -> float:
    """Standard deviation of the teams' base objective (value, or expected weeks survived when chopped)."""
    base = run.ev.base()
    chopped = run.view.league.chopped
    vals = [(o.expected_weeks if chopped else o.value) for o in base.values()]
    sd = float(np.std(vals)) if len(vals) > 1 else 0.0
    return sd if sd > 1e-9 else 1.0


def items(runs: list) -> list[Item]:
    out: list[Item] = []
    for run in runs:
        v, lg = run.view, run.view.league
        spread = league_spread(run)
        for a in run.alerts:
            tier = TIER_SWAP if a.cue == "SWAP" else TIER_LOOK
            out.append(
                Item(
                    tier,
                    math.nan,
                    lg.name,
                    "Lineup",
                    f"{a.slot} {a.player}: {a.swap or a.reason}",
                    a.reason if a.swap else "",
                    a.cue,
                )  # fmt: skip
            )
        if lg.chopped:
            o = run.ev.base()[lg.my_roster_id]
            risk = 1 - o.p_survive_now
            if risk >= CHOP_RISK_ALERT:
                out.append(
                    Item(
                        TIER_LOOK,
                        math.nan,
                        lg.name,
                        "Lineup",
                        f"{risk:.0%} chance of being chopped this week: start for the floor",
                        "",
                        "LOOK",
                    )  # fmt: skip
                )
        for p in run.pickups[:PICKUPS_PER_LEAGUE]:
            gain = p.expected_weeks if lg.chopped else p.value
            if not gain or gain <= 0 or (isinstance(gain, float) and math.isnan(gain)):
                continue
            drop = v.name(p.drop) if p.drop else "an open spot"
            out.append(
                Item(
                    TIER_MOVE,
                    gain / spread,
                    lg.name,
                    "Pickup",
                    f"add {v.name(p.player_id)}, drop {drop}",
                    f"{p.bid.text(lg.me.faab_left)}; {p.note}".strip("; "),
                )  # fmt: skip
            )
        for idea in run.ideas[:TRADES_PER_LEAGUE]:
            a = idea.analysis
            gain = (a.me.after.expected_weeks - a.me.before.expected_weeks) if lg.chopped else a.me.delta
            if gain <= 0:
                continue
            give = ", ".join(
                [v.name(x) for x in a.give.players] + ([f"${a.give.faab} FAAB"] if a.give.faab else [])
            )
            get = ", ".join(
                [v.name(x) for x in a.get.players] + ([f"${a.get.faab} FAAB"] if a.get.faab else [])
            )
            out.append(
                Item(
                    TIER_MOVE,
                    gain / spread,
                    lg.name,
                    "Trade",
                    f"give {give or '-'} for {get or '-'} ({lg.team(a.partner).label})",
                    idea.why,
                )  # fmt: skip
            )
    order = {TIER_SWAP: 0, TIER_LOOK: 1, TIER_MOVE: 2}
    return sorted(out, key=lambda i: (order[i.tier], -(0.0 if math.isnan(i.size) else i.size)))


ACROSS_HEADER = ["#", "League", "Type", "Do this", "Size", "Detail"]


def rows(runs: list) -> list[list]:
    """The block's rows after its title: header, then one row per item in priority order."""
    out: list[list] = [ACROSS_HEADER]
    listed = items(runs)
    for i, it in enumerate(listed, 1):
        size = "" if math.isnan(it.size) else f"{it.size:+.2f}"
        what = f"{it.cue} {it.text}" if it.cue else it.text
        out.append([i, it.league, it.kind, what, size, it.detail])
    if not listed:
        out.append(["", "", "", "Nothing to do this week.", "", ""])
    return out
