"""The close of a week with a Thanksgiving slate (`dfs.slate_close`): both slates' money is booked once each,
and the main slate's learning (Results, the standings every study reads) never sees Thanksgiving's."""

from __future__ import annotations

from datetime import UTC, datetime

import pandas as pd

from dfs import entries as ent
from dfs import paths, slate, slate_close, store
from dfs.bankroll import classify_entry, entries_for_week, parse_contest_history
from dfs.config import Config
from dfs.contests import capture
from dfs.results_autofill import compute_week_results

HEADER = (
    "Sport,Game_Type,Entry_Key,Entry,Contest_Key,Contest_Date_EST,Place,Points,Winnings_Non_Ticket,"
    "Winnings_Ticket,Contest_Entries,Entry_Fee,Prize_Pool,Places_Paid"
)
ROWS = [
    # Thanksgiving (Thursday Nov 26): a double-up and a GPP, Classic
    "NFL,Classic,E1,NFL $5 Double Up,7001,2026-11-26 12:30:00,10,120.5,$10.00,$0.00,100,$5.00,$450.00,50",
    "NFL,Classic,E2,NFL $3 Thanksgiving Special,7002,2026-11-26 12:30:00,900,110.0,$0.00,$0.00,5000,$3.00,"
    '"$13,000.00",1000',
    # Thanksgiving night's Showdown: not Classic, so not a secondary slate's (as today)
    "NFL,Showdown Captain Mode,E3,NFL Showdown $5,7003,2026-11-26 20:20:00,40,60.0,$0.00,$0.00,100,$5.00,"
    "$450.00,20",
    # the Sunday main slate: a double-up and an H2H
    "NFL,Classic,E4,NFL $5 Double Up,7101,2026-11-29 13:00:00,20,140.25,$10.00,$0.00,100,$5.00,$450.00,50",
    "NFL,Classic,E5,NFL $10 Head-to-Head vs. someone,7102,2026-11-29 13:00:00,1,140.25,$18.00,$0.00,2,$10.00,"
    "$18.00,1",
    # last week's Sunday: another week, untouched
    "NFL,Classic,E6,NFL $5 Double Up,6101,2026-11-22 13:00:00,20,131.0,$10.00,$0.00,100,$5.00,$450.00,50",
]


def _history(tmp_path):
    path = tmp_path / "draftkings-contest-entry-history.csv"
    path.write_text(HEADER + "\n" + "\n".join(ROWS) + "\n")
    return path


def _cfg(**slates) -> Config:
    return Config.model_validate(
        {
            "google_sheets": {"sheet_id": "MAIN", "credentials_file": "c.json", "tab_mappings": {}},
            "slates": {k: {"sheet_id": v} for k, v in slates.items()},
        }
    )


def test_the_close_books_both_slates_money_once_each(tmp_path):
    entries = parse_contest_history(pd.read_csv(_history(tmp_path)))
    week = entries_for_week(entries, 12, 2026)
    keys = [e.entry_key for e in week]
    assert sorted(keys) == ["E1", "E2", "E3", "E4", "E5"]  # both slates, each once
    assert sum(float(e.entry_fee) for e in week) == 28.0
    cash = sorted(e.entry_key for e in week if classify_entry(e) == "cash")
    assert cash == ["E1", "E4", "E5"]  # Thanksgiving's double-up is booked in the Cash ledger too


def test_results_is_the_main_slates_record(tmp_path):
    entries = parse_contest_history(pd.read_csv(_history(tmp_path)))
    cfg = _cfg(thu="THU")
    kept = slate_close.main_slate_entries(entries, cfg, 2026, 12)
    assert sorted(e.entry_key for e in kept) == ["E3", "E4", "E5", "E6"]
    results = compute_week_results(kept, 2026)
    assert results[12].cash_pts == 140.25  # Sunday's double-up, never Thanksgiving's 120.5
    assert results[11].cash_pts == 131.0
    # with no secondary slate configured nothing is filtered: the close is exactly what it was
    assert slate_close.main_slate_entries(entries, _cfg(), 2026, 12) == list(entries)


def test_thanksgiving_contests_are_found_from_the_history(tmp_path):
    csv = _history(tmp_path)
    assert slate_close.secondary_contests(csv, ["thu"]) == {"7001": "thu", "7002": "thu"}
    assert slate_close.secondary_contests(csv, []) == {}
    assert slate_close.standings_divert(_cfg(), csv) is None


def _standings(folder, contest_id):
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"contest-standings-{contest_id}.csv"
    path.write_text("Rank,EntryId,EntryName,TimeRemaining,Points,Lineup\n1,1,someone,0,150,QB X\n")
    return path


def test_thanksgiving_standings_are_kept_apart_from_the_main_slates(tmp_path):
    csv = _history(tmp_path)
    downloads, dest = tmp_path / "Downloads", tmp_path / "dk_standings"
    _standings(downloads, "7001")
    _standings(downloads, "7101")
    got = capture.capture_standings(downloads, dest, slate_close.standings_divert(_cfg(thu="THU"), csv))
    assert [p.name for p in dest.iterdir()] == ["contest-standings-7101.csv"]
    apart = paths.DATA_DIR / "slates" / "thu" / "raw" / "dk_standings"
    assert [p.name for p in apart.iterdir()] == ["contest-standings-7001.csv"]
    assert got.new_ids == ["7101"]
    assert any("kept apart" in line for line in capture.describe(got))
    lines = slate_close.close_lines(_cfg(thu="THU"), csv, 2026, 12)
    assert len(lines) == 1
    assert "2 entries" in lines[0] and "$8.00 in fees" in lines[0] and "1 file(s)" in lines[0]
    assert slate_close.close_lines(_cfg(thu="THU"), csv, 2026, 11) == []


def test_without_a_secondary_slate_capture_is_unchanged(tmp_path):
    downloads, dest = tmp_path / "Downloads", tmp_path / "dk_standings"
    _standings(downloads, "7001")
    _standings(downloads, "7101")
    got = capture.capture_standings(downloads, dest)
    assert sorted(p.name for p in dest.iterdir()) == [
        "contest-standings-7001.csv",
        "contest-standings-7101.csv",
    ]
    assert sorted(got.new_ids) == ["7001", "7101"]


def test_a_thursday_sync_never_reaches_the_snapshots_the_scorecards_read():
    with slate.active("thu"):
        store.save("draftkings", pd.DataFrame({"Name": ["Jared Goff"]}))
        store.save("edge", pd.DataFrame({"Name": ["Jared Goff"]}))
    assert not (paths.RAW_DIR / "draftkings").exists()
    assert not (paths.RAW_DIR / "edge").exists()


def _player(kickoff: datetime) -> ent.TablePlayer:
    return ent.TablePlayer("QB", "Jared Goff", "1", "QB", 6500, "GB@DET", "DET", kickoff)


def test_an_entries_file_belongs_to_its_slate():
    thursday = [
        _player(datetime(2026, 11, 26, 17, 30, tzinfo=UTC)),
        _player(datetime(2026, 11, 27, 1, 20, tzinfo=UTC)),
    ]
    sunday = [_player(datetime(2026, 11, 29, 18, 0, tzinfo=UTC))]
    assert not ent.is_slate_file(thursday)  # on main: another slate, as today
    assert ent.is_slate_file(sunday)
    with slate.active("thu"):
        assert ent.is_slate_file(thursday)  # 8:20 pm ET is still Thursday
        assert not ent.is_slate_file(sunday)
