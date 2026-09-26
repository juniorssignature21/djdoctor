"""Database errors: missing tables/columns, connectivity, integrity, data errors."""

from __future__ import annotations

import re
from typing import Any

from django_doctor.branding import CLI_NAME
from django_doctor.exit_codes import ExitCode
from django_doctor.explain.models import Category, Confidence, Diagnosis
from django_doctor.explain.rules.base import RuleContext, rule
from django_doctor.risk import Risk

DB_ERRORS = (
    "OperationalError", "ProgrammingError", "UndefinedColumn", "UndefinedTable",
    "DatabaseError", "InternalError", "InterfaceError",
)

_MISSING_COLUMN = [
    re.compile(r"no such column: (?:(?P<table>\w+)\.)?(?P<column>\w+)"),  # SQLite
    re.compile(r'column "?(?P<table>\w+)"?\."?(?P<column>\w+)"? does not exist'),  # PostgreSQL
    re.compile(r'column "(?P<column>\w+)" of relation "(?P<table>\w+)" does not exist'),
    re.compile(r'column "(?P<column>\w+)" does not exist'),
    re.compile(r"Unknown column '(?:(?P<table>\w+)\.)?(?P<column>\w+)' in"),  # MySQL
    re.compile(r"table (?P<table>\w+) has no column named (?P<column>\w+)"),  # SQLite INSERT
]

_MISSING_TABLE = [
    re.compile(r"no such table: (?:main\.)?(?P<table>\w+)"),
    re.compile(r'relation "(?:\w+\.)?(?P<table>\w+)" does not exist'),
    re.compile(r"Table '(?:\w+\.)?(?P<table>\w+)' doesn't exist"),
]


def _match_any(ctx: RuleContext, patterns, names=DB_ERRORS):
    for pattern in patterns:
        found = ctx.search(pattern, *names)
        if found:
            return found
    return None


def _pending_ops(migrations: dict[str, Any]):
    for entry in migrations.get("pending", []):
        if entry.get("backwards"):
            continue
        for op in entry.get("operations", []):
            yield entry, op


def _change_ops(migrations: dict[str, Any]):
    for app, entries in migrations.get("changes", {}).items():
        for mig in entries:
            for op in mig.get("operations", []):
                yield app, mig, op


# ---------------------------------------------------------------- columns
@rule("database.missing_column")
def missing_column(ctx: RuleContext) -> Diagnosis | None:
    found = _match_any(ctx, _MISSING_COLUMN)
    if not found:
        return None
    exc, match = found
    table = match.groupdict().get("table")
    column = match.group("column")
    qualified = f"{table}.{column}" if table else column

    d = ctx.new(
        "database.missing_column", Category.DATABASE,
        f"The Django model expects this database column:\n\n    {qualified}\n\nbut the database does not contain it.",
        exc=exc, exit_code=ExitCode.DATABASE_ERROR, risk=Risk.LOW,
        why="Django builds SQL from your model definitions. When a model field exists "
            "in code but its column was never created in the database, every query "
            "that touches the model fails.",
    )
    verified = _verify_schema_problem(ctx, d, table, column)
    if not verified:
        d.add_cause("The model was changed but its migration has not been created or applied.", Confidence.LIKELY)
        d.add_cause("The application is connected to a different database than the one you migrated.")
        d.fixes.append("Create and apply migrations for the changed model.")
        d.commands.append(f"{CLI_NAME} migrate")
        d.commands.append(f"{CLI_NAME} migration-plan")
    if not d.risk_note:
        d.risk_note = "Adding a missing column with a migration does not remove existing data."
    return d


def _verify_schema_problem(ctx: RuleContext, d: Diagnosis, table: str | None, column: str | None) -> bool:
    """Cross-check the error with the real migration state. Returns True if conclusive."""
    if ctx.project is None:
        return False
    migrations = ctx.project.migrations
    if not migrations:
        return False
    model = ctx.project.model_for_table(table) if table else None
    if model is None and table is None and column:
        # SQLite sometimes omits the table; find a model with that column.
        for tbl, info in migrations.get("models", {}).items():
            if column in info.get("columns", {}):
                model, table = info, tbl
                break
    if model is None:
        if table:
            d.add_evidence(f"No installed model uses the table '{table}'.", verified=True)
            d.add_cause("The query comes from raw SQL, a removed model, or an app missing from INSTALLED_APPS.")
        return False

    label = model["label"]
    app_label, model_name = label.split(".")
    field_name = model.get("columns", {}).get(column) if column else None
    if column:
        if field_name:
            d.add_evidence(f"Model {label} defines field '{field_name}' (column '{column}').", verified=True)
        else:
            d.add_evidence(f"Model {label} has no field using column '{column}'.", verified=True)
            d.add_cause(
                "The code that runs is out of date with the model, or the column is referenced by raw SQL / a stale query.",
                Confidence.POSSIBLE,
            )

    target_field = field_name or column
    for entry, op in _pending_ops(migrations):
        if entry["app"] != app_label or (op.get("model") or "").lower() != model_name.lower():
            continue
        creates = op["type"] == "CreateModel" and (column is None or target_field in op.get("fields", []))
        adds = op["type"] in ("AddField", "RenameField") and (
            op.get("field_name") == target_field or op.get("new_name") == target_field
        )
        if creates or adds:
            d.add_evidence(
                f"Migration {entry['app']}.{entry['name']} ({op['describe']}) exists but is NOT applied.",
                verified=True,
            )
            d.add_evidence("Migration status: Pending", verified=True)
            d.add_cause("The migration for this change exists but has not been applied to the database.", Confidence.DETECTED)
            d.fixes.append("Apply the pending migration(s).")
            d.commands.append(f"{CLI_NAME} migrate")
            return True

    for app, mig, op in _change_ops(migrations):
        if app != app_label or (op.get("model") or "").lower() != model_name.lower():
            continue
        if op["type"] in ("AddField", "CreateModel") and (
            op.get("field_name") == target_field or target_field in op.get("fields", [])
        ):
            d.add_evidence(f"Model change: {model_name}.{target_field} ({op['describe']})", verified=True)
            d.add_evidence("No migration file exists for this change yet.", verified=True)
            d.add_cause("The model was changed but no migration has been created for it.", Confidence.DETECTED)
            d.fixes.append("Create the migration, review it, then apply it.")
            d.commands.append(f"{CLI_NAME} migrate")
            return True

    schema = migrations.get("schema", {})
    for item in schema.get("missing_columns", []) + schema.get("missing_tables", []):
        if item.get("model") == label and not item.get("app_has_pending"):
            d.add_evidence(f"All migrations for '{app_label}' are recorded as applied, yet the database lacks this {'column' if column else 'table'}.", verified=True)
            d.add_cause(
                "A migration was marked as applied without running (migrate --fake), or the schema "
                "was changed outside Django.", Confidence.LIKELY,
            )
            d.add_cause("DATABASES points to a different database than the one that was migrated.")
            d.fixes.append(
                "Inspect the SQL of the migration that should have created it and compare with the real schema."
            )
            d.commands.append(f"{CLI_NAME} django showmigrations {app_label}")
            d.commands.append(f"{CLI_NAME} django sqlmigrate {app_label} <migration_name>")
            d.risk = Risk.MEDIUM
            d.risk_note = ("Do not un-fake or re-run migrations blindly on a database with real data; "
                           "take a backup first.")
            return True
    if migrations.get("db_error"):
        d.add_evidence("Django Doctor could not read the migration state from the database.", verified=True)
    return False


# ----------------------------------------------------------------- tables
@rule("database.missing_table")
def missing_table(ctx: RuleContext) -> Diagnosis | None:
    found = _match_any(ctx, _MISSING_TABLE)
    if not found:
        return None
    exc, match = found
    table = match.group("table")
    d = ctx.new(
        "database.missing_table", Category.DATABASE,
        f"The database has no table named '{table}'.",
        exc=exc, exit_code=ExitCode.DATABASE_ERROR, risk=Risk.LOW,
        why="Tables are created by migrations. A missing table usually means the migrations "
            "that create it were never applied to this database.",
    )
    conclusive = False
    if ctx.project is not None and (migrations := ctx.project.migrations):
        if table == "django_migrations" or migrations.get("applied_count") == 0:
            d.add_evidence("No migrations have been applied to this database yet.", verified=True)
            d.add_cause("This is a new or empty database that has never been migrated.", Confidence.DETECTED)
            d.commands.append(f"{CLI_NAME} migrate")
            conclusive = True
        if migrations.get("db_missing_file"):
            d.add_evidence("The SQLite database file does not exist yet.", verified=True)
            d.add_cause("The database has not been created yet; `migrate` creates it.", Confidence.DETECTED)
            conclusive = True
        for app in migrations.get("unmigrated_apps", []):
            if table.startswith(app["label"] + "_"):
                d.add_evidence(f"App '{app['label']}' has models but no migrations directory.", verified=True)
                d.add_cause(f"Migrations were never created for '{app['label']}'.", Confidence.DETECTED)
                d.commands.append(f"{CLI_NAME} makemigrations {app['label']}")
                conclusive = True
        if not conclusive:
            conclusive = _verify_schema_problem(ctx, d, table, None)
    if not conclusive:
        d.add_cause("Migrations that create this table have not been applied.", Confidence.LIKELY)
        d.add_cause("The app that owns the table is missing from INSTALLED_APPS or has no migrations.")
        d.add_cause("The application is connected to a different (e.g. empty) database.")
    if f"{CLI_NAME} migrate" not in d.commands:
        d.commands.append(f"{CLI_NAME} migrate")
    d.commands.append(f"{CLI_NAME} migration-plan")
    d.fixes.insert(0, "Apply migrations to create the missing table.")
    return d


# ------------------------------------------------------------ connectivity
_CONNECTION_PATTERNS: list[tuple[str, re.Pattern[str], str, str]] = [
    ("auth_failed",
     re.compile(r"(password authentication failed for user|Access denied for user|\(1045\b|"
                r"role \"[^\"]+\" does not exist|no password supplied)", re.I),
     "The database server rejected Django's credentials.",
     "USER / PASSWORD in DATABASES (or the environment variables they come from) are wrong."),
    ("database_missing",
     re.compile(r'(database "(?P<db>[^"]+)" does not exist|Unknown database \'(?P<db2>[^\']+)\'|\(1049\b)', re.I),
     "The database server is reachable but the database itself does not exist.",
     "The database named in DATABASES['NAME'] has not been created on the server."),
    ("server_unreachable",
     re.compile(r"(could not connect to server|connection to server .* failed|Connection refused|"
                r"Can't connect to (?:local )?MySQL server|\((?:2002|2003|2005)\b|could not translate host name|"
                r"Name or service not known|timeout expired|Connection timed out)", re.I),
     "Django could not reach the database server.",
     "The database server is not running, or HOST/PORT in DATABASES are wrong."),
    ("sqlite_open",
     re.compile(r"unable to open database file", re.I),
     "SQLite could not open or create the database file.",
     "The directory in DATABASES['NAME'] does not exist or is not writable."),
    ("sqlite_locked",
     re.compile(r"database is locked", re.I),
     "The SQLite database is locked by another process or connection.",
     "Another process (a second runserver, a shell, a DB browser) holds a write lock."),
]


@rule("database.connection")
def database_connection(ctx: RuleContext) -> Diagnosis | None:
    for key, pattern, what, likely in _CONNECTION_PATTERNS:
        found = ctx.search(pattern, *DB_ERRORS)
        if not found:
            continue
        exc, match = found
        d = ctx.new(f"database.{key}", Category.DATABASE, what, exc=exc,
                    exit_code=ExitCode.DATABASE_ERROR, risk=Risk.NONE)
        d.add_cause(likely, Confidence.LIKELY)
        _add_db_config_evidence(ctx, d)
        if key == "server_unreachable":
            d.add_cause("A required service container (docker compose) is not started.")
            d.fixes += ["Start the database server.", "Check HOST and PORT in DATABASES."]
        elif key == "auth_failed":
            d.fixes += ["Check the database user and password (usually provided through environment variables).",
                        "Verify the user exists on the database server and can connect from this host."]
        elif key == "database_missing":
            db = match.groupdict().get("db") or match.groupdict().get("db2")
            if db:
                d.add_evidence(f"Database name: {db}")
            d.fixes += ["Create the database on the server (e.g. `createdb <name>` for PostgreSQL), then migrate."]
            d.commands.append(f"{CLI_NAME} migrate")
        elif key == "sqlite_open":
            d.fixes += ["Make sure the directory of the SQLite file exists and is writable."]
        elif key == "sqlite_locked":
            d.fixes += ["Stop other processes using the database file.",
                        "For concurrent workloads, consider PostgreSQL or a larger SQLite `timeout` option."]
        d.commands.append(f"{CLI_NAME} doctor")
        return d
    return None


def _add_db_config_evidence(ctx: RuleContext, d: Diagnosis) -> None:
    if ctx.project is None or not (info := ctx.project.info):
        return
    db = (info.get("databases") or {}).get("default")
    if not db:
        return
    engine = (db.get("engine") or "").rsplit(".", 1)[-1]
    parts = [f"engine={engine}"]
    if db.get("host"):
        parts.append(f"host={db['host']}")
    if db.get("port"):
        parts.append(f"port={db['port']}")
    if db.get("name"):
        parts.append(f"name={db['name']}")
    if db.get("user"):
        parts.append(f"user={db['user']}")
    parts.append("password=" + ("********" if db.get("has_password") else "(not set)"))
    d.add_evidence("DATABASES['default']: " + ", ".join(parts), verified=True, private=True)


# --------------------------------------------------------------- integrity
_INTEGRITY: list[tuple[str, re.Pattern[str]]] = [
    ("unique", re.compile(r"UNIQUE constraint failed: (?P<cols>[\w., ]+)")),
    ("unique", re.compile(r'duplicate key value violates unique constraint "(?P<constraint>[^"]+)"')),
    ("unique", re.compile(r"\(1062, \"Duplicate entry '.*' for key '(?P<constraint>[^']+)'")),
    ("not_null", re.compile(r"NOT NULL constraint failed: (?P<cols>[\w.]+)")),
    ("not_null", re.compile(r'null value in column "(?P<col>\w+)"(?: of relation "(?P<table>\w+)")? violates not-null constraint')),
    ("not_null", re.compile(r"\(1048, \"Column '(?P<col>\w+)' cannot be null")),
    ("foreign_key", re.compile(r"FOREIGN KEY constraint failed|violates foreign key constraint|\(1452,|\(1451,")),
    ("check", re.compile(r"CHECK constraint failed|violates check constraint")),
]


@rule("database.integrity")
def integrity_error(ctx: RuleContext) -> Diagnosis | None:
    for kind, pattern in _INTEGRITY:
        found = ctx.search(pattern, "IntegrityError", "UniqueViolation", "NotNullViolation", "ForeignKeyViolation", "CheckViolation")
        if not found:
            continue
        exc, match = found
        groups = {k: v for k, v in match.groupdict().items() if v}
        target = groups.get("cols") or groups.get("constraint") or (
            f"{groups['table']}.{groups['col']}" if "table" in groups and "col" in groups else groups.get("col", "")
        )
        texts = {
            "unique": ("A row with the same value(s) already exists, so the unique constraint on "
                       f"{target or 'the table'} rejected the write.",
                       ["Check for an existing object before creating (e.g. `get_or_create`).",
                        "Validate uniqueness in forms/serializers (ModelForm does this via `validate_unique`).",
                        "If this happens in tests or fixtures, make generated values unique."]),
            "not_null": (f"A NULL value was written to {target or 'a column'} which does not allow NULL.",
                         ["Provide a value for the field before saving.",
                          "If NULL is legitimate, set `null=True` on the field and create a migration.",
                          "If this happens during `migrate`, the migration adds a NOT NULL column without a default."]),
            "foreign_key": ("A row references a related object that does not exist (foreign key constraint).",
                            ["Make sure the related object is saved before referencing it.",
                             "Check `on_delete` behaviour and fixtures load order."]),
            "check": ("A value violated a CHECK constraint defined on the table.",
                      ["Validate the value before saving; see the model's `Meta.constraints`."]),
        }
        what, fixes = texts[kind]
        d = ctx.new(f"database.integrity.{kind}", Category.DATABASE, what, exc=exc,
                    exit_code=ExitCode.DATABASE_ERROR, risk=Risk.LOW,
                    why="The database enforces constraints that Django declared in your models and migrations.")
        if target:
            d.add_evidence(f"Constraint / column: {target}")
        d.add_cause("The application tried to save data that violates a database constraint.", Confidence.LIKELY)
        d.fixes += fixes
        d.risk_note = "No data was changed: the database rejected the write."
        return d
    return None


# --------------------------------------------------------------- data error
@rule("database.data_error")
def data_error(ctx: RuleContext) -> Diagnosis | None:
    found = ctx.search(
        r"(value too long for type character varying\((?P<len>\d+)\)|Data too long for column '(?P<col>\w+)'|"
        r"invalid input syntax for type (?P<type>\w+)|Out of range value|numeric field overflow)",
        "DataError", "StringDataRightTruncation", "InvalidTextRepresentation", "NumericValueOutOfRange",
    )
    if not found:
        return None
    exc, match = found
    g = match.groupdict()
    if g.get("len") or g.get("col"):
        what = "A value is longer than the column allows" + (f" (max_length={g['len']})." if g.get("len") else f" (column '{g['col']}').")
        fixes = ["Validate/truncate input before saving (ModelForm validation enforces max_length).",
                 "If longer values are legitimate, increase max_length and create a migration."]
    elif g.get("type"):
        what = f"A value could not be converted to the database type '{g['type']}'."
        fixes = ["Convert/validate the value before it reaches the query (e.g. int(), forms)."]
    else:
        what = "A numeric value is out of range for its column."
        fixes = ["Validate the value range or use a larger field type (BigIntegerField, DecimalField digits)."]
    d = ctx.new("database.data_error", Category.DATABASE, what, exc=exc,
                exit_code=ExitCode.DATABASE_ERROR, risk=Risk.LOW)
    d.add_cause("The data does not fit the column definition.", Confidence.LIKELY)
    d.add_cause("SQLite does not enforce lengths, so this may only appear on PostgreSQL/MySQL.")
    d.fixes += fixes
    return d
