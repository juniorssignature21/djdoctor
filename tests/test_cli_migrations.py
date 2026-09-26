"""End-to-end migration commands against real projects."""

import json

import pytest

from django_doctor.exit_codes import ExitCode

pytestmark = pytest.mark.slow

NO_PHONE = "from django.db import models\n\n\nclass Student(models.Model):\n    name = models.CharField(max_length=100)\n"
RENAMED = NO_PHONE + "    phone_number = models.CharField(max_length=20, blank=True)\n"


def test_migrate_fresh_project_creates_and_applies(make_project, cli):
    project = make_project()
    result = cli(["migrate"], cwd=project.root)
    assert result.exit_code == 0, result.output
    assert "Model changes detected" in result.output
    assert "students/migrations/0001_initial.py" in result.output
    assert "Migration completed successfully." in result.output
    assert project.migration_files() == ["0001_initial.py"]
    assert project.sql("select count(*) from django_migrations where app='students'") == [(1,)]
    # Second run: nothing to do
    again = cli(["migrate"], cwd=project.root)
    assert again.exit_code == 0 and "No migrations to apply." in again.output


def test_migrate_new_field(migrated_project, cli):
    migrated_project.append("students/models.py", "    email = models.EmailField(blank=True)\n")
    result = cli(["migrate"], cwd=migrated_project.root)
    assert result.exit_code == 0, result.output
    assert "+ Add field email to student" in result.output
    assert "1 migration(s) applied" in result.output
    columns = [c[1] for c in migrated_project.sql("pragma table_info(students_student)")]
    assert "email" in columns


def test_migrate_dry_run_changes_nothing(migrated_project, cli):
    migrated_project.append("students/models.py", "    email = models.EmailField(blank=True)\n")
    result = cli(["migrate", "--dry-run"], cwd=migrated_project.root)
    assert result.exit_code == 0, result.output
    assert "Dry run" in result.output
    assert migrated_project.migration_files() == ["0001_initial.py"]


def test_removed_field_requires_confirmation(migrated_project, cli):
    migrated_project.set_models(NO_PHONE)
    result = cli(["migrate", "--yes"], cwd=migrated_project.root)
    assert result.exit_code == ExitCode.UNSAFE_OPERATION, result.output
    assert "Potentially destructive migration" in result.output
    assert "Remove field phone from student" in result.output
    assert "3 row(s) currently have a value" in result.output
    assert "will not create and apply this migration automatically" in result.output
    assert "--yes does not confirm destructive operations" in result.output
    # Nothing was written and the data is intact
    assert migrated_project.migration_files() == ["0001_initial.py"]
    assert len(migrated_project.sql("select phone from students_student")) == 3


def test_renamed_field_is_not_assumed(migrated_project, cli):
    migrated_project.set_models(RENAMED)
    result = cli(["migrate"], cwd=migrated_project.root)
    assert result.exit_code == ExitCode.UNSAFE_OPERATION
    assert "does not assume they hold the same data" in result.output
    assert "RenameField" in result.output
    assert "Copy existing phone values into phone_number" in result.output


def test_allow_destructive_backs_up_sqlite(migrated_project, cli):
    migrated_project.set_models(NO_PHONE)
    result = cli(["migrate", "--allow-destructive"], cwd=migrated_project.root)
    assert result.exit_code == 0, result.output
    assert "Backup created" in result.output
    backups = list((migrated_project.root / ".djdoctor" / "backups").glob("*.sqlite3"))
    assert len(backups) == 1
    import sqlite3

    assert len(sqlite3.connect(backups[0]).execute("select phone from students_student").fetchall()) == 3
    assert "phone" not in [c[1] for c in migrated_project.sql("pragma table_info(students_student)")]


def test_existing_destructive_migration_file_is_gated(migrated_project, cli):
    migrated_project.set_models(NO_PHONE)
    migrated_project.manage("makemigrations", "students")  # e.g. pulled from a teammate
    result = cli(["migrate", "--no-makemigrations"], cwd=migrated_project.root)
    assert result.exit_code == ExitCode.UNSAFE_OPERATION
    assert "will not apply this migration automatically" in result.output
    assert len(migrated_project.sql("select phone from students_student")) == 3


def test_not_null_field_without_default(migrated_project, cli):
    migrated_project.append("students/models.py", "    age = models.IntegerField()\n")
    result = cli(["migrate"], cwd=migrated_project.root)
    assert result.exit_code == ExitCode.MIGRATION_PROBLEM
    assert "needs a value for existing rows" in result.output
    assert migrated_project.migration_files() == ["0001_initial.py"]


def test_migration_conflict_stops(migrated_project, cli):
    for name in ("0002_a", "0002_b"):
        migrated_project.write(f"students/migrations/{name}.py", """\
            from django.db import migrations


            class Migration(migrations.Migration):
                dependencies = [("students", "0001_initial")]
                operations = []
            """)
    result = cli(["migrate"], cwd=migrated_project.root)
    assert result.exit_code == ExitCode.MIGRATION_PROBLEM
    assert "Conflicting migrations in 'students'" in result.output
    assert "makemigrations --merge" in result.output


def test_failed_migration_is_explained(migrated_project, cli):
    migrated_project.write("students/migrations/0002_broken.py", """\
        from django.db import migrations


        class Migration(migrations.Migration):
            dependencies = [("students", "0001_initial")]
            operations = [migrations.RunSQL("INSERT INTO students_student (name, phone) VALUES (NULL, 'x')")]
        """)
    result = cli(["migrate", "--yes"], cwd=migrated_project.root)
    assert result.exit_code == ExitCode.MIGRATION_PROBLEM, result.output
    assert "Migration failed" in result.output
    assert "NOT NULL" in result.output
    assert (migrated_project.root / ".djdoctor" / "last_error.log").is_file()


def test_unapply_to_zero_is_destructive(migrated_project, cli):
    result = cli(["migrate", "students", "zero"], cwd=migrated_project.root)
    assert result.exit_code == ExitCode.UNSAFE_OPERATION
    assert "Undo: Create model Student" in result.output


def test_migration_plan_is_read_only(migrated_project, cli):
    migrated_project.set_models(RENAMED)
    result = cli(["migration-plan"], cwd=migrated_project.root)
    assert result.exit_code == 0, result.output
    for text in ("Migration Plan", "App: students", "Remove field phone", "Add field phone_number",
                 "Risk: HIGH", "Recommended strategy", "No changes have been applied."):
        assert text in result.output
    assert migrated_project.migration_files() == ["0001_initial.py"]
    check = cli(["migration-plan", "--check"], cwd=migrated_project.root)
    assert check.exit_code == ExitCode.MIGRATION_PROBLEM


def test_migration_plan_json(migrated_project, cli):
    migrated_project.set_models(NO_PHONE)
    result = cli(["migration-plan", "--json"], cwd=migrated_project.root)
    data = json.loads(result.output)
    assert data["risk"] == "high"
    op = data["changes"][0]["operations"][0]
    assert op["operation"] == "RemoveField" and op["destructive"] is True


def test_makemigrations_check_and_create(migrated_project, cli):
    migrated_project.append("students/models.py", "    email = models.EmailField(blank=True)\n")
    check = cli(["makemigrations", "--check"], cwd=migrated_project.root)
    assert check.exit_code == ExitCode.MIGRATION_PROBLEM
    assert migrated_project.migration_files() == ["0001_initial.py"]
    created = cli(["makemigrations"], cwd=migrated_project.root)
    assert created.exit_code == 0, created.output
    assert migrated_project.migration_files() == ["0001_initial.py", "0002_student_email.py"]
    none = cli(["makemigrations"], cwd=migrated_project.root)
    assert "No changes detected." in none.output


def test_makemigrations_destructive_gate(migrated_project, cli):
    migrated_project.set_models(NO_PHONE)
    result = cli(["makemigrations"], cwd=migrated_project.root)
    assert result.exit_code == ExitCode.UNSAFE_OPERATION
    ok = cli(["makemigrations", "--allow-destructive", "--name", "drop_phone"], cwd=migrated_project.root)
    assert ok.exit_code == 0, ok.output
    assert "0002_drop_phone.py" in migrated_project.migration_files()
