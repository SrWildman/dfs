"""Salary watch (Plan K item 10): each player's DraftKings salary against last week's, flagging a drop of
`DROP` dollars or more when his role held steady.

**Last week's salary** is the main-slate DraftKings file the sync kept for last week (`contests.week_frames`:
the last snapshot before that Sunday's lock), matched on normalised name and team; a player who was not on
last week's main slate (bye, a prime-time game) has no comparison and is left out, never guessed.

**Steady role**: over his last `LAST_GAMES` games played (the Player card's log), his snap share, or else his
targets, each within `STEADY` (10%) of their own average. A drop with a steady role is the flag: the price
fell while the job did not. A drop without one is listed apart, since the role moved too.

Pure: `watch` takes the edge rows, last week's salaries and the game log.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass

from dfs.player_join import normalize_name

DROP = 300
STEADY = 0.10
LAST_GAMES = 3
SHOWN = 12


def key(name: object, team: object) -> tuple[str, str]:
    return normalize_name(str(name or "")), str(team or "").strip().upper()


def _num(v: object) -> float | None:
    try:
        x = float(v)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return None if x != x else x


def steady_role(games: list[dict]) -> str:
    """`snaps 71 / 70 / 75%` or `targets 6 / 6 / 7` when the last three games held within 10% of their own
    average (snaps first), else ""."""
    last = games[:LAST_GAMES]
    if len(last) < LAST_GAMES:
        return ""
    for field, label, unit in (("Snap", "snaps", "%"), ("Tgt", "targets", "")):
        values = [_num(g.get(field)) for g in last]
        if any(v is None for v in values):
            continue
        mean = sum(values) / len(values)  # type: ignore[arg-type]
        if mean <= 0:
            continue
        if all(abs(v - mean) <= STEADY * mean for v in values):  # type: ignore[operator]
            scale = 100 if unit and max(values) <= 1.5 else 1  # type: ignore[type-var] - Snap% is a fraction
            shown = " / ".join(f"{v * scale:.0f}" for v in values)  # type: ignore[operator]
            return f"{label} {shown}{unit}"
    return ""


@dataclass(frozen=True)
class Move:
    id: str
    name: str
    position: str
    team: str
    salary: float
    before: float
    role: str  # the steady-role text, "" when it moved
    logged: bool = True  # he has a three-game log at all (a DST has none)

    @property
    def change(self) -> float:
        return self.salary - self.before


def watch(edge: list[dict], before: dict[tuple[str, str], float] | None, log: dict[str, list[dict]]) -> dict:
    """The page's payload: `flagged` (a drop of DROP+ with a steady role, biggest first), `other_drops`
    (DROP+ without one), the counts, and whether last week's salaries were found at all."""
    if not before:
        return {"found": False, "flagged": [], "other_drops": [], "matched": 0, "drops": 0}
    flagged, other = [], []
    matched = 0
    for r in edge:
        now = _num(r.get("Salary"))
        was = before.get(key(r.get("Name"), r.get("Team")))
        if now is None or was is None:
            continue
        matched += 1
        if was - now < DROP:
            continue
        pid = str(r.get("Id") or "")
        move = Move(
            pid,
            str(r.get("Name", "")),
            str(r.get("Position", "")),
            str(r.get("Team", "")),
            now,
            was,
            steady_role(log.get(pid) or []),
            len(log.get(pid) or []) >= LAST_GAMES,
        )
        (flagged if move.role else other).append(move)
    order = lambda m: (m.change, m.name)  # noqa: E731 - the biggest drop first
    flagged.sort(key=order)
    other.sort(key=order)
    return {
        "found": True,
        "flagged": [asdict(m) | {"change": m.change} for m in flagged[:SHOWN]],
        "other_drops": [asdict(m) | {"change": m.change} for m in other[:SHOWN]],
        "matched": matched,
        "drops": len(flagged) + len(other),
    }


def last_week_salaries(dk_dir, season: int, week: int) -> tuple[dict[tuple[str, str], float] | None, str]:  # noqa: ANN001
    """Last week's main-slate salaries by (name, team), and where they came from (or why not)."""
    from dfs.contests import week_frames

    if week <= 1:
        return None, "no earlier week this season"
    frame, note = week_frames.week_frame(dk_dir, season, week - 1)
    if frame is None:
        return None, note
    out = {}
    for name, team, salary in zip(frame["Name"], frame["Team"], frame["Salary"], strict=True):
        s = _num(salary)
        if s is not None:
            out[key(name, team)] = s
    return out, note
