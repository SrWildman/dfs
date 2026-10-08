"""Shared pieces of every study: the season split, the output writers (every file carries its metadata), and
the code version stamp."""

from __future__ import annotations

import json
import subprocess
from datetime import date
from pathlib import Path

import pandas as pd

from dfs.paths import REPO_ROOT

OUTPUT_DIR = REPO_ROOT / "models" / "research"

# The one split every study uses: choose / fit on the early seasons, report on the late ones.
FIT_SEASONS = list(range(2014, 2022))
TEST_SEASONS = list(range(2022, 2026))
SEASONS = FIT_SEASONS + TEST_SEASONS

STUDIES = ("r1", "r2", "r3", "r4", "r5", "r6")
OFFENSE_POSITIONS = ("QB", "RB", "WR", "TE")


def split_of(season: pd.Series) -> pd.Series:
    """'fit' for 2014-2021, 'test' for 2022-2025."""
    return pd.Series(
        ["fit" if s in FIT_SEASONS else "test" for s in season], index=season.index, dtype="object"
    )


def code_version() -> str:
    """Short git SHA of the code that produced an output ('+dirty' if the tree has uncommitted changes), or
    'unknown' outside a git checkout."""
    try:
        sha = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"], cwd=REPO_ROOT, capture_output=True, text=True, timeout=10
        ).stdout.strip()
        dirty = subprocess.run(
            ["git", "status", "--porcelain", "--", "src/dfs/research"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=10,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return "unknown"
    return (sha + ("+dirty" if dirty else "")) if sha else "unknown"


def metadata(study: str, seasons: list[int], n: int, **extra) -> dict:
    """The block every output carries: which study, which seasons, how many rows, when, and which code."""
    return {
        "study": study,
        "seasons": [min(seasons), max(seasons)],
        "fit_seasons": [FIT_SEASONS[0], FIT_SEASONS[-1]],
        "test_seasons": [TEST_SEASONS[0], TEST_SEASONS[-1]],
        "n": int(n),
        "generated": date.today().isoformat(),
        "code_version": code_version(),
        **extra,
    }


def _clean(obj):
    """JSON-safe: numpy scalars to python, NaN/inf to None."""
    if isinstance(obj, dict):
        return {str(k): _clean(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_clean(v) for v in obj]
    if hasattr(obj, "item") and not isinstance(obj, (str, bytes)):
        try:
            obj = obj.item()
        except (ValueError, AttributeError):
            pass
    if isinstance(obj, float) and (obj != obj or obj in (float("inf"), float("-inf"))):
        return None
    return obj


def write_json(name: str, payload: dict, meta: dict, directory: Path | None = None) -> Path:
    """`<name>` under models/research with `metadata` as the first key."""
    path = (directory or OUTPUT_DIR) / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(_clean({"metadata": meta, **payload}), indent=2) + "\n")
    return path


def meta_sidecar(path: Path) -> Path:
    return path.with_name(path.name + ".meta.json")


def write_csv(name: str, frame: pd.DataFrame, meta: dict, directory: Path | None = None) -> Path:
    """A plain CSV (so any tool reads it) plus `<name>.meta.json` holding the same metadata block."""
    path = (directory or OUTPUT_DIR) / name
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(path, index=False, float_format="%.6g")
    meta_sidecar(path).write_text(json.dumps(_clean(meta), indent=2) + "\n")
    return path
