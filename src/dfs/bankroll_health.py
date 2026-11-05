"""Bankroll health for the Week page (Plan K item 1): the curve by week, weekly spend against the bankroll,
and the stake guide from `stakes.py`'s own rules.

**Where the numbers come from.** The Season tab (one row a week: Cash / GPP / Betting / Other net and risked,
the Ending Bankroll; columns found by their header names) and the Bankroll tab's top block (Starting Bankroll,
Weekly Budget, Weekly Cost, Budget, Deposited, % Budget Remaining; each found by its label, the value in the
cell to its right). The Week page reads no sheet, so the commands that already talk to the live sheet leave
them as an input (`save`, fail-soft): `dfs sync`, `dfs contests plan`, `dfs bankroll stakes` and `dfs week
close`.

**The stake guide.** No new rule: the two rules `[stakes]` holds (`max_pct_bankroll_per_week`,
`max_entry_fee_pct`); when Sam has not set them, `stakes.propose`'s values, labelled "proposed" (they come
from his own weekly volatility and the proposal's stated assumptions). The guide a week is that percentage of
the Starting Bankroll, split by tier the way the contest plan splits a budget (`contest_tiers.plan_dollars`).
When the Weekly Budget is above the guide the payload carries one plain sentence saying so (`over`), shown
once.

`payload` is pure; `read_sheet` and `save` are the only I/O.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from dfs import contest_tiers, stakes

# Season tab headers (sheet_season_view.py builds them; found by name, never by letter)
SEASON_HEADERS = {
    "week": "Week",
    "cash_net": "Cash Net",
    "gpp_net": "GPP Net",
    "betting_net": "Betting Net",
    "other_net": "Other Net",
    "total_net": "Total Net",
    "ending": "Ending Bankroll",
    "cash_risked": "Cash Risked",
    "gpp_risked": "GPP Risked",
    "betting_risked": "Betting Risked",
    "other_risked": "Other Risked",
}
# Bankroll tab labels whose value sits in the next cell to the right
BLOCK_LABELS = (
    "Starting Bankroll",
    "Ending Bankroll",
    "Budget",
    "Deposited",
    "Withdrawn",
    "% Budget Remaining",
    "Weekly Budget",
    "Weekly Cost",
)
BLOCK_SCAN = "A1:L20"  # the area the labels are looked for in (labels, not positions, decide)
SERIES = (  # (key, label): the curve's lines, cumulative net by week
    ("dfs", "NFL DFS"),
    ("betting", "Betting"),
    ("other", "Other sports"),
    ("total", "Total"),
)


def number(value: Any) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int | float):
        return None if value != value else float(value)
    text = str(value).replace("$", "").replace(",", "").strip()
    pct = text.endswith("%")
    text = text.rstrip("%").strip()
    neg = text.startswith("(") and text.endswith(")")
    text = text.strip("()")
    try:
        out = float(text)
    except ValueError:
        return None
    out = -out if neg else out
    return out / 100 if pct else out


def season_rows(grid: list[list[Any]]) -> list[dict]:
    """The Season tab's weeks as dicts (`SEASON_HEADERS` keys), from a grid whose first row is the header.
    Only weeks with something in them: a net or an Ending Bankroll."""
    if not grid:
        return []
    header = [str(h).strip() for h in grid[0]]
    at = {key: header.index(name) for key, name in SEASON_HEADERS.items() if name in header}
    if "week" not in at:
        return []
    out = []
    for row in grid[1:]:

        def cell(key: str, row: list = row) -> float | None:
            i = at.get(key)
            return number(row[i]) if i is not None and i < len(row) and row[i] not in ("", None) else None

        week = cell("week")
        if week is None:
            continue
        rec = {key: cell(key) for key in SEASON_HEADERS}
        rec["week"] = int(week)
        filled = [rec[k] for k in ("cash_net", "gpp_net", "betting_net", "other_net", "ending")]
        if all(v is None for v in filled):
            continue
        out.append(rec)
    return out


def block_values(grid: list[list[Any]]) -> dict[str, float]:
    """Each `BLOCK_LABELS` label found in the grid, with the number in the cell to its right."""
    out: dict[str, float] = {}
    for row in grid:
        for i, cell in enumerate(row[:-1]):
            label = str(cell).strip()
            if label in BLOCK_LABELS and label not in out:
                value = number(row[i + 1])
                if value is not None:
                    out[label] = value
    return out


@dataclass(frozen=True)
class Guide:
    source: str  # "your rules" | "proposed" | "none"
    pct_week: float | None  # percent of the bankroll a week
    pct_entry: float | None
    week: float | None  # dollars a week
    entry: float | None
    tiers: dict[str, float] | None  # A / B / C dollars of `week`
    lines: list[str]  # the arithmetic (proposal) or where the rule came from


def stake_guide(cfg: stakes.StakesConfig, typed, bankroll: float | None) -> Guide:  # noqa: ANN001
    """The guide from Sam's `[stakes]` rules, else `stakes.propose`'s values (labelled proposed)."""
    if cfg.max_pct_bankroll_per_week is not None:
        source, pct_week, pct_entry = "your rules", cfg.max_pct_bankroll_per_week, cfg.max_entry_fee_pct
        lines = ["From [stakes] in config.toml."]
    else:
        proposal = stakes.propose(typed, bankroll)
        if proposal.max_pct_bankroll_per_week is None:
            return Guide("none", None, None, None, None, None, proposal.lines)
        source, pct_week, pct_entry = (
            "proposed",
            proposal.max_pct_bankroll_per_week,
            proposal.max_entry_fee_pct,
        )
        lines = proposal.lines
    has = bankroll is not None and bankroll > 0
    week = round(bankroll * pct_week / 100, 2) if has else None
    entry = round(bankroll * pct_entry / 100, 2) if has and pct_entry is not None else None
    tiers = None
    if week is not None:
        plan = contest_tiers.plan_dollars(week)
        tiers = {t: plan.dollars(t) for t in (contest_tiers.A, contest_tiers.B, contest_tiers.C)}
    return Guide(source, pct_week, pct_entry, week, entry, tiers, list(lines))


def curve(rows: list[dict]) -> list[dict]:
    """Cumulative net by week for each series, the Ending Bankroll, and the week's spend as a share of the
    bankroll it started with (the previous week's Ending Bankroll; None for the first week)."""
    out = []
    cum = dict.fromkeys(("dfs", "betting", "other", "total"), 0.0)
    prev_end = None
    for r in sorted(rows, key=lambda r: r["week"]):
        dfs = (r.get("cash_net") or 0.0) + (r.get("gpp_net") or 0.0)
        bet = r.get("betting_net") or 0.0
        other = r.get("other_net") or 0.0
        cum["dfs"] += dfs
        cum["betting"] += bet
        cum["other"] += other
        cum["total"] += dfs + bet + other
        risked = sum(r.get(k) or 0.0 for k in ("cash_risked", "gpp_risked", "betting_risked", "other_risked"))
        out.append(
            {
                "week": r["week"],
                "net": {"dfs": dfs, "betting": bet, "other": other, "total": dfs + bet + other},
                "cum": {k: round(v, 2) for k, v in cum.items()},
                "ending": r.get("ending"),
                "risked": round(risked, 2),
                "start": prev_end,
                "spend_share": (risked / prev_end) if prev_end and prev_end > 0 else None,
            }
        )
        if r.get("ending") is not None:
            prev_end = r["ending"]
    return out


def money(value: float | None) -> str:
    return "-" if value is None else (f"-${-value:,.2f}" if value < 0 else f"${value:,.2f}")


def over_sentence(budget: float | None, bankroll: float | None, guide: Guide) -> str:
    """One plain sentence when the Weekly Budget is above the guide ("" when it is not, or either is
    unknown)."""
    if budget is None or guide.week is None or budget <= guide.week + 1e-9:
        return ""
    times = budget / guide.week if guide.week > 0 else None
    label = "the proposed" if guide.source == "proposed" else "your"
    return (
        f"Your Weekly Budget {money(budget)} is above what a {money(bankroll)} bankroll supports under "
        f"{label} {guide.pct_week:g}%-a-week rule ({money(guide.week)})"
        + (f": {times:.0f} times the guide." if times and times >= 2 else ".")
    )


def payload(
    rows: list[dict], block: dict[str, float], cfg: stakes.StakesConfig, typed, *, sheet: str = ""
) -> dict:  # noqa: ANN001
    """What the Week page shows (pure): the curve, the bankroll block, the guide and the over sentence."""
    bankroll = block.get("Starting Bankroll")
    budget = block.get("Weekly Budget")
    cost = block.get("Weekly Cost")
    guide = stake_guide(cfg, typed, bankroll)
    return {
        "sheet": sheet,
        "curve": curve(rows),
        "block": block,
        "bankroll": bankroll,
        "budget": budget,
        "cost": cost,
        "budget_left": None if budget is None else round(budget - (cost or 0.0), 2),
        "season_left": block.get("% Budget Remaining"),
        "guide": {
            "source": guide.source,
            "pct_week": guide.pct_week,
            "pct_entry": guide.pct_entry,
            "week": guide.week,
            "entry": guide.entry,
            "tiers": guide.tiers,
            "lines": guide.lines,
        },
        "over": over_sentence(budget, bankroll, guide),
    }


# -- I/O ----------------------------------------------------------------------------------------------------


def read_sheet(client, cfg) -> tuple[list[dict], dict[str, float]]:  # noqa: ANN001 - SheetsClient, Config
    """The Season tab's rows and the Bankroll block's labelled values (read only)."""
    from dfs.sheets import column_letter

    season = cfg.season
    width = column_letter(len(SEASON_HEADERS) + 12)
    grid = client.read_range_unformatted(season.tab, f"A{season.header_row}:{width}{season.last_row}")
    block = client.read_range_unformatted(cfg.bankroll.tab, BLOCK_SCAN)
    return season_rows(grid), block_values(block)


def save(client, cfg, *, season: int, week: int) -> bool:  # noqa: ANN001
    """Read the sheet and leave the `bankroll` input for the Week page. Never raises: False when it could
    not."""
    try:
        from dfs import week_inputs
        from dfs.sheet_stakes import load_typed

        rows, block = read_sheet(client, cfg)
        title, _ = client.describe()
        body = payload(rows, block, cfg.stakes, load_typed(), sheet=title)
        return week_inputs.save_input("bankroll", body, season=season, week=week)
    except Exception:  # noqa: BLE001 - a command never fails over the page
        return False


def save_for_config(cfg, *, season: int, week: int) -> bool:  # noqa: ANN001 - Config
    """`save` on config.toml's own sheet (for callers that hold no client). Never raises."""
    try:
        from dfs.sheets import SheetsClient

        return save(SheetsClient(cfg.google_sheets), cfg, season=season, week=week)
    except Exception:  # noqa: BLE001 - a command never fails over the page
        return False
