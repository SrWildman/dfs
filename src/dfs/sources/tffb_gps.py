"""Kyle Borgognoni's (@kyle_borg) weekly "Pace of Play: Matchups & Stacks
for Week N" article on TFFB -- Sam's existing subscription, so this is not
a new paid source (`docs/planning/PROMPT_GPS.md`).

**Step 0 findings (2026-09-26), against real Week 2/3 CSVs fetched through
the authenticated `tffb` profile:** plain UTF-8, comma-delimited, `\\r\\n`
line endings -- the earlier "binary" read elsewhere was the proxy/generic
fetch tooling choking on the CDN's `content-type: application/octet-stream`
header, not the actual bytes. Three header lines precede the real data (a
title row, a "Teams"/"Pace of Play" group-label banner row, then the real
column header on the third line) -- `GPS_EXPECTED_HEADER` below is that
third line, identical across both weeks checked. 32 data rows follow, one
per NFL team.

The real column header has three columns literally named "Team" (an
always-blank first one, the real DK-style team code, and the opponent's
full name) plus one named "Team2" (the team's own full name) -- `pandas.
read_csv` auto-dedupes these to `Team`/`Team.1`/`Team2`/`Team.2` on read,
the same shape `tffb_sos.py`'s own two same-named team columns already
produce (see its `_row_to_record`), so this isn't a new pattern to
invent.

**Model-implied team scores ARE in the CSV** (`Implied Total`), confirmed
against Sam's own example: Week 3's JAX row reads `29.0`, NE reads `17.5`
-- exactly "JAX 29, NE 17.5". GPS itself is the last column (`1`-`5`
range). Teams are keyed by DK-standard abbreviation in the real `Team`
column (`Team.1` after dedup) -- confirmed against every code seen live
(JAX/BUF/BAL/NE/SF/DET/DAL/GB/...), no drift found yet; `player_join.
normalize_team` still runs over it for the same reason every other source
does (catch a drift the moment one turns up, not before). The CSV's own
`TOTAL` column (each team's row repeats its game's combined total) isn't
kept here -- "Model Tot" is computed on the sheet side as home + away
`ImpliedTotal`, the same live-VLOOKUP-and-combine shape `Pace`'s own C7
addition already uses, rather than trusting the CSV's separately-computed
column to always agree with a simple sum (it happened to, in the one pair
checked, but there's no reason to depend on that when the inputs to
recompute it are already right there per team).

**Not in `LIVE_SYNC_SOURCES`** -- this changes once a week, like `sos_*`/
`snaps`/`pbp`. **Timing:** the article goes up Wednesday. If this week's
isn't published yet, `fetch()` raises `TffbGpsFetchError` (never falls
back to last week's file -- `dfs week new` already clears `data/current/`,
and this module never reads anything off disk itself) -- the normal
`sync.py` catch-and-`record_failure` path turns that into "log a warning
and move on", the same as any other source's fetch failure; no separate
mechanism needed for "not published yet" specifically.

**Fail-soft downstream, not here:** `fetch()` still follows the base
`Source` contract (raise on any failure, real or "not published yet" --
never return a partial frame). It's whoever reads this source's snapshot
back (`derived.py`'s `_attach_gps`, `sheet_views.py`'s Slate Grid/Board
formulas) that degrades gracefully to blank columns when there's nothing
on disk yet, the same pattern `pbp`/`team_metrics` already established.
"""

from __future__ import annotations

import io
import re

import pandas as pd
from playwright.sync_api import Page
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from dfs.browser import persistent_context
from dfs.log import get_logger
from dfs.player_join import normalize_team
from dfs.sources.base import Source, SyncContext

log = get_logger("sources.tffb_gps")

LISTING_URL = "https://www.thefantasyfootballers.com/dfs/"
# How many pages of the listing to search before giving up -- the current
# week's article is always fresh enough to be on page 1 in practice; page 2
# is just a margin of safety, not a real expectation of needing it.
_LISTING_PAGES_TO_SEARCH = 2

# The real header row (third line of the CSV, after pandas' automatic
# dedup of the three columns literally named "Team") -- verified identical
# across Week 2 and Week 3's real files. A drift here means TFFB changed
# the worksheet's shape; `fetch()` fails loudly rather than silently
# mis-mapping a shifted column.
GPS_EXPECTED_HEADER = [
    "Team",
    "Team.1",
    "Team2",
    "Implied Total",
    "Neutral Pace Rk",
    "Plays/G",
    "PROE %",
    "EPA/DB OFF",
    "EPA/RUSH OFF",
    "EPA/DB DEF",
    "EPA/RUSH DEF",
    "H/A",
    "Opp.",
    "Team.2",
    "TOTAL",
    "SLATE",
    "GPS",
]

GPS_COLUMNS = ["Team", "ImpliedTotal", "GPS"]


class TffbGpsFetchError(Exception):
    pass


def _is_pace_of_play_link(href: str, text: str, week: int) -> bool:
    """True if this link's href or text is Kyle Borg's Pace of Play
    article for `week` -- matched on `-week-{N}(-|/|$)` (a real word
    boundary, not a bare substring) so week 1 never matches week 10-19,
    since neither the slug nor the CSV filename follows a stable pattern
    otherwise (confirmed live: Week 1's slug started `nfl-dfs-pace-of-play-
    stacks-for-week-1-...`, Week 2/3's started `pace-of-play-matchups-
    stacks-for-week-N-...` -- different prefixes, "pace-of-play" and a
    week-boundary are the only two things both shapes share)."""
    haystack = f"{href} {text}".lower()
    if "pace-of-play" not in haystack and "pace of play" not in haystack:
        return False
    return re.search(rf"week[\s-]{week}(?![0-9])", haystack) is not None


def _find_article_url(page: Page, week: int) -> str | None:
    """Search TFFB's own `/dfs/` listing (never a constructed slug -- see
    module docstring) for this week's Pace of Play article. `None` if not
    found after searching `_LISTING_PAGES_TO_SEARCH` pages -- the caller
    turns that into "not published yet"."""
    for page_num in range(1, _LISTING_PAGES_TO_SEARCH + 1):
        url = LISTING_URL if page_num == 1 else f"{LISTING_URL}page/{page_num}/"
        page.goto(url, wait_until="domcontentloaded", timeout=30000)
        links = page.eval_on_selector_all("a", "els => els.map(e => [e.href, e.textContent.trim()])")
        for href, text in links:
            if _is_pace_of_play_link(href, text, week):
                return href
    return None


def _find_csv_url(page: Page, article_url: str) -> str | None:
    """The first `.csv` link inside the article body -- never a guessed
    filename (confirmed live: neither Week 2's nor Week 3's CSV filename
    follows a shared pattern)."""
    page.goto(article_url, wait_until="domcontentloaded", timeout=30000)
    links = page.eval_on_selector_all("a[href$='.csv']", "els => els.map(e => e.href)")
    return links[0] if links else None


def parse_gps_csv(text: str) -> pd.DataFrame:
    """Pure: the raw CSV text (already decoded) -> `GPS_COLUMNS` shape.
    Raises `TffbGpsFetchError` if the real header (third line) doesn't
    match `GPS_EXPECTED_HEADER` exactly -- see module docstring for why
    that's the right line to check and what a drift would mean."""
    df = pd.read_csv(io.StringIO(text), skiprows=2)
    header = list(df.columns)
    if header != GPS_EXPECTED_HEADER:
        raise TffbGpsFetchError(
            f"GPS CSV header {header!r} doesn't match the expected shape {GPS_EXPECTED_HEADER!r} -- "
            "TFFB may have changed this worksheet's columns."
        )
    out = pd.DataFrame(
        {
            "Team": df["Team.1"].apply(normalize_team),
            "ImpliedTotal": pd.to_numeric(df["Implied Total"], errors="coerce"),
            "GPS": pd.to_numeric(df["GPS"], errors="coerce"),
        }
    )
    return out[GPS_COLUMNS]


class TffbGpsSource(Source):
    name = "tffb_gps"
    needs_auth = True

    def fetch(self, ctx: SyncContext) -> pd.DataFrame:
        log.info("fetching TFFB Pace of Play (GPS) for week %s", ctx.week)
        with persistent_context("tffb", headless=True) as context:
            page = context.new_page()
            try:
                article_url = _find_article_url(page, ctx.week)
                if article_url is None:
                    raise TffbGpsFetchError(
                        f"GPS not published for Week {ctx.week} yet -- no matching article found "
                        f"in TFFB's {LISTING_URL} listing."
                    )
                csv_url = _find_csv_url(page, article_url)
                if csv_url is None:
                    raise TffbGpsFetchError(f"No .csv link found in the GPS article body at {article_url}.")
                resp = context.request.get(csv_url)
                if resp.status != 200:
                    raise TffbGpsFetchError(f"GPS CSV fetch failed ({resp.status}) at {csv_url}.")
                text = resp.body().decode("utf-8")
            except PlaywrightTimeoutError as e:
                raise TffbGpsFetchError(
                    "TFFB's DFS listing or article page never finished loading -- "
                    "run `dfs auth tffb` if your session expired, or check the site manually."
                ) from e
            finally:
                page.close()

        df = parse_gps_csv(text)
        if df.empty:
            raise TffbGpsFetchError("GPS CSV parsed to zero rows.")
        unmatched = df.loc[df["Team"] == "", "Team"]
        if len(unmatched):
            log.warning("GPS: %d row(s) had no parseable team code", len(unmatched))
        return df
