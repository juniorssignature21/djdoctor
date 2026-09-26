import json

import pytest

from django_doctor.exit_codes import ExitCode

pytestmark = pytest.mark.slow


def test_passthrough_runs_django_command(migrated_project, cli, capfd):
    result = cli(["django", "showmigrations", "students"], cwd=migrated_project.root)
    assert result.exit_code == 0
    out = capfd.readouterr().out
    assert "0001_initial" in out


def test_passthrough_failure_is_explained(migrated_project, cli):
    result = cli(["django", "migrate", "students", "9999"], cwd=migrated_project.root)
    assert result.exit_code != 0
    assert "MIGRATION ERROR" in result.output or "Cannot find a migration" in result.output


def test_flush_requires_confirmation(migrated_project, cli):
    result = cli(["django", "flush", "--noinput"], cwd=migrated_project.root)
    assert result.exit_code == ExitCode.UNSAFE_OPERATION
    assert "delete ALL data" in result.output
    assert migrated_project.sql("select count(*) from students_student") == [(3,)]


def test_info_redacts_secrets(migrated_project, cli):
    migrated_project.append("mysite/settings.py", "DATABASES['default']['PASSWORD'] = 'hunter2'\n")
    result = cli(["info"], cwd=migrated_project.root)
    assert result.exit_code == 0, result.output
    assert "hunter2" not in result.output and "test-secret-key" not in result.output
    assert "********" in result.output
    data = json.loads(cli(["info", "--json"], cwd=migrated_project.root).output)
    assert data["secret_key"]["value"] == "********"
    assert "hunter2" not in json.dumps(data)


def test_urls(migrated_project, cli):
    data = json.loads(cli(["urls", "--json", "--filter", "students"], cwd=migrated_project.root).output)
    assert [p["full_name"] for p in data] == ["students:index"]
    table = cli(["urls"], cwd=migrated_project.root)
    assert "students:index" in table.output and "students.views.index" in table.output


def test_check_clean(migrated_project, cli):
    result = cli(["check"], cwd=migrated_project.root)
    assert result.exit_code == 0 and "no issues" in result.output


def test_check_deploy_warnings(migrated_project, cli):
    result = cli(["check", "--deploy", "--fail-level", "WARNING"], cwd=migrated_project.root)
    assert result.exit_code == ExitCode.CONFIGURATION_ERROR
    assert "security.W018" in result.output  # DEBUG=True


def test_collectstatic_requires_static_root(migrated_project, cli):
    result = cli(["collectstatic", "--yes"], cwd=migrated_project.root)
    assert result.exit_code == ExitCode.CONFIGURATION_ERROR
    assert "STATIC_ROOT is not set" in result.output


def test_collectstatic(migrated_project, cli):
    migrated_project.append("mysite/settings.py", "STATIC_ROOT = BASE_DIR / 'staticfiles'\n")
    no = cli(["collectstatic"], cwd=migrated_project.root)
    assert no.exit_code == ExitCode.UNSAFE_OPERATION  # non-interactive without --yes
    result = cli(["collectstatic", "--yes"], cwd=migrated_project.root)
    assert result.exit_code == 0, result.output
    assert (migrated_project.root / "staticfiles" / "admin").is_dir()


def test_start_refuses_when_settings_broken(make_project, cli):
    project = make_project()
    project.append("mysite/settings.py", "SECRET_KEY = ''\n")
    result = cli(["start", "--noreload"], cwd=project.root)
    assert result.exit_code == ExitCode.CONFIGURATION_ERROR
    assert "SECRET_KEY is empty" in result.output


def test_test_command_runs_suite(migrated_project, cli):
    migrated_project.write("students/tests.py", """\
        from django.test import TestCase


        class T(TestCase):
            def test_ok(self):
                self.assertEqual(1, 1)
        """)
    result = cli(["test", "students"], cwd=migrated_project.root)
    assert result.exit_code == 0, result.output


def test_test_command_explains_environment_errors(migrated_project, cli):
    migrated_project.write("students/tests.py", "import missing_package_xyz\n")
    result = cli(["test", "students"], cwd=migrated_project.root)
    assert result.exit_code != 0
    assert "missing_package_xyz" in result.output
