"""Sleeper's free, unauthenticated, undocumented weekly projections API --
Part C, C3.

`order_by=pts_ppr`, not `order_by=ppr` -- the wrong key silently returns
placeholder records shaped exactly like real ones (confirmed against a
live pull), which is worse than an obvious error.

Sleeper calls the DST slot "DEF" in this endpoint, not "DST" (confirmed
live: `position[]=DST` returns zero rows, `position[]=DEF` returns all 32
teams) -- `POSITIONS` maps DK's own position name to Sleeper's query
value so nothing else in this codebase needs to know about that.

Every source's own fantasy total (`pts_ppr` etc.) is discarded -- Sleeper's
scoring isn't DraftKings' (see `dk_scoring.py`). Only the component stats
this module pulls out of `stats` feed `dk_scoring.score_offense_frame`/
`score_dst_frame`.

Sleeper's own team codes already match DraftKings' exactly (confirmed live
against a real pull, including both LA teams as "LAC"/"LAR" and Jacksonville
as "JAX") -- unlike FantasyPros, no team-code crosswalk is needed here.

This endpoint is undocumented and can change shape without notice (Sam's
own decision, C3): fails loud from this module (a clear exception on a
missing/malformed response), and the existing per-source isolation in
`sync.run_sync` is what makes that fail *soft* for the overall sync --
this source's own failure is caught there, recorded, and every other
source (including `edge`, which treats a missing "sleeper" current CSV
the same as a missing "weather" one) still runs."""

from __future__ import annotations

import httpx
import pandas as pd

from dfs.dk_scoring import DST_STAT_FIELDS, OFFENSE_STAT_FIELDS, score_dst_frame, score_offense_frame
from dfs.log import get_logger
from dfs.sources.base import Source, SyncContext

log = get_logger("sources.sleeper_projections")

PROJECTIONS_URL_TEMPLATE = (
    "https://api.sleeper.com/projections/nfl/{season}/{week}"
    "?season_type=regular&position[]={position}&order_by=pts_ppr"
)

# DK's own position name -> Sleeper's query value. Classic has no kicker
# slot, so K is never fetched (C3's own instruction).
POSITIONS = {"QB": "QB", "RB": "RB", "WR": "WR", "TE": "TE", "DST": "DEF"}

# Sleeper's own stat keys already match dk_scoring.py's canonical
# OFFENSE_STAT_FIELDS names one-for-one except "two_pt", which Sleeper
# splits into three (summed below) -- confirmed against a real live pull
# for every position, so this is a straight subset-and-default-0 read,
# not a rename map.
_OFFENSE_DIRECT_FIELDS = [f for f in OFFENSE_STAT_FIELDS if f != "two_pt"]
_TWO_PT_FIELDS = ["pass_2pt", "rush_2pt", "rec_2pt"]

# DST field mapping is NOT one-for-one -- Sleeper's own DST TD accounting
# is undocumented and split across several fields. `def_td` here is
# built from the three that clearly describe a defensive/return score
# (fumble return, interception return, punt return); `st_td` was left
# OUT because its own meaning is ambiguous against these three (possible
# overlap with `pr_td`, or a separate kick-return TD Sleeper doesn't
# expose a distinct field for in a projection payload) and there's no
# live game result yet this season to check it against. Flagged to Sam:
# once real week 1-2 DST results are available, cross-check `def_td`
# reconstructed this way against nflverse's real box scores before
# trusting it for a DST that actually scores a return TD.
_DST_FIELD_MAP = {
    "sack": "sack",
    "def_int": "int",
    "fum_rec": "fum_rec",
    "safety": "safe",
    "blocked_kick": "blk_kick",
}
_DST_TD_FIELDS = ["def_fum_td", "pass_int_td", "pr_td"]


class SleeperProjectionsFetchError(Exception):
    pass


# Found live, verified against the real week 3 2026 pull: Sleeper returns a
# `stats` dict for EVERY player in its database, including ones it has no
# real weekly projection for at all (a depth-chart QB3, a player who's since
# been benched/injured) -- that dict then holds only ranking metadata
# (`adp_dd_ppr`/`pos_adp_dd_ppr`), no actual per-stat numbers. Scoring that
# as "every real stat is 0" silently turned two real starting QBs (Jayden
# Daniels, Caleb Williams, both genuinely missing real projections in that
# same pull) into a full 0.0 DK score apiece -- confirmed the specific
# cause of an artificial ~-2 to -3 point mean-diff-vs-TFFB offset across
# every position during C2's own validation step. Fixed by treating
# "stats dict has nothing but ranking metadata" as NO projection (NaN,
# excluded from the aggregate for that player), never a real 0 -- same
# "blank is not zero" convention `derived.py` already enforces elsewhere.
_RANKING_ONLY_FIELDS = {"adp_dd_ppr", "pos_adp_dd_ppr"}


def _has_real_projection(stats: dict) -> bool:
    return bool(set(stats) - _RANKING_ONLY_FIELDS)


def _player_name(player: dict) -> str:
    first, last = player.get("first_name") or "", player.get("last_name") or ""
    return f"{first} {last}".strip()


def _extract_offense_row(record: dict) -> dict:
    stats = record.get("stats") or {}
    player = record.get("player") or {}
    row = {
        "Name": _player_name(player),
        "Team": record.get("team") or player.get("team") or "",
        "Position": player.get("position") or "",
    }
    if _has_real_projection(stats):
        for field in _OFFENSE_DIRECT_FIELDS:
            row[field] = stats.get(field, 0.0) or 0.0
        row["two_pt"] = sum(stats.get(f, 0.0) or 0.0 for f in _TWO_PT_FIELDS)
    else:
        for field in [*_OFFENSE_DIRECT_FIELDS, "two_pt"]:
            row[field] = pd.NA
    return row


def _extract_dst_row(record: dict) -> dict:
    stats = record.get("stats") or {}
    player = record.get("player") or {}
    row = {
        "Name": _player_name(player) or (record.get("team") or ""),
        "Team": record.get("team") or player.get("team") or "",
        "Position": "DST",
    }
    if _has_real_projection(stats):
        for dk_field, sleeper_field in _DST_FIELD_MAP.items():
            row[dk_field] = stats.get(sleeper_field, 0.0) or 0.0
        row["def_td"] = sum(stats.get(f, 0.0) or 0.0 for f in _DST_TD_FIELDS)
        row["points_allowed"] = stats.get("pts_allow", 0.0) or 0.0
    else:
        for field in [*_DST_FIELD_MAP, "def_td", "points_allowed"]:
            row[field] = pd.NA
    return row


def fetch_projections(season: int, week: int) -> pd.DataFrame:
    offense_rows: list[dict] = []
    dst_rows: list[dict] = []

    for dk_position, sleeper_position in POSITIONS.items():
        url = PROJECTIONS_URL_TEMPLATE.format(season=season, week=week, position=sleeper_position)
        try:
            resp = httpx.get(url, timeout=30, follow_redirects=True)
            resp.raise_for_status()
        except httpx.HTTPError as e:
            raise SleeperProjectionsFetchError(f"Request to Sleeper failed for {dk_position}: {e}") from e

        try:
            records = resp.json()
        except ValueError as e:
            raise SleeperProjectionsFetchError(
                f"Sleeper returned non-JSON for {dk_position} -- endpoint shape may have changed."
            ) from e
        if not isinstance(records, list):
            raise SleeperProjectionsFetchError(
                f"Sleeper's {dk_position} response wasn't a list -- endpoint shape may have changed."
            )

        if dk_position == "DST":
            dst_rows.extend(_extract_dst_row(r) for r in records)
        else:
            offense_rows.extend(_extract_offense_row(r) for r in records)

    if not offense_rows and not dst_rows:
        raise SleeperProjectionsFetchError("Sleeper returned no players for any position.")

    offense_df = pd.DataFrame(offense_rows)
    if not offense_df.empty:
        offense_df["DkPts"] = score_offense_frame(offense_df[OFFENSE_STAT_FIELDS])

    dst_df = pd.DataFrame(dst_rows)
    if not dst_df.empty:
        dst_df["DkPts"] = score_dst_frame(dst_df[DST_STAT_FIELDS])

    combined = pd.concat([offense_df, dst_df], ignore_index=True, sort=False)
    keep = ["Name", "Team", "Position", "DkPts", *_OFFENSE_DIRECT_FIELDS, "two_pt", *DST_STAT_FIELDS]
    for col in keep:
        if col not in combined.columns:
            combined[col] = pd.NA
    return combined[keep]


class SleeperProjectionsSource(Source):
    name = "sleeper"

    def fetch(self, ctx: SyncContext) -> pd.DataFrame:
        log.info("fetching sleeper projections for week %s, season %s", ctx.week, ctx.season)
        return fetch_projections(ctx.season, ctx.week)
