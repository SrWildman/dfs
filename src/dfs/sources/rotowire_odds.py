"""NFL odds from Rotowire's DraftKings market feed.

Ported from scrapers/nfl_odds/nfl_odds_scraper.py -- the one component of
the old repo that actually worked end to end (pure requests, no browser).
Returns a tidy DataFrame instead of writing a CSV directly; to_sheet_rows()
reproduces the legacy 2-row header the live "oddsraw" tab already has, so
other tabs/formulas that reference it (e.g. oddsFinal) keep working.
"""

from __future__ import annotations

import pandas as pd
import httpx

from dfs.log import get_logger
from dfs.sources.base import Source, SyncContext

log = get_logger("sources.nfl_odds")

BASE_URL = "https://www.rotowire.com/betting/nfl/tables/nfl-games-by-market.php"
DEFAULT_TIMEOUT = 30
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/139.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "Accept-Language": "en-US,en;q=0.5",
    "Referer": "https://www.rotowire.com/betting/nfl/odds",
    "Connection": "keep-alive",
}


class OddsFetchError(Exception):
    pass


def _format_moneyline(value) -> str:
    if not value:
        return ""
    try:
        ml = int(value)
    except (ValueError, TypeError):
        return str(value)
    return f"+{ml}" if ml > 0 else str(ml)


def _format_spread(value) -> str:
    if not value:
        return ""
    try:
        sp = float(value)
    except (ValueError, TypeError):
        return str(value)
    return f"+{sp}" if sp > 0 else str(sp)


def parse_draftkings_odds(raw_games: list[dict]) -> list[dict]:
    parsed = []
    for game in raw_games:
        dk_moneyline = game.get("draftkings_moneyline")
        dk_spread = game.get("draftkings_spread")
        dk_ou = game.get("draftkings_ou")
        if not any([dk_moneyline, dk_spread, dk_ou]):
            continue
        parsed.append(
            {
                "team": game.get("nickname", ""),
                "date": game.get("gameDate", ""),
                "moneyline": _format_moneyline(dk_moneyline),
                "spread": _format_spread(dk_spread),
                "total": str(dk_ou) if dk_ou else "",
                "team_points": str(game.get("draftkings_teamTotalOver") or ""),
                "home_away": game.get("homeAway", ""),
                "abbr": game.get("abbr", ""),
            }
        )
    return parsed


class RotowireOddsSource(Source):
    name = "nfl_odds"

    def fetch(self, ctx: SyncContext) -> pd.DataFrame:
        params = {"week": str(ctx.week), "season": str(ctx.season)}
        log.info("fetching odds for week %s, season %s", ctx.week, ctx.season)
        try:
            resp = httpx.get(BASE_URL, params=params, headers=_HEADERS, timeout=DEFAULT_TIMEOUT)
            resp.raise_for_status()
            raw = resp.json()
        except httpx.HTTPError as e:
            raise OddsFetchError(f"Request to Rotowire failed: {e}") from e
        except ValueError as e:
            raise OddsFetchError(f"Rotowire response was not valid JSON: {e}") from e

        rows = parse_draftkings_odds(raw)
        if not rows:
            raise OddsFetchError(
                f"No DraftKings odds found for week {ctx.week}, season {ctx.season} "
                f"(got {len(raw)} games from Rotowire, none had DK lines)."
            )
        return pd.DataFrame(rows)

    def to_sheet_rows(self, df: pd.DataFrame) -> list[list]:
        rows: list[list] = [
            ["", "", "Win", "Cover", "Total Points", "Total Touchdowns", "Team Points", "Team TDs", "Team TDs"],
            ["Team", "Date", "Moneyline", "Spread", "Over-Under", "Over-Under", "Over-Under", "Over-Under", "Over-Under"],
        ]
        for _, r in df.iterrows():
            rows.append([r["team"], r["date"], r["moneyline"], r["spread"], r["total"], "", r["team_points"], "", ""])
        return rows
