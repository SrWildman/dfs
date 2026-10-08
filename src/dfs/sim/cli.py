"""`dfs sim ...`: fit the correlations, back-test the simulator, run a worked example.

Registered hidden in `dfs.cli` (one line) until the local session wires the simulator into the sheet and adds
the commands to the launcher's `MORE_LABELS`, `commands_doc.SECTIONS` and the generated `docs/COMMANDS.md`.
Heavy imports stay inside the commands so `dfs --help` does not pay for them. No command here touches a sheet,
and none needs credentials -- only the cache `dfs model fetch` fills.
"""

from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console
from rich.markup import escape

from dfs.prompt_missing import PromptingGroup

sim_app = typer.Typer(
    help="Lineup simulator: fit player correlations, back-test, worked example.",
    cls=PromptingGroup,
    hidden=True,
)
console = Console()


def _say(message: str) -> None:
    console.print(f"[dim]{escape(message)}[/dim]")


def _fail(message: str) -> typer.Exit:
    console.print(f"[red]FAIL[/red] {escape(message)}")
    return typer.Exit(1)


def _load_frame_or_exit():
    from dfs.model.data import ModelDataError
    from dfs.sim.pipeline import load_frame

    try:
        return load_frame(log=_say)
    except ModelDataError as e:
        raise _fail(str(e)) from e


@sim_app.command("fit")
def sim_fit(
    n_boot: int = typer.Option(1000, "--n-boot", help="Bootstrap resamples for each interval."),
    seed: int = typer.Option(0, "--seed", help="Seed for every random draw."),
    out_dir: Path = typer.Option(None, "--out-dir", help="Where to write (default: models/sim/)."),
) -> None:
    """Rebuild models/sim/correlations.csv from the cached history (about half a minute)."""
    from dfs.sim import report
    from dfs.sim.correlation import SIM_DIR, fit_correlations, save_fit

    frame = _load_frame_or_exit()
    _say(f"fitting {len(frame):,} out-of-fold player-games")
    fit = fit_correlations(frame, seed=seed, n_boot=n_boot)
    checks = report.fit_checks(fit, seed)
    path = save_fit(fit, out_dir or SIM_DIR, extra_meta=checks)
    console.print(report.fit_summary(fit, checks), markup=False, highlight=False, soft_wrap=True)
    console.print(f"[green]OK[/green] wrote {path} ({len(fit.shipped)} rows)")


@sim_app.command("backtest")
def sim_backtest(
    lineups_per_week: int = typer.Option(200, "--lineups-per-week", help="Random legal lineups per week."),
    n_sims: int = typer.Option(10000, "--n-sims", help="Simulations per lineup."),
    seed: int = typer.Option(0, "--seed", help="Seed for every random draw."),
    jobs: int = typer.Option(1, "--jobs", help="Worker processes (the result does not depend on it)."),
    save: bool = typer.Option(False, "--save", help="Also write the report to models/sim/backtest.md."),
    results_path: Path = typer.Option(None, "--results", help="Also write the per-lineup results (parquet)."),
    from_results: Path = typer.Option(
        None, "--from-results", help="Print the report for saved per-lineup results instead of re-simulating."
    ),
) -> None:
    """Back-test on 2023-2025: random legal lineups, correlations fitted on 2014-2022 only (about 30 minutes
    at the defaults; lower --lineups-per-week for a quick look)."""
    import pandas as pd

    from dfs.sim import report
    from dfs.sim.backtest import run_backtest
    from dfs.sim.correlation import SIM_DIR

    if from_results:
        results = pd.read_parquet(from_results)
    else:
        frame = _load_frame_or_exit()
        results = run_backtest(
            frame, lineups_per_week=lineups_per_week, n_sims=n_sims, seed=seed, n_jobs=jobs, log=_say
        )
    if results_path:
        results.to_parquet(results_path)
    text = report.backtest_report(results)
    console.print(text, markup=False, highlight=False, soft_wrap=True)
    if save:
        path = SIM_DIR / "backtest.md"
        path.write_text(text)
        console.print(f"[green]OK[/green] wrote {path}")


@sim_app.command("demo")
def sim_demo(
    cash_line: float = typer.Option(None, "--cash-line", help="Cash line in points (default 145)."),
    gpp_target: float = typer.Option(None, "--gpp-target", help="GPP target in points (default 190)."),
) -> None:
    """Two lineups from 2025 week 12 -- one stacked around Patrick Mahomes, one not -- and the portfolio."""
    from dfs.sim import report
    from dfs.sim.simulate import DEFAULT_CASH_LINE, DEFAULT_GPP_TARGET

    frame = _load_frame_or_exit()
    text = report.demo_report(
        frame,
        cash_line=DEFAULT_CASH_LINE if cash_line is None else cash_line,
        gpp_target=DEFAULT_GPP_TARGET if gpp_target is None else gpp_target,
    )
    console.print(text, markup=False, highlight=False, soft_wrap=True)
