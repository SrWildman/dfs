"""FantasyPros weekly projection pages -- Part C, C4.

Server-rendered HTML (`pandas.read_html()` works directly on the page's
markup), but anonymously it's gated: confirmed live, every position page
serves exactly the top 10 players before a `<div id="registration-
module">` paywall, nowhere near the rosterable pool C2's validation and
C5b's SPLIT tuning both need. A logged-in session removes the gate
entirely (confirmed live: 80 QBs, 124 RBs, 208 WRs, 123 TEs, 32 DSTs, no
gate markup at all) -- same persistent-browser-profile pattern as `dfs
auth tffb`/`dk` (`dfs auth fantasypros`, `browser.py`), so this module
uses Playwright to load each page inside that authenticated profile and
reads the RENDERED html, rather than a plain unauthenticated `httpx`/
`requests` GET (which would still hit the anonymous 10-row gate).

`robots.txt` sets `Crawl-delay: 5` and does not disallow `/nfl/
projections/` -- honoured here as a real `time.sleep(5)` between each of
the five position requests (~25s total), which is why this source is
left out of `cli.py`'s `LIVE_SYNC_SOURCES` the same way `sleeper_
projections.py` is (C4's own "make it skippable" instruction, satisfied
with no new flag). FantasyPros' own Terms of Service page 404s (checked
live) -- unverified, but `robots.txt` permits this path and Sam chose to
include it with that known (C4's own note).

Every source's own fantasy total (`MISC_FPTS`/`FPTS`) is ignored --
FantasyPros' own scoring isn't DraftKings' (see `dk_scoring.py`). Only the
component stat columns feed `dk_scoring.score_offense_frame`/
`score_dst_frame`. FantasyPros' projection pages don't surface a
2-point-conversion column for any position (checked live, all 5 pages) --
`two_pt` is a real 0.0 for every row here, a known simplification (not
this codebase's usual "blank is not zero" convention, since there is no
FantasyPros-specific 2pt signal to be blank ABOUT -- the field simply
isn't part of what this source publishes), documented rather than left
implicit. Same for DST's `blocked_kick` -- not a column FantasyPros
publishes, real 0.0 for every row.

Team codes mostly already match DraftKings' -- the one confirmed live
drift is `JAC` (FantasyPros) vs `JAX` (DraftKings), handled by
`player_join.TEAM_ALIASES`, not duplicated here. DST rows have no
separate team-code column at all -- the `Player` cell IS the full team
name ("Kansas City Chiefs"), converted here via `FULL_TEAM_NAME_TO_CODE`
(from nflverse's own `teams.csv` `full`/`team` columns, hardcoded since
all 32 are stable season to season -- verified against a real live
FantasyPros DST pull, every name present here matched)."""

from __future__ import annotations

import time
from io import StringIO

import pandas as pd

from dfs.browser import persistent_context
from dfs.dk_scoring import DST_STAT_FIELDS, OFFENSE_STAT_FIELDS, score_dst_frame, score_offense_frame
from dfs.log import get_logger
from dfs.sources.base import Source, SyncContext

log = get_logger("sources.fantasypros_projections")

URL_TEMPLATE = "https://www.fantasypros.com/nfl/projections/{position}.php?week={week}&scoring=PPR"
POSITIONS = {"QB": "qb", "RB": "rb", "WR": "wr", "TE": "te", "DST": "dst"}
CRAWL_DELAY_SECONDS = 5.0

FULL_TEAM_NAME_TO_CODE = {
    "Arizona Cardinals": "ARI",
    "Atlanta Falcons": "ATL",
    "Baltimore Ravens": "BAL",
    "Buffalo Bills": "BUF",
    "Carolina Panthers": "CAR",
    "Chicago Bears": "CHI",
    "Cincinnati Bengals": "CIN",
    "Cleveland Browns": "CLE",
    "Dallas Cowboys": "DAL",
    "Denver Broncos": "DEN",
    "Detroit Lions": "DET",
    "Green Bay Packers": "GB",
    "Houston Texans": "HOU",
    "Indianapolis Colts": "IND",
    "Jacksonville Jaguars": "JAX",
    "Kansas City Chiefs": "KC",
    "Las Vegas Raiders": "LV",
    "Los Angeles Chargers": "LAC",
    "Los Angeles Rams": "LAR",
    "Miami Dolphins": "MIA",
    "Minnesota Vikings": "MIN",
    "New England Patriots": "NE",
    "New Orleans Saints": "NO",
    "New York Giants": "NYG",
    "New York Jets": "NYJ",
    "Philadelphia Eagles": "PHI",
    "Pittsburgh Steelers": "PIT",
    "San Francisco 49ers": "SF",
    "Seattle Seahawks": "SEA",
    "Tampa Bay Buccaneers": "TB",
    "Tennessee Titans": "TEN",
    "Washington Commanders": "WAS",
}


class FantasyProsFetchError(Exception):
    pass


class FantasyProsAuthError(FantasyProsFetchError):
    pass


def _flatten_columns(df: pd.DataFrame) -> pd.DataFrame:
    """FantasyPros' offense tables use a two-row header (`PASSING`/`YDS`
    etc); DST's is a single flat row. Normalizes both to one level,
    `SECTION_STAT` for the multi-index case, the bare stat name for DST."""
    df = df.copy()
    flat = []
    for col in df.columns:
        if isinstance(col, tuple):
            section, stat = col
            flat.append(
                stat if not isinstance(section, str) or section.startswith("Unnamed") else f"{section}_{stat}"
            )
        else:
            flat.append(col)
    df.columns = flat
    return df


def _split_name_team(player_field: str) -> tuple[str, str]:
    """ "Josh Allen BUF" -> ("Josh Allen", "BUF") -- FantasyPros appends the
    team code as the field's last token for every offensive position."""
    parts = str(player_field).rsplit(" ", 1)
    return (parts[0], parts[1]) if len(parts) == 2 else (str(player_field), "")


def _fetch_position_html(context, position_path: str, week: int) -> str:
    url = URL_TEMPLATE.format(position=position_path, week=week)
    page = context.new_page()
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(1500)  # let the page's own JS finish rendering the table
        return page.content()
    finally:
        page.close()


def _parse_offense_table(html: str) -> pd.DataFrame:
    tables = pd.read_html(StringIO(html))
    if not tables:
        raise FantasyProsFetchError(
            "No table found on FantasyPros projections page -- page layout may have changed."
        )
    df = _flatten_columns(tables[0])
    if "Player" not in df.columns:
        raise FantasyProsFetchError(
            "FantasyPros table has no 'Player' column -- page layout may have changed."
        )
    if "registration-module" in html and len(df) <= 10:
        raise FantasyProsAuthError(
            "FantasyPros returned only 10 rows behind its registration wall -- "
            "the saved session may have expired. Run `dfs auth fantasypros` again."
        )
    names_teams = df["Player"].apply(_split_name_team)
    df["Name"] = [nt[0] for nt in names_teams]
    df["Team"] = [nt[1] for nt in names_teams]
    return df


def _offense_row_stats(row: pd.Series) -> dict:
    return {
        "pass_yd": row.get("PASSING_YDS", 0.0) or 0.0,
        "pass_td": row.get("PASSING_TDS", 0.0) or 0.0,
        "pass_int": row.get("PASSING_INTS", 0.0) or 0.0,
        "rush_yd": row.get("RUSHING_YDS", 0.0) or 0.0,
        "rush_td": row.get("RUSHING_TDS", 0.0) or 0.0,
        "rec": row.get("RECEIVING_REC", 0.0) or 0.0,
        "rec_yd": row.get("RECEIVING_YDS", 0.0) or 0.0,
        "rec_td": row.get("RECEIVING_TDS", 0.0) or 0.0,
        "fum_lost": row.get("MISC_FL", 0.0) or 0.0,
        "two_pt": 0.0,  # not published by FantasyPros -- see module docstring
    }


def _dst_row_stats(row: pd.Series) -> dict:
    return {
        "sack": row.get("SACK", 0.0) or 0.0,
        "def_int": row.get("INT", 0.0) or 0.0,
        "fum_rec": row.get("FR", 0.0) or 0.0,
        "def_td": row.get("TD", 0.0) or 0.0,
        "safety": row.get("SAFETY", 0.0) or 0.0,
        "blocked_kick": 0.0,  # not published by FantasyPros -- see module docstring
        "points_allowed": row.get("PA", 0.0) or 0.0,
    }


_OFFENSE_PAGE_FIELDS = [f for f in OFFENSE_STAT_FIELDS if f != "two_pt"]
_DST_PAGE_FIELDS = [f for f in DST_STAT_FIELDS if f != "blocked_kick"]


def _blank_unprojected_rows(stats: pd.DataFrame, page_fields: list[str]) -> pd.DataFrame:
    """FantasyPros pads every position's page out to its full rostered
    depth, not just the players it has a real weekly projection for --
    confirmed live: 11 of 80 QBs, 8 of 124 RBs, 30 of 208 WRs, 10 of 123
    TEs come back with EVERY page-sourced stat at exactly 0.0, all sitting
    well past that position's own rosterable-pool size in the list (real
    example: Caleb Williams, a starting QB TFFB projects at 22.0, showed
    up here as a literal 0.0 across the board). Same underlying problem
    as sleeper_projections.py's ranking-only-stats case, encoded
    differently (real zeros here instead of a missing key) -- treated the
    same way: blanked to NaN (no real projection), never scored as a
    genuine 0. `two_pt`/`blocked_kick` are excluded from this check since
    this module always sets them to a real 0.0 itself (see module
    docstring), not from the page."""
    all_zero = (stats[page_fields].fillna(0) == 0).all(axis=1)
    stats = stats.copy()
    stats.loc[all_zero, stats.columns] = pd.NA
    return stats


def fetch_projections(week: int) -> pd.DataFrame:
    offense_frames: list[pd.DataFrame] = []
    dst_df: pd.DataFrame | None = None

    with persistent_context("fantasypros", headless=True) as context:
        for i, (dk_position, position_path) in enumerate(POSITIONS.items()):
            if i > 0:
                time.sleep(CRAWL_DELAY_SECONDS)
            html = _fetch_position_html(context, position_path, week)

            if dk_position == "DST":
                dst_table = pd.read_html(StringIO(html))
                if not dst_table:
                    raise FantasyProsFetchError(
                        "No DST table found on FantasyPros -- page layout may have changed."
                    )
                dst_df = dst_table[0]
                if "Player" not in dst_df.columns:
                    raise FantasyProsFetchError("FantasyPros DST table has no 'Player' column.")
                if "registration-module" in html and len(dst_df) <= 10:
                    raise FantasyProsAuthError(
                        "FantasyPros returned only 10 DST rows behind its registration wall -- "
                        "the saved session may have expired. Run `dfs auth fantasypros` again."
                    )
                dst_df["Team"] = dst_df["Player"].map(FULL_TEAM_NAME_TO_CODE).fillna("")
                dst_df["Name"] = dst_df["Player"]
                stats = dst_df.apply(_dst_row_stats, axis=1, result_type="expand")
                stats = _blank_unprojected_rows(stats, _DST_PAGE_FIELDS)
                dst_df = pd.concat([dst_df[["Name", "Team"]], stats], axis=1)
                dst_df["Position"] = "DST"
            else:
                df = _parse_offense_table(html)
                df["Position"] = dk_position
                stats = df.apply(_offense_row_stats, axis=1, result_type="expand")
                stats = _blank_unprojected_rows(stats, _OFFENSE_PAGE_FIELDS)
                offense_frames.append(pd.concat([df[["Name", "Team", "Position"]], stats], axis=1))

    if not offense_frames and dst_df is None:
        raise FantasyProsFetchError("FantasyPros returned no data for any position.")

    offense_df = pd.concat(offense_frames, ignore_index=True) if offense_frames else pd.DataFrame()
    if not offense_df.empty:
        offense_df["DkPts"] = score_offense_frame(offense_df[OFFENSE_STAT_FIELDS])
    if dst_df is not None and not dst_df.empty:
        dst_df["DkPts"] = score_dst_frame(dst_df[DST_STAT_FIELDS])

    combined = pd.concat([offense_df, dst_df], ignore_index=True, sort=False)
    keep = ["Name", "Team", "Position", "DkPts", *OFFENSE_STAT_FIELDS]
    for field in DST_STAT_FIELDS:
        if field not in keep:
            keep.append(field)
    for col in keep:
        if col not in combined.columns:
            combined[col] = pd.NA
    return combined[keep]


class FantasyProsProjectionsSource(Source):
    name = "fantasypros"

    def fetch(self, ctx: SyncContext) -> pd.DataFrame:
        log.info("fetching fantasypros projections for week %s", ctx.week)
        return fetch_projections(ctx.week)
