"""``djdoctor collectstatic`` — collectstatic with configuration checks."""

from __future__ import annotations

from pathlib import Path

import typer

from django_doctor.bridge.manage import run_captured
from django_doctor.commands._common import (
    explain_failure_text,
    fail_with_probe_error,
    get_state,
    handle_errors,
    save_last_error,
)
from django_doctor.commands._migrations import confirm_destructive
from django_doctor.exceptions import ConfigurationError, UnsafeOperationError
from django_doctor.exit_codes import ExitCode


def register(app: typer.Typer) -> None:
    @app.command("collectstatic")
    @handle_errors
    def collectstatic(
        ctx: typer.Context,
        yes: bool = typer.Option(False, "--yes", "-y", help="Don't ask for confirmation."),
        dry_run: bool = typer.Option(False, "--dry-run", help="Show what would be copied."),
        clear: bool = typer.Option(False, "--clear", help="Delete existing files in STATIC_ROOT first (destructive)."),
        allow_destructive: bool = typer.Option(False, "--allow-destructive", help="Confirm --clear non-interactively."),
    ) -> None:
        """Collect static files into STATIC_ROOT."""
        state = get_state(ctx)
        c = state.console
        if yes:
            c.assume_yes = True
        project = state.project()
        info = state.runner().run("info")
        if not info.ok:
            fail_with_probe_error(state, info, headline="Settings could not be loaded.")
        static = info.data.get("static") or {}
        root = static.get("root")
        if not root:
            raise ConfigurationError(
                "STATIC_ROOT is not set, so collectstatic has nowhere to copy files.",
                hint="Add STATIC_ROOT = BASE_DIR / 'staticfiles' to your settings.",
            )
        target = Path(root) if Path(root).is_absolute() else project.root / root
        if target.resolve() == project.root.resolve() or project.root.resolve().is_relative_to(target.resolve()):
            raise ConfigurationError(f"STATIC_ROOT ({target}) is the project directory or one of its parents; refusing to write there.")
        c.info(f"Target: {target}")
        if clear:
            c.warning(f"--clear deletes every file currently in {target}.")
            confirm_destructive(state, allow_destructive=allow_destructive, what="delete files in STATIC_ROOT")
        elif not dry_run and not c.confirm(f"Copy static files to {target}?", default=True):
            raise UnsafeOperationError("Aborted. Use --yes to confirm non-interactively.")
        args = ["collectstatic", "--noinput"] + (["--dry-run"] if dry_run else []) + (["--clear"] if clear else [])
        result = run_captured(project, args)
        if not result.ok:
            c.error("collectstatic failed")
            save_last_error(state, result.captured_error)
            explain_failure_text(state, result.captured_error or result.stderr)
            raise typer.Exit(int(ExitCode.CONFIGURATION_ERROR))
        summary = [line for line in result.stdout.splitlines() if line.strip()]
        c.success(summary[-1] if summary else "Static files collected")
