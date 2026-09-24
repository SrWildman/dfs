import pandas as pd
import pytest

from dfs.sources.fantasypros_projections import (
    FULL_TEAM_NAME_TO_CODE,
    URL_TEMPLATE,
    FantasyProsAuthError,
    FantasyProsFetchError,
    _blank_unprojected_rows,
    _dst_row_stats,
    _flatten_columns,
    _offense_row_stats,
    _parse_offense_table,
    _split_name_team,
)

QB_TABLE_HTML = """
<table>
<thead>
<tr><th></th><th colspan="2">PASSING</th><th colspan="2">RUSHING</th><th>MISC</th></tr>
<tr><th>Player</th><th>YDS</th><th>TDS</th><th>YDS</th><th>TDS</th><th>FL</th></tr>
</thead>
<tbody>
<tr><td>Josh Allen BUF</td><td>250.0</td><td>2.0</td><td>35.0</td><td>0.5</td><td>0.2</td></tr>
<tr><td>Zero Backup NYJ</td><td>0.0</td><td>0.0</td><td>0.0</td><td>0.0</td><td>0.0</td></tr>
</tbody>
</table>
"""

GATED_HTML = QB_TABLE_HTML + '<div id="registration-module"></div>'


def test_split_name_team_splits_trailing_team_code():
    assert _split_name_team("Josh Allen BUF") == ("Josh Allen", "BUF")


def test_split_name_team_handles_suffix_names():
    assert _split_name_team("Joe Milton III DAL") == ("Joe Milton III", "DAL")


def test_split_name_team_no_team_token_falls_back_to_whole_string():
    assert _split_name_team("JustAName") == ("JustAName", "")


def test_flatten_columns_joins_multiindex_section_and_stat():
    df = pd.DataFrame([[1]], columns=pd.MultiIndex.from_tuples([("PASSING", "YDS")]))
    flat = _flatten_columns(df)
    assert list(flat.columns) == ["PASSING_YDS"]


def test_flatten_columns_leaves_unnamed_section_as_bare_stat():
    df = pd.DataFrame([[1]], columns=pd.MultiIndex.from_tuples([("Unnamed: 0_level_0", "Player")]))
    flat = _flatten_columns(df)
    assert list(flat.columns) == ["Player"]


def test_flatten_columns_leaves_plain_index_untouched():
    df = pd.DataFrame([[1]], columns=["Player"])
    flat = _flatten_columns(df)
    assert list(flat.columns) == ["Player"]


def test_parse_offense_table_extracts_name_and_team():
    df = _parse_offense_table(QB_TABLE_HTML)
    row = df[df["Name"] == "Josh Allen"].iloc[0]
    assert row["Team"] == "BUF"
    assert row["PASSING_YDS"] == 250.0


def test_parse_offense_table_raises_when_gated_with_ten_or_fewer_rows():
    small_gated = GATED_HTML  # only 2 rows in the fixture table
    with pytest.raises(FantasyProsAuthError):
        _parse_offense_table(small_gated)


def test_parse_offense_table_raises_on_missing_player_column():
    with pytest.raises(FantasyProsFetchError):
        _parse_offense_table("<table><tr><th>NotPlayer</th></tr><tr><td>x</td></tr></table>")


def test_offense_row_stats_maps_columns_and_defaults_missing_to_zero():
    row = pd.Series({"PASSING_YDS": 250.0, "PASSING_TDS": 2.0, "MISC_FL": 0.2})
    stats = _offense_row_stats(row)
    assert stats["pass_yd"] == 250.0
    assert stats["rush_yd"] == 0.0
    assert stats["two_pt"] == 0.0  # never published by FantasyPros


def test_dst_row_stats_maps_columns_and_defaults_blocked_kick_to_zero():
    row = pd.Series({"SACK": 3.0, "INT": 1.0, "PA": 17.5})
    stats = _dst_row_stats(row)
    assert stats["sack"] == 3.0
    assert stats["blocked_kick"] == 0.0
    assert stats["points_allowed"] == 17.5


def test_blank_unprojected_rows_nans_out_a_real_all_zero_row():
    stats = pd.DataFrame(
        [
            {"pass_yd": 250.0, "pass_td": 2.0, "two_pt": 0.0},
            {"pass_yd": 0.0, "pass_td": 0.0, "two_pt": 0.0},
        ]
    )
    result = _blank_unprojected_rows(stats, ["pass_yd", "pass_td"])
    assert result.iloc[0]["pass_yd"] == 250.0
    assert pd.isna(result.iloc[1]["pass_yd"])
    assert pd.isna(result.iloc[1]["two_pt"])  # whole row blanked, not just the checked fields


def test_blank_unprojected_rows_ignores_fields_not_in_page_fields():
    # two_pt is always a real 0.0 from this source -- must not, by itself,
    # trigger the "no real projection" blank for an otherwise-real row.
    stats = pd.DataFrame([{"pass_yd": 250.0, "two_pt": 0.0}])
    result = _blank_unprojected_rows(stats, ["pass_yd"])
    assert result.iloc[0]["pass_yd"] == 250.0


def test_full_team_name_to_code_has_all_32_teams_with_no_duplicate_codes():
    assert len(FULL_TEAM_NAME_TO_CODE) == 32
    assert len(set(FULL_TEAM_NAME_TO_CODE.values())) == 32


def test_url_template_builds_position_and_week():
    url = URL_TEMPLATE.format(position="qb", week=3)
    assert "projections/qb.php" in url
    assert "week=3" in url
    assert "scoring=PPR" in url
