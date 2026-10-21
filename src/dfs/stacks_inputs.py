"""What the Stacks tab needs from the synced week, and the two small caches behind it.

**The slate** comes from the synced EdgeRaw frame (`data/current/edge.csv`): every rosterable player not
listed OUT, IR or Doubtful, his simulator spec (`sim_inputs.build_specs`, CalPts first) and his projected
ownership (TFFB's `Own%`). Before TFFB publishes ownership the field has nothing to be built from, so a
**stand-in** is used (`stand_in_ownership`: each position's share in proportion to CalPts cubed) and the tab
says so and hides every number that depends on the field's make-up (`stacks_tab`).

**The field model** (construction rates and the chalk herd) is fitted on the real fields of the weeks with
standings (`sim.stacks_retro.refit_field_model`, about two minutes: it parses the standings) and saved to
`data/model_cache/stack_field.json`; a sync reads it and never refits. **The retro summary** (the track-record
sentence and the verdict on the leverage signal) is saved beside it in `stacks_retro.json`.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

from dfs.paths import DATA_DIR
from dfs.sim import field_gen

CACHE_DIR = DATA_DIR / "model_cache"
FIELD_MODEL_FILE = CACHE_DIR / "stack_field.json"
RETRO_FILE = CACHE_DIR / "stacks_retro.json"
NOT_PLAYING = frozenset({"OUT", "IR", "D"})
# Ownership shares of the slots a position fills (QB and DST one lineup's worth, RB + WR + TE seven together).
STAND_IN_POWER = 3.0


@dataclass
class LiveSlate:
    slate: field_gen.Slate
    specs: list  # PlayerSpec | None, aligned with slate.names
    ownership_published: bool
    game_info: pd.DataFrame  # team, total, spread, pace
    week: int | None = None
    notes: list[str] = field(default_factory=list)


def ownership_published(edge: pd.DataFrame) -> bool:
    """TFFB's Own% is out (at least one player above zero)."""
    if "Own%" not in edge.columns:
        return False
    return bool((pd.to_numeric(edge["Own%"], errors="coerce").fillna(0.0) > 0).any())


def stand_in_ownership(frame: pd.DataFrame) -> pd.Series:
    """A stand-in for ownership before it is published: within a position (RB, WR and TE share one pot of
    seven slots), a player's share is his CalPts cubed over the group's total. Not a forecast of ownership;
    it only gives the field generator a plausible shape."""
    points = pd.to_numeric(frame["CalPts"], errors="coerce").fillna(0.0).clip(lower=0.0) ** STAND_IN_POWER
    out = pd.Series(0.0, index=frame.index)
    for group, slots in ((["QB"], 1.0), (["DST"], 1.0), (["RB", "WR", "TE"], 7.0)):
        mask = frame["Position"].isin(group)
        total = float(points[mask].sum())
        if total > 0:
            out[mask] = points[mask] / total * slots
    return out


def slate_frame(edge: pd.DataFrame) -> pd.DataFrame:
    """The rosterable rows of the edge frame as the generator's slate frame (`name, position, team, opp,
    salary, own`), with `own` the published Own% or the stand-in."""
    avail = edge["Avail"].fillna("").astype(str).str.upper() if "Avail" in edge.columns else ""
    keep = edge[~pd.Series(avail, index=edge.index).isin(NOT_PLAYING)].copy()
    published = ownership_published(keep)
    own = pd.to_numeric(keep["Own%"], errors="coerce").fillna(0.0) if published else stand_in_ownership(keep)
    return pd.DataFrame(
        {
            "name": keep["Name"].astype(str),
            "position": keep["Position"].astype(str),
            "team": keep["Team"].astype(str),
            "opp": keep["Opp"].astype(str),
            "salary": keep["Salary"],
            "own": own,
        }
    )


def game_info(edge: pd.DataFrame) -> pd.DataFrame:
    """One row per team: the game's `total` (over/under), the team's `spread` and `pace`."""
    rows = {}
    for r in edge.to_dict("records"):
        team = str(r.get("Team") or "")
        if not team or team in rows:
            continue
        rows[team] = {
            "team": team,
            "total": pd.to_numeric(r.get("OverUnder"), errors="coerce"),
            "spread": pd.to_numeric(r.get("Spread"), errors="coerce"),
            "pace": pd.to_numeric(r.get("Pace"), errors="coerce"),
        }
    return pd.DataFrame(list(rows.values()), columns=["team", "total", "spread", "pace"])


def build_live_slate(
    edge: pd.DataFrame, depth_rows=None, gsis_by_id=None, week: int | None = None
) -> LiveSlate:
    from dfs import sim_inputs

    frame = slate_frame(edge)
    published = ownership_published(edge)
    slate = field_gen.Slate.from_frame(frame, own_column="own")
    by_name = sim_inputs.build_specs(edge, depth_rows, gsis_by_id)
    specs = [by_name.get(n) for n in slate.names]
    notes = []
    if not published:
        notes.append("ownership is not published: the field is built from a CalPts stand-in")
    return LiveSlate(slate, specs, published, game_info(edge), week, notes)


# ---------------------------------------------------------------------------------------------------------
# Caches
# ---------------------------------------------------------------------------------------------------------


def save_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, indent=1, sort_keys=True))
    tmp.replace(path)


def load_json(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


def load_field_model(path: Path | None = None):
    """The saved `sim.stacks_retro.FieldModel`, or None when none has been fitted."""
    from dfs.sim.stacks_retro import FieldModel

    d = load_json(path or FIELD_MODEL_FILE)
    if not d:
        return None
    try:
        return FieldModel.from_dict(d)
    except (KeyError, TypeError, ValueError):
        return None


def load_retro(path: Path | None = None) -> dict | None:
    """The saved retro summary: `{"line": str, "verdict": str, "weeks": [...], ...}`, or None."""
    d = load_json(path or RETRO_FILE)
    return d if d and "line" in d and "verdict" in d else None


def top_chalk(slate: field_gen.Slate, herd: field_gen.HerdModel) -> np.ndarray:
    """The herd's chalk lineups on this slate (none when the model has no herd)."""
    from dfs import stacks

    return (
        field_gen.chalk_lineups(slate, min(herd.k, stacks.CHALK_K))
        if herd.share > 0
        else np.zeros((0, 9), dtype=int)
    )
