import pandas as pd

from dfs.live_diff import diff_edge_flags


def _edge_row(id_, name, position, team, flag):
    return {"Id": id_, "Name": name, "Position": position, "Team": team, "Flag": flag}


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
