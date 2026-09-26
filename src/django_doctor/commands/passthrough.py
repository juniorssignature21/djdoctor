"""``djdoctor django <command> ...`` — run any Django management command.

Output is streamed unchanged; if the command fails, its error is explained.
Commands known to destroy data require an explicit confirmation first.
"""

from __future__ import annotations

import typer

from django_doctor.branding import CLI_NAME
from django_doctor.bridge.manage import TracebackRecorder, run_streaming
from django_doctor.commands._common import (
    explain_failure_text,
    get_state,
    handle_errors,
    save_last_error,
)
from django_doctor.commands._migrations import confirm_destructive

#: management command -> what it destroys
DESTRUCTIVE_COMMANDS = {
    "flush": "delete ALL data from the database",
    "reset_db": "drop and recreate the whole database",
    "reset_schema": "drop the database schema",
    "sqlflush": None,  # only prints SQL
}

_TIPS = {
    "migrate": f"Tip: `{CLI_NAME} migrate` checks for destructive operations before applying.",
    "makemigrations": f"Tip: `{CLI_NAME} makemigrations` reviews risky changes first.",
    "runserver": f"Tip: `{CLI_NAME} start` adds startup diagnostics and error explanations.",
}


def register(app: typer.Typer) -> None:
    @app.command(
        "django",
        context_settings={"allow_extra_args": True, "ignore_unknown_options": True, "allow_interspersed_args": False},
    )
    @handle_errors
    def django(
        ctx: typer.Context,
        command: str = typer.Argument(..., help="Django management command, e.g. showmigrations."),
        allow_destructive: bool = typer.Option(False, "--allow-destructive",
                                               help="Confirm destructive commands (flush, reset_db) non-interactively."),
    ) -> None:
        """Run any `manage.py` command, e.g. `djdoctor django showmigrations`.

        Put djdoctor options before the command name; everything after it goes to Django."""
        state = get_state(ctx)
        c = state.console
        project = state.project()
        if DESTRUCTIVE_COMMANDS.get(command):
            c.warning(f"`{command}` will {DESTRUCTIVE_COMMANDS[command]}.")
            confirm_destructive(state, allow_destructive=allow_destructive, what=f"run `{command}`")
        if command in _TIPS and not c.quiet:
            c.print(f"[muted]{_TIPS[command]}[/muted]")
        recorder = TracebackRecorder()
        result = run_streaming(project, [command, *ctx.args], recorder=recorder)
        if result.ok or result.interrupted:
            raise typer.Exit(result.returncode if result.returncode >= 0 else 0)
        text = result.captured_error
        save_last_error(state, text)
        c.print("")
        explain_failure_text(state, text)
        raise typer.Exit(result.returncode if result.returncode > 0 else 1)
