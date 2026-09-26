"""The probe against real temporary projects (runs Django in a subprocess)."""

import pytest

from django_doctor.bridge.runner import ProbeRunner
from django_doctor.migration.planner import build_plan
from django_doctor.project import detect_project
from django_doctor.risk import Risk

pytestmark = pytest.mark.slow


def probe(project, action, args=None):
    return ProbeRunner(detect_project(project.root)).run(action, args)


def test_info(make_project):
    result = probe(make_project(), "info")
    assert result.ok
    data = result.data
    assert data["django"]["version"]
    assert data["settings_module"] == "mysite.settings"
    assert data["secret_key"]["set"] and "value" not in data["secret_key"]
    assert "test-secret-key" not in str(data)  # the probe never returns the secret
    labels = [a["label"] for a in data["installed_apps"]]
    assert "students" in labels
    students = next(a for a in data["installed_apps"] if a["label"] == "students")
    assert students["has_migrations"] and students["models"] == ["Student"]
    assert data["databases"]["default"]["engine"].endswith("sqlite3")


def test_database_missing_file_is_not_created(make_project):
    project = make_project()
    result = probe(project, "database")
    assert result.data["databases"]["default"]["missing_file"] is True
    assert not (project.root / "db.sqlite3").exists()


def test_database_unavailable(make_project):
    project = make_project()
    # An existing directory cannot be opened as an SQLite database.
    project.append("mysite/settings.py", 'DATABASES["default"]["NAME"] = BASE_DIR / "templates"\n')
    entry = probe(project, "database").data["databases"]["default"]
    assert entry["ok"] is False and entry["missing_file"] is False
    assert entry["error"]["type"] == "OperationalError"
    assert "unable to open database file" in entry["error"]["message"]


def test_invalid_settings(make_project):
    project = make_project()
    project.append("mysite/settings.py", "INSTALLED_APPS += ['does_not_exist_app']\n")
    result = probe(project, "info")
    assert not result.ok and result.stage == "apps"
    assert result.error_type == "ModuleNotFoundError"


def test_missing_environment_variable(make_project):
    project = make_project()
    project.append("mysite/settings.py", "SECRET_KEY = os.environ['DJANGO_SECRET_KEY']\n")
    result = probe(project, "info")
    assert not result.ok and result.stage == "settings" and result.error_type == "KeyError"


def test_settings_prints_do_not_break_the_probe(make_project):
    project = make_project()
    project.append("mysite/settings.py", "print('hello from settings')\n")
    assert probe(project, "info").ok


def migrations(project):
    result = probe(project, "migrations", {"schema": True})
    assert result.ok, result.error
    return result.data


def test_no_changes(migrated_project):
    data = migrations(migrated_project)
    plan = build_plan(data)
    assert plan.is_up_to_date and not plan.changes and not plan.pending
    assert data["applied_count"] > 10
    assert data["schema"]["checked"] and not data["schema"]["missing_columns"]


def test_new_model(migrated_project):
    migrated_project.append("students/models.py", "\n\nclass Course(models.Model):\n    title = models.CharField(max_length=50)\n")
    plan = build_plan(migrations(migrated_project))
    ops = [(o.op_type, o.model) for c in plan.changes for o in c.operations]
    assert ops == [("CreateModel", "Course")]
    assert plan.risk is Risk.NONE


def test_new_field(migrated_project):
    migrated_project.append("students/models.py", "    email = models.EmailField(blank=True)\n")
    plan = build_plan(migrations(migrated_project))
    [op] = [o for c in plan.changes for o in c.operations]
    assert op.op_type == "AddField" and op.field_name == "email" and op.risk is Risk.NONE
    assert plan.changes[0].proposed_name == "0002_student_email"


def test_new_not_null_field_without_default_needs_decision(migrated_project):
    migrated_project.append("students/models.py", "    age = models.IntegerField()\n")
    plan = build_plan(migrations(migrated_project))
    assert [q["field"] for q in plan.unresolved_questions] == ["age"]


def test_removed_field_is_destructive_with_row_count(migrated_project):
    migrated_project.set_models("from django.db import models\n\n\nclass Student(models.Model):\n    name = models.CharField(max_length=100)\n")
    plan = build_plan(migrations(migrated_project))
    [op] = plan.destructive_operations
    assert op.op_type == "RemoveField" and op.field_name == "phone" and op.model == "Student"
    assert op.data_at_risk.startswith("3 row(s)")


def test_renamed_field_is_ambiguous(migrated_project):
    migrated_project.set_models(
        "from django.db import models\n\n\nclass Student(models.Model):\n"
        "    name = models.CharField(max_length=100)\n"
        "    phone_number = models.CharField(max_length=20, blank=True)\n")
    plan = build_plan(migrations(migrated_project))
    [candidate] = plan.rename_candidates
    assert (candidate.old, candidate.new) == ("phone", "phone_number")
    # identical definitions: Django itself would have asked about a rename
    assert candidate.source == "django"
    assert plan.risk is Risk.HIGH


def test_pending_migration(migrated_project):
    migrated_project.append("students/models.py", "    email = models.EmailField(blank=True)\n")
    migrated_project.manage("makemigrations", "students")
    plan = build_plan(migrations(migrated_project))
    assert [m.label for m in plan.pending] == ["students.0002_student_email"]
    assert not plan.changes


def test_migration_conflict(migrated_project):
    for name in ("0002_a", "0002_b"):
        migrated_project.write(f"students/migrations/{name}.py", """\
            from django.db import migrations


            class Migration(migrations.Migration):
                dependencies = [("students", "0001_initial")]
                operations = []
            """)
    data = migrations(migrated_project)
    assert data["conflicts"] == {"students": ["0002_a", "0002_b"]}
    assert build_plan(data).blocking_problems


def test_faked_migration_schema_mismatch(migrated_project):
    migrated_project.append("students/models.py", "    email = models.EmailField(blank=True)\n")
    migrated_project.manage("makemigrations", "students")
    migrated_project.manage("migrate", "students", "--fake")
    plan = build_plan(migrations(migrated_project))
    assert [(c["model"], c["column"]) for c in plan.missing_columns] == [("students.Student", "email")]


def test_backwards_plan(migrated_project):
    data = probe(migrated_project, "migrations", {"app_label": "students", "migration_name": "zero"}).data
    plan = build_plan(data)
    assert plan.pending and plan.pending[0].backwards
    assert plan.destructive_operations  # unapplying CreateModel drops the table


def test_urls_and_templates(make_project):
    project = make_project()
    urls = probe(project, "urls").data
    names = {p["full_name"] for p in urls["patterns"]}
    assert "students:index" in names and "students" in urls["namespaces"]
    templates = probe(project, "templates", {"names": ["students/index.html", "nope.html"], "compile_project_templates": True}).data
    assert "students/index.html" in templates["found"] and templates["missing"] == ["nope.html"]
