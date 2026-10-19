"""`dfs entries fill` / `dfs entries print`: a synthetic entries file in DK's exact shape (never a real one).

The file mirrors what DraftKings' "Edit entries" download looks like: the header row (`Entry ID, Contest Name,
Contest ID, Entry Fee`, the nine slots, a blank column, `Instructions`), DK's instruction lines down the
Instructions column, then the player table (`Position, Name + ID, Name, ID, Roster Position, Salary, Game
Info, TeamAbbrev, AvgPointsPerGame`) to the right, starting on the row after the instructions and running
below the last entry. Reserved entries have blank slots.
"""

import csv
import os
from datetime import UTC, datetime

import pytest

from dfs import entries as ent
from dfs.lineups import DK_UPLOAD_HEADER, DK_UPLOAD_SHEET_HEADER, ParsedEntry, split_blank_entries
from dfs.models import ROSTER_SLOTS
from dfs.sheet_dk_upload import tab_grid

TABLE_HEADER = [
    "Position",
    "Name + ID",
    "Name",
    "ID",
    "Roster Position",
    "Salary",
    "Game Info",
    "TeamAbbrev",
    "AvgPointsPerGame",
]
EARLY, LATE, NIGHT = (
    "AAA@BBB 10/18/2026 01:00PM ET",
    "CCC@DDD 10/18/2026 04:05PM ET",
    "EEE@FFF 10/18/2026 04:25PM ET",
)
# (position, name, id, game, team)
PLAYERS = [
    ("QB", "Quinn Early", "1001", EARLY, "AAA"),
    ("QB", "Quentin Late", "1002", LATE, "CCC"),
    ("RB", "Rob Early", "2001", EARLY, "AAA"),
    ("RB", "Ray Early", "2002", EARLY, "BBB"),
    ("RB", "Rex Late", "2003", LATE, "CCC"),
    ("RB", "Rod Night", "2004", NIGHT, "EEE"),
    ("WR", "Walt Early", "3001", EARLY, "AAA"),
    ("WR", "Wade Early", "3002", EARLY, "BBB"),
    ("WR", "Will Early", "3003", EARLY, "AAA"),
    ("WR", "Wes Late", "3004", LATE, "DDD"),
    ("WR", "Wyatt Night", "3005", NIGHT, "FFF"),
    ("TE", "Tom Early", "4001", EARLY, "BBB"),
    ("TE", "Tim Late", "4002", LATE, "CCC"),
    ("DST", "Aces", "5001", EARLY, "AAA"),
    ("DST", "Bears", "5002", LATE, "CCC"),
]
BEFORE = datetime(2026, 10, 18, 16, 0, tzinfo=UTC)  # 12:00 pm ET: nothing has kicked off
AFTER_EARLY = datetime(2026, 10, 18, 18, 0, tzinfo=UTC)  # 2:00 pm ET: the 1:00 games are under way

CELL = {pid: f"{name} ({pid})" for _pos, name, pid, _g, _t in PLAYERS}
NAME_ID = {name: pid for _pos, name, pid, _g, _t in PLAYERS}


def cells(*names: str) -> list[str]:
    return [CELL[NAME_ID[n]] for n in names]


# QB RB RB WR WR WR TE FLEX DST. Rex Late is the RB with the latest kickoff, so he belongs in FLEX.
LINEUP_1 = (
    "Quinn Early",
    "Rob Early",
    "Ray Early",
    "Walt Early",
    "Wade Early",
    "Will Early",
    "Tom Early",
    "Rex Late",
    "Aces",
)
LINEUP_1_AT_DK = cells(*LINEUP_1)  # what DK holds when it already has lineup 1 (FLEX correct)
LINEUP_1_FLEX_WRONG = (  # the same nine, Rex Late typed in the RB slot and Ray Early in FLEX
    "Quinn Early", "Rob Early", "Rex Late", "Walt Early", "Wade Early", "Will Early", "Tom Early",
    "Ray Early", "Aces",
)  # fmt: skip
LINEUP_2 = (
    "Quentin Late",
    "Rob Early",
    "Rex Late",
    "Walt Early",
    "Wade Early",
    "Wes Late",
    "Tim Late",
    "Rod Night",
    "Bears",
)
LINEUP_3 = (
    "Quinn Early",
    "Ray Early",
    "Rod Night",
    "Walt Early",
    "Will Early",
    "Wyatt Night",
    "Tom Early",
    "Wes Late",
    "Aces",
)
LINEUP_4 = (
    "Quentin Late",
    "Rob Early",
    "Ray Early",
    "Wade Early",
    "Will Early",
    "Wyatt Night",
    "Tim Late",
    "Wes Late",
    "Bears",
)


def sheet_lineups(**by_number) -> list[ent.SheetLineup]:
    """Twenty lineup blocks; `by_number` is `n=(names, marker, cash %, top 1%, cal pts)`."""
    out = []
    for n in range(1, 21):
        spec = by_number.get(f"l{n}")
        if spec is None:
            out.append(ent.SheetLineup(n, ("",) * 9))
            continue
        names, marker, cash, top1 = spec[:4]
        out.append(
            ent.SheetLineup(
                n,
                names,
                marker,
                cash_pct=cash,
                top1=top1,
                cal_pts=140.0 + n,
                proj_pts=130.0 + n,
                own=1.25 + n / 100,
            )
        )
    return out


STANDARD_LINEUPS = dict(
    l1=(LINEUP_1, "Cash", 0.58, 0.004),
    l2=(LINEUP_2, "Cash", 0.51, 0.006),
    l3=(LINEUP_3, "GPP", 0.40, 0.031),
    l4=(LINEUP_4, "GPP", 0.35, 0.024),
)

# (entry id, contest name, contest id, fee, slot cells)
ENTRIES = [
    ("9001", "NFL GIANT $25 Double Up [Single Entry]", "700", "$25", []),
    ("9002", "NFL $1 srwildman vs. TBD", "701", "$1", LINEUP_1_AT_DK),
    ("9003", "NFL $1 srwildman vs. TBD", "702", "$1", []),
    ("9004", "NFL $4K Singleback [3 Entry Max]", "703", "$5", []),
    ("9005", "NFL $4K Singleback [3 Entry Max]", "703", "$5", []),
    ("9006", "NFL $4K Singleback [3 Entry Max]", "703", "$5", []),
    ("9007", "NFL $5K Screen Pass [Single Entry]", "704", "$8", []),
    ("9008", "NFL Showdown Captain's Challenge (DAL @ NYG)", "705", "$5", []),
    ("9009", "NFL $2K Reserved Spot [5 Entry Max]", "706", "$3", []),
]


def write_entries_file(path, entries=ENTRIES, players=PLAYERS, slots=ROSTER_SLOTS, instructions=True):
    """DK's file: entries on the left, the player table starting on the row after the instruction lines."""
    header = ["Entry ID", "Contest Name", "Contest ID", "Entry Fee", *slots, "", "Instructions"]
    offset = len(header) - 1  # the Instructions column is where the table starts
    lines = [f"{i}. DK's instruction line {i}" for i in range(1, 7)] if instructions else []
    rows = [header]
    total = max(len(entries), len(lines) + 1 + len(players))
    for i in range(total):
        row = [""] * offset
        if i < len(entries):
            entry_id, name, contest_id, fee, held = entries[i]
            row[:4] = [entry_id, name, contest_id, fee]
            row[4 : 4 + len(held)] = held
        right: list[str] = []
        if i < len(lines):
            right = [lines[i]]
        elif i == len(lines):
            right = list(TABLE_HEADER)
        elif i - len(lines) - 1 < len(players):
            pos, name, pid, game, team = players[i - len(lines) - 1]
            roster = "DST" if pos == "DST" else (pos if pos in ("QB",) else f"{pos}/FLEX")
            right = [pos, f"{name} ({pid})", name, pid, roster, "5000", game, team, "10.5"]
        rows.append(row + right)
    with open(path, "w", newline="", encoding="utf-8") as f:
        csv.writer(f).writerows(rows)
    return path


@pytest.fixture
def entries_file(tmp_path):
    return ent.read_entries_file(write_entries_file(tmp_path / "DraftKings Entries (3).csv"))


def tab_rows_for(**choices: str) -> dict[str, ent.TabRow]:
    """DK Upload rows with the Lineup dropdown Sam set (`e9004="L3"`), slots blank."""
    return {eid[1:]: ent.TabRow(eid[1:], lineup, ("",) * 9) for eid, lineup in choices.items()}


def row_of(plan: ent.FillPlan, entry_id: str) -> ent.RowPlan:
    return next(r for r in plan.rows if r.entry.entry_id == entry_id)


# -- finding and reading the file --------------------------------------------------------------------------


def test_the_newest_entries_file_is_found_by_its_header_row_not_its_name(tmp_path):
    older = write_entries_file(tmp_path / "DraftKings Entries (1).csv")
    newest = write_entries_file(tmp_path / "my lineups, renamed.csv")  # no "Entries" in the name at all
    imposter = tmp_path / "DraftKings Entries (9).csv"  # the right name, a contest-history header
    imposter.write_text("Sport,Game_Type,Entry_Key\nNFL,Classic,1\n")
    os.utime(older, (1_000, 1_000))
    os.utime(newest, (2_000, 2_000))
    os.utime(imposter, (3_000, 3_000))
    found, how = ent.resolve_entries_file(None, tmp_path)
    assert found == newest
    assert "newest" in how and str(tmp_path) in how and "header" in how


def test_a_file_given_with_csv_wins_and_a_missing_one_says_so(tmp_path):
    given = tmp_path / "x.csv"
    assert ent.resolve_entries_file(given, tmp_path) == (given, "given with --csv")
    found, how = ent.resolve_entries_file(None, tmp_path)
    assert found is None and "no DraftKings entries file" in how


def test_the_file_is_read_in_dks_shape(entries_file):
    assert entries_file.kind == "classic"
    assert len(entries_file.entries) == 9
    assert len(entries_file.players) == len(PLAYERS)
    qb = entries_file.players[0]
    assert (qb.name, qb.dk_id, qb.position, qb.cell) == ("Quinn Early", "1001", "QB", "Quinn Early (1001)")
    assert qb.kickoff == datetime(2026, 10, 18, 17, 0, tzinfo=UTC)  # 1:00 pm ET (EDT) is 17:00 UTC
    held = next(e for e in entries_file.entries if e.entry_id == "9002")
    assert held.ids == tuple(NAME_ID[n] for n in LINEUP_1)
    reserved = next(e for e in entries_file.entries if e.entry_id == "9009")
    assert reserved.blank


def test_a_slot_may_hold_a_bare_id():
    assert ent.cell_id("Kirk Cousins (44475679)") == "44475679"
    assert ent.cell_id("44475679") == "44475679"
    assert ent.cell_id("") == ""


def test_a_non_dk_file_is_refused(tmp_path):
    bad = tmp_path / "x.csv"
    bad.write_text("Entry ID,Contest Name\n1,a\n")
    with pytest.raises(ent.EntriesFileError):
        ent.read_entries_file(bad)


def test_showdown_other_slate_and_non_classic_entries_are_skipped_and_counted(tmp_path, entries_file):
    entries, skipped = ent.classify_entries(entries_file)
    assert [e.entry_id for e in entries] == ["9001", "9002", "9003", "9004", "9005", "9006", "9007", "9009"]
    assert skipped == {"Showdown": 1}  # the matchup in the contest name

    showdown = ent.read_entries_file(
        write_entries_file(tmp_path / "sd.csv", slots=("CPT", "FLEX", "FLEX", "FLEX", "FLEX", "FLEX"))
    )
    assert showdown.kind == "showdown"
    assert ent.classify_entries(showdown) == ([], {"Showdown file": 9})

    thursday = [(p, n, i, "AAA@BBB 10/15/2026 08:15PM ET", t) for p, n, i, _g, t in PLAYERS]
    other = ent.read_entries_file(write_entries_file(tmp_path / "tnf.csv", players=thursday))
    entries, skipped = ent.classify_entries(other)
    assert entries == [] and "another slate" in next(iter(skipped))


def test_the_format_comes_from_the_contest_series(entries_file):
    entries, _ = ent.classify_entries(entries_file)
    formats = {e.entry_id: e.format for e in entries}
    assert formats["9001"] == "Double Up"
    assert formats["9002"] == "H2H"
    assert formats["9004"] == "N-max GPP"
    assert formats["9007"] == "SE GPP"


# -- Sam picks, the tool only suggests -----------------------------------------------------------------------


def test_nothing_is_assigned_without_the_dropdown(entries_file):
    plan = ent.plan_fill(entries_file, {}, sheet_lineups(**STANDARD_LINEUPS), now=BEFORE)
    assert all(r.lineup is None for r in plan.rows)  # a suggestion is not a choice
    assert all(not any(r.cells) for r in plan.rows)
    assert plan.upload_rows == []
    assert plan.summary == "8 entries: 0 set, 8 blank"


def test_cash_formats_are_suggested_the_cash_marked_lineup_with_the_best_cash_pct(entries_file):
    plan = ent.plan_fill(entries_file, {}, sheet_lineups(**STANDARD_LINEUPS), now=BEFORE)
    for entry_id in ("9001", "9002", "9003"):  # a Double Up and two H2H: spread over the same best lineup
        assert row_of(plan, entry_id).suggested == "L1"
    assert "best Cash %" in row_of(plan, "9001").why and "58.0%" in row_of(plan, "9001").why


def test_gpp_formats_are_suggested_by_top_1_pct_and_never_the_same_lineup_twice_in_a_contest(entries_file):
    plan = ent.plan_fill(entries_file, {}, sheet_lineups(**STANDARD_LINEUPS), now=BEFORE)
    # SE GPP: L3 (3.1%). The 3-entry contest: L3, L4, then nothing left (only two GPP-marked lineups).
    assert row_of(plan, "9007").suggested == "L3"
    assert [row_of(plan, e).suggested for e in ("9004", "9005", "9006")] == ["L3", "L4", ""]
    assert "already in this contest" in row_of(plan, "9006").why
    assert "2nd-best Top 1%" in row_of(plan, "9005").why


def test_a_suggestion_skips_a_lineup_sam_already_put_in_that_contest(entries_file):
    plan = ent.plan_fill(
        entries_file, tab_rows_for(e9004="L3"), sheet_lineups(**STANDARD_LINEUPS), now=BEFORE
    )
    assert [row_of(plan, e).suggested for e in ("9005", "9006")] == ["L4", ""]
    assert row_of(plan, "9004").lineup == 3  # his pick is untouched


def test_with_no_marked_lineup_the_unmarked_ones_stand_in_and_the_reason_says_so(entries_file):
    lineups = sheet_lineups(l1=(LINEUP_1, "", 0.58, 0.004))
    plan = ent.plan_fill(entries_file, {}, lineups, now=BEFORE)
    assert row_of(plan, "9002").suggested == "L1" and "(unmarked)" in row_of(plan, "9002").why
    none = ent.plan_fill(entries_file, {}, sheet_lineups(), now=BEFORE)
    assert row_of(none, "9002").suggested == "" and "no Cash-marked lineup" in row_of(none, "9002").why


def test_the_suggester_is_a_replaceable_hook(entries_file):
    def best_in(entry, lineups, taken):
        return ent.Suggestion(4, f"Best in says L4 for {entry.format}")

    plan = ent.plan_fill(entries_file, {}, sheet_lineups(**STANDARD_LINEUPS), now=BEFORE, suggester=best_in)
    assert {r.suggested for r in plan.rows} == {"L4"} and row_of(
        plan, "9001"
    ).why == "Best in says L4 for Double Up"


# -- filling what Sam set -----------------------------------------------------------------------------------


def test_a_set_entry_gets_its_nine_slots_and_only_changed_entries_go_in_the_upload(entries_file):
    # 9002 already holds lineup 1 at DK (so it is left out of the upload); 9001 and 9003 do not.
    tab = tab_rows_for(e9001="L1", e9002="L1", e9003="L1", e9007="L3")
    plan = ent.plan_fill(entries_file, tab, sheet_lineups(**STANDARD_LINEUPS), now=BEFORE)
    assert row_of(plan, "9001").cells == LINEUP_1_AT_DK
    assert [r.entry.entry_id for r in plan.upload_rows] == ["9001", "9003", "9007"]
    assert plan.unchanged_at_dk == 1
    assert plan.summary == "8 entries: 4 set (1 DU + 2 H2H <- L1; 1 GPP <- L3), 4 blank"


def test_the_summary_line_has_dks_shape_for_a_big_week():
    rows = []
    entry = lambda fmt: ent.Entry(ent.FileEntry("1", "c", "9", "$1", ("",) * 9), fmt)  # noqa: E731
    for fmt, lineup in [("H2H", 1)] * 20 + [("Double Up", 1)] * 3 + [("N-max GPP", 4)]:
        rows.append(ent.RowPlan(entry(fmt), lineup))
    rows += [ent.RowPlan(entry("H2H"), None) for _ in range(3)]
    assert ent.summary_line(rows) == "27 entries: 24 set (20 H2H + 3 DU <- L1; 1 GPP <- L4), 3 blank"


def test_the_upload_file_is_dks_format_with_only_the_rows_sam_set(entries_file, tmp_path):
    from dfs.lineups import export_csv

    tab = tab_rows_for(e9001="L1", e9003="L2")
    plan = ent.plan_fill(entries_file, tab, sheet_lineups(**STANDARD_LINEUPS), now=BEFORE)
    path = tmp_path / "upload.csv"
    assert export_csv(ent.upload_entries(plan), path) == 2
    rows = list(csv.reader(open(path, encoding="utf-8")))
    assert rows[0] == list(DK_UPLOAD_HEADER)
    assert [r[0] for r in rows[1:]] == ["9001", "9003"]  # nobody else
    assert rows[1][4:] == LINEUP_1_AT_DK


def test_reservations_with_blank_slots_are_filled_when_set_and_listed_when_not(entries_file):
    plan = ent.plan_fill(
        entries_file, tab_rows_for(e9009="L4"), sheet_lineups(**STANDARD_LINEUPS), now=BEFORE
    )
    assert row_of(plan, "9009").upload and all(row_of(plan, "9009").cells)
    blank_line = next(c for c in plan.checks if "with no Lineup" in c)
    assert blank_line.startswith("7 entries with no Lineup (they do not go)")
    assert "NFL $1 srwildman vs. TBD x2" in blank_line  # listed once per contest, with a count


def test_the_flex_rule_puts_the_latest_kickoff_in_flex(entries_file):
    lineups = sheet_lineups(l1=(LINEUP_1_FLEX_WRONG, "Cash", 0.58, 0.004))
    plan = ent.plan_fill(entries_file, tab_rows_for(e9001="L1"), lineups, now=BEFORE)
    assert row_of(plan, "9001").cells == LINEUP_1_AT_DK  # Rex Late (4:05) moved to FLEX, Ray Early to RB2
    assert any("Rex Late to FLEX" in n for n in plan.flex_notes)


def test_a_started_player_is_never_touched(entries_file):
    # DK holds lineup 3 on entry 9003; Sam now sets lineup 4 after the 1:00 games kicked off. Every slot whose
    # current player has started keeps him, and no started player is put in (Rob/Ray/Wade/Tim never are).
    held = {"9003": cells(*LINEUP_3)}
    entries = [(*e[:4], held.get(e[0], e[4])) for e in ENTRIES]
    parsed = ent.read_entries_file(write_entries_file(entries_file.path.parent / "late.csv", entries=entries))
    plan = ent.plan_fill(parsed, tab_rows_for(e9003="L4"), sheet_lineups(**STANDARD_LINEUPS), now=AFTER_EARLY)
    row = row_of(plan, "9003")
    assert row.cells == cells(
        "Quinn Early", "Ray Early", "Rod Night", "Walt Early", "Will Early", "Wes Late", "Tom Early",
        "Wyatt Night", "Aces",
    )  # fmt: skip
    assert row.upload  # the two unstarted swaps (WR3 and FLEX) still go
    assert any("QB Quentin Late" not in c and "Quinn Early started, so it keeps" in c for c in plan.checks)
    assert not {CELL["2001"], CELL["3002"], CELL["4002"]} & set(row.cells)  # started players never put in


def test_a_started_player_is_never_put_into_an_empty_slot(entries_file):
    plan = ent.plan_fill(
        entries_file, tab_rows_for(e9009="L1"), sheet_lineups(**STANDARD_LINEUPS), now=AFTER_EARLY
    )
    row = row_of(plan, "9009")
    assert row.cells[0] == "" and not row.upload  # Quinn Early started and DK holds nothing there
    assert any("stays blank" in c for c in plan.checks)


# -- the checks (reported, never fixed) ---------------------------------------------------------------------


def test_a_lineup_twice_in_one_multi_entry_contest_is_reported(entries_file):
    tab = tab_rows_for(e9004="L3", e9005="L3", e9006="L4")
    plan = ent.plan_fill(entries_file, tab, sheet_lineups(**STANDARD_LINEUPS), now=BEFORE)
    assert any("L3 is in this contest 2 times" in c and "9004, 9005" in c for c in plan.checks)
    assert row_of(plan, "9004").lineup == row_of(plan, "9005").lineup == 3  # reported, not fixed


def test_a_gpp_lineup_in_a_cash_contest_and_a_cash_lineup_in_a_gpp_are_reported(entries_file):
    tab = tab_rows_for(e9001="L3", e9007="L1")
    plan = ent.plan_fill(entries_file, tab, sheet_lineups(**STANDARD_LINEUPS), now=BEFORE)
    assert any("L3 is marked GPP but this is a DU contest" in c for c in plan.checks)
    assert any("L1 is marked Cash but this is a GPP contest" in c for c in plan.checks)


def test_an_empty_or_incomplete_lineup_is_reported_once_and_not_written(entries_file):
    broken = list(LINEUP_2)
    broken[3] = ""
    lineups = sheet_lineups(**{**STANDARD_LINEUPS, "l2": (tuple(broken), "Cash", 0.5, 0.01)})
    tab = tab_rows_for(e9001="L2", e9003="L2", e9007="L9")
    plan = ent.plan_fill(entries_file, tab, lineups, now=BEFORE)
    incomplete = [c for c in plan.checks if c.startswith("L2 is incomplete")]
    assert len(incomplete) == 1 and "WR is empty" in incomplete[0] and "2 entries" in incomplete[0]
    assert any(c.startswith("L9 is empty") for c in plan.checks)
    assert plan.upload_rows == [] and not any(row_of(plan, "9001").cells)


def test_a_name_dks_table_lacks_or_in_the_wrong_slot_is_incomplete(entries_file):
    wrong = list(LINEUP_1)
    wrong[1] = "Walt Early"  # a WR in an RB slot
    wrong[3] = "Nobody Known"
    res = ent.resolve_lineup(sheet_lineups(l1=(tuple(wrong), "", None, None))[0], entries_file.players)
    assert not res.complete
    assert "RB: Walt Early is WR, not a RB" in res.problems
    assert any("Nobody Known is not in this file's player table" in p for p in res.problems)
    twice = ent.resolve_lineup(
        sheet_lineups(l1=(("Quinn Early", "Rob Early", "Rob Early", *LINEUP_1[3:]), "", None, None))[0],
        entries_file.players,
    )
    assert "a player is used twice" in twice.problems


# -- --again after a late swap ------------------------------------------------------------------------------


def test_again_rewrites_only_the_entries_using_the_lineup_that_changed(entries_file):
    first = ent.plan_fill(
        entries_file,
        tab_rows_for(e9001="L1", e9003="L1", e9007="L3"),
        sheet_lineups(**STANDARD_LINEUPS),
        now=BEFORE,
    )
    on_tab = {
        r.entry.entry_id: ent.TabRow(r.entry.entry_id, f"L{r.lineup}", tuple(r.cells), row=2 + i)
        for i, r in enumerate(first.rows)
        if r.lineup
    }
    # a late swap on lineup 1: Rob Early out, Rod Night in (RB1)
    swapped = list(LINEUP_1)
    swapped[1] = "Rod Night"
    lineups = sheet_lineups(**{**STANDARD_LINEUPS, "l1": (tuple(swapped), "Cash", 0.6, 0.004)})
    again = ent.plan_fill(entries_file, on_tab, lineups, now=BEFORE, again=True)
    assert [r.entry.entry_id for r in again.rows if r.changed] == ["9001", "9003"]  # not 9007 (L3)
    assert CELL["2004"] in row_of(again, "9001").cells
    assert tuple(row_of(again, "9007").cells) == on_tab["9007"].cells
    assert [r.entry.entry_id for r in again.upload_rows] == ["9001", "9003"]
    assert all(r.suggested == "" for r in again.rows)  # no suggestion recomputed


# -- re-running never loses a dropdown (matched by Entry ID) ------------------------------------------------


def test_rerunning_never_overwrites_a_dropdown_sam_set_even_when_the_file_changes_order(
    entries_file, tmp_path
):
    first = ent.plan_fill(
        entries_file, tab_rows_for(e9004="L3", e9009="L4"), sheet_lineups(**STANDARD_LINEUPS), now=BEFORE
    )
    grid = tab_grid(first.rows, kept=[])
    on_tab = {
        row[0]: ent.TabRow(
            row[0], row[DK_UPLOAD_SHEET_HEADER.index("Lineup")], tuple(row[4:13]), row=2 + i, raw=tuple(row)
        )
        for i, row in enumerate(grid)
    }
    # DK's next download lists the entries in another order and has one fewer
    reordered = [e for e in reversed(ENTRIES) if e[0] != "9007"]
    parsed = ent.read_entries_file(write_entries_file(tmp_path / "next.csv", entries=reordered))
    second = ent.plan_fill(parsed, on_tab, sheet_lineups(**STANDARD_LINEUPS), now=BEFORE)
    assert row_of(second, "9004").lineup == 3 and row_of(second, "9009").lineup == 4
    kept = [t for t in on_tab.values() if t.entry_id not in {r.entry.entry_id for r in second.rows}]
    assert [t.entry_id for t in kept] == ["9007"]  # not listed any more: written back, never dropped
    regrid = tab_grid(second.rows, kept)
    assert [row[0] for row in regrid][-1] == "9007"
    lineup_col = DK_UPLOAD_SHEET_HEADER.index("Lineup")
    assert {row[0]: row[lineup_col] for row in regrid}["9004"] == "L3"
    assert {row[0]: row[lineup_col] for row in regrid}["9009"] == "L4"


def test_a_lineup_label_outside_the_dropdown_is_no_choice():
    assert ent.lineup_choice("L3", 20) == 3
    assert ent.lineup_choice("l12", 20) == 12
    assert ent.lineup_choice("L21", 20) is None
    assert ent.lineup_choice("", 20) is None
    assert ent.lineup_choice("3", 20) is None


# -- entries print: every lineup in DK's format, no pairing --------------------------------------------------


def test_print_gives_every_lineup_in_dk_slot_format_with_totals_in_front(entries_file, tmp_path):
    lineups = sheet_lineups(**{**STANDARD_LINEUPS, "l5": (LINEUP_1_FLEX_WRONG, "", None, None)})
    rows = ent.print_rows(lineups, entries_file.players, BEFORE)
    assert [r.lineup.number for r in rows] == [1, 2, 3, 4, 5]  # unbuilt lineups are not printed
    one = rows[0]
    assert one.cells == LINEUP_1_AT_DK  # DK slot order, `Name (ID)`
    record = one.as_record()
    assert record[:4] == ["L1", "141.0", "131.0", "126.0%"]  # CalPts, ProjPts, summed Own% in front
    assert record[4:13] == LINEUP_1_AT_DK
    assert rows[4].cells == LINEUP_1_AT_DK  # the FLEX rule applies to a lineup nobody paired
    assert "Rex Late to FLEX" in rows[4].notes[0]

    path = tmp_path / "dk_lineups.csv"
    assert ent.write_print_csv(rows, path) == 5
    written = list(csv.reader(open(path, encoding="utf-8")))
    assert written[0] == ["Lineup", "CalPts", "ProjPts", "Own%", *ROSTER_SLOTS, "Note"]
    assert written[1][:4] == ["L1", "141.0", "131.0", "126.0%"]
    assert written[1][4:13] == LINEUP_1_AT_DK


def test_print_flags_a_started_player_and_leaves_the_slot_alone(entries_file):
    rows = ent.print_rows(sheet_lineups(**STANDARD_LINEUPS), entries_file.players, AFTER_EARLY)
    one = rows[0]
    assert one.cells == LINEUP_1_AT_DK  # nothing is moved or swapped out
    assert any(n == "started: QB Quinn Early" for n in one.notes)
    assert not any("Rex Late" in n for n in one.notes if n.startswith("started"))  # his game has not started


def test_print_still_prints_a_lineup_that_does_not_resolve_with_the_reason(entries_file):
    odd = list(LINEUP_1)
    odd[3] = "Nobody Known"
    odd[6] = ""
    rows = ent.print_rows(sheet_lineups(l1=(tuple(odd), "", 0.5, 0.01)), entries_file.players, BEFORE)
    assert rows[0].cells[3] == "?Nobody Known" and rows[0].cells[6] == ""
    assert any("not in this file's player table" in n for n in rows[0].notes)


# -- DK Upload, the export and the sheet-side pieces ---------------------------------------------------------


def test_the_export_leaves_blank_entries_out_instead_of_failing_on_them():
    full = ParsedEntry(2, "1", "c", "9", "$1", LINEUP_1_AT_DK)
    blank = ParsedEntry(3, "2", "c", "9", "$1", [""] * 9)
    assert split_blank_entries([full, blank]) == ([full], [blank])


def test_the_dk_upload_header_still_starts_with_dks_own_columns():
    assert DK_UPLOAD_SHEET_HEADER[: len(DK_UPLOAD_HEADER)] == DK_UPLOAD_HEADER
    assert DK_UPLOAD_SHEET_HEADER[len(DK_UPLOAD_HEADER) :] == ("Format", "Lineup", "Suggested", "Why")
