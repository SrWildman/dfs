import pytest

from dfs.sources.nflverse_snaps import NflverseSnapsFetchError, parse_snap_counts

CSV_HEADER = "pfr_player_id,player,team,position,week,offense_snaps,offense_pct"


def _csv(*rows: str) -> str:
    return "\n".join([CSV_HEADER, *rows])


def test_parse_snap_counts_renames_to_dk_facing_columns():
    csv = _csv("AbduAm00,Ameer Abdullah,JAX,RB,1,10,0.25")
    df = parse_snap_counts(csv)
    row = df.iloc[0]
    assert row["Name"] == "Ameer Abdullah"
    assert row["Team"] == "JAX"
    assert row["Position"] == "RB"
    assert row["Snap%"] == 0.25


def test_parse_snap_counts_takes_each_players_own_latest_week_not_a_global_one():
    # Confirmed live: a Thursday-night game can already have a later week
    # than the rest of the slate -- each player's OWN latest row wins,
    # not a single global "current week minus one" cutoff.
    csv = _csv(
        "AbduAm00,Ameer Abdullah,JAX,RB,1,10,0.10",
        "AbduAm00,Ameer Abdullah,JAX,RB,2,20,0.50",
        "OtherPl01,Other Player,DAL,WR,1,5,0.05",
    )
    df = parse_snap_counts(csv).set_index("Name")
    assert df.loc["Ameer Abdullah", "Snap%"] == 0.50
    assert df.loc["Other Player", "Snap%"] == 0.05


def test_parse_snap_counts_keeps_a_real_recorded_zero():
    # A real DNP/inactive week is a known fact, not missing data -- stays
    # a real 0.0, not blanked (blanking a truly-absent player is the
    # join's job upstream, not this function's).
    csv = _csv("BenchPl01,Bench Player,NYJ,QB,1,0,0.0")
    df = parse_snap_counts(csv)
    assert df.iloc[0]["Snap%"] == 0.0


def test_parse_snap_counts_raises_on_missing_required_column():
    csv = "player,team,position,week,offense_snaps\nA,DET,RB,1,10"
    with pytest.raises(NflverseSnapsFetchError, match="missing expected column"):
        parse_snap_counts(csv)


def test_parse_snap_counts_raises_on_empty_file():
    with pytest.raises(NflverseSnapsFetchError, match="no rows"):
        parse_snap_counts(CSV_HEADER)
