"""`props.py`: de-vig, consensus, mean-from-line, anytime TD, bonuses, the no-partial rule and the parser.

**The parser tests run on a SYNTHETIC fixture (`tests/fixtures/odds_api/synthetic_event_odds.json`)
written from memory of The Odds API's response shape, not from their documentation: the shape and the
market keys are UNVERIFIED** (see `props.PARSER_ASSUMPTIONS`). The local session replaces the authority
with a recorded response, `tests/fixtures/odds_api/event_odds_sample.json`; `test_recorded_sample_parses`
runs on it as soon as it exists.
"""

import json
import math
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from scipy import optimize, stats

from dfs import props
from dfs.dk_scoring import DK_OFFENSE_SCORING, YARDAGE_BONUS_POINTS

FIXTURES = Path(__file__).parent / "fixtures" / "odds_api"
SYNTHETIC = FIXTURES / "synthetic_event_odds.json"
RECORDED = FIXTURES / "event_odds_sample.json"


@pytest.fixture(scope="module")
def event():
    return json.loads(SYNTHETIC.read_text())


@pytest.fixture(scope="module")
def pool():
    return pd.DataFrame(
        {
            "Name": ["Patrick Mahomes", "Josh Allen", "Isiah Pacheco", "Rashee Rice", "Travis Kelce"],
            "Team": ["KC", "BUF", "KC", "KC", "KC"],
            "Position": ["QB", "QB", "RB", "WR", "TE"],
        }
    )


# ---------------------------------------------------------------------------------------------
# prices and de-vig, hand-checked
# ---------------------------------------------------------------------------------------------


def test_american_to_decimal():
    assert props.american_to_decimal(-110) == pytest.approx(1.9090909)
    assert props.american_to_decimal(150) == pytest.approx(2.5)
    assert props.american_to_decimal(-200) == pytest.approx(1.5)
    with pytest.raises(ValueError):
        props.american_to_decimal(50)


def test_devig_a_symmetric_pair_is_a_coin_flip():
    # 1.91 / 1.91: each side implies 0.52356, the book's total is 1.04712, so each is exactly 0.5
    assert props.devig_multiplicative(1.91, 1.91) == pytest.approx(0.5)


def test_devig_hand_checked_asymmetric_pairs():
    # 1.80 / 2.00: implied 0.555556 and 0.5, total 1.055556 -> over = 0.555556 / 1.055556 = 0.526316
    assert props.devig_multiplicative(1.80, 2.00) == pytest.approx(10 / 19)
    # American -150 / +130 = decimal 1.666667 / 2.30: implied 0.6 and 0.434783 -> 0.6 / 1.034783 = 0.579832
    over = props.devig_multiplicative(props.american_to_decimal(-150), props.american_to_decimal(130))
    assert over == pytest.approx(0.579832, abs=1e-6)
    # the two sides of one market sum to 1
    assert props.devig_multiplicative(2.00, 1.80) == pytest.approx(1 - 10 / 19)


# ---------------------------------------------------------------------------------------------
# the parser (SYNTHETIC fixture, UNVERIFIED shape)
# ---------------------------------------------------------------------------------------------


def test_parser_reads_every_modelled_market_and_skips_the_rest(event):
    quotes = props.parse_event_odds(event)
    assert list(quotes.columns) == props.QUOTE_COLUMNS
    assert set(quotes["market"]) == set(props.MARKETS)  # h2h and the alternate market are not in it
    assert set(quotes["book"]) == {"draftkings", "fanduel", "betmgm", "caesars"}
    # the Caesars Over has no price: skipped and counted, its Under (a lone, readable outcome) is kept
    assert quotes.attrs["skipped"] == 1
    assert (quotes["price"] > 1.0).all()
    yes_no = quotes[quotes["stat"] == "td"]
    assert set(yes_no["side"]) == {"yes", "no"}
    assert yes_no["line"].isna().all()
    assert quotes[quotes["stat"] != "td"]["line"].notna().all()
    one = quotes[(quotes["player"] == "Patrick Mahomes") & (quotes["market"] == "player_pass_yds")]
    assert sorted(one["book"]) == [
        "betmgm",
        "betmgm",
        "caesars",
        "draftkings",
        "draftkings",
        "fanduel",
        "fanduel",
    ]
    assert one["event_id"].unique().tolist() == ["synthetic-event-0001"]


def test_parser_accepts_a_list_of_events_and_the_historical_wrapper(event):
    single = props.parse_event_odds(event)
    assert len(props.parse_event_odds([event, event])) == 2 * len(single)
    wrapped = {"timestamp": "2026-10-14T16:00:00Z", "previous_timestamp": None, "data": event}
    assert len(props.parse_event_odds(wrapped)) == len(single)


def test_parser_converts_american_prices_to_decimal():
    payload = {
        "id": "e",
        "bookmakers": [
            {
                "key": "b",
                "markets": [
                    {
                        "key": "player_rush_yds",
                        "outcomes": [
                            {"name": "Over", "description": "A B", "price": -110, "point": 50.5},
                            {"name": "Under", "description": "A B", "price": -110, "point": 50.5},
                        ],
                    }
                ],
            }
        ],
    }
    quotes = props.parse_event_odds(payload, odds_format="american")
    assert quotes["price"].tolist() == pytest.approx([1.9090909, 1.9090909])
    with pytest.raises(ValueError):
        props.parse_event_odds(payload, odds_format="fractional")


def test_parser_is_tolerant_of_odd_shapes():
    assert props.parse_event_odds({}).empty
    assert props.parse_event_odds(None).empty
    assert props.parse_event_odds({"bookmakers": [{"key": "b"}]}).empty
    odd = {
        "id": "e",
        "bookmakers": [
            {
                "key": "b",
                "markets": [
                    {
                        "key": "player_anytime_td",
                        # the older shape: the scorer's name, no Yes/No and no description
                        "outcomes": [{"name": "Some Player", "price": 3.5}, {"name": "Yes", "price": 2.0}],
                    },
                    {
                        "key": "player_receptions",
                        "outcomes": [{"name": "Over", "description": "X", "price": 1.9}],
                    },
                ],
            }
        ],
    }
    quotes = props.parse_event_odds(odd)
    assert quotes["player"].tolist() == ["Some Player"]
    assert quotes["side"].tolist() == ["yes"]
    assert quotes.attrs["skipped"] == 2  # the description-less Yes, and the Over with no point


def test_recorded_sample_parses():
    """Runs once the local session commits a real response; until then the shape stays UNVERIFIED."""
    if not RECORDED.exists():
        pytest.skip("tests/fixtures/odds_api/event_odds_sample.json has not been recorded yet")
    payload = json.loads(RECORDED.read_text())
    quotes = props.parse_event_odds(payload)
    present = {
        m["key"]
        for e in props._events(payload)
        for b in e.get("bookmakers", [])
        for m in b.get("markets", [])
        if m["key"] in props.MARKETS
    }
    assert present, "the recorded response has none of the markets PropPts models"
    assert present <= set(quotes["market"]), f"parsed nothing for {present - set(quotes['market'])}"
    assert quotes.attrs["skipped"] <= 0.05 * len(quotes), "the parser skipped a lot of a real response"


# ---------------------------------------------------------------------------------------------
# per-book probabilities
# ---------------------------------------------------------------------------------------------


def test_book_probabilities_devig_each_books_pair(event):
    books = props.book_probabilities(props.parse_event_odds(event))
    row = books[
        (books["player"] == "Patrick Mahomes")
        & (books["stat"] == "pass_td")
        & (books["book"] == "draftkings")
    ]
    assert row["line"].iloc[0] == 1.5
    assert row["p"].iloc[0] == pytest.approx(10 / 19)
    # Caesars quoted an Under but its Over had no price: nothing to de-vig, no row
    assert "caesars" not in set(books["book"])


def test_anytime_td_yes_no_pair_is_devigged_and_a_lone_yes_gets_the_haircut(event):
    books = props.book_probabilities(props.parse_event_odds(event))
    td = books[(books["stat"] == "td") & (books["player"] == "Isiah Pacheco")].set_index("book")
    # FanDuel Yes 2.50 / No 1.55: (1/2.5) / (1/2.5 + 1/1.55) = 0.4 / 1.045161 = 0.382716
    assert td.loc["fanduel", "p"] == pytest.approx(0.4 / (0.4 + 1 / 1.55))
    assert td.loc["fanduel", "p_method"] == "devig"
    # DraftKings Yes 2.60 alone: (1/2.6) / (1 + ANYTD_YES_ONLY_VIG)
    assert td.loc["draftkings", "p"] == pytest.approx((1 / 2.6) / (1 + props.ANYTD_YES_ONLY_VIG))
    assert td.loc["draftkings", "p_method"] == "yes_only"


def test_a_book_with_two_lines_keeps_the_one_closest_to_even():
    def quote(side, line, price):
        return {
            "event_id": "e",
            "commence_time": "t",
            "home_team": "h",
            "away_team": "a",
            "book": "b",
            "last_update": "u",
            "market": "player_rush_yds",
            "stat": "rush_yd",
            "player": "P",
            "side": side,
            "line": line,
            "price": price,
        }

    quotes = pd.DataFrame(
        [
            quote("over", 49.5, 1.5),
            quote("under", 49.5, 2.7),  # an alternate line, p ~ 0.64
            quote("over", 59.5, 1.91),
            quote("under", 59.5, 1.91),  # the main line, p = 0.5
        ]
    )
    books = props.book_probabilities(quotes)
    assert books["line"].tolist() == [59.5]
    assert books["p"].iloc[0] == pytest.approx(0.5)


# ---------------------------------------------------------------------------------------------
# consensus across books
# ---------------------------------------------------------------------------------------------


def _books(rows, stat="pass_yd"):
    return pd.DataFrame(
        [
            {
                "event_id": "e",
                "commence_time": "t",
                "home_team": "Kansas City Chiefs",
                "away_team": "Buffalo Bills",
                "stat": stat,
                "player": "P",
                "book": f"b{i}",
                "line": line,
                "p": p,
                "p_method": "devig",
            }
            for i, (line, p) in enumerate(rows)
        ],
        columns=props.BOOK_COLUMNS,
    )


def test_consensus_is_the_median_line_and_the_median_probability_at_it():
    row = props.consensus_lines(_books([(250.5, 0.48), (250.5, 0.50), (250.5, 0.56), (252.5, 0.40)])).iloc[0]
    # lines 250.5 x3 and 252.5: median 250.5; the three books on it give median p 0.50; the 252.5 book
    # is moved
    assert row["line"] == 250.5
    assert row["n_books"] == 4
    assert row["n_at_line"] == 3
    moved = props.shift_probability(0.40, 252.5, 250.5, props.shift_sd("pass_yd", 250.5))
    assert row["p"] == pytest.approx(float(np.median([0.48, 0.50, 0.56, moved])))


def test_a_book_already_on_the_median_line_is_not_touched():
    row = props.consensus_lines(_books([(100.5, 0.47), (100.5, 0.47), (100.5, 0.47)], stat="rec_yd")).iloc[0]
    assert row["p"] == pytest.approx(0.47)
    assert row["n_at_line"] == 3


def test_shift_probability_moves_with_the_line_and_is_the_identity_at_the_same_line():
    assert props.shift_probability(0.55, 60.5, 60.5, 25.0) == 0.55
    # hand check: p = 0.5 at 59.5 moved to 60.5 with sd 20 -> Phi(-1/20) = 0.480061
    assert props.shift_probability(0.5, 59.5, 60.5, 20.0) == pytest.approx(0.480061, abs=1e-6)
    assert props.shift_probability(0.5, 61.5, 60.5, 20.0) == pytest.approx(0.519939, abs=1e-6)


def test_consensus_on_the_fixture(event):
    consensus = props.consensus_lines(props.book_probabilities(props.parse_event_odds(event)))
    mahomes = consensus[(consensus["player"] == "Patrick Mahomes") & (consensus["stat"] == "pass_yd")].iloc[0]
    assert mahomes["line"] == 275.5  # 275.5, 274.5, 275.5
    assert mahomes["n_books"] == 3
    assert mahomes["n_at_line"] == 2
    assert 0.49 < mahomes["p"] < 0.52
    td = consensus[(consensus["player"] == "Rashee Rice") & (consensus["stat"] == "td")].iloc[0]
    assert math.isnan(td["line"])
    assert td["n_books"] == 3
    only_yes = consensus[(consensus["player"] == "Patrick Mahomes") & (consensus["stat"] == "td")].iloc[0]
    assert only_yes["p_method"] == "yes_only"


def test_min_books_drops_thin_markets(event):
    books = props.book_probabilities(props.parse_event_odds(event))
    assert len(props.consensus_lines(books, min_books=3)) < len(props.consensus_lines(books))


# ---------------------------------------------------------------------------------------------
# anytime TD, bonuses, mean from a line
# ---------------------------------------------------------------------------------------------


def test_anytime_td_conversion_p_half():
    assert props.anytime_td_expectation(0.5) == pytest.approx(math.log(2))
    assert props.anytime_td_expectation(0.5) == pytest.approx(0.6931, abs=1e-4)
    # 0.693 TDs at 6 points
    assert props.anytime_td_expectation(0.5) * props.TD_POINTS == pytest.approx(4.16, abs=0.005)
    assert props.TD_POINTS == DK_OFFENSE_SCORING["rush_td"] == DK_OFFENSE_SCORING["rec_td"] == 6.0
    # a low probability is nearly itself, and a coin-flip is NOT 0.5 TDs
    assert props.anytime_td_expectation(0.05) == pytest.approx(0.0513, abs=1e-4)


def test_bonus_probability_exponential_case_by_hand():
    # mean == phi gives gamma shape 1, an exponential with mean 50: P(yards > 99.5) = exp(-99.5 / 50)
    assert props.bonus_probability("rush_yd", 50.0, 50.0, 100.0) == pytest.approx(math.exp(-99.5 / 50.0))
    assert props.bonus_probability("rush_yd", 0.0, 50.0, 100.0) == 0.0


def test_bonus_probability_for_passing_yards_is_a_normal():
    mean, phi = 300.0, 22.0
    z = (299.5 - mean) / math.sqrt(phi * mean)
    assert props.bonus_probability("pass_yd", mean, phi, 300.0) == pytest.approx(
        0.5 * math.erfc(z / math.sqrt(2))
    )
    # more yards, more chance, and a player averaging 100 clears 100 less than half the time (skew)
    assert props.bonus_probability("rush_yd", 120.0, 23.0, 100.0) > props.bonus_probability(
        "rush_yd", 80.0, 23.0, 100.0
    )
    assert props.bonus_probability("rec_yd", 100.0, 24.0, 100.0) < 0.5


def test_mean_from_line_gamma_median_to_mean_by_hand():
    # an exponential's median is mean * ln 2: a line at 50 * ln 2 priced as a coin flip means a mean of 50
    assert props.mean_from_line("rush_yd", 50 * math.log(2), 0.5, phi=50.0) == pytest.approx(50.0, rel=1e-6)


def test_mean_from_line_adds_the_skew_for_receiving_and_rushing_yards_but_not_passing():
    for stat in ("rec_yd", "rush_yd"):
        mean = props.mean_from_line(stat, 60.5, 0.5, phi=props.dispersion("WR", stat))
        assert mean > 60.5 + 3.0  # right-skewed: the mean is above the median
    # passing yards are symmetric: a coin-flip line IS the mean
    assert props.mean_from_line("pass_yd", 250.5, 0.5, phi=22.0) == pytest.approx(250.5, rel=1e-6)


def test_mean_from_line_moves_with_the_probability():
    low, mid, high = (props.mean_from_line("rec_yd", 60.5, p, phi=24.0) for p in (0.40, 0.50, 0.60))
    assert low < mid < high
    # the answer reproduces the probability it was solved from
    for p in (0.35, 0.5, 0.65):
        mean = props.mean_from_line("rec_yd", 60.5, p, phi=24.0)
        assert props.over_probability("rec_yd", mean, 24.0, 60.5) == pytest.approx(p, abs=1e-6)
    # a normal (passing yards) shifts by sd * Phi^-1(p): mean = line + sd * z with sd = sqrt(phi * mean)
    mean = props.mean_from_line("pass_yd", 250.5, 0.60, phi=22.0)
    assert mean > 250.5
    assert stats.norm.sf(250.5, loc=mean, scale=math.sqrt(22.0 * mean)) == pytest.approx(0.60, abs=1e-6)


def test_mean_from_line_for_counts_poisson_by_hand():
    # Poisson, over 0.5 priced at p: P(X >= 1) = 1 - exp(-mean), so mean = -ln(1 - p), the anytime-TD formula
    assert props.mean_from_line("pass_int", 0.5, 0.5, phi=1.0) == pytest.approx(math.log(2))
    # over 1.5 at p = 1 - e^-2 * (1 + 2): the mean is exactly 2
    p = 1 - math.exp(-2) * 3
    assert props.mean_from_line("pass_td", 1.5, p, phi=1.0) == pytest.approx(2.0, rel=1e-6)


def test_mean_from_line_for_overdispersed_counts_uses_a_negative_binomial():
    p = props.over_probability("rec", 5.0, 1.4, 5.5)
    assert props.mean_from_line("rec", 5.5, p, phi=1.4) == pytest.approx(5.0, rel=1e-6)
    # the same probability read as a Poisson would give a different mean: phi matters
    assert props.mean_from_line("rec", 5.5, p, phi=1.0) != pytest.approx(5.0, rel=1e-3)


def test_an_integer_count_line_excludes_the_push():
    # a whole-number line: over = P(X > 4) / (1 - P(X = 4))
    dist = stats.poisson(4.0)
    assert props.over_probability("pass_td", 4.0, 1.0, 4.0) == pytest.approx(dist.sf(4) / (1 - dist.pmf(4)))


def test_extreme_probabilities_are_clipped_not_infinite():
    for p in (0.0, 1.0, 0.999, 0.001):
        value = props.mean_from_line("rec_yd", 60.5, p, phi=24.0)
        assert math.isfinite(value) and value > 0
    assert math.isfinite(props.anytime_td_expectation(1.0))


# ---------------------------------------------------------------------------------------------
# scoring
# ---------------------------------------------------------------------------------------------


def test_score_stat_line_uses_dk_scoring_constants_and_the_expected_bonus():
    mean = {"pass_yd": 250.0, "pass_td": 1.5, "pass_int": 0.7, "rush_yd": 20.0, "td": 0.2}
    out = props.score_stat_line("QB", mean, phi={"pass_yd": 22.0, "rush_yd": 17.0})
    expected_linear = (
        250.0 * DK_OFFENSE_SCORING["pass_yd"]
        + 1.5 * DK_OFFENSE_SCORING["pass_td"]
        - 0.7
        + 20.0 * DK_OFFENSE_SCORING["rush_yd"]
        + 0.2 * 6.0
    )
    z = (299.5 - 250.0) / math.sqrt(22.0 * 250.0)
    pass_bonus = YARDAGE_BONUS_POINTS * 0.5 * math.erfc(z / math.sqrt(2))
    rush_bonus = YARDAGE_BONUS_POINTS * float(stats.gamma.sf(99.5, a=20.0 / 17.0, scale=17.0))
    assert out["bonus"] == pytest.approx(pass_bonus + rush_bonus)
    assert out["PropPts"] == pytest.approx(round(expected_linear + pass_bonus + rush_bonus, 2), abs=0.006)


def test_a_stat_that_is_not_in_the_line_adds_nothing():
    out = props.score_stat_line("WR", {"rec": 5.0, "rec_yd": 60.0, "td": 0.4}, phi={"rec_yd": 24.0})
    assert out["pass_yd"] == 0.0 and out["rush_yd"] == 0.0
    assert out["PropPts"] == pytest.approx(5.0 + 6.0 + 2.4 + out["bonus"], abs=0.006)


# ---------------------------------------------------------------------------------------------
# the no-partial rule, player matching, and the whole chain
# ---------------------------------------------------------------------------------------------


def _consensus(rows):
    """Consensus rows for one KC-BUF event: (player, stat, line, p)."""
    return pd.DataFrame(
        [
            {
                "event_id": "e",
                "commence_time": "t",
                "home_team": "Kansas City Chiefs",
                "away_team": "Buffalo Bills",
                "player": player,
                "stat": stat,
                "line": line,
                "p": p,
                "n_books": 3,
                "n_at_line": 3,
                "p_method": "devig",
            }
            for player, stat, line, p in rows
        ],
        columns=props.CONSENSUS_COLUMNS,
    )


NAN = float("nan")


def test_no_partial_proppts_for_any_position(pool):
    full_wr = [
        ("Rashee Rice", "rec", 5.5, 0.5),
        ("Rashee Rice", "rec_yd", 62.5, 0.5),
        ("Rashee Rice", "td", NAN, 0.3),
    ]
    scored, _ = props.prop_pts(_consensus(full_wr), pool)
    assert scored["PropPts"].notna().all()

    for drop in ("rec", "rec_yd", "td"):  # WR/TE need all three
        rows = [r for r in full_wr if r[1] != drop]
        scored, _ = props.prop_pts(_consensus(rows), pool)
        assert scored["PropPts"].isna().all(), drop
        assert scored["Missing"].iloc[0] == drop

    qb = [("Patrick Mahomes", "pass_yd", 275.5, 0.5), ("Patrick Mahomes", "pass_td", 1.5, 0.5)]
    assert props.prop_pts(_consensus(qb), pool)[0]["PropPts"].notna().all()
    assert props.prop_pts(_consensus(qb[:1]), pool)[0]["Missing"].iloc[0] == "pass_td"
    assert props.prop_pts(_consensus(qb[1:]), pool)[0]["Missing"].iloc[0] == "pass_yd"
    # a QB with an anytime TD and rushing yards but no passing markets is still blank
    rushing_qb = [("Patrick Mahomes", "rush_yd", 16.5, 0.5), ("Patrick Mahomes", "td", NAN, 0.2)]
    assert props.prop_pts(_consensus(rushing_qb), pool)[0]["PropPts"].isna().all()

    rb = [("Isiah Pacheco", "rush_yd", 58.5, 0.5), ("Isiah Pacheco", "td", NAN, 0.4)]
    assert props.prop_pts(_consensus(rb), pool)[0]["PropPts"].notna().all()
    assert props.prop_pts(_consensus(rb[:1]), pool)[0]["PropPts"].isna().all()
    assert props.prop_pts(_consensus(rb[1:]), pool)[0]["Missing"].iloc[0] == "rush_yd"


def test_a_required_market_with_too_few_books_counts_as_missing(pool):
    rows = _consensus([("Patrick Mahomes", "pass_yd", 275.5, 0.5), ("Patrick Mahomes", "pass_td", 1.5, 0.5)])
    rows.loc[rows["stat"] == "pass_td", "n_books"] = 1
    assert props.prop_pts(rows, pool, min_books=2)[0]["PropPts"].isna().all()
    assert props.prop_pts(rows, pool, min_books=1)[0]["PropPts"].notna().all()


def test_dst_and_unknown_positions_get_no_proppts():
    players = pd.DataFrame(
        {"Name": ["Chiefs", "Harrison Butker"], "Team": ["KC", "KC"], "Position": ["DST", "K"]}
    )
    scored, _ = props.prop_pts(_consensus([("Harrison Butker", "td", NAN, 0.1)]), players)
    assert scored.empty


def test_rb_without_receiving_markets_is_scored_and_flagged(pool):
    rb = [("Isiah Pacheco", "rush_yd", 58.5, 0.5), ("Isiah Pacheco", "td", NAN, 0.4)]
    row = props.prop_pts(_consensus(rb), pool)[0].iloc[0]
    assert row["PropPts"] > 0
    assert row["rec"] == 0 and row["rec_yd"] == 0
    assert "no:rec" in row["Flags"] and "no:rec_yd" in row["Flags"]
    with_rec = rb + [("Isiah Pacheco", "rec", 2.5, 0.5), ("Isiah Pacheco", "rec_yd", 15.5, 0.5)]
    full = props.prop_pts(_consensus(with_rec), pool)[0].iloc[0]
    assert full["PropPts"] > row["PropPts"]
    assert full["Flags"] == ""


def test_a_qb_without_an_int_market_uses_his_rate_or_the_league_rate_and_says_so(pool):
    qb = [("Patrick Mahomes", "pass_yd", 275.5, 0.5), ("Patrick Mahomes", "pass_td", 1.5, 0.5)]
    league = props.prop_pts(_consensus(qb), pool)[0].iloc[0]
    assert league["pass_int"] == pytest.approx(-props.QB_INT_LEAGUE_RATE, abs=1e-3)
    assert "int:league" in league["Flags"]
    own = props.prop_pts(_consensus(qb), pool, qb_int_rates={"patrick mahomes": 1.2})[0].iloc[0]
    assert own["pass_int"] == pytest.approx(-1.2, abs=1e-3)
    assert "int:rate" in own["Flags"]
    with_market = props.prop_pts(_consensus([*qb, ("Patrick Mahomes", "pass_int", 0.5, 0.6)]), pool)[0].iloc[
        0
    ]
    assert "int:" not in with_market["Flags"]
    expected_int = props.mean_from_line("pass_int", 0.5, 0.6, props.dispersion("QB", "pass_int"))
    assert with_market["pass_int"] == pytest.approx(-expected_int, abs=1e-3)


def test_players_are_matched_on_name_and_a_team_in_the_event():
    players = pd.DataFrame(
        {
            "Name": ["Mike Williams", "Mike Williams", "Patrick Mahomes II"],
            "Team": ["KC", "BUF", "KC"],
            "Position": ["WR", "WR", "QB"],
        }
    )
    consensus = _consensus([("Mike Williams", "rec", 4.5, 0.5), ("Patrick Mahomes", "pass_yd", 275.5, 0.5)])
    matched, unmatched = props.attach_players(consensus, players)
    # two Mike Williams play in this event: refuse to guess; a Jr/II suffix does not stop Mahomes matching
    assert matched["Name"].tolist() == ["Patrick Mahomes II"]
    assert unmatched["reason"].tolist() == ["ambiguous"]
    elsewhere = consensus.assign(home_team="Green Bay Packers", away_team="Chicago Bears")
    assert props.attach_players(elsewhere, players)[0].empty  # nobody on a team in that event


def test_the_whole_chain_on_the_fixture(event, pool):
    scored, unmatched = props.props_from_responses(event, pool)
    assert unmatched.empty
    by_name = scored.set_index("Name")
    assert list(scored.columns) == props.PROP_COLUMNS
    # Allen quoted only passing yards (no pass TDs): blank, never partial
    assert math.isnan(by_name.loc["Josh Allen", "PropPts"])
    assert by_name.loc["Josh Allen", "Missing"] == "pass_td"
    # Kelce has receptions and yards but no anytime TD: blank
    assert math.isnan(by_name.loc["Travis Kelce", "PropPts"])
    assert by_name.loc["Travis Kelce", "Missing"] == "td"
    for name in ("Patrick Mahomes", "Isiah Pacheco", "Rashee Rice"):
        assert by_name.loc[name, "PropPts"] > 0
    assert 14 < by_name.loc["Patrick Mahomes", "PropPts"] < 25
    assert by_name.loc["Isiah Pacheco", "Position"] == "RB"


def _independent_mean(kind, line, p, phi):
    """The mean-from-line solved here with scipy directly, not through `props`."""
    if kind == "normal":
        return optimize.brentq(lambda m: stats.norm.sf(line, m, math.sqrt(phi * m)) - p, 1, 5000)
    if kind == "gamma":
        return optimize.brentq(lambda m: stats.gamma.sf(line, m / phi, scale=phi) - p, 1e-3, 5000)
    if kind == "poisson":
        return optimize.brentq(lambda m: stats.poisson.sf(math.floor(line), m) - p, 1e-3, 50)
    return optimize.brentq(
        lambda m: stats.nbinom.sf(math.floor(line), m / (phi - 1), 1 / phi) - p, 1e-3, 50
    )  # nbinom


def test_the_fixture_worked_through_independently_for_one_qb_and_one_wr(event, pool):
    """The consensus numbers come from `props`; everything after them is recomputed here with scipy."""
    quotes = props.parse_event_odds(event)
    consensus = props.consensus_lines(props.book_probabilities(quotes))
    scored, _ = props.prop_pts(consensus, pool)
    by_name = scored.set_index("Name")

    def c(player, stat):
        return consensus[(consensus["player"] == player) & (consensus["stat"] == stat)].iloc[0]

    # Mahomes: passing yards (normal), pass TDs (Poisson, phi 0.833 <= 1), INTs (negative binomial,
    # phi 1.045), rushing yards (gamma)
    qb = "Patrick Mahomes"
    py = _independent_mean(
        "normal", c(qb, "pass_yd").line, c(qb, "pass_yd").p, props.dispersion("QB", "pass_yd")
    )
    ptd = _independent_mean("poisson", c(qb, "pass_td").line, c(qb, "pass_td").p, 1.0)
    pint = _independent_mean(
        "nbinom", c(qb, "pass_int").line, c(qb, "pass_int").p, props.dispersion("QB", "pass_int")
    )
    ry = _independent_mean(
        "gamma", c(qb, "rush_yd").line, c(qb, "rush_yd").p, props.dispersion("QB", "rush_yd")
    )
    td = -math.log(1 - c(qb, "td").p)
    z = (299.5 - py) / math.sqrt(props.dispersion("QB", "pass_yd") * py)
    bonus = 3 * 0.5 * math.erfc(z / math.sqrt(2)) + 3 * stats.gamma.sf(
        99.5, ry / props.dispersion("QB", "rush_yd"), scale=props.dispersion("QB", "rush_yd")
    )
    expected = 0.04 * py + 4 * ptd - pint + 0.1 * ry + 6 * td + bonus
    assert by_name.loc[qb, "PropPts"] == pytest.approx(expected, abs=0.011)

    # Rice: receptions (negative binomial), receiving yards (gamma), anytime TD
    wr = "Rashee Rice"
    rec = _independent_mean("nbinom", c(wr, "rec").line, c(wr, "rec").p, props.dispersion("WR", "rec"))
    ryd = _independent_mean(
        "gamma", c(wr, "rec_yd").line, c(wr, "rec_yd").p, props.dispersion("WR", "rec_yd")
    )
    wtd = -math.log(1 - c(wr, "td").p)
    phi = props.dispersion("WR", "rec_yd")
    wr_bonus = 3 * stats.gamma.sf(99.5, ryd / phi, scale=phi)
    assert by_name.loc[wr, "PropPts"] == pytest.approx(rec + 0.1 * ryd + 6 * wtd + wr_bonus, abs=0.011)


# ---------------------------------------------------------------------------------------------
# nflverse helpers
# ---------------------------------------------------------------------------------------------


def test_season_int_rates_shrink_toward_the_league_rate():
    rows = []
    for week in range(1, 5):
        rows.append(("Steady Q", week, 1))  # 4 games, 4 INTs
        rows.append(("Clean Q", week, 0))  # 4 games, 0 INTs
    rows.append(("Backup", 1, 3))  # one game
    df = pd.DataFrame(rows, columns=["player_display_name", "week", "passing_interceptions"])
    df["season"], df["season_type"], df["position"], df["attempts"] = 2026, "REG", "QB", 30
    rates, league = props.season_int_rates(df, 2026, through_week=4)
    assert league == pytest.approx(7 / 9)  # 7 INTs over 9 qualifying games
    k = props.INT_RATE_SHRINK_GAMES
    assert rates["steady q"] == pytest.approx((4 + k * league) / (4 + k))
    assert rates["clean q"] == pytest.approx((0 + k * league) / (4 + k))
    assert rates["clean q"] < rates["steady q"] < rates["backup"]
    # weeks after `through_week` and low-attempt games do not count
    df.loc[df["player_display_name"] == "Backup", "attempts"] = 3
    assert "backup" not in props.season_int_rates(df, 2026, through_week=4)[0]
    assert props.season_int_rates(df, 2026, through_week=0)[0] == {}


def test_the_fitted_dispersion_table_is_complete_and_sane():
    for (position, stat), phi in props.DISPERSION.items():
        assert position in props.REQUIRED_MARKETS and stat in props.DISPERSION_POOLED
        assert phi > 0
    # every stat a position's score uses has a dispersion (own or pooled)
    for stat in ("pass_yd", "pass_td", "pass_int", "rush_yd", "rec", "rec_yd"):
        assert props.DISPERSION_POOLED[stat] > 0
    # yardage is hugely over-dispersed against a Poisson, receptions mildly, TD counts are not
    assert props.dispersion("WR", "rec_yd") > 10
    assert 1.0 < props.dispersion("WR", "rec") < 2.0
    assert props.dispersion("QB", "pass_td") <= 1.0
    assert 0.5 < props.QB_INT_LEAGUE_RATE < 1.0
