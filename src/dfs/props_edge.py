"""PROPS SHADOW: does ESPN's stat-level projection beat the player-prop market? (analysis only)

Nothing here touches a sportsbook, tracks a bet or reaches the sheet's player tabs. It reads what a normal
week already saved -- `data/raw/odds_props/*.json` (The Odds API pulls), `data/raw/espn/*.csv` (ESPN's
projections, stat by stat) and nflverse's `stats_player` (what happened) -- and answers one question per
market: when ESPN's number sits away from the book's line, did the side ESPN favoured win more often than
the price needs? Model Check prints the answer (`PROPS SHADOW`). No extra Odds API credits are used.

**Per player-market (`edge_rows`).**

- The book line is the line most books quote (ties go to the one nearer the median); `n_books` is how many
  quote it. The market probability is the median over those books of the de-vigged Over (or Yes) probability
  (`props.devig_multiplicative`; a Yes-only anytime-TD quote takes `props.ANYTD_YES_ONLY_VIG`). `over_price`
  / `under_price` (decimal) are the median quoted prices at that line.
- The pull is the last one taken before the event's kickoff that contains the event; ESPN's projection is the
  last ESPN snapshot taken at or before that pull (what a bettor could have known at the same moment).
- ESPN's distribution: its projected stat as the MEAN (not re-centred on anything) and a dispersion
  `phi` (variance / mean) fitted from how far actuals landed from ESPN's projections (`fit_dispersion`),
  pooled over the weeks we have and shrunk toward `props.dispersion` with `SHRINK_PLAYERS` pseudo-players.
  A week is judged on a `phi` fitted from the OTHER weeks (the prior alone when there are none), so a
  result is never scored with the spread it was used to fit. `props.over_probability` turns (mean, phi, line)
  into ESPN's P(Over). Anytime TD: P(at least one) = `1 - exp(-(rush_td + rec_td))`.
- `gap` = ESPN's projection minus the line; `edge` = ESPN's P(Over) minus the market's P(Over) (for TDs, Yes).

**The test (`band_table`).** The side ESPN favours (Over when its P(Over) is higher) is "bet" at the median
quoted price for that side; it wins or loses one unit-stake outcome against actuals (a push is dropped, a
player who did not play voids the prop and is dropped). Per market and per band of `|edge|` (5+, 10+, 15+
points; cumulative, so a bigger band is a subset of the smaller): n, how often that side won, the win rate
the prices needed (mean of 1 / decimal price), and the return per unit staked. An anytime-TD "No" is
bettable only where some book quoted one; otherwise only the Yes side is. The verdict word appears at
`MIN_N_VERDICT` rows and not before.

Assumptions: actual anytime TDs are rushing + receiving TDs from nflverse (return and fumble-recovery TDs
are ignored); a prop line quoted at an integer pushes; players join props <-> ESPN <-> nflverse on
normalised name inside the event's two teams.
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import pandas as pd

from dfs import nfl_calendar, props, props_history, results_loop
from dfs.log import get_logger
from dfs.paths import RAW_DIR
from dfs.player_join import normalize_name, normalize_position, normalize_team
from dfs.sources.fantasypros_projections import FULL_TEAM_NAME_TO_CODE

log = get_logger("props_edge")

PULL_DIR_NAME = "odds_props"
_STAMP = "%Y%m%dT%H%M%SZ"
SHRINK_PLAYERS = 50  # pseudo-players of prior carried by the dispersion fit
MIN_N_VERDICT = 100  # no verdict word under this many rows in a cell
EDGE_BANDS = (0.05, 0.10, 0.15)  # |ESPN P - market P| thresholds, cumulative
SEASON_TYPE = "REG"
# ESPN stat column -> nflverse column (TD is handled apart)
ACTUAL_COLUMN = {
    "pass_yd": "passing_yards",
    "pass_td": "passing_tds",
    "rush_yd": "rushing_yards",
    "rec": "receptions",
    "rec_yd": "receiving_yards",
}
# the markets in display order, with a plain label
MARKET_LABEL = {
    "pass_yd": "Pass yards",
    "pass_td": "Pass TDs",
    "rush_yd": "Rush yards",
    "rec": "Receptions",
    "rec_yd": "Rec yards",
    "td": "Anytime TD",
}
EDGE_COLUMNS = [
    "Week",
    "Player",
    "Team",
    "Position",
    "Stat",
    "Line",
    "NBooks",
    "MarketP",
    "EspnMean",
    "Gap",
    "EspnP",
    "Edge",
    "Side",
    "Price",
    "Actual",
    "Won",
    "Profit",
    "Pull",
]


# ---------------------------------------------------------------------------------------------
# 1. Saved pulls -> one market row per (event, player, stat)
# ---------------------------------------------------------------------------------------------


def load_pulls(directory=None) -> list[tuple[datetime, dict]]:
    """Every saved Odds API pull `[(stamp, payload)]`, oldest first. An unreadable file is skipped."""
    directory = directory or RAW_DIR / PULL_DIR_NAME
    out = []
    for path in sorted(directory.glob("*.json")):
        if not re.fullmatch(r"\d{8}T\d{6}Z", path.stem):
            continue
        try:
            payload = json.loads(path.read_text())
        except (OSError, ValueError) as e:
            log.warning("props pull %s unreadable: %s", path.name, e)
            continue
        out.append((datetime.strptime(path.stem, _STAMP).replace(tzinfo=UTC), payload))
    return out


def _commence(event: dict) -> datetime | None:
    try:
        return datetime.fromisoformat(str(event["commence_time"]).replace("Z", "+00:00"))
    except (KeyError, ValueError):
        return None


def events_before_kickoff(pulls: list[tuple[datetime, dict]]) -> list[tuple[datetime, dict]]:
    """One `(pull stamp, event)` per event id: the latest pull taken BEFORE that event's kickoff in which the
    event carries bookmakers. An event no pull saw before it started is absent."""
    best: dict[str, tuple[datetime, dict]] = {}
    for stamp, payload in pulls:
        for event in payload.get("events") or []:
            kickoff = _commence(event)
            if kickoff is None or stamp >= kickoff or not event.get("bookmakers"):
                continue
            current = best.get(event["id"])
            if current is None or stamp > current[0]:
                best[event["id"]] = (stamp, event)
    return sorted(best.values(), key=lambda pair: (pair[0], pair[1]["id"]))


def _line_mode(lines: pd.Series) -> float:
    """The most-quoted line; a tie goes to the one nearer the median, then the lower."""
    counts = lines.value_counts()
    top = counts[counts == counts.max()].index
    median = float(lines.median())
    return float(sorted(top, key=lambda x: (abs(x - median), x))[0])


def market_rows(events: list[tuple[datetime, dict]]) -> pd.DataFrame:
    """One row per (event, player, stat): the book line, `NBooks`, de-vigged `MarketP` (Over, or Yes for a
    TD), and the median decimal `OverPrice` / `UnderPrice` (`YesPrice` / `NoPrice` for a TD, NaN when no book
    quoted that side). `Pull` is the pull's stamp."""
    rows = []
    for stamp, event in events:
        quotes = props.parse_event_odds(event, odds_format="american")
        if quotes.empty:
            continue
        for (player, stat), group in quotes.groupby(["player", "stat"], sort=False):
            first = group.iloc[0]
            base = {
                "EventId": first["event_id"],
                "Commence": first["commence_time"],
                "Home": first["home_team"],
                "Away": first["away_team"],
                "Player": player,
                "Stat": stat,
                "Pull": stamp.strftime(_STAMP),
            }
            if stat == props.TD_STAT:
                per_book = props.book_probabilities(group)
                yes = group.loc[group["side"] == "yes", "price"]
                no = group.loc[group["side"] == "no", "price"]
                if per_book.empty or yes.empty:
                    continue
                rows.append(
                    base
                    | {
                        "Line": float("nan"),
                        "NBooks": len(per_book),
                        "MarketP": float(per_book["p"].median()),
                        "OverPrice": float(yes.median()),
                        "UnderPrice": float(no.median()) if len(no) else float("nan"),
                    }
                )
                continue
            book_lines = []  # (book, line, p over, over price, under price)
            for book, part in group.groupby("book", sort=False):
                for line, at in part.groupby("line"):
                    over = at.loc[at["side"] == "over", "price"]
                    under = at.loc[at["side"] == "under", "price"]
                    if len(over) and len(under):
                        book_lines.append(
                            (
                                book,
                                float(line),
                                props.devig_multiplicative(float(over.iloc[0]), float(under.iloc[0])),
                                float(over.iloc[0]),
                                float(under.iloc[0]),
                            )
                        )
            if not book_lines:
                continue
            table = pd.DataFrame(book_lines, columns=["book", "line", "p", "over", "under"])
            # a book listing several lines keeps its pair closest to a coin flip (as book_probabilities)
            table = table.assign(off=(table["p"] - 0.5).abs()).sort_values("off").drop_duplicates("book")
            line = _line_mode(table["line"])
            at = table[table["line"] == line]
            rows.append(
                base
                | {
                    "Line": line,
                    "NBooks": len(at),
                    "MarketP": float(at["p"].median()),
                    "OverPrice": float(at["over"].median()),
                    "UnderPrice": float(at["under"].median()),
                }
            )
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------------------------
# 2. ESPN: projection at the pull, and the spread of actuals around it
# ---------------------------------------------------------------------------------------------


def espn_at(stamp: datetime) -> pd.DataFrame | None:
    """The latest ESPN snapshot at or before `stamp` with keys added, or None."""
    frame = results_loop.as_of("espn", stamp)
    return None if frame is None else with_keys(frame)


def with_keys(frame: pd.DataFrame) -> pd.DataFrame:
    frame = frame.copy()
    frame["KName"] = frame["Name"].map(normalize_name)
    frame["KTeam"] = frame["Team"].map(normalize_team)
    frame["KPos"] = frame["Position"].map(normalize_position)
    return frame


def week_reference(week: int, season: int) -> datetime:
    """The week's main-slate kickoff (Sunday 1 pm Eastern) as a UTC instant: the moment a week without a props
    pull is read at."""
    sunday = nfl_calendar.week_start_date(week, season) + timedelta(days=5)
    local = pd.Timestamp(datetime(sunday.year, sunday.month, sunday.day, 13, 0)).tz_localize(
        "America/New_York"
    )
    return local.tz_convert("UTC").to_pydatetime()


def _stat_value(row: pd.Series, stat: str) -> float:
    if stat == props.TD_STAT:
        return float(row.get("rush_td", 0) or 0) + float(row.get("rec_td", 0) or 0)
    return float(row.get(stat, 0) or 0)


def actual_value(played: pd.Series, stat: str) -> float:
    """What a nflverse `stats_player` row says the player did in `stat`."""
    if stat == props.TD_STAT:
        return float(played.get("rushing_tds", 0) or 0) + float(played.get("receiving_tds", 0) or 0)
    return float(played.get(ACTUAL_COLUMN[stat], 0) or 0)


def actuals_for_week(stats_player: pd.DataFrame, season: int, week: int) -> pd.DataFrame:
    """The week's regular-season player rows with join keys; one row per (name, team)."""
    frame = stats_player
    if "season" in frame.columns:
        frame = frame[frame["season"] == season]
    if "season_type" in frame.columns:
        frame = frame[frame["season_type"] == SEASON_TYPE]
    frame = frame[frame["week"] == week].copy()
    frame["KName"] = frame["player_display_name"].map(normalize_name)
    frame["KTeam"] = frame["team"].map(normalize_team)
    return frame.drop_duplicates(["KName", "KTeam"])


def fit_rows(stats_player: pd.DataFrame, season: int, weeks: list[int]) -> pd.DataFrame:
    """Every (week, player, stat) with an ESPN projection at the main-slate kickoff AND a played game:
    `Week, Position, Stat, Proj, Actual`. A projection under the history table's smallest level for the
    cell is dropped (no one would price it), so is a player with no stats row (he did not play)."""
    out = []
    for week in weeks:
        espn = espn_at(week_reference(week, season))
        if espn is None:
            continue
        actual = actuals_for_week(stats_player, season, week).set_index(["KName", "KTeam"])
        for row in espn.itertuples(index=False):
            key = (row.KName, row.KTeam)
            if key not in actual.index or row.KPos not in ("QB", "RB", "WR", "TE"):
                continue
            played = actual.loc[key]
            for stat in ACTUAL_COLUMN:
                floor = props_history.CASES.get((row.KPos, stat))
                if floor is None:
                    continue
                proj = _stat_value(pd.Series(row._asdict()), stat)
                if not math.isfinite(proj) or proj < floor[1]:
                    continue
                out.append((week, row.KPos, stat, proj, actual_value(played, stat)))
    return pd.DataFrame(out, columns=["Week", "Position", "Stat", "Proj", "Actual"])


def fit_dispersion(rows: pd.DataFrame) -> pd.DataFrame:
    """Per (position, stat): n players, mean miss (actual - ESPN), the raw `phi = sum((a - p)^2) / sum(p)`,
    the prior (`props.dispersion`) and the shrunk
    `Phi = (n * raw + SHRINK_PLAYERS * prior) / (n + SHRINK_PLAYERS)`."""
    cols = ["Position", "Stat", "n", "Bias", "RawPhi", "Prior", "Phi"]
    if rows.empty:
        return pd.DataFrame(columns=cols)
    out = []
    for (position, stat), part in rows.groupby(["Position", "Stat"], sort=True):
        n = len(part)
        raw = float(((part["Actual"] - part["Proj"]) ** 2).sum() / part["Proj"].sum())
        prior = props.dispersion(position, stat)
        out.append(
            {
                "Position": position,
                "Stat": stat,
                "n": n,
                "Bias": float((part["Actual"] - part["Proj"]).mean()),
                "RawPhi": raw,
                "Prior": prior,
                "Phi": (n * raw + SHRINK_PLAYERS * prior) / (n + SHRINK_PLAYERS),
            }
        )
    return pd.DataFrame(out, columns=cols)


def phi_lookup(fit: pd.DataFrame, position: str, stat: str) -> float:
    hit = fit[(fit["Position"] == position) & (fit["Stat"] == stat)] if not fit.empty else fit
    return float(hit["Phi"].iloc[0]) if len(hit) else props.dispersion(position, stat)


# ---------------------------------------------------------------------------------------------
# 3. The rows and the test
# ---------------------------------------------------------------------------------------------


def _event_codes(home: str, away: str) -> set[str]:
    return {c for c in (FULL_TEAM_NAME_TO_CODE.get(home), FULL_TEAM_NAME_TO_CODE.get(away)) if c}


@dataclass
class Shadow:
    """Everything Model Check's PROPS SHADOW needs. `rows` is one row per player-market (`EDGE_COLUMNS`),
    `table` the band table, `fit` the ESPN dispersion fit on every week, `counts` what was dropped and why."""

    rows: pd.DataFrame
    table: pd.DataFrame
    fit: pd.DataFrame
    counts: dict[str, int] = field(default_factory=dict)
    weeks: list[int] = field(default_factory=list)
    fit_weeks: list[int] = field(default_factory=list)


def edge_rows(
    market: pd.DataFrame,
    stats_player: pd.DataFrame | None,
    *,
    season: int,
    fits: dict[int, pd.DataFrame],
    default_fit: pd.DataFrame,
) -> tuple[pd.DataFrame, dict[str, int]]:
    """Join the market rows to ESPN's projection (as of each pull) and, when `stats_player` has the week, to
    the actual. `fits[week]` is the dispersion fit WITHOUT that week."""
    counts = {"market": len(market), "no_espn": 0, "no_position": 0, "void": 0, "push": 0}
    if market.empty:
        return pd.DataFrame(columns=EDGE_COLUMNS), counts
    espn_cache: dict[str, pd.DataFrame | None] = {}
    actual_cache: dict[int, pd.DataFrame] = {}
    out = []
    for m in market.itertuples(index=False):
        stamp = datetime.strptime(m.Pull, _STAMP).replace(tzinfo=UTC)
        if m.Pull not in espn_cache:
            espn_cache[m.Pull] = espn_at(stamp)
        espn = espn_cache[m.Pull]
        if espn is None:
            counts["no_espn"] += 1
            continue
        codes = _event_codes(m.Home, m.Away)
        name = normalize_name(m.Player)
        hit = espn[(espn["KName"] == name) & (espn["KTeam"].isin(codes))]
        if hit.empty:
            counts["no_espn"] += 1
            continue
        e = hit.iloc[0]
        position = e["KPos"]
        if position not in ("QB", "RB", "WR", "TE"):
            counts["no_position"] += 1
            continue
        week = nfl_calendar.week_for_date(
            pd.Timestamp(m.Commence).tz_convert("America/New_York").date(), season
        )
        mean = _stat_value(e, m.Stat)
        if not math.isfinite(mean) or mean <= 0:
            counts["no_espn"] += 1
            continue
        if m.Stat == props.TD_STAT:
            espn_p = 1.0 - math.exp(-mean)
            gap = float("nan")
        else:
            phi = phi_lookup(fits.get(week, default_fit), position, m.Stat)
            espn_p = props.over_probability(m.Stat, mean, phi, m.Line)
            gap = mean - m.Line
        edge = espn_p - m.MarketP
        side = "over" if edge > 0 else "under"
        price = m.OverPrice if side == "over" else m.UnderPrice
        record = dict.fromkeys(EDGE_COLUMNS)
        record.update(
            Week=week,
            Player=m.Player,
            Team=e["KTeam"],
            Position=position,
            Stat=m.Stat,
            Line=m.Line,
            NBooks=m.NBooks,
            MarketP=m.MarketP,
            EspnMean=mean,
            Gap=gap,
            EspnP=espn_p,
            Edge=edge,
            Side=side,
            Price=price,
            Pull=m.Pull,
        )
        record["Actual"] = float("nan")
        if stats_player is not None:
            if week not in actual_cache:
                actual_cache[week] = actuals_for_week(stats_player, season, week).set_index(
                    ["KName", "KTeam"]
                )
            table = actual_cache[week]
            if len(table):
                if (name, e["KTeam"]) not in table.index:
                    counts["void"] += 1  # he did not play: the book voids the prop
                    continue
                actual = actual_value(table.loc[(name, e["KTeam"])], m.Stat)
                record["Actual"] = actual
                if m.Stat == props.TD_STAT:
                    won = actual >= 1
                else:
                    if actual == m.Line:
                        counts["push"] += 1
                        continue
                    won = (actual > m.Line) == (side == "over")
                if m.Stat == props.TD_STAT and side == "under":
                    won = actual < 1
                record["Won"] = bool(won)
                if price is not None and math.isfinite(price):
                    record["Profit"] = (price - 1.0) if won else -1.0
        out.append(record)
    frame = pd.DataFrame(out, columns=EDGE_COLUMNS)
    if not frame.empty:
        frame["Week"] = frame["Week"].astype(int)
    return frame, counts


def band_table(rows: pd.DataFrame) -> pd.DataFrame:
    """Per market (and `All`) and per band of |edge|: `n`, `Won` (how often the ESPN-favoured side won),
    `Needed` (mean 1 / decimal price: what the prices asked for), `Return` (per unit staked), `Verdict`."""
    cols = ["Market", "Band", "n", "Won", "Needed", "Return", "Verdict", "Thin"]
    if rows.empty:
        return pd.DataFrame(columns=cols)
    scored = rows[rows["Won"].notna() & rows["Price"].notna()].copy()
    scored["Won"] = scored["Won"].astype(float)
    scored["Needed"] = 1.0 / scored["Price"]
    scored["AbsEdge"] = scored["Edge"].abs()
    out = []
    groups = [(label, scored[scored["Stat"] == stat]) for stat, label in MARKET_LABEL.items()]
    groups.append(("All markets", scored))
    for label, part in groups:
        for band in (0.0, *EDGE_BANDS):
            cell = part[part["AbsEdge"] >= band]
            if cell.empty and band:
                continue
            n = len(cell)
            if not n and not part.shape[0]:
                continue
            won = float(cell["Won"].mean()) if n else float("nan")
            needed = float(cell["Needed"].mean()) if n else float("nan")
            ret = float(cell["Profit"].mean()) if n else float("nan")
            out.append(
                {
                    "Market": label,
                    "Band": "all gaps" if band == 0 else f"{band * 100:.0f}+ pts",
                    "n": n,
                    "Won": won,
                    "Needed": needed,
                    "Return": ret,
                    "Verdict": verdict_word(n, won, needed),
                    "Thin": n < MIN_N_VERDICT,
                }
            )
    return pd.DataFrame(out, columns=cols)


def verdict_word(n: int, won: float, needed: float) -> str:
    """Words only at `MIN_N_VERDICT` rows: ESPN's side beat or missed its price, by more than a coin-flip's
    standard error (`sqrt(.25 / n)`), else `no edge shown`."""
    if n < MIN_N_VERDICT:
        return f"n < {MIN_N_VERDICT}"
    se = math.sqrt(0.25 / n)
    if won - needed > 2 * se:
        return "beat the price"
    if needed - won > 2 * se:
        return "missed the price"
    return "no edge shown"


def build_shadow(
    stats_player: pd.DataFrame | None,
    *,
    season: int,
    pulls: list[tuple[datetime, dict]] | None = None,
) -> Shadow:
    """The whole shadow from the saved files. `stats_player` None (not published yet) still returns the market
    side with no results."""
    pulls = load_pulls() if pulls is None else pulls
    events = events_before_kickoff(pulls)
    market = market_rows(events)
    fit_weeks: list[int] = []
    fit_frame = pd.DataFrame(columns=["Week", "Position", "Stat", "Proj", "Actual"])
    if stats_player is not None and not stats_player.empty:
        done = sorted(
            int(w)
            for w in stats_player.loc[stats_player["season_type"] == SEASON_TYPE, "week"].dropna().unique()
        )
        fit_frame = fit_rows(stats_player, season, done)
        fit_weeks = sorted(int(w) for w in fit_frame["Week"].unique())
    fits = {w: fit_dispersion(fit_frame[fit_frame["Week"] != w]) for w in fit_weeks}
    default_fit = fit_dispersion(fit_frame)
    rows, counts = edge_rows(market, stats_player, season=season, fits=fits, default_fit=default_fit)
    counts["fit_players"] = len(fit_frame)
    return Shadow(
        rows=rows,
        table=band_table(rows),
        fit=default_fit,
        counts=counts,
        weeks=sorted(int(w) for w in rows["Week"].unique()) if not rows.empty else [],
        fit_weeks=fit_weeks,
    )


def latest_stats_player() -> pd.DataFrame | None:
    """The newest `stats_player` snapshot on disk (no network), or None."""
    files = sorted((RAW_DIR / "stats_player").glob("*.parquet"))
    return pd.read_parquet(files[-1]) if files else None


def headline(shadow: Shadow) -> str:
    """The section's one sentence: the biggest cell and how much to trust it. Never a verdict under
    `MIN_N_VERDICT` rows."""
    table = shadow.table
    allm = table[(table["Market"] == "All markets") & (table["Band"] == "all gaps")]
    if allm.empty:
        return "No scored player-markets yet."
    n = int(allm["n"].iloc[0])
    big = table[(table["Market"] == "All markets") & (table["Band"] == f"{EDGE_BANDS[1] * 100:.0f}+ pts")]
    tail = ""
    if len(big):
        r = big.iloc[0]
        points = f"{EDGE_BANDS[1] * 100:.0f}"
        tail = (
            f"; where ESPN sat {points}+ points off the book, its side won {r['Won']:.0%} (n={int(r['n'])})"
        )
    if n < MIN_N_VERDICT:
        return f"Too early for a verdict: {n} scored player-markets{tail}."
    return f"{n} scored player-markets{tail}."


def summary_lines(shadow: Shadow) -> list[str]:
    """A few plain lines for the terminal and the tests."""
    lines = [
        f"{len(shadow.rows)} player-markets from {len(shadow.weeks)} week(s) {shadow.weeks}; "
        f"dispersion fitted on {shadow.counts.get('fit_players', 0)} projections, weeks {shadow.fit_weeks}."
    ]
    allm = shadow.table[(shadow.table["Market"] == "All markets")] if not shadow.table.empty else shadow.table
    for r in allm.itertuples(index=False):
        lines.append(
            f"  {r.Band:9s} n={r.n:4d} won {r.Won:.1%} needed {r.Needed:.1%} "
            f"return {r.Return:+.1%}  {r.Verdict}"
        )
    return lines


def main(argv: list[str] | None = None) -> None:  # pragma: no cover - reads local files
    """`python -m dfs.props_edge [--rows]`: print the shadow from the saved files (no network, no sheet)."""
    import argparse

    parser = argparse.ArgumentParser(prog="python -m dfs.props_edge", description=__doc__.splitlines()[0])
    parser.add_argument("--season", type=int, default=nfl_calendar.current_season())
    parser.add_argument("--rows", action="store_true", help="also print every player-market row")
    args = parser.parse_args(argv)
    stats = latest_stats_player()
    shadow = build_shadow(stats, season=args.season)
    print("\n".join(summary_lines(shadow)))
    with pd.option_context("display.width", 200, "display.max_columns", 30, "display.max_rows", 200):
        print(shadow.table.drop(columns=["Thin"]).to_string(index=False))
        print(shadow.fit.to_string(index=False))
        print(shadow.counts)
        if args.rows:
            print(shadow.rows.to_string(index=False))


if __name__ == "__main__":  # pragma: no cover
    main()
