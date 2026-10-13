"""`PropPts`: Vegas player props turned into an expected DraftKings score. Pure: every function takes plain
data (a parsed response, dataframes), none reads a file or the network. The Odds API pull itself, its key and
the join to the DraftKings pool belong to the caller (the local session).

**UNVERIFIED against The Odds API's documentation.** This module was written in a session that could not
reach the-odds-api.com, so the response shape and the market keys below come from memory of their v4 docs,
not from reading them (`PARSER_ASSUMPTIONS` lists every one). `tests/fixtures/odds_api/
synthetic_event_odds.json` is a SYNTHETIC response shaped like that memory, not a recorded one. A real
response committed as `tests/fixtures/odds_api/event_odds_sample.json` replaces it as the authority; the
parser is deliberately tolerant (it skips what it cannot read and counts it) so the first real file shows
what is missing instead of crashing.

**Pipeline** (each step is one function; the maths in `docs/CALCULATIONS.md`, "PropPts"):

1. `parse_event_odds` -> one row per quoted outcome (event, book, market, player, side, line, price).
2. `book_probabilities` -> per book, the **multiplicative de-vig** of its Over/Under pair
   (`p_over = (1/o_over) / (1/o_over + 1/o_under)`); a Yes/No pair the same way; a Yes-only anytime-TD quote
   gets a flat haircut (`ANYTD_YES_ONLY_VIG`, a stated guess).
3. `consensus_lines` -> across books, the **median line**, then the **median de-vigged Over probability at
   that line**. A book quoting another line is moved onto the median line with the local normal shift
   `p' = Phi((line_b - line*) / sd + Phi^-1(p_b))` (the stated interpolation); a book already on the median
   line is not touched.
4. `expected_stat` -> the mean implied by (line, p). The line is a median, not a mean. **Rushing and receiving
   yardage are a gamma with variance `phi * mean`** (right-skewed, so mean > median; passing yards a normal,
   being nearly symmetric); **counts** (receptions, pass TDs, INTs) are
   Poisson when `phi <= 1` and negative binomial with variance `phi * mean` otherwise. `phi` is fitted per
   (position, stat) from nflverse history (`DISPERSION`, refit with `python -m dfs.props_history`).
   Anytime TD: `E[TDs] = -ln(1 - p)` (Poisson), scored as one TD bucket (DK pays rush and receiving TDs the
   same 6).
5. `prop_pts` -> DraftKings points from `dk_scoring`'s own constants, plus the expected 300/100/100 yardage
   bonuses, `bonus * P(yards >= threshold)` under the same fitted distribution.

**No partial PropPts.** A player missing a market his position needs (`REQUIRED_MARKETS`) is blank, never a
score from the markets that exist. DST gets none. Fumbles lost and 2-point conversions have no prop market
and are not in the score (CalPts' per-cell bias absorbs the small constant they leave out).
"""

from __future__ import annotations

import math
from collections.abc import Mapping

import numpy as np
import pandas as pd
from scipy import optimize, stats
from scipy.special import ndtr, ndtri

from dfs.dk_scoring import DK_OFFENSE_SCORING, YARDAGE_BONUS_POINTS, YARDAGE_BONUS_THRESHOLDS
from dfs.log import get_logger
from dfs.player_join import normalize_name, normalize_position, normalize_team

log = get_logger("props")

# ---------------------------------------------------------------------------------------------
# Markets
# ---------------------------------------------------------------------------------------------

# The Odds API market key -> our stat name. UNVERIFIED (see the module docstring): these are the keys the
# task brief listed and that I remember from their v4 docs; confirm against the first real response.
MARKETS = {
    "player_pass_yds": "pass_yd",
    "player_pass_tds": "pass_td",
    "player_pass_interceptions": "pass_int",
    "player_rush_yds": "rush_yd",
    "player_receptions": "rec",
    "player_reception_yds": "rec_yd",
    "player_anytime_td": "td",
}
YARDAGE_STATS = ("pass_yd", "rush_yd", "rec_yd")  # continuous
# Passing yards are close to symmetric (a median-to-mean gap of about 1%, measured on nflverse history; a
# gamma put it at 3% and over-corrected), so they are a normal with variance phi * mean. Rushing and
# receiving yards are right-skewed with a floor at 0: gamma.
NORMAL_STATS = ("pass_yd",)
COUNT_STATS = ("pass_td", "pass_int", "rec")  # integer: Poisson / negative binomial
TD_STAT = "td"  # the anytime-TD market: a probability of >= 1, not an Over/Under line

# What a position MUST have for a PropPts at all. Anything else a player has is used when present.
REQUIRED_MARKETS = {
    "QB": ("pass_yd", "pass_td"),
    "RB": ("rush_yd", "td"),
    "WR": ("rec", "rec_yd", "td"),
    "TE": ("rec", "rec_yd", "td"),
}
# Markets worth noting when absent (the player is still scored): the score then leaves that part out.
OPTIONAL_MARKETS = {
    "QB": ("rush_yd", "td", "pass_int"),
    "RB": ("rec", "rec_yd"),
    "WR": (),
    "TE": (),
}

# One TD bucket: the anytime-TD market does not say rush from receiving, and DK pays them the same.
TD_POINTS = DK_OFFENSE_SCORING["rush_td"]
if (
    DK_OFFENSE_SCORING["rec_td"] != TD_POINTS
):  # pragma: no cover - a DK rules change must be seen, not absorbed
    raise ValueError("rush and receiving TDs no longer score the same: PropPts' one TD bucket is wrong")

# ---------------------------------------------------------------------------------------------
# Constants (every number in the maths is named here)
# ---------------------------------------------------------------------------------------------

# A Yes-only anytime-TD quote is the implied probability plus the book's margin. We cannot see the margin
# from one side, so we take a flat fraction off: p = implied / (1 + ANYTD_YES_ONLY_VIG). A GUESS (books
# hold more on this market than on a -110/-110 pair); it is flagged on every row it touches
# (`p_method == "yes_only"`) and Model Check's PropPts bias is what will correct it.
ANYTD_YES_ONLY_VIG = 0.08
# Probabilities are clipped to this range before inverting: a 99% quote carries no information about the mean
# and sends the solver to infinity.
P_CLIP = (0.02, 0.98)
# The normal shift between two books' lines uses sd = sqrt(phi * level); the level is floored here so a
# 0.5 line does not give a zero sd.
SHIFT_MIN_LEVEL = 1.0
# A yardage bonus is "yards >= threshold" on an integer; in the continuous model that is yards >
# threshold - 0.5.
YARDAGE_CONTINUITY = 0.5
# Variance-to-mean ratio of a single game's stat given what a bettor knew (the last eight games): fitted on
# nflverse 2018-2025 by `python -m dfs.props_history`. See docs/CALCULATIONS.md, "PropPts", for the table. An
# upper bound on the market's own dispersion (the market also knows injuries and matchup), so means from lines
# are a little more spread than the market's: the effect is second order for the mean.
DISPERSION: dict[tuple[str, str], float] = {
    ("QB", "pass_yd"): 22.368,
    ("QB", "pass_td"): 0.833,  # under-dispersed: a Poisson is used (phi <= 1)
    ("QB", "pass_int"): 1.045,
    ("QB", "rush_yd"): 16.794,
    ("RB", "rush_yd"): 23.805,
    ("RB", "rec"): 1.507,
    ("RB", "rec_yd"): 19.825,
    ("WR", "rec"): 1.385,
    ("WR", "rec_yd"): 25.905,
    ("TE", "rec"): 1.313,
    ("TE", "rec_yd"): 20.361,
}
# The same fit with positions pooled: the fallback for a (position, stat) not above (a WR's rushing yards) and
# the sd for moving one book's probability onto another's line.
DISPERSION_POOLED: dict[str, float] = {
    "pass_yd": 22.368,
    "pass_td": 0.833,
    "pass_int": 1.045,
    "rush_yd": 23.006,
    "rec": 1.399,
    "rec_yd": 24.070,
}
# QB interceptions per game, used only when a QB has no INT market and the caller has no rate for him:
# nflverse 2018-2025 QBs with 15+ attempts. Flagged `int:league` on the row.
QB_INT_LEAGUE_RATE = 0.7362
# A season rate over a handful of games is noisy: shrink it toward the league rate with this many
# pseudo-games.
INT_RATE_SHRINK_GAMES = 4


def dispersion(position: str, stat: str) -> float:
    """phi (variance / mean) for a stat at a position; the pooled value for a position we did not fit."""
    return DISPERSION.get((position, stat), DISPERSION_POOLED.get(stat, 1.0))


# ---------------------------------------------------------------------------------------------
# 1. Parse
# ---------------------------------------------------------------------------------------------

# Everything the parser relies on, so the first real response can be checked line by line. UNVERIFIED.
PARSER_ASSUMPTIONS = (
    "response is one event object, a list of them, or {'data': event} as the historical endpoint wraps it",
    "event: id, commence_time, home_team, away_team, bookmakers[]",
    "bookmaker: key (or title), last_update, markets[]",
    "market: key (one of MARKETS), last_update, outcomes[]",
    "outcome: name in Over/Under/Yes/No, description = the player, price, point = the line (none for Yes/No)",
    "price is DECIMAL or AMERICAN (the request's oddsFormat); odds_format='auto' (default) detects which",
    "player_anytime_td: name 'Yes' + description = player; a missing description means name is the player",
)

QUOTE_COLUMNS = [
    "event_id",
    "commence_time",
    "home_team",
    "away_team",
    "book",
    "last_update",
    "market",
    "stat",
    "player",
    "side",
    "line",
    "price",
]
_SIDES = {"over": "over", "under": "under", "yes": "yes", "no": "no"}


def american_to_decimal(price: float) -> float:
    """-110 -> 1.909, +150 -> 2.5."""
    if price == 0 or abs(price) < 100:
        raise ValueError(f"not an American price: {price!r}")
    return 1.0 + (price / 100.0 if price > 0 else 100.0 / -price)


def _events(payload: object) -> list[dict]:
    if isinstance(payload, list):
        return [e for item in payload for e in _events(item)]
    if isinstance(payload, dict):
        if "bookmakers" not in payload and isinstance(payload.get("data"), (dict, list)):
            return _events(payload["data"])
        return [payload]
    return []


def _number(value: object) -> float | None:
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return None if math.isnan(number) else number


ODDS_FORMATS = ("auto", "decimal", "american")
# A decimal price is a payout multiple (1.01 upward) and never negative; an American price is at least 100 in
# size, positive or negative. So one negative price, or one whose size is 100 or more, can only be American.
# (A decimal price of 100+ would be misread, but no prop is quoted at +9900.)
AMERICAN_MIN_ABS_PRICE = 100.0


def detect_odds_format(payload: object) -> str:
    """`"american"` if any price in the payload is negative or at least `AMERICAN_MIN_ABS_PRICE` in size,
    else `"decimal"` (also for a payload with no readable price). Every market in a response shares one
    format (it is a request parameter), so the whole payload is scanned, h2h and spreads included."""
    for event in _events(payload):
        for book in event.get("bookmakers") or []:
            for market in book.get("markets") or []:
                for outcome in market.get("outcomes") or []:
                    price = _number(outcome.get("price"))
                    if price is not None and (price < 0 or abs(price) >= AMERICAN_MIN_ABS_PRICE):
                        return "american"
    return "decimal"


def parse_event_odds(payload: object, *, odds_format: str = "auto") -> pd.DataFrame:
    """Flatten The Odds API event-odds response(s) into one row per quoted outcome, `QUOTE_COLUMNS`.

    `odds_format` is how the response's prices are written: `"decimal"`, `"american"`, or `"auto"` (the
    default: `detect_odds_format`). The API returns whatever `oddsFormat` the request asked for, and a real
    response came back American (-115, +110, +320): read as decimal every negative price was skipped, which
    silently removed whole markets. The format used is logged and kept in `result.attrs["odds_format"]`.

    Tolerant by design: a market we do not model, an outcome without a player, a price we cannot read, a
    bookmaker with no markets are skipped, never raised; the count is logged and kept in `result.attrs
    ["skipped"]` so a real file shows what the parser did not understand. `price` comes out DECIMAL.
    `line` is NaN for Yes/No quotes."""
    if odds_format not in ODDS_FORMATS:
        raise ValueError(f"odds_format must be one of {', '.join(ODDS_FORMATS)}")
    if odds_format == "auto":
        odds_format = detect_odds_format(payload)
    log.info("props: reading prices as %s odds", odds_format)
    rows: list[dict] = []
    skipped = 0
    for event in _events(payload):
        for book in event.get("bookmakers") or []:
            book_key = book.get("key") or book.get("title") or ""
            for market in book.get("markets") or []:
                stat = MARKETS.get(market.get("key"))
                if stat is None:
                    continue  # a market we do not use (spreads, alternates, other props)
                for outcome in market.get("outcomes") or []:
                    side = _SIDES.get(str(outcome.get("name", "")).strip().lower())
                    player = outcome.get("description")
                    if stat == TD_STAT and not player and outcome.get("name") and side is None:
                        player, side = outcome["name"], "yes"  # older shape: the scorer's name, no Yes/No
                    price = _number(outcome.get("price"))
                    line = _number(outcome.get("point"))
                    if odds_format == "american" and price is not None:
                        try:
                            price = american_to_decimal(price)
                        except ValueError:
                            price = None
                    needs_line = stat != TD_STAT
                    if (
                        side is None
                        or not isinstance(player, str)
                        or not player.strip()
                        or price is None
                        or price <= 1.0
                        or (needs_line and line is None)
                        or (needs_line and side not in ("over", "under"))
                        or (not needs_line and side not in ("yes", "no"))
                    ):
                        skipped += 1
                        continue
                    rows.append(
                        {
                            "event_id": event.get("id"),
                            "commence_time": event.get("commence_time"),
                            "home_team": event.get("home_team"),
                            "away_team": event.get("away_team"),
                            "book": book_key,
                            "last_update": market.get("last_update") or book.get("last_update"),
                            "market": market.get("key"),
                            "stat": stat,
                            "player": player.strip(),
                            "side": side,
                            "line": line if needs_line else float("nan"),
                            "price": price,
                        }
                    )
    out = pd.DataFrame(rows, columns=QUOTE_COLUMNS)
    out.attrs["skipped"] = skipped
    out.attrs["odds_format"] = odds_format
    if skipped:
        log.warning("props: skipped %d outcome(s) the parser could not read", skipped)
    return out


# ---------------------------------------------------------------------------------------------
# 2. De-vig
# ---------------------------------------------------------------------------------------------


def devig_multiplicative(price_a: float, price_b: float) -> float:
    """The probability of side A from a two-way market's DECIMAL prices, margin removed multiplicatively:
    `(1/a) / (1/a + 1/b)`. 1.91 / 1.91 -> 0.5; 1.80 / 2.00 -> 0.5263."""
    implied_a, implied_b = 1.0 / price_a, 1.0 / price_b
    return implied_a / (implied_a + implied_b)


BOOK_COLUMNS = [
    "event_id",
    "commence_time",
    "home_team",
    "away_team",
    "stat",
    "player",
    "book",
    "line",
    "p",
    "p_method",
]


def _pair_per_line(group: pd.DataFrame, first: str, second: str) -> list[tuple[float, float]]:
    """(line, de-vigged probability of `first`) for every line at which the book quotes BOTH sides."""
    out = []
    for line, part in group.groupby("line", dropna=False):
        a = part.loc[part["side"] == first, "price"]
        b = part.loc[part["side"] == second, "price"]
        if len(a) and len(b):
            out.append((line, devig_multiplicative(float(a.iloc[0]), float(b.iloc[0]))))
    return out


def book_probabilities(quotes: pd.DataFrame) -> pd.DataFrame:
    """One row per (event, player, stat, book): the de-vigged probability of the Over (or of Yes) at that
    book's line (`BOOK_COLUMNS`). A book listing several lines for one player/market keeps the pair
    closest to a coin flip (its main line). An Over with no Under cannot be de-vigged and is dropped. A
    Yes with no No gets the `ANYTD_YES_ONLY_VIG` haircut and `p_method = "yes_only"`."""
    rows = []
    keys = ["event_id", "stat", "player", "book"]
    for (event_id, stat, player, book), group in quotes.groupby(keys, dropna=False, sort=False):
        first = group.iloc[0]
        if stat == TD_STAT:
            yes = group.loc[group["side"] == "yes", "price"]
            no = group.loc[group["side"] == "no", "price"]
            if not len(yes):
                continue
            if len(no):
                p, method = devig_multiplicative(float(yes.iloc[0]), float(no.iloc[0])), "devig"
            else:
                p, method = (1.0 / float(yes.iloc[0])) / (1.0 + ANYTD_YES_ONLY_VIG), "yes_only"
            line = float("nan")
        else:
            pairs = _pair_per_line(group, "over", "under")
            if not pairs:
                continue
            line, p = min(pairs, key=lambda lp: abs(lp[1] - 0.5))
            method = "devig"
        rows.append(
            {
                "event_id": event_id,
                "commence_time": first["commence_time"],
                "home_team": first["home_team"],
                "away_team": first["away_team"],
                "stat": stat,
                "player": player,
                "book": book,
                "line": line,
                "p": p,
                "p_method": method,
            }
        )
    return pd.DataFrame(rows, columns=BOOK_COLUMNS)


# ---------------------------------------------------------------------------------------------
# 3. Consensus across books
# ---------------------------------------------------------------------------------------------


def shift_probability(p: float, line_from: float, line_to: float, sd: float) -> float:
    """Move an Over probability quoted at `line_from` onto `line_to` with a local normal model:
    `Phi((line_from - line_to) / sd + Phi^-1(p))`. A higher line lowers it; the same line returns `p`."""
    if line_from == line_to:
        return p
    z = float(ndtri(min(max(p, 1e-9), 1 - 1e-9)))
    return float(ndtr((line_from - line_to) / sd + z))


def shift_sd(stat: str, level: float) -> float:
    """The sd used only to move a book's probability between nearby lines: `sqrt(phi * level)` with the pooled
    phi for the stat."""
    return math.sqrt(DISPERSION_POOLED.get(stat, 1.0) * max(level, SHIFT_MIN_LEVEL))


CONSENSUS_COLUMNS = [
    "event_id",
    "commence_time",
    "home_team",
    "away_team",
    "player",
    "stat",
    "line",
    "p",
    "n_books",
    "n_at_line",
    "p_method",
]


def consensus_lines(book_probs: pd.DataFrame, *, min_books: int = 1) -> pd.DataFrame:
    """Across books, per (event, player, stat): the median line, then the median Over/Yes probability at that
    line (`CONSENSUS_COLUMNS`). `n_books` counts books quoting it, `n_at_line` those already on the median
    line (the others were moved onto it with `shift_probability`). Rows with fewer than `min_books` books are
    dropped. `p_method` is `devig` unless every book was a Yes-only haircut."""
    rows = []
    for (event_id, player, stat), group in book_probs.groupby(["event_id", "player", "stat"], sort=False):
        if len(group) < min_books:
            continue
        first = group.iloc[0]
        if stat == TD_STAT:
            line, p, at_line = float("nan"), float(group["p"].median()), len(group)
        else:
            line = float(group["line"].median())
            sd = shift_sd(stat, line)
            moved = [
                shift_probability(float(b.p), float(b.line), line, sd) for b in group.itertuples(index=False)
            ]
            p = float(np.median(moved))
            at_line = int((group["line"] == line).sum())
        method = "yes_only" if (group["p_method"] == "yes_only").all() else "devig"
        rows.append(
            {
                "event_id": event_id,
                "commence_time": first["commence_time"],
                "home_team": first["home_team"],
                "away_team": first["away_team"],
                "player": player,
                "stat": stat,
                "line": line,
                "p": p,
                "n_books": len(group),
                "n_at_line": at_line,
                "p_method": method,
            }
        )
    return pd.DataFrame(rows, columns=CONSENSUS_COLUMNS)


# ---------------------------------------------------------------------------------------------
# 4. Expected stat from (line, p)
# ---------------------------------------------------------------------------------------------


def _count_distribution(mean: float, phi: float):
    """Poisson when phi <= 1, else negative binomial with variance phi * mean."""
    if phi <= 1.0 + 1e-9:
        return stats.poisson(mean)
    return stats.nbinom(mean / (phi - 1.0), 1.0 / phi)


def yardage_survival(stat: str, mean: float, phi: float, threshold: float) -> float:
    """P(yards > threshold) for a yardage stat with this mean: a normal (`NORMAL_STATS`) or a gamma with
    variance `phi * mean`."""
    if stat in NORMAL_STATS:
        return float(stats.norm.sf(threshold, loc=mean, scale=math.sqrt(phi * mean)))
    return float(stats.gamma.sf(threshold, a=mean / phi, scale=phi))


def over_probability(stat: str, mean: float, phi: float, line: float) -> float:
    """P(Over `line`) for a stat with this mean: `P(X > line)` for yardage (continuous, a push has no mass);
    for counts `P(X > line) / (1 - P(X = line))`, the two-way probability a book quotes (a half line has no
    push, so this is just `P(X > line)`)."""
    if stat in YARDAGE_STATS:
        return yardage_survival(stat, mean, phi, line)
    dist = _count_distribution(mean, phi)
    over = float(dist.sf(math.floor(line)))
    if float(line).is_integer():
        push = float(dist.pmf(line))
        return over / (1.0 - push) if push < 1.0 else over
    return over


def mean_from_line(stat: str, line: float, p_over: float, phi: float) -> float:
    """The mean of a stat whose median-ish `line` was priced at Over probability `p_over`: the `mean` that
    makes `over_probability(stat, mean, phi, line) == p_over`. The line itself is NOT the answer: for yardage
    (right-skewed) the mean sits above the median, and `p_over` says how far the line is from the median."""
    p = min(max(p_over, P_CLIP[0]), P_CLIP[1])

    def gap(mean: float) -> float:
        return over_probability(stat, mean, phi, line) - p

    low, high = 1e-6, max(line, 1.0) * 2.0 + 10.0 * phi
    while gap(high) < 0:
        high *= 2.0
        if high > 1e7:  # pragma: no cover - cannot happen for p <= P_CLIP[1]
            raise ValueError("mean_from_line did not bracket a root")
    if gap(low) > 0:
        return low
    return float(optimize.brentq(gap, low, high, xtol=1e-9))


def anytime_td_expectation(p: float) -> float:
    """E[TDs] from the probability of at least one, assuming Poisson: `-ln(1 - p)`. p = 0.5 -> 0.693."""
    return -math.log(1.0 - min(max(p, P_CLIP[0]), P_CLIP[1]))


def expected_stat(stat: str, position: str, line: float, p: float, *, phi: float | None = None) -> float:
    """The expected value of `stat` for a player at `position` from a consensus (line, p). `phi` overrides
    the fitted dispersion (tests)."""
    if stat == TD_STAT:
        return anytime_td_expectation(p)
    return mean_from_line(stat, line, p, dispersion(position, stat) if phi is None else phi)


def bonus_probability(stat: str, mean: float, phi: float, threshold: float) -> float:
    """P(yards >= threshold) for a yardage stat with this mean, under the same distribution as
    `over_probability`: `P(yards > threshold - 0.5)` (yards are whole numbers)."""
    if mean <= 0:
        return 0.0
    return yardage_survival(stat, mean, phi, threshold - YARDAGE_CONTINUITY)


# ---------------------------------------------------------------------------------------------
# 5. Score
# ---------------------------------------------------------------------------------------------

PROP_COLUMNS = [
    "event_id",
    "Name",
    "Team",
    "Position",
    "PropPts",
    "pass_yd",
    "pass_td",
    "pass_int",
    "rush_yd",
    "rec",
    "rec_yd",
    "td",
    "bonus",
    "n_books_min",
    "Missing",
    "Flags",
]


def score_stat_line(
    position: str, mean: Mapping[str, float], *, phi: Mapping[str, float] | None = None
) -> dict:
    """DK points from expected stats: linear terms from `dk_scoring.DK_OFFENSE_SCORING`, one TD bucket, and
    `YARDAGE_BONUS_POINTS * P(yards >= threshold)` for each yardage stat present. Returns the per-stat points
    and `PropPts` (rounded to 2 places, the same as `dk_scoring`)."""
    phi = phi or {}
    pts = {
        "pass_yd": mean.get("pass_yd", 0.0) * DK_OFFENSE_SCORING["pass_yd"],
        "pass_td": mean.get("pass_td", 0.0) * DK_OFFENSE_SCORING["pass_td"],
        "pass_int": mean.get("pass_int", 0.0) * DK_OFFENSE_SCORING["pass_int"],
        "rush_yd": mean.get("rush_yd", 0.0) * DK_OFFENSE_SCORING["rush_yd"],
        "rec": mean.get("rec", 0.0) * DK_OFFENSE_SCORING["rec"],
        "rec_yd": mean.get("rec_yd", 0.0) * DK_OFFENSE_SCORING["rec_yd"],
        "td": mean.get("td", 0.0) * TD_POINTS,
    }
    bonus = 0.0
    for stat, threshold in YARDAGE_BONUS_THRESHOLDS.items():
        if stat in mean:
            bonus += YARDAGE_BONUS_POINTS * bonus_probability(
                stat, mean[stat], phi.get(stat, dispersion(position, stat)), threshold
            )
    pts["bonus"] = bonus
    pts = {k: v + 0.0 for k, v in pts.items()}  # -0.0 (a zero stat times a negative weight) reads as 0.0
    pts["PropPts"] = round(sum(pts.values()), 2)
    return pts


def league_int_rate_fallback(name_key: str, qb_int_rates: Mapping[str, float] | None) -> tuple[float, str]:
    """A QB's expected INTs when there is no INT market: his shrunk season rate if the caller has one, else
    the league rate. Returns (rate, source) with source `rate` or `league`."""
    if qb_int_rates and name_key in qb_int_rates:
        return float(qb_int_rates[name_key]), "rate"
    return QB_INT_LEAGUE_RATE, "league"


def attach_players(consensus: pd.DataFrame, players: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Give each consensus row a DraftKings identity. `players` needs `Name`, `Team`, `Position`. A prop
    player is matched on normalised name AND a team playing in his event (the Odds API names teams in
    full and the DK file uses codes, so `FULL_TEAM_NAME_TO_CODE` bridges them); exactly one match is
    required, so a name shared by two players on the two teams, or an unknown player, is returned in the
    second frame instead of being guessed. Returns (matched rows with `Name`, `Team`, `Position`,
    `name_key`; unmatched rows)."""
    from dfs.sources.fantasypros_projections import FULL_TEAM_NAME_TO_CODE

    pool = players.copy()
    pool["_key"] = pool["Name"].map(normalize_name)
    pool["_team"] = pool["Team"].map(normalize_team)
    pool["_pos"] = pool["Position"].map(normalize_position)
    matched, unmatched = [], []
    for row in consensus.to_dict("records"):
        teams = {
            normalize_team(FULL_TEAM_NAME_TO_CODE.get(row.get("home_team"), "")),
            normalize_team(FULL_TEAM_NAME_TO_CODE.get(row.get("away_team"), "")),
        } - {""}
        key = normalize_name(row["player"])
        hits = pool[(pool["_key"] == key) & pool["_team"].isin(teams)]
        if len(hits) == 1:
            hit = hits.iloc[0]
            matched.append(
                {**row, "Name": hit["Name"], "Team": hit["Team"], "Position": hit["_pos"], "name_key": key}
            )
        else:
            unmatched.append({**row, "reason": "ambiguous" if len(hits) > 1 else "no match"})
    return pd.DataFrame(matched), pd.DataFrame(unmatched)


def prop_pts(
    consensus: pd.DataFrame,
    players: pd.DataFrame,
    *,
    qb_int_rates: Mapping[str, float] | None = None,
    min_books: int = 1,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """`PropPts` per matched player (`PROP_COLUMNS`) and the prop players that could not be matched to
    `players`. `players` is the DK pool: `Name`, `Team`, `Position`. A player missing a market his position
    needs (`REQUIRED_MARKETS`), or whose required market has fewer than `min_books` books, gets `PropPts` NaN
    and `Missing` names what he lacks: never a partial score. DST and positions without a rule are not
    scored. `qb_int_rates` maps `normalize_name(QB)` -> expected INTs per game, for QBs with no INT market."""
    matched, unmatched = attach_players(consensus, players)
    rows = []
    if not matched.empty:
        for (event_id, name, team, position), group in matched.groupby(
            ["event_id", "Name", "Team", "Position"], sort=False
        ):
            if position not in REQUIRED_MARKETS:
                continue
            by_stat = {r.stat: r for r in group.itertuples(index=False)}
            usable = {s: r for s, r in by_stat.items() if r.n_books >= min_books}
            lacking = [s for s in REQUIRED_MARKETS[position] if s not in usable]
            flags = []
            if any(r.p_method == "yes_only" for r in by_stat.values() if r.stat == TD_STAT):
                flags.append("td:yes_only")
            row = {
                "event_id": event_id,
                "Name": name,
                "Team": team,
                "Position": position,
                "PropPts": float("nan"),
                "n_books_min": min((r.n_books for r in usable.values()), default=0),
                "Missing": " ".join(lacking),
            }
            if not lacking:
                mean = {s: expected_stat(s, position, r.line, r.p) for s, r in usable.items()}
                if position == "QB" and "pass_int" not in mean:
                    rate, source = league_int_rate_fallback(group["name_key"].iloc[0], qb_int_rates)
                    mean["pass_int"] = rate
                    flags.append(f"int:{source}")
                absent = [s for s in OPTIONAL_MARKETS.get(position, ()) if s not in mean]
                flags += [f"no:{s}" for s in absent if s != "pass_int"]
                scored = score_stat_line(position, mean)
                row.update({k: round(v, 3) for k, v in scored.items() if k != "PropPts"})
                row["PropPts"] = scored["PropPts"]
            row["Flags"] = " ".join(flags)
            rows.append(row)
    return pd.DataFrame(rows, columns=PROP_COLUMNS), unmatched


def props_from_responses(
    payload: object,
    players: pd.DataFrame,
    *,
    qb_int_rates: Mapping[str, float] | None = None,
    odds_format: str = "auto",
    min_books: int = 1,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """The whole chain for one or more event responses: parse, de-vig, consensus, score. Returns
    (`prop_pts` frame, unmatched prop players)."""
    quotes = parse_event_odds(payload, odds_format=odds_format)
    consensus = consensus_lines(book_probabilities(quotes))
    return prop_pts(consensus, players, qb_int_rates=qb_int_rates, min_books=min_books)


def season_int_rates(
    stats_player: pd.DataFrame, season: int, through_week: int
) -> tuple[dict[str, float], float]:
    """Expected INTs per game for each QB from nflverse `stats_player` (regular season, weeks through
    `through_week`), shrunk toward the league rate: `(INTs + K * league) / (games + K)`, `K =
    INT_RATE_SHRINK_GAMES`. Keyed by `normalize_name`. Returns (rates, league rate that season-to-date)."""
    df = stats_player
    if "season_type" in df.columns:
        df = df[df["season_type"] == "REG"]
    df = df[(df["season"] == season) & (pd.to_numeric(df["week"], errors="coerce") <= through_week)]
    df = df[df["position"] == "QB"]
    df = df[pd.to_numeric(df["attempts"], errors="coerce") >= 15]
    if df.empty:
        return {}, QB_INT_LEAGUE_RATE
    league = float(df["passing_interceptions"].sum() / len(df))
    grouped = df.groupby("player_display_name")["passing_interceptions"].agg(["sum", "count"])
    rates = {
        normalize_name(name): float(
            (row["sum"] + INT_RATE_SHRINK_GAMES * league) / (row["count"] + INT_RATE_SHRINK_GAMES)
        )
        for name, row in grouped.iterrows()
    }
    return rates, league
