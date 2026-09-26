import json

import pytest

from django_doctor.exit_codes import ExitCode

pytestmark = pytest.mark.slow


def statuses(output_json):
    return {r["id"]: r["status"] for r in output_json["results"]}


def test_healthy_project(migrated_project, cli):
    result = cli(["doctor"], cwd=migrated_project.root)
    assert result.exit_code == 0, result.output
    for category in ("Project", "Settings", "Database", "Migrations", "URLs", "Templates", "System checks"):
        assert category in result.output
    assert "Summary:" in result.output and "0 errors" in result.output


def test_doctor_json(migrated_project, cli):
    data = json.loads(cli(["doctor", "--json"], cwd=migrated_project.root).output)
    s = statuses(data)
    assert s["settings.load"] == "ok"
    assert s["database.default"] == "ok"
    assert s["migrations.ok"] == "ok"
    assert data["summary"]["errors"] == 0


def test_pending_and_missing_migrations(migrated_project, cli):
    migrated_project.append("students/models.py", "    email = models.EmailField(blank=True)\n")
    result = cli(["doctor"], cwd=migrated_project.root)
    assert result.exit_code == 0  # warnings only
    assert "1 model change(s) have not been migrated" in result.output
    assert "is missing although its migrations are applied" not in result.output
    strict = cli(["doctor", "--strict"], cwd=migrated_project.root)
    assert strict.exit_code == ExitCode.GENERAL_ERROR


def test_invalid_settings_are_explained(make_project, cli):
    project = make_project()
    project.append("mysite/settings.py", "INSTALLED_APPS += ['corsheaders']\n")
    result = cli(["doctor"], cwd=project.root)
    assert result.exit_code == ExitCode.CONFIGURATION_ERROR
    assert "INSTALLED_APPS could not be loaded" in result.output
    assert "IMPORT ERROR" in result.output
    assert "django-cors-headers" in result.output
    assert "Skipped: settings could not be loaded" in result.output
    assert "Traceback (most recent call last)" not in result.output


def test_missing_env_variable(make_project, cli):
    project = make_project()
    project.append("mysite/settings.py", "SECRET_KEY = os.environ['DJANGO_SECRET_KEY']\nAPI_URL = os.getenv('API_URL')\n")
    result = cli(["doctor"], cwd=project.root)
    assert result.exit_code == ExitCode.CONFIGURATION_ERROR
    assert "The settings read the environment variable DJANGO_SECRET_KEY, but it is not set." in result.output
    assert "1 required environment variable(s) are not set" in result.output
    assert "API_URL" in result.output


def test_env_var_in_unloaded_dotenv(make_project, cli):
    project = make_project()
    project.append("mysite/settings.py", "API_URL = os.getenv('API_URL')\n")
    project.write(".env", "API_URL=https://secret.example.com/token123\n")
    result = cli(["doctor", "--only", "Environment"], cwd=project.root)
    assert "defined in .env but nothing loads that file" in result.output
    assert "token123" not in result.output  # values are never shown


def test_unknown_url_name_and_missing_template(migrated_project, cli):
    migrated_project.write("students/views.py", """\
        from django.shortcuts import redirect, render


        def index(request):
            return render(request, "students/missing.html")


        def go(request):
            return redirect("students:indx")
        """)
    migrated_project.write("templates/base.html", "{% load static %}<a href=\"{% url 'dashboard' %}\">x</a>"
                           "<img src=\"{% static 'img/logo.png' %}\">{% url 'maybe' as u %}")
    result = cli(["doctor"], cwd=migrated_project.root)
    assert result.exit_code == ExitCode.CONFIGURATION_ERROR, result.output
    assert "'students:indx' used at students/views.py:9 — did you mean 'students:index'?" in result.output
    assert "'dashboard' used at templates/base.html:1" in result.output
    assert "'maybe'" not in result.output  # {% url ... as var %} never raises
    assert "'students/missing.html' used at students/views.py:5" in result.output
    assert "'img/logo.png'" in result.output


def test_template_syntax_error(migrated_project, cli):
    migrated_project.write("templates/broken.html", "{% block content %}\n{% static 'x.css' %}\n{% endblock %}")
    result = cli(["doctor", "--only", "Templates"], cwd=migrated_project.root)
    assert "Template syntax error in broken.html" in result.output
    assert "Invalid block tag on line 2: 'static'" in result.output


def test_production_settings(migrated_project, cli):
    migrated_project.append("mysite/settings.py", "DEBUG = False\nSECRET_KEY = 'django-insecure-abc'\n"
                            "CSRF_TRUSTED_ORIGINS = ['example.com']\n")
    data = json.loads(cli(["doctor", "--json"], cwd=migrated_project.root).output)
    s = statuses(data)
    assert s["settings.allowed_hosts"] == "error"
    assert s["settings.secret_key"] == "error"
    assert s["security.csrf_trusted_origins"] == "error"
    assert "django-insecure-abc" not in json.dumps(data)


def test_cors_settings_without_app(migrated_project, cli):
    migrated_project.append("mysite/settings.py", "CORS_ALLOW_ALL_ORIGINS = True\n")
    data = json.loads(cli(["doctor", "--json", "--only", "Security"], cwd=migrated_project.root).output)
    assert statuses(data)["security.cors_unused"] == "warning"


def test_database_unreachable(migrated_project, cli):
    migrated_project.append("mysite/settings.py", 'DATABASES["default"]["NAME"] = BASE_DIR / "templates"\n')
    result = cli(["doctor"], cwd=migrated_project.root)
    assert result.exit_code == ExitCode.DATABASE_ERROR
    assert "'default' is not reachable" in result.output
    assert "SQLite could not open or create the database file." in result.output


def test_skip_categories(migrated_project, cli):
    data = json.loads(cli(["doctor", "--json", "--skip", "Database", "--skip", "Migrations"], cwd=migrated_project.root).output)
    assert not any(r["category"] in ("Database", "Migrations") for r in data["results"])


def test_system_check_errors(migrated_project, cli):
    migrated_project.append("students/models.py", "    code = models.CharField()\n")
    check = cli(["check"], cwd=migrated_project.root)
    if "fields.E120" in check.output:  # Django < 6.0 requires max_length
        assert check.exit_code == ExitCode.CONFIGURATION_ERROR
        assert "CharField needs a maximum length" in check.output
