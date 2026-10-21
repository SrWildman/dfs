"""The Cockpit tab: one place for the last 90 minutes of a game day. Pure: frames in, rows out.

Read-only for the CLI's purposes: nothing on the tab is typed, it reads the other tabs and the saved files and
is rewritten whole by `dfs sync --live`, `dfs lineups late-swap` and `dfs setup cockpit`. Seven blocks, most
urgent first (the same cue vocabulary everywhere, in column A, left of the player):

    SWAP  act now: a player is out, or a better lineup exists    LOOK  check it    FILL  an entry needs a
    lineup    ok  nothing to do

1. **News in your lineups** -- Q / D / OUT / IR among the players in built lineups whose game has not started:
   new since the previous sync (`previous_edge`), laid over by ESPN in the last late-swap run, or standing.
2. **Still swappable** -- per built lineup: the open (unstarted) slots now, and the best change from the last
   `late-swap` run (`late_swap_store`), shown with its time and never as current when the lineup changed.
3. **Weather** -- the games of your open slots: wind (flagged at `derived.WIND_FLAG_THRESHOLD_MPH`), roof.
4. **Lineup warnings** -- `lineup_watch` (overuse, the GPP ownership meter) and `chalk_watch` (heavy chalk in
   none of your cash / GPP lineups): the same checks the Lineups Issues tokens and late-swap print.
5. **Exposure, top 10** -- who is in how many built lineups.
6. **DK Upload** -- entries with and without a Lineup picked.
7. **Budget** -- what the picked entries cost against the Bankroll tab's Weekly Budget and the stakes plan.

Honest limits: "inactives" here is `Avail` from the last sync plus ESPN's game-day injury feed when
`late-swap --refresh-injuries` last ran, not DraftKings' or the NFL's official list. Every block names its
source and age; a block whose input could not be read says so instead of going blank.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd

from dfs import chalk_watch as cw
from dfs import late_swap_store
from dfs import lineup_watch as lw
from dfs.derived import OUT_STATUSES, WIND_FLAG_THRESHOLD_MPH
from dfs.entries import SheetLineup
from dfs.kickoff import KICKOFF_TIMEZONE, parse_kickoff
from dfs.late_swap import lineup_slot_status

COCKPIT_TAB = "Cockpit"
COLUMN_COUNT = 8  # A (cue) .. H
CUE_SWAP, CUE_LOOK, CUE_FILL, CUE_OK = "SWAP", "LOOK", "FILL", "ok"
CUES = (CUE_SWAP, CUE_LOOK, CUE_FILL, CUE_OK)
STATUS_RANK = {"OUT": 0, "IR": 0, "D": 1, "Q": 2}  # Avail values worth a line, worst first
TOP_EXPOSURE = 10
MAX_ROWS_PER_BLOCK = 12
MAX_WEATHER_GAMES = 10
OVERUSE_LISTED = 5
_ET = ZoneInfo(KICKOFF_TIMEZONE)


# ---------------------------------------------------------------------------------------------
# Inputs
# ---------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class DkEntry:
    """One DK Upload row, reduced to what the Cockpit counts."""

    entry_id: str
    fee: float | None
    fmt: str  # the contest's series format ("GPP", "Double Up", ...)
    cash: bool  # a cash format (`contests.series.CASH_FORMATS`)
    lineup: str  # "L3" or "" (not picked)


@dataclass(frozen=True)
class PlanNumbers:
    """The stakes plan reduced to what the Cockpit shows (`stakes.Plan`)."""

    budget: float | None
    spend: float | None
    cash: float | None
    gpp: float | None
    entry_cap: float | None
    warnings: tuple[str, ...] = ()


@dataclass
class CockpitInputs:
    now: datetime  # timezone-aware
    sheet_title: str
    edge: pd.DataFrame
    lineups: list[SheetLineup]
    week: int | None = None
    previous_edge: pd.DataFrame | None = None
    saved_swap: late_swap_store.Saved | None = None
    dk_entries: list[DkEntry] | None = None
    plan: PlanNumbers | None = None
    targets: dict[str, float] = field(default_factory=dict)  # Exposure tab's typed Target, by player name
    unavailable: dict[str, str] = field(default_factory=dict)  # block key -> why its input could not be read
    adjustments: tuple[str, ...] = ()  # the active Adjust / Ownership items (`my_frame`): `Nacua +2`, ...


# ---------------------------------------------------------------------------------------------
# Layout
# ---------------------------------------------------------------------------------------------


@dataclass
class Layout:
    rows: list[list]
    title_row: int = 1
    status_row: int = 2
    section_rows: list[int] = field(default_factory=list)
    header_rows: list[int] = field(default_factory=list)
    headline_rows: list[int] = field(default_factory=list)  # bold one-liners (the next lock)
    note_rows: list[int] = field(default_factory=list)
    cue_rows: dict[int, str] = field(default_factory=dict)  # row -> cue
    percent_cells: list[str] = field(default_factory=list)
    number_cells: list[str] = field(default_factory=list)


class _Builder:
    def __init__(self) -> None:
        self.layout = Layout(rows=[])

    @property
    def next_row(self) -> int:
        return len(self.layout.rows) + 1

    def add(self, values: list) -> int:
        row = [_clean(v) for v in values]
        self.layout.rows.append(row + [""] * (COLUMN_COUNT - len(row)))
        return len(self.layout.rows)

    def blank(self) -> None:
        self.add([])

    def section(self, title: str) -> None:
        self.blank()
        self.layout.section_rows.append(self.add([title]))

    def headline(self, text: str) -> None:
        self.layout.headline_rows.append(self.add(["", text]))

    def line(self, cue: str, *cells) -> int:
        """A cue in column A and the rest from column B."""
        row = self.add([cue, *cells])
        if cue:
            self.layout.cue_rows[row] = cue
        return row

    def header(self, *labels: str) -> None:
        self.layout.header_rows.append(self.add(["", *labels]))

    def note(self, text: str) -> None:
        self.layout.note_rows.append(self.add(["", text]))


def _clean(value):
    if isinstance(value, float) and value != value:
        return ""
    if hasattr(value, "item"):
        value = value.item()
    if isinstance(value, str) and value.startswith(("=", "+", "-", "@")):
        return "'" + value
    return value


# ---------------------------------------------------------------------------------------------
# Small pure helpers
# ---------------------------------------------------------------------------------------------


def clock(when: datetime) -> str:
    """`1:00 PM` in Eastern (the slate's own clock)."""
    local = when.astimezone(_ET)
    return local.strftime("%I:%M %p").lstrip("0")


def countdown(delta_seconds: float) -> str:
    minutes = max(int(delta_seconds // 60), 0)
    hours, mins = divmod(minutes, 60)
    return f"{hours}h {mins:02d}m" if hours else f"{mins}m"


def lineup_ranges(numbers: list[int]) -> str:
    """`[1, 2, 3, 5]` -> `L1-L3 L5`."""
    ordered = sorted(set(numbers))
    parts: list[str] = []
    i = 0
    while i < len(ordered):
        j = i
        while j + 1 < len(ordered) and ordered[j + 1] == ordered[j] + 1:
            j += 1
        parts.append(f"L{ordered[i]}" if i == j else f"L{ordered[i]}-L{ordered[j]}")
        i = j + 1
    return " ".join(parts)


def money(value: float | None) -> str:
    if value is None:
        return "–"
    return f"${value:,.0f}" if float(value).is_integer() else f"${value:,.2f}"


def _signed_points(value: float | None) -> str:
    return "" if value is None else f"{value * 100:+.1f}"


def _by_name(edge: pd.DataFrame) -> pd.DataFrame:
    return edge.drop_duplicates("Name").set_index("Name")


def _text(value) -> str:
    return "" if value is None or (isinstance(value, float) and value != value) else str(value).strip()


def _kickoff(row) -> datetime | None:
    return parse_kickoff(row.get("GameStart")) if row is not None else None


def _goal(marker: str) -> str:
    return {"Cash": "cash", "GPP": "gpp"}.get(marker, "cash")  # a blank marker counts as cash


@dataclass(frozen=True)
class _Held:
    """A player in at least one built lineup."""

    name: str
    pos: str
    team: str
    avail: str
    kickoff: datetime | None
    lineups: tuple[int, ...]


def _held_players(inputs: CockpitInputs, built: list[SheetLineup]) -> list[_Held]:
    by_name = _by_name(inputs.edge)
    where: dict[str, list[int]] = {}
    for lineup in built:
        for name in lineup.names:
            if name and lineup.number not in where.setdefault(name, []):
                where[name].append(lineup.number)  # a name typed twice in one lineup is still one lineup
    out = []
    for name, numbers in where.items():
        row = by_name.loc[name] if name in by_name.index else None
        out.append(
            _Held(
                name=name,
                pos=_text(row.get("Position")) if row is not None else "",
                team=_text(row.get("Team")) if row is not None else "",
                avail=_text(row.get("Avail")) if row is not None else "",
                kickoff=_kickoff(row),
                lineups=tuple(numbers),
            )
        )
    return out


def _started(held: _Held, now: datetime) -> bool:
    return held.kickoff is not None and held.kickoff <= now


# ---------------------------------------------------------------------------------------------
# Blocks
# ---------------------------------------------------------------------------------------------


def _next_lock(b: _Builder, inputs: CockpitInputs, built: list[SheetLineup]) -> None:
    edge, now = inputs.edge, inputs.now
    if "GameStart" not in edge.columns:
        b.headline("NEXT LOCK  unknown (no kickoff times in the last sync)")
        return
    slots = Counter()
    games: dict[datetime, set[str]] = {}
    kicks = {}
    for row in edge.drop_duplicates("Name").itertuples(index=False):
        when = parse_kickoff(getattr(row, "GameStart", None))
        if when is None:
            continue
        kicks[row.Name] = when
        games.setdefault(when, set()).add(str(getattr(row, "GameID", "") or row.Team))
    for lineup in built:
        for name in lineup.names:
            if name in kicks:
                slots[kicks[name]] += 1
    future = sorted(t for t in games if t > now)
    if not future:
        b.headline("NEXT LOCK  every game has kicked off")
        return
    first = future[0]
    mine = slots.get(first, 0)
    text = (
        f"NEXT LOCK  {clock(first)} ET  in {countdown((first - now).total_seconds())}  "
        f"({len(games[first])} game{'s' if len(games[first]) != 1 else ''}; {mine} of your slots)"
    )
    if len(future) > 1:
        text += "   then " + ", ".join(clock(t) for t in future[1:7])
    b.headline(text)


def _news_block(b: _Builder, inputs: CockpitInputs, built: list[SheetLineup]) -> None:
    b.section("1. NEWS IN YOUR LINEUPS  —  Q / D / OUT among your players, games not yet started")
    if not built:
        b.line(CUE_OK, "No lineups built yet.")
        return
    now = inputs.now
    held = _held_players(inputs, built)
    previous = (
        _by_name(inputs.previous_edge)["Avail"].map(_text).to_dict()
        if inputs.previous_edge is not None and "Avail" in inputs.previous_edge.columns
        else None
    )
    espn = {n.name: n for n in (inputs.saved_swap.news if inputs.saved_swap else ())}
    items = []
    for p in held:
        if _started(p, now):
            continue
        espn_item = espn.get(p.name)
        status = p.avail
        if espn_item is not None and STATUS_RANK.get(espn_item.after, 9) < STATUS_RANK.get(status, 9):
            status = espn_item.after  # ESPN's game-day feed escalated it after the sync
        was = None
        if previous is not None and previous.get(p.name, "") != p.avail:
            was = previous.get(p.name, "")
        elif espn_item is not None and espn_item.after != espn_item.before:
            was = espn_item.before
        if status not in STATUS_RANK and was not in STATUS_RANK:
            continue  # nothing to say: clear now and clear before
        tag = "(standing)" if was is None else f"(was {was or 'clear'})"
        cue = CUE_SWAP if status in OUT_STATUSES else CUE_LOOK
        items.append((cue, STATUS_RANK.get(status, 9), p, status or "clear", tag, espn_item is not None))
    items.sort(key=lambda t: (t[0] != CUE_SWAP, t[1], t[2].kickoff or inputs.now, t[2].name))
    if items:
        b.header("Player", "Pos Team", "Status", "In", "Kickoff")
        for cue, _rank, p, status, tag, from_espn in items[:MAX_ROWS_PER_BLOCK]:
            b.line(
                cue,
                p.name,
                f"{p.pos} {p.team}".strip(),
                f"{status} {tag}" + (" · ESPN" if from_espn else ""),
                lineup_ranges(list(p.lineups)),
                clock(p.kickoff) if p.kickoff else "",
            )
        if len(items) > MAX_ROWS_PER_BLOCK:
            b.line("", f"and {len(items) - MAX_ROWS_PER_BLOCK} more")
    started = sum(1 for p in held if _started(p, now))
    quiet = f"No new or standing Q / D / OUT among the {len(held) - started} players still to play"
    if not items:
        b.line(CUE_OK, f"{quiet} in your {len(built)} lineups.")
    elif started:
        b.line(CUE_OK, f"{started} more of your players are already in started games.")
    when = ""
    if inputs.saved_swap is not None and inputs.saved_swap.refreshed_injuries:
        when = f", and ESPN's game-day feed as of the {clock(inputs.saved_swap.saved_at)} ET late-swap run"
    b.note(
        "Source: Avail from the last sync"
        + (" against the sync before it" if previous is not None else "")
        + when
    )


def _sentence(text: str) -> str:
    return text[:1].upper() + text[1:]


def _swappable_block(b: _Builder, inputs: CockpitInputs, built: list[SheetLineup]) -> None:
    b.section("2. STILL SWAPPABLE  —  open slots and the best change from the last late-swap run")
    if not built:
        b.line(CUE_OK, "No lineups built yet.")
        return
    now = inputs.now
    saved = inputs.saved_swap
    fresh = saved if saved is not None and not saved.is_stale(now) else None
    rows = []
    locked_all = []
    for lineup in built:
        names = list(lineup.names)
        statuses = lineup_slot_status(names, inputs.edge, now=now)
        open_now = [s.slot for s in statuses if s.found and s.locked is False]
        if not open_now:
            locked_all.append(lineup.number)
            continue
        goal = _goal(lineup.marker)
        result = fresh.for_lineup(lineup.number) if fresh else None
        matches = result is not None and list(result.names) == names
        if not matches:
            note = "lineup changed since the late-swap run" if result is not None else "no late-swap result"
            rows.append((CUE_LOOK, lineup, goal, open_now, f"{note}: run `dfs lineups late-swap`", "", ""))
        elif set(result.open_slots) != set(open_now):
            rows.append(
                (
                    CUE_LOOK,
                    lineup,
                    goal,
                    open_now,
                    "open slots differ from the run (a game locked): re-run late-swap",
                    "",
                    "",
                )
            )
        elif result.best is None:
            rows.append((CUE_OK, lineup, goal, open_now, "nothing beats it", "", ""))
        else:
            best = result.best
            change = f"{' + '.join(best.out) or '(empty)'} -> {' + '.join(best.into)}"
            if best.delta_p_cash is None and best.delta_p_gpp is None:
                change += f"  ({best.delta_pts:+.1f} pts)"
            rows.append(
                (
                    CUE_SWAP,
                    lineup,
                    goal,
                    open_now,
                    change,
                    _signed_points(best.delta_p_cash),
                    _signed_points(best.delta_p_gpp),
                )
            )
    if rows:
        b.header("Lineup", "Open", "Best change", "Cash % pts", "Top 1% pts")
        rows.sort(key=lambda r: (r[0] != CUE_SWAP, r[0] != CUE_LOOK, r[1].number))
        for cue, lineup, goal, open_now, change, d_cash, d_gpp in rows[:MAX_ROWS_PER_BLOCK]:
            b.line(
                cue,
                f"L{lineup.number} {goal}",
                f"{len(open_now)}: {', '.join(open_now)}",
                change,
                d_cash,
                d_gpp,
            )
    if locked_all:
        b.line(CUE_OK, lineup_ranges(locked_all), "all slots locked")
    if not rows and not locked_all:
        b.line(CUE_OK, "Nothing to swap.")
    if fresh is None:
        b.note(
            "No late-swap result saved yet today"
            if saved is None
            else "The saved late-swap run is from an earlier day"
        )
    else:
        refreshed = " (ESPN injuries read)" if fresh.refreshed_injuries else ""
        b.note(
            f"Best changes are from the late-swap run at {clock(fresh.saved_at)} ET{refreshed}; open slots "
            f"are counted now. Cash % / Top 1% are percentage-point changes. {_sentence(fresh.field_source)}."
        )


def _weather_block(b: _Builder, inputs: CockpitInputs, built: list[SheetLineup]) -> None:
    b.section("3. WEATHER  —  the games of your open slots")
    if not built:
        b.line(CUE_OK, "No lineups built yet.")
        return
    edge = inputs.edge
    if "Wind" not in edge.columns:
        b.line("", "No weather in the last sync.")
        return
    by_name = _by_name(edge)
    games: dict[str, dict] = {}
    for lineup in built:
        for name in lineup.names:
            if name not in by_name.index:
                continue
            row = by_name.loc[name]
            kick = _kickoff(row)
            if kick is not None and kick <= inputs.now:
                continue
            key = _text(row.get("GameID")) or _text(row.get("Team"))
            game = games.setdefault(key, {"slots": 0, "row": row, "kick": kick})
            game["slots"] += 1
    if not games:
        b.line(CUE_OK, "No open slots, so nothing to watch.")
        return
    table = []
    for key, game in games.items():
        row = game["row"]
        wind = pd.to_numeric(row.get("Wind"), errors="coerce")
        roof = _text(row.get("Roof"))
        parts = key.split("_")
        label = f"{parts[2]} @ {parts[3]}" if len(parts) == 4 else key
        windy = bool(wind == wind and wind >= WIND_FLAG_THRESHOLD_MPH)
        table.append(
            (windy, float(wind) if wind == wind else -1.0, label, wind, roof, _text(row.get("WX")), game)
        )
    table.sort(key=lambda t: (not t[0], -t[1], t[6]["kick"] or inputs.now))
    b.header("Game", "Wind", "Roof / WX", "Your slots", "Kickoff")
    for windy, _w, label, wind, roof, wx, game in table[:MAX_WEATHER_GAMES]:
        wind_text = f"{wind:.0f} mph" if wind == wind else ""
        b.line(
            CUE_LOOK if windy else CUE_OK,
            label,
            wind_text,
            " ".join(x for x in (roof, wx) if x),
            game["slots"],
            clock(game["kick"]) if game["kick"] else "",
        )
    b.note(
        f"Source: the last sync's weather. Wind of {WIND_FLAG_THRESHOLD_MPH:g} mph or more is flagged (WIND)."
    )


def _warnings_block(b: _Builder, inputs: CockpitInputs, built: list[SheetLineup]) -> None:
    b.section("4. LINEUP WARNINGS  —  the Lineups tab's checks, in one list")
    if not built:
        b.line(CUE_OK, "No lineups built yet.")
        return
    edge, now = inputs.edge, inputs.now
    any_line = False
    over = lw.overused([lineup.names for lineup in built])
    for name, count, total in over[:OVERUSE_LISTED]:
        b.line(CUE_LOOK, "Overused", f"{name} is in {count} of {total} lineups ({count / total:.0%})")
        any_line = True
    if len(over) > OVERUSE_LISTED:
        b.line(CUE_LOOK, "Overused", f"and {len(over) - OVERUSE_LISTED} more players in over half of them")
    for lineup in built:
        msg = lw.own_meter(lineup.own, _goal(lineup.marker))
        if msg:
            b.line(CUE_LOOK, "GPP meter", f"L{lineup.number}: {msg}")
            any_line = True
    started = set()
    if "GameStart" in edge.columns:
        for row in edge.drop_duplicates("Name").itertuples(index=False):
            when = parse_kickoff(row.GameStart)
            if when is not None and when <= now:
                started.add(row.Name)
    for goal, threshold in (("cash", cw.CASH_PROJECTED_OWN), ("gpp", cw.GPP_PROJECTED_OWN)):
        sets = [list(lineup.names) for lineup in built if _goal(lineup.marker) == goal]
        for chalk in cw.missing_chalk(edge, sets, threshold=threshold, started=started)[: cw.MAX_LISTED]:
            b.line(
                CUE_LOOK,
                "Chalk out",
                f"{chalk.name} ({chalk.position} {chalk.team}, {chalk.own:.0%} projected) is in none of your "
                f"{'GPP' if goal == 'gpp' else 'cash'} lineups",
            )
            any_line = True
    if not any_line:
        b.line(CUE_OK, "Nothing flagged.")
    b.note(
        f"Overused = in more than {lw.OVERUSE_SHARE:.0%} of {lw.MIN_LINEUPS}+ lineups. The meter judges only "
        f"GPP-marked lineups ({lw.GPP_OWN_LOW:.0%}-{lw.GPP_OWN_HIGH:.0%} summed Own%). Chalk is projected "
        f"Own% of {cw.CASH_PROJECTED_OWN:.0%}+ (cash set) or {cw.GPP_PROJECTED_OWN:.0%}+ (GPP set) "
        "in none of the set."
    )


def exposure_divisor(built: list[SheetLineup]) -> int:
    """What a share of lineups is divided by: the number of lineups holding a player. This is the count
    `Exposure!H1` carries (`sheet_views.lineups_built_formula`, a term per Lineups name block), so the
    Cockpit and the Exposure tab agree; never less than 1."""
    return max(len(built), 1)


def _exposure_block(b: _Builder, inputs: CockpitInputs, built: list[SheetLineup]) -> None:
    b.section(f"5. EXPOSURE, TOP {TOP_EXPOSURE}  —  who is in how many of your lineups")
    if not built:
        b.line(CUE_OK, "No lineups built yet.")
        return
    held = _held_players(inputs, built)
    by_name = _by_name(inputs.edge)
    held.sort(
        key=lambda p: (
            -len(p.lineups),
            -float(pd.to_numeric(_cell(by_name, p.name, "Own%"), errors="coerce") or 0),
            p.name,
        )
    )
    b.header("Player", "Pos Team", "Lineups", "Share", "Target", "Own%", "CalPts")
    first = b.next_row
    for p in held[:TOP_EXPOSURE]:
        share = len(p.lineups) / exposure_divisor(built)
        target = inputs.targets.get(p.name)
        b.line(
            "",
            p.name,
            f"{p.pos} {p.team}".strip(),
            len(p.lineups),
            share,
            target if target is not None else "",
            _num(_cell(by_name, p.name, "Own%")),
            _num(_cell(by_name, p.name, "CalPts")),
        )
    last = b.next_row - 1
    if last >= first:
        b.layout.percent_cells += [f"E{first}:G{last}"]
        b.layout.number_cells += [f"H{first}:H{last}"]
    b.note(
        f"Of {len(built)} built lineups (the count Exposure!H1 holds). Target is the Exposure tab's "
        "typed Target; Own% is the sync's projection."
    )


def _cell(by_name: pd.DataFrame, name: str, column: str):
    if name in by_name.index and column in by_name.columns:
        return by_name.loc[name, column]
    return None


def _num(value) -> float | str:
    number = pd.to_numeric(value, errors="coerce")
    return "" if number is None or number != number else float(number)


def _dk_block(b: _Builder, inputs: CockpitInputs, built: list[SheetLineup]) -> None:
    b.section("6. DK UPLOAD  —  which entries still need a Lineup")
    entries = inputs.dk_entries
    if entries is None:
        b.line("", f"DK Upload could not be read: {inputs.unavailable.get('dk', 'no reason given')}.")
        return
    if not entries:
        b.line("", "No entries on DK Upload yet (`dfs entries fill` puts them there).")
        return
    built_labels = {f"L{lineup.number}" for lineup in built}
    unpicked = [e for e in entries if not e.lineup]
    dangling = [e for e in entries if e.lineup and e.lineup not in built_labels]
    if unpicked:
        mix = Counter((e.fmt, e.fee) for e in unpicked)
        breakdown = ", ".join(
            f"{fmt} {money(fee)} x{n}" if fee is not None else f"{fmt} x{n}"
            for (fmt, fee), n in sorted(mix.items(), key=lambda kv: (-kv[1], kv[0][0]))
        )
        b.line(CUE_FILL, f"{len(unpicked)} of {len(entries)} entries have no Lineup picked", breakdown)
    if dangling:
        labels = sorted({e.lineup for e in dangling})
        b.line(CUE_LOOK, f"{len(dangling)} entries point at a lineup that is not built ({', '.join(labels)})")
    picked = len(entries) - len(unpicked)
    if not unpicked and not dangling:
        b.line(CUE_OK, f"All {len(entries)} entries have a Lineup picked.")
    else:
        b.line(CUE_OK, f"{picked} of {len(entries)} picked")
    b.note(
        "Source: the DK Upload tab's Lineup column. Nothing is picked or assigned for you; a started player "
        "is never touched by `dfs entries fill`."
    )


def _budget_block(b: _Builder, inputs: CockpitInputs) -> None:
    b.section("7. BUDGET  —  entries against the weekly budget and your stakes rules")
    plan, entries = inputs.plan, inputs.dk_entries
    if plan is None:
        b.line("", f"Budget could not be read: {inputs.unavailable.get('plan', 'no reason given')}.")
        return
    picked = [e for e in entries or [] if e.lineup]
    cash = sum(e.fee or 0 for e in picked if e.cash)
    gpp = sum(e.fee or 0 for e in picked if not e.cash)
    everything = sum(e.fee or 0 for e in entries or [])
    spend = plan.spend if plan.spend is not None else plan.budget
    b.line(
        "",
        f"Picked entries cost {money(cash + gpp)}",
        f"(cash {money(cash)}, GPP {money(gpp)})",
        f"of {money(spend)} planned" if spend is not None else "(no budget set)",
    )
    if plan.cash is not None or plan.gpp is not None:
        b.line("", f"Your rules give: cash {money(plan.cash)}, GPP {money(plan.gpp)}")
    if spend is not None and entries:
        over = everything > spend + 1e-9
        b.line(
            CUE_LOOK if over else CUE_OK,
            f"Entering all {len(entries)} would cost {money(everything)}",
            "above the plan" if over else "within the plan",
        )
    big = max((e.fee or 0 for e in entries or []), default=0)
    if plan.entry_cap is not None and big > plan.entry_cap + 1e-9:
        b.line(CUE_LOOK, f"Largest entry fee {money(big)} is above your rule ({money(plan.entry_cap)})")
    for warning in plan.warnings[:3]:
        b.line(CUE_LOOK, warning)
    b.note(
        "Source: the Bankroll tab's Weekly Budget and Starting Bankroll, [stakes] in config.toml, "
        "and DK Upload's fees."
    )


# ---------------------------------------------------------------------------------------------
# The tab
# ---------------------------------------------------------------------------------------------


def build_layout(inputs: CockpitInputs) -> Layout:
    """The whole tab as rows plus formatting instructions. Pure."""
    b = _Builder()
    built = [lineup for lineup in inputs.lineups if lineup.built]
    week = f"  Week {inputs.week}" if inputs.week else ""
    b.add([f"COCKPIT{week}  —  as of {clock(inputs.now)} ET"])
    b.add(
        [f"{inputs.sheet_title}  ·  {len(built)} lineup{'s' if len(built) != 1 else ''} built  ·  read-only"]
    )
    if "frame" in inputs.unavailable:
        b.line(
            CUE_LOOK,
            "YOUR FRAME",
            f"could not be read from Suggested ({inputs.unavailable['frame']}): raw numbers are shown",
        )
    elif inputs.adjustments:
        b.line(
            "",
            "YOUR FRAME",
            ", ".join(inputs.adjustments),
            "(typed on Suggested; every number on this tab uses them)",
        )
    b.blank()
    _next_lock(b, inputs, built)
    _news_block(b, inputs, built)
    _swappable_block(b, inputs, built)
    _weather_block(b, inputs, built)
    _warnings_block(b, inputs, built)
    _exposure_block(b, inputs, built)
    _dk_block(b, inputs, built)
    _budget_block(b, inputs)
    b.blank()
    b.note(
        "Rebuilt by `dfs sync --live`, `dfs lineups late-swap`, `dfs setup cockpit`; nothing here is typed. "
        "Cues: SWAP act now · LOOK check · FILL needs a lineup · ok nothing to do."
    )
    return b.layout
