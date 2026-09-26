"""``djdoctor migrate`` — safe migration manager."""

from __future__ import annotations

from pathlib import Path

import typer
from rich.markup import escape

from django_doctor.branding import CLI_NAME
from django_doctor.commands._common import get_state, handle_errors
from django_doctor.commands._migrations import (
    confirm_destructive,
    load_plan,
    preflight,
    report_command_failure,
    show_model_changes,
    stop_on_blocking_problems,
    warn_destructive,
)
from django_doctor.exit_codes import ExitCode
from django_doctor.migration.backup import backup_sqlite, sqlite_database_path
from django_doctor.migration.executor import apply_migrations, create_migrations
from django_doctor.output.formatters import render_operation
from django_doctor.state import AppState


def _looks_like_production(info: dict, database: str | None) -> list[str]:
    reasons = []
    if not info.get("debug"):
        reasons.append("DEBUG is False")
    db = (info.get("databases") or {}).get(database or "default") or {}
    host = (db.get("host") or "").lower()
    if host and host not in ("localhost", "127.0.0.1", "::1", "db", "postgres", "mysql", "database"):
        reasons.append(f"database host is '{db.get('host')}'")
    return reasons


def register(app: typer.Typer) -> None:
    @app.command("migrate")
    @handle_errors
    def migrate(
        ctx: typer.Context,
        app_label: str = typer.Argument(None, help="Only migrate this app."),
        migration_name: str = typer.Argument(None, help="Target migration (use 'zero' to unapply everything)."),
        dry_run: bool = typer.Option(False, "--dry-run", help="Show what would happen without changing anything."),
        yes: bool = typer.Option(False, "--yes", "-y", help="Answer yes to ordinary prompts (NOT destructive ones)."),
        allow_destructive: bool = typer.Option(False, "--allow-destructive",
                                               help="Explicitly allow operations that may delete data (after review)."),
        no_makemigrations: bool = typer.Option(False, "--no-makemigrations", help="Only apply existing migrations."),
        no_backup: bool = typer.Option(False, "--no-backup", help="Skip the automatic SQLite backup before destructive operations."),
        skip_checks: bool = typer.Option(False, "--skip-checks", help="Skip Django system checks."),
        database: str = typer.Option(None, "--database", help="Database alias (default: 'default')."),
    ) -> None:
        """Detect model changes, create migrations and apply them — safely."""
        state = get_state(ctx)
        c = state.console
        if yes:
            c.assume_yes = True
        c.title("Migration Manager")
        pre = preflight(state, run_checks=not skip_checks, app_label=app_label,
                        migration_name=migration_name, database=database)
        plan = pre.plan
        stop_on_blocking_problems(state, plan)
        confirmed_destructive = False

        # ---------------------------------------------------- 1. create
        if plan.changes and not no_makemigrations and not migration_name:
            show_model_changes(state, plan)
            if plan.unresolved_questions:
                c.print("")
                for q in plan.unresolved_questions:
                    c.warning(f"{q['model']}.{q['field']} needs a value for existing rows (no default).")
                for s in plan.strategies:
                    if "without a default" in s.title or "NOT NULL" in s.title or "needs a value" in s.title:
                        c.print(f"  [bold]{escape(s.title)}[/bold]")
                        for step in s.steps:
                            c.print(f"    - {escape(step)}")
                c.print("")
                c.print("Django cannot create this migration non-interactively. Add a default, or run:")
                c.command(f"{CLI_NAME} makemigrations --interactive")
                raise typer.Exit(int(ExitCode.MIGRATION_PROBLEM))
            destructive = [op for ch in plan.changes for op in ch.operations if op.destructive]
            if destructive or plan.rename_candidates:
                warn_destructive(state, destructive, plan)
                if dry_run:
                    _dry_run_footer(state)
                    raise typer.Exit(int(ExitCode.UNSAFE_OPERATION))
                confirm_destructive(state, allow_destructive=allow_destructive, what="create and apply this migration")
                confirmed_destructive = True
            if dry_run:
                c.print("")
                c.info("Dry run: migrations would be created for: " + ", ".join(sorted({ch.app for ch in plan.changes})))
            else:
                c.print("")
                c.out("Creating migration...")
                created = create_migrations(state.project(), pre.info, apps=sorted({ch.app for ch in plan.changes}))
                if not created.ok:
                    report_command_failure(state, created.result.captured_error or created.result.stderr,
                                           "makemigrations failed")
                    raise typer.Exit(int(ExitCode.MIGRATION_PROBLEM))
                for f in created.new_files:
                    c.success(_rel(state, f), indent=0)
                plan = load_plan(state, app_label=app_label, migration_name=migration_name, database=database, fresh=True)
                stop_on_blocking_problems(state, plan)
        elif plan.changes and no_makemigrations:
            c.warning(f"{sum(len(ch.operations) for ch in plan.changes)} model change(s) have no migration "
                      "(--no-makemigrations given).")

        # ------------------------------------------------------ 2. apply
        pending = plan.pending
        if not pending and not (dry_run and plan.changes):
            c.print("")
            c.success("No migrations to apply.")
            _verify(state, plan, app_label, migration_name, database, dry_run=dry_run)
            return

        destructive = [op for m in pending for op in m.operations if op.destructive]
        if destructive and not confirmed_destructive:
            warn_destructive(state, destructive, plan)
            if dry_run:
                _dry_run_footer(state)
                raise typer.Exit(int(ExitCode.UNSAFE_OPERATION))
            confirm_destructive(state, allow_destructive=allow_destructive, what="apply this migration")
            confirmed_destructive = True

        prod = _looks_like_production(pre.info, database)
        if prod and pending:
            c.warning("This may not be a development database (" + ", ".join(prod) + ").")
            if not dry_run and not c.confirm("Apply migrations to this database?", default=False):
                c.error("Aborted. Nothing was applied. Use --yes to confirm non-interactively.")
                raise typer.Exit(int(ExitCode.UNSAFE_OPERATION))

        if dry_run:
            c.print("")
            c.print(f"[heading]Would apply {len(pending)} migration(s):[/heading]")
            for m in pending:
                c.print(f"  {'↶ ' if m.backwards else ''}{escape(m.label)}")
                for op in m.operations:
                    render_operation(c, op, indent=4)
            _dry_run_footer(state)
            return

        if destructive and not no_backup:
            db_path = sqlite_database_path(state.project(), pre.info, database or "default")
            if db_path is not None:
                backup = backup_sqlite(state.project(), db_path)
                c.success(f"Backup created: {_rel(state, backup)}")
            else:
                c.warning("Take a database backup before continuing: Django Doctor only backs up SQLite automatically.")
                if not c.confirm("Have you backed up the database?", default=False) and not allow_destructive:
                    c.error("Aborted. Nothing was applied.")
                    raise typer.Exit(int(ExitCode.UNSAFE_OPERATION))

        c.print("")
        c.out("Applying migrations...")
        result = apply_migrations(state.project(), app_label=app_label, migration_name=migration_name,
                                  database=database, stream=False)
        for name in result.applied:
            c.detail(f"{c.symbols['ok']} {name}", indent=2, style="ok")
        if not result.ok:
            text = result.result.captured_error or (result.result.stdout + result.result.stderr)
            where = f" while applying {result.failed_on}" if result.failed_on else ""
            report_command_failure(state, text, f"Migration failed{where}")
            if result.applied:
                c.print("")
                c.warning(f"{len(result.applied)} migration(s) were applied before the failure and remain applied.")
            c.print("[muted]Django runs each migration in a transaction on PostgreSQL and SQLite; on MySQL/Oracle a "
                    "failed migration may be partially applied.[/muted]")
            raise typer.Exit(int(ExitCode.MIGRATION_PROBLEM))
        c.success(f"{len(result.applied)} migration(s) applied")
        _verify(state, plan, app_label, migration_name, database)


def _verify(state: AppState, plan, app_label, migration_name, database, *, dry_run: bool = False) -> None:
    c = state.console
    if dry_run:
        _dry_run_footer(state)
        return
    after = load_plan(state, app_label=app_label, migration_name=migration_name, database=database, fresh=True)
    remaining = [m for m in after.pending if not m.backwards] if not migration_name else after.pending
    problems = []
    if remaining:
        problems.append(f"{len(remaining)} migration(s) still unapplied")
    if after.missing_tables or after.missing_columns:
        problems.append("the database schema does not match the models")
    if problems:
        c.warning("Verification: " + "; ".join(problems) + f". Run `{CLI_NAME} migration-plan`.")
        raise typer.Exit(int(ExitCode.MIGRATION_PROBLEM))
    if after.changes and not migration_name:
        c.warning(f"{sum(len(ch.operations) for ch in after.changes)} model change(s) still have no migration.")
    c.success("Migration state verified")
    c.print("")
    c.print("[ok]Migration completed successfully.[/ok]")


def _dry_run_footer(state: AppState) -> None:
    state.console.print("")
    state.console.print("[muted]Dry run: no files were created and the database was not changed.[/muted]")


def _rel(state: AppState, path: Path) -> str:
    try:
        return str(path.relative_to(state.project().root))
    except ValueError:
        return str(path)
