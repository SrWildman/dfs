"""dfs -- personal DFS data companion CLI.

Replaces run_all.py / run_update.py / upload.py. Those discarded their own
success/failure result (main()'s return value was never used to set the
process exit code, so `python3 run_all.py` always exited 0 even when every
scraper failed) -- every subcommand here returns a real exit code via
typer.Exit.
"""

from __future__ import annotations

import json as _json
import re
import shlex
import shutil
import subprocess
import sys
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd
import typer
from rich.console import Console
from rich.table import Table

from dfs import nfl_calendar, paths, store
from dfs.bankroll import (
    backfill_entry_keys,
    classify_entry,
    entries_for_week,
    parse_contest_history,
    sync_bucket,
)
from dfs.config import Config, ConfigError, load_config
from dfs.doctor import run_doctor
from dfs.late_swap import lineup_slot_status, swap_candidates
from dfs.launcher import LauncherState, header_lines, suggest_actions
from dfs.line_movement import LineMovementError, diff_odds
from dfs.lineups import build_salary_lookup, export_csv, parse_entries, validate_entry
from dfs.live_diff import diff_edge_flags, diff_queue_changes
from dfs.log import get_logger, setup_logging
from dfs.models import ROSTER_SLOTS
from dfs.ownership import append_ownership, parse_ownership_export
from dfs.pool import clear_all, find_matches, read_players, set_pool
from dfs.results_autofill import compute_week_results, write_results_updates
from dfs.sheet_audit import SKIPPED_TABS, run_audit
from dfs.sheet_columns import LINEUPS_COLUMN_ORDER, PLAYER_POOL_COLUMN_ORDER, PLAYER_POOL_RAW_COLUMN_ORDER
from dfs.sheet_filters import add_all_filter_views, add_basic_filters
from dfs.sheet_lineup_metrics import write_lineup_metrics
from dfs.sheet_links import (
    PLAYER_POOL_RAW_BLOCK,
    PLAYER_POOL_RAW_TAB,
    link_edge_columns,
    write_edge_row_links,
)
from dfs.sheet_pool_control import drain_control_cell_into_added_names, ensure_pool_control_row
from dfs.sheet_pool_deck import remove_pool_deck
from dfs.sheet_pool_formulas import write_pool_formulas
from dfs.sheet_pool_raw_sos import rewrite_opp_pos_rank
from dfs.sheet_pool_usage import write_pool_usage_columns
from dfs.sheet_protection import protect_workbook
from dfs.sheet_reorder import migrate_tab_to_designed_order, remove_header_columns, rename_header_column
from dfs.sheet_style import (
    EDGE_ROWS,
    POOL_RAW_ROWS,
    apply_tab_chrome,
    apply_tab_notes,
    fix_lineups_dst_slot_label,
    polish_bankroll,
    polish_builder_tab,
    polish_edge,
    polish_guardrails,
    polish_lineups_input_column,
    polish_lineups_pct_of_cap,
    polish_lineups_totals_rows,
    style_tier23_tabs,
    style_view_tabs,
)
from dfs.sheet_tab_removal import remove_retired_tabs
from dfs.sheet_typo_guard import add_lineups_typo_guard
from dfs.sheet_views import build_board, build_exposure, build_movement, build_slate_grid, write_queue_section
from dfs.sheets import SheetsClient, SheetsError, column_letter
from dfs.sources import SOURCES
from dfs.sources.base import SyncContext
from dfs.sync import run_sync
from dfs.week import (
    BANKROLL_CARRYOVER_CELLS,
    extract_results_value_columns,
    parse_sheet_id_from_url,
    rewrite_sheet_id,
)
from dfs.weekly_reset import (
    LINEUPS_NAME_BLOCKS,
    LINEUPS_TOTALS_ROWS,
    PLAYER_POOL_HEADER_ROW,
    PLAYER_POOL_NAME_BLOCKS,
    clear_previous_week,
    clear_synced_tabs,
)

# Rough NFL game length -- past this many hours since the LAST game of the
# week kicked off, `dfs`'s launcher assumes the slate is done and suggests
# closing the week rather than a mid-game action. A heuristic, not a real
# schedule lookup (no source here carries game-end times).
GAMES_FINISHED_AFTER_HOURS = 3.5

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
)
# One-time sheet construction -- the stuff you run once per template/sheet
# copy, not during a normal week. See `setup_sheet` below for the composite
# that runs all of it in the right order.
setup_app = typer.Typer(help="One-time sheet construction: build, style and link a sheet from scratch.")
# `dfs sheets X` used to be where all nine `setup_app` commands (plus
# `doctor`, now promoted to top-level) lived. Kept around, hidden from
# `--help`, as a compatibility shim -- 79 places across docs/tests/muscle
# memory said `dfs sheets ...` before this reorganisation, and breaking
# that silently (a renamed command that just says "no such command") is
# worse than a deprecation notice. Remove this whole app once the season's
# over and the old habit has had time to fade -- see CONTRIBUTING.md.
sheets_app = typer.Typer(hidden=True)
auth_app = typer.Typer(help="Log in to sites that require an authenticated session.")
bankroll_app = typer.Typer(help="Reconcile contest history into your bankroll tab.")
lineups_app = typer.Typer(help="Manage lineups: check late swaps, clear last week's picks.")
odds_app = typer.Typer(help="Check how betting lines have moved since your last sync.")
week_app = typer.Typer(help="Start a new week's sheet, or close out the one you're on.")
pool_app = typer.Typer(help="Add or remove players from your pool without opening the sheet.")
ownership_app = typer.Typer(help="Log actual DK contest ownership (Phase 6, Part 7.8).")
app.add_typer(setup_app, name="setup")
app.add_typer(sheets_app, name="sheets")
app.add_typer(auth_app, name="auth")
app.add_typer(bankroll_app, name="bankroll")
app.add_typer(lineups_app, name="lineups")
app.add_typer(odds_app, name="odds")
app.add_typer(week_app, name="week")
app.add_typer(pool_app, name="pool")
app.add_typer(ownership_app, name="ownership")

console = Console()
log = get_logger("cli")


@app.callback(invoke_without_command=True)
def main(
    ctx: typer.Context,
    verbose: bool = typer.Option(False, "--verbose", "-v", help="Show debug logging."),
    json_output: bool = typer.Option(
        False, "--json", help="Emit line-delimited JSON logs instead of console output."
    ),
) -> None:
    setup_logging(verbose=verbose, json_output=json_output)
    if ctx.invoked_subcommand is None:
        run_launcher()


def gather_state() -> LauncherState:
    """The impure half of the launcher (see launcher.py's docstring):
    collects observed facts and nothing else -- no judgment about what
    they mean lives here, that's `suggest_actions`'s job. Degrades field
    by field rather than raising, so a half-working sheet (reachable but
    slow, or missing the `edge` tab mapping) still renders a header instead
    of a traceback (Task 2.6).

    Performance (Task 2.5): everything before the first Sheets call is
    local and instant (config, manifest, EdgeRaw's already-synced GameStart
    column). Reaching the sheet costs exactly two round trips regardless of
    state -- `describe()` (opens/caches the spreadsheet) and one
    `batch_read_ranges` call covering both the pool tick column and every
    lineup block's name cell -- never one read per fact.
    """
    state = LauncherState()
    try:
        cfg = load_config()
    except ConfigError as e:
        state.config_exists = False
        state.config_error = str(e)
        return state

    state.week = SyncContext.current().week

    manifest = store.read_manifest()
    state.synced_sources = sum(1 for v in manifest.values() if not v.get("error"))
    timestamps = [v["synced_at"] for v in manifest.values() if v.get("synced_at")]
    if timestamps:
        latest = max(datetime.fromisoformat(t) for t in timestamps)
        state.freshest_sync_age_hours = (datetime.now(UTC) - latest).total_seconds() / 3600

    try:
        edge_df = store.load_current("edge")
    except FileNotFoundError:
        edge_df = None
    if edge_df is not None and "GameStart" in edge_df.columns:
        starts = pd.to_datetime(edge_df["GameStart"], utc=True, errors="coerce").dropna()
        if len(starts):
            now = pd.Timestamp.now(tz="UTC")
            state.game_started = bool((starts <= now).any())
            if state.game_started:
                hours_since_last_kickoff = (now - starts.max()).total_seconds() / 3600
                state.games_finished = hours_since_last_kickoff >= GAMES_FINISHED_AFTER_HOURS

    try:
        client = SheetsClient(cfg.google_sheets)
        state.sheet_title, _url = client.describe()
    except SheetsError as e:
        state.sheet_error = str(e)
        state.pool_error = state.lineups_error = str(e)
        return state

    state.lineups_total = len(LINEUPS_NAME_BLOCKS)
    edge_tab = cfg.google_sheets.tab_mappings.get("edge")
    if not edge_tab:
        state.pool_error = "no 'edge' tab mapped in config.toml"
        return state

    try:
        lineups_last_row = LINEUPS_NAME_BLOCKS[-1][1]
        pool_values, lineups_values = client.batch_read_ranges(
            [(edge_tab, f"A2:A{EDGE_ROWS}"), (cfg.lineups.builder_tab, f"A2:A{lineups_last_row}")]
        )
    except SheetsError as e:
        state.pool_error = str(e)
        state.lineups_error = str(e)
        return state

    # Pool is a blank/Cash/GPP/Both dropdown now, not a TRUE/FALSE
    # checkbox (Fix 2.11) -- any non-blank value counts as pooled.
    state.pool_count = sum(1 for row in pool_values if row and row[0].strip())

    filled = 0
    for start, _end in LINEUPS_NAME_BLOCKS:
        idx = start - 2  # lineups_values starts at row 2
        cell = lineups_values[idx][0] if 0 <= idx < len(lineups_values) and lineups_values[idx] else ""
        if cell.strip():
            filled += 1
    state.lineups_filled = filled

    return state


def _run_dfs_command(command: str) -> None:
    """Reinvoke `dfs` as a fresh process for a suggestion picked from the
    launcher's menu, rather than calling the target's typer-decorated
    function directly in-process -- that function's own parameter defaults
    are `typer.Option(...)` sentinel objects, only ever resolved to real
    values by Click's own parsing, so calling it in-process without going
    through that would pass those sentinels straight through instead of
    e.g. `False`/`None`. Reinvoking `dfs` on PATH (the same console script
    this launcher is itself running from) is simple and correctly inherits
    the terminal for confirmation prompts and rich tables; falls back to
    `python -m dfs.cli` if `dfs` isn't on PATH for some reason (e.g. run via
    `python -m` directly)."""
    args = shlex.split(command)
    dfs_path = shutil.which("dfs")
    argv = [dfs_path] + args[1:] if dfs_path else [sys.executable, "-m", "dfs.cli", *args[1:]]
    subprocess.run(argv)


def run_launcher() -> None:
    """`dfs` with no subcommand: a compact "where am I" header plus the
    actions that make sense right now (Task 2). Always shows the real `dfs
    ...` command next to each choice -- the point is to make itself
    unnecessary over time, not to become a menu Sam has to remember."""
    state = gather_state()

    for line in header_lines(state):
        console.print(line)
    console.print()

    menu: list[tuple[str, str | None]] = [(a.label, a.command) for a in suggest_actions(state)]
    if state.config_exists:
        menu.append(("Check the sheet's structure", "dfs doctor"))
        menu.append(("Full status", "dfs status"))

    for i, (label, command) in enumerate(menu, start=1):
        hint = f"[dim]{command}[/dim]" if command else "[dim](do this in the sheet)[/dim]"
        console.print(f"  {i}  {label:<32} {hint}")
    console.print("  q  Quit")
    console.print()
    console.print(
        "[dim]Tip: `dfs --install-completion` sets up tab-completion for every command above.[/dim]"
    )
    console.print()

    choice = typer.prompt("Choice", default="q", show_default=False).strip().lower()
    if choice in ("", "q"):
        return

    try:
        index = int(choice) - 1
        label, command = menu[index]
    except (ValueError, IndexError):
        console.print(f"[yellow]Not a valid choice: {choice!r}[/yellow]")
        return

    if command is None:
        console.print(f"[dim]{label} -- there's nothing to run; open the sheet.[/dim]")
        return
    if "<" in command:
        console.print(f"[yellow]That needs an argument -- run it yourself:[/yellow] {command}")
        return

    console.print(f"[dim]$ {command}[/dim]")
    _run_dfs_command(command)


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


@setup_app.command("inspect")
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


@setup_app.command(
    "remove-pool-deck",
    short_help="One-time repair: delete a still-present pool deck. Kept only for an un-migrated sheet.",
)
def sheets_remove_pool_deck(
    sheet_id: str = typer.Option(
        None,
        "--sheet-id",
        help="Remove the pool deck from a different sheet instead of config.toml's -- e.g. the "
        "canonical weekly template.",
    ),
) -> None:
    """Phase 5 (2026-09-16): the pool deck -- frozen rows at the top of
    Lineups holding a sortable/filterable window into Player Pool -- is
    retired. Sam, after a week building real lineups against it: "I've
    used it week 1 and it was a pain," and on the "where is this player"
    jump control alone -- "doesn't get me much. Cut it." Player Pool's own
    colour scales/chips/`Used`/`In` columns cover the browsing job now.

    Kept around (unlike `add-pool-deck`/`add-pool-picks`/`remove-pool-picks`,
    removed in Phase 6 Part 1.6) only in case some un-migrated sheet still
    has a deck sitting on it -- not part of the standing `dfs setup
    sheet`/`polish` pipeline, and a from-scratch build never creates one.

    Deletes the deck's rows (a real Sheets row delete, so every Lineups
    block shifts up with it) and the hidden `PoolSort` helper tab. No-op
    if the deck isn't present (already removed, or never built). See
    `sheet_pool_deck.py`'s module docstring and CONTRIBUTING.md's
    changelog for the full history and what depended on it.
    """
    cfg = _load_config_or_exit()
    gs_cfg = cfg.google_sheets.model_copy(update={"sheet_id": sheet_id}) if sheet_id else cfg.google_sheets
    client = SheetsClient(gs_cfg)
    try:
        title, url = client.describe()
        console.print(
            f"Removing pool deck from {cfg.lineups.builder_tab!r} in: [bold]{title}[/bold]\n{url}\n"
        )
        result = remove_pool_deck(client, cfg.lineups.builder_tab)
    except SheetsError as e:
        console.print(f"[red]Sheets error:[/red] {e}")
        raise typer.Exit(code=1) from e
    console.print(f"[green]OK[/green] {result}")


@setup_app.command(
    "remove-retired-tabs",
    short_help="One-time: remove Scratch/EntriesRaw/GPPin/DKLineupsRaw/DKLineupsFinal.",
)
def sheets_remove_retired_tabs(
    mode: str = typer.Option(
        ...,
        "--mode",
        help="'delete' (template -- future weekly copies start clean) or 'hide' (live sheet -- "
        "EntriesRaw may hold real pasted history a delete can't recover; a hidden tab is still "
        "fully readable/writable by dfs sync). See Part 4b in CONTRIBUTING.md's changelog.",
    ),
    sheet_id: str = typer.Option(
        None,
        "--sheet-id",
        help="Act on a different sheet instead of config.toml's -- e.g. the canonical weekly "
        "template (pass --mode delete there; the live sheet should get --mode hide).",
    ),
) -> None:
    """Phase 6 Part 4b (2026-09-22): `Scratch` (a blank drafting grid, no
    formulas) and the `EntriesRaw`/`GPPin`/`DKLineupsRaw`/`DKLineupsFinal`
    hand-paste DK-contest-history chain (superseded by `dfs week close
    --csv`'s CSV-based path) are retired -- Sam confirmed he does not use
    any of the five, and `dfs` never read or wrote them except to
    clear/style them. Asymmetric by design: run this with `--mode delete`
    against the template and `--mode hide` against the live sheet -- never
    delete on live, `EntriesRaw` may hold real pasted history. Idempotent
    either way. Also removes these five's own rows from the `Instructions`
    tab on whichever sheet this runs against, regardless of `--mode` --
    the documentation should read correctly on both. See
    `sheet_tab_removal.py`'s module docstring and `docs/planning/PROMPT_DATA.md`
    for where a past entry's roster-slot detail lives now that
    `EntriesRaw` is gone (the DK export CSVs on disk).
    """
    if mode not in ("delete", "hide"):
        console.print(f"[red]Error:[/red] --mode must be 'delete' or 'hide', got {mode!r}")
        raise typer.Exit(code=1)
    cfg = _load_config_or_exit()
    gs_cfg = cfg.google_sheets.model_copy(update={"sheet_id": sheet_id}) if sheet_id else cfg.google_sheets
    client = SheetsClient(gs_cfg)
    try:
        title, url = client.describe()
        console.print(f"Removing retired tabs (--mode {mode}) in: [bold]{title}[/bold]\n{url}\n")
        results = remove_retired_tabs(client, mode=mode)
    except SheetsError as e:
        console.print(f"[red]Sheets error:[/red] {e}")
        raise typer.Exit(code=1) from e
    for line in results:
        console.print(f"[green]OK[/green] {line}")


@setup_app.command(
    "remove-sos-placeholders", short_help="One-time: delete the retired blank SoS 1..4 columns."
)
def sheets_remove_sos_placeholders(
    sheet_id: str = typer.Option(
        None,
        "--sheet-id",
        help="Remove the placeholders from a different sheet instead of config.toml's -- e.g. the "
        "canonical weekly template.",
    ),
) -> None:
    """Phase 5 (2026-09-16): `SoS 1`/`SoS 2`/`SoS 3`/`SoS 4` -- four blank
    GAME-zone columns reserved on PlayerPoolRaw/Player Pool/Lineups back
    when strength-of-schedule data didn't exist yet -- are retired now
    that the real sync (`sources/tffb_sos.py`) landed straight into
    `OppPosRank` instead. Sam: "Why still sos 1-4. Should only be one per
    player." Deletes all four columns (a real Sheets column delete, so
    everything to their right shifts left) from each of the three tabs.
    No-op per tab if none of the four are present (already removed, or a
    sheet built after this migration already shipped without them). See
    `sheet_reorder.remove_header_columns` and CONTRIBUTING.md's changelog.
    """
    cfg = _load_config_or_exit()
    gs_cfg = cfg.google_sheets.model_copy(update={"sheet_id": sheet_id}) if sheet_id else cfg.google_sheets
    client = SheetsClient(gs_cfg)
    sos_columns = ["SoS 1", "SoS 2", "SoS 3", "SoS 4"]
    try:
        title, url = client.describe()
        console.print(f"Removing SoS 1..4 placeholders in: [bold]{title}[/bold]\n{url}\n")
        # Player Pool's real header sits at PLAYER_POOL_HEADER_ROW (row 1
        # is the "Add a player" control cell), unlike PlayerPoolRaw/
        # Lineups, which both default to row 1 -- passing the wrong row
        # here would read the control row instead and conclude (wrongly)
        # that none of the four columns are present.
        tabs = [
            (PLAYER_POOL_RAW_TAB, 1),
            (cfg.lineups.player_pool_tab, PLAYER_POOL_HEADER_ROW),
            (cfg.lineups.builder_tab, 1),
        ]
        for tab, header_row in tabs:
            result = remove_header_columns(client, tab, sos_columns, header_row=header_row)
            console.print(f"[green]OK[/green] {result}")
    except SheetsError as e:
        console.print(f"[red]Sheets error:[/red] {e}")
        raise typer.Exit(code=1) from e


@setup_app.command(
    "fix-opp-pos-rank", short_help="One-time: fix PlayerPoolRaw's OppPosRank (was keyed on Team, not Opp.)."
)
def sheets_fix_opp_pos_rank(
    sheet_id: str = typer.Option(
        None,
        "--sheet-id",
        help="Fix a different sheet instead of config.toml's -- e.g. the canonical weekly template.",
    ),
) -> None:
    """Found live 2026-09-16, the first time real strength-of-schedule
    data ever flowed through it: `PlayerPoolRaw`'s `OppPosRank` -- a
    hand-typed `VLOOKUP`+`HLOOKUP` against `SoSComb` that nothing in this
    codebase previously regenerated -- was keyed on this row's own `Team`
    column instead of `Opp.`, so every value measured how tough a
    player's OWN defense is, never their actual opponent's. Rewrites
    every row's formula, keyed correctly this time (`sheet_pool_raw_sos.
    rewrite_opp_pos_rank`). Player Pool/Lineups need no separate fix --
    both already VLOOKUP their own `OppPosRank` off PlayerPoolRaw by
    Name, so they self-heal the moment PlayerPoolRaw's own value is
    correct.
    """
    cfg = _load_config_or_exit()
    gs_cfg = cfg.google_sheets.model_copy(update={"sheet_id": sheet_id}) if sheet_id else cfg.google_sheets
    client = SheetsClient(gs_cfg)
    try:
        title, url = client.describe()
        console.print(f"Fixing OppPosRank in: [bold]{title}[/bold]\n{url}\n")
        result = rewrite_opp_pos_rank(client, PLAYER_POOL_RAW_TAB, last_row=POOL_RAW_ROWS)
        console.print(f"[green]OK[/green] {result}")
    except SheetsError as e:
        console.print(f"[red]Sheets error:[/red] {e}")
        raise typer.Exit(code=1) from e


@setup_app.command(
    "fix-pct-of-cap",
    short_help="One-time: rebuild Lineups' '% of Cap' against the salary cap, not the running total.",
)
def sheets_fix_pct_of_cap(
    sheet_id: str = typer.Option(
        None,
        "--sheet-id",
        help="Fix a different sheet instead of config.toml's -- e.g. the canonical weekly template.",
    ),
) -> None:
    """Part 7.9 (2026-09-17): `Lineups`' `% of Own` column (renamed from
    `% of Rstr` in Phase 6, Part 2) was really cap allocation misnamed and
    mis-derived -- `=F<row>/F$<totals_row>`, this player's DK Sal as a
    share of the lineup's own running salary total, not its rostership.
    Renames the header to `% of Cap` in place first (`rename_header_
    column`, same "rename before any name-based lookup touches it" order
    as every other column rename in this codebase), then redivides by the
    salary cap (`config.toml`'s `[lineups] salary_cap`) instead, which
    also removes the `#DIV/0!` Part 1.2 previously guarded against -- a
    constant denominator can't divide by zero
    (`sheet_style.polish_lineups_pct_of_cap`)."""
    cfg = _load_config_or_exit()
    gs_cfg = cfg.google_sheets.model_copy(update={"sheet_id": sheet_id}) if sheet_id else cfg.google_sheets
    client = SheetsClient(gs_cfg)
    try:
        title, url = client.describe()
        console.print(f"Fixing '% of Cap' in: [bold]{title}[/bold]\n{url}\n")
        lineups_header_row = LINEUPS_NAME_BLOCKS[0][0] - 1
        header_repeats_at = [start - 1 for start, _ in LINEUPS_NAME_BLOCKS[1:]]
        renamed = rename_header_column(
            client,
            cfg.lineups.builder_tab,
            "% of Own",
            "% of Cap",
            header_row=lineups_header_row,
            header_repeats_at=header_repeats_at,
        )
        result = polish_lineups_pct_of_cap(
            client,
            cfg.lineups.builder_tab,
            header_row=lineups_header_row,
            name_blocks=LINEUPS_NAME_BLOCKS,
            salary_cap=cfg.lineups.salary_cap,
        )
        console.print(f"[green]OK[/green] renamed: {renamed}; {result}")
    except SheetsError as e:
        console.print(f"[red]Sheets error:[/red] {e}")
        raise typer.Exit(code=1) from e


@setup_app.command(
    "fix-lineups-dst-label",
    short_help="One-time: correct Lineups' stale 'DEF' defense-slot label to 'DST'.",
)
def sheets_fix_lineups_dst_label(
    sheet_id: str = typer.Option(
        None,
        "--sheet-id",
        help="Fix a different sheet instead of config.toml's -- e.g. the canonical weekly template.",
    ),
) -> None:
    """Found live (2026-09-18) verifying Part 7.4's DST-vs-own-QB
    guardrail with a real test lineup: it never fired. `Lineups`' own
    `Pos.` column is static text, one fixed roster-slot label per row --
    the defense slot says `"DEF"` on every block, both sheets, instead of
    `"DST"` (this codebase's own convention everywhere else), so the
    guardrail's own `MATCH("DST", ...)` could never find it
    (`sheet_style.fix_lineups_dst_slot_label`)."""
    cfg = _load_config_or_exit()
    gs_cfg = cfg.google_sheets.model_copy(update={"sheet_id": sheet_id}) if sheet_id else cfg.google_sheets
    client = SheetsClient(gs_cfg)
    try:
        title, url = client.describe()
        console.print(f"Fixing Lineups' DST slot label in: [bold]{title}[/bold]\n{url}\n")
        lineups_header_row = LINEUPS_NAME_BLOCKS[0][0] - 1
        header = client.read_range(cfg.lineups.builder_tab, f"A{lineups_header_row}:{lineups_header_row}")
        header = header[0] if header else []
        if "Pos." not in header:
            console.print("[red]'Pos.' column not found in Lineups' header.[/red]")
            raise typer.Exit(code=1)
        position_col = column_letter(header.index("Pos."))
        result = fix_lineups_dst_slot_label(
            client, cfg.lineups.builder_tab, position_col=position_col, name_blocks=LINEUPS_NAME_BLOCKS
        )
        console.print(f"[green]OK[/green] {result}")
    except SheetsError as e:
        console.print(f"[red]Sheets error:[/red] {e}")
        raise typer.Exit(code=1) from e


@setup_app.command("add-pool-control", short_help="Create/refresh Player Pool's add-a-player control row.")
def sheets_add_pool_control(
    sheet_id: str = typer.Option(
        None,
        "--sheet-id",
        help="Add/refresh the control row on a different sheet instead of config.toml's -- "
        "e.g. the canonical weekly template.",
    ),
) -> None:
    """A3: create (or refresh) Player Pool's own "add a player" row --
    a live search box (ONE_OF_RANGE validation) against EdgeRaw's Name
    column, pinned at the top of Player Pool itself, for when typing a
    name is faster than scrolling EdgeRaw to tick a checkbox (the "Pool
    picking" filter view, `dfs setup add-filters`, is the third way).
    Replaces the old separate `Pool Picks` tab (see `sheet_pool_control.py`
    and CONTRIBUTING.md's A3 changelog entry) -- Sam never wanted a
    second tab for this. The structural row-insert runs at most once per
    sheet (idempotent, see `ensure_pool_control_row`); safe to re-run any
    time, including as part of a future `dfs setup polish`.

    Also re-runs `write_pool_formulas` against Player Pool -- its Name/
    Overflow formulas need to change to read the UNION of EdgeRaw ticks
    and the control cell, and nothing else re-applies that automatically
    (see `sheet_pool_formulas.py`; it has no standing caller of its own
    in this CLI).
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
        console.print(f"Adding the pool control row to: [bold]{title}[/bold]\n{url}\n")
        results = [ensure_pool_control_row(client, cfg.lineups.player_pool_tab, edge_tab)]
        results.extend(
            write_pool_formulas(client, player_pool_tab=cfg.lineups.player_pool_tab, edge_tab=edge_tab)
        )
    except SheetsError as e:
        console.print(f"[red]Sheets error:[/red] {e}")
        raise typer.Exit(code=1) from e
    for line in results:
        console.print(f"[green]OK[/green] {line}")


@setup_app.command("polish", short_help="Style the sheet: widths, freeze panes, formats, chips, tab order.")
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
    affected. Two exceptions write real formula values, not styling, but
    both are safe to re-run the same way: Lineups' Guardrails column (O,
    additive-only -- it sits strictly left of the EdgeRaw-linked block and
    was never written to before, see `sheet_style.polish_guardrails`) and
    each lineup block's totals row (clears the dead per-slot VLOOKUPs a
    totals row was never a real 10th player for, sums Ceil, and labels
    the row -- see `sheet_style.polish_lineups_totals_rows`).

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
        results.append(
            polish_builder_tab(
                client,
                cfg.lineups.player_pool_tab,
                last_row=pool_last,
                header_row=PLAYER_POOL_HEADER_ROW,
                band_blocks=PLAYER_POOL_NAME_BLOCKS,
                color_scale_groups=PLAYER_POOL_NAME_BLOCKS,
            )
        )
        # +1 past the last block's own last real row (Fix 2.4): the
        # final totals row must still get FIELD_FORMATS/colour scales.
        lineups_last = max(LINEUPS_TOTALS_ROWS)
        lineups_header_row = LINEUPS_NAME_BLOCKS[0][0] - 1
        header_repeats_at = [start - 1 for start, _ in LINEUPS_NAME_BLOCKS[1:]]
        results.append(
            polish_builder_tab(
                client,
                cfg.lineups.builder_tab,
                last_row=lineups_last,
                header_row=lineups_header_row,
                header_repeats_at=header_repeats_at,
                band_blocks=LINEUPS_NAME_BLOCKS,
                color_scale_groups=LINEUPS_NAME_BLOCKS,
            )
        )
        results.append(
            polish_guardrails(
                client,
                cfg.lineups.builder_tab,
                header_row=lineups_header_row,
                name_blocks=LINEUPS_NAME_BLOCKS,
                header_repeats_at=header_repeats_at,
            )
        )
        results.append(
            write_lineup_metrics(
                client,
                cfg.lineups.builder_tab,
                header_row=lineups_header_row,
                name_blocks=LINEUPS_NAME_BLOCKS,
            )
        )
        results.append(polish_lineups_input_column(client, cfg.lineups.builder_tab, LINEUPS_NAME_BLOCKS))
        results.append(add_lineups_typo_guard(client, cfg.lineups.builder_tab, LINEUPS_NAME_BLOCKS))
        results.append(
            polish_lineups_totals_rows(
                client,
                cfg.lineups.builder_tab,
                header_row=lineups_header_row,
                name_blocks=LINEUPS_NAME_BLOCKS,
                salary_cap=cfg.lineups.salary_cap,
            )
        )
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
                    entry_key_columns=(
                        cfg.bankroll.cash.entry_key_column,
                        cfg.bankroll.gpp.entry_key_column,
                    ),
                )
            )
        else:
            results.append("Bankroll: no [bankroll.cash]/[bankroll.gpp] in config -- skipped")

        # The four derived tabs, if `dfs setup build-views` has made them.
        # Skipped cleanly when it hasn't.
        results.extend(style_view_tabs(client))

        # Tier 2/3: DK Upload, Results, and the hand-pasted SoS tabs. Row
        # bounds are generous, provisioned depth (same reasoning as
        # EDGE_ROWS/POOL_RAW_ROWS) -- formatting past the real data costs
        # nothing and each function reads its own header rather than
        # assuming a row count matters structurally.
        results.extend(
            style_tier23_tabs(
                client,
                dk_upload_last_row=200,
                results_last_row=30,
                sos_comb_last_row=40,
            )
        )

        if not skip_chrome:
            results.extend(apply_tab_chrome(client))

        # Fix 3/1.5: a short cell note on every visible tab's A1 explaining
        # what it's for (and, for the tabs that got a basic filter in `dfs
        # setup add-filters`, how to sort/search it) -- pure metadata, so
        # this is safe regardless of what else ran above.
        results.extend(apply_tab_notes(client))
    except SheetsError as e:
        console.print(f"[red]Sheets error:[/red] {e}")
        raise typer.Exit(code=1) from e

    for line in results:
        console.print(f"[green]OK[/green] {line}")


@setup_app.command("build-views", short_help="Build Board/Slate Grid/Exposure/Movement.")
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
            build_board(
                client,
                edge_tab=edge_tab,
                games_tab=games_tab,
                weather_tab=weather_tab,
                player_pool_tab=cfg.lineups.player_pool_tab,
            ),
            build_slate_grid(client, games_tab=games_tab, weather_tab=weather_tab, edge_tab=edge_tab),
            build_exposure(
                client,
                edge_tab=edge_tab,
                lineups_tab=cfg.lineups.builder_tab,
                lineup_count=len(LINEUPS_NAME_BLOCKS),
                lineups_header_row=LINEUPS_NAME_BLOCKS[0][0] - 1,
            ),
            build_movement(client, edge_tab=edge_tab),
        ]
    except SheetsError as e:
        console.print(f"[red]Sheets error:[/red] {e}")
        raise typer.Exit(code=1) from e

    for line in results:
        console.print(f"[green]OK[/green] {line}")

    console.print(
        "\n[dim]Run `dfs setup polish` afterwards -- it styles these four tabs "
        "and slots them into the tab strip.[/dim]"
    )


@setup_app.command("link-edge", short_help="Fill EdgeRaw derived columns into Player Pool/Lineups.")
def sheets_link_edge(
    sheet_id: str = typer.Option(
        None,
        "--sheet-id",
        help="Link a different sheet instead of config.toml's -- e.g. the canonical "
        "weekly template, so new copies already have EdgeRaw's columns linked in.",
    ),
    force: bool = typer.Option(
        False,
        "--force",
        help="Refresh every linked column's formula even where the tab is already fully linked -- "
        "needed after `edge_lookup_formula`'s own generation logic changes (not a position change), "
        "since the normal 'something's missing' trigger never fires on an already-linked tab.",
    ),
) -> None:
    """Fill in EdgeRaw's derived columns (Leverage, Flags, etc.) on Player
    Pool, Lineups, AND PlayerPoolRaw (the hub tab those two already
    VLOOKUP against for Pos./Team/Pts/etc.), via the same VLOOKUP-by-Name
    join. Writes into whatever column already carries a given name in the
    header (Part 2's designed order interleaves these with native
    columns; see `sheet_links.py`'s module docstring) and only creates
    (appends) a column for a name genuinely absent -- a fresh sheet build
    that hasn't been through `dfs setup reorder-columns` yet. The four
    collapsed zones (Game, Ceiling detail, Movement, Weather -- Part 2)
    are grouped so they can be collapsed from the sheet UI (the little
    +/- control above the column letters) when you want a narrower view;
    `Id`/`Flag` (Part 7.9) are hidden outright instead, not grouped. Safe
    to re-run -- a tab where every linked column already exists is left
    alone, not duplicated, unless `--force` is given.

    Also refreshes Player Pool's "Used"/"In" columns (Phase 5B: how many
    of this week's lineups roster a given pool player, and which ones) --
    native formulas referencing Lineups, not EdgeRaw, but the same
    "per-row formula that needs rewriting whenever the row layout moves"
    family of work.
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
            link_edge_columns(client, PLAYER_POOL_RAW_TAB, PLAYER_POOL_RAW_BLOCK, edge_tab, force=force),
            link_edge_columns(
                client,
                cfg.lineups.player_pool_tab,
                PLAYER_POOL_NAME_BLOCKS,
                edge_tab,
                header_row=PLAYER_POOL_HEADER_ROW,
                force=force,
            ),
            link_edge_columns(
                client,
                cfg.lineups.builder_tab,
                LINEUPS_NAME_BLOCKS,
                edge_tab,
                header_row=lineups_header_row,
                header_repeats_at=header_repeats_at,
                force=force,
            ),
            # A3: "Edge ↗" isn't an EdgeRaw-linked VLOOKUP column (not in
            # LINKED_EDGE_COLUMNS), but it's the same "point back at
            # EdgeRaw" family of work, so it's refreshed here rather than
            # adding yet another standing CLI command.
            write_edge_row_links(
                client,
                cfg.lineups.player_pool_tab,
                PLAYER_POOL_NAME_BLOCKS,
                edge_tab,
                header_row=PLAYER_POOL_HEADER_ROW,
            ),
            write_edge_row_links(
                client,
                cfg.lineups.builder_tab,
                LINEUPS_NAME_BLOCKS,
                edge_tab,
                header_row=lineups_header_row,
            ),
            # Phase 5B: "Used"/"In" reference Lineups, not EdgeRaw, but
            # they're the same "native per-row formula that needs
            # refreshing whenever the row layout changes" family of work
            # as the two calls above, so they're refreshed here too rather
            # than adding yet another standing CLI command.
            write_pool_usage_columns(
                client,
                cfg.lineups.player_pool_tab,
                cfg.lineups.builder_tab,
                header_row=PLAYER_POOL_HEADER_ROW,
            ),
        ]
    except SheetsError as e:
        console.print(f"[red]Sheets error:[/red] {e}")
        raise typer.Exit(code=1) from e

    for line in results:
        console.print(f"[green]OK[/green] {line}")


@setup_app.command(
    "reorder-columns",
    short_help="One-time: move PlayerPoolRaw/Player Pool/Lineups into the Phase 3 designed order.",
)
def sheets_reorder_columns(
    sheet_id: str = typer.Option(
        None,
        "--sheet-id",
        help="Reorder a different sheet instead of config.toml's -- ALWAYS run against the "
        "canonical template first, verify with `dfs doctor`, then run again against the "
        "live sheet.",
    ),
) -> None:
    """Phase 3/6, one-time: moves PlayerPoolRaw, Player Pool and Lineups
    into `sheet_columns.py`'s designed column order (a shared spine plus
    the Game/Ceiling detail/Movement/Weather collapsed groups, Phase 6
    Part 2) -- the reorder CONTRIBUTING.md's central hazard section says
    must happen exactly once, to a designed order with headroom already
    built in, never again piecemeal.

    First renames `Rstr%` -> `Own%` and `% of Rstr` -> `% of Own` in
    place on every tab that still has the old text (Part 2: a shared
    spine can't have a column that changes name per tab) -- must run
    BEFORE the reorder below, same reasoning as Section F's `ImpMove` ->
    `ImpliedMove` rename: every column-finding function locates by name,
    so reordering first would make `migrate_tab_to_designed_order` see
    `Own%` as genuinely missing and append a duplicate. No-ops per tab if
    already renamed (`rename_header_column`'s own idempotency).

    Then runs each tab through `sheet_reorder.migrate_tab_to_designed_order`,
    in the one order that's correct: PlayerPoolRaw first and entirely
    (provision -> link EdgeRaw in -> reorder), since Player Pool/Lineups'
    native PlayerPoolRaw-lookup formulas depend on PlayerPoolRaw's
    FINISHED layout; then Player Pool and Lineups, each provisioned,
    EdgeRaw-linked, native-formula-regenerated, and reordered in turn.

    Re-run `dfs setup polish` and `dfs doctor` afterward -- this command
    only renames/moves/creates columns and fills formulas; it doesn't
    touch widths, freeze panes, or conditional formatting.
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
            f"Reordering PlayerPoolRaw/Player Pool/Lineups into the designed order in: "
            f"[bold]{title}[/bold]\n{url}\n"
        )

        header_repeats_at = [start - 1 for start, _ in LINEUPS_NAME_BLOCKS[1:]]
        lineups_header_row = LINEUPS_NAME_BLOCKS[0][0] - 1

        rename_targets = [
            (PLAYER_POOL_RAW_TAB, 1, None),
            (cfg.lineups.player_pool_tab, PLAYER_POOL_HEADER_ROW, None),
            (cfg.lineups.builder_tab, lineups_header_row, header_repeats_at),
        ]
        renamed = 0
        for tab, row, repeats in rename_targets:
            if rename_header_column(client, tab, "Rstr%", "Own%", header_row=row, header_repeats_at=repeats):
                renamed += 1
        if rename_header_column(
            client,
            cfg.lineups.builder_tab,
            "% of Rstr",
            "% of Own",
            header_row=lineups_header_row,
            header_repeats_at=header_repeats_at,
        ):
            renamed += 1
        console.print(
            f"[green]OK[/green] renamed {renamed} header cell(s) (Rstr% -> Own%, % of Rstr -> % of Own)"
        )

        results = migrate_tab_to_designed_order(
            client,
            PLAYER_POOL_RAW_TAB,
            PLAYER_POOL_RAW_COLUMN_ORDER,
            name_blocks=PLAYER_POOL_RAW_BLOCK,
            edge_tab=edge_tab,
            rewrite_native=False,
        )
        results += migrate_tab_to_designed_order(
            client,
            cfg.lineups.player_pool_tab,
            PLAYER_POOL_COLUMN_ORDER,
            name_blocks=PLAYER_POOL_NAME_BLOCKS,
            edge_tab=edge_tab,
            header_row=PLAYER_POOL_HEADER_ROW,
            rewrite_native=True,
        )
        results += migrate_tab_to_designed_order(
            client,
            cfg.lineups.builder_tab,
            LINEUPS_COLUMN_ORDER,
            name_blocks=LINEUPS_NAME_BLOCKS,
            edge_tab=edge_tab,
            header_row=lineups_header_row,
            header_repeats_at=header_repeats_at,
            rewrite_native=True,
        )
    except (SheetsError, ValueError) as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(code=1) from e

    for line in results:
        console.print(f"[green]OK[/green] {line}")


@setup_app.command(
    "fix-flag-split",
    short_help="One-time: split Flag/Flags, drop OwnPct, rename LevBasis -> OwnStatus (Part 7.9).",
)
def sheets_fix_flag_split(
    sheet_id: str = typer.Option(
        None,
        "--sheet-id",
        help="Fix a different sheet instead of config.toml's -- ALWAYS run against the "
        "canonical template first, verify with `dfs doctor`, then run again against the "
        "live sheet.",
    ),
) -> None:
    """Part 7.9 (2026-09-17), three changes to `PlayerPoolRaw`/`Player
    Pool`/`Lineups`, run together since all three touch the same
    Ceiling-detail/spine region:

    1. `LevBasis` -> `OwnStatus` (renamed in place -- Leverage's own
       demotion off the spine left this marker gating `Own%` instead, the
       old name no longer said what it does).
    2. `Flag` (already every matching condition, space-separated, despite
       7.9's own spec assuming a stale first-match-only value -- see
       CONTRIBUTING.md) -> `Flags`, renamed in place, taking over `Flag`'s
       old visible spine slot; a NEW `Flag` column (single
       highest-priority token only) is then provisioned and moved into
       the hidden zone beside `Id`.
    3. `OwnPct` removed entirely (a real column delete) -- its only
       consumer anywhere in this codebase was the Leverage formula in
       `derived.py`, verified by grep before removing.

    Renames and the delete run FIRST (same "rename/delete before any
    name-based lookup touches it" order as every other migration in this
    codebase), then each tab runs through `sheet_reorder.migrate_tab_to_
    designed_order` again to provision the new `Flag` column, re-link
    every EdgeRaw formula against the current column set, and fix Ceiling
    detail's own internal order (CeilPct/Leverage/OwnStatus, was CeilPct/
    OwnPct/Leverage/LevBasis).

    Re-run `dfs setup link-edge --force`, `dfs setup polish`, and `dfs
    doctor` afterward -- this command only renames/removes/moves/creates
    columns; it doesn't touch widths, hiding, or conditional formatting.
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
        console.print(f"Fixing Flag/Flags/OwnPct/OwnStatus in: [bold]{title}[/bold]\n{url}\n")

        header_repeats_at = [start - 1 for start, _ in LINEUPS_NAME_BLOCKS[1:]]
        lineups_header_row = LINEUPS_NAME_BLOCKS[0][0] - 1
        rename_targets = [
            (PLAYER_POOL_RAW_TAB, 1, None),
            (cfg.lineups.player_pool_tab, PLAYER_POOL_HEADER_ROW, None),
            (cfg.lineups.builder_tab, lineups_header_row, header_repeats_at),
        ]
        renamed = 0
        for tab, row, repeats in rename_targets:
            kwargs = {"header_row": row, "header_repeats_at": repeats}
            if rename_header_column(client, tab, "LevBasis", "OwnStatus", **kwargs):
                renamed += 1
            if rename_header_column(client, tab, "Flag", "Flags", **kwargs):
                renamed += 1
        console.print(f"[green]OK[/green] renamed {renamed} header cell(s)")

        for tab, row, _repeats in rename_targets:
            removed = remove_header_columns(client, tab, ["OwnPct"], header_row=row)
            console.print(f"[green]OK[/green] {removed}")

        results = migrate_tab_to_designed_order(
            client,
            PLAYER_POOL_RAW_TAB,
            PLAYER_POOL_RAW_COLUMN_ORDER,
            name_blocks=PLAYER_POOL_RAW_BLOCK,
            edge_tab=edge_tab,
            rewrite_native=False,
        )
        results += migrate_tab_to_designed_order(
            client,
            cfg.lineups.player_pool_tab,
            PLAYER_POOL_COLUMN_ORDER,
            name_blocks=PLAYER_POOL_NAME_BLOCKS,
            edge_tab=edge_tab,
            header_row=PLAYER_POOL_HEADER_ROW,
            rewrite_native=True,
        )
        results += migrate_tab_to_designed_order(
            client,
            cfg.lineups.builder_tab,
            LINEUPS_COLUMN_ORDER,
            name_blocks=LINEUPS_NAME_BLOCKS,
            edge_tab=edge_tab,
            header_row=lineups_header_row,
            header_repeats_at=header_repeats_at,
            rewrite_native=True,
        )
    except (SheetsError, ValueError) as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(code=1) from e

    for line in results:
        console.print(f"[green]OK[/green] {line}")


@setup_app.command(
    "add-filters", short_help="Make sorting/searching visible: basic filters plus saved filter views."
)
def sheets_add_filters(
    sheet_id: str = typer.Option(
        None,
        "--sheet-id",
        help="Add filters to a different sheet instead of config.toml's -- e.g. the "
        "canonical weekly template.",
    ),
) -> None:
    """Fix 1: sorting and searching are real features that were invisible
    -- filter views live behind Data > Filter views, easy to never notice.

    Two mechanisms, in order of what you'll actually see:
    1. A VISIBLE basic filter (Data > Create a filter -- a dropdown arrow
       in every header cell) on EdgeRaw, Results, and the SoS
       tabs. Safe only on plain-value tabs; `set_basic_filter` always
       replaces whatever's there, so this is naturally re-runnable.
    2. Saved filter views (Sheets' Data > Filter views), the secondary,
       preset mechanism: EdgeRaw gets four named views ("Pool picking",
       "Leverage plays", "Available only", "In my pool"); Slate Grid/
       Movement/Exposure/Results/SoS* each get one plain sortable view.

    Deliberately NOT applied to Player Pool, Lineups, PlayerPoolRaw or
    Board -- see `sheet_filters.py`'s own docstring for why those stay on
    the pool deck's own sort/filter instead. Filter views are safe to
    re-run: each is deleted by title before being re-added, never
    duplicated.
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
        console.print(f"Adding filters to: [bold]{title}[/bold]\n{url}\n")
        results = add_basic_filters(client, edge_tab=edge_tab)
        results.extend(add_all_filter_views(client, edge_tab))
    except SheetsError as e:
        console.print(f"[red]Sheets error:[/red] {e}")
        raise typer.Exit(code=1) from e

    for line in results:
        console.print(f"[green]OK[/green] {line}")


@setup_app.command("protect", short_help="Warning-only protection on every formula-driven tab.")
def sheets_protect(
    sheet_id: str = typer.Option(
        None,
        "--sheet-id",
        help="Protect a different sheet instead of config.toml's -- e.g. the canonical weekly template.",
    ),
) -> None:
    """Warning-only protection (never a hard lock) on every fully
    formula-driven tab -- PlayerPoolRaw, Board, Slate Grid, Movement --
    plus Player Pool, Exposure and Lineups protected everywhere EXCEPT
    their own typed cells (Player Pool's add-a-player control; Exposure's
    Target and lineup-count cell; each lineup block's Name column).
    EdgeRaw is left alone entirely -- see `sheet_protection.py`'s own
    docstring for why. Safe to re-run: each tab's protected ranges are
    cleared before being re-added, never stacked.
    """
    cfg = _load_config_or_exit()
    gs_cfg = cfg.google_sheets.model_copy(update={"sheet_id": sheet_id}) if sheet_id else cfg.google_sheets
    client = SheetsClient(gs_cfg)
    try:
        title, url = client.describe()
        console.print(f"Protecting: [bold]{title}[/bold]\n{url}\n")
        results = protect_workbook(
            client, player_pool_tab=cfg.lineups.player_pool_tab, lineups_tab=cfg.lineups.builder_tab
        )
    except SheetsError as e:
        console.print(f"[red]Sheets error:[/red] {e}")
        raise typer.Exit(code=1) from e

    for line in results:
        console.print(f"[green]OK[/green] {line}")


@app.command("doctor", short_help="Check the sheet structure is intact.")
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
    lineups clear`/`dfs setup link-edge` on a fresh weekly copy, instead
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


@setup_app.command("audit-style", short_help="Check that polish actually landed everywhere it should.")
def sheets_audit_style(
    sheet_id: str = typer.Option(
        None,
        "--sheet-id",
        help="Audit a different sheet instead of config.toml's -- e.g. right after `dfs "
        "sheets polish`, to confirm what actually landed.",
    ),
) -> None:
    """Read-only style check: per tab, the header row carries the shared
    dark fill, a freeze pane covers it, every column has an explicit
    pixel width, every FIELD_FORMATS column isn't left on Sheets'
    "Automatic" number format, and a Flag/Avail column has a matching
    chip rule. Exists so `sheet_style.py`'s formatting can't quietly rot
    the way its number-format dicts once did (see Task 2.1's
    consolidation into `FIELD_FORMATS`) -- run this after `dfs setup
    polish` rather than trusting its own "OK" output. Never writes
    anything. Exits non-zero if any audited tab has a finding.
    """
    cfg = _load_config_or_exit()
    gs_cfg = cfg.google_sheets.model_copy(update={"sheet_id": sheet_id}) if sheet_id else cfg.google_sheets
    client = SheetsClient(gs_cfg)
    try:
        title, url = client.describe()
        console.print(f"Auditing: [bold]{title}[/bold]\n{url}\n")
        results = run_audit(client)
    except SheetsError as e:
        console.print(f"[red]Sheets error:[/red] {e}")
        raise typer.Exit(code=1) from e

    for skipped in SKIPPED_TABS:
        console.print(f"[dim]skipped[/dim] {skipped}")

    any_issue = False
    for audit in results:
        if not audit.present:
            console.print(f"[dim]--[/dim] {audit.tab}: not present -- skipped")
            continue
        if audit.clean:
            console.print(f"[green]OK[/green] {audit.tab}")
            continue
        any_issue = True
        for issue in audit.issues:
            console.print(f"[red]FAIL[/red] [{audit.tab}] {issue}")

    if any_issue:
        raise typer.Exit(code=1)


@setup_app.command("sheet", short_help="Run the full one-time sheet build, in order.")
def setup_sheet(
    sheet_id: str = typer.Option(
        None,
        "--sheet-id",
        help="Build a different sheet instead of config.toml's -- e.g. the canonical "
        "weekly template, so new weekly copies inherit everything below.",
    ),
) -> None:
    """Run the full one-time sheet build, in the order that actually works,
    instead of the hand-ordered sequence that used to live only in
    CONTRIBUTING.md's tribal knowledge. Stops at the first step that fails
    -- nothing after it runs, so the sheet is left in a known, reported
    state rather than partially built by whatever happened to come next.

    THE ORDER, and why it's this order and not another:

    1. `add-pool-control` -- a real row INSERT into Player Pool (its own
       add-a-player control row), repointing its Name/Overflow formulas at
       the union of it and EdgeRaw. The pool deck that used to occupy this
       same "step 1, a structural row insert everything else derives its
       header row from" slot on Lineups was retired (Phase 5, 2026-09-16
       -- see `sheet_pool_deck.py`'s module docstring); Lineups' header
       sits at row 1 unconditionally now, nothing left to derive.
    2. `build-views` -- creates Board/Slate Grid/Exposure/Movement. Must
       come before `add-filters`, which adds filter views ONTO three of
       those four tabs and would have nothing to attach to otherwise.
    3. `link-edge` -- appends EdgeRaw's derived columns onto Player Pool/
       Lineups/PlayerPoolRaw. Append-only, so it doesn't need 1-2 to have
       happened first, but running it before the tab set is final would
       mean re-deriving nothing extra -- no reason to run it earlier.
    4. `add-filters` -- filter views on EdgeRaw plus the four view tabs
       from step 2. Depends on 2 (see above).
    5. `protect` -- warning-only protection on every fully formula-driven
       tab, including the four view tabs. Runs after every structural
       tab/column exists so it's protecting the real final layout, not a
       moving target.
    6. `polish` -- presentation only (widths, freeze, number formats,
       chips, tab order). Never inserts/deletes/moves anything, so it's
       safe last -- and running it last means it's styling the finished
       structure, not something a later structural step would shift.
    7. `audit-style` -- read-only check that `polish` actually landed
       everywhere it should have, rather than trusting its own "OK" output.
    8. `dfs doctor` -- the final structural sanity check. Deliberately
       LAST, not first: several of its own checks (LINKED_EDGE_COLUMNS
       present) only pass once steps 1-6 have actually run, so using it as
       a pre-flight check here would just fail before doing anything useful.

    `inspect` (also moved under `setup`) isn't part of this sequence at
    all -- it's a read-only tab lister you'd reach for anytime, not a
    construction step, the same reason `doctor`/`audit-style` aren't
    either except as this composite's own final checks. `remove-pool-deck`
    isn't part of it either -- a one-time repair for a sheet that still
    has the retired deck, not something a fresh build ever creates.
    """
    steps: list[tuple[str, Callable[[], None]]] = [
        ("add-pool-control", lambda: sheets_add_pool_control(sheet_id=sheet_id)),
        ("build-views", lambda: sheets_build_views(sheet_id=sheet_id)),
        ("link-edge", lambda: sheets_link_edge(sheet_id=sheet_id)),
        ("add-filters", lambda: sheets_add_filters(sheet_id=sheet_id)),
        ("protect", lambda: sheets_protect(sheet_id=sheet_id)),
        ("polish", lambda: sheets_polish(sheet_id=sheet_id, skip_chrome=False)),
        ("audit-style", lambda: sheets_audit_style(sheet_id=sheet_id)),
        ("doctor", lambda: sheets_doctor(sheet_id=sheet_id)),
    ]
    for name, step in steps:
        console.print(f"\n[bold]-- {name} --[/bold]")
        try:
            step()
        except typer.Exit as e:
            if e.exit_code:
                console.print(f"\n[red]Stopped at {name!r} -- fix the above and re-run.[/red]")
                raise
    console.print("\n[green]OK[/green] sheet build complete.")


# -- `dfs sheets ...` compatibility aliases -----------------------------
#
# Every one of the nine commands above moved to `dfs setup ...`, and
# `doctor` moved to top-level `dfs doctor`, in this same reorganisation.
# `sheets_app` (declared near the top of this file, `hidden=True`) keeps
# every old spelling working: same function, same options, one notice
# printed first. 79 places (docs, tests, muscle memory) said `dfs sheets
# ...` before today -- a renamed command that just says "no such command"
# is a worse outcome than a deprecation notice for the rest of this season.
#
# Remove this whole block (and `sheets_app`'s registration near the top)
# once the season's over and the old habit's had time to fade.
def _moved_notice(old: str, new: str) -> None:
    console.print(f"[dim]`{old}` has moved to `{new}`.[/dim]")


@sheets_app.command("inspect")
def sheets_inspect_alias() -> None:
    _moved_notice("dfs sheets inspect", "dfs setup inspect")
    sheets_inspect()


@sheets_app.command("polish")
def sheets_polish_alias(
    sheet_id: str = typer.Option(None, "--sheet-id"),
    skip_chrome: bool = typer.Option(False, "--skip-chrome"),
) -> None:
    _moved_notice("dfs sheets polish", "dfs setup polish")
    sheets_polish(sheet_id=sheet_id, skip_chrome=skip_chrome)


@sheets_app.command("build-views")
def sheets_build_views_alias(sheet_id: str = typer.Option(None, "--sheet-id")) -> None:
    _moved_notice("dfs sheets build-views", "dfs setup build-views")
    sheets_build_views(sheet_id=sheet_id)


@sheets_app.command("link-edge")
def sheets_link_edge_alias(sheet_id: str = typer.Option(None, "--sheet-id")) -> None:
    _moved_notice("dfs sheets link-edge", "dfs setup link-edge")
    sheets_link_edge(sheet_id=sheet_id)


@sheets_app.command("add-filters")
def sheets_add_filters_alias(sheet_id: str = typer.Option(None, "--sheet-id")) -> None:
    _moved_notice("dfs sheets add-filters", "dfs setup add-filters")
    sheets_add_filters(sheet_id=sheet_id)


@sheets_app.command("protect")
def sheets_protect_alias(sheet_id: str = typer.Option(None, "--sheet-id")) -> None:
    _moved_notice("dfs sheets protect", "dfs setup protect")
    sheets_protect(sheet_id=sheet_id)


@sheets_app.command("doctor")
def sheets_doctor_alias(sheet_id: str = typer.Option(None, "--sheet-id")) -> None:
    _moved_notice("dfs sheets doctor", "dfs doctor")
    sheets_doctor(sheet_id=sheet_id)


@sheets_app.command("audit-style")
def sheets_audit_style_alias(sheet_id: str = typer.Option(None, "--sheet-id")) -> None:
    _moved_notice("dfs sheets audit-style", "dfs setup audit-style")
    sheets_audit_style(sheet_id=sheet_id)


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

    live_client: SheetsClient | None = None
    if no_upload:
        console.print("[dim]--no-upload: fetching and caching locally only, no Sheets contact.[/dim]")
    else:
        live_client = SheetsClient(cfg.google_sheets)
        try:
            title, url = live_client.describe()
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

    # A6 (2026-09-22): drain any pending add-a-player name into the
    # accumulated list before it can be overwritten by a second typed
    # name -- see `sheet_pool_control.drain_control_cell_into_added_names`.
    # A live-sheet step, so skipped under --no-upload; failure here
    # shouldn't fail an otherwise-successful sync.
    if not no_upload:
        try:
            drain_result = drain_control_cell_into_added_names(
                SheetsClient(cfg.google_sheets), cfg.lineups.player_pool_tab
            )
            console.print(f"[green]OK[/green] {drain_result}")
        except SheetsError as e:
            console.print(f"[yellow]Could not check the add-a-player control cell:[/yellow] {e}")

    if live:
        _print_live_flag_diff(
            old_edge, client=live_client, edge_tab=cfg.google_sheets.tab_mappings.get("edge", "EdgeRaw")
        )

    if any_failed:
        raise typer.Exit(code=1)


def _print_live_flag_diff(
    old_edge: pd.DataFrame | None, client: SheetsClient | None = None, edge_tab: str = "EdgeRaw"
) -> None:
    """Called only from `sync --live`/`dfs go`, after `run_sync` --
    compares EdgeRaw's Flag column from right before this sync
    (`old_edge`, read before `run_sync` ran) to right after, and prints
    the difference. `store.load_previous`/`diff_odds`'s per-snapshot
    pattern isn't reused here because "current" already means "the state
    this sync just replaced" for `old_edge`, captured before the write
    happens -- no need to reach back into raw snapshot history for it.

    Phase 6, Part 3: when `client` is given (omitted under `--no-upload`,
    where nothing should touch the live sheet), also populates the
    Board's Queue section via `write_queue_section` -- this diff used to
    only ever reach the terminal.
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
    else:
        table = Table(title="What changed since the last sync")
        table.add_column("Name")
        table.add_column("Pos")
        table.add_column("Team")
        table.add_column("Old Flag")
        table.add_column("New Flag")
        for _, row in changes.iterrows():
            table.add_row(
                row["Name"], row["Position"], row["Team"], row["OldFlag"] or "-", row["NewFlag"] or "-"
            )
        console.print(table)

    if client is not None:
        queue_changes = diff_queue_changes(old_edge, new_edge)
        try:
            result = write_queue_section(client, queue_changes, edge_tab)
            console.print(f"[green]OK[/green] {result}")
        except SheetsError as e:
            console.print(f"[yellow]Could not update the Board's Queue section:[/yellow] {e}")


@app.command(short_help="Sync, check the sheet, and report what changed -- in one go.")
def go() -> None:
    """Sync, check the sheet, and report what changed -- the three
    commands you'd otherwise run back to back every time anyway. Stops at
    the first failure (a failed sync means nothing to check; a failed
    doctor means don't trust what changed until it's fixed)."""
    cfg = _load_config_or_exit()
    try:
        old_edge = store.load_current("edge")
    except FileNotFoundError:
        old_edge = None

    console.print("[bold]-- sync --[/bold]")
    sync(only=None, no_upload=False, week=None, season=None, live=False)

    console.print("\n[bold]-- doctor --[/bold]")
    sheets_doctor(sheet_id=None)

    console.print("\n[bold]-- what changed --[/bold]")
    _print_live_flag_diff(
        old_edge,
        client=SheetsClient(cfg.google_sheets),
        edge_tab=cfg.google_sheets.tab_mappings.get("edge", "EdgeRaw"),
    )


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

    basis = df["OwnStatus"].iloc[0] if len(df) else "?"
    console.print(f"Ownership status: [bold]{basis}[/bold] (real Own% until TFFB computes it midweek)\n")

    table = Table(title="Top leverage plays")
    columns = (
        "Name",
        "Position",
        "Team",
        "Opp",
        "Salary",
        "ProjPts",
        "Own%",
        "Leverage",
        "GameEnv",
        "Flags",
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
            # Phase 6, Part 2: Own% is now a true fraction (0.146), not a
            # raw percentage-as-number (14.6) -- :.1% does the *100 for
            # display, matching the sheet's own PERCENT-format rendering.
            f"{r['Own%']:.1%}",
            f"{r['Leverage']:.1f}" if pd.notna(r["Leverage"]) else "-",
            f"{r['GameEnv']:.1f}" if pd.notna(r["GameEnv"]) else "-",
            # Part 7.9: "Flags" is every matching condition, space-separated
            # -- "Flag" (singular) is now hidden, top-priority-only.
            r["Flags"] or "",
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
    columns, DK Upload) so the sheet's ready for a new week.

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
        # Each block IS the 9 roster rows now (ROSTER_SLOTS order) --
        # the salary-total row directly below it is no longer part of the
        # block at all (Fix 2.4) -- see weekly_reset.py's
        # LINEUPS_NAME_BLOCKS docstring.
        offset = start - 2  # `raw` starts at row 2
        names = [_sheet_cell(raw, offset + i) for i in range(len(ROSTER_SLOTS))]
        if not any(n.strip() for n in names):
            continue  # lineup not built yet

        statuses = lineup_slot_status(names, edge, now=now)
        if all(s.locked is True for s in statuses):
            continue  # every slot found and locked -- nothing left to decide

        any_shown = True
        table = Table(title=f"Lineup {lineup_number}")
        for col in ("Slot", "Name", "Status", "ProjPts", "Leverage", "Flags"):
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


@week_app.command("new", short_help="Point config.toml at a new weeks sheet copy and sync.")
def week_new(
    sheet_url: str = typer.Argument(
        ..., help="URL (or bare ID) of this week's sheet, already copied from the template."
    ),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation prompt."),
) -> None:
    """Move config.toml to a new week's sheet copy: check the new sheet's
    structure (`dfs doctor`), carry the bankroll and Results log
    forward, clear last week's lineups, and run a full sync -- in that
    order, with one confirmation before anything is written.

    Carrying the bankroll forward means reading the CURRENT sheet's Ending
    balance for each of the three tracked bankrolls (main/DK, PP, UD) and
    writing it as the NEW sheet's Starting balance -- see
    dfs.week.BANKROLL_CARRYOVER_CELLS and docs/planning/ROADMAP.md's Phase 4 section
    for how those cell addresses were found. Everything else on the
    Bankroll tab (weekly budget formulas, Deposited/Withdrawn) is either
    formula-driven and naturally resets, or a running total the user
    updates by hand -- neither needs code here.

    Bankroll's own CONTEST rows (Cash/GPP entry ledgers) are cleared too --
    see `weekly_reset.clear_previous_week`'s `bankroll_*` params -- but only
    AFTER the carryover read above, and only the ledgers' typed columns
    (never the "% Paid"/"Place %" formula columns, and never the Starting/
    Ending balance cells `BANKROLL_CARRYOVER_CELLS` already carried
    forward). Reversed order would read a zeroed Ending balance off a
    sheet whose contest rows were already wiped and carry that forward
    instead -- this function's own call sequence (carryover, THEN
    `clear_previous_week`) is what keeps that from happening; there is no
    separate guard enforcing it.

    `EntriesRaw`/`GPPin`/`DKLineupsRaw`/`DKLineupsFinal` (the hand-paste
    chain this superseded) and `Scratch` are removed entirely as of Part
    4b (2026-09-22, see CONTRIBUTING.md's changelog) -- nothing left here
    to clear for any of the five.

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
            f"`dfs doctor --sheet-id {new_sheet_id}` for the same report on its own."
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
            dk_upload_tab=cfg.lineups.upload_tab,
            bankroll_tab=cfg.bankroll.tab,
            bankroll_cash=cfg.bankroll.cash,
            bankroll_gpp=cfg.bankroll.gpp,
        )
    except SheetsError as e:
        console.print(f"[red]Sheets error:[/red] {e}")
        raise typer.Exit(code=1) from e
    for line in summary:
        console.print(f"[green]OK[/green] {line}")

    # Fix 2.14: blank every synced source's tab BEFORE the first sync
    # runs, not after -- a source that fails partway through must leave
    # an empty tab, never the template's own stale (real-looking, but
    # wrong) leftover data. See weekly_reset.clear_synced_tabs.
    try:
        cleared = clear_synced_tabs(new_client, cfg.google_sheets.tab_mappings, list(SOURCES))
    except SheetsError as e:
        console.print(f"[red]Sheets error:[/red] {e}")
        raise typer.Exit(code=1) from e
    for line in cleared:
        console.print(f"[green]OK[/green] {line}")

    # Same reasoning as the sheet-side clear above, applied to the LOCAL
    # cache `run_sync` actually reads from (`store.load_current`): found
    # live, a stale `data/current/<source>.csv` left over from a previous
    # week silently broke `edge`'s projections<->salaries ID join (each
    # cache was internally valid, just for different weeks) with no
    # error -- `build_edge_frame` doesn't know "this data is old", only
    # "this data is what's there." Clearing it here means a source that
    # fails to sync under the new week reads as no-data-yet, never a
    # previous week's now-mismatched numbers.
    removed = store.clear_current()
    if removed:
        console.print(f"[green]OK[/green] cleared {len(removed)} local synced-data cache(s): {removed}")

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


@week_app.command("close", short_help="Reconcile bankroll from DK contest history.")
def week_close(
    csv: Path = typer.Option(
        ..., "--csv", help="Path to a DK contest-history CSV export (My Contests > export)."
    ),
) -> None:
    """End-of-week bankroll reconciliation -- currently a thin wrapper over
    `dfs bankroll sync --csv`; see below for why it isn't more than that yet.

    Investigated for this command: whether the authenticated browser
    profile `dfs auth dk` already saves could pull contest history
    directly, skipping the manual CSV export. It can, technically -- a
    real per-account export URL was found live (2026-09-23, Sam
    present, inspecting the real button) -- but Sam decided against
    automating requests against his own real-money DK account
    (`dfs auth dk`'s session already triggered DK's own bot/geo
    detection once during that same investigation), so this stays a
    manual export deliberately, not because the technical path doesn't
    exist. Revisit only if Sam explicitly asks to reconsider that
    tradeoff -- this is his account, his call.
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
    """Classify contest entries into Cash/GPP and append new ones to the
    bankroll tab, then auto-fill Results (Week, Cash Pts, H2H Entered/Win --
    Fix 2.16/2.17) from the same export, sorted into NFL weeks by each
    entry's own contest date rather than assuming the file is one week's
    worth. Cash Line and the team-colour columns in Results stay yours.

    Exporting contest history by hand (My Contests > export) and passing
    it here stays the supported path -- see `week_close`'s own docstring
    for why an automated fetch was investigated and deliberately not
    built.
    """
    cfg = _load_config_or_exit()
    _sync_bankroll_from_csv(cfg, csv)


@bankroll_app.command("backfill-keys")
def bankroll_backfill_keys(
    csv: Path = typer.Option(
        ..., "--csv", help="Path to a DK contest-history CSV export (My Contests > export)."
    ),
    sheet_id: str = typer.Option(
        None,
        "--sheet-id",
        help="Repair a different sheet instead of config.toml's -- e.g. a past week's sheet, "
        "since this is a one-time repair for rows that predate the dedupe key, not a standing "
        "per-week command.",
    ),
) -> None:
    """One-time repair: fills in the dedupe-key column for existing
    Bankroll rows that don't have one -- confirmed live (2026-09-16) that
    rows written before this key existed, or by some path that skipped
    it, are invisible to `sync_bucket`'s dedupe check, so a later sync of
    overlapping contest history re-appends them as duplicates. Matches
    each keyless row to exactly one CSV entry by its Place/Entries/
    Entry Fee/Prize Pool/Places Paid (fields that round-trip exactly
    through Sheets' own display formatting); a row matching zero or more
    than one entry is left alone and reported, never guessed. Never
    touches a row's own A-H data or an already-populated key. Safe to
    run against a CSV that also contains entries already fully synced --
    only blank key cells are ever written.
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

    gs_cfg = cfg.google_sheets.model_copy(update={"sheet_id": sheet_id}) if sheet_id else cfg.google_sheets
    client = SheetsClient(gs_cfg)
    try:
        title, url = client.describe()
        console.print(f"Backfilling dedupe keys in: [bold]{title}[/bold]\n{url}\n")
        cash_result = backfill_entry_keys(client, cfg.bankroll.tab, cfg.bankroll.cash, entries, "cash")
        gpp_result = backfill_entry_keys(client, cfg.bankroll.tab, cfg.bankroll.gpp, entries, "gpp")
    except SheetsError as e:
        console.print(f"[red]Sheets error:[/red] {e}")
        raise typer.Exit(code=1) from e

    for result in (cash_result, gpp_result):
        console.print(
            f"[bold]{result.bucket}[/bold]: backfilled {len(result.backfilled)} row(s), "
            f"{len(result.ambiguous_rows)} ambiguous, {len(result.unmatched_rows)} unmatched"
        )
        if result.ambiguous_rows:
            console.print(f"  ambiguous rows (multiple CSV matches, left alone): {result.ambiguous_rows}")
        if result.unmatched_rows:
            console.print(f"  unmatched rows (no CSV match, left alone): {result.unmatched_rows}")


@ownership_app.command("log")
def ownership_log(
    csv: Path = typer.Option(
        ...,
        "--csv",
        help="Path to a DK 'export full standings' CSV, downloaded from a real "
        "contest's results page (a per-contest export, not the account-level contest-history one).",
    ),
    contest_id: str = typer.Option(
        None,
        "--contest-id",
        help="Defaults to the CSV filename's own contest ID (DK names these "
        "contest-standings-<id>.csv) -- pass this only if the file's been renamed.",
    ),
    week: int = typer.Option(None, "--week", help="Defaults to the current NFL week."),
    season: int = typer.Option(None, "--season", help="Defaults to the current season."),
) -> None:
    """Logs one contest's actual per-player ownership into the durable
    local ownership log (`data/ownership_log.csv`) for later calibration
    against archived `ProjOwn` (Phase 6, Part 7.8). File-based on
    purpose, not automated -- see `ownership.py`'s module docstring for
    why an automated per-contest fetch was investigated and deliberately
    not shipped. Safe to re-run against the same file: replaces that
    contest's rows rather than duplicating them.

    This export has no date of its own (unlike the account-level contest-
    history export `dfs bankroll sync` reads), so week/season can't be
    inferred from the file -- pass `--week` explicitly for anything other
    than the current week.
    """
    if not csv.exists():
        console.print(f"[red]No such file:[/red] {csv}")
        raise typer.Exit(code=1)

    resolved_contest_id = contest_id
    if resolved_contest_id is None:
        match = re.search(r"(\d+)", csv.stem)
        if not match:
            console.print(
                f"[red]Could not find a contest ID in the filename {csv.name!r}[/red] -- pass "
                "--contest-id explicitly."
            )
            raise typer.Exit(code=1)
        resolved_contest_id = match.group(1)

    df = pd.read_csv(csv)
    try:
        rows = parse_ownership_export(df)
    except KeyError as e:
        console.print(f"[red]CSV is missing an expected column:[/red] {e}")
        raise typer.Exit(code=1) from e

    resolved_week = week if week is not None else nfl_calendar.current_week()
    resolved_season = season if season is not None else nfl_calendar.current_season()

    written = append_ownership(
        rows, season=resolved_season, week=resolved_week, contest_id=resolved_contest_id
    )
    console.print(
        f"[green]OK[/green] contest {resolved_contest_id}, week {resolved_week}: logged {written} "
        f"player(s) ({int(rows['contest_entries'].iloc[0])} entries in the field)."
    )


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

    console.print(f"Parsed {len(entries)} entries from {csv}.")

    # Fix 2.18 (found live 2026-09-22): the Bankroll cash/GPP ledger ranges
    # are cleared fresh every week by `dfs week new` (weekly_reset.
    # clear_previous_week), and each week lives on its own spreadsheet --
    # so an earlier week's entries are never in the new sheet's dedupe-key
    # column and `sync_bucket`'s dedupe alone can't tell them apart from
    # this week's. Narrow to this week's entries before appending to the
    # ledger; `compute_week_results` below still uses the full, unfiltered
    # `entries` for its season-long Results backfill -- see
    # bankroll.entries_for_week's docstring for why those two need
    # different scopes.
    season = nfl_calendar.current_season()
    week = nfl_calendar.current_week()
    ledger_entries = entries_for_week(entries, week, season)
    if len(ledger_entries) != len(entries):
        console.print(
            f"Week {week}: {len(ledger_entries)} of {len(entries)} entries belong to this week's "
            "ledger -- the rest are earlier weeks, already recorded on their own sheets, and are "
            "skipped here (but still included in the Results backfill below)."
        )

    cash_entries = [e for e in ledger_entries if classify_entry(e) == "cash"]
    gpp_entries = [e for e in ledger_entries if classify_entry(e) == "gpp"]
    console.print(f"This week: {len(cash_entries)} cash, {len(gpp_entries)} GPP.")

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

    # Fix 2.16/2.17: auto-fill Results (Week, Cash Pts, H2H Entered/Win)
    # from the same parsed entries, grouped by the NFL week each entry's
    # own contest date falls into -- not by assuming this CSV is one
    # week's worth. A season-long export backfills every past week it
    # has real data for in one pass; Cash Line and the team-colour
    # columns are never touched (see results_autofill.write_results_updates).
    week_results = compute_week_results(entries, nfl_calendar.current_season())
    try:
        written_weeks = write_results_updates(client, cfg.results, week_results)
    except SheetsError as e:
        console.print(f"[red]Could not write {cfg.results.tab!r}:[/red] {e}")
        raise typer.Exit(code=1) from e
    if written_weeks:
        weeks_str = ", ".join(str(w) for w in written_weeks)
        console.print(f"\n[green]OK[/green] updated {cfg.results.tab!r} for week(s): {weeks_str}")

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


def _pool_client_and_edge_tab() -> tuple[SheetsClient, str]:
    cfg = _load_config_or_exit()
    edge_tab = cfg.google_sheets.tab_mappings.get("edge")
    if not edge_tab:
        console.print("[red]No tab mapped for 'edge' in config.toml.[/red]")
        raise typer.Exit(code=1)
    return SheetsClient(cfg.google_sheets), edge_tab


def _print_candidates(query: str, candidates: list) -> None:
    console.print(f"[yellow]Multiple matches for {query!r} -- not guessing:[/yellow]")
    table = Table()
    table.add_column("Name")
    table.add_column("Pos")
    table.add_column("Salary")
    for c in candidates:
        table.add_row(c.name, c.position, c.salary)
    console.print(table)


@pool_app.command("add")
def pool_add(names: list[str] = typer.Argument(..., help="Player name(s) or substrings to add.")) -> None:
    """Set EdgeRaw's Pool column to "Both" for each name given, by exact
    match if one exists, else by substring -- multiple matches are
    printed (name, position, salary) and skipped rather than guessed at.
    Refine to Cash-only or GPP-only afterward in the sheet itself; the CLI
    doesn't have a flag for that yet."""
    client, edge_tab = _pool_client_and_edge_tab()
    try:
        players = read_players(client, edge_tab)
        for query in names:
            matches = find_matches(players, query)
            if not matches:
                console.print(f"[red]No match for {query!r}[/red]")
            elif len(matches) > 1:
                _print_candidates(query, matches)
            else:
                player = matches[0]
                set_pool(client, edge_tab, player, "Both")
                console.print(f"[green]OK[/green] added {player.name} ({player.position}, {player.salary})")
    except SheetsError as e:
        console.print(f"[red]Sheets error:[/red] {e}")
        raise typer.Exit(code=1) from e


@pool_app.command("remove")
def pool_remove(
    names: list[str] = typer.Argument(..., help="Player name(s) or substrings to remove."),
) -> None:
    """Clear EdgeRaw's Pool column for each name given -- same matching
    rules as `dfs pool add`."""
    client, edge_tab = _pool_client_and_edge_tab()
    try:
        players = read_players(client, edge_tab)
        for query in names:
            matches = find_matches(players, query)
            if not matches:
                console.print(f"[red]No match for {query!r}[/red]")
            elif len(matches) > 1:
                _print_candidates(query, matches)
            else:
                player = matches[0]
                set_pool(client, edge_tab, player, "")
                console.print(f"[green]OK[/green] removed {player.name}")
    except SheetsError as e:
        console.print(f"[red]Sheets error:[/red] {e}")
        raise typer.Exit(code=1) from e


@pool_app.command("list")
def pool_list() -> None:
    """Every currently-pooled EdgeRaw player, grouped by position."""
    client, edge_tab = _pool_client_and_edge_tab()
    try:
        players = read_players(client, edge_tab)
    except SheetsError as e:
        console.print(f"[red]Sheets error:[/red] {e}")
        raise typer.Exit(code=1) from e

    ticked = [p for p in players if p.pooled]
    if not ticked:
        console.print("[yellow]Pool is empty.[/yellow]")
        return

    by_position: dict[str, list] = {}
    for p in ticked:
        by_position.setdefault(p.position, []).append(p)

    for position in sorted(by_position):
        picks = by_position[position]
        console.print(f"\n[bold]{position}[/bold] ({len(picks)})")
        for p in picks:
            console.print(f"  {p.name} ({p.salary})")


@pool_app.command("clear")
def pool_clear(
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation prompt."),
) -> None:
    """Clear every currently-pooled EdgeRaw player. Confirms first
    unless `--yes` is given -- this touches every pooled row at once."""
    client, edge_tab = _pool_client_and_edge_tab()
    try:
        players = read_players(client, edge_tab)
        ticked = [p for p in players if p.pooled]
        if not ticked:
            console.print("[yellow]Pool is already empty.[/yellow]")
            return
        if not yes:
            confirmed = typer.confirm(f"Untick all {len(ticked)} pooled player(s)?")
            if not confirmed:
                console.print("Cancelled.")
                raise typer.Exit(code=0)
        count = clear_all(client, edge_tab, players)
    except SheetsError as e:
        console.print(f"[red]Sheets error:[/red] {e}")
        raise typer.Exit(code=1) from e
    console.print(f"[green]OK[/green] cleared {count} player(s)")


if __name__ == "__main__":
    app()
