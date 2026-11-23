"""The close of a week that had a secondary slate (`slate.py`; Thanksgiving first). Every slate is a normal
slate (Sam, Nov 23): its own close, money and learning, each on its own sheet and in its own folder.

**Money** (`slate_chain`). Every slate is a ledger of its own: `dfs week close --slate thu` books the entries
whose contest falls on that slate's day into the Thanksgiving sheet's Bankroll and its Season row ("12 Thu"),
and the main close leaves them out, so each entry is booked once, in one ledger. The contest log (all of
DraftKings' history, merged) still holds every entry, each tagged with its slate (`tag_log`, `SlateKey`).

**Learning, each slate its own.** A secondary slate's contests are the Classic contests whose start falls on
the slate's day of the week its sheet is for (`slate_chain.entry_slate`). Its standings files are kept in its
own folder (`data/slates/<key>/raw/dk_standings/`, `standings_divert`), and every record its close writes
goes under `data/slates/<key>/` with its key (`slate.data_path`, `slate.tag`): Results' row ("12 Thu"), the
scored week and Model Check, the ownership log, the post-mortem, edge at lock, the scorecards. The main
slate's records never see them, and the fitted models train on main slates unless `[learning]
fit_secondary_slates` says otherwise (`slate.fit_keys`).

With no `[slates.*]` in config.toml every function here is a no-op, and the close is exactly what it was.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from pathlib import Path

import pandas as pd

from dfs import slate
from dfs.config import Config


def configured(cfg: Config) -> list[str]:
    """The secondary slates config.toml has a sheet for (`[slates.<key>]`), sorted."""
    return sorted(getattr(cfg, "slates", {}) or {})


def standings_dir(key: str) -> Path:
    """Where a slate's standings files are kept: `data/raw/dk_standings/` for main, a secondary slate's own
    copy of it (`data/slates/<key>/raw/dk_standings/`, outside `data/raw/`, which main's learning scans)."""
    from dfs import paths

    return slate.data_path(paths.DK_STANDINGS_DIR, key)


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
    """What the main close did with each secondary slate of Week `week`: its entries and money (left for its
    own sheet), where its standings are, and that its learning is its own close's. Empty when the week had
    none."""
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
            f"in {kept}); its learning (Results, the post-mortem, edge at lock, the scorecards) is its own "
            "close's, and no model fit here saw it."
        )
    return lines


def tag_log(cfg: Config, log_path: Path, season: int) -> int:
    """Give every row of the contest log its slate (`slate.SLATE_COLUMN`, `SlateKey`): the configured
    secondary slate's key for its entries (`slate_chain.entry_slate`), else main. A row already tagged with a
    secondary slate keeps it (config.toml may drop `[slates.thu]` after the week; the record stays
    Thanksgiving's), a blank or main one takes what config says now. Returns how many rows changed. A no-op
    (the file untouched) with no `[slates.*]`."""
    from dfs import slate_chain
    from dfs.config import load_base_config
    from dfs.contests import history

    if not configured(cfg) or not Path(log_path).exists():
        return 0
    base = load_base_config()
    chain = slate_chain.links(base, lambda sid: _title(base, sid))
    weeks = slate_chain.slate_weeks(chain)
    log = history.read_log(log_path)
    if log.empty:
        return 0
    dates = pd.to_datetime(log["Contest_Date_EST"], errors="coerce")
    week_col = pd.to_numeric(log["Week"], errors="coerce")
    season_col = pd.to_numeric(log["Season"], errors="coerce")
    now = []
    for moment, game, wk, ssn in zip(dates, log["Game_Type"], week_col, season_col, strict=True):
        if pd.isna(moment) or pd.isna(wk) or pd.isna(ssn) or int(ssn) != season:
            now.append(slate.MAIN)
            continue
        now.append(slate_chain.entry_slate(moment.to_pydatetime(), str(game), season, weeks, int(wk)))
    before = log[slate.SLATE_COLUMN] if slate.SLATE_COLUMN in log.columns else pd.Series("", index=log.index)
    before = before.astype(str).str.strip()
    kept = before.where(before.ne("") & before.ne(slate.MAIN), pd.Series(now, index=log.index))
    changed = int((kept != before).sum())
    if changed:
        out = log.copy()
        out[slate.SLATE_COLUMN] = kept
        cols = [c for c in history.original_columns(out) if c != slate.SLATE_COLUMN]
        out = out[[*cols, slate.SLATE_COLUMN, *history.DERIVED_COLUMNS]]
        tmp = Path(log_path).with_suffix(".csv.tmp")
        out.to_csv(tmp, index=False)
        tmp.replace(log_path)
    return changed


def _title(cfg: Config, sheet_id: str) -> str | None:
    from dfs.sheets import SheetsClient

    try:
        title, _ = SheetsClient(cfg.google_sheets.model_copy(update={"sheet_id": sheet_id})).describe()
    except Exception:  # noqa: BLE001 - a sheet that cannot be read leaves its slate's week to config
        return None
    return title


def own_log(season: int):  # noqa: ANN201 - a DataFrame
    """The account's contest log (`data/contests/contest_log.csv`, `history.read_log`) narrowed to the active
    slate's entries (`slate_chain.log_for_slate`: by the log's `SlateKey` tag, else by config's slate weeks).
    The whole log, as before, with no `[slates.*]` and no tag."""
    from dfs import paths, slate_chain
    from dfs.config import load_base_config
    from dfs.contests import history

    log = history.read_log(paths.CONTESTS_DIR / "contest_log.csv")
    try:
        slates = load_base_config().slates or {}
    except Exception:  # noqa: BLE001 - no config: no secondary slate
        slates = {}
    weeks = {k: int(v.week) for k, v in slates.items() if v.week is not None}
    return slate_chain.log_for_slate(log, slate.current(), season, weeks)
