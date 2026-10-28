"""Sleeper's public API (no login): a league's settings, rosters, users and FAAB; the NFL player table; weekly
stat lines and projections.

Gentle by design: every call is cached on disk under `data/season/sleeper/`, `/players/nfl` at most once a day
(Sleeper's own ask: it is several MB), a finished week's stats forever, projections for a few hours, and the
live calls are spaced by `REQUEST_GAP_S`.

Endpoints (both hosts are Sleeper's own): `api.sleeper.app/v1` for leagues and players, `api.sleeper.com` for
the per-week stats and projections (the same endpoint `sources/sleeper_projections.py` reads for DK; here
every position Sleeper scores is pulled, with the raw stat dict kept so each league can score it its own way).
"""

from __future__ import annotations

import json
import math
import time
from dataclasses import dataclass, field
from pathlib import Path

import httpx

from dfs.log import get_logger
from dfs.paths import DATA_DIR

log = get_logger("season.sleeper_league")

API = "https://api.sleeper.app/v1"
STATS_API = "https://api.sleeper.com"
CACHE_DIR = DATA_DIR / "season" / "sleeper"
REQUEST_GAP_S = 0.25
PLAYERS_MAX_AGE_S = 24 * 3600
PROJECTIONS_MAX_AGE_S = 6 * 3600
LIVE_MAX_AGE_S = 10 * 60
# Every position a Sleeper redraft league can start (IDP is not supported).
FANTASY_POSITIONS = ("QB", "RB", "WR", "TE", "K", "DEF")
# Sleeper's `settings.type`: 0 redraft, 1 keeper, 2 dynasty, 3 chopped (guillotine: the lowest score each week
# is eliminated and its roster goes to waivers).
LEAGUE_TYPE_CHOPPED = 3
# The last fantasy week a league can play when its settings do not say otherwise.
DEFAULT_LAST_WEEK = 17

_last_request = 0.0


class SleeperError(Exception):
    pass


def _get(url: str) -> object:
    global _last_request
    wait = REQUEST_GAP_S - (time.monotonic() - _last_request)
    if wait > 0:
        time.sleep(wait)
    try:
        resp = httpx.get(url, timeout=60, follow_redirects=True)
        _last_request = time.monotonic()
        resp.raise_for_status()
        return resp.json()
    except (httpx.HTTPError, ValueError) as e:
        raise SleeperError(f"Sleeper request failed ({url}): {e}") from e


def cached_get(url: str, cache_file: Path, max_age_s: float | None) -> object:
    """`max_age_s=None` keeps the file forever (a finished week's stats never change)."""
    if cache_file.exists():
        age = time.time() - cache_file.stat().st_mtime
        if max_age_s is None or age < max_age_s:
            return json.loads(cache_file.read_text())
    data = _get(url)
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    tmp = cache_file.with_suffix(".tmp")
    tmp.write_text(json.dumps(data))
    tmp.replace(cache_file)
    return data


def fetch_state() -> dict:
    return cached_get(f"{API}/state/nfl", CACHE_DIR / "state.json", LIVE_MAX_AGE_S)


def fetch_players() -> dict:
    return cached_get(f"{API}/players/nfl", CACHE_DIR / "players_nfl.json", PLAYERS_MAX_AGE_S)


def fetch_user_id(username: str) -> str:
    data = cached_get(f"{API}/user/{username}", CACHE_DIR / "users" / f"{username.lower()}.json", None)
    if not isinstance(data, dict) or not data.get("user_id"):
        raise SleeperError(f"Sleeper has no user named {username!r}.")
    return str(data["user_id"])


def _week_records(kind: str, season: int, week: int, final: bool) -> list[dict]:
    positions = "&".join(f"position[]={p}" for p in FANTASY_POSITIONS)
    url = f"{STATS_API}/{kind}/nfl/{season}/{week}?season_type=regular&{positions}&order_by=pts_ppr"
    max_age = None if final else (PROJECTIONS_MAX_AGE_S if kind == "projections" else LIVE_MAX_AGE_S)
    data = cached_get(url, CACHE_DIR / kind / f"{season}-wk{week:02d}.json", max_age)
    if not isinstance(data, list):
        raise SleeperError(
            f"Sleeper's {kind} for {season} week {week} was not a list; the endpoint may have changed."
        )
    return data


def fetch_week_stats(season: int, week: int, *, final: bool) -> list[dict]:
    """Actual stat lines, one record per player with a `stats` dict in Sleeper's own keys."""
    return _week_records("stats", season, week, final)


def fetch_week_projections(season: int, week: int, *, final: bool = False) -> list[dict]:
    """Projected stat lines. A past week's projections are Sleeper's last ones before its games."""
    return _week_records("projections", season, week, final)


def fetch_transactions(league_id: str, week: int, *, final: bool) -> list[dict]:
    url = f"{API}/league/{league_id}/transactions/{week}"
    data = cached_get(
        url,
        CACHE_DIR / "leagues" / league_id / f"transactions-wk{week:02d}.json",
        None if final else LIVE_MAX_AGE_S,
    )
    return data if isinstance(data, list) else []


@dataclass
class Team:
    roster_id: int
    owner_id: str
    manager: str
    team_name: str
    players: list[str]
    starters: list[str]
    wins: int
    losses: int
    ties: int
    points_for: float
    faab_left: int
    alive: bool = True

    @property
    def label(self) -> str:
        return self.team_name or self.manager


@dataclass
class League:
    name: str
    league_id: str
    title: str
    season: int
    scoring: dict[str, float]
    slots: list[str]
    bench_slots: int
    reserve_slots: int
    playoff_week_start: int
    playoff_teams: int
    playoff_round_type: int
    trade_deadline: int
    waiver_budget: int
    chopped: bool
    league_type: int
    current_week: int
    last_scored_week: int
    teams: list[Team]
    my_roster_id: int
    raw_settings: dict = field(default_factory=dict, repr=False)

    @property
    def last_week(self) -> int:
        """The league's final fantasy week: the end of its playoffs, or Week 17."""
        if self.chopped or not self.playoff_week_start:
            return DEFAULT_LAST_WEEK
        rounds = max(1, math.ceil(math.log2(max(self.playoff_teams, 2))))
        # playoff_round_type: 0 one week a round, 1 a two-week final, 2 two weeks every round.
        weeks = {1: rounds + 1, 2: 2 * rounds}.get(self.playoff_round_type, rounds)
        return self.playoff_week_start + weeks - 1

    @property
    def playoff_weeks(self) -> list[int]:
        if self.chopped or not self.playoff_week_start:
            return []
        return list(range(self.playoff_week_start, self.last_week + 1))

    @property
    def remaining_weeks(self) -> list[int]:
        return list(range(max(self.current_week, self.last_scored_week + 1), self.last_week + 1))

    @property
    def me(self) -> Team:
        return self.team(self.my_roster_id)

    def team(self, roster_id: int) -> Team:
        for t in self.teams:
            if t.roster_id == roster_id:
                return t
        raise KeyError(roster_id)

    def alive_teams(self) -> list[Team]:
        return [t for t in self.teams if t.alive]

    def find_team(self, text: str) -> Team:
        """A partner named by team name, manager, or roster number ("Team 4")."""
        q = text.strip().lower()
        for t in self.teams:
            if q in {t.team_name.lower(), t.manager.lower(), str(t.roster_id), f"team {t.roster_id}"}:
                return t
        hits = [t for t in self.teams if q in t.team_name.lower() or q in t.manager.lower()]
        if len(hits) == 1:
            return hits[0]
        raise SleeperError(f"No single team in {self.name} matches {text!r}.")


def parse_league(
    name: str, league: dict, rosters: list[dict], users: list[dict], my_user_id: str, state: dict
) -> League:
    """Pure: Sleeper's league / rosters / users payloads into a `League`."""
    settings = league.get("settings") or {}
    positions = list(league.get("roster_positions") or [])
    slots = [p for p in positions if p not in {"BN", "IR", "TAXI"}]
    by_user = {str(u.get("user_id")): u for u in users}
    league_type = int(settings.get("type") or 0)
    chopped = league_type == LEAGUE_TYPE_CHOPPED
    budget = int(settings.get("waiver_budget") or 0)
    teams: list[Team] = []
    my_roster = None
    for r in rosters:
        owner = str(r.get("owner_id") or "")
        user = by_user.get(owner, {})
        rs = r.get("settings") or {}
        players = [str(p) for p in (r.get("players") or [])]
        pf = float(rs.get("fpts") or 0) + float(rs.get("fpts_decimal") or 0) / 100
        team = Team(
            roster_id=int(r["roster_id"]),
            owner_id=owner,
            manager=str(user.get("display_name") or f"Team {r['roster_id']}"),
            team_name=str(((user.get("metadata") or {}).get("team_name")) or ""),
            players=players,
            starters=[str(p) for p in (r.get("starters") or []) if p and p != "0"],
            wins=int(rs.get("wins") or 0),
            losses=int(rs.get("losses") or 0),
            ties=int(rs.get("ties") or 0),
            points_for=round(pf, 2),
            faab_left=max(0, budget - int(rs.get("waiver_budget_used") or 0)),
            # A chopped team's roster is emptied onto waivers.
            alive=not (chopped and not players),
        )
        teams.append(team)
        if owner == my_user_id:
            my_roster = team.roster_id
    if my_roster is None:
        raise SleeperError(f"{name}: no roster in this league belongs to the configured username.")
    season = int(league.get("season") or state.get("season"))
    return League(
        name=name,
        league_id=str(league["league_id"]),
        title=str(league.get("name") or "").strip(),
        season=season,
        scoring={k: float(v) for k, v in (league.get("scoring_settings") or {}).items()},
        slots=slots,
        bench_slots=positions.count("BN"),
        reserve_slots=positions.count("IR"),
        playoff_week_start=int(settings.get("playoff_week_start") or 0),
        playoff_teams=int(settings.get("playoff_teams") or 0),
        playoff_round_type=int(settings.get("playoff_round_type") or 0),
        trade_deadline=int(settings.get("trade_deadline") or 0),
        waiver_budget=budget,
        chopped=chopped,
        league_type=league_type,
        current_week=int(state.get("week") or settings.get("leg") or 1),
        last_scored_week=int(settings.get("last_scored_leg") or 0),
        teams=sorted(teams, key=lambda t: t.roster_id),
        my_roster_id=my_roster,
        raw_settings=settings,
    )


def load_league(name: str, league_id: str, username: str) -> League:
    base = CACHE_DIR / "leagues" / league_id
    league = cached_get(f"{API}/league/{league_id}", base / "league.json", LIVE_MAX_AGE_S)
    rosters = cached_get(f"{API}/league/{league_id}/rosters", base / "rosters.json", LIVE_MAX_AGE_S)
    users = cached_get(f"{API}/league/{league_id}/users", base / "users.json", LIVE_MAX_AGE_S)
    if not isinstance(league, dict) or not league.get("league_id"):
        raise SleeperError(f"{name}: Sleeper has no league {league_id}.")
    return parse_league(name, league, rosters, users, fetch_user_id(username), fetch_state())
