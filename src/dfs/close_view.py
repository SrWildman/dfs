"""The Close view's input (blueprint Phase D; `SPECS_A_TO_D.md` section 5): the closed week on one screen,
built by `week_review` from files only and saved as the week input `close`.

- **The week line** (`cash_line`): his double-up lineup's points against the double-up cash lines, how many
  of his double-ups cashed, the share of the field he beat (the median over his double-ups of `1 - pct` from
  `data/contests/decisions.csv`), the copies of his lineup, and the week's net by ledger group (the contest
  log, `ledger.load`).
- **What decided it** (`decided`): the cash post-mortem's five biggest casher / non-casher gaps, each tagged
  `you had him` (in a cash lineup he entered), `a choice` (in a cash build or the pool, not entered) or
  `variance` (in neither).
- **You against the build** (`vs_default`): `data/contests/scorecard.csv`'s overrides, this week and the
  season. Against "the Default" from the first week Phase A saved one (`cash_core` input), "the nearest build"
  before.
- **The four scorecards** (`scorecard.scorecards`), **the tiles** (edge at lock, avoidable errors = blank
  entries, the calibration gap, the cash percentile by week) and **the one thing** (`one_thing`: the first of
  blueprint 13.3's monthly triggers that fires, else nothing).

Pure: every function takes rows and returns dicts.
"""

from __future__ import annotations

import statistics
from collections.abc import Mapping, Sequence

DOUBLE_UP = "Double Up"
DECIDED_N = 5
HAD, CHOICE, VARIANCE = "you had him", "a choice", "variance"
STREAK = 4  # a scorecard behind its baseline this many weeks running
EDGE_WINDOW, EDGE_BAD = 4, 3  # edge at lock ON PAPER (projected) at or under zero in 3 of the last 4 weeks
CALIBRATION_GAP = 0.10  # the cash chance shown runs this far above the cash rate
FUN_RUN = 3  # the fun line over his number this many weeks running


def _f(v: object) -> float | None:
    try:
        x = float(v)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return None if x != x else x


def _true(v: object) -> bool:
    return str(v).strip().lower() in ("true", "1")


def cash_line(rows: Sequence[Mapping], season: int, week: int) -> dict | None:
    """His double-up entries of the week (`decisions.csv` rows): None when there are none (no standings)."""
    du = [
        r
        for r in rows
        if str(r.get("format")) == DOUBLE_UP and int(r["season"]) == season and int(r["week"]) == week
    ]
    if not du:
        return None
    pts = [float(r["points"]) for r in du]
    lines = [x for r in du if (x := _f(r.get("cash_line"))) is not None]
    copies = [int(float(r.get("copies") or 0)) for r in du]
    beat = 1 - statistics.median(float(r["pct"]) for r in du)
    return {
        "points_lo": round(min(pts), 2),
        "points_hi": round(max(pts), 2),
        "line_lo": round(min(lines), 2) if lines else None,
        "line_hi": round(max(lines), 2) if lines else None,
        "entered": len(du),
        "cashed": sum(_true(r.get("cashed")) for r in du),
        "pct_median": round(beat, 4),
        "copies_lo": min(copies),
        "copies_hi": max(copies),
    }


def percentile_by_week(rows: Sequence[Mapping], season: int, week: int) -> dict[str, float]:
    """The share of the double-up field he beat, by week (the median over that week's double-ups)."""
    weeks: dict[int, list[float]] = {}
    for r in rows:
        if str(r.get("format")) == DOUBLE_UP and int(r["season"]) == season and int(r["week"]) <= week:
            weeks.setdefault(int(r["week"]), []).append(float(r["pct"]))
    return {str(w): round(1 - statistics.median(v), 4) for w, v in sorted(weeks.items())}


def money(week_rows: Sequence[Mapping]) -> tuple[float, dict[str, float]]:
    """(net, net by ledger group) of the week's contest-log rows (`ledger.load`'s `net` and `group`)."""
    by: dict[str, float] = {}
    for r in week_rows:
        by[str(r["group"])] = by.get(str(r["group"]), 0.0) + float(r["net"])
    return round(sum(by.values()), 2), {k: round(v, 2) for k, v in by.items()}


def decided(gaps: Sequence, entered_cash: Sequence[Sequence[str]], n: int = DECIDED_N) -> list[dict]:  # noqa: ANN001
    """The post-mortem's `n` biggest gaps (`cash_postmortem.GapRow`), tagged against what he entered."""
    from dfs.cash_postmortem import IN_BUILDS, POOL_ONLY, key

    had = {key(x) for lineup in entered_cash for x in lineup}
    out = []
    for g in list(gaps)[:n]:
        if key(g.name) in had:
            tag = HAD
        elif g.tag in (IN_BUILDS, POOL_ONLY):
            tag = CHOICE
        else:
            tag = VARIANCE
        out.append(
            {
                "name": g.name,
                "position": g.position,
                "in_cashers": round(g.in_cashers, 4),
                "in_non_cashers": round(g.in_non_cashers, 4),
                "field": round(g.field, 4),
                "cash_own": None if g.cash_own is None else round(float(g.cash_own), 4),
                "tag": tag,
                "detail": g.tag_text,
            }
        )
    return out


def vs_default(card_rows: Sequence[Mapping], season: int, week: int, *, against: str) -> dict:
    """This week's and the season's overrides from `scorecard.csv` (one row per closed week)."""

    def pack(rows: list[Mapping]) -> dict:
        return {
            "lineups": sum(int(float(r.get("lineups") or 0)) for r in rows),
            "overrides": sum(int(float(r.get("overrides") or 0)) for r in rows),
            "gain": round(sum(float(r.get("gain") or 0) for r in rows), 2),
            "loss": round(sum(float(r.get("loss") or 0) for r in rows), 2),
            "weeks": len(rows),
        }

    mine = [r for r in card_rows if int(r["season"]) == season and int(r["week"]) <= week]
    this = [r for r in mine if int(r["week"]) == week]
    return {"against": against, "week": pack(this) if this else None, "season": pack(mine)}


def avoidable(blank_by_week: Mapping[int, int], week: int) -> dict:
    """Blank entries (a contest entered with no lineup), this week and the season's recorded weeks."""
    weeks = {w: n for w, n in blank_by_week.items() if w <= week}
    return {
        "week": weeks.get(week),
        "season": sum(weeks.values()),
        "weeks": len(weeks),
        "what": "blank entries",
    }


def one_thing(
    cards: Sequence[Mapping],
    *,
    projection_gains: Sequence[float],
    ownership_gains: Mapping[str, Sequence[float]],
    edge_by_week: Sequence[float],
    calibration_gap: float | None,
    fun_by_week: Sequence[float],
    fun_per_week: float | None,
) -> str:
    """Blueprint 13.3's monthly triggers, the first that fires (information, nothing more); else nothing."""
    if len(projection_gains) >= STREAK and all(g < 0 for g in projection_gains[-STREAK:]):
        return (
            f"CalPts has trailed TFFB {STREAK} weeks running: the blueprint's line for falling back to the "
            "plain mean of sources (check the sources are fresh first)."
        )
    for fmt, base in (("cash", "the double-up curve"), ("gpp", "TFFB's Own%")):
        g = list(ownership_gains.get(fmt) or [])
        if len(g) >= STREAK and all(x < 0 for x in g[-STREAK:]):
            return (
                f"Our {fmt} ownership has trailed {base} {STREAK} weeks running: the line for using {base}."
            )
    last = list(edge_by_week)[-EDGE_WINDOW:]
    if len(last) == EDGE_WINDOW and sum(x <= 0 for x in last) >= EDGE_BAD:
        return (
            f"Edge at lock, on paper, was at or under zero in {sum(x <= 0 for x in last)} of the last "
            f"{EDGE_WINDOW} weeks (projected, not what happened): check the sources are fresh and the pool "
            "is not stale; do not add a model."
        )
    if calibration_gap is not None and calibration_gap > CALIBRATION_GAP:
        return (
            f"The cash chance shown runs {calibration_gap * 100:.0f} points above the cash rate (the line is "
            f"{CALIBRATION_GAP * 100:.0f}): read the band, not the number."
        )
    if fun_per_week:
        run = list(fun_by_week)[-FUN_RUN:]
        if len(run) == FUN_RUN and all(x > fun_per_week for x in run):
            return (
                f"The Fun line was over your ${fun_per_week:,.0f} {FUN_RUN} weeks running (information only)."
            )
    return "Nothing. No trigger fired this week; most weeks, nothing."


def payload(
    *,
    season: int,
    week: int,
    cash: dict | None,
    net: float | None,
    by_group: Mapping[str, float],
    decided_rows: list[dict],
    vs: dict,
    avoid: dict,
    percentile: Mapping[str, float],
    cards: list[dict],
    edge: dict | None,
    calibration_gap: float | None,
    one: str,
    contests: int,
    entries: int,
    du_entered: int = 0,
) -> dict:
    """The `close` input."""
    return {
        "season": season,
        "week": week,
        "cash": cash,
        "net": net,
        "by_group": dict(by_group),
        "decided": decided_rows,
        "vs_default": vs,
        "avoidable": avoid,
        "percentile": dict(percentile),
        "scorecards": cards,
        "edge": edge,
        "calibration_gap": calibration_gap,
        "one_thing": one,
        "contests_with_standings": contests,
        "entries": entries,
        "du_entered": du_entered,
    }


def _money(x: float) -> str:
    return f"{'-' if x < 0 else '+'}${abs(x):,.0f}" if round(x) else "$0"


def _pts(lo: float, hi: float) -> str:
    return f"{lo:.1f}" if abs(hi - lo) < 0.05 else f"{lo:.1f}-{hi:.1f}"


def week_line(c: Mapping | None) -> str:
    """The Close view's first line (and the review's headline), plain text."""
    if not c:
        return ""
    if not c.get("entries"):
        return "No entries this week."
    net = c.get("net")
    tail = f"week net {_money(float(net))}" if net is not None else ""
    cash = c.get("cash")
    if not cash:
        return " · ".join(x for x in ("No double-up standings for this week, so no line", tail) if x)
    lo, hi = cash["points_lo"], cash["points_hi"]
    head = f"Cash lineup{'s' if abs(hi - lo) >= 0.05 else ''} {_pts(lo, hi)} pts"
    if cash.get("line_lo") is not None:
        head += f" against double-up lines of {_pts(cash['line_lo'], cash['line_hi'])}"
    n = cash["entered"]
    bits = [head, f"{cash['cashed']} of {n} double-up{'s' if n != 1 else ''} cashed"]
    bits.append(
        f"beat {cash['pct_median'] * 100:.0f}% of the field" + (" (the median of them)" if n > 1 else "")
    )
    du = int(c.get("du_entered") or 0)
    if du > n:
        bits.append(f"{n} of {du} double-ups with standings")
    if tail:
        bits.append(tail)
    return " · ".join(bits)


headline = week_line
