"""Injury beneficiaries: when a regular is out, what do we actually know about who inherits his volume?

Pure and offline-testable (dataframes in, dataframes out); `signals.py` gathers the inputs. Rebuilt on the
research pack's measured table (`docs/RESEARCH.md` R1, `models/research/`), replacing the hand-set "next man
up gets 60%" rule, which the research showed is wrong for targets and only roughly right for carries.

**Who is out.** `classify_status`: DraftKings' `Avail` (`OUT`/`IR`/`D`, or `Q` for questionable) decides
when it is set; the nflverse report fills in when it is blank (`Out` and `Doubtful` count as out,
`Questionable` as questionable). The sheet's `Avail` is DraftKings' status. Questionable players are listed
SEPARATELY (muted).

**Regulars only.** The research counted an absence only when the player was a regular: his share of the
team's targets at least 0.15, or of its carries at least 0.3 (`research_constants.regular_thresholds`), over
his prior 3 games. Here that is his last 3 games PLAYED (`rotation_window`; Sam, 2026-10-08), and he must
have missed no more than `MAX_GAMES_MISSED` = 2 team games since: a back out for a month is old news (his
team's usage has moved on and his replacements already carry the load), so he is neither listed nor
redistributed. A bit-part's absence is no event either. Roles (RB1, RB2, WR1...) are the research's too:
ranks by usage in the window (`position_order`), not the depth chart.

**Carries (a back is out): the measured table.** `research_constants.carry_shares` gives the fractions of the
VACATED carries that went to each teammate historically: with RB1 out, RB2 gets 0.47, each further back 0.22
and 0.19 goes to nobody in the rotation. **The unassigned share stays unassigned**; the beneficiaries'
shares are normalised only if they would sum above `1 - unassigned`. An RB2 out uses its own cells (RB1
0.52, each RB3+ 0.20, unassigned 0.14); an RB3+ out has no cell (n=2) and is not redistributed. The gained
rushing xFP follows the carries (his rushing xFP per carry); that is what `INJ+` reads.

**With-or-without** (Sam's ruling stands): when he missed at least `WITH_WITHOUT_MIN_GAMES` (2) games with
the same team that a teammate played, the teammates' per-game CARRY change replaces the table (clipped at 0
and scaled so the total never exceeds what he vacated, `conserve`).

**Targets (any pass catcher is out): no redistribution.** Out of sample the 60% rule predicted targets worse
than doing nothing; the learned table only ties it. So a target absence never produces points, never fires
`INJ+`, and is listed as CONTEXT (`absences`): who is out, what he vacated, the teammates' with-or-without
split when it exists (display only) and the historical line "no single teammate gains much: WR2 +13% of the
vacated targets, ~27% goes nowhere".

**Priced in?** `priced_in`: yes when TFFB's `ProjPts` for the beneficiary rose by at least `PRICED_IN_SHARE`
(70%) of the gained xFP between the last snapshot BEFORE the out designation appeared and now. It is BLANK
whenever that cannot be established (the designation predates this slate's first snapshot, or is only on the
nflverse report): nothing is guessed.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from dfs.derived import OUT_STATUSES
from dfs.research_constants import (
    CarryShares,
    carry_shares,
    regular_thresholds,
    target_context,
    target_context_line,
)

PRICED_IN_SHARE = 0.7
# With-or-without needs this many missed games (Sam, 2026-10-07: on real data a single missed game
# produced gains 3-10x what the out player vacated).
WITH_WITHOUT_MIN_GAMES = 2
# A carry beneficiary is tagged `INJ+` once the gain is worth at least this many rushing expected points per
# game. A starting value, not fitted to anything; Model Check keeps score on the token.
INJ_MIN_GAINED_XFP = 1.0
TOKEN_INJ = "INJ+"

STATUS_OUT = "out"
STATUS_QUESTIONABLE = "questionable"
REPORT_OUT = {"out", "doubtful"}
REPORT_QUESTIONABLE = {"questionable"}
AVAIL_QUESTIONABLE = {"Q"}
AVAIL_DOUBTFUL = {"D"}  # DraftKings' own spelling of Doubtful; counts as out, like the report's

METHOD_WITH_WITHOUT = "with-or-without"
METHOD_TABLE = "measured table"
PRICED_YES, PRICED_NO, PRICED_UNKNOWN = "yes", "no", ""  # blank, not "unknown": see `priced_in`

GAIN_COLUMNS = ["tgt_gain", "car_gain", "xfp_gain"]
NO_DEPTH_RANK = 99


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
    """What one out player leaves behind, per game (his last 3 games played)."""

    tgt_g: float
    car_g: float
    rec_xfp_g: float
    rush_xfp_g: float
    games: int

    @property
    def xfp_g(self) -> float:
        """Receiving plus rushing expected points per game (passing xFP is not redistributed)."""
        return self.rec_xfp_g + self.rush_xfp_g

    @property
    def rush_xfp_per_carry(self) -> float:
        """His rushing expected points per carry (0 with no carries)."""
        return self.rush_xfp_g / self.car_g if self.car_g > 0 else 0.0


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


# ---------------------------------------------------------------------------------------------
# The prior window, roles and regulars (the research's definitions, R1, with Sam's ruling on "out")
# ---------------------------------------------------------------------------------------------

WINDOW_GAMES = 3  # a player's window: his last 3 games played for the team (the research's prior 3)
MAX_GAMES_MISSED = 2  # a regular who has missed 3+ team games is old news: his team's usage has moved on
WINDOW_COLUMNS = [
    "Name",
    "Position",
    "Games",
    "tgt_g",
    "car_g",
    "rec_xfp_g",
    "rush_xfp_g",
    "tgt_share",
    "car_share",
    "LastKey",
    "Missed",
    "InRotation",
]


def rotation_window(
    history: pd.DataFrame, team: str, *, before: tuple[int, int], n: int = WINDOW_GAMES
) -> pd.DataFrame:
    """Every player who has played for `team` before `before` = (season, week), indexed by gsis id, over his
    last `n` games played for it (across seasons): per-game means of targets, carries and expected points, his
    share of the team's targets and carries in THOSE games, `Missed` (team games since his last appearance)
    and
    `InRotation` (he played in the team's last `n` games: the research's rotation, "anyone with a touch in the
    window"). A game he missed is not a zero here (Sam, 2026-10-08): a back who sat out two games is still
    judged on the games he played; `MAX_GAMES_MISSED` is what retires a long absence."""
    hist = history[history["Team"] == team].copy()
    hist["_key"] = list(zip(hist["season"].astype(int), hist["week"].astype(int), strict=True))
    hist = hist[hist["_key"] < before]
    if hist.empty:
        return pd.DataFrame(columns=WINDOW_COLUMNS)
    team_keys = sorted(set(hist["_key"]))
    team_totals = hist.drop_duplicates("_key").set_index("_key")[["team_targets", "team_carries"]]
    recent_keys = set(team_keys[-n:])
    rows = []
    for gsis, games in hist.sort_values("_key").groupby("GsisId"):
        last = games.tail(n)
        totals = team_totals.loc[list(last["_key"])].sum()
        count = len(last)
        rows.append(
            {
                "GsisId": gsis,
                "Name": last["Name"].iloc[-1],
                "Position": last["Position"].iloc[-1],
                "Games": count,
                "tgt_g": last["targets"].mean(),
                "car_g": last["carries"].mean(),
                "rec_xfp_g": last["rec_xfp"].mean(),
                "rush_xfp_g": last["rush_xfp"].mean(),
                "tgt_share": last["targets"].sum() / totals["team_targets"]
                if totals["team_targets"] > 0
                else np.nan,
                "car_share": last["carries"].sum() / totals["team_carries"]
                if totals["team_carries"] > 0
                else np.nan,
                "LastKey": last["_key"].iloc[-1],
                "Missed": sum(1 for k in team_keys if k > last["_key"].iloc[-1]),
                "InRotation": last["_key"].iloc[-1] in recent_keys,
            }
        )
    return pd.DataFrame(rows).set_index("GsisId")[WINDOW_COLUMNS]


def vacated_from_row(row: pd.Series) -> Vacated:
    """What one player leaves per game, from his `rotation_window` row."""
    return Vacated(
        tgt_g=float(row["tgt_g"]),
        car_g=float(row["car_g"]),
        rec_xfp_g=float(row["rec_xfp_g"]),
        rush_xfp_g=float(row["rush_xfp_g"]),
        games=int(row["Games"]),
    )


def regular_kinds(window_row: pd.Series) -> list[str]:
    """Which of `targets` / `carries` make him a regular: his share of the team's targets over his last
    `WINDOW_GAMES` games played at least the research's 0.15, or of its carries at least 0.3 (empty = a
    bit-part, or someone who has missed `MAX_GAMES_MISSED`+1 or more team games: not an event)."""
    if window_row.get("Missed", 0) > MAX_GAMES_MISSED:
        return []
    th = regular_thresholds()
    kinds = []
    if pd.notna(window_row.get("tgt_share")) and window_row["tgt_share"] >= th.target_share:
        kinds.append("targets")
    if pd.notna(window_row.get("car_share")) and window_row["car_share"] >= th.carry_share:
        kinds.append("carries")
    return kinds


def position_order(
    window: pd.DataFrame, position: str, depth_rows: pd.DataFrame, team: str, include: set[str] = frozenset()
) -> list[str]:
    """The team's rotation at `position`, best first, ranked as the research ranked it (R1 `rank_rotation`):
    by usage per game in the window, which is carries + targets for a back and targets for a receiver or tight
    end (carries break a tie), then the depth chart, then id. The rotation is whoever played in the team's
    last 3 games, plus anyone in `include` (the out player himself, who may have missed them). Out players are
    INCLUDED: this is the pecking order the measured fractions were learned on. (The depth chart is not the
    primary key: it drops an injured starter to the bottom, which would make a 44%-of-the-carries back an
    "RB3+".)"""
    part = window[(window["Position"] == position) & (window["InRotation"] | window.index.isin(include))]
    usage = part["tgt_g"] + part["car_g"] if position == "RB" else part["tgt_g"] + 1e-6 * part["car_g"]
    chart = {
        g: i for i, g in enumerate(depth_order(depth_rows, team, position, part["tgt_g"] + part["car_g"]))
    }
    return sorted(part.index, key=lambda g: (-float(usage[g]), chart.get(g, NO_DEPTH_RANK), g))


def role_label(position: str, rank: int) -> str:
    """`RB1`, `RB2`, `RB3+`, `WR1`..`WR3`, `WR4+`, `TE1`, `TE2+` from a 1-based rank within his team."""
    cap = {"RB": 3, "WR": 4, "TE": 2}.get(position, 1)
    return f"{position}{rank}" if rank < cap else f"{position}{cap}+"


# ---------------------------------------------------------------------------------------------
# With-or-without
# ---------------------------------------------------------------------------------------------


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


def games_missed(history: pd.DataFrame, gsis: str, team: str, *, before: tuple[int, int]) -> int:
    """Team games since his last appearance for `team` before `before` (0 = he played the last one)."""
    hist = history[history["Team"] == team]
    keys = list(zip(hist["season"].astype(int), hist["week"].astype(int), strict=True))
    team_games = sorted({k for k in keys if k < before})
    mine = sorted({k for k, g in zip(keys, hist["GsisId"], strict=True) if g == gsis and k < before})
    if not mine:
        return len(team_games)
    return sum(1 for k in team_games if k > mine[-1])


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


# ---------------------------------------------------------------------------------------------
# Carries
# ---------------------------------------------------------------------------------------------


def table_shares(remaining: list[str], shares: CarryShares) -> pd.Series:
    """Each remaining back's fraction of the vacated carries: the first (best) gets `next_up`, every other
    `each_other`. The unassigned share is never handed out: the fractions are scaled down together only when
    they would sum above `1 - unassigned`."""
    values = [shares.next_up if i == 0 else shares.each_other for i in range(len(remaining))]
    fractions = pd.Series(values, index=remaining, dtype=float)
    cap = 1.0 - shares.unassigned
    total = fractions.sum()
    if total > cap > 0:
        fractions = fractions * (cap / total)
    return fractions


def carry_gains(vacated: Vacated, fractions: pd.Series) -> pd.DataFrame:
    """Per-back gained carries and rushing xFP per game from the measured fractions (no target gain)."""
    car = fractions * vacated.car_g
    return pd.DataFrame(
        {"tgt_gain": 0.0, "car_gain": car, "xfp_gain": car * vacated.rush_xfp_per_carry},
        index=fractions.index,
    )


def priced_in(projection_now: float, projection_before: float | None, gained_xfp: float) -> str:
    """`yes` when TFFB's projection rose by at least `PRICED_IN_SHARE` of the gained xFP since the last
    snapshot before the out designation; `no` when it rose less; BLANK when either projection or the gain is
    missing (or the gain is not positive): the question has no answer yet."""
    values = (projection_now, projection_before, gained_xfp)
    if any(v is None or (isinstance(v, float) and np.isnan(v)) for v in values) or gained_xfp <= 0:
        return PRICED_UNKNOWN
    return PRICED_YES if (projection_now - projection_before) >= PRICED_IN_SHARE * gained_xfp else PRICED_NO


BENEFICIARY_COLUMNS = [
    "GsisId",
    "Name",
    "Team",
    "Position",
    "OutStatus",
    "OutPlayers",
    "car_gain",
    "xfp_gain",
    "Method",
    "n",
    "Token",
]
ABSENCE_COLUMNS = [
    "GsisId",
    "Name",
    "Team",
    "Position",
    "Role",
    "Regular",
    "tgt_g",
    "car_g",
    "GamesMissed",
    "WithWithoutGames",
    "WithWithout",
    "History",
]


def _confirmed(outs: pd.DataFrame) -> set[str]:
    return set(outs.loc[outs["Status"] == STATUS_OUT, "GsisId"]) if not outs.empty else set()


def beneficiaries(
    outs: pd.DataFrame,
    history: pd.DataFrame,
    depth_rows: pd.DataFrame,
    *,
    before: tuple[int, int],
) -> pd.DataFrame:
    """Who inherits the CARRIES of every out or questionable regular back, one row per beneficiary and status.

    - `outs`: `GsisId`, `Name`, `Team`, `Position`, `Status` (`out`/`questionable`).
    - `history`: `xfp.player_weeks` for this and last season (the prior window and with-or-without).
    - `depth_rows`: `latest_depth` output (a tiebreak in the rotation order only).
    - `before`: (season, week) of the slate; only earlier games count.

    Backs only: a quarterback's scrambles or a receiver's end-around are not "inheriting the backfield".
    Targets are never redistributed (see the module docstring). A back hit by several outs sums the gains;
    `OutPlayers` names them. Rows are sorted by `xfp_gain`, largest first. A player who is out himself is
    never a beneficiary; questionable beneficiaries are computed against the CONFIRMED outs only."""
    if outs is None or outs.empty or history is None or history.empty:
        return pd.DataFrame(columns=BENEFICIARY_COLUMNS)
    confirmed = _confirmed(outs)
    rows = []
    for _, out in outs.iterrows():
        gsis, team = out["GsisId"], out["Team"]
        if out["Position"] != "RB":
            continue
        window = rotation_window(history, team, before=before)
        if gsis not in window.index or "carries" not in regular_kinds(window.loc[gsis]):
            continue
        vacated = vacated_from_row(window.loc[gsis])
        order = position_order(window, "RB", depth_rows, team, {gsis})
        label = role_label("RB", order.index(gsis) + 1)
        shares = carry_shares(label)
        remaining = [g for g in order if g != gsis and g not in confirmed]
        gains, n_missed = with_without_gains(history, gsis, team, before=before, exclude=confirmed)
        if n_missed >= WITH_WITHOUT_MIN_GAMES:
            method, n = METHOD_WITH_WITHOUT, n_missed
            backs = history[(history["Team"] == team) & (history["Position"] == "RB")]["GsisId"].unique()
            mates = [g for g in backs if g != gsis and g not in confirmed]  # carries go to backs only
            car = conserve(gains.reindex(mates).dropna(how="all").clip(lower=0.0), vacated)["car_gain"]
            table = pd.DataFrame(
                {"tgt_gain": 0.0, "car_gain": car, "xfp_gain": car * vacated.rush_xfp_per_carry},
                index=car.index,
            )
        elif shares is not None and remaining:
            method, n = METHOD_TABLE, 0
            table = carry_gains(vacated, table_shares(remaining, shares))
        else:
            continue
        names = history.sort_values(["season", "week"]).groupby("GsisId")["Name"].last()
        for gsis_b, g in table.iterrows():
            if g["car_gain"] <= 0:
                continue
            rows.append(
                {
                    "GsisId": gsis_b,
                    "Name": names.get(gsis_b, gsis_b),
                    "Team": team,
                    "Position": "RB",
                    "OutStatus": out["Status"],
                    "OutPlayers": out["Name"],
                    "car_gain": g["car_gain"],
                    "xfp_gain": g["xfp_gain"],
                    "Method": method,
                    "n": n,
                }
            )
    if not rows:
        return pd.DataFrame(columns=BENEFICIARY_COLUMNS)
    agg = (
        pd.DataFrame(rows)
        .groupby(["GsisId", "OutStatus"], as_index=False)
        .agg(
            Name=("Name", "first"),
            Team=("Team", "first"),
            Position=("Position", "first"),
            OutPlayers=("OutPlayers", lambda s: ", ".join(dict.fromkeys(s))),
            car_gain=("car_gain", "sum"),
            xfp_gain=("xfp_gain", "sum"),
            Method=("Method", "first"),
            n=("n", "max"),
        )
    )
    agg["Token"] = np.where(agg["xfp_gain"] >= INJ_MIN_GAINED_XFP, TOKEN_INJ, "")
    return agg.sort_values("xfp_gain", ascending=False)[BENEFICIARY_COLUMNS].reset_index(drop=True)


def _carry_history_line(shares: CarryShares | None) -> str:
    if shares is None:
        return "historically: too few past absences at this role to quote a split"
    return (
        f"historically: {shares.next_up_label} +{round(100 * shares.next_up):d}% of the vacated carries, "
        f"each further back +{round(100 * shares.each_other):d}%, "
        f"~{round(100 * shares.unassigned):d}% goes nowhere (n={shares.n})"
    )


def _split_text(gains: pd.DataFrame, column: str, unit: str, names: pd.Series, n_missed: int) -> str:
    top = gains[column][gains[column] > 0].sort_values(ascending=False).head(3)
    if top.empty:
        return ""
    parts = ", ".join(f"{names.get(g, g)} +{v:.1f}" for g, v in top.items())
    return f"with-or-without ({n_missed} g): {parts} {unit}"


def absences(
    outs: pd.DataFrame,
    history: pd.DataFrame,
    depth_rows: pd.DataFrame,
    *,
    before: tuple[int, int],
) -> pd.DataFrame:
    """Every CONFIRMED absence of a regular (a back, receiver or tight end whose prior-window share of his
    team's targets or carries clears the research's threshold, `regular_kinds`), as context: role, what he
    vacated, how many team games he has missed, the teammates' with-or-without split when it exists (display
    only for targets), and the historical line for his role. No points are attached: this is information, not
    an edge. Most recent absences first."""
    if outs is None or outs.empty or history is None or history.empty:
        return pd.DataFrame(columns=ABSENCE_COLUMNS)
    confirmed = _confirmed(outs)
    names = history.sort_values(["season", "week"]).groupby("GsisId")["Name"].last()
    positions = history.sort_values(["season", "week"]).groupby("GsisId")["Position"].last()
    rows = []
    for _, out in outs[outs["Status"] == STATUS_OUT].iterrows():
        gsis, team, position = out["GsisId"], out["Team"], out["Position"]
        if position not in ("RB", "WR", "TE"):
            continue
        window = rotation_window(history, team, before=before)
        if gsis not in window.index:
            continue
        kinds = regular_kinds(window.loc[gsis])
        if not kinds:
            continue
        order = position_order(window, position, depth_rows, team, {gsis})
        label = role_label(position, order.index(gsis) + 1) if gsis in order else position
        gains, n_missed = with_without_gains(history, gsis, team, before=before, exclude=confirmed)
        vacated = vacated_from_row(window.loc[gsis])
        split = ""
        if n_missed >= WITH_WITHOUT_MIN_GAMES:
            clipped = conserve(gains.clip(lower=0.0), vacated)
            if position == "RB":
                backs = clipped.index[clipped.index.map(lambda g: positions.get(g) == "RB")]
                split = _split_text(clipped.loc[backs], "car_gain", "carries/G", names, n_missed)
            else:
                catchers = clipped.index[clipped.index.map(lambda g: positions.get(g) in ("WR", "TE", "RB"))]
                split = _split_text(clipped.loc[catchers], "tgt_gain", "targets/G", names, n_missed)
        if position == "RB":
            history_line = _carry_history_line(carry_shares(label))
        else:
            history_line = target_context_line(position, target_context(label))
        share_column = {"targets": "tgt_share", "carries": "car_share"}
        regular = " and ".join(
            f"{round(100 * window.loc[gsis, share_column[k]]):d}% of team {k}" for k in kinds
        )
        rows.append(
            {
                "GsisId": gsis,
                "Name": out["Name"],
                "Team": team,
                "Position": position,
                "Role": label,
                "Regular": regular,
                "tgt_g": vacated.tgt_g,
                "car_g": vacated.car_g,
                "GamesMissed": games_missed(history, gsis, team, before=before),
                "WithWithoutGames": n_missed,
                "WithWithout": split,
                "History": history_line,
            }
        )
    if not rows:
        return pd.DataFrame(columns=ABSENCE_COLUMNS)
    frame = pd.DataFrame(rows)
    ordered = frame.sort_values(["GamesMissed", "tgt_g"], ascending=[True, False])
    return ordered[ABSENCE_COLUMNS].reset_index(drop=True)
