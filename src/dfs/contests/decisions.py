"""The Monday post-mortem of Sam's decisions: what his own lineups did in the contests he entered.

Model Check grades the projections; nothing graded his choices. Everything here is read from the standings
files (his entries are found by his username, the counter stripped) and each contest's own player table
(`%Drafted`, actual `FPTS`). Nothing is guessed: a measure with no data is left out and the tab shows "-".

Per entry (`entry_rows`): the contest, its class (`cash` for H2H / 50-50 / Double Up, `gpp` for SE and N-max
GPPs; other formats are not graded), his score, place and finish percentile (`place / entrants`, lower is
better), whether it was a paid place, the margin to the cash line, and how many OTHER entries held exactly his
nine players ("copies"). The **contest choice** columns put the same lineup into the other contests of the
same class in the same week that his standings files cover: in how many it would have cashed.

**The swing** (`biggest_swing`): for each player in each of his lineups, the field's most-owned player at the
same position within `SWING_BAND` dollars of his salary (the "alternative"); the swing is his player's actual
points minus the alternative's. The largest swing in either direction is the week's biggest decision.
Salaries and positions come from the week's players frame; ownership and points from the contest's own table.

Pure functions over parsed `Standings`; the analysis (`analysis.run`) collects the inputs and writes the CSVs.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass

import numpy as np
import pandas as pd

from dfs.contests.field import Roster
from dfs.contests.series import CASH_FORMATS, N_MAX_GPP, SE_GPP
from dfs.contests.standings import Standings
from dfs.player_join import normalize_name

SWING_BAND = 400  # dollars either side of his player's salary that count as the same price band
GPP_FORMATS = frozenset({SE_GPP, N_MAX_GPP})

ENTRY_COLUMNS = [
    "contest_id",
    "season",
    "week",
    "series",
    "format",
    "cls",
    "entrants",
    "places_paid",
    "cash_line",
    "points",
    "place",
    "pct",
    "cashed",
    "margin",
    "copies",
    "lineup",
    "sib_total",
    "sib_cashed",
    "sib_best_margin",
]
SWING_COLUMNS = [
    "season",
    "week",
    "contest_id",
    "series",
    "player",
    "player_own",
    "player_pts",
    "alt",
    "alt_own",
    "alt_pts",
    "swing",
]
POOL_HIT_COLUMNS = ["season", "week", "pooled", "pooled_hits", "likes", "likes_hits"]


def format_class(fmt: str) -> str:
    """`cash` / `gpp` for the formats that are graded, else an empty string."""
    if fmt in CASH_FORMATS:
        return "cash"
    return "gpp" if fmt in GPP_FORMATS else ""


def short_series(series: str) -> str:
    """ "NFL GIANT $25 Double Up [Single Entry]" -> "GIANT $25 Double Up"."""
    text = re.sub(r"\s*\[[^\]]*\]\s*$", "", str(series)).strip()
    return re.sub(r"^NFL\s+", "", text)


@dataclass(frozen=True)
class ContestInput:
    """One parsed contest of the week, as the post-mortem needs it."""

    standings: Standings
    season: int
    week: int
    series: str
    format: str
    places_paid: int | None
    players: pd.DataFrame | None  # the week's players frame (Name, Team, Position, Opponent, Salary)


def _lineup_key(slots) -> tuple[str, ...]:
    return tuple(sorted(normalize_name(name) for _, name in slots))


def _place(points: np.ndarray, mine: float) -> int:
    """1 + the entries that scored strictly more (ties share a place)."""
    return 1 + int((points > mine + 1e-9).sum())


def entry_rows(inputs: list[ContestInput], me: str) -> pd.DataFrame:
    """One row per entry of `me` in the graded contests (`ENTRY_COLUMNS`), the contest-choice columns filled
    from the other contests of the same week and class."""
    contests = []
    rows = []
    for c in inputs:
        cls = format_class(c.format)
        s = c.standings
        if not cls or s.kind != "Classic" or s.lineups.empty:
            continue
        points = np.sort(s.lineups["points"].to_numpy(dtype=float))[::-1]
        cash_line = s.profile.cash_line
        contests.append((c, cls, points, cash_line))
        keys = pd.Series([_lineup_key(sl) for sl in s.lineups["slots"]])
        counts = keys.value_counts()
        mine = s.lineups[s.lineups["entry_name"].str.casefold() == me.casefold()]
        for idx, rec in mine.iterrows():
            key = keys.loc[s.lineups.index.get_loc(idx)]
            place = _place(points, float(rec["points"]))
            cashed = bool(c.places_paid) and place <= int(c.places_paid)
            rows.append(
                {
                    "contest_id": s.contest_id,
                    "season": c.season,
                    "week": c.week,
                    "series": c.series,
                    "format": c.format,
                    "cls": cls,
                    "entrants": len(points),
                    "places_paid": c.places_paid,
                    "cash_line": cash_line,
                    "points": float(rec["points"]),
                    "place": place,
                    "pct": place / len(points),
                    "cashed": cashed,
                    "margin": float(rec["points"]) - cash_line if not math.isnan(cash_line) else math.nan,
                    "copies": int(counts[key]) - 1,
                    "lineup": "|".join(key),
                    "sib_total": 0,
                    "sib_cashed": 0,
                    "sib_best_margin": math.nan,
                }
            )
    out = pd.DataFrame(rows, columns=ENTRY_COLUMNS)
    if out.empty:
        return out
    # Contest choice: the same nine players in the other contests of this week and class. A contest he entered
    # with this exact lineup is not a sibling; a contest he entered with a different lineup is.
    for i, rec in out.iterrows():
        used = set(out.loc[(out["week"] == rec["week"]) & (out["lineup"] == rec["lineup"]), "contest_id"])
        total = cashed = 0
        best = math.nan
        for c, cls, points, cash_line in contests:
            if c.week != rec["week"] or c.season != rec["season"] or cls != rec["cls"]:
                continue
            if c.standings.contest_id in used or not c.places_paid or math.isnan(cash_line):
                continue
            total += 1
            cashed += int(_place(points, rec["points"]) <= int(c.places_paid))
            margin = rec["points"] - cash_line
            best = margin if math.isnan(best) else max(best, margin)
        out.loc[i, ["sib_total", "sib_cashed", "sib_best_margin"]] = [total, cashed, best]
    return out


def biggest_swing(inputs: list[ContestInput], me: str) -> pd.DataFrame:
    """The largest swing (in points, either direction) between one of his players and the field's most-owned
    player in the same position and price band, per contest of the week; one row per contest that has one
    (`SWING_COLUMNS`). The caller keeps the largest per week."""
    rows = []
    for c in inputs:
        s = c.standings
        if format_class(c.format) == "" or s.kind != "Classic" or c.players is None or s.ownership.empty:
            continue
        roster = Roster(c.players)
        table = s.ownership.assign(_k=s.ownership["player"].map(normalize_name)).drop_duplicates("_k")
        own = table.set_index("_k")["pct_drafted"].to_dict()
        pts = table.set_index("_k")["fpts"].to_dict()
        shown = table.set_index("_k")["player"].to_dict()
        known = {
            str(p).strip(): pos
            for p, pos in zip(s.ownership["player"], s.ownership["dk_position"], strict=True)
            if pos != "FLEX"
        }
        mine = s.lineups[s.lineups["entry_name"].str.casefold() == me.casefold()]
        # every candidate: (key, position, salary) from the frame, so a band is a filter on a list
        candidates = [
            (normalize_name(p.name), p.position, p.salary)
            for plist in roster.by_name.values()
            for p in plist
            if p.salary is not None
        ]
        best = None
        for slots in mine["slots"]:
            for slot, name in slots:
                player, _ = roster.resolve(name, slot, known.get(name))
                key = normalize_name(name)
                if player is None or player.salary is None or key not in own or key not in pts:
                    continue
                band = [
                    k
                    for k, pos, sal in candidates
                    if pos == player.position
                    and abs(sal - player.salary) <= SWING_BAND
                    and k != key
                    and k in own
                ]
                if not band:
                    continue
                alt = max(band, key=lambda k: (own[k], k))
                if own[alt] <= own[key]:
                    continue  # he IS the field's favourite here: there is no more-owned alternative
                swing = float(pts[key]) - float(pts[alt])
                if best is None or abs(swing) > abs(best["swing"]):
                    best = {
                        "season": c.season,
                        "week": c.week,
                        "contest_id": s.contest_id,
                        "series": c.series,
                        "player": shown[key],
                        "player_own": float(own[key]),
                        "player_pts": float(pts[key]),
                        "alt": shown[alt],
                        "alt_own": float(own[alt]),
                        "alt_pts": float(pts[alt]),
                        "swing": swing,
                    }
        if best is not None:
            rows.append(best)
    return pd.DataFrame(rows, columns=SWING_COLUMNS)


def build(inputs: list[ContestInput], me: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    """`(entries, swings)` for every contest in `inputs`."""
    return entry_rows(inputs, me), biggest_swing(inputs, me)


def pool_hit_row(
    season: int,
    week: int,
    scored: pd.DataFrame,
    pool_names: set[str],
    likes_names: set[str],
) -> dict | None:
    """One `POOL_HIT_COLUMNS` row: of his pooled players and of the model's favourites he left out, how many
    beat their `CalPts` (`DkActual > CalPts`) in the week's scored table. None when the week is not scored."""
    rows = scored[(scored["week"] == week) & (scored["season"] == season)]
    rows = rows[rows["Status"] == "scored"].dropna(subset=["DkActual", "CalPts"])
    if rows.empty:
        return None
    keys = rows["Name"].map(normalize_name)
    beat = rows["DkActual"] > rows["CalPts"]

    def count(names: set[str]) -> tuple[int, int]:
        mask = keys.isin({normalize_name(n) for n in names})
        return int(mask.sum()), int((mask & beat).sum())

    pooled, pooled_hits = count(pool_names)
    likes, likes_hits = count(likes_names)
    return {
        "season": season,
        "week": week,
        "pooled": pooled,
        "pooled_hits": pooled_hits,
        "likes": likes,
        "likes_hits": likes_hits,
    }
