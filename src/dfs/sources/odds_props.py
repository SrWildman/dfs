"""`odds_props`: The Odds API player props, turned into `PropPts` (`props.py` does the maths).

**One pull** = the free `/events` list, then one `/events/{id}/odds` call per UNSTARTED game of the week for
the six markets we use, from ONE US region (`regions=us`, every book in it). A call costs (markets that return
data) x (regions); the free list and an empty response cost nothing, and restricting the bookmakers does not
reduce it (measured: 1 market x 3 books = 1 credit, 7 markets x 6 books = 7). The plan is 500 credits a month:
a Saturday main pull of about 78 (6 markets x ~13 games) plus a Sunday-morning refresh of the games not yet
started, capped near 30. `docs/WORKFLOW.md` has the plan and the guards below.

**What is kept.** Every pull writes the raw responses to `data/raw/odds_props/<UTC stamp>.json` (never the
key), and the resulting `PropPts` frame to `data/raw/odds_props/<UTC stamp>.csv` and `data/current/
odds_props.csv`. The snapshots are kept every week: Model Check scores PropPts in shadow through the same
"last snapshot before the player's own kickoff" rule as the other projections, so each row carries its
`GameStart` (Eastern wall-clock with a trailing Z, as TFFB writes it; `kickoff.py`).

**Guards** (`dfs sync` / `cli._props_plan`):
- a full `dfs sync` pulls only on Saturday or Sunday (Eastern) AND only if no snapshot exists from the last 24
  hours, so a Tuesday sync never spends credits on half-posted props;
- `dfs sync --props` (full) and `dfs sync --live --props` pull whatever the day, but never for a game that has
  started (props come down at kickoff) and never when the credits left would not cover the pull;
- plain `dfs sync --live` never pulls (this source is not in `LIVE_SYNC_SOURCES`);
- every pull records `x-requests-remaining/used/last` (`odds_credits`), and `dfs doctor` warns under 100.

The key is `[odds_api] key` in config.toml or `$ODDS_API_KEY`; it is passed as a query parameter (the API's
only way) so every message that could carry the URL is scrubbed of it.
"""

from __future__ import annotations

import glob
import json
import os
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pandas as pd

from dfs import nfl_calendar, odds_credits, props
from dfs import store as store_mod
from dfs.kickoff import KICKOFF_TIMEZONE
from dfs.log import get_logger
from dfs.paths import RAW_DIR
from dfs.sources.base import Source, SyncContext

log = get_logger("sources.odds_props")

API_ROOT = "https://api.the-odds-api.com/v4"
SPORT = "americanfootball_nfl"
REGIONS = "us"
ODDS_FORMAT = "american"
# The six markets we pull. `player_pass_interceptions` is deliberately NOT here: it costs a credit per
# game and the QB's season rate (a flagged fallback in `props.py`) is nearly as good.
PULL_MARKETS = (
    "player_pass_yds",
    "player_pass_tds",
    "player_rush_yds",
    "player_receptions",
    "player_reception_yds",
    "player_anytime_td",
)
RAW_DIR_NAME = "odds_props"
REUSE_HOURS = 24  # a full sync reuses a snapshot younger than this
MAIN_PULL_WEEKDAYS = (5, 6)  # Saturday and Sunday (Eastern): props are posted by then
GAME_PADDING = timedelta(minutes=1)  # a game starting within this of "now" counts as started
STATS_PLAYER_GLOB = str(RAW_DIR / "stats_player" / "*.parquet")
FRAME_COLUMNS = [
    "Id",
    "Name",
    "Team",
    "Position",
    "GameStart",
    *[c for c in props.PROP_COLUMNS if c not in ("Name", "Team", "Position")],
]


class OddsApiError(Exception):
    """A failed or refused pull (no key, an HTTP error, too few credits left)."""


def api_key() -> str:
    """`$ODDS_API_KEY`, else config.toml's `[odds_api] key`. Raises `OddsApiError` when there is none."""
    key = os.environ.get("ODDS_API_KEY")
    if not key:
        from dfs.config import ConfigError, load_config

        try:
            key = load_config().odds_api.key
        except ConfigError:
            key = None
    if not key or key.startswith("PASTE"):
        raise OddsApiError("no Odds API key: put it in config.toml under [odds_api] key (or $ODDS_API_KEY)")
    return key


def _scrub(text: str, key: str) -> str:
    return text.replace(key, "***") if key else text


def _get(client: httpx.Client, path: str, key: str, **params) -> httpx.Response:
    try:
        resp = client.get(f"{API_ROOT}{path}", params={"apiKey": key, **params}, timeout=30)
        resp.raise_for_status()
    except httpx.HTTPStatusError as e:
        raise OddsApiError(
            _scrub(f"The Odds API answered {e.response.status_code} for {path}: {e.response.text[:200]}", key)
        ) from None
    except httpx.HTTPError as e:
        raise OddsApiError(_scrub(f"Request to The Odds API failed for {path}: {e}", key)) from None
    return resp


def eastern_game_start(commence_time: str) -> str:
    """An API `commence_time` (UTC ISO) as TFFB writes `GameStart`: Eastern wall clock with a trailing Z."""
    instant = pd.Timestamp(commence_time)
    instant = instant.tz_localize("UTC") if instant.tzinfo is None else instant.tz_convert("UTC")
    return instant.tz_convert(KICKOFF_TIMEZONE).strftime("%Y-%m-%dT%H:%M:%SZ")


def unstarted_week_events(events: list[dict], week: int, season: int, now: datetime) -> list[dict]:
    """The events of NFL week `week` whose kickoff is still ahead of `now` (props are taken down at kickoff,
    and a started game would cost credits for nothing)."""
    keep = []
    for event in events:
        try:
            start = pd.Timestamp(event["commence_time"]).tz_convert("UTC").to_pydatetime()
        except (KeyError, TypeError, ValueError):
            continue
        local_day = pd.Timestamp(start).tz_convert(KICKOFF_TIMEZONE).date()
        if nfl_calendar.week_for_date(local_day, season) == week and start > now + GAME_PADDING:
            keep.append(event)
    return keep


def slate_events(events: list[dict], salaries: pd.DataFrame) -> list[dict]:
    """The events whose two teams are both on the DraftKings slate in `salaries`: a London game or a night
    game that is not in the Classic pool has no one to price, so its credits would be wasted. An event whose
    team names we cannot map to codes is kept (spend rather than silently lose a game)."""
    from dfs.sources.fantasypros_projections import FULL_TEAM_NAME_TO_CODE

    teams = {str(t).strip().upper() for t in salaries["TeamAbbrev"].dropna()}
    keep = []
    for event in events:
        codes = [
            FULL_TEAM_NAME_TO_CODE.get(event.get("home_team")),
            FULL_TEAM_NAME_TO_CODE.get(event.get("away_team")),
        ]
        if any(c is None for c in codes) or all(str(c).upper() in teams for c in codes):
            keep.append(event)
    return keep


def estimate_cost(n_events: int) -> int:
    """The most a pull can cost: every market returns data in every game."""
    return n_events * len(PULL_MARKETS) * len(REGIONS.split(","))


def dk_players(salaries: pd.DataFrame) -> pd.DataFrame:
    """`Id, Name, Team, Position` of the DraftKings pool from the raw DK file."""
    return pd.DataFrame(
        {
            "Id": salaries["ID"].astype(str),
            "Name": salaries["Name"],
            "Team": salaries["TeamAbbrev"],
            "Position": salaries["Position"],
        }
    ).drop_duplicates(subset="Id")


def _qb_int_rates(season: int, week: int) -> dict[str, float]:
    """Expected INTs per game per QB from the newest nflverse `stats_player` snapshot on disk (no download
    here); empty when there is none, so `props.py` uses the league rate and flags it."""
    files = sorted(glob.glob(STATS_PLAYER_GLOB))
    if not files:
        return {}
    try:
        rates, _league = props.season_int_rates(pd.read_parquet(files[-1]), season, week - 1)
    except Exception as e:  # noqa: BLE001 - a fallback input must never stop the pull
        log.warning("QB interception rates unavailable (%s); using the league rate", e)
        return {}
    return rates


def frame_from_payloads(
    payloads: list[dict],
    salaries: pd.DataFrame,
    *,
    season: int,
    week: int,
    qb_int_rates: dict[str, float] | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """`(PropPts frame, unmatched prop players)` from the event-odds payloads and the DK salary file."""
    pool = dk_players(salaries)
    scored, unmatched = props.props_from_responses(
        payloads, pool, qb_int_rates=qb_int_rates, odds_format=ODDS_FORMAT
    )
    starts = {e.get("id"): eastern_game_start(e["commence_time"]) for e in payloads if e.get("commence_time")}
    scored = scored.merge(pool, on=["Name", "Team", "Position"], how="left")
    scored["GameStart"] = scored["event_id"].map(starts)
    for column in FRAME_COLUMNS:
        if column not in scored.columns:
            scored[column] = float("nan")
    return scored[FRAME_COLUMNS], unmatched


def snapshot_age(now: datetime | None = None) -> timedelta | None:
    """How old the newest props snapshot is, or None when there is none."""
    snaps = store_mod.snapshot_paths(RAW_DIR_NAME)
    if not snaps:
        return None
    return (now or datetime.now(UTC)) - snaps[-1][0]


def full_sync_decision(now: datetime) -> tuple[bool, str]:
    """`(pull, reason)` for a plain full `dfs sync`: Saturday or Sunday (Eastern) and no snapshot in the last
    `REUSE_HOURS`. The reason is printed either way."""
    local = pd.Timestamp(now).tz_convert(KICKOFF_TIMEZONE)
    age = snapshot_age(now)
    if age is not None and age < timedelta(hours=REUSE_HOURS):
        hours = age.total_seconds() / 3600
        return (
            False,
            f"props: reusing the snapshot from {hours:.1f} h ago (a pull is due after {REUSE_HOURS} h)",
        )
    if local.weekday() not in MAIN_PULL_WEEKDAYS:
        return (
            False,
            "props: the main pull is Saturday (props are not all posted yet); `dfs sync --props` forces one",
        )
    return True, "props: pulling (Saturday/Sunday, no snapshot in the last 24 h)"


def pull(
    ctx: SyncContext,
    *,
    now: datetime | None = None,
    client: httpx.Client | None = None,
    salaries: pd.DataFrame | None = None,
    raw_dir: Path | None = None,
    key: str | None = None,
    sleep: Callable[[float], None] | None = None,
) -> pd.DataFrame:
    """The whole pull for `ctx.week`: events, event odds for the unstarted games, raw JSON kept, credits
    recorded, `PropPts` frame returned. Raises `OddsApiError` when it cannot or should not run."""
    now = now or datetime.now(UTC)
    key = key or api_key()
    own_client = client is None
    client = client or httpx.Client()
    raw_dir = Path(raw_dir) if raw_dir else RAW_DIR / RAW_DIR_NAME
    try:
        if salaries is None:
            salaries = store_mod.load_current("draftkings")
        listing = _get(client, f"/sports/{SPORT}/events", key)  # free
        events = slate_events(unstarted_week_events(listing.json(), ctx.week, ctx.season, now), salaries)
        if not events:
            raise OddsApiError(
                f"no unstarted Week {ctx.week} games on the DraftKings slate to pull props for"
            )
        cost = estimate_cost(len(events))
        known = odds_credits.read()
        if known is not None and known.remaining is not None and known.remaining < cost:
            raise OddsApiError(
                f"{known.remaining} credit(s) left and this pull can cost up to {cost}: not pulling"
            )
        payloads, spent, remaining, used = [], 0, None, None
        for event in events:
            resp = _get(
                client,
                f"/sports/{SPORT}/events/{event['id']}/odds",
                key,
                regions=REGIONS,
                markets=",".join(PULL_MARKETS),
                oddsFormat=ODDS_FORMAT,
            )
            payloads.append(resp.json())
            left, used_now, last = odds_credits.from_headers(resp.headers)
            remaining, used = (
                left if left is not None else remaining,
                used_now if used_now is not None else used,
            )
            spent += last or 0
            if sleep:
                sleep(0.2)
    finally:
        if own_client:
            client.close()

    stamp = now.strftime("%Y%m%dT%H%M%SZ")
    raw_dir.mkdir(parents=True, exist_ok=True)
    (raw_dir / f"{stamp}.json").write_text(
        json.dumps({"pulled_at": stamp, "week": ctx.week, "events": payloads})
    )
    reading = odds_credits.record(
        remaining, used, spent=spent, events=len(payloads), now=now, note=f"week {ctx.week}"
    )
    log.info("props: %d game(s), %d credit(s) spent, %s remaining", len(payloads), spent, reading.remaining)

    frame, unmatched = frame_from_payloads(
        payloads, salaries, season=ctx.season, week=ctx.week, qb_int_rates=_qb_int_rates(ctx.season, ctx.week)
    )
    frame.attrs.update({"spent": spent, "remaining": reading.remaining, "unmatched": len(unmatched)})
    if len(unmatched):
        from dfs.paths import CURRENT_DIR

        CURRENT_DIR.mkdir(parents=True, exist_ok=True)
        unmatched.to_csv(CURRENT_DIR / "unmatched_odds_props.csv", index=False)
    return frame


class OddsPropsSource(Source):
    name = "odds_props"
    # A shadow source: a column on EdgeRaw (`PropPts`) and a Model Check row, no raw tab of its own.
    uploads_to_sheet = False

    def fetch(self, ctx: SyncContext) -> pd.DataFrame:
        return pull(ctx)
