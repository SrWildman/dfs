"""The ways to add one free agent to Sam's roster, under `roster_rules`: the cost of the add and its value.

An add at a position the roster is full at (QB / K / DEF over the cap) is a **swap** there:
`trade.settle_full` sends the incumbent out, to an open IR slot if he is allowed to go. It is valued over
the rest of the season, as every pickup is. When every active player at that position is on bye or out the
coming week, the add is also a **stream**: valued on that week alone (`Evaluator.scoped`), with the
position player it costs allowed to go ("bye-week stream: drop him after Week N"). The caller takes the
better of the two.
"""

from __future__ import annotations

from dataclasses import dataclass

from dfs.season_long import roster_rules as rr
from dfs.season_long import trade as tr
from dfs.season_long.league_view import LeagueView
from dfs.season_long.objective import Evaluator


@dataclass
class AddOption:
    kind: str  # "add" (rest of season; a swap where the position is capped) | "stream" (the coming week only)
    roster: list[str]
    dropped: list[str]
    to_ir: list[str]
    ev: Evaluator  # the evaluator that values `roster` (a stream's is scoped to the week)
    stream_week: int | None = None


def stream_applies(view: LeagueView, ev: Evaluator, mine: list[str], pid: str) -> bool:
    """A QB / K / DEF add that would be a swap, while every active player there is out the coming week."""
    pos = rr.position(view, pid)
    if pos not in rr.CAPPED or not ev.weeks:
        return False
    res = rr.reserve_of(view, mine)
    here = [p for p in mine if p not in res and rr.position(view, p) == pos]
    if len(here) < rr.position_caps(view)[pos]:
        return False
    week_pts = ev._pts.get(ev.weeks[0], {})
    return all(rr.unavailable_this_week(view, week_pts, p) for p in here)


def add_options(
    view: LeagueView, ev: Evaluator, mine: list[str], pid: str, *, force: bool = False
) -> list[AddOption]:
    """Every legal way to add `pid`: the ordinary add (with its drop, or the incumbent it swaps out, or the IR
    move), and the bye-week stream where one applies. Empty when the add only makes the roster worse (the
    free agent is the one the rules would cut), unless `force`: then the add is kept and the cheapest other
    player goes, so a named free agent is always reported, with the value he really has."""
    out: list[AddOption] = []
    keep = frozenset({pid}) if force else frozenset()
    st = tr.settle_full([*mine, pid], len(mine), view, [], ev, before=mine, keep=keep)
    if pid in st.roster:
        out.append(AddOption("add", st.roster, st.dropped, st.to_ir, ev))
    if stream_applies(view, ev, mine, pid):
        week = ev.weeks[0]
        scoped = ev.scoped(pid, week)
        st = tr.settle_full(
            [*mine, pid],
            len(mine),
            view,
            [],
            scoped,
            before=mine,
            exempt=frozenset({rr.position(view, pid)}),
            keep=keep,
        )
        if pid in st.roster:
            out.append(AddOption("stream", st.roster, st.dropped, st.to_ir, scoped, week))
    return out


def screen_gain(option: AddOption, mine: list[str], weights: dict[int, float]) -> float:
    """Weighted lineup points the option adds over `mine` (the quick screen the ranking uses)."""
    ev = option.ev
    return sum(weights[w] * (ev.lineup_total(option.roster, w) - ev.lineup_total(mine, w)) for w in ev.weeks)


def weights(view: LeagueView, ev: Evaluator) -> dict[int, float]:
    lg = view.league
    if lg.chopped:
        return {w: view.cfg.survival_decay**i for i, w in enumerate(ev.weeks)}
    p = ev.base()[lg.my_roster_id].p_playoffs
    return {w: (view.cfg.playoff_weight * p if w in lg.playoff_weeks else 1.0) for w in ev.weeks}


def best_option(view: LeagueView, ev: Evaluator, mine: list[str], pid: str) -> AddOption | None:
    opts = add_options(view, ev, mine, pid)
    if not opts:
        return None
    w = weights(view, ev)
    return max(opts, key=lambda o: screen_gain(o, mine, w))


def move_text(view: LeagueView, dropped: list[str], to_ir: list[str], *, verb: bool = True) -> str:
    """The roster spot an add takes: "drop X", "X to IR (nobody dropped)" or "open spot". `verb=False` leaves
    "drop" off, for a column headed Drop."""
    drop = "drop " if verb else ""
    parts = [f"{drop}{view.name(p)}" for p in dropped] + [
        f"{view.name(p)} to IR (nobody dropped)" for p in to_ir
    ]
    return "; ".join(parts) or "open spot"
