"""Ask for a required argument instead of erroring -- for the launcher menu AND for typed commands.

`dfs week close` with no `--csv` used to stop with "Missing option '--csv'". In a real terminal it now asks
("DraftKings contest-history CSV ...") and carries on; with no terminal (a script, cron, CI, a pipe) it fails
exactly as before, so nothing automated changes. A blank answer cancels, and the usual error is shown.

Wiring: every `typer.Typer` in cli.py is created with `cls=PromptingGroup`. When a group resolves a leaf
command, `install` wraps that command's `parse_args`: it runs the normal parse, and on click's
MissingParameter asks for that one parameter (wording and defaults from `launcher.prompt_spec`, so the menu
and typed commands ask identically), appends the answer to the arguments, and parses again. Only a MISSING
parameter is intercepted; a wrong value (a bad choice, a bad number) still errors normally, and `--help`
never prompts. Tests drive it through `CliRunner`, which has no terminal, so they see the old behaviour.
"""

from __future__ import annotations

import sys
from typing import Any

import typer
from typer.core import TyperGroup

from dfs.launcher import MissingParam, answer_to_args, prompt_spec


def interactive() -> bool:
    """True only when both stdin and stdout are a real terminal (so a pipe or cron job never blocks)."""
    return sys.stdin.isatty() and sys.stdout.isatty()


def ask(param: MissingParam) -> str | None:
    """Prompt for one parameter; None when the answer is blank (cancel) or Ctrl-C."""
    hint = " (Enter to cancel)" if param.default is None else ""
    try:
        answer = typer.prompt(
            f"{param.question}{hint}", default=param.default or "", show_default=param.default is not None
        )
    except (typer.Abort, KeyboardInterrupt):
        return None
    return answer.strip() or None


def install(command: Any) -> None:
    """Wrap a leaf click command's `parse_args` so a missing required parameter is asked for (idempotent)."""
    if getattr(command, "_dfs_prompts_for_missing", False):
        return
    original = command.parse_args

    def parse_args(ctx: Any, args: list[str]) -> list[str]:
        extra: list[str] = []
        asked: set[str] = set()
        while True:
            try:
                return original(ctx, [*args, *extra])
            except Exception as e:  # click's own MissingParameter (Typer bundles its own click)
                param = getattr(e, "param", None)
                if type(e).__name__ != "MissingParameter" or param is None or not interactive():
                    raise
                if param.name in asked:
                    raise  # asked once already and still not satisfied: show the real error
                asked.add(param.name)
                spec = prompt_spec(f"dfs {' '.join(ctx.command_path.split()[1:])}", param)
                answer = ask(spec)
                if answer is None:
                    raise
                extra += answer_to_args(spec, answer)

    command.parse_args = parse_args
    command._dfs_prompts_for_missing = True


class PromptingGroup(TyperGroup):
    """A Typer group whose leaf commands ask for missing required parameters (see module docstring)."""

    def resolve_command(self, ctx: Any, args: list[str]):
        name, command, rest = super().resolve_command(ctx, args)
        if command is not None and not hasattr(command, "commands"):
            install(command)
        return name, command, rest
