"""How good are our numbers (Plan K item 9): one small panel the Week page's trust chips link to.

- **CalPts against TFFB, by week**: average error (MAE, DK points) of each over the scored players of every
  closed week, from Model Check's own race (`calibration.backtest_by_week`: CalPts refit on the weeks before
  each one, so no week is scored by a model that saw it).
- **Cash % shown against the cash rate**: every distinct cash build of a closed week (any round), the Cash %
  the Suggested tab showed for it, and whether its nine actual scores beat that week's double-up cash line
  (`data/contests/cash_shown.csv`, one row per build, written at the close by `week_review`). Per week and
  overall: the average shown against the share that cashed.
- **Each signal's record**: Model Check's signal table (`results/signal_table_<season>.csv`): times fired,
  hit rate against CalPts, and its trust chip (FADE↓ Proven, every other signal Shadow: scored, not yet a
  verdict), a thin sample said.

Pure except the two readers.
"""

from __future__ import annotations

import csv
from pathlib import Path

import pandas as pd

CASH_COLUMNS = ["season", "week", "label", "shown", "points", "line", "cashed"]
PROVEN_SIGNALS = ("FADE↓",)
BASELINE_PREFIX = "Unflagged "


def projection_by_week(scored: pd.DataFrame) -> list[dict]:
    """[{week, n, calpts, tffb}] average errors, oldest week first ([] before any scored week)."""
    from dfs import calibration

    if scored is None or scored.empty:
        return []
    trend = calibration.backtest_by_week(scored, shadow=calibration.configured_shadow())
    if trend.empty:
        return []
    out = []
    for week, part in trend.groupby("Week"):
        by = part.set_index("Source")
        if "CalPts" not in by.index or "TFFB" not in by.index:
            continue
        out.append(
            {
                "week": int(week),
                "n": int(by.loc["TFFB", "n"]),
                "calpts": float(by.loc["CalPts", "MAE"]),
                "tffb": float(by.loc["TFFB", "MAE"]),
            }
        )
    return sorted(out, key=lambda r: r["week"])


def cash_calibration(rows: list[dict]) -> dict:
    """Per week and overall: builds, the average Cash % shown, the share that cashed."""
    usable = [r for r in rows if r.get("shown") not in (None, "") and r.get("cashed") not in (None, "")]

    def summary(part: list[dict]) -> dict:
        shown = [float(r["shown"]) for r in part]
        cashed = [str(r["cashed"]).lower() in ("true", "1") for r in part]
        return {
            "n": len(part),
            "shown": sum(shown) / len(shown),
            "cashed": sum(cashed) / len(cashed),
        }

    weeks: dict[int, list[dict]] = {}
    for r in usable:
        weeks.setdefault(int(r["week"]), []).append(r)
    return {
        "weeks": [{"week": w, **summary(p)} for w, p in sorted(weeks.items())],
        "all": summary(usable) if usable else None,
    }


def signal_rows(table: list[dict]) -> list[dict]:
    out = []
    for r in table:
        name = str(r.get("Signal", ""))
        n = int(float(r.get("n") or 0))
        if not name or name.startswith(BASELINE_PREFIX) or n == 0:
            continue
        hit = r.get("HitCal") if r.get("HitCal") not in (None, "") else r.get("HitProj")
        out.append(
            {
                "signal": name,
                "n": n,
                "hit": None if hit in (None, "") else float(hit),
                "vs_cal": None if r.get("VsCal") in (None, "") else float(r["VsCal"]),
                "thin": str(r.get("Thin", "")).lower() == "true",
                "trust": "Proven" if name.startswith(PROVEN_SIGNALS) else "Shadow",
            }
        )
    return sorted(out, key=lambda r: (r["trust"] != "Proven", -r["n"], r["signal"]))


def payload(scored: pd.DataFrame | None, cash_rows: list[dict], signal_table: list[dict]) -> dict:
    return {
        "projection": projection_by_week(scored) if scored is not None else [],
        "cash": cash_calibration(cash_rows),
        "signals": signal_rows(signal_table),
    }


def headline(p: dict | None) -> str:
    if not p:
        return ""
    bits = []
    proj = p.get("projection") or []
    if proj:
        better = sum(r["calpts"] < r["tffb"] for r in proj)
        bits.append(f"CalPts beat TFFB's error in {better} of {len(proj)} weeks")
    cash = (p.get("cash") or {}).get("all")
    if cash:
        bits.append(
            f"Cash % shown {cash['shown'] * 100:.0f}% vs {cash['cashed'] * 100:.0f}% cashed (n={cash['n']})"
        )
    sig = p.get("signals") or []
    if sig:
        bits.append(f"{len(sig)} signals scored")
    return "; ".join(bits)


# -- the files
# -------------------------------------------------------------------------------------------------


def read_csv(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open(newline="") as f:
        return list(csv.DictReader(f))


def replace_week(path: Path, season: int, week: int, rows: list[dict]) -> None:
    """Write `rows` as that season-week's (a rerun replaces them); other weeks are kept."""
    kept = [
        r for r in read_csv(path) if (str(r.get("season")), str(r.get("week"))) != (str(season), str(week))
    ]
    allrows = kept + [{c: r.get(c, "") for c in CASH_COLUMNS} for r in rows]
    allrows.sort(key=lambda r: (int(r["season"]), int(r["week"]), str(r["label"])))
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=CASH_COLUMNS)
        w.writeheader()
        w.writerows(allrows)
