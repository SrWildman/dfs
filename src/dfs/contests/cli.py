"""`dfs contests ...` -- hidden while the engine is unfinished (the repo's convention for that: `hidden=True`,
like `dfs model`; it is not in `docs/COMMANDS.md` or the launcher until it is wired into `week close`)."""

from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console
from rich.markup import escape

from dfs.contests import series as series_mod
from dfs.contests import slate_index
from dfs.paths import CONTESTS_DIR
from dfs.prompt_missing import PromptingGroup

contests_app = typer.Typer(
    help="Learn which contests suit which lineups, and how your fields build theirs.",
    cls=PromptingGroup,
    hidden=True,
)
console = Console()


@contests_app.command("analyze")
def analyze(
    history_files: list[Path] = typer.Option(
        ...,
        "--history",
        help="A DraftKings My Contests -> History export (CSV). Repeat for several; they are merged "
        "into the durable log, deduplicated by Entry_Key.",
    ),
    standings_dir: Path = typer.Option(
        None,
        "--standings",
        help="A folder of contest-standings-<id>.csv files and/or zips of them (searched recursively).",
    ),
    out: Path = typer.Option(CONTESTS_DIR, "--out", help="Where the log and the CSVs are written."),
    me: str = typer.Option(series_mod.MY_USERNAME, "--me", help="Your DraftKings username."),
    players: Path = typer.Option(
        None,
        "--players",
        help="CSV of players: Name, Team, Position, Opponent and optionally Salary (EdgeRaw has them), "
        "with Season and Week columns to cover several weeks. Without it each week's players come from "
        "the last DraftKings salary snapshot before that week's lock (data/raw/draftkings).",
    ),
    index_candidate: str = typer.Option(
        slate_index.SELECTED,
        "--slate-index",
        help="Which slate-scoring index is shown beside each week's fitted effect, and is the 'index' "
        f"comparator: {', '.join(slate_index.CANDIDATES)}, or 'none' to leave it out.",
    ),
    index_file: Path = typer.Option(
        None, "--slate-index-file", help="A season,week,index CSV to use instead of computing the index."
    ),
) -> None:
    """Merges your DraftKings history into a durable log, tags every contest with its series and format,
    scores how tough each series is (and your head-to-head record), and, given a folder of standings files,
    measures how the fields build their lineups. Writes CSVs under --out and prints a short summary; it never
    touches a sheet and never contacts DraftKings."""
    # Imported here, not at module level: the group is registered on every `dfs` start, and this is an
    # unfinished command that must not add import cost or a failure point to the standard week.
    from dfs.contests import analysis, history, week_frames
    from dfs.paths import RAW_DIR

    if index_candidate != analysis.NO_INDEX and index_candidate not in slate_index.CANDIDATES:
        console.print(f"[red]Unknown --slate-index {escape(index_candidate)}[/red]")
        raise typer.Exit(code=2)
    for path in [
        *history_files,
        *([standings_dir] if standings_dir else []),
        *([players] if players else []),
    ]:
        if not path.exists():
            console.print(f"[red]No such file or folder:[/red] {escape(str(path))}")
            raise typer.Exit(code=1)
    try:
        result = analysis.run(
            history_files,
            out,
            standings_dir=standings_dir,
            players=players,
            me=me,
            index_candidate=index_candidate,
            index_file=index_file,
            frames=lambda season, week: week_frames.week_frame(RAW_DIR / "draftkings", season, week),
        )
    except (history.HistoryError, ValueError) as e:
        console.print(f"[red]FAIL[/red] {escape(str(e))}")
        raise typer.Exit(code=1) from e
    written = analysis.write(result)
    for line in analysis.summary_lines(result):
        console.print(escape(line))
    console.print(f"[green]OK[/green] wrote {len(written)} file(s) under {escape(str(out))}")
