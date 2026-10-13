# Odds API fixtures

**`synthetic_event_odds.json` is SYNTHETIC and UNVERIFIED.** It was written by hand, from memory of The Odds
API v4 event-odds response (`/v4/sports/{sport}/events/{eventId}/odds?markets=player_...`), in a session that
could not reach their documentation. Players, prices and lines are invented. It exists so `dfs.props`' parser
and maths have something to run on; it proves nothing about the real API's shape.

It deliberately contains things the parser has to cope with: a non-prop market (`h2h`), an alternate market
(`player_pass_yds_alternate`), a bookmaker with no markets, a bookmaker whose Over has no price, books on
different lines, a Yes-only anytime-TD book beside a Yes/No one, and players missing a market they need.

`event_odds_sample.json` (a REAL recorded response) is to be committed by the local session. Run
`tests/test_props.py` against it: `test_recorded_sample_parses` skips until it exists and then asserts the
parser reads at least one quote for every market in `props.MARKETS` the response contains, and the
assumptions it relies on are listed in `props.PARSER_ASSUMPTIONS`.
