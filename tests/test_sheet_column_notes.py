"""Per-column notes: every new column is explained, and the notes land on the right header cells."""

import pandas as pd

from dfs import sheet_column_notes as notes
from dfs.sheet_model_check import build_layout
from dfs.sheet_views import SLATE_HEADER, SLATE_TEAMS_COLHEADER
from dfs.usage_metrics import USAGE_METRIC_COLUMNS


def test_every_usage_column_and_every_slate_metric_header_has_a_note():
    # The guard: a column added without a definition fails here instead of shipping unexplained.
    assert set(USAGE_METRIC_COLUMNS) <= set(notes.USAGE_NOTES)
    assert set(SLATE_TEAMS_COLHEADER) <= set(notes.SLATE_TEAMS_NOTES)
    new_game_columns = {"GameEnv", "Pace", "PROE", "Expl%"}
    assert new_game_columns <= set(SLATE_HEADER) and new_game_columns <= set(notes.SLATE_GAME_NOTES)


def test_every_model_check_header_is_explained():
    scored = pd.DataFrame(
        [
            {
                "week": 3,
                "Position": "WR" if i % 2 else "RB",
                "ProjPts": 8.0 + i % 9,
                "DkActual": 6.0 + i % 11,
                "Ceiling": 18.0 + i % 5,
                "Salary": 4000 + 100 * i,
                "Val": 2.0 + (i % 12) / 10,
                "ValAdj": float(i),
                "AggPts": 8.0 + i % 9,
                "SleeperPts": 9.0 + i % 7,
                "FantasyProsPts": 9.5 + i % 6,
                "Flags": "CHALK" if i % 10 == 0 else "",
                "RosterablePool": True,
                "Status": "scored",
            }
            for i in range(60)
        ]
    )
    layout = build_layout(scored, weeks=[1, 2, 3, 4])  # four weeks so the consistency table appears too
    headers = {str(cell) for row in layout.header_rows for cell in layout.rows[row - 1] if cell != ""}
    undocumented = {h for h in headers if h not in notes.MODEL_CHECK_NOTES and h != "Position"}
    assert not undocumented, f"Model Check columns with no note: {sorted(undocumented)}"


def test_usage_notes_state_the_window_and_the_week_the_data_runs_through(monkeypatch):
    monkeypatch.setattr(notes, "usage_through_week", lambda: 3)
    note = notes.usage_notes()["Tgt%"]
    assert "last 3 games played" in note and "Data through Week 3" in note
    assert "skipped, never counted as a zero" in note or "skipped" in note
    monkeypatch.setattr(notes, "usage_through_week", lambda: None)
    assert "Data through" not in notes.usage_notes()["Tgt%"]  # no usage data: no invented week


def test_through_week_is_read_from_the_latest_usage_sync(monkeypatch):
    monkeypatch.setattr(notes.store, "load_current", lambda name: pd.DataFrame({"ThroughWeek": [4, 4]}))
    assert notes.usage_through_week() == 4

    def missing(name):
        raise FileNotFoundError

    monkeypatch.setattr(notes.store, "load_current", missing)
    assert notes.usage_through_week() is None


# --- the one registry: every header explained, nothing stale, notes replaced not accumulated ---------


def test_every_header_on_edgeraw_player_pool_and_lineups_has_a_note():
    from dfs import derived
    from dfs.sheet_columns import LINEUPS_COLUMN_ORDER, PLAYER_POOL_COLUMN_ORDER

    registry = notes.player_tab_notes()
    for tab, headers in (
        ("EdgeRaw", derived.EDGE_SHEET_ORDER),
        ("Player Pool", PLAYER_POOL_COLUMN_ORDER),
        ("Lineups", LINEUPS_COLUMN_ORDER),
    ):
        # EdgeRaw alone carries the shadow sources' columns (`EspnPts`, `PropPts`).
        registry = notes.edgeraw_notes() if tab == "EdgeRaw" else notes.player_tab_notes()
        missing = [h for h in headers if h not in registry]
        assert not missing, f"{tab} headers with no note: {missing}"


def test_every_edge_finder_and_board_header_has_a_note():
    from dfs import edge_finder_tab as eft
    from dfs import sheet_views as sv

    registry = notes.edge_finder_tab_notes()
    headers = set(eft.LEAD) | set(eft.TRAILING_HEADERS) | set(eft.MATCHUP_LEAD)
    for group in (
        eft.CASH_COLUMNS,
        eft.GPP_COLUMNS,
        eft.LEVERAGE_COLUMNS,
        eft.CHALK_COLUMNS,
        eft.PUNT_COLUMNS,
        eft.DISAGREE_COLUMNS,
        eft.BENEFICIARY_COLUMNS,
        eft.ABSENCE_COLUMNS,
        eft.TREND_HEADERS,
        eft.MATCHUP_COLUMNS,
        eft.SIGNAL_COLUMNS,
        eft.R6_SIGNAL_COLUMNS,
    ):
        headers |= set(group)
    missing = sorted(h for h in headers if h and h not in registry)
    assert not missing, f"Edge Finder headers with no note: {missing}"

    board = {**notes.player_tab_notes(), **notes.BOARD_NOTES}
    board_headers = set()
    for group in (
        sv.BOARD_SLATE_COLHEADER,
        sv.BOARD_LIST_COLHEADER,
        sv.BOARD_QUEUE_COLHEADER,
        sv.BOARD_CHECK_COLHEADER,
        sv.BOARD_POOL_COLHEADER,
        sv.BOARD_STACKS_COLHEADER,
        sv.BOARD_STACK_COLHEADER,
        sv.BOARD_CHALK_COLHEADER,
    ):
        board_headers |= set(group)
    missing = sorted(h for h in board_headers if h and h not in board)
    assert not missing, f"Board headers with no note: {missing}"


def test_no_note_mentions_a_removed_feature():
    everything = {
        **notes.player_tab_notes(),
        **notes.edge_finder_tab_notes(),
        **notes.BOARD_NOTES,
        **notes.MODEL_CHECK_NOTES,
        **notes.SLATE_TEAMS_NOTES,
    }
    for header, text in everything.items():
        for removed in notes.REMOVED_FEATURES:
            assert removed.lower() not in text.lower(), f"{header!r}'s note mentions the removed {removed!r}"


class _RowNotesClient:
    def __init__(self, header):
        self.header = header
        self.calls = []

    def read_range(self, tab, a1):
        return [self.header]

    def set_row_notes(self, tab, row, texts, *, first_col=0):
        self.calls.append((tab, row, list(texts), first_col))


def test_applying_notes_replaces_the_row_so_a_header_without_a_note_loses_its_old_one():
    client = _RowNotesClient(["Name", "Mystery column", "Tgt%"])
    written = notes.apply_header_notes(client, "Player Pool", 2, {"Name": "n", "Tgt%": "t"})
    assert written == 2
    assert client.calls == [("Player Pool", 2, ["n", "", "t"], 0)]  # "" = clear whatever note was there


def test_row_one_keeps_its_tab_note_in_column_a():
    client = _RowNotesClient(["Pool", "Name", "Team"])
    notes.apply_header_notes(client, "EdgeRaw", 1, {"Pool": "p", "Name": "n", "Team": "t"})
    assert client.calls == [("EdgeRaw", 1, ["n", "t"], 1)]  # column A (the tab's own note) is not touched
