"""The ledger (blueprint Phase B): Sam's own contest results by format, what each costs in rake, how sure the
record is, and this week's money by group. Pure: every input is a frame or a dict the caller read
(`contests.history.read_log`, `entered.load`); nothing here reads a file, a sheet or the network.

**Formats** are the thirteen ground-truth buckets (`BUCKETS`), ported line for line from the ground-truth
pack's `gt_common.bucket` (the acceptance is "to the dollar"): Discord first (a Private contest or "discord"
in the name, paid or free), then free, then Showdown, then any other non-Classic game type, then by the log's
own `series.classify` format; an N-max GPP splits on the "[N Entry Max]" in its name (under 20, 20 and up, or
no max in the name).

**Groups** (`GROUP`): `cash` (H2H, Double-up, 50/50), `small_gpp` (SE GPP), `fun` (Showdown, 3-max/5-max,
20-max and up, N-max with no max in the name, Discord with a fee), `free` (Free, and Discord at $0) and
`other` (Multiplier, Other Classic GPP, Other game types: one grey line, so the ledger adds up to the log).

**The record** per format: entries, weeks, fees, net, ROI = net / fees, the cash rate and the ROI's standard
error by **resampling whole season-weeks** (`cluster_se`: 2,000 draws, `numpy.random.default_rng(0)`, the
population SD of net over fees; entries in one week share a lineup, so they are not independent). `None`
with fewer than two weeks. Rake = the median of `1 - prize pool / (entries x fee)` over the paid entries.

**H2H**: an opponent met `regular_at` (5) or more times in the seasons shown is a regular. Counts only: no
name is in the payload (the names stay in the log and in `data/contests/h2h_log.csv`). A game with no
opponent in the contest name is counted apart ("unnamed"), as the ground truth does.

Nothing here caps, warns or judges: the numbers are shown, the decisions are Sam's.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Any

import numpy as np
import pandas as pd

BUCKETS: tuple[str, ...] = (
    "H2H",
    "Double-up",
    "50/50",
    "SE GPP",
    "3-max/5-max",
    "20-max and up",
    "N-max, max not in name",
    "Other Classic GPP",
    "Multiplier",
    "Discord",
    "Showdown",
    "Free",
    "Other game types",
)
CASH, SMALL_GPP, FUN, FREE, OTHER = "cash", "small_gpp", "fun", "free", "other"
GROUPS: tuple[str, ...] = (CASH, SMALL_GPP, FUN, FREE, OTHER)
GROUP_LABEL = {CASH: "Cash", SMALL_GPP: "Small GPP", FUN: "Fun", FREE: "Free", OTHER: "Other"}
GROUP: dict[str, str] = {
    "H2H": CASH,
    "Double-up": CASH,
    "50/50": CASH,
    "SE GPP": SMALL_GPP,
    "3-max/5-max": FUN,
    "20-max and up": FUN,
    "N-max, max not in name": FUN,
    "Showdown": FUN,
    "Discord": FUN,  # a paid one; at $0 it is free (`group_of`)
    "Free": FREE,
    "Multiplier": OTHER,
    "Other Classic GPP": OTHER,
    "Other game types": OTHER,
}
SEASONS_SHOWN = 5  # this season and the four before it (2022-26 in 2026)
SE_DRAWS = 2000
REGULAR_AT = 5

_MONEY = re.compile(r"[$,]")
_MAX = re.compile(r"\[(\d+)\s*Entry Max\]", flags=re.I)


def money(s: pd.Series) -> pd.Series:
    return pd.to_numeric(s.astype(str).str.replace(_MONEY, "", regex=True), errors="coerce")


def contest_max(entry: str) -> float:
    m = _MAX.search(str(entry))
    return float(m.group(1)) if m else np.nan


def bucket_of(game_type: str, fmt: str, entry_name: str, fee: float) -> str:
    """One entry's format bucket (`gt_common.bucket`, line for line)."""
    name = str(entry_name).lower()
    if fmt == "Private" or "discord" in name:
        return "Discord"
    if fee == 0:
        return "Free"
    if fmt == "Showdown":
        return "Showdown"
    if game_type != "Classic":
        return "Other game types"
    if fmt == "H2H":
        return "H2H"
    if fmt == "Double Up":
        return "Double-up"
    if fmt == "50/50":
        return "50/50"
    if fmt == "SE GPP":
        return "SE GPP"
    if fmt == "Multiplier":
        return "Multiplier"
    if fmt == "N-max GPP":
        cm = contest_max(entry_name)
        if np.isnan(cm):
            return "N-max, max not in name"
        return "3-max/5-max" if cm < 20 else "20-max and up"
    return "Other Classic GPP"


def group_of(bucket: str, fee: float) -> str:
    """The bucket's group; a free Discord contest is free, a paid one fun."""
    if bucket == "Discord" and not fee:
        return FREE
    return GROUP[bucket]


def load(log: pd.DataFrame, *, through: tuple[int, int] | None = None) -> pd.DataFrame:
    """The contest log (`history.read_log`, text) with numbers and the bucket (`gt_common.load`, ported).
    `through=(season, week)` drops anything after that week (and a row of that season with no week)."""
    df = log.copy()
    for c in ["Winnings_Non_Ticket", "Winnings_Ticket", "Entry_Fee", "Prize_Pool", "Contest_Entries"]:
        df[c] = money(df[c])
    df["Season"] = pd.to_numeric(df["Season"], errors="coerce")
    df["Week"] = pd.to_numeric(df["Week"], errors="coerce")
    df = df[df["Season"].notna()].copy()
    df["Season"] = df["Season"].astype(int)
    if through is not None:
        season, week = through
        keep = (df["Season"] < season) | ((df["Season"] == season) & (df["Week"] <= week))
        df = df[keep].copy()
    df["fee"] = df["Entry_Fee"].fillna(0)
    df["win"] = df["Winnings_Non_Ticket"].fillna(0) + df["Winnings_Ticket"].fillna(0)
    df["net"] = df["win"] - df["fee"]
    df["cashed"] = df["win"] > 0
    df["field"] = df["Contest_Entries"]
    df["bucket"] = [
        bucket_of(g, f, n, fee)
        for g, f, n, fee in zip(df["Game_Type"], df["Format"], df["Entry"], df["fee"], strict=True)
    ]
    df["group"] = [group_of(b, fee) for b, fee in zip(df["bucket"], df["fee"], strict=True)]
    df["rake"] = 1 - df["Prize_Pool"] / (df["field"] * df["fee"])
    df["wk"] = df["Season"].astype(str) + "-" + df["Week"].fillna(0).astype(int).astype(str).str.zfill(2)
    return df


def cluster_se(frame: pd.DataFrame, *, draws: int = SE_DRAWS, seed: int = 0) -> float | None:
    """The ROI's standard error in points, resampling whole season-weeks (`gt_common.cluster_se`). None with
    fewer than two weeks or no fees."""
    agg = frame.groupby("wk").agg(net=("net", "sum"), fee=("fee", "sum"))
    if len(agg) < 2 or agg["fee"].sum() == 0:
        return None
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, len(agg), (draws, len(agg)))
    nets, fees = agg["net"].to_numpy()[idx].sum(1), agg["fee"].to_numpy()[idx].sum(1)
    ok = fees > 0
    return float(np.std(nets[ok] / fees[ok]) * 100)


def _r(x: float | None, nd: int) -> float | None:
    return None if x is None or (isinstance(x, float) and np.isnan(x)) else round(float(x), nd)


def record(frame: pd.DataFrame) -> dict[str, Any]:
    """n, weeks, fees, net, ROI, SE (points) and the cash rate of `frame`'s entries."""
    fees, net = float(frame["fee"].sum()), float(frame["net"].sum())
    return {
        "n": len(frame),
        "weeks": int(frame["wk"].nunique()),
        "fees": round(fees, 2),
        "net": round(net, 2),
        "roi": round(net / fees * 100, 1) if fees else None,
        "se": _r(cluster_se(frame), 1),
        "cash": round(float(frame["cashed"].mean()) * 100, 1) if len(frame) else None,
    }


def shown(frame: pd.DataFrame, season: int) -> pd.DataFrame:
    """The seasons the ledger shows: `season` and the `SEASONS_SHOWN - 1` before it."""
    return frame[frame["Season"].between(season - SEASONS_SHOWN + 1, season)]


def rows(frame: pd.DataFrame, season: int) -> list[dict]:
    """One row per bucket that has an entry in the seasons shown, in `BUCKETS` order."""
    seen = shown(frame, season)
    out = []
    for b in BUCKETS:
        g = seen[seen["bucket"] == b]
        if g.empty:
            continue
        this = g[g["Season"] == season]
        paid = g[(g["fee"] > 0) & (g["field"] > 0)]["rake"].dropna()
        by_week = this.groupby(this["Week"].fillna(0).astype(int))["net"].sum()
        by_season = g.groupby("Season")["net"].sum()
        out.append(
            {
                "format": b,
                "group": GROUP[b] if b != "Discord" else (FUN if (g["fee"] > 0).any() else FREE),
                "rake": round(float(paid.median()), 3) if len(paid) else None,
                "all": record(g),
                "season": record(this),
                "by_week": {str(int(k)): round(float(v), 2) for k, v in by_week.items()},
                "by_season": {str(int(k)): round(float(v), 2) for k, v in by_season.items()},
            }
        )
    return out


def _h2h_counts(g: pd.DataFrame) -> dict[str, int]:
    wins = int((g["win"] > g["fee"]).sum())
    ties = int(((g["win"] > 0) & (g["win"] <= g["fee"])).sum())
    return {
        "opponents": int(g["Opponent"].nunique()),
        "games": len(g),
        "wins": wins,
        "losses": len(g) - wins - ties,
        "ties": ties,
    }


def h2h(frame: pd.DataFrame, season: int, *, regular_at: int = REGULAR_AT) -> dict:
    """Sam's H2H record against regulars (met `regular_at`+ times in the seasons shown) and everyone else,
    over the seasons shown and this season. Counts only."""
    h = shown(frame, season)
    h = h[h["bucket"] == "H2H"].copy()
    opp = h["Opponent"].fillna("").astype(str).str.strip()
    h["Opponent"] = opp
    named = h[opp != ""]
    counts = named.groupby("Opponent").size()
    regulars = set(counts[counts >= regular_at].index)
    kind = np.where(opp == "", "unnamed", np.where(opp.isin(regulars), "regular", "other"))

    def split(g: pd.DataFrame, k: np.ndarray) -> dict:
        out = {}
        for name in ("regular", "other", "unnamed"):
            part = g[k == name]
            out[name] = _h2h_counts(part)
            if name == "unnamed":
                out[name]["opponents"] = 0
        return out

    this = (h["Season"] == season).to_numpy()
    return {"all": split(h, kind), "season": split(h[this], kind[this]), "regular_at": regular_at}


def _fee(text: object) -> float:
    try:
        return float(str(text).replace("$", "").replace(",", "") or 0)
    except ValueError:
        return 0.0


def entered_by_group(record_: dict | None) -> dict[str, float]:
    """This week's fees by group from the entered record (`data/entered/`): each lineup's contests (fee x
    entries), the contests the file held with no lineup yet, and `skipped_fees` (a Showdown or another
    slate's entries, format -> dollars)."""
    out = dict.fromkeys(GROUPS, 0.0)
    if not record_:
        return out

    def add(fmt: str, name: str, fee: float, n: int) -> None:
        g = group_of(bucket_of("Classic", fmt, name, fee), fee)
        out[g] += fee * n

    for lu in record_.get("lineups") or []:
        for c in lu.get("contests") or []:
            add(
                str(c.get("format", "")),
                str(c.get("name", "")),
                _fee(c.get("entry_fee")),
                len(c.get("entry_ids") or []),
            )
    for c in record_.get("blank") or []:
        add(
            str(c.get("format", "")),
            str(c.get("name", "")),
            _fee(c.get("entry_fee")),
            len(c.get("entry_ids") or []),
        )
    for fmt, dollars in (record_.get("skipped_fees") or {}).items():
        fee = float(dollars or 0)
        bucket = "Showdown" if fmt in ("Showdown", "Showdown file") else "Other game types"
        out[group_of(bucket, fee)] += fee
    return {k: round(v, 2) for k, v in out.items()}


def payload(
    log: pd.DataFrame,
    *,
    season: int,
    week: int,
    plan: Any = None,  # contest_tiers.TierPlan | None
    fun_per_week: float | None = None,
    entered: dict | None = None,
) -> dict:
    """The week input `ledger` (SPECS_A_TO_D.md section 3), through `season` / `week`."""
    frame = load(log, through=(season, week))
    entered_g = entered_by_group(entered)
    fun_season = frame[(frame["Season"] == season) & (frame["group"] == FUN)]
    planned = {
        CASH: round(plan.a, 2) if plan is not None else None,
        SMALL_GPP: round(plan.b, 2) if plan is not None else None,
        FUN: fun_per_week,
    }
    groups = {
        g: {"planned": planned[g], "entered": entered_g[g] if entered else None}
        for g in (CASH, SMALL_GPP, FUN)
    }
    groups[FUN]["season_net"] = round(float(fun_season["net"].sum()), 2)
    groups[FUN]["season_fees"] = round(float(fun_season["fee"].sum()), 2)
    hh = h2h(frame, season)
    hh["this_week"] = (entered or {}).get("h2h", "") or ""
    seen = shown(frame, season)
    other = seen[seen["bucket"].map(GROUP) == OTHER]
    return {
        "through": {"season": season, "week": week},
        "seasons": list(range(season - SEASONS_SHOWN + 1, season + 1)),
        "rows": rows(frame, season),
        # Multiplier, Other Classic GPP and Other game types as one grey line on the page
        "other": {
            "formats": [b for b in BUCKETS if GROUP[b] == OTHER and (other["bucket"] == b).any()],
            "all": record(other),
            "season": record(other[other["Season"] == season]),
        },
        "groups": groups,
        "h2h": hh,
        "recorded": entered is not None,
    }


def h2h_sentence(h: dict, seasons: Sequence[int]) -> str:
    """ "H2H: against 11 repeat opponents (5+ meetings, 2022-26) you are 25-40 (1-14 this season); against
    everyone else 363-308-2 (82-78 this season)." Counts only."""

    def rec(c: dict) -> str:
        return f"{c['wins']}-{c['losses']}" + (f"-{c['ties']}" if c["ties"] else "")

    span = f"{seasons[0]}-{str(seasons[-1])[-2:]}" if seasons else ""
    a, s = h["all"], h["season"]
    text = (
        f"H2H: against {a['regular']['opponents']} repeat opponents ({h.get('regular_at', REGULAR_AT)}+ "
        f"meetings, {span}) you are {rec(a['regular'])} ({rec(s['regular'])} this season); against everyone "
        f"else {rec(a['other'])} ({rec(s['other'])} this season)."
    )
    if a["unnamed"]["games"]:
        text += f" {a['unnamed']['games']} games had no opponent in the contest name."
    return text
