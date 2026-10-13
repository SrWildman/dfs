"""The durable contest-entry log: parsing, deduplication, season/week/slate and format tagging."""

import csv
from datetime import date, datetime
from pathlib import Path

import pandas as pd
import pytest

from dfs.contests import history

FIXTURES = Path(__file__).parent / "fixtures"
EXPORT = FIXTURES / "history_export.csv"
LATER = FIXTURES / "history_export_later.csv"


def test_the_fixture_really_has_a_bom_and_comma_grouped_numbers():
    raw = EXPORT.read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf")
    assert b'"$8,000.00"' in raw and b'"2,758"' in raw


def test_read_export_keeps_every_column_as_text_and_drops_the_bom():
    df = history.read_export(EXPORT)
    assert list(df.columns) == history.HISTORY_COLUMNS
    assert df.loc[0, "Sport"] == "NFL"  # not "﻿Sport"
    fair_catch = df[df["Entry_Key"] == "5000000001"].iloc[0]
    assert fair_catch["Contest_Entries"] == "2,758"
    assert fair_catch["Prize_Pool"] == "$8,000.00"
    assert fair_catch["Places_Paid"] == "600"


def test_money_and_count_parsers():
    assert str(history.money("$8,000.00")) == "8000.00"
    assert str(history.money("")) == "0" and str(history.money("junk")) == "0"
    assert history.count("2,758") == 2758 and history.count("") is None and history.count("x") is None
    assert history.number("1,500.5") == 1500.5 and history.number("") is None


def test_read_export_rejects_a_zero_byte_file(tmp_path):
    empty = tmp_path / "history.csv"
    empty.write_bytes(b"")
    with pytest.raises(history.HistoryError, match="empty"):
        history.read_export(empty)


def test_read_export_rejects_a_different_csv(tmp_path):
    other = tmp_path / "other.csv"
    other.write_text("a,b\n1,2\n")
    with pytest.raises(history.HistoryError, match="missing column"):
        history.read_export(other)


def test_read_export_accepts_bytes_and_file_objects():
    raw = EXPORT.read_bytes()
    import io

    assert len(history.read_export(raw)) == len(history.read_export(io.BytesIO(raw)))


@pytest.mark.parametrize(
    ("day", "season", "week"),
    [
        (date(2025, 10, 12), 2025, 6),
        (date(2025, 9, 4), 2025, 1),  # opening Thursday
        (date(2026, 1, 4), 2025, 18),  # a January game belongs to the previous season
        (date(2026, 1, 11), 2025, None),  # after week 18's Monday: the playoffs have no week
        (date(2026, 1, 17), 2025, None),
        (date(2026, 8, 20), 2025, None),  # the offseason
        (date(2026, 9, 13), 2026, 1),
        (date(2026, 10, 12), 2026, 5),  # Monday night belongs to the week it closes
    ],
)
def test_season_and_week(day, season, week):
    assert history.season_week(day) == (season, week)


def test_slate_is_main_for_sunday_afternoon_only():
    assert history.slate_of(datetime(2025, 10, 12, 13, 0)) == "main"
    assert history.slate_of(datetime(2025, 10, 12, 16, 25)) == "main"
    assert history.slate_of(datetime(2025, 10, 12, 20, 20)) == "other"  # Sunday night
    assert history.slate_of(datetime(2025, 10, 12, 9, 30)) == "other"  # early international game
    assert history.slate_of(datetime(2025, 10, 9, 20, 15)) == "other"  # Thursday
    assert history.slate_of(datetime(2025, 10, 13, 20, 15)) == "other"  # Monday


def test_merge_into_builds_the_log_with_every_column_and_the_derived_ones(tmp_path):
    log_path = tmp_path / "log.csv"
    result = history.merge_into(log_path, EXPORT)
    assert (result.added, result.updated, result.unchanged) == (31, 0, 0)
    assert result.dropped_other_sports == 1 and result.total == 31
    log = history.read_log(log_path)
    assert list(log.columns) == history.LOG_COLUMNS
    assert "NBA" not in set(log["Sport"])
    row = log[log["Entry_Key"] == "5294179404"].iloc[0]  # the example row from DraftKings
    assert (row["Game_Type"], row["Place"], row["Points"]) == ("Showdown Captain Mode", "351", "146.07")
    assert (row["Winnings_Non_Ticket"], row["Contest_Entries"], row["Prize_Pool"]) == (
        "$8.00",
        "2972",
        "$10,000.00",
    )
    assert (row["Season"], row["Week"], row["Slate"], row["Format"]) == ("2025", "6", "other", "Showdown")
    assert row["Series"] == "NFL Showdown $10K Cover Four [5 Entry Max]" and row["Matchup"] == "BUF @ LAR"


def test_merge_into_is_safe_to_rerun(tmp_path):
    log_path = tmp_path / "log.csv"
    history.merge_into(log_path, EXPORT)
    first = log_path.read_bytes()
    again = history.merge_into(log_path, EXPORT)
    assert (again.added, again.updated, again.unchanged) == (0, 0, 31)
    assert log_path.read_bytes() == first  # not even rewritten
    assert not list(tmp_path.glob("*.tmp"))


def test_overlapping_exports_dedupe_by_entry_key_and_the_later_export_wins(tmp_path):
    log_path = tmp_path / "log.csv"
    history.merge_into(log_path, EXPORT)
    result = history.merge_into(log_path, LATER)
    assert (result.added, result.updated, result.unchanged) == (1, 1, 30)
    log = history.read_log(log_path)
    assert log["Entry_Key"].is_unique and len(log) == 32
    updated = log[log["Entry_Key"] == "5000000005"].iloc[0]
    assert (updated["Place"], updated["Points"]) == ("400", "165.00")


def test_the_export_merged_last_wins(tmp_path):
    """`merge_into(log, export)` means "this export is the newer one": merging an older export second would
    overwrite a final result with a mid-flight one, so exports are merged oldest first (the order given)."""
    newer_first = tmp_path / "a.csv"
    history.merge_into(newer_first, LATER)
    history.merge_into(newer_first, EXPORT)
    place = history.read_log(newer_first).set_index("Entry_Key").loc["5000000005", "Place"]
    assert place == "620"


def test_log_is_sorted_by_contest_date(tmp_path):
    log_path = tmp_path / "log.csv"
    history.merge_into(log_path, EXPORT)
    dates = pd.to_datetime(history.read_log(log_path)["Contest_Date_EST"])
    assert dates.is_monotonic_increasing


def test_season_boundaries_in_the_fixture(tmp_path):
    log_path = tmp_path / "log.csv"
    history.merge_into(log_path, EXPORT)
    log = history.read_log(log_path).set_index("Entry_Key")
    assert (log.loc["5000000023", "Season"], log.loc["5000000023", "Week"]) == ("2025", "18")  # 2026-01-04
    assert (log.loc["5000000020", "Season"], log.loc["5000000020", "Week"]) == ("2025", "")  # 2026-01-17
    assert (log.loc["5000000024", "Season"], log.loc["5000000024", "Week"]) == ("2026", "1")
    assert log.loc["5000000025", "Slate"] == "other"  # a Thursday-night Classic


def test_formats_in_the_fixture_log(tmp_path):
    log_path = tmp_path / "log.csv"
    history.merge_into(log_path, EXPORT)
    log = history.read_log(log_path).set_index("Entry_Key")
    assert log.loc["5000000018", "Format"] == "Other"  # the Single Stat contest the band called cash
    assert log.loc["5000000018", "Bankroll_Class"] == "gpp" == log.loc["5000000018", "Format_Class"]
    assert (
        log.loc["5000000010", "Opponent"] == "rival_one" and log.loc["5000000011", "Opponent"] == "rival_one"
    )
    assert log.loc["5000000015", "Series"] == "DREAMERS DFS by bradikal36"


def test_typed_log_numbers(tmp_path):
    log_path = tmp_path / "log.csv"
    history.merge_into(log_path, EXPORT)
    typed = history.typed(history.read_log(log_path)).set_index("entry_key")
    fair = typed.loc["5000000001"]
    assert (fair["entries"], fair["places_paid"], fair["place"]) == (2758, 600, 87)
    assert fair["fee"] == 20.0 and fair["winnings"] == 40.0 and fair["prize_pool"] == 8000.0
    assert fair["payout_ratio"] == pytest.approx(600 / 2758)
    assert fair["season"] == 2025 and fair["week"] == 6 and fair["slate"] == "main"
    assert "5000000099" not in typed.index  # only in the later export


def test_ticket_winnings_are_added(tmp_path):
    src = tmp_path / "t.csv"
    header = history.HISTORY_COLUMNS
    row = [
        "NFL",
        "Classic",
        "1",
        "NFL $2 Double Up",
        "9",
        "2025-10-12 13:00:00",
        "5",
        "150",
        "$1.00",
        "$2.50",
    ]
    row += ["10", "$2.00", "$18.00", "4"]
    with src.open("w", encoding="utf-8-sig", newline="") as handle:
        csv.writer(handle).writerows([header, row])
    log_path = tmp_path / "l.csv"
    history.merge_into(log_path, src)
    assert history.typed(history.read_log(log_path)).loc[0, "winnings"] == 3.5


def test_rows_without_an_entry_key_are_dropped_and_counted(tmp_path):
    src = tmp_path / "t.csv"
    with src.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(history.HISTORY_COLUMNS)
        writer.writerow(
            ["NFL", "Classic", "", "NFL $2 Double Up", "9", "2025-10-12 13:00:00", "5", "150"] + [""] * 6
        )
    result = history.merge_into(tmp_path / "l.csv", src)
    assert (result.added, result.dropped_no_key, result.total) == (0, 1, 0)


def test_an_unparseable_date_leaves_season_week_blank(tmp_path):
    src = tmp_path / "t.csv"
    with src.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(history.HISTORY_COLUMNS)
        writer.writerow(["NFL", "Classic", "7", "NFL $2 Double Up", "9", "not a date", "5", "150"] + [""] * 6)
    history.merge_into(tmp_path / "l.csv", src)
    row = history.read_log(tmp_path / "l.csv").iloc[0]
    assert (row["Season"], row["Week"], row["Slate"]) == ("", "", "")


def test_read_log_on_a_missing_file_is_an_empty_frame(tmp_path):
    empty = history.read_log(tmp_path / "nope.csv")
    assert list(empty.columns) == history.LOG_COLUMNS and empty.empty


def test_a_malformed_csv_is_a_history_error_not_a_traceback():
    header = ",".join(history.HISTORY_COLUMNS)
    good = "NFL,Classic,1,NFL $2 Double Up,9,2025-10-12 13:00:00,5,150,$1,$0,10,$2,$18,4"
    too_many_fields = good + ",EXTRA,FIELDS,HERE"  # an unquoted comma in a money field looks like this
    with pytest.raises(history.HistoryError, match="could not read"):
        history.read_export(f"{header}\n{good}\n{too_many_fields}\n".encode())
    with pytest.raises(history.HistoryError, match="could not read"):
        history.read_export(b"\xff\xfe\x00bad bytes")


def test_a_column_draftkings_adds_later_is_kept_verbatim(tmp_path):
    src = tmp_path / "t.csv"
    with src.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow([*history.HISTORY_COLUMNS, "Brand_New_Column"])
        row = [
            "NFL",
            "Classic",
            "7",
            "NFL $2 Double Up",
            "9",
            "2025-10-12 13:00:00",
            "5",
            "150",
            "$1.00",
            "$0.00",
        ]
        writer.writerow([*row, "10", "$2.00", "$18.00", "4", "surprise"])
    log_path = tmp_path / "l.csv"
    history.merge_into(log_path, src)
    log = history.read_log(log_path)
    assert list(log.columns) == [*history.HISTORY_COLUMNS, "Brand_New_Column", *history.DERIVED_COLUMNS]
    assert log.loc[0, "Brand_New_Column"] == "surprise" and log.loc[0, "Format"] == "Double Up"
    # an older export without the column merges into it, leaving the column blank for those rows
    history.merge_into(log_path, EXPORT)
    merged = history.read_log(log_path)
    assert len(merged) == 32 and (merged["Brand_New_Column"] == "").sum() == 31
    assert history.merge_into(log_path, src).unchanged == 1


# ---- merge rules (found in review) ---------------------------------------------------------------


def test_a_changed_rule_relabels_the_log_on_the_next_merge(tmp_path, monkeypatch):
    log_path = tmp_path / "log.csv"
    history.merge_into(log_path, EXPORT)
    before = history.read_log(log_path).set_index("Entry_Key").loc["5000000001", "Format"]
    assert before == "SE GPP"
    real = history.series_mod.classify

    def reclassified(*args, **kwargs):
        return real(*args, **kwargs).__class__(**{**real(*args, **kwargs).__dict__, "format": "Other"})

    monkeypatch.setattr(history.series_mod, "classify", reclassified)
    result = history.merge_into(log_path, EXPORT)  # the same export: nothing added or updated ...
    assert (result.added, result.updated) == (0, 0)
    assert (
        history.read_log(log_path).set_index("Entry_Key").loc["5000000001", "Format"] == "Other"
    )  # ... but relabelled


def test_a_contest_still_in_play_never_overwrites_a_result(tmp_path):
    log_path = tmp_path / "log.csv"
    history.merge_into(log_path, EXPORT)
    stale = tmp_path / "stale.csv"
    df = history.read_export(EXPORT)
    df.loc[df["Entry_Key"] == "5000000001", ["Place", "Points"]] = ["", "12.50"]  # mid-flight: no place yet
    df.to_csv(stale, index=False)
    result = history.merge_into(log_path, stale)
    assert result.updated == 0 and result.unchanged == 31
    row = history.read_log(log_path).set_index("Entry_Key").loc["5000000001"]
    assert (row["Place"], row["Points"]) == ("87", "181.20")


def test_a_column_only_the_log_has_survives_a_newer_export_without_it(tmp_path):
    older = tmp_path / "older.csv"
    df = history.read_export(EXPORT)
    df["Retired_Column"] = "keep me"
    df.to_csv(older, index=False)
    log_path = tmp_path / "log.csv"
    history.merge_into(log_path, older)
    result = history.merge_into(log_path, LATER)  # DraftKings no longer sends Retired_Column
    log = history.read_log(log_path)
    assert result.updated == 1 and result.added == 1  # only the genuinely changed and the new row
    assert (log.set_index("Entry_Key").loc["5000000001", "Retired_Column"]) == "keep me"
    assert log.set_index("Entry_Key").loc["5000000099", "Retired_Column"] == ""
