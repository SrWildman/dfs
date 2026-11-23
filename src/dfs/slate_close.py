"""The close of a week that had a secondary slate (`slate.py`; Thanksgiving first): its money is booked on its
own sheet, its learning is kept out of the main slate's.

**Money** (`slate_chain`). Every slate is a ledger of its own: `dfs week close --slate thu` books the entries
whose contest falls on that slate's day into the Thanksgiving sheet's Bankroll and its Season row ("12 Thu"),
and the main close leaves them out, so each entry is booked once, in one ledger. The contest log (all of
DraftKings' history, merged) still holds every entry.

**Learning, main slate only.** A secondary slate's contests are the Classic contests whose start falls on a
secondary slate's day of the week its sheet is for (`slate_chain.entry_slate`). They are kept out of:
- `Results` (Cash Pts, H2H Entered / Win), the main slate's record (`main_slate_entries`);
- the standings folder every learning step reads (`data/raw/dk_standings/`: the ownership log, the double-up
  line, the copies estimate, the cash post-mortem, Model Check's ownership, what won): their standings files
  are kept apart in `data/slates/<key>/dk_standings/` (`standings_divert`).
The rest needs no change: the post-mortem reads the main slate's entries record, edge at lock and the
scorecards read the main slate's DraftKings snapshots (`data/raw/draftkings/`, which a secondary slate never
writes) and the decisions log is the main page's.

With no `[slates.*]` in config.toml every function here is a no-op, and the close is exactly what it was.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from pathlib import Path

import pandas as pd

from dfs import slate
from dfs.config import Config

STANDINGS_SUBDIR = "dk_standings"


def configured(cfg: Config) -> list[str]:
    """The secondary slates config.toml has a sheet for (`[slates.<key>]`), sorted."""
    return sorted(getattr(cfg, "slates", {}) or {})


def standings_dir(key: str) -> Path:
    """Where a secondary slate's standings files are kept: `data/slates/<key>/dk_standings/` (outside
    `data/raw/dk_standings/`, which the learning steps scan recursively)."""
    return slate.root(key) / STANDINGS_SUBDIR


def main_slate_entries(
    entries: Sequence, cfg: Config, season: int, week: int | None, weeks: dict[str, int] | None = None
) -> list:
    """`entries` (`models.ContestEntry`) without the secondary slates' contests. `weeks` is each secondary
    slate's week (`slate_chain.slate_weeks`): with it, every week's are left out (closing Week 13 never puts
    Thanksgiving's double-up back into Week 12's Results). Without it, as #95 did, only Week `week`'s."""
    keys = configured(cfg)
    if not keys or week is None:
        return list(entries)
    from dfs import slate_chain

    return slate_chain.for_slate(entries, slate.MAIN, season, weeks or {k: week for k in keys})


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
            f"${fees:,.2f} in fees, net {won - fees:+,.2f}: left out of this sheet's ledger, booked on its "
            f"own sheet by `dfs week close --slate {key}`. Its standings are kept apart ({n_files} file(s) "
            f"in {kept}); Results, the post-mortem, edge at lock, the scorecards and every model fit ran for "
            "the main slate only."
        )
    return lines
