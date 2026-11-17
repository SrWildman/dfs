"""`data/contests/h2h_log.csv` (blueprint 13.2 item 5): one row per H2H game, written at its week's close:
`season, week, contest_key, fee, posted_or_joined, opponent, meetings_before, result`.

It holds the opponent's name, so it stays in `data/` (gitignored) and nothing prints it; the ledger shows
counts only. Re-running a close adds nothing new (rows are keyed by contest_key). Append-only.
"""

from __future__ import annotations

import csv
from pathlib import Path

import pandas as pd

from dfs import ledger

COLUMNS = [
    "season",
    "week",
    "contest_key",
    "fee",
    "posted_or_joined",
    "opponent",
    "meetings_before",
    "result",
]


def result_of(win: float, fee: float) -> str:
    """win: paid more than the fee; tie: paid back the fee or part of it; else loss (the ledger's rule)."""
    if win > fee:
        return "win"
    return "tie" if win > 0 else "loss"


def rows_for(log: pd.DataFrame, *, season: int, week: int, posted_or_joined: str = "") -> list[dict]:
    """The week's H2H games, each with how many times Sam had met that opponent in any earlier week."""
    frame = ledger.load(log, through=(season, week))
    h = frame[frame["bucket"] == "H2H"].copy()
    h["Opponent"] = h["Opponent"].fillna("").astype(str).str.strip()
    earlier = h[(h["Season"] < season) | ((h["Season"] == season) & (h["Week"] < week))]
    seen = earlier[earlier["Opponent"] != ""].groupby("Opponent").size()
    out = []
    for _, r in h[(h["Season"] == season) & (h["Week"] == week)].iterrows():
        opp = r["Opponent"]
        out.append(
            {
                "season": season,
                "week": week,
                "contest_key": str(r.get("Contest_Key", "")),
                "fee": round(float(r["fee"]), 2),
                "posted_or_joined": posted_or_joined,
                "opponent": opp,
                "meetings_before": int(seen.get(opp, 0)) if opp else "",
                "result": result_of(float(r["win"]), float(r["fee"])),
            }
        )
    return out


def append(path: Path, log: pd.DataFrame, *, season: int, week: int, posted_or_joined: str = "") -> int:
    """Append the week's games not in the file yet; returns how many were added. Never rewrites a line."""
    new = rows_for(log, season=season, week=week, posted_or_joined=posted_or_joined)
    have: set[str] = set()
    if path.exists():
        with path.open(newline="") as fh:
            have = {row.get("contest_key", "") for row in csv.DictReader(fh)}
    new = [r for r in new if r["contest_key"] and r["contest_key"] not in have]
    if not new:
        return 0
    path.parent.mkdir(parents=True, exist_ok=True)
    first = not path.exists() or path.stat().st_size == 0
    with path.open("a", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=COLUMNS)
        if first:
            writer.writeheader()
        writer.writerows(new)
    return len(new)
