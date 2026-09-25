"""Data source registry.

Replaces the old hardcoded scraper lists (utils/config.py's
get_scraper_configs()/get_update_scrapers()), which meant adding a source
touched six different places across the codebase (see the rebuild plan's
"Sources are a registry, not six hardcoded lists"). Adding a source here
means adding one entry to SOURCES.
"""

from __future__ import annotations

from dfs.sources.base import Source
from dfs.sources.dk_salaries import DkSalariesSource
from dfs.sources.edge import EdgeSource
from dfs.sources.fantasypros_projections import FantasyProsProjectionsSource
from dfs.sources.nflverse_games import NflverseGamesSource
from dfs.sources.nflverse_snaps import NflverseSnapsSource
from dfs.sources.rotowire_odds import RotowireOddsSource
from dfs.sources.sleeper_projections import SleeperProjectionsSource
from dfs.sources.tffb_projections import TffbProjectionsSource
from dfs.sources.tffb_sos import TffbSosSource
from dfs.sources.weather import WeatherSource

# Order matters: run_sync iterates SOURCES in this insertion order.
# "weather" reads the GamesRaw CSV "nflverse_games" just saved, and "edge"
# reads the CSVs "projections"/"draftkings"/"sleeper" just saved -- all
# three must run after their inputs. The five "sos_*" sources write
# straight to their own hand-pasted-turned-synced tabs (SoSQB/RB/Wr/TE/
# Def) and feed nothing else in this list -- `edge`/`OppPosRank` still
# reach them the same way they always have, a live sheet formula (VLOOKUP
# chain) reading real data now instead of a blank paste.
#
# Part C, C3/C4: "sleeper"/"fantasypros" are deliberately NOT in cli.py's
# LIVE_SYNC_SOURCES -- a full `dfs sync` includes them, `dfs sync --live`
# doesn't, which is each one's own "make it skippable" instruction
# satisfied with no new flag (FantasyPros' own ~25s Crawl-delay makes this
# doubly important for it). "snaps" (C6) is the same -- a snap-count
# release only changes once a week's games are actually played, not
# worth re-fetching on every fast live-sync pass.
SOURCES: dict[str, Source] = {
    "nfl_odds": RotowireOddsSource(),
    "draftkings": DkSalariesSource(),
    "projections": TffbProjectionsSource(),
    "sleeper": SleeperProjectionsSource(),
    "fantasypros": FantasyProsProjectionsSource(),
    "snaps": NflverseSnapsSource(),
    "sos_qb": TffbSosSource("QB"),
    "sos_rb": TffbSosSource("RB"),
    "sos_wr": TffbSosSource("WR"),
    "sos_te": TffbSosSource("TE"),
    "sos_dst": TffbSosSource("DST"),
    "nflverse_games": NflverseGamesSource(),
    "weather": WeatherSource(),
    "edge": EdgeSource(),
}


def get_source(name: str) -> Source:
    try:
        return SOURCES[name]
    except KeyError:
        raise KeyError(f"Unknown source {name!r}. Known sources: {', '.join(sorted(SOURCES))}") from None
