import pytest

from dfs.sources.tffb_projections import (
    OPTIMIZER_URL_TEMPLATE,
    TffbProjectionsFetchError,
    TffbProjectionsSource,
    _dst_nickname,
    players_to_df,
)


def _player(**overrides):
    base = {
        "Id": "43727325",
        "Name": "Jahmyr Gibbs",
        "Position": "RB",
        "TeamAbbrev": "DET",
        "ProjPts": 23.3,
        "ProjOwn": 0,
        "Opp": "NO",
        "Salary": 8000,
        "Ceiling": 33.29,
        "ImpPts": 28.5,
        "OU": 46.5,
        "Spread": -10.5,
        "Game": "Detroit Lions_New Orleans Saints",
        "GameStart": "2026-09-13T17:00:00Z",
        "Venue": "H",
    }
    base.update(overrides)
    return base


DST_PLAYER = _player(
    Id="43728525",
    Name="Los Angeles Chargers",
    Position="DST",
    TeamAbbrev="LAC",
    ProjPts=9.3,
    Opp="ARI",
)


def test_players_to_df_keeps_core_columns_in_order():
    df = players_to_df([_player()])
    assert list(df.columns[:6]) == ["Id", "Name", "Position", "Team", "ProjPts", "ProjOwn"]
    assert df.iloc[0]["Team"] == "DET"


def test_players_to_df_appends_extra_fields_after_core_six():
    df = players_to_df([_player()])
    assert "Salary" in df.columns
    assert "Ceiling" in df.columns
    assert list(df.columns).index("Salary") >= 6


def test_players_to_df_raises_on_empty():
    with pytest.raises(TffbProjectionsFetchError, match="no players"):
        players_to_df([])


def test_players_to_df_raises_on_missing_core_field():
    bad = _player()
    del bad["ProjPts"]
    with pytest.raises(TffbProjectionsFetchError, match="missing"):
        players_to_df([bad])


def test_players_to_df_tolerates_missing_extra_field():
    bad = _player()
    del bad["Ceiling"]
    df = players_to_df([bad])
    assert "Ceiling" not in df.columns
    assert "Salary" in df.columns


def test_dst_nickname_takes_last_word():
    assert _dst_nickname("Los Angeles Chargers") == "Chargers"
    assert _dst_nickname("San Francisco 49ers") == "49ers"
    assert _dst_nickname("Denver Broncos") == "Broncos"


def test_to_sheet_rows_rewrites_dst_team_to_nickname_only():
    df = players_to_df([_player(), DST_PLAYER])
    rows = TffbProjectionsSource().to_sheet_rows(df)
    header, gibbs, chargers = rows
    assert header[:6] == ["Id", "Name", "Position", "Team", "ProjPts", "ProjOwn"]
    assert gibbs[3] == "DET"  # non-DST untouched
    assert chargers[1] == "Los Angeles Chargers"  # Name left as the full team name
    assert chargers[3] == "Chargers"  # Team rewritten to match DK's DST naming


def test_url_template_uses_season_for_product_year():
    assert OPTIMIZER_URL_TEMPLATE.format(season=2026) == (
        "https://www.thefantasyfootballers.com/2026-ultimate-dfs-pass/"
        "dfs-pass-lineup-optimizer/"
    )
