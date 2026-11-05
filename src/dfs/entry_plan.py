"""The entry plan: which lineup goes into which contest (Plan K item 4).

One view joining three things the tool already has: this week's plan by tier (`contest_tiers.plan_dollars` on
the Weekly Budget), the Suggested builds (`data/suggest_state/<week>.json`), and, once Sam has entered, what
he entered (`data/entered/<season>-wk<NN>.json`, from DraftKings' entries file at lock: every lineup with its
contests and entry ids, and the entries the file held with no lineup yet).

**Checks**, each a plain line (`ok` or the problem):

1. *Kind*: a cash build only in cash contests (H2H, 50/50, Double Up), a GPP build only in GPPs. A lineup of
   his own (not a build) has no kind, so it is not judged.
2. *Entry limits*: no contest holds more of his entries than it allows (`[Single Entry]`, `[3 Entry Max]`, the
   `(2/5)` counter; SE GPPs and H2Hs count as one).
3. *Spend by tier*: the entries' fees by tier (`contest_tiers.classify_entered`) against the plan's dollars.
4. *Every contest has a lineup*: an entry the entries file held empty is named.

**The hint** per contest is `entries.suggest_pick`, the same rule `dfs entries fill` greys in as Suggested:
the cash build with the best Cash % for a cash contest, the GPP build with the best Top 1% for a GPP. It is
labelled **Untested**, not "Best in" (Sam, 2026-11-05: the contest retro found no contest ranking that clears
the bar, and the portfolio plan lost its backtest and is not used here). Sam still uploads to DraftKings
himself.

Pure: dicts in, a dict out.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field

from dfs import contest_tiers, entries
from dfs.contests import series as series_mod

CASH, GPP = "cash", "gpp"


def kind_of_format(fmt: str) -> str:
    return CASH if fmt in series_mod.CASH_FORMATS else GPP


def _num(value) -> float | None:  # noqa: ANN001
    try:
        out = float(str(value).replace("$", "").replace(",", ""))
    except (TypeError, ValueError):
        return None
    return None if out != out else out


@dataclass
class ContestRow:
    contest_id: str
    name: str
    format: str
    fee: float
    entry_max: int | None
    entries: int = 0  # his entries holding a lineup
    blank: int = 0  # his entries the file held with no lineup
    lineups: list[tuple[str, str]] = field(default_factory=list)  # (label, kind or "") per entry
    tier: str = ""
    tier_reason: str = ""
    hint: str = ""
    problems: list[str] = field(default_factory=list)


def contests_from_entered(record: dict) -> list[ContestRow]:
    """Every contest in the entered record, with the lineups in it (one per entry) and its empty entries."""
    rows: dict[str, ContestRow] = {}

    def row(cid: str, name: str, fmt: str, fee) -> ContestRow:  # noqa: ANN001
        if cid not in rows:
            entry_max = contest_tiers.entry_max_from_name(name)
            if entry_max is None and fmt in (series_mod.SE_GPP, series_mod.H2H):
                entry_max = 1
            rows[cid] = ContestRow(cid, name, fmt, _num(fee) or 0.0, entry_max)
        return rows[cid]

    for i, lu in enumerate(record.get("lineups") or [], start=1):
        src = lu.get("source") or {}
        label = lu.get("label") or (
            f"{str(src.get('kind', '')).title()} build {src.get('number')}" if src else f"own #{i}"
        )
        for c in lu.get("contests") or []:
            r = row(
                str(c.get("contest_id")),
                str(c.get("name") or ""),
                str(c.get("format") or ""),
                c.get("entry_fee"),
            )
            n = len(c.get("entry_ids") or []) or 1
            r.entries += n
            r.lineups += [(label, str(src.get("kind") or ""))] * n
    for b in record.get("blank") or []:
        r = row(
            str(b.get("contest_id")), str(b.get("name") or ""), str(b.get("format") or ""), b.get("entry_fee")
        )
        r.blank += len(b.get("entry_ids") or []) or 1
    return list(rows.values())


def build_lineups(builds: Iterable[dict]) -> list[entries.SheetLineup]:
    """The Suggested builds in the shape `entries.suggest_pick` ranks (number = the build's number)."""
    out = []
    for b in builds:
        m = b.get("metrics") or {}
        kind = str(b.get("kind") or "")
        out.append(
            entries.SheetLineup(
                number=int(b.get("number") or 0),
                names=tuple(b.get("names") or ()),
                marker={CASH: entries.CASH_MARKER, GPP: entries.GPP_MARKER}.get(kind, ""),
                cash_pct=_num(m.get("p_cash_shown"))
                if m.get("p_cash_shown") is not None
                else _num(m.get("p_cash")),
                top1=_num(m.get("p_gpp")),
                cal_pts=_num(m.get("projection")),
            )
        )
    return out


def hint_for(row: ContestRow, builds: Sequence[entries.SheetLineup]) -> str:
    if not builds:
        return ""
    entry = entries.Entry(entries.FileEntry("", row.name, row.contest_id, str(row.fee), ()), row.format)
    pick = entries.suggest_pick(entry, builds, frozenset())
    return f"build {pick.lineup}: {pick.why}" if pick.lineup else pick.why


@dataclass
class Plan:
    budget: float | None
    tiers: dict[str, float] | None  # A / B / C dollars
    spent: dict[str, float]
    over: list[str]
    contests: list[ContestRow]
    checks: list[tuple[str, bool]]  # (line, ok)
    builds: list[dict]  # cash / GPP builds with where they belong
    entered: bool
    problems: list[str] = field(default_factory=list)  # every specific problem, kind first


def make(
    *,
    budget: float | None,
    builds: list[dict],
    record: dict | None,
    history: dict[str, dict[str, float]] | None = None,
    avoid: Iterable[str] = (),
    showdown_max: float = contest_tiers.DEFAULT_SHOWDOWN_MAX,
    big_multi_max: float = contest_tiers.DEFAULT_BIG_MULTI_MAX,
) -> Plan:
    tier_plan = (
        contest_tiers.plan_dollars(budget, showdown_max=showdown_max, big_multi_max=big_multi_max)
        if budget
        else None
    )
    tiers = (
        None
        if tier_plan is None
        else {t: tier_plan.dollars(t) for t in (contest_tiers.A, contest_tiers.B, contest_tiers.C)}
    )
    build_rows = [
        {
            "number": b.get("number"),
            "kind": b.get("kind"),
            "goes_in": "cash contests (Tier A: H2H, single-entry double-ups and 50/50s)"
            if b.get("kind") == CASH
            else "GPPs (Tier B: single-entry or 3-max, under 2,500 entrants)",
        }
        for b in builds
    ]
    if not record:
        return Plan(budget, tiers, {}, [], [], [], build_rows, False)
    contests = contests_from_entered(record)
    lineups = build_lineups(builds)
    tiered = contest_tiers.classify_entered(
        [(c.name, c.format, c.fee) for c in contests], history=history or {}, avoid_opponents=avoid
    )
    spend_pairs = []
    checks: list[tuple[str, bool]] = []
    kind_bad, limit_bad, blank_bad = [], [], []
    for c, t in zip(contests, tiered, strict=True):
        c.tier, c.tier_reason = t.verdict.tier, t.verdict.reason
        spend_pairs += [(t.verdict, c.fee)] * (c.entries + c.blank)
        want = kind_of_format(c.format)
        wrong = sorted({label for label, kind in c.lineups if kind in (CASH, GPP) and kind != want})
        if wrong:
            msg = f"{', '.join(wrong)} ({'GPP' if want == CASH else 'cash'}) in {c.name}, a {c.format}"
            c.problems.append(msg)
            kind_bad.append(msg)
        total = c.entries + c.blank
        if c.entry_max is not None and total > c.entry_max:
            msg = f"{c.name}: {total} entries, the contest allows {c.entry_max}"
            c.problems.append(msg)
            limit_bad.append(msg)
        if c.blank:
            msg = f"{c.name}: {c.blank} entr{'y' if c.blank == 1 else 'ies'} with no lineup"
            c.problems.append(msg)
            blank_bad.append(msg)
        c.hint = hint_for(c, lineups)
    spent = dict.fromkeys(contest_tiers.TIERS, 0.0)
    for verdict, fee in spend_pairs:
        spent[verdict.tier] += fee
    over: list[str] = []
    if tier_plan is not None:
        spend = contest_tiers.spend_by_tier(spend_pairs, tier_plan)
        spent, over = dict(spend.spent), list(spend.over)
    checks.append(("Cash builds only in cash contests, GPP builds only in GPPs", not kind_bad))
    checks += [(m, False) for m in kind_bad]
    checks.append(("Single-entry and entry limits respected", not limit_bad))
    checks += [(m, False) for m in limit_bad]
    if tier_plan is None:
        checks.append(("Spend by tier: no Weekly Budget, so no plan to check against", False))
    else:
        checks.append(("Spend by tier within the plan", not over))
        checks += [(f"Over: {o}", False) for o in over]
    checks.append(("Every contest has a lineup", not blank_bad))
    checks += [(m, False) for m in blank_bad]
    problems = kind_bad + limit_bad + [f"Over: {o}" for o in over] + blank_bad
    return Plan(budget, tiers, spent, over, contests, checks, build_rows, True, problems)
