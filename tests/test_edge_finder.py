"""Edge Finder enrichment of the edge frame: columns filled by Id, fail-soft, live vs full."""

from datetime import UTC, datetime

import numpy as np
import pandas as pd
import pytest

from dfs import derived, edge_finder, probabilities, signals
from dfs.sources.base import SyncContext


def _edge_frame():
    rows = [
        (1, "Star", "WR", "DEN", "KC", 8000, 15.0, 14.0, "OUT"),
        (2, "Wr Two", "WR", "DEN", "KC", 5000, 11.0, 10.0, ""),
        (3, "Wr Three", "WR", "DEN", "KC", 3500, 5.0, 5.0, ""),
        (4, "Te One", "TE", "DEN", "KC", 4000, 7.0, 7.0, ""),
        (5, "Rb One", "RB", "DEN", "KC", 6000, 14.0, 13.0, ""),
    ]
    frame = pd.DataFrame(
        [
            {
                "Id": i,
                "Name": n,
                "Position": p,
                "Team": t,
                "Opp": o,
                "Salary": s,
                "ProjPts": pts,
                "AggPts": agg,
                "Avail": a,
                "GameStart": "2026-10-11T13:00:00Z",
            }
            for i, n, p, t, o, s, pts, agg, a in rows
        ]
    )
    for column in derived.EDGE_COLUMNS:
        if column not in frame.columns:
            frame[column] = "" if column == "Edge" else np.nan
    return frame[derived.EDGE_COLUMNS]


def _data():
    from test_signals import _data as signals_data_fixture

    return signals_data_fixture()


def _scored():
    rows = []
    for week in (1, 2, 3):
        for i, (pos, sal, proj, actual) in enumerate(
            [
                ("WR", 5000, 11.0, 13.0),
                ("WR", 3500, 5.0, 7.0),
                ("TE", 4000, 7.0, 6.0),
                ("RB", 6000, 14.0, 12.0),
            ]
        ):
            rows.append(
                {
                    "season": 2026,
                    "week": week,
                    "Id": 100 * week + i,
                    "Position": pos,
                    "Salary": sal,
                    "ProjPts": proj,
                    "SleeperPts": np.nan,
                    "FantasyProsPts": np.nan,
                    "UmPts": np.nan,
                    "AggPts": proj,
                    "DkActual": actual,
                    "RosterablePool": True,
                    "Status": "scored",
                }
            )
    return pd.DataFrame(rows)


@pytest.fixture(autouse=True)
def _offline(monkeypatch):
    monkeypatch.setattr(edge_finder, "_try_current", lambda name: None)
    monkeypatch.setattr(edge_finder, "_r6_features", lambda ctx, notes: None)  # no network in tests
    monkeypatch.setattr("dfs.results_signals.week_snapshots", lambda *a, **k: [])
    monkeypatch.setattr(
        probabilities, "um_projections", lambda frame, **kw: pd.Series(np.nan, index=frame.index)
    )


def test_attach_fills_by_id_and_leaves_unknown_players_blank_never_zero():
    frame = _edge_frame()
    extras = pd.DataFrame(
        {"CalPts": [10.0, 12.0], "Edge": ["INJ+", "FADE↓"], "Hit3x%": [30.0, 40.0]}, index=[2, 4]
    )
    out = derived.attach_edge_finder_columns(frame, extras)
    assert out.set_index("Id").loc[2, "CalPts"] == 10.0 and out.set_index("Id").loc[4, "Edge"] == "FADE↓"
    assert np.isnan(out.set_index("Id").loc[1, "CalPts"])  # no row in extras: blank
    assert out.set_index("Id").loc[1, "Edge"] == ""  # a text column's blank is "", not NaN
    assert list(out.columns) == derived.EDGE_COLUMNS
    assert out["CalPts%ile"].notna().any()  # the within-position helper is recomputed


def test_the_edge_finder_columns_are_appended_after_namekey_so_nothing_earlier_moves():
    cols = derived.EDGE_COLUMNS
    assert cols.index("NameKey") < cols.index("CalPts")
    assert cols[cols.index("NameKey") + 1 :] == derived.EDGE_FINDER_COLUMNS
    assert cols.index("AggPts") == 6 and cols.index("Id") + 1 == cols.index("Flag")  # untouched


def test_enrich_fills_calpts_probabilities_and_tokens_and_keeps_the_column_order():
    out = edge_finder.enrich(
        _edge_frame(),
        SyncContext(week=4, season=2026),
        now=datetime(2026, 10, 5, 12, tzinfo=UTC),
        scored=_scored(),
        data=_data(),
        write=False,
    )
    assert list(out.columns) == derived.EDGE_COLUMNS
    by = out.set_index("Name")
    assert by["CalPts"].notna().all()  # CalPts exists for everyone with a projection
    assert by.loc["Wr Two", "Hit3x%"] > 0 and by.loc["Wr Two", "Floor"] < by.loc["Wr Two", "CeilM"]
    # Star (a WR) is out per DraftKings, but target absences move no points: no INJ+ for the next man up
    assert by.loc["Wr Two", "Edge"] == ""
    assert by.loc["Wr Two", "xFP/G"] == pytest.approx(6.0)


def test_any_failure_returns_the_frame_unchanged(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("no model cache")

    monkeypatch.setattr(signals, "build_signals", boom)
    frame = _edge_frame()
    out = edge_finder.enrich(
        frame, SyncContext(week=4, season=2026), scored=_scored(), data=_data(), write=False
    )
    pd.testing.assert_frame_equal(out, frame)


def test_the_edge_finder_no_longer_infers_um_so_neither_sync_runs_the_model_package(monkeypatch):
    def forbidden(frame, **kw):
        raise AssertionError("UM inference must not run in the Edge Finder: UM is not in CalPts")

    monkeypatch.setattr(probabilities, "um_projections", forbidden)
    for live in (True, False):
        out = edge_finder.enrich(
            _edge_frame(),
            SyncContext(week=4, season=2026, live=live),
            scored=_scored(),
            data=_data(),
            write=False,
        )
        assert out["CalPts"].notna().all()


def test_depth_cutoff_never_reads_a_chart_published_after_the_first_kickoff():
    frame = pd.DataFrame({"GameStart": ["2026-10-11T13:00:00Z"]})  # 1 pm ET = 17:00 UTC
    before = edge_finder.depth_cutoff(frame, datetime(2026, 10, 10, 12, tzinfo=UTC))
    after = edge_finder.depth_cutoff(frame, datetime(2026, 10, 11, 22, tzinfo=UTC))
    assert before == "2026-10-10T12:00:00Z"
    assert after == "2026-10-11T17:00:00Z"  # capped at kickoff, not "now"


def test_the_saved_players_table_carries_each_sources_own_projection_for_the_tab(monkeypatch, tmp_path):
    """The tab splits CalPts - ProjPts by source, so players.csv must hold Sleeper's and FantasyPros' own
    projections (they are not signals columns)."""
    monkeypatch.setattr(edge_finder, "OUTPUT_DIR", tmp_path / "ef")
    monkeypatch.setattr("dfs.signals_data.SIGNALS_DIR", tmp_path / "signals")
    monkeypatch.setattr(
        edge_finder.results_loop,
        "source_projection_columns",
        lambda df, inputs: pd.DataFrame({"Id": df["Id"], "SleeperPts": 9.5, "FantasyProsPts": 8.5}),
    )
    edge_finder.enrich(
        _edge_frame(),
        SyncContext(week=4, season=2026),
        now=datetime(2026, 10, 5, 12, tzinfo=UTC),
        scored=_scored(),
        data=_data(),
        write=True,
    )
    saved = pd.read_csv(tmp_path / "ef" / edge_finder.PLAYERS_FILE)
    assert {"SleeperPts", "FantasyProsPts"} <= set(saved.columns)
    assert saved["SleeperPts"].eq(9.5).all() and saved["FantasyProsPts"].eq(8.5).all()
    assert saved["Id"].is_unique  # one row per player, however the merge ran
