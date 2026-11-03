"""Plan D item 5: `dfs entries record` (dfs.entered)."""

from __future__ import annotations

from datetime import UTC, datetime

from dfs import entered, suggest_state
from dfs.entries import Entry, FileEntry, SheetLineup, TablePlayer

NINE = ["Dak Prescott", "Rhamondre Stevenson", "Jonah Coleman", "CeeDee Lamb", "Zay Flowers", "Puka Nacua"]
NINE += ["Mason Taylor", "Hunter Henry", "Broncos"]


def _state(week=8):
    other = [*NINE[:7], "Travis Kelce", "Bills"]  # 7 of 9
    close = [*NINE[:8], "Bills"]  # 8 of 9
    state = suggest_state.StateFile(week=week)
    for number, (kind, names) in enumerate([("gpp", other), ("cash", close)], start=1):
        b = suggest_state.BuildRecord(number=number, kind=kind, title=f"t{number}", names=names)
        state.rounds.append(
            suggest_state.RoundRecord(round=1, at="x", command="suggest", rules={}, builds=[b])
        )
    return state


def test_a_lineup_matches_the_build_it_shares_8_or_9_players_with():
    state = _state()
    assert entered.match_build(NINE, state).number == 2 and entered.match_build(NINE, state).shared == 8
    exact = suggest_state.BuildRecord(number=5, kind="cash", title="t5", names=["Broncos ", *NINE[:8]])
    state.rounds.append(
        suggest_state.RoundRecord(round=3, at="y", command="iterate", rules={}, builds=[exact])
    )
    best = entered.match_build(["Dak Prescott Jr.", *NINE[1:]], state)  # names normalised
    assert (best.round, best.number, best.shared) == (3, 5, 9)
    assert entered.match_build([*NINE[:6], "A", "B", "C"], state) is None


def _player(i, name):
    return TablePlayer(
        "WR", name, str(100 + i), "WR/FLEX", 5000, "", "DEN", datetime(2026, 11, 1, 18, tzinfo=UTC)
    )


def test_from_entries_groups_entries_by_lineup_and_contest():
    players = [_player(i, n) for i, n in enumerate(NINE)]
    cells = tuple(p.cell for p in players)
    flipped = (cells[1], cells[0], *cells[2:])  # same nine in another order: the same lineup
    entries = [
        Entry(FileEntry("1", "DU A", "c1", "$5", cells), "Double Up"),
        Entry(FileEntry("2", "DU A", "c1", "$5", flipped), "Double Up"),
        Entry(FileEntry("3", "SE", "c2", "$3", cells), "SE GPP"),
        Entry(FileEntry("4", "DU B", "c3", "$5", ("",) * 9), "Double Up"),  # blank: not entered
    ]
    lineups = entered.from_entries(entries, players)
    assert len(lineups) == 1
    (lu,) = lineups
    assert lu.names == NINE and lu.ids == [p.dk_id for p in players]
    assert [(c.contest_id, c.entry_ids) for c in lu.contests] == [("c1", ["1", "2"]), ("c2", ["3"])]


def test_record_round_trips_and_week_close_warns_without_one(monkeypatch):
    suggest_state.save_state(_state())
    sheet = [SheetLineup(1, tuple(NINE), "Cash"), SheetLineup(2, ("",) * 9)]
    record = entered.make_record(2026, 8, "Lineups tab of Week 8", entered.from_sheet(sheet))
    assert [lu.label for lu in record.lineups] == ["L1"] and record.lineups[0].source.number == 2
    assert "missing" not in entered.missing_warning(2026, 8) and "no record" in entered.missing_warning(
        2026, 8
    )
    path = entered.save(record)
    assert path.name == "2026-wk08.json"
    assert entered.load(2026, 8) == record
    assert entered.missing_warning(2026, 8) == ""
    text = "\n".join(entered.summary_lines(record))
    assert "build 2 of round 1 (cash, 8/9)" in text and "1 of 1 came from a Suggested build" in text


def test_a_week_with_no_history_says_so_and_reads_as_own():
    suggest_state.STATE_DIR.mkdir(parents=True, exist_ok=True)
    (suggest_state.STATE_DIR / "_polluted_6.json").write_text("{}")
    record = entered.make_record(2026, 6, "x", entered.from_sheet([SheetLineup(1, tuple(NINE))]))
    assert record.lineups[0].source is None and record.lineups[0].source_text == "own"
    assert "Week 6 has no suggest history" in record.note
    assert "every lineup reads as your own" in "\n".join(entered.summary_lines(record))


def test_file_week_reads_the_latest_kickoff():
    assert entered.file_week([_player(0, "a")]) == (2026, 8)
    assert entered.file_week([]) is None
