"""Contest tiers: which contests the money should go to, as rules, so no lobby list is ever needed.

The lobby changes all week (new contests open, DraftKings releases smaller fields near lock), so this works
from what a contest *is*, not from its name: its format, fee, maximum entrants, entries allowed per user,
prize pool and min-cash. The evidence behind every rule is in `docs/CONTEST_SELECTION.md` (Sam's five
seasons of paid NFL entries plus the industry's game-selection research). One module serves the Week page,
`dfs contests plan` and `dfs week close`.

**Tiers**

- **A, about 60% of the week's money.** H2H at $1-$5; single-entry double-ups and 50/50s at $2-$25 with a
  field under 6,000. (Casual-only contests would be A too, but Sam is not eligible: `[contest_plan]
  casual_eligible = false`.)
- **B, about 30%.** Classic GPPs that are single-entry or 3-max, a field under 2,500, a fee of $3-$15 (more
  where the rake is 13% or less), rake 16% or less, min-cash at least 2x. Prefer the smallest version of a
  family.
- **C, the lottery: at most 10% of the money and $15 a week, of which Showdown $5 and big-field multi-entry
  $10.** Showdown; GPPs with 5,000+ entrants and 4 or more entries a user; anything that is neither A nor B
  nor Avoid (a GPP past Tier B's field size, a fee outside the band, a format the history says nothing about).
- **Avoid**, with the reason: a multi-entry double-up or 50/50; a 4+-max GPP with 1,000-5,000 entrants (Sam's
  worst bucket, -64% over 235 entries); rake above 17%; a GPP whose min-cash is under 2x; an H2H opponent who
  is a regular to avoid (0-3 or worse, or under 30% over 5+ games).

One reading to know: Tier B takes **up to 3 entries a user**, and the Avoid bucket "multi-entry GPPs with
1,000-5,000 entrants" takes **4 or more**, so a 3-max with 1,000-2,500 entrants is B (as the B rule says) and
a 20-max of the same size is Avoid (as the history says).

Rake is `1 - prize pool / (max entrants x fee)`, the share of the fees the site keeps; it is unknown (and no
rule that needs it fires) when any of the three is missing. A rule that needs an input that was not given does
not fire, and the verdict says so in its flags (`field size unknown`), never a guess.

**Flags** (information, not tiers): `overlay possible` (a guaranteed contest, under 80% full: check within 2
hours of lock), `late release` (first seen on Sunday morning), `smaller sibling exists` (the same family in a
smaller field).

Pure: no sheet, no files, no network (the callers read the log and the opponents file).
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any

import pandas as pd

from dfs.contests import series as series_mod

A, B, C, AVOID = "A", "B", "C", "Avoid"
TIERS = (A, B, C, AVOID)

# --- the thresholds (docs/CONTEST_SELECTION.md) -----------------------------------------------------------
H2H_FEE = (1.0, 5.0)
CASH_FEE = (2.0, 25.0)
CASH_MAX_FIELD = 6000
GPP_FEE = (3.0, 15.0)
GPP_MAX_FIELD = 2500
GPP_MAX_ENTRIES_B = 3  # "single-entry or 3-max"
GPP_RAKE_B = 0.16
RAKE_AVOID = 0.17
LOW_RAKE_BIG_FEE = 0.13  # a fee above the band is Tier B where the rake is this low
MIN_CASH_FLOOR = 2.0
WORST_BUCKET = (1000, 5000)  # multi-entry GPPs here lost 64% over 235 entries
BIG_FIELD = 5000
OVERLAY_FILL = 0.80

# What the money is split into (the Weekly Budget): A and B share what C leaves, two to one.
SHARE_C = 0.10
C_MAX_DOLLARS = 15.0

DEFAULT_SHOWDOWN_MAX = 5.0
DEFAULT_BIG_MULTI_MAX = 10.0

LOOK_FOR = {
    A: (
        "H2H at $1-$5: create your own, one game per opponent, skip the regulars who beat you. "
        "Single-entry double-ups and 50/50s at $2-$25 with a field under ~6,000 (50/50s keep 10%, "
        "double-ups 13%)."
    ),
    B: (
        "Single-entry or 3-max Classic GPPs, under ~2,500 entrants, $3-15, min-cash 2x or better, rake "
        "16% or less; prefer the smallest version of a contest family, and late-released small fields on "
        "Sunday morning."
    ),
    C: (
        "The lottery, at most 10% and $15 a week: one big-field multi-entry GPP (5,000+ entrants, $10 or "
        "less) and Showdown ($5 or less). Nothing else belongs here."
    ),
}
AVOID_LOOK_FOR = (
    "Multi-entry double-ups; multi-entry GPPs with 1,000-5,000 entrants; rake above 17%; a GPP whose "
    "min-cash is under 2x; H2H regulars who are 0-3 or worse against you."
)


@dataclass(frozen=True)
class Contest:
    """What is known about one contest. `format` is a `contests.series` format (H2H, 50/50, Double Up, SE GPP,
    N-max GPP, Showdown, ...); an unknown number is None."""

    format: str
    fee: float
    entrants: int | None = None  # the field's maximum (what the prize pool was sized for)
    entry_max: int | None = None  # entries allowed per user; 1 = single entry
    prize_pool: float | None = None
    min_cash: float | None = None  # the smallest prize as a multiple of the fee (2.0 = 2x)
    entered: int | None = None  # entrants so far (for the overlay flag)
    opponent: str = ""  # H2H only
    late_release: bool = False
    family_sizes: Sequence[int] = ()  # other sizes of the same contest family on offer


@dataclass(frozen=True)
class Verdict:
    tier: str
    reason: str
    rake: float | None = None
    flags: tuple[str, ...] = ()
    bucket: str = ""  # the capped line of Tier C this falls under ("Showdown", "Big multi-entry GPP") or ""

    def line(self) -> str:
        rake = "" if self.rake is None else f", rake {self.rake:.0%}"
        flags = "" if not self.flags else f" [{'; '.join(self.flags)}]"
        return f"Tier {self.tier}: {self.reason}{rake}{flags}"


def effective_rake(contest: Contest) -> float | None:
    """`1 - prize pool / (max entrants x fee)`, None when any of the three is missing (or the fee is 0)."""
    if contest.prize_pool is None or not contest.entrants or contest.fee <= 0:
        return None
    return 1.0 - contest.prize_pool / (contest.entrants * contest.fee)


def is_gpp(fmt: str) -> bool:
    return fmt in (series_mod.SE_GPP, series_mod.N_MAX_GPP)


def _money(value: float) -> str:
    return f"${value:,.0f}" if value == int(value) else f"${value:,.2f}"


def _flags(contest: Contest, rake: float | None) -> list[str]:
    flags = []
    if contest.format != series_mod.H2H and contest.entrants is None:
        flags.append("field size unknown")
    if is_gpp(contest.format) and contest.entry_max is None:
        flags.append("entry max unknown")
    if is_gpp(contest.format) and contest.min_cash is None and rake is not None:
        flags.append("min-cash unknown")
    if (
        contest.prize_pool is not None
        and contest.entrants
        and contest.entered is not None
        and contest.entered < OVERLAY_FILL * contest.entrants
        and contest.format != series_mod.H2H
    ):
        flags.append(
            f"overlay possible: {contest.entered / contest.entrants:.0%} full; guaranteed contests under "
            f"{OVERLAY_FILL:.0%} full within 2 hours of lock have fewer opponents for the same prizes"
        )
    if contest.late_release:
        flags.append("late release")
    smaller = [n for n in contest.family_sizes if contest.entrants and n < contest.entrants]
    if smaller:
        flags.append(f"smaller sibling exists ({min(smaller):,} entrants)")
    return flags


def classify(
    contest: Contest, *, avoid_opponents: Iterable[str] = (), casual_eligible: bool = False
) -> Verdict:
    """The tier of one contest, the effective rake, a one-line reason and the flags (see the module doc).
    `avoid_opponents` are the H2H regulars to avoid (`contest_plan.h2h_avoid`)."""
    del casual_eligible  # Casual contests are not recognised from these inputs; Sam is not eligible anyway
    fmt, fee = contest.format, contest.fee
    rake = effective_rake(contest)
    flags = tuple(_flags(contest, rake))
    n, entries = contest.entrants, contest.entry_max

    def v(tier: str, reason: str, bucket: str = "") -> Verdict:
        return Verdict(tier, reason, rake, flags, bucket)

    if fee <= 0:
        return v(A, "free entry: no fee, no rake")

    # --- Avoid: the checks that override a tier ----------------------------------------------------------
    if fmt == series_mod.H2H and contest.opponent:
        regulars = {o.strip().casefold() for o in avoid_opponents}
        if contest.opponent.strip().casefold() in regulars:
            return v(AVOID, f"{contest.opponent} is a regular who beats you (0-3 or worse, or under 30%)")
    if rake is not None and rake > RAKE_AVOID:
        return v(AVOID, f"rake {rake:.0%} is above {RAKE_AVOID:.0%}")
    if fmt in (series_mod.DOUBLE_UP, series_mod.FIFTY_FIFTY) and entries is not None and entries > 1:
        return v(AVOID, f"multi-entry {fmt.lower()} ({entries}-max): pros max-enter these")
    if is_gpp(fmt) and contest.min_cash is not None and contest.min_cash < MIN_CASH_FLOOR:
        return v(AVOID, f"min-cash {contest.min_cash:g}x is under {MIN_CASH_FLOOR:g}x")
    lo, hi = WORST_BUCKET
    if (
        is_gpp(fmt)
        and entries is not None
        and entries > GPP_MAX_ENTRIES_B
        and n is not None
        and lo <= n <= hi
    ):
        return v(AVOID, f"{entries}-max GPP with {n:,} entrants: your worst bucket (-64% over 235 entries)")

    # --- Tier A: H2H and single-entry cash --------------------------------------------------------------
    if fmt == series_mod.H2H:
        if H2H_FEE[0] <= fee <= H2H_FEE[1]:
            return v(A, f"H2H at {_money(fee)}")
        return v(C, f"H2H at {_money(fee)} is outside the ${H2H_FEE[0]:g}-${H2H_FEE[1]:g} band", "Other")
    if fmt in (series_mod.DOUBLE_UP, series_mod.FIFTY_FIFTY):
        kind = "single-entry " if entries == 1 else ""
        if not CASH_FEE[0] <= fee <= CASH_FEE[1]:
            return v(
                C, f"{fmt.lower()} at {_money(fee)} is outside ${CASH_FEE[0]:g}-${CASH_FEE[1]:g}", "Other"
            )
        if n is not None and n >= CASH_MAX_FIELD:
            return v(
                C,
                f"{fmt.lower()} field of {n:,} is past ~{CASH_MAX_FIELD:,} (5k-20k fields cashed 49%)",
                "Other",
            )
        return v(A, f"{kind}{fmt.lower()} at {_money(fee)}" + (f", {n:,} entrants" if n is not None else ""))

    # --- Tier C by format -------------------------------------------------------------------------------
    if fmt == series_mod.SHOWDOWN:
        return v(C, "Showdown loses at every field size (-45% over 734 entries)", "Showdown")
    if not is_gpp(fmt):
        return v(C, f"{fmt}: nothing in your history to rank it, so it counts as the lottery", "Other")

    # --- GPPs: B, else C ---------------------------------------------------------------------------------
    big_multi = entries is not None and entries > GPP_MAX_ENTRIES_B and n is not None and n >= BIG_FIELD
    if big_multi:
        return v(
            C, f"{entries}-max GPP with {n:,} entrants: big-field multi-entry lottery", "Big multi-entry GPP"
        )
    if entries is not None and entries > GPP_MAX_ENTRIES_B:
        return v(C, f"{entries}-max GPP: Tier B is single-entry or 3-max", "Big multi-entry GPP")

    why_not_b = []
    if n is not None and n >= GPP_MAX_FIELD:
        why_not_b.append(f"{n:,} entrants is past {GPP_MAX_FIELD:,}")
    low_rake_big_fee = rake is not None and rake <= LOW_RAKE_BIG_FEE and fee > GPP_FEE[1]
    if fee < GPP_FEE[0]:
        why_not_b.append(f"fee {_money(fee)} is under {_money(GPP_FEE[0])}")
    elif fee > GPP_FEE[1] and not low_rake_big_fee:
        why_not_b.append(
            f"fee {_money(fee)} is over {_money(GPP_FEE[1])} (and the rake is not {LOW_RAKE_BIG_FEE:.0%} "
            "or less)"
        )
    if rake is not None and rake > GPP_RAKE_B:
        why_not_b.append(f"rake {rake:.0%} is above {GPP_RAKE_B:.0%}")
    if why_not_b:
        return v(C, "; ".join(why_not_b), "Other")
    kind = "single-entry" if entries == 1 else (f"{entries}-max" if entries else "")
    parts = [f"{kind} GPP".strip(), f"{_money(fee)}"]
    if n is not None:
        parts.append(f"{n:,} entrants")
    if contest.min_cash is not None:
        parts.append(f"min-cash {contest.min_cash:g}x")
    return v(B, ", ".join(parts))


# --- the week's money -------------------------------------------------------------------------------------


@dataclass(frozen=True)
class TierPlan:
    budget: float
    a: float
    b: float
    c: float
    c_showdown: float
    c_big_multi: float

    def dollars(self, tier: str) -> float:
        return {A: self.a, B: self.b, C: self.c}.get(tier, 0.0)


def plan_dollars(
    budget: float,
    *,
    showdown_max: float = DEFAULT_SHOWDOWN_MAX,
    big_multi_max: float = DEFAULT_BIG_MULTI_MAX,
    c_max: float = C_MAX_DOLLARS,
    c_share: float = SHARE_C,
) -> TierPlan:
    """The Weekly Budget in dollars by tier: C is 10% up to $15, A and B split the rest two to one (so a
    $50 budget is $30 / $15 / $5). C's two lines are capped separately ($5 Showdown, $10 big multi-entry)."""
    c = round(min(c_share * budget, c_max), 2)
    rest = max(0.0, budget - c)
    b = round(rest / 3, 2)
    a = round(rest - b, 2)
    return TierPlan(round(budget, 2), a, b, c, min(showdown_max, c), min(big_multi_max, c))


@dataclass(frozen=True)
class TierSpend:
    """Money entered, by tier, against the plan."""

    plan: TierPlan
    spent: dict[str, float]  # tier -> dollars, over A, B, C, Avoid
    c_lines: dict[str, float]  # "Showdown" / "Big multi-entry GPP" -> dollars
    entries: dict[str, int]
    over: list[str]

    @property
    def total(self) -> float:
        return sum(self.spent.values())

    def line(self) -> str:
        """One line: `Tiers, Week 9: A $28 of $30, B $20 of $15 (over), C $5 of $5; Avoid $0`."""
        parts = []
        for tier in (A, B, C):
            cap = self.plan.dollars(tier)
            tag = " (over)" if self.spent[tier] > cap + 1e-9 else ""
            parts.append(f"{tier} {_money(self.spent[tier])} of {_money(cap)}{tag}")
        avoid = f"; Avoid {_money(self.spent[AVOID])}" if self.spent[AVOID] else ""
        return ", ".join(parts) + avoid


def spend_by_tier(verdicts: Iterable[tuple[Verdict, float]], plan: TierPlan) -> TierSpend:
    """Add up (verdict, fee) pairs by tier and say what is over plan."""
    spent = dict.fromkeys(TIERS, 0.0)
    entries = dict.fromkeys(TIERS, 0)
    c_lines: dict[str, float] = {}
    for verdict, fee in verdicts:
        spent[verdict.tier] += fee
        entries[verdict.tier] += 1
        if verdict.tier == C and verdict.bucket:
            c_lines[verdict.bucket] = c_lines.get(verdict.bucket, 0.0) + fee
    over = []
    for tier in (A, B, C):
        if spent[tier] > plan.dollars(tier) + 1e-9:
            over.append(f"Tier {tier} {_money(spent[tier])} (plan {_money(plan.dollars(tier))})")
    if c_lines.get("Showdown", 0.0) > plan.c_showdown + 1e-9:
        over.append(f"Showdown {_money(c_lines['Showdown'])} (cap {_money(plan.c_showdown)})")
    if c_lines.get("Big multi-entry GPP", 0.0) > plan.c_big_multi + 1e-9:
        over.append(
            f"Big multi-entry GPP {_money(c_lines['Big multi-entry GPP'])} (cap {_money(plan.c_big_multi)})"
        )
    if spent[AVOID]:
        over.append(f"Avoid {_money(spent[AVOID])} (the plan is $0)")
    return TierSpend(plan, spent, c_lines, entries, over)


# --- contests as the files describe them ------------------------------------------------------------------

_SINGLE = re.compile(r"\[\s*(?:single entry|1 entry max)\s*\]|\bsingle entry\b", re.IGNORECASE)
_MAX = re.compile(r"\[\s*(\d+)\s*entry max\s*\]", re.IGNORECASE)


def entry_max_from_name(name: str) -> int | None:
    """The entries a user may make, read from a contest's name: its `(2/5)` counter, `[3 Entry Max]` or
    `Single Entry`; None when the name says nothing."""
    _, _, _, counter_max = series_mod.normalize_name(name)
    if counter_max:
        return counter_max
    found = _MAX.search(name)
    if found:
        return int(found.group(1))
    return 1 if _SINGLE.search(name) else None


def contest_from_log_row(row: Any, *, min_cash: float | None = None) -> Contest:
    """A `contests.history.typed` row (a contest as it ended: its real field and prize) as a `Contest`."""

    def num(value: Any) -> float | None:
        return None if value is None or pd.isna(value) else float(value)

    entrants = num(row.get("entries"))
    entry_max = num(row.get("entry_max")) or entry_max_from_name(str(row.get("entry", "")))
    if not entry_max and row["format"] in (series_mod.SE_GPP, series_mod.H2H):
        entry_max = 1
    return Contest(
        format=str(row["format"]),
        fee=float(row["fee"]),
        entrants=int(entrants) if entrants else None,
        entry_max=int(entry_max) if entry_max else None,
        prize_pool=num(row.get("prize_pool")),
        min_cash=min_cash,
        opponent=str(row.get("opponent") or "") if row["format"] == series_mod.H2H else "",
    )


def history_by_series(typed: pd.DataFrame) -> dict[str, dict[str, float]]:
    """For each series in the log: the median field and prize pool of the paid contests (`entrants`,
    `prize_pool`), the best guess at a contest the entries file names but does not size."""
    paid = typed[typed["fee"] > 0].drop_duplicates("contest_key")
    out: dict[str, dict[str, float]] = {}
    for key, rows in paid.groupby("series"):
        sized = rows[rows["entries"].notna()]
        if len(sized):
            out[str(key)] = {
                "entrants": float(sized["entries"].astype(float).median()),
                "prize_pool": float(sized["prize_pool"].astype(float).median()),
            }
    return out


@dataclass(frozen=True)
class EnteredContest:
    name: str
    fee: float
    verdict: Verdict
    sized_from_history: bool = False


def classify_entered(
    entries: Iterable[tuple[str, str, float]],
    *,
    history: dict[str, dict[str, float]] | None = None,
    avoid_opponents: Iterable[str] = (),
) -> list[EnteredContest]:
    """Tier each `(contest name, format, fee)` of an entries file. The file does not give a contest's field or
    prize pool, so those come from the same series' median in the log (`history_by_series`) when it has one,
    and are unknown (a flag on the verdict) when it does not."""
    out = []
    history = history or {}
    avoid = list(avoid_opponents)
    for name, fmt, fee in entries:
        info = series_mod.classify("Classic", name, fee=fee, contest_entries=None, places_paid=None)
        size = history.get(info.series)
        entry_max = info.entry_max or entry_max_from_name(name)
        if entry_max is None and fmt in (series_mod.SE_GPP, series_mod.H2H):
            entry_max = 1
        contest = Contest(
            format=fmt,
            fee=fee,
            entrants=int(size["entrants"]) if size else None,
            entry_max=entry_max,
            prize_pool=size["prize_pool"] if size else None,
            opponent=info.opponent if fmt == series_mod.H2H else "",
        )
        out.append(EnteredContest(name, fee, classify(contest, avoid_opponents=avoid), size is not None))
    return out


def verdict_rows(verdicts: Iterable[EnteredContest]) -> list[dict[str, Any]]:
    return [
        {
            "name": e.name,
            "fee": e.fee,
            "tier": e.verdict.tier,
            "reason": e.verdict.reason,
            "sized": e.sized_from_history,
        }
        for e in verdicts
    ]


@dataclass
class TierView:
    """The Week page's plan: dollars by tier plus what to look for, as plain data."""

    plan: TierPlan
    look_for: dict[str, str] = field(default_factory=lambda: {**LOOK_FOR, AVOID: AVOID_LOOK_FOR})
