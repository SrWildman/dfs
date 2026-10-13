"""What `dfs week close` (and `dfs contests update`) does with the contest side of the week, step by step.

Every step is soft: a failure in one is reported and the next still runs, because the close itself (the
bankroll reconciliation) has already succeeded by the time this runs. Nothing here contacts DraftKings; the
two files are ones Sam downloaded by hand.

1. keep the history export (a copy in `data/raw/dk_history/`) and merge it into the durable contest log;
2. move any new standings downloads into `data/raw/dk_standings/` (and say so for an empty one);
3. run the engine over the log and every kept standings file (`analysis.run` + `analysis.write`): series and
   formats, score profiles, how the fields build (each week's players from the DraftKings salary snapshot
   taken before that week's lock), toughness, head to head;
4. log each standings file's actual ownership (`data/ownership_log.csv`, one set of rows per contest);
5. fill Results' blank `Cash Line` from the standings of the configured double-up (live sheet only);
6. rebuild the `Contests` tab.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from dfs import contests_cash_line as cash_line
from dfs import nfl_calendar, ownership
from dfs.config import Config
from dfs.contests import analysis, capture, slate_index, standings, week_frames
from dfs.paths import CONTESTS_DIR, DK_HISTORY_DIR, DK_STANDINGS_DIR, OWNERSHIP_LOG_FILE, RAW_DIR
from dfs.sheets import SheetsClient


@dataclass
class UpdateReport:
    lines: list[str] = field(default_factory=list)
    analysis: analysis.Analysis | None = None
    history_used: Path | None = None

    def say(self, text: str) -> None:
        self.lines.append(text)


def open_sheet(cfg: Config, sheet_id: str | None = None) -> tuple[SheetsClient, str, str]:
    """`(client, title, url)` for config.toml's sheet, or for `sheet_id` (a template run)."""
    gs = cfg.google_sheets.model_copy(update={"sheet_id": sheet_id}) if sheet_id else cfg.google_sheets
    client = SheetsClient(gs)
    title, url = client.describe()
    return client, title, url


def resolve_history(csv: Path | None, downloads: Path) -> tuple[Path | None, str]:
    """`(path, how chosen)`: the given file, else the newest history export in `downloads` (by header)."""
    if csv is not None:
        return Path(csv), "given with --csv"
    found = capture.find_history_export(downloads)
    if found is None:
        return None, f"no DraftKings contest-history export found in {downloads}"
    return found, f"newest history export in {downloads} (found by its header row)"


def log_ownership(result: analysis.Analysis, standings_dir: Path | None = None) -> tuple[int, int]:
    """Logs the actual ownership of every kept standings file whose contest is not yet in the ownership log
    (or is there under another week). Returns `(contests logged, contests already right)`."""
    standings_dir = Path(standings_dir) if standings_dir is not None else DK_STANDINGS_DIR
    if result.profiles.empty or not standings_dir.is_dir():
        return 0, 0
    weeks = {
        str(r.contest_id): (int(r.season), int(r.week))
        for r in result.profiles.itertuples(index=False)
        if pd.notna(r.season) and pd.notna(r.week)
    }
    have: dict[str, tuple[int, int]] = {}
    if OWNERSHIP_LOG_FILE.exists():
        existing = pd.read_csv(OWNERSHIP_LOG_FILE, dtype={"contest_id": str})
        have = {
            cid: (int(sub["season"].iloc[0]), int(sub["week"].iloc[0]))
            for cid, sub in existing.groupby("contest_id")
        }
    logged = right = 0
    for source in standings.scan_standings(standings_dir).sources:
        when = weeks.get(source.contest_id)
        if when is None:
            continue
        if have.get(source.contest_id) == when:
            right += 1
            continue
        try:
            parsed = standings.parse_standings(source.raw, contest_id=source.contest_id)
        except standings.StandingsError:
            continue
        if parsed.ownership.empty:
            continue
        ownership.append_ownership(
            parsed.ownership, season=when[0], week=when[1], contest_id=source.contest_id
        )
        logged += 1
    return logged, right


def run_contests(
    cfg: Config,
    csv: Path | None,
    *,
    downloads: Path | None = None,
    client: SheetsClient | None = None,
    fill_cash_line: bool = True,
    season: int | None = None,
    progress: Callable[[str], None] | None = None,
    fetch_season: Callable[[int], pd.DataFrame] | None = slate_index.compute_season,
) -> UpdateReport:
    """The whole contest step. `client` None = files only (no sheet is touched); `fill_cash_line=False` keeps
    Results alone (a template run)."""
    report = UpdateReport()
    say = report.say if progress is None else (lambda text: (report.say(text), progress(text)))
    downloads = Path(downloads) if downloads else Path(cfg.contests.downloads_dir).expanduser()
    season = season if season is not None else nfl_calendar.current_season()

    history_csv, how = resolve_history(csv, downloads)
    if history_csv is None:
        say(f"No contest history to read: {how}.")
        say("  (Export it from DraftKings: My Contests > History > export; then run this again.)")
    else:
        report.history_used = history_csv
        say(f"Contest history: {history_csv.name} -- {how}.")
        try:
            kept, new = capture.keep_history_copy(history_csv, DK_HISTORY_DIR)
            say(f"  {'kept a copy' if new else 'a copy is already kept'}: {kept}")
        except OSError as e:
            say(f"  could not keep a copy ({e}); carrying on")

    say("Standings files:")
    try:
        got = capture.capture_standings(downloads, DK_STANDINGS_DIR)
        for line in capture.describe(got):
            say(line)
    except OSError as e:
        say(f"  could not read {downloads} ({e})")

    if history_csv is None:
        return report
    try:
        result = analysis.run(
            [history_csv],
            CONTESTS_DIR,
            standings_dir=DK_STANDINGS_DIR if DK_STANDINGS_DIR.is_dir() else None,
            frames=lambda s, w: week_frames.week_frame(RAW_DIR / "draftkings", s, w),
            me=_me(),
            fetch_season=fetch_season,
        )
        analysis.write(result)
    except Exception as e:  # noqa: BLE001 - the close already succeeded; report and carry on
        say(f"Contest analysis failed ({e}); the files above are kept. Run `dfs contests update` later.")
        return report
    report.analysis = result
    for merged in result.merges:
        m = merged[1]
        say(f"Contest log: {m.added} new, {m.updated} updated entries -> {m.total} in data/contests/.")
    say(f"Profiles: {len(result.profiles)} standings file(s); {len(result.lineup_errors)} blank lineups.")
    for line in analysis.summary_lines(result):
        if line.startswith(("warning:", "players for", "  ")) or "unmatched" in line:
            say(line)
    try:
        logged, right = log_ownership(result)
        say(f"Ownership log: {logged} contest(s) newly logged, {right} already there.")
    except Exception as e:  # noqa: BLE001
        say(f"Ownership log failed ({e}).")

    if client is None:
        return report
    if fill_cash_line:
        try:
            lines = cash_line.standings_cash_lines(result.profiles, season, cfg.contests.cash_line_series)
            filled = cash_line.fill_cash_lines(client, cfg.results, lines)
            say(f"Results Cash Line (from {cfg.contests.cash_line_series}):")
            for line in filled.lines() or ["  nothing to fill"]:
                say(line)
        except Exception as e:  # noqa: BLE001
            say(f"Cash Line not filled ({e}).")
    try:
        from dfs import sheet_contests

        tables = sheet_contests.load_tables(CONTESTS_DIR)
        layout = sheet_contests.build_layout(tables, season)
        say(sheet_contests.write_contests(client, layout, cfg.contests.tab))
    except Exception as e:  # noqa: BLE001
        say(f"Contests tab not written ({e}).")
    return report


def _me() -> str:
    from dfs.contests import series

    return series.MY_USERNAME
