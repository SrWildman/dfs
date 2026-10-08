"""Injury beneficiaries: when a skill player is out, who inherits his targets, carries and expected points,
and has TFFB's projection already moved?

Pure and offline-testable (dataframes in, dataframes out); `signals.py` gathers the inputs.

**Who is out.** `classify_status`: DraftKings' `Avail` (`OUT`/`IR`, or `Q` for questionable) decides when
it is set; the nflverse report fills in when it is blank (`Out` and `Doubtful` count as out,
`Questionable` as questionable). The `Avail` column on the sheet is DraftKings' status. Questionable
players are listed SEPARATELY (muted), never mixed into the confirmed list.

**What he vacates.** His last `VACATED_WINDOW` games played: targets, carries and expected points per
game (and red-zone opportunities when the play-by-play is available).

**Who gets it, in order of preference.**

1. *With-or-without*: at least `WITH_WITHOUT_MIN_GAMES` (2) games this or last season, with the SAME team,
   in which he missed and a teammate played. Each teammate's gain is his per-game average without the
   out player minus with him, clipped at 0 and scaled so the teammates' total never exceeds what the out
   player vacated (`conserve`). A game counts as missed only between his first game with the team and
   now. (The plan said "at least 1 game"; on real data a single game gave gains 3-10x the vacated
   volume, so Sam moved it to 2 and added the cap, 2026-10-07.)
2. *Depth chart*: the latest snapshot at or before the slate's first kickoff. The next `pos_rank` at his
   position gets `NEXT_UP_SHARE` (60%) of what he vacated; the rest is split among the same-position
   teammates by current share. Rules by position:

   - WR out (TE out is the mirror): of the 40% that is not the next-up's, `SPILL_SHARE` (25%) spills to
     the other pass-catching group (TEs for a WR) by current target share and the rest goes to the other
     WRs. (The plan's "~60% of a WR1's targets go to the WR2" is the next-up's share of the total; the
     spill is read as a share of the remainder. An empty bucket hands its weight to the others.)
   - RB out: carries go to RBs only (next-up 60%, the rest by carry share); targets split
     `RB_TARGET_TO_RB_SHARE` (50%) to RBs (same next-up rule) and the rest to the pass catchers (WR + TE)
     by current target share.
   - receiving expected points follow the target split, rushing expected points follow the carry split.
   - A QB out is not redistributed (nothing in the plan covers it); he is still listed as out.

   Players who are themselves out are never beneficiaries.

**Priced in?** `priced_in`: yes when TFFB's `ProjPts` for the beneficiary rose by at least
`PRICED_IN_SHARE` (70%) of the gained xFP between the last snapshot BEFORE the out designation appeared
and now. When that cannot be established (the designation is only on the nflverse report, or there is no
earlier snapshot) the answer is "unknown", never a guess.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from dfs.derived import OUT_STATUSES

VACATED_WINDOW = 3
NEXT_UP_SHARE = 0.6
SPILL_SHARE = 0.25
RB_TARGET_TO_RB_SHARE = 0.5
PRICED_IN_SHARE = 0.7
# With-or-without needs this many missed games (Sam, 2026-10-07: on real data a single missed game
# produced gains 3-10x what the out player vacated); with fewer the depth chart decides.
WITH_WITHOUT_MIN_GAMES = 2
# A beneficiary is tagged `INJ+` once the gain is worth at least this many expected points per game. A
# starting value, not fitted to anything; Model Check keeps score on the token.
INJ_MIN_GAINED_XFP = 1.0
TOKEN_INJ = "INJ+"

STATUS_OUT = "out"
STATUS_QUESTIONABLE = "questionable"
REPORT_OUT = {"out", "doubtful"}
REPORT_QUESTIONABLE = {"questionable"}
AVAIL_QUESTIONABLE = {"Q"}
AVAIL_DOUBTFUL = {"D"}  # DraftKings' own spelling of Doubtful; counts as out, like the report's

METHOD_WITH_WITHOUT = "with-or-without"
METHOD_DEPTH = "depth chart"
PRICED_YES, PRICED_NO, PRICED_UNKNOWN = "yes", "no", "unknown"

PASS_CATCHERS = {"WR", "TE"}
RECEIVING_GROUPS = {"WR": "TE", "TE": "WR"}  # who a WR's/TE's spill goes to
GAIN_COLUMNS = ["tgt_gain", "car_gain", "xfp_gain"]


def classify_status(avail: object, report_status: object) -> str | None:
    """`out`, `questionable` or None. DraftKings' `Avail` wins when it says anything."""
    avail_text = (
        "" if avail is None or (isinstance(avail, float) and np.isnan(avail)) else str(avail).strip().upper()
    )
    if avail_text in OUT_STATUSES or avail_text in AVAIL_DOUBTFUL:
        return STATUS_OUT
    if avail_text in AVAIL_QUESTIONABLE:
        return STATUS_QUESTIONABLE
    report = (
        ""
        if report_status is None or (isinstance(report_status, float) and np.isnan(report_status))
        else str(report_status).strip().lower()
    )
    if report in REPORT_OUT:
        return STATUS_OUT
    if report in REPORT_QUESTIONABLE:
        return STATUS_QUESTIONABLE
    return None


@dataclass(frozen=True)
class Vacated:
    """What one out player leaves behind, per game (his last `VACATED_WINDOW` games played)."""

    tgt_g: float
    car_g: float
    rec_xfp_g: float
    rush_xfp_g: float
    games: int
    rz_g: float = float("nan")

    @property
    def xfp_g(self) -> float:
        """Receiving plus rushing expected points per game (passing xFP is not redistributed)."""
        return self.rec_xfp_g + self.rush_xfp_g


def latest_depth(depth: pd.DataFrame | None, cutoff: str | None) -> pd.DataFrame:
    """The depth-chart rows at the latest snapshot `dt` at or before `cutoff` (an ISO UTC stamp such as
    `2026-10-11T17:00:00Z`; None = the latest snapshot there is). Empty when none qualifies. Snapshots are
    compared as strings, which sorts ISO stamps correctly."""
    if depth is None or depth.empty:
        return pd.DataFrame(columns=["dt", "Team", "GsisId", "Name", "Position", "pos_rank"])
    dts = depth["dt"].astype(str)
    eligible = dts if cutoff is None else dts[dts <= cutoff]
    if eligible.empty:
        return depth.iloc[0:0]
    return depth[dts == eligible.max()]


def depth_order(
    depth_rows: pd.DataFrame, team: str, position: str, tiebreak: pd.Series | None = None
) -> list[str]:
    """Gsis ids of `team`'s `position` players in depth-chart order: best `pos_rank` first, each once.

    nflverse's chart lists a player once per formation group, and each group has its own rank 1, so two
    players can share a `pos_rank` (found live: SEA's rank-1 RBs were both Wilson, the starter, and Russell,
    a fullback). Ties are broken by `tiebreak` (current volume per game, indexed by gsis id; larger first),
    then by id so the order is deterministic."""
    if depth_rows.empty:
        return []
    part = depth_rows[(depth_rows["Team"] == team) & (depth_rows["Position"] == position)]
    best = part.groupby("GsisId", as_index=False)["pos_rank"].min()
    volume = pd.Series(0.0, index=best["GsisId"]) if tiebreak is None else tiebreak.reindex(best["GsisId"])
    best["_volume"] = volume.fillna(0.0).to_numpy()
    ordered = best.sort_values(["pos_rank", "_volume", "GsisId"], ascending=[True, False, True])
    return ordered["GsisId"].tolist()


def _by_share(members: pd.DataFrame, column: str) -> pd.Series:
    """Fractions summing to 1 across `members` in proportion to `column` (equal when nobody has any)."""
    weight = members[column].fillna(0.0).astype(float)
    total = weight.sum()
    if total <= 0:
        return pd.Series(1.0 / len(members), index=members.index)
    return weight / total


def _next_up_split(members: pd.DataFrame, column: str, order: list[str]) -> pd.Series:
    """Fractions summing to 1: the first member in depth-chart `order` (else the biggest current share) gets
    `NEXT_UP_SHARE`, the rest share the remainder by `column`; a lone member gets everything."""
    if len(members) == 1:
        return pd.Series(1.0, index=members.index)
    ranked = [g for g in order if g in members.index]
    next_up = ranked[0] if ranked else members[column].fillna(0.0).idxmax()
    rest = members.drop(index=next_up)
    out = pd.Series(0.0, index=members.index)
    out[next_up] = NEXT_UP_SHARE
    out[rest.index] = (1 - NEXT_UP_SHARE) * _by_share(rest, column)
    return out


def depth_gains(
    out_position: str,
    vacated: Vacated,
    teammates: pd.DataFrame,
    order_by_position: dict[str, list[str]],
) -> pd.DataFrame:
    """Per-teammate gained `tgt_gain`, `car_gain`, `xfp_gain` per game by the depth-chart rules.

    `teammates` is indexed by gsis id with `Position`, `tgt_g`, `car_g` (current per-game volume).
    `order_by_position` maps a position to its gsis ids in depth order. Players who are out must already
    be excluded by the caller."""
    gains = pd.DataFrame(0.0, index=teammates.index, columns=GAIN_COLUMNS)
    by_pos = {p: teammates[teammates["Position"] == p] for p in ("RB", "WR", "TE")}

    def give_targets(fraction: pd.Series) -> None:
        gains.loc[fraction.index, "tgt_gain"] += fraction * vacated.tgt_g
        gains.loc[fraction.index, "xfp_gain"] += fraction * vacated.rec_xfp_g

    def give_carries(fraction: pd.Series) -> None:
        gains.loc[fraction.index, "car_gain"] += fraction * vacated.car_g
        gains.loc[fraction.index, "xfp_gain"] += fraction * vacated.rush_xfp_g

    if out_position in RECEIVING_GROUPS:
        own, other = by_pos[out_position], by_pos[RECEIVING_GROUPS[out_position]]
        remainder = 1 - NEXT_UP_SHARE
        weights = {
            "next_up": NEXT_UP_SHARE if not own.empty else 0.0,
            "rest": remainder * (1 - SPILL_SHARE) if len(own) > 1 else 0.0,
            "other": remainder * SPILL_SHARE if not other.empty else 0.0,
        }
        total = sum(weights.values())
        if total > 0:
            order = order_by_position.get(out_position, [])
            ranked = [g for g in order if g in own.index]
            next_up = (ranked[0] if ranked else own["tgt_g"].fillna(0.0).idxmax()) if not own.empty else None
            fraction = pd.Series(0.0, index=teammates.index)
            if next_up is not None:
                fraction[next_up] += weights["next_up"] / total
                rest = own.drop(index=next_up)
                if not rest.empty:
                    fraction[rest.index] += weights["rest"] / total * _by_share(rest, "tgt_g")
            if not other.empty:
                fraction[other.index] += weights["other"] / total * _by_share(other, "tgt_g")
            give_targets(fraction[fraction > 0])
    elif out_position == "RB":
        backs = by_pos["RB"]
        catchers = pd.concat([by_pos["WR"], by_pos["TE"]])
        order = order_by_position.get("RB", [])
        if not backs.empty:
            give_carries(_next_up_split(backs, "car_g", order))
        rb_part = RB_TARGET_TO_RB_SHARE if not catchers.empty and not backs.empty else float(not backs.empty)
        if not backs.empty:
            give_targets(rb_part * _next_up_split(backs, "tgt_g", order))
        if not catchers.empty:
            give_targets((1 - rb_part) * _by_share(catchers, "tgt_g"))
    return gains


def with_without_gains(
    history: pd.DataFrame, out_gsis: str, team: str, *, before: tuple[int, int], exclude: set[str]
) -> tuple[pd.DataFrame, int]:
    """Per-teammate per-game change (`tgt_gain`, `car_gain`, `xfp_gain`) in games `out_gsis` MISSED against
    games he played, with the same team, strictly before `before` = (season, week). Returns
    (gains indexed by gsis id, the number of games he missed that a teammate played). Empty with 0 when he
    has no such game.

    `history` is `xfp.player_weeks` over this and last season (`GsisId`, `Team`, `season`, `week`,
    `targets`, `carries`, `xfp`). A game counts as missed only once he has played for `team` (his first
    game with it is the start of the window), so a rookie's pre-debut weeks are not absences."""
    hist = history[(history["Team"] == team)].copy()
    hist["_key"] = list(zip(hist["season"].astype(int), hist["week"].astype(int), strict=True))
    hist = hist[hist["_key"] < before]
    mine = hist[hist["GsisId"] == out_gsis]
    if mine.empty:
        return pd.DataFrame(columns=GAIN_COLUMNS), 0
    start = min(mine["_key"])
    team_games = sorted(set(hist["_key"]))
    played = set(mine["_key"])
    absent = [k for k in team_games if k >= start and k not in played]
    if not absent or not played:
        return pd.DataFrame(columns=GAIN_COLUMNS), 0
    mates = hist[(hist["GsisId"] != out_gsis) & ~hist["GsisId"].isin(exclude) & (hist["_key"] >= start)]
    without = mates[mates["_key"].isin(absent)]
    with_him = mates[mates["_key"].isin(played)]
    columns = {"targets": "tgt_gain", "carries": "car_gain", "xfp": "xfp_gain"}
    means_without = without.groupby("GsisId")[list(columns)].mean()
    means_with = with_him.groupby("GsisId")[list(columns)].mean()
    both = means_without.index.intersection(means_with.index)
    gains = (means_without.loc[both] - means_with.loc[both]).rename(columns=columns)
    n_missed = len({k for k in absent if (without["_key"] == k).any()})
    return gains, n_missed


def conserve(gains: pd.DataFrame, vacated: Vacated) -> pd.DataFrame:
    """Scale each gain column down so the teammates' total never exceeds what the out player vacated
    (targets, carries, xFP per game): opportunity is moved, not created. Gains already within the limit
    are untouched."""
    limits = {"tgt_gain": vacated.tgt_g, "car_gain": vacated.car_g, "xfp_gain": vacated.xfp_g}
    out = gains.copy()
    for column, limit in limits.items():
        total = out[column].sum()
        if total > limit and total > 0:
            out[column] = out[column] * (max(limit, 0.0) / total)
    return out


def priced_in(projection_now: float, projection_before: float | None, gained_xfp: float) -> str:
    """`yes` when TFFB's projection rose by at least `PRICED_IN_SHARE` of the gained xFP since the last
    snapshot before the out designation; `no` when it rose less; `unknown` when either projection or the
    gain is missing (or the gain is not positive)."""
    values = (projection_now, projection_before, gained_xfp)
    if any(v is None or (isinstance(v, float) and np.isnan(v)) for v in values) or gained_xfp <= 0:
        return PRICED_UNKNOWN
    return PRICED_YES if (projection_now - projection_before) >= PRICED_IN_SHARE * gained_xfp else PRICED_NO


def vacated_from_window(window_row: pd.Series, rz_g: float = float("nan")) -> Vacated:
    """A `Vacated` from an `xfp.xfp_windows` row."""
    return Vacated(
        tgt_g=float(window_row["tgt_g"]),
        car_g=float(window_row["car_g"]),
        rec_xfp_g=float(window_row["rec_xfp_g"]),
        rush_xfp_g=float(window_row["rush_xfp_g"]),
        games=int(window_row["Games"]),
        rz_g=rz_g,
    )


BENEFICIARY_COLUMNS = [
    "GsisId",
    "Name",
    "Team",
    "Position",
    "OutStatus",
    "OutPlayers",
    "tgt_gain",
    "car_gain",
    "xfp_gain",
    "Method",
    "n",
    "Token",
]


def beneficiaries(
    outs: pd.DataFrame,
    windows: pd.DataFrame,
    identity: pd.DataFrame,
    history: pd.DataFrame,
    depth_rows: pd.DataFrame,
    *,
    before: tuple[int, int],
) -> pd.DataFrame:
    """Every beneficiary of every out or questionable player, one row per beneficiary and status.

    - `outs`: `GsisId`, `Name`, `Team`, `Position`, `Status` (`out`/`questionable`).
    - `windows`: `xfp.xfp_windows` (indexed by gsis id), the current per-game volume of everyone.
    - `identity`: `GsisId`, `Name`, `Team`, `Position` of every skill player the windows cover.
    - `history`: `xfp.player_weeks` for this and last season (with-or-without).
    - `depth_rows`: `latest_depth` output.
    - `before`: (season, week) of the slate; only earlier games count.

    A beneficiary hit by several outs sums the gains; `OutPlayers` names them; `Method` is that of the
    biggest contribution. Rows are sorted by `xfp_gain`, largest first. A confirmed-out list never
    contains a player who is out himself; questionable beneficiaries are computed against the CONFIRMED
    outs only (a questionable player is assumed to play)."""
    if outs is None or outs.empty:
        return pd.DataFrame(columns=BENEFICIARY_COLUMNS)
    people = identity.drop_duplicates(subset="GsisId").set_index("GsisId")
    confirmed = set(outs.loc[outs["Status"] == STATUS_OUT, "GsisId"])
    rows = []
    for _, out in outs.iterrows():
        gsis, team, position = out["GsisId"], out["Team"], out["Position"]
        if position not in {"WR", "TE", "RB"} or gsis not in windows.index:
            continue
        vacated = vacated_from_window(windows.loc[gsis])
        mates_ids = people.index[(people["Team"] == team) & (people["Position"].isin(PASS_CATCHERS | {"RB"}))]
        mates_ids = [g for g in mates_ids if g != gsis and g not in confirmed]
        mates = people.loc[mates_ids, ["Name", "Team", "Position"]].join(
            windows[["tgt_g", "car_g"]], how="left"
        )
        if mates.empty:
            continue
        gains, n_missed = with_without_gains(history, gsis, team, before=before, exclude=confirmed)
        if n_missed >= WITH_WITHOUT_MIN_GAMES:
            method, n = METHOD_WITH_WITHOUT, n_missed
            gains = conserve(gains.reindex(mates.index).dropna(how="all").clip(lower=0.0), vacated)
        else:
            volume = windows["tgt_g"].fillna(0.0) + windows["car_g"].fillna(0.0)
            order = {p: depth_order(depth_rows, team, p, volume) for p in ("RB", "WR", "TE")}
            gains = depth_gains(position, vacated, mates, order)
            method, n = METHOD_DEPTH, 0
        for gsis_b, g in gains.iterrows():
            if g["xfp_gain"] <= 0 and g["tgt_gain"] <= 0 and g["car_gain"] <= 0:
                continue
            rows.append(
                {
                    "GsisId": gsis_b,
                    "Name": mates.loc[gsis_b, "Name"],
                    "Team": team,
                    "Position": mates.loc[gsis_b, "Position"],
                    "OutStatus": out["Status"],
                    "OutPlayers": out["Name"],
                    "tgt_gain": g["tgt_gain"],
                    "car_gain": g["car_gain"],
                    "xfp_gain": g["xfp_gain"],
                    "Method": method,
                    "n": n,
                }
            )
    if not rows:
        return pd.DataFrame(columns=BENEFICIARY_COLUMNS)
    frame = pd.DataFrame(rows)
    agg = frame.groupby(["GsisId", "OutStatus"], as_index=False).agg(
        Name=("Name", "first"),
        Team=("Team", "first"),
        Position=("Position", "first"),
        OutPlayers=("OutPlayers", lambda s: ", ".join(dict.fromkeys(s))),
        tgt_gain=("tgt_gain", "sum"),
        car_gain=("car_gain", "sum"),
        xfp_gain=("xfp_gain", "sum"),
        Method=("Method", "first"),
        n=("n", "max"),
    )
    agg["Token"] = np.where(agg["xfp_gain"] >= INJ_MIN_GAINED_XFP, TOKEN_INJ, "")
    return agg.sort_values("xfp_gain", ascending=False)[BENEFICIARY_COLUMNS].reset_index(drop=True)
