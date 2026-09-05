"""dfs -- personal DFS data companion CLI.

Replaces run_all.py / run_update.py / upload.py. Those discarded their own
success/failure result (main()'s return value was never used to set the
process exit code, so `python3 run_all.py` always exited 0 even when every
scraper failed) -- every subcommand here returns a real exit code via
typer.Exit.
"""

from __future__ import annotations

import json as _json
from pathlib import Path

import pandas as pd
import typer
from rich.console import Console
from rich.table import Table

from dfs import paths, store
from dfs.bankroll import classify_entry, parse_contest_history, sync_bucket
from dfs.config import Config, ConfigError, load_config
from dfs.derived import EDGE_COLUMNS
from dfs.line_movement import LineMovementError, diff_odds
from dfs.lineups import build_salary_lookup, export_csv, parse_entries, validate_entry
from dfs.log import get_logger, setup_logging
from dfs.sheet_links import PLAYER_POOL_RAW_BLOCK, PLAYER_POOL_RAW_TAB, link_edge_columns
from dfs.sheets import SheetsClient, SheetsError, column_letter
from dfs.sources import SOURCES
from dfs.sources.base import SyncContext
from dfs.sync import run_sync
from dfs.weekly_reset import LINEUPS_NAME_BLOCKS, PLAYER_POOL_NAME_BLOCKS, clear_previous_week

# EdgeRaw columns that get a color-scale conditional format by
# `dfs sheets format-edge`, keyed to the (min, mid, max) colors of the
# gradient -- red -> yellow -> green for "worth a look" columns.
_EDGE_COLOR_SCALE_COLUMNS = {
    "Leverage": (
        {"red": 0.96, "green": 0.80, "blue": 0.80},
        {"red": 1.0, "green": 1.0, "blue": 0.80},
        {"red": 0.72, "green": 0.88, "blue": 0.72},
    ),
    "CeilVal": (
        {"red": 0.96, "green": 0.80, "blue": 0.80},
        {"red": 1.0, "green": 1.0, "blue": 0.80},
        {"red": 0.72, "green": 0.88, "blue": 0.72},
    ),
    "GameEnv": (
        {"red": 0.96, "green": 0.80, "blue": 0.80},
        {"red": 1.0, "green": 1.0, "blue": 0.80},
        {"red": 0.72, "green": 0.88, "blue": 0.72},
    ),
}
_EDGE_FORMAT_LAST_ROW = 1000  # matches write_tab's default worksheet sizing

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
app.add_typer(sheets_app, name="sheets")
app.add_typer(auth_app, name="auth")
app.add_typer(bankroll_app, name="bankroll")
app.add_typer(lineups_app, name="lineups")
app.add_typer(odds_app, name="odds")

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


@sheets_app.command("format-edge")
def sheets_format_edge(
    sheet_id: str = typer.Option(
        None,
        "--sheet-id",
        help="Format a different sheet instead of config.toml's -- e.g. the canonical "
        "weekly template, so new copies already have EdgeRaw formatted. See "
        "CONTRIBUTING.md's 'Adding a new data source' checklist.",
    ),
    tab: str = typer.Option(None, "--tab", help="Tab name (default: config.toml's 'edge' tab mapping)."),
) -> None:
    """One-time formatting for the EdgeRaw tab: frozen header row and
    color scales on Leverage/CeilVal/GameEnv. Re-running just re-applies
    the same rules -- safe, since the tab holds no formulas of its own.
    Run `dfs sync --only edge` at least once first so the tab exists.
    """
    cfg = _load_config_or_exit()
    gs_cfg = cfg.google_sheets.model_copy(update={"sheet_id": sheet_id}) if sheet_id else cfg.google_sheets
    tab_name = tab or cfg.google_sheets.tab_mappings.get("edge")
    if not tab_name:
        console.print("[red]No tab mapped for 'edge' in config.toml, and no --tab given.[/red]")
        raise typer.Exit(code=1)

    client = SheetsClient(gs_cfg)
    try:
        title, url = client.describe()
        console.print(f"Formatting {tab_name!r} in: [bold]{title}[/bold]\n{url}\n")

        client.freeze_header(tab_name)
        console.print("[green]OK[/green] froze header row")

        for column_name, (min_color, mid_color, max_color) in _EDGE_COLOR_SCALE_COLUMNS.items():
            col = column_letter(EDGE_COLUMNS.index(column_name))
            client.add_color_scale(
                tab_name,
                f"{col}2:{col}{_EDGE_FORMAT_LAST_ROW}",
                min_color=min_color,
                mid_color=mid_color,
                max_color=max_color,
            )
            console.print(f"[green]OK[/green] color scale on {column_name} ({col})")
    except SheetsError as e:
        console.print(f"[red]Sheets error:[/red] {e}")
        raise typer.Exit(code=1) from e


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
        results = [
            link_edge_columns(client, PLAYER_POOL_RAW_TAB, PLAYER_POOL_RAW_BLOCK, edge_tab),
            link_edge_columns(client, cfg.lineups.player_pool_tab, PLAYER_POOL_NAME_BLOCKS, edge_tab),
            link_edge_columns(
                client,
                cfg.lineups.builder_tab,
                LINEUPS_NAME_BLOCKS,
                edge_tab,
                header_repeats_at=header_repeats_at,
            ),
        ]
    except SheetsError as e:
        console.print(f"[red]Sheets error:[/red] {e}")
        raise typer.Exit(code=1) from e

    for line in results:
        console.print(f"[green]OK[/green] {line}")


@app.command()
def sync(
    only: str = typer.Option(None, "--only", help="Comma-separated source names to sync (default: all)."),
    no_upload: bool = typer.Option(False, "--no-upload", help="Fetch and store locally, skip Sheets."),
    week: int = typer.Option(None, "--week", help="Override auto-detected NFL week."),
    season: int = typer.Option(None, "--season", help="Override auto-detected NFL season."),
) -> None:
    """Fetch data sources and upload them to the connected Google Sheet."""
    cfg = _load_config_or_exit()

    if only:
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

    if any_failed:
        raise typer.Exit(code=1)


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
) -> None:
    """Clear last week's typed-in lineup data (Lineups/Player Pool name
    columns, Scratch, DK Upload) so the sheet's ready for a new week.

    Formulas and formatting (including conditional formatting) are left
    untouched -- only the typed values a human enters while building
    lineups get cleared. Run this once per new weekly sheet copy, before
    rebuilding lineups for that week.
    """
    cfg = _load_config_or_exit()
    client = SheetsClient(cfg.google_sheets)
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
