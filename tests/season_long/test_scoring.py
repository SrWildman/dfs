import json
from pathlib import Path

import pandas as pd
import pytest

from dfs.season_long.scoring import score_stats, stats_frame

FIXTURE = json.loads((Path(__file__).parent / "fixtures" / "sleeper_week7_lines.json").read_text())


def _lines() -> pd.DataFrame:
    return stats_frame([{"player_id": r["player_id"], "stats": r["stats"]} for r in FIXTURE["lines"]])


@pytest.mark.parametrize(("rec", "column"), [(1.0, "pts_ppr"), (0.5, "pts_half_ppr"), (0.0, "pts_std")])
def test_rescoring_reproduces_sleepers_own_totals(rec, column):
    """Real Week 7 lines (every position incl. K and DEF) in PPR / half / standard scoring."""
    scoring = {**FIXTURE["scoring_ppr"], "rec": rec}
    frame = _lines()
    assert score_stats(frame, scoring).tolist() == pytest.approx(frame[column].fillna(0.0).tolist(), abs=0.01)


def test_league_specific_settings_change_the_points():
    frame = pd.DataFrame({"pass_td": [2.0], "rec": [5.0], "bonus_rec_te": [5.0], "pass_yd": [250.0]})
    six_pt = {"pass_td": 6.0, "pass_yd": 0.04, "rec": 1.0, "bonus_rec_te": 0.5}
    assert score_stats(frame, six_pt).iloc[0] == pytest.approx(12 + 10 + 5 + 2.5)


def test_kicker_fallbacks_for_projection_keys():
    """A projection gives fgm_50p and fgmiss_<range>; the league scores fgm_50_59 / fgm_60p / fgmiss."""
    frame = pd.DataFrame({"fgm_50p": [0.4], "fgmiss_40_49": [0.1], "fgmiss_50p": [0.2]})
    scoring = {"fgm_50_59": 5.0, "fgm_60p": 6.0, "fgmiss": -1.0}
    assert score_stats(frame, scoring).iloc[0] == pytest.approx(0.4 * 5 - 0.3)


def test_actual_60_yard_field_goal_is_not_counted_twice():
    frame = pd.DataFrame({"fgm_50p": [1.0], "fgm_60p": [1.0]})
    assert score_stats(frame, {"fgm_50_59": 5.0, "fgm_60p": 6.0}).iloc[0] == pytest.approx(6.0)


def test_missing_stats_are_zero():
    assert score_stats(pd.DataFrame({"rec": [3.0]}), {"rush_td": 6.0, "rec": 0.5}).iloc[0] == 1.5
