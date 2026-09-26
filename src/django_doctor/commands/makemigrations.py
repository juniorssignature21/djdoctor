"""``djdoctor makemigrations`` — create migrations with a safety review."""

from __future__ import annotations

import typer
from rich.markup import escape

from django_doctor.branding import CLI_NAME
from django_doctor.commands._common import get_state, handle_errors
from django_doctor.commands._migrations import (
    confirm_destructive,
    preflight,
    report_command_failure,
    show_model_changes,
    stop_on_blocking_problems,
    warn_destructive,
)
from django_doctor.exit_codes import ExitCode
from django_doctor.migration.executor import create_migrations


def register(app: typer.Typer) -> None:
    @app.command("makemigrations")
    @handle_errors
    def makemigrations(
        ctx: typer.Context,
        apps: list[str] = typer.Argument(None, help="Only these app labels."),
        name: str = typer.Option(None, "--name", "-n", help="Name for the migration file."),
        dry_run: bool = typer.Option(False, "--dry-run", help="Show what would be created without writing files."),
        check: bool = typer.Option(False, "--check", help="Exit with code 4 if model changes are missing migrations (CI)."),
        interactive: bool = typer.Option(False, "--interactive", "-i",
                                         help="Hand over to Django's interactive makemigrations (rename questions, one-off defaults)."),
        allow_destructive: bool = typer.Option(False, "--allow-destructive", help="Allow creating migrations that remove data."),
    ) -> None:
        """Create migrations for model changes, showing risky operations first."""
        state = get_state(ctx)
        c = state.console
        c.title("makemigrations")
        pre = preflight(state, check_database=False, run_checks=not check)
        plan = pre.plan
        stop_on_blocking_problems(state, plan)
        changes = [ch for ch in plan.changes if not apps or ch.app in apps]
        for app_info in plan.unmigrated_apps:
            if apps and app_info["label"] in apps:
                c.info(f"App '{app_info['label']}' has no migrations yet; an initial migration will be created.")

        if not changes and not any(a["label"] in (apps or []) for a in plan.unmigrated_apps):
            c.success("No changes detected.")
            return
        plan.changes = changes
        show_model_changes(state, plan)
        if check:
            c.print("")
            c.error("Model changes are missing migrations.")
            c.command(f"{CLI_NAME} makemigrations")
            raise typer.Exit(int(ExitCode.MIGRATION_PROBLEM))

        if interactive:
            c.print("")
            c.info("Handing over to Django's interactive makemigrations...")
            result = create_migrations(state.project(), pre.info, apps=apps or None, name=name, interactive=True)
            for f in result.new_files:
                c.success(f"Created {f.relative_to(state.project().root)}")
            raise typer.Exit(0 if result.ok else int(ExitCode.MIGRATION_PROBLEM))

        if plan.unresolved_questions:
            c.print("")
            for q in plan.unresolved_questions:
                c.warning(f"{q['model']}.{q['field']} needs a value for existing rows (no default).")
            c.print("Add a default to the field, or answer Django's questions interactively:")
            c.command(f"{CLI_NAME} makemigrations --interactive")
            raise typer.Exit(int(ExitCode.MIGRATION_PROBLEM))

        destructive = [op for ch in changes for op in ch.operations if op.destructive]
        if destructive or plan.rename_candidates:
            warn_destructive(state, destructive, plan)
            if dry_run:
                raise typer.Exit(int(ExitCode.UNSAFE_OPERATION))
            confirm_destructive(state, allow_destructive=allow_destructive, what="create this migration")

        if dry_run:
            c.print("")
            for ch in changes:
                c.info(f"Would create {escape(ch.app)}/migrations/{escape(ch.proposed_name)}.py")
            c.print("[muted]Dry run: no files were written.[/muted]")
            return
        c.print("")
        c.out("Creating migration...")
        result = create_migrations(state.project(), pre.info, apps=apps or sorted({ch.app for ch in changes}), name=name)
        if not result.ok:
            report_command_failure(state, result.result.captured_error or result.result.stderr, "makemigrations failed")
            raise typer.Exit(int(ExitCode.MIGRATION_PROBLEM))
        for f in result.new_files:
            c.success(str(f.relative_to(state.project().root)))
        c.print("")
        c.print(f"Review the file(s), then apply with: [cmd]{CLI_NAME} migrate[/cmd]")
