"""Explanation rules without a project (text only)."""

import pytest

from django_doctor.exit_codes import ExitCode
from django_doctor.explain.engine import explain_text
from django_doctor.explain.models import Confidence
from django_doctor.risk import Risk

CASES = [
    ("django.db.utils.OperationalError: no such column: students_student.phone_number", "database.missing_column"),
    ('django.db.utils.ProgrammingError: column students_student.phone_number does not exist', "database.missing_column"),
    ("django.db.utils.OperationalError: (1054, \"Unknown column 'students_student.phone' in 'field list'\")", "database.missing_column"),
    ("django.db.utils.OperationalError: no such table: students_student", "database.missing_table"),
    ('django.db.utils.ProgrammingError: relation "students_student" does not exist', "database.missing_table"),
    ('django.db.utils.OperationalError: connection to server at "localhost" (127.0.0.1), port 5432 failed: Connection refused',
     "database.server_unreachable"),
    ('psycopg2.OperationalError: connection to server at "db", port 5432 failed: FATAL:  password authentication failed for user "app"',
     "database.auth_failed"),
    ('django.db.utils.OperationalError: connection to server failed: FATAL:  database "shop" does not exist', "database.database_missing"),
    ("django.db.utils.OperationalError: (1045, \"Access denied for user 'root'@'localhost' (using password: YES)\")", "database.auth_failed"),
    ("django.db.utils.OperationalError: unable to open database file", "database.sqlite_open"),
    ("django.db.utils.OperationalError: database is locked", "database.sqlite_locked"),
    ("django.db.utils.IntegrityError: UNIQUE constraint failed: auth_user.username", "database.integrity.unique"),
    ('django.db.utils.IntegrityError: null value in column "name" of relation "students_student" violates not-null constraint',
     "database.integrity.not_null"),
    ("django.db.utils.IntegrityError: FOREIGN KEY constraint failed", "database.integrity.foreign_key"),
    ("django.db.utils.DataError: value too long for type character varying(20)", "database.data_error"),
    ("django.urls.exceptions.NoReverseMatch: Reverse for 'dashboard' not found. 'dashboard' is not a valid view function or pattern name.",
     "urls.unknown_name"),
    ("django.urls.exceptions.NoReverseMatch: 'shop' is not a registered namespace", "urls.unknown_namespace"),
    ("django.urls.exceptions.NoReverseMatch: Reverse for 'detail' with arguments '('',)' not found. 1 pattern(s) tried: ['students/(?P<pk>[0-9]+)/\\\\Z']",
     "urls.wrong_arguments"),
    ("django.template.exceptions.TemplateDoesNotExist: students/list.html", "templates.does_not_exist"),
    ("django.template.exceptions.TemplateSyntaxError: Invalid block tag on line 3: 'static'. Did you forget to register or load this tag?",
     "templates.syntax"),
    ("django.core.exceptions.ImproperlyConfigured: The SECRET_KEY setting must not be empty.", "settings.empty_secret_key"),
    ("django.core.exceptions.ImproperlyConfigured: Requested setting INSTALLED_APPS, but settings are not configured. You must either define the environment variable DJANGO_SETTINGS_MODULE or call settings.configure() before accessing settings.",
     "settings.not_configured"),
    ("django.core.exceptions.ImproperlyConfigured: Error loading psycopg2 or psycopg module", "settings.missing_db_driver"),
    ("django.core.exceptions.ImproperlyConfigured: 'django.db.backends.postgres' isn't an available database backend or couldn't be imported.",
     "settings.bad_db_engine"),
    ("django.core.exceptions.ImproperlyConfigured: settings.DATABASES is improperly configured. Please supply the ENGINE value. Check settings documentation for more details.",
     "settings.no_database"),
    ("django.core.exceptions.ImproperlyConfigured: AUTH_USER_MODEL refers to model 'accounts.User' that has not been installed",
     "settings.auth_user_model"),
    ("django.core.exceptions.ImproperlyConfigured: Application labels aren't unique, duplicates: students", "settings.duplicate_app"),
    ("django.core.exceptions.ImproperlyConfigured: Set the DATABASE_URL environment variable", "environment.missing_variable"),
    ("decouple.UndefinedValueError: SECRET_KEY not found. Declare it as envvar or define a default value.", "environment.missing_variable"),
    ("ModuleNotFoundError: No module named 'corsheaders'", "imports.module_not_found"),
    ("ModuleNotFoundError: No module named 'django.utils.six'", "imports.removed_django_module"),
    ("ImportError: cannot import name 'ugettext_lazy' from 'django.utils.translation' (/x/__init__.py)", "imports.removed_django_api"),
    ("ImportError: cannot import name 'Student' from partially initialized module 'students.models' (most likely due to a circular import) (/app/students/models.py)",
     "imports.circular"),
    ("ImportError: cannot import name 'Foo' from 'students.models' (/app/students/models.py)", "imports.name_not_found"),
    ("django.core.exceptions.FieldError: Cannot resolve keyword 'nmae' into field. Choices are: id, name, phone", "models.unknown_lookup_field"),
    ("django.core.exceptions.FieldError: Unknown field(s) (emial) specified for Student", "models.form_unknown_field"),
    ("django.core.exceptions.FieldError: Related Field got invalid lookup: icontains", "models.related_lookup"),
    ("django.core.exceptions.ValidationError: ['“abc” value must be an integer.']", "models.validation"),
    ("students.models.Student.DoesNotExist: Student matching query does not exist.", "models.does_not_exist"),
    ("students.models.Student.MultipleObjectsReturned: get() returned more than one Student -- it returned 2!", "models.multiple_objects"),
    ("django.core.exceptions.AppRegistryNotReady: Apps aren't loaded yet.", "settings.apps_not_ready"),
    ("RuntimeError: Model class students.models.Student doesn't declare an explicit app_label and isn't in an application in INSTALLED_APPS.",
     "settings.model_not_installed"),
    ("django.core.exceptions.DisallowedHost: Invalid HTTP_HOST header: 'example.com'. You may need to add 'example.com' to ALLOWED_HOSTS.",
     "security.disallowed_host"),
    ("Forbidden (Origin checking failed - http://localhost:3000 does not match any trusted origins.): /api/login/", "security.csrf"),
    ("Forbidden (CSRF cookie not set.): /login/", "security.csrf"),
    ("Error: That port is already in use.", "server.port_in_use"),
    ("CommandError: Conflicting migrations detected; multiple leaf nodes in the migration graph: (0002_a, 0002_b in students).\nTo fix them run 'python manage.py makemigrations --merge'",
     "migrations.conflict"),
    ("django.db.migrations.exceptions.InconsistentMigrationHistory: Migration admin.0001_initial is applied before its dependency accounts.0001_initial on database 'default'.",
     "migrations.inconsistent_history"),
    ("django.db.migrations.exceptions.NodeNotFoundError: Migration students.0003_x dependencies reference nonexistent parent node ('students', '0002_y')",
     "migrations.node_not_found"),
    ("It is impossible to add a non-nullable field 'age' to student without specifying a default. This is because the database needs something to populate existing rows.",
     "migrations.non_nullable_field"),
    ("django.db.migrations.exceptions.IrreversibleError: Operation <RunPython <function f>> in students.0004_data is not reversible",
     "migrations.irreversible"),
    ("You have 2 unapplied migration(s). Your project may not work properly until you apply the migrations for app(s): students.",
     "migrations.unapplied"),
    ("django.core.management.base.SystemCheckError: SystemCheckError: System check identified some issues:\n\nERRORS:\nstudents.Student.name: (fields.E120) CharFields must define a 'max_length' attribute.",
     "settings.system_check"),
    ("django.core.exceptions.SynchronousOnlyOperation: You cannot call this from an async context - use a thread or sync_to_async.",
     "async.synchronous_only"),
    ("AttributeError: Manager isn't accessible via Student instances", "models.manager_on_instance"),
    ("AttributeError: 'WSGIRequest' object has no attribute 'is_ajax'", "models.removed_is_ajax"),
    ("ValueError: Field 'id' expected a number but got 'abc'.", "models.value_error_field"),
    ("ZeroDivisionError: division by zero", "generic"),
]


@pytest.mark.parametrize("text,rule_id", CASES, ids=[c[1] + f"#{i}" for i, c in enumerate(CASES)])
def test_rule_matches(text, rule_id):
    diagnosis = explain_text(text)
    assert diagnosis is not None
    assert diagnosis.rule_id == rule_id
    assert diagnosis.what_happened
    assert diagnosis.causes, "every diagnosis must offer at least one cause"


def test_missing_env_var_from_keyerror_in_settings():
    text = '''Traceback (most recent call last):
  File "/app/mysite/settings.py", line 5, in <module>
    SECRET_KEY = os.environ["DJANGO_SECRET_KEY"]
  File "<frozen os>", line 679, in __getitem__
KeyError: 'DJANGO_SECRET_KEY'
'''
    d = explain_text(text)
    assert d.rule_id == "environment.missing_variable"
    assert "DJANGO_SECRET_KEY" in d.what_happened
    assert d.exit_code == ExitCode.CONFIGURATION_ERROR


def test_plain_keyerror_is_not_env_var():
    text = '''Traceback (most recent call last):
  File "/app/students/views.py", line 5, in index
    value = data["name"]
KeyError: 'name'
'''
    assert explain_text(text).rule_id == "generic"


def test_without_project_nothing_is_marked_detected_for_database():
    d = explain_text("django.db.utils.OperationalError: no such column: students_student.phone_number")
    assert all(not e.verified for e in d.evidence)
    assert all(c.confidence is not Confidence.DETECTED for c in d.causes)
    assert d.risk is Risk.LOW
    assert "djdoctor migrate" in d.commands


def test_field_error_suggests_close_match():
    d = explain_text("django.core.exceptions.FieldError: Cannot resolve keyword 'nmae' into field. Choices are: id, name, phone")
    assert any("'name'" in c.text for c in d.causes)


def test_secrets_are_redacted_from_messages():
    d = explain_text("django.db.utils.OperationalError: could not connect to server: postgres://app:hunter2@db:5432/shop Connection refused")
    assert "hunter2" not in d.error_message
    assert "hunter2" not in str(d.to_dict())


def test_integrity_detail_values_are_masked():
    d = explain_text('django.db.utils.IntegrityError: duplicate key value violates unique constraint "users_email_key"\n'
                     'DETAIL:  Key (email)=(alice@example.com) already exists.')
    assert "alice@example.com" not in d.error_message


def test_diagnosis_serialises_to_json():
    import json

    d = explain_text("django.template.exceptions.TemplateDoesNotExist: students/list.html")
    data = json.loads(json.dumps(d.to_dict()))
    assert data["category"] == "templates"
    assert data["risk"] == "none"
