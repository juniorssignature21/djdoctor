"""Settings, environment variables, imports and dependencies."""

from __future__ import annotations

import os
import re

from django_doctor.branding import CLI_NAME
from django_doctor.diagnostics.envvars import loads_dotenv, scan_env_usage
from django_doctor.exit_codes import ExitCode
from django_doctor.explain.models import Category, Confidence, Diagnosis
from django_doctor.explain.rules.base import RuleContext, close_matches, rule
from django_doctor.project import env_file_keys
from django_doctor.risk import Risk

#: import name -> pip distribution name, for common packages whose names differ.
PIP_NAMES = {
    "rest_framework": "djangorestframework", "corsheaders": "django-cors-headers",
    "environ": "django-environ", "decouple": "python-decouple", "dotenv": "python-dotenv",
    "psycopg2": "psycopg2-binary", "psycopg": "psycopg[binary]", "MySQLdb": "mysqlclient",
    "PIL": "Pillow", "yaml": "PyYAML", "debug_toolbar": "django-debug-toolbar",
    "crispy_forms": "django-crispy-forms", "crispy_bootstrap5": "crispy-bootstrap5",
    "storages": "django-storages", "django_filters": "django-filter", "allauth": "django-allauth",
    "whitenoise": "whitenoise", "channels": "channels", "celery": "celery", "redis": "redis",
    "dj_database_url": "dj-database-url", "django_extensions": "django-extensions",
    "rest_framework_simplejwt": "djangorestframework-simplejwt", "drf_spectacular": "drf-spectacular",
    "import_export": "django-import-export", "widget_tweaks": "django-widget-tweaks",
    "taggit": "django-taggit", "guardian": "django-guardian", "django_celery_beat": "django-celery-beat",
    "django_celery_results": "django-celery-results", "phonenumber_field": "django-phonenumber-field",
    "phonenumbers": "phonenumbers", "ckeditor": "django-ckeditor", "tinymce": "django-tinymce",
    "mptt": "django-mptt", "polymorphic": "django-polymorphic", "rest_framework_nested": "drf-nested-routers",
    "boto3": "boto3", "stripe": "stripe", "requests": "requests", "sentry_sdk": "sentry-sdk",
    "gunicorn": "gunicorn", "uvicorn": "uvicorn", "daphne": "daphne", "django": "Django",
    "bs4": "beautifulsoup4", "cv2": "opencv-python", "sklearn": "scikit-learn", "jwt": "PyJWT",
    "dateutil": "python-dateutil", "magic": "python-magic", "markdown": "Markdown", "sass_processor": "django-sass-processor",
    "compressor": "django-compressor", "django_htmx": "django-htmx", "ninja": "django-ninja",
    "graphene_django": "graphene-django", "oauth2_provider": "django-oauth-toolkit", "axes": "django-axes",
    "simple_history": "django-simple-history", "model_utils": "django-model-utils", "solo": "django-solo",
    "tailwind": "django-tailwind", "django_browser_reload": "django-browser-reload",
}

#: Modules removed from Django, with the replacement.
REMOVED_MODULES = {
    "django.utils.six": ("Django 3.0", "Use the `six` package or plain Python 3 code."),
    "django.core.urlresolvers": ("Django 2.0", "Import from `django.urls` instead."),
    "django.utils.baseconv": ("Django 5.0", "Use `django.core.signing.b62_encode/b62_decode` or your own implementation."),
    "django.utils.datetime_safe": ("Django 5.0", "Use the `datetime` module directly."),
    "django.contrib.postgres.fields.jsonb": ("Django 4.0", "Use `django.db.models.JSONField`."),
    "django.utils.lru_cache": ("Django 3.0", "Use `functools.lru_cache`."),
}

#: (name, module) -> (removed in, replacement)
REMOVED_NAMES = {
    ("ugettext_lazy", "django.utils.translation"): ("Django 4.0", "gettext_lazy"),
    ("ugettext", "django.utils.translation"): ("Django 4.0", "gettext"),
    ("ungettext", "django.utils.translation"): ("Django 4.0", "ngettext"),
    ("ungettext_lazy", "django.utils.translation"): ("Django 4.0", "ngettext_lazy"),
    ("ugettext_noop", "django.utils.translation"): ("Django 4.0", "gettext_noop"),
    ("force_text", "django.utils.encoding"): ("Django 4.0", "force_str"),
    ("smart_text", "django.utils.encoding"): ("Django 4.0", "smart_str"),
    ("python_2_unicode_compatible", "django.utils.encoding"): ("Django 3.0", "nothing (remove the decorator)"),
    ("url", "django.conf.urls"): ("Django 4.0", "django.urls.re_path (or path)"),
    ("urlquote", "django.utils.http"): ("Django 4.0", "urllib.parse.quote"),
    ("urlquote_plus", "django.utils.http"): ("Django 4.0", "urllib.parse.quote_plus"),
    ("urlunquote", "django.utils.http"): ("Django 4.0", "urllib.parse.unquote"),
    ("is_safe_url", "django.utils.http"): ("Django 4.0", "url_has_allowed_host_and_scheme"),
    ("JSONField", "django.contrib.postgres.fields"): ("Django 4.0", "django.db.models.JSONField"),
    ("FieldDoesNotExist", "django.db.models.fields"): ("Django 3.1", "django.core.exceptions.FieldDoesNotExist"),
    ("utc", "django.utils.timezone"): ("Django 5.0", "datetime.timezone.utc"),
    ("get_storage_class", "django.core.files.storage"): ("Django 5.1", "django.core.files.storage.storages / the STORAGES setting"),
    ("available_attrs", "django.utils.decorators"): ("Django 3.0", "functools.wraps without assigned="),
    ("six", "django.utils"): ("Django 3.0", "the six package"),
    ("lru_cache", "django.utils.functional"): ("Django 3.0", "functools.lru_cache"),
    ("NullBooleanField", "django.db.models"): ("Django 4.0", "BooleanField(null=True)"),
}


def _setting_for_env_var(ctx: RuleContext, var: str):
    if ctx.project is None:
        return []
    return [u for u in scan_env_usage(ctx.project.settings_files()) if u.name == var]


def _env_var_diagnosis(ctx: RuleContext, exc, var: str, how: str) -> Diagnosis:
    d = ctx.new("environment.missing_variable", Category.ENVIRONMENT,
                f"The settings read the environment variable {var}, but it is not set.",
                exc=exc, exit_code=ExitCode.CONFIGURATION_ERROR, risk=Risk.NONE,
                why=f"{how} raises an error when the variable is missing and no default is given.")
    usages = _setting_for_env_var(ctx, var)
    for u in usages[:2]:
        rel = u.file.relative_to(ctx.project.root) if ctx.project else u.file
        target = f" (used for {u.setting})" if u.setting else ""
        d.add_evidence(f"{rel}:{u.line} reads {var} via {u.via}{target}.", verified=True)
    d.add_evidence(f"{var} is {'set' if var in os.environ else 'not set'} in the current environment.", verified=True)
    in_dotenv = []
    if ctx.project is not None:
        for f in ctx.project.project.env_files():
            if var in env_file_keys(f):
                in_dotenv.append(f)
    if in_dotenv:
        rel = in_dotenv[0].relative_to(ctx.project.root) if ctx.project and in_dotenv[0].is_relative_to(ctx.project.root) else in_dotenv[0]
        d.add_evidence(f"{var} is defined in {rel}.", verified=True)
        if ctx.project and not loads_dotenv(ctx.project.settings_files()):
            d.add_cause(f"{rel} defines {var}, but nothing loads that file: the settings do not call load_dotenv()/Env.read_env().",
                        Confidence.LIKELY)
            d.fixes.append("Load the .env file in settings (python-dotenv / django-environ), or export the variables in your shell. "
                           f"You can also set `env_file = \"{rel}\"` in [tool.{CLI_NAME}].")
        else:
            d.add_cause("The .env file is loaded from a different working directory or path.", Confidence.POSSIBLE)
    else:
        d.add_cause(f"{var} was never set for this shell / process.", Confidence.LIKELY)
    d.fixes.append(f"Set it, e.g. `export {var}=...` or add it to your .env file (never commit real secrets).")
    d.fixes.append("Alternatively give the setting a safe development default.")
    d.commands.append(f"{CLI_NAME} doctor")
    return d


@rule("environment.missing_variable")
def missing_env_var(ctx: RuleContext) -> Diagnosis | None:
    # os.environ["X"] -> KeyError: 'X' raised from a line that reads the environment.
    exc = ctx.find("KeyError")
    if exc is not None:
        m = re.fullmatch(r"'(?P<var>[A-Za-z_][A-Za-z0-9_]*)'", exc.first_line.strip())
        if m:
            var = m.group("var")
            frames = exc.frames
            code = " ".join(f.code or "" for f in frames[-3:])
            in_settings = any("settings" in f.file for f in frames[-3:])
            if "environ" in code or "getenv" in code or (in_settings and var.isupper()) or any(f.file.endswith("os.py") for f in frames[-2:]):
                return _env_var_diagnosis(ctx, exc, var, "os.environ[...]")
    found = ctx.search(r"Set the (?P<var>\w+) environment variable", "ImproperlyConfigured")
    if found:
        return _env_var_diagnosis(ctx, found[0], found[1].group("var"), "django-environ's env()")
    found = ctx.search(r"(?P<var>\w+) not found\. Declare it as envvar or define a default value", "UndefinedValueError")
    if found:
        return _env_var_diagnosis(ctx, found[0], found[1].group("var"), "python-decouple's config()")
    return None


# ----------------------------------------------------------- ImproperlyConfigured
@rule("settings.improperly_configured")
def improperly_configured(ctx: RuleContext) -> Diagnosis | None:
    exc = ctx.find("ImproperlyConfigured")
    if exc is None:
        return None
    msg = exc.message

    def make(rule_id: str, what: str, *, risk: Risk = Risk.NONE) -> Diagnosis:
        return ctx.new(rule_id, Category.SETTINGS, what, exc=exc, exit_code=ExitCode.CONFIGURATION_ERROR, risk=risk)

    if "SECRET_KEY setting must not be empty" in msg:
        d = make("settings.empty_secret_key", "SECRET_KEY is empty, so Django refuses to start.")
        usages = _setting_for_env_var_by_setting(ctx, "SECRET_KEY")
        for u in usages:
            d.add_evidence(f"SECRET_KEY is read from the environment variable {u.name} ({u.via}).", verified=True)
            d.add_evidence(f"{u.name} is {'set' if u.name in os.environ else 'not set'} in the current environment.", verified=True)
            if u.name not in os.environ:
                d.add_cause(f"The environment variable {u.name} is not set.", Confidence.DETECTED)
        d.add_cause("The environment variable holding the secret key is not set or not loaded.", Confidence.LIKELY)
        d.fixes += ["Provide SECRET_KEY via an environment variable / .env file.",
                    "Generate one with: python -c \"from django.core.management.utils import get_random_secret_key as g; print(g())\""]
        return d

    m = re.search(r"Requested setting (?P<setting>\w+), but settings are not configured", msg)
    if m:
        d = make("settings.not_configured", f"Code accessed settings.{m.group('setting')} before Django knew which settings module to use.")
        d.add_cause("A script or tool imported Django code without DJANGO_SETTINGS_MODULE set.", Confidence.LIKELY)
        d.add_cause("A module imported at settings-import time uses django.conf.settings (import cycle).")
        d.fixes += ["Run through manage.py / djdoctor, or export DJANGO_SETTINGS_MODULE=<project>.settings.",
                    "In standalone scripts call django.setup() after setting DJANGO_SETTINGS_MODULE."]
        return d

    m = re.search(r"Error loading (?P<mod>[\w.]+)(?: or (?P<mod2>\w+))? module", msg)
    if m:
        mod = m.group("mod")
        pkg = {"psycopg2": "psycopg[binary]", "MySQLdb": "mysqlclient", "cx_Oracle": "oracledb", "oracledb": "oracledb"}.get(mod, mod)
        d = make("settings.missing_db_driver", f"The database driver '{mod}' is not installed in this Python environment.")
        d.add_cause("The database driver package is missing from the active virtualenv.", Confidence.LIKELY)
        d.fixes.append(f"Install it: pip install \"{pkg}\" (and add it to your requirements).")
        _add_interpreter_evidence(ctx, d)
        return d

    m = re.search(r"'(?P<backend>[\w.]+)' isn't an available database backend", msg)
    if m:
        backend = m.group("backend")
        d = make("settings.bad_db_engine", f"DATABASES ENGINE '{backend}' is not a valid database backend.")
        builtin = ["django.db.backends.postgresql", "django.db.backends.mysql", "django.db.backends.sqlite3", "django.db.backends.oracle"]
        for s in close_matches(backend, builtin, cutoff=0.5):
            d.add_cause(f"Typo: did you mean '{s}'?", Confidence.LIKELY)
        d.fixes.append("Fix ENGINE in DATABASES.")
        return d

    if "supply the ENGINE value" in msg or "settings.DATABASES is improperly configured" in msg:
        d = make("settings.no_database", "DATABASES['default'] is empty or has no ENGINE.")
        usages = [u for u in _all_env_usages(ctx) if u.name == "DATABASE_URL"]
        if usages:
            d.add_evidence(f"DATABASES is built from the environment variable DATABASE_URL ({usages[0].via}).", verified=True)
            d.add_evidence(f"DATABASE_URL is {'set' if 'DATABASE_URL' in os.environ else 'not set'}.", verified=True)
            if "DATABASE_URL" not in os.environ:
                d.add_cause("DATABASE_URL is not set, so dj-database-url/django-environ produced an empty configuration.", Confidence.DETECTED)
        d.add_cause("DATABASES is missing, or built from an environment variable that is not set.", Confidence.LIKELY)
        d.fixes.append("Set DATABASE_URL (or define DATABASES explicitly) — e.g. sqlite:///db.sqlite3 for development.")
        return d

    m = re.search(r"The included URLconf '(?P<conf>[\w.]+)' does not appear to have any patterns in it", msg)
    if m:
        conf = m.group("conf")
        d = make("urls.no_patterns", f"The URLconf '{conf}' has no `urlpatterns`.")
        if ctx.project is not None:
            from django_doctor.project import module_files

            files = module_files(ctx.project.root, conf)
            if files:
                text = files[0].read_text(encoding="utf-8", errors="replace")
                rel = files[0].relative_to(ctx.project.root)
                if re.search(r"^urlpatterns\s*[:=]", text, re.M):
                    d.add_evidence(f"{rel} defines urlpatterns.", verified=True)
                    d.add_cause("urlpatterns exists, so the module is most likely partially imported because of a circular import "
                                "(e.g. views importing urls, or reverse() at import time — use reverse_lazy).", Confidence.LIKELY)
                else:
                    d.add_evidence(f"{rel} does not assign a variable called urlpatterns.", verified=True)
                    d.add_cause("The variable is missing or misspelled (it must be exactly `urlpatterns`).", Confidence.DETECTED)
        d.add_cause("A circular import prevents the module from finishing its import.", Confidence.POSSIBLE)
        return d

    m = re.search(r"AUTH_USER_MODEL refers to model '(?P<model>[\w.]+)' that has not been installed", msg)
    if m:
        d = make("settings.auth_user_model", f"AUTH_USER_MODEL points to '{m.group('model')}', which is not an installed model.")
        d.add_cause(f"The app '{m.group('model').split('.')[0]}' is not in INSTALLED_APPS, or the model/app label is misspelled.", Confidence.LIKELY)
        d.fixes.append("Use the format 'app_label.ModelName' and make sure the app is installed.")
        return d

    m = re.search(r"Application labels aren't unique, duplicates: (?P<label>\w+)", msg)
    if m:
        d = make("settings.duplicate_app", f"Two installed apps share the label '{m.group('label')}'.")
        d.add_cause("The same app is listed twice in INSTALLED_APPS (possibly once as 'app' and once as 'app.apps.AppConfig'), "
                    "or two different apps have the same label.", Confidence.LIKELY)
        d.fixes.append("Remove the duplicate, or set a unique `label` on one AppConfig.")
        return d

    m = re.search(r"Cannot import '(?P<name>[\w.]+)'\. Check that '(?P<path>[\w.]+)\.name' is correct", msg)
    if m:
        d = make("settings.app_config_name", f"The AppConfig {m.group('path')} has name = '{m.group('name')}', which cannot be imported.")
        d.add_cause("AppConfig.name must be the full dotted import path of the app (e.g. 'apps.students' when the app lives in apps/).",
                    Confidence.LIKELY)
        return d

    if "without having set the STATIC_ROOT setting" in msg or "without having set the required STATIC_URL" in msg:
        which = "STATIC_ROOT" if "STATIC_ROOT" in msg else "STATIC_URL"
        d = make("settings.static", f"{which} is not configured, but the staticfiles app needs it for this operation.")
        d.fixes.append("Set STATIC_ROOT = BASE_DIR / 'staticfiles' (the folder collectstatic copies files into)."
                       if which == "STATIC_ROOT" else "Set STATIC_URL = 'static/'.")
        d.add_cause(f"{which} is missing from settings.", Confidence.DETECTED)
        return d

    m = re.search(r"WSGI application '(?P<app>[^']+)' could not be loaded", msg)
    if m:
        d = make("settings.wsgi", f"The WSGI application '{m.group('app')}' failed to import.")
        inner = [e for e in ctx.parsed.exceptions if e is not exc]
        if inner:
            d.add_evidence(f"Underlying error: {inner[-1].type}: {inner[-1].first_line}")
        d.add_cause("A middleware in MIDDLEWARE cannot be imported (package missing or path misspelled).", Confidence.LIKELY)
        d.fixes.append("Check MIDDLEWARE entries and that their packages are installed.")
        return d

    if "Creating a ModelForm without either the 'fields' attribute or the 'exclude' attribute is prohibited" in msg:
        d = make("forms.modelform_fields", "A ModelForm (or a generic CreateView/UpdateView) does not declare which fields it uses.")
        d.fixes.append("Add `fields = [...]` to the form's Meta (or `fields` on the view).")
        d.add_cause("Meta.fields / Meta.exclude is missing.", Confidence.DETECTED)
        return d

    m = re.search(r"(?P<view>\w+) is missing a QuerySet", msg)
    if m:
        d = make("views.missing_queryset", f"The generic view {m.group('view')} doesn't know which model to use.")
        d.fixes.append("Set `model = ...` or `queryset = ...` on the view, or override get_queryset().")
        d.add_cause("model/queryset is not defined on the view.", Confidence.DETECTED)
        return d

    d = make("settings.improperly_configured", "Django reported a configuration problem (ImproperlyConfigured).")
    d.add_cause("A setting is missing or invalid; the message above names it.", Confidence.LIKELY)
    d.commands.append(f"{CLI_NAME} doctor")
    return d


def _all_env_usages(ctx: RuleContext):
    return scan_env_usage(ctx.project.settings_files()) if ctx.project else []


def _setting_for_env_var_by_setting(ctx: RuleContext, setting: str):
    return [u for u in _all_env_usages(ctx) if u.setting == setting]


def _add_interpreter_evidence(ctx: RuleContext, d: Diagnosis) -> None:
    if ctx.project is not None:
        d.add_evidence(f"Python interpreter used: {ctx.project.project.python}", verified=True)
        d.add_cause("The project's virtualenv is not activated, so a different Python environment is being used.")


# ------------------------------------------------------------------ imports
@rule("imports.circular")
def circular_import(ctx: RuleContext) -> Diagnosis | None:
    found = ctx.search(
        r"cannot import name '(?P<name>\w+)' from partially initialized module '(?P<module>[\w.]+)' \(most likely due to a circular import\)",
        "ImportError",
    )
    if not found:
        return None
    exc, match = found
    d = ctx.new("imports.circular", Category.IMPORTS,
                f"A circular import: '{match.group('module')}' was imported again while it was still being initialised, "
                f"so '{match.group('name')}' did not exist yet.",
                exc=exc, exit_code=ExitCode.CONFIGURATION_ERROR, risk=Risk.NONE)
    chain = []
    for f in exc.frames:
        if ctx.root is not None and not f.is_within(ctx.root):
            continue
        if ctx.root is None and f.is_library:
            continue
        short = f.short(ctx.root).split(" in ")[0].rsplit(":", 1)[0]
        if short not in chain:
            chain.append(short)
    if chain:
        d.add_evidence("Import cycle through: " + " → ".join(chain))
    d.add_cause("Two modules import each other at module level (commonly models.py ↔ another app's models, or views ↔ urls).",
                Confidence.DETECTED)
    d.fixes += [
        "Reference related models with strings: ForeignKey('otherapp.Model') instead of importing the class.",
        "Move the import inside the function that needs it, or use django.apps.apps.get_model().",
        "Use reverse_lazy() instead of reverse() in module-level code.",
    ]
    return d


@rule("imports.removed_django_api")
def removed_django_api(ctx: RuleContext) -> Diagnosis | None:
    found = ctx.search(r"cannot import name '(?P<name>\w+)' from '(?P<module>[\w.]+)'", "ImportError")
    if found:
        exc, match = found
        key = (match.group("name"), match.group("module"))
        if key in REMOVED_NAMES:
            removed, replacement = REMOVED_NAMES[key]
            d = ctx.new("imports.removed_django_api", Category.IMPORTS,
                        f"'{key[0]}' no longer exists in {key[1]}: it was removed in {removed}.",
                        exc=exc, exit_code=ExitCode.CONFIGURATION_ERROR, risk=Risk.NONE)
            d.add_cause("The code (or a third-party package) was written for an older Django version.", Confidence.DETECTED)
            d.fixes.append(f"Replace it with {replacement}.")
            d.fixes.append("If the import is inside a third-party package, upgrade that package.")
            return d
        d = ctx.new("imports.name_not_found", Category.IMPORTS,
                    f"The module '{key[1]}' has no attribute '{key[0]}'.", exc=exc,
                    exit_code=ExitCode.CONFIGURATION_ERROR, risk=Risk.NONE)
        d.add_cause("The name is misspelled, was renamed/removed in the installed version, or was never defined there.", Confidence.LIKELY)
        d.add_cause("Two modules import each other (circular import).")
        return d
    return None


@rule("imports.module_not_found")
def module_not_found(ctx: RuleContext) -> Diagnosis | None:
    found = ctx.search(r"No module named '(?P<module>[\w.]+)'", "ModuleNotFoundError", "ImportError")
    if not found:
        return None
    exc, match = found
    module = match.group("module")
    top = module.split(".")[0]

    for removed, (version, fix) in REMOVED_MODULES.items():
        if module == removed or module.startswith(removed + "."):
            d = ctx.new("imports.removed_django_module", Category.IMPORTS,
                        f"The module '{removed}' was removed in {version}.", exc=exc,
                        exit_code=ExitCode.CONFIGURATION_ERROR, risk=Risk.NONE)
            d.add_cause("Code written for an older Django version.", Confidence.DETECTED)
            d.fixes.append(fix)
            return d

    d = ctx.new("imports.module_not_found", Category.IMPORTS,
                f"Python could not import the module '{module}'.", exc=exc,
                exit_code=ExitCode.CONFIGURATION_ERROR, risk=Risk.NONE)
    local_dir = None
    if ctx.project is not None:
        root = ctx.project.root
        if (root / top).is_dir() or (root / f"{top}.py").is_file():
            local_dir = root / top
            if module != top:
                d.add_evidence(f"'{top}' is a local package in the project, but '{module}' does not exist inside it.", verified=True)
                d.add_cause("The submodule file is missing or misspelled.", Confidence.LIKELY)
            else:
                d.add_evidence(f"'{top}' exists in the project directory.", verified=True)
                d.add_cause("The command is not running from the project root, so the project is not on sys.path.", Confidence.POSSIBLE)
        else:
            nested = [p for p in root.glob(f"*/{top}")
                      if p.is_dir() and ((p / "__init__.py").exists() or (p / "apps.py").exists())]
            for p in nested[:1]:
                dotted = ".".join(p.relative_to(root).parts)
                d.add_evidence(f"Found a package at {p.relative_to(root)}.", verified=True)
                d.add_cause(f"The app lives in a sub-folder: reference it as '{dotted}' (INSTALLED_APPS and AppConfig.name).",
                            Confidence.LIKELY)
            candidates = [p.name for p in root.iterdir() if p.is_dir() and not p.name.startswith(".")]
            for m in close_matches(top, candidates, cutoff=0.75):
                if m != top:
                    d.add_cause(f"Typo: a local package called '{m}' exists.", Confidence.POSSIBLE)
    if local_dir is None:
        pkg = PIP_NAMES.get(top, top)
        d.add_cause(f"The package providing '{top}' ({pkg}) is not installed in the Python environment being used.",
                    Confidence.LIKELY)
        if ctx.project is not None:
            for req in ctx.project.project.dependency_files():
                text = req.read_text(encoding="utf-8", errors="replace").lower()
                if pkg.split("[")[0].lower() in text:
                    d.add_evidence(f"{req.name} lists {pkg}.", verified=True)
                    d.add_cause("Your requirements include it, so dependencies were probably not installed into the active "
                                "virtualenv (or the wrong virtualenv is active).", Confidence.LIKELY)
                    break
        d.fixes.append(f"pip install \"{pkg}\"   # then add it to your requirements if it is not listed")
        _add_interpreter_evidence(ctx, d)
    return d


# -------------------------------------------------------------- app registry
@rule("settings.apps_not_ready")
def apps_not_ready(ctx: RuleContext) -> Diagnosis | None:
    found = ctx.search(r"(Apps aren't loaded yet|Models aren't loaded yet|The translation infrastructure cannot be initialized)",
                       "AppRegistryNotReady")
    if not found:
        return None
    exc, _ = found
    d = ctx.new("settings.apps_not_ready", Category.SETTINGS,
                "Models or apps were used before Django finished loading the app registry.",
                exc=exc, exit_code=ExitCode.CONFIGURATION_ERROR, risk=Risk.NONE)
    d.add_cause("A model is imported at module level in settings.py, an app's __init__.py, or AppConfig module code.", Confidence.LIKELY)
    d.add_cause("A standalone script imports models without calling django.setup() first.")
    d.fixes += ["Move model imports into functions or AppConfig.ready().",
                "In scripts: set DJANGO_SETTINGS_MODULE, call django.setup(), then import models."]
    return d


@rule("settings.model_not_installed")
def model_not_installed(ctx: RuleContext) -> Diagnosis | None:
    found = ctx.search(r"Model class (?P<cls>[\w.]+) doesn't declare an explicit app_label and isn't in an application in INSTALLED_APPS",
                       "RuntimeError")
    if not found:
        return None
    exc, match = found
    cls = match.group("cls")
    app = cls.split(".models")[0] if ".models" in cls else cls.rsplit(".", 1)[0]
    d = ctx.new("settings.model_not_installed", Category.SETTINGS,
                f"The model {cls} belongs to an app that is not in INSTALLED_APPS.",
                exc=exc, exit_code=ExitCode.CONFIGURATION_ERROR, risk=Risk.NONE)
    if ctx.project and (info := ctx.project.info):
        installed = info.get("installed_app_names", [])
        d.add_evidence(f"'{app}' is {'in' if app in installed else 'not in'} INSTALLED_APPS.", verified=True)
    d.add_cause(f"Add '{app}' to INSTALLED_APPS (or its AppConfig path).", Confidence.LIKELY)
    d.fixes.append(f"Add '{app}' to INSTALLED_APPS, then create migrations for it.")
    return d
