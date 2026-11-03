"""Replay of Sam's own late swaps (Plan C item 2): Weeks 7 and 8, at the times he ran (or would have run) it.

For each of his lineups (his own entries, from the contests' final standings: he made no swaps, so final is
what he held at lock) and each replay time, three questions:

1. **What the tool said.** The saved run where there is one (`data/cockpit/late_swap.json`: Week 8, 3:29 pm
   ET); else today's projected mode re-run (`P`: every player at full-game draws, the projected-ownership
   line), the way `late-swap` ran before live became the default.
2. **What it says now.** The live default (`PS`): the same candidates (the command's own search over his
   Player Pool, `late_swap_search.suggest`: swaps that raise the projection), scored on the real state at that
   time. Beside it, `PS*`: the best of EVERY legal one-for-one swap by the same score, projection or not
   (what the parked "search harder" item would find).
3. **What would have paid.** Hindsight, with the real final points: every legal one-for-one and two-for-two
   swap from the rosterable pool whose game had not started, and which of them would have cashed (cash
   contests: his score reaching the contest's real cash line) or reached / moved toward the top 1% (GPP).

**The real state at a time** comes from nflverse's play-by-play, whose plays carry the wall-clock time: every
started player's DraftKings points from the plays before then (`live_scores.offense_points` / `dst_points`,
the live command's own scoring; a defense's points-allowed tier only once its game is over) and each game's
share played from its clock. The first block of the report checks the final totals against DraftKings' own.

Run: `python -m dfs.late_swap_replay --pbp <play_by_play_2026.parquet> [--weeks 7,8] [--sims 10000]` from the
repo root. Offline, read-only: it reads `data/` and writes nothing but the report it prints.
"""

from __future__ import annotations

import argparse
import itertools
import json
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd

from dfs import late_swap_field as lf
from dfs import live_scores, pool_store, sim_inputs
from dfs.config import SimLinesConfig
from dfs.contests import series
from dfs.kickoff import format_et
from dfs.late_swap import lineup_slot_status
from dfs.late_swap_retro import best_swap, ordered_names
from dfs.late_swap_search import (
    LineupState,
    Swap,
    _fits,
    conflicts,
    lineup_state,
    suggest,
    swap_pool,
)
from dfs.player_join import normalize_name
from dfs.sim import contest_retro as cr

SEASON = cr.SEASON
SALARY_CAP = 50000
METRIC = "CalPts"
TOP = 3 * 4  # the command's own top * OVERSAMPLE
SAVED_RUN = Path("data/cockpit/late_swap.json")
PLAYERS = Path("data/research_cache/players.parquet")

# Week -> the replay times (UTC). Week 8's 3:29 pm ET is Sam's saved run; the rest are the plan's 1:30 and
# 3:30 pm ET (no saved run survives for Week 7: each run overwrites the last).
TIMES: dict[int, list[datetime]] = {
    7: [datetime(2026, 10, 25, 17, 30, tzinfo=UTC), datetime(2026, 10, 25, 19, 30, tzinfo=UTC)],
    8: [datetime(2026, 11, 1, 18, 30, tzinfo=UTC), datetime(2026, 11, 1, 20, 29, 9, tzinfo=UTC)],
}


# ---------------------------------------------------------------------------------------------------------
# The real state at a time, from play-by-play
# ---------------------------------------------------------------------------------------------------------


def _team(code: object) -> str:
    return lf.normalize_team(str(code))


def pbp_state(
    pbp: pd.DataFrame, at: datetime, names: dict[str, str], nicknames: dict[str, str]
) -> tuple[dict[str, float], dict[str, float]]:
    """`(points so far by normalised name, share of the game played by team)` at `at`. `names` maps a gsis id
    to a normalised name, `nicknames` a team code to its defense's normalised name."""
    # A few plays (two-point tries, mostly) carry no time: they happened when the play before them did.
    when = pd.to_datetime(pbp["time_of_day"], utc=True, errors="coerce")
    when = when.groupby(pbp["game_id"]).transform(lambda s: s.ffill().bfill())
    done = pbp[when <= pd.Timestamp(at)]
    last_play = when.groupby(pbp["game_id"]).max()
    progress: dict[str, float] = {}
    final_games: set[str] = set()
    for game_id, plays in pbp.groupby("game_id"):
        before = plays[when[plays.index] <= pd.Timestamp(at)]
        home, away = _team(plays["home_team"].iloc[0]), _team(plays["away_team"].iloc[0])
        if before.empty:
            share = 0.0
        elif last_play[game_id] <= pd.Timestamp(at):
            share = 1.0
            final_games.add(game_id)
        else:
            remaining = pd.to_numeric(before["game_seconds_remaining"], errors="coerce").dropna()
            share = float(np.clip(1.0 - (remaining.iloc[-1] if len(remaining) else 3600) / 3600, 0.0, 1.0))
        progress[home] = progress[away] = share

    off: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))

    def add(ids: pd.Series, values: pd.Series, stat: str) -> None:
        for pid, v in zip(ids, values, strict=True):
            if isinstance(pid, str) and pid and v == v and v:
                off[pid][stat] += float(v)

    num = lambda c: pd.to_numeric(done[c], errors="coerce").fillna(0.0)  # noqa: E731
    add(done["passer_player_id"], num("passing_yards"), "pass_yd")
    add(done["passer_player_id"], num("pass_touchdown"), "pass_td")
    add(done["passer_player_id"], num("interception"), "pass_int")
    add(done["rusher_player_id"], num("rushing_yards"), "rush_yd")
    add(done["rusher_player_id"], num("rush_touchdown"), "rush_td")
    add(done["receiver_player_id"], num("receiving_yards"), "rec_yd")
    add(done["receiver_player_id"], num("complete_pass"), "rec")
    add(done["receiver_player_id"], num("pass_touchdown"), "rec_td")
    add(done["fumbled_1_player_id"], num("fumble_lost"), "fum_lost")
    two = (done["two_point_conv_result"] == "success").astype(float)
    for col in ("passer_player_id", "rusher_player_id", "receiver_player_id"):
        add(done[col], two, "two_pt")
    special = done["play_type"].isin(["kickoff", "punt"])
    ret_td = (num("touchdown") > 0) & special
    add(done.loc[ret_td, "td_player_id"], pd.Series(1.0, index=done.index[ret_td]), "ret_td")

    fpts: dict[str, float] = {}
    for pid, stats in off.items():
        key = names.get(pid)
        if key:
            fpts[key] = fpts.get(key, 0.0) + live_scores.offense_points(stats)

    dst: dict[str, dict[str, float]] = defaultdict(lambda: defaultdict(float))
    for rec in done.itertuples(index=False):
        d, p = _team(rec.defteam), _team(rec.posteam)
        if rec.sack == 1:
            dst[d]["sack"] += 1
        if rec.interception == 1:
            dst[d]["int"] += 1
        if rec.fumble_lost == 1 and isinstance(rec.fumble_recovery_1_team, str):
            dst[_team(rec.fumble_recovery_1_team)]["fum_rec"] += 1
        if rec.safety == 1:
            dst[d]["safety"] += 1
        if rec.punt_blocked == 1 or rec.field_goal_result == "blocked" or rec.extra_point_result == "blocked":
            dst[d]["blocked"] += 1
        if rec.touchdown == 1 and isinstance(rec.td_team, str):
            scorer = _team(rec.td_team)
            if rec.play_type in ("kickoff", "punt") or scorer != p:
                dst[scorer]["def_td"] += 1
    for game_id, plays in pbp.groupby("game_id"):
        if game_id not in final_games:
            continue
        last = plays.iloc[-1]
        home, away = _team(last["home_team"]), _team(last["away_team"])
        dst[home]["points_allowed"] = float(last["total_away_score"])
        dst[away]["points_allowed"] = float(last["total_home_score"])
        dst[home]["_final"] = dst[away]["_final"] = 1.0
    for team, stats in dst.items():
        key = nicknames.get(team)
        if key and progress.get(team, 0.0) > 0:
            blocked = stats.pop("blocked", 0.0)
            fpts[key] = live_scores.dst_points(stats, final=bool(stats.get("_final"))) + 2.0 * blocked
    return fpts, progress


# ---------------------------------------------------------------------------------------------------------
# His lineups and contests
# ---------------------------------------------------------------------------------------------------------


@dataclass
class Entry:
    contest: cr.RealContest
    points: float  # what DK scored him


@dataclass
class MyLineup:
    names: list[str]
    goal: str  # cash when any contest it is in is cash (the money is there), else gpp
    entries: list[Entry] = field(default_factory=list)


def my_lineups(contests: list[cr.RealContest], key_to_name: dict[str, str]) -> list[MyLineup]:
    by_names: dict[tuple[str, ...], MyLineup] = {}
    for c in contests:
        mine = c.standings.lineups[c.standings.lineups["entry_name"] == series.MY_USERNAME]
        for rec in mine.itertuples(index=False):
            names = ordered_names(rec.slots)
            mapped = None if names is None else [key_to_name.get(normalize_name(x)) for x in names]
            if mapped is None or any(m is None for m in mapped):
                continue
            key = tuple(sorted(mapped))
            lu = by_names.setdefault(key, MyLineup(mapped, "cash"))
            lu.entries.append(Entry(c, float(rec.points)))
    for lu in by_names.values():
        lu.goal = "cash" if any(e.contest.fmt == "cash" for e in lu.entries) else "gpp"
    return list(by_names.values())


def finish(points: float, c: cr.RealContest) -> float:
    """His place among the contest's real final scores, as the share of the field at or above him."""
    return float(((c.points > points).sum() + 1) / len(c.points))


# ---------------------------------------------------------------------------------------------------------
# Every legal swap (hindsight)
# ---------------------------------------------------------------------------------------------------------


def all_singles(state: LineupState, pool: list) -> list[Swap]:
    current = state.current_players()
    names_in = {p.name for p in current}
    out = []
    for slot in state.open:
        others = [p for p in current if p != slot.current]
        base = sum(p.salary for p in others)
        for player in pool:
            if player.name in names_in or not _fits(slot.slot, player):
                continue
            if base + player.salary > state.cap or conflicts(player, others):
                continue
            out.append(Swap({slot.index: (slot.current, player)}, 0.0, state.cap - base - player.salary))
    return out


def all_pairs(state: LineupState, pool: list) -> list[Swap]:
    current = state.current_players()
    names_in = {p.name for p in current}
    out = []
    for sa, sb in itertools.combinations(state.open, 2):
        others = [p for p in current if p not in (sa.current, sb.current)]
        base = sum(p.salary for p in others)
        fa = [p for p in pool if p.name not in names_in and _fits(sa.slot, p) and not conflicts(p, others)]
        fb = [p for p in pool if p.name not in names_in and _fits(sb.slot, p) and not conflicts(p, others)]
        for a in fa:
            room = state.cap - base - a.salary
            for b in fb:
                if b.name == a.name or b.salary > room or conflicts(b, [*others, a]):
                    continue
                out.append(Swap({sa.index: (sa.current, a), sb.index: (sb.current, b)}, 0.0, room - b.salary))
    return out


def swapped(names: list[str], swap: Swap) -> list[str]:
    gone = {old.name for old, _ in swap.changes.values() if old is not None}
    return [n for n in names if n not in gone] + [new.name for _, new in swap.changes.values()]


def describe(swap: Swap | None) -> str:
    if swap is None:
        return "keep"
    parts = [f"{old.name if old else '(empty)'} -> {new.name}" for old, new in swap.changes.values()]
    return ", ".join(parts)


# ---------------------------------------------------------------------------------------------------------
# Money: what each choice would have paid, from the contests' real payout tables
# ---------------------------------------------------------------------------------------------------------


def final_points(names: list[str], fpts: dict[str, float]) -> float:
    """DK's total, to the hundredth (a float sum one ulp under a tied cash line would read as a miss)."""
    return round(sum(fpts.get(normalize_name(n), 0.0) for n in names), 2)


def paid(lu: MyLineup, names: list[str], fpts: dict[str, float]) -> float:
    """What `names` would have paid across every entry of `lu` (his own lineup counts its real copies minus
    him; a swapped lineup is one more copy of whatever the field held)."""
    mine = tuple(sorted(normalize_name(n) for n in names)) == tuple(
        sorted(normalize_name(n) for n in lu.names)
    )
    key = tuple(sorted(normalize_name(n) for n in names))
    pts = final_points(names, fpts)
    return sum(cr.realized_payout(e.contest, e.contest.kind, key, pts, own_entry=mine) for e in lu.entries)


def cashed(lu: MyLineup, names: list[str], fpts: dict[str, float]) -> int:
    pts = final_points(names, fpts)
    return sum(int(((e.contest.points > pts + 1e-9).sum() + 1) <= e.contest.places_paid) for e in lu.entries)


@dataclass
class Choice:
    label: str
    swap: Swap | None
    gain: float | None = None  # the mode's own change in Cash % / Top 1% (a fraction)
    note: str = ""


# ---------------------------------------------------------------------------------------------------------
# The replay
# ---------------------------------------------------------------------------------------------------------


def saved_run(week_times: list[datetime]) -> tuple[datetime, list[dict]] | None:
    if not SAVED_RUN.exists():
        return None
    data = json.loads(SAVED_RUN.read_text())
    at = datetime.fromisoformat(data["saved_at"])
    if not any(abs((at - t).total_seconds()) < 60 for t in week_times):
        return None
    return at, data["lineups"]


def match_saved(lineups: list[MyLineup], saved: list[dict]) -> dict[int, tuple[dict, list[str], list[str]]]:
    """Each of his entered lineups -> (the saved run's Lineups-tab lineup, names only on the tab, names only
    in his entry). Exact matches first, then the closest with at least 7 of 9 in common, one to one."""
    out: dict[int, tuple[dict, list[str], list[str]]] = {}
    used: set[int] = set()
    for need in (9, 7):
        for i, lu in enumerate(lineups):
            if i in out:
                continue
            best = max(
                (j for j in range(len(saved)) if j not in used),
                key=lambda j: len(set(saved[j]["names"]) & set(lu.names)),
                default=None,
            )
            if best is None or len(set(saved[best]["names"]) & set(lu.names)) < need:
                continue
            used.add(best)
            tab = set(saved[best]["names"])
            out[i] = (saved[best], sorted(tab - set(lu.names)), sorted(set(lu.names) - tab))
    return out


def saved_choice(saved_lu: dict, ls: LineupState, goal: str) -> Choice:
    best = saved_lu.get("best")
    if not best:
        return Choice("the tool then (saved run)", None, None, "nothing beats it")
    by_name = {s.current.name: s for s in ls.open if s.current is not None}
    changes = {}
    for out_name, in_name in zip(best["out"], best["into"], strict=False):
        slot = by_name.get(out_name)
        if slot is None:
            return Choice(
                "the tool then (saved run)", None, None, f"{out_name} -> {in_name}, not in this entry"
            )
        changes[slot.index] = (slot.current, SimpleNamespace(name=in_name))
    gain = best["delta_p_cash"] if goal == "cash" else best["delta_p_gpp"]
    return Choice("the tool then (saved run)", Swap(changes, 0.0, 0), gain, best.get("overlap") or "")


def pick(live: lf.LiveLines, lineup: list, swaps: list[Swap], specs: dict, goal: str, label: str) -> Choice:
    found = best_swap(live, lineup, swaps, specs, goal) if swaps else None
    if found is None:
        return Choice(label, None)
    return Choice(label, found[2], found[0])


def replay_week(week: int, pbp: pd.DataFrame, *, n_sims: int, seed: int) -> tuple[list[str], list[dict]]:
    edge = cr.pre_lock_edge(week)
    specs = sim_inputs.build_specs(edge)
    kickoffs = lf.kickoffs_from_edge(edge)
    contests = cr.load_real_contests({week})
    fpts = cr.week_points(contests, week)
    key_to_name: dict[str, str] = {}
    for name in specs:
        key_to_name.setdefault(normalize_name(name), name)
    lineups = my_lineups(contests, key_to_name)

    players = pd.read_parquet(PLAYERS)
    gsis = {
        str(g): normalize_name(str(n))
        for g, n in zip(players["gsis_id"], players["display_name"], strict=True)
    }
    nicknames = {
        _team(t): normalize_name(str(n))
        for t, n, p in zip(edge["Team"], edge["Name"], edge["Position"], strict=True)
        if p == "DST"
    }
    wk = pbp[pbp["week"] == week]

    lines = [f"## Week {week}", ""]
    end_pts, _ = pbp_state(wk, datetime(2100, 1, 1, tzinfo=UTC), gsis, nicknames)
    keys = [k for k in (normalize_name(n) for n in specs) if k in fpts and fpts[k] != 0]
    close = [k for k in keys if abs(end_pts.get(k, 0.0) - fpts[k]) <= 0.51]
    lines.append(
        f"Play-by-play check: the final totals match DraftKings' within half a point for {len(close)} of "
        f"{len(keys)} players who scored ({len(close) / max(len(keys), 1):.0%})."
    )
    lines.append("")
    lines.append(
        f"His lineups: {len(lineups)} distinct, {sum(len(lu.entries) for lu in lineups)} entries in "
        f"{len({e.contest.contest_id for lu in lineups for e in lu.entries})} contests:"
    )
    for number, lu in enumerate(lineups, start=1):
        cont = "; ".join(
            f"{e.contest.format} {e.contest.entries:,} (${e.contest.fee:g}, "
            + (
                f"cash line {e.contest.standings.profile.cash_line:.2f})"
                if e.contest.fmt == "cash"
                else f"paid {e.contest.places_paid:,}, top 1% {e.contest.standings.profile.top1:.1f})"
            )
            for e in lu.entries
        )
        won = paid(lu, lu.names, fpts)
        lines.append(
            f"- L{number} ({lu.goal}): {', '.join(lu.names)}. Finished {final_points(lu.names, fpts):.2f}; "
            f"cashed {cashed(lu, lu.names, fpts)} of {len(lu.entries)}, paid ${won:,.2f}. {cont}."
        )
    lines.append("")

    saved = saved_run(TIMES[week])
    blank = lf.slate_state(specs, {}, TIMES[week][0], {}, game_hours=None)
    draws_all = lf.simulate_remaining(blank, n_sims=n_sims, seed=seed)
    cfg_lines = SimLinesConfig()
    p_cash, p_top = lf.projected_lines(draws_all, edge, specs, cfg_lines)
    projected = lf.LiveLines(draws_all, p_cash, p_top, "projected")

    rows: list[dict] = []
    for at in TIMES[week]:
        now_pts, progress = pbp_state(wk, at, gsis, nicknames)
        state = lf.slate_state(specs, kickoffs, at, now_pts, out=lf.unavailable(edge), progress=progress)
        draws = lf.simulate_remaining(state, n_sims=n_sims, seed=seed)
        s_cash, s_top = lf.projected_lines(draws, edge, specs, cfg_lines)
        live = lf.LiveLines(draws, s_cash, s_top, "projected + live state")
        snap = pool_store.pool_at(SEASON, week, at)
        pool_names = set(snap.pool) if snap is not None else set()
        my_pool = swap_pool(edge, now=at, metric=METRIC, pool_names=pool_names, all_players=False)
        everyone = swap_pool(edge, now=at, metric=METRIC, pool_names=pool_names, all_players=True)
        # Every unstarted, playing player on the slate (item 4's question: the live pick off the whole slate).
        slate = swap_pool(edge, now=at, metric=METRIC, pool_names=set(edge["Name"]), all_players=False)
        is_saved = saved is not None and abs((saved[0] - at).total_seconds()) < 60
        matched = match_saved(lineups, saved[1]) if is_saved else {}
        lines.append(f"### {format_et(at)}" + (" (Sam's saved run)" if is_saved else ""))
        lines.append("")
        lines.append(
            f"Projected-field lines on this state: cash {float(s_cash.mean()):.1f}, "
            f"top 1% {float(s_top.mean()):.1f}. His Player Pool: {len(pool_names)} names, {len(my_pool)} not "
            f"started; whole slate not started: {len(slate)}; rosterable and not started: "
            f"{len(everyone)}."
        )
        if is_saved:
            lines.append(
                f"The saved run checked the {len(saved[1])} lineups on the Lineups tab; "
                f"{len(matched)} of his "
                f"{len(lineups)} entered lineups match one of them (exactly: "
                f"{sum(1 for m in matched.values() if not m[1])})."
            )
        lines.append("")
        for number, lu in enumerate(lineups, start=1):
            names = lu.names
            statuses = lineup_slot_status(names, edge, now=at)
            ls = lineup_state(statuses, edge, cap=SALARY_CAP, metric=METRIC)
            if any(n not in specs for n in names):
                lines.append(f"- L{number}: a name the simulator does not hold; skipped.")
                continue
            lineup = [specs[n] for n in names]
            so_far = sum(
                now_pts.get(normalize_name(n), 0.0) for n in names if kickoffs.get(n) and kickoffs[n] <= at
            )
            if not ls.open:
                lines.append(f"- **L{number}**: every slot locked ({so_far:.1f} pts so far).")
                continue
            if lu.goal == "cash":
                where = f"Cash % {live.probabilities(live.scores(lineup))[0]:.0%} on this state"
            else:
                try:
                    where = lf_position(live, lineup)
                except KeyError:
                    where = "position unknown"
            lines.append(
                f"- **L{number}** ({lu.goal}; open {', '.join(s.slot for s in ls.open)}; "
                f"{so_far:.1f} pts so far; {where})"
            )
            sug = suggest(ls, my_pool, top=TOP, time_limit=10.0)
            tool_swaps = [s for s in ([sug.refill] if sug.refill else []) + sug.pairs]
            tool_swaps += [s for group in sug.singles.values() for s in group]
            tool_swaps = [s for s in tool_swaps if s.delta > 0]
            choices: list[Choice] = []
            if is_saved:
                if number - 1 in matched:
                    saved_lu, tab_only, entry_only = matched[number - 1]
                    c = saved_choice(saved_lu, ls, lu.goal)
                    if tab_only:
                        c.note = (c.note + "; " if c.note else "") + (
                            f"the tab's lineup {saved_lu['number']} differed: {', '.join(tab_only)} "
                            "instead of "
                            f"{', '.join(entry_only)}"
                        )
                    choices.append(c)
                else:
                    choices.append(
                        Choice(
                            "the tool then (saved run)", None, None, "not on the Lineups tab: never checked"
                        )
                    )
            else:
                choices.append(
                    pick(projected, lineup, tool_swaps, specs, lu.goal, "the tool then (projections only)")
                )
            choices.append(pick(live, lineup, tool_swaps, specs, lu.goal, "the tool now (live state)"))
            wide = suggest(ls, slate, top=TOP, time_limit=10.0)
            slate_swaps = [s for s in ([wide.refill] if wide.refill else []) + wide.pairs]
            slate_swaps += [s for group in wide.singles.values() for s in group]
            choices.append(
                pick(
                    live,
                    lineup,
                    [s for s in slate_swaps if s.delta > 0],
                    specs,
                    lu.goal,
                    "the tool now, whole slate (live state)",
                )
            )
            choices.append(
                pick(
                    live,
                    lineup,
                    all_singles(ls, my_pool),
                    specs,
                    lu.goal,
                    "any 1-for-1 from his pool, live score",
                )
            )
            base_paid = paid(lu, names, fpts)
            base_cash = cashed(lu, names, fpts)
            row = {"week": week, "at": format_et(at), "lineup": number, "goal": lu.goal, "base": base_paid}
            for c in choices:
                after = swapped(names, c.swap) if c.swap is not None else names
                money = paid(lu, after, fpts) - base_paid
                what = "Cash %" if lu.goal == "cash" else "Top 1%"
                gain = f" ({what} {c.gain * 100:+.1f} pts)" if c.gain is not None else ""
                result = (
                    f"finished {final_points(after, fpts):.2f}, cashed {cashed(lu, after, fpts)} of "
                    f"{len(lu.entries)} (was {base_cash}), ${money:+,.2f}"
                    if c.swap is not None
                    else "keep"
                )
                extra = f" [{c.note}]" if c.note else ""
                lines.append(
                    f"  - {c.label}: {describe(c.swap) if c.swap else 'nothing beats it'}{gain}: "
                    f"{result}{extra}."
                )
                row[c.label.split(" (")[0]] = money
            hs = hindsight(ls, names, lu, my_pool, everyone, fpts, base_paid)
            lines += hs[0]
            row.update(hs[1])
            rows.append(row)
        lines.append("")
    return lines, rows


def lf_position(live: lf.LiveLines, lineup: list) -> str:
    from dfs.late_swap_position import lineup_position, position_text

    return position_text(lineup_position(live, lineup))


def hindsight(
    ls: LineupState,
    names: list[str],
    lu: MyLineup,
    my_pool: list,
    everyone: list,
    fpts: dict[str, float],
    base_paid: float,
) -> tuple[list[str], dict]:
    """Every legal one- and two-player swap scored with the real final points and paid from the real
    payout tables: the best, and how many would have paid more than keeping."""
    out, row = [], {}
    mine = {p.name for p in my_pool}
    for label, swaps in (("1-for-1", all_singles(ls, everyone)), ("2-for-2", all_pairs(ls, everyone))):
        if not swaps:
            out.append(f"  - hindsight {label}: no legal swap.")
            row[f"hindsight best {label}"] = 0.0
            continue
        finals = np.array([final_points(swapped(names, s), fpts) for s in swaps])
        order = np.argsort(-finals)[:40]  # the money is in the top scores; paying all of them is slow
        money = {int(i): paid(lu, swapped(names, swaps[i]), fpts) - base_paid for i in order}
        best = max(money, key=lambda i: (money[i], finals[i]))
        better = sum(1 for v in money.values() if v > 0.005)
        from_pool = sum(
            1
            for i, v in money.items()
            if v > 0.005 and all(n.name in mine for _, n in swaps[i].changes.values())
        )
        out.append(
            f"  - hindsight {label} ({len(swaps):,} legal): best {describe(swaps[best])}: finished "
            f"{finals[best]:.2f}, ${money[best]:+,.2f}; {better} of the 40 top-scoring swaps pay more than "
            "keeping "
            f"({from_pool} of them from his pool)."
        )
        row[f"hindsight best {label}"] = max(money[best], 0.0)
    return out, row


def summary(rows: list[dict]) -> list[str]:
    if not rows:
        return []
    df = pd.DataFrame(rows).fillna(0.0)
    cols = [c for c in df.columns if c not in ("week", "at", "lineup", "goal", "base")]
    out = ["## Summary: dollars won or lost against keeping, summed over his lineups", ""]
    out.append("| when | " + " | ".join(cols) + " |")
    out.append("|---|" + "---:|" * len(cols))
    for (week, at), g in df.groupby(["week", "at"], sort=False):
        out.append(f"| Week {week}, {at} | " + " | ".join(f"${g[c].sum():+,.2f}" for c in cols) + " |")
    out.append("")
    return out


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--pbp", required=True, help="nflverse play_by_play_<season>.parquet (full columns)")
    parser.add_argument("--weeks", default="7,8")
    parser.add_argument("--sims", type=int, default=10000)
    parser.add_argument("--seed", type=int, default=lf.SEED)
    args = parser.parse_args(argv)
    pbp = pd.read_parquet(args.pbp)
    body, rows = [], []
    for week in (int(w) for w in args.weeks.split(",")):
        lines, week_rows = replay_week(week, pbp, n_sims=args.sims, seed=args.seed)
        body += lines
        rows += week_rows
    out = [f"# Late-swap replay, Weeks {args.weeks} ({args.sims:,} draws)", "", *summary(rows), *body]
    print("\n".join(out))


if __name__ == "__main__":
    main()
