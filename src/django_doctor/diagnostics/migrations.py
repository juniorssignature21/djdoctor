"""Migration state checks, built on the same plan as ``djdoctor migration-plan``."""

from __future__ import annotations

from collections.abc import Iterator

from django_doctor.branding import CLI_NAME
from django_doctor.diagnostics.context import DoctorContext
from django_doctor.diagnostics.models import CheckResult, Status
from django_doctor.migration.planner import build_plan
from django_doctor.security import redact_text


def check_migrations(ctx: DoctorContext) -> Iterator[CheckResult]:
    if not ctx.settings_loaded:
        yield CheckResult("migrations.skipped", "Migrations", Status.SKIPPED, "Skipped: settings could not be loaded")
        return
    result = ctx.probe("migrations", {"schema": True})
    if not result.ok:
        yield CheckResult("migrations.error", "Migrations", Status.ERROR, "Migration state could not be analysed",
                          [redact_text(result.error_message)], diagnosis=ctx.explain(result))
        return
    plan = build_plan(result.data)

    if plan.graph_error:
        from django_doctor.explain.engine import explain_probe_error

        yield CheckResult("migrations.graph", "Migrations", Status.ERROR, "The migration graph is broken",
                          [redact_text(plan.graph_error.get("message", ""))],
                          diagnosis=explain_probe_error(plan.graph_error, ctx.explain_context))
        return
    for app, names in plan.conflicts.items():
        yield CheckResult("migrations.conflict", "Migrations", Status.ERROR,
                          f"Conflicting migrations in '{app}' (multiple leaf nodes)", names,
                          hint="Create a merge migration after reviewing both branches.",
                          commands=[f"{CLI_NAME} django makemigrations --merge"])
    if plan.inconsistent_history:
        yield CheckResult("migrations.inconsistent", "Migrations", Status.ERROR, "Inconsistent migration history",
                          [plan.inconsistent_history.get("message", "")],
                          hint=f"Run `{CLI_NAME} explain` on this message for repair options; do not delete migrations.")
    if plan.db_error:
        yield CheckResult("migrations.db", "Migrations", Status.WARNING,
                          "Applied migrations could not be read from the database (see Database)")

    forward = plan.pending_forward()
    if forward and not plan.db_error:
        details = [m.label + (f"  ⚠ {m.risk.label} risk" if m.risk.rank >= 2 else "") for m in forward[:15]]
        if plan.db_missing_file:
            title = f"Database not created yet: {len(forward)} migration(s) will be applied"
        else:
            title = f"{len(forward)} migration(s) have not been applied"
        yield CheckResult("migrations.pending", "Migrations", Status.WARNING, title, details,
                          commands=[f"{CLI_NAME} migration-plan", f"{CLI_NAME} migrate"])

    if plan.changes:
        details = []
        for change in plan.changes:
            for op in change.operations:
                details.append(f"{change.app}: {op.describe}" + ("  ⚠ destructive" if op.destructive else ""))
        yield CheckResult("migrations.missing", "Migrations", Status.WARNING,
                          f"{len(details)} model change(s) have not been migrated", details[:20],
                          commands=[f"{CLI_NAME} migration-plan", f"{CLI_NAME} migrate"])
    for q in plan.unresolved_questions:
        yield CheckResult("migrations.needs_default", "Migrations", Status.WARNING,
                          f"{q['model']}.{q['field']} needs a default before a migration can be created non-interactively",
                          hint=f"Run `{CLI_NAME} migration-plan` for options.")
    for app in plan.unmigrated_apps:
        yield CheckResult("migrations.unmigrated_app", "Migrations", Status.WARNING,
                          f"App '{app['label']}' has models but no migrations", [", ".join(app["models"][:10])],
                          commands=[f"{CLI_NAME} makemigrations {app['label']}"])
    if plan.ghost_migrations:
        yield CheckResult("migrations.ghost", "Migrations", Status.WARNING,
                          "Migrations are recorded as applied but their files are missing", plan.ghost_migrations[:15],
                          hint="Restore the files from version control; never re-create them with different content.")
    for item in plan.missing_tables:
        yield CheckResult("migrations.schema_table", "Migrations", Status.ERROR,
                          f"Table '{item['table']}' for {item['model']} is missing although its migrations are applied",
                          hint="The migration may have been faked or the table dropped outside Django. Back up before repairing.")
    for item in plan.missing_columns:
        yield CheckResult("migrations.schema_column", "Migrations", Status.ERROR,
                          f"Column '{item['table']}.{item['column']}' ({item['model']}.{item['field']}) is missing "
                          "although its migrations are applied",
                          hint="The migration may have been faked or the schema changed outside Django. Back up before repairing.")
    if plan.is_up_to_date and not plan.unmigrated_apps and not plan.missing_tables and not plan.missing_columns \
            and not plan.db_error:
        yield CheckResult("migrations.ok", "Migrations", Status.OK,
                          f"Migrations are up to date ({plan.applied_count} applied)")
