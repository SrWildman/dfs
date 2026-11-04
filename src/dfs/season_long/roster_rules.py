"""Roster sense: what a manager does with a roster spot, as rules, so no evaluator can talk him out of it.

The evaluators add up rest-of-season lineup points. A second QB, K or DEF picks up every future bye week
and injury, so on points alone he "beats" a bench RB who rarely starts. That is correct arithmetic and
wrong roster management (Sam, 2026-11-04: "I would never drop a position player for a backup QB except on
a bye week. K and DST, I never need more than 1."). These rules sit under every place an add or a drop is
chosen (`trade.settle`, so the waivers, the finder, the analyzer and the chopped plan; and the Sunday check):

1. **Caps.** Active K at most `[roster] max_k`, active DEF at most `max_def` (season.toml, default 1 each).
   Active QB at most the number of slots that can start a QB (1 in a 1-QB league, 2 with a SUPER_FLEX). A
   league that starts more K / DEF than the cap raises it to its slot count. Players in IR slots count
   toward no cap.
2. **An add at a capped position is a swap there.** The incumbent goes (to IR when he can), never an RB,
   WR or TE. With nobody at the position the add fills the empty slot, and the drop is the player missed
   least.
3. **Bye and injury cover counts only the coming scoring week.** A second QB / K / DEF is a *stream* when
   every active player there is on bye or OUT / IR / Doubtful that week: valued on that week alone, and
   dropping a position player for him is allowed ("bye-week stream: drop him after Week N").
4. **IR slots before any drop.** A player the league lets go to IR (`League.reserve_statuses`) on the
   active roster, with a slot open, is moved there instead of anyone being dropped. A reserve player whose
   tag has cleared is flagged: activate him.
5. **Never a drop from an IR slot, and never a drop that leaves a starting slot empty.**
6. **A same-position swap has to be worth it.** A QB / K / DEF swapped for the incumbent is suggested only
   when its rest-of-season lineup gain is at least `[roster] min_swap_gain` (default 5 points): a coin-flip
   backup is not worth the move. An empty-slot fill (nobody active there) and a bye-week stream are exempt.

This module is pure helpers over a `LeagueView`: positions, caps, who sits in reserve, who may go to IR.
"""

from __future__ import annotations

from dataclasses import dataclass

from dfs.season_long import team_value as tvm
from dfs.season_long.league_view import LeagueView

CAPPED = ("QB", "K", "DEF")
# A player with one of these tags is not playing the coming week (Sleeper's tags; the Sunday check lays
# ESPN's feed over them, not this module).
UNAVAILABLE = {"Out", "Doubtful", "IR", "PUP", "NFI", "Sus", "Suspended", "COV", "DNR"}


def position(view: LeagueView, pid: str) -> str:
    s = view.ros.summary
    return str(s["position"].get(pid, "")) if pid in s.index else ""


def status(view: LeagueView, pid: str) -> str:
    s = view.ros.summary
    return str(s["injury_status"].get(pid, "") or "") if pid in s.index else ""


def position_caps(view: LeagueView) -> dict[str, int]:
    """Most active players at QB / K / DEF: the league's own slot count, K and DEF at least season.toml's."""
    slots = view.league.slots

    def starts(pos: str) -> int:
        return sum(1 for s in slots if pos in tvm.slot_eligible(s))

    return {
        "QB": max(starts("QB"), 1),
        "K": max(view.cfg.roster.max_k, starts("K")),
        "DEF": max(view.cfg.roster.max_def, starts("DEF")),
    }


def is_same_position_swap(view: LeagueView, pid: str, dropped: list[str], to_ir: list[str]) -> bool:
    """Adding `pid` (a QB / K / DEF) sent his own position's incumbent out, dropped or to IR. False for an
    empty-slot fill (nobody left) and for any other position."""
    pos = position(view, pid)
    return pos in CAPPED and any(position(view, p) == pos for p in [*dropped, *to_ir])


def reserve_of(view: LeagueView, roster: list[str], before: list[str] | None = None) -> set[str]:
    """Who in `roster` sits in an IR slot: those Sleeper has in a reserve list (anywhere), up to the league's
    slots, the ones already in reserve on `before` first (the rest are active)."""
    lg = view.league
    reserved = [p for p in roster if p in lg.reserve_ids]
    prior = set(before or ())
    reserved.sort(key=lambda p: p not in prior)
    return set(reserved[: lg.reserve_slots])


def active(view: LeagueView, roster: list[str], reserve: set[str] | None = None) -> list[str]:
    res = reserve_of(view, roster) if reserve is None else reserve
    return [p for p in roster if p not in res]


def counts(view: LeagueView, players: list[str]) -> dict[str, int]:
    out: dict[str, int] = {}
    for p in players:
        pos = position(view, p)
        out[pos] = out.get(pos, 0) + 1
    return out


def ir_eligible(view: LeagueView, pid: str) -> bool:
    """The league lets this player's injury tag go to an IR slot."""
    return status(view, pid) in view.league.reserve_statuses


def ir_open(view: LeagueView, reserve: set[str]) -> int:
    return max(view.league.reserve_slots - len(reserve), 0)


def unavailable_this_week(view: LeagueView, week_pts: dict[str, float], pid: str) -> bool:
    """On bye, not projected, or tagged out for the coming scoring week."""
    return week_pts.get(pid, 0.0) <= 0 or status(view, pid) in UNAVAILABLE


@dataclass
class IRLine:
    kind: str  # "move" (an eligible player to an open slot) | "activate" (a reserve player who is healthy)
    player_id: str
    text: str


def ir_lines(view: LeagueView, roster_id: int | None = None) -> list[IRLine]:
    """The IR housekeeping for one team (Sam's by default): move X to IR while a slot is open, and activate a
    reserve player whose tag has cleared. Nobody is dropped by either."""
    lg = view.league
    team = lg.team(lg.my_roster_id if roster_id is None else roster_id)
    s = view.ros.summary
    res = set(team.reserve)
    out: list[IRLine] = []
    for pid in team.reserve:
        if not ir_eligible(view, pid):
            tag = status(view, pid) or "healthy"
            out.append(
                IRLine(
                    "activate",
                    pid,
                    f"IR slot holds a healthy player ({tag}): activate {view.name(pid)}",
                )
            )
    eligible = sorted(
        (p for p in team.players if p not in res and ir_eligible(view, p)),
        key=lambda p: -float(s["ros_pts"].get(p, 0.0)) if p in s.index else 0.0,
    )
    for pid in eligible[: ir_open(view, res)]:
        out.append(
            IRLine(
                "move",
                pid,
                f"move {view.name(pid)} ({position(view, pid)}, {status(view, pid)}) to IR: "
                "frees a roster spot, nobody dropped",
            )
        )
    return out
