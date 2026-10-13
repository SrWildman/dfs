"""Finding and keeping the two DraftKings downloads: by header, copies, moves, empties, zips."""

import os
import zipfile
from pathlib import Path

from dfs.contests import capture

FIXTURES = Path(__file__).parent / "fixtures"
HISTORY = FIXTURES / "history_export.csv"
STANDINGS = FIXTURES / "standings"


def put(folder: Path, name: str, data: bytes, mtime: int | None = None) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / name
    path.write_bytes(data)
    if mtime is not None:
        os.utime(path, (mtime, mtime))
    return path


def test_a_history_export_is_recognised_by_its_header_not_its_name(tmp_path):
    put(tmp_path, "whatever it was called (3).csv", HISTORY.read_bytes())
    put(tmp_path, "salaries.csv", b"Position,Name,Salary\nQB,A,5000\n")
    put(tmp_path, "empty.csv", b"")
    found = capture.find_history_export(tmp_path)
    assert found is not None and found.name == "whatever it was called (3).csv"


def test_the_newest_history_export_wins(tmp_path):
    put(tmp_path, "old.csv", HISTORY.read_bytes(), mtime=1_700_000_000)
    put(tmp_path, "new.csv", HISTORY.read_bytes(), mtime=1_800_000_000)
    assert capture.find_history_export(tmp_path).name == "new.csv"


def test_no_history_export_in_the_folder_is_none_and_a_missing_folder_is_none(tmp_path):
    put(tmp_path, "notes.csv", b"a,b\n1,2\n")
    assert capture.find_history_export(tmp_path) is None
    assert capture.find_history_export(tmp_path / "nope") is None


def test_the_export_is_copied_once_and_a_second_run_finds_the_copy(tmp_path):
    source = put(tmp_path / "dl", "draftkings-contest-entry-history.csv", HISTORY.read_bytes())
    dest = tmp_path / "kept"
    first, new = capture.keep_history_copy(source, dest)
    assert new and first.parent == dest and first.read_bytes() == HISTORY.read_bytes()
    assert first.name.startswith(
        "draftkings-contest-entry-history-20"
    )  # dated: the next export will not collide
    again, new_again = capture.keep_history_copy(source, dest)
    assert not new_again and again == first
    assert len(list(dest.glob("*.csv"))) == 1


def test_a_different_export_with_the_same_name_is_kept_beside_the_first_never_over_it(tmp_path):
    dest = tmp_path / "kept"
    a = put(tmp_path / "dl", "history-2026-10-13.csv", HISTORY.read_bytes())
    first, _ = capture.keep_history_copy(a, dest)
    b = put(tmp_path / "dl2", "history-2026-10-13.csv", HISTORY.read_bytes() + b"\n")
    second, new = capture.keep_history_copy(b, dest)
    assert new and second != first and first.read_bytes() == HISTORY.read_bytes()


def test_new_standings_are_moved_out_of_downloads_and_an_empty_one_is_reported_and_left(tmp_path):
    downloads, kept = tmp_path / "dl", tmp_path / "kept"
    put(
        downloads,
        "contest-standings-900000001.csv",
        (STANDINGS / "contest-standings-900000001.csv").read_bytes(),
    )
    put(downloads, "contest-standings-900000005.csv", b"")
    put(downloads, "unrelated.csv", b"a,b\n")
    report = capture.capture_standings(downloads, kept)
    assert report.new_ids == ["900000001"]
    assert (kept / "contest-standings-900000001.csv").exists()
    assert not (downloads / "contest-standings-900000001.csv").exists()
    assert [p.name for p in report.empty] == ["contest-standings-900000005.csv"]
    assert (downloads / "contest-standings-900000005.csv").exists()  # an empty download is left where it is
    assert (downloads / "unrelated.csv").exists()
    text = "\n".join(capture.describe(report))
    assert "EMPTY" in text and "two weeks" in text


def test_a_contest_already_kept_is_not_captured_twice(tmp_path):
    downloads, kept = tmp_path / "dl", tmp_path / "kept"
    data = (STANDINGS / "contest-standings-900000001.csv").read_bytes()
    put(kept, "contest-standings-900000001.csv", data)
    put(downloads, "contest-standings-900000001 (1).csv", data)
    report = capture.capture_standings(downloads, kept)
    assert not report.moved and [ids for _, ids in report.already] == [["900000001"]]
    assert (downloads / "contest-standings-900000001 (1).csv").exists()


def test_a_zip_of_standings_is_moved_whole_whatever_it_is_called(tmp_path):
    downloads, kept = tmp_path / "dl", tmp_path / "kept"
    downloads.mkdir()
    with zipfile.ZipFile(downloads / "NFL GIANT Contest Standings (2).zip", "w") as archive:
        archive.writestr(
            "contest-standings-900000002.csv", (STANDINGS / "contest-standings-900000002.csv").read_bytes()
        )
    report = capture.capture_standings(downloads, kept)
    assert report.new_ids == ["900000002"]
    assert (kept / "NFL GIANT Contest Standings (2).zip").exists()
    assert capture.standings_bytes(kept / "NFL GIANT Contest Standings (2).zip")[0][0] == "900000002"


def test_a_zip_holding_only_empty_files_is_an_empty_download(tmp_path):
    downloads = tmp_path / "dl"
    downloads.mkdir()
    with zipfile.ZipFile(downloads / "x.zip", "w") as archive:
        archive.writestr("contest-standings-900000009.csv", b"")
    report = capture.capture_standings(downloads, tmp_path / "kept")
    assert [p.name for p in report.empty] == ["x.zip"] and not report.moved


def test_an_empty_downloads_folder_says_so_and_names_the_two_week_limit(tmp_path):
    report = capture.capture_standings(tmp_path / "nope", tmp_path / "kept")
    text = "\n".join(capture.describe(report))
    assert "no standings files" in text and "Monday" in text
