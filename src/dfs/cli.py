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

from dfs import paths
from dfs.config import Config, ConfigError, load_config
from dfs.log import get_logger, setup_logging
from dfs.sheets import SheetsClient, SheetsError

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


if __name__ == "__main__":
    app()
