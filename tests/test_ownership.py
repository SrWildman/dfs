from io import StringIO

import pandas as pd

from dfs.ownership import already_logged_contest_ids, append_ownership, parse_ownership_export

# Mirrors the real shape confirmed live 2026-09-23 against a real DK
# "export full standings" CSV: columns A-F are an unrelated entry
# leaderboard, column G is blank, H-K are per-player ownership sharing
# the same row numbers by coincidence of the export format. A player
# used in more than one roster-slot type across the field (Bijan Robinson
# here, RB and FLEX) gets one row per slot type, each a partial share.
_SAMPLE_CSV = """Rank,EntryId,EntryName,TimeRemaining,Points,Lineup,,Player,Roster Position,%Drafted,FPTS
1,111,alice,0,185.66,some lineup text,,Bijan Robinson,RB,51.00%,11.1
2,112,bob,0,173.36,some lineup text,,Dalton Schultz,TE,45.00%,29
3,113,carol,0,170.46,some lineup text,,Bijan Robinson,FLEX,4.00%,11.1
,,,,,,,Bears ,DST,1.00%,7
,,,,,,,Derrick Henry,RB,32.00%,17.7
"""


def _sample_df() -> pd.DataFrame:
    return pd.read_csv(StringIO(_SAMPLE_CSV))


def test_parse_ownership_export_sums_a_players_split_roster_slot_rows():
    result = parse_ownership_export(_sample_df())
    bijan = result[result["player"] == "Bijan Robinson"].iloc[0]
    assert bijan["pct_drafted"] == 0.55  # 0.51 + 0.04
    assert bijan["dk_position"] == "RB"  # real position preferred over FLEX


def test_parse_ownership_export_keeps_a_single_row_player_as_is():
    result = parse_ownership_export(_sample_df())
    dalton = result[result["player"] == "Dalton Schultz"].iloc[0]
    assert dalton["pct_drafted"] == 0.45
    assert dalton["fpts"] == 29
    assert dalton["dk_position"] == "TE"


def test_parse_ownership_export_strips_trailing_whitespace_from_dst_names():
    result = parse_ownership_export(_sample_df())
    assert "Bears" in result["player"].tolist()
    assert "Bears " not in result["player"].tolist()


def test_parse_ownership_export_counts_real_entries_not_player_rows():
    result = parse_ownership_export(_sample_df())
    # 3 real entries (Rank 1-3); 5 player-ownership rows -- the two
    # tables are independent, sharing row numbers only by coincidence.
    assert (result["contest_entries"] == 3).all()


def test_parse_ownership_export_raises_on_a_genuinely_different_shape():
    bad = pd.DataFrame({"Not": ["a"], "Real": ["shape"]})
    try:
        parse_ownership_export(bad)
        raise AssertionError("expected KeyError")
    except KeyError:
        pass


def test_append_ownership_writes_a_fresh_log(tmp_path, monkeypatch):
    log_file = tmp_path / "ownership_log.csv"
    monkeypatch.setattr("dfs.ownership.OWNERSHIP_LOG_FILE", log_file)
    monkeypatch.setattr("dfs.paths.ensure_data_dirs", lambda: None)

    rows = parse_ownership_export(_sample_df())
    written = append_ownership(rows, season=2026, week=3, contest_id="195860733")

    assert written == len(rows)
    on_disk = pd.read_csv(log_file, dtype={"contest_id": str})
    assert set(on_disk["contest_id"]) == {"195860733"}
    assert set(on_disk["week"]) == {3}


def test_append_ownership_replaces_rather_than_duplicates_on_rerun(tmp_path, monkeypatch):
    log_file = tmp_path / "ownership_log.csv"
    monkeypatch.setattr("dfs.ownership.OWNERSHIP_LOG_FILE", log_file)
    monkeypatch.setattr("dfs.paths.ensure_data_dirs", lambda: None)

    rows = parse_ownership_export(_sample_df())
    append_ownership(rows, season=2026, week=3, contest_id="195860733")
    append_ownership(rows, season=2026, week=3, contest_id="195860733")

    on_disk = pd.read_csv(log_file, dtype={"contest_id": str})
    assert len(on_disk) == len(rows)  # not doubled


def test_append_ownership_keeps_other_contests_rows_intact(tmp_path, monkeypatch):
    log_file = tmp_path / "ownership_log.csv"
    monkeypatch.setattr("dfs.ownership.OWNERSHIP_LOG_FILE", log_file)
    monkeypatch.setattr("dfs.paths.ensure_data_dirs", lambda: None)

    rows = parse_ownership_export(_sample_df())
    append_ownership(rows, season=2026, week=3, contest_id="111")
    append_ownership(rows, season=2026, week=4, contest_id="222")

    on_disk = pd.read_csv(log_file, dtype={"contest_id": str})
    assert set(on_disk["contest_id"]) == {"111", "222"}


def test_already_logged_contest_ids_empty_when_no_log_yet(tmp_path, monkeypatch):
    monkeypatch.setattr("dfs.ownership.OWNERSHIP_LOG_FILE", tmp_path / "nope.csv")
    assert already_logged_contest_ids() == set()


def test_already_logged_contest_ids_reflects_whats_on_disk(tmp_path, monkeypatch):
    log_file = tmp_path / "ownership_log.csv"
    monkeypatch.setattr("dfs.ownership.OWNERSHIP_LOG_FILE", log_file)
    monkeypatch.setattr("dfs.paths.ensure_data_dirs", lambda: None)

    rows = parse_ownership_export(_sample_df())
    append_ownership(rows, season=2026, week=3, contest_id="195860733")

    assert already_logged_contest_ids() == {"195860733"}
