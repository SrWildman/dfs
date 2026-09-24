import pandas as pd

from dfs.player_join import (
    JoinResult,
    join_key,
    join_source_to_dk,
    match_rate_report,
    normalize_name,
    normalize_team,
)


def test_normalize_name_strips_punctuation_case_and_suffix():
    assert normalize_name("Michael Pittman Jr.") == "michael pittman"
    assert normalize_name("Odell Beckham Jr") == "odell beckham"
    assert normalize_name("A.J. Brown") == "aj brown"
    assert normalize_name("Amon-Ra St. Brown") == "amonra st brown"


def test_normalize_name_blank_and_non_string_are_empty():
    assert normalize_name("") == ""
    assert normalize_name(None) == ""
    assert normalize_name(float("nan")) == ""


def test_normalize_team_rewrites_known_aliases_only():
    assert normalize_team("LA") == "LAR"
    assert normalize_team("JAC") == "JAX"
    assert normalize_team("lac") == "LAC"  # untouched alias, just uppercased


def test_join_key_is_position_and_team_scoped_not_name_alone():
    # Same normalized name, different team/position -> different keys.
    assert join_key("Josh Allen", "BUF", "QB") != join_key("Josh Allen", "JAX", "LB")


def _dk_frame(rows: list[dict]) -> pd.DataFrame:
    return pd.DataFrame(rows)


def test_join_matches_on_name_team_position():
    dk = _dk_frame(
        [
            {"Id": "1", "Name": "Josh Allen", "Team": "BUF", "Position": "QB"},
            {"Id": "2", "Name": "Saquon Barkley", "Team": "PHI", "Position": "RB"},
        ]
    )
    source = pd.DataFrame(
        [
            {"src_name": "Josh Allen", "src_team": "BUF", "src_pos": "QB", "proj": 24.5},
            {"src_name": "Saquon Barkley", "src_team": "PHI", "src_pos": "RB", "proj": 18.2},
        ]
    )

    result = join_source_to_dk(
        dk,
        source,
        source_name_col="src_name",
        source_team_col="src_team",
        source_position_col="src_pos",
        source="test",
    )

    assert result.pool_matched == 2
    assert result.pool_total == 2
    assert set(result.matched["Id"]) == {"1", "2"}
    assert result.matched.set_index("Id").loc["1", "proj"] == 24.5


def test_join_matches_dst_by_team_only():
    dk = _dk_frame([{"Id": "9", "Name": "Texans", "Team": "HOU", "Position": "DST"}])
    source = pd.DataFrame([{"src_name": "Houston Texans", "src_team": "HOU", "src_pos": "DST", "proj": 7.5}])

    result = join_source_to_dk(
        dk,
        source,
        source_name_col="src_name",
        source_team_col="src_team",
        source_position_col="src_pos",
        source="test",
    )

    assert result.pool_matched == 1
    assert result.matched.iloc[0]["proj"] == 7.5


def test_join_reports_unmatched_pool_players_without_dropping_them_silently():
    dk = _dk_frame(
        [
            {"Id": "1", "Name": "Matched Guy", "Team": "BUF", "Position": "QB"},
            {"Id": "2", "Name": "Missed Guy", "Team": "PHI", "Position": "RB"},
        ]
    )
    source = pd.DataFrame([{"src_name": "Matched Guy", "src_team": "BUF", "src_pos": "QB", "proj": 20.0}])

    result = join_source_to_dk(
        dk,
        source,
        source_name_col="src_name",
        source_team_col="src_team",
        source_position_col="src_pos",
        source="test",
    )

    assert result.pool_matched == 1
    assert result.pool_total == 2
    assert result.unmatched_pool_names == ["Missed Guy"]


def test_join_respects_pool_mask_for_rates_and_unmatched_but_not_for_matching_itself():
    dk = _dk_frame(
        [
            {"Id": "1", "Name": "Starter", "Team": "BUF", "Position": "RB"},
            {"Id": "2", "Name": "Backup Outside Pool", "Team": "BUF", "Position": "RB"},
        ]
    )
    source = pd.DataFrame([{"src_name": "Starter", "src_team": "BUF", "src_pos": "RB", "proj": 15.0}])
    pool_mask = pd.Series([True, False])

    result = join_source_to_dk(
        dk,
        source,
        source_name_col="src_name",
        source_team_col="src_team",
        source_position_col="src_pos",
        source="test",
        pool_mask=pool_mask,
    )

    assert result.pool_total == 1
    assert result.pool_matched == 1
    assert result.unmatched_pool_names == []
    assert result.by_position == {"RB": (1, 1)}


def test_join_falls_back_to_alias_file_for_key_misses(tmp_path, monkeypatch):
    alias_file = tmp_path / "aliases.csv"
    alias_file.write_text("dk_id,source,alias_name\n1,test,Robby Anderson\n")
    monkeypatch.setattr("dfs.player_join.PLAYER_ALIASES_FILE", alias_file)

    dk = _dk_frame([{"Id": "1", "Name": "Robbie Anderson", "Team": "ARI", "Position": "WR"}])
    # Source spells the same player differently enough that the key miss --
    # the alias says "test's own name for DK Id 1 is 'Robby Anderson'".
    source = pd.DataFrame([{"src_name": "Robby Anderson", "src_team": "ARI", "src_pos": "WR", "proj": 9.5}])

    result = join_source_to_dk(
        dk,
        source,
        source_name_col="src_name",
        source_team_col="src_team",
        source_position_col="src_pos",
        source="test",
    )

    assert result.pool_matched == 1
    assert result.matched.iloc[0]["proj"] == 9.5


def test_join_by_position_breaks_out_matched_and_pool_total_per_position():
    dk = _dk_frame(
        [
            {"Id": "1", "Name": "QB One", "Team": "BUF", "Position": "QB"},
            {"Id": "2", "Name": "RB One", "Team": "BUF", "Position": "RB"},
            {"Id": "3", "Name": "RB Two Missed", "Team": "BUF", "Position": "RB"},
        ]
    )
    source = pd.DataFrame(
        [
            {"src_name": "QB One", "src_team": "BUF", "src_pos": "QB", "proj": 20.0},
            {"src_name": "RB One", "src_team": "BUF", "src_pos": "RB", "proj": 12.0},
        ]
    )

    result = join_source_to_dk(
        dk,
        source,
        source_name_col="src_name",
        source_team_col="src_team",
        source_position_col="src_pos",
        source="test",
    )

    assert result.by_position == {"QB": (1, 1), "RB": (1, 2)}


def test_match_rate_report_builds_one_row_per_source_and_position():
    result = JoinResult(
        matched=pd.DataFrame(),
        pool_total=3,
        pool_matched=2,
        by_position={"QB": (1, 1), "RB": (1, 2)},
    )
    report = match_rate_report({"sleeper": result})

    assert set(report["Position"]) == {"QB", "RB"}
    rb_row = report[report["Position"] == "RB"].iloc[0]
    assert rb_row["Matched"] == 1
    assert rb_row["Pool"] == 2
    assert rb_row["Rate"] == 50.0
