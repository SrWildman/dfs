"""`odds_props`: The Odds API player props, turned into `PropPts` (`props.py` does the maths).

**One pull** = the free `/events` list, then one `/events/{id}/odds` call per UNSTARTED game of the week for
the six markets we use, from ONE US region (`regions=us`, every book in it). A call costs (markets that return
data) x (regions); the free list and an empty response cost nothing, and restricting the bookmakers does not
reduce it (measured: 1 market x 3 books = 1 credit, 7 markets x 6 books = 7). The plan is 500 credits a month:
a Saturday main pull of about 78 (6 markets x ~13 games) plus status-change refreshes on game days.
`docs/WORKFLOW.md` has the plan and the guards below.

**What is kept.** Every pull writes the raw responses to `data/raw/odds_props/<UTC stamp>.json` (never the
key), and the resulting `PropPts` frame to `data/raw/odds_props/<UTC stamp>.csv` and `data/current/
odds_props.csv`. The snapshots are kept every week: Model Check scores PropPts in shadow through the same
"last snapshot before the player's own kickoff" rule as the other projections, so each row carries its
`GameStart` (Eastern wall-clock with a trailing Z, as TFFB writes it; `kickoff.py`).

**Guards** (`dfs sync` / `cli._props_plan`):
- **at most ONE automatic pull per NFL week**: the first full `dfs sync` on a Saturday or Sunday (Eastern);
  later full syncs that week reuse it (the credit log marks it `<season> week <n> auto`, so a forced or
  test pull does not use it up). Any other day needs `--props`;
- `dfs sync --live --props` refreshes only the games whose players' DraftKings `Status` changed since the last
  pull (`refresh_plan`: someone quoted, or a $5,000+ starter, went Q/OUT/IR or came back) and spends nothing
  when none did; a refresh merges into the saved frame so the other games keep their prices;
- **budget**: before any pull, credits left / NFL weekends before the monthly reset (`[odds_api] reset_day`)
  must cover the pull's worst case (`budget_check`), else it is skipped with that sentence (not a failure);
- a pull never fetches a started game or a game off the DraftKings slate;
- plain `dfs sync --live` and `dfs week new` never pull (this source is not in `LIVE_SYNC_SOURCES`);
- every pull records `x-requests-remaining/used/last` (`odds_credits`), and `dfs doctor` warns under 100.

The key is `[odds_api] key` in config.toml or `$ODDS_API_KEY`; it is passed as a query parameter (the API's
only way) so every message that could carry the URL is scrubbed of it.
"""

from __future__ import annotations

import glob
import json
import os
from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import httpx
import pandas as pd

from dfs import nfl_calendar, odds_credits, props
from dfs import store as store_mod
from dfs.kickoff import KICKOFF_TIMEZONE
from dfs.log import get_logger
from dfs.paths import RAW_DIR
from dfs.sources.base import Source, SourceSkipped, SyncContext

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
    """A failed pull (no key, an HTTP error)."""


class PropsSkipped(SourceSkipped):
    """A pull a guard declined (budget, nothing changed, no games): reported as a skip, never a failure."""


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


def _note(season: int, week: int, kind: str) -> str:
    return f"{season} week {week} {kind}"


def auto_pulled(season: int, week: int, log_file: Path | None = None) -> str | None:
    """When this NFL week's one automatic pull happened (its UTC stamp), or None. Read from the credit log:
    only a pull the full sync made on its own counts (`kind` "auto"), so a forced `--props` pull or an early
    test pull does not use up the week's main pull."""
    path = log_file or odds_credits.LOG_FILE
    if not Path(path).exists():
        return None
    wanted = _note(season, week, "auto")
    try:
        log_frame = pd.read_csv(path, dtype=str, keep_default_na=False)
    except (OSError, ValueError, pd.errors.ParserError):
        return None
    hit = log_frame[log_frame.get("note", pd.Series(dtype=str)) == wanted]
    return str(hit["pulled_at"].iloc[-1]) if len(hit) else None


def full_sync_decision(now: datetime, season: int, week: int) -> tuple[bool, str]:
    """`(pull, reason)` for a plain full `dfs sync`: AT MOST ONE automatic pull per NFL week, made by the
    first full sync on Saturday or Sunday (Eastern). Later full syncs that week reuse it. A pull on any other
    day needs `--props`. The reason is printed either way."""
    done = auto_pulled(season, week)
    if done:
        return (
            False,
            f"props: Week {week}'s one automatic pull was made {done}; reusing it "
            "(`--live --props` refreshes)",
        )
    local = pd.Timestamp(now).tz_convert(KICKOFF_TIMEZONE)
    if local.weekday() not in MAIN_PULL_WEEKDAYS:
        return (
            False,
            "props: the main pull is Saturday (props are not all posted yet); `dfs sync --props` forces one",
        )
    return True, f"props: pulling Week {week} (the first full sync on a Saturday or Sunday)"


def weekends_left(now: datetime, reset_day: int) -> int:
    """How many NFL Sundays (Eastern) remain from today up to, not including, the next quota reset day. At
    least the one being played this weekend counts; a Sunday that is itself the reset day belongs to the
    next quota."""
    today = pd.Timestamp(now).tz_convert(KICKOFF_TIMEZONE).date()
    year, month = today.year, today.month
    reset = date(year, month, reset_day)
    if reset <= today:
        reset = date(year + (month == 12), 1 if month == 12 else month + 1, reset_day)
    count, day = 0, today
    while day < reset:
        count += day.weekday() == 6
        day += timedelta(days=1)
    return count


def budget_check(remaining: int | None, cost: int, now: datetime, reset_day: int) -> tuple[bool, str]:
    """May a pull that can cost up to `cost` credits run? Credits left, divided by the NFL weekends left
    before the quota resets, must cover it (so one big pull cannot starve the weekends after it); unknown
    credits pass. The reason is the sentence the sync prints when it does not."""
    if remaining is None:
        return True, ""
    weekends = max(weekends_left(now, reset_day), 1)
    share = remaining / weekends
    if cost <= share:
        return True, ""
    return False, (
        f"{remaining} credit(s) left for {weekends} NFL weekend(s) before the reset on day {reset_day} "
        f"is {share:.0f} a weekend, and this pull can cost up to {cost}: skipped"
    )


def _team_codes(event: dict) -> set[str]:
    from dfs.sources.fantasypros_projections import FULL_TEAM_NAME_TO_CODE

    return {
        c
        for c in (
            FULL_TEAM_NAME_TO_CODE.get(event.get("home_team")),
            FULL_TEAM_NAME_TO_CODE.get(event.get("away_team")),
        )
        if c
    }


def _status_by_id(frame: pd.DataFrame) -> dict[str, str]:
    status = frame["Status"].fillna("") if "Status" in frame.columns else pd.Series("", index=frame.index)
    return dict(zip(frame["ID"].astype(str), status.astype(str).str.strip(), strict=False))


def changed_teams(
    previous: pd.DataFrame, current: pd.DataFrame, quoted_ids: set[str], *, starter_salary: int = 5000
) -> set[str]:
    """Teams with a player whose DraftKings `Status` (Q / OUT / IR / blank) changed between two salary files,
    and who matters to props: he was quoted in the last pull, or he is a starter by salary
    (`starter_salary`+). A refresh pulls only these teams' games; no change means nothing is spent."""
    before, after = _status_by_id(previous), _status_by_id(current)
    salary = pd.to_numeric(current.get("Salary"), errors="coerce")
    team = dict(zip(current["ID"].astype(str), current["TeamAbbrev"].astype(str).str.upper(), strict=False))
    big = set(current["ID"].astype(str)[salary.fillna(0) >= starter_salary])
    out = set()
    for pid, status in after.items():
        if pid in before and before[pid] != status and (pid in quoted_ids or pid in big):
            out.add(team[pid])
    return out


def refresh_plan(
    ctx: SyncContext,
    now: datetime,
    *,
    last_snapshot: tuple[datetime, pd.DataFrame] | None = None,
    previous_salaries: pd.DataFrame | None = None,
    salaries: pd.DataFrame | None = None,
) -> tuple[frozenset[str] | None, str]:
    """For `--live --props`: `(teams to refresh, message)`. No earlier props snapshot this week ->
    `(None, ...)`, every unstarted game (a first pull). An earlier one -> only the teams with a changed DK
    status since it; none changed -> an empty set and a message saying nothing is spent."""
    if last_snapshot is None:
        snaps = [(ts, p) for ts, p in store_mod.snapshot_paths(RAW_DIR_NAME)]
        week_start = nfl_calendar.week_start_date(ctx.week, ctx.season)
        snaps = [(ts, p) for ts, p in snaps if ts.date() >= week_start]
        if snaps:
            last_snapshot = (snaps[-1][0], pd.read_csv(snaps[-1][1]))
    if last_snapshot is None:
        return None, "props: no earlier snapshot this week; pulling every unstarted game"
    stamp, frame = last_snapshot
    if salaries is None:
        salaries = store_mod.load_current("draftkings")
    if previous_salaries is None:
        earlier = [p for ts, p in store_mod.snapshot_paths("draftkings") if ts <= stamp]
        if not earlier:
            return (
                None,
                "props: no DraftKings snapshot from before the last pull; pulling every unstarted game",
            )
        previous_salaries = pd.read_csv(earlier[-1])
    quoted = {str(int(float(i))) for i in frame["Id"].dropna()} if "Id" in frame.columns else set()
    teams = changed_teams(previous_salaries, salaries, quoted)
    if not teams:
        return frozenset(), (
            "props: no DraftKings status has changed since the last pull "
            f"({stamp:%a %H:%M} UTC); nothing spent"
        )
    return frozenset(
        teams
    ), f"props: status changed on {', '.join(sorted(teams))}; refreshing those games only"


def pull(
    ctx: SyncContext,
    *,
    now: datetime | None = None,
    client: httpx.Client | None = None,
    salaries: pd.DataFrame | None = None,
    raw_dir: Path | None = None,
    key: str | None = None,
    sleep: Callable[[float], None] | None = None,
    reset_day: int | None = None,
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
        if ctx.props_teams is not None:  # a refresh: only the games whose players' status changed
            events = [e for e in events if _team_codes(e) & ctx.props_teams]
        if not events:
            raise PropsSkipped(
                f"no unstarted Week {ctx.week} games on the DraftKings slate to pull props for"
            )
        cost = estimate_cost(len(events))
        known = odds_credits.read()
        remaining_now = known.remaining if known is not None else None
        if remaining_now is not None and remaining_now < cost:
            raise PropsSkipped(
                f"{remaining_now} credit(s) left and this pull can cost up to {cost}: not pulling"
            )
        allowed, why = budget_check(
            remaining_now, cost, now, reset_day if reset_day is not None else _reset_day()
        )
        if not allowed:
            raise PropsSkipped(why)
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
        remaining,
        used,
        spent=spent,
        events=len(payloads),
        now=now,
        note=_note(ctx.season, ctx.week, ctx.props_kind),
    )
    log.info("props: %d game(s), %d credit(s) spent, %s remaining", len(payloads), spent, reading.remaining)

    frame, unmatched = frame_from_payloads(
        payloads, salaries, season=ctx.season, week=ctx.week, qb_int_rates=_qb_int_rates(ctx.season, ctx.week)
    )
    if ctx.props_teams is not None:
        frame = _merge_with_previous(frame, {e.get("id") for e in payloads})
    frame.attrs.update({"spent": spent, "remaining": reading.remaining, "unmatched": len(unmatched)})
    if len(unmatched):
        from dfs.paths import CURRENT_DIR

        CURRENT_DIR.mkdir(parents=True, exist_ok=True)
        unmatched.to_csv(CURRENT_DIR / "unmatched_odds_props.csv", index=False)
    return frame


def _reset_day() -> int:
    from dfs.config import ConfigError, load_config

    try:
        return load_config().odds_api.reset_day
    except ConfigError:
        return 1


def _merge_with_previous(frame: pd.DataFrame, refreshed_events: set) -> pd.DataFrame:
    """A refresh prices only some games; the saved frame must still hold the others' last prices, or their
    PropPts would blank out of EdgeRaw. Rows of the previous frame for games NOT refreshed are kept."""
    try:
        previous = store_mod.load_current("odds_props")
    except FileNotFoundError:
        return frame
    kept = previous[~previous["event_id"].isin(refreshed_events)]
    merged = pd.concat([kept, frame], ignore_index=True)
    merged.attrs.update(frame.attrs)
    return merged[FRAME_COLUMNS]


class OddsPropsSource(Source):
    name = "odds_props"
    # A shadow source: a column on EdgeRaw (`PropPts`) and a Model Check row, no raw tab of its own.
    uploads_to_sheet = False

    def fetch(self, ctx: SyncContext) -> pd.DataFrame:
        return pull(ctx)
