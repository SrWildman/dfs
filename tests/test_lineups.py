from datetime import UTC, datetime

import pandas as pd

from dfs.kickoff import parse_kickoff
from dfs.lineups import (
    build_salary_lookup,
    export_csv,
    kickoffs_by_id,
    parse_entries,
    parse_player_cell,
    reorder_flex,
    validate_entry,
)
from dfs.models import ROSTER_SLOTS

HEADER = ["Entry ID", "Contest Name", "Contest ID", "Entry Fee", *ROSTER_SLOTS, "", "Instructions"]


def _lineup_row(entry_id="E1", contest="Contest", contest_id="C1", fee="$5", players=None):
    players = players or {
        "QB": "Josh Allen (1)",
        "RB1": "Saquon Barkley (2)",
        "RB2": "Derrick Henry (3)",
        "WR1": "Tyreek Hill (4)",
        "WR2": "Ja'Marr Chase (5)",
        "WR3": "Justin Jefferson (6)",
        "TE": "Travis Kelce (7)",
        "FLEX": "CeeDee Lamb (8)",
        "DST": "Ravens  (9)",
    }
    return [entry_id, contest, contest_id, fee] + list(players.values()) + ["", ""]


def _salary_df():
    rows = [
        ("QB", "Josh Allen", 1, "BUF", 7000),
        ("RB", "Saquon Barkley", 2, "PHI", 6500),
        ("RB", "Derrick Henry", 3, "BAL", 6000),
        ("WR", "Tyreek Hill", 4, "MIA", 6500),
        ("WR", "Ja'Marr Chase", 5, "CIN", 6000),
        ("WR", "Justin Jefferson", 6, "MIN", 5500),
        ("TE", "Travis Kelce", 7, "KC", 4500),
        ("WR", "CeeDee Lamb", 8, "DAL", 5000),
        ("DST", "Ravens", 9, "BAL", 3000),
    ]
    return pd.DataFrame(rows, columns=["Position", "Name", "ID", "TeamAbbrev", "Salary"])


def test_parse_player_cell():
    assert parse_player_cell("Josh Allen (12345)") == ("Josh Allen", "12345")
    assert parse_player_cell("Ravens  (9)") == ("Ravens", "9")
    assert parse_player_cell("") is None
    assert parse_player_cell("   ") is None
    assert parse_player_cell("not a valid cell") is None


def test_parse_entries_skips_blank_rows_and_is_positional():
    rows = [HEADER, _lineup_row(entry_id="E1"), ["", "", "", "", "", "", "", "", "", "", "", "", ""]]
    entries = parse_entries(rows)
    assert len(entries) == 1
    assert entries[0].entry_id == "E1"
    assert entries[0].row_number == 2
    assert len(entries[0].slot_cells) == len(ROSTER_SLOTS)


def test_validate_entry_passes_for_legal_lineup():
    lookup = build_salary_lookup(_salary_df())
    entries = parse_entries([HEADER, _lineup_row()])
    result = validate_entry(entries[0], lookup, salary_cap=50000)
    assert result.ok, result.errors
    assert len(result.players) == 9
    assert result.salary_total == sum(p.salary for p in result.players)


def test_validate_entry_flags_salary_cap_violation():
    lookup = build_salary_lookup(_salary_df())
    entries = parse_entries([HEADER, _lineup_row()])
    result = validate_entry(entries[0], lookup, salary_cap=1000)
    assert not result.ok
    assert any("exceeds cap" in e for e in result.errors)


def test_validate_entry_flags_wrong_position_in_flex():
    lookup = build_salary_lookup(_salary_df())
    players = {
        "QB": "Josh Allen (1)",
        "RB1": "Saquon Barkley (2)",
        "RB2": "Derrick Henry (3)",
        "WR1": "Tyreek Hill (4)",
        "WR2": "Ja'Marr Chase (5)",
        "WR3": "Justin Jefferson (6)",
        "TE": "Travis Kelce (7)",
        "FLEX": "Josh Allen (1)",
        "DST": "Ravens  (9)",
    }
    entries = parse_entries([HEADER, _lineup_row(players=players)])
    result = validate_entry(entries[0], lookup)
    assert not result.ok
    assert any("QB" in e and "FLEX" in e for e in result.errors)


def test_validate_entry_flags_duplicate_player():
    lookup = build_salary_lookup(_salary_df())
    players = {
        "QB": "Josh Allen (1)",
        "RB1": "Saquon Barkley (2)",
        "RB2": "Saquon Barkley (2)",
        "WR1": "Tyreek Hill (4)",
        "WR2": "Ja'Marr Chase (5)",
        "WR3": "Justin Jefferson (6)",
        "TE": "Travis Kelce (7)",
        "FLEX": "CeeDee Lamb (8)",
        "DST": "Ravens  (9)",
    }
    entries = parse_entries([HEADER, _lineup_row(players=players)])
    result = validate_entry(entries[0], lookup)
    assert not result.ok
    assert any("used twice" in e for e in result.errors)


def test_validate_entry_flags_unknown_player_id():
    lookup = build_salary_lookup(_salary_df())
    players = {
        "QB": "Someone Else (999)",
        "RB1": "Saquon Barkley (2)",
        "RB2": "Derrick Henry (3)",
        "WR1": "Tyreek Hill (4)",
        "WR2": "Ja'Marr Chase (5)",
        "WR3": "Justin Jefferson (6)",
        "TE": "Travis Kelce (7)",
        "FLEX": "CeeDee Lamb (8)",
        "DST": "Ravens  (9)",
    }
    entries = parse_entries([HEADER, _lineup_row(players=players)])
    result = validate_entry(entries[0], lookup)
    assert not result.ok
    assert any("not in current salary data" in e for e in result.errors)


def test_export_csv_writes_only_given_entries(tmp_path):
    entries = parse_entries([HEADER, _lineup_row(entry_id="E1"), _lineup_row(entry_id="E2")])
    out = tmp_path / "export.csv"
    n = export_csv(entries, out)
    assert n == 2
    content = out.read_text()
    assert content.splitlines()[0] == "Entry ID,Contest Name,Contest ID,Entry Fee," + ",".join(ROSTER_SLOTS)
    assert "E1" in content and "E2" in content


# --- the FLEX rule on export (Week 6): the latest kickoff among the flex position's players goes in FLEX ---

EARLY, LATE_SNF = "2026-10-18T13:00:00Z", "2026-10-18T20:20:00Z"
BEFORE = datetime(2026, 10, 18, 15, 0, tzinfo=UTC)  # 11 am ET
DURING = datetime(2026, 10, 18, 18, 0, tzinfo=UTC)  # 2 pm ET: the 1 pm games are under way


def _validated(*, flex_cell=None):
    lookup = build_salary_lookup(_salary_df())
    row = _lineup_row()
    if flex_cell:
        row[4 + ROSTER_SLOTS.index("FLEX")] = flex_cell
    return [validate_entry(e, lookup, salary_cap=50000) for e in parse_entries([HEADER, row])]


def _kicks(**late):
    """Every player at 1 pm except the ids in `late` (dk id -> kickoff string)."""
    ids = [str(i) for i in range(1, 10)]
    return {i: parse_kickoff(late.get(f"id{i}", EARLY)) for i in ids}


def test_export_moves_the_wr_with_the_latest_kickoff_into_flex_and_says_so():
    # WR3 (Justin Jefferson, id 6) plays Sunday night; the FLEX WR (Lamb, id 8) plays at 1 pm
    results = _validated()
    entries, messages = reorder_flex(results, _kicks(id6=LATE_SNF), BEFORE)
    cells = entries[0].slot_cells
    assert cells[ROSTER_SLOTS.index("FLEX")] == "Justin Jefferson (6)"
    assert cells[5] == "CeeDee Lamb (8)"  # the third WR slot took the old FLEX player
    assert [c for i, c in enumerate(cells) if i not in (5, 7)] == [
        c for i, c in enumerate(results[0].entry.slot_cells) if i not in (5, 7)
    ]
    assert messages == ["row 2 (E1): Moved Justin Jefferson to FLEX (plays SNF), CeeDee Lamb to WR"]


def test_export_leaves_a_lineup_whose_flex_already_plays_last_or_ties_or_has_started():
    results = _validated()
    assert reorder_flex(results, _kicks(id8=LATE_SNF), BEFORE) == ([results[0].entry], [])  # already right
    assert reorder_flex(results, _kicks(id6=LATE_SNF, id8=LATE_SNF), BEFORE)[1] == []  # a tie
    entries, messages = reorder_flex(results, _kicks(id6=LATE_SNF), DURING)  # Lamb's 1 pm game has started
    assert entries == [results[0].entry]
    assert messages == ["row 2 (E1): FLEX left as it is (a player's game has started)"]
    missing = _kicks(id6=LATE_SNF)
    missing["8"] = None
    assert reorder_flex(results, missing, BEFORE)[1] == [
        "row 2 (E1): FLEX not checked (a kickoff time is missing)"
    ]


def test_kickoffs_by_id_reads_the_edge_frame_through_kickoff_py():
    edge = pd.DataFrame({"Id": [6, 8], "GameStart": [LATE_SNF, ""]})
    got = kickoffs_by_id(edge)
    assert got["6"] == parse_kickoff(LATE_SNF) and got["8"] is None
