"""The Season sheet's layout, pure: every tab's rows from the leagues' views and trade ideas.

One sheet for every league; leagues are rows and pickers, never extra tabs (Sam, 2026-10-29). Six visible
tabs and three hidden raw ones:

- **Overview**: one row per league, then Sam's exposure (each player he rosters and in how many leagues);
- **My Teams**: one block per league (roster with values and this week's lineup, the needs line);
- **Trade Finder**: the targeting box (`BOX_FIELDS`, the only typed cells besides the pickers) and every
  league's ideas in one list with a League column;
- **League** / **Players**: a league picker in `B1` and a FILTER over the raw tab below it;
- **Analyzer**: the last trade analyzed.
- `SeasonLeaguesRaw` (the picker's list), `SeasonTeamsRaw`, `SeasonPlayersRaw`: rewritten whole by every
  `dfs season sync`.

Every column a formula reads is found by name in `TEAM_COLUMNS` / `PLAYER_COLUMNS` (`raw_letter`), never typed
as a letter, so a column added to a raw tab moves every formula with it.
"""

from __future__ import annotations

import math
import re
from collections import defaultdict
from dataclasses import dataclass, field

import pandas as pd

from dfs.season_long import finder as fd
from dfs.season_long import trade as tr
from dfs.season_long.league_view import LeagueView
from dfs.season_long.objective import Evaluator
from dfs.sheets import column_letter

OVERVIEW_TAB = "Overview"
MY_TEAMS_TAB = "My Teams"
TRADE_FINDER_TAB = "Trade Finder"
LEAGUE_TAB = "League"
PLAYERS_TAB = "Players"
ANALYZER_TAB = "Analyzer"
WAIVERS_TAB = "Waivers"
VISIBLE_TABS = (
    OVERVIEW_TAB,
    MY_TEAMS_TAB,
    WAIVERS_TAB,
    TRADE_FINDER_TAB,
    LEAGUE_TAB,
    PLAYERS_TAB,
    ANALYZER_TAB,
)
LEAGUES_RAW = "SeasonLeaguesRaw"
TEAMS_RAW = "SeasonTeamsRaw"
PLAYERS_RAW = "SeasonPlayersRaw"
RAW_TABS = (LEAGUES_RAW, TEAMS_RAW, PLAYERS_RAW)
PICKER_CELL = "B1"
RAW_ROWS = 5000

SLOT_TYPES = ("QB", "RB", "WR", "TE", "FLEX", "SUPER_FLEX", "WRRB_FLEX", "REC_FLEX", "K", "DEF")
TEAM_COLUMNS = [
    "League", "Team", "Manager", "Mine", "Record", "PointsFor", "FAAB", "ValueRank", "TeamValue",
    "PlayoffOdds", "SurviveThisWeek", "ExpWeeksSurvived", "LastManStanding", "BiggestNeed", "Needs",
    *[f"{s} pts/wk" for s in SLOT_TYPES],
]  # fmt: skip
PLAYER_COLUMNS = [
    "League", "Name", "Pos", "NFL", "Status", "Owner", "Mine", "ROS", "ROSWeighted", "PlayoffWeeks",
    "NextWeek", "GamesLeft", "OurValue", "Consensus", "OursMinusConsensus", "Read",
]  # fmt: skip
# The League and Players tabs show these raw columns (all but League, which the picker already says), sorted.
LEAGUE_VIEW = [c for c in TEAM_COLUMNS if c not in {"League", "Mine"}]
LEAGUE_SORT = "ValueRank"
PLAYERS_VIEW = [c for c in PLAYER_COLUMNS if c != "League"]
PLAYERS_SORT = "ROSWeighted"
VIEW_HEADER_ROW = 3

BOX_TITLE_ROW = 1
BOX_FIELDS = ("League", "Get", "Give", "Keep", "Partner", "Shapes", "Max consensus loss", "Show lopsided")
BOX_FIRST_ROW = 2
IDEAS_HEADER = [
    "League", "#", "You give", "You get", "Partner", "Your gain", "Their gain", "Market for them",
    "Read", "Why",
]  # fmt: skip
IDEAS_HEADER_ROW = BOX_FIRST_ROW + len(BOX_FIELDS) + 2

OVERVIEW_HEADER = [
    "League", "Sleeper league", "Kind", "Record", "Standing", "Team value rank", "Odds", "Biggest need",
    "Free move first", "Best trade idea", "Ours vs market", "Note",
]  # fmt: skip
MEDIAN_NOTE = "Plays a second game each week against the league median: weekly points count double."


def box_cell(name: str) -> str:
    return f"B{BOX_FIRST_ROW + BOX_FIELDS.index(name)}"


def raw_letter(columns: list[str], name: str) -> str:
    return column_letter(columns.index(name))


def _pct(x: float) -> str:
    return "" if x is None or (isinstance(x, float) and math.isnan(x)) else f"{x:.0%}"


# -- the data ----------------------------------------------------------------------------------------------


@dataclass
class LeagueRun:
    """One league's sync: the view, its evaluator and market, and the ideas found (top first)."""

    view: LeagueView
    ev: Evaluator
    market: tr.Market
    ideas: list[fd.Idea]
    free_move: tuple[list[str] | None, list[str], list[str]] = (None, [], [])
    notes: list[str] = field(default_factory=list)
    check: str = ""  # consensus_check.CheckResult.line()
    pickups: list = field(default_factory=list)  # waivers.Pickup, best first
    cuffs: list = field(default_factory=list)  # waivers.handcuffs
    chop: object = None  # waivers.ChopPlan in a chopped league


def _standing(view: LeagueView, rid: int) -> int:
    teams = view.league.alive_teams()
    order = sorted(teams, key=lambda t: (-t.wins, -t.points_for))
    return [t.roster_id for t in order].index(rid) + 1


def _value_ranks(run: LeagueRun) -> dict[int, int]:
    base = run.ev.base()
    order = sorted(base, key=lambda r: -base[r].value)
    return {rid: i + 1 for i, rid in enumerate(order)}


def needs_text(run: LeagueRun, rid: int, n: int = 3) -> str:
    return (
        ", ".join(f"{s} {d:+.1f}/wk (#{r})" for s, d, r in run.view.needs.get(rid, [])[:n] if d < 0) or "none"
    )


def teams_frame(runs: list[LeagueRun]) -> pd.DataFrame:
    rows = []
    for run in runs:
        v, lg = run.view, run.view.league
        base, ranks = run.ev.base(), _value_ranks(run)
        for t in lg.alive_teams():
            o = base[t.roster_id]
            need = v.needs.get(t.roster_id, [])
            strength = v.strengths.get(t.roster_id, {})
            rows.append({
                "League": lg.name, "Team": t.label, "Manager": t.manager,
                "Mine": "Y" if t.roster_id == lg.my_roster_id else "",
                "Record": "" if lg.chopped else f"{t.wins}-{t.losses}" + (f"-{t.ties}" if t.ties else ""),
                "PointsFor": t.points_for, "FAAB": t.faab_left, "ValueRank": ranks[t.roster_id],
                "TeamValue": round(o.value, 3 if lg.chopped else 1),
                "PlayoffOdds": "" if lg.chopped else round(o.p_playoffs, 3),
                "SurviveThisWeek": round(o.p_survive_now, 3) if lg.chopped else "",
                "ExpWeeksSurvived": round(o.expected_weeks, 2) if lg.chopped else "",
                "LastManStanding": round(o.p_last_standing, 3) if lg.chopped else "",
                "BiggestNeed": need[0][0] if need and need[0][1] < 0 else "",
                "Needs": needs_text(run, t.roster_id),
                **{f"{s} pts/wk": strength.get(s, "") for s in SLOT_TYPES},
            })  # fmt: skip
    return pd.DataFrame(rows, columns=TEAM_COLUMNS)


def players_frame(runs: list[LeagueRun]) -> pd.DataFrame:
    rows = []
    for run in runs:
        v, lg = run.view, run.view.league
        owner = {p: t for t in lg.alive_teams() for p in t.players}
        s = v.ros.summary
        keep = s[(s["ros_pts"] > 0) | s.index.isin(owner)]
        for pid, r in keep.iterrows():
            ours, cons = float(run.market.ours.get(pid, 0.0)), float(run.market.consensus.get(pid, 0.0))
            gap = ours - cons
            read = ""
            if abs(gap) >= tr.DISAGREE_MIN_PTS and max(ours, cons) >= min(ours, cons) * (
                1 + tr.DISAGREE_SHARE
            ):
                read = "we like him more" if gap > 0 else "market likes him more"
            t = owner.get(pid)
            rows.append({
                "League": lg.name, "Name": r["name"], "Pos": r["position"], "NFL": r["team"],
                "Status": r["injury_status"], "Owner": t.label if t else "free agent",
                "Mine": "Y" if t is not None and t.roster_id == lg.my_roster_id else "",
                "ROS": r["ros_pts"], "ROSWeighted": r["ros_weighted"], "PlayoffWeeks": r["playoff_pts"],
                "NextWeek": "" if pd.isna(r["next_week"]) else round(float(r["next_week"]), 1),
                "GamesLeft": int(r["games_left"]), "OurValue": round(ours, 1), "Consensus": round(cons, 1),
                "OursMinusConsensus": round(gap, 1), "Read": read,
            })  # fmt: skip
    frame = pd.DataFrame(rows, columns=PLAYER_COLUMNS)
    return frame.sort_values(["League", "ROSWeighted"], ascending=[True, False]).head(RAW_ROWS - 1)


_DATE_LIKE = re.compile(r"^\d{1,4}[-/]\d{1,2}([-/]\d{1,4})?$")


def literal(value: object) -> object:
    """A text cell as typed text. Sheets parses what it is sent (USER_ENTERED), so "8-6" became a date and
    "+33.4 value" a broken formula; a leading apostrophe keeps either as the text it is. Numbers pass."""
    if isinstance(value, str) and (value[:1] in {"=", "+", "-"} or _DATE_LIKE.match(value)):
        return "'" + value
    return value


def literal_rows(rows: list[list]) -> list[list]:
    return [[literal(v) for v in row] for row in rows]


def cells(frame: pd.DataFrame) -> list[list]:
    body = frame.astype(object).where(frame.notna(), "").values.tolist()
    return literal_rows([list(frame.columns), *body])


# -- visible tabs ------------------------------------------------------------------------------------------


def side_text(view: LeagueView, side: tr.Side) -> str:
    return (
        ", ".join([view.name(p) for p in side.players] + ([f"${side.faab} FAAB"] if side.faab else [])) or "-"
    )


def gain_text(a: tr.Analysis, t: tr.TeamResult) -> str:
    if a.chopped:
        return f"{t.after.expected_weeks - t.before.expected_weeks:+.2f} wks survived"
    return f"{t.delta:+.1f} value ({_pct(t.before.p_playoffs)}->{_pct(t.after.p_playoffs)} playoffs)"


def free_move_text(run: LeagueRun) -> str:
    roster, added, dropped = run.free_move
    if roster is None:
        return "none"
    v = run.view
    return "; ".join(f"add {v.name(a)}, drop {v.name(d)}" for a, d in zip(added, dropped, strict=True))


def overview_rows(runs: list[LeagueRun]) -> list[list]:
    rows: list[list] = [["Season: every league", *[""] * (len(OVERVIEW_HEADER) - 1)], OVERVIEW_HEADER]
    for run in runs:
        v, lg = run.view, run.view.league
        me = lg.my_roster_id
        o = run.ev.base()[me]
        kind = "chopped" if lg.chopped else "redraft"
        record = (
            f"{len(lg.alive_teams())} of {len(lg.teams)} alive"
            if lg.chopped
            else f"{lg.me.wins}-{lg.me.losses}"
        )
        standing = "" if lg.chopped else f"{_standing(v, me)} of {len(lg.alive_teams())}"
        if lg.chopped:
            odds = (
                f"survive wk {lg.remaining_weeks[0]} {_pct(o.p_survive_now)}, last man "
                f"{o.p_last_standing:.1%}"
            )
        else:
            odds = f"playoffs {_pct(o.p_playoffs)}"
        need = v.needs.get(me, [])
        best = run.ideas[0] if run.ideas else None
        idea = (
            f"give {side_text(v, best.analysis.give)} for {side_text(v, best.analysis.get)} "
            f"({lg.team(best.analysis.partner).label}; {gain_text(best.analysis, best.analysis.me)})"
            if best
            else "none found"
        )
        note = "; ".join(run.notes)
        rows.append(
            [
                lg.name,
                lg.title,
                kind,
                record,
                standing,
                f"{_value_ranks(run)[me]} of {len(lg.alive_teams())}",
                odds,
                f"{need[0][0]} {need[0][1]:+.1f}/wk" if need and need[0][1] < 0 else "none",
                free_move_text(run),
                idea,
                run.check,
                note,
            ]
        )
    rows += [
        [""],
        ["Your exposure: each player you roster, and in how many leagues"],
        ["Player", "Pos", "Leagues", "Which"],
    ]
    seen: dict[str, list[str]] = defaultdict(list)
    pos: dict[str, str] = {}
    for run in runs:
        for pid in run.view.league.me.players:
            name = run.view.name(pid)
            seen[name].append(run.view.league.name)
            pos[name] = str(run.view.table.at[pid, "position"]) if pid in run.view.table.index else ""
    for name in sorted(seen, key=lambda n: (-len(seen[n]), n)):
        rows.append([name, pos[name], len(seen[name]), ", ".join(seen[name])])
    return rows


@dataclass
class Block:
    rows: list[list]
    title_rows: list[int]
    header_rows: list[int]


def my_teams_block(runs: list[LeagueRun]) -> Block:
    rows: list[list] = []
    titles, headers = [], []
    for run in runs:
        v, lg = run.view, run.view.league
        me = lg.me
        week = lg.remaining_weeks[0] if lg.remaining_weeks else None
        lineup = next((w for w in run.ev.lineups(me.players) if w.week == week), None)
        slot_of = {pid: lg.slots[j] for j, pid in (lineup.starters.items() if lineup else [])}
        titles.append(len(rows) + 1)
        rows.append([f"{lg.name}: {me.label} ({lg.title})"])
        rows.append([f"Needs: {needs_text(run, me.roster_id)}. Free move first: {free_move_text(run)}."])
        headers.append(len(rows) + 1)
        rows.append(
            [
                f"Week {week} slot",
                "Player",
                "Pos",
                "NFL",
                "Status",
                f"Wk {week}",
                "ROS",
                "ROS weighted",
                "Market",
            ]
        )
        s = v.ros.summary
        roster = s.reindex(me.players).sort_values("ros_weighted", ascending=False)
        order = sorted(
            roster.index, key=lambda p: (p not in slot_of, lg.slots.index(slot_of[p]) if p in slot_of else 0)
        )
        for pid in order:
            r = roster.loc[pid]
            rows.append(
                [
                    slot_of.get(pid, "bench"),
                    r["name"],
                    r["position"],
                    r["team"],
                    r["injury_status"],
                    "" if pd.isna(r["next_week"]) else round(float(r["next_week"]), 1),
                    r["ros_pts"],
                    r["ros_weighted"],
                    round(float(run.market.consensus.get(pid, 0.0)), 1),
                ]
            )
        rows.append([""])
    return Block(rows, titles, headers)


def ideas_rows(runs: list[LeagueRun]) -> list[list]:
    out = [IDEAS_HEADER]
    for run in runs:
        v = run.view
        for i, idea in enumerate(run.ideas, 1):
            a = idea.analysis
            out.append(
                [
                    v.league.name,
                    i,
                    side_text(v, a.give),
                    side_text(v, a.get),
                    v.league.team(a.partner).label,
                    gain_text(a, a.me),
                    gain_text(a, a.them),
                    f"{a.fair_ratio:.0%}" if math.isfinite(a.fair_ratio) else "all gain",
                    "; ".join(a.labels),
                    idea.why,
                ]
            )
    return out


def box_rows(values: dict[str, str]) -> list[list]:
    rows = [['Targeting (type in column B, then say "find me trades")']]
    for name in BOX_FIELDS:
        rows.append([name, values.get(name, "")])
    rows.append(
        [
            "Names or positions, comma-separated. Shapes: 1-1, 2-1, 1-2, 2-2. Loss: e.g. 10%. "
            "Show lopsided: yes adds ideas the market has over 125% for the partner."
        ]
    )
    return rows


def view_formula(raw: str, columns: list[str], view: list[str], sort_by: str, ascending: bool) -> str:
    """`=SORT(FILTER({cols}, League = B1), sort col, asc)` over the raw tab, columns by name."""
    league = raw_letter(columns, "League")
    # Open-ended ranges (`B2:B`): a sync that grows a raw tab inserts rows, and Sheets moves a fixed end row
    # (B2:B5000 became B2:B6085 on the first real sync), which the doctor then reads as stale.
    picks = ", ".join(f"{raw}!{raw_letter(columns, c)}2:{raw_letter(columns, c)}" for c in view)
    cond = f"{raw}!{league}2:{league}=$B$1"
    sort_index = view.index(sort_by) + 1
    return (
        f'=IFERROR(SORT(FILTER({{{picks}}}, {cond}), {sort_index}, {"TRUE" if ascending else "FALSE"}), "")'
    )


def league_tab_rows() -> list[list]:
    return [
        ["League", ""],
        ["Every team's strength by slot (points per week from that slot in its best lineups) and needs."],
        LEAGUE_VIEW,
        [view_formula(TEAMS_RAW, TEAM_COLUMNS, LEAGUE_VIEW, LEAGUE_SORT, True)],
    ]


def players_tab_rows() -> list[list]:
    return [
        ["League", ""],
        [
            "Rest-of-season points in this league's scoring; our value and the market's "
            "(FantasyPros ROS consensus)."
        ],
        PLAYERS_VIEW,
        [view_formula(PLAYERS_RAW, PLAYER_COLUMNS, PLAYERS_VIEW, PLAYERS_SORT, False)],
    ]


def analyzer_rows(view: LeagueView, lines: list[str], a: tr.Analysis, when: str) -> list[list]:
    rows: list[list] = [[f"Analyzer: {view.league.name} ({view.league.title}), {when}"], [""]]
    rows += [[line.strip()] for line in lines]
    rows += [[""], ["Week", "Your lineup change", "Their lineup change"]]
    for w in sorted(a.me.weekly_gain):
        tag = " (playoffs)" if w in view.league.playoff_weeks else ""
        rows.append([f"{w}{tag}", a.me.weekly_gain[w], a.them.weekly_gain.get(w, 0.0)])
    return rows


WAIVERS_HEADER = ["League", "#", "Add", "Pos", "NFL", "Drop", "Your gain", "This week", "Bid guide", "Note"]


def waivers_block(runs: list[LeagueRun]) -> Block:
    """Every league's best pickups in one list, then each league's handcuffs and, if chopped, the bid plan."""
    rows: list[list] = [["Waivers: the best free agents for your team, and what to bid"], WAIVERS_HEADER]
    titles, headers = [1], [2]
    for run in runs:
        v = run.view
        s = v.ros.summary
        for i, p in enumerate(run.pickups, 1):
            gain = f"{p.expected_weeks:+.2f} wks survived" if v.league.chopped else f"{p.value:+.1f} value"
            rows.append([
                v.league.name, i, v.name(p.player_id), s["position"].get(p.player_id, ""),
                s["team"].get(p.player_id, ""), v.name(p.drop) if p.drop else "open spot", gain,
                f"{p.this_week:+.1f}", p.bid.text(v.league.me.faab_left), p.note,
            ])  # fmt: skip
    for run in runs:
        v = run.view
        if run.cuffs:
            rows += [[""], [f"{v.league.name}: handcuffs to stash (value if his RB1 misses time)"]]
            titles.append(len(rows))
            for pid, value, rb1 in run.cuffs:
                rows.append([v.league.name, "", v.name(pid), "RB", v.ros.summary["team"].get(pid, ""), "",
                             f"+{value:.1f}", "", f"backs up {v.name(rb1)}", ""])  # fmt: skip
        plan = run.chop
        if plan is not None:
            rows += [
                [""],
                [
                    f"{v.league.name}: bid plan for {plan.team} "
                    f"({plan.chop_risk:.0%} likely chopped this week)"
                ],
            ]
            titles.append(len(rows))
            rows.append(
                [
                    "Player",
                    "Your survival gain",
                    "Worth to you",
                    "League paid",
                    "Bid",
                    f"You have ${plan.faab}",
                ]
            )
            headers.append(len(rows))
            for pid, gain, worth, price in plan.picks:
                rows.append(
                    [v.name(pid), f"{gain:+.3f}", f"${worth}", f"${price:.0f}", plan.bid(worth, price)]
                )
            if plan.rivals:
                rows.append(["Most FAAB: " + ", ".join(f"{n} ${f}" for n, f in plan.rivals)])
    return Block(rows, titles, headers)
