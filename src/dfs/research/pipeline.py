"""Run the studies from the cache: what `dfs research run` calls. No network; a missing dataset is a
`ResearchDataError` naming `dfs research fetch`."""

from __future__ import annotations

from collections.abc import Callable

from dfs.research import r1_redistribution, r2_matchup, r3_signals, r4_weather, r5_checks, r6_usage
from dfs.research.common import OUTPUT_DIR, STUDIES

# study -> (module, the files it writes)
STUDY_MODULES = {
    "r1": (r1_redistribution, ["redistribution.csv", "redistribution_constants.json"]),
    "r2": (r2_matchup, ["matchup_weights.json"]),
    "r3": (r3_signals, ["signal_thresholds.json"]),
    "r4": (r4_weather, ["weather.json"]),
    "r5": (r5_checks, ["quick_checks.json"]),
    "r6": (r6_usage, ["usage_signals.json", "trend_bands.json"]),
}


def resolve(study: str) -> list[str]:
    """`all` or one of r1..r6 as the list of studies to run, in order."""
    study = study.lower()
    if study == "all":
        return list(STUDIES)
    if study not in STUDY_MODULES:
        raise ValueError(f"unknown study {study!r}: use one of {', '.join(STUDIES)} or all")
    return [study]


def run_studies(study: str = "all", log: Callable[[str], None] = lambda msg: None) -> list[str]:
    """Run the named study (or all of them), rebuilding its files under `models/research/`. Returns the paths
    written, relative to the repository."""
    from dfs.research.baseline import ensure_cache

    names = resolve(study)
    if any(n != "r1" for n in names):
        ensure_cache(log=log)
    written = []
    for name in names:
        module, files = STUDY_MODULES[name]
        module.run(log=log)
        written += [str((OUTPUT_DIR / f).relative_to(OUTPUT_DIR.parent.parent)) for f in files]
    return written
