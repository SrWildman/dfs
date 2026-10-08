"""The measured constants are read from models/research/ at run time, never hard-coded."""

import json
import shutil

import pytest

from dfs import derived
from dfs import research_constants as rc
from dfs.sources import weather


def _copy(tmp_path):
    folder = tmp_path / "research"
    shutil.copytree(rc.RESEARCH_DIR, folder)
    return folder


def test_signal_thresholds_are_the_researchs_fade_te_only_and_usage_rb_carry_share():
    th = rc.signal_thresholds()
    assert th.fade_gap_points == 2.0 and th.fade_positions == ("TE",)
    assert th.usage_up == 0.10 and th.usage_down == 0.05 and th.usage_positions == ("RB",)
    assert (th.recent_games, th.earlier_games, th.prior_games_needed) == (2, 6, 8)


def test_a_changed_threshold_in_the_json_changes_the_signal_with_no_code_change(tmp_path):
    folder = _copy(tmp_path)
    data = json.loads((folder / rc.SIGNALS_FILE).read_text())
    data["signals"]["FADE_down_gap_only"]["chosen"]["x"] = 3.5
    (folder / rc.SIGNALS_FILE).write_text(json.dumps(data))
    assert rc.signal_thresholds(folder).fade_gap_points == 3.5


def test_a_missing_file_or_key_names_itself_instead_of_guessing(tmp_path):
    with pytest.raises(rc.ResearchConstantsError, match="signal_thresholds.json"):
        rc.signal_thresholds(tmp_path)
    folder = _copy(tmp_path)
    data = json.loads((folder / rc.SIGNALS_FILE).read_text())
    del data["signals"]["USAGE_up"]
    (folder / rc.SIGNALS_FILE).write_text(json.dumps(data))
    with pytest.raises(rc.ResearchConstantsError, match="USAGE_up"):
        rc.signal_thresholds(folder)


def test_a_regular_is_the_researchs_target_or_carry_share():
    reg = rc.regular_thresholds()
    assert (reg.target_share, reg.carry_share) == (0.15, 0.30)


def test_the_target_context_quotes_the_json_and_has_nothing_for_a_thin_or_negative_cell():
    ctx = rc.target_context("WR1")
    assert ctx.next_up_label == "WR2" and 0.10 < ctx.next_up < 0.16 and ctx.unassigned > 0.2
    line = rc.target_context_line("WR", ctx)
    assert line.startswith("historically, no single teammate gains much: WR2 +13% of the vacated targets")
    assert rc.target_context("RB1") is None  # not a pass-catcher cell
    assert "too few past absences" in rc.target_context_line("WR", None)


def test_the_wind_flag_fires_at_fifteen_mph_in_both_places():
    assert derived.WIND_FLAG_THRESHOLD_MPH == weather.WIND_FLAG_THRESHOLD_MPH == 15.0
