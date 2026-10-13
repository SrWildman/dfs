"""Regenerates the synthetic DraftKings files in `fixtures/` (run `python tests/contests/build_fixtures.py`).

Everything here is invented: made-up usernames, made-up scores, made-up contest ids. The formats match the
real DraftKings exports (UTF-8 BOM, `$` money with thousands commas, quoted fields where a comma appears, the
two unrelated tables sharing the rows of a standings file). No real DraftKings file is, or should ever be,
committed. The numbers asserted in the tests are worked out by hand in `test_field.py`, not read back from
these files.
"""

from __future__ import annotations

import csv
import zipfile
from pathlib import Path

HERE = Path(__file__).parent / "fixtures"
STANDINGS = HERE / "standings"

HISTORY_HEADER = (
    "Sport,Game_Type,Entry_Key,Entry,Contest_Key,Contest_Date_EST,Place,Points,Winnings_Non_Ticket,"
    "Winnings_Ticket,Contest_Entries,Entry_Fee,Prize_Pool,Places_Paid"
)

# One history row per line, "|"-separated so the names and the comma-grouped numbers need no escaping here
# (the csv module quotes them on the way out, as DraftKings does). 2025 week 6 is Sun 2025-10-12.
HISTORY_ROWS = """
NFL|Classic|5000000001|NFL $8K Fair Catch [Single Entry]|800000001|2025-10-12 13:00:00|87|181.20|$40.00|$0.00|2,758|$20.00|$8,000.00|600
NFL|Classic|5000000002|NFL $75K Fair Catch [Single Entry]|800000002|2025-10-12 13:00:00|9,400|140.10|$0.00|$0.00|9,500|$20.00|$75,000.00|1,900
NFL|Classic|5000000003|NFL $6K Huddle [Single Entry]|800000003|2025-10-12 13:00:00|310|160.00|$0.00|$0.00|1,200|$10.00|$6,000.00|240
NFL|Classic|5000000004|NFL $10 Hundo [Single Entry]|800000004|2025-10-12 13:00:00|55|150.00|$0.00|$0.00|120|$10.00|$1,000.00|30
NFL|Classic|5000000005|NFL $3K Screen Pass [3 Entry Max] (2/3)|800000005|2025-10-12 13:00:00|620|150.00|$0.00|$0.00|1,000|$5.00|$3,000.00|200
NFL|Classic|5000000006|NFL $4K Singleback [5 Entry Max] (3/5)|800000006|2025-10-12 13:00:00|90|181.20|$12.00|$0.00|800|$5.00|$4,000.00|160
NFL|Classic|5000000007|NFL GIANT $25 Double Up [Single Entry]|800000007|2025-10-12 13:00:00|5,100|147.00|$50.00|$0.00|12,000|$25.00|$270,000.00|5,400
NFL|Classic|5000000008|NFL Single Entry $5 Double Up|800000008|2025-10-12 13:00:00|7,000|120.00|$0.00|$0.00|8,000|$5.00|$36,000.00|3,600
NFL|Classic|5000000009|NFL $2 Double Up|800000009|2025-10-12 13:00:00|10|170.00|$4.00|$0.00|10|$2.00|$18.00|4
NFL|Classic|5000000010|NFL $1 srwildman vs. rival_one|800000010|2025-10-12 13:00:00|1|150.00|$1.80|$0.00|2|$1.00|$1.80|1
NFL|Classic|5000000011|NFL $1 rival_one vs. srwildman|800000011|2025-10-12 13:00:00|2|130.00|$0.00|$0.00|2|$1.00|$1.80|1
NFL|Classic|5000000012|NFL $1 srwildman vs. rival_two|800000012|2025-10-12 13:00:00|1|155.00|$1.80|$0.00|2|$1.00|$1.80|1
NFL|Classic|5000000013|NFL Discord-Only Around the World Contest [Read Contest Description!]|800000013|2025-10-12 13:00:00|40|150.00|$0.00|$0.00|300|$0.00|$100.00|10
NFL|Classic|5000000014|$2K T-Mobile Lightening Drive Football Takeover|800000014|2025-10-12 13:00:00|1,000|120.00|$0.00|$0.00|5,000|$0.00|$2,000.00|500
NFL|Classic|5000000015|DREAMERS DFS  by bradikal36 #5|800000015|2025-10-12 13:00:00|3|170.00|$0.00|$0.00|12|$10.00|$100.00|3
NFL|Classic|5000000016|NFL 10x Booster [Top 5 Win $100]|800000016|2025-10-12 13:00:00|7|150.00|$0.00|$0.00|20|$10.00|$200.00|5
NFL|Classic|5000000017|NFL $10 Quintuple Up [Top 5 Win $50]|800000017|2025-10-12 13:00:00|2|175.00|$50.00|$0.00|25|$10.00|$250.00|5
NFL|Single Stat - Total Yards|5000000018|NFL Single Stat $1 7-Player (Top 3 Win)|800000018|2025-10-12 13:00:00|2|300.00|$1.50|$0.00|7|$1.00|$6.00|3
NFL|Showdown Captain Mode|5294179404|NFL Showdown $10K Cover Four [5 Entry Max] (BUF @ LAR) (2/5)|196681359|2025-10-13 20:15:00|351|146.07|$8.00|$0.00|2972|$4.00|$10,000.00|765
NFL|Showdown Captain Mode|5294179405|NFL Showdown $10K Cover Four [5 Entry Max] (BUF @ LAR) (3/5)|196681359|2025-10-13 20:15:00|1,200|120.50|$0.00|$0.00|2972|$4.00|$10,000.00|765
NFL|Showdown|5294179406|NFL Showdown $5K Hail Mary [Single Entry] (KC @ DEN)|196681360|2025-10-05 20:20:00|200|130.00|$0.00|$0.00|1,500|$5.00|$5,000.00|300
NFL|KFC Sunday Night Showdown Series|5294179407|KFC Sunday Night Showdown (DAL @ NYG)|196681361|2025-10-05 20:20:00|20|140.00|$5.00|$0.00|100|$3.00|$250.00|10
NFL|In-Game Showdown (Q4)|5294179408|NFL In-Game Showdown (Q4) $3 (BUF @ LAR)|196681362|2025-10-13 21:30:00|5|30.00|$0.00|$0.00|50|$3.00|$100.00|10
NFL|Best Ball|5000000019|NFL Best Ball $5 Mini|800000019|2025-10-12 13:00:00|100|1,500.00|$0.00|$0.00|300|$5.00|$1,000.00|60
NFL|Playoff Best Ball|5000000020|NFL Playoff Best Ball $5|800000020|2026-01-17 16:30:00|100|900.00|$0.00|$0.00|300|$5.00|$1,000.00|60
NFL|Madden Classic|5000000021|NFL Madden Classic $2|800000021|2025-10-12 13:00:00|10|140.00|$0.00|$0.00|40|$2.00|$60.00|8
NFL|Snake|5000000022|NFL Snake $5 Draft|800000022|2025-10-12 13:00:00|3|99.00|$0.00|$0.00|6|$5.00|$25.00|2
NFL|Classic|5000000023|NFL $8K Fair Catch [Single Entry]|800000023|2026-01-04 13:00:00|200|170.00|$0.00|$0.00|2,000|$20.00|$8,000.00|400
NFL|Classic|5000000024|NFL $8K Fair Catch [Single Entry]|800000024|2026-09-13 13:00:00|100|175.00|$0.00|$0.00|2,000|$20.00|$8,000.00|400
NFL|Classic|5000000025|NFL $5K Thursday Night Kickoff [Single Entry]|800000025|2025-10-09 20:15:00|50|90.00|$0.00|$0.00|500|$5.00|$5,000.00|100
NBA|Classic|5000000026|NBA $5 Double Up|800000026|2025-12-01 19:00:00|10|250.00|$10.00|$0.00|100|$5.00|$450.00|45
NFL|Classic|5000000027|NFL Tiny $2 Double Up [Single Entry]|900000001|2025-10-12 13:00:00|5|185.00|$0.00|$0.00|10|$2.00|$18.00|4
"""  # noqa: E501

LATER_NEW_ROW = (
    "NFL|Classic|5000000099|NFL $8K Fair Catch [Single Entry]|800000099|2026-09-20 13:00:00|50|190.00|"
    "$100.00|$0.00|2,500|$20.00|$8,000.00|500"
)

UNIVERSE = """
Name,Team,Position,Opponent,Salary
Jordan Love,GB,QB,CHI,6600
Caleb Williams,CHI,QB,GB,6000
Jared Goff,DET,QB,MIN,6200
D'Andre Swift,CHI,RB,GB,6100
Jaylen Warren,PIT,RB,CIN,5300
Josh Jacobs,GB,RB,CHI,6400
Jahmyr Gibbs,DET,RB,MIN,7200
Braelon Allen,NYJ,RB,MIA,4400
Christian Watson,GB,WR,CHI,5700
Matthew Golden,GB,WR,CHI,5200
DJ Moore,CHI,WR,GB,5800
Rome Odunze,CHI,WR,GB,5400
Amon-Ra St. Brown,DET,WR,MIN,8100
Michael Wilson,ARI,WR,SEA,4600
Wan'Dale Robinson,NYG,WR,DAL,4200
Sam LaPorta,DET,TE,MIN,4700
Tucker Kraft,GB,TE,CHI,4900
Cole Kmet,CHI,TE,GB,3600
Jets,NYJ,DST,MIA,2800
Bears,CHI,DST,GB,3000
Packers,GB,DST,CHI,3200
"""

# The ten entries of the hand-built contest: rank | entry id | name | points | QB,RB,RB,WR,WR,WR,TE,FLEX,DST.
# E1 and E2 are the same lineup (a duplicate, tied for rank 1); Sam's entries use the made-up name `test_me`.
FIELD_ENTRIES = """
1|9000000101|alpha_fox|208.66|Jordan Love,Josh Jacobs,D'Andre Swift,Christian Watson,Matthew Golden,DJ Moore,Tucker Kraft,Jaylen Warren,Jets
1|9000000102|bravo_owl|208.66|Jordan Love,Josh Jacobs,D'Andre Swift,Christian Watson,Matthew Golden,DJ Moore,Tucker Kraft,Jaylen Warren,Jets
3|9000000103|charlie_elk|195.0|Jordan Love,D'Andre Swift,Jaylen Warren,Christian Watson,Rome Odunze,Michael Wilson,Sam LaPorta,Amon-Ra St. Brown,Bears
4|9000000104|delta_hawk|190.5|Caleb Williams,D'Andre Swift,Braelon Allen,DJ Moore,Rome Odunze,Michael Wilson,Cole Kmet,Jaylen Warren,Packers
5|9000000105|echo_wolf|185.0|Jared Goff,Braelon Allen,Josh Jacobs,Amon-Ra St. Brown,Matthew Golden,Christian Watson,Sam LaPorta,D'Andre Swift,Jets
6|9000000106|test_me (1/2)|170.0|Jordan Love,Josh Jacobs,Jaylen Warren,Christian Watson,Matthew Golden,Michael Wilson,Tucker Kraft,D'Andre Swift,Packers
7|9000000107|foxtrot_bat|165.0|Caleb Williams,D'Andre Swift,Braelon Allen,DJ Moore,Michael Wilson,Wan'Dale Robinson,Sam LaPorta,Rome Odunze,Jets
8|9000000108|golf_yak|150.0|Jared Goff,Jaylen Warren,Braelon Allen,Michael Wilson,Wan'Dale Robinson,Christian Watson,Cole Kmet,Tucker Kraft,Bears
9|9000000109|test_me (2/2)|140.0|Jordan Love,Josh Jacobs,D'Andre Swift,Christian Watson,DJ Moore,Rome Odunze,Tucker Kraft,Matthew Golden,Jets
10|9000000110|hotel_ram|120.0|Caleb Williams,D'Andre Swift,Josh Jacobs,DJ Moore,Rome Odunze,Amon-Ra St. Brown,Cole Kmet,Jaylen Warren,Packers
"""  # noqa: E501

STANDINGS_HEADER = ["Rank", "EntryId", "EntryName", "TimeRemaining", "Points", "Lineup", ""]
STANDINGS_HEADER += ["Player", "Roster Position", "%Drafted", "FPTS"]


def rows_of(text: str) -> list[list[str]]:
    return [line.split("|") for line in text.strip().splitlines()]


def write_history(path: Path, rows: list[list[str]]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(HISTORY_HEADER.split(","))
        writer.writerows(rows)


def lineup_text(players: list[str]) -> str:
    """DraftKings' text for a Classic lineup: slots in label order, the DST by nickname then TWO spaces."""
    qb, rb1, rb2, wr1, wr2, wr3, te, flex, dst = players
    slots = f"FLEX {flex} QB {qb} RB {rb1} RB {rb2} TE {te} WR {wr1} WR {wr2} WR {wr3}"
    return f"DST {dst}  {slots}"


def entry(rank, entry_id, name, points, lineup) -> tuple:
    text = lineup if isinstance(lineup, str) else lineup_text(lineup)
    return (str(rank), entry_id, name, float(points), text)


def ownership_rows(entries: list[tuple]) -> list[list[str]]:
    """The per-player table: one row per (player, slot type), `%Drafted` over the whole field."""
    counts: dict[tuple[str, str], int] = {}
    for _rank, _id, _name, _points, text in entries:
        parts = text.replace("  ", " ").split()
        # text is "DST x FLEX y QB ..." -> rebuild (slot, name) pairs from the known layout
        slots, current, name = [], None, []
        for token in parts:
            if token in {"DST", "FLEX", "QB", "RB", "TE", "WR", "CPT"}:
                if current:
                    slots.append((current, " ".join(name)))
                current, name = token, []
            else:
                name.append(token)
        slots.append((current, " ".join(name)))
        for key in slots:
            counts[(key[1], key[0])] = counts.get((key[1], key[0]), 0) + 1
    total = len(entries)
    ranked = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    return [[n, s, f"{100 * c / total:.2f}%", f"{(len(n) * 1.7) % 30:.1f}"] for (n, s), c in ranked]


def write_standings(path: Path, entries: list[tuple], owned: list[list[str]], *, short_rows=False) -> None:
    """Two tables on shared rows. With `short_rows`, entry rows past the end of the player table carry only
    9 fields (the trailing cells are missing), as in a big contest."""
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(STANDINGS_HEADER)
        for i in range(max(len(entries), len(owned))):
            left = [""] * 6
            if i < len(entries):
                rank, entry_id, name, points, text = entries[i]
                left = [rank, entry_id, name, "0", f"{points:.2f}", text]
            if i < len(owned):
                writer.writerow([*left, "", *owned[i]])
            elif short_rows:
                writer.writerow([*left, "", "", ""])
            else:
                writer.writerow([*left, "", "", "", "", ""])


def build() -> None:
    STANDINGS.mkdir(parents=True, exist_ok=True)
    history = rows_of(HISTORY_ROWS)
    write_history(HERE / "history_export.csv", history)

    # A later export: contest 5000000005 has its final result, one new contest, and no NBA row.
    later = [row for row in history if row[0] == "NFL"]
    for row in later:
        if row[2] == "5000000005":
            row[6], row[7] = "400", "165.00"
    later.append(LATER_NEW_ROW.split("|"))
    write_history(HERE / "history_export_later.csv", later)

    (HERE / "players_week.csv").write_text(UNIVERSE.strip() + "\n", encoding="utf-8")

    # 1. the hand-built 10-entry contest: the player table runs PAST the entries (a small contest)
    field_entries = [entry(r, i, n, p, line.split(",")) for r, i, n, p, line in rows_of(FIELD_ENTRIES)]
    write_standings(
        STANDINGS / "contest-standings-900000001.csv", field_entries, ownership_rows(field_entries)
    )

    # 2. a bigger contest: the entries run past a short player table, and those rows have 9 fields
    qbs = ["Josh Allen", "Lamar Jackson", "C.J. Stroud", "Joe Burrow"]
    rbs = [
        "Aaron Jones Sr.",
        "Saquon Barkley",
        "Derrick Henry",
        "Kyren Williams",
        "Bijan Robinson",
        "Breece Hall",
    ]
    wrs = [
        "Ja'Marr Chase",
        "CeeDee Lamb",
        "Amon-Ra St. Brown",
        "Puka Nacua",
        "Justin Jefferson",
        "Nico Collins",
    ]
    wrs += ["DK Metcalf"]
    tes = ["Travis Kelce", "Trey McBride", "George Kittle"]
    dsts = ["49ers", "Ravens", "Jets"]
    big = []
    for i in range(12):
        lineup = [qbs[i % 4], rbs[i % 6], rbs[(i + 1) % 6], wrs[i % 7], wrs[(i + 2) % 7], wrs[(i + 4) % 7]]
        lineup += [tes[i % 3], rbs[(i + 3) % 6], dsts[i % 3]]
        big.append(entry(i + 1, f"900000020{i:02d}", f"user_{i:02d}", 200.0 - 7.5 * i, lineup))
    big_owned = [
        ["Josh Allen", "QB", "25.00%", "21.4"],
        ["Aaron Jones Sr.", "RB", "16.67%", "12.1"],
        ["Aaron Jones Sr.", "FLEX", "8.33%", "12.1"],
        ["Ja'Marr Chase", "WR", "41.67%", "30.2"],
        ["49ers", "DST", "33.33%", "9.0"],
    ]
    write_standings(STANDINGS / "contest-standings-900000002.csv", big, big_owned, short_rows=True)

    # 3. ties, entry counters, three lineups that will not parse (8 slots, blank, no slot tokens)
    base = ["Josh Allen", "Derrick Henry", "Saquon Barkley", "Ja'Marr Chase", "CeeDee Lamb", "Puka Nacua"]
    eight = "DST Jets  FLEX Breece Hall QB Josh Allen RB Derrick Henry RB Kyren Williams"
    eight += " TE Travis Kelce WR Puka Nacua WR DK Metcalf"
    messy = [
        entry(1, "9000000301", "tied_one", 180.0, [*base, "Travis Kelce", "Nico Collins", "Ravens"]),
        entry(2, "9000000302", "tied_two (1/3)", 170.5, [*base, "Trey McBride", "Breece Hall", "49ers"]),
        entry(2, "9000000303", "tied_two (2/3)", 170.5, [*base, "Trey McBride", "Breece Hall", "49ers"]),
        entry(4, "9000000304", "broken_eight", 150.0, eight),
        entry(5, "9000000305", "broken_blank", 140.0, ""),
        entry(6, "9000000306", "broken_junk", 130.0, "Josh Allen RB Derrick Henry"),
    ]
    owned_messy = [["Josh Allen", "QB", "50.00%", "20.0"], ["Derrick Henry", "RB", "100.00%", "15.0"]]
    write_standings(STANDINGS / "contest-standings-900000003.csv", messy, owned_messy)

    # 4. a Showdown contest (CPT + 5 FLEX)
    flex = "FLEX Ja'Marr Chase FLEX Derrick Henry FLEX Travis Kelce FLEX Puka Nacua FLEX Ravens"
    flex2 = "FLEX Josh Allen FLEX Ja'Marr Chase FLEX Travis Kelce FLEX Puka Nacua FLEX Ravens"
    show = [
        entry(1, "9000000401", "show_one", 210.0, f"CPT Josh Allen  {flex}"),
        entry(2, "9000000402", "show_two", 190.0, f"CPT Derrick Henry {flex2}"),
    ]
    owned_show = [["Josh Allen", "CPT", "50.00%", "30.0"], ["Josh Allen", "FLEX", "50.00%", "20.0"]]
    write_standings(STANDINGS / "contest-standings-900000004.csv", show, owned_show)

    # 5. a failed download: a 0-byte CSV
    (STANDINGS / "contest-standings-900000005.csv").write_bytes(b"")

    # 6. a zip with an arbitrary name: one standings CSV, one 0-byte CSV, one non-CSV file
    inner = [
        entry(1, "9000000601", "zip_one", 199.9, big[0][4]),
        entry(2, "9000000602", "zip_two", 150.1, big[1][4]),
    ]
    scratch = STANDINGS / "_zip_member.csv"
    write_standings(scratch, inner, [["Josh Allen", "QB", "50.00%", "21.4"]])
    stamp = (2025, 10, 14, 0, 0, 0)
    with zipfile.ZipFile(
        STANDINGS / "NFL GIANT Contest Standings (2).zip", "w", zipfile.ZIP_DEFLATED
    ) as archive:
        archive.writestr(
            zipfile.ZipInfo("contest-standings-900000006.csv", date_time=stamp), scratch.read_bytes()
        )
        archive.writestr(zipfile.ZipInfo("contest-standings-900000007.csv", date_time=stamp), b"")
        archive.writestr(zipfile.ZipInfo("notes.txt", date_time=stamp), b"not a standings file")
    scratch.unlink()


if __name__ == "__main__":
    build()
