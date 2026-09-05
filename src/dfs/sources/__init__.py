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
from dfs.sources.rotowire_odds import RotowireOddsSource
from dfs.sources.tffb_projections import TffbProjectionsSource

SOURCES: dict[str, Source] = {
    "nfl_odds": RotowireOddsSource(),
    "draftkings": DkSalariesSource(),
    "projections": TffbProjectionsSource(),
}


def get_source(name: str) -> Source:
    try:
        return SOURCES[name]
    except KeyError:
        raise KeyError(f"Unknown source {name!r}. Known sources: {', '.join(sorted(SOURCES))}") from None
