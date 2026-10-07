"""`dfs model ...`: fetch the public history, train and back-test the UM model, inspect the shipped artifacts.

Registered (hidden, one line) in `dfs.cli`; unhide it by dropping `hidden=True` below once the local session
has wired the model into the sheet and added the command to the launcher and `docs/COMMANDS.md`, which the
tests require for any visible command. Heavy imports (scikit-learn, pandas) stay inside the commands so
`dfs --help` does not pay for them.

No command here touches a sheet, and none needs credentials.
"""

from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console
from rich.markup import escape
from rich.table import Table

from dfs.prompt_missing import PromptingGroup

model_app = typer.Typer(
    help="The UM player-outcome model: fetch history, train, back-test, inspect.",
    cls=PromptingGroup,
    hidden=True,
)
console = Console()


def _say(message: str) -> None:
    console.print(f"[dim]{escape(message)}[/dim]")


def _fail(message: str) -> typer.Exit:
    console.print(f"[red]FAIL[/red] {escape(message)}")
    return typer.Exit(1)


def _load_history_or_exit():
    from dfs.model import data
    from dfs.model.history import load_history
    from dfs.model.predict import available_seasons
    from dfs.nfl_calendar import current_season

    seasons = available_seasons(current_season())
    if not seasons:
        raise _fail("no history cached -- run `dfs model fetch`")
    try:
        return load_history(seasons)
    except data.ModelDataError as e:
        raise _fail(str(e)) from e


@model_app.command("fetch")
def model_fetch(
    refresh_all: bool = typer.Option(
        False, "--refresh-all", help="Re-download completed seasons too (they never change)."
    ),
) -> None:
    """Download or refresh the public nflverse / ffopportunity history into data/model_cache/.

    Completed seasons are fetched once; the season in progress and the schedule are refreshed every time.
    """
    from dfs.model import data
    from dfs.nfl_calendar import current_season

    through = current_season()
    try:
        done = data.fetch_history(through, refresh_all=refresh_all, log=_say)
    except data.ModelDataError as e:
        raise _fail(str(e)) from e
    cached = sorted({int(p.stem.rsplit("_", 1)[1]) for p in data.CACHE_DIR.glob("stats_player_*.parquet")})
    console.print(
        f"[green]OK[/green] cache through {through}: seasons {cached[0]}-{cached[-1]}; "
        f"downloaded {len(done)} completed season(s) this run"
    )


@model_app.command("train")
def model_train(
    through: int = typer.Option(
        None, "--through", help="Last season to train on (default: the last complete season in the cache)."
    ),
    out_dir: Path = typer.Option(None, "--out-dir", help="Where to write artifacts (default: models/um/)."),
) -> None:
    """Rebuild the artifacts: holdout comparison, shipping decision, shipped models, distribution tables and
    backtest.md. Run it once a season, or on demand; it needs `dfs model fetch` first."""
    from dfs.model.artifacts import MAX_ARTIFACT_BYTES, ArtifactError
    from dfs.model.distribution import ARTIFACT_DIR
    from dfs.model.pipeline import run_training, write_run
    from dfs.model.report import render_backtest

    history, join_report = _load_history_or_exit()
    run = run_training(history, join_report, through, log=_say)
    directory = out_dir or ARTIFACT_DIR
    sizes = write_run(run, directory, render_backtest(run))
    _print_holdout(run)
    total = sizes["TOTAL"]
    console.print(
        f"[green]OK[/green] wrote {directory}: " + ", ".join(f"{k} {v:,} B" for k, v in sizes.items())
    )
    if total > MAX_ARTIFACT_BYTES:
        raise _fail(
            str(
                ArtifactError(
                    f"artifacts total {total:,} bytes, over the {MAX_ARTIFACT_BYTES:,} limit -- "
                    "reduce max_iter in train.HGB_PARAMS"
                )
            )
        )


def _print_holdout(run) -> None:
    from dfs.model.history import POSITIONS

    table = Table(
        title=f"Holdout {run.holdout[0]}-{run.holdout[-1]} (MAE in DK points; rho = weekly Spearman)"
    )
    for column in (
        "Pos",
        "Rows",
        "MAE L8",
        "MAE blend",
        "MAE GBM",
        "rho L8",
        "rho blend",
        "rho GBM",
        "Shipped",
    ):
        table.add_column(column, justify="left" if column in ("Pos", "Shipped") else "right")
    for position in POSITIONS:
        r = run.results[position]
        m = r.metrics
        table.add_row(
            position,
            f"{r.n_holdout:,}",
            f"{m['mae_l8']:.3f}",
            f"{m['mae_blend']:.3f}",
            f"{m['mae_gbm']:.3f}",
            f"{m['rho_l8']:.3f}",
            f"{m['rho_blend']:.3f}",
            f"{m['rho_gbm']:.3f}",
            r.method,
        )
    console.print(table)

    cal = Table(title="Distribution calibration (out-of-fold)")
    for column in ("Pos", "Below q20", "Above q85", "Coverage ok", "Max reliability gap", "Bins beyond +/-4"):
        cal.add_column(column, justify="left" if column == "Pos" else "right")
    from dfs.model import evaluate
    from dfs.model.report import band

    for position in POSITIONS:
        c = run.coverage[position]
        s = evaluate.summarize(position, c, run.reliability[position])
        cal.add_row(
            position,
            band(c["below_q20"]),
            band(c["above_q85"]),
            "yes" if c["ok"] else "NO",
            f"{s['max_abs_gap_pts']:.1f} pts",
            f"{s['bins_over_tol']}/{s['bins']}",
        )
    console.print(cal)


@model_app.command("backtest")
def model_backtest(
    saved: bool = typer.Option(
        False, "--saved", help="Print the shipped backtest.md instead of recomputing."
    ),
) -> None:
    """Print the holdout and calibration tables. By default this recomputes them from the cache (about half
    a minute) without writing anything; `--saved` prints the report that shipped with the artifacts."""
    if saved:
        from dfs.model.distribution import ARTIFACT_DIR

        path = ARTIFACT_DIR / "backtest.md"
        if not path.exists():
            raise _fail(f"{path} not found -- run `dfs model train`")
        console.print(path.read_text(), markup=False, highlight=False)
        return
    from dfs.model.pipeline import run_training

    history, join_report = _load_history_or_exit()
    _print_holdout(run_training(history, join_report, log=_say))


@model_app.command("info")
def model_info() -> None:
    """Show the shipped artifacts: version, training seasons, chosen method per position, holdout metrics
    and the scikit-learn version they were built with (flagging a mismatch)."""
    import sklearn

    from dfs.model.artifacts import ArtifactError, check_compatible, read_metadata
    from dfs.model.distribution import ARTIFACT_DIR

    try:
        meta = read_metadata()
    except ArtifactError as e:
        raise _fail(str(e)) from e
    console.print(f"artifacts: {ARTIFACT_DIR}")
    console.print(f"version {meta['artifact_version']}, trained {meta['trained_on']}")
    console.print(
        f"training seasons {meta['train_seasons'][0]}-{meta['train_seasons'][1]}, "
        f"holdout {meta['holdout_seasons'][0]}-{meta['holdout_seasons'][1]}"
    )
    console.print(f"scikit-learn: trained with {meta['sklearn_version']}, installed {sklearn.__version__}")
    table = Table()
    for column in ("Pos", "Method", "Holdout rows", "MAE", "rho"):
        table.add_column(column, justify="left" if column in ("Pos", "Method") else "right")
    for position, info in meta["positions"].items():
        m = info["holdout_metrics"]
        key = "gbm" if info["method"] == "gbm" else "blend"
        table.add_row(
            position,
            info["method"],
            f"{info['n_holdout']:,}",
            f"{m['mae_' + key]:.3f}",
            f"{m['rho_' + key]:.3f}",
        )
    console.print(table)
    try:
        check_compatible(meta)
    except ArtifactError as e:
        raise _fail(str(e)) from e
    console.print("[green]OK[/green] artifacts are compatible with this install")
