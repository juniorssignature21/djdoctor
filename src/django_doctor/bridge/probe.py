"""Django Doctor inspection probe.

This file is executed *as a script* by the Python interpreter of the inspected
project::

    python probe.py ACTION --input args.json --output result.json

It must therefore be completely self-contained: it may only import the standard
library and Django, and it must stay compatible with every Python version that
a supported Django release runs on (no ``match``, no ``X | Y`` annotations,
no dict union operators).

Safety contract: the probe is READ-ONLY. It never writes to the database, never
creates SQLite files, never writes migration files and never returns secret
values (passwords, SECRET_KEY, API keys).
"""

import json
import os
import sys
import traceback

PROBE_PROTOCOL = 1
MAX_TEMPLATE_LISTING = 5000
SECRET_WORDS = ("SECRET", "PASSWORD", "PASSWD", "TOKEN", "API_KEY", "APIKEY", "PRIVATE", "CREDENTIAL")


# --------------------------------------------------------------------- errors
def serialize_exception(exc):
    chain = []
    seen = set()
    current = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        chain.append(
            {
                "type": type(current).__name__,
                "module": type(current).__module__,
                "message": _safe_str(current),
            }
        )
        current = current.__cause__ or current.__context__
    return {
        "type": type(exc).__name__,
        "module": type(exc).__module__,
        "message": _safe_str(exc),
        "traceback": "".join(traceback.format_exception(type(exc), exc, exc.__traceback__)),
        "chain": chain,
    }


def _safe_str(value):
    try:
        return str(value)
    except Exception:  # pragma: no cover - defensive
        return repr(value)


class ProbeFailure(Exception):
    def __init__(self, stage, exc):
        Exception.__init__(self, stage)
        self.stage = stage
        self.exc = exc


# ---------------------------------------------------------------------- setup
def setup_django():
    """Import settings and populate the app registry, reporting which stage failed."""
    project_root = os.getcwd()
    if project_root not in sys.path:
        sys.path.insert(0, project_root)
    try:
        import django
    except Exception as exc:
        raise ProbeFailure("django-import", exc)
    module = os.environ.get("DJANGO_SETTINGS_MODULE")
    if not module:
        raise ProbeFailure(
            "settings",
            RuntimeError("DJANGO_SETTINGS_MODULE is not set and could not be determined."),
        )
    import importlib

    try:
        importlib.import_module(module)
    except Exception as exc:
        raise ProbeFailure("settings", exc)
    try:
        django.setup()
    except Exception as exc:
        raise ProbeFailure("apps", exc)
    return django


# ---------------------------------------------------------------- utilities
def _jsonable(value, depth=0):
    if depth > 4:
        return repr(value)
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_jsonable(v, depth + 1) for v in value]
    if isinstance(value, dict):
        return dict((str(k), _jsonable(v, depth + 1)) for k, v in value.items())
    return str(value)


def _is_secret_name(name):
    upper = name.upper()
    return any(word in upper for word in SECRET_WORDS)


def _sqlite_file_missing(connection):
    if connection.vendor != "sqlite":
        return False
    name = str(connection.settings_dict.get("NAME") or "")
    if not name or name == ":memory:" or name.startswith("file:") or "mode=memory" in name:
        return False
    return not os.path.exists(name)


def _limit_connect_timeout(connection):
    """Avoid hanging for minutes on an unreachable server (in-memory only)."""
    options = connection.settings_dict.setdefault("OPTIONS", {})
    if connection.vendor == "postgresql" or connection.vendor == "mysql":
        options.setdefault("connect_timeout", 5)


# ===================================================================== actions
def action_packages(args):
    try:
        from importlib import metadata
    except ImportError:  # pragma: no cover - Python < 3.8
        import importlib_metadata as metadata
    result = {}
    for name in args.get("distributions", []):
        try:
            result[name] = metadata.version(name)
        except Exception:
            result[name] = None
    modules = {}
    import importlib.util

    for name in args.get("modules", []):
        try:
            modules[name] = importlib.util.find_spec(name) is not None
        except Exception:
            modules[name] = False
    return {"distributions": result, "modules": modules}


def action_info(args):
    django = setup_django()
    import platform

    from django.apps import apps
    from django.conf import settings

    try:
        from django.db.migrations.loader import MigrationLoader

        migrations_module = MigrationLoader.migrations_module
    except Exception:  # pragma: no cover
        migrations_module = None

    installed = []
    for config in apps.get_app_configs():
        mig_module = None
        mig_path = None
        has_migrations = False
        if migrations_module is not None:
            try:
                mig_module, _explicit = migrations_module(config.label)
            except Exception:
                mig_module = None
            if mig_module:
                try:
                    import importlib

                    mod = importlib.import_module(mig_module)
                    has_migrations = True
                    mod_file = getattr(mod, "__file__", None)
                    if mod_file:
                        mig_path = os.path.dirname(mod_file)
                    else:
                        paths = list(getattr(mod, "__path__", []))
                        mig_path = paths[0] if paths else None
                except Exception:
                    has_migrations = False
        installed.append(
            {
                "label": config.label,
                "name": config.name,
                "path": config.path,
                "models": [m._meta.object_name for m in config.get_models()],
                "migrations_module": mig_module,
                "migrations_path": mig_path,
                "has_migrations": has_migrations,
            }
        )

    databases = {}
    for alias, conf in settings.DATABASES.items():
        databases[alias] = {
            "engine": conf.get("ENGINE"),
            "name": str(conf.get("NAME") or ""),
            "host": conf.get("HOST") or "",
            "port": str(conf.get("PORT") or ""),
            "user": conf.get("USER") or "",
            "has_password": bool(conf.get("PASSWORD")),
            "option_keys": sorted((conf.get("OPTIONS") or {}).keys()),
        }

    templates = []
    for backend in getattr(settings, "TEMPLATES", []):
        options = backend.get("OPTIONS", {}) or {}
        templates.append(
            {
                "backend": backend.get("BACKEND"),
                "dirs": [str(d) for d in backend.get("DIRS", [])],
                "app_dirs": bool(backend.get("APP_DIRS")),
                "has_loaders": "loaders" in options,
                "context_processors": list(options.get("context_processors", [])),
            }
        )

    secret = getattr(settings, "SECRET_KEY", "") or ""
    other = {}
    for name in dir(settings):
        if not name.isupper():
            continue
        if name.startswith("CORS_") or name in (
            "CSRF_TRUSTED_ORIGINS", "SECURE_SSL_REDIRECT", "SESSION_COOKIE_SECURE",
            "CSRF_COOKIE_SECURE", "SECURE_HSTS_SECONDS", "SECURE_PROXY_SSL_HEADER",
            "USE_TZ", "TIME_ZONE", "LANGUAGE_CODE", "EMAIL_BACKEND", "DEFAULT_FILE_STORAGE",
            "STATICFILES_STORAGE", "WSGI_APPLICATION", "ASGI_APPLICATION",
        ):
            if _is_secret_name(name):
                continue
            other[name] = _jsonable(getattr(settings, name))

    storages = getattr(settings, "STORAGES", None)
    return {
        "python": {"version": platform.python_version(), "executable": sys.executable},
        "django": {"version": django.get_version()},
        "settings_module": os.environ.get("DJANGO_SETTINGS_MODULE"),
        "settings_file": getattr(sys.modules.get(os.environ.get("DJANGO_SETTINGS_MODULE", "")), "__file__", None),
        "debug": bool(settings.DEBUG),
        "allowed_hosts": list(settings.ALLOWED_HOSTS),
        "secret_key": {
            "set": bool(secret),
            "length": len(secret),
            "unique_characters": len(set(secret)),
            "insecure_prefix": str(secret).startswith("django-insecure"),
        },
        "installed_apps": installed,
        "installed_app_names": list(settings.INSTALLED_APPS),
        "middleware": list(getattr(settings, "MIDDLEWARE", None) or []),
        "databases": databases,
        "templates": templates,
        "root_urlconf": getattr(settings, "ROOT_URLCONF", None),
        "static": {
            "url": settings.STATIC_URL,
            "root": str(settings.STATIC_ROOT) if settings.STATIC_ROOT else None,
            "dirs": [str(d[1] if isinstance(d, (list, tuple)) else d) for d in settings.STATICFILES_DIRS],
            "storages": _jsonable(storages) if storages else None,
        },
        "media": {
            "url": settings.MEDIA_URL,
            "root": str(settings.MEDIA_ROOT) if settings.MEDIA_ROOT else None,
        },
        "auth_user_model": getattr(settings, "AUTH_USER_MODEL", None),
        "default_auto_field": getattr(settings, "DEFAULT_AUTO_FIELD", None),
        "other": other,
    }


def action_checks(args):
    setup_django()
    from django.core import checks

    kwargs = {"include_deployment_checks": bool(args.get("deploy"))}
    tags = args.get("tags") or None
    if tags:
        kwargs["tags"] = tags
    if args.get("databases"):
        kwargs["databases"] = list(args["databases"])
    messages = checks.run_checks(**kwargs)
    result = []
    for m in messages:
        result.append(
            {
                "level": m.level,
                "level_name": {10: "DEBUG", 20: "INFO", 30: "WARNING", 40: "ERROR", 50: "CRITICAL"}.get(m.level, str(m.level)),
                "id": m.id,
                "msg": _safe_str(m.msg),
                "hint": _safe_str(m.hint) if m.hint else None,
                "obj": _describe_obj(m.obj),
                "silenced": m.is_silenced(),
            }
        )
    return {"messages": result}


def _describe_obj(obj):
    if obj is None:
        return None
    meta = getattr(obj, "_meta", None)
    if meta is not None and hasattr(meta, "label"):
        return meta.label
    model = getattr(obj, "model", None)
    if model is not None and hasattr(obj, "name") and hasattr(model, "_meta"):
        return "%s.%s" % (model._meta.label, obj.name)
    return _safe_str(obj)


def action_database(args):
    setup_django()
    from django.db import connections

    result = {}
    for alias in connections:
        conn = connections[alias]
        entry = {"vendor": conn.vendor, "ok": False, "error": None, "missing_file": False}
        if _sqlite_file_missing(conn):
            entry["missing_file"] = True
            entry["path"] = str(conn.settings_dict.get("NAME"))
            result[alias] = entry
            continue
        _limit_connect_timeout(conn)
        try:
            conn.ensure_connection()
            with conn.cursor() as cursor:
                cursor.execute("SELECT 1")
            entry["ok"] = True
            try:
                entry["server_version"] = _safe_str(getattr(conn, "pg_version", None) or getattr(conn, "mysql_version", None) or "")
            except Exception:
                pass
        except Exception as exc:
            entry["error"] = serialize_exception(exc)
        finally:
            try:
                conn.close()
            except Exception:
                pass
        result[alias] = entry
    return {"databases": result}


# ------------------------------------------------------------- migrations
def _field_info(field):
    if field is None:
        return None
    info = {"class": type(field).__name__}
    try:
        from django.db.models import NOT_PROVIDED

        info["null"] = bool(getattr(field, "null", False))
        info["blank"] = bool(getattr(field, "blank", False))
        default = getattr(field, "default", NOT_PROVIDED)
        info["has_default"] = default is not NOT_PROVIDED
        db_default = getattr(field, "db_default", NOT_PROVIDED)
        info["has_db_default"] = db_default is not NOT_PROVIDED
        if info["has_default"]:
            text = "<callable %s>" % getattr(default, "__name__", "?") if callable(default) else repr(default)
            info["default"] = text[:80]
        info["empty_strings_allowed"] = bool(getattr(field, "empty_strings_allowed", False))
        info["max_length"] = getattr(field, "max_length", None)
        info["unique"] = bool(getattr(field, "_unique", False) or getattr(field, "primary_key", False))
        info["primary_key"] = bool(getattr(field, "primary_key", False))
        info["db_column"] = getattr(field, "db_column", None)
        info["many_to_many"] = bool(getattr(field, "many_to_many", False))
        remote = getattr(getattr(field, "remote_field", None), "model", None)
        if remote is not None:
            info["related_model"] = remote if isinstance(remote, str) else remote._meta.label
        try:
            name, path, fargs, kwargs = field.deconstruct()
            kwargs.pop("default", None)
            info["signature"] = "%s|%s" % (path, sorted((k, repr(v)) for k, v in kwargs.items() if k not in ("verbose_name", "help_text")))
        except Exception:
            info["signature"] = info["class"]
    except Exception:
        pass
    return info


def _state_field(state, app_label, model_name, field_name):
    try:
        model_state = state.models[(app_label, model_name.lower())]
        return model_state.fields[field_name]
    except Exception:
        return None


def _state_model_fields(state, app_label, model_name):
    try:
        return list(state.models[(app_label, model_name.lower())].fields.keys())
    except Exception:
        return []


def _serialize_operation(op, app_label, state):
    from django.db import migrations as m

    data = {"type": type(op).__name__, "app": app_label}
    try:
        data["describe"] = op.describe()
    except Exception:
        data["describe"] = type(op).__name__
    data["reversible"] = bool(getattr(op, "reversible", True))
    data["reduces_to_sql"] = bool(getattr(op, "reduces_to_sql", True))
    model_name = getattr(op, "model_name", None) or getattr(op, "name", None)
    if isinstance(op, (m.AddField, m.AlterField, m.RemoveField, m.RenameField)):
        data["model"] = op.model_name
    elif isinstance(op, (m.CreateModel, m.DeleteModel, m.AlterModelTable)):
        data["model"] = op.name
    elif hasattr(op, "model_name"):
        data["model"] = op.model_name
    else:
        data["model"] = None

    if isinstance(op, m.AddField):
        data["field_name"] = op.name
        data["field"] = _field_info(op.field)
        data["preserve_default"] = bool(op.preserve_default)
    elif isinstance(op, m.AlterField):
        data["field_name"] = op.name
        data["field"] = _field_info(op.field)
        data["old_field"] = _field_info(_state_field(state, app_label, op.model_name, op.name))
    elif isinstance(op, m.RemoveField):
        data["field_name"] = op.name
        data["old_field"] = _field_info(_state_field(state, app_label, op.model_name, op.name))
    elif isinstance(op, m.RenameField) or isinstance(op, m.RenameModel):
        data["old_name"] = op.old_name
        data["new_name"] = op.new_name
    elif isinstance(op, m.DeleteModel):
        data["old_fields"] = _state_model_fields(state, app_label, op.name)
    elif isinstance(op, m.CreateModel):
        data["fields"] = [name for name, _f in op.fields]
    elif isinstance(op, m.RunSQL):
        sql = op.sql
        if isinstance(sql, (list, tuple)):
            sql = "\n".join(str(s[0] if isinstance(s, (list, tuple)) else s) for s in sql)
        data["sql"] = str(sql)[:2000]
    elif isinstance(op, m.RunPython):
        code = op.code
        data["code"] = "%s.%s" % (getattr(code, "__module__", "?"), getattr(code, "__qualname__", getattr(code, "__name__", "?")))
    elif isinstance(op, m.SeparateDatabaseAndState):
        data["database_operations"] = [
            _serialize_operation(inner, app_label, state) for inner in op.database_operations
        ]
    if model_name and "model" not in data:
        data["model"] = model_name
    if data.get("model"):
        # Operations store lower-cased model names; prefer the declared name.
        try:
            data["model"] = state.models[(app_label, data["model"].lower())].name
        except Exception:
            pass
    return data


def _serialize_ops_progressively(operations, app_label, state):
    """Serialize ops while advancing ``state`` so AlterField/RemoveField can see the old field."""
    out = []
    for op in operations:
        out.append(_serialize_operation(op, app_label, state))
        try:
            op.state_forwards(app_label, state)
        except Exception:
            pass
    return out


def _make_questioner():
    from django.db.migrations.questioner import MigrationQuestioner

    class RecordingQuestioner(MigrationQuestioner):
        """Never prompts. Records every question Django would have asked."""

        def __init__(self):
            MigrationQuestioner.__init__(self, defaults={}, specified_apps=set(), dry_run=True)
            self.questions = []

        def ask_initial(self, app_label):
            return MigrationQuestioner.ask_initial(self, app_label)

        def ask_not_null_addition(self, field_name, model_name):
            self.questions.append({"kind": "not_null_addition", "model": model_name, "field": field_name})
            return None

        def ask_not_null_alteration(self, field_name, model_name):
            self.questions.append({"kind": "not_null_alteration", "model": model_name, "field": field_name})
            return None

        def ask_rename(self, model_name, old_name, new_name, field_instance):
            self.questions.append({"kind": "rename_field", "model": model_name, "old": old_name, "new": new_name})
            return False

        def ask_rename_model(self, old_model_state, new_model_state):
            self.questions.append({"kind": "rename_model", "old": old_model_state.name, "new": new_model_state.name})
            return False

        def ask_merge(self, app_label):
            return False

        def ask_auto_now_add_addition(self, field_name, model_name):
            self.questions.append({"kind": "auto_now_add_addition", "model": model_name, "field": field_name})
            return None

        def ask_unique_callable_default_addition(self, field_name, model_name):
            self.questions.append({"kind": "unique_callable_default", "model": model_name, "field": field_name})
            return None

    return RecordingQuestioner()


def _resolve_targets(loader, app_label, migration_name):
    graph = loader.graph
    if not app_label:
        return graph.leaf_nodes(), False
    if app_label not in loader.migrated_apps:
        raise ValueError("App '%s' does not have migrations." % app_label)
    if migration_name is None:
        return [key for key in graph.leaf_nodes() if key[0] == app_label], False
    if migration_name == "zero":
        return [(app_label, None)], True
    migration = loader.get_migration_by_prefix(app_label, migration_name)
    return [(app_label, migration.name)], True


def action_migrations(args):
    setup_django()
    from django.apps import apps
    from django.db import DEFAULT_DB_ALIAS, connections
    from django.db.migrations.autodetector import MigrationAutodetector
    from django.db.migrations.loader import MigrationLoader
    from django.db.migrations.state import ProjectState

    alias = args.get("database") or DEFAULT_DB_ALIAS
    result = {
        "database": alias,
        "graph_error": None,
        "conflicts": {},
        "unmigrated_apps": [],
        "changes": {},
        "questions": [],
        "autodetect_error": None,
        "db_error": None,
        "db_missing_file": False,
        "inconsistent_history": None,
        "applied_count": 0,
        "pending": [],
        "ghost_migrations": [],
        "schema": {"checked": False, "missing_tables": [], "missing_columns": []},
        "row_counts": {},
        "models": {},
        "disk_migrations": {},
    }

    # Map of every model: helps explain "no such column" style errors.
    for model in apps.get_models(include_auto_created=True):
        meta = model._meta
        result["models"][meta.db_table] = {
            "label": meta.label,
            "app": meta.app_label,
            "managed": bool(meta.managed),
            "proxy": bool(meta.proxy),
            "columns": dict((f.column, f.name) for f in meta.local_concrete_fields),
        }

    # 1. Graph from disk only (no database access) — like makemigrations.
    try:
        disk_loader = MigrationLoader(None, ignore_no_migrations=True)
    except Exception as exc:
        result["graph_error"] = serialize_exception(exc)
        return result

    for (app, name) in sorted(disk_loader.disk_migrations):
        result["disk_migrations"].setdefault(app, []).append(name)

    result["conflicts"] = dict((k, sorted(v)) for k, v in disk_loader.detect_conflicts().items())
    for label in sorted(disk_loader.unmigrated_apps):
        try:
            config = apps.get_app_config(label)
        except LookupError:
            continue
        models = [m._meta.object_name for m in config.get_models() if m._meta.managed]
        if models:
            result["unmigrated_apps"].append({"label": label, "path": config.path, "models": models})

    # 2. Model changes that have no migration yet.
    if not result["conflicts"]:
        questioner = _make_questioner()
        try:
            autodetector = MigrationAutodetector(
                disk_loader.project_state(), ProjectState.from_apps(apps), questioner
            )
            changes = autodetector.changes(graph=disk_loader.graph)
            describe_state = disk_loader.project_state()
            for app_label in sorted(changes):
                entries = []
                for migration in changes[app_label]:
                    entries.append(
                        {
                            "name": migration.name,
                            "operations": _serialize_ops_progressively(
                                migration.operations, app_label, describe_state
                            ),
                        }
                    )
                result["changes"][app_label] = entries
            result["questions"] = questioner.questions
        except Exception as exc:
            result["autodetect_error"] = serialize_exception(exc)

    # 3. Database state (read-only).
    connection = connections[alias]
    if _sqlite_file_missing(connection):
        result["db_missing_file"] = True
        _pending_without_db(disk_loader, result, args)
        return result

    _limit_connect_timeout(connection)
    plan_done = False
    try:
        from django.db.migrations.exceptions import InconsistentMigrationHistory
        from django.db.migrations.executor import MigrationExecutor

        executor = MigrationExecutor(connection)
        loader = executor.loader
        try:
            loader.check_consistent_history(connection)
        except InconsistentMigrationHistory as exc:
            result["inconsistent_history"] = serialize_exception(exc)

        applied = set(loader.applied_migrations)
        result["applied_count"] = len(applied)
        result["ghost_migrations"] = sorted(
            ["%s.%s" % key for key in applied if key not in loader.disk_migrations
             and key[0] in loader.migrated_apps]
        )

        targets, explicit = _resolve_targets(loader, args.get("app_label"), args.get("migration_name"))
        if result["conflicts"] and not explicit:
            targets = loader.graph.leaf_nodes()
        plan = executor.migration_plan(targets)
        state = executor._create_project_state(with_applied_migrations=True)
        for migration, backwards in plan:
            if backwards:
                end_state = loader.project_state((migration.app_label, migration.name), at_end=True)
                ops = [_serialize_operation(op, migration.app_label, end_state) for op in migration.operations]
            else:
                ops = _serialize_ops_progressively(migration.operations, migration.app_label, state)
            result["pending"].append(
                {
                    "app": migration.app_label,
                    "name": migration.name,
                    "backwards": bool(backwards),
                    "atomic": bool(getattr(migration, "atomic", True)),
                    "file": _migration_file(migration),
                    "operations": ops,
                }
            )
        plan_done = True

        if args.get("schema", True):
            _check_schema(connection, apps, result)
        _count_rows_at_risk(connection, apps, result)
    except Exception as exc:
        result["db_error"] = serialize_exception(exc)
        if not plan_done:
            result["pending"] = []
            _pending_without_db(disk_loader, result, args)
    finally:
        try:
            connection.close()
        except Exception:
            pass
    return result


def _migration_file(migration):
    module = sys.modules.get(migration.__module__)
    path = getattr(module, "__file__", None)
    if path:
        try:
            return os.path.relpath(path, os.getcwd())
        except ValueError:
            return path
    return None


def _pending_without_db(disk_loader, result, args):
    """When the DB cannot be read, every on-disk migration is potentially pending."""
    graph = disk_loader.graph
    try:
        plan_nodes = []
        for leaf in graph.leaf_nodes():
            for node in graph.forwards_plan(leaf):
                if node not in plan_nodes:
                    plan_nodes.append(node)
        from django.db.migrations.state import ProjectState

        state = ProjectState(real_apps=getattr(disk_loader, "unmigrated_apps", None))
        for key in plan_nodes:
            migration = disk_loader.graph.nodes[key]
            ops = _serialize_ops_progressively(migration.operations, migration.app_label, state)
            result["pending"].append(
                {
                    "app": migration.app_label,
                    "name": migration.name,
                    "backwards": False,
                    "atomic": bool(getattr(migration, "atomic", True)),
                    "file": _migration_file(migration),
                    "operations": ops,
                    "assumed": True,
                }
            )
    except Exception:
        pass


def _check_schema(connection, apps, result):
    pending_apps = set(p["app"] for p in result["pending"] if not p["backwards"])
    with connection.cursor() as cursor:
        tables = set(t.lower() for t in connection.introspection.table_names(cursor))
        for model in apps.get_models(include_auto_created=True):
            meta = model._meta
            if not meta.managed or meta.proxy or getattr(meta, "swapped", False):
                continue
            if meta.app_label not in result["disk_migrations"]:
                continue  # unmigrated app: tables are not managed by migrations
            table = meta.db_table
            entry = {"model": meta.label, "table": table, "app_has_pending": meta.app_label in pending_apps}
            if table.lower() not in tables:
                result["schema"]["missing_tables"].append(entry)
                continue
            try:
                columns = set(
                    c.name.lower() for c in connection.introspection.get_table_description(cursor, table)
                )
            except Exception:
                continue
            for field in meta.local_concrete_fields:
                if field.column and field.column.lower() not in columns:
                    item = dict(entry)
                    item.update({"field": field.name, "column": field.column})
                    result["schema"]["missing_columns"].append(item)
    result["schema"]["checked"] = True


def _count_rows_at_risk(connection, apps, result):
    """Count rows/values that destructive operations would remove (read-only SELECTs)."""
    candidates = []
    for entry in result["pending"]:
        for op in entry["operations"]:
            candidates.append((entry["app"], op, entry["backwards"]))
    for app_label, entries in result["changes"].items():
        for mig in entries:
            for op in mig["operations"]:
                candidates.append((app_label, op, False))

    quote = connection.ops.quote_name
    with connection.cursor() as cursor:
        for app_label, op, backwards in candidates:
            kind = op["type"]
            removing_field = (kind == "RemoveField" and not backwards) or (kind == "AddField" and backwards)
            removing_model = (kind == "DeleteModel" and not backwards) or (kind == "CreateModel" and backwards)
            if not (removing_field or removing_model) or not op.get("model"):
                continue
            try:
                model = apps.get_model(app_label, op["model"])
                table = model._meta.db_table
            except LookupError:
                table = "%s_%s" % (app_label, op["model"].lower())
            key = "%s.%s" % (app_label, op["model"].lower())
            try:
                if removing_model:
                    cursor.execute("SELECT COUNT(*) FROM %s" % quote(table))
                    result["row_counts"][key] = {"rows": cursor.fetchone()[0]}
                else:
                    column = op.get("field_name")
                    old = op.get("old_field") or op.get("field") or {}
                    if old.get("db_column"):
                        column = old["db_column"]
                    elif old.get("related_model"):
                        column = "%s_id" % column
                    cursor.execute(
                        "SELECT COUNT(*) FROM %s WHERE %s IS NOT NULL" % (quote(table), quote(column))
                    )
                    result["row_counts"]["%s.%s" % (key, op.get("field_name"))] = {"non_null": cursor.fetchone()[0]}
            except Exception:
                continue


# -------------------------------------------------------------------- urls
def action_urls(args):
    setup_django()
    from django.urls import URLPattern, URLResolver, get_resolver

    patterns = []

    def view_name(callback):
        # Class-based views expose view_class; DRF viewsets expose cls.
        target = getattr(callback, "view_class", None) or getattr(callback, "cls", None) or callback
        module = getattr(target, "__module__", "?")
        name = getattr(target, "__qualname__", None) or getattr(target, "__name__", None) or repr(target)
        return "%s.%s" % (module, name)

    def walk(resolver_patterns, prefix, namespaces):
        for p in resolver_patterns:
            if isinstance(p, URLResolver):
                ns = namespaces + ([p.namespace] if p.namespace else [])
                walk(p.url_patterns, prefix + str(p.pattern), ns)
            elif isinstance(p, URLPattern):
                full_name = ":".join(namespaces + [p.name]) if p.name else None
                patterns.append(
                    {
                        "pattern": prefix + str(p.pattern),
                        "name": p.name,
                        "full_name": full_name,
                        "view": view_name(p.callback),
                    }
                )

    resolver = get_resolver()
    walk(resolver.url_patterns, "", [])
    namespaces = sorted(set(_namespace_keys(resolver)))
    return {"patterns": patterns, "namespaces": namespaces}


def _namespace_keys(resolver, prefix=""):
    keys = []
    try:
        for ns, (_prefix, sub) in resolver.namespace_dict.items():
            full = prefix + ns
            keys.append(full)
            keys.extend(_namespace_keys(sub, full + ":"))
    except Exception:
        pass
    return keys


# --------------------------------------------------------------- templates
def action_templates(args):
    setup_django()
    from django.template import TemplateDoesNotExist, TemplateSyntaxError, engines
    from django.template.loader import get_template

    result = {"found": {}, "missing": [], "syntax_errors": [], "available": [], "errors": []}
    project_root = os.path.realpath(os.getcwd())

    template_dirs = []
    try:
        for engine in engines.all():
            for d in getattr(engine, "template_dirs", []) or []:
                template_dirs.append(str(d))
    except Exception as exc:
        result["errors"].append(serialize_exception(exc))

    for base in template_dirs:
        if not os.path.isdir(base):
            continue
        for dirpath, dirnames, filenames in os.walk(base):
            for filename in filenames:
                rel = os.path.relpath(os.path.join(dirpath, filename), base).replace(os.sep, "/")
                if rel not in result["available"]:
                    result["available"].append(rel)
                if len(result["available"]) >= MAX_TEMPLATE_LISTING:
                    break
    result["template_dirs"] = template_dirs

    for name in args.get("names", []):
        try:
            template = get_template(name)
            origin = getattr(getattr(template, "origin", None), "name", None)
            result["found"][name] = origin
        except TemplateDoesNotExist:
            result["missing"].append(name)
        except TemplateSyntaxError as exc:
            result["syntax_errors"].append({"template": name, "message": _safe_str(exc)})
        except Exception as exc:
            result["errors"].append(serialize_exception(exc))

    if args.get("compile_project_templates"):
        checked = set(args.get("names", []))
        for base in template_dirs:
            real = os.path.realpath(base)
            if not real.startswith(project_root) or not os.path.isdir(base):
                continue
            for dirpath, dirnames, filenames in os.walk(base):
                for filename in filenames:
                    if not filename.endswith((".html", ".txt", ".xml", ".htm", ".email")):
                        continue
                    rel = os.path.relpath(os.path.join(dirpath, filename), base).replace(os.sep, "/")
                    if rel in checked:
                        continue
                    checked.add(rel)
                    try:
                        get_template(rel)
                    except TemplateSyntaxError as exc:
                        result["syntax_errors"].append({"template": rel, "message": _safe_str(exc)})
                    except Exception:
                        pass
    return result


# ------------------------------------------------------------------ static
def action_static(args):
    setup_django()
    from django.apps import apps

    if not apps.is_installed("django.contrib.staticfiles"):
        return {"installed": False, "missing": [], "found": {}}
    from django.contrib.staticfiles import finders

    found = {}
    missing = []
    for path in args.get("paths", []):
        try:
            location = finders.find(path)
        except Exception:
            location = None
        if location:
            found[path] = location if isinstance(location, str) else location[0]
        else:
            missing.append(path)
    return {"installed": True, "missing": missing, "found": found}


ACTIONS = {
    "packages": action_packages,
    "info": action_info,
    "checks": action_checks,
    "database": action_database,
    "migrations": action_migrations,
    "urls": action_urls,
    "templates": action_templates,
    "static": action_static,
}


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] not in ACTIONS:
        sys.stderr.write("usage: probe.py {%s} --input FILE --output FILE\n" % ",".join(ACTIONS))
        return 64
    action = argv[0]
    input_path = output_path = None
    if "--input" in argv:
        input_path = argv[argv.index("--input") + 1]
    if "--output" in argv:
        output_path = argv[argv.index("--output") + 1]
    args = {}
    if input_path:
        with open(input_path, "r") as fh:
            args = json.load(fh)

    # User code (settings.py, apps.py ...) may print; keep stdout clean anyway.
    real_stdout = sys.stdout
    sys.stdout = sys.stderr
    try:
        payload = {"protocol": PROBE_PROTOCOL, "ok": True, "data": ACTIONS[action](args)}
    except ProbeFailure as failure:
        payload = {
            "protocol": PROBE_PROTOCOL,
            "ok": False,
            "stage": failure.stage,
            "error": serialize_exception(failure.exc),
        }
    except BaseException as exc:  # SystemExit from user code, KeyboardInterrupt...
        if isinstance(exc, KeyboardInterrupt):
            raise
        payload = {"protocol": PROBE_PROTOCOL, "ok": False, "stage": "action", "error": serialize_exception(exc)}
    finally:
        sys.stdout = real_stdout

    text = json.dumps(payload, default=str)
    if output_path:
        with open(output_path, "w") as fh:
            fh.write(text)
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
