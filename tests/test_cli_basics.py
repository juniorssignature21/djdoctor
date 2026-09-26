from django_doctor import __version__
from django_doctor.cli import normalize_argv
from django_doctor.exit_codes import EXIT_CODE_DOCS, ExitCode


def test_normalize_argv_hoists_global_flags():
    assert normalize_argv(["doctor", "--verbose"]) == ["--verbose", "doctor"]
    assert normalize_argv(["migrate", "--dry-run", "-q"]) == ["-q", "migrate", "--dry-run"]
    assert normalize_argv(["--settings", "x.y", "doctor", "--project", "p"]) == ["--settings", "x.y", "--project", "p", "doctor"]


def test_normalize_argv_leaves_passthrough_alone():
    assert normalize_argv(["django", "migrate", "-v", "2"]) == ["django", "migrate", "-v", "2"]
    assert normalize_argv(["test", "students", "-v", "2"]) == ["test", "students", "-v", "2"]


def test_version(cli):
    result = cli(["--version"])
    assert result.exit_code == 0 and __version__ in result.output


def test_help_lists_commands_and_exit_codes(cli):
    result = cli(["--help"])
    assert result.exit_code == 0
    for command in ("doctor", "migrate", "migration-plan", "explain", "start", "django"):
        assert command in result.output
    assert "6 = Unsafe" in result.output


def test_exit_codes_are_stable():
    assert [int(c) for c in ExitCode] == [0, 1, 2, 3, 4, 5, 6, 130]
    assert set(EXIT_CODE_DOCS) == set(ExitCode)


def test_outside_project(cli, tmp_path):
    result = cli(["doctor"], cwd=tmp_path)
    assert result.exit_code == ExitCode.PROJECT_NOT_FOUND
    assert "No Django project detected." in result.output
    assert "searched the current directory and its parents" in result.output
    assert "Traceback" not in result.output
