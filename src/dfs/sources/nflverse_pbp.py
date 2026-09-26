"""nflverse's free, unauthenticated play-by-play release -- Part C, C7.

Fetches two full-season parquet files every sync (the current season, plus
the prior season for the early-week blend -- see `team_metrics.
PBP_PRIOR_WEIGHT_GAMES`) and reduces them to one small per-team table
(Pace/PROE/Expl%). All the real math -- the neutral-script filter, the
three metrics, the blend -- lives in `team_metrics.py`, pure and
offline-testable; this module is the thin, untested fetch-and-assemble
wrapper `derived.py`'s own docstring says every source should be.

Release-asset URLs only, per PROMPT_PART_C.md's own warning:
`github.com/<org>/<repo>/raw/...` 403s in some environments, confirmed
against this exact pattern (`sources/nflverse_games.py`/`nflverse_snaps.py`
already use the same release-asset host for their own files). Verified
live (2026-09-25): the current (2026) file, three weeks into the season,
is 2.5MB; the completed 2025 season is 19MB -- both fetched fresh on every
sync, same as every other Part C source (no source-specific caching here,
matching TFFB/Sleeper/FantasyPros' own "refetch in full every time"
pattern) -- see `sources/__init__.py`'s own note on why "pbp" is excluded
from `cli.py`'s `LIVE_SYNC_SOURCES` the same way sleeper/fantasypros/snaps
already are.

What's actually saved under `data/raw/pbp/`/`data/current/pbp.csv` (C8) is
this module's OWN already-computed per-team output (Team/Pace/PROE/Expl%),
not the raw multi-thousand-row play-by-play itself -- the same "a source's
snapshot is the shape the rest of the pipeline consumes" convention every
other Part C source already follows (Sleeper/FantasyPros save their own
already-DK-scored rows, not the source API's raw JSON/HTML). Flagged
explicitly since C7's own text just says "snapshot it": the raw pbp
parquet itself is nflverse's own permanent public archive, not data that
disappears after this week the way a weekly projection does, so there's
no "can't be re-fetched later" reason to duplicate tens of megabytes of it
into this repo's own `data/raw/` on every sync the way there is for
Sleeper/FantasyPros.
"""

from __future__ import annotations

import io

import httpx
import pandas as pd

from dfs.log import get_logger
from dfs.sources.base import Source, SyncContext
from dfs.sources.nflverse_games import NFLVERSE_TO_DK_TEAM
from dfs.team_metrics import (
    PBP_PRIOR_WEIGHT_GAMES,
    blend_with_prior,
    team_explosive_pct,
    team_games_played,
    team_pace,
    team_proe,
)

log = get_logger("sources.nflverse_pbp")

PBP_URL_TEMPLATE = (
    "https://github.com/nflverse/nflverse-data/releases/download/pbp/play_by_play_{season}.parquet"
)

TEAM_METRIC_COLUMNS = ["Team", "Pace", "PROE", "Expl%"]


class NflversePbpFetchError(Exception):
    pass


def _fetch_pbp(season: int) -> pd.DataFrame:
    url = PBP_URL_TEMPLATE.format(season=season)
    try:
        resp = httpx.get(url, timeout=90, follow_redirects=True)
        resp.raise_for_status()
    except httpx.HTTPError as e:
        raise NflversePbpFetchError(
            f"Request to nflverse for season {season} play-by-play failed: {e}"
        ) from e
    try:
        return pd.read_parquet(io.BytesIO(resp.content))
    except Exception as e:  # pyarrow raises its own exception types, not one common base
        raise NflversePbpFetchError(f"Could not parse season {season} play-by-play parquet: {e}") from e


def build_team_metrics(current_pbp: pd.DataFrame, prior_pbp: pd.DataFrame | None) -> pd.DataFrame:
    """Per-team Pace/PROE/Expl% from `current_pbp`, blended with
    `prior_pbp`'s own full-season value (or current-season alone if
    `prior_pbp` is unavailable -- fail soft, same as every other optional
    Part C input), remapped from nflverse's own team codes to
    DraftKings' via the single confirmed drift `nflverse_games.py` already
    found (`{"LA": "LAR"}`) -- imported from there rather than
    re-verified, since it's the same underlying fact about the same
    upstream data, not a second independent finding."""
    current_pace = team_pace(current_pbp)
    current_proe = team_proe(current_pbp)
    current_expl = team_explosive_pct(current_pbp)
    games_played = team_games_played(current_pbp)

    if prior_pbp is not None:
        prior_pace = team_pace(prior_pbp)
        prior_proe = team_proe(prior_pbp)
        prior_expl = team_explosive_pct(prior_pbp)
        pace = blend_with_prior(current_pace, prior_pace, games_played, PBP_PRIOR_WEIGHT_GAMES)
        proe = blend_with_prior(current_proe, prior_proe, games_played, PBP_PRIOR_WEIGHT_GAMES)
        expl = blend_with_prior(current_expl, prior_expl, games_played, PBP_PRIOR_WEIGHT_GAMES)
    else:
        pace, proe, expl = current_pace, current_proe, current_expl

    frame = pd.DataFrame({"Pace": pace, "PROE": proe, "Expl%": expl})
    frame.index.name = "Team"
    frame = frame.reset_index()
    frame["Team"] = frame["Team"].replace(NFLVERSE_TO_DK_TEAM)
    return frame[TEAM_METRIC_COLUMNS]


class NflversePbpSource(Source):
    name = "pbp"

    def fetch(self, ctx: SyncContext) -> pd.DataFrame:
        log.info("fetching nflverse play-by-play for season %s", ctx.season)
        current_pbp = _fetch_pbp(ctx.season)
        try:
            prior_pbp = _fetch_pbp(ctx.season - 1)
        except NflversePbpFetchError as e:
            log.warning(
                "prior-season play-by-play unavailable (%s) -- Pace/PROE/Expl%% blend with the "
                "current season alone this run",
                e,
            )
            prior_pbp = None
        return build_team_metrics(current_pbp, prior_pbp)
