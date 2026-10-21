"""Per-column explanations, attached as a cell note on each header (hover the little corner mark).

A column name alone does not say what a number means -- "WOPR", "Opp Def EPA/pass", "Ceiling skill (85th
pct)" are all jargon to anyone who did not build the sheet. Each definition lives here, once, and is applied
wherever that header appears, so the explanation is always one hover away and cannot drift between tabs.

**One registry, keyed by header text.** `player_tab_notes()` is every header on EdgeRaw, Player Pool and
Lineups (both spellings where the tabs differ: `Position`/`Pos.`, `Salary`/`DK Sal`, `ProjPts`/`Pts`,
`Ceiling`/`Ceil`), `edge_finder_tab_notes()` adds the Edge Finder's own, `BOARD_NOTES` the Board's tables.
Every apply REPLACES the notes on the header row (a note that no longer matches its header is cleared: that
is what left a `Tgt%` note on `GameStart` after a column move), and a test fails when a visible header has
no note or a note mentions a removed feature (`REMOVED_FEATURES`).

The groups (headers are matched by their TEXT, never by position):

- `USAGE_NOTES`: the five usage-volume columns on EdgeRaw, Player Pool, Lineups and PlayerPoolRaw. They carry
  the window ("last 3 games played") and the week the data runs through -- the number comes from the latest
  usage sync on disk (`usage_through_week`), so it is as fresh as the last `dfs setup polish`.
- `SLATE_GAME_NOTES` / `SLATE_TEAMS_NOTES`: Slate Grid's game metrics and its TEAMS section. The two
  share header texts (`Pace`, `PROE`, `Expl%`) but mean different things (both teams averaged vs one
  team), so they are separate dicts applied to separate header rows.
- `MODEL_CHECK_NOTES`: every column on the `Model Check` tab.

`apply_header_notes` reads the header row and writes the notes; `apply_notes_to_values` does the same for a
header row whose text the caller already has (Model Check, whose rows are built in memory).
"""

from __future__ import annotations

from dfs import store
from dfs.sheet_columns import ALL_PCT_COLUMNS
from dfs.sheet_consider import CONSIDER_NOTE
from dfs.sheets import SheetsClient
from dfs.usage_metrics import USAGE_WINDOW_GAMES

_WINDOW = (
    f"Window: his last {USAGE_WINDOW_GAMES} games played (fewer if he has fewer; a game he missed is "
    f"skipped, "
    "never counted as a zero). Volume, not efficiency -- usage holds up week to week, efficiency regresses. "
    "Coloured against the other players at his position. Blank where it does not apply."
)

USAGE_NOTES = {
    "Tgt%": (
        "TARGET SHARE: his targets divided by his team's targets over the window. "
        f"WR, TE and RB only. {_WINDOW}"
    ),
    "WOPR": (
        "WEIGHTED OPPORTUNITY RATING: 1.5 x target share + 0.7 x air-yards share (nflverse's own formula), "
        f"recomputed over the window. WR and TE only. {_WINDOW}"
    ),
    "Rush%": (
        "RUSH SHARE: his carries divided by his team's carries over the window (QB scrambles count as "
        f"carries). RB and QB only. {_WINDOW}"
    ),
    "RZ/G": (
        "RED-ZONE LOOKS PER GAME: targets plus carries from inside the opponent's 20-yard line, per game "
        f"over the window. RB, WR, TE and QB. {_WINDOW}"
    ),
    "HVT/G": (
        "HIGH-VALUE TOUCHES PER GAME: targets plus carries from inside the opponent's 10-yard line, per game "
        f"over the window. RB only. {_WINDOW}"
    ),
}

_CONTEXT_LABEL = "Context, not proven to beat projections; tracked in Model Check."
_NOT_RATED = "Not rated below 10 pts (QBs projected that low are left blank; they are not rostered anyway)."
_DST_LOW = (
    "A DST projected under 4 pts is shown muted: the engine understates the upside of the lowest decile."
)
_MODEL = "From the outcome-distribution tables in docs/MODEL.md, applied to CalPts."

_WEATHER_NOTE = (
    "WIND flag: fires at 15 mph or more (research: QBs lose about a point from 10 mph, flat out to 20; the "
    "old 20 mph line caught only a fifth as many games). Rain and cold are CONTEXT only, not in CalPts and "
    "not a flag: rain cost QBs about 1.7 points and each 10°F under 50°F about 0.5 (WR/TE less); Temp and "
    "Precip are on WeatherRaw. Domes and closed roofs have no wind."
)

EDGE_FINDER_NOTES = {
    "Wind": "Forecast sustained wind (mph) at kickoff. " + _WEATHER_NOTE,
    "CalPts": (
        "CALIBRATED PROJECTION: every source (TFFB, Sleeper, FantasyPros) with its level bias corrected "
        "per position and salary tier (shrunk toward zero), then weighted by how accurate each has been. "
        "Fitted only on weeks BEFORE this one. The projection late swap, the lineup odds and the Suggested "
        "builds use (ProjPts where CalPts is blank); TFFB stays visible for comparison. Model Check's "
        "Projection Race keeps scoring it. Coloured within his position."
    ),
    "Hit3x%": (
        "CASH: the chance he scores at least 3 x his salary per $1,000 (the line behind Val >= 3), 0-100. "
        f"{_MODEL} {_NOT_RATED} {_DST_LOW}"
    ),
    "Boom%": (
        f"GPP UPSIDE: the chance he scores at least 4 x his salary per $1,000, 0-100. {_MODEL} "
        f"{_NOT_RATED} {_DST_LOW}"
    ),
    "Bust%": (
        "CASH RISK: the chance he scores under 2 x his salary per $1,000, 0-100. LOWER is better. "
        f"{_MODEL} {_NOT_RATED} {_DST_LOW}"
    ),
    "Floor": f"20th percentile of his outcomes, in points. {_MODEL} {_NOT_RATED} {_DST_LOW}",
    "CeilM": (
        "The model's ceiling: the 85th percentile of his outcomes, in points. Beside TFFB's own Ceil, which "
        f"has been well calibrated (beaten about 14% of the time). {_MODEL} {_NOT_RATED} {_DST_LOW}"
    ),
    "xFP/G": (
        "EXPECTED FANTASY POINTS per game over his last 3 games played, from ffopportunity's opportunity "
        "model (what his targets, carries and field position were worth in DK points, whether or not he "
        f"scored). Coloured within position. {_CONTEXT_LABEL}"
    ),
    "Edge": (
        "TOKENS (context, not proven edges): INJ+ a back ahead of him is out and he inherits carries (the "
        "measured split from 12 seasons); FADE↓ a tight end scoring 2+ points a game above his expected "
        "points over 3 games; USAGE↑ / USAGE↓ a back whose carry share rose 10+ points / fell 5+ points "
        "over his last 2 games against the 6 before; Proj ▼ / Proj ▲ the projection is likely too high / too "
        "low, because his recent usage spiked or slumped (the research shows projections over-react to both; "
        "a ? means only the weaker evidence supports it); Props ▲ +x.x / Props ▼ -x.x the Vegas props "
        "projection (PropPts) is x.x above / below CalPts, shown when they are 3.0 or more apart. The Props "
        "chip is context only: it changes nothing in Do, CalPts or any probability; Model Check tracks it. "
        f"{_CONTEXT_LABEL} Not part of Flags: bold names are unchanged."
    ),
}

SLATE_GAME_NOTES = {
    "Wind": "Forecast sustained wind (mph) at kickoff. " + _WEATHER_NOTE,
    "GameEnv": (
        "GAME ENVIRONMENT (0-100): how shootout-friendly the game is -- an equal-weight percentile blend of "
        "total, spread tightness, combined pace and PROE. This is the average of both teams' values."
    ),
    "Pace": (
        "Seconds between offensive snaps in neutral game script (close score, first three quarters). "
        "LOWER is "
        "faster, so green. Average of both teams; blended with last season early in the year."
    ),
    "PROE": (
        "PASS RATE OVER EXPECTED in neutral script (nflverse's pass_oe): above 0 = passes more than "
        "the situation "
        "implies. Average of both teams."
    ),
    "Expl%": (
        "EXPLOSIVE-PLAY RATE: the share of plays gaining 20+ yards passing or 10+ rushing. Average "
        "of both teams."
    ),
}

SLATE_TEAMS_NOTES = {
    "Team": (
        "One row per team on this week's schedule, highest implied total first. Dimmed = no player "
        "on the DK slate."
    ),
    "Opp": "This week's opponent.",
    "Implied": (
        "Vegas implied team total from the game's total and spread: home = (Total + Spread) / 2, "
        "away = (Total - Spread) / 2 (Spread is signed from the home team's view)."
    ),
    "Pace": (
        "This team's seconds between snaps in neutral script. LOWER is faster, so green. Blended with last "
        "season early in the year."
    ),
    "PROE": "This team's pass rate over expected in neutral script; above 0 = passes more than expected.",
    "Expl%": "Share of this team's plays gaining 20+ yards passing or 10+ rushing.",
    "Off EPA/play": (
        "Expected points added per play by this team's OFFENSE (every game state, real scrimmage plays). "
        "Higher = better offense. Blended with last season early in the year."
    ),
    "Off EPA/pass": "Expected points added per PASS play by this team's offense. Higher = better.",
    "Off EPA/rush": "Expected points added per RUSH play by this team's offense. Higher = better.",
    "Opp Def EPA/pass": (
        "Expected points added per pass play that the OPPONENT's defense ALLOWS. Higher = softer "
        "pass defense, "
        "so a better spot for this offense (green)."
    ),
    "Opp Def EPA/rush": (
        "Expected points added per rush play that the OPPONENT's defense ALLOWS. Higher = softer run "
        "defense, "
        "so a better spot for this offense (green)."
    ),
}

_PTS = (
    "TFFB's projection: his median outcome in DraftKings points. CalPts (our corrected projection) "
    "sits beside "
    "it, and TFFB stays visible everywhere for comparison."
)
_SAL = "DraftKings salary. Nine players share the $50,000 cap."
_CEIL = (
    "TFFB's own ceiling: about his 85th-percentile outcome (14% of players beat it). Blank where TFFB gives "
    "none, never a zero. CeilM is the model's version."
)
_GROUP = (
    "Section marker: the columns after it, up to the next marker, are one group. Collapse it "
    "with the +/- above."
)

# Everything on EdgeRaw, Player Pool and Lineups that is not a usage, weather, slate-game or probability
# column (those have their own dicts above). Where the tabs spell a header differently, both spellings
# are here.
CORE_NOTES = {
    "Pool": (
        "Whether he is in your pool: Cash, GPP or Both. Pick one to add him, blank it to remove him. The "
        "same "
        "cell on EdgeRaw, Player Pool and the Edge Finder."
    ),
    "Name": "The player, spelled as DraftKings spells him.",
    "Position": "DraftKings position: QB, RB, WR, TE or DST.",
    "Pos.": "DraftKings position: QB, RB, WR, TE or DST. (On Lineups it is the roster slot, FLEX included.)",
    "Pos": "DraftKings position: QB, RB, WR, TE or DST.",
    "Team": "His team.",
    "Opp": "This week's opponent.",
    "Opp.": "This week's opponent.",
    "Salary": _SAL,
    "DK Sal": _SAL,
    "ProjPts": _PTS,
    "Pts": _PTS,
    "AggPts": (
        "The plain average of every projection source (TFFB, Sleeper, FantasyPros). Context: CalPts is the "
        "corrected version."
    ),
    "Val": (
        "VALUE: projected points per $1,000 of salary (ProjPts divided by Salary / 1,000). 3.0 is the usual "
        "cash pace, 4.0 the GPP pace."
    ),
    "ValAdj": (
        "VALUE, ADJUSTED (0-100, within his position): half how high his projection ranks among that "
        "position's regular starters, half how far he beats what his salary normally buys. EdgeRaw is sorted "
        "by it. Compare players within a position, not across positions."
    ),
    "Ceiling": _CEIL,
    "Ceil": _CEIL,
    "CeilVal": "CEILING VALUE: his Ceiling points per $1,000 of salary. Blank where Ceiling is blank.",
    "Own%": (
        "PROJECTED OWNERSHIP: the share of lineups that will hold him. Reads 0 for everyone until ownership "
        "publishes, usually late in the week."
    ),
    "Avail": "DraftKings' status: Q (questionable), OUT or IR. Blank means no designation.",
    "Flags": (
        "Every warning that applies, most urgent first: OUT; WIND (15+ mph); IMPL↑ / IMPL↓ (his team's "
        "implied "
        "total moved 6+ points); LEVERAGE; CHALK (owned 20%+); TFFB↑ / TFFB↓ (TFFB far from the other "
        "sources). Bold names have a flag."
    ),
    "Flag": "The single most urgent flag (hidden helper behind the bold Name).",
    "GAME": _GROUP + " This one is the game: total, spread, pace and the matchup.",
    "Spread": "The point spread, signed from the home team's view (negative = the home team is favoured).",
    "O/U": "The game's Vegas total: the points both teams are expected to score.",
    "OverUnder": "The game's Vegas total: the points both teams are expected to score.",
    "Team Implied": (
        "His team's Vegas implied total: (Total plus or minus the spread) / 2. The higher, the more points "
        "his offense is expected to score."
    ),
    "OppPosRank": (
        "TFFB's strength-of-schedule rank (1-32) of his opponent at his position. 1 is the EASIEST matchup "
        "(that defense allows the most points to his position), 32 the toughest, so low is green. Noisy "
        "early in the season; OppEPA is steadier."
    ),
    "OppEPA": (
        "How soft his matchup is, in expected points added per play: the opponent's pass defense for a "
        "QB, WR "
        "or TE, its rush defense for an RB, and for a DST the opposing offense with the sign flipped. Higher "
        "= a softer matchup."
    ),
    "GameID": "The game's identifier. Two players with the same GameID are in the same game.",
    "TmRank": (
        "Where his salary ranks among his team's players at his position (1 = the highest-priced). A proxy "
        "for the depth chart, not a measured share: TmRank 1 at WR is that team's WR1 by price."
    ),
    "CEIL": _GROUP + " This one is ceiling detail: CeilPct, Leverage, OwnStatus, Floor and CeilM.",
    "CeilPct": "His Ceiling's percentile within his position, 0-100. A proxy for upside before ownership.",
    "Leverage": (
        "CeilPct minus his ownership percentile within his position (-100 to +100). Positive = more upside "
        "than the field's ownership gives him. Blank until ownership publishes."
    ),
    "OwnStatus": "Whether Own% is real ('real') or not published yet ('unpublished').",
    "MOVE": _GROUP + " This one is line movement since the week's first odds snapshot.",
    "ImpliedMove": (
        "How far his team's implied total has moved since the first odds snapshot of the week, in points. "
        "6 or more sets IMPL↑ / IMPL↓."
    ),
    "TotMove": "How far the game's total has moved since the first odds snapshot of the week.",
    "SpdMove": "How far the spread has moved since the first odds snapshot of the week.",
    "GameStart": "Kickoff, Eastern time. Late swap and the FLEX rule use it to know who has started.",
    "WX": _GROUP + " This one is the weather.",
    "Venue": "H (home) or A (away).",
    "Stadium": "The stadium.",
    "Roof": (
        "outdoors or dome. Blank means nflverse does not know yet (common for retractable roofs). "
        "Domes have no wind."
    ),
    "USAGE": _GROUP + " This one is usage volume: snap share, targets, carries and red-zone looks.",
    "Snap%": (
        "OFFENSIVE SNAP SHARE in his most recent game (nflverse). Blank for a player with no recorded game "
        "and for defenses; a real 0% stays 0%."
    ),
    "Id": "His DraftKings player id (hidden). Pool and the add-a-player box find him by it, never by name.",
    "NameKey": "His name in a plain lower-case form (hidden), used only to join sources.",
    "Edge ↗": (
        "Opens his row on EdgeRaw (the filtered view), where the Pool dropdown changes or removes him."
    ),
    "Overflow": (
        "Warns when a position has more players in the pool than its block has rows (nobody is "
        "dropped silently)."
    ),
    "Used": "How many of this week's lineups hold him.",
    "In": "Which of this week's lineups hold him.",
    "% of Cap": "His salary as a share of the cap (config.toml [lineups] salary_cap).",
    "Issues": (
        "Per player: DUPLICATE, or his Avail flag (OUT, IR, Q). On the Total row: OVER the cap, INCOMPLETE "
        "(fewer than 9 players), DST/QB (your DST faces your own QB), RB/GAME (two RBs from one game), "
        "'Weak for cash' (a Cash lineup under 40% Cash %), or OK."
    ),
    "Games": "How many different games your nine players come from.",
    "Min Unique": (
        "The fewest players this lineup has that some other lineup of yours does not (9 = nothing shared)."
    ),
    "Player Key": (
        "DraftKings' canonical name for whatever was typed in column A (hidden), so two spellings "
        "of one player are caught."
    ),
}

# The STAKES block on Bankroll (`sheet_stakes.py`): both tables' headers, matched by text.
STAKES_NOTES = {
    "Format": "The kind of contest (`series.classify`); the Cash and GPP rows add up their formats.",
    "Entries": "How many entries you made, whatever the fee.",
    "ROI": 'Winnings minus fees, divided by fees. "–" when you paid no fees (freerolls).',
    "Cash rate": "The share of entries that finished in a paid place.",
    "Weekly SD": (
        "The standard deviation of your weekly net (winnings minus fees, one number per NFL week with "
        'entries). A bigger number is a bumpier ride. "–" under 2 weeks.'
    ),
    "Bucket": "Cash (H2H, 50/50, Double Up) or GPP (every other format): the two bankrolls this tab keeps.",
    "Weight on own ROI": "min(1, entries / 100): how much of the share comes from the bucket's own ROI.",
    "ROI share": "The bucket's ROI divided by the ROI total of the buckets at or above the floor.",
    "Even share": "An equal split among the buckets at or above the floor.",
    "Note": "Why a bucket gets nothing from the default split (no history, or ROI below the floor).",
    "Share": "Weight x ROI share + (1 - weight) x even share: the default split of the planned spend.",
}

CONTESTS_NOTES = {
    "Your decisions": "A measure of your own choices, added up over the weeks that have a standings file.",
    "So far": "The season's number for that measure.",
    "Based on": "How many entries the number rests on, and how many different weeks they come from.",
    "Build feature": (
        "How a lineup was put together: a stack, a bring-back, RB + his own DST, ownership, salary left."
    ),
    "Top 1% did": (
        "The share of the best 1% of lineups (or their average, for ownership and salary) in your large GPPs "
        "that did this."
    ),
    "Whole field did": "The same measure over every lineup in those contests.",
    "Gap (top 1% minus field)": (
        "What the winners did more or less than the field: percentage points for shares, a ratio (x) for the "
        "ownership product. Directional until more weeks pile up."
    ),
    "Top-1% lineups counted": "How many top-1% lineups the first column rests on.",
    "Field lineups counted": "How many lineups the second column rests on.",
    "Series": "The contest's name, without the entry counter, the Showdown matchup or the H2H opponent.",
    "Format": "The kind of contest: H2H, 50/50, Double Up, Multiplier (Triple Up ...), SE GPP, N-max GPP.",
    "Fee": "The entry fee (the middle one, if it changed).",
    "Field": "The typical number of entries in this contest.",
    "Paid %": "The share of the field that gets paid (places paid / entries).",
    "Played": "How many times you entered it this season.",
    "ROI": "Winnings minus fees, divided by fees, this season. Freerolls are not in this table.",
    "Cash rate": "The share of this season's entries that finished in a paid place.",
    "Avg finish": (
        "Your average finish as a share of the field (place / entries), this season. Lower is better: "
        "10% means the top tenth. Blank for head-to-head."
    ),
    "Cash line (avg slate)": (
        "The score that finishes at this contest's paid share on an average slate, from the format's "
        "curve. Cash lines move a lot with how the slate scored, so read it as a typical week, not this one."
    ),
    "Latest cash line": "The last paid place's score in your most recent standings file for this contest.",
    "Cash line vs same-format peers": (
        "This contest's real cash line minus the average cash line of the other contests of its format in "
        "the same weeks, averaged over those weeks. Above 0 means it took a higher score to cash (tougher); "
        "below "
        "0, easier. Blank until a week has standings files for two contests of the format."
    ),
    "Weeks behind it": "How many weeks of standings files the gap rests on. Few weeks, loose number.",
    "Week": "The NFL week the contest was played.",
    "Entrants": "How many entries the contest had.",
    "Cash line": "The score of the last paid place.",
    "Top 10%": "The score that just makes the top tenth of the field.",
    "Top 1%": "The score that just makes the top one percent.",
    "Winner": "The winning score.",
    "Median": "The middle score of the field.",
    "Contests": "How many contests' standings files the row averages.",
    "QB + 1 catcher": "Share of lineups with the QB and at least one WR or TE from his team.",
    "QB + 2 catchers": "Share of lineups with the QB and at least two WR or TE from his team.",
    "Bring-back": "Share of lineups with a player (not a DST) from the QB's opponent.",
    "Salary left": "Average salary left unspent out of $50,000.",
    "Left $1,000+": "Share of lineups that leave $1,000 or more unspent.",
    "FLEX RB": "Share of lineups whose FLEX is a running back.",
    "FLEX WR": "Share of lineups whose FLEX is a wide receiver.",
    "FLEX TE": "Share of lineups whose FLEX is a tight end.",
    "Chalk (of 5)": "How many of the contest's five most-owned players the average lineup holds.",
    "DST with QB": "Share of lineups whose defense is the QB's own team.",
    "DST vs QB": "Share of lineups whose defense faces the QB's team.",
    "Duplicates": "Share of lineups whose exact nine players appear in two or more entries of the contest.",
    "Your score": "The band your lineup's score fell in.",
    "Entries": "How many head-to-head entries fell in the band.",
    "Wins": "Head-to-head wins.",
    "Losses": "Head-to-head losses.",
    "Ties": "Ties (the fee is refunded; counted as half a win in the rate).",
    "Win rate": "Wins plus half the ties, divided by entries.",
    "Opponent (2+ meetings)": "An opponent you have met at least twice.",
    "Meetings": "How many times you have played them.",
    "Net": "Winnings minus fees against them, in dollars.",
    "Entries this season": "How many entries this season.",
    "Net this season": "Winnings minus fees this season, in dollars.",
    "Entries, all seasons": "How many entries in the whole log.",
    "Net, all seasons": "Winnings minus fees over the whole log, in dollars.",
}

MODEL_CHECK_NOTES = {
    "Salary tier": "The salary band a bias is measured in (CalPts corrects per position and band).",
    "Bias now": (
        "How far this source misses at this position and price, shrunk toward zero when few players back "
        "it. Negative = the source runs HIGH; CalPts takes that many points off. Fitted on every scored "
        "week so far."
    ),
    "Bias last week": "The same number as it stood before this week was scored (blank on the first fit).",
    "TFFB": "The weight TFFB's projection gets in CalPts at this position (fitted on every scored week).",
    "Sleeper": "The weight Sleeper's projection gets in CalPts at this position.",
    "FantasyPros": "The weight FantasyPros' projection gets in CalPts at this position.",
    "TFFB (last week)": "TFFB's weight before this week was scored.",
    "Sleeper (last week)": "Sleeper's weight before this week was scored.",
    "FantasyPros (last week)": "FantasyPros' weight before this week was scored.",
    "Unflagged avg": (
        "Mean of (actual minus CalPts) for the same position's players with no signal: what a signal has "
        "to beat."
    ),
    "n": "Sample size. A row with n under 30 is 'thin' (muted italic): too few players to say anything yet.",
    "Beat Ceiling": "Share of players whose actual DK points were MORE than their published Ceiling.",
    "90% low": "Lower end of the 90% Wilson confidence interval for the rate beside it.",
    "90% high": "Upper end of the 90% Wilson confidence interval for the rate beside it.",
    "Ceiling is really the Nth percentile": (
        "1 minus the share who beat their Ceiling. 85% means Ceiling behaves like an 85th-percentile outcome "
        "(FantasyLabs' own ceiling, the top 15%, is the hypothesis being tested)."
    ),
    "Ceiling skill (80th pct)": (
        "How well Ceiling works as an 80th-percentile forecast (pinball skill): 1 minus its pinball loss "
        "over "
        "the loss "
        "of the best "
        "constant quantile. 0 = no better than ignoring the player. Highest across the three columns = the "
        "quantile Ceiling really is. Never RMSE: a quantile is not a mean."
    ),
    "Ceiling skill (85th pct)": "The same skill read as an 85th-percentile forecast (see the 80th pct).",
    "Ceiling skill (90th pct)": "The same skill read as a 90th-percentile forecast (see the 80th pct).",
    "Bias": (
        "Mean of (actual minus projected) DK points. Negative = the projections ran HIGH. "
        "Coloured red below zero, green above."
    ),
    "MAE": "Mean absolute error: the average miss in points, ignoring direction.",
    "ESPN vs market": (
        "How far ESPN's chance of the Over (Yes for an anytime TD) sits from the books', in points. "
        "Each band includes the bigger gaps."
    ),
    "ESPN side won": "How often the side ESPN favoured won. Pushes and players who sat are left out.",
    "Price needed": "The win rate the median quoted prices asked for (the mean of 1 / decimal price).",
    "Return": "Profit per unit staked on ESPN's side at the median quoted price. Shadow: nothing is bet.",
    "Verdict": "A word only from 100 rows in a cell: ESPN's side beat or missed its price by 2 SEs.",
    "Slope": (
        "Calibration slope: regress actual on projected. 1.0 = well calibrated; below 1 = the "
        "projections are "
        "too extreme (high ones too high, low ones too low); above 1 = too conservative."
    ),
    "R²": (
        "Share of the variance in actual points the projection explains. Public studies find only "
        "3-23% even for "
        "the best sources -- a low value is the sport, not a bug."
    ),
    "Spearman": (
        "Rank correlation within position: does the projection ORDER players correctly? 1 = perfect order, "
        "0 = none. More relevant to DFS than exact points."
    ),
    "Projected": "Projection bucket (en dash = range, + = and up).",
    "Mean projected": "Average projected points in the bucket.",
    "Mean actual": (
        "Average actual DK points in the bucket. Below 'Mean projected' = the projections ran high."
    ),
    "Quintile": ("ValAdj quintile WITHIN position (a QB is ranked only against QBs). Q5 = highest ValAdj."),
    "Mean residual": (
        "Mean of actual points minus the points his SALARY predicts (fit on actual points vs salary, per "
        "position, over the weeks scored). Positive = beat his price."
    ),
    "Source": "Whose projection: TFFB's ProjPts, Sleeper, FantasyPros, or AggPts (the equal-weight average).",
    "Projected Val": "Projected points per $1,000 of salary (Val), in bands.",
    "Hit 3x": "Share of players whose ACTUAL points reached 3 times their salary per $1,000 (the cash line).",
    "Hit 4x": "Share of players whose ACTUAL points reached 4 times their salary per $1,000 (the GPP line).",
    "Flag": "A flag on EdgeRaw's Flags column, recomputed from the archived data with today's code.",
    "Actual - proj.": "Mean of (actual minus projected) points for flagged players. Negative = ran high.",
    "Unflagged n": "Players at the same positions with no flag at all.",
    "Unflagged mean": "Mean of (actual minus projected) points for those unflagged players, for comparison.",
    "Weeks": "Weeks included in the consistency measure.",
    "Mean MAE": "Average of the source's weekly MAE.",
    "CV": "Coefficient of variation of the weekly MAE (std / mean): lower = steadier week to week.",
    "Players rated by": (
        "The shadow source whose players the row is measured on. A shadow source is scored every week but is "
        "not in CalPts until it has enough scored weeks (config.toml [projection] shadow_min_weeks)."
    ),
    "Projection": (
        "TFFB = TFFB's own ProjPts; AggPts = the equal-weight average of every source; CalPts = each source "
        "bias-corrected by position and salary tier, then weighted (TFFB, Sleeper, FantasyPros; not UM). "
        "UM = the UM model, tracked as its own row; (shadow) = ESPN or props, tracked, not in CalPts yet. "
        "All are judged on weeks CalPts did not train on."
    ),
    "ρ (rank)": (
        "Rank correlation within position and week (averaged over groups of 5+, weighted by n): does the "
        "projection ORDER players correctly? Hard to beat TFFB at this."
    ),
    "Week": "The week being predicted (CalPts was fitted on the weeks before it).",
    "TFFB MAE": "TFFB's mean absolute miss that week.",
    "AggPts MAE": "AggPts' mean absolute miss that week.",
    "UM MAE": "The UM model's mean absolute miss that week, on the players it rates.",
    "Measure": (
        "Hit 3x = P(actual >= 3 x salary/$1,000), Boom 4x = P(actual >= 4x), Bust under 2x = P(actual < 2x): "
        "the three probability columns, judged on whether they happened."
    ),
    "Decile": "Players ranked by predicted probability, cut into ten equal bins; 1 = lowest.",
    "Predicted": "Mean predicted probability in the decile.",
    "Realized": "Share of the decile's players for whom it actually happened.",
    "Gap (pts)": "Realized minus predicted, in percentage points. Positive = it happens more than predicted.",
    "Off?": "'off' when the gap exceeds 8 points with n of 30 or more. Reported, never adjusted.",
    "CalPts MAE": "CalPts' mean absolute miss that week.",
    "TFFB ρ": "TFFB's rank correlation that week (within position, n-weighted).",
    "AggPts ρ": "AggPts' rank correlation that week.",
    "CalPts ρ": "CalPts' rank correlation that week.",
    "Signal": (
        "A context signal: FADE↓ (TEs only, DK points vs expected), USAGE↑/USAGE↓ (RB carry share, last 2 "
        "games vs the 6 before), INJ+ (a back ahead of him is out), and the top-8 / bottom-4 position "
        "matchups. 'Unflagged QB/RB/WR/TE' rows are the same position with no signal at all: read each "
        "signal against them. Context, not proven to beat projections."
    ),
    "Actual - ProjPts": (
        "Mean of (actual minus TFFB ProjPts) for the group. Positive = they beat the projection."
    ),
    "Actual - CalPts": "Mean of (actual minus CalPts) for the group.",
    "Hit (vs ProjPts)": (
        "Share on the right side of ProjPts: above it for an 'up' signal, below it for a 'down' signal."
    ),
    "Hit (vs CalPts)": "The same hit rate measured against CalPts.",
}


# The Edge Finder's own headers (the player tabs' dicts cover Pool, Name, Pos, Team, Salary, Own%, CalPts,
# Hit3x%, Boom%, Bust%, Floor, CeilM, ValAdj, ProjPts and Edge there too).
GREY_NOTE = (
    "Grey row = 'Next up' (filler that rounds out Top plays, not a model pick) or thin evidence "
    "(under 3 games, or a Proj chip marked '?'); the Do and the Why say which."
)
NAME_WITH_CONSIDER = f"{CORE_NOTES['Name']} {CONSIDER_NOTE}"

EDGE_FINDER_TAB_NOTES = {
    "Name": f"{NAME_WITH_CONSIDER} {GREY_NOTE}",
    "Do": (
        "The verb: what to do with him. 'Cash add', 'GPP add' and 'GPP leverage' are the pool candidates; "
        "'Next up' (greyed, in Top plays' Cash block) only fills the list, it is not a model pick; "
        "'In pool (Both)' once you have ticked him. Reads your Pool live."
    ),
    "Why": "A one-line reason. Hover the cell for the full explanation.",
    "Lev": (
        "LEVERAGE: his Boom% rank minus his Own% rank among his position's players (-100 to +100). +25 or "
        "more is much more upside than ownership. Blank until ownership publishes."
    ),
    "TFFB": "TFFB's projection, in DraftKings points.",
    "Sleeper": "Sleeper's projection, in DraftKings points.",
    "FantPros": "FantasyPros' projection, in DraftKings points.",
    "Diff": "CalPts minus TFFB's ProjPts. Positive = we are higher than TFFB.",
    "+Car/G": (
        "Extra carries per game he inherits while the regular back is out (the measured carries table)."
    ),
    "+xFP/G": "What those extra carries are worth in DraftKings points per game.",
    "Method": (
        "How the bump was worked out: 'table' = the measured carries table, 'w/wo' = his own "
        "with-or-without history."
    ),
    "Priced?": (
        "Whether his salary already moved after the injury designation. Blank when no earlier "
        "snapshot exists."
    ),
    "Role": "His role on his team before the absence.",
    "Tgt/G": "Targets per game over his last 3 games played.",
    "Car/G": "Carries per game over his last 3 games played.",
    "Missed": "Team games he has missed so far this season.",
    "Metric": "The usage measure that moved.",
    "Last 3": "His average over the last 3 games played.",
    "Earlier": "His average over the games before those 3.",
    "Change": "Last 3 minus Earlier.",
    "Trend": "▲ rising or ▼ falling, only when the move is bigger than normal week-to-week noise.",
    "Matchup": "The offense and the opponent it faces. Click + to open the team's top players.",
    "Grade": (
        "Soft is one of the 8 easiest matchups on the slate, Tough one of the 4 hardest, Middle the rest. "
        "Context only."
    ),
    "Players": "How many of the team's players are listed under it.",
    "Token": "The context chip that fired for him (see Edge). Unproven: a tiebreaker at most.",
    "DK/G L3": "His actual DraftKings points per game over his last 3 games.",
    "Read": "The signal and its plain-language reading. Model Check scores each one against its baseline.",
}

BOARD_NOTES = {
    "Matchup": "The game, away at home.",
    "Total": "The game's Vegas total.",
    "Fav": "The favourite.",
    "Spread": "The spread, from the favourite's side.",
    "Shootout?": (
        "Shootout when the game total is 48 or more (a first-pass line, not yet tuned on real slates)."
    ),
    "GPS": (
        "TFFB's pace-of-play score for the game, 1-5 (Kyle Borgognoni's weekly article). Blank "
        "until it publishes."
    ),
    "Player": f"The player; Pool is his live Cash / GPP / Both control. {CONSIDER_NOTE}",
    "What changed": "What moved since the last sync, for the players in your pool.",
    "Why": "Why he is on this list.",
    "Pooled": "Players in your pool at this position.",
    "Cash": "Of those, tagged Cash or Both.",
    "GPP": "Of those, tagged GPP or Both.",
    "Min Sal": "Cheapest salary in your pool at this position.",
    "Max Sal": "Most expensive salary in your pool at this position.",
    "Avg Sal": "Average salary of your pool at this position.",
    "Cheapest": "Your cheapest pooled player at this position.",
    "Cheapest Sal": "His salary.",
    "QB": "The quarterback.",
    "Opp": "The opponent.",
    "Pass catchers": "His team's top pass catchers by salary.",
    "Bring-back": "The opposing team's best pass catcher, the usual bring-back.",
    "Sal": "DraftKings salary.",
    "Team": "His team.",
    "WR1": "The team's highest-priced WR (TmRank 1).",
    "WR2": "The team's second-highest-priced WR.",
    "WR3": "The team's third-highest-priced WR.",
    "TE1": "The team's highest-priced TE.",
    "RB1": "The team's highest-priced RB.",
}

# Features that no longer exist. A note that mentions one is stale (the test pins this list).
REMOVED_FEATURES = (
    "Pool Picks",
    "Pool deck",
    "Source column",
    "checkbox",
    "TRUE/FALSE",
    "Set column",
    "LineMove",
)


# The hidden `*%ile` helpers behind the colour steps, one per coloured column (`ALL_PCT_COLUMNS`).
PERCENTILE_HELPER_NOTES = {
    helper: f"Hidden helper: {base}'s percentile within his position (0-100). The colour steps read it."
    for base, helper in ALL_PCT_COLUMNS.items()
}


# Issues cells are clipped at 110 px, so each one carries a note with the full warnings it can show. A note
# is static text and cannot follow the formula, so each note lists every warning of its kind (a roster row,
# the FLEX row, a Total row) rather than the one currently showing.
ISSUES_SLOT_NOTE = (
    "ISSUES for this pick. Blank means nothing is wrong.\n"
    "DUPLICATE: the same player is in this lineup twice.\n"
    "OUT or IR: he is ruled out; swap him.\n"
    "Q: he is questionable; check the news before his game."
)
ISSUES_FLEX_NOTE = (
    ISSUES_SLOT_NOTE + "\n"
    "Move NAME to FLEX (plays TIME): someone else in this lineup kicks off later than the player in FLEX. "
    "Put the latest kickoff in FLEX so you can still swap him late. Lineup Tools > Fix FLEX order does it. "
    "Quiet when two players tie for the latest kickoff or a game has started."
)
ISSUES_TOTAL_NOTE = (
    "ISSUES for the whole lineup. OK means none.\n"
    "OVER $N: salary is above the cap by that much.\n"
    "INCOMPLETE n/9: fewer than nine players.\n"
    "DST/QB: your DST plays against your own QB, which works against you.\n"
    "RB/GAME: two of your RBs are from the same game.\n"
    "Weak for cash: Cash % N%: a lineup marked Cash whose simulated Cash % is under 40%.\n"
    "Chalk: Name N% +k: a complete lineup holding none of the players projected at 16%+ owned (about 20% "
    "expected in a double-up; 25%+ if marked GPP), most owned first; double-up chalk runs above the "
    "projection (projected 31% is about 55% owned).\n"
    "Overused: Name 9/14 +k: a player of this lineup who is in more than half of your built lineups.\n"
    "Own sum N%: a lineup marked GPP whose total Own% is outside the 125%-145% GPP target (none for Cash).\n"
    "More than one can show at once."
)


SHADOW_SOURCE_NOTES = {
    "EspnPts": (
        "SHADOW: not in CalPts until 3 scored weeks. ESPN's own weekly projection, scored to DraftKings "
        "rules from its component stats (never ESPN's fantasy points). Scored in Model Check every week. "
        "Blank where ESPN has no projection for him."
    ),
    "PropPts": (
        "SHADOW: not in CalPts until 3 scored weeks. Vegas player props turned into a DraftKings score "
        "(de-vigged lines and anytime-TD odds from The Odds API, every book in the US region). Needs all the "
        "markets his position uses, so it is blank rather than partial (always blank for a DST). Scored in "
        "Model Check every week. Pulled Saturday (and refreshed Sunday for games not yet started). When it "
        "is 3.0 or more from CalPts the Edge column shows a Props chip: context only."
    ),
}

FIELD_OWN_NOTES = {
    "GppOwn": (
        "SHADOW: nothing reads it. TFFB's projected Own%, sharpened for your GPPs (TFFB runs about 5 points "
        "light on the chalk). Scored against your contests' real ownership in Model Check every week. "
        "Blank until TFFB publishes ownership."
    ),
    "CashOwn": (
        "SHADOW: nothing reads it. The same for your double-ups, where the chalk is much heavier. Scored in "
        "Model Check every week. Blank until TFFB publishes ownership."
    ),
    "OurOwn": (
        "SHADOW: nothing reads it. What your GPPs would own if the field used CalPts instead of its own "
        "projection. OurOwn above GppOwn = the field is light on a player we like. Model Check's WHEN WE "
        "DISAGREE says whether that gap has come true."
    ),
}


def player_tab_notes() -> dict[str, str]:
    """Every header on EdgeRaw, Player Pool and Lineups (and the Edge Finder's number columns), by text.
    The usage notes carry the week the usage data runs through, so this reads the latest sync."""
    return {
        **CORE_NOTES,
        **PERCENTILE_HELPER_NOTES,
        **SLATE_GAME_NOTES,
        **EDGE_FINDER_NOTES,
        **SHADOW_SOURCE_NOTES,
        **FIELD_OWN_NOTES,
        **usage_notes(),
    }


def edgeraw_notes() -> dict[str, str]:
    """EdgeRaw's: the player tabs' plus the soft-yellow "Consider" explanation on `Name` (it lists everyone,
    pooled or not; Player Pool and Lineups never show the yellow)."""
    return {**player_tab_notes(), "Name": NAME_WITH_CONSIDER}


def edge_finder_tab_notes() -> dict[str, str]:
    """The Edge Finder's header notes: the player tabs' plus its own."""
    return {**player_tab_notes(), **EDGE_FINDER_TAB_NOTES}


def usage_through_week() -> int | None:
    """The week the usage columns currently run through (the latest near-full week in the most recent
    `usage` sync), or None when there is no usage data yet. Read from `data/current/usage.csv`."""
    try:
        usage = store.load_current("usage")
    except FileNotFoundError:
        return None
    if usage.empty or "ThroughWeek" not in usage.columns:
        return None
    value = usage["ThroughWeek"].dropna()
    return int(value.iloc[0]) if not value.empty else None


def usage_notes() -> dict[str, str]:
    """`USAGE_NOTES` with the current "Data through Week N" appended (as of the last usage sync)."""
    week = usage_through_week()
    suffix = f" Data through Week {week} (as of the last sync)." if week is not None else ""
    return {name: text + suffix for name, text in USAGE_NOTES.items()}


def apply_notes_to_values(
    client: SheetsClient, tab: str, row: int, header: list, notes: dict[str, str], *, first_col: int = 0
) -> int:
    """REPLACE the notes on `row` (whose header text is `header`): every cell whose text is a key of `notes`
    gets that note, and every other cell from `first_col` on loses whatever note it had. Clearing is the
    point: a column that moved used to leave its old note on whichever header now sits there. One request
    per row.
    Returns how many notes were written."""
    texts = [notes.get(str(name).strip(), "") for name in header][first_col:]
    if texts:
        client.set_row_notes(tab, row, texts, first_col=first_col)
    return sum(1 for text in texts if text)


def apply_header_notes(client: SheetsClient, tab: str, header_row: int, notes: dict[str, str]) -> int:
    """Read `tab`'s header row and replace its notes with those in `notes` (matched by header text, so safe
    wherever the columns sit). Column A of row 1 is left alone: it carries the tab's own note
    (`sheet_style.TAB_NOTES`). Returns how many notes were written."""
    rows = client.read_range(tab, f"A{header_row}:{header_row}")
    return apply_notes_to_values(
        client, tab, header_row, rows[0] if rows else [], notes, first_col=1 if header_row == 1 else 0
    )
