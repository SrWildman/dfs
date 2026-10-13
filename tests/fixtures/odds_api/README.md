# Odds API fixtures

**`synthetic_event_odds.json` is SYNTHETIC and UNVERIFIED.** It was written by hand, from memory of The Odds
API v4 event-odds response (`/v4/sports/{sport}/events/{eventId}/odds?markets=player_...`), in a session that
could not reach their documentation. Players, prices and lines are invented. It exists so `dfs.props`' parser
and maths have something to run on; it proves nothing about the real API's shape.

It deliberately contains things the parser has to cope with: a non-prop market (`h2h`), an alternate market
(`player_pass_yds_alternate`), a bookmaker with no markets, a bookmaker whose Over has no price, books on
different lines, a Yes-only anytime-TD book beside a Yes/No one, and players missing a market they need.

`synthetic_event_odds_american.json` is the same kind of invention in **American** odds (-115, +110, +320),
the format the first real response turned out to use: Over/Under pairs with negative prices on every line, a
Yes-only anytime-TD quote at every book (no "No" side), three books, no malformed outcomes. Its job is to show
`odds_format="auto"` picks American and skips nothing.

`event_odds_sample.json` (a REAL recorded response) is to be committed by the local session. Run
`tests/test_props.py` against it: `test_recorded_sample_parses` skips until it exists and then asserts the
parser reads at least one quote for every market in `props.MARKETS` the response contains, and the
assumptions it relies on are listed in `props.PARSER_ASSUMPTIONS`.
