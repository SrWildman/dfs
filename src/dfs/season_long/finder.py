"""The trade finder: realistic trades that make Sam's team better.

For every other team, every 1-for-1, 2-for-1 (either way) and 2-for-2 among each roster's top `POOL_SIZE`
players (by our rest-of-season value). A trade is kept when, by our model, Sam gains AND the partner gains
(its needs are met), AND the market calls it fair or better for the partner (`trade.evaluate_trade`).

Two passes, because the full model simulates the season: a quick screen on lineup points alone (each remaining
week's best lineup, before and after, both teams; playoff weeks at `playoff_weight` times the team's current
playoff odds, a chopped league's weeks at `survival_decay ** i`) and the market check, then the full analysis
of each partner's best `FINALISTS_PER_PARTNER`. When the market has the partner short, the smallest FAAB
sweetener that makes it fair is added from Sam's side (never more than he has, and only if he still gains);
when it has the partner well ahead, the FAAB Sam could ask for comes with the idea.

Targeting (`Filters`): `get` / `give` a named player or a position; `keep` untouchables; `partner`; `shapes`;
`max_consensus_loss`.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from itertools import combinations

from dfs.player_join import normalize_name
from dfs.season_long import ros
from dfs.season_long import team_value as tvm
from dfs.season_long import trade as tr
from dfs.season_long.league_view import LeagueView
from dfs.season_long.objective import Evaluator

POOL_SIZE = 7
FINALISTS_PER_PARTNER = 10
SHAPES = ("1-1", "2-1", "1-2", "2-2")
# A partner the screen has losing more than this many weighted lineup points is not worth a full look.
SCREEN_PARTNER_SLACK = 5.0
# Ask for FAAB when the market has the partner this far ahead.
ASK_MARGIN = 0.15
# Beyond this (the partner's consensus in over out), an idea is "lopsided": too good for them to look like
# a real offer, so it is hidden unless asked for, and always carries the FAAB to ask back (2026-10-28).
LOPSIDED_MARKET = 1.25


@dataclass
class Filters:
    get: list[str] = field(default_factory=list)
    give: list[str] = field(default_factory=list)
    keep: list[str] = field(default_factory=list)
    partner: str | None = None
    shapes: tuple[str, ...] = SHAPES
    max_consensus_loss: float = tr.DEFAULT_MAX_CONSENSUS_LOSS
    show_lopsided: bool = False
    # Names to move either way, sorted per league (`players_in`): one Sam owns is a sell, one on another
    # roster a buy; a trade that moves at least one of them counts. A name not in a league is skipped there.
    players: list[str] = field(default_factory=list)
    # The targeted search (`dfs season sync` / `trades` when season.toml has a buy list): a trade must land a
    # name from this list (one on another roster in this league) or a player at Sam's weakest position here.
    targets: list[str] | None = None


@dataclass
class Idea:
    analysis: tr.Analysis
    shape: str
    why: str
    ask_faab: int = 0
    lopsided: bool = False


def parse_shapes(text: str | None) -> tuple[str, ...]:
    if not text:
        return SHAPES
    out = []
    for part in text.split(","):
        s = part.strip()
        if s not in SHAPES:
            raise tr.TradeError(f"Shape {s!r}: use {', '.join(SHAPES)} (you give - you get).")
        out.append(s)
    return tuple(out)


def _match_exact(term: str, pool: list[str], view: LeagueView) -> list[str]:
    """A list name (buy list, `--players`) matches its own player only: the full name, never a last name
    alone (Sam's "Jonah Coleman" matched another team's Keon Coleman in a league where Jonah is his)."""
    q = normalize_name(term)
    return [p for p in pool if normalize_name(view.name(p)) == q]


# Kickers and defenses are streamed from waivers, not traded for: the targeted "weakest spot" skips them.
STREAMED_SLOTS = {"K", "DEF"}


def players_in(
    terms: list[str], mine: list[str], others: list[str], view: LeagueView
) -> tuple[set[str], set[str]]:
    """(sells, buys): each name matched once in this league, to Sam's roster or another; others skipped."""
    sells, buys = set(), set()
    for term in terms:
        hits = _match_exact(term, [*mine, *others], view)
        if len(hits) == 1:
            (sells if hits[0] in mine else buys).add(hits[0])
    return sells, buys


def _resolve(
    terms: list[str], pool: list[str], view: LeagueView, missing: list[str] | None = None
) -> tuple[set[str], set[str]]:
    """Names -> player ids in `pool`; position codes stay positions. With `missing`, a name matching nobody in
    the pool is noted there and skipped (a list run over every league: not yours, or not theirs, here)."""
    players, positions = set(), set()
    for term in terms:
        t = term.strip()
        if not t:
            continue
        if t.upper() in ros.POSITIONS or t.upper() == "DST":
            positions.add("DEF" if t.upper() == "DST" else t.upper())
            continue
        # A list run over every league (`missing`) matches full names only, like the buy list.
        hits = _match_exact(t, pool, view) if missing is not None else tr._match(t, pool, view)
        if not hits and missing is not None:
            missing.append(t)
            continue
        if len(hits) != 1:
            who = ", ".join(view.name(h) for h in hits[:5]) or "nobody"
            raise tr.TradeError(f"{t!r} matches {who}; type more of the name.")
        players.add(hits[0])
    return players, positions


class _Screen:
    """Weighted lineup points before and after (the full analysis adds each side's free waiver move)."""

    def __init__(self, view: LeagueView, ev: Evaluator, fa: list[str]):
        self.view, self.ev, self.fa = view, ev, fa
        self._baseline: dict[int, float] = {}
        lg = view.league
        base = ev.base()
        if lg.chopped:
            decay = view.cfg.survival_decay
            self.weight = {rid: {w: decay**i for i, w in enumerate(ev.weeks)} for rid in ev.ids}
        else:
            self.weight = {
                rid: {
                    w: (view.cfg.playoff_weight * base[rid].p_playoffs if w in lg.playoff_weeks else 1.0)
                    for w in ev.weeks
                }
                for rid in ev.ids
            }

    def total(self, rid: int, roster: list[str]) -> float:
        ev, wt = self.ev, self.weight[rid]
        return sum(wt[w] * ev.lineup_total(roster, w) for w in ev.weeks)

    def gain(self, rid: int, new: list[str]) -> float:
        if rid not in self._baseline:
            self._baseline[rid] = self.total(rid, self.ev.rosters[rid])
        return self.total(rid, new) - self._baseline[rid]


def _slot_line(view: LeagueView, ev: Evaluator, old: list[str], new: list[str]) -> str:
    slots = view.league.slots
    pts = view.ros.weekly.set_index(["player_id", "week"])["pts"]

    def per_slot(roster: list[str]) -> dict[str, float]:
        totals = dict.fromkeys(slots, 0.0)
        lineups = ev.lineups(roster)
        for wl in lineups:
            for j, pid in wl.starters.items():
                totals[slots[j]] += float(pts.get((pid, wl.week), 0.0))
        return {s: v / max(len(lineups), 1) for s, v in totals.items()}

    a, b = per_slot(old), per_slot(new)
    best = max(b, key=lambda s: b[s] - a[s])
    return f"{best} {b[best] - a[best]:+.1f}/wk"


def _why(view: LeagueView, ev: Evaluator, a: tr.Analysis, ask: int, lopsided: bool = False) -> str:
    me, partner = view.league.my_roster_id, a.partner
    mine_old, theirs_old = ev.rosters[me], ev.rosters[partner]
    mine_new = [p for p in mine_old if p not in a.give.players] + a.get.players
    theirs_new = [p for p in theirs_old if p not in a.get.players] + a.give.players
    parts = [f"you: {_slot_line(view, ev, mine_old, mine_new)}"]
    if a.chopped:
        parts.append(
            f"survive wk {ev.weeks[0]} {a.me.before.p_survive_now:.0%}->{a.me.after.p_survive_now:.0%}"
        )
    elif math.isfinite(a.me.after.p_playoffs):
        parts.append(f"playoffs {a.me.before.p_playoffs:.0%}->{a.me.after.p_playoffs:.0%}")
    parts.append(f"them: {_slot_line(view, ev, theirs_old, theirs_new)}")
    if a.chopped and a.them.before.p_survive_now < 0.85:
        parts.append(f"they are near the cut ({1 - a.them.before.p_survive_now:.0%} chop risk) and gain now")
    parts.append(f"market {a.fair_ratio:.0%} for them" if math.isfinite(a.fair_ratio) else "market: all gain")
    parts += a.labels
    if ask:
        parts.append(f"could ask ${ask} FAAB back")
    elif lopsided:
        parts.append(f"lopsided, but they have ${ev.faab[partner]} FAAB to give back")
    return "; ".join(parts)


def find_trades(
    view: LeagueView,
    ev: Evaluator,
    market: tr.Market,
    filters: Filters,
    top: int = 20,
    notes: list[str] | None = None,
) -> list[Idea]:
    lg = view.league
    me = lg.my_roster_id
    s = view.ros.summary
    mine = ev.rosters[me]
    others = [p for rid, r in ev.rosters.items() if rid != me for p in r]
    keep, _ = _resolve(filters.keep, mine, view)
    not_mine: list[str] = []
    not_theirs: list[str] = []
    give_p, give_pos = _resolve(filters.give, mine, view, not_mine)
    get_p, get_pos = _resolve(filters.get, others, view, not_theirs)
    if notes is not None:
        if not_mine:
            notes.append(f"not on your roster here, skipped: {', '.join(not_mine)}")
        if not_theirs:
            notes.append(f"not on another roster here, skipped: {', '.join(not_theirs)}")
    # Every name asked for is missing here (and no position was): nothing to search, not everything.
    if (filters.give and not give_p and not give_pos) or (filters.get and not get_p and not get_pos):
        return []
    sells, buys = players_in(filters.players, mine, others, view)
    if filters.players and notes is not None:
        sold = ", ".join(view.name(p) for p in sorted(sells)) or "none"
        bought = ", ".join(view.name(p) for p in sorted(buys)) or "none"
        notes.append(f"from your list: selling {sold}; buying {bought}")
    if filters.players and not (sells or buys):
        return []
    target_ids: set[str] = set()
    target_pos: set[str] = set()
    if filters.targets is not None:
        target_ids = {
            p for t in filters.targets if len(hits := _match_exact(t, others, view)) == 1 for p in hits
        }
        need = next(
            ((slot, d) for slot, d, _ in view.needs.get(me, []) if d < 0 and slot not in STREAMED_SLOTS), None
        )
        if need is not None:
            target_pos = set(tvm.slot_eligible(need[0]))
        if notes is not None:
            names = ", ".join(view.name(p) for p in sorted(target_ids)) or "none here"
            weak = f"{need[0]} ({need[1]:+.1f}/wk)" if need else "none"
            notes.append(f"targets: your buy list ({names}) and your weakest spot, {weak}")
        if not target_ids and not target_pos:
            return []
    partners = [rid for rid in ev.ids if rid != me]
    if filters.partner:
        partners = [lg.find_team(filters.partner).roster_id]
    fa = tr.free_agents(view)
    screen = _Screen(view, ev, fa)
    pos = s["position"]

    def top_players(roster: list[str], exclude: set[str], must: set[str]) -> list[str]:
        ranked = [
            p
            for p in s["ros_pts"].reindex(roster).dropna().sort_values(ascending=False).index
            if p not in exclude
        ]
        return list(dict.fromkeys([*[p for p in must if p in roster], *ranked[:POOL_SIZE]]))

    my_pool = top_players(mine, keep, give_p | (sells - keep))
    ideas: list[Idea] = []
    for rid in partners:
        theirs = ev.rosters[rid]
        at_need = [p for p in s["ros_pts"].reindex(theirs).dropna().sort_values(ascending=False).index
                   if pos.get(p) in target_pos][:3]  # fmt: skip
        their_pool = top_players(theirs, set(), get_p | buys | target_ids | set(at_need))
        candidates = []
        for shape in filters.shapes:
            n_give, n_get = int(shape[0]), int(shape[2])
            for give in combinations(my_pool, n_give):
                if give_p and not (give_p & set(give)):
                    continue
                if give_pos and not any(pos.get(p) in give_pos for p in give):
                    continue
                for get in combinations(their_pool, n_get):
                    if get_p and not (get_p & set(get)):
                        continue
                    if get_pos and not any(pos.get(p) in get_pos for p in get):
                        continue
                    if filters.players and not ((sells & set(give)) or (buys & set(get))):
                        continue
                    if filters.targets is not None and not (
                        (target_ids & set(get)) or any(pos.get(p) in target_pos for p in get)
                    ):
                        continue
                    cons_in = sum(market.consensus.get(p, 0.0) for p in give)
                    cons_out = sum(market.consensus.get(p, 0.0) for p in get)
                    short = (1 - filters.max_consensus_loss) * cons_out - cons_in
                    sweet = 0
                    if short > 0:
                        if market.points_per_dollar <= 0:
                            continue
                        sweet = math.ceil(short / market.points_per_dollar)
                        if sweet > ev.faab[me]:
                            continue
                    if not filters.show_lopsided and cons_out > 0:
                        if (cons_in + sweet * market.points_per_dollar) / cons_out > LOPSIDED_MARKET:
                            continue
                    new_me, _, _ = tr.settle(
                        [p for p in mine if p not in give] + list(get), len(mine), view, fa
                    )
                    new_th, _, _ = tr.settle(
                        [p for p in theirs if p not in get] + list(give), len(theirs), view, fa
                    )
                    g_me = screen.gain(me, new_me) - sweet * market.points_per_dollar
                    g_th = screen.gain(rid, new_th)
                    if g_me <= 0 or g_th < -SCREEN_PARTNER_SLACK:
                        continue
                    candidates.append((g_me, shape, give, get, sweet))
        candidates.sort(key=lambda c: -c[0])
        for _, shape, give, get, sweet in candidates[:FINALISTS_PER_PARTNER]:
            a = tr.evaluate_trade(
                view,
                ev,
                market,
                rid,
                tr.Side(list(give), sweet),
                tr.Side(list(get)),
                fa,
                filters.max_consensus_loss,
            )
            fair = a.fair_ratio >= 1 - filters.max_consensus_loss
            if a.me.delta <= 0 or a.them.delta <= 0 or not fair:
                continue
            lopsided = a.fair_ratio > LOPSIDED_MARKET
            if lopsided and not filters.show_lopsided:
                continue
            ask = 0
            if market.points_per_dollar > 0 and a.fair_ratio > 1 + ASK_MARGIN:
                surplus = a.them.consensus_in - a.them.consensus_out * (1 + ASK_MARGIN / 2)
                ask = (
                    int(min(surplus / market.points_per_dollar, ev.faab[rid]))
                    if math.isfinite(surplus)
                    else 0
                )
            ideas.append(Idea(a, shape, _why(view, ev, a, ask, lopsided), ask, lopsided))
    ideas.sort(key=lambda i: -i.analysis.me.delta)
    return ideas[:top]


def idea_line(view: LeagueView, idea: Idea) -> str:
    a = idea.analysis

    def side(sd: tr.Side) -> str:
        return ", ".join([view.name(p) for p in sd.players] + ([f"${sd.faab} FAAB"] if sd.faab else []))

    gain = (
        f"{a.me.after.expected_weeks - a.me.before.expected_weeks:+.2f} wks survived"
        if a.chopped
        else f"{a.me.delta:+.1f} value"
    )
    moves = [f"you add {view.name(p)} (free agent)" for p in a.me.added] + [
        f"you drop {view.name(p)}" for p in a.me.dropped
    ]
    extra = f" ({'; '.join(moves)})" if moves else ""
    return (
        f"give {side(a.give)} | get {side(a.get)}{extra} | with "
        f"{view.league.team(a.partner).label} | {gain} | "
        f"{idea.why}"
    )


def normalize_terms(text: str | None) -> list[str]:
    return [t.strip() for t in (text or "").split(",") if t.strip() and normalize_name(t)]
