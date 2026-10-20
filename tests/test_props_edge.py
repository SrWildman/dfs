"""props_edge: the props shadow (pure parts on tiny hand-built payloads, no files, no network)."""

from __future__ import annotations

import math
from datetime import UTC, datetime

import pandas as pd
import pytest

from dfs import props, props_edge
from dfs.props_edge import MIN_N_VERDICT, Shadow

KICKOFF = "2026-10-18T17:00:00Z"
PULL = datetime(2026, 10, 17, 17, 0, tzinfo=UTC)


def _outcome(side, player, price, point=None):
    out = {"name": side, "description": player, "price": price}
    if point is not None:
        out["point"] = point
    return out


def _event(books, event_id="e1", commence=KICKOFF):
    return {
        "id": event_id,
        "commence_time": commence,
        "home_team": "Atlanta Falcons",
        "away_team": "Chicago Bears",
        "bookmakers": books,
    }


def _book(key, markets):
    return {"key": key, "markets": [{"key": k, "outcomes": o} for k, o in markets.items()]}


def _rec_yds(player, line, over, under):
    return [_outcome("Over", player, over, line), _outcome("Under", player, under, line)]


def test_events_before_kickoff_keeps_the_latest_pull_that_precedes_the_game():
    early = {"events": [_event([_book("dk", {})], commence=KICKOFF)]}
    full = {"events": [_event([_book("dk", {"player_reception_yds": []})])]}
    late = {"events": [_event([_book("dk", {"player_reception_yds": []})])]}
    pulls = [
        (datetime(2026, 10, 14, tzinfo=UTC), early),
        (PULL, full),
        (datetime(2026, 10, 18, 18, 0, tzinfo=UTC), late),  # after kickoff: never used
    ]
    chosen = props_edge.events_before_kickoff(pulls)
    assert [stamp for stamp, _ in chosen] == [PULL]  # the later pull wins over the earlier one
    assert len(chosen) == 1


def test_line_mode_prefers_the_most_quoted_line_then_the_one_nearer_the_median():
    assert props_edge._line_mode(pd.Series([91.5, 92.5, 92.5])) == 92.5
    assert props_edge._line_mode(pd.Series([91.5, 92.5, 93.5, 94.5])) == 92.5  # tie: nearer the median, lower


def test_market_rows_devig_the_books_at_the_modal_line_and_keep_median_prices():
    event = _event(
        [
            _book("a", {"player_reception_yds": _rec_yds("Drake London", 92.5, -110, -110)}),
            _book("b", {"player_reception_yds": _rec_yds("Drake London", 92.5, -120, 100)}),
            _book("c", {"player_reception_yds": _rec_yds("Drake London", 93.5, -110, -110)}),
        ]
    )
    rows = props_edge.market_rows([(PULL, event)])
    assert len(rows) == 1
    row = rows.iloc[0]
    assert row["Line"] == 92.5 and row["NBooks"] == 2
    p_a = props.devig_multiplicative(props.american_to_decimal(-110), props.american_to_decimal(-110))
    p_b = props.devig_multiplicative(props.american_to_decimal(-120), props.american_to_decimal(100))
    assert row["MarketP"] == pytest.approx((p_a + p_b) / 2)
    assert row["OverPrice"] == pytest.approx(
        (props.american_to_decimal(-110) + props.american_to_decimal(-120)) / 2
    )


def test_market_rows_anytime_td_keeps_a_missing_no_price_as_nan():
    event = _event([_book("a", {"player_anytime_td": [_outcome("Yes", "Bijan Robinson", -200)]})])
    row = props_edge.market_rows([(PULL, event)]).iloc[0]
    assert math.isnan(row["UnderPrice"]) and row["OverPrice"] == pytest.approx(1.5)
    assert 0.5 < row["MarketP"] < 0.66  # implied 0.667 less the Yes-only haircut


def test_fit_dispersion_shrinks_toward_the_history_prior():
    rows = pd.DataFrame(
        {
            "Week": 6,
            "Position": "WR",
            "Stat": "rec_yd",
            "Proj": [50.0] * 10,
            "Actual": [30.0, 70.0] * 5,  # miss of 20 every time: raw phi = 400 / 50 = 8
        }
    )
    fit = props_edge.fit_dispersion(rows).iloc[0]
    prior = props.dispersion("WR", "rec_yd")
    assert fit["RawPhi"] == pytest.approx(8.0) and fit["n"] == 10 and fit["Bias"] == pytest.approx(0.0)
    expected = (10 * 8.0 + props_edge.SHRINK_PLAYERS * prior) / (10 + props_edge.SHRINK_PLAYERS)
    assert fit["Phi"] == pytest.approx(expected)
    assert 8.0 < fit["Phi"] < prior


def test_phi_lookup_falls_back_to_the_prior_when_there_is_no_fit():
    assert props_edge.phi_lookup(
        props_edge.fit_dispersion(pd.DataFrame()), "WR", "rec_yd"
    ) == props.dispersion("WR", "rec_yd")


def test_verdict_words_wait_for_enough_rows_and_clear_two_standard_errors():
    assert props_edge.verdict_word(MIN_N_VERDICT - 1, 0.9, 0.5) == f"n < {MIN_N_VERDICT}"
    assert props_edge.verdict_word(400, 0.60, 0.52) == "beat the price"
    assert props_edge.verdict_word(400, 0.44, 0.52) == "missed the price"
    assert props_edge.verdict_word(400, 0.53, 0.52) == "no edge shown"


def _rows(n, edge, won, price=1.91, stat="rec_yd"):
    return pd.DataFrame(
        {
            "Stat": stat,
            "Edge": edge,
            "Won": won,
            "Price": price,
            "Profit": [(price - 1) if w else -1.0 for w in won],
        }
    )


def test_band_table_is_cumulative_and_counts_only_scored_rows():
    rows = pd.concat(
        [
            _rows(2, 0.02, [True, False]),
            _rows(2, 0.07, [True, True]),
            _rows(1, -0.12, [False]),
            _rows(1, 0.30, [None]).astype({"Won": object}),  # not played yet: no result
        ],
        ignore_index=True,
    )
    table = props_edge.band_table(rows)
    cells = table[table["Market"] == "Rec yards"].set_index("Band")
    assert cells.loc["all gaps", "n"] == 5
    assert cells.loc["5+ pts", "n"] == 3  # 0.07, 0.07, -0.12
    assert cells.loc["10+ pts", "n"] == 1
    assert cells.loc["5+ pts", "Won"] == pytest.approx(2 / 3)
    assert cells.loc["5+ pts", "Needed"] == pytest.approx(1 / 1.91)
    assert cells.loc["all gaps", "Return"] == pytest.approx((3 * 0.91 - 2) / 5)
    assert bool(cells.loc["all gaps", "Thin"]) is True
    assert "All markets" in set(table["Market"])


def test_edge_rows_side_result_and_voids(monkeypatch):
    event = _event(
        [
            _book("a", {"player_reception_yds": _rec_yds("Drake London", 50.5, -110, -110)}),
            _book("b", {"player_reception_yds": _rec_yds("Drake London", 50.5, -110, -110)}),
            _book("a2", {"player_reception_yds": _rec_yds("Ghost Player", 40.5, -110, -110)}),
        ]
    )
    market = props_edge.market_rows([(PULL, event)])
    espn = props_edge.with_keys(
        pd.DataFrame(
            {
                "Name": ["Drake London", "Ghost Player"],
                "Team": ["ATL", "ATL"],
                "Position": ["WR", "WR"],
                "rec_yd": [80.0, 60.0],
            }
        )
    )
    monkeypatch.setattr(props_edge, "espn_at", lambda stamp: espn)
    stats = pd.DataFrame(
        {
            "season": 2026,
            "season_type": "REG",
            "week": 6,
            "player_display_name": ["Drake London"],
            "team": ["ATL"],
            "receiving_yards": [75],
        }
    )
    fit = props_edge.fit_dispersion(pd.DataFrame())
    rows, counts = props_edge.edge_rows(market, stats, season=2026, fits={}, default_fit=fit)
    assert counts["void"] == 1  # Ghost Player has no stats row: the prop is void
    row = rows.iloc[0]
    assert row["Side"] == "over" and row["Edge"] > 0 and row["Gap"] == pytest.approx(29.5)
    assert bool(row["Won"]) is True and row["Actual"] == 75
    assert row["Profit"] == pytest.approx(props.american_to_decimal(-110) - 1)


def _shadow():
    rows = _rows(120, 0.12, [True] * 70 + [False] * 50)
    return Shadow(
        rows=rows,
        table=props_edge.band_table(rows),
        fit=pd.DataFrame(),
        counts={"fit_players": 436},
        weeks=[6],
        fit_weeks=[6],
    )


def _scored():
    return pd.DataFrame(
        {
            "RosterablePool": [True],
            "week": [6],
            "Position": ["WR"],
            "ProjPts": [10.0],
            "Actual": [12.0],
            "Status": ["played"],
        }
    )


def test_headline_never_gives_a_verdict_under_the_minimum():
    thin = Shadow(
        rows=_rows(10, 0.2, [True] * 10),
        table=props_edge.band_table(_rows(10, 0.2, [True] * 10)),
        fit=pd.DataFrame(),
    )
    assert props_edge.headline(thin).startswith("Too early for a verdict: 10 scored")
    assert not props_edge.headline(_shadow()).startswith("Too early")


def test_props_shadow_section_renders_and_is_left_out_without_a_shadow():
    from dfs import sheet_model_check as mc

    builder = mc._Builder()
    mc._add_props_shadow(builder, _shadow())
    text = " | ".join(str(c) for row in builder.layout.rows for c in row if c != "")
    assert "PROPS SHADOW" in text and "Rec yards" in text and "beat the price" in text
    empty = mc._Builder()
    mc._add_props_shadow(empty, None)
    assert empty.layout.rows == []
    placeholder = mc._Builder()
    mc._add_props_shadow(placeholder, Shadow(rows=pd.DataFrame(), table=pd.DataFrame(), fit=pd.DataFrame()))
    assert any("Not enough data yet" in str(c) for row in placeholder.layout.rows for c in row)
