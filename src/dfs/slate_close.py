"""The close of a week that had a secondary slate (`slate.py`; Thanksgiving first): its money is booked, its
learning is kept out of the main slate's.

**Money.** One `dfs week close` per week reads DraftKings' history export. Every NFL entry of the week, both
slates', is booked once in the Bankroll ledger (by entry key, as always) and the contest log, so the ledger
and the contest plan count both. Nothing here changes that.

**Learning, main slate only.** A secondary slate's contests are the Classic contests of the week whose start
falls on a secondary slate's day (`slate.contest_slate`, for the slates `config.toml` has a sheet for). They
are kept out of:
- `Results` (Cash Pts, H2H Entered / Win), the main slate's record (`main_slate_entries`);
- the standings folder every learning step reads (`data/raw/dk_standings/`: the ownership log, the double-up
  line, the copies estimate, the cash post-mortem, Model Check's ownership, what won): their standings files
  are kept apart in `data/slates/<key>/dk_standings/` (`standings_divert`).
The rest needs no change: the post-mortem reads the main slate's entries record, edge at lock and the
scorecards read the main slate's DraftKings snapshots (`data/raw/draftkings/`, which a secondary slate never
writes) and the decisions log is the main page's. None of them runs for a secondary slate: with three games
and no history of its own there is nothing they can measure safely, so the close says so (`close_lines`).

With no `[slates.*]` in config.toml every function here is a no-op, and the close is exactly what it was.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from pathlib import Path

import pandas as pd

from dfs import nfl_calendar, slate
from dfs.config import Config

STANDINGS_SUBDIR = "dk_standings"


def configured(cfg: Config) -> list[str]:
    """The secondary slates config.toml has a sheet for (`[slates.<key>]`), sorted."""
    return sorted(getattr(cfg, "slates", {}) or {})


def standings_dir(key: str) -> Path:
    """Where a secondary slate's standings files are kept: `data/slates/<key>/dk_standings/` (outside
    `data/raw/dk_standings/`, which the learning steps scan recursively)."""
    return slate.root(key) / STANDINGS_SUBDIR


def main_slate_entries(entries: Sequence, cfg: Config, season: int, week: int | None) -> list:
    """`entries` (`models.ContestEntry`) without the secondary slates' contests of Week `week` (other weeks
    are
    left exactly as they are)."""
    keys = configured(cfg)
    if not keys or week is None:
        return list(entries)
    out = []
    for e in entries:
        moment = getattr(e, "contest_date", None)
        same_week = moment is not None and nfl_calendar.season_week_for_date(moment.date(), season) == week
        if same_week and slate.contest_slate(moment, e.game_type, keys) is not None:
            continue
        out.append(e)
    return out


def secondary_contests(history_csv: Path | None, keys: Sequence[str]) -> dict[str, str]:
    """`{contest id: slate key}` for every secondary-slate contest in a history export (all weeks: a contest's
    id is unique, and its day decides it). Empty with no keys, no export, or an export that cannot be read."""
    if not keys or history_csv is None:
        return {}
    from dfs.contests import history

    try:
        frame = history.read_export(history_csv)
    except (OSError, history.HistoryError):
        return {}
    frame = frame[frame["Sport"].str.strip().str.upper() == "NFL"]
    out: dict[str, str] = {}
    for contest_id, game_type, when in zip(
        frame["Contest_Key"], frame["Game_Type"], frame["Contest_Date_EST"], strict=True
    ):
        moment = pd.to_datetime(when, errors="coerce")
        key = slate.contest_slate(None if pd.isna(moment) else moment.to_pydatetime(), game_type, keys)
        if key is not None:
            out[str(contest_id).strip()] = key
    return out


def standings_divert(cfg: Config, history_csv: Path | None) -> Callable[[str], Path | None] | None:
    """For `contests.capture.capture_standings`: a contest id -> its secondary slate's standings folder (None
    for a main-slate contest). None when no secondary slate is configured, so capture is unchanged."""
    by_contest = secondary_contests(history_csv, configured(cfg))
    if not by_contest:
        return None
    return lambda contest_id: standings_dir(by_contest[contest_id]) if contest_id in by_contest else None


def close_lines(cfg: Config, history_csv: Path | None, season: int, week: int | None) -> list[str]:
    """What the close did with each secondary slate of Week `week`: its entries and money (booked with the
    main slate's), where its standings are, and that the learning steps ran for the main slate only. Empty
    when the week had none."""
    keys = configured(cfg)
    if not keys or history_csv is None or week is None:
        return []
    from dfs.contests import history

    try:
        frame = history.enrich(history.read_export(history_csv))
    except (OSError, history.HistoryError):
        return []
    by_contest = secondary_contests(history_csv, keys)
    lines = []
    for key in keys:
        ids = {c for c, k in by_contest.items() if k == key}
        rows = frame[frame["Contest_Key"].astype(str).str.strip().isin(ids)]
        rows = rows[pd.to_numeric(rows["Week"], errors="coerce") == week]
        if rows.empty:
            continue
        fees = sum(float(history.money(v)) for v in rows["Entry_Fee"])
        won = sum(
            float(history.money(a)) + float(history.money(b))
            for a, b in zip(rows["Winnings_Non_Ticket"], rows["Winnings_Ticket"], strict=True)
        )
        kept = standings_dir(key)
        n_files = len([p for p in kept.iterdir() if p.is_file()]) if kept.is_dir() else 0
        lines.append(
            f"The {slate.label(key)} slate, Week {week}: {len(rows)} entr{'y' if len(rows) == 1 else 'ies'}, "
            f"${fees:,.2f} in fees, net {won - fees:+,.2f}: booked in the Bankroll ledger and the contest "
            "log "
            "with the main slate's. Its standings are kept apart "
            f"({n_files} file(s) in {kept}); Results, the post-mortem, edge at lock, the scorecards and "
            "every "
            "model fit ran for the main slate only."
        )
    return lines
