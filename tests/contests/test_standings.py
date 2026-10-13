"""Standings files: bare CSV, zip, 0-byte downloads, the two tables of different lengths, ties, lineups."""

import zipfile
from pathlib import Path

import pandas as pd
import pytest

from dfs.contests import standings as S

DIR = Path(__file__).parent / "fixtures" / "standings"
SMALL = DIR / "contest-standings-900000001.csv"  # 10 entries, player table longer than the entries
BIG = DIR / "contest-standings-900000002.csv"  # 12 entries, player table of 5 rows, 9-field entry rows
MESSY = DIR / "contest-standings-900000003.csv"  # ties, counters, three lineups that do not parse
SHOWDOWN = DIR / "contest-standings-900000004.csv"
EMPTY = DIR / "contest-standings-900000005.csv"
ZIP = DIR / "NFL GIANT Contest Standings (2).zip"


def parse(path, places_paid=4, contest_id=None):
    return S.parse_standings(
        path.read_bytes(), contest_id=contest_id or path.stem.split("-")[-1], places_paid=places_paid
    )


# ---- lineups -------------------------------------------------------------------------------------


def test_the_example_lineup_from_the_export_parses_into_nine_slots():
    text = (
        "DST Jets  FLEX Braelon Allen QB Jordan Love RB Jaylen Warren RB D'Andre Swift TE Sam LaPorta "
        "WR Christian Watson WR Michael Wilson WR Matthew Golden"
    )
    kind, slots, reason = S.parse_lineup(text)
    assert (kind, reason) == ("Classic", "")
    assert slots == (
        ("DST", "Jets"),
        ("FLEX", "Braelon Allen"),
        ("QB", "Jordan Love"),
        ("RB", "Jaylen Warren"),
        ("RB", "D'Andre Swift"),
        ("TE", "Sam LaPorta"),
        ("WR", "Christian Watson"),
        ("WR", "Michael Wilson"),
        ("WR", "Matthew Golden"),
    )


@pytest.mark.parametrize(
    "name",
    ["Aaron Jones Sr.", "Amon-Ra St. Brown", "C.J. Stroud", "Ja'Marr Chase", "Marvin Harrison Jr.", "49ers"],
)
def test_names_with_punctuation_suffixes_and_digits_survive(name):
    text = f"DST Jets  FLEX {name} QB A RB B RB C TE D WR E WR F WR G"
    kind, slots, _ = S.parse_lineup(text)
    assert kind == "Classic" and ("FLEX", name) in slots


def test_the_dst_nickname_and_its_two_spaces_do_not_leak_into_names():
    _, slots, _ = S.parse_lineup("DST 49ers  FLEX A QB B RB C RB D TE E WR F WR G WR H")
    assert slots[0] == ("DST", "49ers") and slots[1] == ("FLEX", "A")


def test_a_showdown_lineup_parses_as_showdown():
    kind, slots, _ = S.parse_lineup("CPT Josh Allen  FLEX A FLEX B FLEX C FLEX D FLEX E")
    assert kind == "Showdown" and slots[0] == ("CPT", "Josh Allen") and len(slots) == 6


@pytest.mark.parametrize(
    ("text", "reason"),
    [
        ("DST Jets  FLEX A QB B RB C RB D TE E WR F WR G", "8 slots"),  # one WR short
        ("DST Jets  FLEX A QB B RB C RB D TE E WR F WR G WR H WR I", "10 slots"),
        ("", "blank"),
        (None, "blank"),
        (float("nan"), "blank"),
        ("Josh Allen RB Derrick Henry", "text before the first slot"),
        ("DST  FLEX A QB B RB C RB D TE E WR F WR G WR H", "has no player"),
        ("CPT A FLEX B FLEX C", "not a Classic"),
        ("QB A QB B RB C RB D TE E WR F WR G WR H FLEX I", "not a Classic"),  # no DST, two QBs
    ],
)
def test_a_lineup_that_does_not_parse_is_reported_never_guessed(text, reason):
    kind, slots, why = S.parse_lineup(text)
    assert slots is None and kind == "" and reason in why


def test_a_lowercase_te_in_a_name_is_not_a_slot():
    _, slots, _ = S.parse_lineup("DST Jets  FLEX Te'o Smith QB B RB C RB D TE E WR F WR G WR H")
    assert ("FLEX", "Te'o Smith") in slots


# ---- entry names ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("someuser (1/3)", ("someuser", 1, 3)),
        ("someuser(12/150)", ("someuser", 12, 150)),
        ("someuser", ("someuser", None, None)),
        ("  padded  ", ("padded", None, None)),
        (None, ("", None, None)),
        (float("nan"), ("", None, None)),
    ],
)
def test_entry_name_counter(raw, expected):
    assert S.split_entry_name(raw) == expected


# ---- the files -----------------------------------------------------------------------------------


def test_the_standings_fixtures_have_a_bom_and_two_tables():
    raw = SMALL.read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf")
    assert (
        raw.decode("utf-8-sig").splitlines()[0]
        == "Rank,EntryId,EntryName,TimeRemaining,Points,Lineup,,Player,Roster Position,%Drafted,FPTS"
    )


def test_a_small_contest_where_the_player_table_is_longer_than_the_entries():
    st = parse(SMALL)
    assert len(st.entries) == 10 and len(st.ownership) > 10  # the table runs past the last entry
    rows = len(SMALL.read_text(encoding="utf-8-sig").splitlines()) - 1
    assert rows > 10  # blank A-F cells on the extra rows
    assert st.kind == "Classic" and len(st.lineups) == 10 and st.lineup_errors.empty and st.warnings == []
    assert st.entries["entry_id"].iloc[0] == "9000000101"  # ids stay text, no float mangling


def test_a_big_contest_where_entries_run_past_the_player_table_with_nine_field_rows():
    lines = BIG.read_text(encoding="utf-8-sig").splitlines()
    assert len(lines[-1].split(",")) == 9  # the entry rows past the player table have only 9 fields
    st = parse(BIG)
    assert len(st.entries) == 12 and len(st.ownership) == 4  # Aaron Jones' RB and FLEX rows merge
    assert len(st.lineups) == 12 and st.lineup_errors.empty
    assert st.profile.entrants == 12


def test_ownership_is_the_existing_parser_called_as_is():
    st = parse(BIG)
    jones = st.ownership.set_index("player").loc["Aaron Jones Sr."]
    assert jones["pct_drafted"] == pytest.approx(0.25)  # 16.67% RB + 8.33% FLEX
    assert jones["dk_position"] == "RB"
    assert st.ownership["contest_entries"].iloc[0] == 12


def test_ties_share_a_rank_and_entry_counters_are_split_off():
    st = parse(MESSY)
    assert list(st.entries["rank"]) == [1, 2, 2, 4, 5, 6]
    tied = st.entries[st.entries["rank"] == 2]
    assert list(tied["entry_name"]) == ["tied_two", "tied_two"]
    assert list(tied["entry_no"]) == [1, 2] and list(tied["entry_max"]) == [3, 3]
    assert pd.isna(st.entries.loc[0, "entry_no"])


def test_lineups_that_do_not_parse_are_reported_with_a_reason_and_kept_out_of_the_field():
    st = parse(MESSY)
    assert len(st.lineups) == 3
    assert list(st.lineup_errors["entry_name"]) == ["broken_eight", "broken_blank", "broken_junk"]
    reasons = list(st.lineup_errors["error"])
    assert "8 slots" in reasons[0] and "blank" in reasons[1] and "text before the first slot" in reasons[2]
    assert any("did not parse" in w for w in st.warnings)


def test_a_showdown_contest_is_tagged_showdown():
    st = parse(SHOWDOWN)
    assert st.kind == "Showdown" and set(st.lineups["kind"]) == {"Showdown"}
    assert all(len(slots) == 6 for slots in st.lineups["slots"])


def test_a_zero_byte_file_is_an_error_not_a_crash():
    assert EMPTY.stat().st_size == 0
    with pytest.raises(S.StandingsError, match="0 bytes"):
        S.parse_standings(EMPTY.read_bytes(), contest_id="900000005")


def test_not_a_standings_file(tmp_path):
    with pytest.raises(S.StandingsError, match="missing column"):
        S.parse_standings(b"a,b\n1,2\n", contest_id="1")
    with pytest.raises(S.StandingsError, match="no entries"):
        S.parse_standings(
            b"Rank,EntryId,EntryName,TimeRemaining,Points,Lineup,,Player,Roster Position,%Drafted,FPTS\n",
            contest_id="1",
        )
    with pytest.raises(S.StandingsError, match="not UTF-8"):
        S.parse_standings(b"\xff\xfe\x00bad", contest_id="1")


# ---- the score profile ---------------------------------------------------------------------------


def test_score_profile_of_the_hand_built_contest():
    p = parse(SMALL, places_paid=4).profile
    # sorted descending: 208.66, 208.66, 195.0, 190.5, 185.0, 170.0, 165.0, 150.0, 140.0, 120.0
    assert p.entrants == 10 and p.places_paid == 4
    assert p.cash_line == 190.5  # the score at the last paid place (4th)
    assert p.top10 == 208.66 and p.top1 == 208.66 and p.win == 208.66  # ceil(10% of 10) = 1st
    assert p.median == pytest.approx((185.0 + 170.0) / 2)
    assert p.complete


def test_cash_line_is_nan_without_places_paid_and_clipped_to_the_field():
    assert pd.isna(parse(SMALL, places_paid=None).profile.cash_line)
    assert parse(SMALL, places_paid=0).profile.places_paid is None
    assert parse(SMALL, places_paid=99).profile.cash_line == 120.0  # more paid than entered: the last score


def test_top_10_percent_and_top_1_percent_positions():
    st = parse(BIG, places_paid=6)
    # scores 200, 192.5, ... 12 entries: ceil(1.2) = 2nd for top 10%, 1st for top 1%
    assert st.profile.top10 == 192.5 and st.profile.top1 == 200.0
    assert st.profile.cash_line == pytest.approx(200.0 - 7.5 * 5)


def test_a_contest_in_play_is_not_complete():
    raw = SMALL.read_text(encoding="utf-8-sig").replace(",0,208.66,", ",12,208.66,", 1).encode()
    assert S.parse_standings(raw, contest_id="1").profile.complete is False


def test_curve_points_are_mid_rank_percentiles():
    curve = parse(SMALL, places_paid=4).profile.curve()
    assert [(round(q, 4), score) for q, score in curve] == [
        (0.05, 208.66),  # the winner: (1 - 0.5) / 10
        (0.05, 208.66),  # top 1%
        (0.05, 208.66),  # top 10%
        (0.5, 177.5),  # the median
        (0.35, 190.5),  # the cash line: (4 - 0.5) / 10
    ]


# ---- finding the files ---------------------------------------------------------------------------


def test_scan_reads_bare_csvs_and_zips_and_reports_the_empty_ones():
    scan = S.scan_standings(DIR)
    assert sorted(s.contest_id for s in scan.sources) == [
        "900000001",
        "900000002",
        "900000003",
        "900000004",
        "900000006",
    ]
    assert sorted(Path(p.split("!")[-1]).name for p in scan.empty) == [
        "contest-standings-900000005.csv",
        "contest-standings-900000007.csv",
    ]
    zipped = next(s for s in scan.sources if s.contest_id == "900000006")
    assert "NFL GIANT Contest Standings (2).zip!" in zipped.origin
    assert S.parse_standings(zipped.raw, contest_id="900000006").profile.entrants == 2
    assert scan.duplicates == [] and scan.unidentified == [] and scan.skipped == []


def test_scan_accepts_a_single_file_or_a_single_zip():
    assert [s.contest_id for s in S.scan_standings(SMALL).sources] == ["900000001"]
    assert [s.contest_id for s in S.scan_standings(ZIP).sources] == ["900000006"]


def test_scan_reads_a_zip_with_any_name_and_ignores_non_csv_members(tmp_path):
    archive = tmp_path / "whatever (7).zip"
    with zipfile.ZipFile(archive, "w") as z:
        z.writestr("nested/dir/contest-standings-123456789.csv", SMALL.read_bytes())
        z.writestr("readme.txt", "hello")
    scan = S.scan_standings(tmp_path)
    assert [(s.contest_id, "readme" in s.origin) for s in scan.sources] == [("123456789", False)]


def test_scan_reports_a_duplicate_contest_and_a_csv_without_an_id(tmp_path):
    (tmp_path / "contest-standings-111111111.csv").write_bytes(SMALL.read_bytes())
    (tmp_path / "contest-standings-111111111 (1).csv").write_bytes(SMALL.read_bytes())
    (tmp_path / "mystery.csv").write_bytes(SMALL.read_bytes())
    scan = S.scan_standings(tmp_path)
    assert [s.contest_id for s in scan.sources] == ["111111111"]
    assert len(scan.duplicates) == 1 and len(scan.unidentified) == 1


def test_scan_survives_a_corrupt_zip(tmp_path):
    (tmp_path / "broken.zip").write_bytes(b"this is not a zip")
    scan = S.scan_standings(tmp_path)
    assert scan.sources == [] and len(scan.skipped) == 1


def test_contest_id_from_a_name():
    assert S.contest_id_from_name("contest-standings-196681359.csv") == "196681359"
    assert S.contest_id_from_name("some/dir/contest-standings-42.csv") == "42"
    assert S.contest_id_from_name("export 123456 (2).csv") == "123456"
    assert S.contest_id_from_name("no digits.csv") is None


def test_a_zip_member_parses_exactly_like_the_bare_file(tmp_path):
    with zipfile.ZipFile(tmp_path / "x.zip", "w") as z:
        z.writestr("contest-standings-900000001.csv", SMALL.read_bytes())
    [source] = S.scan_standings(tmp_path).sources
    from_zip = S.parse_standings(source.raw, contest_id=source.contest_id, places_paid=4)
    bare = parse(SMALL, places_paid=4)
    assert from_zip.entries.equals(bare.entries) and from_zip.profile == bare.profile


def test_a_file_mixing_classic_and_showdown_lineups_is_mixed_not_unknown():
    text = SMALL.read_text(encoding="utf-8-sig").splitlines()
    swapped = text[:]
    swapped[2] = swapped[2].replace(
        swapped[2].split(",")[5], "CPT A  FLEX B FLEX C FLEX D FLEX E FLEX F", 1
    )  # entry 2 becomes a Showdown lineup
    st = S.parse_standings("\n".join(swapped).encode(), contest_id="1")
    assert st.kind == "Mixed" and any("more than one kind" in w for w in st.warnings)
    from dfs.contests import field as F

    assert "mixes" in F.field_stats(st, None).skipped
