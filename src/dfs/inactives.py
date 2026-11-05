"""Sunday inactives check, with the pivots worked out in advance (Plan K item 2).

**Who is checked.** Every player in a lineup Sam entered (`data/entered/<season>-wk<NN>.json`, written by `dfs
entries record` at lock), else the lineups on the Lineups tab (what the Cockpit read), whose status in the
last sync is OUT, IR, Doubtful or Questionable and whose game has not started (a started player is locked:
there is nothing to swap). Grouped by lineup.

**The pivots** (three each, `PIVOTS`), all of these at once:

- the slot he holds: his own position, or RB / WR / TE when he is in the FLEX (a lineup's names are in
  DraftKings' slot order, `models.ROSTER_SLOTS`; a lineup without nine names in that order uses his own
  position);
- salary at most what he frees plus what the lineup leaves under the $50,000 cap;
- a game that has not started, and a clean status (no Q / D / OUT / IR), and not already in the lineup;
- ranked by `CalPts` for a cash lineup and by `Leverage` (EdgeRaw's ceiling rank minus ownership rank) for a
  GPP lineup. Before ownership publishes `Leverage` is blank, so a GPP lineup falls back to `CalPts` and says
  so.

A lineup's kind is the build it came from (`source.kind`), else its contests (a cash format makes it cash, any
GPP makes it GPP), else its Lineups-tab marker (blank counts as cash, as on the Cockpit).

Making the swap stays manual in DraftKings. Pure: rows in, dataclasses out.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import datetime

from dfs.contests import series as series_mod
from dfs.kickoff import kickoff_label, parse_kickoff
from dfs.models import ROSTER_SLOTS
from dfs.player_join import normalize_name
from dfs.sim.field_gen import FLEX_POSITIONS, SALARY_CAP

PIVOTS = 3
FLAGGED = ("OUT", "IR", "D", "Q")  # worst first
STATUS_WORD = {"OUT": "OUT", "IR": "IR", "D": "Doubtful", "Q": "Questionable"}
CASH, GPP = "cash", "gpp"


def _num(value) -> float | None:  # noqa: ANN001
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return None if out != out else out


@dataclass(frozen=True)
class Source:
    """A lineup to check: label, kind ("cash" / "gpp"), the nine names in slot order (or fewer), contests."""

    label: str
    kind: str
    names: tuple[str, ...]
    contests: tuple[str, ...] = ()


@dataclass(frozen=True)
class Pivot:
    name: str
    position: str
    team: str
    salary: int
    calpts: float | None
    leverage: float | None
    kickoff: datetime | None
    kickoff_text: str = ""  # `Sun 1:00 pm` (Eastern)


@dataclass
class Flag:
    name: str
    position: str
    team: str
    status: str  # OUT / IR / D / Q
    slot: str
    salary: int | None
    budget: int | None  # the most a pivot may cost: his salary plus what the lineup leaves
    kickoff: datetime | None
    pivots: list[Pivot] = field(default_factory=list)
    ranked_by: str = "CalPts"
    kickoff_text: str = ""


@dataclass
class LineupCheck:
    source: Source
    flags: list[Flag]
    left: int | None  # salary left under the cap (None: a salary is unknown)


def kind_from(source_kind: str, formats: Iterable[str], marker: str = "") -> str:
    if source_kind in (CASH, GPP):
        return source_kind
    fmts = [f for f in formats if f]
    if fmts:
        return GPP if any(f not in series_mod.CASH_FORMATS for f in fmts) else CASH
    return GPP if marker == "GPP" else CASH


def sources_from_entered(record: dict) -> list[Source]:
    """`data/entered/<season>-wk<NN>.json` (as read JSON) as lineups to check."""
    out = []
    for i, lu in enumerate(record.get("lineups") or [], start=1):
        src = lu.get("source") or {}
        contests = lu.get("contests") or []
        kind = kind_from(str(src.get("kind") or ""), [str(c.get("format") or "") for c in contests])
        label = lu.get("label") or (
            f"{kind.title()} build {src.get('number')}" if src else f"Entered lineup {i}"
        )
        names = tuple(str(n or "") for n in lu.get("names") or [])
        out.append(Source(label, kind, names, tuple(str(c.get("name") or "") for c in contests)))
    return out


def sources_from_sheet(lineups: Iterable[dict]) -> list[Source]:
    """The Lineups tab's built lineups (`{"label", "marker", "names"}`), blank marker as cash."""
    return [
        Source(
            str(lu.get("label") or ""),
            kind_from("", (), str(lu.get("marker") or "")),
            tuple(lu.get("names") or []),
        )
        for lu in lineups
        if any(str(n).strip() for n in lu.get("names") or [])
    ]


def slot_of(names: Sequence[str], index: int, position: str) -> str:
    if len(names) == len(ROSTER_SLOTS):
        return ROSTER_SLOTS[index]
    return position


def fits(slot: str, position: str) -> bool:
    return position in FLEX_POSITIONS if slot == "FLEX" else position == slot


def started(row: dict, now: datetime) -> bool:
    when = parse_kickoff(row.get("GameStart"))
    return when is not None and when <= now


def status(row: dict) -> str:
    value = row.get("Avail")
    if value is None or (isinstance(value, float) and value != value):  # a blank cell read into a frame
        return ""
    return str(value).strip().upper()


def pivots_for(
    edge: Sequence[dict],
    slot: str,
    budget: int | None,
    kind: str,
    exclude: set[str],
    now: datetime,
    n: int = PIVOTS,
) -> tuple[list[Pivot], str]:
    """The `n` best healthy, unstarted players who fit `slot` and cost at most `budget`, and what ranked
    them."""
    pool = []
    for r in edge:
        pos = str(r.get("Position") or "")
        salary = _num(r.get("Salary"))
        if (
            not fits(slot, pos)
            or salary is None
            or (budget is not None and salary > budget)
            or status(r)
            or started(r, now)
            or normalize_name(r.get("Name")) in exclude
        ):
            continue
        pool.append(r)
    ranked_by = "CalPts"
    if kind == GPP and any(_num(r.get("Leverage")) is not None for r in pool):
        ranked_by = "Leverage"
    elif kind == GPP:
        ranked_by = "CalPts (Leverage is not out yet)"

    def key(r: dict) -> tuple:
        cal = _num(r.get("CalPts")) or _num(r.get("ProjPts")) or 0.0
        if ranked_by == "Leverage":
            lev = _num(r.get("Leverage"))
            return (lev is None, -(lev or 0.0), -cal, str(r.get("Name")))
        return (-cal, str(r.get("Name")))

    out = [
        Pivot(
            str(r.get("Name") or ""),
            str(r.get("Position") or ""),
            str(r.get("Team") or ""),
            int(_num(r.get("Salary")) or 0),
            _num(r.get("CalPts")) or _num(r.get("ProjPts")),
            _num(r.get("Leverage")),
            parse_kickoff(r.get("GameStart")),
            kickoff_label(r.get("GameStart")),
        )
        for r in sorted(pool, key=key)[:n]
    ]
    return out, ranked_by


def check(edge: Sequence[dict], sources: Sequence[Source], now: datetime) -> list[LineupCheck]:
    """Every lineup with at least one flagged, unstarted player, worst status first inside it."""
    by_name = {}
    for r in edge:
        by_name.setdefault(normalize_name(r.get("Name")), r)
    out = []
    for src in sources:
        rows = [by_name.get(normalize_name(n)) for n in src.names]
        salaries = [_num(r.get("Salary")) if r is not None else None for r in rows]
        named = [n for n in src.names if str(n).strip()]
        left = (
            int(SALARY_CAP - sum(salaries))
            if len(named) == len(ROSTER_SLOTS) and all(s is not None for s in salaries)
            else None
        )
        mine = {normalize_name(n) for n in named}
        flags = []
        for i, (name, row) in enumerate(zip(src.names, rows, strict=True)):
            if row is None or status(row) not in FLAGGED or started(row, now):
                continue
            pos = str(row.get("Position") or "")
            slot = slot_of(src.names, i, pos)
            salary = salaries[i]
            budget = (
                int(salary + left)
                if salary is not None and left is not None
                else (int(salary) if salary is not None else None)
            )
            pivots, ranked_by = pivots_for(edge, slot, budget, src.kind, mine, now)
            flags.append(
                Flag(
                    str(row.get("Name") or name),
                    pos,
                    str(row.get("Team") or ""),
                    status(row),
                    slot,
                    None if salary is None else int(salary),
                    budget,
                    parse_kickoff(row.get("GameStart")),
                    pivots,
                    ranked_by,
                    kickoff_label(row.get("GameStart")),
                )
            )
        if flags:
            flags.sort(key=lambda f: (FLAGGED.index(f.status), f.kickoff or now, f.name))
            out.append(LineupCheck(src, flags, left))
    return out
