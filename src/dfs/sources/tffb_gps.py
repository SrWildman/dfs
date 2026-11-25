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
isn't published yet, `fetch()` raises `TffbGpsPending` (a `SourcePending`,
and a `TffbGpsFetchError`; never falls back to last week's file -- `dfs week
new` already clears `data/current/`, and this module never reads anything
off disk itself) -- `sync.py` reports that as **pending** (a yellow line,
exit code 0, the Week page shows it as pending), not as a failure. Every
other problem (a missing CSV link, a bad status, a changed header, a page
that never loaded) is still a `TffbGpsFetchError` and still fails.

**Fail-soft downstream, not here:** `fetch()` still follows the base
`Source` contract (raise on any failure, or `SourcePending` for "not
published yet" -- never return a partial frame). It's whoever reads this source's snapshot
back (`derived.py`'s `_attach_gps`, `sheet_views.py`'s Slate Grid/Board
formulas) that degrades gracefully to blank columns when there's nothing
on disk yet, the same pattern `pbp`/`team_metrics` already established.

**A named slate (2026-11-24).** Thanksgiving's article carries no week number
(`pace-of-play-dfs-stacks-for-thanksgiving-day-slate`), so the article is found by
the slate it names, not only the week (`_is_pace_of_play_link`):

- **The main slate** keeps the `week N` match, and never takes an article that
  names a secondary slate (a day, Thanksgiving, Black Friday, Christmas), so a
  "Week 12 Thanksgiving" article is never the main slate's. In the playoffs
  (`nfl_calendar` stops at week 18, so there is no week number) it takes the
  article that names the round of today's date (`playoff_round`).
- **A secondary slate** (`slate.py`) takes an article that names it: its day
  (`saturday`) or a holiday on its date (Thanksgiving, Black Friday, Christmas).
  An article that also names a week must name this week, one that names a playoff
  round must name this round, and one that names another day's slate is not its.
- **`[slates.<key>] gps_url`** in config.toml is used as is, without the listing,
  for any title the matcher doesn't know.

**Fewer teams.** Thanksgiving's article (checked live 2026-11-24) links the whole
week's CSV, 32 teams with a `SLATE` column (`THANKS`, `BLACK`, `MAIN`, `SNF`, ...).
A secondary slate keeps only its own teams, the ones in its DraftKings salaries
(`keep_slate_teams`; Sam, Nov 24: by the salaries, not TFFB's `SLATE` labels), so
Thanksgiving's GPSRaw has 6 rows; any row count above zero is fine. With no salaries
saved yet every row is kept, with a warning. The main slate keeps every row, as
before. Either way the CSV is checked against the slate's salaries
(`check_slate_teams`): a slate team it lacks is a warning, and a CSV with none of
the slate's teams is another slate's article and fails.
"""

from __future__ import annotations

import io
import re
from collections.abc import Iterable
from datetime import date

import pandas as pd
from playwright.sync_api import Page
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from dfs.browser import persistent_context
from dfs.log import get_logger
from dfs.player_join import normalize_team
from dfs.sources.base import Source, SourcePending, SyncContext

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


class TffbGpsPending(SourcePending, TffbGpsFetchError):
    """This week's Pace of Play article is not on TFFB's listing yet (it goes up Wednesday): pending, not
    failed. Still a `TffbGpsFetchError`, so a caller that catches the family keeps working."""


# The words an article's slug or title names a secondary slate with: its day (`slate.DAYS` keys)...
DAY_ARTICLE_WORDS: dict[str, tuple[str, ...]] = {
    "mon": ("monday",),
    "tue": ("tuesday",),
    "wed": ("wednesday",),
    "thu": ("thursday",),
    "fri": ("friday",),
    "sat": ("saturday",),
}
# ...or a holiday on its date (`holidays_on`).
HOLIDAY_WORDS: dict[str, tuple[str, ...]] = {
    "thanksgiving": ("thanksgiving",),
    "black friday": ("black friday",),
    "christmas": ("christmas", "xmas"),
}
# The playoff rounds, by weeks after week 18's start (`playoff_round`); week 4 after is the Super Bowl's bye.
PLAYOFF_ROUNDS: dict[int, tuple[str, ...]] = {
    1: ("wild card", "wildcard"),
    2: ("divisional",),
    3: ("conference", "championship"),
    5: ("super bowl",),
}
_TAG_RE = re.compile(r"<[^>]*>")
_WEEK_RE = re.compile(r"week[\s-]+([0-9]+)(?![0-9])")


def holidays_on(day: date) -> set[str]:
    """The `HOLIDAY_WORDS` holidays that fall on `day`: Thanksgiving (the fourth Thursday of November),
    Black Friday (the day after it), Christmas (December 25)."""
    out: set[str] = set()
    if day.month == 11 and day.weekday() == 3 and 22 <= day.day <= 28:
        out.add("thanksgiving")
    if day.month == 11 and day.weekday() == 4 and 23 <= day.day <= 29:
        out.add("black friday")
    if day.month == 12 and day.day == 25:
        out.add("christmas")
    return out


def playoff_round(today: date, season: int) -> int | None:
    """The `PLAYOFF_ROUNDS` key of `today` (1 Wild Card ... 5 Super Bowl), counted in whole weeks from week
    18's Tuesday start, the same anchor every NFL week uses; None in the regular season or the bye week."""
    from dfs import nfl_calendar

    weeks_after = (today - nfl_calendar.week_start_date(nfl_calendar.MAX_WEEK, season)).days // 7
    return weeks_after if weeks_after in PLAYOFF_ROUNDS else None


def _named(haystack: str, words: Iterable[str]) -> bool:
    return any(re.search(rf"(?<![a-z]){re.escape(w)}(?![a-z])", haystack) for w in words)


def _haystack(href: str, text: str) -> str:
    """The link's href and its text (any markup in it dropped: a card's image tag carries a file name), lower
    case, with the slug's hyphens as spaces."""
    return f"{href} {_TAG_RE.sub(' ', text)}".lower().replace("-", " ").replace("_", " ")


def _is_pace_of_play_link(
    href: str,
    text: str,
    week: int,
    *,
    key: str = "main",
    slate_day: date | None = None,
    round_no: int | None = None,
) -> bool:
    """True if this link's href or text is Kyle Borg's Pace of Play article for slate `key` of `week`.

    **Main** (`key="main"`): `week N` (a real word boundary, so week 1 never matches week 10-19: Week 1's slug
    started `nfl-dfs-pace-of-play-stacks-for-week-1-...`, Week 2/3's `pace-of-play-matchups-stacks-for-week-N-
    ...`; "pace of play" and a week boundary are the only two things both shapes share), and no secondary
    slate named (`DAY_ARTICLE_WORDS`, `HOLIDAY_WORDS`), so a "Week 12 Thanksgiving" article is never main's.
    In the playoffs (`round_no`, from `playoff_round`) the round's name instead of the week.

    **A secondary slate** (`key` a `slate.DAYS` key, `slate_day` its date): its day word, or a holiday that
    falls on `slate_day`. An article naming two slates ("Thanksgiving & Black Friday") is each one's. Not
    last week's: a week number, if named, is `week`; in the playoffs a round, if named, is `round_no`."""
    haystack = _haystack(href, text)
    if "pace of play" not in haystack:
        return False
    weeks = {int(n) for n in _WEEK_RE.findall(haystack)}
    rounds = {r for r, words in PLAYOFF_ROUNDS.items() if _named(haystack, words)}
    days = {k for k, words in DAY_ARTICLE_WORDS.items() if _named(haystack, words)}
    holidays = {h for h, words in HOLIDAY_WORDS.items() if _named(haystack, words)}

    if key == "main":
        if days or holidays:
            return False
        if round_no is not None:
            return rounds == {round_no}
        return week in weeks

    own_holidays = holidays & holidays_on(slate_day) if slate_day is not None else set()
    if key not in days and not own_holidays:
        return False
    if weeks and weeks != {week}:
        return False
    return round_no is None or rounds <= {round_no}


def _find_article_url(
    page: Page,
    week: int,
    *,
    key: str = "main",
    slate_day: date | None = None,
    round_no: int | None = None,
) -> str | None:
    """Search TFFB's own `/dfs/` listing (never a constructed slug -- see
    module docstring) for this slate's Pace of Play article. `None` if not
    found after searching `_LISTING_PAGES_TO_SEARCH` pages -- the caller
    turns that into "not published yet"."""
    for page_num in range(1, _LISTING_PAGES_TO_SEARCH + 1):
        url = LISTING_URL if page_num == 1 else f"{LISTING_URL}page/{page_num}/"
        page.goto(url, wait_until="domcontentloaded", timeout=30000)
        links = page.eval_on_selector_all("a", "els => els.map(e => [e.href, e.textContent.trim()])")
        for href, text in links:
            if _is_pace_of_play_link(href, text, week, key=key, slate_day=slate_day, round_no=round_no):
                return href
    return None


def override_url(key: str, week: int) -> str | None:
    """`[slates.<key>] gps_url` from config.toml, used as is; None on the main slate, when it is not set, or
    when config.toml cannot be read. Only for the week the table is for (its `week`, which `dfs week new
    --slate` rewrites): a Saturday's URL left in config.toml is never next Saturday's article."""
    if key == "main":
        return None
    try:
        from dfs.config import load_base_config

        entry = load_base_config().slates.get(key)
    except Exception:  # noqa: BLE001 - no config: no override, the listing search runs
        return None
    if entry is None or not entry.gps_url:
        return None
    if entry.week is not None and entry.week != week:
        log.warning(
            "GPS: [slates.%s] gps_url is for week %s, not week %s: ignored (searching the listing)",
            key,
            entry.week,
            week,
        )
        return None
    return entry.gps_url


def check_slate_teams(gps: pd.DataFrame, slate_teams: Iterable[str]) -> list[str]:
    """The slate's teams (its DraftKings salaries' `TeamAbbrev`) missing from the GPS CSV, sorted; the
    caller warns for each. Raises TffbGpsFetchError when the CSV has none of them at all: that is another
    slate's article (last week's Saturday, the main slate's), never this one's. No slate teams known: []."""
    want = {normalize_team(t) for t in slate_teams if str(t).strip()}
    if not want:
        return []
    have = {t for t in gps["Team"] if t}
    if not want & have:
        raise TffbGpsFetchError(
            f"The GPS CSV has none of this slate's teams ({', '.join(sorted(want))}; it has "
            f"{', '.join(sorted(have)) or 'none'}): it is another slate's article. Sync draftkings first "
            "if its file is old, or set [slates.<key>] gps_url."
        )
    return sorted(want - have)


def keep_slate_teams(gps: pd.DataFrame, slate_teams: Iterable[str]) -> pd.DataFrame:
    """The GPS rows of the slate's teams (its DraftKings salaries' `TeamAbbrev`), in the CSV's order."""
    want = {normalize_team(t) for t in slate_teams if str(t).strip()}
    return gps[gps["Team"].isin(want)].reset_index(drop=True)


def _slate_teams() -> list[str]:
    """The active slate's DraftKings teams from its saved salaries; [] when there are none yet."""
    from dfs import store

    try:
        return [str(t) for t in store.load_current("draftkings")["TeamAbbrev"].dropna()]
    except (FileNotFoundError, KeyError, ValueError):
        return []


def _find_csv_url(page: Page, article_url: str) -> str | None:
    """The first `.csv` link inside the article body -- never a guessed
    filename (confirmed live: neither Week 2's nor Week 3's CSV filename
    follows a shared pattern)."""
    page.goto(article_url, wait_until="domcontentloaded", timeout=30000)
    links = page.eval_on_selector_all("a[href$='.csv']", "els => els.map(e => e.href)")
    return links[0] if links else None


# Where to look for the real header row. Weeks 2-8 had three lines above the data
# (title, group-label banner, header); Week 9 (2026-11-04) had two (title and banner
# merged into one line), so the header is found by its label, not by a fixed line.
_HEADER_SEARCH_LINES = 6
_LEGACY_HEADER_LINE = 2  # the old fixed position -- used when no header row is found
# Header names TFFB has spelled two ways; the value is the spelling `GPS_EXPECTED_HEADER` uses.
_HEADER_ALIASES = {"PROE": "PROE %"}


def _header_line(text: str) -> int:
    """Index of the header line: the first of the top `_HEADER_SEARCH_LINES` lines whose
    first cell is `Team` and that has an `Implied Total` column. Falls back to the old
    fixed position so a file with no such row still fails with the header-drift error."""
    for i, line in enumerate(text.splitlines()[:_HEADER_SEARCH_LINES]):
        if line.split(",", 1)[0].strip() == "Team" and "Implied Total" in line:
            return i
    return _LEGACY_HEADER_LINE


def parse_gps_csv(text: str) -> pd.DataFrame:
    """Pure: the raw CSV text (already decoded) -> `GPS_COLUMNS` shape.
    Raises `TffbGpsFetchError` if the real header row doesn't match
    `GPS_EXPECTED_HEADER` exactly (after `_HEADER_ALIASES`) -- see module
    docstring for what a drift would mean. The header row is found by its
    `Team` label (two or three lines down), and `PROE` / `PROE %` are both
    accepted (Week 9 dropped the `%`)."""
    df = pd.read_csv(io.StringIO(text), skiprows=_header_line(text))
    df = df.rename(columns=_HEADER_ALIASES)
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
        from dfs import slate

        key = slate.current()
        on = "" if key == slate.MAIN else f"the {slate.label(key)} slate of "
        log.info("fetching TFFB Pace of Play (GPS) for %sweek %s", on, ctx.week)
        slate_day = None if key == slate.MAIN else slate.slate_date(key, ctx.season, ctx.week)
        round_no = playoff_round(date.today(), ctx.season)
        override = override_url(key, ctx.week)
        with persistent_context("tffb", headless=True) as context:
            page = context.new_page()
            try:
                if override:
                    log.info("GPS: using [slates.%s] gps_url %s", key, override)
                    article_url = override
                else:
                    article_url = _find_article_url(
                        page, ctx.week, key=key, slate_day=slate_day, round_no=round_no
                    )
                if article_url is None:
                    raise TffbGpsPending(
                        f"GPS not published for {on}Week {ctx.week} yet -- no matching article found "
                        f"in TFFB's {LISTING_URL} listing."
                        + ("" if key == slate.MAIN else f" (Set [slates.{key}] gps_url if it is up.)")
                    )
                log.info("GPS article: %s", article_url)
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
        teams = _slate_teams()
        for team in check_slate_teams(df, teams):
            log.warning("GPS: slate team %s is not in the Pace of Play CSV (its GPS stays blank)", team)
        if key != slate.MAIN:
            if teams:
                df = keep_slate_teams(df, teams)
            else:
                log.warning(
                    "GPS: no DraftKings salaries saved for the %s slate yet, so all %d rows are kept "
                    "(sync draftkings, then tffb_gps, to keep the slate's own teams)",
                    slate.label(key),
                    len(df),
                )
        return df
