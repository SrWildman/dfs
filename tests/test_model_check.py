"""Model Check verdict lines and plain labels (usability round, slice 6)."""

import pandas as pd

# ---- verdict lines and plain labels (usability round, slice 6) ------------------------------------------


def _race(rows):
    return pd.DataFrame(rows, columns=["Source", "Position", "n", "Rho", "MAE", "Bias"])


def test_the_race_verdict_names_the_best_projection_and_says_clear_only_with_a_gap_and_enough_players():
    from dfs.sheet_model_check import race_verdict

    race = _race(
        [
            ("TFFB", "RB", 182, 0.64, 5.23, -2.3),
            ("CalPts", "RB", 182, 0.64, 4.70, -1.0),
            ("AggPts", "RB", 182, 0.63, 5.00, -1.5),
            ("TFFB", "QB", 78, 0.33, 5.74, -1.0),
            ("CalPts", "QB", 78, 0.31, 5.84, -1.0),  # the best is 0.10 ahead: too close
            ("TFFB", "WR", 40, 0.6, 6.0, 0.0),
            ("CalPts", "WR", 40, 0.6, 5.0, 0.0),  # a clear gap but only 40 players
        ]
    )
    text = race_verdict(race)
    assert "RB: CalPts clearly best (MAE 4.70 vs AggPts 5.00)." in text
    assert "QB: too close to call." in text and "WR: too close to call." in text


def test_the_reliability_verdict_says_which_way_each_measure_runs_and_when_nothing_is_adjusted():
    from dfs.sheet_model_check import reliability_verdict

    table = pd.DataFrame(
        {
            "Measure": ["Hit3x%"] * 2 + ["Boom%"] * 2,
            "Decile": [1, 2, 1, 2],
            "n": [100, 100, 100, 100],
            "Gap": [-4.0, -2.0, 0.1, -0.2],
        }
    )
    text = reliability_verdict(table)
    assert text.startswith("Hit3x% runs ~3 points high; Boom% is about right")
    assert text.endswith("no adjustment until ~Week 8.")


def test_the_signals_verdict_compares_each_big_enough_signal_to_its_positions_baseline():
    from dfs.sheet_model_check import signals_verdict

    table = pd.DataFrame(
        {
            "Signal": ["FADE↓", "USAGE↑", "Unflagged TE", "Unflagged RB"],
            "n": [41, 12, 60, 80],
            "VsProj": [-2.0, 0.0, 0.5, 0.0],
            "VsCal": [-2.2, 0.1, 0.3, -0.5],
        }
    )
    text = signals_verdict(table)
    assert (
        "FADE↓ (n=41): −2.2" not in text
        and "FADE↓ (n=41): -2.2 vs CalPts, 2.5 below the unflagged TE" in text
    )
    assert "USAGE↑" not in text  # n=12: not enough players to word it
    empty = signals_verdict(table.assign(n=[5, 5, 5, 5]))
    assert "No signal has 30 players yet" in empty


def test_the_plain_labels_replace_tau_and_implied_quantile():
    from dfs import sheet_model_check as smc
    from dfs.sheet_column_notes import MODEL_CHECK_NOTES

    assert (
        smc.SKILL_80 == "Ceiling skill (80th pct)"
        and smc.CEILING_REAL == "Ceiling is really the Nth percentile"
    )
    for label in (smc.SKILL_80, smc.SKILL_85, smc.SKILL_90, smc.CEILING_REAL):
        assert label in MODEL_CHECK_NOTES  # the note moved with the label
    assert "Skill τ=0.80" not in MODEL_CHECK_NOTES and "Implied quantile" not in MODEL_CHECK_NOTES
