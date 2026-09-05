# Legacy scrapers (reference only)

`fantasy_footballers/` (projections) and `tffb_sos/` (strength of schedule)
have not been ported to the new `dfs` CLI yet -- both need a real look at
The Fantasy Footballers' post-login DOM (the page is fully paywalled) before
writing Playwright automation, and SoS specifically isn't meaningful until
a few weeks into the season anyway.

These scripts **do not run anymore** -- they import from `utils/`, which no
longer exists (superseded by `src/dfs/`). They're kept only so their
manual-interaction flow and URLs are there to reference when porting.

The eventual replacements go in `src/dfs/sources/tffb_projections.py` and
`src/dfs/sources/tffb_sos.py`, using the shared browser profile in
`src/dfs/browser.py` (`dfs auth tffb`).
