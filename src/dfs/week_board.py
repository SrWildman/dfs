"""Week page v2's answers, as plain data: the board, the pool panel, the brief, the decisions and the week's
progress. Pure: every input is something `week_inputs.gather` already read (and, in serve mode, what
`week_writes.SheetEdits.read` got from the sheet); nothing here reads a file, a sheet or the network, and the
HTML is `week_top`'s.

**The board** is one table that replaces Plan I's four Plays lists. Its roles are Plan I's definitions
(`week_plays.plays`), unchanged:

- *Value*: CalPts per $1K, CalPts the tiebreak;
- *Cash core*: the best Floor per $1K among the top half on CalPts, at most 4 QB / TE / DST and 8 RB / WR
  (`week_plays.cash_core`); *Chalk core*: every 30%+ CashOwn player, shown even outside the caps and never
  behind Show more (a player on both lists is chalk core);
- *Upside*: best Boom% (once GppOwn is out, the top half of Boom% by leverage);
- *Fade*: the `FADE↓` chip.

Each player appears once with every role he holds; the top `SHOWN` of each role per position are shown, the
next ones (to `DEPTH`) sit behind *Show more*. **Fade wins**: a player with `FADE↓` is only ever a Fade, at
the bottom of his position with the signal's record, never also a play.

The Why line adds what the columns do not show (his role and usage, the game, the matchup, an injury or the
wind); never a restatement such as points per $1K.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from dfs import kickoff, nfl_calendar, week_plays
from dfs.week_inputs import WeekInputs, parse_time
from dfs.week_plays import FADE, POSITIONS, num

VALUE, CASH, CHALK_CORE, UPSIDE, FADE_ROLE = "Value", "Cash core", "Chalk core", "Upside", "Fade"
ROLES = (VALUE, CASH, CHALK_CORE, UPSIDE, FADE_ROLE)
CASH_ROLES = (VALUE, CASH, CHALK_CORE)  # a starting-pool Cash
SHOWN = 3  # per role and position, on the board without Show more
DEPTH = 8  # per role and position, with Show more
CHALK = week_plays.CHALK
STAR = 15.0  # a Questionable player projected this many CalPts is a "star" (a decision of his own)
VALUE_LINE = 3.0  # points per $1K: the value map's reference line and the brief's "thinnest position"
CHEAP = {"QB": 5500, "RB": 5000, "WR": 4500, "TE": 4000, "DST": 3000}  # the brief's "cheap"
PIVOT_RANGE = 800  # a pivot for a Questionable star: same position, within this many dollars
PIVOTS = 3
DATA_LIKES = 5


def avail(row: dict) -> str:
    return week_plays.avail(row)


def implied_total(row: dict) -> float | None:
    """His team's implied total: half the game total minus half his team's spread."""
    total, spread = num(row.get("OverUnder")), num(row.get("Spread"))
    if total is None or spread is None:
        return None
    return total / 2 - spread / 2


def opp_implied(row: dict) -> float | None:
    total, spread = num(row.get("OverUnder")), num(row.get("Spread"))
    if total is None or spread is None:
        return None
    return total / 2 + spread / 2


def per_k(row: dict) -> float | None:
    return week_plays.value_per_k(row)


def pid(row: dict) -> str:
    return str(row.get("Id") or "")


def short_name(name: str) -> str:
    """The last name for a label ("Smith-Njigba"); a DST keeps its name; a suffix is skipped."""
    parts = str(name).split()
    while len(parts) > 1 and re.fullmatch(r"(Jr\.?|Sr\.?|II|III|IV|V)", parts[-1]):
        parts.pop()
    return parts[-1] if parts else str(name)


# -- the Why line ------------------------------------------------------------------------------------------


def game_rank(edge: list[dict]) -> dict[str, int]:
    """GameID (or team pair) -> rank by total (1 = the slate's highest)."""
    games = week_plays.slate(edge)
    return {g.key: i + 1 for i, g in enumerate(g for g in games if g.total is not None)}


def game_key(row: dict) -> str:
    return str(row.get("GameID") or "") or "|".join(sorted(t for t in (row.get("Team"), row.get("Opp")) if t))


def why_facts(row: dict, ranks: dict[str, int] | None = None) -> str:
    """What the board's columns do not already say: his role and usage, the game, the matchup, the injury and
    the wind. Never "X points per $1K" (that is the Per $1K column)."""
    pos = row.get("Position", "")
    parts: list[str] = []
    snap = num(row.get("Snap% L5"))
    share = (snap * 100 if snap <= 1.5 else snap) if snap is not None else None
    tgt, car = num(row.get("Tgt L5")), num(row.get("Car L5"))
    rank = num(row.get("TmRank"))
    role = f"{pos}{rank:.0f}" if rank is not None and pos in ("RB", "WR", "TE") else ""
    if pos == "RB" and car is not None:
        use = f"{car:.0f} carries" + (f" + {tgt:.0f} targets" if tgt else "") + " a game"
    elif pos in ("WR", "TE") and tgt is not None:
        use = f"{tgt:.1f}".removesuffix(".0") + " targets a game"
    elif pos == "QB" and (num(row.get("DK L5")) or 0) >= 1:
        use = f"{num(row.get('DK L5')):.1f} DK pts a game lately"
    else:
        use = ""
    if use and share is not None and pos in ("RB", "WR", "TE"):
        use += f", {share:.0f}% of snaps"
    parts.append(" ".join(x for x in (role + ":" if role and use else role, use) if x))
    implied = implied_total(row)
    g = (ranks or {}).get(game_key(row))
    if pos == "DST":
        opp = opp_implied(row)
        if opp is not None:
            parts.append(f"{row.get('Opp', '')} implied {opp:.1f}")
    elif implied is not None:
        top = f", the slate's #{g} game" if g is not None and g <= 2 else ""
        parts.append(f"team total {implied:.1f}{top}")
    opp_rank = num(row.get("OppPosRank"))
    if opp_rank is not None and pos != "DST":
        if opp_rank <= 8:
            parts.append(f"soft matchup ({opp_rank:.0f} of 32 vs {pos})")
        elif opp_rank >= 25:
            parts.append(f"tough matchup ({opp_rank:.0f} of 32 vs {pos})")
    wind = num(row.get("Wind"))
    if wind is not None and wind >= week_plays.WIND_MPH and str(row.get("Roof") or "") != "dome":
        parts.append(f"{wind:.0f} mph wind")
    if "INJ+" in str(row.get("Edge") or ""):
        parts.append("a regular teammate is out")
    status = avail(row)
    if status in week_plays.STATUS_WORD:
        parts.append(week_plays.STATUS_WORD[status])
    return " · ".join(p for p in parts if p)


# -- the board ---------------------------------------------------------------------------------------------


@dataclass
class BoardRow:
    row: dict
    roles: list[str]
    shown: bool
    why: str
    record: str = ""  # a fade's Model Check record

    @property
    def name(self) -> str:
        return str(self.row.get("Name", ""))

    @property
    def id(self) -> str:
        return pid(self.row)

    @property
    def fade(self) -> bool:
        return FADE_ROLE in self.roles

    @property
    def calpts(self) -> float:
        return num(self.row.get("CalPts")) or 0.0


@dataclass
class Board:
    by_pos: dict[str, list[BoardRow]]
    cash_pending: bool
    gpp_pending: bool
    players: int

    def rows(self) -> list[BoardRow]:
        return [r for pos in POSITIONS for r in self.by_pos.get(pos, [])]

    def role_of(self) -> dict[str, list[str]]:
        """Name -> roles, for every player on the board (shown or behind Show more)."""
        return {r.name: r.roles for r in self.rows()}

    @property
    def empty(self) -> bool:
        return not self.players


def _index(edge: list[dict]) -> dict[tuple[str, str], dict]:
    return {(str(r.get("Name", "")), str(r.get("Team", ""))): r for r in edge}


def board(inp: WeekInputs) -> Board:
    edge = inp.edge or []
    view = week_plays.plays(edge, inp.pool, per=DEPTH)
    by_key = _index(edge)
    ranks = game_rank(edge)
    faded = {(r.get("Name"), r.get("Team")) for r in edge if week_plays.has_token(r, FADE)}
    out: dict[str, list[BoardRow]] = {}
    for pos in POSITIONS:
        roles: dict[tuple[str, str], list[str]] = {}
        shown: set[tuple[str, str]] = set()
        for role, plays in (
            (VALUE, view.value.get(pos, [])),
            (CASH, view.cash.get(pos, [])),
            (UPSIDE, view.gpp.get(pos, [])),
        ):
            kept = [p for p in plays if (p.name, p.team) not in faded]  # fade wins
            for rank, p in enumerate(kept):
                key = (p.name, p.team)
                chalk_core = role == CASH and p.chalk_core
                roles.setdefault(key, []).append(CHALK_CORE if chalk_core else role)
                if rank < SHOWN or chalk_core:  # chalk core is never behind Show more
                    shown.add(key)
        rows = [
            BoardRow(by_key[k], r, k in shown, why_facts(by_key[k], ranks))
            for k, r in roles.items()
            if k in by_key
        ]
        rows.sort(key=lambda b: (not b.shown, -len(b.roles), -b.calpts))
        fades = [p for p in view.fades if p.pos == pos]
        for i, p in enumerate(fades):
            row = by_key.get((p.name, p.team))
            if row is None:
                continue
            record = week_plays.record_line(FADE, inp.signal_table, inp.scored_weeks)
            rows.append(BoardRow(row, [FADE_ROLE], i < SHOWN, why_facts(row, ranks), record))
        if rows:
            out[pos] = rows
    return Board(out, view.cash_own_pending, view.gpp_own_pending, view.players)


# -- the pool ----------------------------------------------------------------------------------------------


def pool_by_name(inp: WeekInputs, live_pool: dict[str, str] | None) -> dict[str, str]:
    """Name -> Cash / GPP / Both. Serve mode passes the sheet's pool (by Id), else the last saved snapshot."""
    if live_pool is None:
        return {k: v for k, v in inp.pool.items() if v}
    return {str(r.get("Name", "")): live_pool[pid(r)] for r in inp.edge or [] if live_pool.get(pid(r))}


def pool_by_id(inp: WeekInputs, live_pool: dict[str, str] | None) -> dict[str, str]:
    if live_pool is not None:
        return dict(live_pool)
    names = pool_by_name(inp, None)
    return {pid(r): names[r.get("Name", "")] for r in inp.edge or [] if r.get("Name") in names and pid(r)}


def in_kind(value: str, kind: str) -> bool:
    return value == "Both" or value == kind


@dataclass
class PoolPanel:
    total: int
    counts: dict[str, dict[str, int]]  # "Cash" / "GPP" -> position -> count
    gaps: list[str]
    questionable: list[dict]  # pool rows that are Q / D
    out: list[dict]  # pool rows that are OUT / IR
    likes: list[BoardRow]  # the board's best who are not in the pool
    start: list[tuple[dict, str]]  # the suggested starting pool: (row, Cash / GPP / Both), not yet pooled


def pool_panel(inp: WeekInputs, b: Board, pool: dict[str, str]) -> PoolPanel:
    rows = {str(r.get("Name", "")): r for r in inp.edge or []}
    counts = {k: dict.fromkeys(POSITIONS, 0) for k in ("Cash", "GPP")}
    for name, value in pool.items():
        pos = (rows.get(name) or {}).get("Position", "")
        for kind in counts:
            if pos in counts[kind] and in_kind(value, kind):
                counts[kind][pos] += 1
    gaps: list[str] = []
    for kind, by_pos in counts.items():
        if not sum(by_pos.values()):
            continue
        missing = [p for p in POSITIONS if by_pos[p] == 0]
        if missing:
            gaps.append(f"{kind}: no {', no '.join(missing)}")
        if by_pos["DST"] == 1:
            gaps.append(f"{kind}: one DST")
    pooled = [rows[n] for n in pool if n in rows]
    questionable = [r for r in pooled if avail(r) in ("Q", "D")]
    out = [r for r in pooled if avail(r) in ("OUT", "IR")]
    likes = [r for r in b.rows() if r.shown and not r.fade and r.name not in pool]
    likes.sort(key=lambda r: (r.row.get("Position") == "DST", -min(len(r.roles), 2), -r.calpts))
    return PoolPanel(
        total=len(pool),
        counts=counts,
        gaps=gaps,
        questionable=questionable,
        out=out,
        likes=likes[:DATA_LIKES],
        start=starting_pool(b, pool),
    )


def starting_pool(b: Board, pool: dict[str, str]) -> list[tuple[dict, str]]:
    """The board's shown, healthy, non-fade players not already pooled: Value, Cash core and Chalk core as
    Cash, Upside as GPP, both kinds as Both. A player Sam already pooled is never changed."""
    out = []
    for r in b.rows():
        if not r.shown or r.fade or r.name in pool or avail(r.row) in week_plays.STATUS_WORD:
            continue
        out.append((r.row, kind_of(r.roles)))
    return out


def kind_of(roles) -> str:
    """The pool kind a set of board roles points to: Value / Cash core / Chalk core = Cash, Upside = GPP,
    both = Both (the starting pool's rule, and the one-click add's)."""
    cash = any(role in roles for role in CASH_ROLES)
    gpp = UPSIDE in roles
    return "Both" if cash and gpp else "Cash" if cash else "GPP"


# -- the decisions -----------------------------------------------------------------------------------------


@dataclass
class Option:
    key: str  # what the page sends back
    label: str


@dataclass
class Decision:
    key: str  # "chalk:<Id>" | "q:<Id>" | "stack"
    kind: str  # chalk | q | stack
    row: dict | None  # the player (chalk / q)
    options: list[Option]
    answer: str = ""  # the option chosen, as the sheet (or the decisions file) says
    answer_text: str = ""
    undo: bool = False  # the page can undo the answer
    pivots: list[dict] = field(default_factory=list)  # q: the pivots offered
    stacks: list[dict] = field(default_factory=list)  # stack: the two stacks
    say: str = ""  # static page: the phrase that does the same


def slate_names(inp: WeekInputs) -> list[str]:
    return list(dict.fromkeys(str(r.get("Name", "")) for r in inp.edge or [] if r.get("Name")))


def parsed_rules(inp: WeekInputs, rules: dict[str, str] | None):
    from dfs import suggest_state as st

    if rules is None:
        return None
    teams = {str(r.get("Team")) for r in inp.edge or [] if r.get("Team")}
    return st.parse_rules(st.RulesText.from_labels(rules), slate_names(inp), teams)


def chalk_rows(inp: WeekInputs) -> list[dict]:
    """Every non-DST player the double-ups are expected to own at `CHALK`+, most owned first."""
    rows = [
        r
        for r in inp.edge or []
        if (num(r.get("CashOwn")) or 0) >= CHALK
        and r.get("Position") != "DST"
        and avail(r) not in week_plays.UNPLAYABLE
    ]
    return sorted(rows, key=lambda r: -(num(r.get("CashOwn")) or 0))


def q_stars(inp: WeekInputs) -> list[dict]:
    rows = [r for r in inp.edge or [] if avail(r) == "Q" and (num(r.get("CalPts")) or 0) >= STAR]
    return sorted(rows, key=lambda r: -(num(r.get("CalPts")) or 0))


def pivots_for(inp: WeekInputs, star: dict) -> list[dict]:
    """The best healthy players at his position within `PIVOT_RANGE` of his salary (no fades), by CalPts."""
    sal = week_plays.salary(star)
    rows = [
        r
        for r in week_plays.rosterable(inp.edge or [])
        if r.get("Position") == star.get("Position")
        and r.get("Name") != star.get("Name")
        and not avail(r)
        and abs(week_plays.salary(r) - sal) <= PIVOT_RANGE
        and not week_plays.has_token(r, FADE)
    ]
    return sorted(rows, key=lambda r: -(num(r.get("CalPts")) or 0))[:PIVOTS]


def stack_names(stack: dict) -> list[str]:
    mates = [m.strip() for m in re.split(r"[,+]", str(stack.get("mates") or "")) if m.strip()]
    return [str(stack.get("qb") or "").strip(), *mates]


def top_two_stacks(inp: WeekInputs) -> list[dict]:
    """The best saved stack and the best one after it with a different QB (two stacks of one QB are not a
    choice); else the top two as they are."""
    rows = list((inp.stacks or {}).get("rows") or [])
    if len(rows) < 2:
        return rows
    other = next((r for r in rows[1:] if r.get("qb") != rows[0].get("qb")), rows[1])
    return [rows[0], other]


def decisions(
    inp: WeekInputs, rules: dict[str, str] | None, answers: dict | None, *, chalk_max: int = 3, q_max: int = 2
) -> list[Decision]:
    """3-6 explicit choices: each 30%+ double-up chalk player (Play / Fade), each Questionable star (a pivot
    or wait), and the GPP stack (the top two). `rules` is the sheet's rules box (None: not read, so nothing is
    shown as answered from it); `answers` is the decisions file (pivots)."""
    parsed = parsed_rules(inp, rules)
    out: list[Decision] = []
    for r in chalk_rows(inp)[:chalk_max]:
        name = r["Name"]
        d = Decision(
            f"chalk:{pid(r)}",
            "chalk",
            r,
            [Option("play", "Play"), Option("fade", "Fade")],
            say=f"always {name} in cash / never {name} in cash",
        )
        if parsed is not None:
            if name in parsed.always_kind.get("cash", []):
                d.answer, d.answer_text, d.undo = "play", "Play: on Always use for cash builds", True
            elif name in parsed.never_kind.get("cash", set()):
                d.answer, d.answer_text, d.undo = "fade", "Fade: on Never use for cash builds", True
            elif name in parsed.always:
                d.answer, d.answer_text = "play", "Play: on Always use (every build, typed in the sheet)"
            elif name in parsed.never:
                d.answer, d.answer_text = "fade", "Fade: on Never use (every build, typed in the sheet)"
        out.append(d)
    for r in q_stars(inp)[:q_max]:
        d = Decision(
            f"q:{pid(r)}",
            "q",
            r,
            [Option("pivot", "Line up a pivot"), Option("wait", "Wait for news")],
            pivots=pivots_for(inp, r),
            say=f"add <pivot> to my pool (a {r.get('Position', '')} within ${PIVOT_RANGE})",
        )
        rec = (answers or {}).get(d.key)
        if rec and rec.get("pivot"):
            d.answer, d.undo = "pivot", True
            d.answer_text = f"Pivot lined up: {rec['pivot']} added to your pool as Both"
        out.append(d)
    stacks = top_two_stacks(inp)
    if len(stacks) == 2:
        d = Decision(
            "stack",
            "stack",
            None,
            [
                Option("a", short_name(stacks[0].get("qb", ""))),
                Option("b", short_name(stacks[1].get("qb", ""))),
                Option("both", "Both"),
            ],
            stacks=stacks,
            say="more <QB>; more <teammate> on Max exposure",
        )
        if parsed is not None:
            has = [all(n in parsed.more for n in stack_names(s)) for s in stacks]
            if all(has):
                d.answer, d.answer_text, d.undo = "both", "Both: steered on Max exposure (GPP builds)", True
            elif has[0] or has[1]:
                pick = stacks[0] if has[0] else stacks[1]
                d.answer, d.undo = ("a" if has[0] else "b"), True
                d.answer_text = f"{pick.get('qb', '')}: steered on Max exposure (GPP builds)"
        out.append(d)
    return out


# -- the progress strip ------------------------------------------------------------------------------------

DONE, NEXT, TODO = "done", "next", "todo"
STEP_NAMES = ("Pool", "Contest plan", "Builds", "Entries recorded", "Late swap", "Close")
STEP_ACTION = {  # what moves the step on: (phrase to say, command to run)
    "Pool": ("add X, Y to my pool", ""),
    "Contest plan": ("contest plan", "dfs contests plan"),
    "Builds": ("build me lineups", "dfs lineups suggest"),
    "Entries recorded": ("record my entries", "dfs entries record"),
    "Late swap": ("late swap", "dfs lineups late-swap --refresh-injuries"),
    "Close": ("close the week", "dfs week close --research"),
}


@dataclass
class Step:
    name: str
    state: str
    status: str

    @property
    def say(self) -> str:
        return STEP_ACTION[self.name][0]

    @property
    def command(self) -> str:
        return STEP_ACTION[self.name][1]


def day_label(now: datetime, at: datetime | None) -> str:
    if at is None:
        return ""
    return f"{at.astimezone():%a}" if (now - at) < timedelta(days=6) else f"{at.astimezone():%b %-d}"


def week_closed(inp: WeekInputs) -> datetime | None:
    """When `dfs week close` last succeeded after this week's last kickoff (from the run log)."""
    starts = [kickoff.parse_kickoff(r.get("GameStart")) for r in inp.edge or []]
    starts = [s for s in starts if s is not None]
    last = max(starts) if starts else None
    begin = datetime.combine(
        nfl_calendar.week_start_date(inp.week, inp.season), datetime.min.time()
    ).astimezone()
    after = last or begin
    closes = [r.time for r in inp.runs if r.ok and r.command.startswith("dfs week close") and r.time > after]
    return max(closes) if closes else None


def progress(inp: WeekInputs, pool: dict[str, str]) -> list[Step]:
    """The six steps of the week, each done / next / to do, from the real state. The next step is the first
    one not done."""
    now = inp.now
    start = datetime.combine(
        nfl_calendar.week_start_date(inp.week, inp.season), datetime.min.time()
    ).astimezone()
    done: dict[str, str] = {}
    if pool:
        done["Pool"] = f"{len(pool)} player{'s' if len(pool) != 1 else ''}"
    plan_at = parse_time((inp.contest_plan or {}).get("saved_at"))
    if plan_at is not None:
        done["Contest plan"] = f"done {day_label(now, plan_at)}"
    s = inp.suggest or {}
    s_at = parse_time(s.get("at"))
    if s.get("builds") and s_at is not None and s_at >= start:
        done["Builds"] = f"round {s.get('round', '?')}, {day_label(now, s_at)}"
    entered = inp.entered or {}
    if entered:
        n = len(entered.get("lineups") or [])
        done["Entries recorded"] = f"{n} lineup{'s' if n != 1 else ''}" if n else "recorded"
    ls_at = parse_time((inp.late_swap or {}).get("saved_at"))
    if ls_at is not None and ls_at >= start:
        done["Late swap"] = f"ran {day_label(now, ls_at)} {ls_at.astimezone():%-I:%M %p}".replace(" 0", " ")
    closed = week_closed(inp)
    if closed is not None:
        done["Close"] = f"done {day_label(now, closed)}"
    todo = {
        "Pool": "0 players",
        "Contest plan": "not yet",
        "Builds": "not yet",
        "Entries recorded": "at lock",
        "Late swap": "Sunday",
        "Close": "Monday",
    }
    steps = []
    seen_next = False
    for name in STEP_NAMES:
        if name in done:
            steps.append(Step(name, DONE, done[name]))
        elif not seen_next:
            steps.append(Step(name, NEXT, todo[name]))
            seen_next = True
        else:
            steps.append(Step(name, TODO, todo[name]))
    return steps


def next_step(steps: list[Step]) -> Step | None:
    return next((s for s in steps if s.state == NEXT), None)


# -- the brief ---------------------------------------------------------------------------------------------


def _join(items: list[str]) -> str:
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + " and " + items[-1]


def news_due(row: dict, now: datetime) -> str:
    """When news on a Questionable player is due: Friday's final injury report midweek, else inactives 90
    minutes before his kickoff."""
    start = kickoff.parse_kickoff(row.get("GameStart"))
    if start is None:
        return "news by Friday's final injury report"
    day = now.astimezone().weekday()
    inactive = (start - timedelta(minutes=90)).astimezone()
    if day in (5, 6) or start - now < timedelta(days=2):
        return f"inactives {kickoff.format_et(inactive, suffix=False)}"
    return "news by Friday's final injury report"


@dataclass
class Sentence:
    """One line of the brief: plain text with player names as `{Name}` links (`names`), rendered by
    `week_top`."""

    key: str
    text: str
    names: list[dict] = field(default_factory=list)  # the rows the {Name} placeholders refer to


def _p(row: dict) -> str:
    return "{" + str(row.get("Name", "")) + "}"


NEWS_OWN = 0.15  # the brief's team-news sentence: players owned this much in either format...
NEWS_SALARY = 7000  # ...or priced this high
NEWS_MAX = 3  # per list


def news_sentence(inp: WeekInputs) -> Sentence | None:
    """The brief's team-news line (Sam, 2026-11-09): among the chalk and the high-priced players, who catches
    from a backup QB and who leads his team's targets with a teammate out, most owned first, up to
    `NEWS_MAX` of each. Facts, not a call."""
    news = getattr(inp, "news", None) or {}
    backup, lead = [], []
    for r in inp.edge or []:
        facts = news.get(pid(r)) or []
        if avail(r) in week_plays.UNPLAYABLE or not facts:
            continue
        own = max(num(r.get("CashOwn")) or 0, num(r.get("GppOwn")) or 0)
        if own < NEWS_OWN and (num(r.get("Salary")) or 0) < NEWS_SALARY:
            continue
        qb = next((f for f in facts if f.kind == "qb" and f.tone == "down"), None)
        if qb is not None:
            swap = (
                qb.text.removeprefix("QB downgrade: ")
                .replace(" (backup)", "")
                .replace(" (", ", ")
                .rstrip(")")
            )
            backup.append((own, r, f"{_p(r)} ({swap})"))
        top = next((f for f in facts if f.kind == "lead"), None)
        if top is not None:
            mate = next((f for f in facts if f.kind == "mate_out" and f.tone == "up"), None)
            share = top.short.removeprefix("Lead target ")
            gone = f" ({mate.short.split(' out')[0]} out)" if mate is not None else ""
            lead.append((own, r, f"{_p(r)} {share}{gone}"))
    if not backup and not lead:
        return None
    parts, rows = [], []
    for label, items in (
        ("catching from a backup QB", backup),
        ("leading the targets with a teammate out", lead),
    ):
        items = sorted(items, key=lambda x: -x[0])[:NEWS_MAX]
        if items:
            parts.append(f"{label}, " + _join([t for _o, _r, t in items]))
            rows += [r for _o, r, _t in items]
    return Sentence("news", "Team news: " + "; ".join(parts) + ".", rows)


def brief(inp: WeekInputs, b: Board, panel: PoolPanel) -> list[Sentence]:
    """5-7 plain sentences from the data, each skipped when its data is missing. Saturday and Sunday lead with
    the builds and the swaps."""
    edge = inp.edge or []
    if not edge:
        return [Sentence("empty", "Nothing is synced for this week yet: sync to fill the page.")]
    day = inp.now.astimezone().weekday()
    out: dict[str, Sentence] = {}
    games = [g for g in week_plays.slate(edge) if g.total is not None]
    if len(games) >= 2:

        def gtext(g) -> str:
            fav = f", {g.favorite} implied {g.implied[g.favorite]:.1f}" if g.favorite in g.implied else ""
            title = f"{g.away} @ {g.home}" if g.away and g.home else g.key
            return f"{title} ({g.total:g}{fav})"

        out["games"] = Sentence("games", f"Games to target: {gtext(games[0])} and {gtext(games[1])}.")
    chalk = [r for r in edge if num(r.get("CashOwn")) is not None and r.get("Position") != "DST"]
    if chalk:
        top = sorted(
            (r for r in chalk if avail(r) not in week_plays.UNPLAYABLE),
            key=lambda r: -(num(r["CashOwn"]) or 0),
        )[:3]
        heavy = [r for r in top if (num(r["CashOwn"]) or 0) >= CHALK]
        items = [f"{_p(r)} ({(num(r['CashOwn']) or 0) * 100:.0f}% in double-ups)" for r in top]
        tail = (
            f" {_join([_p(r) for r in heavy])} {'is' if len(heavy) == 1 else 'are'} 30%+: play or fade "
            f"{'him' if len(heavy) == 1 else 'them'} on purpose."
            if heavy
            else " Nobody is at 30% yet."
        )
        out["chalk"] = Sentence("chalk", f"Cash core forming around {_join(items)}.{tail}", top)
    team_news = news_sentence(inp)
    if team_news is not None:
        out["news"] = team_news
    thin = thinnest(edge)
    if thin is not None:
        pos, n, row = thin
        out["value"] = Sentence(
            "value",
            f"{pos} is the thinnest position: "
            + (f"only {n} healthy {pos}{'s' if n != 1 else ''} clear" if n else f"no healthy {pos} clears")
            + f" {VALUE_LINE:g} pts per $1K. The best cheap one is {_p(row)} "
            f"at ${week_plays.salary(row):,.0f} "
            f"({num(row.get('CalPts')) or 0:.1f} pts).",
            [row],
        )
    fades = [r.row for r in b.rows() if r.fade]
    if fades:
        rec = week_plays.record_line(FADE, inp.signal_table, inp.scored_weeks).split(": ", 1)[-1]
        out["fades"] = Sentence(
            "fades", f"Fade (FADE↓, {rec}): {_join([_p(r) for r in fades[:4]])}.", fades[:4]
        )
    stars = q_stars(inp)[:4]
    if stars:
        due: dict[str, list[dict]] = {}
        for r in stars:
            due.setdefault(news_due(r, inp.now), []).append(r)
        if len(due) == 1:
            when, rows = next(iter(due.items()))
            text = f"Questionable stars, {when}: {_join([_p(r) for r in rows])}."
        else:
            text = "Questionable stars: " + _join([f"{_p(r)} ({news_due(r, inp.now)})" for r in stars]) + "."
        out["q"] = Sentence("q", text, stars)
    st = ((inp.stacks or {}).get("rows") or [])[:1]
    if st:
        s = st[0]
        top1 = num(s.get("top1"))
        odds = f", Top 1% {top1 * 100:.1f}%" if top1 is not None else ""
        out["stack"] = Sentence(
            "stack",
            f"Top GPP stack by the sim: {s.get('qb', '')} + {s.get('mates', '')} "
            f"({s.get('game', '')}{odds}).",
        )
    if panel.total:
        gaps = f"; gaps: {'; '.join(panel.gaps)}" if panel.gaps else ""
        q = f"; {len(panel.questionable)} Questionable" if panel.questionable else ""
        outs = f"; OUT in your pool: {_join([_p(r) for r in panel.out])}" if panel.out else ""
        cash, gpp = sum(panel.counts["Cash"].values()), sum(panel.counts["GPP"].values())
        out["pool"] = Sentence(
            "pool", f"Your pool: {panel.total} players ({cash} cash, {gpp} GPP){gaps}{q}{outs}.", panel.out
        )
    else:
        out["pool"] = Sentence("pool", "Your pool is empty: start from the board below.")
    builds = (inp.suggest or {}).get("builds") or []
    if day in (5, 6):
        if builds:
            out["builds"] = Sentence(
                "builds",
                f"{len(builds)} builds saved (round {(inp.suggest or {}).get('round', '?')}).",
            )
        else:
            out["builds"] = Sentence("builds", "No builds yet this week: build once the pool is set.")
    if day == 6:
        ls = inp.late_swap or {}
        at = parse_time(ls.get("saved_at"))
        start = datetime.combine(
            nfl_calendar.week_start_date(inp.week, inp.season), datetime.min.time()
        ).astimezone()
        if at is not None and at >= start:
            out["swap"] = Sentence(
                "swap", f"Late swap last ran {at.astimezone():%-I:%M %p}: run it again before the 4 pm games."
            )
        else:
            out["swap"] = Sentence("swap", "Late swap has not run today: run it after inactives.")
    order = (
        ["swap", "builds", "q", "news", "pool", "chalk", "stack", "fades", "games", "value"]
        if day == 6
        else ["builds", "pool", "chalk", "news", "q", "stack", "fades", "games", "value"]
        if day == 5
        else ["games", "chalk", "news", "value", "fades", "q", "stack", "pool"]
    )
    return [out[k] for k in order if k in out][:7]


def thinnest(edge: list[dict]) -> tuple[str, int, dict] | None:
    """(position, how many healthy players clear `VALUE_LINE` pts per $1K, the best cheap one) for the RB / WR
    / TE position with the fewest such players; None when nobody cheap qualifies."""
    rows = [r for r in week_plays.rosterable(edge) if not avail(r) and not week_plays.has_token(r, FADE)]
    best = None
    for pos in ("RB", "WR", "TE"):
        at = [r for r in rows if r.get("Position") == pos]
        if not at:
            continue
        n = sum(1 for r in at if (per_k(r) or 0) >= VALUE_LINE)
        cheap = [r for r in at if week_plays.salary(r) <= CHEAP[pos]]
        if not cheap:
            continue
        pick = max(cheap, key=lambda r: (per_k(r) or 0, num(r.get("CalPts")) or 0))
        if best is None or n < best[1]:
            best = (pos, n, pick)
    return best
