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

**A secondary slate** (`slate.py`: Thanksgiving, a Saturday, Christmas). The
optimizer's slate dropdown lists the week's Classic slates by name (its own
`/slates/dk/{season}/{week}` call: "Main", "Classic (Afternoon Only)", and on
a holiday week "Week 12 Classic (Thu)"), and picking one swaps `Main` in the
player call's path for that name, URL-encoded (seen in Chrome on 2026-11-23,
with the afternoon-only slate's own, concentrated ownership). So on a
secondary slate this picks the slate the way Sam would: the same page load,
then that name in the dropdown (`pick_slate_name`), and keeps the player
response for it. Every player's game must be on the slate's day, or the fetch
fails rather than build the slate from another slate's players. The main
slate's fetch is unchanged.
"""

from __future__ import annotations

import re
from datetime import date
from urllib.parse import unquote

import pandas as pd
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from dfs.browser import persistent_context
from dfs.log import get_logger
from dfs.player_join import adopt_dk_spelling
from dfs.sources.base import Source, SourcePending, SyncContext

log = get_logger("sources.tffb_projections")

OPTIMIZER_URL_TEMPLATE = (
    "https://www.thefantasyfootballers.com/{season}-ultimate-dfs-pass/dfs-pass-lineup-optimizer/"
)
PLAYERS_ENDPOINT_MARKER = "players/dk/classic"
SLATES_ENDPOINT_MARKER = "/slates/dk/"
FRAME_SELECTOR = "iframe[src*='dfsforecast']"
SLATE_SETTLE_MS = 3000  # after the Main list loads, before the dropdown is used (the page's other calls)

# The words a TFFB slate name may use for a secondary slate's day (`slate.DAYS` keys).
DAY_WORDS: dict[str, tuple[str, ...]] = {
    "mon": ("mon", "monday"),
    "tue": ("tue", "tues", "tuesday"),
    "wed": ("wed", "wednesday"),
    "thu": ("thu", "thur", "thurs", "thursday", "thanksgiving"),
    "fri": ("fri", "friday", "christmas", "xmas"),
    "sat": ("sat", "saturday"),
}

# Order matters for the first six: PlayerPoolRaw's existing VLOOKUPs key off
# TFFBOptoRaw's exact column positions (B=Name, D=Team, E=ProjPts, F=ProjOwn).
# Everything after that is additive -- those formulas never reference past
# column F, so appending more fields here can't break them.
CORE_FIELDS = ["Id", "Name", "Position", "TeamAbbrev", "ProjPts", "ProjOwn"]
EXTRA_FIELDS = ["Opp", "Salary", "Ceiling", "ImpPts", "OU", "Spread", "Game", "GameStart", "Venue"]
# TFFBOptoRaw's columns, A onward, when TFFB sends every field. PlayerPoolRaw VLOOKUPs into this tab by column
# NUMBER (`sheet_literal_lookups.PLAYER_POOL_RAW_LOOKUPS`; a test ties the two together), so a new field goes
# at the end of EXTRA_FIELDS, never in the middle.
SHEET_COLUMNS = ["Team" if f == "TeamAbbrev" else f for f in CORE_FIELDS] + EXTRA_FIELDS


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


def pick_slate_name(names: list[str], key: str) -> str:
    """The one TFFB Classic slate name for secondary slate `key`: a name that says the slate's day ("Week 12
    Classic (Thu)", "Classic (Thanksgiving)") and is not a range of days ("Classic (Thu-Mon)"). Raises
    SourcePending when there is none yet (TFFB posts a holiday slate early in the week) and
    TffbProjectionsFetchError when more than one fits: never a guess."""
    words = DAY_WORDS.get(key, ())
    fits = []
    for name in names:
        lowered = name.lower()
        if "-" in lowered or "showdown" in lowered:
            continue
        tokens = set(re.findall(r"[a-z]+", lowered))
        if tokens & set(words):
            fits.append(name)
    if not fits:
        raise SourcePending(
            f"TFFB has no Classic slate for the {key} slate yet (its slates: {', '.join(names) or 'none'})."
        )
    if len(fits) > 1:
        raise TffbProjectionsFetchError(
            f"More than one TFFB slate could be the {key} slate ({', '.join(fits)}): say which in an issue "
            "rather than guess."
        )
    return fits[0]


def classic_names(slates_payload: dict) -> list[str]:
    """The Classic slate names from the optimizer's `/slates/` response."""
    return [str(s.get("name", "")) for s in slates_payload.get("classic", []) if s.get("name")]


def off_day_games(players: list[dict], day: date) -> list[str]:
    """The games among `players` that are not on `day` (by `GameStart`'s date, as TFFB writes it in Eastern
    time); every game when none has a start."""
    games = {}
    for p in players:
        games.setdefault(str(p.get("Game") or p.get("TeamAbbrev") or "?"), str(p.get("GameStart") or ""))
    return sorted(g for g, start in games.items() if start[:10] != day.isoformat())


class TffbProjectionsSource(Source):
    name = "projections"
    needs_auth = True

    def fetch(self, ctx: SyncContext) -> pd.DataFrame:
        from dfs import slate

        if not slate.is_main():
            key = slate.current()
            players = self.fetch_slate(ctx, key)
            day = slate.slate_date(key, ctx.season, ctx.week)
            wrong = off_day_games(players, day)
            if wrong:
                raise TffbProjectionsFetchError(
                    f"TFFB's slate for the {key} slate has games off {day:%A %b %d}: {', '.join(wrong[:6])}."
                )
            return players_to_df(players)
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

    def fetch_slate(self, ctx: SyncContext, key: str, name: str | None = None) -> list[dict]:
        """A secondary slate's players: the optimizer's own page load, then its slate (`name`, else the one
        `pick_slate_name` finds in the optimizer's slate list) picked in the dropdown as Sam would; the
        player response for that slate's path."""
        url = OPTIMIZER_URL_TEMPLATE.format(season=ctx.season)
        log.info("fetching tffb projections for the %s slate, week %s", key, ctx.week)
        with persistent_context("tffb", headless=True) as context:
            page = context.new_page()
            slates: list[dict] = []
            page.on(
                "response",
                lambda r: slates.append(r.json()) if SLATES_ENDPOINT_MARKER in r.url and r.ok else None,
            )
            try:
                # the page is ready once its own (Main) player list has loaded; the slate list comes with it
                with page.expect_response(lambda r: PLAYERS_ENDPOINT_MARKER in r.url, timeout=20000):
                    page.goto(url, wait_until="domcontentloaded")
                page.wait_for_timeout(SLATE_SETTLE_MS)
                if not slates and name is None:
                    raise TffbProjectionsFetchError("TFFB's optimizer never listed its slates.")
                chosen = name or pick_slate_name(classic_names(slates[-1]), key)
                marker = f"{PLAYERS_ENDPOINT_MARKER}/fantasy_footballers/{chosen}/"
                frame = page.frame_locator(FRAME_SELECTOR)
                with page.expect_response(lambda r: marker in unquote(r.url), timeout=20000) as resp_info:
                    frame.locator(".ant-select-selector", has_text="Main").first.click(timeout=10000)
                    frame.locator(".ant-select-item-option", has_text=chosen).first.click(timeout=10000)
                resp = resp_info.value
            except PlaywrightTimeoutError as e:
                raise TffbProjectionsFetchError(
                    f"TFFB's optimizer did not load the {key} slate at {url} -- run `dfs auth tffb` if your "
                    "session expired."
                ) from e
            if not resp.ok:
                raise TffbProjectionsFetchError(
                    f"Optimizer's player endpoint returned {resp.status} for {chosen!r}: {resp.text()[:300]}"
                )
            players = resp.json().get("players", [])
            page.close()
        log.info("tffb slate %r: %d players", chosen, len(players))
        return players

    def to_sheet_rows(self, df: pd.DataFrame) -> list[list]:
        df = df.copy()
        is_dst = df["Position"] == "DST"
        df.loc[is_dst, "Team"] = df.loc[is_dst, "Name"].apply(_dst_nickname)
        # Plan N: DK's spelling on the tab PlayerPoolRaw reads Pts / Own% from by exact name (Joe Milton III).
        try:
            from dfs import store

            salaries = store.load_current("draftkings")
        except FileNotFoundError:
            salaries = None
        df.loc[~is_dst, "Name"] = adopt_dk_spelling(df.loc[~is_dst, "Name"], df.loc[~is_dst, "Id"], salaries)
        return super().to_sheet_rows(df)
