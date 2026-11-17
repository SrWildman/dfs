"""One Default cash lineup, its core and its open spots (blueprint Phase A; `SPECS_A_TO_D.md` section 2).

Pure: no sheet, no file, no simulator. `lineup_suggest.suggest_builds` and `lineup_iterate.iterate_builds`
hand in the cash search's lineups (the exact top `CASH_TOP_K` by projection, best first) and the cash pool.

- **Default** = the first lineup by `default_key`: the highest projection (six decimals), then the cheaper
  lineup, then the names (the same tie-breaks `lineup_suggest.cash_key` has always used). `tied` is how many
  lineups share that projection.
- **Near-optimal set** = every lineup projecting within `NEAR_POINTS` of the Default. `truncated` is true when
  the search returned a full `k` lineups and the last of them is still inside the band (more may exist).
- **Core** = every Default player in at least `CORE_SHARE` of the near-optimal set (a set of one lineup makes
  all nine core).
- **Open spots** = the Default's other players, in DK roster order, each in the slot `lineup_suggest.seat`
  gives him. A spot's **alternatives** are single swaps worked out from the pool (never read off the set, so
  each number is true of that one swap whether or not the set was truncated): a pool player not in the
  Default who fits the slot (his position; RB / WR / TE for FLEX), costs at most the Default player's salary
  plus what is left under the cap, keeps the lineup `legal` and projects no more than `NEAR_POINTS` under the
  player he replaces. The best `MAX_ALTERNATIVES` by projection, then the lower salary, then the name. A spot
  with none is still a spot ("nothing within 1.5 points at the price").

**The band.** No per-build percentage is shown anywhere. The Default's simulated chance against the measured
double-up line (`[sim.lines] calibrated_cash_offset`, `data/model_cache/du_line.json`) only picks a word
(`under` below 45%, `coin flip` to 55%, `lean` above), and the word is shown with the base rate of every cash
build scored so far (`data/contests/cash_shown.csv`): "lean: cash builds have cashed 14 of 25". The number
itself is kept for the calibration scorecard and never printed.
"""

from __future__ import annotations

import builtins
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

from dfs.late_swap_search import SwapPlayer, conflicts
from dfs.lineup_shapes import SKILL
from dfs.models import ROSTER_SLOTS

NEAR_POINTS = 1.5  # near-optimal: projection within this of the Default
CORE_SHARE = 0.70  # core: in at least this share of the near-optimal lineups
MAX_ALTERNATIVES = 2  # an open spot shows the Default's player and up to two others
VALUE_DECIMALS = 6  # projections compare at this precision (lineup_suggest.VALUE_DECIMALS)
UNDER, COIN_FLIP, LEAN = "under", "coin flip", "lean"
BAND_EDGES = (0.45, 0.55)  # shown below the first: under; up to the second: coin flip; above: lean
LATE_AFTER = timedelta(hours=2)  # a kickoff this long after the slate's first is a late game ("L")
NOTHING_AT_THE_PRICE = f"nothing within {NEAR_POINTS:g} points at the price"


def slot_labels(slots: Sequence[str] = ROSTER_SLOTS) -> tuple[str, ...]:
    """DK's slots with a number where a slot repeats: QB, RB1, RB2, WR1, WR2, WR3, TE, FLEX, DST."""
    out = []
    for i, slot in enumerate(slots):
        if slots.count(slot) == 1:
            out.append(slot)
        else:
            out.append(f"{slot}{slots[: i + 1].count(slot)}")
    return tuple(out)


SLOT_LABELS = slot_labels()


def freed(d_salary: int) -> str:
    """Salary freed by a swap, signed: "+$300 freed" (cheaper), "-$200 freed" (dearer)."""
    return f"{'+' if d_salary >= 0 else '-'}${abs(d_salary):,} freed"


def projection(lineup: Sequence[SwapPlayer]) -> float:
    return sum(p.value for p in lineup)


def salary(lineup: Sequence[SwapPlayer]) -> int:
    return sum(p.salary for p in lineup)


def default_key(lineup: Sequence[SwapPlayer]) -> tuple:
    """Best first: the higher projection (six decimals), then the cheaper lineup, then the sorted names."""
    return (-round(projection(lineup), VALUE_DECIMALS), salary(lineup), tuple(sorted(p.name for p in lineup)))


def near_optimal(
    lineups: Sequence[Sequence[SwapPlayer]], *, near_points: float = NEAR_POINTS, k: int | None = None
) -> tuple[list[list[SwapPlayer]], bool]:
    """(every lineup within `near_points` of the best, best first; whether the set may be cut short: `k`
    lineups came back and the last of them is still inside the band). Without `k` a last lineup inside the
    band counts as cut short."""
    ordered = sorted((list(lu) for lu in lineups), key=default_key)
    if not ordered:
        return [], False
    floor = round(projection(ordered[0]) - near_points, VALUE_DECIMALS)
    inside = [lu for lu in ordered if round(projection(lu), VALUE_DECIMALS) >= floor]
    full = k is None or len(ordered) >= k
    return inside, full and len(inside) == len(ordered)


def basic_legal(cap: int) -> Callable[[Sequence[SwapPlayer]], bool]:
    """The rules every lineup follows: nine different players, the cap, no DST against his own QB, one RB a
    game (`late_swap_search.conflicts`)."""

    def ok(lineup: Sequence[SwapPlayer]) -> bool:
        names = [p.name for p in lineup]
        if len(set(names)) != len(names) or salary(lineup) > cap:
            return False
        return not any(conflicts(p, [o for o in lineup if o is not p]) for p in lineup)

    return ok


def fits(slot: str, player: SwapPlayer) -> bool:
    return player.position in SKILL if slot == "FLEX" else player.position == slot


@dataclass(frozen=True)
class Alternative:
    player: SwapPlayer
    d_points: float  # his projection minus the Default player's
    d_salary: int  # salary FREED: positive means he is cheaper
    held: int  # near-optimal lineups that hold him


@dataclass(frozen=True)
class Spot:
    slot: str  # "RB2", "FLEX": SLOT_LABELS
    index: int  # in ROSTER_SLOTS
    player: SwapPlayer  # the Default's player
    held: int
    alternatives: tuple[Alternative, ...]


@dataclass(frozen=True)
class CashCore:
    default: tuple[SwapPlayer, ...]  # seated: ROSTER_SLOTS order, the FLEX rule applied
    projection: float
    salary: int
    left: int
    tied: int  # lineups sharing the top projection
    searched: int  # lineups the search returned
    near: int  # lineups in the near-optimal set
    truncated: bool
    core: tuple[tuple[SwapPlayer, int], ...]  # (player, near-optimal lineups holding him), roster order
    open: tuple[Spot, ...]
    complete: bool = True  # False: the search was cut short, the Default may not be the true top
    near_points: float = NEAR_POINTS
    core_share: float = CORE_SHARE

    @property
    def core_names(self) -> tuple[str, ...]:
        return tuple(p.name for p, _ in self.core)


def cash_core(
    lineups: Sequence[Sequence[SwapPlayer]],
    pool: Sequence[SwapPlayer],
    *,
    cap: int,
    kickoff: Mapping[str, datetime | None],
    legal: Callable[[Sequence[SwapPlayer]], bool] | None = None,
    near_points: float = NEAR_POINTS,
    core_share: float = CORE_SHARE,
    k: int | None = None,
    complete: bool = True,
    max_alternatives: int = MAX_ALTERNATIVES,
) -> CashCore | None:
    """The Default, its core and its open spots from the cash search's `lineups`; None when there is none.
    `legal` is an extra test a swapped lineup must pass (an iterate rerun's rules), on top of the basic
    rules; `k` is how many lineups the search was asked for (the truncation flag)."""
    from dfs.lineup_suggest import seat  # lineup_suggest imports this module

    near, truncated = near_optimal(lineups, near_points=near_points, k=k)
    if not near:
        return None
    top = near[0]
    top_points = round(projection(top), VALUE_DECIMALS)
    tied = sum(1 for lu in near if round(projection(lu), VALUE_DECIMALS) == top_points)
    seated = seat(top, dict(kickoff))
    held: dict[str, int] = {}
    for lu in near:
        for p in lu:
            held[p.name] = held.get(p.name, 0) + 1
    need = core_share * len(near)
    ok = basic_legal(cap)
    left = cap - salary(seated)
    in_default = {p.name for p in seated}
    core, spots = [], []
    for i, (slot, player) in enumerate(zip(ROSTER_SLOTS, seated, strict=True)):
        count = held.get(player.name, 0)
        if count >= need - 1e-9:
            core.append((player, count))
            continue
        alts = []
        for p in pool:
            if p.name in in_default or not fits(slot, p):
                continue
            if p.salary > player.salary + left or p.value < player.value - near_points - 1e-9:
                continue
            swapped = [p if j == i else q for j, q in enumerate(seated)]
            if not ok(swapped) or (legal is not None and not legal(swapped)):
                continue
            alts.append(p)
        alts.sort(key=lambda p: (-round(p.value, VALUE_DECIMALS), p.salary, p.name))
        spots.append(
            Spot(
                slot=SLOT_LABELS[i],
                index=i,
                player=player,
                held=count,
                alternatives=tuple(
                    Alternative(
                        player=p,
                        d_points=round(p.value - player.value, VALUE_DECIMALS),
                        d_salary=player.salary - p.salary,
                        held=held.get(p.name, 0),
                    )
                    for p in alts[:max_alternatives]
                ),
            )
        )
    return CashCore(
        default=tuple(seated),
        projection=projection(seated),
        salary=salary(seated),
        left=left,
        tied=tied,
        searched=len(lineups),
        near=len(near),
        truncated=truncated,
        core=tuple(core),
        open=tuple(spots),
        complete=complete,
        near_points=near_points,
        core_share=core_share,
    )


# ---------------------------------------------------------------------------------------------------------
# The band
# ---------------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Band:
    word: str  # under / coin flip / lean; "" when there is no number to place
    shown: float | None  # kept for the calibration scorecard; NEVER printed
    cashed: int | None
    of: int | None
    line_offset: float | None
    line_weeks: tuple[int, ...] = ()

    @property
    def record(self) -> str:
        if self.of:
            return f"cash builds have cashed {self.cashed} of {self.of}"
        return "no cash build has been scored yet"

    @property
    def text(self) -> str:
        if not self.word:
            return f"no band yet (ownership has not published); {self.record}"
        return f"{self.word}: {self.record}"


def band_word(shown: float | None) -> str:
    if shown is None:
        return ""
    low, high = BAND_EDGES
    if shown < low:
        return UNDER
    return COIN_FLIP if shown <= high else LEAN


def band(
    shown: float | None,
    record: tuple[int, int] | None,
    offset: float | None,
    weeks: Sequence[int] = (),
) -> Band:
    """The Default's band: a word from its simulated chance against the measured line, beside the record
    (cashed, scored) of every cash build so far."""
    cashed, of = record if record else (None, None)
    return Band(
        word=band_word(shown),
        shown=None if shown is None else round(float(shown), 4),
        cashed=cashed,
        of=of,
        line_offset=offset,
        line_weeks=tuple(int(w) for w in weeks),
    )


def record_of(rows: Sequence[Mapping[str, object]]) -> tuple[int, int] | None:
    """(cashed, scored) over `data/contests/cash_shown.csv`'s rows, all of them; None with no row."""
    scored = [r for r in rows if str(r.get("cashed", "")).strip() != ""]
    if not scored:
        return None
    cashed = sum(1 for r in scored if str(r.get("cashed")).strip().lower() in ("true", "1"))
    return cashed, len(scored)


# ---------------------------------------------------------------------------------------------------------
# What the page and the round keep
# ---------------------------------------------------------------------------------------------------------


def details_from_rows(rows: Sequence[Mapping[str, object]]) -> dict[str, dict]:
    """Name -> what a card shows beside a player, from EdgeRaw rows (the synced frame as records): his Id,
    position, team, opponent, CashOwn (a fraction; None before ownership), status (`Avail`) and kickoff."""
    from dfs.kickoff import kickoff_label, parse_kickoff

    out: dict[str, dict] = {}
    starts = [parse_kickoff(r.get("GameStart")) for r in rows]
    known = [s for s in starts if s is not None]
    first = min(known) if known else None
    for r, start in zip(rows, starts, strict=True):
        name = str(r.get("Name") or "").strip()
        if not name or name in out:
            continue
        try:
            own = float(r.get("CashOwn"))  # type: ignore[arg-type]
            own = None if own != own or own <= 0 else own
        except (TypeError, ValueError):
            own = None
        status = str(r.get("Avail") or "").strip()
        out[name] = {
            "id": str(r.get("Id") or ""),
            "position": str(r.get("Position") or ""),
            "team": str(r.get("Team") or ""),
            "opp": str(r.get("Opp") or ""),
            "cash_own": own,
            "status": status,
            "kickoff": kickoff_label(r.get("GameStart")) if start is not None else "",
            "late": bool(start is not None and first is not None and start - first >= LATE_AFTER),
            "flags": [status] if status in ("Q", "D", "OUT", "IR") else [],
        }
    return out


def _player(p: SwapPlayer, details: Mapping[str, dict]) -> dict:
    d = details.get(p.name, {})
    return {
        "id": d.get("id", ""),
        "name": p.name,
        "position": p.position,
        "team": p.team,
        "opp": p.opp,
        "salary": p.salary,
        "projection": round(p.value, 2),
        "cash_own": d.get("cash_own"),
        "status": d.get("status", ""),
        "kickoff": d.get("kickoff", ""),
        "late": bool(d.get("late", False)),
        "flags": list(d.get("flags", [])),
    }


def payload(
    core: CashCore,
    details: Mapping[str, dict],
    band_: Band,
    *,
    round: int,  # noqa: A002 - the round number, as the state file calls it
    at: str,
    metric: str = "CalPts",
    faded: Sequence[str] = (),
    picked: Mapping[str, str] | None = None,
    flex_order: str = "",
) -> dict:
    """The week input `cash_core` (and the round's record): see `SPECS_A_TO_D.md` section 2. `faded` names
    players a `cash:` Never use took out (listed under the core, with an undo on the page); `picked` maps a
    slot label to the name typed on Always use for it."""
    number, round = round, builtins.round  # noqa: A001 - the parameter keeps the state file's name
    picked = dict(picked or {})
    return {
        "round": number,
        "at": at,
        "metric": metric,
        "complete": core.complete,
        "searched": core.searched,
        "near": core.near,
        "near_truncated": core.truncated,
        "near_points": core.near_points,
        "core_share": core.core_share,
        "default": {
            "names": [p.name for p in core.default],
            "ids": [details.get(p.name, {}).get("id", "") for p in core.default],
            "slots": list(SLOT_LABELS),
            "projection": round(core.projection, 2),
            "salary": core.salary,
            "left": core.left,
            "tied": core.tied,
            "band": {
                "word": band_.word,
                "shown": band_.shown,
                "cashed": band_.cashed,
                "of": band_.of,
                "line_offset": band_.line_offset,
                "line_weeks": list(band_.line_weeks),
                "text": band_.text,
            },
            "flex_order": flex_order,
            "players": [_player(p, details) for p in core.default],
        },
        "core": [{**_player(p, details), "in": n, "of": core.near, "faded": False} for p, n in core.core]
        + [{"name": n, "faded": True} for n in faded],
        "open": [
            {
                "slot": s.slot,
                "position": s.player.position,
                "default": {**_player(s.player, details), "in": s.held},
                "alternatives": [
                    {
                        **_player(a.player, details),
                        "d_points": round(a.d_points, 2),
                        "d_salary": a.d_salary,
                        "in": a.held,
                    }
                    for a in s.alternatives
                ],
                "picked": picked.get(s.slot, ""),
            }
            for s in core.open
        ],
    }


def summary_lines(core: CashCore, band_: Band) -> list[str]:
    """The terminal's lines: the Default, its band, its core and each open spot as differences."""
    tie = f" (tied with {core.tied - 1} other{'s' if core.tied > 2 else ''})" if core.tied > 1 else ""
    of = f"{core.near}+" if core.truncated else str(core.near)
    lines = [
        f"Default cash lineup: {core.projection:.1f} pts{tie}, ${core.salary:,} (${core.left:,} left)  ·  "
        f"{band_.text}",
        "  " + ", ".join(f"{lab} {p.name}" for lab, p in zip(SLOT_LABELS, core.default, strict=True)),
        f"  Core (in {core.core_share:.0%}+ of the {of} lineups within {core.near_points:g} pts): "
        + (", ".join(p.name for p, _ in core.core) or "nobody"),
    ]
    if not core.open:
        lines.append("  No open spot: every near-optimal lineup agrees.")
    for s in core.open:
        alts = "; ".join(
            f"{a.player.name} {a.d_points:+.1f} pts, {freed(a.d_salary)}, in {a.held} of {of}"
            for a in s.alternatives
        )
        lines.append(
            f"  Open {s.slot}: {s.player.name} (in {s.held} of {of})  ·  " + (alts or NOTHING_AT_THE_PRICE)
        )
    if not core.complete:
        lines.append("  The search was cut short: a better build may exist.")
    return lines
