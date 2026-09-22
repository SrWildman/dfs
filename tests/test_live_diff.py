import pandas as pd

from dfs.live_diff import diff_edge_flags, diff_queue_changes


def _edge_row(id_, name, position, team, flag):
    return {"Id": id_, "Name": name, "Position": position, "Team": team, "Flags": flag}


def _queue_row(id_, name, position, team, avail, flag, salary):
    return {
        "Id": id_,
        "Name": name,
        "Position": position,
        "Team": team,
        "Avail": avail,
        "Flags": flag,
        "Salary": salary,
    }


def test_diff_edge_flags_reports_only_changed_rows():
    previous = pd.DataFrame(
        [
            _edge_row("1", "Player A", "RB", "KC", ""),
            _edge_row("2", "Player B", "WR", "SF", "LEVERAGE"),
        ]
    )
    current = pd.DataFrame(
        [
            _edge_row("1", "Player A", "RB", "KC", "OUT"),
            _edge_row("2", "Player B", "WR", "SF", "LEVERAGE"),
        ]
    )

    changes = diff_edge_flags(previous, current)

    assert len(changes) == 1
    row = changes.iloc[0]
    assert row["Name"] == "Player A"
    assert row["OldFlag"] == ""
    assert row["NewFlag"] == "OUT"


def test_diff_edge_flags_treats_new_player_as_previously_unflagged():
    previous = pd.DataFrame([_edge_row("1", "Player A", "RB", "KC", "")])
    current = pd.DataFrame(
        [
            _edge_row("1", "Player A", "RB", "KC", ""),
            _edge_row("2", "Player B", "WR", "SF", "WIND"),
        ]
    )

    changes = diff_edge_flags(previous, current)

    assert len(changes) == 1
    assert changes.iloc[0]["Name"] == "Player B"
    assert changes.iloc[0]["OldFlag"] == ""
    assert changes.iloc[0]["NewFlag"] == "WIND"


def test_diff_edge_flags_reports_a_cleared_flag():
    previous = pd.DataFrame([_edge_row("1", "Player A", "RB", "KC", "LINE↑")])
    current = pd.DataFrame([_edge_row("1", "Player A", "RB", "KC", "")])

    changes = diff_edge_flags(previous, current)

    assert len(changes) == 1
    assert changes.iloc[0]["OldFlag"] == "LINE↑"
    assert changes.iloc[0]["NewFlag"] == ""


def test_diff_edge_flags_returns_empty_frame_when_nothing_changed():
    previous = pd.DataFrame([_edge_row("1", "Player A", "RB", "KC", "CHALK")])
    current = pd.DataFrame([_edge_row("1", "Player A", "RB", "KC", "CHALK")])

    changes = diff_edge_flags(previous, current)

    assert changes.empty


def test_diff_edge_flags_sorts_newly_flagged_players_first():
    previous = pd.DataFrame(
        [
            _edge_row("1", "Zack", "WR", "KC", "LEVERAGE"),
            _edge_row("2", "Amy", "RB", "SF", ""),
        ]
    )
    current = pd.DataFrame(
        [
            _edge_row("1", "Zack", "WR", "KC", ""),  # flag cleared
            _edge_row("2", "Amy", "RB", "SF", "OUT"),  # newly flagged
        ]
    )

    changes = diff_edge_flags(previous, current)

    assert list(changes["Name"]) == ["Amy", "Zack"]


def test_diff_queue_changes_catches_a_move_to_questionable():
    # Flags' own "OUT" token (derived.OUT_STATUSES) never fires for Q --
    # this is the gap diff_edge_flags alone can't cover.
    previous = pd.DataFrame([_queue_row("1", "Player A", "RB", "KC", "", "", 6000)])
    current = pd.DataFrame([_queue_row("1", "Player A", "RB", "KC", "Q", "", 6000)])

    changes = diff_queue_changes(previous, current)

    assert len(changes) == 1
    assert "Avail -> Q" in changes.iloc[0]["Reason"]


def test_diff_queue_changes_catches_a_salary_change_with_no_flag_change():
    previous = pd.DataFrame([_queue_row("1", "Player A", "RB", "KC", "", "", 6000)])
    current = pd.DataFrame([_queue_row("1", "Player A", "RB", "KC", "", "", 5800)])

    changes = diff_queue_changes(previous, current)

    assert len(changes) == 1
    assert "Salary 6000 -> 5800" in changes.iloc[0]["Reason"]


def test_diff_queue_changes_still_catches_a_flag_change():
    previous = pd.DataFrame([_queue_row("1", "Player A", "RB", "KC", "", "", 6000)])
    current = pd.DataFrame([_queue_row("1", "Player A", "RB", "KC", "", "WIND", 6000)])

    changes = diff_queue_changes(previous, current)

    assert len(changes) == 1
    assert "Flags (none) -> WIND" in changes.iloc[0]["Reason"]


def test_diff_queue_changes_does_not_retrigger_for_an_already_out_player():
    previous = pd.DataFrame([_queue_row("1", "Player A", "RB", "KC", "OUT", "OUT", 6000)])
    current = pd.DataFrame([_queue_row("1", "Player A", "RB", "KC", "OUT", "OUT", 6000)])

    changes = diff_queue_changes(previous, current)

    assert changes.empty


def test_diff_queue_changes_ignores_a_brand_new_player_with_no_signal():
    previous = pd.DataFrame([_queue_row("1", "Player A", "RB", "KC", "", "", 6000)])
    current = pd.DataFrame(
        [
            _queue_row("1", "Player A", "RB", "KC", "", "", 6000),
            _queue_row("2", "Player B", "WR", "SF", "", "", 5500),
        ]
    )

    changes = diff_queue_changes(previous, current)

    assert changes.empty


def test_diff_queue_changes_surfaces_a_brand_new_players_real_flag():
    previous = pd.DataFrame([_queue_row("1", "Player A", "RB", "KC", "", "", 6000)])
    current = pd.DataFrame(
        [
            _queue_row("1", "Player A", "RB", "KC", "", "", 6000),
            _queue_row("2", "Player B", "WR", "SF", "", "LEVERAGE", 5500),
        ]
    )

    changes = diff_queue_changes(previous, current)

    assert len(changes) == 1
    assert changes.iloc[0]["Name"] == "Player B"


def test_diff_queue_changes_reports_multiple_triggers_at_once():
    previous = pd.DataFrame([_queue_row("1", "Player A", "RB", "KC", "", "", 6000)])
    current = pd.DataFrame([_queue_row("1", "Player A", "RB", "KC", "Q", "WIND", 5800)])

    changes = diff_queue_changes(previous, current)

    reason = changes.iloc[0]["Reason"]
    assert "Avail -> Q" in reason
    assert "Flags" in reason
    assert "Salary" in reason


def test_diff_queue_changes_returns_empty_frame_when_nothing_changed():
    previous = pd.DataFrame([_queue_row("1", "Player A", "RB", "KC", "", "CHALK", 6000)])
    current = pd.DataFrame([_queue_row("1", "Player A", "RB", "KC", "", "CHALK", 6000)])

    changes = diff_queue_changes(previous, current)

    assert changes.empty
