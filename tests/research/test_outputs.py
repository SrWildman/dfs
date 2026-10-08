"""Output schemas: every JSON and CSV the studies write carries its metadata and the keys the local round
reads. The same checkers run on the files shipped under models/research/ and on files freshly written from
synthetic results, so a stale shipped file and a drifted writer both fail."""

import json
import math

import pandas as pd
import pytest

from dfs.research import r1_redistribution as r1
from dfs.research import r2_matchup as r2
from dfs.research.common import (
    FIT_SEASONS,
    OUTPUT_DIR,
    TEST_SEASONS,
    meta_sidecar,
    metadata,
    write_csv,
    write_json,
)

from .helpers import make_games, make_injuries, make_matchup_tables, make_stats

META_KEYS = {"study", "seasons", "fit_seasons", "test_seasons", "n", "generated", "code_version"}
JSON_FILES = {
    "matchup_weights.json": "r2",
    "signal_thresholds.json": "r3",
    "weather.json": "r4",
    "quick_checks.json": "r5",
    "redistribution_constants.json": "r1",
}
REDISTRIBUTION_COLUMNS = [
    "channel",
    "absent",
    "absent_kind",
    "item",
    "item_kind",
    "seasons",
    "n",
    "mean_frac",
    "median_frac",
    "pooled_frac",
    "ci90_lo",
    "ci90_hi",
    "net_mean_frac",
    "net_median_frac",
    "net_pooled_frac",
    "net_ci90_lo",
    "net_ci90_hi",
    "spread90_lo",
    "spread90_hi",
]


def check_metadata(meta: dict, study: str):
    assert META_KEYS <= set(meta)
    assert meta["study"] == study
    assert meta["seasons"] == [2014, 2025]
    assert meta["fit_seasons"] == [FIT_SEASONS[0], FIT_SEASONS[-1]]
    assert meta["test_seasons"] == [TEST_SEASONS[0], TEST_SEASONS[-1]]
    assert isinstance(meta["n"], int) and meta["n"] > 0
    pd.Timestamp(meta["generated"])  # an ISO date
    assert isinstance(meta["code_version"], str) and meta["code_version"]


def check_matchup_weights(payload: dict):
    assert set(payload["positions"]) == {"QB", "RB", "WR", "TE", "DST"}
    for position, targets in payload["positions"].items():
        assert set(targets) == {"vs_um", "vs_trailing8"}
        for res in targets.values():
            assert {
                "n_fit",
                "n_test",
                "chosen",
                "holdout",
                "context_not_edge",
                "variants",
                "schedule_adjustment",
            } <= set(res)
            chosen = res["chosen"]
            assert {
                "lookback",
                "schedule_adjusted",
                "alpha",
                "components",
                "weights_per_sd",
                "means",
                "sds",
                "intercept",
            } <= set(chosen)
            assert (
                set(chosen["weights_per_sd"]) == set(chosen["components"]) == set(r2.components_for(position))
            )
            assert all(isinstance(w, float) for w in chosen["weights_per_sd"].values())
            assert {
                "mae_baseline",
                "mae_model",
                "mae_gain_bias_corrected",
                "mae_gain_ci90",
                "r2_gain",
            } <= set(res["holdout"])
            assert len(res["holdout"]["mae_gain_ci90"]) == 2
            assert isinstance(res["context_not_edge"], bool)


def check_redistribution_constants(payload: dict):
    assert {"definitions", "counts", "recommended", "prediction_mae", "control_game_mean_gain"} <= set(
        payload
    )
    assert {"period_stability", "by_reason", "refit_rule_constants_fit_seasons"} <= set(payload)
    assert payload["definitions"]["hand_set_rule"] == pytest.approx(
        {"next_up_share": 0.6, "rest_of_position_share": 0.3, "other_group_share": 0.1, "unassigned": 0.0}
    )
    assert payload["counts"]["clean_events"] > 0
    for row in payload["prediction_mae"]:
        assert {"channel", "seasons", "subset", "method", "n", "mae", "gain_vs_a", "gain_vs_b"} <= set(row)
    assert {r["method"] for r in payload["prediction_mae"]} == set(r1.METHODS)


def check_redistribution_csv(frame: pd.DataFrame):
    assert frame.columns.tolist() == REDISTRIBUTION_COLUMNS
    assert "all" in set(frame["seasons"]) <= {"fit", "test", "all"}
    assert set(frame["channel"]) <= {"targets", "carries"}
    assert (frame["n"] >= 1).all()


@pytest.mark.parametrize("name,study", JSON_FILES.items())
def test_shipped_json_has_metadata_first(name, study):
    path = OUTPUT_DIR / name
    assert path.exists(), f"{name} is missing -- run `dfs research run --study all`"
    raw = json.loads(path.read_text())
    assert next(iter(raw)) == "metadata"
    check_metadata(raw["metadata"], study)


def test_shipped_matchup_weights():
    check_matchup_weights(json.loads((OUTPUT_DIR / "matchup_weights.json").read_text()))


def test_shipped_redistribution():
    frame = pd.read_csv(OUTPUT_DIR / "redistribution.csv")
    check_redistribution_csv(frame)
    assert set(frame["seasons"]) == {"fit", "test", "all"}  # the real history has both periods
    meta = json.loads(meta_sidecar(OUTPUT_DIR / "redistribution.csv").read_text())
    check_metadata(meta, "r1")
    check_redistribution_constants(json.loads((OUTPUT_DIR / "redistribution_constants.json").read_text()))


def test_shipped_signal_thresholds():
    signals = json.loads((OUTPUT_DIR / "signal_thresholds.json").read_text())["signals"]
    assert {"BUY_up", "FADE_down", "FADE_down_gap_only", "USAGE_up", "USAGE_down", "salary_lag"} <= set(
        signals
    )
    assert signals["salary_lag"]["tested"] is False
    for name in ("BUY_up", "FADE_down", "FADE_down_gap_only"):
        sig = signals[name]
        assert sig["recommendation"] in {"keep", "borderline", "drop"}
        assert {"chosen", "fit", "test", "n_per_season", "by_position", "sweep"} <= set(sig)
        assert {"n_flagged", "diff", "lo90", "hi90", "hit_rate_flagged", "hit_rate_unflagged"} <= set(
            sig["fit"]
        )
    for direction in ("USAGE_up", "USAGE_down"):
        assert set(signals[direction]["per_metric"]) == {"target_share", "carry_share", "rz_share"}
        assert signals[direction]["combined"]["recommendation"] in {"keep", "borderline", "drop"}


def test_shipped_weather():
    w = json.loads((OUTPUT_DIR / "weather.json").read_text())
    assert {
        "wind_bins",
        "temp_bins",
        "precipitation",
        "total_points_by_wind",
        "pricing_check_per_5mph",
    } <= set(w)
    assert {"threshold_sweep", "joint_model", "recommendation", "data_quality"} <= set(w)
    assert {r["bin"] for r in w["wind_bins"]} == {"0-9", "10-14", "15-19", "20-24", "25+"}
    assert {r["position"] for r in w["wind_bins"]} == {"QB", "WR", "TE"}
    rec = w["recommendation"]
    assert {"recommended_mph", "qualifying_thresholds", "first_bin_meeting_rule_all"} <= set(rec)
    assert {t["threshold_mph"] for t in w["threshold_sweep"]["positions"]["QB"]} == {
        10,
        12,
        15,
        17,
        20,
        22,
        25,
    }


def test_shipped_quick_checks():
    q = json.loads((OUTPUT_DIR / "quick_checks.json").read_text())
    assert set(q["checks"]) == {"short_week", "divisional", "home", "back_to_back_road"}
    assert q["skipped"] == []
    for check in q["checks"].values():
        assert check["recommendation"] in {"keep", "borderline", "drop"}
        assert {"pooled", "by_position", "verdict_by_position", "definition", "expected_direction"} <= set(
            check
        )


def test_writers_roundtrip_metadata_and_nan(tmp_path):
    meta = metadata("r2", list(range(2014, 2026)), 12)
    check_metadata(meta, "r2")
    path = write_json("x.json", {"a": float("nan"), "b": [1.5, float("inf")], "c": {"d": 2}}, meta, tmp_path)
    raw = json.loads(path.read_text())
    assert next(iter(raw)) == "metadata"
    assert raw["a"] is None and raw["b"] == [1.5, None]  # JSON has no NaN: it is written as null
    csv = write_csv("y.csv", pd.DataFrame({"p": [1.0, 2.0]}), meta, tmp_path)
    assert pd.read_csv(csv)["p"].tolist() == [1.0, 2.0]  # a plain CSV any tool reads
    assert json.loads(meta_sidecar(csv).read_text())["study"] == "r2"


def test_r2_writer_matches_the_schema(tmp_path):
    result = r2.run_study(r2.matchup_features(*make_matchup_tables()))
    r2.write_outputs(result, 123, tmp_path)
    raw = json.loads((tmp_path / "matchup_weights.json").read_text())
    check_matchup_weights(raw)
    assert not any(
        isinstance(v, float) and math.isnan(v) for v in raw["metadata"].values() if isinstance(v, float)
    )


def test_r1_writer_matches_the_schema(tmp_path):
    games = make_games(bye_week=5)
    sp = make_stats(games, missing={("AAA", "WR1", 7), ("AAA", "RB1", 8), ("CCC", "WR2", 9)})
    # one newcomer, so the outsiders item exists
    result = r1.run_study(sp, games, make_injuries())
    r1.write_outputs(result, tmp_path)
    frame = pd.read_csv(tmp_path / "redistribution.csv")
    check_redistribution_csv(frame)
    check_metadata(json.loads(meta_sidecar(tmp_path / "redistribution.csv").read_text()), "r1")
    payload = json.loads((tmp_path / "redistribution_constants.json").read_text())
    check_metadata(payload["metadata"], "r1")
    check_redistribution_constants(payload)
