"""What won last week, and how the field builds (Plan K item 6).

From the closed week's standings (the downloads `week close` keeps) and each contest's field measures
(`contests.field.field_stats`, the same measures `field_stats.csv` holds), by contest type (the contest log's
Format: Double Up, 50/50, SE GPP, N-max GPP, ...):

- **The winning lineup and the cash-line lineup** of the type's largest contest: first place, and the lineup
  in the last paid place (`Places_Paid`). Each with its points, its ownership sum (the nine players'
  `%Drafted` in that contest, as fractions), its salary left and its stack shape.
- **Field tendencies**, pooled over every lineup of every contest of the type: % with a QB stack (the QB and
  one or more of his WR / TE, `field.py`'s definition), % with two or more, % with a bring-back (any player
  from the QB's opponent), the average salary left, the share with a RB in the FLEX (three RBs), and the
  top stacks (the QB teams of the stacked lineups).
- **Sam's lineups** against the same numbers (his own rows in the same standings).

Pure: `review` takes `Contest`s built by `week_review` from `sim.contest_retro.RealContest`.
"""

from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass

import pandas as pd

from dfs.player_join import normalize_name
from dfs.sim import payouts

CASH_FORMATS = payouts.FLAT_FORMATS  # double-ups, 50/50s, multipliers: a flat cash line

TOP_STACKS = 3


@dataclass(frozen=True)
class Contest:
    contest_id: str
    series: str
    format: str
    entries: int
    places_paid: int
    features: pd.DataFrame  # field.field_stats(...).features: one row per lineup
    own: dict[str, float]  # normalised name -> %Drafted (fraction)


def _mean(values: pd.Series) -> float | None:
    v = pd.to_numeric(values, errors="coerce").dropna()
    return None if v.empty else float(v.mean())


def shape(row: dict | pd.Series) -> str:
    """`LAR QB + 2 WR/TE, bring-back` / `no QB stack`."""
    s1, s2 = row.get("stack1"), row.get("stack2")
    team = str(row.get("qb_team") or "")
    if s1 != s1 or s1 is None:  # NaN: the lineup did not resolve
        return "unknown"
    if not s1:
        return "no QB stack" + (", bring-back" if row.get("bringback") == 1.0 else "")
    out = f"{team} QB + {'2+' if s2 else '1'} WR/TE"
    return out + (", bring-back" if row.get("bringback") == 1.0 else "")


def lineup_view(row: pd.Series, own: dict[str, float]) -> dict:
    names = [n for n in str(row.get("lineup_key", "")).split("|") if n]
    shares = [own.get(normalize_name(n)) for n in names]
    known = [s for s in shares if s is not None]
    unspent = row.get("unspent")
    return {
        "names": names,
        "points": float(row["points"]),
        "own_sum": sum(known) if known else None,
        "own_known": len(known),
        "salary_left": None if unspent is None or unspent != unspent else float(unspent),
        "shape": shape(row),
    }


def tendencies(features: pd.DataFrame) -> dict:
    """The pooled field measures of a set of lineups (see the module docstring)."""
    if features.empty:
        return {"n": 0}
    resolved = features[features["stack1"].notna()]
    stacked = resolved[resolved["qb_team"].astype(str) != ""]
    tops = Counter(stacked["qb_team"].astype(str))
    n_res = len(resolved)
    return {
        "n": int(len(features)),
        "n_resolved": int(n_res),
        "stack": _mean(resolved["stack1"]),
        "stack2": _mean(resolved["stack2"]),
        "bringback": _mean(resolved["bringback"]),
        "salary_left": _mean(features["unspent"]),
        "flex_rb": float((features["flex_pos"] == "RB").mean()) if len(features) else None,
        "top_stacks": [(team, c / n_res) for team, c in tops.most_common(TOP_STACKS)] if n_res else [],
    }


def _ranked(features: pd.DataFrame) -> pd.DataFrame:
    return features.sort_values("points", ascending=False, kind="mergesort").reset_index(drop=True)


def review(contests: list[Contest], me: str, week: int) -> dict:
    """The page's payload: one block per contest type, the cash types first."""
    by_format: dict[str, list[Contest]] = {}
    for c in contests:
        by_format.setdefault(c.format, []).append(c)
    blocks = []
    for fmt, group in by_format.items():
        big = max(group, key=lambda c: (c.entries, c.contest_id))
        ranked = _ranked(big.features)
        winner = lineup_view(ranked.iloc[0], big.own) if len(ranked) else None
        place = min(max(1, big.places_paid), len(ranked))
        line = lineup_view(ranked.iloc[place - 1], big.own) if len(ranked) and big.places_paid else None
        pooled = pd.concat([c.features for c in group], ignore_index=True)
        mine = pooled[pooled["entry_name"].astype(str).str.strip().str.lower() == me.lower()]
        mine_view = None
        if len(mine):
            mine_view = tendencies(mine)
            rows = []
            for c in group:
                m = c.features[c.features["entry_name"].astype(str).str.strip().str.lower() == me.lower()]
                for _, r in m.iterrows():
                    rows.append(lineup_view(r, c.own))
            sums = [r["own_sum"] for r in rows if r["own_sum"] is not None]
            mine_view["own_sum"] = sum(sums) / len(sums) if sums else None
            mine_view["entries"] = len(rows)
        field_sums = [
            sum(c.own.get(normalize_name(n), 0.0) for n in str(k).split("|") if n)
            for c in group
            for k in c.features["lineup_key"]
        ]
        field = tendencies(pooled)
        field["own_sum"] = sum(field_sums) / len(field_sums) if field_sums else None
        blocks.append(
            {
                "format": fmt,
                "cash": fmt in CASH_FORMATS,
                "contests": len(group),
                "largest": {
                    "series": big.series,
                    "entries": big.entries,
                    "places_paid": big.places_paid,
                },
                "winner": winner,
                "cash_line": line,
                "field": field,
                "mine": mine_view,
            }
        )
    blocks.sort(key=lambda b: (not b["cash"], -b["largest"]["entries"], b["format"]))
    return {"week": week, "formats": blocks}


def headline(payload: dict | None) -> str:
    """One line for the collapsed section."""
    if not payload or not payload.get("formats"):
        return ""
    gpp = next((b for b in payload["formats"] if not b["cash"] and b.get("winner")), None)
    cash = next((b for b in payload["formats"] if b["cash"] and b.get("cash_line")), None)
    bits = [f"Week {payload['week']}"]
    if gpp:
        w = gpp["winner"]
        own = f", {w['own_sum'] * 100:.0f}% owned" if w.get("own_sum") is not None else ""
        bits.append(f"{gpp['format']} won with {w['points']:.1f} ({w['shape']}{own})")
    if cash:
        bits.append(f"{cash['format']} line {cash['cash_line']['points']:.1f}")
    return "; ".join(bits)


def pct(x: float | None) -> str:
    return "-" if x is None or (isinstance(x, float) and math.isnan(x)) else f"{x * 100:.0f}%"
