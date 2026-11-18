"""`week_compare.py`: the compare tray's rows and directions (also the colour by position of every page
table). They drove the sheet's Compare tab until removal F9; the tab is gone, the rules are unchanged."""

from dfs import week_compare as cc
from dfs.derived import EDGE_SHEET_ORDER

ALL_ROWS = [item for _title, rows in cc.SECTIONS for item in rows]


def test_every_compared_row_is_an_edgeraw_column_or_the_implied_total():
    for item in ALL_ROWS:
        if item.column == "Implied":
            assert not item.edge
            continue
        assert item.edge and item.column in EDGE_SHEET_ORDER, item


def test_every_measure_the_compare_tab_had_is_still_compared():
    wanted = [
        "Team", "Pos", "Salary", "Opp", "Kickoff (ET)", "Status", "Flags",
        "TFFB", "Sleeper", "FantasyPros", "ESPN", "Props", "AggPts", "CalPts",
        "Hit3x%", "Boom%", "Bust%", "Ceil (model)", "Val", "ValAdj",
        "Own% (TFFB)", "GppOwn (shadow)", "CashOwn (shadow)", "Leverage",
        "Snap% L5", "Targets L5", "Carries L5", "DK pts L5", "Target share", "xFP / game", "xFP last 3",
        "xFP trend", "Receiving share of xFP",
        "TPRR~ (proxy)", "xFP per $1k", "Targets per $1k", "HVT per $1k (RB)", "PriceGap (SD)",
        "Total", "Spread", "Implied (team)", "OppPosRank", "OppEPA (whole defense)", "Wind (mph)",
    ]  # fmt: skip
    assert [item.label for item in ALL_ROWS] == wanted


def test_the_directions_are_the_stated_ones():
    direction = {item.label: item.better for item in ALL_ROWS}
    lows = {k for k, v in direction.items() if v == cc.LOW}
    assert lows == {"Bust%", "OppPosRank"}
    nones = {k for k, v in direction.items() if v is None}
    # Sam, 2026-10-22: salary and ownership depend on cash vs GPP, so they are never shaded
    assert {
        "Team", "Pos", "Salary", "Opp", "Kickoff (ET)", "Status", "Flags",
        "Own% (TFFB)", "GppOwn (shadow)", "CashOwn (shadow)", "Spread", "Wind (mph)",
    } == nones  # fmt: skip
    assert direction["CalPts"] == cc.HIGH and direction["Leverage"] == cc.HIGH
    for phrase in ("Bust%", "OppPosRank", "Salary", "cash vs GPP"):
        assert phrase in cc.HIGHLIGHT_NOTE  # the lower-is-better rows and the unshaded ones are in one note


def test_the_matchup_note_says_how_oppposrank_and_oppepa_differ():
    assert "defense against his position" in cc.MATCHUP_NOTE and "the defense overall" in cc.MATCHUP_NOTE
    assert cc.NOTES["MATCHUP AND GAME"] == cc.MATCHUP_NOTE
    assert "Player tab" not in cc.NOTES["USAGE"]  # the game by game rows are in the drawer now
