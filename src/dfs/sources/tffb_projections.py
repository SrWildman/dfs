"""DraftKings projections from The Fantasy Footballers' DFS Pass optimizer.

legacy/README.md flagged this as needing a real look at the post-login DOM
before porting -- the old fantasy_footballers/scraper.py just opened the
optimizer in a real browser and told a human to click "Projections" by hand.
That DOM turned out to be a third-party (dfsforecast.com/"FTN Optimizer")
app embedded in an iframe with its own entitlement token.

Two more-obvious approaches were tried and rejected before this one:
  - TFFB's own (non-iframe) per-position rankings pages
    (dfs-pass-weekly-dfs-rankings-draftkings/?position=X) have Player/Opp/
    Pts/Salary but no ownership at all -- ruled out for inconsistency with
    wanting one shape covering both.
  - Clicking the optimizer's own "Projections" export button and capturing
    the resulting CSV download (matches a real manual download byte for
    byte: Id,Name,Position,Team,ProjPts,ProjOwn, 742 rows). Worked
    interactively but not headlessly: the Ant Design table it exports from
    never left its loading-skeleton state in a fresh Playwright profile,
    even after 12+ seconds. Console/network capture during that hang showed
    the real data call succeeding fine (200, full payload) -- two *other*
    calls (`user-groups`, `lineup-runs`) 400 with `{"error": "no saved
    settings"}` / `{"error": "No lineup runs for slate"}` (a fresh
    account/slate has neither yet), and something downstream of those
    evidently blocks the table from ever rendering. Not worth chasing
    further given the next option makes the whole question moot.

What actually works: the same page load fires
`GET partner.dfsforecast.com/nfl/players/dk/classic/fantasy_footballers/Main/{season}/{week}`
(bearer token minted from the iframe's entitlement token via an
`/auth/...` call the page also makes) and that response alone *is* the
export -- same 742 rows, same fields, confirmed byte-for-byte against a
manual download. So this captures that one response directly instead of
waiting on any UI to render or triggering any download.

Uses the shared authenticated browser profile from browser.py (`dfs auth
tffb`). The URL's season segment (e.g. "2026-ultimate-dfs-pass") is built
from ctx.season rather than hardcoded, since TFFB names each year's product
after the NFL season it covers -- same number nfl_calendar.current_season()
returns. No week selector is touched -- the optimizer defaults to the
current week, same constraint nfl_odds/dk_salaries already have.

ProjOwn is 0 for every player until TFFB actually computes ownership later
in the week (confirmed against a real manual download the same day this was
written) -- that's TFFB's data timing, not a scraping gap.
"""

from __future__ import annotations

import pandas as pd
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from dfs.browser import persistent_context
from dfs.log import get_logger
from dfs.sources.base import Source, SyncContext

log = get_logger("sources.tffb_projections")

OPTIMIZER_URL_TEMPLATE = (
    "https://www.thefantasyfootballers.com/{season}-ultimate-dfs-pass/dfs-pass-lineup-optimizer/"
)
PLAYERS_ENDPOINT_MARKER = "players/dk/classic"

# Order matters for the first six: PlayerPoolRaw's existing VLOOKUPs key off
# TFFBOptoRaw's exact column positions (B=Name, D=Team, E=ProjPts, F=ProjOwn).
# Everything after that is additive -- those formulas never reference past
# column F, so appending more fields here can't break them.
CORE_FIELDS = ["Id", "Name", "Position", "TeamAbbrev", "ProjPts", "ProjOwn"]
EXTRA_FIELDS = ["Opp", "Salary", "Ceiling", "ImpPts", "OU", "Spread", "Game", "GameStart", "Venue"]


class TffbProjectionsFetchError(Exception):
    pass


def _dst_nickname(full_team_name: str) -> str:
    """ "Los Angeles Chargers" -> "Chargers". Every NFL nickname is one word,
    so the last token is always right. The API's Team column for DST rows
    is an abbreviation ("LAC"), but PlayerPoolRaw's DST VLOOKUP key is
    DraftKings' own DST naming (DkSalClean's Name column, e.g. "Chargers")
    -- so Team gets overwritten with the nickname derived from Name at
    write time, without touching the live sheet's formulas."""
    return full_team_name.strip().rsplit(" ", 1)[-1]


def players_to_df(players: list[dict]) -> pd.DataFrame:
    """Raw `players` list from the API -> the legacy TFFBOptoRaw column
    shape (Id, Name, Position, Team, ProjPts, ProjOwn), plus whichever
    EXTRA_FIELDS are present (Opp, Salary, Ceiling, ImpPts, OU, Spread,
    Game, GameStart, Venue) appended after them."""
    if not players:
        raise TffbProjectionsFetchError("Optimizer returned no players.")
    missing = [f for f in CORE_FIELDS if f not in players[0]]
    if missing:
        raise TffbProjectionsFetchError(
            f"Optimizer player records are missing field(s) {missing}; TFFB may have changed their API shape."
        )
    fields = CORE_FIELDS + [f for f in EXTRA_FIELDS if f in players[0]]
    df = pd.DataFrame(players)[fields]
    return df.rename(columns={"TeamAbbrev": "Team"})


class TffbProjectionsSource(Source):
    name = "projections"
    needs_auth = True

    def fetch(self, ctx: SyncContext) -> pd.DataFrame:
        url = OPTIMIZER_URL_TEMPLATE.format(season=ctx.season)
        log.info("fetching tffb projections for week %s, season %s", ctx.week, ctx.season)
        with persistent_context("tffb", headless=True) as context:
            page = context.new_page()
            try:
                with page.expect_response(
                    lambda r: PLAYERS_ENDPOINT_MARKER in r.url, timeout=20000
                ) as resp_info:
                    page.goto(url, wait_until="domcontentloaded")
                resp = resp_info.value
            except PlaywrightTimeoutError as e:
                raise TffbProjectionsFetchError(
                    f"Optimizer's player data endpoint never responded at {url} -- "
                    "run `dfs auth tffb` if your session expired, or check the page "
                    "manually for a paywall."
                ) from e

            if not resp.ok:
                raise TffbProjectionsFetchError(
                    f"Optimizer's player endpoint returned {resp.status}: {resp.text()[:300]}"
                )
            players = resp.json().get("players", [])
            page.close()

        return players_to_df(players)

    def to_sheet_rows(self, df: pd.DataFrame) -> list[list]:
        df = df.copy()
        is_dst = df["Position"] == "DST"
        df.loc[is_dst, "Team"] = df.loc[is_dst, "Name"].apply(_dst_nickname)
        return super().to_sheet_rows(df)
