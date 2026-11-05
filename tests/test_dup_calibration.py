"""The duplicate estimate calibrated on fixture standings (Plan K item 3)."""

from __future__ import annotations

import pandas as pd
import pytest

from dfs.sim import dup_calibration as dc
from dfs.sim import dups


def fixture_contest(entries: int = 400) -> tuple[list[tuple[str, ...]], dict[str, float]]:
    """A standings file's lineups: the chalk lineup 60 times, a near-chalk one 8 times, the rest unique."""
    own = {f"c{i}": 0.6 for i in range(9)} | {f"m{i}": 0.3 for i in range(9)}
    own |= {f"u{i}": 0.02 for i in range(400)}
    chalk = tuple(f"c{i}" for i in range(9))
    mid = tuple(f"m{i}" for i in range(9))
    unique = [tuple(f"u{(j * 9 + i) % 400}" for i in range(9)) for j in range(entries - 68)]
    return [chalk] * 60 + [mid] * 8 + unique, own


def test_entry_rows_count_the_other_entries_with_the_same_nine():
    keys, own = fixture_contest()
    rows = dc.entry_rows(keys, own, len(keys))
    assert rows["copies"].iloc[0] == 59
    assert rows["copies"].iloc[60] == 7
    assert rows["copies"].iloc[-1] == 0
    assert rows["product"].iloc[0] == pytest.approx(0.6**9)


def test_the_fit_on_fixture_standings_ranks_chalk_above_unique_and_beats_a_flat_guess():
    keys, own = fixture_contest()
    rows = pd.concat([dc.entry_rows(keys, own, len(keys)), dc.entry_rows(keys, own, len(keys))])
    model = dc.fit(rows)
    predicted = dc.predict(model, rows)
    assert predicted[0] > predicted[60] > predicted[-1]
    flat = [rows["copies"].mean()] * len(rows)
    assert dc.log_rmse(rows["copies"], predicted) < dc.log_rmse(rows["copies"], flat)


def test_summarise_weights_the_error_by_entries_and_adds_up_tops_and_mine():
    held = [
        {"fmt": "gpp", "week": 4, "n": 100, "log_rmse": 0.5, "top": [{"observed": 10, "predicted": 4.0}],
         "mine": [{"observed": 0, "predicted": 0.5}]},
        {"fmt": "gpp", "week": 5, "n": 300, "log_rmse": 1.0, "top": [{"observed": 2, "predicted": 6.0}],
         "mine": []},
    ]  # fmt: skip
    s = dc.summarise(held)["gpp"]
    assert s["weeks"] == [4, 5] and s["entries"] == 400
    assert s["log_rmse"] == pytest.approx(((0.25 * 100 + 1.0 * 300) / 400) ** 0.5)
    assert (s["top_observed"], s["top_predicted"]) == (12, 10.0)
    assert (s["mine_observed"], s["mine_n"]) == (0, 1)
    assert "cash" not in dc.summarise(held)


def test_the_saved_fit_round_trips(tmp_path):
    result = {"season": 2026, "weeks": [4, 5], "fit": {"cash": {"scale": 6.4, "exponent": 0.5}, "gpp": {}}}
    dc.save(result, tmp_path)
    loaded = dc.load(tmp_path)
    assert dc.model_from(loaded, "cash") == dups.DupModel(scale=6.4, exponent=0.5)
    assert dc.model_from(loaded, "gpp") is None
    assert dc.model_from(None, "cash") is None
    assert dc.load(tmp_path / "nowhere") is None


def test_no_standings_is_a_clear_error(monkeypatch):
    from dfs.sim import contest_retro

    monkeypatch.setattr(contest_retro, "load_real_contests", lambda weeks=None: [])
    with pytest.raises(ValueError, match="no saved standings"):
        dc.run([], 2026)
