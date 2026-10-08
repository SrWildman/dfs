"""Per-column explanations, attached as a cell note on each header (hover the little corner mark).

A column name alone does not say what a number means -- "WOPR", "Opp Def EPA/pass", "Ceiling skill (85th
pct)" are all jargon to anyone who did not build the sheet. Each definition lives here, once, and is applied
wherever that header appears, so the explanation is always one hover away and cannot drift between tabs.

Three groups, one dict each (headers are matched by their TEXT, never by position):

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
from dfs.sheets import SheetsClient, column_letter
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
        "Fitted only on weeks BEFORE this one. Not the default projection: Model Check's Projection Race "
        "shows whether it earns that. Coloured within his position."
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
        "a ? means only the weaker evidence supports it). "
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

MODEL_CHECK_NOTES = {
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
    "Projection": (
        "TFFB = TFFB's own ProjPts; AggPts = the equal-weight average of every source; CalPts = each source "
        "bias-corrected by position and salary tier, then weighted (TFFB, Sleeper, FantasyPros; not UM). "
        "UM = the UM model, tracked as its own row. All are judged on weeks CalPts did not train on."
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
    client: SheetsClient, tab: str, row: int, header: list, notes: dict[str, str]
) -> int:
    """Set a note on every header cell in `header` (the values of row `row`) whose text is a key of `notes`.
    Returns how many notes were written."""
    written = 0
    for index, name in enumerate(header):
        text = notes.get(str(name))
        if text:
            client.set_note(tab, f"{column_letter(index)}{row}", text)
            written += 1
    return written


def apply_header_notes(client: SheetsClient, tab: str, header_row: int, notes: dict[str, str]) -> int:
    """Read `tab`'s header row and note every column named in `notes`. Matches by header text, so it is
    safe wherever the columns sit; returns how many notes were written."""
    rows = client.read_range(tab, f"A{header_row}:{header_row}")
    return apply_notes_to_values(client, tab, header_row, rows[0] if rows else [], notes)
