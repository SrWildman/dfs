import pandas as pd
import pytest

from dfs.lineups import (
    build_salary_lookup,
    export_csv,
    parse_entries,
    parse_player_cell,
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
        "QB": "Josh Allen (1)", "RB1": "Saquon Barkley (2)", "RB2": "Derrick Henry (3)",
        "WR1": "Tyreek Hill (4)", "WR2": "Ja'Marr Chase (5)", "WR3": "Justin Jefferson (6)",
        "TE": "Travis Kelce (7)", "FLEX": "Josh Allen (1)", "DST": "Ravens  (9)",
    }
    entries = parse_entries([HEADER, _lineup_row(players=players)])
    result = validate_entry(entries[0], lookup)
    assert not result.ok
    assert any("QB" in e and "FLEX" in e for e in result.errors)


def test_validate_entry_flags_duplicate_player():
    lookup = build_salary_lookup(_salary_df())
    players = {
        "QB": "Josh Allen (1)", "RB1": "Saquon Barkley (2)", "RB2": "Saquon Barkley (2)",
        "WR1": "Tyreek Hill (4)", "WR2": "Ja'Marr Chase (5)", "WR3": "Justin Jefferson (6)",
        "TE": "Travis Kelce (7)", "FLEX": "CeeDee Lamb (8)", "DST": "Ravens  (9)",
    }
    entries = parse_entries([HEADER, _lineup_row(players=players)])
    result = validate_entry(entries[0], lookup)
    assert not result.ok
    assert any("used twice" in e for e in result.errors)


def test_validate_entry_flags_unknown_player_id():
    lookup = build_salary_lookup(_salary_df())
    players = {
        "QB": "Someone Else (999)", "RB1": "Saquon Barkley (2)", "RB2": "Derrick Henry (3)",
        "WR1": "Tyreek Hill (4)", "WR2": "Ja'Marr Chase (5)", "WR3": "Justin Jefferson (6)",
        "TE": "Travis Kelce (7)", "FLEX": "CeeDee Lamb (8)", "DST": "Ravens  (9)",
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
