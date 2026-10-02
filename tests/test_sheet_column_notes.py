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
