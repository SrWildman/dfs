"""dfs -- personal DFS data companion CLI.

Replaces run_all.py / run_update.py / upload.py. Those discarded their own
success/failure result (main()'s return value was never used to set the
process exit code, so `python3 run_all.py` always exited 0 even when every
scraper failed) -- every subcommand here returns a real exit code via
typer.Exit.
"""

from __future__ import annotations

import json as _json
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd
import typer
from rich.console import Console
from rich.table import Table

from dfs import paths, store
from dfs.bankroll import classify_entry, parse_contest_history, sync_bucket
from dfs.config import Config, ConfigError, load_config
from dfs.doctor import run_doctor
from dfs.late_swap import lineup_slot_status, swap_candidates
from dfs.line_movement import LineMovementError, diff_odds
from dfs.lineups import build_salary_lookup, export_csv, parse_entries, validate_entry
from dfs.live_diff import diff_edge_flags
from dfs.log import get_logger, setup_logging
from dfs.models import ROSTER_SLOTS
from dfs.sheet_links import PLAYER_POOL_RAW_BLOCK, PLAYER_POOL_RAW_TAB, link_edge_columns
from dfs.sheet_pool_deck import DECK_ROWS, add_pool_deck
from dfs.sheet_style import (
    POOL_RAW_ROWS,
    apply_tab_chrome,
    polish_bankroll,
    polish_builder_tab,
    polish_edge,
    polish_guardrails,
    polish_lineups_input_column,
    style_view_tabs,
)
from dfs.sheet_views import build_board, build_exposure, build_movement, build_slate_grid
from dfs.sheets import SheetsClient, SheetsError
from dfs.sources import SOURCES
from dfs.sources.base import SyncContext
from dfs.sync import run_sync
from dfs.week import (
    BANKROLL_CARRYOVER_CELLS,
    extract_results_value_columns,
    parse_sheet_id_from_url,
    rewrite_sheet_id,
)
from dfs.weekly_reset import LINEUPS_NAME_BLOCKS, PLAYER_POOL_NAME_BLOCKS, clear_previous_week

# `dfs sync --live` re-syncs only what actually moves within a game day:
# odds (line movement), DK's own Status (late inactives), and weather
# (forecast firming up as kickoff nears) -- then recomputes `edge` off
# them. `projections` (needs `dfs auth tffb`, and TFFB's numbers don't
# change hour to hour) and `nflverse_games` (stadium/roof/schedule --
# static for the week) are deliberately left out.
LIVE_SYNC_SOURCES = ["nfl_odds", "draftkings", "weather", "edge"]

app = typer.Typer(
    name="dfs",
    help="Sync DFS data into Google Sheets, export DK lineups, track results and bankroll.",
    no_args_is_help=True,
)
sheets_app = typer.Typer(help="Inspect and manage the connected Google Sheet.")
auth_app = typer.Typer(help="Log in to sites that require an authenticated session.")
bankroll_app = typer.Typer(help="Reconcile contest history into your bankroll tab.")
lineups_app = typer.Typer(help="Manage the sheet's lineup-building tabs.")
odds_app = typer.Typer(help="Inspect synced odds data.")
week_app = typer.Typer(help="Move config.toml between weekly sheet copies.")
app.add_typer(sheets_app, name="sheets")
app.add_typer(auth_app, name="auth")
app.add_typer(bankroll_app, name="bankroll")
app.add_typer(lineups_app, name="lineups")
app.add_typer(odds_app, name="odds")
app.add_typer(week_app, name="week")

console = Console()
log = get_logger("cli")


@app.callback()
def main(
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Show debug logging."),
    json_output: bool = typer.Option(
        False, "--json", help="Emit line-delimited JSON logs instead of console output."
    ),
) -> None:
    setup_logging(verbose=verbose, json_output=json_output)


@app.command()
def status() -> None:
    """Show whether config is set up and how fresh each data source is."""
    cfg = _load_config_or_exit()

    console.print(f"[green]OK[/green] config.toml loaded ({paths.CONFIG_FILE})")

    creds = paths.credentials_path(cfg.google_sheets.credentials_file)
    if creds.exists():
        console.print(f"[green]OK[/green] credentials file found ({creds.name})")
    else:
        console.print(f"[yellow]missing[/yellow] credentials file not found: {creds}")

    try:
        title, url = SheetsClient(cfg.google_sheets).describe()
        console.print(f"[green]OK[/green] connected sheet: [bold]{title}[/bold]\n         {url}")
    except SheetsError as e:
        console.print(f"[red]Sheets error:[/red] {e}")
        raise typer.Exit(code=1) from e

    if not paths.MANIFEST_FILE.exists():
        console.print("[yellow]no data yet[/yellow] -- run `dfs sync` to pull sources")
        return

    manifest = _json.loads(paths.MANIFEST_FILE.read_text())
    table = Table(title="Data sources")
    table.add_column("source")
    table.add_column("tab")
    table.add_column("last synced")
    table.add_column("rows")
    table.add_column("status")
    for source, tab in cfg.google_sheets.tab_mappings.items():
        entry = manifest.get(source)
        if entry is None:
            table.add_row(source, tab, "-", "-", "[yellow]never synced[/yellow]")
        elif entry.get("error"):
            table.add_row(
                source, tab, entry.get("synced_at", "-"), "-", f"[red]failed: {entry['error']}[/red]"
            )
        else:
            table.add_row(
                source, tab, entry.get("synced_at", "-"), str(entry.get("rows", "-")), "[green]ok[/green]"
            )
    console.print(table)


def _load_config_or_exit() -> Config:
    try:
        return load_config()
    except ConfigError as e:
        console.print(f"[red]Config error:[/red] {e}")
        raise typer.Exit(code=1) from e


@sheets_app.command("inspect")
def sheets_inspect() -> None:
    """List every tab in the connected sheet, with dimensions and header row."""
    cfg = _load_config_or_exit()
    client = SheetsClient(cfg.google_sheets)
    try:
        title, url = client.describe()
        console.print(f"Connected sheet: [bold]{title}[/bold]\n{url}\n")
        tabs = client.list_tabs()
    except SheetsError as e:
        console.print(f"[red]Sheets error:[/red] {e}")
        raise typer.Exit(code=1) from e

    known_tabs = set(cfg.google_sheets.tab_mappings.values())
    table = Table(title=f"Tabs in {title!r}")
    table.add_column("tab")
    table.add_column("size")
    table.add_column("mapped?")
    table.add_column("header row")
    for tab in tabs:
        mapped = "[green]yes[/green]" if tab.title in known_tabs else "[dim]no[/dim]"
        header = ", ".join(tab.header) if tab.header else "[dim](empty)[/dim]"
        table.add_row(tab.title, f"{tab.rows}x{tab.cols}", mapped, header)
    console.print(table)


@sheets_app.command("add-pool-deck")
def sheets_add_pool_deck(
    sheet_id: str = typer.Option(
        None,
        "--sheet-id",
        help="Add the pool deck to a different sheet instead of config.toml's -- e.g. the "
        "canonical weekly template, so new copies already have it.",
    ),
) -> None:
    """One-time structural change: insert DECK_ROWS frozen rows at the top
    of Lineups holding a sortable, filterable window into Player Pool --
    full metric columns (Salary, Pts, Ceil, Val, CeilVal, Leverage,
    Flag, ...), not just names, so a pick can be made without a second
    window open. Superseded a first, names-only "Bench" attempt (see
    `sheet_pool_deck.py`'s module docstring and CONTRIBUTING.md's
    changelog); migrates a sheet still in that state -- or in the pool
    deck's own original, taller size -- automatically.

    Uses a real Sheets row insert (not a tab rewrite), so every existing
    Lineups formula and conditional-format range shifts down with it.
    Also creates the hidden `PoolSort` helper tab the deck's window
    formulas read from. Safe to re-run: a deck already at the current
    size is left alone.
    """
    cfg = _load_config_or_exit()
    gs_cfg = cfg.google_sheets.model_copy(update={"sheet_id": sheet_id}) if sheet_id else cfg.google_sheets
    client = SheetsClient(gs_cfg)
    try:
        title, url = client.describe()
        console.print(f"Adding pool deck to {cfg.lineups.builder_tab!r} in: [bold]{title}[/bold]\n{url}\n")
        result = add_pool_deck(
            client, lineups_tab=cfg.lineups.builder_tab, pool_tab=cfg.lineups.player_pool_tab
        )
    except SheetsError as e:
        console.print(f"[red]Sheets error:[/red] {e}")
        raise typer.Exit(code=1) from e
    console.print(f"[green]OK[/green] {result}")


@sheets_app.command("polish")
def sheets_polish(
    sheet_id: str = typer.Option(
        None,
        "--sheet-id",
        help="Style a different sheet instead of config.toml's -- e.g. the canonical "
        "weekly template, so new copies are already styled.",
    ),
    skip_chrome: bool = typer.Option(
        False, "--skip-chrome", help="Leave the tab strip alone (no reorder, recolour or hiding)."
    ),
) -> None:
    """Mostly presentation: widths, freeze panes, number formats, header
    treatment, Flag/Avail chips, and the tab strip ordered by phase of the
    week -- none of which inserts, deletes, moves or renames a column, row
    or tab, so no VLOOKUP index, name block or config row range is
    affected. The one exception is Lineups' Guardrails column (O): those
    are real formula values, not styling, but they're additive-only
    (O sits strictly left of the EdgeRaw-linked block and was never
    written to before) and safe to re-run the same way -- see
    `sheet_style.polish_guardrails`.

    Safe to re-run: each tab's conditional formats are cleared before its
    own are applied (Guardrails clears only column O's rules, never the
    rest of Lineups' -- see `SheetsClient.clear_conditional_formats`).
    """
    cfg = _load_config_or_exit()
    gs_cfg = cfg.google_sheets.model_copy(update={"sheet_id": sheet_id}) if sheet_id else cfg.google_sheets
    edge_tab = cfg.google_sheets.tab_mappings.get("edge")
    if not edge_tab:
        console.print("[red]No tab mapped for 'edge' in config.toml.[/red]")
        raise typer.Exit(code=1)

    client = SheetsClient(gs_cfg)
    results: list[str] = []
    try:
        title, url = client.describe()
        console.print(f"Styling: [bold]{title}[/bold]\n{url}\n")

        results.append(polish_edge(client, edge_tab))
        results.append(polish_builder_tab(client, PLAYER_POOL_RAW_TAB, last_row=POOL_RAW_ROWS))
        pool_last = max(end for _, end in PLAYER_POOL_NAME_BLOCKS)
        results.append(polish_builder_tab(client, cfg.lineups.player_pool_tab, last_row=pool_last))
        lineups_last = max(end for _, end in LINEUPS_NAME_BLOCKS)
        lineups_header_row = LINEUPS_NAME_BLOCKS[0][0] - 1
        results.append(
            polish_builder_tab(
                client,
                cfg.lineups.builder_tab,
                last_row=lineups_last,
                header_row=lineups_header_row,
                freeze_rows=DECK_ROWS,
                freeze_cols=0,
                header_repeats_at=[start - 1 for start, _ in LINEUPS_NAME_BLOCKS[1:]],
            )
        )
        results.append(
            polish_guardrails(
                client,
                cfg.lineups.builder_tab,
                header_row=lineups_header_row,
                name_blocks=LINEUPS_NAME_BLOCKS,
            )
        )
        results.append(polish_lineups_input_column(client, cfg.lineups.builder_tab, LINEUPS_NAME_BLOCKS))
        if cfg.bankroll and cfg.bankroll.cash and cfg.bankroll.gpp:
            results.append(
                polish_bankroll(
                    client,
                    cfg.bankroll.tab,
                    cash=(
                        cfg.bankroll.cash.header_row,
                        cfg.bankroll.cash.first_row,
                        cfg.bankroll.cash.last_row,
                    ),
                    gpp=(
                        cfg.bankroll.gpp.header_row,
                        cfg.bankroll.gpp.first_row,
                        cfg.bankroll.gpp.last_row,
                    ),
                )
            )
        else:
            results.append("Bankroll: no [bankroll.cash]/[bankroll.gpp] in config -- skipped")

        # The four derived tabs, if `dfs sheets build-views` has made them.
        # Skipped cleanly when it hasn't.
        results.extend(style_view_tabs(client))

        if not skip_chrome:
            results.extend(apply_tab_chrome(client))
    except SheetsError as e:
        console.print(f"[red]Sheets error:[/red] {e}")
        raise typer.Exit(code=1) from e

    for line in results:
        console.print(f"[green]OK[/green] {line}")


@sheets_app.command("build-views")
def sheets_build_views(
    sheet_id: str = typer.Option(
        None,
        "--sheet-id",
        help="Build the view tabs in a different sheet instead of config.toml's.",
    ),
) -> None:
    """Create (or rebuild) the four derived, read-only tabs: Board, Slate
    Grid, Exposure and Movement.

    All four are formula-driven off tabs that already exist and are written
    to by nothing else, so creating them cannot affect any existing
    position. Safe to re-run -- Exposure's typed Target column is read back
    and restored rather than overwritten.
    """
    cfg = _load_config_or_exit()
    gs_cfg = cfg.google_sheets.model_copy(update={"sheet_id": sheet_id}) if sheet_id else cfg.google_sheets
    mappings = cfg.google_sheets.tab_mappings
    edge_tab = mappings.get("edge")
    games_tab = mappings.get("nflverse_games")
    weather_tab = mappings.get("weather")
    if not (edge_tab and games_tab and weather_tab):
        console.print("[red]config.toml needs 'edge', 'nflverse_games' and 'weather' tab mappings.[/red]")
        raise typer.Exit(code=1)

    client = SheetsClient(gs_cfg)
    try:
        title, url = client.describe()
        console.print(f"Building view tabs in: [bold]{title}[/bold]\n{url}\n")
        results = [
            build_board(client, edge_tab=edge_tab, games_tab=games_tab, weather_tab=weather_tab),
            build_slate_grid(client, games_tab=games_tab, weather_tab=weather_tab),
            build_exposure(
                client,
                edge_tab=edge_tab,
                lineups_tab=cfg.lineups.builder_tab,
                lineup_count=len(LINEUPS_NAME_BLOCKS),
                lineups_data_start_row=LINEUPS_NAME_BLOCKS[0][0] - 1,
            ),
            build_movement(client, edge_tab=edge_tab),
        ]
    except SheetsError as e:
        console.print(f"[red]Sheets error:[/red] {e}")
        raise typer.Exit(code=1) from e

    for line in results:
        console.print(f"[green]OK[/green] {line}")

    console.print(
        "\n[dim]Run `dfs sheets polish` afterwards -- it styles these four tabs "
        "and slots them into the tab strip.[/dim]"
    )


@sheets_app.command("link-edge")
def sheets_link_edge(
    sheet_id: str = typer.Option(
        None,
        "--sheet-id",
        help="Link a different sheet instead of config.toml's -- e.g. the canonical "
        "weekly template, so new copies already have EdgeRaw's columns linked in.",
    ),
) -> None:
    """One-time setup: append EdgeRaw's derived columns (Leverage, Flag,
    etc.) onto the far right of Player Pool, Lineups, AND PlayerPoolRaw
    (the hub tab those two already VLOOKUP against for Pos./Team/Pts/etc.),
    via the same VLOOKUP-by-Name join. Append-only: never inserts, so
    nothing already there shifts (see CONTRIBUTING.md's Phase 8 postmortem
    on why that matters). The new columns are grouped so they can be
    collapsed from the sheet UI (the little +/- control above the column
    letters) when you want the older, narrower view back. Safe to re-run --
    a tab that's already linked is left alone, not duplicated.
    """
    cfg = _load_config_or_exit()
    gs_cfg = cfg.google_sheets.model_copy(update={"sheet_id": sheet_id}) if sheet_id else cfg.google_sheets
    edge_tab = cfg.google_sheets.tab_mappings.get("edge")
    if not edge_tab:
        console.print("[red]No tab mapped for 'edge' in config.toml.[/red]")
        raise typer.Exit(code=1)

    client = SheetsClient(gs_cfg)
    try:
        title, url = client.describe()
        console.print(
            f"Linking EdgeRaw into Player Pool/Lineups/PlayerPoolRaw in: [bold]{title}[/bold]\n{url}\n"
        )

        header_repeats_at = [start - 1 for start, _ in LINEUPS_NAME_BLOCKS[1:]]
        lineups_header_row = LINEUPS_NAME_BLOCKS[0][0] - 1
        results = [
            link_edge_columns(client, PLAYER_POOL_RAW_TAB, PLAYER_POOL_RAW_BLOCK, edge_tab),
            link_edge_columns(client, cfg.lineups.player_pool_tab, PLAYER_POOL_NAME_BLOCKS, edge_tab),
            link_edge_columns(
                client,
                cfg.lineups.builder_tab,
                LINEUPS_NAME_BLOCKS,
                edge_tab,
                header_row=lineups_header_row,
                header_repeats_at=header_repeats_at,
            ),
        ]
    except SheetsError as e:
        console.print(f"[red]Sheets error:[/red] {e}")
        raise typer.Exit(code=1) from e

    for line in results:
        console.print(f"[green]OK[/green] {line}")


@sheets_app.command("doctor")
def sheets_doctor(
    sheet_id: str = typer.Option(
        None,
        "--sheet-id",
        help="Check a different sheet instead of config.toml's -- e.g. a freshly made "
        "weekly copy or the canonical template, before pointing anything at it.",
    ),
) -> None:
    """Read-only structural check: every tab named in config.toml exists,
    EdgeRaw's header matches derived.EDGE_COLUMNS, LINKED_EDGE_COLUMNS is
    linked exactly once (not zero, not twice) on Player Pool/Lineups/
    PlayerPoolRaw, Lineups' header repeats fall exactly where
    LINEUPS_NAME_BLOCKS expects, and Bankroll's configured header rows
    aren't blank. Never writes anything. Exits non-zero on any failure --
    this is the check that would have caught a stale template's missing
    tabs and drifted column positions before they broke `dfs export`/`dfs
    lineups clear`/`dfs sheets link-edge` on a fresh weekly copy, instead
    of surfacing three commands later as a crash or a silently wrong
    formula.
    """
    cfg = _load_config_or_exit()
    gs_cfg = cfg.google_sheets.model_copy(update={"sheet_id": sheet_id}) if sheet_id else cfg.google_sheets
    client = SheetsClient(gs_cfg)
    try:
        title, url = client.describe()
        console.print(f"Checking: [bold]{title}[/bold]\n{url}\n")
        issues = run_doctor(client, cfg)
    except SheetsError as e:
        console.print(f"[red]Sheets error:[/red] {e}")
        raise typer.Exit(code=1) from e

    if not issues:
        console.print("[green]OK[/green] all structural checks passed")
        return

    for issue in issues:
        console.print(f"[red]FAIL[/red] [{issue.check}] {issue.detail}")
    raise typer.Exit(code=1)


@app.command()
def sync(
    only: str = typer.Option(None, "--only", help="Comma-separated source names to sync (default: all)."),
    no_upload: bool = typer.Option(False, "--no-upload", help="Fetch and store locally, skip Sheets."),
    week: int = typer.Option(None, "--week", help="Override auto-detected NFL week."),
    season: int = typer.Option(None, "--season", help="Override auto-detected NFL season."),
    live: bool = typer.Option(
        False,
        "--live",
        help="Re-sync only fast-moving sources (odds, DK statuses, weather) plus edge, "
        "and print what changed in EdgeRaw's Flag column since the last sync. The "
        "Sunday-afternoon command -- not a substitute for a full `dfs sync`.",
    ),
) -> None:
    """Fetch data sources and upload them to the connected Google Sheet."""
    cfg = _load_config_or_exit()

    if live and only:
        console.print(
            "[red]--live and --only are mutually exclusive[/red] -- --live already picks its own sources."
        )
        raise typer.Exit(code=1)

    if live:
        source_names = LIVE_SYNC_SOURCES
    elif only:
        requested = [s.strip() for s in only.split(",") if s.strip()]
        unknown = [s for s in requested if s not in SOURCES]
        if unknown:
            console.print(f"[red]Unknown source(s):[/red] {', '.join(unknown)}")
            console.print(f"Known sources: {', '.join(sorted(SOURCES))}")
            raise typer.Exit(code=1)
        source_names = requested
    else:
        source_names = list(SOURCES)

    if no_upload:
        console.print("[dim]--no-upload: fetching and caching locally only, no Sheets contact.[/dim]")
    else:
        try:
            title, url = SheetsClient(cfg.google_sheets).describe()
        except SheetsError as e:
            console.print(f"[red]Sheets error:[/red] {e}")
            raise typer.Exit(code=1) from e
        console.print(f"Writing to sheet: [bold]{title}[/bold]\n{url}\n")

    old_edge: pd.DataFrame | None = None
    if live and "edge" in source_names:
        try:
            old_edge = store.load_current("edge")
        except FileNotFoundError:
            old_edge = None

    ctx = SyncContext.current(week=week, season=season)
    console.print(f"Syncing week {ctx.week}, season {ctx.season} ({len(source_names)} source(s))...")

    results = run_sync(cfg, source_names, ctx, upload=not no_upload)

    table = Table(title="Sync results")
    table.add_column("source")
    table.add_column("rows")
    table.add_column("status")
    any_failed = False
    for r in results:
        if r.ok:
            table.add_row(r.source, str(r.rows), "[green]ok[/green]")
        else:
            any_failed = True
            table.add_row(r.source, "-", f"[red]failed: {r.error}[/red]")
    console.print(table)

    if live:
        _print_live_flag_diff(old_edge)

    if any_failed:
        raise typer.Exit(code=1)


def _print_live_flag_diff(old_edge: pd.DataFrame | None) -> None:
    """Called only from `sync --live`, after `run_sync` -- compares
    EdgeRaw's Flag column from right before this sync (`old_edge`, read
    before `run_sync` ran) to right after, and prints the difference.
    `store.load_previous`/`diff_odds`'s per-snapshot pattern isn't reused
    here because "current" already means "the state this sync just
    replaced" for `old_edge`, captured before the write happens -- no need
    to reach back into raw snapshot history for it.
    """
    if old_edge is None:
        console.print(
            "\n[dim]No previous EdgeRaw snapshot to diff against -- this is the first live sync.[/dim]"
        )
        return

    try:
        new_edge = store.load_current("edge")
    except FileNotFoundError:
        return

    changes = diff_edge_flags(old_edge, new_edge)
    console.print()
    if changes.empty:
        console.print("[dim]No Flag changes since the last sync.[/dim]")
        return

    table = Table(title="What changed since the last sync")
    table.add_column("Name")
    table.add_column("Pos")
    table.add_column("Team")
    table.add_column("Old Flag")
    table.add_column("New Flag")
    for _, row in changes.iterrows():
        table.add_row(row["Name"], row["Position"], row["Team"], row["OldFlag"] or "-", row["NewFlag"] or "-")
    console.print(table)


@app.command()
def edge(
    top: int = typer.Option(20, "--top", "-n", help="Number of leverage plays to show."),
    position: str = typer.Option(None, "--position", "-p", help="Filter to one position (e.g. RB)."),
) -> None:
    """Print the top leverage plays from the last `dfs sync` locally --
    no Sheets round-trip, quick look without opening the sheet."""
    try:
        df = store.load_current("edge")
    except FileNotFoundError as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(code=1) from e

    if position:
        df = df[df["Position"].str.upper() == position.upper()]
        if df.empty:
            console.print(f"[yellow]No players at position {position!r}.[/yellow]")
            raise typer.Exit(code=1)

    basis = df["LevBasis"].iloc[0] if len(df) else "?"
    console.print(f"Leverage basis: [bold]{basis}[/bold] (real ProjOwn until TFFB computes it midweek)\n")

    table = Table(title="Top leverage plays")
    columns = (
        "Name",
        "Position",
        "Team",
        "Opp",
        "Salary",
        "ProjPts",
        "ProjOwn",
        "Leverage",
        "GameEnv",
        "Flag",
    )
    for col in columns:
        table.add_column(col)
    for _, r in df.head(top).iterrows():
        table.add_row(
            r["Name"],
            r["Position"],
            r["Team"],
            str(r["Opp"]),
            str(r["Salary"]),
            f"{r['ProjPts']:.1f}",
            f"{r['ProjOwn']:.1f}",
            f"{r['Leverage']:.1f}" if pd.notna(r["Leverage"]) else "-",
            f"{r['GameEnv']:.1f}" if pd.notna(r["GameEnv"]) else "-",
            r["Flag"] or "",
        )
    console.print(table)


@odds_app.command("movement")
def odds_movement(
    top: int = typer.Option(10, "--top", "-n", help="Number of biggest moves to show."),
) -> None:
    """Diff the two most recent nfl_odds syncs and show which teams'
    lines moved the most -- what to check before re-running a full sync
    on Sunday. Needs at least two `dfs sync`/`dfs sync --only nfl_odds`
    runs this week; the first one has nothing to diff against yet."""
    try:
        previous = store.load_previous("nfl_odds")
        current = store.load_current("nfl_odds")
    except FileNotFoundError as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(code=1) from e

    try:
        df = diff_odds(previous, current)
    except LineMovementError as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(code=1) from e

    table = Table(title="Line movement since the last sync")
    for col in ("Team", "Spread", "ΔSpread", "Total", "ΔTotal", "Team Implied", "ΔImplied", "Flag"):
        table.add_column(col)
    for _, r in df.head(top).iterrows():
        table.add_row(
            r["Team"],
            f"{r['SpreadCur']:+.1f}" if pd.notna(r["SpreadCur"]) else "-",
            f"{r['SpreadDelta']:+.1f}" if pd.notna(r["SpreadDelta"]) else "-",
            f"{r['TotalCur']:.1f}" if pd.notna(r["TotalCur"]) else "-",
            f"{r['TotalDelta']:+.1f}" if pd.notna(r["TotalDelta"]) else "-",
            f"{r['TeamPointsCur']:.1f}" if pd.notna(r["TeamPointsCur"]) else "-",
            f"{r['TeamPointsDelta']:+.1f}" if pd.notna(r["TeamPointsDelta"]) else "-",
            r["Flag"] or "",
        )
    console.print(table)


@app.command()
def export(
    output: Path = typer.Option(..., "--output", "-o", help="Path to write the DK bulk-upload CSV."),
) -> None:
    """Validate paired lineups in the lineups tab and export DK's upload CSV."""
    cfg = _load_config_or_exit()

    try:
        salary_df = store.load_current("draftkings")
    except FileNotFoundError as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(code=1) from e

    client = SheetsClient(cfg.google_sheets)
    try:
        rows = client.read_tab(cfg.lineups.upload_tab)
    except SheetsError as e:
        console.print(f"[red]Sheets error:[/red] {e}")
        raise typer.Exit(code=1) from e

    entries = parse_entries(rows)
    if not entries:
        console.print(f"[yellow]No entries found in {cfg.lineups.upload_tab!r} (all rows blank).[/yellow]")
        raise typer.Exit(code=1)

    lookup = build_salary_lookup(salary_df)
    results = [validate_entry(e, lookup, salary_cap=cfg.lineups.salary_cap) for e in entries]

    table = Table(title=f"Lineups from {cfg.lineups.upload_tab!r}")
    table.add_column("row")
    table.add_column("entry id")
    table.add_column("contest")
    table.add_column("salary")
    table.add_column("status")
    for r in results:
        if r.ok:
            table.add_row(
                str(r.entry.row_number),
                r.entry.entry_id,
                r.entry.contest_name,
                str(r.salary_total),
                "[green]ok[/green]",
            )
        else:
            table.add_row(
                str(r.entry.row_number),
                r.entry.entry_id,
                r.entry.contest_name,
                "-",
                "[red]" + "; ".join(r.errors) + "[/red]",
            )
    console.print(table)

    valid = [r.entry for r in results if r.ok]
    if valid:
        n = export_csv(valid, output)
        console.print(f"[green]Wrote {n} lineup(s) to {output}[/green]")

    invalid_count = len(results) - len(valid)
    if invalid_count:
        entry_word = "entry" if invalid_count == 1 else "entries"
        console.print(f"[red]{invalid_count} {entry_word} skipped due to validation errors above.[/red]")
        raise typer.Exit(code=1)


@lineups_app.command("clear")
def lineups_clear(
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation prompt."),
    sheet_id: str = typer.Option(
        None,
        "--sheet-id",
        help="Clear a different sheet instead of config.toml's -- e.g. a fresh weekly "
        "copy or the canonical template, before pointing config.toml at it.",
    ),
) -> None:
    """Clear last week's typed-in lineup data (Lineups/Player Pool name
    columns, Scratch, DK Upload) so the sheet's ready for a new week.

    Formulas and formatting (including conditional formatting) are left
    untouched -- only the typed values a human enters while building
    lineups get cleared. Run this once per new weekly sheet copy, before
    rebuilding lineups for that week.
    """
    cfg = _load_config_or_exit()
    gs_cfg = cfg.google_sheets.model_copy(update={"sheet_id": sheet_id}) if sheet_id else cfg.google_sheets
    client = SheetsClient(gs_cfg)
    try:
        title, url = client.describe()
    except SheetsError as e:
        console.print(f"[red]Sheets error:[/red] {e}")
        raise typer.Exit(code=1) from e

    console.print(f"About to clear last week's lineup data from: [bold]{title}[/bold]\n{url}\n")
    console.print(
        f"  - {cfg.lineups.builder_tab}: Name column\n"
        f"  - {cfg.lineups.player_pool_tab}: Name column\n"
        f"  - {cfg.lineups.scratch_tab}: all data\n"
        f"  - {cfg.lineups.upload_tab}: all data\n"
    )
    if not yes and not typer.confirm("Proceed?"):
        console.print("Cancelled.")
        raise typer.Exit(code=0)

    try:
        summary = clear_previous_week(
            client,
            lineups_tab=cfg.lineups.builder_tab,
            player_pool_tab=cfg.lineups.player_pool_tab,
            scratch_tab=cfg.lineups.scratch_tab,
            dk_upload_tab=cfg.lineups.upload_tab,
        )
    except SheetsError as e:
        console.print(f"[red]Sheets error:[/red] {e}")
        raise typer.Exit(code=1) from e

    for line in summary:
        console.print(f"[green]OK[/green] {line}")


def _sheet_cell(rows: list[list[str]], index: int) -> str:
    """`rows` is a `read_range` result (0-indexed from the range's first
    row); a blank cell can come back as a missing trailing row OR an empty
    inner list depending on where it falls in the range, so both need
    guarding, not just an IndexError on `rows[index]`."""
    if index < 0 or index >= len(rows):
        return ""
    row = rows[index]
    return row[0] if row else ""


@lineups_app.command("late-swap")
def lineups_late_swap(
    top: int = typer.Option(3, "--top", "-n", help="Number of swap candidates to show per open slot."),
) -> None:
    """Check every built lineup in the Lineups tab against real kickoff
    times: which of your rostered players have already locked and which
    haven't, plus who's still available at each open slot right now.

    Reads EdgeRaw locally (`store.load_current`) and the Lineups tab's
    typed Name column -- no Sheets write, so nothing here needs
    confirmation. Run `dfs sync --live` first for the freshest picture;
    this command itself doesn't re-sync anything.
    """
    cfg = _load_config_or_exit()

    try:
        edge = store.load_current("edge")
    except FileNotFoundError as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(code=1) from e

    client = SheetsClient(cfg.google_sheets)
    try:
        title, url = client.describe()
        last_row = LINEUPS_NAME_BLOCKS[-1][1]
        raw = client.read_range(cfg.lineups.builder_tab, f"A2:A{last_row}")
    except SheetsError as e:
        console.print(f"[red]Sheets error:[/red] {e}")
        raise typer.Exit(code=1) from e

    console.print(f"Checking lineups in: [bold]{title}[/bold]\n{url}\n")

    now = datetime.now(UTC)
    any_shown = False

    for lineup_number, (start, _end) in enumerate(LINEUPS_NAME_BLOCKS, start=1):
        # Each block's first 9 rows are the roster (ROSTER_SLOTS order);
        # its final row is a salary total, not a player -- see
        # weekly_reset.py's LINEUPS_NAME_BLOCKS docstring.
        offset = start - 2  # `raw` starts at row 2
        names = [_sheet_cell(raw, offset + i) for i in range(len(ROSTER_SLOTS))]
        if not any(n.strip() for n in names):
            continue  # lineup not built yet

        statuses = lineup_slot_status(names, edge, now=now)
        if all(s.locked is True for s in statuses):
            continue  # every slot found and locked -- nothing left to decide

        any_shown = True
        table = Table(title=f"Lineup {lineup_number}")
        for col in ("Slot", "Name", "Status", "ProjPts", "Leverage", "Flag"):
            table.add_column(col)
        for s in statuses:
            if not s.found:
                status = "[yellow]?[/yellow]"
            elif s.locked:
                status = "[dim]LOCKED[/dim]"
            else:
                status = "[green]OPEN[/green]"
            table.add_row(
                s.slot,
                s.name or "[dim](empty)[/dim]",
                status,
                f"{s.proj_pts:.1f}" if pd.notna(s.proj_pts) else "-",
                f"{s.leverage:.1f}" if pd.notna(s.leverage) else "-",
                s.flag or "",
            )
        console.print(table)

        rostered = {s.name for s in statuses if s.name}
        for s in statuses:
            if s.found and s.locked is not False:
                continue  # only suggest swaps for a confirmed-open or empty slot
            candidates = swap_candidates(edge, s.slot, rostered, now=now, top=top)
            if candidates.empty:
                continue
            console.print(f"  Swap candidates for {s.slot} ({s.name or 'empty'}):")
            for _, c in candidates.iterrows():
                console.print(f"    {c['Name']} ({c['Team']}) -- Leverage {c['Leverage']:.1f}")
        console.print()

    if not any_shown:
        console.print("[dim]No lineup currently has an open (not-yet-locked) slot to check.[/dim]")


@week_app.command("new")
def week_new(
    sheet_url: str = typer.Argument(
        ..., help="URL (or bare ID) of this week's sheet, already copied from the template."
    ),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation prompt."),
) -> None:
    """Move config.toml to a new week's sheet copy: check the new sheet's
    structure (`dfs sheets doctor`), carry the bankroll and Results log
    forward, clear last week's lineups, and run a full sync -- in that
    order, with one confirmation before anything is written.

    Carrying the bankroll forward means reading the CURRENT sheet's Ending
    balance for each of the three tracked bankrolls (main/DK, PP, UD) and
    writing it as the NEW sheet's Starting balance -- see
    dfs.week.BANKROLL_CARRYOVER_CELLS and docs/ROADMAP.md's Phase 4 section
    for how those cell addresses were found. Everything else on the
    Bankroll tab (weekly budget formulas, Deposited/Withdrawn) is either
    formula-driven and naturally resets, or a running total the user
    updates by hand -- neither needs code here.

    Carrying Results forward means copying every already-typed week's row
    (config.toml's `[results]` table -- Week, Cash Pts/Line, H2H Entered/
    Win, Red/Blue/Black) from the current sheet to the new one, since
    Results is a season-level log, not a per-week one -- a fresh weekly
    copy's own Results tab starts with the template's empty pre-built
    rows, and would otherwise lose the whole season's history on every
    `dfs week new`. The two formula columns already built into each row
    (Cash Results, H2H %) are never touched -- see
    dfs.week.extract_results_value_columns. If the current sheet has no
    Results tab at all (e.g. moving off a sheet that predates this), this
    step is skipped with a note instead of failing.
    """
    cfg = _load_config_or_exit()

    try:
        new_sheet_id = parse_sheet_id_from_url(sheet_url)
    except ValueError as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(code=1) from e

    old_sheet_id = cfg.google_sheets.sheet_id
    if new_sheet_id == old_sheet_id:
        console.print("[yellow]That's already the sheet config.toml points at -- nothing to do.[/yellow]")
        raise typer.Exit(code=1)

    old_client = SheetsClient(cfg.google_sheets)
    new_gs_cfg = cfg.google_sheets.model_copy(update={"sheet_id": new_sheet_id})
    new_client = SheetsClient(new_gs_cfg)

    try:
        old_title, old_url = old_client.describe()
    except SheetsError as e:
        console.print(f"[red]Could not open the current sheet ({old_sheet_id}):[/red] {e}")
        raise typer.Exit(code=1) from e

    try:
        new_title, new_url = new_client.describe()
    except SheetsError as e:
        console.print(f"[red]Could not open the new sheet ({new_sheet_id}):[/red] {e}")
        console.print("Make sure it's shared with the service account and try again.")
        raise typer.Exit(code=1) from e

    console.print(f"Current sheet: [bold]{old_title}[/bold]\n{old_url}\n")
    console.print(f"New sheet:     [bold]{new_title}[/bold]\n{new_url}\n")

    console.print("Checking the new sheet's structure before touching anything...")
    try:
        issues = run_doctor(new_client, cfg)
    except SheetsError as e:
        console.print(f"[red]Sheets error checking the new sheet:[/red] {e}")
        raise typer.Exit(code=1) from e
    if issues:
        for issue in issues:
            console.print(f"[red]FAIL[/red] [{issue.check}] {issue.detail}")
        console.print(
            "\n[red]The new sheet failed structural checks -- stopping before any write.[/red]\n"
            "Fix the sheet (or its config.toml mapping) and re-run, or run "
            f"`dfs sheets doctor --sheet-id {new_sheet_id}` for the same report on its own."
        )
        raise typer.Exit(code=1)
    console.print("[green]OK[/green] new sheet passed structural checks\n")

    bankroll_tab = cfg.bankroll.tab
    carryover: list[tuple[str, str]] = []
    try:
        for old_cell, new_cell in BANKROLL_CARRYOVER_CELLS:
            value = old_client.read_range(bankroll_tab, old_cell)
            resolved = value[0][0] if value and value[0] else ""
            carryover.append((new_cell, resolved))
    except SheetsError as e:
        console.print(f"[red]Could not read {bankroll_tab!r} on the current sheet:[/red] {e}")
        raise typer.Exit(code=1) from e

    console.print("Bankroll to carry forward:")
    for (old_cell, _), (new_cell, value) in zip(BANKROLL_CARRYOVER_CELLS, carryover, strict=True):
        console.print(f"  {bankroll_tab}!{old_cell} -> {bankroll_tab}!{new_cell} = {value!r}")

    results_cfg = cfg.results
    results_rows: list[list[str]] = []
    try:
        results_rows = old_client.read_range(
            results_cfg.tab, f"A{results_cfg.first_row}:J{results_cfg.last_row}"
        )
    except SheetsError:
        console.print(
            f"\n[yellow]No {results_cfg.tab!r} tab on the current sheet -- nothing to carry forward.[/yellow]"
        )
    weeks_found = [r[0] for r in results_rows if r and r[0].strip()]
    if weeks_found:
        console.print(f"\n{results_cfg.tab!r} weeks to carry forward: {', '.join(weeks_found)}")

    if not yes and not typer.confirm(
        "\nRewrite config.toml, carry the bankroll forward, clear last week's lineups, "
        "and run a full sync against the NEW sheet?"
    ):
        console.print("Cancelled -- nothing changed.")
        raise typer.Exit(code=0)

    try:
        updated_text = rewrite_sheet_id(
            paths.CONFIG_FILE.read_text(), new_sheet_id=new_sheet_id, previous_sheet_id=old_sheet_id
        )
    except ValueError as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(code=1) from e
    paths.CONFIG_FILE.write_text(updated_text)
    console.print(f"[green]OK[/green] config.toml now points at {new_sheet_id} (previous: {old_sheet_id})")

    try:
        for new_cell, value in carryover:
            new_client.update_range(bankroll_tab, new_cell, [[value]])
    except SheetsError as e:
        console.print(f"[red]Could not write {bankroll_tab!r} on the new sheet:[/red] {e}")
        raise typer.Exit(code=1) from e
    console.print(f"[green]OK[/green] carried bankroll forward into {bankroll_tab!r}")

    if results_rows:
        try:
            for col_range, values in extract_results_value_columns(results_rows).items():
                start, _, end = col_range.partition(":")
                end = end or start
                new_client.update_range(
                    results_cfg.tab, f"{start}{results_cfg.first_row}:{end}{results_cfg.last_row}", values
                )
        except SheetsError as e:
            console.print(f"[red]Could not write {results_cfg.tab!r} on the new sheet:[/red] {e}")
            raise typer.Exit(code=1) from e
        console.print(f"[green]OK[/green] carried {len(weeks_found)} week(s) of {results_cfg.tab!r} forward")

    try:
        summary = clear_previous_week(
            new_client,
            lineups_tab=cfg.lineups.builder_tab,
            player_pool_tab=cfg.lineups.player_pool_tab,
            scratch_tab=cfg.lineups.scratch_tab,
            dk_upload_tab=cfg.lineups.upload_tab,
        )
    except SheetsError as e:
        console.print(f"[red]Sheets error:[/red] {e}")
        raise typer.Exit(code=1) from e
    for line in summary:
        console.print(f"[green]OK[/green] {line}")

    load_config.cache_clear()
    new_cfg = _load_config_or_exit()
    ctx = SyncContext.current()
    console.print(f"\nSyncing week {ctx.week}, season {ctx.season} ({len(SOURCES)} source(s))...")
    results = run_sync(new_cfg, list(SOURCES), ctx, upload=True)

    table = Table(title="Sync results")
    table.add_column("source")
    table.add_column("rows")
    table.add_column("status")
    any_failed = False
    for r in results:
        if r.ok:
            table.add_row(r.source, str(r.rows), "[green]ok[/green]")
        else:
            any_failed = True
            table.add_row(r.source, "-", f"[red]failed: {r.error}[/red]")
    console.print(table)

    if any_failed:
        raise typer.Exit(code=1)


@week_app.command("close")
def week_close(
    csv: Path = typer.Option(
        ..., "--csv", help="Path to a DK contest-history CSV export (My Contests > export)."
    ),
) -> None:
    """End-of-week bankroll reconciliation -- currently a thin wrapper over
    `dfs bankroll sync --csv`; see below for why it isn't more than that yet.

    Investigated for this command: whether the authenticated browser
    profile `dfs auth dk` already saves could pull contest history
    directly, skipping the manual CSV export. It can't, today -- not
    because it was tried and failed, but because doing so means probing
    DraftKings' undocumented authenticated endpoints (the "My Contests"
    page has no public API; whatever it calls internally isn't stable
    enough to build against sight-unseen), and that's a live exploratory
    scrape against your real logged-in session, not something to attempt
    unattended in a coding session. If that gets revisited, it needs doing
    with you present, watching real requests. Until then, exporting
    contest history by hand (My Contests > export) and passing it here
    stays the supported path -- this command exists mainly so "close the
    week" has one name in the weekly workflow, not two.
    """
    cfg = _load_config_or_exit()
    console.print("[bold]Closing the week[/bold] -- reconciling bankroll from DK contest history.\n")
    _sync_bankroll_from_csv(cfg, csv)


@bankroll_app.command("sync")
def bankroll_sync(
    csv: Path = typer.Option(
        ..., "--csv", help="Path to a DK contest-history CSV export (My Contests > export)."
    ),
) -> None:
    """Classify contest entries into Cash/GPP and append new ones to the bankroll tab.

    Live DK auth (`dfs auth dk`) will eventually feed this automatically;
    for now, export your contest history from DraftKings' website and
    point this at the file.
    """
    cfg = _load_config_or_exit()
    _sync_bankroll_from_csv(cfg, csv)


def _sync_bankroll_from_csv(cfg: Config, csv: Path) -> None:
    """Shared by `bankroll sync` and `week close` -- see week_close's
    docstring for why `week close` doesn't yet pull contest history itself
    and still needs this same `--csv` export as an input."""
    if cfg.bankroll.cash is None or cfg.bankroll.gpp is None:
        console.print(
            "[red]config.toml is missing [bankroll.cash]/[bankroll.gpp][/red] "
            "-- see config.example.toml for the shape."
        )
        raise typer.Exit(code=1)

    if not csv.exists():
        console.print(f"[red]No such file:[/red] {csv}")
        raise typer.Exit(code=1)

    df = pd.read_csv(csv)
    try:
        entries = parse_contest_history(df)
    except KeyError as e:
        console.print(f"[red]CSV is missing an expected column:[/red] {e}")
        raise typer.Exit(code=1) from e

    cash_entries = [e for e in entries if classify_entry(e) == "cash"]
    gpp_entries = [e for e in entries if classify_entry(e) == "gpp"]
    console.print(f"Parsed {len(entries)} entries: {len(cash_entries)} cash, {len(gpp_entries)} GPP.")

    client = SheetsClient(cfg.google_sheets)
    try:
        cash_result = sync_bucket(client, cfg.bankroll.tab, cfg.bankroll.cash, cash_entries, "cash")
        gpp_result = sync_bucket(client, cfg.bankroll.tab, cfg.bankroll.gpp, gpp_entries, "gpp")
    except SheetsError as e:
        console.print(f"[red]Sheets error:[/red] {e}")
        raise typer.Exit(code=1) from e

    any_skipped = False
    for result in (cash_result, gpp_result):
        console.print(
            f"\n[bold]{result.bucket}[/bold]: wrote {len(result.written_entries)}, "
            f"already synced {result.already_synced}, skipped (table full) {result.skipped_full}"
        )
        for e in result.written_entries:
            console.print(f"  + {e.entry} -- place {e.place}, {e.points} pts, net {e.net}")
        any_skipped = any_skipped or result.skipped_full > 0

    if any_skipped:
        console.print(
            "[yellow]Some entries were skipped -- their table ran out of configured rows. "
            "Extend last_row in config.toml (and add matching formula rows in the sheet) "
            "to fit more.[/yellow]"
        )
        raise typer.Exit(code=1)


@auth_app.command("tffb")
def auth_tffb() -> None:
    """One-time interactive login to The Fantasy Footballers (DFS Pass)."""
    from dfs.browser import interactive_login

    interactive_login(
        "tffb",
        "https://www.thefantasyfootballers.com/2025-ultimate-dfs-pass/dfs-pass-lineup-optimizer/",
        success_check="the DFS Lineup Optimizer, not a 'Get the DFS Pass to unlock' paywall",
    )


@auth_app.command("dk")
def auth_dk() -> None:
    """One-time interactive login to DraftKings."""
    from dfs.browser import interactive_login

    interactive_login(
        "dk",
        "https://www.draftkings.com/",
        success_check="your DraftKings lobby/account menu, not a login prompt",
    )


if __name__ == "__main__":
    app()
