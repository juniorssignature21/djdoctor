from django_doctor.diagnostics.envvars import loads_dotenv, scan_env_usage


def _scan(tmp_path, source):
    f = tmp_path / "settings.py"
    f.write_text(source)
    return {u.name: u for u in scan_env_usage([f])}


def test_os_environ_patterns(tmp_path):
    u = _scan(tmp_path, '''
import os
from os import getenv
SECRET_KEY = os.environ["SECRET_KEY"]
DEBUG = os.environ.get("DEBUG", "0") == "1"
DB_HOST = os.getenv("DB_HOST")
REDIS = getenv("REDIS_URL", "redis://")
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "x.settings")
''')
    assert u["SECRET_KEY"].required and u["SECRET_KEY"].setting == "SECRET_KEY"
    assert not u["DEBUG"].required and u["DEBUG"].has_default
    assert not u["DB_HOST"].required and not u["DB_HOST"].has_default
    assert u["REDIS_URL"].has_default
    assert "DJANGO_SETTINGS_MODULE" not in u


def test_django_environ(tmp_path):
    u = _scan(tmp_path, '''
import environ
env = environ.Env(DEBUG=(bool, False))
DEBUG = env("DEBUG")
SECRET_KEY = env("SECRET_KEY")
ALLOWED = env.list("ALLOWED_HOSTS", default=[])
DATABASES = {"default": env.db()}
TIMEOUT = env.int("TIMEOUT", 30)
''')
    assert not u["DEBUG"].required  # default declared in the Env scheme
    assert u["SECRET_KEY"].required
    assert not u["ALLOWED_HOSTS"].required
    assert u["DATABASE_URL"].required
    assert not u["TIMEOUT"].required


def test_decouple_and_dj_database_url(tmp_path):
    u = _scan(tmp_path, '''
from decouple import config
import dj_database_url
SECRET_KEY = config("SECRET_KEY")
DEBUG = config("DEBUG", default=False, cast=bool)
DATABASES = {"default": dj_database_url.config(default="sqlite:///db.sqlite3")}
''')
    assert u["SECRET_KEY"].required
    assert not u["DEBUG"].required
    assert not u["DATABASE_URL"].required


def test_unrelated_config_function_is_ignored(tmp_path):
    u = _scan(tmp_path, 'def config(x): return x\nA = config("NOT_AN_ENV")\n')
    assert "NOT_AN_ENV" not in u


def test_loads_dotenv(tmp_path):
    f = tmp_path / "s.py"
    f.write_text("from dotenv import load_dotenv\nload_dotenv()\n")
    assert loads_dotenv([f])
    f.write_text("X = 1\n")
    assert not loads_dotenv([f])
