"""R6: which usage signals, as LEVELS and as CHANGES, carry information beyond the projection?

R3 tested a few shapes (a target-share jump, a red-zone-share jump, carry-share jumps). This round tests a
wider, DraftKings-specific set -- see `CANDIDATES` -- as

  level_hi      the last-3-games level (L3) is at or above a threshold
  change_up     L3 minus the 6 games before those is at or above +threshold
  change_down   the same change is at or below -threshold
  deep_volume   (aDOT only) a deep L3 aDOT AND a high L3 target share

all computed from games BEFORE the one being predicted (`dfs.research.r6_features`). For each candidate x
position x shape (a "spec"):

  1. thresholds come from the FIT seasons' quantiles of the feature among that position's UM-population
  rows; 2. the threshold with the largest |t| of (flagged - unflagged) mean residual `actual DK - UM` in
  2014-2021 is chosen (needs `MIN_FLAGGED` flagged fit rows; the sign of the effect is whatever the fit
  seasons show); 3. that one threshold is reported untouched on 2022-2025.

Verdicts (`classify`):

  keep      same sign in fit and test, |test effect| >= 0.5 DK points, the test 90% interval excludes 0, and
  at least 150 flagged test rows -- then, among kept signals, the stronger of any pair that flags mostly the
  same player-games (Jaccard > 0.5) survives and the other is dropped as redundant; context   same sign in
  fit and test, and exactly ONE of those three further conditions missed: a near-miss, shown but not worth a
  chip; drop      anything else.

Because dozens of specs x several thresholds are swept, some will pass by luck. `shuffle_baseline` re-runs the
WHOLE procedure (threshold choice on the fit seasons, then the keep rule on the test seasons) on residuals
permuted within position-week, which keeps every flag exactly as it is (its persistence, its overlap with the
other flags) and severs only its link to the outcome. The mean number of keeps that produces is what chance
alone delivers from this search. Intervals are clustered by player, as in R3.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from dfs.research import data
from dfs.research.baseline import load_baseline
from dfs.research.common import FIT_SEASONS, OFFENSE_POSITIONS, SEASONS, metadata, write_json
from dfs.research.flags import MIN_EFFECT, add_split_and_demeaned
from dfs.research.r6_features import GAME_KEY, player_game_frame, team_proe, usage_features
from dfs.research.r6_trend import trend_bands
from dfs.research.stats import Z90, _se_from_scores, cluster_diff

MIN_FLAGGED = 150  # flagged FIT rows before a threshold may be chosen (as R3)
MIN_TEST_N = 150  # flagged TEST rows before a signal may be kept
UM_BINS = 10  # the UM-matched control compares players within the same decile of UM
JACCARD_LIMIT = 0.5  # kept signals flagging more than this share of the same player-games are redundant
MAX_FLAG_SHARE = 0.5  # a rule that flags more than half its position is not a signal
N_SHUFFLES = 500
SEED = 20260607

LEVEL_QUANTILES = (0.70, 0.80, 0.90, 0.95)  # of the fit-season L3 level: top 30% ... top 5%
UP_QUANTILES = (0.75, 0.85, 0.90, 0.95)  # of the fit-season L3 - prior change
DOWN_QUANTILES = (0.25, 0.15, 0.10, 0.05)
DEEP_QUANTILES = (0.60, 0.80)  # deep_volume: aDOT quantile x target-share quantile
VOLUME_QUANTILES = (0.60, 0.80)

SHAPES = ("level_hi", "change_up", "change_down", "deep_volume")
OPS = {"level_hi": ">=", "change_up": ">=", "change_down": "<=", "deep_volume": ">="}


@dataclass(frozen=True)
class Candidate:
    key: str
    label: str
    metric: str  # prefix of the `<metric>_l3 / _prior / _chg` feature columns
    positions: tuple[str, ...]
    shapes: tuple[str, ...]
    source: str
    unit: str
    definition: str
    own: bool = False  # a candidate added by this round, not in the brief
    why: str = ""


CANDIDATES: tuple[Candidate, ...] = (
    Candidate(
        "rb_tgt_share",
        "RB target share",
        "tgt_share",
        ("RB",),
        ("level_hi", "change_up", "change_down"),
        "stats_player",
        "share",
        "mean over the window of (RB targets / team targets) per game",
    ),
    Candidate(
        "rb_rec_pg",
        "RB receptions per game",
        "rec_pg",
        ("RB",),
        ("level_hi", "change_up", "change_down"),
        "stats_player",
        "per game",
        "mean receptions per game over the window",
    ),
    Candidate(
        "ay_share",
        "Air-yards share",
        "ay_share",
        ("WR", "TE"),
        ("level_hi", "change_up", "change_down"),
        "stats_player",
        "share",
        "mean over the window of nflverse air_yards_share per game",
    ),
    Candidate(
        "adot",
        "aDOT (air yards per target)",
        "adot",
        ("WR", "TE"),
        ("level_hi", "deep_volume"),
        "stats_player",
        "yards",
        "sum of receiving air yards / sum of targets over the window; deep_volume also needs L3 target share",
    ),
    Candidate(
        "wopr",
        "WOPR change",
        "wopr",
        ("WR", "TE"),
        ("change_up", "change_down"),
        "stats_player",
        "index",
        "mean over the window of nflverse WOPR (1.5 x target share + 0.7 x air-yards share) per game",
    ),
    Candidate(
        "snap_pct",
        "Snap share change",
        "snap_pct",
        ("RB", "WR", "TE"),
        ("change_up", "change_down"),
        "snap counts",
        "share",
        "mean over the window of offensive snaps / team offensive snaps per game (PFR snap counts, "
        "joined by id)",
    ),
    Candidate(
        "ez_tgt",
        "End-zone targets per game",
        "ez_tgt_pg",
        ("WR", "TE", "RB"),
        ("level_hi", "change_up", "change_down"),
        "pbp",
        "per game",
        "mean per game of targets with air_yards >= yardline_100",
    ),
    Candidate(
        "gl_share",
        "Goal-line carry share",
        "gl_share",
        ("RB",),
        ("level_hi", "change_up", "change_down"),
        "pbp",
        "share",
        "sum of the RB's carries from inside the 5 / sum of team carries from inside the 5 over the window",
    ),
    Candidate(
        "qb_rush",
        "QB designed rushes per game",
        "qb_rush_pg",
        ("QB",),
        ("level_hi", "change_up", "change_down"),
        "pbp",
        "per game",
        "mean per game of QB carries that are not scrambles (qb_scramble == 0)",
    ),
    Candidate(
        "hvt",
        "High-value touches per game",
        "hvt_pg",
        ("RB",),
        ("level_hi", "change_up", "change_down"),
        "pbp",
        "per game",
        "mean per game of targets plus carries from inside the 10",
    ),
    Candidate(
        "proe",
        "Team pass rate over expected, change",
        "proe",
        ("WR", "TE"),
        ("change_up", "change_down"),
        "pbp",
        "points",
        "the player's TEAM mean pass_oe (percentage points) over the window, from the team's own games",
    ),
    Candidate(
        "tgt_pg",
        "Targets per game",
        "tgt_pg",
        ("WR", "TE", "RB"),
        ("level_hi", "change_up", "change_down"),
        "stats_player",
        "per game",
        "mean targets per game over the window",
        own=True,
        why="R3 and candidates 1-3 test target SHARE; raw target volume also moves with how much the team "
        "throws, which share hides (and candidate 11 tests the throwing rate itself).",
    ),
    Candidate(
        "deep_tgt",
        "Deep targets per game",
        "deep_tgt_pg",
        ("WR", "TE"),
        ("level_hi", "change_up", "change_down"),
        "pbp",
        "per game",
        "mean per game of targets with air_yards >= 20",
        own=True,
        why="aDOT (candidate 4) is a rate and says nothing about volume; the COUNT of deep looks is what "
        "turns into DK points on a big play, and it is the same pbp pass as end-zone targets.",
    ),
)
CANDIDATE_BY_KEY = {c.key: c for c in CANDIDATES}


# --------------------------------------------------------------------------------------------------------
# The signal frame
# --------------------------------------------------------------------------------------------------------


def signal_frame(base: pd.DataFrame, feats: pd.DataFrame) -> pd.DataFrame:
    """UM-population offense rows with the window features attached, `split` and the residuals demeaned within
    (position, season): `resid_c` (actual - UM) and `resid_l8_c` (actual - trailing-8 DK)."""
    b = base[base["position"].isin(OFFENSE_POSITIONS) & base["um"].notna()].copy()
    b = b.merge(feats.drop(columns=["team"]), on=GAME_KEY, how="left", validate="m:1")
    b = add_split_and_demeaned(b)
    b["resid_l8_c"] = b["resid_l8"] - b.groupby(["position", "season"])["resid_l8"].transform("mean")
    # The same residual compared only among players UM rates alike: demeaned within (position, season, UM
    # decile). UM's QB / RB / TE blend has no shrinkage, so it over-projects its own top decile; a usage flag
    # that merely picks out high-UM players would otherwise look like a signal.
    b["um_decile"] = b.groupby(["position", "season"])["um"].transform(
        lambda x: pd.qcut(x.rank(method="first"), UM_BINS, labels=False)
    )
    b["resid_m"] = b["resid"] - b.groupby(["position", "season", "um_decile"])["resid"].transform("mean")
    return b.sort_values(["position", "t", "gsis_id"]).reset_index(drop=True)


# --------------------------------------------------------------------------------------------------------
# Specs: one (candidate, position, shape), its thresholds and their flag vectors
# --------------------------------------------------------------------------------------------------------


def nice(x: float) -> float:
    """Two significant digits: a threshold somebody can type into a sheet formula."""
    return float(f"{x:.2g}")


def spec_features(c: Candidate, shape: str) -> list[str]:
    if shape == "deep_volume":
        return [f"{c.metric}_l3", "tgt_share_l3"]
    return [f"{c.metric}_{'l3' if shape == 'level_hi' else 'chg'}"]


def _grid(values: np.ndarray, quantiles: tuple[float, ...]) -> list[float]:
    """Distinct rounded quantiles of the fit-season values."""
    if len(values) == 0:
        return []
    return sorted({nice(q) for q in np.quantile(values, quantiles)})


def _passes(x: np.ndarray, op: str, value: float) -> np.ndarray:
    return (x >= value) if op == ">=" else (x <= value)


@dataclass
class Spec:
    id: str
    candidate: Candidate
    position: str
    shape: str
    features: list[str]
    idx: np.ndarray  # row positions in the frame this spec is evaluated on
    fit: np.ndarray  # bool per row of idx
    rules: list[list[dict]]  # per threshold, the conditions that are ANDed
    flags: np.ndarray  # (len(idx), n_thresholds) bool
    codes: dict[str, tuple[np.ndarray, int]]  # per period: player cluster codes, count


def build_spec(f: pd.DataFrame, c: Candidate, position: str, shape: str) -> Spec:
    feats = spec_features(c, shape)
    mask = np.asarray(f["position"] == position)
    for col in feats:
        mask = mask & f[col].notna().to_numpy()
    idx = np.flatnonzero(mask)
    fit = f["split"].to_numpy()[idx] == "fit"
    op = OPS[shape]
    if shape == "deep_volume":
        a_vals = f[feats[0]].to_numpy()[idx]
        s_vals = f[feats[1]].to_numpy()[idx]
        combos = [
            [{"feature": feats[0], "op": op, "value": a}, {"feature": feats[1], "op": op, "value": s}]
            for a in _grid(a_vals[fit], (DEEP_QUANTILES[0], DEEP_QUANTILES[1]))
            for s in _grid(s_vals[fit], (VOLUME_QUANTILES[0], VOLUME_QUANTILES[1]))
        ]
    else:
        quantiles = {"level_hi": LEVEL_QUANTILES, "change_up": UP_QUANTILES, "change_down": DOWN_QUANTILES}[
            shape
        ]
        x = f[feats[0]].to_numpy()[idx]
        combos = [[{"feature": feats[0], "op": op, "value": v}] for v in _grid(x[fit], quantiles)]
    rules, columns = [], []
    for conds in combos:
        flag = np.ones(len(idx), dtype=bool)
        for cond in conds:
            flag &= _passes(f[cond["feature"]].to_numpy()[idx], cond["op"], cond["value"])
        # A rule that flags (nearly) everyone or no one in the fit seasons is no signal; a duplicate flag set
        # from rounding is the same signal twice.
        share = flag[fit].mean() if fit.any() else 0.0
        if not (0 < share <= MAX_FLAG_SHARE) or any(np.array_equal(flag, other) for other in columns):
            continue
        rules.append(conds)
        columns.append(flag)
    flags = np.column_stack(columns) if columns else np.zeros((len(idx), 0), dtype=bool)
    players = f["gsis_id"].to_numpy()[idx]
    codes = {}
    for name, sel in (("fit", fit), ("test", ~fit)):
        c_codes, uniques = pd.factorize(players[sel])
        codes[name] = (c_codes, len(uniques))
    return Spec(f"{c.key}|{position}|{shape}", c, position, shape, feats, idx, fit, rules, flags, codes)


def build_specs(f: pd.DataFrame) -> list[Spec]:
    return [build_spec(f, c, pos, shape) for c in CANDIDATES for pos in c.positions for shape in c.shapes]


# --------------------------------------------------------------------------------------------------------
# Evaluation
# --------------------------------------------------------------------------------------------------------


def diff_stats(x: np.ndarray, flag: np.ndarray, codes: np.ndarray, n_clusters: int) -> dict:
    """`stats.cluster_diff` with the cluster codes precomputed (the shuffle calls this tens of thousands of
    times). Same numbers: pinned by a test."""
    n1 = int(flag.sum())
    n0 = len(flag) - n1
    nan = float("nan")
    if n1 == 0 or n0 == 0:
        return {"n_flag": n1, "n_other": n0, "diff": nan, "se": nan, "lo": nan, "hi": nan, "t": nan}
    m1, m0 = x[flag].mean(), x[~flag].mean()
    scores = np.where(flag, (x - m1) / n1, -(x - m0) / n0)
    se = _se_from_scores(scores, codes, n_clusters)
    diff = float(m1 - m0)
    return {
        "n_flag": n1,
        "n_other": n0,
        "diff": diff,
        "se": se,
        "lo": diff - Z90 * se,
        "hi": diff + Z90 * se,
        "t": diff / se if se > 0 else nan,
    }


def evaluate_spec(spec: Spec, resid_c: np.ndarray) -> dict:
    """Every threshold in the fit and test seasons, and the threshold chosen on the fit seasons alone (largest
    |t| among those with at least `MIN_FLAGGED` flagged and `MIN_FLAGGED` unflagged fit rows)."""
    x = resid_c[spec.idx]
    parts = {"fit": spec.fit, "test": ~spec.fit}
    per_threshold = []
    for k in range(spec.flags.shape[1]):
        row = {}
        for name, sel in parts.items():
            codes, g = spec.codes[name]
            row[name] = diff_stats(x[sel], spec.flags[sel, k], codes, g)
        per_threshold.append(row)
    best, best_t = None, -1.0
    for k, row in enumerate(per_threshold):
        fit = row["fit"]
        eligible = fit["n_flag"] >= MIN_FLAGGED and fit["n_other"] >= MIN_FLAGGED
        t = abs(fit["t"]) if fit["t"] == fit["t"] else -1.0
        row["eligible"] = bool(eligible)
        if eligible and t > best_t:
            best, best_t = k, t
    return {"thresholds": per_threshold, "chosen": best}


def classify(fit: dict, test: dict) -> dict:
    """keep / context / drop for one chosen threshold, from its fit and test `diff_stats`."""
    same_sign = bool(fit["diff"] * test["diff"] > 0) if test["diff"] == test["diff"] else False
    if not same_sign:
        return {"verdict": "drop", "reasons": ["fit and test effects do not share a sign"], "passes": False}
    misses = []
    if abs(test["diff"]) < MIN_EFFECT:
        misses.append(f"test effect {test['diff']:+.2f} pts is smaller than {MIN_EFFECT}")
    if not (test["lo"] > 0 or test["hi"] < 0):
        misses.append("test 90% interval includes 0")
    if test["n_flag"] < MIN_TEST_N:
        misses.append(f"only {test['n_flag']} flagged test rows (< {MIN_TEST_N})")
    if not misses:
        return {"verdict": "keep", "reasons": [], "passes": True}
    verdict = "context" if len(misses) == 1 else "drop"
    return {"verdict": verdict, "reasons": misses, "passes": False}


def passes_both(spec: Spec, resid_c: np.ndarray, resid_m: np.ndarray) -> tuple[bool, bool]:
    """(passes the keep rule on `resid_c`, passes it on `resid_m` too at the same threshold): the
    shuffle's one question, asked of both targets."""
    res = evaluate_spec(spec, resid_c)
    if res["chosen"] is None:
        return False, False
    row = res["thresholds"][res["chosen"]]
    if not classify(row["fit"], row["test"])["passes"]:
        return False, False
    matched = chosen_stats(spec, res["chosen"], resid_m)
    return True, classify(matched["fit"], matched["test"])["passes"]


def chosen_stats(spec: Spec, k: int, target: np.ndarray) -> dict:
    """`diff_stats` of one threshold's flag against `target`, in the fit and the test seasons."""
    x = target[spec.idx]
    out = {}
    for name, sel in (("fit", spec.fit), ("test", ~spec.fit)):
        codes, g = spec.codes[name]
        keep = ~np.isnan(x[sel])
        out[name] = diff_stats(x[sel][keep], spec.flags[sel, k][keep], codes[keep], g)
    return out


# --------------------------------------------------------------------------------------------------------
# The shuffle baseline
# --------------------------------------------------------------------------------------------------------


def permute_within(values: np.ndarray, groups: np.ndarray, rng: np.random.Generator) -> np.ndarray:
    """`values` with each group's entries randomly permuted among that group's rows."""
    order_by_group = np.argsort(groups, kind="stable")
    shuffled = np.lexsort((rng.random(len(values)), groups))
    out = np.empty_like(values)
    out[order_by_group] = values[shuffled]
    return out


def shuffle_baseline(
    f: pd.DataFrame, specs: list[Spec], n_shuffles: int = N_SHUFFLES, seed: int = SEED
) -> dict:
    """How many keeps does chance deliver from this search? Each shuffle permutes the residual among the
    players of the same position, season and week (every flag stays as it is; both residual targets get the
    SAME permutation), then runs the full choose-on-fit, judge-on-test procedure for every spec.
    Deterministic under `seed`. Reported for the brief's rule and for the stricter UM-matched one."""
    rng = np.random.default_rng(seed)
    groups = pd.factorize(f["position"] + "|" + f["season"].astype(str) + "|" + f["week"].astype(str))[0]
    resid_c, resid_m = f["resid_c"].to_numpy(), f["resid_m"].to_numpy()
    kept = np.zeros((n_shuffles, len(specs), 2), dtype=bool)
    for b in range(n_shuffles):
        # One permutation of the rows for both targets: permute the row numbers, then index both with it.
        order = permute_within(np.arange(len(f)), groups, rng)
        c, m = resid_c[order], resid_m[order]
        for j, spec in enumerate(specs):
            kept[b, j] = passes_both(spec, c, m)
    out = {
        "seed": seed,
        "n_shuffles": n_shuffles,
        "n_specs": len(specs),
        "n_thresholds_swept": int(sum(s.flags.shape[1] for s in specs)),
        "per_spec_keep_rate": {s.id: float(kept[:, j, 0].mean()) for j, s in enumerate(specs)},
        "per_spec_keep_rate_um_matched": {s.id: float(kept[:, j, 1].mean()) for j, s in enumerate(specs)},
    }
    for i, name in enumerate(("brief_rule", "um_matched_rule")):
        per_run = kept[:, :, i].sum(axis=1)
        out[name] = {
            "expected_keeps_by_chance": float(per_run.mean()),
            "p05": float(np.quantile(per_run, 0.05)),
            "p95": float(np.quantile(per_run, 0.95)),
            "max": int(per_run.max()),
            "histogram": {int(k): int((per_run == k).sum()) for k in np.unique(per_run)},
        }
    return out


# --------------------------------------------------------------------------------------------------------
# One spec's full record
# --------------------------------------------------------------------------------------------------------


def _effect_record(f: pd.DataFrame, flag: np.ndarray, target: str, cluster: str = "gsis_id") -> dict:
    """Flagged vs unflagged on `target` (a demeaned residual), with the raw `actual - UM` means and the hit
    rates (share of rows with actual > projection) of each group."""
    stat = cluster_diff(f[target].to_numpy(), flag, f[cluster].to_numpy())
    raw = f["resid"].to_numpy()
    return {
        "n_flagged": stat["n_flag"],
        "n_other": stat["n_other"],
        "mean_resid_flagged": float(raw[flag].mean()) if flag.any() else float("nan"),
        "mean_resid_unflagged": float(raw[~flag].mean()) if (~flag).any() else float("nan"),
        "diff": stat["diff"],
        "lo90": stat["lo"],
        "hi90": stat["hi"],
        "t": stat["t"],
        "hit_rate_flagged": float((raw[flag] > 0).mean()) if flag.any() else float("nan"),
        "hit_rate_unflagged": float((raw[~flag] > 0).mean()) if (~flag).any() else float("nan"),
    }


def _short(stats: dict) -> dict:
    return {
        "n_flagged": stats["n_flag"],
        "diff": stats["diff"],
        "lo90": stats["lo"],
        "hi90": stats["hi"],
        "t": stats["t"],
    }


def describe_rule(rule: list[dict]) -> str:
    def part(cond):
        name = cond["feature"].replace("_l3", " (L3)").replace("_chg", " (L3 - prior 6)")
        return f"{name} {cond['op']} {cond['value']:g}"

    return " and ".join(part(cond) for cond in rule)


def spec_record(f: pd.DataFrame, spec: Spec, ev: dict, rates: tuple[float, float]) -> dict:
    c = spec.candidate
    sweep = [
        {
            "rule": rule,
            "eligible": row["eligible"],
            **{p: {k: row[p][k] for k in ("n_flag", "diff", "lo", "hi", "t")} for p in ("fit", "test")},
        }
        for rule, row in zip(spec.rules, ev["thresholds"], strict=True)
    ]
    out = {
        "id": spec.id,
        "candidate": c.key,
        "label": c.label,
        "position": spec.position,
        "shape": spec.shape,
        "source": c.source,
        "own_candidate": c.own,
        "features": spec.features,
        "n_rows": int(len(spec.idx)),
        "n_thresholds_swept": len(spec.rules),
        "sweep": sweep,
        "null_keep_rate": rates[0],
        "null_keep_rate_um_matched": rates[1],
    }
    k = ev["chosen"]
    if k is None:
        return {
            **out,
            "chosen": None,
            "verdict": "drop",
            "reasons": ["no threshold had enough flagged fit rows"],
            "passes_keep_rule": False,
            "um_matched": None,
            "survives_um_matching": False,
        }
    sub = f.iloc[spec.idx].reset_index(drop=True)
    flag = spec.flags[:, k]
    rec = {}
    for name, sel in (("fit", spec.fit), ("test", ~spec.fit)):
        s = sub[sel].reset_index(drop=True)
        rec[name] = _effect_record(s, flag[sel], "resid_c")
    others = {
        "vs_l8": chosen_stats(spec, k, f["resid_l8_c"].to_numpy()),
        "um_matched": chosen_stats(spec, k, f["resid_m"].to_numpy()),
    }
    verdict = classify(ev["thresholds"][k]["fit"], ev["thresholds"][k]["test"])
    matched = classify(others["um_matched"]["fit"], others["um_matched"]["test"])
    per_season = pd.Series(flag).groupby(sub["season"].to_numpy()).sum()
    resid = sub["resid_c"].to_numpy()
    seasons = sub["season"].to_numpy()
    by_season = {
        int(y): float(resid[(seasons == y) & flag].mean() - resid[(seasons == y) & ~flag].mean())
        for y in np.unique(seasons)
        if (flag & (seasons == y)).any() and (~flag & (seasons == y)).any()
    }
    return {
        **out,
        "chosen": {"rule": spec.rules[k], "definition": describe_rule(spec.rules[k])},
        "fit": rec["fit"],
        "test": rec["test"],
        "vs_l8": {p: _short(others["vs_l8"][p]) for p in ("fit", "test")},
        "um_matched": {
            **{p: _short(others["um_matched"][p]) for p in ("fit", "test")},
            "verdict": matched["verdict"],
            "reasons": matched["reasons"],
        },
        "n_per_season": {int(s): int(v) for s, v in per_season.items()},
        "effect_by_season": by_season,
        "verdict": verdict["verdict"],
        "reasons": verdict["reasons"],
        "fit_interval_excludes_zero": bool(rec["fit"]["lo90"] > 0 or rec["fit"]["hi90"] < 0),
        "passes_keep_rule": verdict["passes"],
        "passes_um_matched": matched["passes"],
    }


# --------------------------------------------------------------------------------------------------------
# Overlap
# --------------------------------------------------------------------------------------------------------


def jaccard(a: np.ndarray, b: np.ndarray) -> float:
    union = np.logical_or(a, b).sum()
    return float(np.logical_and(a, b).sum() / union) if union else 0.0


def resolve_overlap(
    candidates: list[dict], flags: dict[str, np.ndarray], strength
) -> tuple[list[dict], dict[str, dict], list[dict]]:
    """Walk the passing signals strongest first (`strength(record)`, larger is stronger). One that flags
    mostly the same player-games (Jaccard > `JACCARD_LIMIT`) as a stronger one already kept is redundant.
    Returns (kept, {redundant id: {"with": id, "jaccard": j}}, the Jaccard of every compared pair). Signals
    in different positions cannot overlap and are not compared."""
    ordered = sorted(candidates, key=lambda r: -strength(r))
    kept, redundant, pairs = [], {}, []
    for r in ordered:
        clash = None
        for other in kept:
            if other["position"] != r["position"]:
                continue
            j = jaccard(flags[r["id"]], flags[other["id"]])
            pairs.append({"a": other["id"], "b": r["id"], "jaccard": j})
            if j > JACCARD_LIMIT and clash is None:
                clash = {"with": other["id"], "jaccard": j}
        if clash is None:
            kept.append(r)
        else:
            redundant[r["id"]] = clash
    return kept, redundant, pairs


def chosen_flag(spec: Spec, ev: dict, n_rows: int) -> np.ndarray:
    flag = np.zeros(n_rows, dtype=bool)
    flag[spec.idx] = spec.flags[:, ev["chosen"]]
    return flag


ROUTES = {
    "answer": "No free nflverse release has per-player routes run. Nothing was proxied with snaps.",
    "checked": {
        "pbp_participation": "has a `route` column, but it is ONE label per play -- the route of the "
        "targeted receiver on a pass play, blank otherwise -- with no player id attached; it is also only "
        "~38% populated in 2016-2022. `offense_players` lists who was on the field, which is snaps, not "
        "routes.",
        "ftn_charting": "play-level flags (play action, motion, screen, drop, contested ball ...); no "
        "routes.",
        "pfr_advstats (rec / pass / rush)": "weekly broken tackles, drops and rating when targeted; no "
        "routes.",
        "nextgen_stats (receiving)": "separation, cushion, air yards share and YAC; no routes.",
        "snap_counts": "offensive snaps and snap share, not routes.",
        "stats_player / ffopportunity ep_weekly / stats_team": "no routes column.",
    },
    "consequence": "Targets per route run (TPRR) and routes-based target share cannot be built from public "
    "data. Snap share (candidate 6) is the closest thing available and is tested as itself.",
}


def run_study(f: pd.DataFrame, n_shuffles: int = N_SHUFFLES, seed: int = SEED, log=lambda msg: None) -> dict:
    specs = build_specs(f)
    log(f"R6: {len(specs)} specs, {sum(s.flags.shape[1] for s in specs)} thresholds")
    resid_c = f["resid_c"].to_numpy()
    evals = {s.id: evaluate_spec(s, resid_c) for s in specs}
    log(f"R6: shuffle baseline ({n_shuffles} shuffles)")
    shuffle = shuffle_baseline(f, specs, n_shuffles, seed)
    by_id = {s.id: s for s in specs}
    records = [
        spec_record(
            f,
            s,
            evals[s.id],
            (shuffle["per_spec_keep_rate"][s.id], shuffle["per_spec_keep_rate_um_matched"][s.id]),
        )
        for s in specs
    ]
    flags = {
        r["id"]: chosen_flag(by_id[r["id"]], evals[r["id"]], len(f))
        for r in records
        if r["chosen"] is not None
    }
    # The brief's rule, then redundancy among what passes it.
    passing = [r for r in records if r["passes_keep_rule"]]
    kept, redundant, pairs = resolve_overlap(passing, flags, lambda r: abs(r["test"]["t"]))
    for r in passing:
        if r["id"] in redundant:
            clash = redundant[r["id"]]
            r["verdict"] = "drop"
            r["reasons"] = [
                f"passes the keep rule but flags mostly the same player-games as {clash['with']} "
                f"(Jaccard {clash['jaccard']:.2f}), which is stronger"
            ]
            r["redundant_with"] = clash["with"]
    # The stricter rule: still a keep once compared only among players UM rates alike.
    both = [r for r in passing if r["passes_um_matched"]]
    kept_m, redundant_m, _ = resolve_overlap(both, flags, lambda r: abs(r["um_matched"]["test"]["t"]))
    for r in records:
        r["survives_um_matching"] = r["id"] in {k["id"] for k in kept_m}
        if r["id"] in redundant_m:
            r["redundant_with_um_matched"] = redundant_m[r["id"]]["with"]
    shuffle["observed_keeps_before_overlap"] = len(passing)
    shuffle["observed_keeps_um_matched_before_overlap"] = sum(r["passes_um_matched"] for r in passing)
    for name, observed in (
        ("brief_rule", shuffle["observed_keeps_before_overlap"]),
        ("um_matched_rule", shuffle["observed_keeps_um_matched_before_overlap"]),
    ):
        hist = shuffle[name]["histogram"]
        shuffle[name]["share_of_shuffles_with_at_least_observed"] = float(
            sum(v for k, v in hist.items() if int(k) >= observed) / sum(hist.values())
        )
    return {
        "signals": records,
        "keeps": [r["id"] for r in kept],
        "keeps_um_matched": [r["id"] for r in kept_m],
        # Sam's decision: build only the UM-matched signals. `keeps` (the brief's rule alone) is kept for the
        # record; it includes rows that are mostly UM's top-end over-projection and must not become chips.
        "recommended_chips": [r["id"] for r in kept_m],
        "recommended_chips_note": "Build only these (they keep against UM and among players UM rates alike). "
        "Rows in `keeps` but not here are not to be built.",
        "overlap_pairs": pairs,
        "shuffle": shuffle,
        "routes": ROUTES,
        "candidates": [
            {
                "key": c.key,
                "label": c.label,
                "positions": list(c.positions),
                "shapes": list(c.shapes),
                "source": c.source,
                "unit": c.unit,
                "definition": c.definition,
                "own_candidate": c.own,
                "why": c.why,
            }
            for c in CANDIDATES
        ],
    }


def write_outputs(result: dict, bands: dict, n: int, directory=None) -> None:
    meta = metadata(
        "r6",
        SEASONS,
        n,
        window={"recent_games": 3, "prior_games": 6, "min_prior_games_level": 3, "min_prior_games_change": 9},
        min_flagged_fit=MIN_FLAGGED,
        min_flagged_test=MIN_TEST_N,
        min_effect_points=MIN_EFFECT,
        jaccard_limit=JACCARD_LIMIT,
        keep_rule=(
            "same sign in fit and test, |test effect| >= min_effect, test 90% interval excludes 0, test n >= "
            "min_flagged_test; context = the same but exactly one of those three missed"
        ),
    )
    write_json("usage_signals.json", result, meta, directory)
    band_meta = metadata("r6", SEASONS, bands["n_player_games"], trend_seasons=[2018, 2025])
    write_json("trend_bands.json", bands, band_meta, directory)


def run(log=lambda msg: None, n_shuffles: int = N_SHUFFLES, seed: int = SEED) -> dict:
    log("R6: building per-game usage measures and their prior-only windows")
    base = load_baseline(log=log)
    sp = data.read_stats_player(
        SEASONS,
        [
            "player_id",
            "position",
            "team",
            "season",
            "week",
            "season_type",
            "targets",
            "receptions",
            "carries",
            "receiving_air_yards",
            "target_share",
            "air_yards_share",
            "wopr",
        ],
    )
    pbp = data.read_pbp()
    games = player_game_frame(sp, pbp, data.read_snaps(), data.read_players())
    team_games = team_proe(pbp)
    feats = usage_features(games, team_games)
    frame = signal_frame(base, feats)
    result = run_study(frame, n_shuffles, seed, log)
    log("R6: trend bands")
    bands = trend_bands(games, feats, frame)
    write_outputs(result, bands, len(frame))
    return {**result, "frame": frame, "bands": bands}


__all__ = ["run", "run_study", "FIT_SEASONS"]
