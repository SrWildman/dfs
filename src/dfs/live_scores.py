"""Every started player's DraftKings points so far, from ESPN's public box scores: late swap's state with no
download (`dfs lineups late-swap --state live`).

`site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard?dates=YYYYMMDD` lists a day's games with their
status (period and clock); `.../summary?event=<id>` holds each game's box score, player by player. Same public
site family and terms as `late_swap_news` (personal, non-commercial use); nothing here touches DraftKings.

**Scoring** is DraftKings Classic (`dk_scoring`'s table) applied to the box score's REALIZED stats: passing
yards 0.04 and a 300-yard bonus, passing TD 4, interception -1, rushing / receiving yards 0.1 and a 100-yard
bonus each, TD 6, reception 1, fumble lost -1, kick and punt return TD 6. A defense gets sacks 1,
interceptions 2, the opponent's fumbles lost (recoveries) 2, defensive and return TDs 6, safeties 2 and the
points-allowed tier (10 / 7 / 4 / 1 / 0 / -1 / -4), the tier only once the game is final. What the box score
does not carry is left out (blocked kicks), and a two-point conversion is read from the scoring plays'
text, so a defense can be a couple of points short. Against the real Weeks 4-6 points of 601 scoring
players it was exact for 99.0% (mean error 0.017; `docs/RESEARCH.md` R9).

**Game progress** is the share of regulation played, from the period and clock (`game_progress`), so the
share of a game left comes from the game, not from a flat clock.

Pure parsing (`parse_scoreboard`, `parse_box`, `*_points`) plus `fetch_scores`, which reads the network and
says so with `ScoresError` (late swap then runs as today)."""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import httpx

from dfs.player_join import normalize_name, normalize_team

SCOREBOARD_URL = "https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard"
SUMMARY_URL = "https://site.api.espn.com/apis/site/v2/sports/football/nfl/summary"
TIMEOUT_SECONDS = 20.0
REGULATION_SECONDS = 3600.0
QUARTER_SECONDS = 900.0
PASS_BONUS_YARDS, YARD_BONUS_YARDS, YARD_BONUS = 300.0, 100.0, 3.0
# DraftKings' points-allowed tiers: (highest points allowed in the tier, DST points)
POINTS_ALLOWED_TIERS = ((0, 10.0), (6, 7.0), (13, 4.0), (20, 1.0), (27, 0.0), (34, -1.0))
POINTS_ALLOWED_WORST = -4.0


class ScoresError(Exception):
    """ESPN's scores could not be read; late swap carries on without them."""


@dataclass(frozen=True)
class Game:
    event_id: str
    state: str  # "pre" | "in" | "post"
    period: int
    clock_seconds: float
    teams: tuple[str, str]  # normalised team codes (home, away)

    @property
    def progress(self) -> float:
        return game_progress(self.state, self.period, self.clock_seconds)


def game_progress(state: str, period: int, clock_seconds: float) -> float:
    """The share of regulation played: 0 before kickoff, 1 once final (overtime counts as played)."""
    if state == "post":
        return 1.0
    if state != "in":
        return 0.0
    if period > 4:
        return 1.0
    played = (period - 1) * QUARTER_SECONDS + (
        QUARTER_SECONDS - min(max(clock_seconds, 0.0), QUARTER_SECONDS)
    )
    return min(max(played / REGULATION_SECONDS, 0.0), 1.0)


def parse_scoreboard(payload: dict) -> list[Game]:
    games = []
    for event in payload.get("events", []):
        status = event.get("status") or {}
        competition = (event.get("competitions") or [{}])[0]
        teams = {
            c.get("homeAway"): normalize_team((c.get("team") or {}).get("abbreviation"))
            for c in competition.get("competitors", [])
        }
        if not event.get("id") or "home" not in teams or "away" not in teams:
            continue
        games.append(
            Game(
                str(event["id"]),
                str((status.get("type") or {}).get("state", "")),
                int(status.get("period") or 0),
                float(status.get("clock") or 0.0),
                (teams["home"], teams["away"]),
            )
        )
    return games


# ---------------------------------------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------------------------------------


def offense_points(s: dict[str, float]) -> float:
    """DraftKings points of one offensive player's realized stats."""
    pass_yd, rush_yd, rec_yd = s.get("pass_yd", 0.0), s.get("rush_yd", 0.0), s.get("rec_yd", 0.0)
    total = (
        0.04 * pass_yd
        + 4.0 * s.get("pass_td", 0.0)
        - 1.0 * s.get("pass_int", 0.0)
        + 0.1 * (rush_yd + rec_yd)
        + 6.0 * (s.get("rush_td", 0.0) + s.get("rec_td", 0.0) + s.get("ret_td", 0.0))
        + 1.0 * s.get("rec", 0.0)
        - 1.0 * s.get("fum_lost", 0.0)
        + 2.0 * s.get("two_pt", 0.0)
    )
    total += YARD_BONUS * (
        (pass_yd >= PASS_BONUS_YARDS) + (rush_yd >= YARD_BONUS_YARDS) + (rec_yd >= YARD_BONUS_YARDS)
    )
    return float(total)


def points_allowed_score(points_allowed: float) -> float:
    for ceiling, score in POINTS_ALLOWED_TIERS:
        if points_allowed <= ceiling:
            return score
    return POINTS_ALLOWED_WORST


def dst_points(s: dict[str, float], *, final: bool) -> float:
    """DraftKings points of a defense's realized stats; the points-allowed tier only counts once the game is
    final (before that it is not earned yet)."""
    total = (
        1.0 * s.get("sack", 0.0)
        + 2.0 * s.get("int", 0.0)
        + 2.0 * s.get("fum_rec", 0.0)
        + 6.0 * s.get("def_td", 0.0)
        + 2.0 * s.get("safety", 0.0)
    )
    if final:
        total += points_allowed_score(s.get("points_allowed", 0.0))
    return float(total)


# ---------------------------------------------------------------------------------------------------------
# One box score
# ---------------------------------------------------------------------------------------------------------

_NUMBER = re.compile(r"-?\d+(?:\.\d+)?")
# "(Kirk Cousins Pass to Cody White for Two-Point Conversion)", "(Jayden Daniels Rush for Two-Point ...)"
_TWO_POINT = re.compile(
    r"\(([^()]+?) (?:Pass to ([^()]+?)|Rush|Run) for Two-Point Conversion\)", re.IGNORECASE
)


def _num(text: object) -> float:
    found = _NUMBER.search(str(text))
    return float(found.group()) if found else 0.0


# category -> {ESPN key: our stat}; "interceptions" is a passing stat in one category, defensive in another
_PLAYER_KEYS = {
    "passing": {"passingYards": "pass_yd", "passingTouchdowns": "pass_td", "interceptions": "pass_int"},
    "rushing": {"rushingYards": "rush_yd", "rushingTouchdowns": "rush_td"},
    "receiving": {"receptions": "rec", "receivingYards": "rec_yd", "receivingTouchdowns": "rec_td"},
    "fumbles": {"fumblesLost": "fum_lost"},
    "kickReturns": {"kickReturnTouchdowns": "ret_td"},
    "puntReturns": {"puntReturnTouchdowns": "ret_td"},
}
_TEAM_KEYS = {
    "defensive": {"sacks": "sack", "defensiveTouchdowns": "def_td"},
    "fumbles": {"fumblesLost": "fum_lost_own"},
    "interceptions": {
        "interceptions": "int"
    },  # an interception return TD is already in `defensiveTouchdowns`
    "kickReturns": {"kickReturnTouchdowns": "def_td"},
    "puntReturns": {"puntReturnTouchdowns": "def_td"},
}


@dataclass
class Box:
    """One game's realized stats: `players` by normalised name, `teams` (the defense's stats) by team code."""

    players: dict[str, dict[str, float]] = field(default_factory=dict)
    teams: dict[str, dict[str, float]] = field(default_factory=dict)
    nicknames: dict[str, str] = field(default_factory=dict)  # team code -> normalised nickname ("chiefs")
    player_team: dict[str, str] = field(default_factory=dict)  # normalised name -> team code


def parse_box(summary: dict, game: Game) -> Box:
    """The realized stats of one `summary` payload. A team's defensive stats include its opponent's fumbles
    lost (recoveries) and the points its opponent's offense scored (the opponent's total less the defensive
    and return TDs and the safeties scored against the opponent's own offense)."""
    box = Box()
    ids: dict[str, str] = {}
    for entry in (summary.get("boxscore") or {}).get("teams", []):
        team = entry.get("team") or {}
        code = normalize_team(team.get("abbreviation"))
        ids[str(team.get("id"))] = code
        box.nicknames[code] = normalize_name(team.get("name") or team.get("shortDisplayName") or "")
        box.teams.setdefault(code, {})
    for entry in (summary.get("boxscore") or {}).get("players", []):
        code = normalize_team((entry.get("team") or {}).get("abbreviation"))
        team = box.teams.setdefault(code, {})
        for category in entry.get("statistics", []):
            name, keys = category.get("name"), category.get("keys") or []
            for athlete in category.get("athletes", []):
                label = normalize_name((athlete.get("athlete") or {}).get("displayName") or "")
                stats = athlete.get("stats") or []
                if label:
                    box.player_team.setdefault(label, code)
                for key, ours in _PLAYER_KEYS.get(name, {}).items():
                    if key in keys and label:
                        value = _num(stats[keys.index(key)]) if keys.index(key) < len(stats) else 0.0
                        row = box.players.setdefault(label, {})
                        row[ours] = row.get(ours, 0.0) + value
                for key, ours in _TEAM_KEYS.get(name, {}).items():
                    if key in keys and keys.index(key) < len(stats):
                        team[ours] = team.get(ours, 0.0) + _num(stats[keys.index(key)])
    for play in summary.get("scoringPlays", []):  # a good two-point try scores for the passer and the catcher
        found = _TWO_POINT.search(str(play.get("text", "")))
        if found:
            for who in found.groups():
                if who:
                    row = box.players.setdefault(normalize_name(who), {})
                    row["two_pt"] = row.get("two_pt", 0.0) + 1.0
    # points scored against each defense, fumble recoveries (the opponent's fumbles lost) and safeties
    scores = {}
    for competitor in ((summary.get("header") or {}).get("competitions") or [{}])[0].get("competitors", []):
        scores[normalize_team((competitor.get("team") or {}).get("abbreviation"))] = _num(
            competitor.get("score")
        )
    safeties: dict[str, float] = {}
    for play in summary.get("scoringPlays", []):
        if "safety" in str((play.get("type") or {}).get("text", "")).lower():
            code = ids.get(str((play.get("team") or {}).get("id")))
            if code:
                safeties[code] = safeties.get(code, 0.0) + 1.0
    teams = list(game.teams)
    for code in teams:
        other = next((t for t in teams if t != code), None)
        if other is None or code not in box.teams:
            continue
        mine, theirs = box.teams[code], box.teams.get(other, {})
        mine["fum_rec"] = theirs.get(
            "fum_lost_own", 0.0
        )  # the opponent's fumbles lost are this defense's recoveries
        mine["safety"] = safeties.get(code, 0.0)
        # what the OTHER side scored on defense or special teams is not points allowed by this defense
        mine["points_allowed"] = max(
            scores.get(other, 0.0) - 6.0 * theirs.get("def_td", 0.0) - 2.0 * safeties.get(code, 0.0), 0.0
        )
    return box


# ---------------------------------------------------------------------------------------------------------
# The whole slate
# ---------------------------------------------------------------------------------------------------------


@dataclass
class LiveScores:
    """What `slate_state` needs: `fpts` (normalised name -> DK points so far, defenses under their nickname),
    `progress` (team code -> share of regulation played) and the games it came from."""

    fpts: dict[str, float]
    progress: dict[str, float]
    games: list[Game]
    fetched_at: datetime
    notes: list[str] = field(default_factory=list)
    by_team: dict[str, dict[str, float]] = field(
        default_factory=dict
    )  # team code -> {normalised name: points}

    def describe(self) -> str:
        live = sum(1 for g in self.games if g.state == "in")
        done = sum(1 for g in self.games if g.state == "post")
        return f"ESPN box scores: {live} game(s) in progress, {done} final, read {self.fetched_at:%H:%M} UTC"


def score_box(box: Box, game: Game) -> tuple[dict[str, float], dict[str, float]]:
    """`(offensive players' points, defenses' points)` of one game's box, by normalised name."""
    players = {label: offense_points(stats) for label, stats in box.players.items()}
    final = game.state == "post"
    defenses = {
        box.nicknames[code]: dst_points(stats, final=final)
        for code, stats in box.teams.items()
        if code in box.nicknames
    }
    return players, defenses


def fetch_scores(
    dates: Iterable[str],
    *,
    client: httpx.Client | None = None,
    cache_dir: Path | None = None,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> LiveScores:
    """Read the scoreboard of each `dates` (`YYYYMMDD`) and the box score of every game under way or final. A
    FINAL game's box score is kept in `cache_dir` (it never changes); one in progress is always re-read.
    Raises `ScoresError` when the scoreboard cannot be read; a game whose box fails is skipped and noted."""
    own = client is None
    http = client or httpx.Client(timeout=TIMEOUT_SECONDS, follow_redirects=True)
    games: list[Game] = []
    notes: list[str] = []
    fpts: dict[str, float] = {}
    by_team: dict[str, dict[str, float]] = {}
    progress: dict[str, float] = {}
    try:
        for date in sorted(set(dates)):
            try:
                response = http.get(SCOREBOARD_URL, params={"dates": date})
                response.raise_for_status()
                games += parse_scoreboard(response.json())
            except (httpx.HTTPError, ValueError) as e:
                raise ScoresError(f"scoreboard {date}: {e}") from e
        seen: set[str] = set()
        for game in games:
            if game.event_id in seen:
                continue
            seen.add(game.event_id)
            for team in game.teams:
                progress[team] = game.progress
            if game.state not in ("in", "post"):
                continue
            payload = _read_cache(cache_dir, game) if game.state == "post" else None
            if payload is None:
                try:
                    response = http.get(SUMMARY_URL, params={"event": game.event_id})
                    response.raise_for_status()
                    payload = response.json()
                except (httpx.HTTPError, ValueError) as e:
                    notes.append(f"box score of {'-'.join(game.teams)} not read ({e})")
                    continue
                if game.state == "post":
                    _write_cache(cache_dir, game, payload)
            box = parse_box(payload, game)
            players, defenses = score_box(box, game)
            fpts.update(players)
            fpts.update(defenses)
            for label, points in players.items():
                by_team.setdefault(box.player_team.get(label, ""), {})[label] = points
    finally:
        if own:
            http.close()
    return LiveScores(fpts, progress, games, now(), notes, by_team)


def _cache_path(cache_dir: Path | None, game: Game) -> Path | None:
    return None if cache_dir is None else Path(cache_dir) / f"{game.event_id}.json"


def _read_cache(cache_dir: Path | None, game: Game) -> dict | None:
    path = _cache_path(cache_dir, game)
    try:
        return json.loads(path.read_text()) if path is not None and path.exists() else None
    except (OSError, ValueError):
        return None


def _write_cache(cache_dir: Path | None, game: Game, payload: dict) -> None:
    path = _cache_path(cache_dir, game)
    if path is None:
        return
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload))
    except OSError:
        pass


def kickoff_dates(kickoffs: Iterable[datetime | None]) -> list[str]:
    """The Eastern calendar dates (`YYYYMMDD`) the kickoffs fall on: the scoreboards to read."""
    import pandas as pd

    from dfs.kickoff import KICKOFF_TIMEZONE

    return sorted(
        {pd.Timestamp(k).tz_convert(KICKOFF_TIMEZONE).strftime("%Y%m%d") for k in kickoffs if k is not None}
    )


def _first_last(key: str) -> tuple[str, str]:
    parts = key.split()
    return (parts[0], parts[-1]) if parts else ("", "")


def points_for_specs(scores: LiveScores, specs: Iterable) -> dict[str, float]:
    """`{normalised name: points so far}` for the specs whose game has started.

    A spec (an EdgeRaw player: `id` his name, `team`) takes ESPN's points under his own normalised name;
    failing that, under a name on the SAME team with the same last name and a first name that starts the
    same way (DK's "Mitch Tinsley" is ESPN's "Mitchell Tinsley"). A player ESPN lists nowhere has no stats
    yet (its box score only names players with a stat), so he is left out and counts as 0."""
    out: dict[str, float] = {}
    for spec in specs:
        team = normalize_team(spec.team)
        if scores.progress.get(team, 0.0) <= 0:
            continue
        key = normalize_name(spec.id)
        if key in scores.fpts:
            out[key] = scores.fpts[key]
            continue
        first, last = _first_last(key)
        near = [
            points
            for name, points in scores.by_team.get(team, {}).items()
            if _first_last(name)[1] == last
            and (_first_last(name)[0].startswith(first[:3]) or first.startswith(_first_last(name)[0][:3]))
        ]
        if len(near) == 1:
            out[key] = near[0]
    return out
