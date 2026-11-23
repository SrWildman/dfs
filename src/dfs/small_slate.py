"""Small slates: a slate is small by its game count, not its day (Thanksgiving's 3 games, a Saturday's 2-3,
Christmas, and every playoff slate, the Sunday "main" of Wild Card, Divisional and Championship included).

A small pool concentrates ownership hard (a 2-game slate had six skill players projected over 50%), makes
same-team pairs routine and leaves salary unused on purpose. So, only when the slate is small
(`games <= [small_slate] max_games`, default 4):

- **The ownership cuts are the slate's own** (`slate_chalk_cut`): "chalk" is the top `CHALK_QUANTILE` of the
  slate's double-up ownership (`CashOwn`, players at `RELEVANT_OWN`+), never below the fixed main-slate cut.
  On a main slate (more than `max_games` games, or a slate whose size is not known) every cut is the fixed
  constant, exactly as before.
- **Rules that don't fit a small pool** are off by default: the cash Default's cheap-pair rule (the pair is
  still flagged), and the builders' two hard rules (one RB a game, no DST against his own QB:
  `late_swap_search.relaxed_rules`).
- **What is not calibrated says so**: the double-up line and the copies estimate are fitted on main slates
  (`CASH_LINE_NOTE`, `COPIES_NOTE`).

The slate's size is read from its own DraftKings draft group (`data/current/draftkings.csv`, or a secondary
slate's own copy, `slate.py`): the distinct games in `Game Info` (`current`, cached on the file's modification
time). A frame or the page's rows count as small only when that draft group is small AND they cover that few
games too (`frame_is_small`): a test fixture of three teams, or a past main slate's frame read during a
playoff week, stays a main slate. Everything else here is pure.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

MAX_GAMES = 4  # `[small_slate] max_games`: a slate of this many games or fewer is small
CHALK_QUANTILE = 0.90  # small slate: "chalk" is the top 10% of the slate's double-up ownership...
RELEVANT_OWN = 0.01  # ...among the players at 1%+ (the rest are nobody's question)
MIN_RELEVANT = 10  # fewer owned players than this and the quantile is noise: the fixed cut stands

_GAME_RE = re.compile(r"^\s*([A-Z]{2,4})@([A-Z]{2,4})\b")

NOTE = (
    "Small slate: ownership concentrates, so chalk is the top 10% of this slate's projected double-up "
    "ownership, not a flat 30%; the cheap-pair rule, one RB a game and no DST against his QB are off; salary "
    "left is a fair way to differ."
)
CASH_LINE_NOTE = "small slate: line not calibrated (the +9.5 offset is fitted on main slates)"
COPIES_NOTE = (
    "Copies on a small slate: the same ownership-product estimate as the main slate (fitted on main-slate "
    "standings), so it rises with this slate's ownership; untested here: no small-slate standings exist yet."
)
SALARY_LEFT_NOTE = (
    "Salary left is a dupe lever on a small slate: lineups that leave $1,000+ unused are duplicated less "
    "(information, never forced)."
)


@dataclass(frozen=True)
class SlateSize:
    games: int
    max_games: int = MAX_GAMES
    chalk_cut: float | None = None  # the slate's own chalk cut (small slates only); None: the fixed one

    @property
    def small(self) -> bool:
        return 0 < self.games <= self.max_games

    @property
    def label(self) -> str:
        """`3-game slate`."""
        return f"{self.games}-game slate"


# -- counting the games --


def games_in_draft_group(game_info: Iterable[object]) -> int:
    """The distinct games in DraftKings' `Game Info` cells (`TB@DET 11/22/2026 01:00PM ET`)."""
    seen = set()
    for cell in game_info:
        m = _GAME_RE.match(str(cell or ""))
        if m:
            seen.add(frozenset(m.groups()))
    return len(seen)


def games_in_frame(frame: pd.DataFrame) -> int:
    """The distinct games in a player frame: `Game Info` when it has one, else unordered Team/Opp pairs."""
    if "Game Info" in frame.columns:
        return games_in_draft_group(frame["Game Info"])
    if not {"Team", "Opp"} <= set(frame.columns):
        return 0
    pairs = {
        frozenset((str(t).strip(), str(o).strip().lstrip("@")))
        for t, o in zip(frame["Team"], frame["Opp"], strict=False)
        if str(t).strip() and str(o).strip() and str(t) != "nan" and str(o) != "nan"
    }
    return len(pairs)


# -- the slate's own chalk cut --


def slate_chalk_cut(own: Iterable[object], base: float) -> float:
    """The small slate's chalk cut: the `CHALK_QUANTILE` of the ownership values at `RELEVANT_OWN`+, never
    below `base`; `base` when fewer than `MIN_RELEVANT` players are owned (or none are)."""
    values = pd.to_numeric(pd.Series(list(own), dtype=object), errors="coerce").dropna()
    values = values[values >= RELEVANT_OWN]
    if len(values) < MIN_RELEVANT:
        return base
    return max(base, round(float(np.quantile(values.to_numpy(dtype=float), CHALK_QUANTILE)), 4))


def size_of(
    salaries: pd.DataFrame | None,
    edge: pd.DataFrame | None = None,
    *,
    max_games: int = MAX_GAMES,
    own_column: str = "CashOwn",
) -> SlateSize | None:
    """The slate's size from its draft group (`salaries`, DK's shape), else from `edge`'s Team/Opp; with the
    slate's own chalk cut (from `edge`'s `own_column`) when it is small. None when neither says."""
    games = 0
    if salaries is not None and len(salaries):
        games = games_in_frame(salaries)
    if not games and edge is not None and len(edge):
        games = games_in_frame(edge)
    if not games:
        return None
    size = SlateSize(games, max_games)
    if size.small and edge is not None and own_column in edge.columns:
        from dfs.week_plays import CHALK

        cut = slate_chalk_cut(edge[own_column], CHALK)
        size = SlateSize(games, max_games, cut if cut != CHALK else None)
    return size


def cut(base: float, size: SlateSize | None) -> float:
    """`base` (the fixed main-slate cut) unless the slate is small and has its own cut: then the slate's,
    scaled by `base / 30%` so a lower fixed cut (the GPP 20-25% lines) moves by the same ratio."""
    from dfs.week_plays import CHALK

    if size is None or not size.small or size.chalk_cut is None:
        return base
    return max(base, round(size.chalk_cut * base / CHALK, 4))


# -- the current slate (the page, the builders, the live calls) --

_cache: dict[tuple, SlateSize | None] = {}


def _mtime(path: Path) -> float | None:
    try:
        return path.stat().st_mtime
    except OSError:
        return None


def configured_max_games() -> int:
    """`[small_slate] max_games` from config.toml; the default without a config (tests, a fresh checkout)."""
    try:
        from dfs.config import load_base_config

        return load_base_config().small_slate.max_games
    except Exception:  # noqa: BLE001 - no config: the default
        return MAX_GAMES


def current(max_games: int | None = None) -> SlateSize | None:
    """The running slate's size (`slate.py` picks whose files) from its synced DraftKings draft group
    (`draftkings.csv`'s `Game Info`); None before a sync. Cached on the file's modification time."""
    from dfs import store

    limit = configured_max_games() if max_games is None else max_games
    dk = store.current_dir() / "draftkings.csv"
    key = (str(dk), _mtime(dk), limit)
    if key in _cache:
        return _cache[key]
    salaries = _read(dk)
    games = games_in_frame(salaries) if salaries is not None and "Game Info" in salaries.columns else 0
    size = SlateSize(games, limit) if games else None
    _cache.clear()
    _cache[key] = size
    return size


def _read(path: Path) -> pd.DataFrame | None:
    try:
        return pd.read_csv(path)
    except (OSError, ValueError):
        return None


def _small_games(games: int, max_games: int | None) -> bool:
    """A frame of `games` games is judged small only when the running slate's own draft group is small
    (`current`) AND the frame covers no more games than the limit: a test fixture of three teams, or a past
    main slate's frame read during a playoff week, stays a main slate."""
    limit = configured_max_games() if max_games is None else max_games
    size = current(limit)
    return size is not None and size.small and 0 < games <= limit


def frame_is_small(frame: pd.DataFrame, max_games: int | None = None) -> bool:
    """True when the running slate is small (`current`) and `frame` (every player of one slate, EdgeRaw's)
    covers that few games too."""
    return _small_games(games_in_frame(frame), max_games)


_rows_memo: dict[tuple, tuple[object, float]] = {}


def rows_cut(rows: list[dict] | None, base: float, max_games: int | None = None) -> float:
    """The chalk cut for the slate whose EdgeRaw rows (the page's `inp.edge`, every player of the slate) are
    `rows`: `base` on a main slate; on a small one (`frame_is_small`) the slate's own (`slate_chalk_cut` on
    their `CashOwn`), scaled to `base` as `cut` does. Memoised on the list (a page render asks often)."""
    if not rows:
        return base
    limit = configured_max_games() if max_games is None else max_games
    key = (id(rows), len(rows), base, limit, current(limit))
    hit = _rows_memo.get(key)
    if hit is not None and hit[0] is rows:
        return hit[1]
    frame = pd.DataFrame([{k: r.get(k) for k in ("Team", "Opp", "CashOwn")} for r in rows])
    games = games_in_frame(frame)
    value = base
    if _small_games(games, limit):
        from dfs.week_plays import CHALK

        own_cut = slate_chalk_cut(frame["CashOwn"], CHALK)
        value = cut(base, SlateSize(games, limit, own_cut if own_cut != CHALK else None))
    if len(_rows_memo) > 32:
        _rows_memo.clear()
    _rows_memo[key] = (rows, value)
    return value


def rows_size(rows: list[dict] | None, max_games: int | None = None) -> SlateSize | None:
    """The slate's size for the page (`inp.edge`): None unless it is small (`frame_is_small`)."""
    if not rows:
        return None
    limit = configured_max_games() if max_games is None else max_games
    games = games_in_frame(pd.DataFrame([{k: r.get(k) for k in ("Team", "Opp")} for r in rows]))
    return SlateSize(games, limit) if _small_games(games, limit) else None


def percent(value: float) -> str:
    """`30%` (a chalk cut as the page writes it)."""
    return f"{value * 100:.0f}%"


def frame_cut(edge: pd.DataFrame, base: float, max_games: int | None = None) -> float:
    """`cut` for a whole slate's EdgeRaw frame, by its own `CashOwn`: `base` on a main slate."""
    if not frame_is_small(edge, max_games):
        return base
    limit = configured_max_games() if max_games is None else max_games
    return cut(base, size_of(None, edge, max_games=limit))


def column_cut(frame: pd.DataFrame, column: str, base: float, max_games: int | None = None) -> float:
    """A fixed cut on `column` (a fraction, like EdgeRaw's 20% `CHALK` flag on TFFB's Own%) made the slate's
    own when the slate is small (`frame_is_small`): the top 10% of `column`, never under `base`."""
    if column not in frame.columns or not frame_is_small(frame, max_games):
        return base
    return slate_chalk_cut(frame[column], base)
