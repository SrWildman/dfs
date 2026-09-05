# Legacy scrapers (reference only)

`tffb_sos/` (strength of schedule) hasn't been ported to the new `dfs`
CLI yet -- it needs a real look at The Fantasy Footballers' post-login
DOM (paywalled) before writing Playwright automation, and SoS specifically
isn't meaningful until a few weeks into the season anyway.

This script **does not run anymore** -- it imports from `utils/`, which no
longer exists (superseded by `src/dfs/`). It's kept only so its
manual-interaction flow and URLs are there to reference when porting.

The eventual replacement goes in `src/dfs/sources/tffb_sos.py`, using the
shared browser profile in `src/dfs/browser.py` (`dfs auth tffb`).

Projections (`fantasy_footballers/`, formerly here too) are already
ported -- see `src/dfs/sources/tffb_projections.py`, whose docstring
covers what was tried and rejected before landing on the current
approach.
