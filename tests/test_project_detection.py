import sys

import pytest

from django_doctor.exceptions import InterpreterError, ProjectNotFoundError
from django_doctor.project import detect_project, find_nested_manage_py


def test_detects_from_root_and_subdirectory(make_project):
    project = make_project()
    for start in (project.root, project.root / "students" / "migrations"):
        detected = detect_project(start)
        assert detected.root == project.root
        assert detected.manage_py == project.root / "manage.py"
        assert detected.settings_module == "mysite.settings"
        assert detected.settings.origin == "manage.py"
        assert detected.python == sys.executable


def test_settings_files(make_project):
    project = make_project()
    assert detect_project(project.root).settings_files() == [project.root / "mysite" / "settings.py"]


def test_settings_package(make_project):
    project = make_project()
    (project.root / "mysite" / "settings.py").rename(project.root / "mysite" / "base.py")
    project.write("mysite/settings/__init__.py", "from mysite.base import *\n")
    project.write("mysite/settings/dev.py", "DEBUG = True\n")
    files = detect_project(project.root).settings_files()
    assert [f.name for f in files] == ["__init__.py", "dev.py"]


def test_not_a_project(tmp_path):
    with pytest.raises(ProjectNotFoundError) as info:
        detect_project(tmp_path)
    assert "No Django project detected." in info.value.message
    assert "parents" in info.value.detail


def test_nested_project_hint(make_project, tmp_path):
    make_project("backend")
    assert find_nested_manage_py(tmp_path) == [tmp_path / "backend" / "manage.py"]
    with pytest.raises(ProjectNotFoundError) as info:
        detect_project(tmp_path)
    assert "backend" in info.value.hint


def test_settings_priority(make_project, monkeypatch):
    project = make_project()
    assert detect_project(project.root, settings="other.settings").settings.origin == "--settings"
    monkeypatch.setenv("DJANGO_SETTINGS_MODULE", "env.settings")
    detected = detect_project(project.root)
    assert detected.settings.module == "env.settings" and detected.settings.origin == "environment"
    assert any("overrides" in n for n in detected.notes)


def test_config_file(make_project):
    project = make_project()
    project.write("pyproject.toml", '[tool.djdoctor]\nsettings = "mysite.settings_ci"\nskip_checks = ["Security"]\n')
    detected = detect_project(project.root)
    assert detected.settings.module == "mysite.settings_ci" and detected.settings.origin == "config"
    assert detected.config.skip_checks == ["Security"]


def test_heuristic_settings(make_project):
    project = make_project()
    project.write("manage.py", "print('custom manage.py without settings')\n")
    detected = detect_project(project.root)
    assert detected.settings.module == "mysite.settings" and detected.settings.origin == "heuristic"


def test_bad_python(make_project):
    project = make_project()
    with pytest.raises(InterpreterError):
        detect_project(project.root, python="/nonexistent/python")


def test_env_file_is_loaded_into_subprocess_env(make_project):
    project = make_project()
    project.write(".env", "export API_TOKEN='abc'\n# comment\nOTHER=1\n")
    project.write(".djdoctor.toml", 'env_file = ".env"\n')
    env = detect_project(project.root).subprocess_env()
    assert env["API_TOKEN"] == "abc" and env["OTHER"] == "1"
    assert env["DJANGO_SETTINGS_MODULE"] == "mysite.settings"
