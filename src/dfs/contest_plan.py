"""Plan D item 3: the weekly contest plan, and the check of a week against it (`dfs contests plan`, terminal
only).

The audit (Nov 2026) over five seasons of paid NFL entries: Double Up +9.1% ROI, SE GPP +5.2%, H2H -2.8%,
N-max GPP -32.2%, Showdown -44.8%. Sam's call is to shrink the losing formats, not stop them, so the plan
caps two of them and points the rest at the formats that hold up:

- Showdown: at most `showdown_max` a week ($5);
- N-max GPPs with `big_field_entrants` (2,000) or more entrants: at most `big_nmax_max` ($10);
- the rest to double-ups, 50/50s, H2Hs and single-entry GPPs under `small_field_entrants` (1,000)
  entrants;
- the week's total at most the Bankroll tab's Weekly Budget.

Everything here is arithmetic on Sam's own log (`data/contests/contest_log.csv`, NFL only) and his own
settings (`[contest_plan]` in config.toml); it never changes what he enters. Pure: no sheet, no files (the
CLI reads them)."""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from dfs.config import ContestPlanConfig
from dfs.contests import series as series_mod

SHOWDOWN = "Showdown"
BIG_NMAX = "Big N-max GPP"
PREFERRED = "Cash + small SE GPP"
OUTSIDE = "Outside the plan"
PLAN_BUCKETS = (SHOWDOWN, BIG_NMAX, PREFERRED, OUTSIDE)
PREFERRED_FORMATS = frozenset({series_mod.DOUBLE_UP, series_mod.FIFTY_FIFTY, series_mod.H2H})

# Field-size bands for the rake and ROI table (the audit's bands).
FIELD_BANDS = (
    (0, 200, "up to 200"),
    (201, 1000, "201-1,000"),
    (1001, 5000, "1,001-5,000"),
    (5001, None, "5,001+"),
)

H2H_AVOID_MIN_LOSSES = 3  # 0-3 or worse
H2H_AVOID_MIN_GAMES = 5
H2H_AVOID_MAX_RATE = 0.30


def plan_bucket(fmt: str, entrants: int | None, cfg: ContestPlanConfig) -> str:
    """Which line of the plan an entry falls under."""
    if fmt == series_mod.SHOWDOWN:
        return SHOWDOWN
    if fmt == series_mod.N_MAX_GPP and entrants is not None and entrants >= cfg.big_field_entrants:
        return BIG_NMAX
    if fmt in PREFERRED_FORMATS:
        return PREFERRED
    if fmt == series_mod.SE_GPP and entrants is not None and entrants < cfg.small_field_entrants:
        return PREFERRED
    return OUTSIDE


def with_plan_bucket(typed: pd.DataFrame, cfg: ContestPlanConfig) -> pd.DataFrame:
    out = typed.copy()
    entrants = out["entries"].astype("Float64")
    out["plan_bucket"] = [
        plan_bucket(f, None if pd.isna(n) else int(n), cfg)
        for f, n in zip(out["format"], entrants, strict=True)
    ]
    return out


@dataclass(frozen=True)
class PlanLine:
    bucket: str
    cap: float | None  # None: no cap of its own (the total caps it)
    text: str


def plan_lines(cfg: ContestPlanConfig, budget: float | None) -> list[PlanLine]:
    rest = None if budget is None else max(0.0, budget - cfg.showdown_max - cfg.big_nmax_max)
    rest_text = "the rest of the budget" if rest is None else f"up to ${rest:,.2f} (the rest of the budget)"
    return [
        PlanLine(SHOWDOWN, cfg.showdown_max, f"Showdown: at most ${cfg.showdown_max:,.2f}"),
        PlanLine(
            BIG_NMAX,
            cfg.big_nmax_max,
            f"N-max GPPs with {cfg.big_field_entrants:,}+ entrants: at most ${cfg.big_nmax_max:,.2f}",
        ),
        PlanLine(
            PREFERRED,
            rest,
            f"Double-ups, 50/50s, H2Hs and single-entry GPPs under {cfg.small_field_entrants:,} entrants: "
            f"{rest_text}",
        ),
        PlanLine(OUTSIDE, 0.0, "Anything else (other GPPs, multipliers, ...): $0 in the plan"),
    ]


@dataclass(frozen=True)
class WeekCheck:
    season: int
    week: int
    spent: dict[str, float]
    total: float
    budget: float | None
    over: list[str]  # "Showdown $31.00 (cap $5.00)"

    def line(self) -> str:
        """`week close`'s one line: "spent $X vs plan $Y; over in: ..." """
        plan = "no budget set" if self.budget is None else f"plan ${self.budget:,.2f}"
        over = "; over in: " + ", ".join(self.over) if self.over else "; within the plan"
        return f"Contest plan, Week {self.week}: spent ${self.total:,.2f} vs {plan}{over}"


def check_week(
    typed: pd.DataFrame, season: int, week: int, cfg: ContestPlanConfig, budget: float | None
) -> WeekCheck:
    """`week`'s paid entries in `typed` (the log, `history.typed`) against the plan."""
    rows = with_plan_bucket(typed[(typed["season"] == season) & (typed["week"] == week)], cfg)
    spent = {b: float(rows.loc[rows["plan_bucket"] == b, "fee"].sum()) for b in PLAN_BUCKETS}
    total = float(rows["fee"].sum())
    over = []
    for line in plan_lines(cfg, budget):
        if line.cap is not None and spent[line.bucket] > line.cap + 1e-9:
            over.append(f"{line.bucket} ${spent[line.bucket]:,.2f} (cap ${line.cap:,.2f})")
    if budget is not None and total > budget + 1e-9:
        over.append(f"the total ${total:,.2f} (budget ${budget:,.2f})")
    return WeekCheck(season, week, spent, total, budget, over)


def last_week(typed: pd.DataFrame, season: int) -> int | None:
    weeks = typed.loc[typed["season"] == season, "week"].dropna()
    return int(weeks.max()) if len(weeks) else None


def rake(frame: pd.DataFrame) -> pd.Series:
    """Per entry: `1 - prize_pool / (entrants x fee)`, the share of the entry fees the site keeps (negative
    when a contest overlaid). NaN for free or unknown-size contests."""
    pool = frame["prize_pool"].astype("Float64")
    take = frame["entries"].astype("Float64") * frame["fee"].astype("Float64")
    return (1 - pool / take).where(take > 0)


@dataclass(frozen=True)
class RakeRow:
    label: str
    contests: int
    rake: float | None  # median over distinct contests
    entries: int
    roi: float | None


def _roi(frame: pd.DataFrame) -> float | None:
    fees = float(frame["fee"].sum())
    return float((frame["winnings"] - frame["fee"]).sum()) / fees if fees > 0 else None


def _rake_row(label: str, frame: pd.DataFrame) -> RakeRow:
    paid = frame[frame["fee"] > 0]
    contests = paid.drop_duplicates("contest_key")
    r = rake(contests).dropna()
    return RakeRow(label, len(contests), float(r.median()) if len(r) else None, len(paid), _roi(paid))


def rake_by_format(typed: pd.DataFrame) -> list[RakeRow]:
    return [
        _rake_row(fmt, typed[typed["format"] == fmt])
        for fmt in series_mod.FORMATS
        if ((typed["format"] == fmt) & (typed["fee"] > 0)).any()
    ]


def rake_by_field(typed: pd.DataFrame, formats: frozenset[str]) -> list[RakeRow]:
    """The same table by field size, over the GPP formats in `formats` (where field size is the choice)."""
    sub = typed[typed["format"].isin(formats)]
    n = sub["entries"].astype("Float64")
    out = []
    for low, high, label in FIELD_BANDS:
        mask = (n >= low) & ((n <= high) if high is not None else True)
        out.append(_rake_row(label, sub[mask.fillna(False).astype(bool)]))
    return out


@dataclass(frozen=True)
class Opponent:
    name: str
    wins: int
    losses: int
    games: int
    net: float

    @property
    def record(self) -> str:
        return f"{self.wins}-{self.losses}"


def h2h_avoid(opponents: pd.DataFrame) -> list[Opponent]:
    """`h2h_opponents.csv`'s regulars to avoid: winless at 0-3 or worse, or under 30% over 5+ games. Worst
    first."""
    out = []
    for _, r in opponents.iterrows():
        wins, losses, games = int(r["wins"]), int(r["losses"]), int(r["meetings"])
        decided = wins + losses
        winless = wins == 0 and losses >= H2H_AVOID_MIN_LOSSES
        poor = games >= H2H_AVOID_MIN_GAMES and decided > 0 and wins / decided < H2H_AVOID_MAX_RATE
        if winless or poor:
            out.append(Opponent(str(r["opponent"]), wins, losses, games, float(r["net"])))
    return sorted(out, key=lambda o: (o.wins / max(1, o.wins + o.losses), -o.losses, o.name))


def _money(v: float | None) -> str:
    return "-" if v is None else (f"-${-v:,.2f}" if v < 0 else f"${v:,.2f}")


def _pct(v: float | None) -> str:
    return "-" if v is None else f"{v * 100:+.1f}%"


def report_lines(
    typed: pd.DataFrame,
    opponents: pd.DataFrame | None,
    *,
    season: int,
    week: int | None,
    cfg: ContestPlanConfig,
    budget: float | None,
    budget_source: str,
) -> list[str]:
    """The whole `dfs contests plan` report, as plain lines."""
    from dfs.stakes import build_stats

    out = [
        "THIS WEEK'S PLAN, BY FORMAT" + (f"  (budget {_money(budget)}, {budget_source})" if budget else "")
    ]
    out += [f"  {line.text}" for line in plan_lines(cfg, budget)]
    out.append(
        f"  Total: at most {_money(budget)} (the Bankroll tab's Weekly Budget)"
        if budget
        else "  Total: no budget"
    )
    out.append("")

    if week is None:
        out.append(f"LAST WEEK AGAINST THE PLAN: no {season} entries in the log yet")
    else:
        check = check_week(typed, season, week, cfg, budget)
        out.append(f"LAST WEEK AGAINST THE PLAN (Week {week}, from the contest log)")
        caps = {line.bucket: line.cap for line in plan_lines(cfg, budget)}
        for bucket in PLAN_BUCKETS:
            cap = caps[bucket]
            plan = _money(cap) if cap is not None else "-"
            out.append(f"  {bucket:<22} {_money(check.spent[bucket]):>10}   plan {plan}")
        out.append(f"  {check.line()}")
    out.append("")

    out.append(f"ROI BY FORMAT (paid entries)      {season:>18}   all seasons")
    out.append(f"  {'Format':<14} {'entries':>8} {'ROI':>8}   {'entries':>8} {'ROI':>8}")
    for row in build_stats(typed[typed["fee"] > 0], season):
        if row.kind != "format":
            continue
        s, a = row.season, row.overall
        out.append(f"  {row.label:<14} {s.entries:>8} {_pct(s.roi):>8}   {a.entries:>8} {_pct(a.roi):>8}")
    out.append("")

    out.append("EFFECTIVE RAKE  (1 - prize pool / (entrants x fee), median over the contests you played)")
    out.append(f"  {'Format':<14} {'contests':>8} {'rake':>7} {'ROI':>8}")
    for r in rake_by_format(typed):
        out.append(f"  {r.label:<14} {r.contests:>8} {_pct(r.rake).lstrip('+'):>7} {_pct(r.roi):>8}")
    gpp = frozenset({series_mod.SE_GPP, series_mod.N_MAX_GPP})
    out.append("  Classic GPPs by field size:")
    for r in rake_by_field(typed, gpp):
        out.append(
            f"  {r.label:<14} {r.entries:>8} {_pct(r.rake).lstrip('+'):>7} {_pct(r.roi):>8}   (entries)"
        )
    out.append(
        f"  Prefer: cash formats with the lowest rake, and GPPs under {cfg.small_field_entrants:,} entrants."
    )
    out.append("")

    if opponents is None:
        out.append("H2H OPPONENTS TO AVOID: no h2h_opponents.csv yet (`dfs contests update` writes it)")
    else:
        avoid = h2h_avoid(opponents)
        out.append(
            f"H2H OPPONENTS TO AVOID ({len(avoid)}: winless at 0-{H2H_AVOID_MIN_LOSSES} or worse, or under "
            f"{H2H_AVOID_MAX_RATE:.0%} over {H2H_AVOID_MIN_GAMES}+ games)"
        )
        for o in avoid:
            out.append(f"  {o.name:<24} {o.record:>6}  ({o.games} games, net {_money(o.net)})")
    out.append("")
    out.append("Arithmetic on your own log and your own [contest_plan] settings, not advice.")
    return out


def sheet_budget(cfg, *, warn=print) -> tuple[float | None, str]:  # noqa: ANN001 - dfs.config.Config
    """The Bankroll tab's Weekly Budget on the live week's sheet, and where it came from (read only). Kept out
    of `dfs.contests`, which holds no sheet code."""
    from dfs.sheet_stakes import _cell
    from dfs.sheets import SheetsClient, SheetsError
    from dfs.week import BANKROLL_WEEKLY_BUDGET_CELL

    try:
        client = SheetsClient(cfg.google_sheets)
        title, _ = client.describe()
        value = _cell(client, cfg.bankroll.tab, BANKROLL_WEEKLY_BUDGET_CELL)
    except SheetsError as e:
        warn(f"Could not read the Weekly Budget ({e}); planning without a total cap.")
        return None, "no sheet"
    return value, f"{title}'s {cfg.bankroll.tab}!{BANKROLL_WEEKLY_BUDGET_CELL}"


def week_page_payload(
    typed: pd.DataFrame,
    opponents: pd.DataFrame | None,
    *,
    season: int,
    week: int | None,
    cfg: ContestPlanConfig,
    budget: float | None,
) -> dict:
    """What `dfs contests plan` leaves for the Week page (`week_inputs.save_input("contest_plan", ...)`, the
    shape of the page's `CONTEST_PLAN_SHAPE`)."""
    lines = plan_lines(cfg, budget)
    check = check_week(typed, season, week, cfg, budget) if week is not None else None
    roi = [
        {"format": r.label, "entries": r.entries, "roi": r.roi, "rake": r.rake} for r in rake_by_format(typed)
    ]
    return {
        "caps": {line.bucket: line.cap for line in lines},
        "spent": dict(check.spent) if check else {},
        "budget": budget,
        "total_spent": check.total if check else None,
        "over": list(check.over) if check else [],
        "roi": roi,
        "h2h_avoid": [{"opponent": o.name, "record": o.record} for o in h2h_avoid(opponents)]
        if opponents is not None
        else [],
        "line": check.line() if check else "",
        "checked_week": week,
    }
