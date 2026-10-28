"""The trade analyzer: what a trade does to both teams, by our model and by the market's.

`dfs season trade "Give A, B + $10 FAAB" "Get C" --league Work`. FAAB can ride on either side. The partner is
whoever rosters the players Sam gets (or `--partner`).

**Rosters stay legal.** A team that ends up over its roster size drops its lowest-valued players (never one on
IR); one that ends up short adds the best free agents, so a 2-for-1 is priced with the roster spot it frees.

**Our model** (`objective`): each side's change in team value. Redraft: rest-of-season lineup points with the
playoff weeks weighted by the team's own odds of making the playoffs, shown apart, plus the change in those
odds. Chopped: the change in survival value, expected weeks survived and this week's survival chance.

**The market** (`consensus`): each side's consensus value in and out; FAAB at the league's points per dollar
(`faab`). Fair to the partner when what they get is at least `1 - max_consensus_loss` of what they give.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

import pandas as pd

from dfs.player_join import normalize_name
from dfs.season_long import consensus as cs
from dfs.season_long import faab as faab_mod
from dfs.season_long import sleeper_league as sl
from dfs.season_long.league_view import LeagueView
from dfs.season_long.objective import Evaluator, Outcome

DEFAULT_MAX_CONSENSUS_LOSS = 0.05
# A player is "buy low" / "sell high" when our value and the consensus value differ by this share and points.
DISAGREE_SHARE = 0.2
DISAGREE_MIN_PTS = 10.0
_FAAB = re.compile(r"\d+")


class TradeError(Exception):
    pass


@dataclass
class Side:
    players: list[str]
    faab: int = 0


@dataclass
class Market:
    """Consensus values for one league (computed once)."""

    consensus: pd.Series  # per player_id, points over waiver
    ours: pd.Series  # per player_id, our ros points over waiver
    points_per_dollar: float
    faab_source: str


def build_market(view: LeagueView, ev: Evaluator, ecr: pd.DataFrame | None = None) -> Market:
    s = view.ros.summary
    rostered = sum(len(t.players) for t in view.league.alive_teams())
    if ecr is None:
        ecr = cs.fetch_ecr(view.league.scoring)
    ranks = cs.match_to_sleeper(ecr, view.table)
    lc = next((lg for lg in view.cfg.leagues if lg.name == view.league.name), None)
    fv = faab_mod.faab_value(ev, lc.faab_point_value if lc else None)
    return Market(
        consensus=cs.consensus_values(s["ros_pts"], ranks, rostered),
        ours=cs.our_over_waiver(s["ros_pts"], rostered),
        points_per_dollar=fv.points_per_dollar,
        faab_source=fv.source,
    )


# -- parsing -----------------------------------------------------------------------------------------------


def _match(name: str, candidates: list[str], view: LeagueView) -> list[str]:
    q = normalize_name(name)
    names = {pid: normalize_name(view.name(pid)) for pid in candidates}
    exact = [p for p, n in names.items() if n == q]
    if exact:
        return exact
    return [p for p, n in names.items() if q and (q in n or n.split()[-1:] == q.split()[-1:] and len(q) > 2)]


def parse_side(text: str, pool: list[str], view: LeagueView, label: str) -> Side:
    side = Side([])
    for raw in re.split(r"[,+]| and ", text or ""):
        item = raw.strip()
        if not item:
            continue
        if "$" in item or "faab" in item.lower():
            m = _FAAB.search(item)
            if not m:
                raise TradeError(f"{label}: {item!r} is not a FAAB amount (write it as $15 or 15 FAAB).")
            side.faab += int(m.group(0))
            continue
        hits = _match(item, pool, view)
        if len(hits) != 1:
            who = ", ".join(view.name(h) for h in hits[:5]) or "nobody"
            raise TradeError(f"{label}: {item!r} matches {who}; type more of the name.")
        side.players.append(hits[0])
    return side


# -- legality ----------------------------------------------------------------------------------------------


def roster_limit(league: sl.League) -> int:
    return len(league.slots) + league.bench_slots + league.reserve_slots


def free_agents(view: LeagueView) -> list[str]:
    taken = {p for t in view.league.teams for p in t.players}
    s = view.ros.summary
    fa = s[~s.index.isin(taken) & (s["team"] != "")]
    return list(fa.sort_values("ros_pts", ascending=False).index)


FA_CANDIDATES = 15
# ...plus this many of the best at every position: a kicker or a defense never makes the overall top 15, and
# an empty K or DEF slot is the biggest free gain there is.
FA_PER_POSITION = 3


def fa_candidates(view: LeagueView, fa: list[str], roster: list[str]) -> list[str]:
    """The free agents worth trying in a roster spot: the best `FA_CANDIDATES` and each position's top few."""
    pool = [p for p in fa if p not in roster]
    pos = view.ros.summary["position"]
    out = list(pool[:FA_CANDIDATES])
    for position in pos.reindex(pool).dropna().unique():
        out += [p for p in pool if pos.get(p) == position][:FA_PER_POSITION]
    return list(dict.fromkeys(out))


def _lineup_sum(ev: Evaluator, roster: list[str]) -> float:
    return sum(ev.lineup_total(roster, w) for w in ev.weeks)


def settle(
    roster: list[str], size_before: int, view: LeagueView, fa: list[str], ev: Evaluator | None = None
) -> tuple[list[str], list[str], list[str]]:
    """Legal roster after a trade: (roster, dropped, added).

    With `ev`, the drop is the player whose loss costs the remaining lineups least and the add the free agent
    (of the best `FA_CANDIDATES`) who adds most. Without it (the finder's quick screen), the lowest
    rest-of-season points, never the last player at a position that has a slot of its own."""
    # A roster already over Sleeper's size (a player on a reserve or taxi spot) keeps its size.
    limit = max(roster_limit(view.league), size_before)
    s = view.ros.summary
    dedicated = {slot: view.league.slots.count(slot) for slot in set(view.league.slots)}
    roster, dropped, added = list(roster), [], []
    while len(roster) > limit:
        droppable = [p for p in roster if s["injury_status"].get(p, "") not in {"IR", "PUP", "NFI"}] or roster
        if ev is not None:
            full = _lineup_sum(ev, roster)
            worst = min(droppable, key=lambda p: full - _lineup_sum(ev, [q for q in roster if q != p]))
        else:
            count = pd.Series([s["position"].get(p, "") for p in roster]).value_counts()
            spare = [
                p
                for p in droppable
                if count.get(s["position"].get(p, ""), 0) > dedicated.get(s["position"].get(p, ""), 0)
            ]
            worst = min(spare or droppable, key=lambda p: float(s["ros_pts"].get(p, 0.0)))
        roster.remove(worst)
        dropped.append(worst)
    pool = [p for p in fa if p not in roster]
    while len(roster) < min(size_before, limit) and pool:
        if ev is not None:
            base = _lineup_sum(ev, roster)
            pick = max(fa_candidates(view, pool, roster), key=lambda p: _lineup_sum(ev, [*roster, p]) - base)
        else:
            pick = pool[0]
        pool.remove(pick)
        roster.append(pick)
        added.append(pick)
    return roster, dropped, added


MAX_FREE_MOVES = 3
# A free swap must add at least this many rest-of-season lineup points to count (not a coin-flip backup).
MIN_FREE_GAIN = 3.0


def best_swap(
    view: LeagueView, ev: Evaluator, roster: list[str], fa: list[str]
) -> tuple[list[str] | None, str, str]:
    """A roster's best single free-agent swap (add one, drop the one it misses least), if it gains lineup
    points: (roster after, added, dropped), else (None, "", "")."""
    base = _lineup_sum(ev, roster)
    best: tuple[list[str] | None, str, str] = (None, "", "")
    best_gain = MIN_FREE_GAIN
    for pid in fa_candidates(view, fa, roster):
        after, dropped, _ = settle([*roster, pid], len(roster), view, [], ev)
        gain = _lineup_sum(ev, after) - base
        if gain > best_gain and dropped and dropped[0] != pid:
            best, best_gain = (after, pid, dropped[0]), gain
    return best


def best_moves(
    view: LeagueView, ev: Evaluator, roster: list[str], fa: list[str]
) -> tuple[list[str] | None, list[str], list[str]]:
    """Up to `MAX_FREE_MOVES` swaps, each the best left, while one still gains: (roster, adds, drops), roster
    None when no swap helps."""
    current, adds, drops = list(roster), [], []
    pool = list(fa)
    for _ in range(MAX_FREE_MOVES):
        after, added, dropped = best_swap(view, ev, current, pool)
        if after is None:
            break
        current, pool = after, [p for p in pool if p != added]
        adds.append(added)
        drops.append(dropped)
    return (current if adds else None), adds, drops


def free_move(
    view: LeagueView, ev: Evaluator, rid: int, fa: list[str]
) -> tuple[list[str] | None, list[str], list[str]]:
    """A team's best free moves today (`best_moves` on its roster), cached on the evaluator. Sam moves first:
    another team's free moves never take a free agent his would."""
    cache = ev.__dict__.setdefault("_free_moves", {})
    if rid not in cache:
        me = view.league.my_roster_id
        taken = set() if rid == me else set(free_move(view, ev, me, fa)[1])
        cache[rid] = best_moves(view, ev, ev.rosters[rid], [p for p in fa if p not in taken])
    return cache[rid]


# -- the analysis ------------------------------------------------------------------------------------------


@dataclass
class TeamResult:
    roster_id: int
    before: Outcome
    after: Outcome
    dropped: list[str]
    added: list[str]
    weekly_gain: dict[int, float]
    consensus_in: float
    consensus_out: float

    @property
    def delta(self) -> float:
        return self.after.value - self.before.value


@dataclass
class Analysis:
    league: str
    chopped: bool
    give: Side
    get: Side
    partner: int
    me: TeamResult
    them: TeamResult
    fair_ratio: float  # partner's consensus in / out
    labels: list[str] = field(default_factory=list)
    verdict: str = ""


def _weekly_gain(ev: Evaluator, before: list[str], after: list[str]) -> dict[int, float]:
    return {w: round(ev.lineup_total(after, w) - ev.lineup_total(before, w), 2) for w in ev.weeks}


def disagreements(view: LeagueView, market: Market, give: list[str], get: list[str]) -> list[str]:
    out = []
    for pid in get:
        o, c = float(market.ours.get(pid, 0.0)), float(market.consensus.get(pid, 0.0))
        if o - c >= DISAGREE_MIN_PTS and o >= c * (1 + DISAGREE_SHARE):
            out.append(f"buy-low {view.name(pid)}")
    for pid in give:
        o, c = float(market.ours.get(pid, 0.0)), float(market.consensus.get(pid, 0.0))
        if c - o >= DISAGREE_MIN_PTS and c >= o * (1 + DISAGREE_SHARE):
            out.append(f"sell-high {view.name(pid)}")
    return out


def evaluate_trade(
    view: LeagueView,
    ev: Evaluator,
    market: Market,
    partner: int,
    give: Side,
    get: Side,
    fa: list[str] | None = None,
    max_consensus_loss: float = DEFAULT_MAX_CONSENSUS_LOSS,
) -> Analysis:
    lg = view.league
    me = lg.my_roster_id
    mine, theirs = ev.rosters[me], ev.rosters[partner]
    if give.faab > ev.faab[me]:
        raise TradeError(f"You have ${ev.faab[me]} FAAB, not ${give.faab}.")
    if get.faab > ev.faab[partner]:
        raise TradeError(f"{lg.team(partner).label} has ${ev.faab[partner]} FAAB, not ${get.faab}.")
    fa = free_agents(view) if fa is None else fa
    new_me = [p for p in mine if p not in give.players] + get.players
    new_them = [p for p in theirs if p not in get.players] + give.players
    new_me, me_drop, me_add = settle(new_me, len(mine), view, fa, ev)
    new_them, th_drop, th_add = settle(new_them, len(theirs), view, [p for p in fa if p not in me_add], ev)
    faab_after = {me: ev.faab[me] - give.faab + get.faab, partner: ev.faab[partner] + give.faab - get.faab}
    # Whatever a team can do for free on waivers it can do with or without the trade, so both sides are
    # compared after their best free move (`best_swap`), before and after, whichever is worth more: the trade
    # is credited only with what the waiver wire cannot do.
    base = ev.base()
    alt = {rid: free_move(view, ev, rid, fa)[0] for rid in (me, partner)}
    alt = {rid: r for rid, r in alt.items() if r is not None}
    if alt:
        alt_out = ev.evaluate(rosters=alt)
        base = {
            rid: max(o, alt_out[rid], key=lambda x: x.value) if rid in alt else o for rid, o in base.items()
        }
    after = ev.evaluate(rosters={me: new_me, partner: new_them}, faab=faab_after)
    mv_me = best_moves(view, ev, new_me, fa)
    mv_th = best_moves(view, ev, new_them, [p for p in fa if p not in mv_me[1]])
    if mv_me[0] is not None or mv_th[0] is not None:
        swapped = ev.evaluate(
            rosters={me: mv_me[0] or new_me, partner: mv_th[0] or new_them}, faab=faab_after
        )
        for rid, mv in ((me, mv_me), (partner, mv_th)):
            if mv[0] is not None and swapped[rid].value > after[rid].value:
                after[rid] = swapped[rid]
    ppd = market.points_per_dollar

    def cons(pids: list[str]) -> float:
        return float(sum(market.consensus.get(p, 0.0) for p in pids))

    them_in = cons(give.players) + give.faab * ppd
    them_out = cons(get.players) + get.faab * ppd
    ratio = them_in / them_out if them_out > 0 else math.inf
    res = Analysis(
        league=lg.name,
        chopped=lg.chopped,
        give=give,
        get=get,
        partner=partner,
        me=TeamResult(
            me, base[me], after[me], me_drop, me_add, _weekly_gain(ev, mine, new_me), them_out, them_in
        ),
        them=TeamResult(
            partner,
            base[partner],
            after[partner],
            th_drop,
            th_add,
            _weekly_gain(ev, theirs, new_them),
            them_in,
            them_out,
        ),
        fair_ratio=ratio,
        labels=disagreements(view, market, give.players, get.players),
    )
    res.verdict = verdict(res, max_consensus_loss)
    return res


def _fmt_delta(a: Analysis, t: TeamResult) -> str:
    if a.chopped:
        return f"{t.after.expected_weeks - t.before.expected_weeks:+.2f} expected weeks survived"
    return f"{t.delta:+.1f} team value"


def verdict(a: Analysis, max_consensus_loss: float) -> str:
    fair = a.fair_ratio >= 1 - max_consensus_loss
    mine, theirs = a.me.delta > 0, a.them.delta > 0
    pct = f"{(a.fair_ratio - 1) * 100:+.0f}%" if math.isfinite(a.fair_ratio) else "all gain"
    if not mine:
        return f"Decline: it costs you ({_fmt_delta(a, a.me)})."
    if theirs and fair:
        return (
            f"Propose it: you gain ({_fmt_delta(a, a.me)}), they gain too, and the market "
            f"calls it fair for them ({pct})."
        )
    if fair:
        return (
            f"Worth offering: you gain ({_fmt_delta(a, a.me)}); by our model they lose, but by "
            "the market they "
            f"come out {pct}, so they may well take it."
        )
    return (
        f"Good for you ({_fmt_delta(a, a.me)}) but unlikely to be accepted: by the market they "
        f"come out {pct}."
    )


def analyze(
    view: LeagueView,
    ev: Evaluator,
    market: Market,
    give_text: str,
    get_text: str,
    partner_text: str | None = None,
) -> Analysis:
    lg = view.league
    me = lg.my_roster_id
    give = parse_side(give_text, ev.rosters[me], view, "Give")
    others = [p for rid, r in ev.rosters.items() if rid != me for p in r]
    if partner_text:
        partner = lg.find_team(partner_text).roster_id
        pool = ev.rosters[partner]
    else:
        pool = others
    get = parse_side(get_text, pool, view, "Get")
    if not partner_text:
        owners = {rid for rid, r in ev.rosters.items() if rid != me and set(get.players) & set(r)}
        if len(owners) != 1:
            raise TradeError("Name the partner with --partner: the players you get are not all on one team.")
        partner = owners.pop()
    if not set(get.players) <= set(ev.rosters[partner]):
        raise TradeError("Every player you get must be on the partner's roster.")
    return evaluate_trade(view, ev, market, partner, give, get)


def describe(view: LeagueView, a: Analysis, ev: Evaluator | None = None) -> list[str]:
    lg = view.league
    partner = lg.team(a.partner).label

    def side(s: Side) -> str:
        parts = [view.name(p) for p in s.players] + ([f"${s.faab} FAAB"] if s.faab else [])
        return ", ".join(parts) or "nothing"

    lines = [f"{lg.name}: you give {side(a.give)}; you get {side(a.get)} (with {partner})."]
    cache = getattr(ev, "_free_moves", {}) if ev is not None else {}
    for rid, who in ((lg.my_roster_id, "you"), (a.partner, partner)):
        mv = cache.get(rid)
        if mv and mv[0] is not None:
            moves = ", ".join(
                f"add {view.name(x)} for {view.name(d)}" for x, d in zip(mv[1], mv[2], strict=True)
            )
            lines.append(
                f"  (free first, {who}: {moves}; both sides are measured after their best free moves)"
            )
    for who, t in (("You", a.me), (partner, a.them)):
        if a.chopped:
            lines.append(
                f"  {who}: expected weeks survived {t.before.expected_weeks:.2f} -> "
                f"{t.after.expected_weeks:.2f}; "
                f"survive this week {t.before.p_survive_now:.0%} -> {t.after.p_survive_now:.0%}; "
                f"last man standing {t.before.p_last_standing:.1%} -> {t.after.p_last_standing:.1%}; "
                f"survival value {t.delta:+.3f}"
            )
        else:
            reg_w = len([w for w in t.weekly_gain if w not in lg.playoff_weeks]) or 1
            reg_gain = sum(g for w, g in t.weekly_gain.items() if w not in lg.playoff_weeks)
            po_gain = sum(g for w, g in t.weekly_gain.items() if w in lg.playoff_weeks)
            lines.append(
                f"  {who}: team value {t.delta:+.1f}; lineup {reg_gain / reg_w:+.1f} pts/week "
                "rest of regular season, "
                f"{po_gain:+.1f} over the playoff weeks; P(playoffs) {t.before.p_playoffs:.0%} -> "
                f"{t.after.p_playoffs:.0%}"
            )
        moves = [f"drops {view.name(p)}" for p in t.dropped] + [
            f"adds {view.name(p)} (free agent)" for p in t.added
        ]
        if moves:
            lines.append(f"    ({'; '.join(moves)})")
    ratio = f"{a.fair_ratio:.0%}" if math.isfinite(a.fair_ratio) else "n/a"
    lines.append(
        f"  Market (FantasyPros rest-of-season consensus): {partner} gets {a.them.consensus_in:.0f}, gives "
        f"{a.them.consensus_out:.0f} ({ratio})."
    )
    if a.labels:
        lines.append(f"  {'; '.join(a.labels)}")
    lines.append(f"  Verdict: {a.verdict}")
    return lines
