"""Test fixtures: real, temporary Django projects.

``make_project`` writes a small but complete project (manage.py, settings,
urls, one app). ``migrated_project`` copies a session-wide project whose
database has already been migrated, which keeps slow tests fast.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import textwrap
from dataclasses import dataclass
from pathlib import Path

import pytest
from typer.testing import CliRunner

from django_doctor.cli import app, normalize_argv

SETTINGS_TEMPLATE = '''\
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
SECRET_KEY = "test-secret-key-for-django-doctor-0123456789-abcdefghijklmnopqrstuvwxyz"
DEBUG = True
ALLOWED_HOSTS = []
INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "students",
]
MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
]
ROOT_URLCONF = "mysite.urls"
TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]
DATABASES = {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": BASE_DIR / "db.sqlite3"}}
STATIC_URL = "static/"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
'''

MANAGE_PY = '''\
#!/usr/bin/env python
import os
import sys


def main():
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "mysite.settings")
    from django.core.management import execute_from_command_line

    execute_from_command_line(sys.argv)


if __name__ == "__main__":
    main()
'''

URLS = '''\
from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("admin/", admin.site.urls),
    path("students/", include("students.urls")),
]
'''

APP_URLS = '''\
from django.urls import path

from . import views

app_name = "students"
urlpatterns = [
    path("", views.index, name="index"),
]
'''

VIEWS = '''\
from django.shortcuts import render


def index(request):
    return render(request, "students/index.html")
'''

MODELS = '''\
from django.db import models


class Student(models.Model):
    name = models.CharField(max_length=100)
    phone = models.CharField(max_length=20, blank=True)
'''


@dataclass
class Project:
    root: Path

    def write(self, relative: str, content: str) -> Path:
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(textwrap.dedent(content), encoding="utf-8")
        return path

    def append(self, relative: str, content: str) -> None:
        path = self.root / relative
        path.write_text(path.read_text(encoding="utf-8") + content, encoding="utf-8")

    def set_models(self, content: str) -> None:
        self.write("students/models.py", content)

    def manage(self, *args: str, check: bool = True) -> subprocess.CompletedProcess:
        env = clean_env()
        proc = subprocess.run([sys.executable, "manage.py", *args], cwd=self.root, env=env,
                              capture_output=True, text=True)
        if check and proc.returncode != 0:
            raise AssertionError(f"manage.py {' '.join(args)} failed:\n{proc.stdout}\n{proc.stderr}")
        return proc

    def migration_files(self, app: str = "students") -> list[str]:
        return sorted(p.name for p in (self.root / app / "migrations").glob("0*.py"))

    def sql(self, query: str):
        import sqlite3

        con = sqlite3.connect(self.root / "db.sqlite3")
        try:
            return con.execute(query).fetchall()
        finally:
            con.close()


def clean_env() -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not k.startswith(("DJANGO_", "DJDOCTOR_"))}
    return env


def create_project(root: Path, *, models: str = MODELS, with_initial_migration: bool = False) -> Project:
    project = Project(root)
    root.mkdir(parents=True, exist_ok=True)
    project.write("manage.py", MANAGE_PY)
    project.write("mysite/__init__.py", "")
    project.write("mysite/settings.py", SETTINGS_TEMPLATE)
    project.write("mysite/urls.py", URLS)
    project.write("students/__init__.py", "")
    project.write("students/apps.py", """\
        from django.apps import AppConfig


        class StudentsConfig(AppConfig):
            default_auto_field = "django.db.models.BigAutoField"
            name = "students"
        """)
    project.write("students/migrations/__init__.py", "")
    project.write("students/models.py", models)
    project.write("students/urls.py", APP_URLS)
    project.write("students/views.py", VIEWS)
    project.write("students/templates/students/index.html", "{% load static %}<a href=\"{% url 'students:index' %}\">home</a>\n")
    (root / "templates").mkdir(exist_ok=True)
    if with_initial_migration:
        project.manage("makemigrations", "students", "--noinput")
    return project


@pytest.fixture(autouse=True)
def _isolate_environment(monkeypatch):
    monkeypatch.setattr("django_doctor.explain.engine.RAISE_RULE_ERRORS", True)
    for key in list(os.environ):
        if key.startswith(("DJANGO_", "DJDOCTOR_")):
            monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("COLUMNS", "200")
    monkeypatch.setenv("NO_COLOR", "1")


@pytest.fixture
def make_project(tmp_path):
    def factory(name: str = "proj", **kwargs) -> Project:
        return create_project(tmp_path / name, **kwargs)

    return factory


@pytest.fixture(scope="session")
def _migrated_template(tmp_path_factory) -> Path:
    root = tmp_path_factory.mktemp("template") / "proj"
    project = create_project(root, with_initial_migration=True)
    project.manage("migrate", "--noinput")
    project.manage("shell", "-c", "from students.models import Student\n"
                   "for i in range(3): Student.objects.create(name=f's{i}', phone=f'555-000{i}')")
    return root


@pytest.fixture
def migrated_project(_migrated_template, tmp_path) -> Project:
    """A migrated project whose Student table contains 3 rows with a phone number."""
    target = tmp_path / "proj"
    shutil.copytree(_migrated_template, target, ignore=shutil.ignore_patterns("__pycache__", ".djdoctor"))
    return Project(target)


@dataclass
class CliResult:
    exit_code: int
    output: str
    exception: BaseException | None


@pytest.fixture
def cli(monkeypatch):
    runner = CliRunner()

    def invoke(args: list[str], cwd: Path | None = None, input: str | None = None) -> CliResult:
        if cwd is not None:
            monkeypatch.chdir(cwd)
        result = runner.invoke(app, normalize_argv(list(args)), input=input, catch_exceptions=True)
        exc = result.exception if not isinstance(result.exception, SystemExit) else None
        return CliResult(result.exit_code, result.output, exc)

    return invoke
