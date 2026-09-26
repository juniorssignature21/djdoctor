import json

import pytest

from django_doctor.exit_codes import ExitCode

NO_REVERSE = ("Traceback (most recent call last):\n  File \"/app/students/views.py\", line 3, in v\n"
              "    return redirect('dashbord')\n"
              "django.urls.exceptions.NoReverseMatch: Reverse for 'dashbord' not found. "
              "'dashbord' is not a valid view function or pattern name.\n")


def test_explain_text(cli, tmp_path):
    result = cli(["explain", "--text", "django.db.utils.OperationalError: no such table: students_student"], cwd=tmp_path)
    assert result.exit_code == 0
    for heading in ("DATABASE ERROR", "What happened", "Recommended fix", "Commands to try", "Risk level"):
        assert heading in result.output
    assert "Likely cause:" in result.output


def test_explain_file_and_json(cli, tmp_path):
    log = tmp_path / "error.log"
    log.write_text("some noise\n" + NO_REVERSE + "[26/Sep/2026] \"GET / HTTP/1.1\" 500\n")
    data = json.loads(cli(["explain", str(log), "--json"], cwd=tmp_path).output)
    assert data["rule_id"] == "urls.unknown_name"
    assert data["location"].startswith("/app/students/views.py:3")


def test_explain_stdin(cli, tmp_path):
    result = cli(["explain"], cwd=tmp_path, input="Starting server\n" + NO_REVERSE)
    assert result.exit_code == 0
    assert "Starting server" in result.output  # piped input is echoed through
    assert "URL ERROR" in result.output


def test_explain_nothing_found(cli, tmp_path):
    result = cli(["explain", "-"], cwd=tmp_path, input="all good\n")
    assert result.exit_code == ExitCode.GENERAL_ERROR


def test_explain_without_input_or_saved_error(cli, make_project):
    project = make_project()
    result = cli(["explain"], cwd=project.root)
    assert result.exit_code == ExitCode.GENERAL_ERROR
    assert "No error to explain." in result.output


def test_explain_missing_file(cli, tmp_path):
    assert cli(["explain", str(tmp_path / "nope.log")], cwd=tmp_path).exit_code == ExitCode.GENERAL_ERROR


@pytest.mark.slow
def test_explain_uses_last_captured_error_and_project_facts(migrated_project, cli):
    migrated_project.append("students/models.py", "    email = models.EmailField(blank=True)\n")
    migrated_project.manage("makemigrations", "students")
    failing = cli(["django", "shell", "-c", "from students.models import Student; list(Student.objects.all())"],
                  cwd=migrated_project.root)
    assert failing.exit_code != 0
    assert (migrated_project.root / ".djdoctor" / "last_error.log").is_file()
    result = cli(["explain"], cwd=migrated_project.root)
    assert result.exit_code == 0, result.output
    assert "students_student.email" in result.output
    assert "Migration students.0002_student_email (Add field email to student) exists but is NOT applied." in result.output
    assert "Migration status: Pending" in result.output
    assert "Detected:" in result.output
    no_inspect = cli(["explain", "--no-inspect"], cwd=migrated_project.root)
    assert "Detected:" not in no_inspect.output and "Likely cause:" in no_inspect.output


@pytest.mark.slow
def test_explain_suggests_existing_url_name(migrated_project, cli):
    text = NO_REVERSE.replace("dashbord", "index")
    result = cli(["explain", "--text", text], cwd=migrated_project.root)
    assert "The URL is namespaced. Use one of 'admin:index', 'students:index' instead of 'index'." in result.output
