"""Security hardening: terminal injection, file permissions, secret exposure."""

import io
import os
import stat
import sys

import pytest

from django_doctor.ai.base import build_user_prompt
from django_doctor.explain.engine import explain_text
from django_doctor.explain.models import Category, Diagnosis
from django_doctor.output.console import Console
from django_doctor.output.formatters import render_diagnosis
from django_doctor.security import strip_control

MALICIOUS = "ValueError: bad \x1b]52;c;ZXZpbA==\x07 \x1b[2J\x1b]0;pwned\x07 \x1b[31mred\x1b[0m end\x08"


def test_strip_control_removes_escape_sequences():
    cleaned = strip_control(MALICIOUS)
    assert "\x1b" not in cleaned and "\x07" not in cleaned and "\x08" not in cleaned
    assert "red" in cleaned and "end" in cleaned and "pwned" not in cleaned
    assert strip_control("a\n\tb") == "a\n\tb"


def test_untrusted_log_cannot_inject_terminal_sequences():
    buf = io.StringIO()
    render_diagnosis(Console(stdout=buf, color=False), explain_text(MALICIOUS), show_raw=True)
    assert "\x1b" not in buf.getvalue() and "\x07" not in buf.getvalue()


def test_console_sanitises_every_path():
    buf = io.StringIO()
    c = Console(stdout=buf, stderr=buf, color=False)
    for method in (c.print, c.out, c.raw, c.error, c.warning, c.success, c.detail):
        method("x\x1b[2Jy")
    assert "\x1b" not in buf.getvalue()


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permissions")
@pytest.mark.slow
def test_backups_and_error_log_are_private(migrated_project, cli):
    migrated_project.set_models("from django.db import models\n\n\nclass Student(models.Model):\n"
                                "    name = models.CharField(max_length=100)\n")
    assert cli(["migrate", "--allow-destructive"], cwd=migrated_project.root).exit_code == 0
    state = migrated_project.root / ".djdoctor"
    [backup] = (state / "backups").glob("*.sqlite3")
    assert stat.S_IMODE(os.stat(state).st_mode) == 0o700
    assert stat.S_IMODE(os.stat(state / "backups").st_mode) == 0o700
    assert stat.S_IMODE(os.stat(backup).st_mode) == 0o600


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permissions")
@pytest.mark.slow
def test_last_error_log_is_private_and_redacted(migrated_project, cli):
    cli(["django", "shell", "-c", "raise RuntimeError('postgres://app:hunter2@db/x')"], cwd=migrated_project.root)
    log = migrated_project.root / ".djdoctor" / "last_error.log"
    assert stat.S_IMODE(os.stat(log).st_mode) == 0o600
    assert "hunter2" not in log.read_text()


@pytest.mark.slow
def test_probe_never_returns_storage_options(migrated_project, cli):
    import json

    migrated_project.append("mysite/settings.py", (
        "STORAGES = {'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage',\n"
        "  'OPTIONS': {'location': '/tmp', 'base_url': 'https://token-abc123@cdn.example.com/'}},\n"
        "  'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'}}\n"))
    out = cli(["info", "--json"], cwd=migrated_project.root).output
    assert "token-abc123" not in out
    assert json.loads(out)["static"]["storages"]["default"] == {"BACKEND": "django.core.files.storage.FileSystemStorage"}


def test_ai_payload_excludes_private_evidence_and_request_path():
    d = Diagnosis("x", Category.DATABASE, "t", "what", error_type="OperationalError",
                  error_message="password=hunter2", location="app/views.py:3\n    secret_code()",
                  request_path="/users/42/private")
    d.add_evidence("DATABASES['default']: host=db.internal.example", verified=True, private=True)
    d.add_evidence("Migration students.0002 is pending", verified=True)
    payload = build_user_prompt(d.to_dict())
    assert "db.internal.example" not in payload
    assert "Migration students.0002 is pending" in payload
    assert "hunter2" not in payload and "secret_code" not in payload and "/users/42" not in payload


def test_ai_preview_sends_nothing(cli, tmp_path):
    result = cli(["explain", "--ai-preview", "--text", "ValueError: x"], cwd=tmp_path)
    assert result.exit_code == 0
    assert "nothing was sent" in result.output and '"error_type": "ValueError"' in result.output
