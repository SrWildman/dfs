"""Stake sizing: arithmetic on Sam's own contest history and Sam's own rules. Never advice.

Three pure pieces, no sheet and no network (the Bankroll tab's STAKES block, `sheet_stakes.py`, only lays them
out):

1. **His numbers** (`build_stats`): for every format in the contest log, this season and over every season in
   the log, the entries, ROI, cash rate and volatility (the standard deviation of the weekly net). A figure
   the data cannot support is `None` (shown as "–"), never a guess: ROI needs a fee paid, volatility needs two
   weeks.
2. **This week's plan** (`make_plan`): the weekly budget (the Bankroll tab's own Weekly Budget cell, never a
   second copy in config) and the rules he set in `[stakes]` (`max_pct_bankroll_per_week`,
   `max_entry_fee_pct`, an optional `split`), turned into "cash $X, GPP $Y; largest entry $Z" plus a warning
   for every rule the week breaks. A rule left unset is simply off: the plan shows the arithmetic without it.
3. **A proposal** (`propose`) for the two rules he has not set yet, from his own bankroll and his own weekly
   volatility, with every step of the arithmetic. Read-only: it is printed, never written to config.

**The default split** (`default_split`) is between the two bankrolls the tab keeps, Cash (H2H, 50/50, Double
Up: `series.CASH_FORMATS`) and GPP (everything else). The formula, in full:

    eligible   a bucket with history (entries > 0, fees paid) and all-seasons ROI >= ROI_FLOOR (0%)
    raw_b      = w_b * roi_share_b + (1 - w_b) * (1 / k)           over the k eligible buckets
    share_b    = raw_b / sum(raw)                                   (the weights differ, so renormalise)
    w_b        = min(1, entries_b / SHRINK_ENTRIES)                 (SHRINK_ENTRIES = 100)
    roi_share  = roi_b / sum(roi over eligible)                     (even when that sum is 0)

so a bucket with fewer than 100 entries is pulled toward an even split in proportion to how short of 100 it
is, and a bucket at 100+ entries is split purely by measured ROI. **The floor is 0% ROI over every season in
the log:** a bucket below it gets $0 (`ineligible`), whatever its sample size. That is the most conservative
reading of "never a format with negative ROI above its floor" (the floor is a ROI of zero and the allocation
above it is nothing); a bucket with no history is also left out, since nothing says it is not losing money.
A `[stakes] split` he sets himself overrides all of this and only draws a warning when it puts money on a
bucket the floor would have excluded.

The numbers describe the past. Nothing here predicts anything, and the output says so.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import pandas as pd

from dfs.config import StakesConfig
from dfs.contests import series as series_mod
from dfs.sheet_contests import _cashed

CASH, GPP = "cash", "gpp"
BUCKETS = (CASH, GPP)
BUCKET_LABEL = {CASH: "Cash", GPP: "GPP", "betting": "Betting"}
BETTING = "betting"  # a budget line only: `[stakes] betting_pct` of the weekly budget, never split by ROI
PLAN_BUCKETS = (CASH, GPP, BETTING)

SHRINK_ENTRIES = 100  # a bucket reaches full weight on its own ROI at this many entries
ROI_FLOOR = 0.0  # all-seasons ROI below this: the default split allocates nothing to the bucket
MIN_WEEKS_FOR_SD = 2

# The proposal's assumptions, named so they can be argued with. A "bad week" is Z weekly standard deviations
# of net, scaled to the spend; the proposal lets that bad week cost at most BAD_WEEK_LOSS_PCT of the bankroll.
# A single entry is at most 1 / MIN_ENTRIES_PER_WEEK of the week's cap.
BAD_WEEK_Z = 2.0
BAD_WEEK_LOSS_PCT = 10.0
MIN_ENTRIES_PER_WEEK = 5
MIN_WEEKS_FOR_PROPOSAL = 8
WEEK_PCT_STEP = 0.5
ENTRY_PCT_STEP = 0.1

NOT_ADVICE = "This is arithmetic on your own history and your own rules, not financial advice."


@dataclass(frozen=True)
class Stat:
    """One format (or bucket) over one span of the log. `None` = not computable from the data."""

    entries: int
    fees: float
    net: float
    roi: float | None
    cash_rate: float | None
    weekly_sd: float | None
    weeks: int


@dataclass(frozen=True)
class StatRow:
    """A line of the by-format table: its label, whether it is a format or a total, and both spans."""

    label: str
    kind: str  # "format" | "bucket" | "all"
    season: Stat
    overall: Stat


def _stat(frame: pd.DataFrame) -> Stat:
    if frame.empty:
        return Stat(0, 0.0, 0.0, None, None, None, 0)
    fees = float(frame["fee"].sum())
    net = float((frame["winnings"] - frame["fee"]).sum())
    dated = frame[frame["week"].notna()]
    weekly = (dated["winnings"] - dated["fee"]).groupby([dated["season"], dated["week"]]).sum()
    return Stat(
        entries=len(frame),
        fees=fees,
        net=net,
        roi=net / fees if fees > 0 else None,
        cash_rate=float(_cashed(frame).mean()),
        weekly_sd=float(weekly.std(ddof=1)) if len(weekly) >= MIN_WEEKS_FOR_SD else None,
        weeks=len(weekly),
    )


def bucket_of(fmt: str) -> str:
    return CASH if fmt in series_mod.CASH_FORMATS else GPP


def build_stats(typed: pd.DataFrame, season: int) -> list[StatRow]:
    """The by-format table: every format with at least one entry (in `series.FORMATS` order), then the Cash
    and GPP totals, then everything. Each over `season` and over the whole log."""
    rows = []

    def row(label: str, kind: str, mask: pd.Series) -> None:
        sub = typed[mask]
        rows.append(StatRow(label, kind, _stat(sub[sub["season"] == season]), _stat(sub)))

    for fmt in series_mod.FORMATS:
        if (typed["format"] == fmt).any():
            row(fmt, "format", typed["format"] == fmt)
    buckets = typed["format"].map(bucket_of)
    for bucket in BUCKETS:
        row(BUCKET_LABEL[bucket], "bucket", buckets == bucket)
    row("All formats", "all", pd.Series(True, index=typed.index))
    return rows


# The formats that get money (Sam, 2026-10-18): Double Up in the Cash bucket and single-entry GPP in the GPP
# bucket. Every other format (H2H, 50/50, multi-entry GPP, Showdown, ...) gets $0 whatever its history.
MONEY_FORMATS = {CASH: series_mod.DOUBLE_UP, GPP: series_mod.SE_GPP}


def bucket_stats(rows: list[StatRow]) -> dict[str, Stat]:
    """The all-seasons Stat that decides each bucket's share: its money format's own row (Double Up for Cash,
    SE GPP for GPP), so the floor is applied by format and not by the bucket's total."""
    by_label = {r.label: r for r in rows if r.kind == "format"}
    empty = _stat(pd.DataFrame())
    return {b: by_label[MONEY_FORMATS[b]].overall if MONEY_FORMATS[b] in by_label else empty for b in BUCKETS}


# ---- the default split ----------------------------------------------------------------------------


@dataclass(frozen=True)
class SplitDetail:
    bucket: str
    entries: int
    roi: float | None
    eligible: bool
    reason: str  # why it is not eligible; "" when it is
    weight: float  # w_b
    roi_share: float
    even_share: float
    share: float


def default_split(stats: dict[str, Stat]) -> list[SplitDetail]:
    """The default split between Cash and GPP, per the module docstring's formula. Shares sum to 1, or are all
    0 when no bucket clears the floor."""
    eligible = {}
    reasons = {}
    for bucket in BUCKETS:
        stat = stats[bucket]
        if stat.entries == 0 or stat.roi is None:
            reasons[bucket] = "no history"
        elif stat.roi < ROI_FLOOR:
            reasons[bucket] = f"ROI below the {ROI_FLOOR:.0%} floor"
        else:
            eligible[bucket] = stat
    k = len(eligible)
    roi_total = sum(s.roi for s in eligible.values())
    parts = {}  # bucket -> (w, roi_share, even_share, raw)
    for bucket, stat in eligible.items():
        weight = min(1.0, stat.entries / SHRINK_ENTRIES)
        even = 1 / k
        roi_share = stat.roi / roi_total if roi_total > 0 else even
        parts[bucket] = (weight, roi_share, even, weight * roi_share + (1 - weight) * even)
    total = sum(raw for *_, raw in parts.values())
    details = []
    for bucket in BUCKETS:
        stat = stats[bucket]
        if bucket not in parts:
            details.append(SplitDetail(bucket, stat.entries, stat.roi, False, reasons[bucket], 0, 0, 0, 0))
            continue
        weight, roi_share, even, raw = parts[bucket]
        details.append(
            SplitDetail(bucket, stat.entries, stat.roi, True, "", weight, roi_share, even, raw / total)
        )
    return details


# ---- this week's plan -----------------------------------------------------------------------------


@dataclass
class Plan:
    budget: float | None
    bankroll: float | None
    week_cap: float | None  # max_pct_bankroll_per_week of the bankroll
    spend: float | None  # min(budget, week_cap)
    entry_cap: float | None  # max_entry_fee_pct of the bankroll
    shares: dict[str, float]
    split_source: str  # "your split" | "default"
    details: list[SplitDetail]
    amounts: dict[str, float | None]
    warnings: list[str] = field(default_factory=list)
    arithmetic: dict[str, str] = field(default_factory=dict)


def money(value: float | None) -> str:
    if value is None:
        return "–"
    return f"-${-value:,.2f}" if value < 0 else f"${value:,.2f}"


def _pct(value: float | None, digits: int = 1) -> str:
    return "–" if value is None else f"{value * 100:.{digits}f}%"


def make_plan(
    cfg: StakesConfig,
    *,
    budget: float | None,
    bankroll: float | None,
    stats: dict[str, Stat],
    spent_so_far: float | None = None,
    largest_entry_so_far: float | None = None,
) -> Plan:
    """This week's plan from the Weekly Budget, the starting bankroll and the `[stakes]` rules. `spent_so_far`
    and `largest_entry_so_far` (what the week's ledgers already hold) only feed warnings."""
    warnings: list[str] = []
    arithmetic: dict[str, str] = {}
    has_bankroll = bankroll is not None and bankroll > 0
    week_cap = entry_cap = None

    if cfg.max_pct_bankroll_per_week is not None:
        if has_bankroll:
            week_cap = bankroll * cfg.max_pct_bankroll_per_week / 100
            arithmetic["week_cap"] = f"{cfg.max_pct_bankroll_per_week:g}% x {money(bankroll)}"
        else:
            warnings.append("max_pct_bankroll_per_week is set but the Starting Bankroll cell is blank or 0.")
    if cfg.max_entry_fee_pct is not None:
        if has_bankroll:
            entry_cap = bankroll * cfg.max_entry_fee_pct / 100
            arithmetic["entry_cap"] = f"{cfg.max_entry_fee_pct:g}% x {money(bankroll)}"
        else:
            warnings.append("max_entry_fee_pct is set but the Starting Bankroll cell is blank or 0.")

    if budget is None:
        spend = week_cap
        warnings.append("The Weekly Budget cell is blank, so there is no budget to split.")
    elif week_cap is not None and week_cap < budget:
        spend = week_cap
        warnings.append(
            f"Your weekly budget {money(budget)} is above your max_pct_bankroll_per_week rule "
            f"({arithmetic['week_cap']} = {money(week_cap)}); the plan spends {money(week_cap)}."
        )
    else:
        spend = budget

    details = default_split(stats)
    if cfg.split is not None:
        shares = {CASH: cfg.split.cash / 100, GPP: cfg.split.gpp / 100}
        source = "your split"
        for d in details:
            if shares[d.bucket] > 0 and not d.eligible:
                warnings.append(
                    f"Your split puts {shares[d.bucket]:.0%} on {BUCKET_LABEL[d.bucket]}, which the default "
                    f"rule would leave out ({d.reason}; all-seasons ROI {_pct(d.roi)})."
                )
    else:
        shares = {d.bucket: d.share for d in details}
        source = "default"
        if not any(d.eligible for d in details):
            warnings.append(
                "Neither Cash nor GPP clears the ROI floor over your history, so the default split "
                "allocates $0; set [stakes] split to allocate anyway."
            )
    betting = cfg.betting_pct / 100
    shares = {
        b: s * (1 - betting) for b, s in shares.items()
    }  # the contest split shares the rest of the budget
    shares[BETTING] = betting
    amounts = {b: (None if spend is None else spend * shares[b]) for b in PLAN_BUCKETS}

    if spend is not None and spent_so_far is not None and spent_so_far > spend + 1e-9:
        warnings.append(
            f"This week's ledgers already hold {money(spent_so_far)} of entries, above {money(spend)}."
        )
    if entry_cap is not None and largest_entry_so_far is not None and largest_entry_so_far > entry_cap + 1e-9:
        warnings.append(
            f"This week's largest entry fee {money(largest_entry_so_far)} is above your "
            f"max_entry_fee_pct rule ({arithmetic['entry_cap']} = {money(entry_cap)})."
        )
    return Plan(
        budget, bankroll, week_cap, spend, entry_cap, shares, source, details, amounts, warnings, arithmetic
    )


def plan_line(plan: Plan) -> str:
    """The sentence the plan leads with: "Your rules give: cash $X, GPP $Y; largest entry $Z"."""
    largest = money(plan.entry_cap) if plan.entry_cap is not None else "no limit set"
    return (
        f"Your rules give: cash {money(plan.amounts[CASH])}, GPP {money(plan.amounts[GPP])}, "
        f"betting {money(plan.amounts[BETTING])}; largest entry {largest}"
    )


def unset_rules(cfg: StakesConfig) -> list[str]:
    names = []
    if cfg.max_pct_bankroll_per_week is None:
        names.append("max_pct_bankroll_per_week")
    if cfg.max_entry_fee_pct is None:
        names.append("max_entry_fee_pct")
    return names


# ---- the proposal ---------------------------------------------------------------------------------


@dataclass
class Proposal:
    weeks: int
    weekly_sd: float | None
    mean_spend: float | None
    sd_per_dollar: float | None  # k = weekly_sd / mean_spend
    max_pct_bankroll_per_week: float | None
    max_entry_fee_pct: float | None
    bankroll: float | None
    lines: list[str]


def _floor_to(value: float, step: float) -> float:
    return round(math.floor(value / step + 1e-9) * step, 10)


def propose(typed: pd.DataFrame, bankroll: float | None) -> Proposal:
    """Values for the two rules he has not set, from his own weekly volatility, with the arithmetic.

        k           = weekly standard deviation of net / average weekly entry fees     (his history)
        bad week    = BAD_WEEK_Z * k * spend      (what a 2-sigma week costs when you spend `spend`)
        pct_week    = BAD_WEEK_LOSS_PCT / (BAD_WEEK_Z * k)   so a bad week costs at most 10% of the bankroll
        entry pct   = pct_week / MIN_ENTRIES_PER_WEEK         so no entry is over a fifth of the week's cap

    both rounded DOWN (to 0.5 and 0.1). The bankroll only converts the percentages to dollars: the
    percentages come from the history alone."""
    dated = typed[typed["week"].notna()]
    weekly = (
        pd.DataFrame({"net": dated["winnings"] - dated["fee"], "fee": dated["fee"]})
        .groupby([dated["season"], dated["week"]])
        .sum()
    )
    weekly = weekly[weekly["fee"] > 0]  # a week with only freeroll entries risked nothing
    weeks = len(weekly)
    if weeks < MIN_WEEKS_FOR_PROPOSAL:
        note = (
            f"Not enough history for a proposal: {weeks} week(s) of entries, {MIN_WEEKS_FOR_PROPOSAL} needed."
        )
        return Proposal(weeks, None, None, None, None, None, bankroll, [note])
    sd = float(weekly["net"].std(ddof=1))
    spend = float(weekly["fee"].mean())
    k = sd / spend
    raw_week = BAD_WEEK_LOSS_PCT / (BAD_WEEK_Z * k) if k > 0 else 100.0
    pct_week = min(100.0, _floor_to(raw_week, WEEK_PCT_STEP))
    pct_entry = _floor_to(pct_week / MIN_ENTRIES_PER_WEEK, ENTRY_PCT_STEP)
    lines = [
        f"History: {weeks} weeks with entries (every season in the log). Weekly net standard deviation "
        f"{money(sd)}; average weekly entry fees {money(spend)}.",
        f"k = {money(sd)} / {money(spend)} = {k:.2f} (weekly net standard deviation per dollar entered).",
        f"A bad week is {BAD_WEEK_Z:g} standard deviations: it costs {BAD_WEEK_Z:g} x {k:.2f} = "
        f"{BAD_WEEK_Z * k:.2f} of what you spend. Letting it cost at most {BAD_WEEK_LOSS_PCT:g}% of the "
        "bankroll:",
        f"max_pct_bankroll_per_week = {BAD_WEEK_LOSS_PCT:g}% / {BAD_WEEK_Z * k:.2f} = {raw_week:.2f}%, "
        f"rounded down to {pct_week:g}%.",
        f"max_entry_fee_pct = {pct_week:g}% / {MIN_ENTRIES_PER_WEEK} entries = "
        f"{pct_week / MIN_ENTRIES_PER_WEEK:.2f}%, rounded down to {pct_entry:g}%.",
    ]
    if bankroll is not None and bankroll > 0:
        lines.append(
            f"On a {money(bankroll)} bankroll that is {money(bankroll * pct_week / 100)} a week and "
            f"{money(bankroll * pct_entry / 100)} for the largest entry."
        )
    else:
        lines.append("Bankroll not read: the dollar amounts need it.")
    lines.append(
        f"The {BAD_WEEK_Z:g} standard deviations, the {BAD_WEEK_LOSS_PCT:g}% and the {MIN_ENTRIES_PER_WEEK} "
        f"entries are this proposal's own assumptions (stakes.py), not findings. {NOT_ADVICE}"
    )
    return Proposal(weeks, sd, spend, k, pct_week, pct_entry, bankroll, lines)
