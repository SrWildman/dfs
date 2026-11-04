"""The Week page's Plays, Slate and What-changed views: pure selection from the synced slate.

Nothing here reads a file, a sheet or the network, and nothing computes a model number the tool does not
already compute: every input is a row of `data/current/edge.csv` (`week_inputs.load_edge`, strings as
saved) or the same table from the previous sync (`week_inputs.load_prev_edge`). The page (`week_page`)
renders what these functions return.

**Plays** answer "who are the right plays this week?", by position, five each:

- *Best projected value*: `CalPts` per $1K, `CalPts` the tiebreak.
- *Cash core*: `CalPts` and value both at least the position's median, best `Floor` first. With
  `CashOwn` published, the double-up ownership is shown and 30%+ is chalk (Plan D's chalk check).
- *GPP upside*: best `Boom%` (`Ceiling` the tiebreak). With `GppOwn` published, the list is the sheet's
  GPP leverage view (`edge_finder_tab.with_leverage`: Boom% rank minus ownership rank within the
  position, among the top half of the position's Boom%) so low-owned upside comes first.
- *Fades*: the `FADE↓` chip (tight ends scoring above their usage; Model Check scored it working). The
  other chips (`Proj ▼`, `USAGE↓`) are shown apart as shadow signals.

The pool is the same rosterable, available pool the Edge Finder tab uses: top `VAL_ADJ_ROSTERABLE_TOP_N`
by projection per position, not OUT / IR / Doubtful. Before ownership publishes the ownership columns
say so rather than showing a blank.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from dfs import kickoff
from dfs.derived import VAL_ADJ_ROSTERABLE_TOP_N

POSITIONS = ("QB", "RB", "WR", "TE", "DST")
PER_POSITION = 5
UNPLAYABLE = {"OUT", "IR", "D"}  # DraftKings' status: out, injured reserve, doubtful
CHALK = 0.30  # 30%+ of the double-up field: "play or fade on purpose"
WIND_MPH = 15.0  # sources/weather.py's WIND_FLAG_THRESHOLD_MPH
TOTAL_MOVED = 2.0  # a game total that moved this far since the opening snapshot is highlighted
LINE_MOVED = 1.0  # a total or spread that moved this far since the last sync is listed
CALPTS_MOVED = 2.0  # a CalPts move this large since the last sync is listed
MATTERS = 5.0  # ...for a player whose CalPts is at least this on either side (or who is yours): no backups
OWN_NOT_OUT = "ownership publishes ~Fri/Sat"
FADE = "FADE↓"
SHADOW_TOKENS = ("Proj ▼", "USAGE↓")  # shown apart, as shadow signals
STARTERS = {
    "QB": 1,
    "RB": 2,
    "WR": 3,
    "TE": 1,
}  # likely starters, by salary rank among a team's healthy players
STATUS_ORDER = {"OUT": 0, "D": 1, "Q": 2}
STATUS_WORD = {"OUT": "OUT", "D": "Doubtful", "Q": "Questionable", "IR": "IR"}


def num(value: object) -> float | None:
    try:
        out = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return None if out != out else out


def avail(row: dict) -> str:
    return str(row.get("Avail") or "").strip().upper()


def salary(row: dict) -> float:
    return num(row.get("Salary")) or 0.0


def has_token(row: dict, token: str) -> bool:
    return token in str(row.get("Edge") or "")


def median(values: list[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    mid = len(ordered) // 2
    return ordered[mid] if len(ordered) % 2 else (ordered[mid - 1] + ordered[mid]) / 2


def pct_rank(value: float, values: list[float]) -> float:
    """The percentile rank (0-100) of `value` among `values`, ties averaged (pandas' `rank(pct=True)`)."""
    below = sum(1 for v in values if v < value)
    equal = sum(1 for v in values if v == value)
    return (below + (equal + 1) / 2) / len(values) * 100


# -- who is in the pool of plays --------------------------------------------------------------------------


def rosterable(rows: list[dict]) -> list[dict]:
    """Healthy, priced players with a projection: the top `VAL_ADJ_ROSTERABLE_TOP_N` per position by `ProjPts`
    (the reference population the sheet's percentiles use), minus OUT / IR / Doubtful."""
    out: list[dict] = []
    for pos in POSITIONS:
        at = [
            r
            for r in rows
            if r.get("Position") == pos
            and salary(r) > 0
            and (num(r.get("ProjPts")) or num(r.get("CalPts"))) is not None
            and num(r.get("CalPts")) is not None
        ]
        at.sort(key=lambda r: -(num(r.get("ProjPts")) or num(r.get("CalPts")) or 0.0))
        out += [r for r in at[: VAL_ADJ_ROSTERABLE_TOP_N[pos]] if avail(r) not in UNPLAYABLE]
    return out


def value_per_k(row: dict) -> float | None:
    cal, sal = num(row.get("CalPts")), salary(row)
    return cal / (sal / 1000) if cal is not None and sal > 0 else None


def own_published(rows: list[dict], column: str) -> bool:
    return any(num(r.get(column)) is not None for r in rows)


# -- one play ---------------------------------------------------------------------------------------------


@dataclass
class Play:
    name: str
    pos: str
    team: str
    opp: str
    salary: float
    calpts: float | None
    projpts: float | None
    value: float | None
    floor: float | None
    ceiling: float | None
    boom: float | None
    hit3x: float | None
    bust: float | None
    avail: str
    kickoff: str
    game: str
    why: str
    cash_own: float | None = None
    gpp_own: float | None = None
    lev: float | None = None
    chalk: bool = False
    chips: list[str] = field(default_factory=list)  # signal chips on his row: "FADE↓", "USAGE↑", ...
    in_pool: str = ""  # the pool state when he is already in Sam's pool


def game_text(row: dict) -> str:
    team, opp = row.get("Team", ""), row.get("Opp", "")
    total, spread = num(row.get("OverUnder")), num(row.get("Spread"))
    if not opp:
        return team
    text = f"{team} vs {opp}"
    if spread is not None:
        text = f"{team} {'pick' if spread == 0 else f'{spread:+g}'} vs {opp}"
    return f"{text}, total {total:g}" if total is not None else text


def kickoff_text(row: dict) -> str:
    return kickoff.kickoff_label(row.get("GameStart")) or str(row.get("Kickoff") or "")


def why_line(row: dict, kind: str) -> str:
    """The reason in football terms: his usage, matchup, game and injury, from the facts on his row. A cash or
    GPP line leads with the odds behind the list; a fade with what the chip means."""
    pos = row.get("Position", "")
    parts: list[str] = []
    hit3x, bust, boom = num(row.get("Hit3x%")), num(row.get("Bust%")), num(row.get("Boom%"))
    ceil_m = num(row.get("CeilM")) or num(row.get("Ceiling"))
    if kind == "cash" and hit3x is not None:
        parts.append(
            f"{hit3x:.0f}% to reach 3x salary" + (f", {bust:.0f}% to bust" if bust is not None else "")
        )
    elif kind == "gpp" and boom is not None:
        parts.append(
            f"{boom:.0f}% to reach 4x salary" + (f", ceiling {ceil_m:.1f} pts" if ceil_m is not None else "")
        )
    elif kind == "fade":
        parts.append("tight end scoring 2+ points a game above his expected points over the last three games")
    elif kind == "warn":
        parts.append(
            "the projection looks too high for his usage"
            if has_token(row, "Proj ▼")
            else "his carry share has been falling"
        )
    elif kind == "value":
        value = value_per_k(row)
        if value is not None:
            parts.append(f"{value:.1f} points per $1K")
    # usage (last five games played)
    snap, tgt, car = num(row.get("Snap% L5")), num(row.get("Tgt L5")), num(row.get("Car L5"))
    use = ""
    if pos == "RB" and car is not None:
        use = f"{car:.0f} carries" + (f", {tgt:.0f} targets" if tgt else "") + " a game lately"
    elif pos in ("WR", "TE") and tgt is not None:
        use = f"{tgt:.1f} targets a game lately"
    elif pos == "QB" and num(row.get("DK L5")) is not None:
        use = f"{num(row.get('DK L5')):.1f} DK points a game lately"
    if use and snap and pos in ("RB", "WR", "TE"):
        share = snap * 100 if snap <= 1.5 else snap  # saved as a fraction
        use += f" on {share:.0f}% of snaps"
    parts.append(use)
    # matchup: TFFB's rank of the opponent against his position, 1 = easiest
    rank = num(row.get("OppPosRank"))
    if rank is not None and rank <= 8:
        parts.append(f"soft matchup (defense ranks {rank:.0f} of 32 against {pos}s)")
    elif rank is not None and rank >= 25:
        parts.append(f"tough matchup ({rank:.0f} of 32)")
    total = num(row.get("OverUnder"))
    if total is not None and total >= 48:
        parts.append(f"high-scoring game (total {total:g})")
    wind = num(row.get("Wind"))
    if wind is not None and wind >= WIND_MPH and str(row.get("Roof") or "") != "dome":
        parts.append(f"{wind:.0f} mph wind")
    status = avail(row)
    if status in STATUS_WORD:
        parts.append(f"{STATUS_WORD[status]}: check before kickoff")
    return "; ".join(p for p in parts if p)


def chips_of(row: dict) -> list[str]:
    tokens = str(row.get("Edge") or "")
    return [t for t in ("FADE↓", "USAGE↑", "USAGE↓", "INJ+", "Proj ▼", "Proj ▲") if t in tokens]


def make_play(row: dict, kind: str, *, pool: dict[str, str], lev: float | None = None) -> Play:
    cash_own, gpp_own = num(row.get("CashOwn")), num(row.get("GppOwn"))
    return Play(
        name=row.get("Name", ""),
        pos=row.get("Position", ""),
        team=row.get("Team", ""),
        opp=row.get("Opp", ""),
        salary=salary(row),
        calpts=num(row.get("CalPts")),
        projpts=num(row.get("ProjPts")),
        value=value_per_k(row),
        floor=num(row.get("Floor")) or None,
        ceiling=num(row.get("CeilM")) or num(row.get("Ceiling")),
        boom=num(row.get("Boom%")),
        hit3x=num(row.get("Hit3x%")),
        bust=num(row.get("Bust%")),
        avail=avail(row),
        kickoff=kickoff_text(row),
        game=game_text(row),
        why=why_line(row, kind),
        cash_own=cash_own,
        gpp_own=gpp_own,
        lev=lev,
        chalk=cash_own is not None and cash_own >= CHALK,
        chips=chips_of(row),
        in_pool=pool.get(row.get("Name", ""), ""),
    )


# -- a signal's own track record (Model Check) ---------------------------------------------------------------


def record_line(token: str, table: dict[str, dict] | None, weeks: tuple[int, int] | None) -> str:
    """Model Check's record for the signal behind a chip, in one line: `FADE↓: 67% hit, -2.5 vs CalPts,
    Weeks 1-8 (n=52)`. `table` is `results/signal_table_<season>.csv` by signal group (`week_inputs.
    load_signal_table`); the group is the Model Check row the chip is scored in
    (`player_card.signal_group`)."""
    from dfs import player_card

    group = player_card.signal_group(token)
    if group is None:
        return f"{token}: no Model Check record for this signal"
    rec = (table or {}).get(group)
    if rec is None:
        return f"{token}: no Model Check record yet"
    n = int(rec.get("n") or 0)
    if n == 0:
        return f"{token}: has not fired in a scored game yet"
    bits = []
    hit = rec.get("HitCal") if rec.get("HitCal") is not None else rec.get("HitProj")
    if hit is not None:
        bits.append(f"{hit * 100:.0f}% hit")
    if rec.get("VsCal") is not None:
        bits.append(f"{rec['VsCal']:+.1f} vs CalPts")
    if weeks:
        bits.append(f"Weeks {weeks[0]}-{weeks[1]}" if weeks[0] != weeks[1] else f"Week {weeks[0]}")
    line = f"{token}: " + ", ".join(bits) + f" (n={n})"
    return line + (", thin sample" if rec.get("Thin") else "")


# -- the lists --------------------------------------------------------------------------------------------


@dataclass
class PlaysView:
    value: dict[str, list[Play]]
    cash: dict[str, list[Play]]
    gpp: dict[str, list[Play]]
    fades: list[Play]
    shadow: list[Play]
    cash_own_pending: bool  # CashOwn is not published yet
    gpp_own_pending: bool
    players: int  # how many players the lists were drawn from

    @property
    def empty(self) -> bool:
        return not self.players


def _top(rows: list[dict], key, n: int = PER_POSITION) -> list[dict]:
    return sorted(rows, key=key)[:n]


def plays(
    edge: list[dict] | None, pool: dict[str, str] | None = None, *, per: int = PER_POSITION
) -> PlaysView:
    pool = pool or {}
    rows = rosterable(edge or [])
    cash_pub, gpp_pub = own_published(rows, "CashOwn"), own_published(rows, "GppOwn")
    value: dict[str, list[Play]] = {}
    cash: dict[str, list[Play]] = {}
    gpp: dict[str, list[Play]] = {}
    for pos in POSITIONS:
        at = [r for r in rows if r.get("Position") == pos]
        if not at:
            continue
        by_value = _top(at, lambda r: (-(value_per_k(r) or 0.0), -(num(r.get("CalPts")) or 0.0)), per)
        value[pos] = [make_play(r, "value", pool=pool) for r in by_value]
        cals = [num(r.get("CalPts")) or 0.0 for r in at]
        vals = [value_per_k(r) or 0.0 for r in at]
        cal_mid, val_mid = median(cals), median(vals)
        core = [
            r
            for r in at
            if (num(r.get("CalPts")) or 0.0) >= (cal_mid or 0.0)
            and (value_per_k(r) or 0.0) >= (val_mid or 0.0)
        ]
        cash[pos] = [
            make_play(r, "cash", pool=pool)
            for r in _top(
                core, lambda r: (-(num(r.get("Floor")) or 0.0), -(num(r.get("CalPts")) or 0.0)), per
            )
        ]
        gpp[pos] = _gpp(at, pool, gpp_pub, per)
    fades = [
        make_play(r, "fade", pool=pool)
        for r in _top(
            [r for r in (edge or []) if has_token(r, FADE) and avail(r) not in UNPLAYABLE and salary(r) > 0],
            lambda r: -(num(r.get("CalPts")) or 0.0),
            per * 2,
        )
    ]
    pool_rows = {id(r) for r in rows}
    shadow = [
        make_play(r, "warn", pool=pool)
        for r in _top(
            [
                r
                for r in (edge or [])
                if id(r) in pool_rows
                and any(has_token(r, t) for t in SHADOW_TOKENS)
                and not has_token(r, FADE)
            ],
            lambda r: -(num(r.get("CalPts")) or 0.0),
            per,
        )
    ]
    return PlaysView(value, cash, gpp, fades, shadow, not cash_pub, not gpp_pub, len(rows))


def _gpp(at: list[dict], pool: dict[str, str], published: bool, per: int) -> list[Play]:
    """GPP upside at one position: best Boom%; once GppOwn is out, the top half of Boom% by leverage."""
    booms = [(r, num(r.get("Boom%"))) for r in at]
    booms = [(r, b) for r, b in booms if b is not None]
    if not published:
        top = _top(
            [r for r, _ in booms],
            lambda r: (-(num(r.get("Boom%")) or 0.0), -(num(r.get("Ceiling")) or 0.0)),
            per,
        )
        return [make_play(r, "gpp", pool=pool) for r in top]
    owned = [(r, num(r.get("GppOwn"))) for r, _ in booms]
    owned = [(r, o) for r, o in owned if o is not None]
    if len(owned) < 2:
        return []
    boom_values = [b for _, b in booms]
    own_values = [o for _, o in owned]
    boom_mid = median(boom_values) or 0.0
    levs: list[tuple[dict, float]] = []
    for r, own in owned:
        boom = num(r.get("Boom%")) or 0.0
        if boom >= boom_mid:
            levs.append((r, round(pct_rank(boom, boom_values) - pct_rank(own, own_values))))
    levs.sort(key=lambda x: (-x[1], -(num(x[0].get("Boom%")) or 0.0)))
    return [make_play(r, "gpp", pool=pool, lev=lev) for r, lev in levs[:per]]


# -- the slate --------------------------------------------------------------------------------------------


@dataclass
class Game:
    key: str
    away: str
    home: str
    total: float | None
    tot_move: float | None  # since the opening snapshot (`TotMove`)
    favorite: str
    spread: float | None  # the favorite's, negative
    spread_move: float | None  # the favorite's, since the opening snapshot
    implied: dict[str, float]  # team -> implied total
    start: str  # GameStart, sorted on
    kickoff: str
    roof: str
    wind: float | None
    wx: str
    injuries: list[tuple[str, str, str, str]] = field(default_factory=list)  # name, pos, team, status
    top: bool = False  # one of the three highest totals
    moved: bool = False  # the total moved TOTAL_MOVED or more since the opening snapshot

    @property
    def weather(self) -> str:
        if self.roof == "dome":
            return "dome"
        bits = []
        if self.wind is not None:
            bits.append(f"wind {self.wind:.0f} mph" + (" (windy)" if self.wind >= WIND_MPH else ""))
        if self.wx:
            bits.append(self.wx)
        if self.roof:
            bits.insert(0, self.roof)
        return ", ".join(bits)


def _teams(rows: list[dict]) -> tuple[str, str]:
    """(away, home): `GameID` is `season_week_AWAY_HOME`; without it the first team seen is the away side."""
    m = re.search(r"_([A-Za-z]+)_([A-Za-z]+)$", str(rows[0].get("GameID") or ""))
    if m:
        return m.group(1), m.group(2)
    teams = [t for r in rows for t in (r.get("Team"), r.get("Opp")) if t]
    uniq = list(dict.fromkeys(teams))
    return (uniq + ["", ""])[0], (uniq + ["", ""])[1]


def key_injuries(team_rows: list[dict]) -> list[tuple[str, str, str, str]]:
    """OUT / Doubtful / Questionable among a team's likely starters: the top `STARTERS[pos]` by salary among
    its players not on IR. (A player on IR for weeks is not news.)"""
    out = []
    for pos, n in STARTERS.items():
        at = sorted(
            (r for r in team_rows if r.get("Position") == pos and avail(r) != "IR" and salary(r) > 0),
            key=lambda r: -salary(r),
        )[:n]
        out += [(r["Name"], pos, r.get("Team", ""), avail(r)) for r in at if avail(r) in STATUS_ORDER]
    return sorted(out, key=lambda x: (STATUS_ORDER[x[3]], x[0]))


def slate(edge: list[dict] | None) -> list[Game]:
    """Every game in the synced slate, highest total first, the top three environments and the games whose
    total moved `TOTAL_MOVED`+ marked."""
    groups: dict[str, list[dict]] = {}
    for r in edge or []:
        key = str(r.get("GameID") or "") or "|".join(sorted(t for t in (r.get("Team"), r.get("Opp")) if t))
        if key:
            groups.setdefault(key, []).append(r)
    games: list[Game] = []
    for key, rows in groups.items():
        away, home = _teams(rows)
        first = rows[0]
        spreads: dict[str, float] = {}
        moves: dict[str, float] = {}
        for r in rows:
            team = r.get("Team", "")
            if team and team not in spreads and num(r.get("Spread")) is not None:
                spreads[team] = num(r["Spread"])  # type: ignore[assignment]
            if team and team not in moves and num(r.get("SpdMove")) is not None:
                moves[team] = num(r["SpdMove"])  # type: ignore[assignment]
        total = next((num(r.get("OverUnder")) for r in rows if num(r.get("OverUnder")) is not None), None)
        favorite = min(spreads, key=lambda t: spreads[t]) if spreads else ""
        implied = {t: (total - s) / 2 for t, s in spreads.items()} if total is not None else {}
        tot_move = next((num(r.get("TotMove")) for r in rows if num(r.get("TotMove")) is not None), None)
        game = Game(
            key=key,
            away=away,
            home=home,
            total=total,
            tot_move=tot_move,
            favorite=favorite,
            spread=spreads.get(favorite),
            spread_move=moves.get(favorite),
            implied=implied,
            start=str(first.get("GameStart") or ""),
            kickoff=kickoff_text(first),
            roof=str(first.get("Roof") or ""),
            wind=num(first.get("Wind")),
            wx=str(first.get("WX") or ""),
            injuries=key_injuries(rows),
        )
        game.moved = tot_move is not None and abs(tot_move) >= TOTAL_MOVED
        games.append(game)
    games.sort(key=lambda g: (-(g.total if g.total is not None else -1.0), g.start))
    for g in [g for g in games if g.total is not None][:3]:
        g.top = True
    return games


def opening_known(games: list[Game]) -> bool:
    """True when any game carries a movement figure, i.e. the odds diff against an opening snapshot ran."""
    return any(g.tot_move is not None or g.spread_move is not None for g in games)


# -- what changed since the last sync -----------------------------------------------------------------------


@dataclass
class Change:
    kind: str  # status | calpts | line | out
    text: str
    mine: bool = False  # the player is in Sam's pool or on a Suggested build


@dataclass
class Changes:
    status: list[Change]  # a status change for a player of his
    new_outs: list[Change]
    calpts: list[Change]
    lines: list[Change]

    @property
    def any(self) -> bool:
        return bool(self.status or self.new_outs or self.calpts or self.lines)


def _id(row: dict) -> str:
    return str(row.get("Id") or "") or f"{row.get('Name', '')}|{row.get('Team', '')}"


def changes(current: list[dict], previous: list[dict], mine: set[str]) -> Changes:
    """What moved between two syncs: a status change for anyone in `mine` (the pool and every Suggested
    build), new OUTs (anyone who matters), CalPts moves of `CALPTS_MOVED`+, total / spread moves of
    `LINE_MOVED`+."""
    before = {_id(r): r for r in previous}
    status: list[Change] = []
    new_outs: list[Change] = []
    cal: list[tuple[float, Change]] = []
    for r in current:
        old = before.get(_id(r))
        if old is None:
            continue
        name = r.get("Name", "")
        is_mine = name in mine
        was, now = avail(old), avail(r)
        label = f"{name} ({r.get('Position', '')} {r.get('Team', '')})".replace("  ", " ")
        if was != now:
            wording = f"{STATUS_WORD.get(was) or 'healthy'} to {STATUS_WORD.get(now) or 'healthy'}"
            if is_mine:
                status.append(Change("status", f"{label}: {wording}", True))
            matters = is_mine or max(num(old.get("CalPts")) or 0.0, num(r.get("CalPts")) or 0.0) >= MATTERS
            if now in ("OUT", "IR") and was not in ("OUT", "IR") and matters:
                new_outs.append(Change("out", f"{label}: {wording}", is_mine))
        a, b = num(old.get("CalPts")), num(r.get("CalPts"))
        if (
            a is not None
            and b is not None
            and abs(b - a) >= CALPTS_MOVED
            and salary(r) > 0
            and (is_mine or max(a, b) >= MATTERS)
        ):
            cal.append(
                (
                    abs(b - a),
                    Change("calpts", f"{label}: CalPts {a:.1f} to {b:.1f} ({b - a:+.1f})", is_mine),
                )
            )
    cal.sort(key=lambda x: (not x[1].mine, -x[0]))
    new_outs.sort(key=lambda c: not c.mine)
    return Changes(status, new_outs, [c for _, c in cal], _line_moves(current, previous))


def _line_moves(current: list[dict], previous: list[dict]) -> list[Change]:
    now = {g.key: g for g in slate(current)}
    was = {g.key: g for g in slate(previous)}
    out: list[tuple[float, Change]] = []
    for key, g in now.items():
        old = was.get(key)
        if old is None:
            continue
        bits = []
        size = 0.0
        if g.total is not None and old.total is not None and abs(g.total - old.total) >= LINE_MOVED:
            bits.append(f"total {old.total:g} to {g.total:g} ({g.total - old.total:+g})")
            size = max(size, abs(g.total - old.total))
        if (
            g.spread is not None
            and old.spread is not None
            and g.favorite == old.favorite
            and abs(g.spread - old.spread) >= LINE_MOVED
        ):
            bits.append(f"{g.favorite} spread {old.spread:g} to {g.spread:g}")
            size = max(size, abs(g.spread - old.spread))
        elif g.favorite and old.favorite and g.favorite != old.favorite:
            bits.append(f"favorite {old.favorite} to {g.favorite}")
            size = max(size, LINE_MOVED)
        if bits:
            out.append((size, Change("line", f"{g.away} @ {g.home}: " + ", ".join(bits))))
    out.sort(key=lambda x: -x[0])
    return [c for _, c in out]
