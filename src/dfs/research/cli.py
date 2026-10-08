"""`dfs research ...`: fetch the extra public datasets and rebuild the research outputs from the cache.

Registered (hidden, one line) in `dfs.cli`, like `dfs model`. Nothing here touches a sheet or needs
credentials. Heavy imports (pandas, scikit-learn) stay inside the commands so `dfs --help` does not pay
for them.
"""

from __future__ import annotations

import typer
from rich.console import Console
from rich.markup import escape

from dfs.prompt_missing import PromptingGroup

research_app = typer.Typer(
    help="Research pack: measure the Edge Finder's hand-set constants against public history.",
    cls=PromptingGroup,
    hidden=True,
)
console = Console()


def _say(message: str) -> None:
    console.print(f"[dim]{escape(message)}[/dim]")


def _fail(message: str) -> typer.Exit:
    console.print(f"[red]FAIL[/red] {escape(message)}")
    return typer.Exit(1)


@research_app.command("fetch")
def research_fetch(
    refresh: bool = typer.Option(False, "--refresh", help="Re-download seasons already cached."),
) -> None:
    """Download the 2014-2025 public history the studies read (player and team stats, ffopportunity, the
    schedule, injury reports and a reduced play-by-play) into data/model_cache/ and data/research_cache/."""
    from dfs.research import data

    try:
        data.fetch_all(refresh=refresh, log=_say)
    except data.ResearchDataError as e:
        raise _fail(str(e)) from e
    console.print("[green]OK[/green] research cache is current through 2025")


@research_app.command("run")
def research_run(
    study: str = typer.Option("all", "--study", help="r1, r2, r3, r4, r5 or all."),
) -> None:
    """Rebuild the study outputs under models/research/ from the cache (no network).

    r1 redistribution of a missing starter's volume, r2 matchup weights, r3 signal thresholds, r4 weather,
    r5 quick checks (short week, divisional, home / away, back-to-back road).
    """
    from dfs.research import data, pipeline

    try:
        pipeline.resolve(study)
    except ValueError as e:
        raise _fail(str(e)) from e
    try:
        written = pipeline.run_studies(study, log=_say)
    except data.ResearchDataError as e:
        raise _fail(str(e)) from e
    for path in written:
        console.print(f"[green]OK[/green] wrote {path}")
