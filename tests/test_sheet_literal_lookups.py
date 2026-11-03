"""Plan D item 7: every hand-built VLOOKUP's typed column number lands on the column it means, in the order
the writer of that tab writes it."""

from __future__ import annotations

import pandas as pd
import pytest

from dfs import sheet_literal_lookups as sll
from dfs.sources import rotowire_odds, tffb_projections

TARGET_COLUMNS = {
    sll.TFFB_TAB: tffb_projections.SHEET_COLUMNS,
    sll.ODDS_FINAL: list(sll.ODDS_FINAL_COLUMNS),
    sll.ODDSRAW: list(rotowire_odds.ODDSRAW_COLUMNS),
}


@pytest.mark.parametrize("lookup", sll.ALL_LOOKUPS, ids=lambda lu: f"{lu.tab}.{lu.header}")
def test_every_typed_index_returns_the_column_it_means(lookup):
    hits = sll.vlookups(lookup.formula)
    assert len(hits) == len(lookup.returns) >= 1
    for hit, wanted in zip(hits, lookup.returns, strict=True):
        columns = TARGET_COLUMNS[hit.target]
        assert hit.column < len(columns), (
            f"{lookup.header}: column {hit.column + 1} is past {hit.target}'s last"
        )
        assert columns[hit.column] == wanted, (
            f"{lookup.tab}!{lookup.header} reads {hit.target} column {hit.column + 1} "
            f"({columns[hit.column]!r}), meant {wanted!r}: a writer's column order moved"
        )


def test_the_tffb_writer_writes_the_columns_in_that_order():
    record = {f: 1 for f in tffb_projections.CORE_FIELDS + tffb_projections.EXTRA_FIELDS}
    assert list(tffb_projections.players_to_df([record]).columns) == tffb_projections.SHEET_COLUMNS


def test_the_odds_writer_writes_the_columns_in_that_order():
    row = {"team": "T", "date": "D", "moneyline": "M", "spread": "S", "total": "O", "team_points": "P"}
    rows = rotowire_odds.RotowireOddsSource().to_sheet_rows(pd.DataFrame([row]))
    assert rows[2] == ["T", "D", "M", "S", "O", "", "P", "", ""]
    assert rows[1][3] == "Spread" and rows[1][4] == rows[1][6] == "Over-Under"


def test_vlookups_reads_quoted_tabs_and_both_dollar_styles():
    (hit,) = sll.vlookups("=VLOOKUP(A2,'Some Tab'!B:$E,3,FALSE)")
    assert (hit.target, hit.first, hit.index, hit.column) == ("Some Tab", "B", 3, 3)
