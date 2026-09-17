"""Strength of schedule, per position, from TFFB's FootClan page --
automates what's been a weekly hand-paste into `SoSQB`/`SoSRB`/`SoSWr`/
`SoSTE`/`SoSDef` since those tabs were first reserved.

Sam: "SOS comes from TFFB... we should be filtering to just the current
week and this does the rest of season... SOS 1-4 doesn't make sense. We
should show the proper position sos for each player, not all of them" --
then, once the per-week filter and the underlying data model were
confirmed live (Claude-in-Chrome, 2026-09-16): "I want this part of the
same sync."

URL: `thefantasyfootballers.com/footclan/strength-of-schedule/?position=X`
-- same footclan subscription this project already uses for
`projections`; `dfs auth tffb`'s existing session covers this page too
(confirmed live, no separate login).

One `Source` PER POSITION (`?position=` genuinely serves a different
dataset per position -- confirmed live: the exact same generic field
names, `week_N`/`week_N_position_rank`/`week_N_opponent`, hold that
position's own numbers depending only on which `?position=` was loaded,
so there is no single page load covering all five). D/ST's own param
value is `"D"`, not `"DST"`/`"DEF"` -- read off the tab's own resulting
URL live, not guessed.

**How this actually works, and why it's not a network-intercept the way
`tffb_projections.py` is:** the page's own help text advertises per-week
filtering ("you can use this tool... on a per-week basis"), but nothing
about it is server-side-filtered by week -- the whole season's numbers
for the selected position arrive in the initial page load already, fed
into a jQuery DataTables instance (`#strength-of-schedule`) via a plain
`data: [...]` option. Selecting a week is a pure CLIENT-SIDE recompute:
no new network request fires. Confirmed live by clicking "Week 2" and
watching Cincinnati's `overall_rank` change from 18 (the page's default
full-season view) to 1 -- the exact number the page itself then displays
-- while a DIFFERENT, similarly-named field, `week_2_position_rank`,
stayed a constant 32 throughout and never matched what was on screen.
That field looked like the obvious candidate first and would have been
silently wrong.

So this drives the same interaction a person would: load the position's
page, open the week-filter dropdown, click the current week's own button
(`a.week-button[data-week="N"]`, a real DOM attribute -- confirmed by
inspecting the button live, not a guessed selector), then read
`overall_rank` / `opponent_avg` / `week_N_opponent` back off the live
DataTable instance via `jQuery('#strength-of-schedule').DataTable().
rows().data().toArray()`. Materially more fragile than
`tffb_projections.py`'s captured-JSON-response technique -- this depends
on TFFB's own CSS class names and a third-party JS library's internal
row-object shape, neither a stable contract -- so a page redesign breaks
this LOUDLY (`TffbSosFetchError` from a missing field), never silently.

No `PAE` field exists anywhere on this page (checked) -- the sheet's old
hand-pasted `PAE` column was either a different TFFB view never found
this session, or something whoever pasted it computed themselves.
Dropped here rather than invented. No home/away field either (only
recoverable by parsing an "@" prefix out of rendered HTML, not a real
data field) -- skipped as not worth the extra fragility for something
nobody asked for.

**Which week counts as "current" is NOT `nfl_calendar.current_week()`
here, on purpose.** Checked live, same day (2026-09-16, a Wednesday
between Week 1's Monday-night finale and Week 2's Thursday kickoff):
`nfl_calendar.current_week()` returns 1 (correct for ITS OWN documented
purpose -- sorting a contest-history export into weeks, line-movement
diff baselines -- where "still week 1 until Thursday" is the right
convention), but this page's own default filter already reads "Weeks
2-18", i.e. TFFB itself has already moved on to treating week 2 as the
live, relevant one -- matching the live sheet's own "Week 2" title, not
`ctx.week`. Passing `ctx.week` (1) here would have pulled ALREADY-PLAYED
week 1 matchups while Sam is building week 2 lineups -- caught only by
comparing this page's own default range against `ctx.week` directly,
not by any assumption. So this reads the lower bound of the page's own
default range (parsed off `.ffb-filters--button`'s own text, e.g.
"Weeks 2-18" -> 2) as the week to select, deferring to TFFB's own
judgment of "current" the same way `tffb_projections.py` already does
by never touching the optimizer's own week selector at all. `ctx.week`
is still read and compared against the parsed value -- logged as a
warning, not a hard failure, if they disagree by more than one week
(the boundary case above is an expected one-week gap, not an error).

Team abbreviations here are already DK-compatible (checked all 32,
including the usual `LAR`/`JAX`/`LV`/`WAS` drift points against
`nflverse_games.py`'s own mapping) -- no normalization needed.
"""

from __future__ import annotations

import re

import pandas as pd
from playwright.sync_api import Page
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from dfs.browser import persistent_context
from dfs.log import get_logger
from dfs.sources.base import Source, SyncContext

log = get_logger("sources.tffb_sos")

BASE_URL = "https://www.thefantasyfootballers.com/footclan/strength-of-schedule/"

# TFFB's own `?position=` values -- D/ST is "D", not "DST"/"DEF".
POSITION_PARAMS = {"QB": "QB", "RB": "RB", "WR": "WR", "TE": "TE", "DST": "D"}

_REQUIRED_ROW_FIELDS = ("team", "name", "overall_rank", "opponent_avg")

# Matches the filter button's own default label, e.g. "Weeks 2-18" or a
# possible single-week "Week 2" -- the FIRST number is what this module
# treats as "the current week" (see module docstring for why that's
# TFFB's own judgment, not `nfl_calendar.current_week()`).
_DEFAULT_RANGE_RE = re.compile(r"(\d+)")


class TffbSosFetchError(Exception):
    pass


def _current_week_per_page(page: Page) -> int:
    """Reads the week-filter button's own default label (before this
    module clicks anything) and returns the first week number in it."""
    label = page.text_content(".ffb-filters--button") or ""
    m = _DEFAULT_RANGE_RE.search(label)
    if not m:
        raise TffbSosFetchError(
            f"Could not parse a week number out of the filter button's own label {label!r} -- "
            "TFFB may have changed this page's layout."
        )
    return int(m.group(1))


def _select_week_and_read_rows(page: Page, week: int) -> list[dict]:
    """Clicks the page's own week-filter down to just `week`, then reads
    the live DataTable's row data back. See module docstring for why this
    (not the raw embedded data) is the correct source of `overall_rank`/
    `opponent_avg`."""
    page.click(".ffb-filters--button")
    page.click(f'a.week-button[data-week="{week}"]')
    # The recompute is synchronous JS, not a network round-trip -- this
    # just gives the click handler a moment to finish before reading back.
    page.wait_for_timeout(300)
    return page.evaluate("jQuery('#strength-of-schedule').DataTable().rows().data().toArray()")


def _row_to_record(row: dict, week: int, position: str) -> dict:
    missing = [f for f in _REQUIRED_ROW_FIELDS if f not in row]
    if missing:
        raise TffbSosFetchError(
            f"{position} strength-of-schedule row is missing field(s) {missing} -- "
            "TFFB may have changed this page's data shape."
        )
    return {
        "Team": row["name"],
        "Team.1": row["team"],
        "Rank": row["overall_rank"],
        "FPA": row["opponent_avg"],
        "Opp": row.get(f"week_{week}_opponent", ""),
    }


class TffbSosSource(Source):
    """One instance per position -- `position` set at construction rather
    than five near-identical hardcoded subclasses, same reasoning as
    `EntryTableConfig` covering both Bankroll buckets from one shape."""

    needs_auth = True

    def __init__(self, position: str):
        if position not in POSITION_PARAMS:
            raise ValueError(f"Unknown position {position!r}; expected one of {list(POSITION_PARAMS)}")
        self.position = position
        self.name = f"sos_{position.lower()}"

    def fetch(self, ctx: SyncContext) -> pd.DataFrame:
        url = f"{BASE_URL}?position={POSITION_PARAMS[self.position]}"
        log.info("fetching TFFB strength of schedule for %s (ctx.week=%s)", self.position, ctx.week)
        with persistent_context("tffb", headless=True) as context:
            page = context.new_page()
            try:
                page.goto(url, wait_until="domcontentloaded")
                page.wait_for_function(
                    "window.jQuery && jQuery.fn.dataTable "
                    "&& jQuery.fn.dataTable.isDataTable('#strength-of-schedule')",
                    timeout=15000,
                )
                week = _current_week_per_page(page)
                if abs(week - ctx.week) > 1:
                    log.warning(
                        "TFFB's own current week (%s) differs from ctx.week (%s) by more than one -- "
                        "using TFFB's, but this gap is bigger than the usual pre-kickoff mismatch.",
                        week,
                        ctx.week,
                    )
                rows = _select_week_and_read_rows(page, week)
            except PlaywrightTimeoutError as e:
                raise TffbSosFetchError(
                    f"Strength-of-schedule page never finished loading at {url} -- "
                    "run `dfs auth tffb` if your session expired, or check the page manually."
                ) from e
            finally:
                page.close()

        if not rows:
            raise TffbSosFetchError(f"Strength-of-schedule page returned no teams for {self.position}.")
        records = [_row_to_record(row, week, self.position) for row in rows]
        return pd.DataFrame(records, columns=["Team", "Team.1", "Rank", "FPA", "Opp"])
