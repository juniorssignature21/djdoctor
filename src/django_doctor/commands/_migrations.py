"""Shared flow for ``migrate``, ``makemigrations`` and ``migration-plan``."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import typer
from rich.markup import escape

from django_doctor.branding import CLI_NAME
from django_doctor.commands._common import (
    explain_failure_text,
    fail_with_probe_error,
    save_last_error,
)
from django_doctor.diagnostics.check_kb import advice_for
from django_doctor.exceptions import UnsafeOperationError
from django_doctor.exit_codes import ExitCode
from django_doctor.explain.engine import explain_probe_error
from django_doctor.migration.planner import MigrationPlan, build_plan
from django_doctor.migration.safety import OperationAssessment
from django_doctor.output.formatters import render_diagnosis, render_operation
from django_doctor.state import AppState


@dataclass
class Preflight:
    info: dict[str, Any]
    plan: MigrationPlan
    db_ok: bool
    db_missing_file: bool


def preflight(
    state: AppState,
    *,
    check_database: bool = True,
    run_checks: bool = True,
    app_label: str | None = None,
    migration_name: str | None = None,
    database: str | None = None,
    announce: bool = True,
) -> Preflight:
    c = state.console
    project = state.project()
    runner = state.runner()
    if announce:
        c.success("Django project detected" + (f" ({project.root.name})" if c.verbose else ""))

    info_result = runner.run("info")
    if not info_result.ok:
        fail_with_probe_error(state, info_result, headline="Django could not be loaded.")
    info = info_result.data
    if announce:
        c.success(f"Django {info['django']['version']} detected")
        c.success(f"Settings loaded ({info.get('settings_module')})")

    db_ok, db_missing = True, False
    if check_database:
        db_result = runner.run("database")
        if not db_result.ok:
            fail_with_probe_error(state, db_result, headline="Database connections could not be tested.")
        entry = db_result.data.get("databases", {}).get(database or "default", {})
        if entry.get("missing_file"):
            db_missing = True
            if announce:
                c.info("SQLite database file does not exist yet — it will be created")
        elif entry.get("ok"):
            if announce:
                c.success("Database connection successful")
        else:
            db_ok = False
            c.error("Database connection failed")
            error = entry.get("error") or {}
            if error:
                render_diagnosis(c, explain_probe_error(error, _ctx(state)))
            raise typer.Exit(int(ExitCode.DATABASE_ERROR))

    if run_checks:
        checks = runner.run("checks", {"deploy": False})
        if not checks.ok:
            fail_with_probe_error(state, checks, headline="Django's system checks crashed.")
        messages = [m for m in checks.data.get("messages", []) if not m.get("silenced")]
        errors = [m for m in messages if m["level"] >= 40]
        if errors:
            c.error(f"System checks found {len(errors)} error(s)")
            for m in errors:
                c.print(f"    [err]{escape(m.get('id') or '')}[/err] {escape(m.get('obj') or '')}: {escape(m['msg'])}")
                advice = advice_for(m.get("id"))
                if advice:
                    c.print(f"      [muted]{escape(advice.fix)}[/muted]")
            c.print("")
            c.print(f"Fix these first (see `{CLI_NAME} check`). Django refuses to migrate while they exist.")
            raise typer.Exit(int(ExitCode.CONFIGURATION_ERROR))
        if announce:
            warnings = [m for m in messages if m["level"] >= 30]
            c.success("System checks passed" + (f" ({len(warnings)} warning(s))" if warnings else ""))

    plan = load_plan(state, app_label=app_label, migration_name=migration_name, database=database)
    return Preflight(info, plan, db_ok, db_missing)


def load_plan(state: AppState, *, app_label: str | None = None, migration_name: str | None = None,
              database: str | None = None, fresh: bool = False) -> MigrationPlan:
    args = {"schema": True, "app_label": app_label, "migration_name": migration_name, "database": database}
    result = state.runner().run("migrations", args, use_cache=not fresh)
    if not result.ok:
        fail_with_probe_error(state, result, headline="The migration state could not be analysed.",
                              exit_code=ExitCode.MIGRATION_PROBLEM)
    return build_plan(result.data)


def _ctx(state: AppState):
    from django_doctor.commands._common import project_context

    return project_context(state)


def stop_on_blocking_problems(state: AppState, plan: MigrationPlan) -> None:
    c = state.console
    if not plan.blocking_problems:
        return
    for problem in plan.blocking_problems:
        c.error(problem)
    error = plan.graph_error or plan.inconsistent_history or plan.autodetect_error
    if error:
        render_diagnosis(c, explain_probe_error(error, _ctx(state)))
    elif plan.conflicts:
        c.print("")
        c.print("Create a merge migration after reviewing both branches:")
        c.command(f"{CLI_NAME} django makemigrations --merge")
    raise typer.Exit(int(ExitCode.MIGRATION_PROBLEM))


def show_model_changes(state: AppState, plan: MigrationPlan) -> None:
    c = state.console
    c.print("")
    c.print("[heading]Model changes detected:[/heading]")
    by_model: dict[tuple[str, str], list[OperationAssessment]] = {}
    for change in plan.changes:
        for op in change.operations:
            by_model.setdefault((change.app, op.model or "?"), []).append(op)
    for (app, model), ops in by_model.items():
        c.print(f"  {escape(app)}.{escape(model)}")
        for op in ops:
            render_operation(c, op, indent=4)


def warn_destructive(state: AppState, ops: list[OperationAssessment], plan: MigrationPlan) -> None:
    c = state.console
    c.print("")
    c.print("[err]⚠ Potentially destructive migration[/err]")
    c.print("")
    c.print("Detected:")
    for op in ops:
        c.print(f"  [warn]{escape(op.describe)}[/warn]" + (f" [muted]({escape(op.app)})[/muted]" if op.app else ""))
        for reason in op.reasons:
            c.print(f"    [muted]{escape(reason)}[/muted]")
        if op.data_at_risk:
            c.print(f"    [warn]Data at risk: {escape(op.data_at_risk)}[/warn]")
    if plan.rename_candidates:
        c.print("")
        for r in plan.rename_candidates:
            if r.kind == "field":
                c.print(f"  [warn]Ambiguous:[/warn] {escape(r.model)}.{escape(r.old)} removed and "
                        f"{escape(r.model)}.{escape(r.new)} added. Django Doctor does not assume they hold the same data.")
    c.print("")
    c.print("This operation may permanently remove existing database data.")
    if plan.strategies:
        c.print("")
        c.print("[heading]Recommended strategy:[/heading]")
        for s in plan.strategies[:3]:
            c.print(f"  [bold]{escape(s.title)}[/bold]")
            n = 0
            for step in s.steps:
                if step.startswith("  "):
                    c.print(f"      {escape(step.strip())}")
                else:
                    n += 1
                    c.print(f"    {n}. {escape(step)}")


def confirm_destructive(state: AppState, *, allow_destructive: bool, what: str) -> None:
    """Raise UnsafeOperationError unless the user explicitly accepted data loss."""
    c = state.console
    if allow_destructive:
        c.print("")
        c.warning("--allow-destructive given: continuing with destructive operations.")
        return
    c.print("")
    if c.interactive:
        if c.confirm_destructive(f"Django Doctor will not {what} automatically. Review the migration before continuing."):
            return
        raise UnsafeOperationError("Aborted: destructive operations were not confirmed. Nothing was changed.")
    raise UnsafeOperationError(
        f"Django Doctor will not {what} automatically.",
        detail="Review the migration before continuing. Nothing was changed.",
        hint=(f"After reviewing, re-run with --allow-destructive (e.g. `{CLI_NAME} migrate --allow-destructive`), "
              f"or run `{CLI_NAME} migration-plan` for a safe strategy. --yes does not confirm destructive operations."),
    )


def report_command_failure(state: AppState, text: str | None, headline: str) -> None:
    c = state.console
    c.error(headline)
    save_last_error(state, text)
    if text:
        diagnosis = explain_failure_text(state, text)
        if diagnosis is None:
            c.print("")
            c.raw(text[-3000:])
