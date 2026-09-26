"""Migration graph and migration command errors."""

from __future__ import annotations

import re

from django_doctor.branding import CLI_NAME
from django_doctor.exit_codes import ExitCode
from django_doctor.explain.models import Category, Confidence, Diagnosis
from django_doctor.explain.rules.base import RuleContext, rule
from django_doctor.risk import Risk


@rule("migrations.conflict")
def migration_conflict(ctx: RuleContext) -> Diagnosis | None:
    found = ctx.search(r"Conflicting migrations detected")
    if not found:
        return None
    exc, _ = found
    groups = re.findall(r"\(([^()]*?) in (\w+)\)", exc.message)
    d = ctx.new(
        "migrations.conflict", Category.MIGRATIONS,
        "Two or more migrations in the same app claim to be the latest one (the migration graph has multiple leaf nodes).",
        exc=exc, exit_code=ExitCode.MIGRATION_PROBLEM, risk=Risk.LOW,
        why="This happens when two branches each added a migration to the same app and were merged. "
            "Django refuses to guess the order.",
    )
    for names, app in groups:
        d.add_evidence(f"App '{app}': {names}")
    d.add_cause("Migrations were created in parallel on different branches.", Confidence.LIKELY)
    d.fixes += [
        "Create a merge migration and review it. Merge migrations contain no schema operations.",
        "If both migrations touch the same field, check that the combined result is what you intend.",
    ]
    d.commands.append(f"{CLI_NAME} django makemigrations --merge")
    d.commands.append(f"{CLI_NAME} migration-plan")
    return d


@rule("migrations.inconsistent_history")
def inconsistent_history(ctx: RuleContext) -> Diagnosis | None:
    found = ctx.search(
        r"Migration (?P<migration>[\w.]+) is applied before its dependency (?P<dependency>[\w.]+) on database '(?P<db>[^']+)'",
    )
    if not found:
        return None
    exc, match = found
    migration, dependency = match.group("migration"), match.group("dependency")
    d = ctx.new(
        "migrations.inconsistent_history", Category.MIGRATIONS,
        f"The database records {migration} as applied, but its dependency {dependency} is not applied.",
        exc=exc, exit_code=ExitCode.MIGRATION_PROBLEM, risk=Risk.HIGH,
        why="Django checks that the applied migrations form a consistent history. Here a migration "
            "was applied before a migration it depends on existed or was applied.",
    )
    dep_app = dependency.split(".")[0]
    user_model = None
    if ctx.project and (info := ctx.project.info):
        user_model = info.get("auth_user_model")
    if user_model and user_model.split(".")[0] == dep_app and migration.startswith(("admin.", "auth.")):
        d.add_evidence(f"AUTH_USER_MODEL = '{user_model}' lives in the app '{dep_app}'.", verified=True)
        d.add_cause(
            "A custom user model was introduced after the database had already been migrated with the default user model.",
            Confidence.LIKELY,
        )
    else:
        d.add_cause("A new dependency was added to an already-applied migration, or migrations were edited after being applied.", Confidence.POSSIBLE)
    d.fixes += [
        "Development database without valuable data: recreate the database and run migrations from scratch "
        "(Django Doctor will not do this for you).",
        "Database with real data: do not delete anything. Take a backup and repair the django_migrations table "
        "deliberately (e.g. faking the missing dependency after verifying its tables exist).",
    ]
    d.commands.append(f"{CLI_NAME} django showmigrations")
    d.risk_note = "Every fix touches migration history. Back up the database before changing anything."
    return d


@rule("migrations.node_not_found")
def node_not_found(ctx: RuleContext) -> Diagnosis | None:
    found = ctx.search(
        r"Migration (?P<migration>[\w.]+) dependencies reference nonexistent parent node \('(?P<app>\w+)', '(?P<name>\w+)'\)",
    )
    if not found:
        return None
    exc, match = found
    missing = f"{match.group('app')}.{match.group('name')}"
    d = ctx.new(
        "migrations.node_not_found", Category.MIGRATIONS,
        f"Migration {match.group('migration')} depends on {missing}, which does not exist on disk.",
        exc=exc, exit_code=ExitCode.MIGRATION_PROBLEM, risk=Risk.MEDIUM,
    )
    d.add_cause(f"The migration file {missing} was deleted or renamed.", Confidence.LIKELY)
    d.add_cause(f"The app '{match.group('app')}' was removed from INSTALLED_APPS or is not importable.")
    d.fixes += [
        "Restore the missing migration file from version control, e.g. "
        f"`git log --all --diff-filter=D -- '*{match.group('app')}/migrations/{match.group('name')}.py'`.",
        "Never renumber or delete migrations that were already applied somewhere.",
    ]
    return d


@rule("migrations.missing_migrations")
def missing_migrations(ctx: RuleContext) -> Diagnosis | None:
    found = ctx.search(r"Your models in app\(s\): (?P<apps>.+?) have changes that are not yet reflected in a migration")
    unapplied = ctx.search(r"You have (?P<n>\d+) unapplied migration\(s\)")
    if not found and not unapplied:
        return None
    exc, match = found or unapplied  # type: ignore[misc]
    if found:
        d = ctx.new("migrations.missing_migrations", Category.MIGRATIONS,
                    f"Model changes in {match.group('apps')} have no migration yet.", exc=exc,
                    exit_code=ExitCode.MIGRATION_PROBLEM, risk=Risk.LOW)
        d.add_cause("Models were edited without running makemigrations.", Confidence.DETECTED)
    else:
        d = ctx.new("migrations.unapplied", Category.MIGRATIONS,
                    f"{match.group('n')} migration(s) have not been applied to the database.", exc=exc,
                    exit_code=ExitCode.MIGRATION_PROBLEM, risk=Risk.LOW)
        d.add_cause("New migrations were pulled or created but `migrate` has not run.", Confidence.DETECTED)
    d.fixes.append("Review the plan, then create/apply migrations.")
    d.commands += [f"{CLI_NAME} migration-plan", f"{CLI_NAME} migrate"]
    return d


@rule("migrations.non_nullable_field")
def non_nullable_field(ctx: RuleContext) -> Diagnosis | None:
    found = ctx.search(
        r"(It is impossible to add a non-nullable field '(?P<field>\w+)' to (?P<model>\w+) without specifying a default"
        r"|It is impossible to change a nullable field '(?P<field2>\w+)' on (?P<model2>\w+) to non-nullable"
        r"|It is impossible to add the field '(?P<field3>\w+)' with 'auto_now_add=True' to (?P<model3>\w+))"
    )
    if not found:
        return None
    exc, match = found
    g = match.groupdict()
    field = g.get("field") or g.get("field2") or g.get("field3")
    model = g.get("model") or g.get("model2") or g.get("model3")
    d = ctx.new(
        "migrations.non_nullable_field", Category.MIGRATIONS,
        f"Django cannot create a migration for {model}.{field}: existing rows would have no value for a NOT NULL column.",
        exc=exc, exit_code=ExitCode.MIGRATION_PROBLEM, risk=Risk.MEDIUM,
    )
    d.add_cause(f"{model}.{field} is NOT NULL and has no default, and the table may already contain rows.", Confidence.DETECTED)
    d.fixes += [
        f"Add a `default=` to {model}.{field}, or",
        "make it nullable (`null=True`), migrate, fill the data with a data migration, then make it NOT NULL, or",
        "run the interactive `makemigrations` and provide a one-off default deliberately.",
    ]
    d.commands.append(f"{CLI_NAME} makemigrations --interactive")
    return d


@rule("migrations.irreversible")
def irreversible(ctx: RuleContext) -> Diagnosis | None:
    found = ctx.search(r"Operation (?P<op>.+?) in (?P<migration>[\w.]+) is not reversible", "IrreversibleError")
    if not found:
        return None
    exc, match = found
    d = ctx.new("migrations.irreversible", Category.MIGRATIONS,
                f"Migration {match.group('migration')} cannot be unapplied: {match.group('op')} has no reverse operation.",
                exc=exc, exit_code=ExitCode.MIGRATION_PROBLEM, risk=Risk.HIGH)
    d.add_cause("A RunPython/RunSQL operation was written without reverse code.", Confidence.DETECTED)
    d.fixes += ["Add `reverse_code=` (RunPython) or `reverse_sql=` (RunSQL); use `migrations.RunPython.noop` if nothing needs undoing.",
                "Consider whether rolling back is really needed — a new forward migration is often safer."]
    return d


@rule("migrations.bad_migration")
def bad_migration(ctx: RuleContext) -> Diagnosis | None:
    found = ctx.search(r"(Migration \w+ in app \w+ has no Migration class|Cannot find a migration matching '(?P<name>[^']+)' from app '(?P<app>\w+)')",
                       "BadMigrationError", "CommandError", "KeyError")
    if not found:
        return None
    exc, match = found
    d = ctx.new("migrations.bad_migration", Category.MIGRATIONS, exc.first_line, exc=exc,
                exit_code=ExitCode.MIGRATION_PROBLEM, risk=Risk.NONE)
    d.add_cause("The migration name is misspelled or the file is not a valid migration.", Confidence.LIKELY)
    d.commands.append(f"{CLI_NAME} django showmigrations")
    return d
