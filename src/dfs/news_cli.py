"""`dfs news ...`: the news change log (`news_log`, `news_store`). Files only: no sheet, no network."""

from __future__ import annotations

from datetime import datetime

import typer
from rich.console import Console
from rich.markup import escape
from rich.table import Table

from dfs import news_log, news_store
from dfs.prompt_missing import PromptingGroup

news_app = typer.Typer(
    help="The news change log: what changed between syncs (status, projections, ownership, lines) and when.",
    cls=PromptingGroup,
)
console = Console()

ALL_FIELDS = "all"
FIELD_NAMES = tuple(spec.name for spec in news_log.FIELDS)


def _local(at: str) -> str:
    """The log's UTC `at` as Eastern wall-clock text ("Sun 11:57 am")."""
    when = datetime.strptime(at, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=news_log.UTC)
    return when.astimezone(news_log.EASTERN).strftime("%a %I:%M %p").replace(" 0", " ").lower().capitalize()


def parse_fields(text: str) -> tuple[str, ...]:
    """`--field`'s comma-separated names as a tuple (empty = every field); an unknown name is an error."""
    names = tuple(part.strip().lower() for part in text.split(",") if part.strip())
    if ALL_FIELDS in names:
        return ()
    unknown = [name for name in names if name not in FIELD_NAMES]
    if unknown or not names:
        raise ValueError(
            f"unknown field(s) {', '.join(unknown) or '(none given)'}: use {', '.join(FIELD_NAMES)} or all"
        )
    return names


@news_app.command("backfill")
def backfill() -> None:
    """Rebuild the news change log (`data/news/changes.csv`) from every saved EdgeRaw snapshot
    (`data/raw/edge/`): each snapshot against the one before it, one row per changed field. Append-only and
    safe to run twice: a snapshot already in the log is skipped, so a second run adds nothing. `dfs sync`,
    `dfs sync --live` and `dfs lineups late-swap --refresh-injuries` keep the log current by themselves; run
    this once to load the season so far, or after a sync warned that the log was not updated. Reads and
    writes files only: no sheet."""
    try:
        added = news_store.backfill()
    except OSError as e:
        console.print(f"[red]{escape(str(e))}[/red]")
        raise typer.Exit(code=1) from e
    for (season, week), rows in sorted(added.items()):
        console.print(f"  {season} Week {week}: {rows:,} row(s) added", highlight=False)
    total = sum(added.values())
    console.print(
        f"[green]OK[/green] {total:,} row(s) added to {news_store.changes_path()}"
        if total
        else f"[green]OK[/green] nothing to add: {news_store.changes_path()} already holds every snapshot"
    )


@news_app.command("changes")
def changes(
    week: int = typer.Option(None, "--week", help="The NFL week (default: the current one)."),
    season: int = typer.Option(None, "--season", help="The season (default: the current one)."),
    field: str = typer.Option(
        news_log.STATUS,
        "--field",
        help="Which fields, comma-separated: " + ", ".join(FIELD_NAMES) + f", or {ALL_FIELDS}.",
    ),
    player: str = typer.Option(None, "--player", help="Only names containing this text."),
) -> None:
    """Print a week's rows from the news change log, oldest first: when each change was seen (Eastern; the
    time of the sync that saw it, not of the news itself), who, the field, and the value before and after.
    Status changes by default. Read-only: it reads `data/news/changes.csv` and nothing else."""
    from dfs import nfl_calendar

    season = season or nfl_calendar.current_season()
    week = week or nfl_calendar.current_week()
    try:
        fields = parse_fields(field)
    except ValueError as e:
        console.print(f"[red]{escape(str(e))}[/red]")
        raise typer.Exit(code=1) from e
    rows = news_store.week_changes(season, week, fields=fields)
    if player:
        rows = rows[rows["name"].str.contains(player, case=False, regex=False)]
    if rows.empty:
        console.print(
            f"No changes logged for {season} Week {week} ({field}). "
            "`dfs news backfill` loads the log from the saved snapshots."
        )
        return
    table = Table(title=f"{season} Week {week}: {len(rows):,} change(s), {field}")
    for column in ("seen (ET)", "player", "pos", "team", "field", "before", "after", "source"):
        table.add_column(column)
    for row in rows.itertuples(index=False):
        table.add_row(
            _local(row.at),
            escape(row.name),
            row.position,
            row.team,
            row.field,
            row.before or "-",
            row.after or "-",
            row.source,
        )
    console.print(table)
