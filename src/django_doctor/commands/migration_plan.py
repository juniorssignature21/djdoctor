"""``djdoctor migration-plan`` — read-only analysis of pending migration work."""

from __future__ import annotations

import typer

from django_doctor.commands._common import get_state, handle_errors
from django_doctor.commands._migrations import preflight
from django_doctor.exit_codes import ExitCode
from django_doctor.output.formatters import print_json, render_plan


def register(app: typer.Typer) -> None:
    @app.command("migration-plan")
    @handle_errors
    def migration_plan(
        ctx: typer.Context,
        app_label: str = typer.Argument(None, help="Plan migrating only this app."),
        migration_name: str = typer.Argument(None, help="Target migration (e.g. 0003 or 'zero')."),
        database: str = typer.Option(None, "--database", help="Database alias."),
        json_output: bool = typer.Option(False, "--json", help="Machine-readable output."),
        check: bool = typer.Option(False, "--check", help="Exit with code 4 unless everything is up to date (CI)."),
    ) -> None:
        """Show model changes, pending migrations, risks and a safe strategy. Changes nothing."""
        state = get_state(ctx)
        c = state.console
        if json_output:
            c.verbosity = type(c.verbosity).QUIET
        pre = preflight(state, check_database=False, run_checks=False, app_label=app_label,
                        migration_name=migration_name, database=database, announce=False)
        plan = pre.plan
        if json_output:
            print_json(c, plan.to_dict())
        else:
            render_plan(c, plan, title="Migration Plan" if not app_label else f"Migration Plan — {app_label}")
            c.print("")
            c.print("[muted]No changes have been applied.[/muted]")
        if plan.blocking_problems:
            raise typer.Exit(int(ExitCode.MIGRATION_PROBLEM))
        if check and not plan.is_up_to_date:
            raise typer.Exit(int(ExitCode.MIGRATION_PROBLEM))
