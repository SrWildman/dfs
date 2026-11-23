"""The Lock state of the Week page (blueprint Phase C items 2 and 3; `SPECS_A_TO_D.md` section 4).

Sunday morning, one screen: what changed since the build Sam's entered lineups came from, sorted by the points
at stake, each row with the best swap from `inactives.py`'s pivots; a four-line pre-lock checklist; the late
window. Information only: nothing here blocks, warns or enforces, and nothing writes a sheet.

**The state** (`state`): `close` once every main-slate game has had `FINISHED_AFTER` to finish, or the week is
closed; `lock` from `LOCK_OPENS` before the slate's first kickoff until then; `build` otherwise (and with
no kickoffs on file). The kickoffs are EdgeRaw's `GameStart` (`kickoff.parse_kickoff`), the main slate's
only, so a Thursday game never opens the Lock state.

**The lineups** (`lineup_sources`; Sam, 2026-11-17): before lock, the Lineups tab, hand-built and suggested
alike, read fresh by the served page on every refresh (else as the last sync read it). He rebuilds often and
records entries only at lock, so the recorded entries (`data/entered/`) are used only once a game has started
and they exist. Nothing on this screen asks him to record them.

**The baseline** (`baseline`; Sam, 2026-11-17): the newest `lineups suggest` round his lineups came from (8 or
more of 9 players in one of its builds, `entered.py`'s rule); none from a build, the latest round; no build
this week, Saturday 9:00 pm ET. The spec said "the latest round": Week 9's latest was a Sunday 12:11 pm
rebuild, 14 minutes after Pickens went OUT, and it hid him.

**A live problem is never hidden** (Sam, 2026-11-17): a player in one of those lineups, the Default cash
lineup or the pool whose CURRENT status is OUT, IR, Doubtful or Questionable is a row whatever time his status
changed, until he is out of all three.

**The rows** (`rows`): `changes.csv` netted from the baseline (`net_changes`: first `before` to last `after`
per player and field; a value that moved and moved back is not a change), one row per player: a status row
when his status moved (or is a live problem), else a projection row when CalPts moved `MIN_MOVE["calpts"]`
or more, else an ownership row when cash or GPP ownership moved 5 points or more. Each row says which of his
lineups hold him (`touches`), whether he is pooled, the teammates whose numbers moved (for a status row), and
the best swap: `inactives.pivots_for` for the slot he holds in each lineup, exactly as the inactives check
calls it (salary at most his plus what the lineup leaves, game not started, clean status, CalPts for cash and
Leverage for GPP). The gain is the pivot's CalPts minus his CURRENT CalPts (0 for a player now OUT or IR). A
swap is offered only when the change went against him (a worse status, or CalPts down) and it gains
`MIN_SWAP_GAIN` or more: a GPP lineup's contrarian pick is not news (an ownership rise counts against a GPP
lineup only). A status row also lists up to three teammates who moved (`MATE_MOVE`), touches their
lineups, and when he is in none of them offers the best swap on a teammate the move went against (the
prototype's Pickens row); those teammates get no row of their own. A status change for a player under
`MIN_STATUS_PTS` (a backup) or to / from IR is counted, never a row (a live problem always is). Order: rows
that touch a lineup, then rows that touch only the pool, each by `stake` (the swap's gain, else
|d_points|), ties newest first.

Pure: rows and dicts in, dataclasses out. No file, no clock, no network.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pandas as pd

from dfs import flex_order, inactives
from dfs.kickoff import format_et, kickoff_label, parse_kickoff
from dfs.models import ROSTER_SLOTS
from dfs.player_join import normalize_name
from dfs.sim.field_gen import SALARY_CAP

STATES = ("build", "lock", "close")
LOCK_OPENS = timedelta(hours=13)  # the Lock state starts this long before the slate's first kickoff
FINISHED_AFTER = timedelta(hours=3.5)  # the last game is over this long after its kickoff (cli.py's rule)
MIN_SWAP_GAIN = 0.5  # points; below this a swap is not "better"
MIN_MOVE = {"calpts": 1.0, "cash_own": 0.05, "gpp_own": 0.05}  # a net move smaller than this is not a row
MIN_STATUS_PTS = (
    5.0  # a status change for a player projected under this (before and now) is a backup's: no row
)
# a teammate listed under a status change: moved this much (points, ownership); "against" = a drop this big
MATE_MOVE = {"calpts": 0.8, "own": 0.08, "against": 0.5}
SALARY_SLACK = 1500  # a lineup leaving this much or more unspent is worth a look
STALE_SYNC = timedelta(hours=3)  # on a Sunday, data older than this colours the News tile
INACTIVES_BEFORE = timedelta(minutes=90)  # NFL inactives come out about 90 minutes before each kickoff
LIVE = ("OUT", "IR", "D", "Q")  # a current status that keeps a row on the list (worst first)
WORSE = {"": 0, "Q": 1, "D": 2, "OUT": 3, "IR": 3}
EASTERN = ZoneInfo("America/New_York")
SATURDAY_BASELINE_HOUR = 21  # no build this week: the list starts Saturday 9:00 pm ET
FIELDS = ("status", "calpts", "cash_own", "gpp_own")  # what the change list reads from changes.csv
DEFAULT_LABEL = "Default"


def _num(value) -> float | None:  # noqa: ANN001
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return None if out != out else out


def _status(value) -> str:  # noqa: ANN001
    text = "" if value is None or (isinstance(value, float) and value != value) else str(value)
    return text.strip().upper()


def _time(value) -> datetime | None:  # noqa: ANN001
    if isinstance(value, datetime):
        return value
    if not value:
        return None
    try:
        stamp = pd.Timestamp(str(value))
    except (TypeError, ValueError):
        return None
    if pd.isna(stamp):
        return None
    return (stamp.tz_localize("UTC") if stamp.tzinfo is None else stamp).to_pydatetime()


def clock(moment: datetime | None) -> str:
    """`11:57 am` in Eastern; "" when unknown."""
    return format_et(moment, suffix=False).split(" ", 1)[1] if moment is not None else ""


def when(moment: datetime | None) -> str:
    """`Sun 11:57 am` in Eastern; "" when unknown."""
    return format_et(moment, suffix=False) if moment is not None else ""


# -- the state ---------------------------------------------------------------------------------------------


def kickoffs(edge: Iterable[dict] | None) -> list[datetime]:
    return sorted({k for r in edge or [] if (k := parse_kickoff(r.get("GameStart"))) is not None})


def state(now: datetime, kickoffs: Sequence[datetime], closed: datetime | None) -> str:
    """`build`, `lock` or `close` at `now` (see the module docstring)."""
    if closed is not None:
        return "close"
    if not kickoffs:
        return "build"
    if now >= max(kickoffs) + FINISHED_AFTER:
        return "close"
    if now >= min(kickoffs) - LOCK_OPENS:
        return "lock"
    return "build"


# -- the baseline ------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Baseline:
    at: datetime
    label: str  # "since the build your lineups came from (round 3, Sat 2:51 pm)"


MATCH_MIN = (
    8  # of 9 players shared with a saved build: that build is the lineup's source (`entered.py`'s rule)
)


def saturday_night(first_kickoff: datetime | None, fallback: datetime) -> datetime:
    """9:00 pm ET on the Saturday before the slate's Sunday (`fallback`'s week with no kickoff). On a
    secondary
    slate (`slate.py`: Thanksgiving), 9:00 pm ET the night before its own day."""
    from dfs import slate

    day = (first_kickoff or fallback).astimezone(EASTERN).date()
    target = 6 if slate.is_main() else slate.DAYS[slate.current()]
    while day.weekday() != target:  # the slate's Sunday (its own day on a secondary slate)
        day += timedelta(days=1)
    sat = day - timedelta(days=1)
    return datetime(sat.year, sat.month, sat.day, SATURDAY_BASELINE_HOUR, tzinfo=EASTERN)


def source_round(names: Sequence[str], rounds: Sequence[dict]) -> int | None:
    """The round a lineup came from: the build sharing the most of its players (8 or more), the newest round
    on a tie (`entered.match_build`'s rule); None for a lineup of his own."""
    keys = {normalize_name(n) for n in names if str(n).strip()}
    if len(keys) < MATCH_MIN:
        return None
    best: tuple[int, int] | None = None
    for r in rounds:
        for b in r.get("builds") or []:
            shared = len(keys & {normalize_name(n) for n in b})
            if shared >= MATCH_MIN and (best is None or (shared, r["round"]) >= best):
                best = (shared, int(r["round"]))
    return best[1] if best else None


def baseline(
    rounds: Sequence[dict],
    lineups: Sequence[Sequence[str]],
    first_kickoff: datetime | None,
    now: datetime,
) -> Baseline:
    """Where the change list starts (Sam, 2026-11-17): the newest round his lineups came from; none from a
    build, the latest round; no build this week, Saturday 9:00 pm ET. Rounds after `now` do not exist yet."""
    rounds = [r for r in rounds if r.get("at") is not None and r["at"] <= now]
    at = {int(r["round"]): r["at"] for r in rounds}
    found = [n for names in lineups if (n := source_round(names, rounds)) is not None]
    if found:
        n = max(found)
        return Baseline(at[n], f"since the build your lineups came from (round {n}, {when(at[n])})")
    if at:
        n = max(at)
        return Baseline(at[n], f"since your last build (round {n}, {when(at[n])})")
    return Baseline(saturday_night(first_kickoff, now), "since Saturday 9:00 pm ET (no build this week)")


# -- netting changes.csv ------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Net:
    player_id: str
    name: str
    position: str
    team: str
    field: str
    before: str
    after: str
    seen: datetime  # `at` of the last row that contributed

    @property
    def delta(self) -> float | None:
        a, b = _num(self.after), _num(self.before)
        if a is None and b is None:
            return None
        return (a or 0.0) - (b or 0.0)


def _same(field_: str, before: str, after: str) -> bool:
    if field_ == "status":
        return _status(before) == _status(after)
    a, b = _num(after), _num(before)
    if a is None or b is None:
        return a is None and b is None
    return abs(a - b) < 1e-9


def _frame(changes) -> pd.DataFrame | None:  # noqa: ANN001
    if changes is None:
        return None
    return changes if isinstance(changes, pd.DataFrame) else pd.DataFrame(list(changes))


def net_changes(changes, since: datetime, until: datetime | None = None) -> list[Net]:  # noqa: ANN001
    """Player rows after `since` (a frame or a list of `changes.csv` rows), netted per player and field: the
    first `before` to the last `after`."""
    changes = _frame(changes)
    if changes is None or changes.empty:
        return []
    rows = changes[changes["player_id"].astype(str).str.strip() != ""]
    if "field" in rows:
        rows = rows[rows["field"].isin(FIELDS)]
    out: dict[tuple[str, str], list] = {}
    for r in rows.itertuples(index=False):
        at = _time(r.at)
        if at is None or at <= since or (until is not None and at > until):
            continue
        key = (str(r.player_id), str(r.field))
        if key not in out:
            out[key] = [r, str(r.before), str(r.after), at]
        else:
            out[key][2], out[key][3] = str(r.after), max(out[key][3], at)
    nets = []
    for (pid, fld), (r, before, after, seen) in out.items():
        if _same(fld, before, after):
            continue
        nets.append(Net(pid, str(r.name), str(r.position), str(r.team), fld, before, after, seen))
    return nets


def last_status_change(changes) -> dict[str, datetime]:  # noqa: ANN001
    """When each player's status last changed this week (any time), by player id."""
    out: dict[str, datetime] = {}
    changes = _frame(changes)
    if changes is None or changes.empty:
        return out
    for r in changes[changes["field"] == "status"].itertuples(index=False):
        at = _time(r.at)
        if at is not None:
            out[str(r.player_id)] = max(out.get(str(r.player_id), at), at)
    return out


# -- the change list ---------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Mate:
    player_id: str
    name: str
    position: str
    d_points: float | None
    d_cash_own: float | None
    d_gpp_own: float | None


@dataclass(frozen=True)
class Swap:
    """The best pivot for him: `into` replaces `out` in `lineups` (the labels the same pivot fits)."""

    out: str
    out_id: str
    into: inactives.Pivot
    into_id: str
    gain: float  # the pivot's CalPts minus his current CalPts (0 for OUT / IR)
    d_salary: int
    own: float | None  # the pivot's ownership for the lineup's kind (cash or GPP)
    slot: str
    lineups: tuple[str, ...]
    ranked_by: str = "CalPts"

    def text(self) -> str:
        own = f" · own {self.own * 100:.0f}%" if self.own is not None else ""
        sal = f"{'+' if self.d_salary >= 0 else '−'}${abs(self.d_salary):,} salary"
        return f"{self.out} → {self.into.name} {self.gain:+.1f} pts · {sal}{own}"


@dataclass(frozen=True)
class Row:
    key: str  # "<player_id>:<kind>"; stable across renders, used by the buttons
    seen: datetime | None  # `at` of the last row that contributed (None: before this week's log)
    player_id: str
    name: str
    position: str
    team: str
    kind: str  # status | projection | ownership
    what: str  # "George Pickens OUT", "Devin Singletary: cash own 31% to 40%"
    d_points: float | None
    d_cash_own: float | None
    d_gpp_own: float | None
    mates: tuple[Mate, ...]
    touches: tuple[str, ...]  # labels of the lineups that hold him
    pooled: bool
    swap: Swap | None
    stake: float
    started: bool
    status: str = ""  # his status now
    sticky: bool = False  # listed because his current status is a live problem, not because it moved

    @property
    def in_lineup(self) -> bool:
        return bool(self.touches)


@dataclass
class ChangeList:
    act: list[Row] = field(default_factory=list)  # rows with a better swap
    none: list[Row] = field(default_factory=list)  # rows that touch a lineup or the pool, nothing better
    other: int = 0  # changes that touch no lineup and no pooled player

    @property
    def all(self) -> list[Row]:
        return self.act + self.none

    @property
    def top(self) -> Row | None:
        return self.act[0] if self.act else (self.none[0] if self.none else None)


STATUS_WORD = {"OUT": "OUT", "IR": "IR", "D": "Doubtful", "Q": "Questionable"}


def status_text(name: str, before: str, after: str) -> str:
    before, after = _status(before), _status(after)
    if after in ("OUT", "IR"):
        return f"{name} {after}"
    if not after:
        return f"{name} cleared (was {STATUS_WORD.get(before, before) or 'listed'})"
    if not before:
        return f"{name} {STATUS_WORD.get(after, after)}"
    return f"{name} {STATUS_WORD.get(before, before)} to {STATUS_WORD.get(after, after)}"


def _pct(x: float | None) -> str:
    return f"{x * 100:.0f}%" if x is not None else "?"


def unique_labels(sources: Sequence[inactives.Source]) -> list[inactives.Source]:
    """Two entered lineups from the same build share a label: number the repeats."""
    seen: dict[str, int] = {}
    out = []
    for s in sources:
        seen[s.label] = seen.get(s.label, 0) + 1
        out.append(
            s
            if seen[s.label] == 1
            else inactives.Source(f"{s.label} ({seen[s.label]})", s.kind, s.names, s.contests)
        )
    return out


def _by_name(edge: Sequence[dict]) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for r in edge:
        out.setdefault(normalize_name(r.get("Name")), r)
    return out


def _lineup_left(src: inactives.Source, by_name: Mapping[str, dict]) -> int | None:
    named = [n for n in src.names if str(n).strip()]
    sal = [_num((by_name.get(normalize_name(n)) or {}).get("Salary")) for n in named]
    if len(named) != len(ROSTER_SLOTS) or any(s is None for s in sal):
        return None
    return int(SALARY_CAP - sum(sal))


def _calpts(row: dict | None) -> float | None:
    if row is None:
        return None
    return _num(row.get("CalPts")) if _num(row.get("CalPts")) is not None else _num(row.get("ProjPts"))


def best_swap(
    edge: Sequence[dict],
    row: dict,
    holders: Sequence[inactives.Source],
    now: datetime,
    by_name: Mapping[str, dict],
    *,
    less_owned: bool = False,
) -> Swap | None:
    """The best pivot for the player `row` across the lineups that hold him (the inactives check's call, one
    lineup at a time); None when nothing gains `MIN_SWAP_GAIN`. `less_owned`: the move was his ownership
    rising, so only a pivot owned less than he now is answers it (a leverage problem is never answered with
    a chalkier player)."""
    me = normalize_name(row.get("Name"))
    now_pts = 0.0 if _status(row.get("Avail")) in ("OUT", "IR") else (_calpts(row) or 0.0)
    salary = _num(row.get("Salary"))
    found: dict[str, Swap] = {}
    for src in holders:
        idx = next((i for i, n in enumerate(src.names) if normalize_name(n) == me), None)
        if idx is None:
            continue
        slot = inactives.slot_of(src.names, idx, str(row.get("Position") or ""))
        left = _lineup_left(src, by_name)
        budget = (
            int(salary + left)
            if salary is not None and left is not None
            else (int(salary) if salary is not None else None)
        )
        exclude = {normalize_name(n) for n in src.names if str(n).strip()}
        pivots, ranked_by = inactives.pivots_for(edge, slot, budget, src.kind, exclude, now, n=len(edge))
        # the inactives check's ranking (CalPts for cash, Leverage for GPP), searched past its three: the
        # first that gains. For an OUT player that is the check's own first pivot; for a healthy player whose
        # numbers moved, its top three are often punts that project under him (Week 9: $3,000 WRs for Lamb)
        own_col = "CashOwn" if src.kind == inactives.CASH else "GppOwn"
        cap = _num(row.get(own_col)) if less_owned else None

        def ok(q: inactives.Pivot, cap: float | None = cap, own_col: str = own_col) -> bool:
            if (q.calpts or 0.0) - now_pts < MIN_SWAP_GAIN:
                return False
            if cap is None:
                return True
            q_own = _num((by_name.get(normalize_name(q.name)) or {}).get(own_col))
            return q_own is not None and q_own < cap

        p = next((q for q in pivots if ok(q)), None)
        if p is None:
            continue
        gain = (p.calpts or 0.0) - now_pts
        prow = by_name.get(normalize_name(p.name)) or {}
        own = _num(prow.get("CashOwn" if src.kind == inactives.CASH else "GppOwn"))
        prev = found.get(p.name)
        if prev is None:
            found[p.name] = Swap(
                str(row.get("Name") or ""),
                str(row.get("Id") or ""),
                p,
                str(prow.get("Id") or ""),
                gain,
                int(p.salary - (salary or 0)),
                own,
                slot,
                (src.label,),
                ranked_by,
            )
        else:
            found[p.name] = Swap(**{**prev.__dict__, "lineups": prev.lineups + (src.label,)})
    if not found:
        return None
    return max(found.values(), key=lambda s: (s.gain, len(s.lineups), s.into.name))


def rows(
    nets: Sequence[Net],
    edge: Sequence[dict] | None,
    sources: Sequence[inactives.Source],
    pool: Mapping[str, str],
    now: datetime,
    *,
    default_names: Sequence[str] = (),
    status_seen: Mapping[str, datetime] | None = None,
) -> ChangeList:
    """The change list (see the module docstring). `pool` is name -> Cash / GPP / Both; `default_names` the
    Default cash lineup's nine (a touch "Default" while no entries are recorded, and a live problem kept);
    `status_seen` when each player's status last changed this week, for a kept live problem."""
    edge = list(edge or [])
    by_name = _by_name(edge)
    by_id = {str(r.get("Id") or ""): r for r in edge if r.get("Id")}
    sources = unique_labels(sources)
    default_keys = {normalize_name(n) for n in default_names if n}
    pool_keys = {normalize_name(n) for n, v in pool.items() if v}
    holders_of: dict[str, list[inactives.Source]] = {}
    for s in sources:
        for n in s.names:
            if str(n).strip():
                holders_of.setdefault(normalize_name(n), []).append(s)
    use_default = not sources and bool(default_keys)

    per: dict[str, dict[str, Net]] = {}
    sticky_ids: set[str] = set()
    for n in nets:
        per.setdefault(n.player_id, {})[n.field] = n

    def touches(key: str) -> tuple[str, ...]:
        labels = tuple(s.label for s in holders_of.get(key, []))
        if not labels and use_default and key in default_keys:
            labels = (DEFAULT_LABEL,)
        return labels

    # Sam's rule: a live problem in a lineup, the Default or the pool is always listed
    for r in edge:
        key = normalize_name(r.get("Name"))
        pid = str(r.get("Id") or "")
        st = _status(r.get("Avail"))
        if not pid or st not in LIVE or "status" in per.get(pid, {}):
            continue
        if key in holders_of or key in default_keys or key in pool_keys:
            seen = (status_seen or {}).get(pid)
            per.setdefault(pid, {})["status"] = Net(
                pid,
                str(r.get("Name") or ""),
                str(r.get("Position") or ""),
                str(r.get("Team") or ""),
                "status",
                st,
                st,
                seen or now,
            )
            sticky_ids.add(pid)

    by_team: dict[str, list[Net]] = {}
    for n in nets:
        if n.field in ("calpts", "cash_own", "gpp_own"):
            by_team.setdefault(n.team, []).append(n)

    def mates_of(pid: str, team: str) -> tuple[Mate, ...]:
        moved: dict[str, dict[str, float | None]] = {}
        for n in by_team.get(team, []):
            if n.player_id != pid:
                moved.setdefault(n.player_id, {"name": n.name, "pos": n.position})[n.field] = n.delta
        ms = [
            Mate(m_id, str(v["name"]), str(v["pos"]), v.get("calpts"), v.get("cash_own"), v.get("gpp_own"))
            for m_id, v in moved.items()
            if abs(v.get("calpts") or 0) >= MATE_MOVE["calpts"]
            or abs(v.get("cash_own") or 0) >= MATE_MOVE["own"]
            or abs(v.get("gpp_own") or 0) >= MATE_MOVE["own"]
        ]
        ms.sort(key=lambda m: -(abs(m.d_points or 0) + 10 * abs(m.d_cash_own or 0)))
        return tuple(ms[:3])

    def against(d_pts, d_c, d_g, holders):  # noqa: ANN001, ANN202
        """(the lineups a move went against, whether only his ownership did): a projection drop hurts every
        lineup; an ownership rise only a GPP lineup's leverage (chalk getting chalkier is fine in cash)."""
        if d_pts is not None and d_pts <= -MATE_MOVE["against"]:
            return list(holders), False
        if max(d_c or 0.0, d_g or 0.0) >= MIN_MOVE["cash_own"]:
            return [h for h in holders if h.kind != inactives.CASH], True
        return [], False

    # status rows first: the teammates they list are part of them, not rows of their own
    order = sorted(per, key=lambda pid: "status" not in per[pid])
    absorbed: set[str] = set()
    out_rows: list[Row] = []
    other = 0
    for pid in order:
        fields = per[pid]
        if pid in absorbed and "status" not in fields:
            continue
        sticky = pid in sticky_ids
        any_net = next(iter(fields.values()))
        live = by_id.get(pid)
        name = str((live or {}).get("Name") or any_net.name)
        key = normalize_name(name)
        cal, cown, gown = fields.get("calpts"), fields.get("cash_own"), fields.get("gpp_own")
        d_pts = cal.delta if cal else None
        d_c = cown.delta if cown else None
        d_g = gown.delta if gown else None
        stat = fields.get("status")
        now_status = (
            _status((live or {}).get("Avail")) if live is not None else (_status(stat.after) if stat else "")
        )
        holders = holders_of.get(key, [])
        if stat is not None and not sticky:
            pts = max(_calpts(live) or 0.0, (_num(cal.before) or 0.0) if cal else 0.0)
            if pts < MIN_STATUS_PTS or "IR" in (_status(stat.before), _status(stat.after)):
                other += 1  # a backup's news, or an IR move: logged, never a row
                continue
        if stat is not None:
            kind = "status"
            what = (
                f"{name} {STATUS_WORD.get(now_status, now_status)}"
                if sticky
                else status_text(name, stat.before, stat.after)
            )
            worse = sticky or WORSE.get(_status(stat.after), 0) > WORSE.get(_status(stat.before), 0)
            hit, by_own = (list(holders) if worse else []), False
        elif d_pts is not None and abs(d_pts) >= MIN_MOVE["calpts"]:
            kind = "projection"
            what = f"{name}: proj {_num(cal.before) or 0:.1f} to {_num(cal.after) or 0:.1f}"
            hit, by_own = against(d_pts, d_c, d_g, holders)
        elif (d_c is not None and abs(d_c) >= MIN_MOVE["cash_own"]) or (
            d_g is not None and abs(d_g) >= MIN_MOVE["gpp_own"]
        ):
            kind = "ownership"
            if d_c is not None and abs(d_c) >= MIN_MOVE["cash_own"]:
                what = f"{name}: cash own {_pct(_num(cown.before))} to {_pct(_num(cown.after))}"
            else:
                what = f"{name}: GPP own {_pct(_num(gown.before))} to {_pct(_num(gown.after))}"
            hit, by_own = against(None, d_c, d_g, holders)
        else:
            continue
        mates = mates_of(pid, any_net.team) if kind == "status" else ()
        tch = list(touches(key))
        for m in mates:  # a teammate's lineups are touched by his news
            for lab in touches(normalize_name(m.name)):
                if lab not in tch:
                    tch.append(lab)
        pooled = key in pool_keys or any(normalize_name(m.name) in pool_keys for m in mates)
        if not tch and not pooled:
            other += 1
            continue
        absorbed.update(m.player_id for m in mates)
        started = live is not None and inactives.started(live, now)
        swap = None
        if hit and not started and live is not None:
            swap = best_swap(edge, live, hit, now, by_name, less_owned=by_own)
        if kind == "status" and swap is None and not holders:  # his news, a teammate's swap
            found = []
            for m in mates:
                mrow = by_id.get(m.player_id)
                mh = holders_of.get(normalize_name(m.name), [])
                if mrow is None or not mh or inactives.started(mrow, now):
                    continue
                mhit, m_own = against(m.d_points, m.d_cash_own, m.d_gpp_own, mh)
                if mhit and (sw := best_swap(edge, mrow, mhit, now, by_name, less_owned=m_own)) is not None:
                    found.append(sw)
            swap = max(found, key=lambda x: x.gain) if found else None
        stake = swap.gain if swap is not None else abs(d_pts or 0.0)
        seen = max((n.seen for n in fields.values()), default=None)
        if sticky and stat is not None and (status_seen or {}).get(pid) is None:
            seen = None
        out_rows.append(
            Row(
                f"{pid}:{kind}",
                seen,
                pid,
                name,
                any_net.position,
                any_net.team,
                kind,
                what,
                d_pts,
                d_c,
                d_g,
                mates,
                tuple(tch),
                pooled,
                swap,
                round(stake, 2),
                started,
                now_status,
                sticky,
            )
        )
    out_rows.sort(
        key=lambda r: (
            not r.in_lineup,
            -r.stake,
            -WORSE.get(r.status, 0),  # a tie: the player ruled out before the one cleared
            -r.seen.timestamp() if r.seen else 0.0,
            r.name,
        )
    )
    offered: set[tuple[str, str]] = set()
    for i, r in enumerate(out_rows):  # a teammate's swap shows once, on the first row that offers it
        sw = r.swap
        if sw is None or normalize_name(sw.out) == normalize_name(r.name):
            continue
        if (sw.out_id, sw.into_id) in offered:
            out_rows[i] = replace(r, swap=None, stake=round(abs(r.d_points or 0.0), 2))
        offered.add((sw.out_id, sw.into_id))
    result = ChangeList(other=other)
    for r in out_rows:
        (result.act if r.swap is not None and not r.started else result.none).append(r)
    return result


# -- the checklist -----------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Check:
    key: str
    ok: bool | None  # None: not known (nothing to check against)
    text: str
    detail: str


def _seats(src: inactives.Source, by_name: Mapping[str, dict]) -> list[flex_order.Seat] | None:
    if len(src.names) != len(ROSTER_SLOTS):
        return None
    seats = []
    for slot, n in zip(ROSTER_SLOTS, src.names, strict=True):
        r = by_name.get(normalize_name(n))
        if r is None:
            return None
        seats.append(
            flex_order.Seat(
                slot, str(r.get("Name") or n), str(r.get("Position") or ""), parse_kickoff(r.get("GameStart"))
            )
        )
    return seats


def checklist(
    edge: Sequence[dict] | None,
    sources: Sequence[inactives.Source],
    entered: dict | None,
    now: datetime,
    *,
    where: str = "",
    cash_core: dict | None = None,
) -> list[Check]:
    """The five lines: (1) no OUT, IR or Doubtful player in any lineup; (2) late-game players sit in
    FLEX-eligible slots; (3) every contest has an entry; (4) salary: over the cap, or `SALARY_SLACK` or more
    unspent; (5) no cheap same-team pair in a cash lineup (`cash_pairs`, the salary line from `cash_core`'s
    `cheap_pair`; the Default's own pair says what it earns). `where` names the lineups' source when it is
    not the entered record."""
    edge = list(edge or [])
    by_name = _by_name(edge)
    sources = unique_labels(sources)
    tail = f" ({where})" if where else ""
    out: list[Check] = []
    if not sources or not edge:
        none = "no lineups on the Lineups tab yet"
        return [
            Check("inactive", None, "No inactive players in any lineup", none),
            Check("flex", None, "Late-game players sit in FLEX-eligible slots", none),
            Check("entries", None, "Every contest has an entry", none),
            Check("salary", None, "Salary sanity", none),
            Check("pair", None, PAIR_TEXT, none),
        ]
    bad = [
        (lc.source.label, f, inactives.STATUS_WORD[f.status])
        for lc in inactives.check(edge, sources, now)
        for f in lc.flags
        if f.status in ("OUT", "IR", "D")
    ]
    out.append(
        Check(
            "inactive",
            not bad,
            "No inactive players in any lineup",
            (
                f"none of {len(sources)} lineups holds an OUT, IR or Doubtful player"
                if not bad
                else "; ".join(f"{lab}: {f.name} ({w})" for lab, f, w in bad)
            )
            + tail,
        )
    )
    moves, unknown = [], 0
    for src in sources:
        seats = _seats(src, by_name)
        if seats is None:
            unknown += 1
            continue
        plan = flex_order.plan_flex(seats, now)
        if plan.moves and plan.to_flex is not None and plan.from_flex is not None:
            early = (plan.from_flex.kickoff or now) < (plan.to_flex.kickoff or now)
            moves.append(
                f"{src.label}: move {plan.to_flex.name} to FLEX (holds {plan.from_flex.name}, "
                f"{'early' if early else 'as late'})"
            )
    out.append(
        Check(
            "flex",
            not moves,
            "Late-game players sit in FLEX-eligible slots",
            ("every lineup keeps its latest player in the FLEX" if not moves else "; ".join(moves))
            + (
                f"; {unknown} lineup{'s' if unknown != 1 else ''} not checked (a name or kickoff is missing)"
                if unknown
                else ""
            )
            + tail,
        )
    )
    if entered and entered.get("lineups"):
        from dfs import entry_plan

        contests = entry_plan.contests_from_entered(entered)
        held = sum(c.entries for c in contests)
        blank = [c for c in contests if c.blank]
        n_blank = sum(c.blank for c in blank)
        skipped = sum((entered.get("skipped") or {}).values())
        detail = f"{held} of {held + n_blank} entries hold a lineup"
        if blank:
            fees = sum(c.fee * c.blank for c in blank)
            formats = ", ".join(sorted({c.format or "unknown" for c in blank}))
            detail += f"; {n_blank} blank ({formats}, ${fees:,.0f} in fees)"
        if skipped:
            detail += f"; {skipped} other-slate entr{'ies' if skipped != 1 else 'y'} not checked"
        out.append(Check("entries", not blank, "Every contest has an entry", detail))
    else:
        out.append(
            Check(
                "entries",
                None,
                "Every contest has an entry",
                "the contests are known once DraftKings' entries file is recorded at lock",
            )
        )
    sal = []
    for src in sources:
        left = _lineup_left(src, by_name)
        if left is not None:
            sal.append((src.label, SALARY_CAP - left))
    over = [(lab, s) for lab, s in sal if s > SALARY_CAP]
    slack = [(lab, s) for lab, s in sal if SALARY_CAP - s >= SALARY_SLACK]
    if not sal:
        out.append(Check("salary", None, "Salary sanity", "no lineup's salaries are known" + tail))
    else:
        detail = (
            f"all {len(sal)} lineups between ${min(s for _, s in sal):,} and ${max(s for _, s in sal):,}"
            if not over and not slack
            else "; ".join(
                [f"{lab}: ${s - SALARY_CAP:,} over the cap" for lab, s in over]
                + [f"{lab}: ${SALARY_CAP - s:,} unspent" for lab, s in slack]
            )
        )
        out.append(Check("salary", not over and not slack, "Salary sanity", detail + tail))
    out.append(pair_check(sources, by_name, cash_core, tail))
    return out


PAIR_TEXT = "No cheap same-team pair in a cash lineup"


def pair_check(
    sources: Sequence[inactives.Source], by_name: Mapping[str, dict], cash_core: dict | None, tail: str = ""
) -> Check:
    """The cheap-pair flag (`cash_pairs`, Sam's Week 11 note) on every cash lineup: two RB / WR / TE from one
    team both under the line. Information, like every check: the lineup stays his."""
    from dfs import cash_pairs

    cp = (cash_core or {}).get("cheap_pair") or {}
    line = int(cp.get("cheap_salary") or cash_pairs.CHEAP_SALARY)
    kept = cp.get("pair") or {}
    default = set(((cash_core or {}).get("default") or {}).get("names") or [])
    cash = [s for s in sources if s.kind == "cash"]
    if not cash:
        return Check("pair", None, PAIR_TEXT, "no cash lineup" + tail)
    flags = []
    for src in cash:
        names = [str(n) for n in src.names if str(n).strip()]
        rows = {n: r for n in names if (r := by_name.get(normalize_name(n))) is not None}
        for found in cash_pairs.pairs(cash_pairs.from_rows(names, rows), line):
            text = cash_pairs.CheapPair(found, True, cash_pairs.EARNS, None).what
            if (
                kept.get("kept")
                and set(names) == default
                and {p.name for p in found} == set(kept.get("names") or [])
            ):
                text = kept.get("text") or text  # the Default's own: what it earns over the best without
            flags.append(f"{src.label}: {text}")
    n = len(cash)
    return Check(
        "pair",
        not flags,
        PAIR_TEXT,
        ("; ".join(flags) if flags else f"none of {n} cash lineup{'s' if n != 1 else ''} holds one") + tail,
    )


# -- the late window ---------------------------------------------------------------------------------------


@dataclass(frozen=True)
class LateItem:
    name: str
    slot: str
    position: str
    kickoff: datetime | None
    kickoff_text: str
    salary: int | None
    calpts: float | None
    alt: inactives.Pivot | None  # the best pivot when it gains MIN_SWAP_GAIN or more
    gain: float | None


@dataclass(frozen=True)
class LateLineup:
    label: str
    kind: str
    contests: int
    items: tuple[LateItem, ...]


def late_window(
    edge: Sequence[dict] | None, sources: Sequence[inactives.Source], now: datetime
) -> list[LateLineup]:
    """Per lineup, its players in a game after the slate's first kickoff that has not started, each with the
    best pivot that still fits (the inactives check's call, CalPts for cash, Leverage for GPP)."""
    edge = list(edge or [])
    starts = kickoffs(edge)
    if not starts:
        return []
    first = starts[0]
    by_name = _by_name(edge)
    out = []
    for src in unique_labels(sources):
        left = _lineup_left(src, by_name)
        exclude = {normalize_name(n) for n in src.names if str(n).strip()}
        items = []
        for i, n in enumerate(src.names):
            r = by_name.get(normalize_name(n))
            if r is None:
                continue
            k = parse_kickoff(r.get("GameStart"))
            if k is None or k <= first or k <= now:
                continue
            pos = str(r.get("Position") or "")
            slot = inactives.slot_of(src.names, i, pos)
            sal = _num(r.get("Salary"))
            budget = int(sal + left) if sal is not None and left is not None else None
            pivots, _ = inactives.pivots_for(edge, slot, budget, src.kind, exclude, now, n=1)
            mine = 0.0 if _status(r.get("Avail")) in ("OUT", "IR") else (_calpts(r) or 0.0)
            alt, gain = None, None
            if pivots and (pivots[0].calpts or 0.0) - mine >= MIN_SWAP_GAIN:
                alt, gain = pivots[0], (pivots[0].calpts or 0.0) - mine
            items.append(
                LateItem(
                    str(r.get("Name") or n),
                    slot,
                    pos,
                    k,
                    kickoff_label(r.get("GameStart")),
                    None if sal is None else int(sal),
                    _calpts(r),
                    alt,
                    gain,
                )
            )
        if items:
            out.append(LateLineup(src.label, src.kind, len(src.contests), tuple(items)))
    return out


# -- the strip ---------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Tile:
    key: str
    label: str
    value: str
    note: str
    tone: str = ""  # "" grey, "warn" amber, "ok" green


def _span(delta: timedelta) -> str:
    mins = max(0, int(delta.total_seconds() // 60))
    if mins < 60:
        return f"{mins} min"
    h, m = divmod(mins, 60)
    if h >= 48:
        return f"{h // 24} days"
    return f"{h}h {m:02d}m"


def time_tile(st: str, now: datetime, starts: Sequence[datetime]) -> Tile:
    if st == "close":
        last = max(starts) if starts else None
        return Tile(
            "time", "Time to lock", "Closed", f"last game {when(last)}" if last else "the week is closed"
        )
    if not starts:
        return Tile("time", "Time to lock", "not known", "no kickoffs synced yet")
    first = starts[0]
    late = sorted({clock(k) for k in starts if k > first})
    late_note = f" · late {' / '.join(late)}" if late else ""
    if now < first:
        return Tile("time", "Time to lock", _span(first - now), f"first lock {when(first)} ET{late_note}")
    nxt = next((k for k in starts if k > now), None)
    if nxt is None:
        return Tile("time", "Time to lock", "Locked", "every game has started")
    return Tile("time", "Time to lock", f"{_span(nxt - now)}", f"next lock {when(nxt)} ET (late window open)")


def entered_tile(ledger: dict | None) -> Tile:
    groups = (ledger or {}).get("groups") or {}
    if not groups:
        return Tile("entered", "Entered vs planned", "not run yet", "dfs contests plan saves it")
    planned = sum(float(g.get("planned") or 0) for g in groups.values())
    if not (ledger or {}).get("recorded"):
        return Tile(
            "entered",
            "Entered vs planned",
            f"plan ${planned:,.0f}",
            "nothing entered on file yet",
        )
    entered = sum(float(g.get("entered") or 0) for g in groups.values())
    return Tile(
        "entered",
        "Entered vs planned",
        f"${entered:,.0f} of ${planned:,.0f}",
        "entered this week against the plan",
    )


def open_tile(st: str, changes: ChangeList | None, answered: set[str], cash_core: dict | None) -> Tile:
    if st == "lock":
        n = sum(1 for r in (changes.act if changes else []) if r.key not in answered)
        return Tile("open", "Open decisions", str(n), "changes with a better swap to take or leave")
    if st == "close":
        return Tile("open", "Open decisions", "0", "nothing pending on a closed week")
    core = cash_core or {}
    spots = sum(1 for s in core.get("open") or [] if not s.get("picked"))
    faded = sum(1 for c in core.get("core") or [] if c.get("faded"))
    if not core:
        return Tile("open", "Open decisions", "-", "no cash build yet")
    return Tile(
        "open",
        "Open decisions",
        str(spots + faded),
        f"{spots} open spot{'s' if spots != 1 else ''} to pick, {faded} core fade{'s' if faded != 1 else ''}",
    )


def news_tile(
    edge: Sequence[dict] | None, sources: Sequence[inactives.Source], now: datetime, synced: datetime | None
) -> Tile:
    edge = list(edge or [])
    by_name = _by_name(edge)
    tags = {
        normalize_name(n)
        for s in sources
        for n in s.names
        if (r := by_name.get(normalize_name(n))) is not None
        and _status(r.get("Avail")) in ("Q", "D")
        and not inactives.started(r, now)
    }
    nxt = next((k - INACTIVES_BEFORE for k in kickoffs(edge) if k - INACTIVES_BEFORE > now), None)
    note = []
    if nxt is not None:
        note.append(f"next: inactives about {clock(nxt)} (90 min before {clock(nxt + INACTIVES_BEFORE)})")
    if synced is not None:
        note.append(f"last sync {when(synced)}")
    old = synced is not None and now.astimezone(EASTERN).weekday() == 6 and now - synced > STALE_SYNC
    if old:
        note.append("over three hours old")
    return Tile(
        "news",
        "News pending",
        f"{len(tags)} Q/D",
        " · ".join(note) or "no sync on file",
        "warn" if old else "",
    )


def health_tile(checks: Sequence[Check]) -> Tile:
    bad = next((c for c in checks if c.ok is False), None)
    if bad is not None:
        return Tile("health", "Lineup health", bad.detail.split("; ")[0], "see the checklist", "warn")
    if checks and all(c.ok is None for c in checks):
        return Tile("health", "Lineup health", "-", "no lineups to check yet")
    return Tile("health", "Lineup health", "OK", "every check passes", "ok")


# -- everything the page needs, from the week's inputs ----------------------------------------------------


@dataclass
class LockView:
    state: str
    base: Baseline
    changes: ChangeList
    checks: list[Check]
    late: list[LateLineup]
    tiles: list[Tile]
    where: str  # where the lineups came from ("" when there are none)
    answered: dict[str, dict]  # Lock-state row key -> the latest answer (decisions log)
    synced: datetime | None
    has_log: bool  # this week has rows in the change log
    sources: tuple = ()  # the lineups checked (`inactives.Source`), as `lineup_sources` chose them


def last_sync(manifest: Mapping[str, dict]) -> datetime | None:
    return max(
        (t for v in manifest.values() if (t := _time((v or {}).get("synced_at"))) is not None), default=None
    )


def answers(decisions: Sequence[dict] | None) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for d in decisions or []:
        if d.get("state") in ("lock", "late") and d.get("row"):
            out[str(d["row"])] = d
    return out


def lineup_sources(inp, live_lineups: list[dict] | None, started: bool):  # noqa: ANN001 - WeekInputs
    """(the lineups to check, where they came from, the entered record or None) (Sam, 2026-11-17): once a
    game has started, the entries he recorded (when they exist by now); otherwise the Lineups tab, hand-built
    and suggested alike: read fresh by the served page, else as the last sync read it. Nothing here asks him
    to record entries."""
    from dfs import week_inputs

    entered = inp.entered if inp.entered and inp.entered.get("lineups") else None
    recorded = _time((entered or {}).get("recorded_at"))
    if entered and started and (recorded is None or recorded <= inp.now):
        return inactives.sources_from_entered(entered), "", entered
    if live_lineups is not None:
        return inactives.sources_from_sheet(live_lineups), "the Lineups tab, read now", None
    saved = (inp.sheet_lineups or {}).get("lineups") or []
    if saved:
        at = week_inputs.parse_time((inp.sheet_lineups or {}).get("saved_at"))
        when_ = f", {when(at)}" if at is not None else ""
        return inactives.sources_from_sheet(saved), f"the Lineups tab as the last sync read it{when_}", None
    return [], "", None


def week_over(season: int, week: int, now: datetime) -> datetime | None:
    """The start of the next NFL week when `now` is past it, else None: a past week (`dfs week page --week N`
    reads the current slate, whose kickoffs are another week's) is a closed week."""
    from dfs import nfl_calendar

    try:
        nxt = datetime.combine(
            nfl_calendar.week_start_date(week + 1, season), datetime.min.time(), tzinfo=EASTERN
        )
    except (ValueError, KeyError):
        return None
    return nxt if now >= nxt else None


def view(inp, live_lineups: list[dict] | None = None) -> LockView:  # noqa: ANN001 - WeekInputs
    """The Lock state's whole picture from the week's inputs (pure: it only reads `inp`, and `live_lineups`,
    the Lineups tab the served page just read)."""
    from dfs import week_board

    starts = kickoffs(inp.edge)
    st = state(inp.now, starts, week_board.week_closed(inp) or week_over(inp.season, inp.week, inp.now))
    started = bool(starts) and inp.now >= starts[0]
    sources, where, entered = lineup_sources(inp, live_lineups, started)
    base = baseline(inp.suggest_rounds, [s.names for s in sources], starts[0] if starts else None, inp.now)
    default = ((inp.cash_core or {}).get("default") or {}).get("names") or []
    changes = rows(
        net_changes(inp.changes, base.at, inp.now),
        inp.edge,
        sources,
        week_board.pool_by_name(inp, None),
        inp.now,
        default_names=default,
        status_seen=last_status_change(inp.changes),
    )
    checks = checklist(inp.edge, sources, entered, inp.now, where=where, cash_core=inp.cash_core)
    late = late_window(inp.edge, sources, inp.now)
    synced = last_sync(inp.manifest)
    answered = answers(inp.decisions_log)
    tiles = [
        time_tile(st, inp.now, starts),
        entered_tile(inp.ledger),
        open_tile(st, changes, set(answered), inp.cash_core),
        news_tile(inp.edge, sources, inp.now, synced),
        health_tile(checks),
    ]
    return LockView(
        st, base, changes, checks, late, tiles, where, answered, synced, bool(inp.changes), tuple(sources)
    )


# -- an answer to a row (Swap / Keep / Later) -----------------------------------------------------------

ACTIONS = ("swap", "keep", "later")


def find(lv: LockView, key: str) -> Row | None:
    return next((r for r in lv.changes.all if r.key == key), None)


def record(
    row: Row, action: str, lineups: Sequence[str], *, season: int, week: int, now: datetime, late: bool
) -> dict:
    """The decisions-log record for an answer (`decisions_log.append`): the swap's pivot is the alternative,
    the lineup's current player the default at the time; `state` is `late` once the first game has started
    (`late`)."""
    if action not in ACTIONS:
        raise ValueError(f"unknown action {action!r}")
    sw = row.swap
    me = {"id": row.player_id, "name": row.name}
    alt = (
        [
            {
                "id": sw.into_id,
                "name": sw.into.name,
                "d_points": round(sw.gain, 2),
                "d_salary": sw.d_salary,
                "own": sw.own,
            }
        ]
        if sw is not None
        else []
    )
    if sw is not None:  # the lineup's player the swap takes out: him, or the teammate his news moved
        me = {"id": sw.out_id, "name": sw.out}
    chosen = {"id": sw.into_id, "name": sw.into.name} if action == "swap" and sw is not None else me
    labels = [str(x) for x in lineups] or list(sw.lineups if sw else row.touches)
    return {
        "season": season,
        "week": week,
        "at": now.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "state": "late" if late else "lock",
        "kind": action,
        "lineup": ", ".join(labels),
        "lineups": labels,
        "slot": sw.slot if sw is not None else "",
        "chosen": chosen,
        "alternatives": alt,
        "default_at_time": me,
        "row": row.key,
        "what": row.what,
        "source": "page",
    }


def message(row: Row, action: str, lineups: Sequence[str]) -> str:
    """What the page says after an answer: a Swap names the swap to make on DraftKings."""
    sw = row.swap
    if action == "swap" and sw is not None:
        where = ", ".join(lineups) or ", ".join(sw.lineups)
        return (
            f"Make this swap on DraftKings: in {where}, take out {sw.out} and put in {sw.into.name} "
            f"({sw.into.position} {sw.into.team}, ${sw.into.salary:,}, {sw.gain:+.1f} pts). "
            "Recorded; nothing was written to the sheet."
        )
    word = {"swap": "Swap", "keep": "Keep", "later": "Later"}[action]
    return f"{word} recorded for {row.name}. Nothing was written to the sheet."


__all__ = [
    "LOCK_OPENS",
    "MIN_MOVE",
    "MIN_SWAP_GAIN",
    "STATES",
    "Baseline",
    "ChangeList",
    "Check",
    "LateLineup",
    "LockView",
    "Net",
    "Row",
    "Swap",
    "Tile",
    "baseline",
    "checklist",
    "late_window",
    "net_changes",
    "rows",
    "state",
    "view",
]
