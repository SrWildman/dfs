"""dfs -- personal DFS data companion CLI.

Replaces run_all.py / run_update.py / upload.py. Those discarded their own
success/failure result (main()'s return value was never used to set the
process exit code, so `python3 run_all.py` always exited 0 even when every
scraper failed) -- every subcommand here returns a real exit code via
typer.Exit.
"""

from __future__ import annotations

import json as _json

import typer
from rich.console import Console
from rich.table import Table

from pathlib import Path

import pandas as pd

from dfs import paths, store
from dfs.bankroll import classify_entry, parse_contest_history, sync_bucket
from dfs.config import Config, ConfigError, load_config
from dfs.lineups import build_salary_lookup, export_csv, parse_entries, validate_entry
from dfs.log import get_logger, setup_logging
from dfs.sheets import SheetsClient, SheetsError
from dfs.sources import SOURCES
from dfs.sources.base import SyncContext
from dfs.sync import run_sync

app = typer.Typer(
    name="dfs",
    help="Sync DFS data into Google Sheets, export DK lineups, track results and bankroll.",
    no_args_is_help=True,
)
sheets_app = typer.Typer(help="Inspect and manage the connected Google Sheet.")
auth_app = typer.Typer(help="Log in to sites that require an authenticated session.")
bankroll_app = typer.Typer(help="Reconcile contest history into your bankroll tab.")
app.add_typer(sheets_app, name="sheets")
app.add_typer(auth_app, name="auth")
app.add_typer(bankroll_app, name="bankroll")

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
            table.add_row(source, tab, entry.get("synced_at", "-"), "-", f"[red]failed: {entry['error']}[/red]")
        else:
            table.add_row(source, tab, entry.get("synced_at", "-"), str(entry.get("rows", "-")), "[green]ok[/green]")
    console.print(table)


def _load_config_or_exit() -> Config:
    try:
        return load_config()
    except ConfigError as e:
        console.print(f"[red]Config error:[/red] {e}")
        raise typer.Exit(code=1)


@sheets_app.command("inspect")
def sheets_inspect() -> None:
    """List every tab in the connected sheet, with dimensions and header row."""
    cfg = _load_config_or_exit()
    client = SheetsClient(cfg.google_sheets)
    try:
        tabs = client.list_tabs()
    except SheetsError as e:
        console.print(f"[red]Sheets error:[/red] {e}")
        raise typer.Exit(code=1)

    known_tabs = set(cfg.google_sheets.tab_mappings.values())
    table = Table(title=f"Tabs in sheet {cfg.google_sheets.sheet_id}")
    table.add_column("tab")
    table.add_column("size")
    table.add_column("mapped?")
    table.add_column("header row")
    for tab in tabs:
        mapped = "[green]yes[/green]" if tab.title in known_tabs else "[dim]no[/dim]"
        header = ", ".join(tab.header) if tab.header else "[dim](empty)[/dim]"
        table.add_row(tab.title, f"{tab.rows}x{tab.cols}", mapped, header)
    console.print(table)


@app.command()
def sync(
    only: str = typer.Option(
        None, "--only", help="Comma-separated source names to sync (default: all)."
    ),
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
def export(
    output: Path = typer.Option(..., "--output", "-o", help="Path to write the DK bulk-upload CSV."),
) -> None:
    """Validate paired lineups in the lineups tab and export DK's upload CSV."""
    cfg = _load_config_or_exit()

    try:
        salary_df = store.load_current("draftkings")
    except FileNotFoundError as e:
        console.print(f"[red]{e}[/red]")
        raise typer.Exit(code=1)

    client = SheetsClient(cfg.google_sheets)
    try:
        rows = client.read_tab(cfg.lineups.upload_tab)
    except SheetsError as e:
        console.print(f"[red]Sheets error:[/red] {e}")
        raise typer.Exit(code=1)

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
            table.add_row(str(r.entry.row_number), r.entry.entry_id, r.entry.contest_name, str(r.salary_total), "[green]ok[/green]")
        else:
            table.add_row(
                str(r.entry.row_number), r.entry.entry_id, r.entry.contest_name, "-",
                "[red]" + "; ".join(r.errors) + "[/red]",
            )
    console.print(table)

    valid = [r.entry for r in results if r.ok]
    if valid:
        n = export_csv(valid, output)
        console.print(f"[green]Wrote {n} lineup(s) to {output}[/green]")

    invalid_count = len(results) - len(valid)
    if invalid_count:
        console.print(f"[red]{invalid_count} entr{'y' if invalid_count == 1 else 'ies'} skipped due to validation errors above.[/red]")
        raise typer.Exit(code=1)


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
        raise typer.Exit(code=1)

    cash_entries = [e for e in entries if classify_entry(e) == "cash"]
    gpp_entries = [e for e in entries if classify_entry(e) == "gpp"]
    console.print(f"Parsed {len(entries)} entries: {len(cash_entries)} cash, {len(gpp_entries)} GPP.")

    client = SheetsClient(cfg.google_sheets)
    try:
        cash_result = sync_bucket(client, cfg.bankroll.tab, cfg.bankroll.cash, cash_entries, "cash")
        gpp_result = sync_bucket(client, cfg.bankroll.tab, cfg.bankroll.gpp, gpp_entries, "gpp")
    except SheetsError as e:
        console.print(f"[red]Sheets error:[/red] {e}")
        raise typer.Exit(code=1)

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
