"""The measured constants from the research pack, read at run time from `models/research/` (never hard-coded).

`dfs research run` writes the JSON / CSV files; this module only reads them (it does not edit
`src/dfs/research/`). A constant that the local round hand-set and the research measured is loaded here, so
re-running the research and committing the new files changes the sheet with no code change. Every loader fails
with `ResearchConstantsError` naming the missing file or key, never with a guess.

- `signal_thresholds()` -- the FADE (TE only) gap and the USAGE (RB carry share only) jumps, with the windows.
- `carry_shares()` -- who inherits an absent RB's carries (next up / each further back / unassigned).
- `target_context()` -- the historical target split quoted next to a target absence (display only).
- `regular_thresholds()` -- the research's definition of a "regular" (whose absence counts).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from dfs.paths import REPO_ROOT

# The research's window for a usage jump: the last RECENT games against the EARLIER games before them, and
# both windows must be full. Imported from the study so the two cannot drift apart.
from dfs.research.r3_signals import EARLIER as USAGE_EARLIER_GAMES
from dfs.research.r3_signals import RECENT as USAGE_RECENT_GAMES

RESEARCH_DIR = REPO_ROOT / "models" / "research"
MIN_CELL = 8  # the research's own minimum cell size (redistribution.csv.meta.json)
SIGNALS_FILE = "signal_thresholds.json"
REDISTRIBUTION_JSON = "redistribution_constants.json"
REDISTRIBUTION_CSV = "redistribution.csv"


class ResearchConstantsError(Exception):
    """A research output is missing or does not hold the key a loader needs."""


def _read_json(name: str, directory: Path | None = None) -> dict:
    path = (directory or RESEARCH_DIR) / name
    if not path.exists():
        raise ResearchConstantsError(
            f"{path} is missing -- run `dfs research run` or restore models/research/"
        )
    return json.loads(path.read_text())


def _need(mapping: dict, *keys: str, where: str):
    value = mapping
    for key in keys:
        if not isinstance(value, dict) or key not in value:
            raise ResearchConstantsError(f"{where}: key {'/'.join(keys)!r} not found (missing {key!r})")
        value = value[key]
    return value


@dataclass(frozen=True)
class SignalThresholds:
    """The thresholds the research kept. Fractions are fractions (0.10 = ten percentage points)."""

    fade_gap_points: float  # TE: last-3 DK/G - last-3 xFP/G at least this
    fade_positions: tuple[str, ...]  # positions whose verdict is "keep" (TE)
    usage_up: float  # RB carry-share jump (last 2 vs the 6 before) at least +this
    usage_down: float  # ... at most -this
    usage_positions: tuple[str, ...]  # positions whose verdict is "keep" (RB)
    recent_games: int = USAGE_RECENT_GAMES
    earlier_games: int = USAGE_EARLIER_GAMES

    @property
    def prior_games_needed(self) -> int:
        """A jump needs both windows full: recent + earlier prior games."""
        return self.recent_games + self.earlier_games


def signal_thresholds(directory: Path | None = None) -> SignalThresholds:
    """FADE (gap only, the measured TE version) and USAGE (carry share) from `signal_thresholds.json`."""
    data = _read_json(SIGNALS_FILE, directory)["signals"]
    where = SIGNALS_FILE
    fade = _need(data, "FADE_down_gap_only", where=where)
    carry_up = _need(data, "USAGE_up", "per_metric", "carry_share", where=where)
    carry_down = _need(data, "USAGE_down", "per_metric", "carry_share", where=where)
    keep = lambda block: tuple(  # noqa: E731
        pos for pos, verdict in _need(block, "verdict_by_position", where=where).items() if verdict == "keep"
    )
    return SignalThresholds(
        fade_gap_points=float(_need(fade, "chosen", "x", where=where)),
        fade_positions=keep(fade),
        usage_up=float(_need(carry_up, "chosen", "threshold", where=where)),
        usage_down=float(_need(carry_down, "chosen", "threshold", where=where)),
        usage_positions=tuple(sorted(set(keep(carry_up)) & set(keep(carry_down)))),
    )


@dataclass(frozen=True)
class RegularThresholds:
    """The research's "regular": prior-3-game target share at least `target_share` OR carry share at least
    `carry_share` (so a bit-part's absence is not an event)."""

    target_share: float
    carry_share: float


def regular_thresholds(directory: Path | None = None) -> RegularThresholds:
    """Parsed from `definitions.regular` in `redistribution_constants.json` (a sentence, not two numbers)."""
    text = str(
        _need(_read_json(REDISTRIBUTION_JSON, directory), "definitions", "regular", where=REDISTRIBUTION_JSON)
    )
    target = re.search(r"target share\s*>=\s*([0-9.]+)", text)
    carry = re.search(r"carry share\s*>=\s*([0-9.]+)", text)
    if not (target and carry):
        raise ResearchConstantsError(
            f"{REDISTRIBUTION_JSON}: cannot read the 'regular' thresholds from {text!r}"
        )
    return RegularThresholds(target_share=float(target.group(1)), carry_share=float(carry.group(1)))


@dataclass(frozen=True)
class CarryShares:
    """Where an absent back's carries go, as fractions of the VACATED carries (net of the control games).

    `next_up` is the best remaining back (RB2 when RB1 is out), `each_other` each further back, and
    `unassigned` the share that goes to no teammate in the rotation and STAYS unassigned. The shares may sum
    above `1 - unassigned` for a deep rotation; callers normalise only then."""

    absent: str
    next_up_label: str
    next_up: float
    each_other: float
    unassigned: float
    n: int


@dataclass(frozen=True)
class TargetContext:
    """What history says about a missing pass catcher's targets: no single teammate gains much."""

    absent: str
    next_up_label: str
    next_up: float
    unassigned: float
    n: int


def _csv_value(table: pd.DataFrame, absent: str, item: str, channel: str) -> tuple[float, int]:
    row = table[
        (table["channel"] == channel)
        & (table["absent"] == absent)
        & (table["item"] == item)
        & (table["seasons"] == "all")
    ]
    if row.empty:
        raise ResearchConstantsError(f"{REDISTRIBUTION_CSV}: no row for {channel}/{absent}/{item}")
    return float(row.iloc[0]["net_mean_frac"]), int(row.iloc[0]["n"])


def _csv(directory: Path | None) -> pd.DataFrame:
    path = (directory or RESEARCH_DIR) / REDISTRIBUTION_CSV
    if not path.exists():
        raise ResearchConstantsError(
            f"{path} is missing -- run `dfs research run` or restore models/research/"
        )
    return pd.read_csv(path)


def carry_shares(absent: str, directory: Path | None = None) -> CarryShares | None:
    """The measured carries split when `absent` (`RB1` or `RB2`) is out; None for a label the study has no
    cell for (RB3+ out had n=2).

    RB1 out: the next up is RB2, read from the JSON's `recommended` block (cross-checked against the CSV,
    which must agree). RB2 out: the next up is RB1, read from the CSV. The unassigned share is the JSON's
    `nowhere`; the per-player share of each further back is the CSV's `RB3+` row (the JSON holds only the
    whole RB group)."""
    if absent not in ("RB1", "RB2"):
        return None
    block = _need(
        _read_json(REDISTRIBUTION_JSON, directory),
        "recommended",
        f"carries:{absent}",
        where=REDISTRIBUTION_JSON,
    )
    unassigned = float(_need(block, "nowhere", "net_mean_frac", where=REDISTRIBUTION_JSON))
    n = int(_need(block, "nowhere", "n", where=REDISTRIBUTION_JSON))
    table = _csv(directory)
    if absent == "RB1":
        item = _need(block, "next_lower_same_position", "item", where=REDISTRIBUTION_JSON)
        next_up = float(_need(block, "next_lower_same_position", "net_mean_frac", where=REDISTRIBUTION_JSON))
        csv_next, _ = _csv_value(table, absent, item, "carries")
        if abs(csv_next - next_up) > 0.02:
            raise ResearchConstantsError(
                f"{REDISTRIBUTION_JSON} and {REDISTRIBUTION_CSV} disagree on {absent} -> {item}: "
                f"{next_up:.3f} vs {csv_next:.3f}"
            )
        label = str(item)
    else:
        label = "RB1"
        next_up, _ = _csv_value(table, absent, label, "carries")
    each_other, _ = _csv_value(table, absent, "RB3+", "carries")
    return CarryShares(
        absent=absent, next_up_label=label, next_up=next_up, each_other=each_other, unassigned=unassigned, n=n
    )


def target_context(absent: str, directory: Path | None = None) -> TargetContext | None:
    """The historical target split quoted beside a target absence (`WR1`, `WR2`, `WR3`, `TE1`): the next man
    up's share of the vacated targets and the share that goes nowhere. None when the study has no usable cell
    (too few absences, or a negative leak)."""
    if not absent.startswith(("WR", "TE")):
        return None
    block = _read_json(REDISTRIBUTION_JSON, directory).get("recommended", {}).get(f"targets:{absent}")
    if not block:
        return None
    nxt = block.get("next_lower_same_position")
    nowhere = block.get("nowhere")
    if not nxt or not nowhere or "net_mean_frac" not in nxt or "net_mean_frac" not in nowhere:
        return None
    if nowhere["n"] < MIN_CELL or nowhere["net_mean_frac"] < 0:
        return None
    return TargetContext(
        absent=absent,
        next_up_label=str(nxt["item"]),
        next_up=float(nxt["net_mean_frac"]),
        unassigned=float(nowhere["net_mean_frac"]),
        n=int(nowhere["n"]),
    )


def target_context_line(absent_position: str, ctx: TargetContext | None) -> str:
    """The sentence shown beside a target absence, numbers from the JSON (net means)."""
    lead = "historically, no single teammate gains much"
    if ctx is None:
        return f"{lead} (too few past absences at this role to quote a split)"
    return (
        f"{lead}: {ctx.next_up_label} +{round(100 * ctx.next_up):d}% of the vacated targets, "
        f"~{round(100 * ctx.unassigned):d}% goes nowhere (n={ctx.n})"
    )
