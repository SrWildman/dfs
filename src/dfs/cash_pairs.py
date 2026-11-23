"""Two cheap players from one team in a cash lineup: make them earn it, and say so (Sam's Week 11 note).

Sam: "Some builds spit out two Titans WRs... that's something I'd never play," and "there is sometimes a real
good reason for the 2 low priced guys... but rare. I just want to be more considerate of it rather than just
assuming it's good." So it is not a ban.

- **The pair**: two skill players (RB, WR or TE; never the QB, and never the DST, which nearly always costs
  under the line: Sam's call) from one team who both cost under `[cash] cheap_salary` (default $6,000, Sam's
  call after Week 11's Default held Ayomanor $3,700 and Tate $5,500).
- **The Default avoids it unless it earns its place**: when the best cash lineup holds a pair, the search is
  run again with "no cheap pair" as a rule, keeping the pair's most-owned player in cash (Sam: "Tate had the
  higher roster%", so Ayomanor is the one to go; `keeper`). The Default keeps the pair only when its lineup
  beats that one by more than `[cash] cheap_pair_margin` (default 1.5 points, the near-optimal band);
  otherwise the pair-free lineups are the cash candidates, so the Default, its core and its open spots all
  come from them.
- **Either way it is flagged** (`CheapPair.text`): a pair kept, with what it earns over the lineup without
  it, one click away as a difference (`swaps`); a pair avoided, with who went out and who stayed. A pair Sam
  made himself (one of them on Always use: an open-spot pick, "Someone else", a Play) is allowed, and the
  flag says so.
- **One rule in every solver**: the whole-lineup test (`leaf`) is the definition; `feasible` cuts a branch the
  moment it holds a pair, and `build_exact.LeafRules.cheap_salary` describes it, so the exact search and the
  MILP checker hold the same lineups (`tests/test_build_exact.py`).
- **Cash only**: no GPP shape reads any of this.

Pure: no sheet, no file.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field

from dfs.late_swap_search import SwapPlayer

CHEAP_SALARY = 6000  # `[cash] cheap_salary`: under this, a RB, WR or TE is "cheap"; 0 turns the rule off
PAIR_MARGIN = 1.5  # `[cash] cheap_pair_margin`: points a pair's lineup must beat the best without one by
PAIRED = frozenset({"RB", "WR", "TE"})  # the positions a pair is made of (Sam: skill players only)


def is_cheap(p: SwapPlayer, cheap_salary: int) -> bool:
    return p.position in PAIRED and p.salary < cheap_salary


def pairs(lineup: Sequence[SwapPlayer], cheap_salary: int = CHEAP_SALARY) -> list[tuple[SwapPlayer, ...]]:
    """Every team with two or more cheap skill players in `lineup`, each as its players (cheapest first)."""
    if cheap_salary <= 0:
        return []
    by_team: dict[str, list[SwapPlayer]] = {}
    for p in lineup:
        if p is not None and is_cheap(p, cheap_salary):
            by_team.setdefault(p.team, []).append(p)
    return [
        tuple(sorted(ps, key=lambda p: (p.salary, p.name)))
        for _team, ps in sorted(by_team.items())
        if len(ps) > 1
    ]


def feasible(
    cheap_salary: int, base: Callable | None = None
) -> Callable[[list[SwapPlayer], list[str]], bool]:
    """The search's branch test (`late_swap_search._Search`): False the moment a partial lineup holds a pair
    (adding players never removes one), then `base`'s own test."""

    def test(placed: list[SwapPlayer], rest: list[str]) -> bool:
        if pairs(placed, cheap_salary):
            return False
        return base is None or base(placed, rest)

    return test


def leaf(cheap_salary: int, base: Callable | None = None) -> Callable[[Sequence[SwapPlayer]], bool]:
    """The whole-lineup test: no pair, and `base`'s own test. It carries its description for the exact
    solvers (`build_exact.LeafRules`, `cheap_salary` added to what `base` described)."""
    from dfs.build_exact import LeafRules, described, rules_of

    def test(lineup: Sequence[SwapPlayer]) -> bool:
        return not pairs(lineup, cheap_salary) and (base is None or base(lineup))

    rules = rules_of(base) or LeafRules()
    return described(test, dataclasses.replace(rules, cheap_salary=cheap_salary))


# ---------------------------------------------------------------------------------------------------------
# The Default's choice and its flag
# ---------------------------------------------------------------------------------------------------------

EARNS, PICKED, NO_LINEUP = "earns", "picked", "no lineup without"


@dataclass(frozen=True)
class CheapPair:
    """A pair in the Default (`kept`), or one the Default avoided (`kept` False). Both are flagged."""

    players: tuple[SwapPlayer, ...]  # the pair (or three), cheapest first
    kept: bool
    why: str  # EARNS / PICKED (Sam put them there: Always use) / NO_LINEUP (no legal lineup without one)
    gain: float | None  # the pair's lineup minus the best without one, points; None without such a lineup
    alternative: tuple[SwapPlayer, ...] = ()  # the best lineup without one (the Default when avoided)
    margin: float = PAIR_MARGIN
    keep: SwapPlayer | None = None  # the pair's most-owned player, whom the lineup without one keeps
    own: Mapping[str, float] = field(default_factory=dict)  # cash ownership of the pair's players
    paired: tuple[SwapPlayer, ...] = ()  # the best lineup WITH the pair (the Default when kept)

    @property
    def team(self) -> str:
        return self.players[0].team

    @property
    def out(self) -> tuple[SwapPlayer, ...]:
        """The pair's players the lineup without one leaves out (all but `keep`)."""
        return tuple(p for p in self.players if self.keep is None or p.name != self.keep.name)

    @property
    def what(self) -> str:
        """`Two cheap TEN WRs (Elic Ayomanor $3,700, Carnell Tate $5,500)`."""
        positions = {p.position for p in self.players}
        kind = f"{self.players[0].position}s" if len(positions) == 1 else "players"
        count = "Two" if len(self.players) == 2 else str(len(self.players))
        names = ", ".join(f"{p.name} ${p.salary:,}" for p in self.players)
        return f"{count} cheap {self.team} {kind} ({names})"

    @property
    def without(self) -> str:
        """`the best lineup without them`, or `without Elic Ayomanor` when it keeps the most-owned one."""
        if self.keep is None:
            return "the best lineup without them"
        return "the best lineup without " + " and ".join(p.name for p in self.out)

    @property
    def text(self) -> str:
        """The flag's one line."""
        if not self.kept:
            if self.keep is None:
                return f"{self.what}: swapped out, the pair was only {_signed(self.gain)} over {self.without}"
            gone = " and ".join(p.name for p in self.out)
            owns = ", ".join(f"{p.name} {_pct(self.own.get(p.name))}" for p in (self.keep, *self.out))
            return (
                f"{self.what}: {gone} out, {self.keep.name} kept (cash own {owns}); the pair was only "
                f"{_signed(self.gain)} over {self.without}"
            )
        if self.why == PICKED:
            return f"{self.what}: your pick" + (
                f", {_signed(self.gain)} over {self.without}" if self.gain is not None else ""
            )
        if self.why == NO_LINEUP:
            return f"{self.what}: no legal lineup without them"
        return f"{self.what}: {_signed(self.gain)} over {self.without}"


def _signed(x: float | None) -> str:
    return "-" if x is None else f"{x:+.1f} pts"


def _pct(x: float | None) -> str:
    return "?" if x is None else f"{100 * x:.0f}%"


def keeper(pair: Sequence[SwapPlayer], own: Mapping[str, float]) -> SwapPlayer | None:
    """The pair's most-owned player in cash (Sam, Week 11: "Tate had the higher roster%", so Ayomanor is the
    one to go), or None when any of them has no ownership yet (then the lineup without one is simply the
    best pair-free lineup). Ties: the higher projection, then the name."""
    if not pair or any(own.get(p.name) is None for p in pair):
        return None
    return max(pair, key=lambda p: (own[p.name], p.value, p.name))


def swaps(
    default: Sequence[SwapPlayer], alternative: Sequence[SwapPlayer]
) -> list[tuple[SwapPlayer, SwapPlayer | None]]:
    """`alternative` as a difference against `default`: (out, in) for each player that differs, by position
    where one lines up."""
    have = {p.name for p in alternative}
    want = {p.name for p in default}
    out = [p for p in default if p.name not in have]
    into = [p for p in alternative if p.name not in want]
    pairs_: list[tuple[SwapPlayer, SwapPlayer | None]] = []
    for p in out:
        match = next((q for q in into if q.position == p.position), None) or (into[0] if into else None)
        if match is not None:
            into.remove(match)
        pairs_.append((p, match))
    return pairs_


def choose(
    lineups: list[list[SwapPlayer]],
    search_free: Callable[[tuple[SwapPlayer, ...]], list[list[SwapPlayer]]],
    *,
    fixed: Sequence[str] = (),
    own: Mapping[str, float] | None = None,
    cheap_salary: int = CHEAP_SALARY,
    margin: float = PAIR_MARGIN,
) -> tuple[list[list[SwapPlayer]], CheapPair | None]:
    """The cash candidates and the pair they hold or avoided. `lineups` is the cash search's, best first;
    `search_free(keep)` runs the same search with "no cheap pair" as a rule and the players `keep` in every
    lineup (only when the best lineup holds a pair). The lineup without the pair keeps its most-owned player
    in cash (`own`, `keeper`) and drops the rest; with no ownership yet, or no such lineup, it is the best
    pair-free lineup.

    Returns `lineups` and the kept pair when it earns its place (more than `margin` over the lineup without
    it), when Sam put one of them there (`fixed`: Always use, an open-spot pick; the rule never moves a
    player he did not ask to move) or when no lineup avoids it; else the pair-free lineups and the avoided
    pair. Both are flagged."""
    from dfs.cash_core import default_key, projection

    if cheap_salary <= 0 or not lineups:
        return lineups, None
    best = min(lineups, key=default_key)
    found = pairs(best, cheap_salary)
    if not found:
        return lineups, None
    own = dict(own or {})
    names = set(fixed)
    picked = next((ps for ps in found if any(p.name in names for p in ps)), None)
    pair = picked or found[0]
    keep = keeper(pair, own)
    free = search_free((keep,)) if keep is not None else []
    if not free:
        keep, free = None, search_free(())
    alt = min(free, key=default_key) if free else None
    gain = None if alt is None else round(projection(best) - projection(alt), 6)
    owns = {p.name: own[p.name] for p in pair if p.name in own}

    def flag(kept: bool, why: str) -> CheapPair:
        return CheapPair(pair, kept, why, gain, tuple(alt or ()), margin, keep, owns, tuple(best))

    if picked is not None:  # Sam put one of them there (Always use): his call, kept and flagged
        return lineups, flag(True, PICKED)
    if alt is None:
        return lineups, flag(True, NO_LINEUP)
    if gain > margin + 1e-9:
        return lineups, flag(True, EARNS)
    return free, flag(False, EARNS)


def payload(pair: CheapPair | None, default: Sequence[SwapPlayer], cheap_salary: int) -> dict:
    """The `cheap_pair` part of the `cash_core` week input: the rule's salary line, and the pair the Default
    holds (`kept`: `change` is the best lineup without it, as a difference against the Default) or avoided
    (`change` is what the rule changed: the lineup with the pair, as a difference to the Default)."""
    from dfs.cash_core import projection, salary

    out: dict = {"cheap_salary": cheap_salary, "pair": None}
    if pair is None:
        return out
    if pair.kept:
        before, after = list(default), list(pair.alternative)
    else:
        before, after = list(pair.paired), list(default)
    out["pair"] = {
        "kept": pair.kept,
        "why": pair.why,
        "names": [p.name for p in pair.players],
        "keep": pair.keep.name if pair.keep is not None else "",
        "text": pair.text,
        "gain": None if pair.gain is None else round(pair.gain, 2),
        "margin": pair.margin,
        "change": (
            {
                "kept": pair.kept,  # True: the lineup without them; False: what the rule changed
                "projection": round(projection(after), 2),
                "salary": salary(after),
                "from_projection": round(projection(before), 2),
                "swaps": [
                    {
                        "out": a.name,
                        "in": b.name if b is not None else "",
                        "position": a.position,
                        "d_points": round((b.value if b is not None else 0.0) - a.value, 2),
                        "d_salary": a.salary - (b.salary if b is not None else 0),
                    }
                    for a, b in swaps(before, after)
                ],
            }
            if before and after
            else None
        ),
    }
    return out


def from_rows(names: Sequence[str], rows: dict[str, dict]) -> list[SwapPlayer]:
    """The players of a lineup known by its names, from edge rows (`Name`, `Position`, `Team`, `Salary`); a
    name with no row or no salary is left out (it cannot make a pair)."""
    out = []
    for n in names:
        r = rows.get(n)
        if r is None:
            continue
        try:
            sal = int(float(r.get("Salary")))
        except (TypeError, ValueError):
            continue
        out.append(
            SwapPlayer(
                str(r.get("Name") or n),
                str(r.get("Position") or ""),
                str(r.get("Team") or ""),
                "",
                "",
                sal,
                0.0,
            )
        )
    return out
