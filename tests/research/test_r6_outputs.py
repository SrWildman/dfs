"""R6 output schemas: `usage_signals.json` and `trend_bands.json` carry their metadata and the keys the
local round reads, and the verdicts agree with the numbers beside them. The same checkers run on the files
shipped under models/research/ and on files freshly written from synthetic results, so a stale shipped file
and a drifted writer both fail."""

import json

import numpy as np
import pandas as pd
import pytest

from dfs.research import r6_trend as T
from dfs.research import r6_usage as R
from dfs.research.common import FIT_SEASONS, OUTPUT_DIR, TEST_SEASONS
from dfs.research.r6_features import GAME_KEY

from .test_r6_usage import make_frame

META_KEYS = {"study", "seasons", "fit_seasons", "test_seasons", "n", "generated", "code_version"}
SIGNAL_KEYS = {
    "id",
    "candidate",
    "label",
    "position",
    "shape",
    "source",
    "own_candidate",
    "features",
    "n_rows",
    "n_thresholds_swept",
    "sweep",
    "null_keep_rate",
    "null_keep_rate_um_matched",
    "chosen",
    "verdict",
    "reasons",
    "passes_keep_rule",
    "survives_um_matching",
}
EFFECT_KEYS = {
    "n_flagged",
    "n_other",
    "mean_resid_flagged",
    "mean_resid_unflagged",
    "diff",
    "lo90",
    "hi90",
    "t",
    "hit_rate_flagged",
    "hit_rate_unflagged",
}
SHORT_KEYS = {"n_flagged", "diff", "lo90", "hi90", "t"}
BAND_KEYS = {"n", "sd", "abs_p80", "abs_p90", "arrow_threshold", "flag_rate"}
EXPECTED_SPECS = {f"{c.key}|{pos}|{shape}" for c in R.CANDIDATES for pos in c.positions for shape in c.shapes}


def check_metadata(meta: dict, study: str = "r6", seasons=(2014, 2025)):
    assert META_KEYS <= set(meta)
    assert meta["study"] == study
    assert meta["seasons"] == list(seasons)
    assert meta["fit_seasons"] == [FIT_SEASONS[0], FIT_SEASONS[-1]]
    assert meta["test_seasons"] == [TEST_SEASONS[0], TEST_SEASONS[-1]]
    assert isinstance(meta["n"], int) and meta["n"] > 0
    pd.Timestamp(meta["generated"])
    assert isinstance(meta["code_version"], str) and meta["code_version"]


def check_usage_signals(payload: dict, full: bool = True):
    meta = payload["metadata"]
    check_metadata(meta)
    assert meta["window"] == {
        "recent_games": 3,
        "prior_games": 6,
        "min_prior_games_level": 3,
        "min_prior_games_change": 9,
    }
    assert {"min_flagged_fit", "min_flagged_test", "min_effect_points", "keep_rule"} <= set(meta)
    assert {
        "signals",
        "keeps",
        "keeps_um_matched",
        "overlap_pairs",
        "shuffle",
        "routes",
        "candidates",
    } <= set(payload)
    signals = payload["signals"]
    ids = [s["id"] for s in signals]
    assert len(ids) == len(set(ids)) and set(ids) == EXPECTED_SPECS
    for s in signals:
        assert SIGNAL_KEYS <= set(s), s["id"]
        assert s["id"] == f"{s['candidate']}|{s['position']}|{s['shape']}"
        assert s["position"] in {"QB", "RB", "WR", "TE"} and s["shape"] in R.SHAPES
        assert s["verdict"] in {"keep", "context", "drop"}
        assert 0 <= s["null_keep_rate"] <= 1 and 0 <= s["null_keep_rate_um_matched"] <= 1
        for row in s["sweep"]:
            assert {"rule", "eligible", "fit", "test"} <= set(row)
            assert {"n_flag", "diff", "lo", "hi", "t"} <= set(row["fit"])
        if s["chosen"] is None:
            assert s["verdict"] == "drop"
            continue
        assert {"rule", "definition"} <= set(s["chosen"])
        for cond in s["chosen"]["rule"]:
            assert {"feature", "op", "value"} <= set(cond) and cond["op"] in {">=", "<="}
        for period in ("fit", "test"):
            assert EFFECT_KEYS <= set(s[period])
            assert SHORT_KEYS <= set(s["vs_l8"][period])
            assert SHORT_KEYS <= set(s["um_matched"][period])
            assert 0 <= s[period]["hit_rate_flagged"] <= 1 and 0 <= s[period]["hit_rate_unflagged"] <= 1
            assert s[period]["lo90"] <= s[period]["diff"] <= s[period]["hi90"]
        assert s["um_matched"]["verdict"] in {"keep", "context", "drop"}
        assert all(int(y) in range(2014, 2026) for y in s["n_per_season"])
        assert s["n_per_season"].keys() >= s["effect_by_season"].keys()
        assert sum(s["n_per_season"].values()) == s["fit"]["n_flagged"] + s["test"]["n_flagged"]
        # a keep is a keep by the numbers beside it
        if s["passes_keep_rule"]:
            fit, test = s["fit"]["diff"], s["test"]
            assert fit * test["diff"] > 0
            assert abs(test["diff"]) >= meta["min_effect_points"]
            assert test["lo90"] > 0 or test["hi90"] < 0
            assert test["n_flagged"] >= meta["min_flagged_test"]
        if s["verdict"] == "keep":
            assert s["passes_keep_rule"]
    kept = {s["id"] for s in signals if s["verdict"] == "keep"}
    assert set(payload["keeps"]) == kept
    by_id = {s["id"]: s for s in signals}
    for i in payload["keeps_um_matched"]:
        assert by_id[i]["survives_um_matching"] and by_id[i]["passes_keep_rule"]
        assert by_id[i]["um_matched"]["verdict"] == "keep"
    assert {i for i, s in by_id.items() if s["survives_um_matching"]} == set(payload["keeps_um_matched"])
    shuffle = payload["shuffle"]
    assert {"seed", "n_shuffles", "n_specs", "n_thresholds_swept", "brief_rule", "um_matched_rule"} <= set(
        shuffle
    )
    assert shuffle["n_specs"] == len(signals)
    if full:
        assert shuffle["n_shuffles"] >= 200
    for name in ("brief_rule", "um_matched_rule"):
        block = shuffle[name]
        assert {"expected_keeps_by_chance", "p05", "p95", "max", "histogram"} <= set(block)
        assert 0 <= block["expected_keeps_by_chance"] <= len(signals)
        assert sum(block["histogram"].values()) == shuffle["n_shuffles"]
    assert {c["key"] for c in payload["candidates"]} == {c.key for c in R.CANDIDATES}
    assert sum(c["own_candidate"] for c in payload["candidates"]) in (1, 2, 3)
    assert "per-player routes" in payload["routes"]["answer"]
    assert payload["routes"]["checked"]


def check_trend_bands(payload: dict):
    check_metadata(payload["metadata"], seasons=(2014, 2025))
    assert payload["metadata"]["trend_seasons"] == [2018, 2025]
    assert payload["window"] == {"recent_games": 3, "prior_games": 6, "min_prior_games": 9}
    assert payload["target_flag_rate"] == 0.15
    assert set(payload["metrics"]) == set(T.TREND_METRICS)
    assert isinstance(payload["n_player_games"], int) and payload["n_player_games"] > 0
    for key, m in payload["metrics"].items():
        label, feature, positions, unit = T.TREND_METRICS[key]
        assert m["label"] == label and m["feature"] == f"{feature}_chg" and m["unit"] == unit
        assert set(m["positions"]) == set(positions)
        for pos, block in m["positions"].items():
            for population in ("all", "pool"):
                assert BAND_KEYS <= set(block[population]), (key, pos)
        for population in ("all", "pool"):
            assert BAND_KEYS <= set(m["pooled"][population])
        rec = m["recommended"]
        assert set(rec["per_position"]) == set(positions) == set(rec["flag_rate_at_pooled"])
        for pos, v in rec["per_position"].items():
            assert v["threshold"] > 0 and 0 < v["flag_rate"] < 0.5
            b = m["positions"][pos]["pool"]
            assert b["abs_p80"] <= v["threshold"] * 1.25 and b["abs_p90"] >= v["threshold"] * 0.8
        assert rec["pooled"]["threshold"] > 0


# --------------------------------------------------------------------------------------------------------
# Shipped files
# --------------------------------------------------------------------------------------------------------


def test_shipped_usage_signals_match_the_schema_and_their_own_numbers():
    path = OUTPUT_DIR / "usage_signals.json"
    assert path.exists(), "run `dfs research run --study r6`"
    check_usage_signals(json.loads(path.read_text()))


def test_shipped_trend_bands_match_the_schema():
    path = OUTPUT_DIR / "trend_bands.json"
    assert path.exists(), "run `dfs research run --study r6`"
    check_trend_bands(json.loads(path.read_text()))


# --------------------------------------------------------------------------------------------------------
# Freshly written files
# --------------------------------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def synthetic_outputs(tmp_path_factory):
    frame = make_frame(seed=3, plant=("tgt_pg_chg", "TE", -2.0, 1.5))
    result = R.run_study(frame, n_shuffles=3, seed=4)
    rng = np.random.default_rng(0)
    rows = [
        (pid, season, week, pos)
        for pid, pos in (("w1", "WR"), ("r1", "RB"), ("t1", "TE"), ("q1", "QB"))
        for season in (2018, 2019, 2024)
        for week in range(1, 18)
    ]
    games = pd.DataFrame(rows, columns=[*GAME_KEY, "position"]).assign(team="AAA")
    feats = games[[*GAME_KEY, "team"]].copy()
    for _, feature, _, _ in T.TREND_METRICS.values():
        feats[f"{feature}_chg"] = rng.normal(0, 0.1, len(feats))
    bands = T.trend_bands(games, feats, games)
    out = tmp_path_factory.mktemp("r6")
    R.write_outputs(result, bands, len(frame), directory=out)
    return out, result


def test_freshly_written_usage_signals_match_the_schema(synthetic_outputs):
    out, _ = synthetic_outputs
    check_usage_signals(json.loads((out / "usage_signals.json").read_text()), full=False)


def test_freshly_written_trend_bands_match_the_schema(synthetic_outputs):
    out, _ = synthetic_outputs
    check_trend_bands(json.loads((out / "trend_bands.json").read_text()))


def test_the_json_is_strict_json_with_no_nan(synthetic_outputs):
    out, _ = synthetic_outputs
    bad = []
    for name in ("usage_signals.json", "trend_bands.json"):
        json.loads((out / name).read_text(), parse_constant=bad.append)
    assert bad == []  # NaN / Infinity would have been handed to parse_constant
